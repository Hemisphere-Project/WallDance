"""The settings swipe's candidate runner (Plan Cordée P5): the kept frames through other settings.

``core.swipe`` decides (the referee, the collector, the verdict); this module produces what it judges: for
each candidate setting (model x network input), the boxes the live path WOULD have produced on the kept
frames -- same ROI crop, same enhancement, letterbox to the candidate's input, same decode back to original
camera pixels -- without touching the live pipeline's state:

* its own ``GpuPipeline`` (own enhancer: no temporal denoise carried over, the brightness gate measured on
  each frame instead of every 10th), its own model instance and ``PoseRunner``;
* boxes kept down to ``LOW_CONF`` (0.02) so the scoring chooses its own threshold;
* one engine at a time: load, warm up, time each inference, then drop it (a TensorRT model is freed by
  refcount the moment its last reference goes: ~1 GB back at once on mgr-ran) right after a live frame, before
  the next.  One ``gc.collect`` and one ``torch.cuda.empty_cache`` at the very end of the swipe, also right
  after a live frame (a full collection holds the GIL ~90 ms on mgr-ran and frees no GPU memory; per
  candidate it stalled a live frame each time).  Never builds an engine.

Thread safety (measured on mgr-ran 26/11, ``tmp_analysis/swipe-1126/live/exp`` and ``runs``): ultralytics
8.4.1xx captures a CUDA graph when it loads a TensorRT engine.  While one thread captures, another thread's
CUDA work breaks it: in torch's default ``global`` mode the load fails (``cudaErrorStreamCaptureInvalidated``);
in ``thread_local`` mode the multi-stream engines' TensorRT aux streams are blocking streams, so the other
thread's legacy-stream work still fails (``cudaErrorStreamCaptureImplicit``, and ``torch.cuda.synchronize``:
``CUDA_ERROR_STREAM_CAPTURE_UNSUPPORTED``) -- the live loop's YOLO call failed that way during a swipe.  So
(``install_capture_guards``, ``no_graph_capture``):

* the swipe's engines never capture a graph: in the swipe's thread ``torch.cuda.graph`` does nothing and the
  TensorRT backend runs ``execute_v2`` (its path for dynamic engines; the graph saves ~50 us a call);
* one engine load at a time in the process (``core.model_manager.ENGINE_LOAD_LOCK``, taken by
  ``ModelManager.load_model`` and by the swipe for each candidate's whole run), and the swipe stops at its next
  frame when a live model load begins: no swipe CUDA work overlaps the live model's capture;
* other threads' captures default to ``thread_local`` (defence in depth).

Coordinates: original camera pixels, ``(x0, y0, x1, y1, conf)``, like ``core.swipe``.
"""
from __future__ import annotations

import copy
import gc
import inspect
import math
import threading
import time
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from core.input_shape import RECT_PRESETS, Shape, letterbox_scale, parse_shape, pick_shape
from core.swipe import Box, Candidate, SwipeParams, SwipeResult, Tally, decide, score_frame

SWIPE_MODELS: Tuple[str, ...] = ("yolo11x-pose", "yolo11l-pose")
LOW_CONF = 0.02              # boxes kept down to this confidence (the scoring picks its threshold)
# Thomas's band for the dancer's height in the network (the readiness « dancer size » row), not tuned here:
SMALL_NET_PX = 110.0         # under this she is « small » at the live input (only the « not tried » line says so)
LARGE_NET_PX = 200.0         # over this the input size no longer matters: only the model is in question
PRUNE_NET_PX = LARGE_NET_PX  # (the first draft's name)
WARMUP_CALLS = 2


# ----------------------------------------------------------------------------------------------------
# Candidates
# ----------------------------------------------------------------------------------------------------
def parse_candidate(text: str, imgsz: int) -> Candidate:
    """``x@1280`` / ``l@1920x1056`` / ``yolo11l-pose@1760x960`` -> Candidate (``imgsz`` = the project's
    square size, which a rectangle keeps)."""
    model, sep, size = str(text).strip().partition("@")
    if not sep or not model or not size:
        raise ValueError(f"candidate {text!r}: expected '<model>@<size>' (x@1280, l@1920x1056)")
    if not model.startswith("yolo"):
        model = f"yolo11{model}-pose"
    size = size.lower().replace(" ", "")
    if "x" in size:
        return Candidate(model, int(imgsz), parse_shape(size))
    return Candidate(model, int(size), None)


def candidate_scale(c: Candidate, roi_w: int, roi_h: int) -> float:
    """Original px -> network px for this candidate on this ROI."""
    return letterbox_scale(roi_w, roi_h, c.imgsz, c.shape) if roi_w > 0 and roi_h > 0 else 0.0


@dataclass
class CandidatePlan:
    candidates: List[Candidate]                                   # to run, the live setting first
    skipped: List[Tuple[Candidate, str]] = field(default_factory=list)   # not run, and why (ASCII)

    def skipped_text(self) -> List[str]:
        return [f"{c.label} not tried: {why}" for c, why in self.skipped]


