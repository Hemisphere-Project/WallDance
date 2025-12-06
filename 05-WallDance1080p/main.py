"""
WallDance 1080p - Multi-person pose detection for wall dancers
Optimized for: 50m scene, 6 dancers, low-light outdoor conditions

Usage:
    ./run.sh [options]
    
Controls:
    q - Quit
    e - Toggle enhancement
    t - Toggle trails
    r - Reset tracker
    + - Increase upscale
    - - Decrease upscale
"""

import cv2
import numpy as np
import time
from dataclasses import dataclass
from typing import List
from ultralytics import YOLO

from config import (
    CAMERA_INDEX, CAMERA_WIDTH, CAMERA_HEIGHT, CAMERA_FPS,
    UPSCALE_FACTOR, ENHANCE_ENABLED,
    YOLO_MODEL, YOLO_CONFIDENCE, YOLO_IOU_THRESHOLD, MAX_PERSONS,
    KEYPOINT_CONFIDENCE, DISPLAY_ENABLED, DISPLAY_SCALE, SHOW_INFO,
    SHOW_TRAILS, SHOW_SKELETON, SHOW_KEYPOINTS, SHOW_BBOX, SHOW_ID,
    OSC_ENABLED
)
from enhancer import ImageEnhancer
from tracker import DancerTracker
from osc_output import OSCSender
from visualization import draw_dancer, draw_info_overlay, draw_help_overlay


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
    """Main WallDance application."""
    
    def __init__(self):
        print("=" * 60)
        print("WallDance 1080p - Multi-Person Pose Detection")
        print("=" * 60)
        
        # Load YOLO model
        print(f"Loading {YOLO_MODEL}...")
        self.model = YOLO(YOLO_MODEL)
        print("Model loaded!")
        
        # Initialize components
        self.enhancer = ImageEnhancer()
        self.tracker = DancerTracker()
        self.osc = OSCSender() if OSC_ENABLED else None
        
        # State
        self.upscale_factor = UPSCALE_FACTOR
        self.enhance_enabled = ENHANCE_ENABLED
        
        # Visualization settings (toggleable)
        self.show_trails = SHOW_TRAILS
        self.show_skeleton = SHOW_SKELETON
        self.show_keypoints = SHOW_KEYPOINTS
        self.show_bbox = SHOW_BBOX
        self.show_ids = SHOW_ID
        self.show_help = False  # Help overlay toggle
        
        # FPS tracking
        self.fps = 0
        self.frame_count = 0
        self.last_fps_time = time.time()
    
    def get_settings(self):
        """Return current visualization settings as dict."""
        return {
            'trails': self.show_trails,
            'skeleton': self.show_skeleton,
            'keypoints': self.show_keypoints,
            'bbox': self.show_bbox,
            'ids': self.show_ids,
        }
    
    def process_frame(self, frame):
        """Process single frame through full pipeline."""
        original_h, original_w = frame.shape[:2]
        
        # 1. Enhancement (on original resolution for speed)
        if self.enhance_enabled:
            enhanced, was_enhanced = self.enhancer.enhance(frame)
        else:
            enhanced = frame
            was_enhanced = False
        
        # 2. Upscale for detection
        if self.upscale_factor != 1.0:
            new_w = int(original_w * self.upscale_factor)
            new_h = int(original_h * self.upscale_factor)
            process_frame = cv2.resize(enhanced, (new_w, new_h), 
                                       interpolation=cv2.INTER_LINEAR)
        else:
            process_frame = enhanced
            new_w, new_h = original_w, original_h
        
        # 3. YOLO inference
        results = self.model(
            process_frame,
            conf=YOLO_CONFIDENCE,
            iou=YOLO_IOU_THRESHOLD,
            max_det=MAX_PERSONS,
            verbose=False
        )
        
        # 4. Extract detections
        detections = []
        for result in results:
            if result.keypoints is not None and len(result.keypoints) > 0:
                keypoints_data = result.keypoints.data.cpu().numpy()
                boxes = result.boxes.xyxy.cpu().numpy() if result.boxes is not None else None
                
                for i, kpts in enumerate(keypoints_data):
                    # kpts shape: (17, 3) -> x, y, conf
                    keypoints = kpts[:, :2]
                    confidence = kpts[:, 2]
                    
                    # Get bounding box
                    if boxes is not None and i < len(boxes):
                        x1, y1, x2, y2 = boxes[i]
                        bbox = (x1, y1, x2 - x1, y2 - y1)
                    else:
                        # Compute from keypoints
                        valid = confidence > KEYPOINT_CONFIDENCE
                        if np.any(valid):
                            xs = keypoints[valid, 0]
                            ys = keypoints[valid, 1]
                            bbox = (xs.min(), ys.min(), 
                                   xs.max() - xs.min(), ys.max() - ys.min())
                        else:
                            continue
                    
                    detections.append((keypoints, confidence, bbox))
        
        # 5. Update tracker (in upscaled coordinates)
        tracked = self.tracker.update(detections)
        
        # 6. Create scaled copies for output (don't modify originals!)
        scale = 1.0 / self.upscale_factor if self.upscale_factor != 1.0 else 1.0
        scaled_tracks = []
        
        for track in tracked:
            # Create a lightweight copy with scaled coordinates
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
        if self.osc:
            self.osc.send_frame(scaled_tracks, original_w, original_h)
        
        return scaled_tracks, enhanced
    
    def run(self):
        """Main loop."""
        # Open camera
        print(f"Opening camera {CAMERA_INDEX}...")
        cap = cv2.VideoCapture(CAMERA_INDEX)
        
        if not cap.isOpened():
            print("Error: Could not open camera!")
            return
        
        # Set camera properties
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)
        
        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"Camera opened: {actual_w}x{actual_h}")
        
        print("\nControls: Press 'H' for help overlay")
        print()
        
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failed to read frame")
                break
            
            # Process - returns scaled tracks and enhanced image
            tracked, display_frame = self.process_frame(frame)
            
            # Visualization
            if DISPLAY_ENABLED:
                # Draw dancers on enhanced frame
                for track in tracked:
                    draw_dancer(
                        display_frame, track,
                        show_skeleton=self.show_skeleton,
                        show_keypoints=self.show_keypoints,
                        show_bbox=self.show_bbox,
                        show_trail=self.show_trails,
                        show_id=self.show_ids
                    )
                
                if SHOW_INFO:
                    # Update FPS
                    self.frame_count += 1
                    if time.time() - self.last_fps_time >= 1.0:
                        self.fps = self.frame_count / (time.time() - self.last_fps_time)
                        self.frame_count = 0
                        self.last_fps_time = time.time()
                    
                    draw_info_overlay(
                        display_frame, 
                        self.fps, 
                        len(tracked),
                        self.enhancer.get_status(),
                        self.upscale_factor,
                        self.get_settings()
                    )
                
                # Draw help overlay if enabled
                if self.show_help:
                    draw_help_overlay(display_frame)
                
                # Scale display if needed
                if DISPLAY_SCALE != 1.0:
                    disp_h = int(display_frame.shape[0] * DISPLAY_SCALE)
                    disp_w = int(display_frame.shape[1] * DISPLAY_SCALE)
                    display_frame = cv2.resize(display_frame, (disp_w, disp_h))
                
                cv2.imshow('WallDance', display_frame)
            
            # Handle keys
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('h'):
                self.show_help = not self.show_help
            elif key == ord('e'):
                self.enhance_enabled = not self.enhance_enabled
                print(f"Enhancement: {'ON' if self.enhance_enabled else 'OFF'}")
            elif key == ord('t'):
                self.show_trails = not self.show_trails
                print(f"Trails: {'ON' if self.show_trails else 'OFF'}")
            elif key == ord('s'):
                self.show_skeleton = not self.show_skeleton
                print(f"Skeleton: {'ON' if self.show_skeleton else 'OFF'}")
            elif key == ord('k'):
                self.show_keypoints = not self.show_keypoints
                print(f"Keypoints: {'ON' if self.show_keypoints else 'OFF'}")
            elif key == ord('b'):
                self.show_bbox = not self.show_bbox
                print(f"Bounding box: {'ON' if self.show_bbox else 'OFF'}")
            elif key == ord('i'):
                self.show_ids = not self.show_ids
                print(f"IDs: {'ON' if self.show_ids else 'OFF'}")
            elif key == ord('r'):
                self.tracker.reset()
                if self.osc:
                    self.osc.send_clear()
                print("Tracker reset")
            elif key == ord('+') or key == ord('='):
                self.upscale_factor = min(4.0, self.upscale_factor + 0.5)
                print(f"Upscale: {self.upscale_factor}x")
            elif key == ord('-'):
                self.upscale_factor = max(1.0, self.upscale_factor - 0.5)
                print(f"Upscale: {self.upscale_factor}x")
        
        cap.release()
        cv2.destroyAllWindows()
        print("WallDance stopped.")


def main():
    app = WallDance()
    app.run()


if __name__ == "__main__":
    main()
