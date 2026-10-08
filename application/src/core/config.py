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
# Go up from src/core/ to application/, then up to workspace root, then into models/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
YOLO_MODEL = "yolo11x-pose.pt"      # Options: yolo11n/s/m/l/x-pose.pt
                                    # Phase 2b corpus benchmark: 11x >= 11m on
                                    # 9/12 scenes (+0.45/+0.51 on small-far/dark,
                                    # 11m's wins are noise); the FPS budget +
                                    # per-rig fps table keep imgsz honest
YOLO_CONFIDENCE = 0.15              # Detection confidence threshold (0.1-0.9).
                                    # D33 (2026-10-08): the validated D27 value is the
                                    # default of a new project (was 0.25); the
                                    # sensitivity dial anchors on it (dial 50 = 0.15).
                                    # See DETECTION_DEFAULTS below.
YOLO_IOU_THRESHOLD = 0.45           # NMS IoU threshold
YOLO_IMGSZ = 1280                   # YOLO input size (640, 800, 960, 1280, 1536, 1920, 2560)
                                    # D33: x@1280 is the default (was 800).  The
                                    # 2026-10-07 far-wall replays: YOLO recall at the
                                    # wall 0-14 % at 800 vs 31-67 % at 1280 (the dancer
                                    # is ~123 px in YOLO's input at 1280, ~77 px at 800).
                                    # The engine extra/build_engines builds FIRST.
                                    # IMPORTANT: Should be ≤ camera resolution for best results
                                    # - 640-960: Fast, good for close-up / webcam
                                    # - 1280: Balanced, good for 1080p cameras at medium distance
                                    # - 1920-2560: Only useful with 4K cameras for distant subjects
                                    # Values > camera resolution cause padding and reduced accuracy
MAX_PERSONS = 6                     # Maximum dancers REPORTED (OSC/overlay).
                                    # Enforced at the tracker report boundary
                                    # (bug 12c): top-K by hits, older id wins
                                    # ties. Internal tracks are not capped, so
                                    # identity survives a transient ghost flood.
                                    # Per-project config key: `max_persons`.

# TensorRT optimization
USE_TENSORRT = True                 # If True, export and use TensorRT .engine files
                                    # Provides ~2x inference speedup
                                    # Engine is GPU-specific (rebuilt per GPU)
                                    # First run will take 2-5 minutes to export

# GPU Processing Path (see SPECIFICATIONS.md Section 14)
USE_GPU_PATH = True                 # Enable GPU frame buffer and GPU-accelerated processing
                                    # Requires OpenCV with CUDA support
                                    # Falls back to CPU if CUDA not available

# OpenCV worker threads (audit 2026-10 PERF-1). `import ultralytics` calls
# cv2.setNumThreads(0) (a PyTorch-DataLoader workaround), which left the whole
# app -- including the CPU motion feed, the prod critical path -- single-
# threaded. Every module that imports ultralytics (core/pipeline.py,
# core/model_manager.py) restores this count right after the import. Motion
# outputs are bit-identical at any count (tests/test_cv2_threads_bit_identity.py).
# Capped at 4: the motion worker shares the CPU with the main thread (YOLO
# launches, tracker, GUI), IDS acquisition and the recording encoder.
CV2_NUM_THREADS = 4

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

# Takeover duplicate merge (ROADMAP §4.2 Phase 2 ②).  Corpus-measured: the
# duplicate pressure on duo/textured scenes is NOT two tracks sitting on one
# spot (the case above) — it is a track that loses its dancer to another
# track at a "takeover" moment, then wanders on bridge/ghost feeds.  It keeps
# moving, so the frozen-ghost report gate (skeleton-stale + slow) never
# catches it.  Discriminator: a real pair of dancers is seen simultaneously
# by YOLO early and often (both tracks skeleton-fed the same frame: 22–96 %
# of frames on real pairs vs ~0 % on zombie pairs across the 12-scenario
# corpus), so a pair that has essentially never co-fed, currently close, fed
# one-sided, is one dancer with two tracks.
TRACKER_DUP_TAKEOVER_PROXIMITY_RATIO = 0.6  # Centroids within person_height × this
TRACKER_DUP_TAKEOVER_HITS = 4            # Qualifying frames within the window …
TRACKER_DUP_TAKEOVER_WINDOW = 8          # … of this many pair-coexistence frames
TRACKER_DUP_COFED_VETO = 3               # Pair co-fed frames ≥ this ⇒ two real
                                         # dancers — never takeover-merge them

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

# Output box-SIZE smoothing base (Track X, OSC_CONTRACT §B.2).  Causal EMA alpha
# at output_smoothing_l = 1 (the light de-jitter floor); the slider divides this
# by L, so higher L = smoother box at the cost of causal group-delay latency.
BOX_SIZE_OUTPUT_SMOOTHING = 0.5         # EMA alpha at L=1 (~1 frame group delay)

