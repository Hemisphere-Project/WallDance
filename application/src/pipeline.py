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
)
from background import BackgroundSubtractor
from enhancer import ImageEnhancer, TORCH_CUDA_AVAILABLE
from motion_detector import MotionDetector
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
    person_height_min_ratio: float = PERSON_HEIGHT_MIN_RATIO
    person_height_max_ratio: float = PERSON_HEIGHT_MAX_RATIO
    brightness_threshold: int = 60  # Auto-bypass threshold (0-255)
    denoise_strength: float = 0.0   # Temporal denoising (0.0-1.0)
    greyscale: bool = False         # Convert to greyscale (mono camera simulation)
    osc_enabled: bool = True
    use_gpu_path: bool = USE_GPU_PATH  # Enable GPU frame buffer
    bg_subtract_enabled: bool = BG_SUBTRACT_ENABLED  # Static BG subtraction
    bg_subtract_sensitivity: int = BG_SUBTRACT_SENSITIVITY  # Threshold 0-255


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

    def detect(self, person_height: int):
        blobs = self._detector.detect(person_height)
        if blobs and (self._lb_scale != 1.0 or self._pad_x or self._pad_y):
            for blob in blobs:
                blob.bbox[0] = blob.bbox[0] * self._lb_scale + self._pad_x
                blob.bbox[1] = blob.bbox[1] * self._lb_scale + self._pad_y
                blob.bbox[2] *= self._lb_scale
                blob.bbox[3] *= self._lb_scale
                blob.centroid[0] = blob.centroid[0] * self._lb_scale + self._pad_x
                blob.centroid[1] = blob.centroid[1] * self._lb_scale + self._pad_y
        return blobs


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

        # Motion bridge detector (Phase 3)
        self.motion_detector = MotionDetector() if MOTION_BRIDGE_ENABLED else None
        self._motion_lb_scale = 1.0
        self._motion_pad_x = 0
        self._motion_pad_y = 0
        
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
    
    def process_gpu_direct(self, gpu_tensor: 'torch.Tensor', need_preview: bool = True, frame_number: int | None = None) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """Process a pre-uploaded GPU tensor (optimized path for IDS camera).
        
        This bypasses the CPU→GPU upload step for lowest latency when the
        camera provides frames directly as GPU tensors via read_gpu().
        
        Args:
            gpu_tensor: GPU tensor (1, 3, H, W) float32 [0,1] RGB format
            need_preview: Whether to generate preview output
            frame_number: Display frame number for tracker logging (overlay match)
            
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
            scaled_tracks = self._run_yolo_and_track(yolo_tensor, gpu_timing, timing, original_w, original_h, frame_number=frame_number)

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
        # Start MOG2 feed in background thread — runs on CPU while YOLO uses GPU
        mog2_thread = None
        if self.motion_detector is not None and raw_frame is not None:
            t_mog_start = time.time()
            gray_for_motion = cv2.cvtColor(raw_frame, cv2.COLOR_BGR2GRAY)
            timing["mog2_cvt"] = (time.time() - t_mog_start) * 1000
            mog2_thread = threading.Thread(
                target=self.motion_detector.feed, args=(gray_for_motion,),
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

        # Extract detections
        t0 = time.time()
        detections = self._extract_detections(results)
        detections = self._filter_duplicate_detections(detections)
        timing["extract"] = (time.time() - t0) * 1000
        timing.update(self._extract_transfer_timing)

        # Join MOG2 thread before tracker needs blobs
        if mog2_thread is not None:
            mog2_thread.join()
            timing["mog2_feed"] = (time.time() - t_mog_start) * 1000 - timing.get("mog2_cvt", 0)

        # Tracking
        t0 = time.time()
        # Set content-area bounds for edge-exit detection.
        # In the GPU path, tracker coords are in letterboxed imgsz-space;
        # the actual content sits between pad_x and imgsz - pad_x.
        letterbox = gpu_timing.get('letterbox', {})
        lb_scale = letterbox.get('scale', 1.0)
        pad_x = letterbox.get('pad_x', 0)
        pad_y = letterbox.get('pad_y', 0)
        self.tracker.set_frame_dimensions(self.settings.imgsz, pad_x=pad_x)

        if self.motion_detector is not None and raw_frame is not None:
            # Wrap detector to scale blob coords to letterboxed YOLO space
            self._motion_lb_scale = lb_scale
            self._motion_pad_x = pad_x
            self._motion_pad_y = pad_y

        t_trk = time.time()
        tracked = self.tracker.update(
            detections, frame_number=frame_number,
            motion_detector=self._get_letterbox_motion_detector())
        timing["tracker_update"] = (time.time() - t_trk) * 1000
        timing["track"] = (time.time() - t0) * 1000
        timing["path_track"] = "cpu"

        # Unscale from letterboxed YOLO space to original camera space
        scaled_tracks = [self._unscale_letterbox(track, lb_scale, pad_x, pad_y) for track in tracked]

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
    
    def set_preview_size(self, width: int, height: int):
        """Set preview dimensions for GPU pipeline."""
        if self._gpu_pipeline is not None:
            self._gpu_pipeline.settings.preview_width = width
            self._gpu_pipeline.settings.preview_height = height
    
    def set_preview_fps_cap(self, fps_cap: Optional[float]):
        """Set preview FPS cap for GPU pipeline rate limiting."""
        if self._gpu_pipeline is not None:
            self._gpu_pipeline.settings.preview_fps_cap = fps_cap
            self._gpu_pipeline.update_settings(self._gpu_pipeline.settings)
    
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
        if self.motion_detector is not None:
            t_mog_start = time.time()
            gray_for_motion = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            timing["mog2_cvt"] = (time.time() - t_mog_start) * 1000
            mog2_thread = threading.Thread(
                target=self.motion_detector.feed, args=(gray_for_motion,),
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
        timing["extract"] = (time.time() - t0) * 1000
        timing.update(self._extract_transfer_timing)

        # Join MOG2 thread before tracker needs blobs
        if mog2_thread is not None:
            mog2_thread.join()
            timing["mog2_feed"] = (time.time() - t_mog_start) * 1000 - timing.get("mog2_cvt", 0)

        # 4. Tracking
        t0 = time.time()
        # CPU path: YOLO outputs in original frame coords
        self.tracker.set_frame_dimensions(original_w)

        t_trk = time.time()
        tracked = self.tracker.update(
            detections, frame_number=frame_number,
            motion_detector=self.motion_detector)
        timing["tracker_update"] = (time.time() - t_trk) * 1000
        timing["track"] = (time.time() - t0) * 1000
        timing["path_track"] = "cpu"

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

    def _get_letterbox_motion_detector(self):
        """Return a proxy that scales blob coords to letterboxed space, or None."""
        if self.motion_detector is None:
            return None
        return _LetterboxMotionProxy(
            self.motion_detector,
            self._motion_lb_scale,
            self._motion_pad_x,
            self._motion_pad_y,
        )

    def _unscale_letterbox(self, track: DancerTrack, lb_scale: float, pad_x: int, pad_y: int) -> ScaledTrack:
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
        inv_scale = 1.0 / lb_scale if lb_scale > 0 else 1.0
        
        # Keypoints: (x, y) -> subtract pad -> divide by scale
        keypoints = (track.keypoints - pad_xy) * inv_scale
        
        # Bbox: (x, y, w, h) -> x,y subtract pad and scale; w,h just scale
        bbox = track.bbox.copy()
        bbox[0] = (bbox[0] - pad_x) * inv_scale  # x
        bbox[1] = (bbox[1] - pad_y) * inv_scale  # y
        bbox[2] = bbox[2] * inv_scale  # w
        bbox[3] = bbox[3] * inv_scale  # h
        
        # History and velocity (guard against overflow from inf/NaN in tracker)
        history = [(pt - pad_xy) * inv_scale for pt in track.history]
        velocity = track.get_velocity() * inv_scale
        if not np.all(np.isfinite(velocity)):
            velocity = np.zeros(2, dtype=np.float64)

        # Smoothed centroid (same unscale transform)
        sm_centroid = (track.get_smoothed_centroid() - pad_xy) * inv_scale
        
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

    def _filter_duplicate_detections(self, detections):
        if len(detections) <= 1:
            return detections

        # Conservative thresholds — require strong evidence before merging.
        # Centroid AND keypoint must BOTH be close (except for very-high IoU).
        centroid_dist_thresh = self.settings.person_height_px * 0.3
        keypoint_dist_thresh = self.settings.person_height_px * 0.1
        min_height = self.settings.person_height_px * self.settings.person_height_min_ratio
        max_height = self.settings.person_height_px * self.settings.person_height_max_ratio

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
                if dist < self.settings.person_height_px * 1.5 and dist < best_dist:
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
        shadow_radius = self.settings.person_height_px * SHADOW_PROXIMITY_RATIO
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