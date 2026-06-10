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
from enum import Enum
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
YOLO_MODEL = "yolo11m-pose.pt"      # Options: yolo11n/s/m/l/x-pose.pt
                                    # n=fastest, x=most accurate
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
# P3 Stage 3b — source-weighted measurement.  A motion-blob measurement (a
# synthetic detection, or a bridge) localises the dancer less precisely than a
# YOLO skeleton, so its Kalman update uses inflated R (less trust): YOLO anchors,
# motion relays/reinforces without yanking the track.
MOTION_MEASUREMENT_NOISE_MULT = 4.0

# --- Robust tracking (Phases 2-4) ---
# Match gate ratios (scale factors applied to PERSON_HEIGHT_PX)
TRACKER_MATCH_GATE_RATIO = 0.95        # Match gate as fraction of person_height
TRACKER_NEW_TRACK_GATE_RATIO = 0.55    # New-track creation gate
TRACKER_DUPLICATE_GATE_RATIO = 0.25    # Duplicate suppression gate
TRACKER_GHOST_MIN_AGE = 100            # Min frames before ghost check applies
TRACKER_GHOST_MAX_HIT_RATE = 0.05     # Tracks below 5% hit rate → ghost

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

# Duplicate-track merge — when two *established* tracks consistently
# occupy the same position they are almost certainly the same dancer
# tracked twice (e.g. D1/D7 in session 20260403).  The younger/lower-
# hit track is absorbed into the older one.
TRACKER_DUPLICATE_MERGE_PROXIMITY = 0.3  # Centroids within person_height × this
TRACKER_DUPLICATE_MERGE_FRAMES = 8       # Consecutive close frames to trigger merge

# Production refinements — identity lock, close-dancing resilience
# Once a track is established (hits >= TRACKER_ESTABLISHED_FRAMES), it
# gets special treatment to preserve identity during close dancing.
TRACKER_ESTABLISHED_MAX_AGE_MULT = 3.0  # Established tracks survive this ×
                                         # longer without matches vs new tracks
TRACKER_CLOSE_PROXIMITY_RATIO = 0.35    # Two tracks are "close" when distance
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
TRACKER_MAHALANOBIS_GATE = 16.27        # Chi² gate (df=2, 99.97% confidence).
                                         # Rejects detection↔track pairs where
                                         # the detection is statistically too far
                                         # from the track's Kalman-predicted pos.
                                         # Prevents "teleport" assignments.
                                         # Relaxed from 9.21 (99%) to avoid gating
                                         # correct matches when Kalman velocity is
                                         # amplified during track convergence.
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
TRACKER_MAX_DISPLACEMENT_RATIO = 0.5     # Max displacement (as fraction of
                                         # distance_threshold) from last measured
                                         # position for recently-matched established
                                         # tracks.  Rejects cost-matrix entries
                                         # where skeleton matching masks a bad
                                         # centroid jump.  With dist_thresh=76px
                                         # → cap ≈ 38px (p99 of good matches ≈ 18).
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
# WEB MONITOR (smartphone preview + focus / lighting assist)
# =============================================================================
# Streams the downscaled preview over MJPEG to a phone on the same LAN so the
# camera can be focused and the IR lighting judged without standing at the
# laptop.  See docs/ROADMAP.md (P0) and src/web_monitor.py.
# Open http://<laptop-ip>:<port>/ on a phone.  Read-only; never touches camera
# or tracker state.  Frames are only pushed while the preview is enabled.
WEB_MONITOR_ENABLED = True          # Start the MJPEG monitor server on launch
WEB_MONITOR_PORT = 8080             # HTTP port
WEB_MONITOR_HOST = "0.0.0.0"        # Bind address (0.0.0.0 = all interfaces)
WEB_MONITOR_JPEG_QUALITY = 70       # MJPEG quality (1-100); lower = less bandwidth
WEB_MONITOR_MAX_FPS = 15            # Cap stream frame rate (phone-friendly)