# Identity-slot output layer (audit 2026-10 CONT-6, core/identity_slots.py).
# OUTPUT-ONLY: N stable OSC ids (1..max_dancers) over the tracker's churning
# track ids, coasting through short losses, One-Euro smoothed per slot.
# Per-project config keys: identity_slots_enabled, max_dancers, stability,
# coast_s, use_ir_belt, osc_send_state (operator knobs, phase 6 Live).
IDENTITY_SLOTS_ENABLED = True           # default for new configs / absent key
IDENTITY_SLOTS_MAX_DANCERS = 2          # the show's dancer cap (D4: capped, not constant)
IDENTITY_SLOTS_STABILITY = 0.5          # 0 = responsive .. 1 = calm (One-Euro presets)
IDENTITY_SLOTS_COAST_S = 2.0            # hold a vanished dancer this long: bridges 355/356
                                        # tracker losses on the 2026-10 replays + field takes
IDENTITY_SLOTS_USE_IR_BELT = True       # use core/belt_detector.py when importable
IDENTITY_SLOTS_STATIC_GUARD = True      # static-ghost guard + yield (a figure that never moves
                                        # cannot take / keep a slot a moving dancer needs);
                                        # PLAN_25M 2026-10-06: neutral on 12 replays, a ghost-
                                        # starved dancer 0.004 -> 0.80 on-dancer
IDENTITY_SLOTS_STATIC_RELEASE_S = 0.0   # > 0: drop a slot static this long even with no newcomer
                                        # (OFF: at 8 s it dropped a still floor dancer)
IDENTITY_SLOTS_FILTER_INPUT = "smoothed"  # One-Euro input: smoothed | raw | raw_skeleton (D18)
IDENTITY_SLOTS_SMART_HOLD = True        # Hold earned by reputation (>= 1 s of skeleton-backed tracking AND
                                        # >= 0.25 h of travel since entry, else 1.5 s) and cut to 0.3 s for a
                                        # dancer leaving across the ROI border; neutral at Hold 2 s, -24 %
                                        # ghost-point frames at Hold 5 s (PLAN_25M §C.11)
# IR-belt static-glint map, learned online (PLAN_25M A6): every N-th frame a global
# belt pass updates a per-cell persistence EMA, live dancers protected; a cell above
# the threshold becomes static and the gated belt queries ignore it.  ~9 s to learn
# a fixed glint (a floor projector, a lamp) at 20 fps.
BELT_STATIC_EVERY_N = 10
BELT_STATIC_ALPHA = 0.05
BELT_STATIC_ON = 0.6
# Clean-plate foreground ("background snapshot", core/foreground.py, 2026-10-07): a median of
# ~2 s of the EMPTY wall (Calibrate captures it; "Capture empty wall" re-takes it), saved in the
# project as plates/plate_<stamp>.npz (config key fg_plate).  Each frame the raw ROI is compared
# with it (4x downscaled, brightness-normalised): ghost veto at slot binding, a foreground hold
# for slots YOLO lost, and the belt backing.  No plate = no foreground (the base behaviour).
FOREGROUND_ENABLED = True
PLATE_CAPTURE_FRAMES = 40               # 2 s at 20 fps
# Light changed since the empty-wall snapshot (Thomas 2026-10-07: "luminosity change is a killer"): the
# raw ROI brightness over the snapshot's (core/foreground.ForegroundDetector.light_ratio), sampled at 1 Hz
# in standby and RUN alike; when its median over LIGHT_CHANGE_S leaves [1/RATIO, RATIO] the operator is
# told once per episode (alert + alerts strip + readiness) to Calibrate on the empty wall again.  The
# night project's light fell ~3x from 21:33: far outside; a 1.5x band ignores small drifts the plate's
# gain normalisation absorbs.
LIGHT_CHANGE_RATIO = 1.5
LIGHT_CHANGE_S = 10.0
LIGHT_CHANGE_MIN_SAMPLES = 5            # 1 Hz samples needed before the median counts (~5 s after start)
# Height guard (D29, 2026-10-07): Calib2 (Dancers) left the operator flow and was the only writer of
# person_height_px.  A stale value makes the size gate (0.3-2.5 x, frames with >= 2 detections) drop
# the real dancers: the night project kept 45 px for bodies of 120-190 px, so a duo at the wall loses
# both boxes whenever both are detected.  The guard measures the height of confident full skeletons in
# the RAW detections (before that gate) and adopts their median only when the gate rejects most of
# them (>= HEIGHT_GUARD_OUTSIDE): a right configured height is never touched, and a minority outside the
# gate (somebody near the camera: a 569 px person on bdx1005-s5) cannot hijack it.  auto_height
# (continuous learning) is the ground-stage option.
HEIGHT_GUARD = True
HEIGHT_GUARD_OUTSIDE = 0.8
HEIGHT_GUARD_MIN_SAMPLES = 100     # in the 10 s window: >= 5 s of one dancer (a 1 s close-up on
                                   # bdx1005-s8 must not re-scale the gates: 20 samples -> 569 px)
HEIGHT_GUARD_MAX_SPREAD = 0.5      # (q3 - q1) / median of the window: a wall show (fixed distance)
                                   # is tight; a floor walk toward the lens (bdx1005-s5/s8, 187-507 px)
                                   # has no single height -> left alone (that is auto_height's case)
