"""
Configuration for WallDance 1080p
Optimized for: 50m wide scene, 6 dancers, low-light outdoor conditions

All parameters are tunable - adjust based on your specific setup.
"""

# =============================================================================
# CAMERA & INPUT
# =============================================================================
CAMERA_INDEX = 0                    # Camera device index (0 = default webcam/capture card)
CAMERA_WIDTH = 1920                 # Input resolution width
CAMERA_HEIGHT = 1080                # Input resolution height
CAMERA_FPS = 30                     # Target camera FPS

# =============================================================================
# IMAGE PROCESSING - UPSCALING
# =============================================================================
# At 50m scene width on 1080p, a 1.7m person is only ~65 pixels tall
# Upscaling dramatically improves detection of small figures
UPSCALE_FACTOR = 2.0                # 1.0 = native, 2.0 = 4K equivalent, 3.0 = 6K
                                    # RTX 3090 can handle 2.0-3.0 at 15-25 FPS

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
                                    # n=fastest, x=most accurate
YOLO_CONFIDENCE = 0.25              # Detection confidence threshold (0.1-0.9)
YOLO_IOU_THRESHOLD = 0.45           # NMS IoU threshold
MAX_PERSONS = 6                     # Maximum dancers to track

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
TRACKER_MAX_AGE = 20                # Frames to keep lost track
TRACKER_MIN_HITS = 2                # Hits to confirm track
TRACKER_DISTANCE_THRESHOLD = 300    # Max match distance (pixels, in upscaled space)
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
DISPLAY_SCALE = 0.75                # Scale display for large resolutions
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
