"""
WallDance 1080p - Multi-person pose detection for wall dancers
Optimized for: 50m scene, 6 dancers, low-light outdoor conditions

Features:
- YOLO11-pose detection with upscaling for small figures
- Kalman+Hungarian tracking for ID persistence
- Low-light enhancement (CLAHE + gamma)
- OSC output for VJ/lighting integration
- DearPyGui control panel

Usage:
    ./run.sh
"""

import cv2
import numpy as np
import time
from dataclasses import dataclass
from typing import List, Optional
from ultralytics import YOLO
import dearpygui.dearpygui as dpg

from config import (
    CAMERA_INDEX, CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS,
    UPSCALE_FACTOR, ENHANCE_ENABLED,
    YOLO_MODEL, YOLO_CONFIDENCE, YOLO_IOU_THRESHOLD, YOLO_IMGSZ, MAX_PERSONS,
    KEYPOINT_CONFIDENCE, DISPLAY_SCALE, PREVIEW_ENABLED,
    SHOW_TRAILS, SHOW_SKELETON, SHOW_KEYPOINTS, SHOW_BBOX, SHOW_ID,
    OSC_ENABLED, OSC_IP, OSC_PORT,
    CLAHE_CLIP_LIMIT, GAMMA_CORRECTION,
    TRACKER_DISTANCE_THRESHOLD, TRACKER_MAX_AGE,
    PERSON_HEIGHT_PX, PERSON_HEIGHT_MIN_RATIO, PERSON_HEIGHT_MAX_RATIO,
    MODELS_DIR
)
from enhancer import ImageEnhancer
from tracker import DancerTracker
from osc_output import OSCSender
from visualization import draw_dancer
from gui import WallDanceGUI


@dataclass
class ScaledTrack:
    """Lightweight container for scaled track data (for output/visualization)"""
    track_id: int
    keypoints: np.ndarray
    confidence: np.ndarray
    bbox: np.ndarray
    history: List[np.ndarray]
    velocity: np.ndarray