# Follow (2026-10-07 laptop pass): on all six night takes the guard first adopted the operator's WALK-IN
# height (285-384 px, close to the camera, the first population seen) and never came back to the wall
# height (~127 px), which sits inside the new gate -- a duo's tracker gates would stay ~2.4x too wide.
# A second, slow sampler sees what the dense one misses: over the last HEIGHT_GUARD_FOLLOW_S, every
# full skeleton at a lower keypoint threshold, weighted so each second counts once (the walk-in close to
# the camera gives ~20 skeletons a second, a dark dancer at the back wall one -- per sample the walk-in
# would win; on s4 the dense rule never fired at the wall at all).  With >= HEIGHT_GUARD_FOLLOW_MIN_SECONDS
# seconds that agree on a height (spread rule above): before the guard owns the height, >= OUTSIDE of them
# outside the gate -> adopted (the first rule, slow); once it owns it (it set it last; a configured height
# it never fired on stays untouched), a median beyond HEIGHT_GUARD_FOLLOW_RATIO either way -> adopted
# (the follow).  A close-up of a few seconds stays a minority of the minute; a bimodal window (walk-in +
# wall) waits until one population dominates; anything else setting the height (config load, the
# Advanced slider) ends the guard's ownership.
HEIGHT_GUARD_FOLLOW_S = 60.0
HEIGHT_GUARD_FOLLOW_MIN_SECONDS = 15
HEIGHT_GUARD_FOLLOW_RATIO = 1.5
HEIGHT_GUARD_FOLLOW_KPT_CONF = 0.3     # head / ankle keypoint confidence of a follow sample: on the dark
                                       # night take s2c the still dancer at the back wall gives NO head + ankle
                                       # >= 0.5 for 140 s, ~1 s in 4 at 0.4, every second at 0.3 (median
                                       # 127-128 px = its box height); the first adoption keeps 0.5
# Empty-wall YOLO check (core/empty_wall.py, D29): Calibrate ends by stepping the enhancement down
# (gamma, then CLAHE) until YOLO finds no person on the empty wall.
EMPTY_WALL_CHECK = True
OSC_SEND_STATE = False                  # opt-in /walldance/dancer/state [id, state, age_s]

# =============================================================================
# TRACKING EVENT LOG (Phase 0 — diagnostics)
# =============================================================================
TRACKER_EVENT_LOG_ENABLED = True        # Write structured JSONL event log
# No working-dir file any more (CONT-10): playback/replay runs log into their
# session dir, live runs into a per-show folder projects/<p>/sessions/<stamp>_live
# (core/tracking_logger.py).  The old ./tracking_events.jsonl appended forever.
TRACKER_EVENT_LOG_FILE = None
TRACKER_EVENT_LOG_MAX_ENTRIES = 3000    # Rolling in-memory buffer size
TRACKER_EVENT_LOG_FLUSH_INTERVAL = 2.0  # Seconds between auto-flushes
TRACKER_EVENT_LOG_SEGMENT_MB = 64       # rotate a session's JSONL past this size
TRACKER_EVENT_LOG_MAX_SEGMENTS = 32     # per session (~2 GB); oldest dropped beyond
TRACKER_EVENT_LOG_KEEP_LIVE = 30        # newest live-show folders kept per project

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
TRACKER_SWAP_CORRECTORS = False          # Master switch for the three post-hoc
                                         # swap correctors (occlusion-cascade,
                                         # merge-direction, two-opt).  Default
                                         # OFF (ROADMAP §3a / §4.2 Phase 2 ⑧):
                                         # the slot-7-fit heuristics false-fire
                                         # on aerial/erratic motion, suppressing
                                         # the REAL track — corpus-measured off
                                         # = mean −0.027, id churn improves on
                                         # every affected scene, hangar-aerial
                                         # golden flips to PASS.  Re-enable per
                                         # scene via the replay/config key
                                         # `tracker_swap_correctors` only for
                                         # shows with sustained two-dancer
                                         # contact (texture-duo regime).
TRACKER_CASCADE_SUPPRESSION_FRAMES = 5   # After CASCADE_OCCLUSION_SWAP fires for
                                         # an established track, suppress it from
                                         # Pass 1 for this many frames so the
                                         # tentative track keeps priority.
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
TRACKER_TWO_OPT_MIN_GAIN = 0.10          # Minimum relative cost reduction to
                                         # accept a 2-opt swap (fraction of
                                         # original cost sum).  Prevents noisy
                                         # micro-swaps.
