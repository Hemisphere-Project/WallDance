"""
Configuration for WallDance 1080p
Optimized for: 50m wide scene, 6 dancers, low-light outdoor conditions

All parameters are tunable - adjust based on your specific setup.
"""

# =============================================================================
# PATHS
# =============================================================================
# Shared models directory at workspace root (used by all workflows)
import os
# Go up from src/ to 05-WallDance1080p/, then up to WallDance/, then into models/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WORKSPACE_ROOT = os.path.dirname(_PROJECT_ROOT)
MODELS_DIR = os.path.join(_WORKSPACE_ROOT, "models")

# =============================================================================
# CAMERA & INPUT
# =============================================================================
CAMERA_INDEX = 0                    # Camera device index (0 = default webcam/capture card)
CAMERA_WIDTH = 1920                 # Input resolution width
CAMERA_HEIGHT = 1080                # Input resolution height
CAMERA_FPS = 30                     # Target camera FPS

# =============================================================================
# IMAGE PROCESSING - UPSCALING (DEPRECATED - use YOLO imgsz instead)
# =============================================================================
# NOTE: YOLO's imgsz parameter is more efficient than pre-upscaling
# Set to 1.0 and use YOLO_IMGSZ for small figure detection
UPSCALE_FACTOR = 1.0                # 1.0 = native (recommended)
                                    # Use YOLO_IMGSZ=1280 instead of upscaling

# =============================================================================
# IMAGE PROCESSING - LOW LIGHT ENHANCEMENT
# =============================================================================
ENHANCE_ENABLED = True              # Enable adaptive enhancement
ENHANCE_AUTO_DETECT = True          # Auto-detect when enhancement is needed

# CLAHE (Contrast Limited Adaptive Histogram Equalization)
CLAHE_CLIP_LIMIT = 3.0              # Higher = more contrast (1.0-5.0)
CLAHE_TILE_SIZE = 8                 # Tile grid size (4-16)

# Gamma correction for dark scenes
GAMMA_CORRECTION = 1.2              # >1.0 brightens, <1.0 darkens (0.5-2.0)
GAMMA_AUTO = True                   # Auto-adjust gamma based on brightness

# Brightness threshold for auto-enhancement
BRIGHTNESS_THRESHOLD = 60           # Below this (0-255), apply enhancement

# =============================================================================
# YOLO MODEL
# =============================================================================
YOLO_MODEL = "yolo11m-pose.pt"      # Options: yolo11n/s/m/l/x-pose.pt
                                    #          yolov8n/s/m/l/x-pose.pt
                                    # n=fastest, x=most accurate
                                    # v8 models are older but well-tested
YOLO_CONFIDENCE = 0.25              # Detection confidence threshold (0.1-0.9)
YOLO_IOU_THRESHOLD = 0.45           # NMS IoU threshold
YOLO_IMGSZ = 1280                   # YOLO input size (640, 960, 1280, 1920, 2560)
                                    # IMPORTANT: Should be ≤ camera resolution for best results
                                    # - 640-960: Fast, good for close-up / webcam
                                    # - 1280: Balanced, good for 1080p cameras at medium distance
                                    # - 1920-2560: Only useful with 4K cameras for distant subjects
                                    # Values > camera resolution cause padding and reduced accuracy
MAX_PERSONS = 6                     # Maximum dancers to track

# TensorRT optimization
USE_TENSORRT = True                 # If True, export and use TensorRT .engine files
                                    # Provides ~2x inference speedup
                                    # Engine is GPU-specific (rebuilt per GPU)
                                    # First run will take 2-5 minutes to export

# =============================================================================
# PERSON SIZE CALIBRATION
# =============================================================================
# Expected height of a person in pixels (at camera resolution)
# Use the calibration slider in GUI to adjust based on your scene
# This helps filter false detections and scale tracking thresholds
PERSON_HEIGHT_PX = 150              # Expected person height in pixels (50-800)
                                    # Small figures at 50m: ~100-150px
                                    # Medium distance: ~200-400px
                                    # Close up (webcam): ~500-800px