class WallDance:
    """Main WallDance application with DearPyGui interface."""
    
    def __init__(self):
        print("=" * 60)
        print("WallDance 1080p - Multi-Person Pose Detection")
        print("=" * 60)
        
        # Ensure models directory exists
        import os
        os.makedirs(MODELS_DIR, exist_ok=True)
        print(f"Models directory: {MODELS_DIR}")
        
        # Load YOLO model from shared models folder
        model_path = os.path.join(MODELS_DIR, YOLO_MODEL)
        print(f"Loading {YOLO_MODEL} from {MODELS_DIR}...")
        self.model = YOLO(model_path)
        self.yolo_confidence = YOLO_CONFIDENCE
        self.yolo_imgsz = YOLO_IMGSZ
        self.max_persons = MAX_PERSONS
        print("Model loaded!")
        
        # Initialize components
        self.enhancer = ImageEnhancer()
        self.tracker = DancerTracker()
        self.osc: Optional[OSCSender] = None
        self.osc_enabled = OSC_ENABLED
        self.osc_ip = OSC_IP
        self.osc_port = OSC_PORT
        if self.osc_enabled:
            self._init_osc()
        
        # Processing state
        self.upscale_factor = UPSCALE_FACTOR
        self.enhance_enabled = ENHANCE_ENABLED
        self.enhance_lite = False  # Lite mode: gamma only, no CLAHE
        self.current_model = YOLO_MODEL  # Full name with .pt
        self.current_model_name = YOLO_MODEL.replace('.pt', '')  # Display name
        self.use_fp16 = False
        self.frame_skip = 0  # 0=process every frame, N=skip N frames between processing
        self.frame_skip_counter = 0
        self.last_tracks = []  # Cache for frame skipping
        self.preview_enabled = PREVIEW_ENABLED  # Push video to GUI (disable to measure FPS impact)
        
        # Person size calibration
        self.person_height_px = PERSON_HEIGHT_PX  # Expected person height in pixels
        self.person_height_min = PERSON_HEIGHT_MIN_RATIO
        self.person_height_max = PERSON_HEIGHT_MAX_RATIO
        
        # Visualization settings
        self.show_trails = SHOW_TRAILS
        self.show_skeleton = SHOW_SKELETON
        self.show_keypoints = SHOW_KEYPOINTS
        self.show_bbox = SHOW_BBOX
        self.show_ids = SHOW_ID
        
        # Performance tracking
        self.fps = 0.0
        self.frame_count = 0
        self.last_fps_time = time.time()
        self.last_frame_time = time.time()
        self.latency_ms = 0.0
        self.timing = {}  # Per-stage timing info
        
        # Camera
        self.cap: Optional[cv2.VideoCapture] = None
        self.camera_width = CAMERA_WIDTH
        self.camera_height = CAMERA_HEIGHT
        
        # GUI
        self.gui: Optional[WallDanceGUI] = None
        self.running = False
        
        # Display size for GUI
        self.display_scale = DISPLAY_SCALE
        self.display_width = int(CAMERA_WIDTH * self.display_scale)
        self.display_height = int(CAMERA_HEIGHT * self.display_scale)
    
    def _init_osc(self):
        """Initialize OSC sender."""
        try:
            self.osc = OSCSender(self.osc_ip, self.osc_port)
        except Exception as e:
            print(f"OSC init failed: {e}")
            self.osc = None
    
    def _get_gui_config(self):
        """Get current config for GUI initialization."""
        return {
            'video_width': self.display_width,
            'video_height': self.display_height,
            'camera_width': self.camera_width,
            'camera_height': self.camera_height,
            'model': self.current_model_name,
            'confidence': self.yolo_confidence,
            'max_persons': self.max_persons,
            'fp16': self.use_fp16,
            'frame_skip': self.frame_skip,
            'yolo_imgsz': self.yolo_imgsz,
            'person_height_px': self.person_height_px,
            'enhance_enabled': self.enhance_enabled,
            'enhance_lite': self.enhance_lite,
            'clahe_clip': CLAHE_CLIP_LIMIT,
            'gamma': GAMMA_CORRECTION,
            'upscale_factor': self.upscale_factor,
            'show_skeleton': self.show_skeleton,
            'show_keypoints': self.show_keypoints,
            'show_bbox': self.show_bbox,
            'show_trails': self.show_trails,
            'show_ids': self.show_ids,
            'tracker_distance': TRACKER_DISTANCE_THRESHOLD,
            'tracker_max_age': TRACKER_MAX_AGE,
            'osc_enabled': self.osc_enabled,
            'osc_ip': self.osc_ip,
            'osc_port': self.osc_port,
            'preview_enabled': self.preview_enabled,
        }
    
    def _get_gui_callbacks(self):
        """Get callback functions for GUI."""
        return {
            'on_enhance_toggle': self._cb_enhance_toggle,
            'on_enhance_lite_toggle': self._cb_enhance_lite_toggle,
            'on_upscale_change': self._cb_upscale_change,
            'on_clahe_change': self._cb_clahe_change,
            'on_gamma_change': self._cb_gamma_change,
            'on_confidence_change': self._cb_confidence_change,
            'on_max_persons_change': self._cb_max_persons_change,
            'on_model_change': self._cb_model_change,
            'on_fp16_toggle': self._cb_fp16_toggle,
            'on_frame_skip_change': self._cb_frame_skip_change,
            'on_imgsz_change': self._cb_imgsz_change,
            'on_person_height_change': self._cb_person_height_change,
            'on_visualization_toggle': self._cb_visualization_toggle,
            'on_tracker_distance_change': self._cb_tracker_distance_change,
            'on_tracker_age_change': self._cb_tracker_age_change,
            'on_tracker_reset': self._cb_tracker_reset,
            'on_osc_toggle': self._cb_osc_toggle,
            'on_osc_config': self._cb_osc_config,
            'on_preview_toggle': self._cb_preview_toggle,
            'on_save_config': self._cb_save_config,
            'on_load_config': self._cb_load_config,
            'on_quit': self._cb_quit,
        }
    
    # === GUI Callbacks ===
    
    def _cb_enhance_toggle(self, enabled: bool):
        self.enhance_enabled = enabled
        print(f"Enhancement: {'ON' if enabled else 'OFF'}")
    
    def _cb_enhance_lite_toggle(self, enabled: bool):
        self.enhance_lite = enabled
        print(f"Enhancement Lite Mode: {'ON (gamma only)' if enabled else 'OFF (full CLAHE)'}")
    
    def _cb_upscale_change(self, factor: float):
        self.upscale_factor = factor
        print(f"Upscale: {factor:.1f}x")
    
    def _cb_clahe_change(self, value: float):
        self.enhancer.clahe_clip = value
        self.enhancer._update_clahe()
        print(f"CLAHE clip: {value:.1f}")
    
    def _cb_gamma_change(self, value: float):
        self.enhancer.gamma = value
        self.enhancer._update_gamma_lut()
        print(f"Gamma: {value:.2f}")
    
    def _cb_confidence_change(self, value: float):
        self.yolo_confidence = value
        print(f"Confidence: {value:.2f}")
    
    def _cb_max_persons_change(self, value: int):
        self.max_persons = value
        print(f"Max persons: {value}")
    
    def _cb_visualization_toggle(self, name: str, enabled: bool):
        if name == 'skeleton':
            self.show_skeleton = enabled
        elif name == 'keypoints':
            self.show_keypoints = enabled
        elif name == 'bbox':
            self.show_bbox = enabled
        elif name == 'trails':
            self.show_trails = enabled
        elif name == 'ids':
            self.show_ids = enabled
        print(f"{name.capitalize()}: {'ON' if enabled else 'OFF'}")
    
    def _cb_tracker_distance_change(self, value: int):
        self.tracker.distance_threshold = value
        print(f"Tracker distance: {value}px")
    
    def _cb_tracker_age_change(self, value: int):
        self.tracker.max_age = value
        print(f"Tracker max age: {value} frames")
    
    def _cb_tracker_reset(self):
        self.tracker.reset()
        if self.osc:
            self.osc.send_clear()
        print("Tracker reset")
    
    def _cb_osc_toggle(self, enabled: bool):
        self.osc_enabled = enabled
        if enabled and not self.osc:
            self._init_osc()
        print(f"OSC: {'ON' if enabled else 'OFF'}")
    
    def _cb_osc_config(self, ip: str, port: int):
        if ip != self.osc_ip or port != self.osc_port:
            self.osc_ip = ip
            self.osc_port = port
            if self.osc_enabled:
                self._init_osc()
            print(f"OSC target: {ip}:{port}")
    
    def _cb_save_config(self):
        # TODO: Implement config saving
        print("Config save not yet implemented")
    
    def _cb_load_config(self):
        # TODO: Implement config loading
        print("Config load not yet implemented")
    
    def _cb_model_change(self, model_name: str):
        """Switch YOLO model variant."""
        import os
        # GUI sends name without .pt extension
        full_model_name = f"{model_name}.pt"
        if full_model_name == self.current_model:
            return
        model_path = os.path.join(MODELS_DIR, full_model_name)
        print(f"Loading model: {full_model_name} from {MODELS_DIR}...")
        try:
            self.model = YOLO(model_path)
            self.current_model = full_model_name
            self.current_model_name = model_name
            print(f"Model loaded: {full_model_name}")
        except Exception as e:
            print(f"Failed to load model {full_model_name}: {e}")
    
    def _cb_fp16_toggle(self, enabled: bool):
        """Toggle FP16 half-precision inference."""
        self.use_fp16 = enabled
        print(f"FP16 inference: {'ON' if enabled else 'OFF'}")
    
    def _cb_frame_skip_change(self, value: int):
        """Set frame skip value. 0=process all, N=skip N frames between."""
        self.frame_skip = max(0, int(value))
        if self.frame_skip == 0:
            print("Frame skip: OFF (process every frame)")
        else:
            print(f"Frame skip: {self.frame_skip} (process every {self.frame_skip + 1} frames)")
    
    def _cb_imgsz_change(self, value: int):
        """Set YOLO input size."""
        self.yolo_imgsz = int(value)
        # Warn if imgsz is larger than camera resolution
        max_cam_dim = max(self.camera_width, self.camera_height)
        if self.yolo_imgsz > max_cam_dim:
            print(f"⚠️  YOLO imgsz {self.yolo_imgsz} > camera {max_cam_dim}px - may reduce accuracy (padding)")
        else:
            print(f"YOLO imgsz: {self.yolo_imgsz}")
    
    def _cb_person_height_change(self, value: int):
        """Set expected person height in pixels for calibration."""
        self.person_height_px = int(value)
        # Also update tracker distance threshold based on person size
        # Typical movement per frame shouldn't exceed ~30% of body height
        self.tracker.distance_threshold = max(200, int(self.person_height_px * 1.5))
    
    def _cb_quit(self):
        self.running = False
    
    def _cb_preview_toggle(self, enabled: bool):
        """Toggle video preview to GUI (for FPS impact measurement)."""
        self.preview_enabled = enabled
        if enabled:
            print("Preview: ON (video pushed to GUI)")
        else:
            print("Preview: OFF (no video output - measure raw FPS)")
    
    # === Detection Filtering ===
    
    def _compute_iou(self, bbox1, bbox2):
        """Compute IoU between two bboxes (x, y, w, h format)."""
        x1, y1, w1, h1 = bbox1
        x2, y2, w2, h2 = bbox2
        
        # Convert to x1, y1, x2, y2
        box1 = (x1, y1, x1 + w1, y1 + h1)
        box2 = (x2, y2, x2 + w2, y2 + h2)
        
        # Intersection
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
    
    def _bbox_contains(self, outer, inner):
        """Check if outer bbox mostly contains inner bbox."""
        x1, y1, w1, h1 = outer
        x2, y2, w2, h2 = inner
        
        # Inner bbox center
        cx = x2 + w2 / 2
        cy = y2 + h2 / 2
        
        # Check if inner center is within outer bbox
        in_x = x1 <= cx <= x1 + w1
        in_y = y1 <= cy <= y1 + h1
        
        # Also check if inner is much smaller (likely a body part, not a full person)
        size_ratio = (w2 * h2) / (w1 * h1) if w1 * h1 > 0 else 1.0
        
        return in_x and in_y and size_ratio < 0.7  # Inner is inside and smaller
    
    def _filter_duplicate_detections(self, detections):
        """
        Filter out duplicate detections (same person detected twice).
        Uses person_height_px for scaling thresholds.
        When duplicates are found, MERGE their keypoints instead of discarding.
        """
        if len(detections) <= 1:
            return detections
        
        # Thresholds scaled by person height
        centroid_dist_thresh = self.person_height_px * 0.75  # Within 75% of body height
        keypoint_dist_thresh = self.person_height_px * 0.25  # Within 25% of body height
        min_height = self.person_height_px * self.person_height_min
        max_height = self.person_height_px * self.person_height_max
        
        # First pass: filter by size (but keep track of small ones for merging)
        size_filtered = []
        small_detections = []  # Detections that are too small (likely body parts)
        
        for kpts, conf, bbox in detections:
            h = bbox[3]  # bbox height
            if h < min_height:
                # Too small - might be a body part, keep for potential merge
                small_detections.append((kpts, conf, bbox))
                continue
            if h > max_height:
                # Too large - likely a false detection
                continue
            size_filtered.append((kpts, conf, bbox))
        
        # If we have small detections, try to merge them with main detections
        for small_kpts, small_conf, small_bbox in small_detections:
            small_center = np.array([small_bbox[0] + small_bbox[2]/2, 
                                     small_bbox[1] + small_bbox[3]/2])
            
            # Find closest main detection
            best_match = None
            best_dist = float('inf')
            
            for i, (kpts, conf, bbox) in enumerate(size_filtered):
                main_center = np.array([bbox[0] + bbox[2]/2, bbox[1] + bbox[3]/2])
                dist = np.linalg.norm(small_center - main_center)
                
                # If within 1.5x person height, consider merging
                if dist < self.person_height_px * 1.5 and dist < best_dist:
                    best_dist = dist
                    best_match = i
            
            if best_match is not None:
                # Merge keypoints: keep higher confidence for each keypoint
                main_kpts, main_conf, main_bbox = size_filtered[best_match]
                merged_kpts = main_kpts.copy()
                merged_conf = main_conf.copy()
                
                for k in range(len(main_kpts)):
                    if small_conf[k] > main_conf[k]:
                        merged_kpts[k] = small_kpts[k]
                        merged_conf[k] = small_conf[k]
                
                # Update the main detection with merged keypoints
                size_filtered[best_match] = (merged_kpts, merged_conf, main_bbox)
        
        if len(size_filtered) <= 1:
            return size_filtered
        
        # Sort by bbox area (largest first) - full body detections are usually larger
        det_with_area = [(i, kpts, conf, bbox, bbox[2] * bbox[3]) 
                         for i, (kpts, conf, bbox) in enumerate(size_filtered)]
        det_with_area.sort(key=lambda x: x[4], reverse=True)
        
        kept_indices = []
        suppressed = set()
        
        for i, kpts_i, conf_i, bbox_i, area_i in det_with_area:
            if i in suppressed:
                continue
            
            kept_indices.append(i)
            
            # Check remaining detections for duplicates to merge
            for j, kpts_j, conf_j, bbox_j, area_j in det_with_area:
                if j in suppressed or j == i:
                    continue
                
                should_merge = False
                
                # Method 1: IoU overlap
                iou = self._compute_iou(bbox_i, bbox_j)
                if iou > 0.3:  # Significant overlap
                    should_merge = True
                
                # Method 2: Smaller bbox contained in larger
                elif self._bbox_contains(bbox_i, bbox_j):
                    should_merge = True
                
                # Method 3: Centroid proximity
                else:
                    mask_i = conf_i > KEYPOINT_CONFIDENCE
                    mask_j = conf_j > KEYPOINT_CONFIDENCE
                    
                    if np.any(mask_i) and np.any(mask_j):
                        cent_i = np.average(kpts_i[mask_i], axis=0, weights=conf_i[mask_i])
                        cent_j = np.average(kpts_j[mask_j], axis=0, weights=conf_j[mask_j])
                        
                        dist = np.linalg.norm(cent_i - cent_j)
                        
                        # If centroids within 75% of expected person height, likely same person
                        if dist < centroid_dist_thresh:
                            should_merge = True
                        
                        # Check if they share keypoints (same person, different detection)
                        elif np.sum(mask_i & mask_j) >= 5:
                            both_valid = mask_i & mask_j
                            kpt_dists = np.linalg.norm(kpts_i[both_valid] - kpts_j[both_valid], axis=1)
                            if np.median(kpt_dists) < keypoint_dist_thresh:
                                should_merge = True
                
                if should_merge:
                    # Merge keypoints from j into i (keep higher confidence)
                    for k in range(len(kpts_i)):
                        if conf_j[k] > conf_i[k]:
                            kpts_i[k] = kpts_j[k]
                            conf_i[k] = conf_j[k]
                    suppressed.add(j)
        
        kept = [size_filtered[i] for i in sorted(kept_indices)]
        return kept
    
    # === Visual Helpers ===
    
    def _draw_height_ruler(self, frame):
        """Draw a visual ruler showing expected person height on the left side of frame."""
        h, w = frame.shape[:2]
        height_px = self.person_height_px
        
        # Position: left side of frame, vertically centered
        x = 30  # 30 pixels from left edge
        y_center = h // 2
        y_top = y_center - height_px // 2
        y_bottom = y_center + height_px // 2
        
        # Clamp to frame bounds
        y_top = max(10, y_top)
        y_bottom = min(h - 10, y_bottom)
        
        # Colors
        color = (0, 255, 255)  # Yellow/cyan
        bg_color = (0, 0, 0)   # Black background
        
        # Draw vertical line with caps (the ruler)
        line_thickness = 2
        cap_width = 15
        
        # Background for visibility
        cv2.line(frame, (x, y_top), (x, y_bottom), bg_color, line_thickness + 4)
        cv2.line(frame, (x - cap_width//2, y_top), (x + cap_width//2, y_top), bg_color, line_thickness + 4)
        cv2.line(frame, (x - cap_width//2, y_bottom), (x + cap_width//2, y_bottom), bg_color, line_thickness + 4)
        
        # Foreground ruler
        cv2.line(frame, (x, y_top), (x, y_bottom), color, line_thickness)
        cv2.line(frame, (x - cap_width//2, y_top), (x + cap_width//2, y_top), color, line_thickness)
        cv2.line(frame, (x - cap_width//2, y_bottom), (x + cap_width//2, y_bottom), color, line_thickness)
        
        # Label with height value
        label = f"{height_px}px"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        thickness = 1
        (tw, th), _ = cv2.getTextSize(label, font, font_scale, thickness)
        text_x = x - tw // 2
        text_y = y_bottom + th + 8
        
        # Text background
        cv2.rectangle(frame, (text_x - 2, text_y - th - 2), 
                      (text_x + tw + 2, text_y + 2), bg_color, -1)
        cv2.putText(frame, label, (text_x, text_y), font, font_scale, color, thickness)
    
    # === Processing ===
    
    def process_frame(self, frame: np.ndarray):
        """Process single frame through full pipeline."""
        frame_start = time.time()
        original_h, original_w = frame.shape[:2]
        
        # 1. Enhancement
        t0 = time.time()
        if self.enhance_enabled:
            if self.enhance_lite:
                # Lite mode: gamma only, much faster
                enhanced = self.enhancer.enhance_simple(frame)
            else:
                # Full mode: CLAHE + gamma
                enhanced, _ = self.enhancer.enhance(frame)
        else:
            enhanced = frame
        t_enhance = (time.time() - t0) * 1000
        
        # 2. Upscale for detection (optional - YOLO imgsz is more efficient)
        t0 = time.time()
        if self.upscale_factor != 1.0:
            new_w = int(original_w * self.upscale_factor)
            new_h = int(original_h * self.upscale_factor)
            process_frame = cv2.resize(enhanced, (new_w, new_h), 
                                       interpolation=cv2.INTER_LINEAR)
        else:
            process_frame = enhanced
        t_upscale = (time.time() - t0) * 1000
        
        # 3. YOLO inference (imgsz controls internal resolution)
        t0 = time.time()
        results = self.model(
            process_frame,
            imgsz=self.yolo_imgsz,
            conf=self.yolo_confidence,
            iou=YOLO_IOU_THRESHOLD,
            max_det=self.max_persons,
            half=self.use_fp16,
            verbose=False
        )
        t_yolo = (time.time() - t0) * 1000
        
        # 4. Extract detections
        t0 = time.time()
        detections = []
        for result in results:
            if result.keypoints is not None and len(result.keypoints) > 0:
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
                            bbox = (xs.min(), ys.min(), 
                                   xs.max() - xs.min(), ys.max() - ys.min())
                        else:
                            continue
                    
                    detections.append((keypoints, confidence, bbox))
        
        # Filter duplicate detections (same person detected twice)
        detections = self._filter_duplicate_detections(detections)
        t_extract = (time.time() - t0) * 1000
        
        # 5. Update tracker
        t0 = time.time()
        tracked = self.tracker.update(detections)
        t_track = (time.time() - t0) * 1000
        
        # Store timing for display
        self.timing = {
            'enhance': t_enhance,
            'upscale': t_upscale,
            'yolo': t_yolo,
            'extract': t_extract,
            'track': t_track,
            'total': (time.time() - frame_start) * 1000
        }
        
        # 6. Create scaled copies
        scale = 1.0 / self.upscale_factor if self.upscale_factor != 1.0 else 1.0
        scaled_tracks = []
        
        for track in tracked:
            scaled_track = ScaledTrack(
                track_id=track.track_id,
                keypoints=track.keypoints * scale,
                confidence=track.confidence.copy(),
                bbox=track.bbox * scale,
                history=[pt * scale for pt in track.history],
                velocity=track.get_velocity() * scale
            )
            scaled_tracks.append(scaled_track)
        
        # 7. Send OSC
        if self.osc and self.osc_enabled:
            self.osc.send_frame(scaled_tracks, original_w, original_h)
        
        # Track latency
        self.latency_ms = (time.time() - frame_start) * 1000
        
        return scaled_tracks, enhanced
    
    def run(self):
        """Main application loop."""
        # Open camera
        print(f"Opening camera {CAMERA_INDEX}...")
        self.cap = cv2.VideoCapture(CAMERA_INDEX)
        
        if not self.cap.isOpened():
            print("Error: Could not open camera!")
            return
        
        # Set camera properties
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
        
        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"Camera opened: {actual_w}x{actual_h}")
        
        # Initialize GUI
        print("Initializing GUI...")
        self.gui = WallDanceGUI(
            config=self._get_gui_config(),
            callbacks=self._get_gui_callbacks()
        )
        
        # Calculate window size
        window_width = self.display_width + 360  # Video + control panel
        window_height = max(self.display_height + 80, 700)  # Ensure controls fit
        self.gui.setup(width=window_width, height=window_height)
        
        # Setup keyboard handler
        with dpg.handler_registry():
            dpg.add_key_press_handler(callback=self._handle_key)
        
        dpg.show_viewport()
        
        print("Starting main loop...")
        self.running = True
        self.last_tracked = []  # Store last tracked results for frame skipping
        
        while self.running and dpg.is_dearpygui_running():
            # Read frame
            ret, frame = self.cap.read()
            if not ret:
                print("Failed to read frame")
                break
            
            # Frame skipping logic: 0=process all, N=skip N frames between processing
            if self.frame_skip == 0:
                should_process = True
            else:
                self.frame_skip_counter += 1
                should_process = (self.frame_skip_counter > self.frame_skip)
                if should_process:
                    self.frame_skip_counter = 0
            
            if should_process:
                # Full processing with YOLO
                tracked, display_frame = self.process_frame(frame)
                self.last_tracked = tracked
            else:
                # Skip YOLO, just prepare display frame with last tracks
                if self.enhance_enabled:
                    display_frame, _ = self.enhancer.enhance(frame)
                else:
                    display_frame = frame.copy()
                tracked = self.last_tracked
            
            # Preview: only process visualization if enabled
            if self.preview_enabled:
                # Draw dancers on frame
                for track in tracked:
                    draw_dancer(
                        display_frame, track,
                        show_skeleton=self.show_skeleton,
                        show_keypoints=self.show_keypoints,
                        show_bbox=self.show_bbox,
                        show_trail=self.show_trails,
                        show_id=self.show_ids
                    )
                
                # Draw person height calibration ruler on left side
                self._draw_height_ruler(display_frame)
                
                # Resize for display
                if self.display_scale != 1.0:
                    display_frame = cv2.resize(
                        display_frame, 
                        (self.display_width, self.display_height)
                    )
                
                # Update GUI video frame
                self.gui.update_frame(display_frame)
            
            # Update FPS
            self.frame_count += 1
            now = time.time()
            if now - self.last_fps_time >= 1.0:
                self.fps = self.frame_count / (now - self.last_fps_time)
                self.frame_count = 0
                self.last_fps_time = now
                
                # Print timing stats to terminal every second
                if self.timing:
                    t = self.timing
                    print(f"FPS: {self.fps:5.1f} | Enh: {t.get('enhance', 0):5.1f}ms | "
                          f"YOLO: {t.get('yolo', 0):5.1f}ms | "
                          f"Trk: {t.get('track', 0):4.1f}ms | Total: {t.get('total', 0):5.1f}ms | "
                          f"imgsz: {self.yolo_imgsz} | h: {self.person_height_px}px")
            
            # Update stats
            brightness = self.enhancer.get_status().get('brightness', 0)
            self.gui.update_stats(
                fps=self.fps,
                num_dancers=len(tracked),
                latency_ms=self.latency_ms,
                brightness=brightness,
                timing=self.timing
            )
            
            # Render GUI frame
            dpg.render_dearpygui_frame()
        
        # Cleanup
        self.cap.release()
        dpg.destroy_context()
        print("WallDance stopped.")
    
    def _handle_key(self, sender, app_data):
        """Handle keyboard shortcuts."""
        key = app_data
        
        # Map key codes to actions
        if key == dpg.mvKey_Q:
            self.running = False
        elif key == dpg.mvKey_E:
            self.enhance_enabled = not self.enhance_enabled
            self.gui.sync_checkbox('enhance', self.enhance_enabled)
            print(f"Enhancement: {'ON' if self.enhance_enabled else 'OFF'}")
        elif key == dpg.mvKey_T:
            self.show_trails = not self.show_trails
            self.gui.sync_checkbox('trails', self.show_trails)
            print(f"Trails: {'ON' if self.show_trails else 'OFF'}")
        elif key == dpg.mvKey_S:
            self.show_skeleton = not self.show_skeleton
            self.gui.sync_checkbox('skeleton', self.show_skeleton)
            print(f"Skeleton: {'ON' if self.show_skeleton else 'OFF'}")
        elif key == dpg.mvKey_K:
            self.show_keypoints = not self.show_keypoints
            self.gui.sync_checkbox('keypoints', self.show_keypoints)
            print(f"Keypoints: {'ON' if self.show_keypoints else 'OFF'}")
        elif key == dpg.mvKey_B:
            self.show_bbox = not self.show_bbox
            self.gui.sync_checkbox('bbox', self.show_bbox)
            print(f"Bounding box: {'ON' if self.show_bbox else 'OFF'}")
        elif key == dpg.mvKey_I:
            self.show_ids = not self.show_ids
            self.gui.sync_checkbox('ids', self.show_ids)
            print(f"IDs: {'ON' if self.show_ids else 'OFF'}")
        elif key == dpg.mvKey_P:
            self.preview_enabled = not self.preview_enabled
            self.gui.sync_checkbox('preview', self.preview_enabled)
            print(f"Preview: {'ON' if self.preview_enabled else 'OFF (measure raw FPS)'}")
        elif key == dpg.mvKey_R:
            self._cb_tracker_reset()
        elif key == dpg.mvKey_Add or key == 61:  # 61 is '=' key
            self.upscale_factor = min(4.0, self.upscale_factor + 0.5)
            self.gui.sync_slider('upscale', self.upscale_factor)
            print(f"Upscale: {self.upscale_factor}x")
        elif key == dpg.mvKey_Subtract or key == 45:  # 45 is '-' key
            self.upscale_factor = max(1.0, self.upscale_factor - 0.5)
            self.gui.sync_slider('upscale', self.upscale_factor)
            print(f"Upscale: {self.upscale_factor}x")


def main():
    app = WallDance()
    app.run()


if __name__ == "__main__":
    main()
