"""Thin per-frame YOLO call for the GPU show path (PERF-3).

``YOLO.__call__`` on a GPU tensor does, every frame, work WallDance never
uses:

* ``get_cfg`` rebuilds and type-checks the whole predictor config, and the
  ``half=`` kwarg prints an ultralytics 8.4 deprecation warning to stderr;
* ``setup_source`` builds a fresh ``LoadTensor``;
* three ``ops.Profile`` blocks call a device-wide ``torch.cuda.synchronize``
  six times;
* ``DetectionPredictor.postprocess`` downloads the whole letterboxed input
  as uint8 (``convert_torch2numpy_batch``: ~4.9 MB D2H at imgsz 1280) only to
  read its ``.shape`` (box/keypoint scaling, ``Results.orig_shape``).

``PoseRunner`` keeps the predictor that a regular call set up and, while the
call arguments and the predictor are unchanged, runs exactly the predictor's
own ``preprocess`` -> ``inference`` -> ``postprocess`` (same NMS, same
scaling, same ``Results``) on the tensor, with ``LoadTensor``'s input check
and a zero-stride stand-in for the original image (only its shape is read).
The detections are therefore identical to the regular call's.  The first call,
any change of model/predictor/conf/iou/imgsz/half, a non-[0, 1] input, an
input the regular path would reject, and any failure go through the regular
``model(...)`` call.  ``Results.speed`` and ``Results.orig_img`` are not
filled on the fast path (nothing in WallDance reads them).
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

try:
    import torch
except ImportError:  # pragma: no cover - the GPU path needs torch
    torch = None

_ZERO_U8 = np.zeros((), dtype=np.uint8)
_STRIDE = 32  # LoadTensor._single_check's default stride


class PoseRunner:
    """Call ``model`` like ``model(t, imgsz=, conf=, iou=, half=, verbose=False)``."""

    def __init__(self, model: Any):
        self.model = model
        self._key: Optional[tuple] = None
        self._pred = None
        self.enabled = True          # turned off for good if the fast path misbehaves
        self.fast_calls = 0
        self.regular_calls = 0
        self.disabled_reason: Optional[str] = None

    # ------------------------------------------------------------------
    def __call__(self, tensor, *, imgsz: int, conf: float, iou: float, half: bool):
        key = (imgsz, conf, iou, bool(half))
        if self.enabled and key == self._key and self._fast_ready(conf, iou):
            try:
                out = self._fast(tensor)
            except Exception as exc:  # noqa: BLE001 - the regular call decides
                out = None
                fast_exc = exc
            else:
                fast_exc = None
            if out is not None:
                self.fast_calls += 1
                return out
            results = self._regular(tensor, key, imgsz, conf, iou, half)
            if fast_exc is not None:
                # The regular call worked where the fast path raised: an
                # ultralytics drift we do not understand -- stop trying.
                self.enabled = False
                self.disabled_reason = f"{type(fast_exc).__name__}: {fast_exc}"
                print(f"[YOLO] fast path disabled ({self.disabled_reason}); "
                      "using the regular ultralytics call")
            return results
        return self._regular(tensor, key, imgsz, conf, iou, half)

    # ------------------------------------------------------------------
    def _regular(self, tensor, key, imgsz, conf, iou, half):
        self.regular_calls += 1
        results = self.model(tensor, imgsz=imgsz, conf=conf, iou=iou,
                             half=half, verbose=False)
        self._key = key
        self._pred = getattr(self.model, "predictor", None)
        return results

    def _fast_ready(self, conf: float, iou: float) -> bool:
        """The predictor still holds exactly the state our last regular call
        left (nothing else re-ran or re-built it in between)."""
        pred = getattr(self.model, "predictor", None)
        if pred is None or pred is not self._pred or torch is None:
            return False
        args = getattr(pred, "args", None)
        st = getattr(pred, "source_type", None)
        try:
            return (args.conf == conf and args.iou == iou
                    and bool(getattr(st, "tensor", False))
                    and not args.visualize and not args.augment and not args.embed
                    and not (args.save or args.save_txt or args.save_crop or args.show)
                    and pred.batch is not None and pred.done_warmup
                    and pred.model is not None)
        except AttributeError:
            return False

    def _fast(self, im):
        """BasePredictor.stream_inference for one tensor batch, minus the
        per-call setup, Profile syncs and the orig_img download.  Returns
        None to request the regular call."""
        pred = self._pred
        if (not isinstance(im, torch.Tensor) or im.dim() != 4 or not all(im.shape)
                or im.shape[2] % _STRIDE or im.shape[3] % _STRIDE):
            return None   # the regular path warns / raises for these
        eps = torch.finfo(im.dtype).eps if im.is_floating_point() else 0
        with pred._lock, torch.inference_mode():
            if im.max() > 1.0 + eps:          # LoadTensor would rescale (and warn)
                return None
            x = pred.preprocess(im)
            preds = pred.inference(x)
            b, c, h, w = im.shape
            # convert_torch2numpy_batch(im)[..., ::-1] is (B, H, W, C); only
            # its per-image .shape is ever read.
            orig = [np.broadcast_to(_ZERO_U8, (h, w, c)) for _ in range(b)]
            return pred.postprocess(preds, x, orig)
