"""
Frame processing pipeline for WallDance.
Handles enhancement, YOLO inference, duplicate filtering, tracking, and OSC output.
Supports full GPU pipeline for zero-copy processing (see gpu_pipeline.py).
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from ultralytics import YOLO

from config import (
    BG_SUBTRACT_ENABLED,
    BG_SUBTRACT_SENSITIVITY,
    BRIGHTNESS_THRESHOLD,
    KEYPOINT_CONFIDENCE,
    PERSON_HEIGHT_MAX_RATIO,
    PERSON_HEIGHT_MIN_RATIO,
    YOLO_IOU_THRESHOLD,
    USE_GPU_PATH,
    SHADOW_QUALITY_MIN_KEYPOINTS,
    SHADOW_QUALITY_MIN_CONFIDENCE,
    SHADOW_PROXIMITY_RATIO,
    MOTION_BRIDGE_ENABLED,
    MOTION_BRIDGE_MOG2_LEARN_RATE,
    MOTION_BRIDGE_MOG2_VAR_THRESHOLD,
    MOTION_BRIDGE_SENSITIVITY,
    TrackingMode,
    MOTION_FIRST_BLOB_OVERLAP_RATIO,
    MOTION_FIRST_ASPECT_RANGE,
    MOTION_FIRST_INCLUDE_SHADOWS,
    MOTION_CROSSVAL_ENABLED,
    MOTION_CROSSVAL_CORE_SCALE,
    MOTION_CROSSVAL_EMA_ALPHA,
    MOTION_CROSSVAL_STICKY_RATIO,
    MOTION_CROSSVAL_CELL_RATIO,
    MOTION_CROSSVAL_MIN_FG_RATIO,
    MOTION_CROSSVAL_EXISTING_TRACK_BYPASS,
    MOTION_CROSSVAL_BYPASS_MAX_AGE,
    MOTION_CROSSVAL_WARMUP_FRAMES,
    MOTION_CROSSVAL_WARMUP_MIN_KPTS,
    MOTION_CROSSVAL_WARMUP_MIN_CONF,
    MOTION_CROSSVAL_MOG2_LEARN_RATE,
    MOTION_FIRST_MOG2_LEARN_RATE,
    MOTION_LOWLIGHT_LUMA_THRESHOLD,
    MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT,
    MOTION_CROSSVAL_LOWLIGHT_MIN_VALID_KPTS,
    MOTION_CROSSVAL_LOWLIGHT_MIN_MEAN_CONF,
    MOTION_CROSSVAL_REACQUIRE_FRAMES,
    MOTION_CROSSVAL_REACQUIRE_MIN_KPTS,
    MOTION_CROSSVAL_REACQUIRE_MIN_CONF,
    MOTION_CROSSVAL_BYPASS_MIN_WARMUP,
    MOTION_CROSSVAL_CONFIDENT_MIN_KPTS,
    MOTION_CROSSVAL_CONFIDENT_MIN_CONF,
)
from background import BackgroundSubtractor
from enhancer import ImageEnhancer, TORCH_CUDA_AVAILABLE
from motion_detector import MotionDetector
from motion_model import MotionModel
from calibration import ExclusionMaskBuilder
from osc_output import OSCSender
from tracker import DancerTrack, DancerTracker

# Import GPU pipeline (optional, for zero-copy GPU path)
try:
    from gpu_pipeline import GpuPipeline, GpuPipelineSettings, CUDA_AVAILABLE as GPU_CUDA_AVAILABLE, KORNIA_AVAILABLE
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
    brightness_threshold: int = 60  # Auto-bypass threshold (0-255)
    denoise_strength: float = 0.0   # Temporal denoising (0.0-1.0)
    greyscale: bool = False         # Convert to greyscale (mono camera simulation)
    osc_enabled: bool = True
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


class _OffsetMotionProxy:
    """Proxy that offsets blob coords from ROI-local space to full-frame space."""

    def __init__(self, detector, offset_x: int, offset_y: int):
        self._detector = detector
        self._offset_x = offset_x
        self._offset_y = offset_y

    def detect(self, person_height: int, **detect_kwargs):
        blobs = self._detector.detect(person_height, **detect_kwargs)
        if blobs and (self._offset_x or self._offset_y):
            for blob in blobs:
                blob.bbox[0] += self._offset_x
                blob.bbox[1] += self._offset_y
                blob.centroid[0] += self._offset_x
                blob.centroid[1] += self._offset_y
        return blobs

    def extract_local_motion_blob(self, x: float, y: float, w: float, h: float,
                                  target_centroid=None, **kwargs):
        target = None
        if target_centroid is not None:
            target = np.array([
                target_centroid[0] - self._offset_x,
                target_centroid[1] - self._offset_y,
            ], dtype=np.float64)
        blob, motion_ratio = self._detector.extract_local_motion_blob(
            x - self._offset_x,
            y - self._offset_y,
            w,
            h,
            target_centroid=target,
            **kwargs,
        )
        if blob is not None and (self._offset_x or self._offset_y):
            blob.bbox[0] += self._offset_x
            blob.bbox[1] += self._offset_y
            blob.centroid[0] += self._offset_x
            blob.centroid[1] += self._offset_y
        return blob, motion_ratio

    def motion_ratio_in_bbox(self, x: float, y: float, w: float, h: float,
                             **kwargs) -> float:
        return self._detector.motion_ratio_in_bbox(
            x - self._offset_x,
            y - self._offset_y,
            w,
            h,
            **kwargs,
        )

    def frame_diff_blob_in_bbox(self, x: float, y: float, w: float, h: float,
                                target_centroid=None, **kwargs):
        target = None
        if target_centroid is not None:
            target = np.array([
                target_centroid[0] - self._offset_x,
                target_centroid[1] - self._offset_y,
            ], dtype=np.float64)
        blob, ratio = self._detector.frame_diff_blob_in_bbox(
            x - self._offset_x,
            y - self._offset_y,
            w,
            h,
            target_centroid=target,
            **kwargs,
        )
        if blob is not None and (self._offset_x or self._offset_y):
            blob.bbox[0] += self._offset_x
            blob.bbox[1] += self._offset_y
            blob.centroid[0] += self._offset_x
            blob.centroid[1] += self._offset_y
        return blob, ratio

    def bridge_diagnostics(self, x: float, y: float, w: float, h: float,
                           **kwargs) -> dict:
        return self._detector.bridge_diagnostics(
            x - self._offset_x,
            y - self._offset_y,
            w,
            h,
            **kwargs,
        )


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
        self.settings = settings
        self.enhancer = enhancer or ImageEnhancer()
        self.tracker = tracker or DancerTracker()
        self.osc = osc_sender
        self._timing: Dict[str, float] = {}
        self._extract_transfer_timing: Dict[str, float] = {}
        
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
        self._exclusion = ExclusionMaskBuilder()  # auto exclusion mask (P1.4)

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

    @staticmethod
    def _is_cuda_kernel_compat_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return (
            "no kernel image is available for execution on the device" in msg
            or "cuda error: no kernel image" in msg
        )

    def _disable_gpu_path_and_fallback(self, reason: str):
        if self._gpu_fallback_reason is not None:
            return

        self._gpu_fallback_reason = reason
        self._gpu_path_active = False
        self._gpu_pipeline = None
        self.settings.use_gpu_path = False
        self.settings.use_fp16 = False

        try:
            if hasattr(self.model, "to"):
                self.model.to("cpu")
        except Exception as e:
            print(f"[Pipeline] Warning: could not move model to CPU: {e}")

        print("[Pipeline] GPU path disabled due to CUDA runtime incompatibility; falling back to CPU processing.")
        print(f"[Pipeline] Reason: {reason}")

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
        if self._gpu_path_active and self._gpu_pipeline is not None:
            try:
                return self._process_gpu(frame, need_preview, frame_number=frame_number)
            except RuntimeError as e:
                if self._is_cuda_kernel_compat_error(e):
                    self._disable_gpu_path_and_fallback(str(e))
                    return self._process_cpu(frame, frame_number=frame_number)
                raise
        return self._process_cpu(frame, frame_number=frame_number)
    
    def _process_gpu(self, frame: np.ndarray, need_preview: bool = True, frame_number: int | None = None) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """GPU pipeline: zero-copy enhancement + YOLO."""
        frame_start = time.time()
        original_h, original_w = frame.shape[:2]
        timing: Dict[str, float] = {}
        
        # Sync GPU pipeline settings
        self._sync_gpu_settings()
        
        # 1. GPU Pipeline: Upload + Enhance + YOLO prep
        yolo_tensor, preview_frame, gpu_timing = self._gpu_pipeline.process(frame, preview_enabled=need_preview)
        
        # Merge GPU timing
        timing.update(gpu_timing)
        timing["path_enhance"] = "gpu"
        
        # 2-6. YOLO → Track → OSC
        scaled_tracks = self._run_yolo_and_track(
            yolo_tensor, gpu_timing, timing, original_w, original_h,
            frame_number=frame_number, raw_frame=frame)
        
        latency_ms = (time.time() - frame_start) * 1000
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

        try:
            frame_start = time.time()
            _, _, original_h, original_w = gpu_tensor.shape
            timing: Dict[str, float] = {}

            # Sync GPU pipeline settings
            self._sync_gpu_settings()

            # 1. GPU Pipeline: process_gpu_tensor (skip upload, already on GPU)
            yolo_tensor, preview_frame, gpu_timing = self._gpu_pipeline.process_gpu_tensor(
                gpu_tensor, preview_enabled=need_preview
            )

            # Merge GPU timing
            timing.update(gpu_timing)
            timing["path_enhance"] = "gpu-direct"

            # 2-6. YOLO → Track → OSC
            scaled_tracks = self._run_yolo_and_track(yolo_tensor, gpu_timing, timing, original_w, original_h, frame_number=frame_number, raw_frame=raw_frame)

            latency_ms = (time.time() - frame_start) * 1000
            timing["total"] = latency_ms
            self._timing = timing

            return scaled_tracks, preview_frame, timing, latency_ms
        except RuntimeError as e:
            if not self._is_cuda_kernel_compat_error(e):
                raise

            self._disable_gpu_path_and_fallback(str(e))

            cpu_rgb = gpu_tensor.squeeze(0).detach().clamp(0, 1).permute(1, 2, 0).cpu().numpy()
            cpu_bgr = cv2.cvtColor((cpu_rgb * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
            return self._process_cpu(cpu_bgr, frame_number=frame_number)

    def _run_yolo_and_track(
        self,
        yolo_tensor: 'torch.Tensor',
        gpu_timing: Dict[str, float],
        timing: Dict[str, float],
        original_w: int,
        original_h: int,
        frame_number: int | None = None,
        raw_frame: np.ndarray | None = None,
    ) -> List[ScaledTrack]:
        """Shared YOLO inference → extract → track → unscale → OSC pipeline.

        Mutates *timing* in place and returns the final scaled tracks.
        MOG2 feed runs in a background thread overlapping YOLO (CPU ∥ GPU).
        """
        roi = gpu_timing.get('roi', {})
        roi_x = int(roi.get('x', 0))
        roi_y = int(roi.get('y', 0))

        # Start MOG2 feed in background thread — runs on CPU while YOLO uses GPU
        mog2_thread = None
        if (self.bridge_motion_detector is not None
                or self.crossval_motion_detector is not None) and raw_frame is not None:
            t_mog_start = time.time()
            motion_frame = raw_frame
            if roi.get('enabled'):
                roi_w = int(roi.get('w', 0))
                roi_h = int(roi.get('h', 0))
                motion_frame = raw_frame[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
            gray_for_motion = cv2.cvtColor(motion_frame, cv2.COLOR_BGR2GRAY)
            timing["mog2_cvt"] = (time.time() - t_mog_start) * 1000
            mog2_thread = threading.Thread(
                target=self._feed_motion_detectors, args=(gray_for_motion,),
                daemon=True)
            mog2_thread.start()

        # YOLO inference (GPU) — runs in parallel with MOG2 feed (CPU)
        t0 = time.time()
        results = self.model(
            yolo_tensor,
            imgsz=self.settings.imgsz,
            conf=self.settings.confidence,
            iou=YOLO_IOU_THRESHOLD,
            half=self.settings.use_fp16,
            verbose=False,
        )
        timing["yolo"] = (time.time() - t0) * 1000
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
        t0 = time.time()
        detections = self._extract_detections(results)
        detections = self._filter_duplicate_detections(detections, effective_person_height=scaled_person_height)
        timing["extract"] = (time.time() - t0) * 1000
        timing.update(self._extract_transfer_timing)

        # Join MOG2 thread before tracker needs blobs
        if mog2_thread is not None:
            mog2_thread.join()
            timing["mog2_feed"] = (time.time() - t_mog_start) * 1000 - timing.get("mog2_cvt", 0)

        # Cross-validate YOLO detections against MOG2 motion mask
        n_before_xval = len(detections)
        detections, crossval_stats = self._crossval_motion_filter(
            detections,
            self.crossval_motion_detector,
            scaled_person_height,
            scale=lb_scale,
            letterbox_pad_x=pad_x,
            letterbox_pad_y=pad_y,
            roi_x=roi_x,
            roi_y=roi_y,
            roi_local_after_unscale=True,
        )
        timing.update(crossval_stats)
        timing["crossval_rejected"] = n_before_xval - len(detections)

        # Auto exclusion mask (P1.4): collect during calibration, else reject
        # detections that land in known scenery / ghost cells.
        detections = self._exclusion_step(
            detections, self.crossval_motion_detector,
            (lb_scale, pad_x, pad_y, roi_x, roi_y, True),
            scaled_person_height, timing)

        # In MOTION_FIRST mode, eagerly detect blobs for synthetic detections
        eager_blobs = None
        if self._tracking_mode == TrackingMode.MOTION_FIRST and self.bridge_motion_detector is not None:
            # detect() operates in original-frame coords (not letterboxed),
            # so pass the un-scaled person_height.
            eager_blobs = self.bridge_motion_detector.detect(
                self.settings.person_height_px,
                aspect_range=MOTION_FIRST_ASPECT_RANGE,
                include_shadows=MOTION_FIRST_INCLUDE_SHADOWS,
            )

        # Tracking
        t0 = time.time()
        # Set content-area bounds for edge-exit detection.
        # In the GPU path, tracker coords are in letterboxed imgsz-space;
        # the actual content sits between pad_x and imgsz - pad_x.
        self.tracker.set_frame_dimensions(self.settings.imgsz, pad_x=pad_x)
        self.tracker.set_person_height(scaled_person_height)

        if self.bridge_motion_detector is not None and raw_frame is not None:
            # Wrap detector to scale blob coords to letterboxed YOLO space
            self._motion_lb_scale = lb_scale
            self._motion_pad_x = pad_x
            self._motion_pad_y = pad_y

        # Scale eager blobs to letterboxed space if needed
        lb_motion = self._get_letterbox_motion_detector()
        if eager_blobs is not None and lb_motion is not None:
            # Blobs are in original coords; apply letterbox transform
            for blob in eager_blobs:
                blob.bbox[0] = blob.bbox[0] * lb_scale + pad_x
                blob.bbox[1] = blob.bbox[1] * lb_scale + pad_y
                blob.bbox[2] *= lb_scale
                blob.bbox[3] *= lb_scale
                blob.centroid[0] = blob.centroid[0] * lb_scale + pad_x
                blob.centroid[1] = blob.centroid[1] * lb_scale + pad_y

        t_trk = time.time()
        tracked = self.tracker.update(
            detections, frame_number=frame_number,
            motion_detector=lb_motion,
            motion_blobs=eager_blobs)
        timing["tracker_update"] = (time.time() - t_trk) * 1000
        timing["track"] = (time.time() - t0) * 1000
        timing["path_track"] = "cpu"

        # Update re-acquisition counter for crossval death-spiral prevention
        if tracked:
            self._crossval_no_track_frames = 0
        else:
            self._crossval_no_track_frames += 1
        timing["crossval_no_track_frames"] = self._crossval_no_track_frames

        # Unscale from letterboxed YOLO space to original camera space
        scaled_tracks = [
            self._unscale_letterbox(track, lb_scale, pad_x, pad_y, roi_x=roi_x, roi_y=roi_y)
            for track in tracked
        ]

        # OSC output
        if self.osc and self.settings.osc_enabled:
            self.osc.send_frame(scaled_tracks, original_w, original_h)

        return scaled_tracks

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

    def _offset_detections(
        self,
        detections: List[Tuple[np.ndarray, np.ndarray, np.ndarray]],
        offset_x: int,
        offset_y: int,
    ) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        """Offset detections from ROI-local coordinates back into full-frame space."""
        if not detections or (offset_x == 0 and offset_y == 0):
            return detections

        offset_xy = np.array([offset_x, offset_y])
        offset_bbox = np.array([offset_x, offset_y, 0.0, 0.0])
        shifted = []
        for keypoints, confidence, bbox in detections:
            shifted.append((keypoints + offset_xy, confidence, bbox + offset_bbox))
        return shifted
    
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
    
    def _process_cpu(self, frame: np.ndarray, frame_number: int | None = None) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """CPU pipeline: traditional enhancement + YOLO."""
        frame_start = time.time()
        original_h, original_w = frame.shape[:2]
        timing: Dict[str, float] = {}

        # 0. Background subtraction (before enhancement)
        if self.settings.bg_subtract_enabled and self.bg_subtractor.has_reference:
            t0 = time.time()
            frame = self.bg_subtractor.apply_cpu(frame, self.settings.bg_subtract_sensitivity)
            timing["bg_subtract"] = (time.time() - t0) * 1000
            timing["bg_fg_ratio"] = self.bg_subtractor.foreground_ratio
            timing["bg_mismatched"] = self.bg_subtractor.is_mismatched
        else:
            timing["bg_subtract"] = 0.0

        roi = self._resolve_roi(original_w, original_h)
        if roi is not None:
            roi_x, roi_y, roi_x2, roi_y2 = roi
            frame = frame[roi_y:roi_y2, roi_x:roi_x2]
            timing["roi"] = {
                "enabled": True,
                "x": roi_x,
                "y": roi_y,
                "w": roi_x2 - roi_x,
                "h": roi_y2 - roi_y,
            }
        else:
            roi_x = 0
            roi_y = 0
            timing["roi"] = {
                "enabled": False,
                "x": 0,
                "y": 0,
                "w": original_w,
                "h": original_h,
            }

        # 1. Enhancement
        t0 = time.time()
        
        brightness = 0.0
        should_enhance = self.settings.enhance_enabled
        if should_enhance and not self.settings.enhance_lite and not self.settings.enhance_force:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            brightness = float(np.mean(gray))
            if brightness >= self.settings.brightness_threshold:
                should_enhance = False
        
        if should_enhance:
            if self.settings.enhance_lite:
                enhanced = self.enhancer.enhance_simple(frame)
            else:
                enhanced, _ = self.enhancer.enhance(frame)
            enhance_on_gpu = getattr(self.enhancer, 'last_used_gpu', False)
        else:
            enhanced = frame
            enhance_on_gpu = False
            
        timing["enhance"] = (time.time() - t0) * 1000
        timing["path_enhance"] = "gpu" if enhance_on_gpu else "cpu"
        timing["brightness"] = brightness

        # Start MOG2 feed in background thread — runs on CPU while YOLO uses GPU
        mog2_thread = None
        t_mog_start = None
        if self.bridge_motion_detector is not None or self.crossval_motion_detector is not None:
            t_mog_start = time.time()
            gray_for_motion = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            timing["mog2_cvt"] = (time.time() - t_mog_start) * 1000
            mog2_thread = threading.Thread(
                target=self._feed_motion_detectors, args=(gray_for_motion,),
                daemon=True)
            mog2_thread.start()

        # 2. YOLO inference (GPU) — runs in parallel with MOG2 feed (CPU)
        t0 = time.time()
        results = self.model(
            enhanced,
            imgsz=self.settings.imgsz,
            conf=self.settings.confidence,
            iou=YOLO_IOU_THRESHOLD,
            half=self.settings.use_fp16,
            verbose=False,
        )
        timing["yolo"] = (time.time() - t0) * 1000
        timing["path_yolo"] = "gpu"

        # 3. Extract detections
        t0 = time.time()
        detections = self._extract_detections(results)
        detections = self._filter_duplicate_detections(detections)
        detections = self._offset_detections(detections, roi_x, roi_y)
        timing["extract"] = (time.time() - t0) * 1000
        timing.update(self._extract_transfer_timing)

        # Join MOG2 thread before tracker needs blobs
        if mog2_thread is not None:
            mog2_thread.join()
            timing["mog2_feed"] = (time.time() - t_mog_start) * 1000 - timing.get("mog2_cvt", 0)

        # Cross-validate YOLO detections against MOG2 motion mask
        n_before_xval = len(detections)
        detections, crossval_stats = self._crossval_motion_filter(
            detections,
            self.crossval_motion_detector,
            self.settings.person_height_px,
            roi_x=roi_x,
            roi_y=roi_y,
        )
        timing.update(crossval_stats)
        timing["crossval_rejected"] = n_before_xval - len(detections)

        # Auto exclusion mask (P1.4): CPU path — original-space coords (scale 1,
        # no letterbox pad), ROI subtracted (roi_local=False).
        detections = self._exclusion_step(
            detections, self.crossval_motion_detector,
            (1.0, 0.0, 0.0, roi_x, roi_y, False),
            self.settings.person_height_px, timing)

        # In MOTION_FIRST mode, eagerly detect blobs for synthetic detections
        eager_blobs = None
        if self._tracking_mode == TrackingMode.MOTION_FIRST and self.bridge_motion_detector is not None:
            eager_blobs = self.bridge_motion_detector.detect(
                self.settings.person_height_px,
                aspect_range=MOTION_FIRST_ASPECT_RANGE,
                include_shadows=MOTION_FIRST_INCLUDE_SHADOWS,
            )
            # Apply ROI offset to blobs
            if eager_blobs and (roi_x or roi_y):
                for blob in eager_blobs:
                    blob.bbox[0] += roi_x
                    blob.bbox[1] += roi_y
                    blob.centroid[0] += roi_x
                    blob.centroid[1] += roi_y

        # 4. Tracking
        t0 = time.time()
        # CPU path: YOLO outputs in original frame coords — no scaling needed
        self.tracker.set_frame_dimensions(original_w)
        self.tracker.set_person_height(self.settings.person_height_px)
        motion_detector = self.bridge_motion_detector
        if motion_detector is not None and (roi_x or roi_y):
            motion_detector = _OffsetMotionProxy(motion_detector, roi_x, roi_y)

        t_trk = time.time()
        tracked = self.tracker.update(
            detections, frame_number=frame_number,
            motion_detector=motion_detector,
            motion_blobs=eager_blobs)
        timing["tracker_update"] = (time.time() - t_trk) * 1000
        timing["track"] = (time.time() - t0) * 1000
        timing["path_track"] = "cpu"
        timing["original_w"] = original_w
        timing["original_h"] = original_h

        # Update re-acquisition counter for crossval death-spiral prevention
        if tracked:
            self._crossval_no_track_frames = 0
        else:
            self._crossval_no_track_frames += 1
        timing["crossval_no_track_frames"] = self._crossval_no_track_frames

        # 5. Convert tracks to ScaledTrack (identity scale)
        scaled_tracks = [
            ScaledTrack(
                track_id=t.track_id,
                keypoints=t.keypoints.copy(),
                confidence=t.confidence.copy(),
                bbox=t.bbox.copy(),
                history=[pt.copy() for pt in t.history],
                velocity=t.get_velocity().copy(),
                smoothed_centroid=t.get_smoothed_centroid().copy(),
                is_bridged=getattr(t, 'is_bridged', False),
            ) for t in tracked
        ]

        # 6. OSC output
        if self.osc and self.settings.osc_enabled:
            self.osc.send_frame(scaled_tracks, original_w, original_h)

        latency_ms = (time.time() - frame_start) * 1000
        timing["total"] = latency_ms
        self._timing = timing
        return scaled_tracks, enhanced, timing, latency_ms

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

    def _feed_motion_detectors(self, gray: np.ndarray) -> None:
        """Feed the single motion model from one grayscale frame."""
        if self.motion_model is None:
            return
        # Apply the same CLAHE + gamma used for YOLO / preview so the motion
        # model sees the contrast-enhanced image.
        # NOTE: Bug #1 (per-frame adaptive CLAHE before MOG2) is fixed in
        # Stage 3, where it is coupled with the varThreshold retune + frame-diff
        # crossval.  Keeping the historical enhanced-gray feed here lets Stage 2
        # isolate a single behavior change (one slow MOG2 for crossval).
        gray = self._enhance_gray_for_motion(gray)
        # Handle on the exact signal MOG2 consumes (Go-Live noise calibration).
        self._last_motion_gray = gray
        self.motion_model.feed(gray)

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
        ox = (cx - pad_x) / scale if scale != 1.0 else cx
        oy = (cy - pad_y) / scale if scale != 1.0 else cy
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

    def set_exclusion(self, grid, cells) -> None:
        self._exclusion.set_cells(grid, cells)

    def get_exclusion(self) -> tuple:
        return self._exclusion.get_cells()

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
        
        # Bbox: (x, y, w, h) -> x,y subtract pad and scale; w,h just scale
        bbox = track.bbox.copy()
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
        
        return ScaledTrack(
            track_id=track.track_id,
            keypoints=keypoints,
            confidence=track.confidence.copy(),
            bbox=bbox,
            history=history,
            velocity=velocity,
            smoothed_centroid=sm_centroid,
            is_bridged=getattr(track, 'is_bridged', False),
        )

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------
    def _extract_detections(self, results) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        detections = []
        kpts_cpu_ms = 0.0
        boxes_cpu_ms = 0.0
        for result in results:
            if result.keypoints is None or len(result.keypoints) == 0:
                continue
            t0 = time.perf_counter()
            keypoints_data = result.keypoints.data.cpu().numpy()
            kpts_cpu_ms += (time.perf_counter() - t0) * 1000.0

            if result.boxes is not None:
                t0 = time.perf_counter()
                boxes = result.boxes.xyxy.cpu().numpy()
                boxes_cpu_ms += (time.perf_counter() - t0) * 1000.0
            else:
                boxes = None

            for i, kpts in enumerate(keypoints_data):
                keypoints = kpts[:, :2]
                confidence = kpts[:, 2]
                if boxes is not None and i < len(boxes):
                    x1, y1, x2, y2 = boxes[i]
                    bbox = (x1, y1, x2 - x1, y2 - y1)
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
        """Reject YOLO detections that do not show enough recent motion.

        Decision tree (first match wins):
          1. BYPASS    — detection overlaps a recently-matched track
          2. MOTION    — MOG2 foreground ratio exceeds threshold
          3. HYSTERESIS — smoothed EMA score above sticky threshold
          4. CONFIDENT — strong skeleton (≥8 kpts, ≥0.45 conf)
          5. REACQUIRE — no tracks for N frames, decent skeleton
          6. REJECT

        Returns:
            Tuple of (kept_detections, stats_dict)
        """
        stats = {
            "crossval_kept_motion": 0,
            "crossval_kept_hysteresis": 0,
            "crossval_kept_bypass": 0,
            "crossval_kept_confident": 0,
            "crossval_kept_reacquire": 0,
            "crossval_rejected_low_motion": 0,
            "crossval_rejected_weak_skeleton": 0,
            "crossval_rejected_warmup_quality": 0,
            "crossval_skip_disabled": 0,
            "crossval_skip_no_mask": 0,
            "crossval_skip_warmup": 0,
        }
        if not MOTION_CROSSVAL_ENABLED:
            stats["crossval_skip_disabled"] = 1
            return detections, stats
        if motion_det is None or not motion_det.has_mask:
            stats["crossval_skip_no_mask"] = 1
            return detections, stats

        # During MOG2 warmup, cross-validation is off but apply skeleton
        # quality filter to prevent ghost floods at low YOLO confidence.
        if motion_det.frame_count < MOTION_CROSSVAL_WARMUP_FRAMES:
            stats["crossval_skip_warmup"] = 1
            if not detections:
                return detections, stats
            kept_warmup = []
            for kpts, conf, bbox in detections:
                visible = conf > KEYPOINT_CONFIDENCE
                n_valid = int(np.sum(visible))
                mean_conf = (float(np.mean(conf[visible]))
                             if n_valid > 0 else 0.0)
                if (n_valid >= MOTION_CROSSVAL_WARMUP_MIN_KPTS
                        and mean_conf >= MOTION_CROSSVAL_WARMUP_MIN_CONF):
                    kept_warmup.append((kpts, conf, bbox))
                else:
                    stats["crossval_rejected_warmup_quality"] += 1
            return kept_warmup, stats
        if not detections:
            self._crossval_motion_memory = {}
            return detections, stats

        bypass_candidates = []
        bypass_gate = person_height * 0.6
        current_frame = getattr(self.tracker, 'frame_count', 0)
        if MOTION_CROSSVAL_EXISTING_TRACK_BYPASS:
            for track in self.tracker.tracks:
                if track.time_since_update > MOTION_CROSSVAL_BYPASS_MAX_AGE:
                    continue
                if getattr(track, '_warmup_score', 0.0) < MOTION_CROSSVAL_BYPASS_MIN_WARMUP:
                    continue
                # Only bypass if track centroid is near a recently motion-
                # confirmed cell.  Wall-painting ghosts sit at static
                # positions that never produce real MOG2 motion.
                track_cell = self._crossval_cell_key(track.bbox, person_height)
                motion_age = current_frame - self._crossval_motion_cells.get(track_cell, -9999)
                if motion_age > MOTION_CROSSVAL_BYPASS_MAX_AGE * 3:
                    # Also check adjacent cells (dancer may drift slightly)
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

        # Re-acquisition mode: if we have had zero confirmed tracks for too
        # long, let strong skeletons through to break the death spiral.
        reacquire_mode = (
            self._crossval_no_track_frames >= MOTION_CROSSVAL_REACQUIRE_FRAMES
        )

        kept = []
        next_memory: Dict[tuple[int, int], float] = {}
        sticky_threshold = MOTION_CROSSVAL_MIN_FG_RATIO * MOTION_CROSSVAL_STICKY_RATIO
        is_lowlight = motion_det.last_brightness < MOTION_LOWLIGHT_LUMA_THRESHOLD

        for kpts, conf, bbox in detections:
            cell = self._crossval_cell_key(bbox, person_height)

            # Convert tracker-space bbox -> original-space -> ROI-local mask space
            ox = (bbox[0] - letterbox_pad_x) / scale if scale != 1.0 else bbox[0]
            oy = (bbox[1] - letterbox_pad_y) / scale if scale != 1.0 else bbox[1]
            ow = bbox[2] / scale if scale != 1.0 else bbox[2]
            oh = bbox[3] / scale if scale != 1.0 else bbox[3]
            if roi_local_after_unscale:
                mask_x = ox
                mask_y = oy
            else:
                mask_x = ox - roi_x
                mask_y = oy - roi_y

            ratio = motion_det.motion_ratio_in_bbox(
                mask_x, mask_y, ow, oh,
                core_scale=MOTION_CROSSVAL_CORE_SCALE)
            prev_score = self._crossval_motion_memory.get(cell, 0.0)
            smoothed = (MOTION_CROSSVAL_EMA_ALPHA * ratio
                        + (1.0 - MOTION_CROSSVAL_EMA_ALPHA) * prev_score)
            next_memory[cell] = smoothed

            visible = conf > KEYPOINT_CONFIDENCE
            n_valid = int(np.sum(visible))
            mean_conf = (float(np.mean(conf[visible]))
                         if n_valid > 0 else 0.0)
            motion_threshold = MOTION_CROSSVAL_MIN_FG_RATIO
            if is_lowlight:
                motion_threshold *= MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT

            # ── Step 1: BYPASS — near an existing tracked dancer ────────
            # First priority: if a detection overlaps a recently-matched
            # track, keep it unconditionally.  This makes tracked dancers
            # hard to lose during brief low-motion moments.
            det_centroid = np.array([
                bbox[0] + bbox[2] * 0.5,
                bbox[1] + bbox[3] * 0.5,
            ])
            bypassed = False
            for track in bypass_candidates:
                track_centroid = track.get_centroid()
                if float(np.linalg.norm(det_centroid - track_centroid)) > bypass_gate:
                    if self._bbox_iou_xywh(bbox, track.bbox) <= 0.1:
                        continue
                kept.append((kpts, conf, bbox))
                stats["crossval_kept_bypass"] += 1
                bypassed = True
                break
            if bypassed:
                continue

            # ── Step 2: Weak skeleton early rejection (low light) ───────
            # In low light, ghost detections with few keypoints and poor
            # confidence are rejected before they can benefit from motion
            # noise or hysteresis.
            is_weak_skeleton = (
                is_lowlight
                and n_valid < MOTION_CROSSVAL_LOWLIGHT_MIN_VALID_KPTS
                and mean_conf < MOTION_CROSSVAL_LOWLIGHT_MIN_MEAN_CONF
            )
            if is_weak_skeleton and ratio < motion_threshold:
                stats["crossval_rejected_weak_skeleton"] += 1
                continue

            # ── Step 3: MOTION — MOG2 confirms real movement ───────────
            if ratio >= motion_threshold:
                # Even with motion, reject truly garbage skeletons in low
                # light that have almost no visible keypoints.
                if is_lowlight and n_valid <= 2:
                    stats["crossval_rejected_weak_skeleton"] += 1
                    continue
                kept.append((kpts, conf, bbox))
                stats["crossval_kept_motion"] += 1
                # Mark this cell as motion-confirmed for bypass eligibility
                self._crossval_motion_cells[cell] = current_frame
                continue

            # ── Step 4: HYSTERESIS — temporal persistence ──────────────
            if smoothed >= sticky_threshold * (MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT if is_lowlight else 1.0):
                kept.append((kpts, conf, bbox))
                stats["crossval_kept_hysteresis"] += 1
                self._crossval_motion_cells[cell] = current_frame
                continue

            # ── Step 5: CONFIDENT — strong skeleton, no motion needed ──
            # A very confident detection (many keypoints at high conf) is
            # accepted without motion proof.  This lets clearly-visible
            # stationary dancers through while still blocking ghosts.
            if (n_valid >= MOTION_CROSSVAL_CONFIDENT_MIN_KPTS
                    and mean_conf >= MOTION_CROSSVAL_CONFIDENT_MIN_CONF):
                kept.append((kpts, conf, bbox))
                stats["crossval_kept_confident"] += 1
                continue

            # ── Step 6: REACQUIRE — death-spiral escape ────────────────
            if (reacquire_mode
                    and n_valid >= MOTION_CROSSVAL_REACQUIRE_MIN_KPTS
                    and mean_conf >= MOTION_CROSSVAL_REACQUIRE_MIN_CONF):
                kept.append((kpts, conf, bbox))
                stats["crossval_kept_reacquire"] += 1
                continue

            # ── Step 7: REJECT ─────────────────────────────────────────
            stats["crossval_rejected_low_motion"] += 1

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