# =============================================================================
# GO-LIVE SCENE CALIBRATION (one explicit, logged calibration — P2)
# =============================================================================
# A dedicated "Calibrate" button measures the scene over a short window (YOLO
# forced on, works live OR during recording playback) and sets the biggest
# manual knobs automatically, then leaves them fixed.  Explicit, logged, and
# the operator confirms before it is saved to the project — NOT silent
# auto-tuning.  See docs/ROADMAP.md (P2) and src/calibration.py.
AUTOCAL_WINDOW_FRAMES = 90          # Frames to collect before computing (~3s @30fps)
AUTOCAL_MIN_HEIGHT_SAMPLES = 20     # Min YOLO detection-height samples to trust height
AUTOCAL_HEIGHT_PCTL_LO = 5.0        # Low percentile of detection heights → min_ratio
AUTOCAL_HEIGHT_PCTL_HI = 95.0       # High percentile of detection heights → max_ratio
AUTOCAL_MIN_RATIO_BOUNDS = (0.2, 0.8)   # Clamp for the derived person_height_min_ratio
AUTOCAL_MAX_RATIO_BOUNDS = (1.5, 4.0)   # Clamp for the derived person_height_max_ratio
AUTOCAL_NOISE_SCALE = 0.5           # Downscale for the noise/FP estimate (matches MOG2 scale)
AUTOCAL_EXPOSURE_STABLE_CV = 0.03   # Brightness σ/μ below this → exposure considered converged
# varThreshold is chosen *empirically*, not from a pixel-σ formula (MOG2 already
# self-normalises to input noise, so a σ→varThreshold map is dimensionless and
# saturates).  Each candidate runs as its own MOG2 model over the window and is
# scored by the background false-positive rate — the median grid-tile foreground
# fraction, which is robust to the dancer minority (no bbox transform needed).
# The lowest (most sensitive) candidate whose FP rate stays under the target
# wins; if even the highest cannot, the highest is used and flagged "saturated"
# (the scene is too noisy for MOG2 — fix IR / decouple CLAHE per audit #1).
# 8.0 added per the TUNING Phase-C joint search (var=8 woke MOG2 cold-blob
# recovery on slot 4); safe to offer now that the Phase-F frozen-ghost gate landed.
AUTOCAL_VARTHRESH_CANDIDATES = (8.0, 16.0, 24.0, 32.0, 40.0, 56.0, 80.0, 120.0)  # ascending
AUTOCAL_FP_TARGET = 0.005           # Max background median-tile foreground fraction (0.5%)
AUTOCAL_FP_GRID = (8, 5)            # Grid for the robust background-FP estimate

# --- Calib1 scene pass (UX_PLAN.md U3) --------------------------------------
# varThreshold and mog2_scale interact (KNOBS.md finding #2: scale only pays
# off once var wakes the silhouette), so the FP sweep runs jointly over
# var × scale.  Preference order at equal sensitivity: 0.7 first (Phase-C
# winner), then full-res fidelity, then the cheap/conservative 0.5.
AUTOCAL_SCALE_CANDIDATES = (0.5, 0.7, 1.0)
AUTOCAL_SCALE_PREFERENCE = (0.7, 1.0, 0.5)
AUTOCAL_SWEEP_STRIDE = 2            # Score the var×scale models every Nth frame (CPU cost)
# Exposure servo: drive IDS exposure first (up to the motion-blur budget),
# then analog gain (Starvis2 = low read noise).  All provisional until the
# annotated-footage loop re-fits them (UX_PLAN §6).
AUTOCAL_BLUR_BUDGET_MS = 25.0       # Max exposure: motion-blur cap (NOT the FPS cap)
AUTOCAL_SERVO_TARGET_BRIGHTNESS = 70.0  # Raw-scene mean luma target
AUTOCAL_SERVO_TOLERANCE = 12.0      # Acceptable band around the target
AUTOCAL_SERVO_CLIP_MAX_PCT = 0.5    # Back off when > this % of pixels >= 250
AUTOCAL_SERVO_GAIN_MAX_DB = 36.0    # Analog-gain ceiling for the servo
AUTOCAL_SERVO_SETTLE_FRAMES = 6     # Frames to let the sensor apply each command
AUTOCAL_SERVO_MAX_STEPS = 30        # Hard stop for the servo loop
# Gamma seed: chosen so the measured raw median maps near mid-gray; CLAHE is
# reduced on noisy scenes (CLAHE amplifies noise — ROADMAP bug #1 lesson).
AUTOCAL_GAMMA_TARGET = 110.0
AUTOCAL_GAMMA_BOUNDS = (0.8, 2.2)
AUTOCAL_CLAHE_DEFAULT = 2.5
AUTOCAL_CLAHE_NOISY = 1.5
AUTOCAL_CLAHE_NOISE_SIGMA = 4.0     # Noise σ above which the reduced clip is used