# Own-height gates (2026-10-07): an established track's re-association gates (displacement gate, match
# threshold, close accept) scale with min(global person height, the track's OWN YOLO box height) -- never
# wider than before, tighter where the global height is too big.  The night project's height guard held
# the walk-in height (427-605 px) while the dancer at the back wall was ~128 px, so a dancer track bridged
# for a few frames re-associated with a false person 2-4.5 body heights away (s2c 38.55 s, s6 28.0 s,
# s4 37.65 s at gamma 1.0).  The track's own size comes from its skeleton, not its box: the median torso
# (mid-shoulders to mid-hips, both >= TRACKER_TORSO_KPT_CONF) over its last TRACKER_OWN_HEIGHT_WINDOW
# real-skeleton frames (10 s) x TRACKER_TORSO_TO_HEIGHT (shoulders ~0.82, hips ~0.53 of the stature; a
# standing box ~ the stature).  Box-based sizes failed both ways: box heights shrink with floor work (white
# duo on-dancer -10), a high percentile of box sides inflates with a climber's reaches (s4 jump kept).
# No confident torso yet (MIN_SAMPLES) -> the global height.  Only when the global height is clearly too big
# for the track (> TRACKER_OWN_HEIGHT_TRIGGER x its own size; the night failures: 2.5-4.8 x) -- a well-
# configured scene keeps its tuned gates (applied always, the duos moved: texture duo on-dancer -4.5, white
# duo coverage -1; at 1.5 x the textured duo still moved: on-dancer -6.9).
TRACKER_OWN_HEIGHT_TRIGGER = 2.0
TRACKER_OWN_HEIGHT_GATES = True
TRACKER_OWN_HEIGHT_WINDOW = 200
TRACKER_OWN_HEIGHT_MIN_SAMPLES = 20
TRACKER_TORSO_KPT_CONF = 0.5
TRACKER_TORSO_TO_HEIGHT = 3.4
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
OSC_SEND_ERROR_ALERT_INTERVAL_S = 10.0  # A failing send (unreachable host, a
                                    # broadcast target, full socket buffer) is
                                    # dropped and the show goes on; console +
                                    # operator alert at most once per interval
                                    # (audit 2026-10 ARCH-6)

# OSC message format (canonical wire contract: docs/OSC_CONTRACT.md).
# id is the FIRST arg of each /dancer message (one flat stream, not per-id addresses):
# /walldance/dancer/centroid    [id, x, y]            (normalized 0-1)
# /walldance/dancer/bbox        [id, x, y, w, h]      (normalized 0-1)
# /walldance/dancer/keypoints   [id, x0,y0,c0, ...]   (17 keypoints, normalized)
# /walldance/dancer/velocity    [id, vx, vy]          (normalized per frame)
# /walldance/count              [n, id0, id1, ...]    (count + the active id set)
# /walldance/meta/latency_ms    [ms]                  (0 at L=1; L/fps*1000 at L>1)
# /walldance/clear              [1]                   (emitted when no dancers tracked)

# =============================================================================
# VISUALIZATION
# =============================================================================
PREVIEW_ENABLED = True              # Push video to GUI (disable to measure FPS impact)
# Render at lower resolution to save GPU/CPU, but keep the on-screen area size.
PREVIEW_RENDER_SCALE = 0.35         # Texture resolution scale (0.3-1.0); lower = faster
                                    # IDS 2688×1528 @ 0.35 → 940×535 (~1.5 MB uint8 transfer)
                                    # IDS 2688×1528 @ 0.50 → 1344×764 (~3.1 MB — too heavy)
PREVIEW_DISPLAY_SCALE = 0.5        # On-screen preview area scale relative to camera
# Preview refresh caps (GPU download + compose + overlays + texture upload).
# Display only: tracking / OSC run on every frame regardless.  The operator's
# "Preview FPS cap" toggle selects the low one (and halves the preview size).
PREVIEW_MAX_FPS = 15.0             # default cap (the 20 fps stream: 3 frames in 4)
PREVIEW_CAPPED_FPS = 10.0          # with the "Preview FPS cap" toggle on
SHOW_SKELETON = True                # Draw skeleton
SHOW_KEYPOINTS = False              # Draw keypoints (off by default, Thomas 2026-10-07)
SHOW_BBOX = True                    # Draw bounding box
SHOW_TRAILS = False                 # Draw motion trails (off by default, Thomas 2026-10-07)
SHOW_ID = True                      # Draw track ID
SHOW_BALL = True                    # Draw the output ball (the emitted OSC centroid)

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

# Remote ops API (services/remote_api.py, docs/REMOTE_OPS.md): the second client
# of the command/event seam, for a dev box driving the prod laptop over the
# tailnet. Binds LOOPBACK ONLY -- reached through an SSH port-forward
# (extra/wdremote.py), so nothing listens on the venue LAN. Bearer token in
# REMOTE_API_TOKEN_FILE (created on first start, readable over SSH).
REMOTE_API_ENABLED = True
REMOTE_API_HOST = "127.0.0.1"
REMOTE_API_PORT = 8765
REMOTE_API_TOKEN_FILE = "~/.walldance/remote_token"
REMOTE_API_STATUS_INTERVAL_S = 0.5  # main loop -> /status snapshot cadence

