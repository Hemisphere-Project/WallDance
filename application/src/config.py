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
PERSON_HEIGHT_PX = 150              # Expected person height in pixels (20-800)
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
                                    # All distance thresholds auto-derive from
                                    # PERSON_HEIGHT_PX via configurable ratios below.
TRACKER_DORMANT_MAX_AGE = 150       # Max frames to remember a lost track for re-ID
                                    # (~10 sec at 15 FPS).  When a track expires
                                    # from active tracking (max_age), it moves to
                                    # a dormant pool.  If a new detection appears
                                    # near the dormant position with matching
                                    # skeleton shape, the old ID is resurrected.
TRACKER_VELOCITY_WEIGHT = 0.6       # Trust in velocity prediction (0-1)
TRACKER_PROCESS_NOISE = 2.5         # Kalman Q - velocity adaptation
TRACKER_MEASUREMENT_NOISE = 2.0     # Kalman R - smoothing

# --- Robust tracking (Phases 2-4) ---
# Match gate ratios (scale factors applied to PERSON_HEIGHT_PX)
TRACKER_MATCH_GATE_RATIO = 0.95        # Match gate as fraction of person_height
TRACKER_NEW_TRACK_GATE_RATIO = 0.4     # New-track creation gate
TRACKER_DUPLICATE_GATE_RATIO = 0.2     # Duplicate suppression gate

# Pairwise separation memory — discourages ID swaps between bodies
# that have historically been far apart (known-separate bodies),
# while being lenient with always-close bodies (shadow artifacts).
TRACKER_SEPARATION_MEMORY_FRAMES = 30  # Rolling window (frames)
TRACKER_SEPARATION_PENALTY_WEIGHT = 0.3 # Cost penalty weight (0-1)

# Velocity prediction influence — easy-tweak knob
TRACKER_VELOCITY_PREDICTION_INFLUENCE = 0.5  # 0 = trust raw position
                                              # 1 = trust Kalman prediction
                                              # Lower for unpredictable movement

# Anti-merge constraints — reject detections that are suspiciously
# large for an established track (likely two people merged into one).
TRACKER_ESTABLISHED_FRAMES = 15        # Hits before a track is "established"
TRACKER_MERGE_SIZE_RATIO = 2.0         # Reject if det_area > track_avg × this

# Occlusion handling — keeps tracks alive when hidden behind
# another tracked body instead of ageing them to death.
TRACKER_OCCLUSION_DISTANCE_RATIO = 1.0  # Track is "occluded" if its predicted
                                         # position is within height × this of
                                         # a matched track's position.
TRACKER_OCCLUSION_AGE_FACTOR = 0.1      # Aging rate while occluded (0.1 = 10×
                                         # slower, 0 = freeze completely)
TRACKER_DORMANT_VELOCITY_DECAY = 0.95   # Per-frame velocity decay for dormant
                                         # position projection (< 1.0 = slow down)

# Shadow suppression — filters ghost tracks caused by person shadows
# being detected as separate people.  Two-layer approach:
#   1. Pre-tracker: low-quality detections near a high-quality one are
#      suppressed before the tracker ever sees them.
#   2. Tracker: tracks that consistently shadow another track
#      (correlated velocity + proximity) are auto-killed.
SHADOW_QUALITY_MIN_KEYPOINTS = 8        # Detections with fewer valid keypoints
                                         # than this are considered "low quality"
SHADOW_QUALITY_MIN_CONFIDENCE = 0.50    # Mean confidence below this = low quality
SHADOW_PROXIMITY_RATIO = 1.5            # Suppression radius = person_height * this
SHADOW_TRACK_VELOCITY_CORR = 0.80       # Velocity cosine similarity threshold
                                         # for shadow-track detection (0-1)
SHADOW_TRACK_FRAMES = 12                # Consecutive shadow-correlated frames
                                         # before a track is killed

# Production refinements — identity lock, close-dancing resilience
# Once a track is established (hits >= TRACKER_ESTABLISHED_FRAMES), it
# gets special treatment to preserve identity during close dancing.
TRACKER_ESTABLISHED_MAX_AGE_MULT = 3.0  # Established tracks survive this ×
                                         # longer without matches vs new tracks
TRACKER_CLOSE_PROXIMITY_RATIO = 0.6     # Two tracks are "close" when distance
                                         # < person_height × this.  Triggers
                                         # skeleton-dominant matching.
