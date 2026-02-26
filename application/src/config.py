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
# Go up from src/ to application/, then up to workspace root, then into models/
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
# IMAGE PROCESSING - LOW LIGHT ENHANCEMENT
# =============================================================================
ENHANCE_ENABLED = True              # Enable adaptive enhancement

# CLAHE (Contrast Limited Adaptive Histogram Equalization)
CLAHE_CLIP_LIMIT = 3.0              # Higher = more contrast (1.0-5.0)

# Gamma correction for dark scenes
GAMMA_CORRECTION = 1.2              # >1.0 brightens, <1.0 darkens (0.5-2.0)

# Brightness threshold for auto-enhancement
BRIGHTNESS_THRESHOLD = 60           # Below this (0-255), apply enhancement

# Temporal Denoising (GPU only)
DENOISE_STRENGTH = 0.0              # 0.0 = Off, 0.9 = Strong smoothing
                                    # Reduces sensor noise in low light
                                    # Only active when USE_GPU_PATH = True

# =============================================================================
# YOLO MODEL
# =============================================================================
YOLO_MODEL = "yolo11m-pose.pt"      # Options: yolo26n/s/m/l/x-pose.pt (latest, NMS-free)
                                    #          yolo11n/s/m/l/x-pose.pt
                                    #          yolov8n/s/m/l/x-pose.pt
                                    # n=fastest, x=most accurate
                                    # YOLO26: Latest, end-to-end NMS-free, optimized for edge
                                    # YOLO11: Well-balanced, production-ready
                                    # YOLOv8: Older but well-tested
YOLO_CONFIDENCE = 0.25              # Detection confidence threshold (0.1-0.9)
YOLO_IOU_THRESHOLD = 0.45           # NMS IoU threshold
YOLO_IMGSZ = 800                    # YOLO input size (640, 800, 960, 1280, 1536, 1920, 2560)
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

# GPU Processing Path (see SPECIFICATIONS.md Section 14)
USE_GPU_PATH = True                 # Enable GPU frame buffer and GPU-accelerated processing
                                    # Requires OpenCV with CUDA support
                                    # Falls back to CPU if CUDA not available

# IDS staged rollout switches (stability-first)
# GPU-direct is ON for maximum YOLO efficiency: frame uploads via pinned
# memory async DMA (~4 MB mono8), YOLO runs on GPU tensor directly.
# Preview is rate-limited to reduce GPU→CPU PCIe traffic.
# Full investigation: docs/IDS_CAMERA_STALL_INVESTIGATION.md
IDS_USE_GPU_DIRECT = True
# Cap IDS acquisition FPS (independent from OpenCV camera FPS).
# Lower values can improve stream stability on full-resolution IDS capture.
IDS_MAX_FPS = 20
# Upper limit on auto-exposure (µs). Prevents the camera from choosing
# exposure times so long that the frame rate drops below IDS_MAX_FPS.
# Rule of thumb: (1_000_000 / IDS_MAX_FPS) - 5000  (readout headroom).
# 0 = no limit (camera decides; may drop to ~10 FPS in the dark).
IDS_AUTO_EXPOSURE_LIMIT_US = 45000   # 45 ms → guarantees ≥ 20 FPS
# On-device ROI crop — reduces USB3 bandwidth at the sensor level.
# Fixed pixel budget: the crop area will never exceed this many pixels.
# The U3-34E0XCP native sensor is 2688×1528; budget of 1528*1528 ≈ 2.3 MP.
# The actual W×H is derived from IDS_CROP_PIXELS and IDS_RATIO.
# Set to 0 to disable on-device crop (full sensor).
# IDS_CROP_PIXELS =  1528 * 1528 # SAFE
IDS_CROP_PIXELS =  1528 * 1528 # SEEMS STABLE
# Aspect ratio (W/H) of the on-device crop. Adjustable at runtime via GUI.
# Range: 0.5 – 2.0.  Values outside sensor bounds are clamped automatically.
IDS_RATIO = 1.0
# Load camera settings from a stored UserSet on startup.
# Set in IDS Cockpit: Device → UserSet → Save to UserSet1.
# "" = don't load (use defaults), "UserSet1", "UserSet2", etc.
IDS_USER_SET = "UserSet1"

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
TRACKER_DISTANCE_THRESHOLD = 500    # Initial fallback only — overridden at startup
                                    # by set_person_height(PERSON_HEIGHT_PX).
                                    # All distance thresholds now auto-derive from
                                    # PERSON_HEIGHT_PX (the single master dial):
                                    #   match gate      = height × 1.2
                                    #   new-track gate  = height × 0.4
                                    #   duplicate gate  = height × 0.2
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
PREVIEW_ENABLED = True              # Push video to GUI (disable to measure FPS impact)
# Render at lower resolution to save GPU/CPU, but keep the on-screen area size.
PREVIEW_RENDER_SCALE = 0.35         # Texture resolution scale (0.3-1.0); lower = faster
                                    # IDS 2688×1528 @ 0.35 → 940×535 (~1.5 MB uint8 transfer)
                                    # IDS 2688×1528 @ 0.50 → 1344×764 (~3.1 MB — too heavy)
PREVIEW_DISPLAY_SCALE = 0.5        # On-screen preview area scale relative to camera
SHOW_SKELETON = True                # Draw skeleton
SHOW_KEYPOINTS = True               # Draw keypoints
SHOW_BBOX = True                    # Draw bounding box
SHOW_TRAILS = True                  # Draw motion trails
SHOW_ID = True                      # Draw track ID

# =============================================================================
# VIDEO RECORDING
# =============================================================================
# Codec used when recording to a slot.
#
#   "MJPG"  – Motion JPEG, .avi container. Near-lossless at high quality,
#              reasonable file size. Plays on Windows with K-Lite codec pack.
#              Good balance of quality and compatibility. DEFAULT.
#
#   "mp4v"  – MPEG-4 Part 2, .mp4 container. Lossy, smaller files.
#              Plays natively on Windows/macOS without any extra codec.
#              Use if you need files that open anywhere out of the box.
#
#   "FFV1"  – Lossless, .avi container. No artifacts whatsoever.
#              Large files (~1-2 GB/min at 1080p). Plays in VLC or the app,
#              but NOT in Windows Media Player / Movies & TV natively.
#              Best for archival or analysis where quality is critical.
#
RECORDING_CODEC = "FFV1"

# MJPG quality (1-100). Only affects MJPG codec; ignored for FFV1/mp4v.
# Default OpenCV is ~95 which causes visible artifacts in dark scenes.
# 98-100 is near-lossless but produces larger files (~3-5× vs default).
RECORDING_QUALITY = 100

# Colors for different dancers (BGR)
DANCER_COLORS = [
    (0, 255, 0),      # Green
    (255, 100, 0),    # Blue
    (0, 100, 255),    # Orange
    (255, 255, 0),    # Cyan
    (255, 0, 255),    # Magenta
    (0, 255, 255),    # Yellow
]