# Persistent app log (services/app_log.py): stdout/stderr teed to
# <repo>/logs/walldance_<stamp>.log (gitignored); newest APP_LOG_KEEP kept.
APP_LOG_ENABLED = True
APP_LOG_KEEP = 40

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
# The servo exposes for the part of the image that matters: the operator's ROI (the wall
# band) when one is drawn, else the frame, and drives that region's MEDIAN raw luma -- not
# the frame mean, which windows / lamps / retroreflective belts (saturated by design) drag
# up: 2026-10-08 daylight hangar, 6 % of the frame clipped even at 0.2 ms and the old
# "back off on > 0.5 % clipped" rule drove the wall black.  The median ignores up to half
# of the region being bright.
AUTOCAL_SERVO_TARGET_BRIGHTNESS = 70.0  # Region (ROI, else frame) median raw luma target
AUTOCAL_SERVO_TOLERANCE = 12.0      # Acceptable band around the target
# Clipping (pixels >= 250) only backs the servo off when it covers this share of the REGION
# AND the region's median is at/above target -- never while the median is below target.
# 15 %: far above what a lamp, a window patch or the dancers' belts cover inside the band
# (the field's 6 % was the whole frame, windows included), far below the 50 % where the
# median itself saturates (the "too bright" branch takes over there).  The back-off is one
# gentle step that keeps the median in band, so it cannot oscillate with the dark branch.
AUTOCAL_SERVO_CLIP_MAX_PCT = 15.0   # % of the region >= 250 (with median >= target) -> back off
AUTOCAL_SERVO_GAIN_MAX_DB = 36.0    # Gain ceiling for the servo (dB), further cut to the
                                    # camera's real range (IMX664: 30 dB = x31.6, ids_camera)
AUTOCAL_SERVO_SETTLE_FRAMES = 6     # Frames to let the sensor apply each command
AUTOCAL_SERVO_MAX_STEPS = 30        # Hard stop for the servo loop
# Gamma seed: chosen so the measured raw median maps near mid-gray; CLAHE is
# reduced on noisy scenes (CLAHE amplifies noise — ROADMAP bug #1 lesson).
AUTOCAL_GAMMA_TARGET = 110.0
# Upper bound relaxed 2.2 -> 4.0 (2026-06-16): the 12-corpus per-project sweep
# (tmp_analysis/calib_project_20260616) found relaxed gamma (toward the schema's
# 4.0 ceiling) was the best pick on 4/7 HANGAR/TOGO projects — near-black IR
# scenes are darker than the old 2.2 clamp allowed (whitebg2 b=5 wanted ~4.6;
# letting it reach 4.0 improved that project's score ~40%). The noise-cap below
# (AUTOCAL_GAMMA_NOISY_MAX) still independently protects noisy near-black scenes
# (e.g. TOGO-night) where extreme gamma would just amplify noise.
AUTOCAL_GAMMA_BOUNDS = (0.8, 4.0)
AUTOCAL_CLAHE_DEFAULT = 2.5
AUTOCAL_CLAHE_NOISY = 1.5
AUTOCAL_CLAHE_NOISE_SIGMA = 4.0     # Noise σ above which the reduced clip is used
AUTOCAL_GAMMA_NOISY_MAX = 1.8       # Gamma cap when the measured window noise σ
                                    # exceeds AUTOCAL_CLAHE_NOISE_SIGMA (⑤b):
                                    # on a verydark scene the mid-gray seed
                                    # brightens aggressively and mostly
                                    # amplifies noise → MOG2/frame-diff ghosts
                                    # (the TOGO-night Phase 1 lesson). Applied
                                    # after the window, so the var sweep saw
                                    # the brighter gamma = conservative var

# --- Calib2 subject pass (UX_PLAN.md U4) -------------------------------------
# Dancer calibration: accumulative evidence pool across runs/situations
# (live or playback).  All numeric rules provisional until the
# annotated-footage loop re-fits them (UX_PLAN §6).
AUTOCAL2_WINDOW_FRAMES = 240        # Collection window per run (~10 s @ 24 fps)
AUTOCAL2_MIN_SAMPLES = 40           # Min pooled height samples to trust the pool
AUTOCAL2_NET_HEIGHT_TARGET = 110.0  # Dancer height in YOLO net-input px. Phase 2b
                                    # benchmark (tmp_analysis/phase2b/SUMMARY.md):
                                    # knee medians 83-102 px, p75 104-128 across
                                    # all model tiers — 110 validated; oversizing
                                    # past the knee WORSENS quality (median -0.10)
AUTOCAL2_NET_HEIGHT_TARGET_DARK = 45.0  # High-noise regime target (noise σ above
                                    # AUTOCAL_CLAHE_NOISE_SIGMA, the ⑤b condition):
                                    # downscale acts as denoise — dark-crowd best
                                    # at net 52 px (640), 0.76→0.19 vs the net≥110
                                    # pick; outdoor-night U-curve optimum 38-65 px.
                                    # Phase 2b, n=2 dark scenes; re-check on the rig
AUTOCAL2_CONF_MARGIN = 0.05         # Sensitivity seed: p05 BOX-conf minus this (⑤a)
AUTOCAL2_CONF_BOUNDS = (0.15, 0.65) # Clamp for the seeded confidence — corpus-
                                    # measured best-τ spans 0.15–0.65
                                    # (CORPUS_ANALYSIS §6.5/§6.7); the old 0.50
                                    # ceiling was where the kp-conf seed pinned