PERSON_HEIGHT_MIN_RATIO = 0.3       # Min detection height as ratio of expected
PERSON_HEIGHT_MAX_RATIO = 2.5       # Max detection height as ratio of expected

# =============================================================================
# KEYPOINT DETECTION
# =============================================================================
KEYPOINT_CONFIDENCE = 0.3           # Minimum confidence to consider keypoint valid

# COCO keypoint indices:
# 0: nose, 1: left_eye, 2: right_eye, 3: left_ear, 4: right_ear,
# 5: left_shoulder, 6: right_shoulder, 7: left_elbow, 8: right_elbow,
# 9: left_wrist, 10: right_wrist, 11: left_hip, 12: right_hip,
# 13: left_knee, 14: right_knee, 15: left_ankle, 16: right_ankle

# Skeleton connections for drawing
SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4),      # Face
    (5, 6), (5, 7), (7, 9),              # Left arm
    (6, 8), (8, 10),                     # Right arm
    (5, 11), (6, 12), (11, 12),          # Torso
    (11, 13), (13, 15),                  # Left leg
    (12, 14), (14, 16)                   # Right leg
]

# =============================================================================
# TRACKER
# =============================================================================
TRACKER_MAX_AGE = 45                # Frames to keep lost track (~3 sec at 15 FPS)
TRACKER_MIN_HITS = 2                # Hits to confirm track
TRACKER_DISTANCE_THRESHOLD = 500    # Max match distance (pixels, at 1280 imgsz)
                                    # Increase for fast-moving dancers or camera far away
TRACKER_VELOCITY_WEIGHT = 0.6       # Trust in velocity prediction (0-1)
TRACKER_PROCESS_NOISE = 2.5         # Kalman Q - velocity adaptation
TRACKER_MEASUREMENT_NOISE = 2.0     # Kalman R - smoothing

# =============================================================================
# OSC OUTPUT
# =============================================================================
OSC_ENABLED = True                  # Enable OSC output
OSC_IP = "127.0.0.1"                # Target IP address
OSC_PORT = 9000                     # Target port

# OSC message format:
# /walldance/dancer/<id>/centroid    [x, y]           (normalized 0-1)
# /walldance/dancer/<id>/bbox        [x, y, w, h]     (normalized 0-1)
# /walldance/dancer/<id>/keypoints   [x0,y0,c0, ...]  (17 keypoints, normalized)
# /walldance/dancer/<id>/velocity    [vx, vy]         (normalized per frame)
# /walldance/count                   [n]              (number of tracked dancers)

# =============================================================================
# VISUALIZATION
# =============================================================================
DISPLAY_ENABLED = True              # Show visualization window
PREVIEW_ENABLED = True              # Push video to GUI (disable to measure FPS impact)
# Render at lower resolution to save GPU/CPU, but keep the on-screen area size.
PREVIEW_RENDER_SCALE = 0.5          # Texture resolution scale (0.3-1.0); lower = faster
PREVIEW_DISPLAY_SCALE = 0.5        # On-screen preview area scale relative to camera
PREVIEW_SCALE = PREVIEW_DISPLAY_SCALE  # Backward compatibility alias
SHOW_SKELETON = True                # Draw skeleton
SHOW_KEYPOINTS = True               # Draw keypoints
SHOW_BBOX = True                    # Draw bounding box
SHOW_TRAILS = True                  # Draw motion trails
SHOW_ID = True                      # Draw track ID
SHOW_INFO = True                    # Show FPS, enhancement status, etc.

# Colors for different dancers (BGR)
DANCER_COLORS = [
    (0, 255, 0),      # Green
    (255, 100, 0),    # Blue
    (0, 100, 255),    # Orange
    (255, 255, 0),    # Cyan
    (255, 0, 255),    # Magenta
    (0, 255, 255),    # Yellow
]