# --- Calib2 subject pass (UX_PLAN.md U4) -------------------------------------
# Dancer calibration: accumulative evidence pool across runs/situations
# (live or playback).  All numeric rules provisional until the
# annotated-footage loop re-fits them (UX_PLAN §6).
AUTOCAL2_WINDOW_FRAMES = 240        # Collection window per run (~10 s @ 24 fps)
AUTOCAL2_MIN_SAMPLES = 40           # Min pooled height samples to trust the pool
AUTOCAL2_NET_HEIGHT_TARGET = 110.0  # Dancer height in YOLO net-input px (pose needs ~>100)
AUTOCAL2_CONF_MARGIN = 0.05         # Sensitivity seed: p05 keypoint-conf minus this
AUTOCAL2_CONF_BOUNDS = (0.15, 0.50) # Clamp for the seeded confidence
AUTOCAL2_BLUR_FRACTION = 0.10       # Allowed motion blur as a fraction of person height
AUTOCAL2_SPEED_PCTL = 95.0          # Speed percentile that sets the blur budget
AUTOCAL2_BLUR_BOUNDS_MS = (5.0, 30.0)  # Clamp for the refined blur budget
AUTOCAL2_STALE_TOL = 0.10           # ROI long-side relative change → run flagged stale
AUTOCAL2_FRAME_SAMPLES = 12         # Raw frames saved per run (future gamma/CLAHE sweep)

# --- Auto exclusion mask (P1.4) -------------------------------------------
# During calibration, grid cells that show persistent MOG2 motion but ~never a
# confirmed skeleton are scenery / ghost sources (trees, balcony, wall paint,
# shadows).  They get masked, and detections landing there are rejected at the
# source — replacing most of what the per-frame crossval motion filter does,
# safely, because the scene is fixed per show.  See docs/ROADMAP.md P1.4.
AUTOCAL_EXCL_GRID = (16, 10)        # Exclusion grid resolution (cols, rows) over the frame
AUTOCAL_EXCL_MOTION_FRAC = 0.10     # Tile counts as "moving" this frame if ≥10% of it is FG
AUTOCAL_EXCL_MOTION_FREQ = 0.30     # Cell must move in ≥30% of frames to be a ghost candidate
AUTOCAL_EXCL_SKEL_FREQ = 0.02       # ...and hold a skeleton in ≤2% of frames
AUTOCAL_EXCL_MIN_FRAMES = 30        # Need at least this many observed frames to build a mask

# =============================================================================
# STARTUP
# =============================================================================
# On launch, show a project picker (ordered by last-save, last project
# highlighted, Enter to launch) instead of silently auto-loading the last
# project — gives a deliberate, fast crash-recovery path.  See docs/ROADMAP.md §7B.
# Escape hatches that skip the picker and auto-load the last project: set this
# False, or set the env var WALLDANCE_AUTOLAUNCH_LAST=1 (for unattended/kiosk
# boot), or pass --project / a config path on the CLI.
PROJECT_PICKER_ON_START = True

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
MOTION_BRIDGE_MOG2_SCALE = 0.5     # Downscale factor for MOG2 (0.25-1.0, runs behind YOLO)
MOTION_BRIDGE_MIN_AREA = 100        # Min blob area in px² (filter noise)
MOTION_BRIDGE_MIN_AREA_LOWLIGHT_MULT = 1.8  # Raise min blob area in low light
MOTION_BRIDGE_GATE_GROWTH_PER_MISS = 0.18   # Expand bridge gate as misses grow
MOTION_BRIDGE_GATE_ESTABLISHED_MULT = 1.35  # Established tracks get a wider blob gate
MOTION_BRIDGE_SENSITIVITY = 0.55           # 0.0 = conservative bridge,
                                            # 1.0 = very permissive bridge.
MOTION_BRIDGE_INCLUDE_SHADOWS = True        # Include MOG2 shadow-class pixels
                                             # (127) in bridge blobs — essential
                                             # for IR setups where dancer body
                                             # appears darker than background.
MOTION_BRIDGE_LOCAL_MIN_FG_RATIO = 0.02     # Track-local fallback requires this
                                             # fraction of clean fg inside the
                                             # predicted query box.
MOTION_BRIDGE_LOCAL_EXPAND_PER_MISS = 0.12  # Grow fallback query box as miss
                                             # streak increases.
MOTION_BRIDGE_LOCAL_MAX_EXPANSION = 2.0     # Cap fallback query-box scaling.
MOTION_BRIDGE_LOCAL_MIN_BLOB_AREA = 50      # Min blob area (px²) for local/frame-diff
                                             # bridge tiers.  Blobs smaller than this
                                             # are noise — accepting them lets the
                                             # Kalman velocity drift unchecked.
MOTION_BRIDGE_MAX_PRESENCE_FRAMES = 15      # Max consecutive presence-only bridge frames
                                             # (no coherent blob).  After this the track
                                             # stops being bridged and ages normally.