TRACKER_CLOSE_POS_WEIGHT = 0.10         # Position weight when tracks are close
TRACKER_CLOSE_KPT_WEIGHT = 0.70         # Keypoint-shape weight when close
TRACKER_CLOSE_SIZE_WEIGHT = 0.20        # Bbox-size weight when close
TRACKER_ESTABLISHED_SEP_BOOST = 2.0     # Separation penalty multiplier for
                                         # pairs of established tracks

# Edge-aware exit / resurrection — dancers enter/exit from frame edges.
# When a track disappears in the CENTER of the frame (not near an edge)
# it almost certainly was occluded, not truly gone.  This makes the
# dormant resurrection gate much more generous for center-disappeared
# tracks so we recover the original ID instead of minting a new one.
TRACKER_EDGE_ZONE_RATIO = 0.12          # Left/right edge zone as fraction of
                                         # frame width.  A track whose last
                                         # known x is within this zone of
                                         # either edge is considered to have
                                         # "exited from an edge" (really left).
TRACKER_EDGE_EXIT_AGE_MULT = 0.5         # Edge-exited tracks die at max_age × this.
                                         # < 1.0 = they vanish faster (they left
                                         # the scene, no need to linger).
TRACKER_CENTER_NEW_TRACK_GATE_MULT = 1.5 # New-track creation in the CENTER zone
                                         # requires new_track_min_distance × this.
                                         # > 1.0 = harder to mint new IDs away
                                         # from edges (prevents ghost splits).
TRACKER_CENTER_EXIT_RESURRECT_BOOST = 2.0  # Gate multiplier for dormant
                                            # snapshots that disappeared in
                                            # the center.  Higher = easier
                                            # to match → prefer re-ID.

# Centroid output smoothing (for OSC / TouchDesigner)
# EMA (exponential moving average) on the unscaled centroid for
# jitter-free generative video input.  Does NOT affect tracking.
CENTROID_OUTPUT_SMOOTHING = 0.5         # EMA alpha (0 = max smooth, 1 = raw)
                                         # 0.3-0.5 is good for generative video

# =============================================================================
# TRACKING EVENT LOG (Phase 0 — diagnostics)
# =============================================================================
TRACKER_EVENT_LOG_ENABLED = True        # Write structured JSONL event log
TRACKER_EVENT_LOG_FILE = "tracking_events.jsonl"  # Output file (in working dir)
TRACKER_EVENT_LOG_MAX_ENTRIES = 3000    # Rolling in-memory buffer size
TRACKER_EVENT_LOG_FLUSH_INTERVAL = 2.0  # Seconds between auto-flushes

# =============================================================================
# PHASE 1 — HARDENED ASSOCIATION
# =============================================================================
TRACKER_MAHALANOBIS_GATE = 9.21         # Chi² gate (df=2, 99% confidence).
                                         # Rejects detection↔track pairs where
                                         # the detection is statistically too far
                                         # from the track's Kalman-predicted pos.
                                         # Prevents "teleport" assignments.
                                         # Set to 0 to disable.
TRACKER_MAHALANOBIS_GATE_NOISE = 700.0   # Measurement noise used ONLY for the
                                         # Mahalanobis gate covariance S.
                                         # The Kalman R (MEASUREMENT_NOISE=2.0)
                                         # is tuned for smoothing — it collapses
                                         # the innovation cov to ~4px², gating
                                         # anything >6px away.  For gating we
                                         # need to tolerate normal YOLO jitter
                                         # (10-50px), so we inflate R_gate.
                                         # With 700: ~80px passes, ~110px gated,
                                         # 294px teleport still firmly blocked.
TRACKER_CASCADED_MATCHING = True         # Established tracks match first (pass 1),
                                         # tentative tracks match remaining
                                         # detections (pass 2).  Prevents newly-
                                         # spawned tracks from stealing detections
                                         # that belong to established dancers.
TRACKER_CASCADE_OCCLUSION_SWAP = True    # Post-cascade swap: when a detection
                                         # merger occurs (n_det < n_tracks) and
                                         # an exiting established track claims a
                                         # detection that a nearby tentative track
                                         # should have, swap the assignment so the
                                         # tentative track survives.
TRACKER_CASCADE_SUPPRESSION_FRAMES = 5   # After CASCADE_OCCLUSION_SWAP fires for
                                         # an established track, suppress it from
                                         # Pass 1 for this many frames so the
                                         # tentative track keeps priority.
TRACKER_MERGE_DIRECTION_SWAP = True      # Post-cascade swap: when two tracks
                                         # emerge from a merge/occlusion zone
                                         # on the wrong sides (velocity direction
                                         # reversed relative to pre-merge history),
                                         # swap them back.
