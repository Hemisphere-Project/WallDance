"""
Frame processing pipeline for WallDance.
Handles enhancement, YOLO inference, duplicate filtering, tracking, and OSC output.
Supports full GPU pipeline for zero-copy processing (see gpu_pipeline.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from ultralytics import YOLO

from config import (
    BRIGHTNESS_THRESHOLD,
    KEYPOINT_CONFIDENCE,
    PERSON_HEIGHT_MAX_RATIO,
    PERSON_HEIGHT_MIN_RATIO,
    TRACKER_DISTANCE_THRESHOLD,
    YOLO_IOU_THRESHOLD,
    USE_GPU_PATH,
)
from enhancer import ImageEnhancer, TORCH_CUDA_AVAILABLE
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
    max_persons: int
    use_fp16: bool
    upscale_factor: float
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


@dataclass
class ScaledTrack:
    track_id: int
    keypoints: np.ndarray
    confidence: np.ndarray
    bbox: np.ndarray
    history: List[np.ndarray]
    velocity: np.ndarray


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
        
        # GPU pipeline (zero-copy path)
        self._gpu_pipeline: Optional[GpuPipeline] = None
        self._gpu_path_active = False
        
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
            self._gpu_path_active = True
            print("[Pipeline] GPU pipeline active - zero-copy enhancement + YOLO (kornia/PyTorch)")
        elif TORCH_CUDA_AVAILABLE:
            print("[Pipeline] CUDA available - YOLO on GPU, Enhancement on CPU")
        else:
            print("[Pipeline] CUDA not available - all processing on CPU")

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

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def process(self, frame: np.ndarray, need_preview: bool = True) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """Run a single frame through the pipeline.
        
        When GPU pipeline is active:
        - Frame is uploaded to GPU once
        - Enhancement runs on GPU (kornia CLAHE + gamma)
        - GPU tensor passed directly to YOLO (zero-copy)
        - Preview downloaded only when needed
        
        Args:
            frame: BGR numpy array from camera
            need_preview: Whether to generate preview output (for rate limiting)
        
        Returns:
            (tracked, enhanced_frame, timing, latency_ms)
        """
        if self._gpu_path_active and self._gpu_pipeline is not None:
            return self._process_gpu(frame, need_preview)
        else:
            return self._process_cpu(frame)
    
    def _process_gpu(self, frame: np.ndarray, need_preview: bool = True) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """GPU pipeline: zero-copy enhancement + YOLO."""
        frame_start = time.time()
        original_h, original_w = frame.shape[:2]
        timing: Dict[str, float] = {}
        
        # Sync GPU pipeline settings
        self._sync_gpu_settings()
        
        # 1. GPU Pipeline: Upload + Enhance + YOLO prep
        # GPU pipeline handles rate limiting internally, returns preview_new flag
        yolo_tensor, preview_frame, gpu_timing = self._gpu_pipeline.process(frame, preview_enabled=need_preview)
        
        # Merge GPU timing
        timing.update(gpu_timing)
        # GPU pipeline is always "gpu" path - enhancement may be active or bypassed
        # Show "gpu" to indicate we're using GPU pipeline (even if enhancement bypassed)
        timing["path_enhance"] = "gpu"
        
        # 2. YOLO inference with GPU tensor (zero-copy!)
        t0 = time.time()
        results = self.model(
            yolo_tensor,  # Pass GPU tensor directly!
            imgsz=self.settings.imgsz,
            conf=self.settings.confidence,
            iou=YOLO_IOU_THRESHOLD,
            max_det=self.settings.max_persons,
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
        
        # 4. Tracking
        t0 = time.time()
        tracked = self.tracker.update(detections)
        timing["track"] = (time.time() - t0) * 1000
        timing["path_track"] = "cpu"
        
        # 5. Unscale detections from letterboxed YOLO space to original camera space
        # Letterbox info tells us: scale factor and padding applied
        letterbox = gpu_timing.get('letterbox', {})
        lb_scale = letterbox.get('scale', 1.0)
        pad_x = letterbox.get('pad_x', 0)
        pad_y = letterbox.get('pad_y', 0)
        scaled_tracks = [self._unscale_letterbox(track, lb_scale, pad_x, pad_y) for track in tracked]
        
        # 6. OSC output
        if self.osc and self.settings.osc_enabled:
            self.osc.send_frame(scaled_tracks, original_w, original_h)
        
        latency_ms = (time.time() - frame_start) * 1000
        timing["total"] = latency_ms
        self._timing = timing
        
        # preview_frame is always generated in GPU path (never None)
        return scaled_tracks, preview_frame, timing, latency_ms
    
    def process_gpu_direct(self, gpu_tensor: 'torch.Tensor', need_preview: bool = True) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """Process a pre-uploaded GPU tensor (optimized path for IDS camera).
        
        This bypasses the CPU→GPU upload step for lowest latency when the
        camera provides frames directly as GPU tensors via read_gpu().
        
        Args:
            gpu_tensor: GPU tensor (1, 3, H, W) float32 [0,1] RGB format
            need_preview: Whether to generate preview output
            
        Returns:
            (tracked, enhanced_frame, timing, latency_ms)
        """
        if not self._gpu_path_active or self._gpu_pipeline is None:
            raise RuntimeError("GPU path not active, cannot use process_gpu_direct")
        
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
        timing["path_enhance"] = "gpu-direct"  # Indicate direct GPU path
        
        # 2. YOLO inference with GPU tensor (zero-copy!)
        t0 = time.time()
        results = self.model(
            yolo_tensor,
            imgsz=self.settings.imgsz,
            conf=self.settings.confidence,
            iou=YOLO_IOU_THRESHOLD,
            max_det=self.settings.max_persons,
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
        
        # 4. Tracking
        t0 = time.time()
        tracked = self.tracker.update(detections)
        timing["track"] = (time.time() - t0) * 1000
        timing["path_track"] = "cpu"
        
        # 5. Unscale detections from letterboxed YOLO space to original camera space
        letterbox = gpu_timing.get('letterbox', {})
        lb_scale = letterbox.get('scale', 1.0)
        pad_x = letterbox.get('pad_x', 0)
        pad_y = letterbox.get('pad_y', 0)
        scaled_tracks = [self._unscale_letterbox(track, lb_scale, pad_x, pad_y) for track in tracked]
        
        # 6. OSC output
        if self.osc and self.settings.osc_enabled:
            self.osc.send_frame(scaled_tracks, original_w, original_h)
        
        latency_ms = (time.time() - frame_start) * 1000
        timing["total"] = latency_ms
        self._timing = timing
        
        return scaled_tracks, preview_frame, timing, latency_ms

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
    
    def _process_cpu(self, frame: np.ndarray) -> Tuple[List[ScaledTrack], np.ndarray, Dict[str, float], float]:
        """CPU pipeline: traditional enhancement + YOLO."""
        frame_start = time.time()
        original_h, original_w = frame.shape[:2]
        timing: Dict[str, float] = {}

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

        # 2. Upscale (optional)
        t0 = time.time()
        if self.settings.upscale_factor != 1.0:
            new_w = int(original_w * self.settings.upscale_factor)
            new_h = int(original_h * self.settings.upscale_factor)
            process_frame = cv2.resize(enhanced, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        else:
            process_frame = enhanced
        timing["upscale"] = (time.time() - t0) * 1000
        timing["path_resize"] = "cpu"

        # 3. YOLO inference
        t0 = time.time()
        results = self.model(
            process_frame,
            imgsz=self.settings.imgsz,
            conf=self.settings.confidence,
            iou=YOLO_IOU_THRESHOLD,
            max_det=self.settings.max_persons,
            half=self.settings.use_fp16,
            verbose=False,
        )
        timing["yolo"] = (time.time() - t0) * 1000
        timing["path_yolo"] = "gpu"

        # 4. Extract detections
        t0 = time.time()
        detections = self._extract_detections(results)
        detections = self._filter_duplicate_detections(detections)
        timing["extract"] = (time.time() - t0) * 1000

        # 5. Tracking
        t0 = time.time()
        tracked = self.tracker.update(detections)
        timing["track"] = (time.time() - t0) * 1000
        timing["path_track"] = "cpu"

        # 6. Scale back
        scale = 1.0 / self.settings.upscale_factor if self.settings.upscale_factor != 1.0 else 1.0
        scaled_tracks = [self._scale_track(track, scale) for track in tracked]

        # 7. OSC output
        if self.osc and self.settings.osc_enabled:
            self.osc.send_frame(scaled_tracks, original_w, original_h)

        latency_ms = (time.time() - frame_start) * 1000
        timing["total"] = latency_ms
        self._timing = timing
        return scaled_tracks, enhanced, timing, latency_ms

    def _scale_track(self, track: DancerTrack, scale: float) -> ScaledTrack:
        return ScaledTrack(
            track_id=track.track_id,
            keypoints=track.keypoints * scale,
            confidence=track.confidence.copy(),
            bbox=track.bbox * scale,
            history=[pt * scale for pt in track.history],
            velocity=track.get_velocity() * scale,
        )
    
    def _scale_track_xy(self, track: DancerTrack, scale_x: float, scale_y: float) -> ScaledTrack:
        """Scale track with different X and Y factors (for non-square YOLO input)."""
        scale_xy = np.array([scale_x, scale_y])
        scale_bbox = np.array([scale_x, scale_y, scale_x, scale_y])
        return ScaledTrack(
            track_id=track.track_id,
            keypoints=track.keypoints * scale_xy,
            confidence=track.confidence.copy(),
            bbox=track.bbox * scale_bbox,
            history=[pt * scale_xy for pt in track.history],
            velocity=track.get_velocity() * scale_xy,
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
        
        # History and velocity
        history = [(pt - pad_xy) * inv_scale for pt in track.history]
        velocity = track.get_velocity() * inv_scale
        
        return ScaledTrack(
            track_id=track.track_id,
            keypoints=keypoints,
            confidence=track.confidence.copy(),
            bbox=bbox,
            history=history,
            velocity=velocity,
        )

    # ------------------------------------------------------------------
    # Detection helpers
    # ------------------------------------------------------------------
    def _extract_detections(self, results) -> List[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
        detections = []
        for result in results:
            if result.keypoints is None or len(result.keypoints) == 0:
                continue
            keypoints_data = result.keypoints.data.cpu().numpy()
            boxes = result.boxes.xyxy.cpu().numpy() if result.boxes is not None else None

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
        return detections

    def _filter_duplicate_detections(self, detections):
        if len(detections) <= 1:
            return detections

        centroid_dist_thresh = self.settings.person_height_px * 0.75
        keypoint_dist_thresh = self.settings.person_height_px * 0.25
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
        iou = self._compute_iou(bbox_i, bbox_j)
        if iou > 0.3:
            return True
        if self._bbox_contains(bbox_i, bbox_j):
            return True
        mask_i = conf_i > KEYPOINT_CONFIDENCE
        mask_j = conf_j > KEYPOINT_CONFIDENCE
        if np.any(mask_i) and np.any(mask_j):
            cent_i = np.average(kpts_i[mask_i], axis=0, weights=conf_i[mask_i])
            cent_j = np.average(kpts_j[mask_j], axis=0, weights=conf_j[mask_j])
            dist = np.linalg.norm(cent_i - cent_j)
            if dist < centroid_dist_thresh:
                return True
            if np.sum(mask_i & mask_j) >= 5:
                both_valid = mask_i & mask_j
                kpt_dists = np.linalg.norm(kpts_i[both_valid] - kpts_j[both_valid], axis=1)
                if np.median(kpt_dists) < keypoint_dist_thresh:
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
        return in_x and in_y and size_ratio < 0.7

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------
    @property
    def timing(self) -> Dict[str, float]:
        return self._timing

    def get_brightness(self) -> float:
        return self.enhancer.get_status().get("brightness", 0.0)