def plan_candidates(current: Candidate, roi_w: int, roi_h: int,
                    engine_exists: Callable[[Candidate], bool], *,
                    models: Sequence[str] = SWIPE_MODELS, rects: Sequence[Shape] = RECT_PRESETS,
                    dancer_net_px: Optional[float] = None, only: Optional[Iterable[Candidate]] = None,
                    all_sizes: bool = False) -> CandidatePlan:
    """The swipe chooses the MODEL; the dancer's height in the network chooses the input size (26/11: the
    height picks the size, the scene the model).  ``dancer_net_px`` = her height at the live input (the
    « dancer size » readiness row's figure); unknown counts as small.

    * >= LARGE_NET_PX (200): sizes no longer matter -- the live setting and the other model at the live input;
    * otherwise both models at the LARGEST suitable input that has an engine (the one showing her tallest:
      the project's square or a rectangle ``pick_shape`` accepts for this ROI); intermediate inputs and the
      other model at the live input are not tried (a 60-frame window cannot separate neighbouring sizes: on
      the 08/10 truth one swipe in four then named a size 2-4.5 points under the best; the 110-200 px band,
      where the other model at the live input was also tried, is out until a take covers it -- the review of
      26/11 measured it naming a size 2-5 points under the best on a quarter to half of the swipes).

    The live setting is first and always run.  A missing engine is never built: the next smaller input of
    that model is taken, and the result says so.  ``only`` (the driver's --candidates) and ``all_sizes``
    (every model x every suitable size, for diagnostics) bypass the rule."""
    sizes: List[Tuple[int, Optional[Shape]]] = [(int(current.imgsz), None)]
    if roi_w > 0 and roi_h > 0:
        for r in rects:
            if pick_shape(roi_w, roi_h, [tuple(r)]) is not None:
                sizes.append((int(current.imgsz), (int(r[0]), int(r[1]))))
    every = [Candidate(m, sz, sh) for m in models for sz, sh in sizes]
    cur_scale = candidate_scale(current, roi_w, roi_h)
    h = float(dancer_net_px) if dancer_net_px else None

    def h_at(c: Candidate) -> Optional[float]:
        if h is None or cur_scale <= 0:
            return None
        return h * candidate_scale(c, roi_w, roi_h) / cur_scale

    def engine_text(c: Candidate) -> str:
        return f"its engine is not built on this machine ({c.engine_file})"

    plan = CandidatePlan([current])
    reasons: Dict[Candidate, str] = {}
    if only is not None or all_sizes:
        wanted = list(only) if only is not None else list(every)
        for c in wanted:
            if c == current or c in plan.candidates:
                continue
            if engine_exists(c):
                plan.candidates.append(c)
            else:
                plan.skipped.append((c, engine_text(c)))
    else:
        others = [Candidate(m, current.imgsz, current.shape) for m in models if m != current.model]
        if h is not None and h >= LARGE_NET_PX:
            wanted = others
            for c in every:
                if c != current and c not in wanted:
                    reasons[c] = (f"the dancer is already ~{h:.0f} px tall at {current.label}: "
                                  "the input size no longer matters")
        else:
            wanted = []
            for m in models:
                by_size = sorted((c for c in every if c.model == m),
                                 key=lambda c: -candidate_scale(c, roi_w, roi_h))
                for c in by_size:
                    if c == current or engine_exists(c):
                        wanted.append(c)
                        break
                    reasons[c] = engine_text(c)
            for c in every + others:
                if c != current and c not in wanted and c not in reasons:
                    hc = h_at(c)
                    if hc is None:
                        reasons[c] = "the dancer's height is not measured: only the largest inputs are tried"
                    elif (c.imgsz, c.shape) == (current.imgsz, current.shape):
                        reasons[c] = (f"the dancer is only {hc:.0f} px tall there" if hc < SMALL_NET_PX else
                                      f"the dancer is ~{hc:.0f} px tall there, under {LARGE_NET_PX:.0f}: "
                                      "the largest input is tried")
                    else:
                        reasons[c] = f"an intermediate input (the dancer ~{hc:.0f} px there): the largest is tried"
        for c in wanted:
            if c == current or c in plan.candidates:
                continue
            if engine_exists(c):
                plan.candidates.append(c)
            else:
                reasons[c] = engine_text(c)
        seen = set(plan.candidates)
        for c in every + [c for c in reasons if c not in every]:
            if c not in seen and c in reasons:
                plan.skipped.append((c, reasons[c]))
                seen.add(c)
    if not engine_exists(current):
        plan.skipped.insert(0, (current, f"{engine_text(current)}; the live setting must be re-run"))
        plan.candidates.remove(current)
    return plan


