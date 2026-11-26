"""The settings swipe's candidate runner (Plan Cordée P5): the kept frames through other settings.

``core.swipe`` decides (the referee, the collector, the verdict); this module produces what it judges: for
each candidate setting (model x network input), the boxes the live path WOULD have produced on the kept
frames -- same ROI crop, same enhancement, letterbox to the candidate's input, same decode back to original
camera pixels -- without touching the live pipeline's state:

* its own ``GpuPipeline`` (own enhancer: no temporal denoise carried over, the brightness gate measured on
  each frame instead of every 10th), its own model instance and ``PoseRunner``;
* boxes kept down to ``LOW_CONF`` (0.02) so the scoring chooses its own threshold;
* one engine at a time: load, warm up, time each inference, release (``torch.cuda.empty_cache``) before the
  next -- the show laptop shares its GPU with TouchDesigner.  Never builds an engine.

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
PRUNE_NET_PX = 200.0         # the dancer already this tall at the network: larger inputs are not tried
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
    skipped: List[Tuple[Candidate, str]] = field(default_factory=list)   # not run, and why

    def skipped_text(self) -> List[str]:
        return [f"{c.label} not tried: {why}" for c, why in self.skipped]


def plan_candidates(current: Candidate, roi_w: int, roi_h: int,
                    engine_exists: Callable[[Candidate], bool], *,
                    models: Sequence[str] = SWIPE_MODELS, rects: Sequence[Shape] = RECT_PRESETS,
                    dancer_net_px: Optional[float] = None, prune_px: float = PRUNE_NET_PX,
                    only: Optional[Iterable[Candidate]] = None) -> CandidatePlan:
    """{models} x {the project's square, the rectangles that suit this ROI}, the live setting always in.

    A rectangle suits the ROI when ``pick_shape`` would take it over the square of its pixel count (a portrait
    ROI for the portrait presets).  When the dancer already reaches the network at ``prune_px`` or more at the
    live input (``dancer_net_px``), inputs that show her larger are not tried.  A candidate without its engine
    is skipped, never built.  ``only`` restricts the set (the live setting stays)."""
    sizes: List[Tuple[int, Optional[Shape]]] = [(int(current.imgsz), None)]
    if roi_w > 0 and roi_h > 0:
        for r in rects:
            if pick_shape(roi_w, roi_h, [tuple(r)]) is not None:
                sizes.append((int(current.imgsz), (int(r[0]), int(r[1]))))
    wanted: List[Candidate] = [Candidate(m, sz, sh) for m in models for sz, sh in sizes]
    if only is not None:
        allowed = set(only)
        wanted = [c for c in wanted if c in allowed] + [c for c in allowed if c not in wanted]
    if current in wanted:
        wanted.remove(current)
    plan = CandidatePlan([current])
    cur_scale = candidate_scale(current, roi_w, roi_h)
    prune = dancer_net_px is not None and dancer_net_px >= prune_px and cur_scale > 0
    for c in wanted:
        if prune and candidate_scale(c, roi_w, roi_h) > cur_scale * 1.01:
            plan.skipped.append((c, f"the dancers already reach the network at ~{dancer_net_px:.0f} px "
                                    f"at {current.label}"))
            continue
        if not engine_exists(c):
            plan.skipped.append((c, f"its engine is not built on this machine ({c.engine_file})"))
            continue
        plan.candidates.append(c)
    if not engine_exists(current):
        plan.skipped.insert(0, (current, f"its engine is not built on this machine ({current.engine_file}); "
                                         "the live setting must be re-run"))
        plan.candidates.remove(current)
    return plan


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
        aside: kept frames are 0.5 s apart, there is no stream to smooth)."""
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
    release_s: float = 0.0
    error: str = ""                                        # "" = ran; "cancelled"; or why it failed

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
        self._bg = bg_subtractor if inp.bg_subtract else None
        self.pipe = GpuPipeline(GpuPipelineSettings(
            enhance_enabled=inp.enhance_enabled, enhance_lite=inp.enhance_lite,
            enhance_force=inp.enhance_force, brightness_threshold=float(inp.brightness_threshold),
            clahe_clip=inp.clahe_clip, clahe_grid=inp.clahe_grid, gamma=inp.gamma, greyscale=inp.greyscale,
            denoise_enabled=False, roi_enabled=inp.roi_enabled, roi_x=inp.roi_x, roi_y=inp.roi_y,
            roi_w=inp.roi_w, roi_h=inp.roi_h))
        # the live enhancer measures the brightness every 10th frame of a stream; kept frames are 0.5 s
        # apart, so each frame is measured on its own (same gate, no stale value)
        self.pipe._enhancer._brightness_interval = 1

    # -- one frame ---------------------------------------------------------------------------------------
    def _upload(self, image: np.ndarray):
        """BGR uint8 (H, W, 3) or a mono (H, W) frame -> (1, 3, H, W) float RGB [0, 1] on the device, as
        ``GpuPipeline._upload_to_gpu`` (a mono frame is the IDS frame's one channel: R == G == B)."""
        torch = self._torch
        t = torch.from_numpy(np.ascontiguousarray(image)).to(self.device)
        if t.dim() == 2:
            return t.float().mul_(1.0 / 255.0).unsqueeze(0).unsqueeze(0).expand(1, 3, -1, -1).contiguous()
        return t.permute(2, 0, 1).unsqueeze(0).float().mul_(1.0 / 255.0).flip(1)

    def prepare(self, image: np.ndarray, cand: Candidate):
        """(network tensor, letterbox info, (roi_x, roi_y)) for one kept frame at ``cand``'s input: the live
        path's ROI crop, enhancement and letterbox."""
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
        return tensor, lb, (int(roi["x"]), int(roi["y"]))

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

    def _run(self, images, cand, engine_path, *, cancelled, pace, on_frame) -> CandidateRun:
        from core.yolo_runner import PoseRunner
        torch = self._torch
        run = CandidateRun(cand)
        imgsz = (int(cand.shape[0]), int(cand.shape[1])) if cand.shape else int(cand.imgsz)
        th, tw = (imgsz if isinstance(imgsz, tuple) else (imgsz, imgsz))
        model = runner = None
        try:
            t0 = self.clock()
            try:
                model = self.loader(engine_path)
                runner = PoseRunner(model)
                z = torch.full((1, 3, th, tw), 0.5, device=self.device)
                for _ in range(WARMUP_CALLS):
                    runner(z, imgsz=imgsz, conf=self.conf, iou=self.inp.iou, half=self.inp.half)
                del z
                self._sync()
            except Exception as exc:  # noqa: BLE001 - a candidate that cannot load is skipped, not fatal
                run.error = f"load failed: {type(exc).__name__}: {str(exc)[:160]}"
                return run
            run.load_s = self.clock() - t0
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
                    results = runner(tensor, imgsz=imgsz, conf=self.conf, iou=self.inp.iou,
                                     half=self.inp.half)
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
        finally:
            t5 = self.clock()
            runner = model = None
            self.release()
            run.release_s = self.clock() - t5

    def _sync(self) -> None:
        if self.device.type == "cuda":
            self._torch.cuda.synchronize(self.device)

    def release(self) -> None:
        gc.collect()
        if self.device.type == "cuda":
            try:
                self._torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass


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
    """The confidence threshold the verdict recommends for its winner, if ``core.swipe`` gives one."""
    for name in ("recommended_conf", "conf", "confidence"):
        v = getattr(result, name, None)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 < float(v) < 1.0:
            return float(v)
    return None


def _accepts(fn, name: str) -> bool:
    try:
        return name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


def run_swipe(images: Sequence[np.ndarray], observations: Sequence[Any], plan: CandidatePlan,
              current: Candidate, runner: CandidateRunner, engine_path: Callable[[Candidate], str],
              params: Optional[SwipeParams] = None, *,
              progress: Optional[Callable[[str, float], None]] = None,
              cancelled: Optional[Callable[[], bool]] = None, pace: Optional[Callable[[], None]] = None,
              lost_frames: int = 0, lost: Optional[Sequence[bool]] = None,
              lost_share: Optional[float] = None,
              clock: Callable[[], float] = time.perf_counter) -> SwipeOutcome:
    """Every planned candidate on the same frames, one engine at a time; the tallies; ``decide``.

    ``lost`` (per frame: the live setting left a dancer bare) and ``lost_share`` (the collector's estimate of
    how often it does) go to ``Tally.add`` / ``decide`` when ``core.swipe`` takes them (its frame weighting)."""
    p = params or SwipeParams()
    out = SwipeOutcome(current, None, skipped=list(plan.skipped), frames=len(images), lost_frames=lost_frames)
    tallies: Dict[Candidate, Tally] = {}
    t0 = clock()
    n = len(plan.candidates)
    for k, cand in enumerate(plan.candidates):
        if cancelled is not None and cancelled():
            out.cancelled = True
            break
        if progress is not None:
            progress(f"trying {cand.label} ({k + 1}/{n})", k / max(1, n))
        run = runner.run(images, cand, engine_path(cand), cancelled=cancelled, pace=pace)
        out.runs.append(run)
        if run.error == "cancelled":
            out.cancelled = True
            break
        if run.error:
            continue
        t = Tally()
        flags = list(lost) if lost is not None and _accepts(t.add, "lost") else [None] * len(run.boxes)
        for obs, boxes, ms, fl in zip(observations, run.boxes, run.ms, flags):
            if fl is None:
                t.add(score_frame(obs, boxes, p), ms)
            else:
                t.add(score_frame(obs, boxes, p), ms, lost=bool(fl))
        tallies[cand] = t
    out.timing = {"load_s": sum(r.load_s for r in out.runs),
                  "infer_s": sum(sum(r.ms) for r in out.runs) / 1000.0,
                  "prep_s": sum(sum(r.prep_ms) for r in out.runs) / 1000.0,
                  "release_s": sum(r.release_s for r in out.runs),
                  "run_s": clock() - t0}
    if not out.cancelled and tallies:
        kw = {"lost_share": lost_share} if lost_share is not None and _accepts(decide, "lost_share") else {}
        out.result = decide(tallies, current, p, **kw)
    return out