MOTION_BRIDGE_VELOCITY_FRICTION = 0.5       # Per-frame velocity damping during bridge.
                                             # Without this, Kalman velocity runs away
                                             # because bridge resets time_since_update
                                             # and the normal miss-friction never fires.
MOTION_BRIDGE_FRAME_DIFF_THRESHOLD = 6      # Abs pixel-intensity change to count
                                             # as motion in frame-diff fallback.
                                             # Low because frames are downscaled
                                             # and blurred before comparison.
MOTION_BRIDGE_FRAME_DIFF_MIN_RATIO = 0.02   # Min fraction of changed pixels in
                                             # the query box for frame-diff bridge.
# Progressive Kalman noise inflation: (bridge_frame_threshold, R_multiplier)
MOTION_BRIDGE_NOISE_STAGES = [(10, 1.5), (30, 2.5), (80, 4.0)]
MOTION_BRIDGE_WARMUP_INCREMENT = 0.4    # Warmup score added per bridge-blob match.
                                         # Lower than YOLO (+1.0) so a motion-only
                                         # track needs ~40 consistent blob frames
                                         # (~2s @ 20fps) to reach output threshold.

# =============================================================================
# TRACKING MODE — YOLO-first vs Motion-first detection priority
# =============================================================================
class TrackingMode(Enum):
    YOLO_FIRST = "yolo_first"       # Default: YOLO primary, motion blobs bridge only
    MOTION_FIRST = "motion_first"   # Motion blobs as primary detections alongside YOLO

TRACKING_MODE = TrackingMode.YOLO_FIRST

# Motion-first overrides: when MOTION_FIRST is active, these values
# replace the defaults above for better blob-driven detection.
MOTION_FIRST_MOG2_LEARN_RATE = 0.0003   # Slower → static dancer stays foreground longer
MOTION_FIRST_MIN_HITS = 1               # Confirm motion-seeded tracks immediately
MOTION_FIRST_BRIDGE_MAX_FRAMES = 60     # Max frames without a match before track dies (× 3 for established)
MOTION_FIRST_BLOB_OVERLAP_RATIO = 0.3   # Blob-YOLO overlap gate (× person_height)
MOTION_FIRST_SYNTHETIC_MIN_FRAMES = 3   # Require brief blob persistence before spawning a synthetic detection
MOTION_FIRST_SYNTHETIC_CELL_RATIO = 0.35  # Spatial cell size as person_height ratio for blob persistence
MOTION_FIRST_ASPECT_RANGE = (0.3, 2.0)  # Tighter aspect filter for top-shot views
MOTION_FIRST_INCLUDE_SHADOWS = True     # Include MOG2 shadow-class pixels in
                                         # eager blob spawning — essential for IR
                                         # setups with dark dancer on bright BG.
MOTION_FIRST_WARMUP_FRAMES = 60         # Suppress blobs during MOG2 warmup
MOTION_FIRST_STATIC_BLOB_FRAMES = 90    # Suppress blobs static for this many frames

# =============================================================================
# CROSS-VALIDATION: scored detection gate (P3 Stage 3a)
# =============================================================================
# Reject background false positives so YOLO confidence can stay LOW (catching
# awkward poses).  A detection is kept if it has a strong skeleton OR shows
# recent FRAME-DIFF motion (θ_m) OR overlaps a live track; else rejected.
# Frame-diff — not MOG2 foreground — is the motion signal: static textured
# background + slow lighting drift read as MOG2 foreground but produce no
# frame-to-frame change, so frame-diff is the ghost killer MOG2 cannot be.
# (The former 7-step tree's warmup/sticky/reacquire/min-fg constants were
# retired in Stage 3d.)
MOTION_CROSSVAL_ENABLED = True           # Master toggle for cross-validation
MOTION_CROSSVAL_EMA_ALPHA = 0.65         # Temporal smoothing for per-region
                                          # motion score. Higher = trust current
                                          # frame more, lower = more hysteresis.
MOTION_CROSSVAL_CELL_RATIO = 0.5         # Spatial memory cell size as a fraction
                                          # of person_height for hysteresis.
MOTION_CROSSVAL_EXISTING_TRACK_BYPASS = True  # If a detection overlaps an
                                              # already-tracked person, skip
                                              # motion check (keep matched).
MOTION_CROSSVAL_BYPASS_MAX_AGE = 5       # Recently-matched tracks bypass motion
                                          # check for up to this many miss frames.
                                          # Higher = harder to lose a tracked dancer
                                          # during brief low-motion moments.