AUTOCAL2_FPS_BUDGET = 20.0          # imgsz pick must keep predicted fps above
                                    # this (bug 12e / P-6; cost ∝ imgsz² from
                                    # the runs' measured fps). Above the
                                    # OPS_MIN_SHOW_FPS=15 alarm floor on purpose:
                                    # calibration should not aim AT the alarm
AUTOCAL2_BLUR_FRACTION = 0.10       # Allowed motion blur as a fraction of person height
AUTOCAL2_SPEED_PCTL = 95.0          # Speed percentile that sets the blur budget
AUTOCAL2_BLUR_BOUNDS_MS = (5.0, 30.0)  # Clamp for the refined blur budget
AUTOCAL2_STALE_TOL = 0.10           # ROI long-side relative change → run flagged stale
AUTOCAL2_FRAME_SAMPLES = 12         # Raw frames saved per run (future gamma/CLAHE sweep)
AUTOCAL2_NOISE_REUSE_S = 600.0      # Track C: if Aim ran within this many seconds,
                                    # Calib2 reuses Aim's clean-scene noise σ for the
                                    # dark net-height target instead of the live (now
                                    # dancer-populated) motion_model reading.

# --- Detection-sensitivity macro (UX_PLAN.md U5 / KNOBS.md E2) ---------------
# One operator dial 0-100 (50 = the calibrated seed).  Higher = more sensitive
# (fewer drops, more ghosts): confidence drops below the seed, and at the loose
# end varThreshold ramps to the floor to wake MOG2 cold-blob recovery (safe now
# that the Phase-F frozen-ghost gate landed).  Lower = stricter (fewer ghosts).
#
# Span re-fit (Phase 2 ⑦): the dial interpolates from the seed to ABSOLUTE
# corpus-measured bounds — best-τ spans 0.15–0.65 across the 12-scenario
# corpus (CORPUS_ANALYSIS §6.5/§6.7).  The previous fixed deltas (+0.25/−0.15
# around the seed) covered that range only when the seed happened to sit
# right; absolute bounds make the full measured range reachable from ANY
# seed, with the dial resolution adapting to where the seed sits.
SENS_CONF_MAX = 0.65                # Confidence at slider = 0 (strictest)
SENS_CONF_MIN = 0.15                # Confidence at slider = 100 (loosest)
SENS_VAR_FLOOR = 8.0                # varThreshold at slider = 100 (corpus-confirmed §3.1)
SENS_VAR_KNEE = 75.0                # Slider point where var starts ramping down

# Dial B "Gap bridging" span (OPERATOR_V2 §2.2; motion_sensitivity).  Monotonic
# "fewer drops", calibrated-seeded at 50.  Range = G1's validated grid
# {0.25, 0.55, 0.85} (tmp_analysis/g1); a modest fine-tune, not a dramatic lever.
SENS_BRIDGE_MIN = 0.25             # motion_sensitivity at slider = 0 (less bridging)
SENS_BRIDGE_MAX = 0.85             # motion_sensitivity at slider = 100 (more, fewer drops)

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

# Number of recording slots per project (recordings bar buttons 1..N; the
# command validators in runtime/api.py hard-code the same 1-9 range).
RECORDING_SLOTS = 9

# Disk use per codec, GB per hour at the IDS show crop (1488x1528) and 20 fps,
# for the readiness "disk" row ("~N h of <codec>"). Measured 2026-10 by
# re-encoding IR-rig recordings (hangar/residence slots 3-4, tango-H/H2):
#   FFV1  0.77 MB/frame on the show footage = 55 GB/h (brighter IR takes up to
#         ~0.97 MB/frame = 70 GB/h). Lossless, so it barely depends on content.
#   MJPG  (RECORDING_QUALITY 100) 0.02-0.055 MB/frame on dark IR = 1.5-4 GB/h;
#   mp4v  0.005-0.016 MB/frame = 0.4-1.2 GB/h. Both scale with scene content:
#         bright non-IR footage measured up to ~25 (MJPG) / ~17 (mp4v) GB/h.
RECORDING_GB_PER_HOUR = {
    "FFV1": 55.0,
    "MJPG": 4.0,
    "mp4v": 1.2,
}

# MJPG quality (1-100). Only affects MJPG codec; ignored for FFV1/mp4v.
# Default OpenCV is ~95 which causes visible artifacts in dark scenes.
# 98-100 is near-lossless but produces larger files (~3-5× vs default).
RECORDING_QUALITY = 100

# Video import into slots (REQ-1). .avi/.mp4 are byte-copied; other containers
# (.mov/.mkv/...) are transcoded with OpenCV's built-in MJPG encoder (present in
# every build, unlike FFV1/H.264) so the slot list and replay tools can read
# them. 98 keeps dark scenes clean at ~1/2-1/3 the size of 100.
IMPORT_TRANSCODE_CODEC = "MJPG"
IMPORT_TRANSCODE_QUALITY = 98