# ----------------------------------------------------------------------------------------------------
# The operator's excluded cells
# ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ExclusionCells:
    """The exclusion mask's cells, as the referee must see them.  The pipeline drops a detection whose centre
    falls in an excluded cell before tracking (``FrameProcessor._apply_exclusion``): such a box is harmless in
    the show, so the swipe drops it from every candidate's boxes (the live re-run included) and from the
    tap's live boxes before scoring -- the plain cell test, without the pipeline's exception for a box near a
    recently updated track (the runner has no tracks).

    The geometry is the pipeline's: a box centre in original px, ROI-local, normalised over the motion mask's
    footprint (its size / the MOG2 scale), then ``ExclusionMaskBuilder.excluded``'s cell lookup on the
    effective cells ``(auto | manual_add) - manual_remove`` of the ``exclusion_grid``."""
    gx: int
    gy: int
    cells: frozenset
    x0: float                 # original px of the grid's top-left corner (the ROI's)
    y0: float
    span_w: float             # original px the grid spans
    span_h: float

    def excluded(self, cx: float, cy: float) -> bool:
        if not self.cells or self.span_w <= 0 or self.span_h <= 0:
            return False
        nx = (float(cx) - self.x0) / self.span_w
        ny = (float(cy) - self.y0) / self.span_h
        if not (0.0 <= nx < 1.0 and 0.0 <= ny < 1.0):
            return False
        return (int(nx * self.gx), int(ny * self.gy)) in self.cells

    def filter(self, boxes: Sequence[Box]) -> List[Box]:
        return [b for b in boxes if not self.excluded((b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5)]

    @classmethod
    def from_processor(cls, proc, roi: Tuple[int, int, int, int]) -> Optional["ExclusionCells"]:
        """The processor's mask now, for an ROI ``(x, y, w, h)``; None when the pipeline would exclude
        nothing (no cell, a calibration collecting, no motion mask yet)."""
        ex = getattr(proc, "_exclusion", None)
        if ex is None or getattr(ex, "collecting", False) or not getattr(ex, "active", False):
            return None
        det = getattr(proc, "crossval_motion_detector", None)
        mask = getattr(det, "_clean_mask", None) if det is not None else None
        if mask is None:
            return None
        mh, mw = mask.shape[:2]
        s = float(getattr(det, "_scale", 1.0)) or 1.0
        return cls(int(ex.gx), int(ex.gy), frozenset(ex.effective_cells()), float(roi[0]), float(roi[1]),
                   mw / s, mh / s)


# ----------------------------------------------------------------------------------------------------
# What the live path does before the network, frozen when the swipe starts
# ----------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class SwipeInput:
    roi_enabled: bool = False
    roi_x: int = 0
    roi_y: int = 0
    roi_w: int = 0
    roi_h: int = 0
    enhance_enabled: bool = False
    enhance_lite: bool = False
    enhance_force: bool = False
    brightness_threshold: float = 60.0
    clahe_clip: float = 2.0
    clahe_grid: int = 8
    gamma: float = 1.2
    greyscale: bool = False
    bg_subtract: bool = False
    bg_sensitivity: int = 30
    half: bool = True
    iou: float = 0.7

    @classmethod
    def from_processor(cls, proc) -> "SwipeInput":
        """The settings ``FrameProcessor._sync_gpu_settings`` hands the live GPU pipeline (temporal denoise
        aside: kept frames are SwipeParams.min_gap_s apart at least, there is no stream to smooth)."""
        from core.config import YOLO_IOU_THRESHOLD
        s, enh = proc.settings, proc.enhancer
        bg = getattr(proc, "bg_subtractor", None)
        return cls(
            roi_enabled=bool(s.roi_enabled), roi_x=int(s.roi_x), roi_y=int(s.roi_y),
            roi_w=int(s.roi_w), roi_h=int(s.roi_h),
            enhance_enabled=bool(s.enhance_enabled), enhance_lite=bool(s.enhance_lite),
            enhance_force=bool(s.enhance_force), brightness_threshold=float(s.brightness_threshold),
            clahe_clip=float(enh.clahe_clip), clahe_grid=8, gamma=float(enh.gamma),
            greyscale=bool(getattr(s, "greyscale", False)),
            bg_subtract=bool(s.bg_subtract_enabled and bg is not None
                             and getattr(bg, "has_reference", False)),
            bg_sensitivity=int(s.bg_subtract_sensitivity),
            half=bool(s.use_fp16), iou=float(YOLO_IOU_THRESHOLD))

    def roi(self, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
        """(x, y, w, h) the live GPU pipeline crops (``GpuPipeline._resolve_roi``)."""
        if not self.roi_enabled or frame_w <= 1 or frame_h <= 1:
            return 0, 0, int(frame_w), int(frame_h)
        x = max(0, min(int(self.roi_x), frame_w - 1))
        y = max(0, min(int(self.roi_y), frame_h - 1))
        x2 = max(x + 1, min(frame_w, x + max(1, int(self.roi_w))))
        y2 = max(y + 1, min(frame_h, y + max(1, int(self.roi_h))))
        return x, y, x2 - x, y2 - y


def unletterbox_xyxy(xyxy: np.ndarray, lb: Dict[str, Any], roi_x: float, roi_y: float) -> np.ndarray:
    """Network-input px (letterboxed ROI) -> original camera px: the inverse of the live letterbox
    (``GpuPipeline._prepare_yolo_input``), as ``FrameProcessor._unscale_letterbox`` does for tracks."""
    a = np.asarray(xyxy, dtype=np.float64).reshape(-1, 4).copy()
    s = float(lb.get("scale", 1.0)) or 1.0
    a[:, [0, 2]] = (a[:, [0, 2]] - float(lb.get("pad_x", 0))) / s + float(roi_x)
    a[:, [1, 3]] = (a[:, [1, 3]] - float(lb.get("pad_y", 0))) / s + float(roi_y)
    return a


def _np(x) -> np.ndarray:
    if x is None:
        return np.zeros((0,), np.float32)
    if hasattr(x, "detach"):
        x = x.detach()
    if hasattr(x, "cpu"):
        x = x.cpu()
    if hasattr(x, "numpy"):
        x = x.numpy()
    return np.asarray(x)


def boxes_from_results(results, lb: Dict[str, Any], roi_x: float, roi_y: float) -> List[Box]:
    """Every box of an ultralytics result list, in original px, with its confidence."""
    out: List[Box] = []
    for r in results or []:
        b = getattr(r, "boxes", None)
        if b is None or len(b) == 0:
            continue
        xyxy = unletterbox_xyxy(_np(b.xyxy), lb, roi_x, roi_y)
        conf = _np(b.conf).reshape(-1)
        out.extend((float(q[0]), float(q[1]), float(q[2]), float(q[3]), float(c))
                   for q, c in zip(xyxy, conf))
    return out


# ----------------------------------------------------------------------------------------------------
# CUDA-graph capture guards (see the module docstring)
# ----------------------------------------------------------------------------------------------------
_capture_lock = threading.Lock()
_tls = threading.local()


class _NoCapture:
    """Stands in for ``torch.cuda.graph(...)`` in a thread under ``no_graph_capture``: captures nothing."""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


class no_graph_capture:
    """In this thread, for the ``with`` block: TensorRT engines load without a CUDA graph."""

    def __enter__(self):
        self._prev = getattr(_tls, "no_capture", False)
        _tls.no_capture = True
        return self

    def __exit__(self, *exc):
        _tls.no_capture = self._prev
        return False


def install_capture_guards() -> bool:
    """Idempotent, process-wide: ``torch.cuda.graph`` captures default to ``thread_local`` (an explicit mode is
    kept) and capture nothing in a thread under ``no_graph_capture``, where ultralytics' TensorRT backend then
    keeps no graph.  True when installed (or already)."""
    try:
        import torch
    except ImportError:
        return False
    with _capture_lock:
        orig = getattr(torch.cuda, "graph", None)
        if orig is None:
            return False
        if not getattr(orig, "_wd_guarded", False):
            class _GuardedGraph(orig):            # type: ignore[misc, valid-type]
                _wd_guarded = True

                def __new__(cls, *args, **kwargs):
                    if getattr(_tls, "no_capture", False):
                        return _NoCapture()
                    return super().__new__(cls)

                def __init__(self, *args, **kwargs):
                    args = list(args)
                    if len(args) > 3:
                        if args[3] == "global":
                            args[3] = "thread_local"
                    elif kwargs.get("capture_error_mode", "global") == "global":
                        kwargs["capture_error_mode"] = "thread_local"
                    super().__init__(*args, **kwargs)

            _GuardedGraph.__name__ = getattr(orig, "__name__", "graph")
            torch.cuda.graph = _GuardedGraph
        try:
            from ultralytics.nn.backends.tensorrt import TensorRTBackend
        except Exception:  # noqa: BLE001 - an ultralytics without the graph path: nothing to hook
            return True
        if not getattr(TensorRTBackend.load_model, "_wd_wrapped", False):
            orig_load = TensorRTBackend.load_model

            def load_model(self, weight):
                orig_load(self, weight)
                if getattr(_tls, "no_capture", False) and getattr(self, "graph", None) is not None:
                    self.graph = None             # nothing was captured: run execute_v2
            load_model._wd_wrapped = True
            TensorRTBackend.load_model = load_model
        return True



# ----------------------------------------------------------------------------------------------------
# The runner
# ----------------------------------------------------------------------------------------------------
@dataclass
class CandidateRun:
    candidate: Candidate
    boxes: List[List[Box]] = field(default_factory=list)   # per frame, original px, conf >= LOW_CONF
    ms: List[float] = field(default_factory=list)          # inference (network + NMS + boxes to CPU) per frame
    prep_ms: List[float] = field(default_factory=list)     # upload + crop + enhance + letterbox per frame
    load_s: float = 0.0                                    # engine load + warm-up
    release_s: float = 0.0                                 # dropping the model (its destructor frees the engine)
    error: str = ""                                        # "" = ran; "cancelled"; or why it failed
    stored: bool = False                                   # the live setting's own boxes, kept by the tap: no run

    @property
    def frames(self) -> int:
        return len(self.boxes)

    @property
    def ms_p50(self) -> Optional[float]:
        return float(np.median(self.ms)) if self.ms else None


def default_loader(path: str):
    from ultralytics import YOLO
    return YOLO(str(path), task="pose")


class CandidateRunner:
    """Runs kept frames through one candidate at a time, on a private GPU pipeline."""

    def __init__(self, inp: SwipeInput, *, conf: float = LOW_CONF, device=None,
                 loader: Callable[[str], Any] = default_loader, bg_subtractor=None,
                 clock: Callable[[], float] = time.perf_counter):
        import torch
        from core.gpu_pipeline import GpuPipeline, GpuPipelineSettings
        self.inp = inp
        self.conf = float(conf)
        self.loader = loader
        self.clock = clock
        self._torch = torch
        self.device = torch.device(device) if device is not None else torch.device(
            "cuda" if torch.cuda.is_available() else "cpu")
        # the live subtractor's own copy: shares the reference tensor, keeps its own ratio / mismatch state
        # (apply_gpu updates them; the live display must not see the swipe's frames)
        self._bg = copy.copy(bg_subtractor) if inp.bg_subtract and bg_subtractor is not None else None
        self.pipe = GpuPipeline(GpuPipelineSettings(
            enhance_enabled=inp.enhance_enabled, enhance_lite=inp.enhance_lite,
            enhance_force=inp.enhance_force, brightness_threshold=float(inp.brightness_threshold),
            clahe_clip=inp.clahe_clip, clahe_grid=inp.clahe_grid, gamma=inp.gamma, greyscale=inp.greyscale,
            denoise_enabled=False, roi_enabled=inp.roi_enabled, roi_x=inp.roi_x, roi_y=inp.roi_y,
            roi_w=inp.roi_w, roi_h=inp.roi_h))
        # the live enhancer measures the brightness every 10th frame of a stream; kept frames are seconds
        # apart, so each frame is measured on its own (same gate, no stale value)
        self.pipe._enhancer._brightness_interval = 1

    # -- one frame ---------------------------------------------------------------------------------------
    def _upload(self, image: np.ndarray):
        """BGR uint8 (H, W, 3) or a mono (H, W) frame -> (1, 3, H, W) float RGB [0, 1] on the device, contiguous,
        as ``GpuPipeline._upload_to_gpu`` (a mono frame is the IDS frame's one channel, or a take's gray frame
        kept as one: R == G == B).  Both layouts are the same contiguous tensor, so a gray frame gives the same
        network input bit for bit kept with one channel or three -- the lite enhancement's arithmetic depends on
        the layout (2.98e-08 apart on 0.0003 % of the values from a strided upload, 26/11)."""
        torch = self._torch
        t = torch.from_numpy(np.ascontiguousarray(image)).to(self.device)
        if t.dim() == 2:
            return t.float().mul_(1.0 / 255.0).unsqueeze(0).unsqueeze(0).expand(1, 3, -1, -1).contiguous()
        return t.permute(2, 0, 1).unsqueeze(0).float().mul_(1.0 / 255.0).flip(1).contiguous()

    def prepare(self, image: np.ndarray, cand: Candidate):
        """(network tensor, letterbox info, (roi_x, roi_y)) for one kept frame at ``cand``'s input: the live
        path's ROI crop, enhancement and letterbox.  The tensor is contiguous: the lite enhancement of a 3-channel
        frame left it strided (26/11), and a TensorRT engine without a CUDA graph (every swipe engine, every
        engine on ultralytics 8.4.63) reads its raw data pointer."""
        from core.gpu_pipeline import GpuFrame
        h, w = image.shape[:2]
        pipe = self.pipe
        frame = GpuFrame(self._upload(image), is_bgr=False)
        if self._bg is not None:
            frame = GpuFrame(self._bg.apply_gpu(frame.tensor, self.inp.bg_sensitivity), is_bgr=False)
        roi = pipe._resolve_roi(w, h)
        frame = pipe._crop_to_roi(frame, roi)
        enhanced, _ = pipe._enhancer.enhance(frame, pipe.settings)
        tensor, lb = pipe._prepare_yolo_input(enhanced, int(cand.imgsz), cand.shape)
        return tensor.contiguous(), lb, (int(roi["x"]), int(roi["y"]))      # (a no-op when it already is)

    # -- one candidate -----------------------------------------------------------------------------------
    def run(self, images: Sequence[np.ndarray], cand: Candidate, engine_path: str, *,
            cancelled: Optional[Callable[[], bool]] = None, pace: Optional[Callable[[], None]] = None,
            on_frame: Optional[Callable[[int, int], None]] = None) -> CandidateRun:
        """Load ``engine_path``, warm it up, run every image, release it.  ``pace()`` is called before each
        inference (the app waits there for the live loop to finish a frame); ``cancelled()`` stops early."""
        install_capture_guards()
        try:
            from core.model_manager import ENGINE_LOAD_LOCK
        except Exception:  # noqa: BLE001 - no ultralytics (a CPU test with a fake model)
            ENGINE_LOAD_LOCK = threading.RLock()
        with ENGINE_LOAD_LOCK, no_graph_capture():
            return self._run(images, cand, engine_path, cancelled=cancelled, pace=pace, on_frame=on_frame)

    def load(self, engine_path: str, cand: Candidate, model=None) -> Tuple[Any, Any, float]:
        """(model, PoseRunner, seconds) for one engine, warmed at ``cand``'s input (``model``: the loader's
        object already made).  The caller holds ENGINE_LOAD_LOCK and ``no_graph_capture`` (``run`` does; the
        seamless switch's load and the background swipe's resident load too)."""
        from core.yolo_runner import PoseRunner
        imgsz = self.imgsz_arg(cand)
        th, tw = (imgsz if isinstance(imgsz, tuple) else (imgsz, imgsz))
        t0 = self.clock()
        if model is None:
            model = self.loader(engine_path)
        runner = PoseRunner(model)
        z = self._torch.full((1, 3, th, tw), 0.5, device=self.device)
        for _ in range(WARMUP_CALLS):
            runner(z, imgsz=imgsz, conf=self.conf, iou=self.inp.iou, half=self.inp.half)
        del z
        self._sync()
        return model, runner, self.clock() - t0

    @staticmethod
    def imgsz_arg(cand: Candidate):
        return (int(cand.shape[0]), int(cand.shape[1])) if cand.shape else int(cand.imgsz)

    def infer(self, images: Sequence[np.ndarray], cand: Candidate, runner, *,
              cancelled: Optional[Callable[[], bool]] = None, pace: Optional[Callable[[], None]] = None,
              on_frame: Optional[Callable[[int, int], None]] = None) -> CandidateRun:
        """Every image through an already loaded model (``runner``: its PoseRunner), timed; no load, no
        release.  ``pace()`` before each inference, ``cancelled()`` stops early."""
        run = CandidateRun(cand)
        imgsz = self.imgsz_arg(cand)
        for i, image in enumerate(images):
            if cancelled is not None and cancelled():
                run.error = "cancelled"
                break
            try:
                t1 = self.clock()
                tensor, lb, (rx, ry) = self.prepare(image, cand)
                self._sync()
                t2 = self.clock()
                if pace is not None:
                    pace()
                t3 = self.clock()
                results = runner(tensor, imgsz=imgsz, conf=self.conf, iou=self.inp.iou, half=self.inp.half)
                boxes = boxes_from_results(results, lb, rx, ry)       # .cpu(): waits for the GPU
                t4 = self.clock()
            except Exception as exc:  # noqa: BLE001 - this candidate is out, the swipe goes on
                run.error = f"inference failed: {type(exc).__name__}: {str(exc)[:160]}"
                break
            run.prep_ms.append((t2 - t1) * 1000.0)
            run.ms.append((t4 - t3) * 1000.0)
            run.boxes.append(boxes)
            if on_frame is not None:
                on_frame(i + 1, len(images))
        return run

    def _run(self, images, cand, engine_path, *, cancelled, pace, on_frame) -> CandidateRun:
        run = CandidateRun(cand)
        model = runner = None
        try:
            t0 = self.clock()
            try:
                # the engine deserialises at the warm-up: a live load, project switch or Apply waiting for
                # ENGINE_LOAD_LOCK waits one engine load at most
                model = self.loader(engine_path)
                if cancelled is not None and cancelled():
                    run.error = "cancelled"
                    return run
                model, runner, _ = self.load(engine_path, cand, model=model)
                load_s = self.clock() - t0
            except Exception as exc:  # noqa: BLE001 - a candidate that cannot load is skipped, not fatal
                run.error = f"load failed: {type(exc).__name__}: {str(exc)[:160]}"
                return run
            run = self.infer(images, cand, runner, cancelled=cancelled, pace=pace, on_frame=on_frame)
            run.load_s = load_s
            return run
        finally:
            if pace is not None and model is not None and run.error != "cancelled":
                pace()                                # the destructor's stall right after a live frame
            t5 = self.clock()
            runner = model = None                     # frees the engine (refcount)
            run.release_s = self.clock() - t5

    def _sync(self) -> None:
        if self.device.type == "cuda":
            self._torch.cuda.synchronize(self.device)

    def finalize(self, pace: Optional[Callable[[], None]] = None, empty_cache: bool = True) -> Dict[str, float]:
        """End of the swipe: one full collection and one ``empty_cache`` (the swipe's cached blocks), right
        after a live frame, under the engine-load lock.  ``empty_cache=False`` (a cancelled or dropped swipe:
        the live model may be reloading, and its first RUN frame captures a graph outside any lock): the
        collection only, no CUDA call -- the candidates' engines are already freed (refcount, under the lock)
        and the cached blocks stay in torch's cache for the live loop."""
        try:
            from core.model_manager import ENGINE_LOAD_LOCK
        except Exception:  # noqa: BLE001
            ENGINE_LOAD_LOCK = threading.RLock()
        t0 = t1 = t2 = self.clock()
        with ENGINE_LOAD_LOCK:
            if pace is not None and empty_cache:
                pace()
            t0 = self.clock()
            gc.collect()
            t1 = t2 = self.clock()
            if empty_cache and self.device.type == "cuda":
                try:
                    self._torch.cuda.empty_cache()
                except Exception:  # noqa: BLE001
                    pass
                t2 = self.clock()
        return {"gc_s": t1 - t0, "empty_cache_s": t2 - t1}


# ----------------------------------------------------------------------------------------------------
# The whole swipe: every candidate, the tallies, the verdict
# ----------------------------------------------------------------------------------------------------
@dataclass
class SwipeOutcome:
    current: Candidate
    result: Optional[SwipeResult]
    runs: List[CandidateRun] = field(default_factory=list)
    skipped: List[Tuple[Candidate, str]] = field(default_factory=list)
    frames: int = 0                 # kept frames the swipe ran on
    lost_frames: int = 0            # ... of which the live setting left a dancer bare
    timing: Dict[str, float] = field(default_factory=dict)   # collect_s, load_s, infer_s, prep_s, total_s
    cancelled: bool = False
    live_error: str = ""            # the live setting's own re-run failed: no verdict (nothing to compare with)
    timed_out: bool = False         # the run phase reached its ceiling (``run_swipe(max_s=)``): no verdict

    def run_for(self, cand: Candidate) -> Optional[CandidateRun]:
        return next((r for r in self.runs if r.candidate == cand), None)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-ready: the verdict, one row per candidate (best first), what was skipped, the timing."""
        res = self.result
        rows = []
        seen = set()
        for row in (res.rows if res is not None else []):
            run = self.run_for(row.candidate)
            seen.add(row.candidate)
            rows.append({**_jsonable_fields(row, skip=("candidate",)),
                         "label": row.candidate.label, "model": row.candidate.model,
                         "imgsz": row.candidate.imgsz, "shape": list(row.candidate.shape or ()) or None,
                         "load_s": round(run.load_s, 2) if run else None,
                         "frames_run": run.frames if run else 0})
        for run in self.runs:
            if run.candidate not in seen:
                rows.append({"label": run.candidate.label, "model": run.candidate.model,
                             "imgsz": run.candidate.imgsz,
                             "shape": list(run.candidate.shape or ()) or None,
                             "ok": False, "note": run.error or "not scored",
                             "load_s": round(run.load_s, 2), "frames_run": run.frames,
                             "ms_p50": run.ms_p50})
        out = {"current": self.current.label, "frames": self.frames, "lost_frames": self.lost_frames,
               "live_error": self.live_error, "timed_out": self.timed_out,
               "rows": rows,
               "skipped": [{"label": c.label, "why": w, "text": f"{c.label} not tried: {w}"}
                           for c, w in self.skipped],
               "timing": {k: round(float(v), 2) for k, v in self.timing.items()},
               "cancelled": self.cancelled}
        if res is not None:
            out.update(_jsonable_fields(res, skip=("current", "winner", "rows")))
            out["winner"] = res.winner.label if res.winner is not None else None
            out["winner_setting"] = ({"model": res.winner.model, "imgsz": res.winner.imgsz,
                                      "shape": list(res.winner.shape or ()) or None}
                                     if res.winner is not None else None)
            out["reason"] = res.reason
            conf = recommended_conf(res)
            if conf is not None:
                out["recommended_conf"] = conf
        return out


def _jsonable(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return round(v, 4) if math.isfinite(v) else None
    if isinstance(v, (np.floating, np.integer)):
        return _jsonable(v.item())
    if isinstance(v, Candidate):
        return v.label
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if is_dataclass(v):
        return _jsonable_fields(v)
    return str(v)


def _jsonable_fields(obj, skip: Sequence[str] = ()) -> Dict[str, Any]:
    """Every dataclass field of ``obj`` but ``skip``, JSON-ready: whatever ``core.swipe`` adds to its rows
    and result (a guard figure, a recommended confidence) travels to the operator without a change here."""
    return {f.name: _jsonable(getattr(obj, f.name)) for f in fields(obj) if f.name not in skip}


def recommended_conf(result) -> Optional[float]:
    """The confidence the verdict recommends for its winner (``SwipeResult.recommended_conf``), or None.
    Only that field: ``SwipeParams.conf`` is the referee's own threshold, not the show's."""
    v = getattr(result, "recommended_conf", None)
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 < float(v) < 1.0:
        return float(v)
    return None


def _accepts(fn, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def live_run_from_boxes(current: Candidate, boxes: Sequence[Sequence[Box]]) -> CandidateRun:
    """The live setting's row without running its engine: the boxes the live pass made on each kept frame
    (``SwipeFrame.boxes``: cut at the show's confidence, after the operator's excluded cells).  ``ms`` empty: the
    referee's cost bar then takes ``Candidate.cost`` for every row."""
    return CandidateRun(current, boxes=[list(b) for b in boxes], stored=True)


def score_runs(observations: Sequence[Any], runs: Sequence[CandidateRun], current: Candidate,
               params: SwipeParams, *, lost: Optional[Sequence[bool]] = None, lost_share: Optional[float] = None,
               exclusion: Optional[ExclusionCells] = None) -> Optional[SwipeResult]:
    """The tallies and ``decide`` for runs on the same frames -- one code path for the operator's swipe and the
    background one.  A run that failed or stopped part-way is left out (the live one's: None, no verdict);
    the excluded cells are applied once (a stored live row already has them)."""
    p = params
    tallies: Dict[Candidate, Tally] = {}
    n = len(observations)
    for run in runs:
        if run.error or len(run.boxes) < n:
            if run.candidate == current:
                return None
            continue
        t = Tally()
        flags = list(lost) if lost is not None and _accepts(t.add, "lost") else [None] * n
        ms = list(run.ms) if len(run.ms) == len(run.boxes) else [None] * len(run.boxes)
        for obs, boxes, m, fl in zip(observations, run.boxes, ms, flags):
            if exclusion is not None and not run.stored:
                boxes = exclusion.filter(boxes)
            if fl is None:
                t.add(score_frame(obs, boxes, p), m)
            else:
                t.add(score_frame(obs, boxes, p), m, lost=bool(fl))
        tallies[run.candidate] = t
    if current not in tallies:
        return None
    kw = {"lost_share": lost_share} if lost_share is not None and _accepts(decide, "lost_share") else {}
    return decide(tallies, current, p, **kw)


def run_swipe(images: Sequence[np.ndarray], observations: Sequence[Any], plan: CandidatePlan,
              current: Candidate, runner: CandidateRunner, engine_path: Callable[[Candidate], str],
              params: Optional[SwipeParams] = None, *,
              progress: Optional[Callable[[str, float], None]] = None,
              cancelled: Optional[Callable[[], bool]] = None, pace: Optional[Callable[[], None]] = None,
              lost_frames: int = 0, lost: Optional[Sequence[bool]] = None,
              lost_share: Optional[float] = None, exclusion: Optional[ExclusionCells] = None,
              max_s: Optional[float] = None, live_boxes: Optional[Sequence[Sequence[Box]]] = None,
              clock: Callable[[], float] = time.perf_counter) -> SwipeOutcome:
    """Every planned candidate on the same frames, one engine at a time; the tallies; ``decide``.

    ``live_boxes``: the live setting's own boxes on each frame (``SwipeFrame.boxes``) -- its row is scored from
    them and its engine is not loaded nor re-run (``live_run_from_boxes``); None re-runs it like a candidate.
    ``lost`` (per frame: the live setting left a dancer bare) and ``lost_share`` (the collector's estimate of
    how often it does) go to ``Tally.add`` / ``decide`` when ``core.swipe`` takes them (for the record).
    ``exclusion``: boxes in the operator's excluded cells are dropped before scoring (``ExclusionCells``).
    ``max_s``: a ceiling on the whole run (``core.config.SWIPE_RUN_MAX_S`` in the app): past it the run stops at
    the next frame and gives no verdict (``timed_out``) rather than running on.

    A verdict stands only on every setting scored on every frame: a setting whose run stopped part-way without
    being cancelled is left out like one that failed (the live setting's: no verdict)."""
    p = params or SwipeParams()
    out = SwipeOutcome(current, None, skipped=list(plan.skipped), frames=len(images), lost_frames=lost_frames)
    t0 = clock()
    outer = cancelled

    def cancelled() -> bool:                    # noqa: F811 - the caller's, and the ceiling
        if max_s is not None and clock() - t0 > float(max_s):
            out.timed_out = True
            return True
        return outer is not None and outer()
    stored = live_boxes is not None and current in plan.candidates
    if stored:
        out.runs.append(live_run_from_boxes(current, live_boxes))
    to_run = [c for c in plan.candidates if not (stored and c == current)]
    n = len(to_run)
    fin: Dict[str, float] = {}
    try:
        for k, cand in enumerate(to_run):
            if cancelled():
                out.cancelled = not out.timed_out
                break
            if progress is not None:
                progress(f"trying {cand.label} ({k + 1}/{n})", k / max(1, n))
            run = runner.run(images, cand, engine_path(cand), cancelled=cancelled, pace=pace)
            out.runs.append(run)
            if run.error == "cancelled":
                out.cancelled = not out.timed_out
                break
            if not run.error and len(run.boxes) < len(images):
                run.error = f"ran {len(run.boxes)} / {len(images)} frames"
            if run.error and cand == current:   # nothing to compare the others with: stop here
                out.live_error = run.error
                break
    finally:
        if hasattr(runner, "finalize"):
            fin = runner.finalize(pace, empty_cache=not out.cancelled)
    out.timing = {"load_s": sum(r.load_s for r in out.runs),
                  "infer_s": sum(sum(r.ms) for r in out.runs) / 1000.0,
                  "prep_s": sum(sum(r.prep_ms) for r in out.runs) / 1000.0,
                  "release_s": sum(r.release_s for r in out.runs) + sum(fin.values()),
                  "run_s": clock() - t0}
    if not out.cancelled and not out.live_error and not out.timed_out:
        if not any(r.candidate == current and not r.error for r in out.runs):
            out.live_error = "not run"
        else:
            out.result = score_runs(observations, out.runs, current, p, lost=lost, lost_share=lost_share,
                                    exclusion=exclusion)
    return out
