"""
Frame processing pipeline for WallDance.
Handles enhancement, YOLO inference, duplicate filtering, tracking, and OSC output.
Supports full GPU pipeline for zero-copy processing (see gpu_pipeline.py).
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from collections import namedtuple
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from ultralytics import YOLO

from core.config import (
    CV2_NUM_THREADS,
    BG_SUBTRACT_ENABLED,
    BG_SUBTRACT_SENSITIVITY,
    BRIGHTNESS_THRESHOLD,
    KEYPOINT_CONFIDENCE,
    PERSON_HEIGHT_MAX_RATIO,
    PERSON_HEIGHT_MIN_RATIO,
    TRACKER_MAX_AGE,
    YOLO_IOU_THRESHOLD,
    USE_GPU_PATH,
    SHADOW_QUALITY_MIN_KEYPOINTS,
    SHADOW_QUALITY_MIN_CONFIDENCE,
    SHADOW_PROXIMITY_RATIO,
    MOTION_BRIDGE_ENABLED,
    MOTION_BRIDGE_MOG2_LEARN_RATE,
    MOTION_BRIDGE_MOG2_VAR_THRESHOLD,
    MOTION_BRIDGE_SENSITIVITY,
    BOX_SIZE_OUTPUT_SMOOTHING,
    IDENTITY_SLOTS_ENABLED,
    IDENTITY_SLOTS_MAX_DANCERS,
    IDENTITY_SLOTS_STABILITY,
    IDENTITY_SLOTS_COAST_S, IDENTITY_SLOTS_STATIC_GUARD,
    IDENTITY_SLOTS_STATIC_RELEASE_S, IDENTITY_SLOTS_FILTER_INPUT, IDENTITY_SLOTS_SMART_HOLD,
    BELT_STATIC_EVERY_N, BELT_STATIC_ALPHA, BELT_STATIC_ON,
    FOREGROUND_ENABLED, HEIGHT_GUARD, HEIGHT_GUARD_MAX_SPREAD, HEIGHT_GUARD_MIN_SAMPLES,
    HEIGHT_GUARD_OUTSIDE, HEIGHT_GUARD_FOLLOW_S, HEIGHT_GUARD_FOLLOW_MIN_SECONDS, HEIGHT_GUARD_FOLLOW_RATIO,
    HEIGHT_GUARD_FOLLOW_KPT_CONF,
    IDENTITY_SLOTS_USE_IR_BELT,
    OSC_SEND_STATE,
    TrackingMode,
    MOTION_FIRST_BLOB_OVERLAP_RATIO,
    MOTION_FIRST_ASPECT_RANGE,
    MOTION_FIRST_INCLUDE_SHADOWS,
    MOTION_CROSSVAL_ENABLED,
    MOTION_CROSSVAL_EMA_ALPHA,
    MOTION_CROSSVAL_CELL_RATIO,
    MOTION_CROSSVAL_EXISTING_TRACK_BYPASS,
    MOTION_CROSSVAL_BYPASS_MAX_AGE,
    MOTION_FIRST_MOG2_LEARN_RATE,
    MOTION_LOWLIGHT_LUMA_THRESHOLD,
    MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT,
    MOTION_CROSSVAL_BYPASS_MIN_WARMUP,
    MOTION_CROSSVAL_CONFIDENT_MIN_KPTS,
    MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO,
    MOTION_CROSSVAL_CONFIDENT_MIN_CONF,
)
from core.background import BackgroundSubtractor
from core.enhancer import ImageEnhancer, TORCH_CUDA_AVAILABLE
from core.motion_detector import MotionDetector
from core.motion_model import MotionModel
from core.calibration import ExclusionMaskBuilder
from core.osc_output import OSCSender
from core.output_smoother import OutputSmoother, SmootherInput
from core.identity_slots import (IdentitySlots, SlotParams, STATE_BELT, STATE_LIVE,
                                 candidate_from_track)
from core.tracker import DancerTrack, DancerTracker
from core.yolo_runner import PoseRunner

# Import GPU pipeline (optional, for zero-copy GPU path)
try:
    from core.gpu_pipeline import GpuPipeline, GpuPipelineSettings, CUDA_AVAILABLE as GPU_CUDA_AVAILABLE, KORNIA_AVAILABLE
    GPU_PIPELINE_AVAILABLE = GPU_CUDA_AVAILABLE and KORNIA_AVAILABLE
except ImportError:
    GPU_PIPELINE_AVAILABLE = False
    GpuPipeline = None
    GpuPipelineSettings = None

# Import torch for type hints
try:
    import torch
    TORCH_AVAILABLE = True
except ImportError:
    torch = None
    TORCH_AVAILABLE = False
    GpuPipeline = None
    GpuPipelineSettings = None

# PERF-1: `import ultralytics` (above) ran cv2.setNumThreads(0) process-wide,
# leaving the CPU motion feed single-threaded. Restore a multi-threaded OpenCV
# (motion outputs are bit-identical: tests/test_cv2_threads_bit_identity.py).
cv2.setNumThreads(CV2_NUM_THREADS)

_RawDet = namedtuple("_RawDet", "frames_since_skeleton confidence bbox")   # height guard sample


@dataclass
class ProcessingSettings:
    confidence: float
    imgsz: int
    use_fp16: bool
    enhance_enabled: bool
    enhance_lite: bool
    enhance_force: bool  # Force enhancement even when brightness > threshold
    person_height_px: int
    motion_sensitivity: float = MOTION_BRIDGE_SENSITIVITY
    person_height_min_ratio: float = PERSON_HEIGHT_MIN_RATIO
    person_height_max_ratio: float = PERSON_HEIGHT_MAX_RATIO
    # P3 Stage 3a scored-gate thresholds (θ_s = strong-skeleton, θ_m = frame-diff
    # motion).  Exposed here so the Stage-4 known-N calibration can tune them.
    crossval_skel_min_kpts: int = MOTION_CROSSVAL_CONFIDENT_MIN_KPTS
    crossval_skel_min_conf: float = MOTION_CROSSVAL_CONFIDENT_MIN_CONF
    crossval_motion_min_ratio: float = MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO
    brightness_threshold: int = 60  # Auto-bypass threshold (0-255)
    denoise_strength: float = 0.0   # Temporal denoising (0.0-1.0)
    greyscale: bool = False         # Convert to greyscale (mono camera simulation)
    osc_enabled: bool = True
    # Output box-clamp (Track X, OSC_CONTRACT §B.1): while a track is sustained
    # without a fresh YOLO skeleton, report a box of the last-known YOLO size at
    # the smoothed centroid.  OUTPUT-ONLY — applied at the ScaledTrack/OSC/preview
    # boundary; never mutates DancerTrack.bbox.  Locked default ON.
    box_clamp_enabled: bool = True
    # Output smoothing depth / latency selector (Track X, OSC_CONTRACT §B).  This
    # is the SINGLE control over the one /walldance/dancer/* output stream:
    #   L = 1 → causal / live: zero look-ahead, light box-size EMA de-jitter.
    #   L > 1 → fixed-lag RTS-smoothed stream, released L frames late (acausal),
    #           with retroactive bridge correction.  No second stream — the
    #           lagged signal IS the /walldance/dancer/* stream at L>1.
    # OUTPUT-ONLY (never touches the tracker/detector → replay goldens unaffected).
    output_smoothing_l: int = 1
    # Identity-slot output layer (CONT-6, core/identity_slots.py): N stable
    # OSC ids over the tracker's churning track ids.  OUTPUT-ONLY: process()
    # still returns the tracker's reported tracks (replays/goldens unchanged);
    # the slot stream is what OSC sends (``FrameProcessor.last_emitted``).
    identity_slots_enabled: bool = IDENTITY_SLOTS_ENABLED
    max_dancers: int = IDENTITY_SLOTS_MAX_DANCERS
    stability: float = IDENTITY_SLOTS_STABILITY
    coast_s: float = IDENTITY_SLOTS_COAST_S
    static_ghost_guard: bool = IDENTITY_SLOTS_STATIC_GUARD
    static_release_s: float = IDENTITY_SLOTS_STATIC_RELEASE_S
    slot_filter_input: str = IDENTITY_SLOTS_FILTER_INPUT
    smart_hold: bool = IDENTITY_SLOTS_SMART_HOLD
    use_ir_belt: bool = IDENTITY_SLOTS_USE_IR_BELT
    belt_backing: bool = True                   # belt-only cap restarts on evidence (A/B switch)
    entry_min_travel_h: float = 0.0             # a NEW dancer must have moved this x h (0 = off)
    auto_height: bool = False                   # learn person_height_px from confident full skeletons
    height_guard: bool = HEIGHT_GUARD           # re-measure a person_height_px whose gate drops the dancers
    fg_enabled: bool = FOREGROUND_ENABLED       # use the clean plate when one is loaded
    fg_plate_path: str = ""                     # absolute path of the plate (.npz), "" = none
    osc_send_state: bool = OSC_SEND_STATE
    use_gpu_path: bool = USE_GPU_PATH  # Enable GPU frame buffer
    bg_subtract_enabled: bool = BG_SUBTRACT_ENABLED  # Static BG subtraction
    bg_subtract_sensitivity: int = BG_SUBTRACT_SENSITIVITY  # Threshold 0-255
    roi_enabled: bool = False
    roi_x: int = 0
    roi_y: int = 0
    roi_w: int = 0
    roi_h: int = 0


@dataclass
class ScaledTrack:
    track_id: int
    keypoints: np.ndarray
    confidence: np.ndarray
    bbox: np.ndarray
    history: List[np.ndarray]
    velocity: np.ndarray
    smoothed_centroid: Optional[np.ndarray] = None  # EMA-smoothed, jitter-free
    is_bridged: bool = False  # True when track is in motion-bridge mode
    box_conf: Optional[float] = None  # YOLO box conf of the detection that fed
                                      # this track THIS frame (None when bridged
                                      # or cold-blob fed) — calib2 seed (⑤a)
    # Output-only signals for the fixed-lag/RTS output smoother (Track X §2).
    # Copied from read-only tracker scalars at finalize — copying them never
    # touches tracker state, so replay goldens stay byte-identical.
    #   frames_since_skeleton == 0  ⟺  a real skeleton fed this track this frame
    #                                   (is_real_skeleton); grows on bridge/miss.
    #   centroid_raw = the tracker KF estimate kf.x[:2] (the RAW, non-EMA centroid,
    #                  distinct from the EMA smoothed_centroid), in ORIGINAL space
    #                  — the de-jitter input that avoids cascaded filtering.
    frames_since_skeleton: Optional[int] = None
    centroid_raw: Optional[np.ndarray] = None
    # Output-only evidence copied from read-only tracker scalars at finalize,
    # for the identity-slot output layer's establishment gate (CONT-6):
    # cumulative matches, frames alive, misses since the last update, and what
    # fed the track this frame (yolo / synthetic / bridge / coast).
    hits: Optional[int] = None
    age: Optional[int] = None
    time_since_update: Optional[int] = None
    feed_src: Optional[str] = None
    # Identity-slot output (CONT-6): set on the emitted slot tracks only
    # (track_id = slot id): live / belt / coasting, seconds in that state.
    slot_state: Optional[str] = None
    slot_age_s: Optional[float] = None


class _LetterboxMotionProxy:
    """Proxy that scales blob coords from original to letterboxed space.

    The tracker works in letterboxed YOLO coordinates (GPU path), but
    the MotionDetector runs on the original-resolution frame.  This
    proxy transparently scales detect() results.
    """

    def __init__(self, detector, lb_scale: float, pad_x: int, pad_y: int):
        self._detector = detector
        self._lb_scale = lb_scale
        self._pad_x = pad_x
        self._pad_y = pad_y

    def _to_local(self, x, y):
        """Letterbox → ROI-local coords."""
        inv = 1.0 / self._lb_scale if self._lb_scale != 0 else 1.0
        return (x - self._pad_x) * inv, (y - self._pad_y) * inv

    def _to_letterbox(self, x, y):
        """ROI-local → letterbox coords."""
        return x * self._lb_scale + self._pad_x, y * self._lb_scale + self._pad_y

    def detect(self, person_height: int, **detect_kwargs):
        # Convert person_height from letterbox to ROI-local scale
        original_ph = max(1, int(person_height / self._lb_scale)) if self._lb_scale > 0 else person_height
        blobs = self._detector.detect(original_ph, **detect_kwargs)
        if blobs and (self._lb_scale != 1.0 or self._pad_x or self._pad_y):
            for blob in blobs:
                blob.bbox[0] = blob.bbox[0] * self._lb_scale + self._pad_x
                blob.bbox[1] = blob.bbox[1] * self._lb_scale + self._pad_y
                blob.bbox[2] *= self._lb_scale
                blob.bbox[3] *= self._lb_scale
                blob.centroid[0] = blob.centroid[0] * self._lb_scale + self._pad_x
                blob.centroid[1] = blob.centroid[1] * self._lb_scale + self._pad_y
        return blobs

    def extract_local_motion_blob(self, x: float, y: float, w: float, h: float,
                                  target_centroid=None, **kwargs):
        inv_scale = 1.0 / self._lb_scale if self._lb_scale != 0 else 1.0
        ox = (x - self._pad_x) * inv_scale
        oy = (y - self._pad_y) * inv_scale
        ow = w * inv_scale
        oh = h * inv_scale
        target = None
        if target_centroid is not None:
            target = np.array([
                (target_centroid[0] - self._pad_x) * inv_scale,
                (target_centroid[1] - self._pad_y) * inv_scale,
            ], dtype=np.float64)
        blob, motion_ratio = self._detector.extract_local_motion_blob(
            ox, oy, ow, oh, target_centroid=target, **kwargs)
        if blob is not None and (self._lb_scale != 1.0 or self._pad_x or self._pad_y):
            blob.bbox[0] = blob.bbox[0] * self._lb_scale + self._pad_x
            blob.bbox[1] = blob.bbox[1] * self._lb_scale + self._pad_y
            blob.bbox[2] *= self._lb_scale
            blob.bbox[3] *= self._lb_scale
            blob.centroid[0] = blob.centroid[0] * self._lb_scale + self._pad_x
            blob.centroid[1] = blob.centroid[1] * self._lb_scale + self._pad_y
        return blob, motion_ratio

    def motion_ratio_in_bbox(self, x: float, y: float, w: float, h: float,
                             **kwargs) -> float:
        inv_scale = 1.0 / self._lb_scale if self._lb_scale != 0 else 1.0
        return self._detector.motion_ratio_in_bbox(
            (x - self._pad_x) * inv_scale,
            (y - self._pad_y) * inv_scale,
            w * inv_scale,
            h * inv_scale,
            **kwargs,
        )

    def mask_stats_in_region(self, x: float, y: float, w: float, h: float) -> dict:
        """Query mask foreground stats for a letterbox-space region."""
        inv_scale = 1.0 / self._lb_scale if self._lb_scale != 0 else 1.0
        return self._detector.mask_stats_in_region(
            (x - self._pad_x) * inv_scale,
            (y - self._pad_y) * inv_scale,
            w * inv_scale,
            h * inv_scale,
        )

    def frame_diff_blob_in_bbox(self, x: float, y: float, w: float, h: float,
                                target_centroid=None, **kwargs):
        inv_scale = 1.0 / self._lb_scale if self._lb_scale != 0 else 1.0
        ox = (x - self._pad_x) * inv_scale
        oy = (y - self._pad_y) * inv_scale
        ow = w * inv_scale
        oh = h * inv_scale
        target = None
        if target_centroid is not None:
            target = np.array([
                (target_centroid[0] - self._pad_x) * inv_scale,
                (target_centroid[1] - self._pad_y) * inv_scale,
            ], dtype=np.float64)
        blob, ratio = self._detector.frame_diff_blob_in_bbox(
            ox, oy, ow, oh, target_centroid=target, **kwargs)
        if blob is not None and (self._lb_scale != 1.0 or self._pad_x or self._pad_y):
            blob.bbox[0] = blob.bbox[0] * self._lb_scale + self._pad_x
            blob.bbox[1] = blob.bbox[1] * self._lb_scale + self._pad_y
            blob.bbox[2] *= self._lb_scale
            blob.bbox[3] *= self._lb_scale
            blob.centroid[0] = blob.centroid[0] * self._lb_scale + self._pad_x
            blob.centroid[1] = blob.centroid[1] * self._lb_scale + self._pad_y
        return blob, ratio

    def bridge_diagnostics(self, x: float, y: float, w: float, h: float,
                           **kwargs) -> dict:
        inv_scale = 1.0 / self._lb_scale if self._lb_scale != 0 else 1.0
        return self._detector.bridge_diagnostics(
            (x - self._pad_x) * inv_scale,
            (y - self._pad_y) * inv_scale,
            w * inv_scale,
            h * inv_scale,
            **kwargs,
        )


@dataclass
class _TrackerSpace:
    """How the tracker's coordinate space relates to the motion model's
    ROI-local original-resolution space (bug #10 unification).

    ``tracker = roi_local_px * scale + (pad_x, pad_y) + roi_offset`` — the GPU
    path tracks in ROI-local letterboxed imgsz space (scale = letterbox scale,
    pad = letterbox pad, no roi offset → ``roi_local=True``); the CPU path
    tracks in full-frame original space (scale 1, pad 0, roi offset added →
    ``roi_local=False``).
    """
    person_height: int        # person height in tracker space
    scale: float = 1.0        # letterbox scale (tracker px per roi-local px)
    pad_x: float = 0.0        # letterbox pad, tracker space
    pad_y: float = 0.0
    roi_x: int = 0            # ROI origin in original-frame space
    roi_y: int = 0
    roi_local: bool = False   # True: tracker space is ROI-local (GPU path)
    frame_width: int = 0      # content width for tracker edge-exit detection

    @property
    def mask_params(self) -> tuple:
        """The tracker→mask transform tuple consumed by the scored gate and
        the exclusion step: (scale, pad_x, pad_y, roi_x, roi_y, roi_local)."""
        return (self.scale, self.pad_x, self.pad_y,
                self.roi_x, self.roi_y, self.roi_local)

    def blobs_to_tracker(self, blobs):
        """Map motion blobs from ROI-local original space into tracker space
        (in place). Identity for the no-ROI CPU case."""
        off_x = 0 if self.roi_local else self.roi_x
        off_y = 0 if self.roi_local else self.roi_y
        if not blobs or (self.scale == 1.0 and not self.pad_x
                         and not self.pad_y and not off_x and not off_y):
            return blobs
        for blob in blobs:
            blob.bbox[0] = blob.bbox[0] * self.scale + self.pad_x + off_x
            blob.bbox[1] = blob.bbox[1] * self.scale + self.pad_y + off_y
            blob.bbox[2] *= self.scale
            blob.bbox[3] *= self.scale
            blob.centroid[0] = blob.centroid[0] * self.scale + self.pad_x + off_x
            blob.centroid[1] = blob.centroid[1] * self.scale + self.pad_y + off_y
        return blobs


class _BeltHook:
    """Per-frame adapter: identity-slot belt queries (full-frame px) ->
    ``BeltDetector.detect_near`` on the ROI gray (ROI-local px)."""

    def __init__(self, proc, det, gray, ox, oy):
        self._proc, self._det, self._gray = proc, det, gray
        self._ox, self._oy = float(ox), float(oy)

    def batch(self, queries):
        preds = [(sid, float(x) - self._ox, float(y) - self._oy, float(gate),
                  None if bw is None else float(bw))
                 for sid, x, y, gate, bw in queries]
        try:
            res = self._det.detect_near(self._gray, preds)
        except Exception as exc:  # noqa: BLE001 - never stop the show
            self._proc._belt_state = "unavailable"
            print(f"[Slots] IR belt detector failed, disabled: {exc}")
            return {}
        out = {}
        for sid, *_rest in queries:
            blob = res.get(sid) if isinstance(res, dict) else None
            if blob is not None and getattr(blob, "reason", None) is None:
                out[sid] = (float(blob.cx) + self._ox, float(blob.cy) + self._oy,
                            float(getattr(blob, "score", 1.0)))
        return out

    def __call__(self, slot_id, x, y, gate_px):
        return self.batch([(slot_id, x, y, gate_px, None)]).get(slot_id)


class FrameProcessor:
    """Encapsulates the main video processing steps."""

    def __init__(
        self,
        model: YOLO,
        settings: ProcessingSettings,
        enhancer: Optional[ImageEnhancer] = None,
        tracker: Optional[DancerTracker] = None,
        osc_sender: Optional[OSCSender] = None,
    ):
        self.model = model
        self._yolo_runner: Optional[PoseRunner] = None  # PERF-3, see _run_yolo
        self.settings = settings
        self.enhancer = enhancer or ImageEnhancer()
        self.tracker = tracker or DancerTracker()
        self.osc = osc_sender
        self._timing: Dict[str, float] = {}
        self._extract_transfer_timing: Dict[str, float] = {}
        # Optional callback(detections, gray_for_motion, roi_x, roi_y,
        # original_w, original_h) used by the detect-pass cache builder (TUNING
        # Phase B).  None on the live path.
        self._cache_capture_gpu = None   # Track P: GPU/TRT detect-cache hook

        # Background subtraction
        self.bg_subtractor = BackgroundSubtractor()

        # Unified motion model (P3 Stage 2): ONE slow MOG2 (silhouette for
        # bridging) + frame-diff, replacing the former two MOG2 models that
        # differed only in learn rate (ROADMAP Bug #2).  bridge_motion_detector
        # / crossval_motion_detector are now read-only views onto this single
        # model (see the properties below); consumers migrate to the clean
        # MotionModel surface in Stage 3.
        self.motion_model: Optional[MotionModel] = (
            MotionModel() if (MOTION_BRIDGE_ENABLED or MOTION_CROSSVAL_ENABLED)
            else None)
        self._tracking_mode = TrackingMode.YOLO_FIRST
        self._motion_lb_scale = 1.0
        self._motion_pad_x = 0
        self._motion_pad_y = 0
        self._crossval_motion_memory: Dict[tuple[int, int], float] = {}
        self._crossval_no_track_frames: int = 0  # consecutive frames with 0 confirmed tracks
        self._crossval_motion_cells: Dict[tuple[int, int], int] = {}  # cell → last frame with real motion
        self.tracker.set_motion_bridge_sensitivity(settings.motion_sensitivity)
        self._configure_motion_detectors()

        # Cached CLAHE / gamma LUT for motion-detector enhancement
        self._motion_clahe: Optional[cv2.CLAHE] = None
        self._motion_clahe_clip: float = 0.0
        self._motion_gamma_lut: Optional[np.ndarray] = None
        self._motion_gamma_val: float = 0.0
        self._last_motion_gray: Optional[np.ndarray] = None  # enhanced gray fed to MOG2
        # P-2: one long-lived motion-feed worker (was a Thread spawned per frame).
        # The MOG2/frame-diff feed runs on CPU in parallel with GPU YOLO, then we
        # block on it before the tracker needs blobs — identical sync points.
        self._motion_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="mog2-feed")
        self._motion_future = None
        self._exclusion = ExclusionMaskBuilder()  # auto exclusion mask (P1.4)
        # Per-frame YOLO box confidences keyed by bbox value (calib2 seed, ⑤a)
        self._last_box_confs: Dict[tuple, float] = {}
        # Output box-SIZE EMA state (Track X §B.2): track_id -> (w, h) smoothed
        # in original space; pruned to the reported set each frame.
        self._box_size_ema: Dict[int, tuple] = {}
        # Fixed-lag / RTS output smoother (Track X §2).  Lazily engaged when
        # L>1 (it becomes the /walldance/dancer/* stream); reset on an
        # inactive→active edge so it never releases stale buffered frames.
        self._output_smoother: Optional[OutputSmoother] = None
        self._output_lagged_active = False
        # Identity-slot output layer (CONT-6): lazily built; ``last_emitted``
        # is the causal stream OSC carries this frame (slot tracks when the
        # layer is on, else the tracker's reported tracks).  The output clock
        # is the wall clock live; replays install a frame clock.
        self._slots: Optional[IdentitySlots] = None
        self.last_emitted: List[ScaledTrack] = []
        self._output_clock = time.perf_counter
        self._out_last_t: Optional[float] = None
        self._out_dt = 0.05
        # IR waist-belt hook (core/belt_detector.py, lazily imported; absent =
        # no-op).  The raw gray of the current frame + its ROI offset.
        self._belt_detector = None
        self._belt_state = "unloaded"      # unloaded | ready | unavailable
        self._belt_gray: Optional[np.ndarray] = None
        self._belt_gray_offset = (0, 0)
        # online static-glint map for the belt detector (A6): learned every N frames
        self._belt_static = None
        self._belt_static_ok = True
        self._belt_frame = 0
        # Clean-plate foreground (core/foreground.py): detector built from the plate file
        # (settings.fg_plate_path) or a live capture; this frame's FgFrame for the slots.
        self._fg_detector = None
        self._fg_loaded_path: Optional[str] = None
        self._plate_capture = None          # PlateCapture in progress (live "Capture empty wall")
        self._plate_capture_done = None     # callback(CleanPlate | None, error str)
        self.last_fg = None
        self._last_fg_ms = 0.0
        self._auto_height = None            # core.auto_height.AutoHeight when settings.auto_height
        self._height_guard = None           # core.auto_height.AutoHeight on raw detections (height guard)
        self.height_guard_event: Optional[Tuple[int, int]] = None   # (old, new) px, popped by the main loop
        self._height_owned_px: Optional[int] = None   # the height the guard set last (it then follows)
        self._height_follow = None          # AutoHeight over HEIGHT_GUARD_FOLLOW_S while the guard owns it
        # The dancers' measured height (median px of the confident full skeletons over >= 2 s, samples,
        # wall time) and the original -> YOLO-input scale: the readiness "dancer size" row (D29)
        self.dancer_height: Optional[Tuple[float, int, float]] = None
        self.last_lb_scale: float = 0.0
        # Raw detection heights (original-space px) BEFORE the size gate — the
        # height-staleness alarm must see what the gate would reject (⑤d)
        self.last_raw_det_heights: List[float] = []
        self.last_raw_dets: List[Tuple[float, float, float, float]] = []

        # GPU pipeline (zero-copy path)
        self._gpu_pipeline: Optional[GpuPipeline] = None
        self._gpu_path_active = False
        self._gpu_fallback_reason: Optional[str] = None
        
        if settings.use_gpu_path and GPU_PIPELINE_AVAILABLE:
            # Create GPU pipeline with settings
            gpu_settings = GpuPipelineSettings(
                enhance_enabled=settings.enhance_enabled,
                enhance_lite=settings.enhance_lite,
                enhance_force=settings.enhance_force,
                brightness_threshold=float(settings.brightness_threshold),
                clahe_clip=self.enhancer.clahe_clip,
                clahe_grid=8,
                gamma=self.enhancer.gamma,
                preview_width=960,  # Will be updated by app
                preview_height=540,  # Will be updated by app
                yolo_imgsz=settings.imgsz,
            )
            self._gpu_pipeline = GpuPipeline(gpu_settings)
            self._gpu_pipeline._bg_subtractor = self.bg_subtractor  # Share instance
            self._gpu_path_active = True
            print("[Pipeline] GPU pipeline active - zero-copy enhancement + YOLO (kornia/PyTorch)")
        elif TORCH_CUDA_AVAILABLE:
            print("[Pipeline] CUDA available - YOLO on GPU, Enhancement on CPU")
        else:
            print("[Pipeline] CUDA not available - all processing on CPU")
            self._gpu_fallback_reason = "CUDA not available - PyTorch was built without GPU support or no compatible GPU found."

    # ------------------------------------------------------------------
    # Configuration management
    # ------------------------------------------------------------------
    def update_settings(self, **kwargs):
        for key, value in kwargs.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)

    def attach_osc(self, osc_sender: Optional[OSCSender]):
        self.osc = osc_sender

    @property
    def gpu_path_active(self) -> bool:
        """Check if GPU path is currently active."""
        return self._gpu_path_active

    @property
    def gpu_fallback_reason(self) -> Optional[str]:
        """Return CUDA/GPU fallback reason when GPU path was disabled."""
        return self._gpu_fallback_reason

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray, need_preview: bool = True, frame_number: int | None = None) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """Run a single frame through the pipeline.
        
        When GPU pipeline is active:
        - Frame is uploaded to GPU once
        - Enhancement runs on GPU (kornia CLAHE + gamma)
        - GPU tensor passed directly to YOLO (zero-copy)
        - Preview downloaded only when needed
        
        Args:
            frame: BGR numpy array from camera
            need_preview: Whether to generate preview output (for rate limiting)
            frame_number: Display frame number for tracker logging (overlay match)
        
        Returns:
            (tracked, enhanced_frame, timing, latency_ms)
        """
        # Track P: GPU-only.  The CPU path was removed — there is no non-GPU
        # machine, and YOLO-on-CPU is unusably slow.
        if not (self._gpu_path_active and self._gpu_pipeline is not None):
            raise RuntimeError(
                "GPU pipeline required (Track P removed the CPU fallback path); "
                "no GPU pipeline is active.")
        return self._process_gpu(frame, need_preview, frame_number=frame_number)
    
    def _process_gpu(self, frame: np.ndarray, need_preview: bool = True, frame_number: int | None = None) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """GPU pipeline: zero-copy enhancement + YOLO."""
        frame_start = time.perf_counter()
        original_h, original_w = frame.shape[:2]
        timing: Dict[str, float] = {}
        
        # Sync GPU pipeline settings
        self._sync_gpu_settings()

        # 0. Start the CPU motion feed first, so it overlaps the GPU upload +
        # enhance as well as YOLO (it only needs the raw frame + the ROI).
        motion = self._start_motion_feed(
            frame, self._gpu_pipeline._resolve_roi(original_w, original_h),
            timing, mono_raw=False)

        # 1. GPU Pipeline: Upload + Enhance + YOLO prep
        try:
            yolo_tensor, preview_frame, gpu_timing = self._gpu_pipeline.process(
                frame, preview_enabled=need_preview)
        except BaseException:
            self._await_motion_feed()   # never leave a feed in flight
            raise
        
        # Merge GPU timing
        timing.update(gpu_timing)
        timing["path_enhance"] = "gpu"
        
        # 2-6. YOLO → Track → OSC
        scaled_tracks = self._run_yolo_and_track(
            yolo_tensor, gpu_timing, timing, original_w, original_h,
            frame_number=frame_number, raw_frame=frame, motion=motion)
        
        latency_ms = (time.perf_counter() - frame_start) * 1000
        timing["total"] = latency_ms
        self._timing = timing
        
        return scaled_tracks, preview_frame, timing, latency_ms
    
    def process_gpu_direct(self, gpu_tensor: 'torch.Tensor', need_preview: bool = True, frame_number: int | None = None, raw_frame: np.ndarray | None = None) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """Process a pre-uploaded GPU tensor (optimized path for IDS camera).
        
        This bypasses the CPU→GPU upload step for lowest latency when the
        camera provides frames directly as GPU tensors via read_gpu().
        
        Args:
            gpu_tensor: GPU tensor (1, 3, H, W) float32 [0,1] RGB format
            need_preview: Whether to generate preview output
            frame_number: Display frame number for tracker logging (overlay match)
            raw_frame: Optional BGR numpy frame for MOG2 motion detection.
                       Without this, motion bridge / motion-first are disabled.
            
        Returns:
            (tracked, enhanced_frame, timing, latency_ms)
        """
        if not self._gpu_path_active or self._gpu_pipeline is None:
            raise RuntimeError("GPU path not active, cannot use process_gpu_direct")

        # Track P: GPU-only — no CPU fallback (a CUDA error now propagates).
        frame_start = time.perf_counter()
        _, _, original_h, original_w = gpu_tensor.shape
        timing: Dict[str, float] = {}

        # Sync GPU pipeline settings
        self._sync_gpu_settings()

        # 0. Motion feed first (overlaps enhance + YOLO).  This is the IDS-only
        # path: the camera delivers mono frames expanded to BGR (R==G==B), so
        # the motion feed takes the single channel directly (P-1, mono_raw).
        motion = self._start_motion_feed(
            raw_frame, self._gpu_pipeline._resolve_roi(original_w, original_h),
            timing, mono_raw=True)

        # 1. GPU Pipeline: process_gpu_tensor (skip upload, already on GPU)
        try:
            yolo_tensor, preview_frame, gpu_timing = self._gpu_pipeline.process_gpu_tensor(
                gpu_tensor, preview_enabled=need_preview
            )
        except BaseException:
            self._await_motion_feed()   # never leave a feed in flight
            raise

        # Merge GPU timing
        timing.update(gpu_timing)
        timing["path_enhance"] = "gpu-direct"

        # 2-6. YOLO → Track → OSC
        scaled_tracks = self._run_yolo_and_track(
            yolo_tensor, gpu_timing, timing, original_w, original_h,
            frame_number=frame_number, raw_frame=raw_frame, motion=motion)

        latency_ms = (time.perf_counter() - frame_start) * 1000
        timing["total"] = latency_ms
        self._timing = timing

        return scaled_tracks, preview_frame, timing, latency_ms

    def _run_yolo_and_track(
        self,
        yolo_tensor: 'torch.Tensor',
        gpu_timing: Dict[str, float],
        timing: Dict[str, float],
        original_w: int,
        original_h: int,
        frame_number: int | None = None,
        raw_frame: np.ndarray | None = None,
        motion: Optional[Tuple[float, np.ndarray]] = None,
    ) -> List[ScaledTrack]:
        """Shared YOLO inference → extract → track → unscale → OSC pipeline.

        Mutates *timing* in place and returns the final scaled tracks.
        ``motion`` is the (submit time, gray) of the MOG2/frame-diff feed the
        caller started (``_start_motion_feed``) before the GPU stage; it runs
        on the worker thread overlapping enhance + YOLO (CPU ∥ GPU) and is
        awaited before the tracker needs blobs.
        """
        roi = gpu_timing.get('roi', {})
        roi_x = int(roi.get('x', 0))
        roi_y = int(roi.get('y', 0))
        gray_for_motion = motion[1] if motion is not None else None

        # YOLO inference (GPU) — runs in parallel with MOG2 feed (CPU)
        t0 = time.perf_counter()
        results = self._run_yolo(yolo_tensor)
        timing["yolo"] = (time.perf_counter() - t0) * 1000
        timing["path_yolo"] = "gpu"

        # Scale person_height_px from original-camera space to letterboxed
        # YOLO-tensor space.  Detections and the tracker both operate in
        # the letterboxed coordinate system in the GPU path.
        letterbox = gpu_timing.get('letterbox', {})
        lb_scale = letterbox.get('scale', 1.0)
        pad_x = letterbox.get('pad_x', 0)
        pad_y = letterbox.get('pad_y', 0)
        scaled_person_height = max(1, int(self.settings.person_height_px * lb_scale))

        # Extract detections
        t0 = time.perf_counter()
        detections = self._extract_detections(results)
        # Raw det heights in ORIGINAL-space px, before the size gate — the
        # height-staleness alarm must see what the gate would reject (⑤d).
        inv_lb = 1.0 / lb_scale if lb_scale > 0 else 1.0
        self.last_raw_det_heights = [float(d[2][3]) * inv_lb for d in detections]
        # (box conf, centre x, centre y, height) in original px: the empty-wall ghost check (D29)
        self.last_raw_dets = [
            (self._last_box_confs.get(self._bbox_conf_key(d[2]), 0.0),
             roi_x + (float(d[2][0]) + float(d[2][2]) / 2 - pad_x) * inv_lb,
             roi_y + (float(d[2][1]) + float(d[2][3]) / 2 - pad_y) * inv_lb,
             float(d[2][3]) * inv_lb) for d in detections]
        self.last_lb_scale = float(lb_scale) if lb_scale else self.last_lb_scale
        self._guard_person_height(detections, inv_lb)
        detections = self._filter_duplicate_detections(detections, effective_person_height=scaled_person_height)
        timing["extract"] = (time.perf_counter() - t0) * 1000
        timing.update(self._extract_transfer_timing)

        # Block on the MOG2 feed before tracker needs blobs (same sync point)
        if motion is not None:
            t_wait = time.perf_counter()
            self._await_motion_feed()
            t_done = time.perf_counter()
            # submit -> done window (spans enhance + YOLO); mog2_wait is what
            # the main thread actually blocked (> 0 = motion on the critical path)
            timing["mog2_feed"] = (t_done - motion[0]) * 1000
            timing["mog2_wait"] = (t_done - t_wait) * 1000

        # GPU tracker space: ROI-local letterboxed imgsz coords — content sits
        # between pad_x and imgsz - pad_x for edge-exit detection.
        space = _TrackerSpace(
            person_height=scaled_person_height,
            scale=lb_scale,
            pad_x=pad_x,
            pad_y=pad_y,
            roi_x=roi_x,
            roi_y=roi_y,
            roi_local=True,
            frame_width=self.settings.imgsz,
        )

        if self.bridge_motion_detector is not None and raw_frame is not None:
            # Configure the letterbox proxy for tracker-space motion queries
            self._motion_lb_scale = lb_scale
            self._motion_pad_x = pad_x
            self._motion_pad_y = pad_y
        lb_motion = self._get_letterbox_motion_detector()

        clamp = self.settings.box_clamp_enabled

        def finalize(track):
            # Unscale from letterboxed YOLO space to original camera space
            return self._unscale_letterbox(
                track, lb_scale, pad_x, pad_y, roi_x=roi_x, roi_y=roi_y,
                clamp_to_yolo_size=clamp)

        # Track P Stage 1: capture the GPU/TRT detect-pass (letterbox-space
        # detections + the tracker space + the motion gray) so a search can
        # re-run the post-YOLO chain from a TRT cache.  No cost on the live path.
        if self._cache_capture_gpu is not None:
            self._cache_capture_gpu(detections, space, gray_for_motion,
                                    original_w, original_h)
        # IR belt hook input: the raw (un-enhanced) ROI gray, full-frame offset.
        self._belt_gray = gray_for_motion
        self._belt_gray_offset = (roi_x, roi_y)

        return self._post_yolo_chain(
            detections, space, lb_motion, finalize,
            original_w, original_h, frame_number, timing)

    def _run_yolo(self, yolo_tensor: 'torch.Tensor'):
        """``self.model(yolo_tensor, imgsz=, conf=, iou=, half=, verbose=False)``
        through the PERF-3 ``PoseRunner`` (same detections, without the
        per-call ultralytics setup / device syncs / orig_img download).  The
        runner follows ``self.model`` (the app swaps models by assignment)."""
        runner = self._yolo_runner
        if runner is None or runner.model is not self.model:
            runner = self._yolo_runner = PoseRunner(self.model)
            # Kill switch: WD_POSE_RUNNER=0 forces the regular ultralytics call
            # (the fast path reads predictor internals; verify per ultralytics).
            if os.environ.get("WD_POSE_RUNNER", "1") == "0":
                runner.enabled = False
                runner.disabled_reason = "WD_POSE_RUNNER=0"
        return runner(
            yolo_tensor,
            imgsz=self.settings.imgsz,
            conf=self.settings.confidence,
            iou=YOLO_IOU_THRESHOLD,
            half=self.settings.use_fp16,
        )

    def replay_gpu_cached(self, dets, space_dict, gray, original_w, original_h,
                          frame_number, timing):
        """Track P Stage 1: re-run the GPU post-YOLO chain from a cached
        detect-pass (letterbox-space ``dets`` + the ``_TrackerSpace`` fields +
        the motion ``gray``), skipping YOLO.  Mirrors the live
        ``_run_yolo_and_track`` tail so a cache replay equals a direct GPU run
        for any post-YOLO config change."""
        if gray is not None:
            self._feed_motion_detectors(gray)
        space = _TrackerSpace(
            person_height=int(space_dict["person_height"]),
            scale=float(space_dict["scale"]),
            pad_x=float(space_dict["pad_x"]),
            pad_y=float(space_dict["pad_y"]),
            roi_x=int(space_dict["roi_x"]),
            roi_y=int(space_dict["roi_y"]),
            roi_local=True,
            frame_width=int(space_dict["frame_width"]),
        )
        self._motion_lb_scale = space.scale
        self._motion_pad_x = space.pad_x
        self._motion_pad_y = space.pad_y
        lb_motion = self._get_letterbox_motion_detector()
        clamp = self.settings.box_clamp_enabled
        self._belt_gray = gray
        self._belt_gray_offset = (space.roi_x, space.roi_y)

        def finalize(track):
            return self._unscale_letterbox(
                track, space.scale, space.pad_x, space.pad_y,
                roi_x=space.roi_x, roi_y=space.roi_y, clamp_to_yolo_size=clamp)

        return self._post_yolo_chain(
            dets, space, lb_motion, finalize,
            original_w, original_h, frame_number, timing)

    def _sync_gpu_settings(self):
        """Sync ProcessingSettings to GpuPipelineSettings."""
        if self._gpu_pipeline is None:
            return
        gs = self._gpu_pipeline.settings
        gs.enhance_enabled = self.settings.enhance_enabled
        gs.enhance_lite = self.settings.enhance_lite
        gs.enhance_force = self.settings.enhance_force
        gs.brightness_threshold = float(self.settings.brightness_threshold)
        gs.clahe_clip = self.enhancer.clahe_clip
        gs.gamma = self.enhancer.gamma
        gs.greyscale = self.settings.greyscale
        
        # Map denoise_strength (0.0-1.0) to alpha (1.0-0.0)
        # Strength 0.0 -> Alpha 1.0 (No smoothing)
        # Strength 0.9 -> Alpha 0.1 (Heavy smoothing)
        if self.settings.denoise_strength > 0.0:
            gs.denoise_enabled = True
            gs.denoise_alpha = max(0.01, 1.0 - self.settings.denoise_strength)
        else:
            gs.denoise_enabled = False
            
        gs.yolo_imgsz = self.settings.imgsz
        
        # Background subtraction
        gs.bg_subtract_enabled = self.settings.bg_subtract_enabled
        gs.bg_subtract_sensitivity = self.settings.bg_subtract_sensitivity
        gs.roi_enabled = self.settings.roi_enabled
        gs.roi_x = self.settings.roi_x
        gs.roi_y = self.settings.roi_y
        gs.roi_w = self.settings.roi_w
        gs.roi_h = self.settings.roi_h

    def _resolve_roi(self, frame_w: int, frame_h: int) -> Optional[Tuple[int, int, int, int]]:
        """Return a clamped ROI as (x, y, x2, y2) in full-frame pixels."""
        if not self.settings.roi_enabled:
            return None
        if frame_w <= 1 or frame_h <= 1:
            return None

        x = max(0, min(int(self.settings.roi_x), frame_w - 1))
        y = max(0, min(int(self.settings.roi_y), frame_h - 1))
        w = max(1, int(self.settings.roi_w))
        h = max(1, int(self.settings.roi_h))
        x2 = max(x + 1, min(frame_w, x + w))
        y2 = max(y + 1, min(frame_h, y + h))
        return x, y, x2, y2

    def set_preview_size(self, width: int, height: int):
        """Set preview dimensions for GPU pipeline."""
        if self._gpu_pipeline is not None:
            self._gpu_pipeline.settings.preview_width = width
            self._gpu_pipeline.settings.preview_height = height
            self._gpu_pipeline._cached_preview = None

    def set_preview_fps_cap(self, fps_cap: Optional[float]):
        """Set preview FPS cap for GPU pipeline rate limiting."""
        if self._gpu_pipeline is not None:
            self._gpu_pipeline.settings.preview_fps_cap = fps_cap
            self._gpu_pipeline.update_settings(self._gpu_pipeline.settings)

    def invalidate_preview_cache(self):
        """Drop any cached GPU preview so the next preview reflects current settings."""
        if self._gpu_pipeline is not None:
            self._gpu_pipeline._cached_preview = None

    def _post_yolo_chain(
        self,
        detections,
        space: _TrackerSpace,
        motion_proxy,
        finalize,
        original_w: int,
        original_h: int,
        frame_number: int | None,
        timing: Dict[str, float],
    ) -> List[ScaledTrack]:
        """The post-YOLO chain, shared by both paths (bug #10): scored gate →
        exclusion → cold blobs → tracker → finalize → OSC.

        ``space`` parameterizes the tracker↔motion-mask transforms,
        ``motion_proxy`` is the tracker-space view of the motion detector (the
        letterbox/offset proxies stay — only the orchestration is unified) and
        ``finalize`` maps a tracker-space DancerTrack to an original-space
        ScaledTrack.  ``timing`` is mutated in place.
        """
        # Cross-validate YOLO detections against the motion evidence
        n_before_xval = len(detections)
        detections, crossval_stats = self._crossval_motion_filter(
            detections,
            self.crossval_motion_detector,
            space.person_height,
            scale=space.scale,
            letterbox_pad_x=space.pad_x,
            letterbox_pad_y=space.pad_y,
            roi_x=space.roi_x,
            roi_y=space.roi_y,
            roi_local_after_unscale=space.roi_local,
        )
        timing.update(crossval_stats)
        timing["crossval_rejected"] = n_before_xval - len(detections)

        # Auto exclusion mask (P1.4): collect during calibration, else reject
        # detections that land in known scenery / ghost cells.
        detections = self._exclusion_step(
            detections, self.crossval_motion_detector, space.mask_params,
            space.person_height, timing)

        # Cold-detection blobs (P3 Stage 3b — always on, gated): promote moving
        # blobs YOLO missed to synthetic detections; gated in ROI-local space,
        # then mapped into tracker space.
        eager_blobs = None
        if self.bridge_motion_detector is not None:
            eager_blobs = self.bridge_motion_detector.detect(
                self.settings.person_height_px,
                aspect_range=MOTION_FIRST_ASPECT_RANGE,
                include_shadows=MOTION_FIRST_INCLUDE_SHADOWS,
            )
            eager_blobs = self._gate_cold_blobs(eager_blobs)
            eager_blobs = space.blobs_to_tracker(eager_blobs)

        # Tracking
        t0 = time.perf_counter()
        self.tracker.set_frame_dimensions(space.frame_width,
                                          pad_x=int(space.pad_x))
        self.tracker.set_person_height(space.person_height)

        t_trk = time.perf_counter()
        tracked = self.tracker.update(
            detections, frame_number=frame_number,
            motion_detector=motion_proxy,
            motion_blobs=eager_blobs)
        timing["tracker_update"] = (time.perf_counter() - t_trk) * 1000
        timing["track"] = (time.perf_counter() - t0) * 1000
        timing["path_track"] = "cpu"
        timing["original_w"] = original_w
        timing["original_h"] = original_h

        # Update re-acquisition counter for crossval death-spiral prevention
        if tracked:
            self._crossval_no_track_frames = 0
        else:
            self._crossval_no_track_frames += 1
        timing["crossval_no_track_frames"] = self._crossval_no_track_frames

        scaled_tracks = [finalize(t) for t in tracked]
        if self.settings.auto_height:
            self._learn_person_height(scaled_tracks)

        # Attach the YOLO box conf of the detection that fed each track this
        # frame (calib2 sensitivity seed, ⑤a).  DancerTrack.update stores the
        # matched bbox verbatim, so a value match recovers the pairing;
        # bridged / cold-blob-fed tracks resolve to None by construction.
        if self._last_box_confs:
            for t, st in zip(tracked, scaled_tracks):
                if t.time_since_update == 0:
                    st.box_conf = self._last_box_confs.get(
                        self._bbox_conf_key(t.bbox))

        # Single output stream selected by L (OSC_CONTRACT §B).  ONE namespace,
        # /walldance/dancer/*:
        #   L = 1 → causal / live: light box-size EMA, emitted this frame.
        #   L > 1 → the fixed-lag RTS-smoothed stream, released L frames late.
        # The PREVIEW always uses the causal ``scaled_tracks`` (returned below),
        # so the operator's view stays live regardless of L.
        L = max(1, int(self.settings.output_smoothing_l))
        osc_on = bool(self.osc) and bool(self.settings.osc_enabled)

        # The smoother is fed exactly when it emits (osc on AND L>1); resetting
        # on the inactive→active edge means an OSC toggle or an L 1→>1 change
        # re-warms a fresh window instead of releasing a stale tail stitched
        # across the gap as if dt=1.
        lagged_active = L > 1 and osc_on
        if (lagged_active and not self._output_lagged_active
                and self._output_smoother is not None):
            self._output_smoother.reset()
        self._output_lagged_active = lagged_active

        if L == 1:
            # Causal box-size EMA on the tracker's reported tracks -- the
            # returned / preview stream, byte-identical with slots on or off.
            self._smooth_output_box_sizes(scaled_tracks)

        # Identity slots (CONT-6): N stable ids over the churning track ids,
        # coasting + One-Euro smoothing per slot -- the stream OSC carries.
        # OUTPUT-ONLY: the returned ``scaled_tracks`` are untouched.
        slots_on = bool(self.settings.identity_slots_enabled)
        out_tracks = (self._run_identity_slots(scaled_tracks, finalize,
                                               original_w, original_h)
                      if slots_on else scaled_tracks)
        if slots_on and self.last_fg is not None:
            timing["fg"] = self._last_fg_ms

        if L == 1:
            if osc_on:
                self.osc.send_frame(out_tracks, original_w, original_h)
        elif lagged_active:
            # At L>1 the RTS smoother runs on the slot ids when slots are on
            # (no restart on tracker id churn).
            lagged_tracks = self._run_output_smoother(out_tracks, L)
            if lagged_tracks:  # empty during the first L-frame warm-up
                self.osc.send_frame(lagged_tracks, original_w, original_h)
        if osc_on and slots_on and self.settings.osc_send_state and self._slots is not None:
            # Opt-in /walldance/dancer/state (OSC_CONTRACT §D): every slot,
            # lost ones included, causal (not lagged at L>1).
            self.osc.send_states(self.slot_states())
        self.last_emitted = out_tracks

        return scaled_tracks

    # ------------------------------------------------------------------
    # Identity-slot output layer (CONT-6)
    # ------------------------------------------------------------------
    def set_output_clock(self, clock) -> None:
        """Seconds clock for the output stage (slots' coast timers, One-Euro).
        Live = wall clock; replays install ``frame / fps`` (deterministic)."""
        self._output_clock = clock
        self._out_last_t = None

    def configure_identity_slots(self, **kw) -> None:
        """Live operator knobs: ``identity_slots_enabled``, ``max_dancers``,
        ``stability``, ``coast_s``, ``use_ir_belt``, ``osc_send_state``,
        ``static_ghost_guard``, ``static_release_s``, ``slot_filter_input``."""
        for key in ("identity_slots_enabled", "use_ir_belt", "osc_send_state",
                    "static_ghost_guard", "smart_hold"):
            if kw.get(key) is not None:
                setattr(self.settings, key, bool(kw[key]))
        if kw.get("max_dancers") is not None:
            self.settings.max_dancers = max(1, int(kw["max_dancers"]))
        if kw.get("stability") is not None:
            self.settings.stability = min(3.0, max(0.0, float(kw["stability"])))
        if kw.get("coast_s") is not None:
            self.settings.coast_s = max(0.0, float(kw["coast_s"]))
        if kw.get("static_release_s") is not None:
            self.settings.static_release_s = max(0.0, float(kw["static_release_s"]))
        if kw.get("slot_filter_input") in ("smoothed", "raw", "raw_skeleton"):
            self.settings.slot_filter_input = str(kw["slot_filter_input"])
        if self._slots is not None:
            g = bool(self.settings.static_ghost_guard)
            self._slots.configure(max_dancers=self.settings.max_dancers,
                                  stability=self.settings.stability,
                                  coast_s=self.settings.coast_s,
                                  static_guard=g, static_yield=g,
                                  static_release_s=self.settings.static_release_s,
                                  filter_input=self.settings.slot_filter_input,
                                  smart_hold=bool(self.settings.smart_hold))
        if kw.get("identity_slots_enabled") is False:
            self.reset_output()

    def reset_output(self) -> None:
        """Forget the slot table (tracker reset / playback restart / disable)."""
        if self._slots is not None:
            self._slots.reset()
        self._out_last_t = None
        self.last_emitted = []
        self._belt_static = None          # a new scene / take: relearn the glints
        if self._belt_detector is not None:
            self._belt_detector.static = None

    def slot_states(self) -> List[tuple]:
        """[(slot id, state, age_s)] for every slot (lost included)."""
        if self._slots is None:
            return []
        t = self._out_last_t or 0.0
        return [(s.sid, s.state, round(max(0.0, t - s.state_since), 3))
                for s in self._slots.slots]

    @property
    def belt_status(self) -> str:
        """``off`` | ``unloaded`` (not tried yet) | ``ready`` | ``unavailable``
        (module not landed / failed)."""
        if not self.settings.use_ir_belt:
            return "off"
        return self._belt_state

    def _belt_hook(self):
        """This frame's belt hook (``identity_slots.BeltMeasure``), or None.

        Lazily builds ``core.belt_detector.BeltDetector`` (gated mode,
        ``detect_near``: ~0.3 ms per prediction, one belt answers at most one
        slot -- the global ``detect()`` has false positives on lit arms/shoes
        and is never used live).  The gray is the raw ROI crop fed to the
        motion model, so queries are shifted by the ROI origin.  Any import /
        construction / call failure turns the hook into a logged no-op: plain
        coasting stays the base behaviour."""
        if not self.settings.use_ir_belt or self._belt_state == "unavailable":
            return None
        if self._belt_state == "unloaded":
            try:
                from core.belt_detector import BeltDetector, BeltParams
                self._belt_detector = BeltDetector(BeltParams(), static=None)
                self._belt_state = "ready"
                print("[Slots] IR belt detector ready")
            except Exception as exc:  # noqa: BLE001 - optional module
                self._belt_state = "unavailable"
                print(f"[Slots] IR belt detector unavailable ({type(exc).__name__}: {exc})")
                return None
        gray = self._belt_gray
        if gray is None:
            return None
        self._belt_frame += 1
        if self._belt_static_ok and self._belt_frame % max(1, BELT_STATIC_EVERY_N) == 0:
            self._update_belt_static(gray, *self._belt_gray_offset)
        return _BeltHook(self, self._belt_detector, gray, *self._belt_gray_offset)

    def _update_belt_static(self, gray, ox, oy) -> None:
        """Static-glint map for the belt detector, learned online: a global belt pass
        (every BELT_STATIC_EVERY_N frames) feeds a per-cell persistence EMA; discs
        around LIVE slots are protected (a coasting slot is not: a glint at its
        predicted waist is exactly what must be learned).  Any failure stops the
        learning for the session, never the belt."""
        try:
            from core.belt_detector import StaticMap
            g = gray if gray.ndim == 2 else gray[:, :, 0]
            if self._belt_static is None or self._belt_static.shape != tuple(g.shape[:2]):
                self._belt_static = StaticMap(g.shape[:2], cell=8)
            blobs = [b for b in self._belt_detector.detect(g, return_all=True)
                     if b.reason not in ("small", "static")]
            protect = []
            if self._slots is not None:
                t_now = self._out_last_t if self._out_last_t is not None else 0.0
                for s in self._slots.slots:
                    # live slots, and belt-only slots backed by independent evidence (a recent
                    # YOLO skeleton on their track, or the foreground): a still dancer's belt
                    # must not be learned as a glint
                    backed = (s.state == STATE_BELT
                              and t_now - getattr(s, "belt_backed_t", -1e9) <= 1.0)
                    if (s.state == STATE_LIVE or backed) and s.pos is not None and s.wh is not None:
                        protect.append((float(s.pos[0]) - ox, float(s.pos[1]) - oy,
                                        0.6 * float(s.wh[1])))
            self._belt_static.update(blobs, protect, alpha=BELT_STATIC_ALPHA, on=BELT_STATIC_ON)
            self._belt_detector.static = self._belt_static
        except Exception as exc:  # noqa: BLE001 - optional refinement
            self._belt_static_ok = False
            print(f"[Slots] belt static map disabled ({type(exc).__name__}: {exc})")

    # ------------------------------------------------------------------
    # Clean-plate foreground ("background snapshot", core/foreground.py)
    # ------------------------------------------------------------------
    def set_clean_plate(self, plate) -> None:
        """Install a ``CleanPlate`` (or None to drop it) for the foreground."""
        if plate is None:
            self._fg_detector = None
            return
        from core.foreground import ForegroundDetector
        self._fg_detector = ForegroundDetector(plate)

    @property
    def plate_status(self) -> str:
        """``off`` | ``none`` (no plate) | ``ready`` | ``size`` (plate from another camera
        crop) | ``stale`` (the scene no longer matches: re-capture) | ``capturing``."""
        if self._plate_capture is not None:
            return "capturing"
        if not self.settings.fg_enabled:
            return "off"
        det = self._fg_detector
        if det is None:
            return "none"
        last = self.last_fg
        if last is not None and not last.valid:
            return {"plate size": "size", "plate stale": "stale"}.get(last.reason, "ready")
        return "ready"

    def start_plate_capture(self, n_frames: int, done) -> None:
        """Average the next ``n_frames`` raw frames into a new plate (the wall must be
        empty); ``done(plate, error)`` runs on the processing thread when finished."""
        from core.foreground import PlateCapture
        self._plate_capture = PlateCapture(n_frames)
        self._plate_capture_done = done

    def _feed_plate_capture(self, raw_frame) -> None:
        cap = self._plate_capture
        if cap is None or raw_frame is None:
            return
        try:
            if cap.add(raw_frame):
                plate = cap.plate(source="live")
                self._plate_capture = None
                self.set_clean_plate(plate)
                done, self._plate_capture_done = self._plate_capture_done, None
                if done is not None:
                    done(plate, "")
        except Exception as exc:  # noqa: BLE001 - a failed capture must not stop the show
            self._plate_capture = None
            done, self._plate_capture_done = self._plate_capture_done, None
            if done is not None:
                done(None, f"{type(exc).__name__}: {exc}")

    def _ensure_plate(self):
        """The ForegroundDetector of the plate named by ``settings.fg_plate_path`` (loaded once per
        path; a load failure is logged and leaves it None)."""
        path = self.settings.fg_plate_path or ""
        if path and path != self._fg_loaded_path:
            self._fg_loaded_path = path
            try:
                from core.foreground import CleanPlate
                self.set_clean_plate(CleanPlate.load(path))
                print(f"[Foreground] plate loaded: {path}")
            except Exception as exc:  # noqa: BLE001
                self._fg_detector = None
                print(f"[Foreground] plate not loaded ({type(exc).__name__}: {exc})")
        return self._fg_detector

    def light_ratio(self, frame) -> Optional[float]:
        """Live / empty-wall-snapshot brightness of the ROI of a raw frame (``ForegroundDetector
        .light_ratio``), or None (no plate, another camera crop, a snapshot too dark to measure).
        Independent of ``fg_enabled`` and of RUN: the ops tick samples it at 1 Hz, so a light
        change is caught in standby too (the plate comparison itself only runs in the pipeline)."""
        det = self._ensure_plate()
        if det is None or frame is None:
            return None
        h, w = frame.shape[:2]
        s = self.settings
        if s.roi_enabled and s.roi_w > 0 and s.roi_h > 0:
            x0, y0 = max(0, int(s.roi_x)), max(0, int(s.roi_y))
            x1, y1 = min(w, x0 + int(s.roi_w)), min(h, y0 + int(s.roi_h))
        else:
            x0, y0, x1, y1 = 0, 0, w, h
        if x1 <= x0 or y1 <= y0:
            return None
        return det.light_ratio(frame[y0:y1, x0:x1], x0, y0, (w, h))

    @property
    def plate_stamp(self) -> str:
        """When the loaded snapshot was taken ("10-06 21:16"), "" without one."""
        det = self._fg_detector
        created = getattr(getattr(det, "plate", None), "created", "") or ""
        return created[5:16].replace("T", " ")

    def _fg_frame(self, original_w: int, original_h: int):
        """This frame's foreground (``FgFrame``) for the slot layer, or None (off, no plate,
        another camera crop).  Loads the plate file named by ``settings.fg_plate_path``
        once; a load failure is logged and leaves the foreground off."""
        self.last_fg = None
        if not self.settings.fg_enabled:
            return None
        det = self._ensure_plate()
        gray = self._belt_gray
        if det is None or gray is None:
            return None
        t0 = time.perf_counter()
        try:
            fg = det.process(gray, int(self._belt_gray_offset[0]), int(self._belt_gray_offset[1]),
                             (int(original_w), int(original_h)))
        except Exception as exc:  # noqa: BLE001 - optional evidence, never fatal
            print(f"[Foreground] disabled ({type(exc).__name__}: {exc})")
            self._fg_detector = None
            return None
        self.last_fg = fg
        self._last_fg_ms = (time.perf_counter() - t0) * 1000
        return fg if fg.valid else None

    def _learn_person_height(self, scaled_tracks) -> None:
        """Automatic person height (core/auto_height.py): confident full skeletons this frame
        move ``person_height_px`` toward the median of the last 10 s (used from the next frame)."""
        if self._auto_height is None:
            from core.auto_height import AutoHeight
            self._auto_height = AutoHeight()
        h = self._auto_height.update(scaled_tracks, float(self._output_clock()),
                                     float(self.settings.person_height_px))
        if h is not None:
            self.settings.person_height_px = max(1, int(round(h)))

    def _guard_person_height(self, detections, inv_lb: float) -> None:
        """Height guard (config.HEIGHT_GUARD): the confident full skeletons among this frame's RAW
        detections (original px, before the size gate), over the last 10 s.  When the size gate around
        ``person_height_px`` rejects >= HEIGHT_GUARD_OUTSIDE of them and they agree on a height (spread
        <= HEIGHT_GUARD_MAX_SPREAD), the configured height is wrong for the people actually seen: their
        median is adopted (used from the next frame).  A minority outside the gate (somebody near the
        camera) is what the gate is for; a spread-out population (people walking toward the lens) has
        no single height to adopt.  Once the guard has set the height it follows the dancers over a
        longer window (HEIGHT_GUARD_FOLLOW_*): the first population it saw may be a walk-in close to
        the camera, not the wall."""
        if self._height_guard is None:
            from core.auto_height import AutoHeight
            self._height_guard = AutoHeight(min_samples=HEIGHT_GUARD_MIN_SAMPLES)
        dets = [_RawDet(0, d[1], (0.0, 0.0, 0.0, float(d[2][3]) * inv_lb)) for d in detections]
        ph = float(self.settings.person_height_px)
        t = float(self._output_clock())
        ready = self._height_guard.update(dets, t, ph) is not None
        if dets and len(self._height_guard.window()) >= 40:          # >= 2 s of a dancer: measured
            win = sorted(self._height_guard.window())
            self.dancer_height = (float(win[len(win) // 2]), len(win), time.time())
        if self._height_follow is None:
            from core.auto_height import AutoHeight
            self._height_follow = AutoHeight(window_s=HEIGHT_GUARD_FOLLOW_S, min_samples=1,
                                             kpt_conf=HEIGHT_GUARD_FOLLOW_KPT_CONF)
        self._height_follow.update(dets, t, ph)
        if self._height_owned_px is not None and int(ph) != self._height_owned_px:
            self._height_owned_px = None              # set by someone else (config, slider): theirs now
        if not self.settings.height_guard or self.settings.auto_height:
            return
        lo = ph * float(self.settings.person_height_min_ratio)
        hi = ph * float(self.settings.person_height_max_ratio)
        hw, seconds = self._height_follow.per_second()     # each second with a skeleton weighs 1
        if seconds >= HEIGHT_GUARD_FOLLOW_MIN_SECONDS:
            from core.auto_height import weighted_quantile
            med = weighted_quantile(hw, 0.5)
            spread = (weighted_quantile(hw, 0.75) - weighted_quantile(hw, 0.25)) / max(med, 1.0)
            if spread <= HEIGHT_GUARD_MAX_SPREAD:
                if self._height_owned_px is not None:
                    if not ph / HEIGHT_GUARD_FOLLOW_RATIO <= med <= ph * HEIGHT_GUARD_FOLLOW_RATIO:
                        self._adopt_person_height(
                            ph, med, f"the dancers measure {med:.0f} px over {seconds} s of confident full "
                                     f"skeletons in the last {HEIGHT_GUARD_FOLLOW_S:.0f} s")
                        return
                else:
                    outside = sum(w for h, w in hw if not lo <= h <= hi) / float(seconds)
                    if outside >= HEIGHT_GUARD_OUTSIDE:
                        self._adopt_person_height(
                            ph, med, f"{outside:.0%} of {seconds} s of confident full skeletons (median "
                                     f"{med:.0f} px) fall outside the size gate {lo:.0f}-{hi:.0f} px")
                        return
        if not ready or self._height_owned_px is not None:   # owned: only the follow moves it (no ping-pong)
            return
        hs = sorted(self._height_guard.window())
        outside = sum(1 for h in hs if not lo <= h <= hi) / float(len(hs))
        med = hs[len(hs) // 2]
        spread = (hs[(3 * len(hs)) // 4] - hs[len(hs) // 4]) / max(med, 1.0)
        if outside >= HEIGHT_GUARD_OUTSIDE and spread <= HEIGHT_GUARD_MAX_SPREAD:
            self._adopt_person_height(
                ph, med, f"{outside:.0%} of the confident full skeletons ({len(hs)}, median {med:.0f} px) "
                         f"fall outside the size gate {lo:.0f}-{hi:.0f} px")

    def _adopt_person_height(self, old: float, med: float, why: str) -> None:
        new = max(1, int(round(med)))
        print(f"[HeightGuard] person height {old:.0f} -> {new} px: {why}")
        self.height_guard_event = (int(old), new)
        self.settings.person_height_px = new
        self._height_owned_px = new

    def _fg_protect(self, cands, t: float):
        """Boxes (original px) the plate update must not absorb: slots measured live or by a
        backed belt, and every reported track with a recent YOLO skeleton (a still dancer)."""
        boxes = []
        for s in self._slots.slots:
            if s.pos is None or s.wh is None:
                continue
            backed = s.state == STATE_BELT and t - getattr(s, "belt_backed_t", -1e9) <= 1.0
            if s.state == STATE_LIVE or backed:
                w, h = 0.6 * float(s.wh[1]), 1.2 * float(s.wh[1])
                boxes.append((s.pos[0] - w / 2, s.pos[1] - h / 2, s.pos[0] + w / 2, s.pos[1] + h / 2))
        for c in cands:
            if c.fss is not None and int(c.fss) <= 20:
                w, h = max(c.w, 0.6 * c.h), 1.2 * c.h
                boxes.append((c.x - w / 2, c.y - h / 2, c.x + w / 2, c.y + h / 2))
        return boxes

    def _zone_ok_fn(self, original_w: int, original_h: int):
        """Original-px point -> outside the exclusion mask (normalized over the
        ROI, like the MOG2 mask the cells are defined on)."""
        excl = self._exclusion
        if not excl.active:
            return lambda x, y: True
        roi = self._resolve_roi(original_w, original_h)
        x0, y0, x1, y1 = roi if roi is not None else (0, 0, original_w, original_h)
        w = max(1.0, float(x1 - x0))
        h = max(1.0, float(y1 - y0))
        return lambda x, y: not excl.excluded((x - x0) / w, (y - y0) / h)

    def _run_identity_slots(self, scaled_tracks, finalize,
                            original_w: int, original_h: int) -> List[ScaledTrack]:
        """Feed the reported tracks (+ the hidden state of slot-bound ones) to
        the slot layer and return the emitted slot ScaledTracks."""
        if self._slots is None:
            g = bool(self.settings.static_ghost_guard)
            self._slots = IdentitySlots(SlotParams(
                max_dancers=int(self.settings.max_dancers),
                stability=float(self.settings.stability),
                coast_s=float(self.settings.coast_s),
                static_guard=g, static_yield=g,
                static_release_s=float(self.settings.static_release_s),
                filter_input=str(self.settings.slot_filter_input),
                smart_hold=bool(self.settings.smart_hold),
                belt_backing=bool(self.settings.belt_backing),
                entry_min_travel_h=float(self.settings.entry_min_travel_h)))
        t = float(self._output_clock())
        if self._out_last_t is not None and t > self._out_last_t:
            self._out_dt = min(0.25, t - self._out_last_t)
        self._out_last_t = t
        zone_ok = self._zone_ok_fn(original_w, original_h)
        cands = []
        for st in scaled_tracks:
            c = candidate_from_track(st)
            if c is not None:
                c.zone_ok = zone_ok(c.x, c.y)
                cands.append(c)
        want = set(self._slots.bound_keys()) - {c.key for c in cands}
        hidden = {}
        if want:   # bound tracks the tracker kept alive but hid (frozen gate)
            for trk in self.tracker.tracks:
                if trk.track_id in want:
                    c = candidate_from_track(finalize(trk))
                    if c is not None:
                        hidden[c.key] = c
        roi = self._resolve_roi(original_w, original_h)
        bounds = tuple(float(v) for v in roi) if roi is not None else (0.0, 0.0, float(original_w), float(original_h))
        belt_hook = self._belt_hook()
        fg = self._fg_frame(original_w, original_h)
        outs = self._slots.update(cands, t, belt=belt_hook, hidden=hidden, bounds=bounds, fg=fg)
        if self._fg_detector is not None and self.last_fg is not None:
            self._fg_detector.update_plate(self._fg_protect(cands, t))
        self._log_slots(outs)
        return [self._slot_track(o) for o in outs]

    def _slot_track(self, o) -> ScaledTrack:
        """SlotOutput -> ScaledTrack on the slot id (OSC / preview / RTS).  The
        skeleton of the bound (or last bound) track is translated rigidly onto
        the smoothed centroid."""
        src = o.payload
        c = np.array([o.x, o.y], dtype=np.float64)
        if src is not None:
            sc = src.smoothed_centroid if src.smoothed_centroid is not None else c
            delta = c - np.asarray(sc, dtype=np.float64)
            kpts = np.asarray(src.keypoints, dtype=np.float64) + delta
            conf = np.asarray(src.confidence, dtype=np.float64).copy()
        else:
            kpts = np.tile(c, (17, 1))
            conf = np.zeros(17, dtype=np.float64)
        return ScaledTrack(
            track_id=int(o.slot_id),
            keypoints=kpts,
            confidence=conf,
            bbox=np.array([o.x - o.w / 2.0, o.y - o.h / 2.0, o.w, o.h], dtype=np.float64),
            history=[],
            velocity=np.array([o.vx, o.vy], dtype=np.float64) * self._out_dt,  # px/frame
            smoothed_centroid=c,
            is_bridged=o.state != STATE_LIVE or bool(getattr(src, "is_bridged", False)),
            frames_since_skeleton=o.fss if o.fss is not None else 999,
            centroid_raw=np.array([o.raw_x, o.raw_y], dtype=np.float64),
            hits=getattr(src, "hits", None),
            feed_src=getattr(src, "feed_src", None) if o.state == STATE_LIVE else o.state,
            slot_state=o.state,
            slot_age_s=o.age_s,
        )

    def _log_slots(self, outs) -> None:
        """Slot states into this frame's FRAME_SUMMARY (CONT-10) + SLOT_EVENT
        lines for binds / losses."""
        logger = getattr(self.tracker, "logger", None)
        if logger is None or not getattr(logger, "enabled", False):
            return
        by_sid = {o.slot_id: o for o in outs}
        rows = []
        for s in self._slots.slots:
            o = by_sid.get(s.sid)
            rows.append({"id": s.sid, "st": s.state,
                         "key": None if o is None else o.key,
                         "age": None if o is None else o.age_s})
        annotate = getattr(logger, "annotate_frame_summary", None)
        if callable(annotate):
            annotate(slots=rows, emitted_slots=[o.slot_id for o in outs])
        for ev in self._slots.events:
            logger.log("SLOT_EVENT", ev)

    def _run_output_smoother(self, scaled_tracks, L: int) -> List[ScaledTrack]:
        """Feed the reported tracks to the fixed-lag/RTS smoother and return the
        lagged (L-frames-late, RTS-smoothed) ScaledTracks released this frame.

        OUTPUT-ONLY (Track X §2): consumes ``centroid_raw`` (NOT the EMA
        ``smoothed_centroid`` — no cascade) and the reported box size; rebuilds
        each released track COHERENTLY — centroid + box on the RTS-smoothed
        trajectory, the keypoints rigidly translated by the same centroid
        correction so the skeleton stays aligned with the corrected centroid/box
        (esp. through retroactively-corrected gaps), and the velocity taken from
        the RTS-smoothed state (de-jittered, not the raw per-frame velocity).

        The smoother buffers each ScaledTrack as its release payload for L frames;
        callers must treat reported ScaledTracks as immutable (the lagged copies
        share keypoint/confidence arrays via ``replace``)."""
        if self._output_smoother is None:
            self._output_smoother = OutputSmoother(
                lag=L, max_age=int(getattr(self.tracker, 'max_age', TRACKER_MAX_AGE)))
        inputs: List[SmootherInput] = []
        for st in scaled_tracks:
            if st.centroid_raw is None or st.frames_since_skeleton is None:
                continue  # missing the smoother signals — skip (non-DancerTrack)
            fss = int(st.frames_since_skeleton)
            inputs.append(SmootherInput(
                track_id=int(st.track_id),
                centroid=np.asarray(st.centroid_raw, dtype=np.float64),
                wh=np.array([float(st.bbox[2]), float(st.bbox[3])],
                            dtype=np.float64),
                is_real_skeleton=(fss == 0),
                frames_since_skeleton=fss,
                payload=st,
            ))
        lagged: List[ScaledTrack] = []
        for o in self._output_smoother.process(inputs, lag=L):
            st0 = o.payload
            smoothed_c = np.array([float(o.centroid[0]), float(o.centroid[1])],
                                  dtype=np.float64)
            w, h = float(o.wh[0]), float(o.wh[1])
            cx, cy = smoothed_c
            # Rigid skeleton translation by the centroid correction (smoothed -
            # raw) keeps keypoints coherent with the corrected centroid/box.
            delta = smoothed_c - np.asarray(st0.centroid_raw, dtype=np.float64)
            lagged.append(replace(
                st0,
                smoothed_centroid=smoothed_c,
                centroid_raw=smoothed_c.copy(),
                bbox=np.array([cx - w / 2.0, cy - h / 2.0, w, h],
                              dtype=np.float64),
                keypoints=np.asarray(st0.keypoints, dtype=np.float64) + delta,
                velocity=np.asarray(o.velocity, dtype=np.float64),
            ))
        return lagged

    def _smooth_output_box_sizes(self, scaled_tracks):
        """Causal box-SIZE EMA on the reported boxes (Track X, OSC_CONTRACT §B.2).

        OUTPUT-ONLY 'smoothness vs latency' stage, applied only at **L=1** (the
        causal / live stream).  EMA the reported bbox SIZE around its own center
        (position/centroid are already EMA-smoothed); never touches DancerTrack.
        alpha = BOX_SIZE_OUTPUT_SMOOTHING (L=1) → ~1-frame group delay, light
        de-jitter.  For **L>1** the ACAUSAL fixed-lag / RTS smoother
        (``_run_output_smoother``: look-ahead buffer + retroactive bridge
        correction) IS the /walldance/dancer/* stream instead — this causal EMA
        is bypassed.  State is keyed by track_id and pruned to the reported set
        each frame (ids are unbounded over a show)."""
        L = max(1, int(self.settings.output_smoothing_l))
        alpha = max(0.05, min(1.0, BOX_SIZE_OUTPUT_SMOOTHING / L))
        ema = self._box_size_ema
        seen = set()
        for st in scaled_tracks:
            tid = int(st.track_id)
            seen.add(tid)
            w = float(st.bbox[2])
            h = float(st.bbox[3])
            prev = ema.get(tid)
            if prev is None:
                sw, sh = w, h
            else:
                sw = alpha * w + (1.0 - alpha) * prev[0]
                sh = alpha * h + (1.0 - alpha) * prev[1]
            ema[tid] = (sw, sh)
            cx = float(st.bbox[0]) + w / 2.0
            cy = float(st.bbox[1]) + h / 2.0
            st.bbox = np.array([cx - sw / 2.0, cy - sh / 2.0, sw, sh],
                               dtype=np.float64)
        if len(ema) != len(seen):
            for tid in [t for t in ema if t not in seen]:
                del ema[tid]

    def _configure_motion_detectors(self):
        """Apply the mode-specific learning rate to the single motion model.

        One MOG2 cannot serve both consumers' former rates (bridge 0.001 /
        crossval 0.005), so it runs at the SLOW rate bridging requires (a
        paused dancer must stay foreground for seconds).  Crossval's drift
        rejection moves to frame-differencing in Stage 3.
        """
        if self.motion_model is None:
            return
        rate = (MOTION_FIRST_MOG2_LEARN_RATE
                if self._tracking_mode == TrackingMode.MOTION_FIRST
                else MOTION_BRIDGE_MOG2_LEARN_RATE)
        self.motion_model.set_learn_rate(rate)

    def _enhance_gray_for_motion(self, gray: np.ndarray) -> np.ndarray:
        """Apply CLAHE + gamma to a single-channel gray frame.

        Uses the same enhancer settings (clahe_clip, gamma) that the GPU
        pipeline applies to the YOLO / preview path so the motion detector
        sees a contrast-boosted image identical to what appears on screen.
        """
        clip = self.enhancer.clahe_clip
        gamma = self.enhancer.gamma
        force = self.settings.enhance_force

        if not force and clip <= 1.0 and gamma == 1.0:
            return gray

        out = gray
        if clip > 1.0:
            if self._motion_clahe is None or self._motion_clahe_clip != clip:
                self._motion_clahe = cv2.createCLAHE(
                    clipLimit=clip, tileGridSize=(8, 8))
                self._motion_clahe_clip = clip
            out = self._motion_clahe.apply(out)

        if gamma != 1.0:
            if self._motion_gamma_lut is None or self._motion_gamma_val != gamma:
                inv = 1.0 / gamma
                self._motion_gamma_lut = np.array(
                    [((i / 255.0) ** inv) * 255 for i in range(256)],
                    dtype=np.uint8)
                self._motion_gamma_val = gamma
            out = cv2.LUT(out, self._motion_gamma_lut)

        return out

    def _start_motion_feed(self, raw_frame: Optional[np.ndarray], roi: Dict,
                           timing: Dict[str, float], mono_raw: bool = False
                           ) -> Optional[Tuple[float, np.ndarray]]:
        """Crop the motion gray to the ROI and submit it to the motion worker.

        Returns (submit time, gray) or None when no motion model consumes it
        (or there is no CPU frame).  ``roi`` is the GPU pipeline's resolved ROI
        for this frame (``GpuPipeline._resolve_roi``), so the motion crop and
        the GPU crop always agree.
        """
        if self._plate_capture is not None:
            self._feed_plate_capture(raw_frame)
        if raw_frame is None or (self.bridge_motion_detector is None
                                 and self.crossval_motion_detector is None):
            return None
        t_mog_start = time.perf_counter()
        motion_frame = raw_frame
        if roi.get('enabled'):
            roi_x = int(roi.get('x', 0))
            roi_y = int(roi.get('y', 0))
            roi_w = int(roi.get('w', 0))
            roi_h = int(roi.get('h', 0))
            motion_frame = raw_frame[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
        # P-1: a mono source is expanded to BGR with R==G==B, so the single
        # channel equals cv2 BGR2GRAY bit-for-bit (weights sum to 1.0) — skip
        # the conversion.  Only the IDS path sets mono_raw; everything else
        # (incl. replay/goldens) keeps cvtColor → byte-identical.
        gray = (np.ascontiguousarray(motion_frame[:, :, 0])
                if mono_raw and motion_frame.ndim == 3
                else cv2.cvtColor(motion_frame, cv2.COLOR_BGR2GRAY))
        t_submit = time.perf_counter()
        timing["mog2_cvt"] = (t_submit - t_mog_start) * 1000
        self._submit_motion_feed(gray)  # P-2
        return t_submit, gray

    def _submit_motion_feed(self, gray: np.ndarray) -> None:
        """P-2: hand the motion gray to the persistent worker (was a Thread
        spawned per frame). Pairs with ``_await_motion_feed`` (the old ``join``)."""
        self._motion_future = self._motion_executor.submit(
            self._feed_motion_detectors, gray)

    def _await_motion_feed(self) -> None:
        """Block until the in-flight motion feed finishes (the old ``join``).
        A feed error is logged, not raised — matching the old daemon thread,
        whose exceptions never propagated into the main loop."""
        fut, self._motion_future = self._motion_future, None
        if fut is None:
            return
        try:
            fut.result()
        except Exception:
            import traceback
            traceback.print_exc()

    def _feed_motion_detectors(self, gray: np.ndarray) -> None:
        """Feed the single motion model from one grayscale frame.

        Bug #1 fix (P3 Stage 3): feed a FIXED gray, NOT the per-frame adaptive
        CLAHE+gamma display signal.  Adaptive CLAHE amplifies noise differently
        each frame, so the textured background registers fake frame-to-frame
        "motion" — which the Stage-3a scored gate reads as a moving dancer and
        admits as a ghost.  A fixed gray keeps the frame-diff signal temporally
        clean (its whole point).  MotionModel applies an optional FIXED gamma
        (frame-independent, no jitter) to lift dark IR for MOG2 silhouettes.
        """
        if self.motion_model is None:
            return
        gray = self._fixed_gamma_for_motion(gray)
        self._last_motion_gray = gray
        self.motion_model.feed(gray)

    def _gate_cold_blobs(self, blobs):
        """Keep only cold-detection blobs that show real frame-diff motion and
        are outside exclusion zones (P3 Stage 3b).

        Cold detection should spawn a track from a *moving* dancer YOLO missed,
        not from static textured background — which produces MOG2 silhouettes
        (so ``detect()`` returns it) but no frame-to-frame change.  Blobs are in
        the motion model's native (ROI-local) coords here, before any letterbox/
        ROI-offset scaling, so the frame-diff + exclusion checks line up with
        the mask.
        """
        md = self.bridge_motion_detector
        if not blobs or md is None:
            return blobs
        theta_m = self.settings.crossval_motion_min_ratio
        kept = []
        for b in blobs:
            bx, by, bw, bh = b.bbox
            _blob, ratio = md.frame_diff_blob_in_bbox(
                float(bx), float(by), float(bw), float(bh), min_ratio=theta_m)
            if ratio < theta_m:
                continue
            nxy = self._exclusion_norm_xy(
                float(b.centroid[0]), float(b.centroid[1]), md,
                1.0, 0, 0, 0, 0, True)
            if nxy is not None and self._exclusion.excluded(*nxy):
                continue
            kept.append(b)
        return kept

    def _fixed_gamma_for_motion(self, gray: np.ndarray) -> np.ndarray:
        """Apply ONLY the fixed display gamma to the motion gray — no adaptive
        CLAHE (Bug #1 fix).

        Gamma is a frame-independent monotonic LUT, so it lifts dark IR for both
        MOG2 silhouettes and frame-diff WITHOUT introducing the per-frame noise
        amplification that adaptive CLAHE does.  This is what keeps the
        frame-diff ghost signal temporally clean while the dancer stays visible.
        """
        gamma = self.enhancer.gamma
        if gamma == 1.0:
            return gray
        if self._motion_gamma_lut is None or self._motion_gamma_val != gamma:
            inv = 1.0 / gamma
            self._motion_gamma_lut = np.array(
                [((i / 255.0) ** inv) * 255 for i in range(256)], dtype=np.uint8)
            self._motion_gamma_val = gamma
        return cv2.LUT(gray, self._motion_gamma_lut)

    @property
    def bridge_motion_detector(self):
        """Read-only view of the single MotionModel's detector, for bridging.

        Active when bridging is enabled or in motion-first mode.  (Stage-2
        compatibility shim; the tracker bridge migrates to the MotionModel
        surface in Stage 3.)
        """
        if self.motion_model is None:
            return None
        if MOTION_BRIDGE_ENABLED or self._tracking_mode == TrackingMode.MOTION_FIRST:
            return self.motion_model.detector
        return None

    @property
    def crossval_motion_detector(self):
        """Read-only view of the single MotionModel's detector, for cross-validation."""
        if self.motion_model is None or not MOTION_CROSSVAL_ENABLED:
            return None
        return self.motion_model.detector

    @property
    def motion_detector(self):
        """Backward-compatible primary motion detector accessor."""
        return self.bridge_motion_detector or self.crossval_motion_detector

    def get_motion_scale(self) -> float:
        """Return the current MOG2 scale."""
        return self.motion_model.scale if self.motion_model is not None else 0.75

    def get_motion_sensitivity(self) -> float:
        """Return the current motion-bridge sensitivity."""
        return float(self.settings.motion_sensitivity)

    def set_motion_scale(self, scale: float) -> None:
        """Apply the MOG2 scale to the motion model (keeps model + detector
        scale in sync so coordinate transforms stay correct)."""
        if self.motion_model is not None:
            self.motion_model.set_scale(scale)

    def set_motion_sensitivity(self, sensitivity: float) -> None:
        """Update bridge sensitivity for runtime recovery behavior."""
        value = max(0.0, min(1.0, float(sensitivity)))
        self.settings.motion_sensitivity = value
        self.tracker.set_motion_bridge_sensitivity(value)

    def set_box_clamp_enabled(self, enabled: bool) -> None:
        """Toggle the output box-clamp (Track X §B.1).  Output-only."""
        self.settings.box_clamp_enabled = bool(enabled)

    def set_output_smoothing(self, depth: int) -> None:
        """Set the output smoothing depth / latency selector L (Track X §B).

        L>=1.  L=1 → causal/live; L>1 → the fixed-lag RTS-smoothed stream,
        released L frames late, on the single /walldance/dancer/* tap."""
        self.settings.output_smoothing_l = max(1, int(depth))

    def get_last_motion_gray(self) -> Optional[np.ndarray]:
        """The most recent enhanced gray fed to MOG2 (Go-Live noise calibration).

        This is the post-CLAHE/gamma signal the background model actually sees,
        so its temporal noise is the right thing to set varThreshold from.
        Returns None if no frame has been processed through motion detection.
        """
        return self._last_motion_gray

    def get_motion_var_threshold(self) -> float:
        """Return the current MOG2 base varThreshold (Go-Live calibration)."""
        if self.motion_model is not None:
            return self.motion_model.get_var_threshold()
        return float(MOTION_BRIDGE_MOG2_VAR_THRESHOLD)

    def set_motion_var_threshold(self, base: float) -> None:
        """Apply the MOG2 base varThreshold to the motion model."""
        if self.motion_model is not None:
            self.motion_model.set_var_threshold(base)

    # ------------------------------------------------------------------
    # Auto exclusion mask (P1.4)
    # ------------------------------------------------------------------
    def _exclusion_norm_xy(self, cx, cy, motion_det, scale, pad_x, pad_y,
                           roi_x, roi_y, roi_local):
        """Map a tracker-space centroid to normalized [0,1] coords over the MOG2
        mask, mirroring the crossval tracker→original→ROI-local→mask transform."""
        mask = motion_det._clean_mask if motion_det is not None else None
        if mask is None:
            return None
        # Pad must be subtracted even at scale == 1.0 — the letterbox can pad
        # one axis without scaling (e.g. 1280x720 ROI @ imgsz 1280). Bug #9.
        inv = 1.0 / scale if scale > 0 else 1.0
        ox = (cx - pad_x) * inv
        oy = (cy - pad_y) * inv
        if not roi_local:
            ox -= roi_x
            oy -= roi_y
        s = motion_det._scale
        mh, mw = mask.shape[:2]
        if mw <= 0 or mh <= 0:
            return None
        return (ox * s) / mw, (oy * s) / mh

    def _observe_exclusion(self, detections, motion_det, params):
        """Feed one frame to the exclusion builder during calibration."""
        if motion_det is None or motion_det._clean_mask is None:
            return
        pts = []
        for _kpts, _conf, bbox in detections:
            nxy = self._exclusion_norm_xy(
                bbox[0] + bbox[2] * 0.5, bbox[1] + bbox[3] * 0.5,
                motion_det, *params)
            if nxy is not None:
                pts.append(nxy)
        self._exclusion.observe(motion_det._clean_mask, pts)

    def _apply_exclusion(self, detections, motion_det, params, person_height):
        """Drop detections in excluded (ghost) cells, unless near a confirmed
        track (a real dancer who wandered into a masked region is protected)."""
        if motion_det is None or motion_det._clean_mask is None or not detections:
            return detections, 0
        guard = person_height * 0.6
        track_centroids = [
            t.get_centroid() for t in self.tracker.tracks
            if t.time_since_update <= MOTION_CROSSVAL_BYPASS_MAX_AGE
        ]
        kept, rejected = [], 0
        for det in detections:
            kpts, conf, bbox = det
            cx = bbox[0] + bbox[2] * 0.5
            cy = bbox[1] + bbox[3] * 0.5
            nxy = self._exclusion_norm_xy(cx, cy, motion_det, *params)
            if nxy is not None and self._exclusion.excluded(*nxy):
                near = any(float(np.hypot(cx - tc[0], cy - tc[1])) <= guard
                           for tc in track_centroids)
                if not near:
                    rejected += 1
                    continue
            kept.append(det)
        return kept, rejected

    def _exclusion_step(self, detections, motion_det, params, person_height, timing):
        """Collect (during calibration) or apply (when a mask is active)."""
        if self._exclusion.collecting:
            self._observe_exclusion(detections, motion_det, params)
            return detections
        if self._exclusion.active:
            detections, n = self._apply_exclusion(
                detections, motion_det, params, person_height)
            timing["exclusion_rejected"] = n
        return detections

    # -- exclusion public API (driven by the app's Calibrate flow) ------
    def start_exclusion_calibration(self) -> None:
        self._exclusion.start()

    def cancel_exclusion_calibration(self) -> None:
        self._exclusion.cancel()

    def finish_exclusion_calibration(self):
        """Build the mask from the collected window and return an ExclusionResult."""
        return self._exclusion.build()

    def set_exclusion(self, grid, cells, manual_add=(), manual_remove=()) -> None:
        self._exclusion.set_cells(grid, cells, manual_add, manual_remove)

    def get_exclusion(self) -> tuple:
        """(grid, effective cells) — the mask as applied."""
        return self._exclusion.get_cells()

    def get_exclusion_state(self) -> tuple:
        """(grid, auto, manual_add, manual_remove) — the split, for persistence."""
        return self._exclusion.get_state()

    def toggle_exclusion_cell(self, nx: float, ny: float):
        """Toggle the mask cell under a normalized [0,1] point (manual editor).

        Returns (col, row, new_state) or None when outside the grid.
        """
        cell = self._exclusion.cell_at(nx, ny)
        if cell is None:
            return None
        state = self._exclusion.toggle_cell(*cell)
        return (*cell, state)

    def paint_exclusion_cell(self, nx: float, ny: float, excluded: bool):
        """Force the mask cell under a normalized point (paint-drag).

        Returns (col, row) or None when outside the grid.
        """
        cell = self._exclusion.cell_at(nx, ny)
        if cell is None:
            return None
        self._exclusion.set_cell(*cell, excluded)
        return cell

    def clear_exclusion(self) -> None:
        self._exclusion.clear()

    def reset_motion_detectors(self) -> None:
        """Reset the motion model and clear cross-validation state."""
        if self.motion_model is not None:
            self.motion_model.reset()
        self._crossval_motion_memory.clear()
        self._crossval_no_track_frames = 0
        self._crossval_motion_cells.clear()

    def _get_letterbox_motion_detector(self):
        """Return a proxy that scales blob coords to letterboxed space, or None."""
        if self.bridge_motion_detector is None:
            return None
        return _LetterboxMotionProxy(
            self.bridge_motion_detector,
            self._motion_lb_scale,
            self._motion_pad_x,
            self._motion_pad_y,
        )

    def set_tracking_mode(self, mode: TrackingMode):
        """Switch tracking mode and keep the motion model aligned."""
        self._tracking_mode = mode
        if self.motion_model is None and (
                MOTION_BRIDGE_ENABLED or MOTION_CROSSVAL_ENABLED
                or mode == TrackingMode.MOTION_FIRST):
            self.motion_model = MotionModel()
        self._configure_motion_detectors()

    def _unscale_letterbox(
        self,
        track: DancerTrack,
        lb_scale: float,
        pad_x: int,
        pad_y: int,
        roi_x: int = 0,
        roi_y: int = 0,
        clamp_to_yolo_size: bool = False,
    ) -> ScaledTrack:
        """
        Unscale track from letterboxed YOLO space to original camera space.
        
        Letterbox applies: original -> scale down -> pad to square
        To reverse: subtract padding -> divide by scale
        
        Args:
            track: Track with coords in letterboxed space
            lb_scale: Scale factor that was applied (original * scale = letterboxed)
            pad_x: Horizontal padding added (left side)
            pad_y: Vertical padding added (top side)
        """
        # Subtract padding, then divide by scale
        pad_xy = np.array([pad_x, pad_y])
        roi_offset = np.array([roi_x, roi_y])
        inv_scale = 1.0 / lb_scale if lb_scale > 0 else 1.0
        
        # Keypoints: (x, y) -> subtract pad -> divide by scale
        keypoints = (track.keypoints - pad_xy) * inv_scale + roi_offset
        
        # Bbox: (x, y, w, h) -> x,y subtract pad and scale; w,h just scale.
        # Output box-clamp (Track X): while motion-bridged, substitute a
        # last-YOLO-size box at the smoothed centroid — output-only, never
        # mutates track.bbox.  clamp_to_yolo_size defaults False so the
        # transform unit tests (self=None, stub track w/o reported_bbox) keep
        # exercising the raw path.
        bbox = (track.reported_bbox(True) if clamp_to_yolo_size
                else track.bbox.copy())
        bbox[0] = (bbox[0] - pad_x) * inv_scale + roi_x  # x
        bbox[1] = (bbox[1] - pad_y) * inv_scale + roi_y  # y
        bbox[2] = bbox[2] * inv_scale  # w
        bbox[3] = bbox[3] * inv_scale  # h
        
        # History and velocity (guard against overflow from inf/NaN in tracker)
        history = [(pt - pad_xy) * inv_scale + roi_offset for pt in track.history]
        velocity = track.get_velocity() * inv_scale
        if not np.all(np.isfinite(velocity)):
            velocity = np.zeros(2, dtype=np.float64)

        # Smoothed centroid (same unscale transform)
        sm_centroid = (track.get_smoothed_centroid() - pad_xy) * inv_scale + roi_offset

        # Raw KF centroid (Track X §2) — output-only de-jitter input; gets the
        # SAME unscale transform as the smoothed centroid.  Guarded so the
        # transform unit tests (self=None, stub track without get_centroid) keep
        # exercising the raw path.
        get_raw = getattr(track, 'get_centroid', None)
        if callable(get_raw):
            centroid_raw = (get_raw() - pad_xy) * inv_scale + roi_offset
        else:
            centroid_raw = None

        return ScaledTrack(
            track_id=track.track_id,
            keypoints=keypoints,
            confidence=track.confidence.copy(),
            bbox=bbox,
            history=history,
            velocity=velocity,
            smoothed_centroid=sm_centroid,
            is_bridged=getattr(track, 'is_bridged', False),
            frames_since_skeleton=getattr(track, '_frames_since_skeleton', None),
            centroid_raw=centroid_raw,
            hits=getattr(track, 'hits', None),
            age=getattr(track, 'age', None),
            time_since_update=getattr(track, 'time_since_update', None),
            feed_src=getattr(track, '_feed_src', None),
        )

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _bbox_conf_key(bbox) -> tuple:
        """Value key for the per-frame bbox→box-conf map (Phase 2 ⑤a).

        DancerTrack.update stores the matched detection bbox verbatim, so an
        exact (rounded) value match recovers which YOLO box fed a track.
        """
        return tuple(round(float(v), 1) for v in bbox[:4])

    def _extract_detections(self, results) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        detections = []
        kpts_cpu_ms = 0.0
        boxes_cpu_ms = 0.0
        # Per-frame YOLO box confidences keyed by bbox value — calib2's
        # sensitivity seed needs them in the SAME units settings.confidence
        # thresholds (keypoint-conf units pin the seed; ROADMAP bug #11 /
        # CORPUS_ANALYSIS §6.5a).  Duplicate filtering reuses the surviving
        # bbox arrays, so survivors always resolve in this map.
        self._last_box_confs = {}
        # (#3) reset per call so a zero-detection frame reports no STALE GPU→CPU
        # transfer timing — the per-detection assignment below only fires when
        # there are detections, so without this it kept the last frame's value.
        self._extract_transfer_timing = {}
        for result in results:
            if result.keypoints is None or len(result.keypoints) == 0:
                continue
            t0 = time.perf_counter()
            keypoints_data = result.keypoints.data.cpu().numpy()
            kpts_cpu_ms += (time.perf_counter() - t0) * 1000.0

            if result.boxes is not None:
                t0 = time.perf_counter()
                boxes = result.boxes.xyxy.cpu().numpy()
                box_confs = result.boxes.conf.cpu().numpy()
                boxes_cpu_ms += (time.perf_counter() - t0) * 1000.0
            else:
                boxes = None
                box_confs = None

            for i, kpts in enumerate(keypoints_data):
                keypoints = kpts[:, :2]
                confidence = kpts[:, 2]
                if boxes is not None and i < len(boxes):
                    x1, y1, x2, y2 = boxes[i]
                    bbox = (x1, y1, x2 - x1, y2 - y1)
                    if box_confs is not None and i < len(box_confs):
                        self._last_box_confs[self._bbox_conf_key(bbox)] = \
                            float(box_confs[i])
                else:
                    valid = confidence > KEYPOINT_CONFIDENCE
                    if np.any(valid):
                        xs = keypoints[valid, 0]
                        ys = keypoints[valid, 1]
                        bbox = (xs.min(), ys.min(), xs.max() - xs.min(), ys.max() - ys.min())
                    else:
                        continue
                detections.append((keypoints, confidence, np.array(bbox)))

                self._extract_transfer_timing = {
                    "extract_kpts_cpu": kpts_cpu_ms,
                    "extract_boxes_cpu": boxes_cpu_ms,
                    "extract_cpu_total": kpts_cpu_ms + boxes_cpu_ms,
                }
        return detections

    @staticmethod
    def _bbox_iou_xywh(box_a: np.ndarray, box_b: np.ndarray) -> float:
        """Return IoU for two (x, y, w, h) boxes."""
        ax1, ay1, aw, ah = box_a
        bx1, by1, bw, bh = box_b
        ax2, ay2 = ax1 + aw, ay1 + ah
        bx2, by2 = bx1 + bw, by1 + bh
        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h
        if inter_area <= 0.0:
            return 0.0
        area_a = max(0.0, aw) * max(0.0, ah)
        area_b = max(0.0, bw) * max(0.0, bh)
        denom = area_a + area_b - inter_area
        if denom <= 0.0:
            return 0.0
        return float(inter_area / denom)

    def _crossval_cell_key(self, bbox: np.ndarray, person_height: int) -> tuple[int, int]:
        """Stable spatial key for cross-validation hysteresis."""
        cell_size = max(16.0, person_height * MOTION_CROSSVAL_CELL_RATIO)
        cx = bbox[0] + bbox[2] * 0.5
        cy = bbox[1] + bbox[3] * 0.5
        return int(cx / cell_size), int(cy / cell_size)

    def _crossval_motion_filter(
        self,
        detections,
        motion_det: MotionDetector | None,
        person_height: int,
        scale: float = 1.0,
        letterbox_pad_x: float = 0.0,
        letterbox_pad_y: float = 0.0,
        roi_x: float = 0.0,
        roi_y: float = 0.0,
        roi_local_after_unscale: bool = False,
    ):
        """Ghost-reject YOLO detections with a single scored gate (P3 Stage 3a).

        Keeps a detection if ANY of:
          • SKELETON — strong, well-resolved skeleton (θ_s: kpts + mean conf)
          • MOTION   — recent FRAME-DIFF motion in the box (θ_m).  Frame-diff,
                       not MOG2 foreground: static textured background + slow
                       lighting drift read as MOG2 foreground but show NO
                       frame-to-frame change, so this rejects exactly those
                       ghosts while catching a moving (new) dancer.
          • TRACK    — overlaps a live, motion-confirmed track (continuity).
        Else REJECT.  (Detections in masked dead zones are dropped upstream by
        the P1.4 exclusion step.)  This replaces the former 7-step tree.

        Returns:
            Tuple of (kept_detections, stats_dict)
        """
        stats = {
            "crossval_kept_skeleton": 0,
            "crossval_kept_motion": 0,
            "crossval_kept_track": 0,
            "crossval_rejected": 0,
            "crossval_skip_disabled": 0,
            "crossval_skip_no_motion": 0,
        }
        if not MOTION_CROSSVAL_ENABLED:
            stats["crossval_skip_disabled"] = 1
            return detections, stats
        if motion_det is None:
            stats["crossval_skip_no_motion"] = 1
            return detections, stats
        if not detections:
            self._crossval_motion_memory = {}
            return detections, stats

        # θ_s / θ_m (calibration-settable; see ProcessingSettings).
        skel_min_kpts = self.settings.crossval_skel_min_kpts
        skel_min_conf = self.settings.crossval_skel_min_conf
        motion_threshold = self.settings.crossval_motion_min_ratio
        is_lowlight = motion_det.last_brightness < MOTION_LOWLIGHT_LUMA_THRESHOLD
        if is_lowlight:
            # Raise the motion bar in low light so sensor noise isn't read as motion.
            motion_threshold *= MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT

        # TRACK candidates: live tracks near a recently motion-confirmed cell.
        # The motion-cell guard prevents a static ghost track from granting
        # continuity to its own re-detection.
        bypass_candidates = []
        bypass_gate = person_height * 0.6
        current_frame = getattr(self.tracker, 'frame_count', 0)
        if MOTION_CROSSVAL_EXISTING_TRACK_BYPASS:
            for track in self.tracker.tracks:
                if track.time_since_update > MOTION_CROSSVAL_BYPASS_MAX_AGE:
                    continue
                if getattr(track, '_warmup_score', 0.0) < MOTION_CROSSVAL_BYPASS_MIN_WARMUP:
                    continue
                track_cell = self._crossval_cell_key(track.bbox, person_height)
                motion_age = current_frame - self._crossval_motion_cells.get(track_cell, -9999)
                if motion_age > MOTION_CROSSVAL_BYPASS_MAX_AGE * 3:
                    tx, ty = track_cell
                    any_near = any(
                        (current_frame - self._crossval_motion_cells.get((tx+dx, ty+dy), -9999))
                        <= MOTION_CROSSVAL_BYPASS_MAX_AGE * 3
                        for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                        if (dx, dy) != (0, 0)
                    )
                    if not any_near:
                        continue
                bypass_candidates.append(track)

        kept = []
        next_memory: Dict[tuple[int, int], float] = {}

        for kpts, conf, bbox in detections:
            cell = self._crossval_cell_key(bbox, person_height)

            # Convert tracker-space bbox -> original-space -> ROI-local mask
            # space. Pad is subtracted unconditionally: scale == 1.0 can still
            # carry a one-axis letterbox pad (bug #9; _unscale_letterbox is
            # the reference).
            inv = 1.0 / scale if scale > 0 else 1.0
            ox = (bbox[0] - letterbox_pad_x) * inv
            oy = (bbox[1] - letterbox_pad_y) * inv
            ow = bbox[2] * inv
            oh = bbox[3] * inv
            if roi_local_after_unscale:
                mask_x, mask_y = ox, oy
            else:
                mask_x, mask_y = ox - roi_x, oy - roi_y

            # MOTION evidence: frame-diff fraction in the box, EMA-smoothed per
            # cell so a dancer mid-stride (momentary low motion) doesn't flicker.
            _blob, raw_motion = motion_det.frame_diff_blob_in_bbox(
                mask_x, mask_y, ow, oh, min_ratio=motion_threshold)
            prev_score = self._crossval_motion_memory.get(cell, 0.0)
            motion = (MOTION_CROSSVAL_EMA_ALPHA * raw_motion
                      + (1.0 - MOTION_CROSSVAL_EMA_ALPHA) * prev_score)
            next_memory[cell] = motion

            # SKELETON evidence.
            visible = conf > KEYPOINT_CONFIDENCE
            n_valid = int(np.sum(visible))
            mean_conf = float(np.mean(conf[visible])) if n_valid > 0 else 0.0
            skel_ok = n_valid >= skel_min_kpts and mean_conf >= skel_min_conf

            # TRACK evidence: overlaps a live, motion-confirmed track.
            det_centroid = np.array([bbox[0] + bbox[2] * 0.5,
                                     bbox[1] + bbox[3] * 0.5])
            near_track = False
            for track in bypass_candidates:
                if float(np.linalg.norm(det_centroid - track.get_centroid())) <= bypass_gate:
                    near_track = True
                    break
                if self._bbox_iou_xywh(bbox, track.bbox) > 0.1:
                    near_track = True
                    break

            # ── Scored gate ────────────────────────────────────────────
            if near_track:
                kept.append((kpts, conf, bbox))
                stats["crossval_kept_track"] += 1
                self._crossval_motion_cells[cell] = current_frame
            elif motion >= motion_threshold:
                kept.append((kpts, conf, bbox))
                stats["crossval_kept_motion"] += 1
                self._crossval_motion_cells[cell] = current_frame
            elif skel_ok:
                kept.append((kpts, conf, bbox))
                stats["crossval_kept_skeleton"] += 1
            else:
                stats["crossval_rejected"] += 1

        self._crossval_motion_memory = next_memory
        return kept, stats

    def _filter_duplicate_detections(self, detections, effective_person_height: int | None = None):
        if len(detections) <= 1:
            return detections

        ph = effective_person_height if effective_person_height is not None else self.settings.person_height_px

        # Conservative thresholds — require strong evidence before merging.
        # Centroid AND keypoint must BOTH be close (except for very-high IoU).
        centroid_dist_thresh = ph * 0.3
        keypoint_dist_thresh = ph * 0.1
        min_height = ph * self.settings.person_height_min_ratio
        max_height = ph * self.settings.person_height_max_ratio

        size_filtered = []
        small_detections = []
        for kpts, conf, bbox in detections:
            h = bbox[3]
            if h < min_height:
                small_detections.append((kpts, conf, bbox))
                continue
            if h > max_height:
                continue
            size_filtered.append((kpts, conf, bbox))

        for small_kpts, small_conf, small_bbox in small_detections:
            small_center = np.array([small_bbox[0] + small_bbox[2] / 2, small_bbox[1] + small_bbox[3] / 2])
            best_match = None
            best_dist = float("inf")
            for i, (_, _, bbox) in enumerate(size_filtered):
                main_center = np.array([bbox[0] + bbox[2] / 2, bbox[1] + bbox[3] / 2])
                dist = np.linalg.norm(small_center - main_center)
                if dist < ph * 1.5 and dist < best_dist:
                    best_dist = dist
                    best_match = i
            if best_match is not None:
                main_kpts, main_conf, main_bbox = size_filtered[best_match]
                merged_kpts = main_kpts.copy()
                merged_conf = main_conf.copy()
                for k in range(len(main_kpts)):
                    if small_conf[k] > main_conf[k]:
                        merged_kpts[k] = small_kpts[k]
                        merged_conf[k] = small_conf[k]
                size_filtered[best_match] = (merged_kpts, merged_conf, main_bbox)

        if len(size_filtered) <= 1:
            return size_filtered

        # --- Shadow suppression (pre-tracker) ---
        # Low-quality detections near a high-quality detection are likely
        # shadow ghosts. Suppress them before pairwise NMS.
        shadow_radius = ph * SHADOW_PROXIMITY_RATIO
        shadow_suppressed = set()
        for i, (kpts_i, conf_i, bbox_i) in enumerate(size_filtered):
            n_valid_i = int(np.sum(conf_i > KEYPOINT_CONFIDENCE))
            mean_conf_i = (float(np.mean(conf_i[conf_i > KEYPOINT_CONFIDENCE]))
                           if n_valid_i > 0 else 0.0)
            is_low_i = (n_valid_i < SHADOW_QUALITY_MIN_KEYPOINTS
                        or mean_conf_i < SHADOW_QUALITY_MIN_CONFIDENCE)
            if not is_low_i:
                continue
            # This detection has weak skeleton — check if a strong one
            # is nearby.
            cent_i = np.array([bbox_i[0] + bbox_i[2] / 2,
                               bbox_i[1] + bbox_i[3] / 2])
            for j, (kpts_j, conf_j, bbox_j) in enumerate(size_filtered):
                if j == i or j in shadow_suppressed:
                    continue
                n_valid_j = int(np.sum(conf_j > KEYPOINT_CONFIDENCE))
                mean_conf_j = (float(np.mean(conf_j[conf_j > KEYPOINT_CONFIDENCE]))
                               if n_valid_j > 0 else 0.0)
                is_high_j = (n_valid_j >= SHADOW_QUALITY_MIN_KEYPOINTS
                             and mean_conf_j >= SHADOW_QUALITY_MIN_CONFIDENCE)
                if not is_high_j:
                    continue
                cent_j = np.array([bbox_j[0] + bbox_j[2] / 2,
                                   bbox_j[1] + bbox_j[3] / 2])
                if np.linalg.norm(cent_i - cent_j) < shadow_radius:
                    shadow_suppressed.add(i)
                    break

        if shadow_suppressed:
            size_filtered = [det for idx, det in enumerate(size_filtered)
                             if idx not in shadow_suppressed]
            if len(size_filtered) <= 1:
                return size_filtered

        det_with_area = [(i, kpts, conf, bbox, bbox[2] * bbox[3]) for i, (kpts, conf, bbox) in enumerate(size_filtered)]
        det_with_area.sort(key=lambda x: x[4], reverse=True)

        kept_indices = []
        suppressed = set()
        for i, kpts_i, conf_i, bbox_i, _ in det_with_area:
            if i in suppressed:
                continue
            kept_indices.append(i)
            for j, kpts_j, conf_j, bbox_j, _ in det_with_area:
                if j in suppressed or j == i:
                    continue
                if self._should_merge(bbox_i, bbox_j, kpts_i, conf_i, kpts_j, conf_j, centroid_dist_thresh, keypoint_dist_thresh):
                    for k in range(len(kpts_i)):
                        if conf_j[k] > conf_i[k]:
                            kpts_i[k] = kpts_j[k]
                            conf_i[k] = conf_j[k]
                    suppressed.add(j)
                    
        return [size_filtered[i] for i in sorted(kept_indices)]

    def _should_merge(self, bbox_i, bbox_j, kpts_i, conf_i, kpts_j, conf_j, centroid_dist_thresh, keypoint_dist_thresh) -> bool:
        # 1. Very high IoU → almost certainly the same person detected twice
        iou = self._compute_iou(bbox_i, bbox_j)
        if iou > 0.7:
            return True
        # 2. One bbox fully contained in the other (sub-detection / body part)
        if self._bbox_contains(bbox_i, bbox_j):
            return True
        # 3. Centroid AND keypoint proximity — both must hold (AND, not OR).
        #    This prevents merging two real people whose centroids happen to
        #    be close but whose skeletons clearly differ.
        mask_i = conf_i > KEYPOINT_CONFIDENCE
        mask_j = conf_j > KEYPOINT_CONFIDENCE
        if np.any(mask_i) and np.any(mask_j):
            cent_i = np.average(kpts_i[mask_i], axis=0, weights=conf_i[mask_i])
            cent_j = np.average(kpts_j[mask_j], axis=0, weights=conf_j[mask_j])
            centroid_close = np.linalg.norm(cent_i - cent_j) < centroid_dist_thresh
            keypoints_close = False
            if np.sum(mask_i & mask_j) >= 5:
                both_valid = mask_i & mask_j
                kpt_dists = np.linalg.norm(kpts_i[both_valid] - kpts_j[both_valid], axis=1)
                keypoints_close = np.median(kpt_dists) < keypoint_dist_thresh
            if centroid_close and keypoints_close:
                return True
        return False

    @staticmethod
    def _compute_iou(bbox1, bbox2) -> float:
        x1, y1, w1, h1 = bbox1
        x2, y2, w2, h2 = bbox2
        box1 = (x1, y1, x1 + w1, y1 + h1)
        box2 = (x2, y2, x2 + w2, y2 + h2)
        ix1 = max(box1[0], box2[0])
        iy1 = max(box1[1], box2[1])
        ix2 = min(box1[2], box2[2])
        iy2 = min(box1[3], box2[3])
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        intersection = (ix2 - ix1) * (iy2 - iy1)
        area1 = w1 * h1
        area2 = w2 * h2
        union = area1 + area2 - intersection
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _bbox_contains(outer, inner) -> bool:
        x1, y1, w1, h1 = outer
        x2, y2, w2, h2 = inner
        cx = x2 + w2 / 2
        cy = y2 + h2 / 2
        in_x = x1 <= cx <= x1 + w1
        in_y = y1 <= cy <= y1 + h1
        size_ratio = (w2 * h2) / (w1 * h1) if w1 * h1 > 0 else 1.0
        return in_x and in_y and size_ratio < 0.5

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def timing(self) -> Dict[str, float]:
        return self._timing