# Take provenance (MRK-0): every recording's .meta carries the camera settings
# at start/stop, app version, the project config and the rig sheet; while a take
# records, camera telemetry (exposure, gain, AE/AG, temperature) is sampled into
# <take>.camlog.jsonl every RECORDING_CAMLOG_INTERVAL_S (AE drifts within takes).
RECORDING_CAMLOG_INTERVAL_S = 1.0

# Rig sheet defaults for a new project (phase 1 Rig; the fields the camera cannot
# report). EMPTY = unknown: the code never claims hardware.  It used to pre-fill
# "Tamron M118FM08 (8 mm)" / 8 mm / "MidOpt BP850", so every take's .meta claimed
# the 8 mm while the 6 mm (M118FM06) was mounted.  A new project ("Start blank")
# inherits the on-camera part of the most recently used project's rig sheet
# instead (config_schema.RIG_SHEET_INHERIT_FIELDS); the operator enters the rest.
# A saved sheet that is still exactly the old pre-fill loses its lens/focal on
# load (config_schema.drop_legacy_rig_prefill).  Schema: config_schema.RIG_FIELDS.
RIG_DEFAULTS: dict = {}

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
# No bridge warm-up credit for a track inside the occlusion radius of a track
# matched this frame (CONT-1 follow-up, approved 2026-10-06): the motion there
# belongs to the matched dancer, so a bridge must not incubate a duplicate of
# it into a confirmed id.  The bridge still relays the track (position, tsu).
# Replay-measured with the BUG-2 tsu clamp, hangar aerial take (slot 4):
# frames with 2 ids 203 -> 127, ids 25 -> 20, coverage 0.942 -> 0.949.
MOTION_BRIDGE_OCCLUDED_WARMUP_GUARD = True

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

# Frame-diff staleness cap (bug #4).  The raw frame pair behind frame-diff
# queries only advances when the GLOBAL peak diff exceeds a small threshold;
# on a clean static stretch (quiet sensor + even IR — exactly the Starvis2
# scenes we are building toward) the pair freezes and queries keep reporting
# the LAST motion event indefinitely.  Past this many frames without an
# advance, frame-diff reports zero.  Under the cap the stale pair still
# bridges slow movers (their accumulating diff re-advances the pair).
MOTION_DIFF_PAIR_MAX_AGE_FRAMES = 30      # ~1.2 s at 25 fps; replay-validated

# =============================================================================
# TRACK WARMUP SCORING — delay output, not tracking
# =============================================================================
# New tracks accumulate a warmup score over consecutive matches.
# They are only output (to OSC / overlay) once score reaches the
# threshold.  This suppresses flickering background ghosts without
# slowing down the tracker's internal matching.
TRACK_WARMUP_THRESHOLD = 15               # Consecutive-match score to confirm.
TRACK_WARMUP_DECAY = 0.8                 # Score decay per missed frame.
#
# The integral above is the PRIMARY confirmation/retention mechanism — four
# replay-measured variants (2026-06-10, bug #14) proved its hysteresis is
# load-bearing: pure windowed replacements either confirmed fixed-spot
# false-positive bursts as permanent ghosts (latched) or flickered real
# dancers out during short detection dips (un-latched).  Its one real gap:
# below ~45% sustained detection rate it can NEVER confirm (corpus-measured:
# an aerial dancer detected 1 frame in 3, permanently unreported).  Hence a
# second, LIVE initial-confirmation path for intermittent moving subjects:
TRACK_WARMUP_SLOW_WINDOW = 40    # frames (~2 s @ 20 fps) of YOLO-credit
                                  # history (1.0 match, 0.0 bridge/miss —
                                  # bridge credit here incubates ghosts)
TRACK_WARMUP_SLOW_CREDITS = 12.0  # 30% duty floor: 1-in-3 confirms in ~2 s
TRACK_WARMUP_SLOW_MIN_TRAVEL_RATIO = 0.5  # ...only if the track TRAVELLED
                                  # (history span >= ratio x own bbox height):
                                  # duty alone cannot separate an intermittent
                                  # dancer from a flickering fixed texture
                                  # spot — dancers move, wall spots don't.
TRACK_WARMUP_SLOW_MIN_SEPARATION_RATIO = 0.7  # A slow-path-only track is NOT
                                  # reported while its centroid sits within
                                  # ratio x max(pair heights) of an already-
                                  # confirmed track (replay-measured: the
                                  # dominant slow-path ghosts are duplicate
                                  # tracks riding a dancer — moving, 30%+
                                  # duty — not fixed spots).  A real second
                                  # dancer farther than this confirms fine.
TRACK_WARMUP_INTERMITTENT_ENABLED = True   # The intermittent path is a
                                  # PER-SCENE switch (config key
                                  # `tracker_intermittent_confirm`).
                                  # Replay-measured across the 12-scenario
                                  # corpus: it wins on aerial/dark scenes
                                  # (hangar-aerial drop 0.126->0.074,
                                  # dark-crowd longest drop 9.6->5.8 s) and
                                  # loses on texture/facade scenes (duplicate
                                  # +flicker ghosts) (ROADMAP 3b).  D33
                                  # (2026-10-08): ON by default (was OFF) --
                                  # the show is the dark IR wall, and every
                                  # 2026-10-07 validation ran with it (D27).
                                  # A project that stores the key keeps its
                                  # value; the corpus manifests pin OFF.