TRACKER_MERGE_SWAP_COOLDOWN_FRAMES = 30 # After MERGE_DIRECTION_SWAP fires for a
                                         # pair of tracks, suppress it for this
                                         # many frames.  Prevents oscillation
                                         # when two crossing dancers keep
                                         # triggering swap ↔ re-swap cycles.
TRACKER_TWO_OPT_SWAP = True              # Post-assignment 2-opt swap detector.
                                         # For each pair of nearby matched tracks,
                                         # check if swapping their detections
                                         # reduces total cost.  Catches wrong
                                         # assignments that heuristic swaps miss.
TRACKER_TWO_OPT_MIN_GAIN = 0.10          # Minimum relative cost reduction to
                                         # accept a 2-opt swap (fraction of
                                         # original cost sum).  Prevents noisy
                                         # micro-swaps.
TRACKER_CLOSE_ACCEPT_RATIO = 0.20        # Unconditional match acceptance: if
                                         # raw centroid distance < person_height
                                         # × this ratio, accept the Hungarian
                                         # assignment regardless of blended cost.
                                         # Prevents false rejections when the
                                         # track is physically right on top of
                                         # the detection but cost is inflated by
                                         # crowded-zone multipliers / penalties.

# =============================================================================
# PHASE 2 — TEMPORAL POSE SIGNATURE
# =============================================================================
TRACKER_POSE_HISTORY_DEPTH = 15          # Frames of skeleton history to keep
                                         # per track.  Used for trajectory-
                                         # based matching in crowded zones.
TRACKER_TRAJECTORY_WEIGHT = 0.30         # Weight of trajectory similarity in
                                         # the crowded-zone cost blend.  Higher
                                         # = more influence from pose history.
TRACKER_IOU_WEIGHT = 0.10                # IoU cost weight in normal matching.
                                         # Predicted bbox vs detection bbox.
TRACKER_CLOSE_IOU_WEIGHT = 0.05          # IoU cost weight in crowded zones.
                                         # Lower because skeleton shape is more
                                         # discriminative when dancers overlap.

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

# =============================================================================
# BACKGROUND SUBTRACTION
# =============================================================================
BG_SUBTRACT_ENABLED = False         # Enable static background subtraction
BG_SUBTRACT_SENSITIVITY = 30       # Threshold 0-255 (lower = more aggressive removal)
                                    # 20-40 works well for most scenes

# =============================================================================
# MOTION BRIDGE (Phase 3) — MOG2 foreground blobs for YOLO gap bridging
# =============================================================================
# Bridges lost tracks using MOG2 foreground blobs when YOLO drops detection.
# Designed for fixed-camera IR static background setups.
MOTION_BRIDGE_ENABLED = True
MOTION_BRIDGE_MAX_FRAMES = 80       # Max consecutive blob-only frames per track
MOTION_BRIDGE_GATE_RATIO = 0.5      # Blob must be within person_height × this
MOTION_BRIDGE_MOG2_HISTORY = 500    # MOG2 background model history (frames)
MOTION_BRIDGE_MOG2_VAR_THRESHOLD = 40  # Pixel deviation for foreground (raise for noisy BG)
MOTION_BRIDGE_MOG2_LEARN_RATE = 0.001  # Very slow → dancers stay foreground
MOTION_BRIDGE_MOG2_SCALE = 0.75    # Downscale factor for MOG2 (0.25-1.0, runs behind YOLO)
MOTION_BRIDGE_MIN_AREA = 100        # Min blob area in px² (filter noise)
# Progressive Kalman noise inflation: (bridge_frame_threshold, R_multiplier)
MOTION_BRIDGE_NOISE_STAGES = [(10, 2.0), (30, 4.0), (80, 8.0)]

# 15 perceptually distinct colors for dancer IDs (BGR).
# Deterministic by track_id: color = DANCER_COLORS[(id-1) % 15].
DANCER_COLORS = [
    (0, 255, 0),       #  1  Green
    (255, 100, 0),     #  2  Blue
    (0, 100, 255),     #  3  Orange
    (255, 255, 0),     #  4  Cyan
    (255, 0, 255),     #  5  Magenta
    (0, 255, 255),     #  6  Yellow
    (255, 180, 100),   #  7  Light blue
    (80, 200, 255),    #  8  Gold
    (200, 110, 255),   #  9  Pink
    (100, 255, 170),   # 10  Mint
    (50, 50, 255),     # 11  Red
    (255, 220, 180),   # 12  Ice blue
    (60, 180, 75),     # 13  Forest green
    (190, 130, 60),    # 14  Teal
    (130, 80, 230),    # 15  Salmon
]