MOTION_CROSSVAL_BYPASS_MIN_WARMUP = 2.0  # Min warmup score for bypass eligibility.
                                          # Prevents fresh ghost tracks (score 1.0)
                                          # from bypassing crossval. A track needs
                                          # at least 1 successful re-match (score 2.0)
                                          # before it can shield nearby detections.
MOTION_LOWLIGHT_LUMA_THRESHOLD = 55      # Below this, assume sensor noise dominates
MOTION_LOWLIGHT_MEDIAN_KERNEL = 5        # Extra median filter for noisy low-light frames
MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT = 1.2  # Require more motion in low light
                                          # (reduced from 1.6 — cleaned mask +
                                          # coherence + adaptive varThreshold
                                          # already handle noise at source)
MOTION_LOWLIGHT_VAR_THRESHOLD_MULT = 2.0 # Multiply MOG2 varThreshold in low light
                                         # Higher = fewer noise pixels classified as
                                         # foreground.  2.0 → varThreshold 80 when dim.
MOTION_CROSSVAL_MIN_COHERENCE = 0.35     # Min fraction of foreground pixels that must
                                         # belong to the largest connected component
                                         # inside a query bbox.  Below this, the fg is
                                         # scattered noise, not a coherent motion blob.
                                         # 0.0 = disable coherence check.

# "Very confident" skeleton pass — detections with a strong, well-resolved
# skeleton are accepted without MOG2 confirmation.  This handles the case
# where a dancer stands still (no MOG2 motion) but is clearly visible.
MOTION_CROSSVAL_CONFIDENT_MIN_KPTS = 8   # Min valid keypoints for auto-pass.
MOTION_CROSSVAL_CONFIDENT_MIN_CONF = 0.45  # Min mean conf for auto-pass.

# P3 Stage 3a — scored detection gate.  A detection is kept if it has a strong
# skeleton (the CONFIDENT thresholds above) OR shows recent FRAME-DIFF motion
# OR overlaps a live track.  Frame-diff (not MOG2 foreground) is the motion
# signal because static textured background + slow lighting drift register as
# MOG2 foreground but produce NO frame-to-frame change — so this is the ghost
# killer.  θ_m below is the minimum frame-diff foreground fraction in the box.
MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO = 0.02  # θ_m — tuned on residence1-solo

# =============================================================================
# TRACK WARMUP SCORING — delay output, not tracking
# =============================================================================
# New tracks accumulate a warmup score over consecutive matches.
# They are only output (to OSC / overlay) once score reaches the
# threshold.  This suppresses flickering background ghosts without
# slowing down the tracker's internal matching.
TRACK_WARMUP_THRESHOLD = 15               # Consecutive-match score to confirm.
                                          # At +1.0/match and -0.8/miss, a
                                          # reliably-detected dancer confirms in
                                          # ~20 frames (1s at 20fps).  A ghost
                                          # that matches intermittently never
                                          # reaches this threshold.
TRACK_WARMUP_DECAY = 0.8                 # Score decay per missed frame.
                                          # Higher = misses hurt more, ghosts
                                          # drained faster.  Must be < 1.0.

# Report-gate against "frozen-on-the-wall" ghost tracks (TUNING Phase F).
# A track abandoned by its dancer can linger if recurring cold-blob detections
# (aggressive low-varThreshold / high mog2_scale) keep matching it at a fixed
# wall feature/shadow.  Measured: residence1-solo slot4 @ var8/scale0.7 — an
# established track lost the dancer, froze at (681,995), and was reported for 15
# frames as a 2nd "ghost dancer" while the real dancer (a separate track) swung
# away.
#
# Discriminator: such a ghost is BOTH (a) skeleton-stale — no real pose
# (≥1 keypoint over KEYPOINT_CONFIDENCE) for several frames, only zero-confidence
# cold-blob/bridge updates — AND (b) effectively stationary.  A real dancer in a
# YOLO gap is *moving* (bridge/blobs follow them), and a still dancer keeps
# getting skeletons — so the AND of the two is specific to abandoned ghosts and
# preserves both legitimate bridging and motion-only moving dancers.
TRACKER_REPORT_REQUIRES_SKELETON = True   # master switch for the gate
TRACKER_GHOST_SKELETON_AGE = 3            # frames w/o a real skeleton before the
                                          # frozen-check applies (small is safe:
                                          # a gap-bridged dancer is moving, so the
                                          # speed test below spares it)
TRACKER_GHOST_FROZEN_SPEED_RATIO = 0.03   # × person_height_px = px/frame below
                                          # which a skeleton-stale track counts
                                          # as "frozen" → ghost, not reported

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