# --- D33 (2026-10-08): detection defaults of a new project ---------------------
# The validated D27 settings (docs/PLAN_25M_2026-10.md C.13): yolo_first,
# confidence 0.15 with the sensitivity dial anchored on it (dial 50 = 0.15),
# intermittent confirm ON, yolo11x-pose at 1280 on TensorRT.  The one source for
# the app at startup ("Start blank", then a save = a new project), a project /
# safe-defaults load whose file LACKS a key (config_schema.fill_detection_defaults),
# tests/replay.py and the GUI fallbacks.  A key the project file stores keeps its
# value: tmp_analysis/plan25m/apply_d33.py moves an existing project.
# gamma / CLAHE / MOG2 / exposure are per venue (Calibrate), never defaults here.
DETECTION_DEFAULTS = {
    "model": YOLO_MODEL.replace(".pt", ""),
    "yolo_imgsz": YOLO_IMGSZ,
    "use_tensorrt": USE_TENSORRT,       # the show path; a missing engine is loud
    "confidence": YOLO_CONFIDENCE,
    "sensitivity_conf_seed": YOLO_CONFIDENCE,   # filled only with `confidence`
    "sensitivity": 50.0,                        # (a stored confidence anchors the dial)
    "tracking_mode": TRACKING_MODE.value,
    "tracker_intermittent_confirm": TRACK_WARMUP_INTERMITTENT_ENABLED,
}

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

# =============================================================================
# OPS CLUSTER - readiness check, health alerts, watchdog (TODO Phase 7)
# =============================================================================
OPS_READINESS_ENABLED = True        # Run the show-readiness check on STANDBY->RUN
OPS_OSC_PROBE_TIMEOUT_S = 0.25      # Connected-UDP probe wait (best effort)
OPS_MIN_SHOW_FPS = 15.0             # Readiness warns if loop FPS is below this
OPS_CALIB_AGE_WARN_H = 24.0         # Warn if the newest project save is older
OPS_DISK_WARN_FREE_GB = 60.0        # ~1 h of FFV1 recording (~55 GB/h, RECORDING_GB_PER_HOUR)
OPS_DISK_FAIL_FREE_GB = 10.0        # ~10 min of FFV1 recording headroom
OPS_FPS_BASELINE_WINDOW_S = 60.0    # Rolling-median FPS baseline window (RUN only)
OPS_FPS_DROP_FRACTION = 0.5         # Alert when fps < fraction * baseline ...
OPS_FPS_DROP_SUSTAIN_S = 10.0       # ... sustained this long
OPS_NO_DETECTION_ALERT_S = 30.0     # Zero tracked dancers in RUN (live, model ready)
OPS_OVER_CAP_ALERT_S = 10.0         # Reported tracks capped at MAX_PERSONS this long
                                    # = "more people than max_persons visible" (bug 12c);
                                    # transient ghost flashes stay quiet
OPS_HEIGHT_STALE_S = 120.0          # Rolling-median RAW detection height outside the
                                    # configured min/max gate this long = "person
                                    # height calibration looks stale" (⑤d; would
                                    # have caught the bulk-copied h=56 configs).
                                    # Raw = pre-size-gate: out-of-gate dancers
                                    # never become tracks, so track heights
                                    # cannot carry this signal
OPS_HEIGHT_WINDOW_S = 60.0          # Window for that rolling median
OPS_HEIGHT_MIN_SAMPLES = 20         # Median needs at least this many height samples
OPS_CAMERA_DOWN_ALERT_S = 15.0      # Reconnecting longer than this = loud alert
OPS_GPU_TEMP_ALERT_C = 85           # Matches the GUI badge red threshold
OPS_GPU_TEMP_SUSTAIN_S = 30.0
OPS_GPU_POLL_S = 5.0                # GPU stats poll cadence inside the health tick
OPS_ALERT_COOLDOWN_S = 120.0        # Per-alert-kind re-fire interval
OPS_WATCHDOG_HANG_S = 10.0          # Heartbeat age that counts as a main-loop hang
OPS_WATCHDOG_POLL_S = 1.0
# Main-loop exception boundary (audit 2026-10 ARCH-6). A CUDA/TRT/tracker/OSC
# error on one frame skips that frame (alert + traceback in the log) instead of
# ending the show. This many CONSECUTIVE failures (~2.5 s at 20 fps) mean a
# persistent fault (e.g. a dead CUDA context): the session stops cleanly --
# recordings finalised, logs flushed, logs/last_crash.json for the next start
# -- with exit code 3, so the launcher offers Restart.
OPS_TICK_ERROR_EXIT_STREAK = 50
OPS_TICK_ERROR_ALERT_INTERVAL_S = 10.0  # traceback + operator alert at most this often
