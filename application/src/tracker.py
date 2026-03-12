"""
Multi-person tracker using Kalman Filter + Hungarian Algorithm.
Optimized for wall dancers with potentially rotated orientations.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment
from filterpy.kalman import KalmanFilter
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from config import (
    TRACKER_MAX_AGE, TRACKER_MIN_HITS, TRACKER_DISTANCE_THRESHOLD,
    TRACKER_DORMANT_MAX_AGE,
    TRACKER_VELOCITY_WEIGHT, TRACKER_PROCESS_NOISE, TRACKER_MEASUREMENT_NOISE,
    KEYPOINT_CONFIDENCE,
    TRACKER_MATCH_GATE_RATIO, TRACKER_NEW_TRACK_GATE_RATIO,
    TRACKER_DUPLICATE_GATE_RATIO, TRACKER_SEPARATION_MEMORY_FRAMES,
    TRACKER_SEPARATION_PENALTY_WEIGHT, TRACKER_VELOCITY_PREDICTION_INFLUENCE,
    TRACKER_ESTABLISHED_FRAMES, TRACKER_MERGE_SIZE_RATIO,
    TRACKER_OCCLUSION_DISTANCE_RATIO, TRACKER_OCCLUSION_AGE_FACTOR,
    TRACKER_DORMANT_VELOCITY_DECAY,
    SHADOW_TRACK_VELOCITY_CORR, SHADOW_TRACK_FRAMES,
    SHADOW_PROXIMITY_RATIO,
    TRACKER_ESTABLISHED_MAX_AGE_MULT, TRACKER_CLOSE_PROXIMITY_RATIO,
    TRACKER_CLOSE_POS_WEIGHT, TRACKER_CLOSE_KPT_WEIGHT,
    TRACKER_CLOSE_SIZE_WEIGHT, TRACKER_ESTABLISHED_SEP_BOOST,
    TRACKER_EDGE_ZONE_RATIO, TRACKER_CENTER_EXIT_RESURRECT_BOOST,
    TRACKER_EDGE_EXIT_AGE_MULT, TRACKER_CENTER_NEW_TRACK_GATE_MULT,
    CENTROID_OUTPUT_SMOOTHING,
    TRACKER_EVENT_LOG_ENABLED, TRACKER_EVENT_LOG_FILE,
    TRACKER_EVENT_LOG_MAX_ENTRIES, TRACKER_EVENT_LOG_FLUSH_INTERVAL,
    TRACKER_MAHALANOBIS_GATE, TRACKER_MAHALANOBIS_GATE_NOISE,
    TRACKER_CASCADED_MATCHING, TRACKER_CASCADE_OCCLUSION_SWAP,
    TRACKER_MERGE_DIRECTION_SWAP, TRACKER_CASCADE_SUPPRESSION_FRAMES,
    TRACKER_POSE_HISTORY_DEPTH, TRACKER_TRAJECTORY_WEIGHT,
)
from tracking_logger import TrackingLogger

# Set to True for detailed tracking debug output
TRACKER_DEBUG = False


class DormantSnapshot:
    """Frozen snapshot of a track that has left the active pool.

    Stored in the dormant ("graveyard") pool so that if the same person
    reappears we can resurrect the original track ID instead of minting
    a new one.  Matching uses position + bbox height + keypoint shape.

    Stores velocity so that ``projected_position`` can extrapolate
    where the person *should* be after N dormant frames.
    """
    __slots__ = ('track_id', 'last_position', 'velocity', 'keypoints',
                 'confidence', 'bbox_height', 'age', 'was_occluded',
                 'exited_from_edge', 'hits', 'track_age', 'vx_history',
                 'pose_history', 'position_history', 'confidence_history',
                 'bbox_area_history', 'smoothed_centroid',
                 'last_match_frame', 'last_occluded_frame',
                 'occlusion_start_frame', 'last_reacquired_frame',
                 'last_merge_frame', 'merge_episode_start_frame',
                 'merge_episode_id')

    def __init__(self, track: 'DancerTrack', exited_from_edge: bool = True):
        self.track_id: int = track.track_id
        self.last_position: np.ndarray = track.get_last_known_position().copy()
        self.velocity: np.ndarray = track.get_velocity().copy()
        self.keypoints: np.ndarray = track.keypoints.copy()
        self.confidence: np.ndarray = track.confidence.copy()
        self.bbox_height: float = float(track.bbox[3])
        self.age: int = 0  # frames since entering dormant pool
        self.was_occluded: bool = getattr(track, '_occluded', False)
        self.exited_from_edge: bool = exited_from_edge
        self.hits: int = int(track.hits)
        self.track_age: int = int(track.age)
        self.vx_history = [float(v) for v in track._vx_history]
        self.pose_history = [
            {
                'kpts_norm': snap['kpts_norm'].copy(),
                'mask': snap['mask'].copy(),
                'aspect': float(snap['aspect']),
            }
            for snap in track._pose_history
        ]
        self.position_history = [pos.copy() for pos in track.history]
        self.confidence_history = [conf.copy() for conf in track.confidence_history]
        self.bbox_area_history = [float(area) for area in track.bbox_area_history]
        self.smoothed_centroid = track.get_smoothed_centroid().copy()
        self.last_match_frame = int(getattr(track, '_last_match_frame', -1))
        self.last_occluded_frame = int(getattr(track, '_last_occluded_frame', -1))
        self.occlusion_start_frame = getattr(track, '_occlusion_start_frame', None)
        self.last_reacquired_frame = int(getattr(track, '_last_reacquired_frame', -1))
        self.last_merge_frame = int(getattr(track, '_last_merge_frame', -1))
        self.merge_episode_start_frame = getattr(track, '_merge_episode_start_frame', None)
        self.merge_episode_id = int(getattr(track, '_merge_episode_id', 0))

    def projected_position(self) -> np.ndarray:
        """Extrapolate position using stored velocity with decay."""
        if self.age == 0 or np.linalg.norm(self.velocity) < 0.1:
            return self.last_position.copy()
        # Sum of decayed velocity over `age` frames:
        #   pos += v * sum(decay^i for i in 0..age-1)
        decay = TRACKER_DORMANT_VELOCITY_DECAY
        if abs(decay - 1.0) < 1e-6:
            displacement = self.velocity * self.age
        else:
            geo_sum = (1.0 - decay ** self.age) / (1.0 - decay)
            displacement = self.velocity * geo_sum
        return self.last_position + displacement


class DancerTrack:
    """Single dancer track with Kalman filter for position smoothing."""
    
    _id_counter = 0
    
    def __init__(self, keypoints, confidence, bbox, smoothing_depth=1):
        """
        Initialize a new track.
        
        Args:
            keypoints: (17, 2) array of (x, y) positions
            confidence: (17,) array of confidence scores
            bbox: (x, y, w, h) bounding box
            smoothing_depth: Number of frames to average for confidence smoothing
        """
        DancerTrack._id_counter += 1
        self.track_id = DancerTrack._id_counter
        self.keypoints = keypoints.copy()
        self.confidence = confidence.copy()
        self.bbox = np.array(bbox)
        self.hits = 1
        self.age = 0
        self.time_since_update = 0
        self._occluded = False           # True when hidden behind another track
        self._shadow_streak = 0          # Consecutive frames detected as shadow
        self.smoothing_depth = smoothing_depth
        self._last_match_frame = -1
        self._last_occluded_frame = -1
        self._occlusion_start_frame: int | None = None
        self._last_reacquired_frame = -1
        self._last_merge_frame = -1
        self._merge_episode_start_frame: int | None = None
        self._merge_episode_id = 0

        # Velocity-direction history for merge-exit swap detection (Phase 1d)
        self._vx_history: deque[float] = deque(maxlen=20)

        # Phase 2: per-frame skeleton history for trajectory matching
        self._pose_history: deque[dict[str, Any]] = deque(
            maxlen=TRACKER_POSE_HISTORY_DEPTH)
        # Store initial frame
        centroid_init = self._compute_centroid(keypoints, confidence)
        kpts_norm_init = keypoints - centroid_init
        self._pose_history.append({
            'kpts_norm': kpts_norm_init.copy(),
            'mask': confidence > KEYPOINT_CONFIDENCE,
            'aspect': float(bbox[2]) / max(1.0, float(bbox[3])),
        })

        # Smoothed centroid for output (EMA, does not affect tracking)
        centroid = centroid_init
        self._smoothed_centroid = centroid.copy()
        
        # History for visualization
        self.history: deque[np.ndarray] = deque(maxlen=30)
        
        # Confidence history for temporal smoothing
        self.confidence_history: deque[np.ndarray] = deque(
            maxlen=max(1, smoothing_depth))
        
        # Bbox area history for anti-merge detection (Phase 4)
        self.bbox_area_history: deque[float] = deque(maxlen=30)
        self.bbox_area_history.append(float(bbox[2] * bbox[3]))
        
        # Initialize Kalman filter for centroid tracking
        # State: [x, y, vx, vy, ax, ay]
        self.kf = KalmanFilter(dim_x=6, dim_z=2)
        
        dt = 1.0
        self.kf.F = np.array([
            [1, 0, dt, 0,  0.5*dt**2, 0],
            [0, 1, 0,  dt, 0,         0.5*dt**2],
            [0, 0, 1,  0,  dt,        0],
            [0, 0, 0,  1,  0,         dt],
            [0, 0, 0,  0,  1,         0],
            [0, 0, 0,  0,  0,         1]
        ])
        
        self.kf.H = np.array([
            [1, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0]
        ])
        
        self.kf.R = np.eye(2) * TRACKER_MEASUREMENT_NOISE
        
        q = TRACKER_PROCESS_NOISE
        self.kf.Q = np.diag([q*0.1, q*0.1, q*1.0, q*1.0, q*2.0, q*2.0])
        
        # Initial covariance scales with detection size so that the
        # filter starts with proportional uncertainty regardless of
        # whether persons are 50px or 200px tall.
        h = max(10.0, float(bbox[3]))  # bbox height in pixels
        pos_var = (h * 0.67) ** 2       # ~2/3 of person height
        vel_var = pos_var * 0.1         # velocity less certain
        acc_var = pos_var * 0.01        # acceleration even less
        self.kf.P = np.diag([pos_var, pos_var,
                             vel_var, vel_var,
                             acc_var, acc_var])
        
        centroid = self._compute_centroid(keypoints, confidence)
        self.kf.x = np.zeros((6, 1))
        self.kf.x[:2] = centroid.reshape(2, 1)
        
        self.history.append(centroid)
    
    def _compute_centroid(self, keypoints, confidence, thresh=KEYPOINT_CONFIDENCE):
        """Compute weighted centroid of visible keypoints."""
        mask = confidence > thresh
        if not np.any(mask):
            # Fallback to bbox center
            return np.array([self.bbox[0] + self.bbox[2]/2, 
                           self.bbox[1] + self.bbox[3]/2])
        
        weights = confidence[mask]
        points = keypoints[mask]
        return np.average(points, axis=0, weights=weights)
    
    def predict(self):
        """Predict next state."""
        # Add slight friction to missing tracks so they don't accelerate away,
        # but let them coast through occlusions so they emerge on the correct side!
        if self.time_since_update > 0:
            self.kf.x[2:4] *= 0.9  # coast through occlusion (10% dampening)
            self.kf.x[4:6] *= 0.5  # drop acceleration completely
            
        self.kf.predict()
        self.age += 1
        self.time_since_update += 1
        
        # Clamp velocity to prevent runaway predictions
        # Max reasonable velocity scales with detection size: a dancer
        # can't teleport more than ~0.67× their bbox height per frame.
        own_height = max(10.0, float(self.bbox[3]))
        MAX_VELOCITY = own_height * 0.67
        vel = self.kf.x[2:4].flatten()
        speed = np.linalg.norm(vel)
        if not np.isfinite(speed) or speed > MAX_VELOCITY:
            if speed > 0 and np.isfinite(speed):
                scale = MAX_VELOCITY / speed
            else:
                scale = 0.0  # reset to zero if NaN/inf
            self.kf.x[2:4] *= scale
            self.kf.x[4:6] *= scale  # Also clamp acceleration
        
        # Final safety: kill any remaining NaN/inf in state
        if not np.all(np.isfinite(self.kf.x)):
            self.kf.x = np.nan_to_num(self.kf.x, nan=0.0, posinf=0.0, neginf=0.0)
        
        return self.kf.x[:2].flatten()
    
    def update(self, keypoints, confidence, bbox, merge_frame=False):
        """Update track with new detection.
        
        Args:
            merge_frame: True when n_detections < n_tracks (detection
                merger).  Pose history is NOT recorded on merge frames
                because the matched skeleton is a blend of multiple
                dancers and would contaminate trajectory matching.
        """
        self.keypoints = keypoints.copy()
        self.confidence = confidence.copy()
        self.bbox = np.array(bbox)
        self.bbox_area_history.append(float(bbox[2] * bbox[3]))
        self.hits += 1
        self.time_since_update = 0
        self._fractional_age = 0.0
        
        # Store confidence for temporal smoothing
        self.confidence_history.append(confidence.copy())
        
        centroid = self._compute_centroid(keypoints, confidence)
        self.kf.update(centroid.reshape(2, 1))
        self.history.append(centroid)

        # Record x-velocity for merge-exit direction swap
        self._vx_history.append(float(self.kf.x[2, 0]))

        # Phase 2: store skeleton snapshot for trajectory matching.
        # Skip on merge frames — the skeleton is a blend of multiple
        # dancers and would hurt trajectory matching accuracy.
        if not merge_frame:
            kpts_norm = keypoints - centroid
            self._pose_history.append({
                'kpts_norm': kpts_norm.copy(),
                'mask': confidence > KEYPOINT_CONFIDENCE,
                'aspect': float(bbox[2]) / max(1.0, float(bbox[3])),
            })

        # Update smoothed centroid (EMA for jitter-free output)
        alpha = CENTROID_OUTPUT_SMOOTHING
        self._smoothed_centroid = (alpha * centroid
                                   + (1.0 - alpha) * self._smoothed_centroid)

    def _note_merge_episode(self, frame_index: int):
        """Record participation in a merge or occlusion episode."""
        if frame_index < 0:
            return
        if self._last_merge_frame < 0 or frame_index - self._last_merge_frame > 1:
            self._merge_episode_id += 1
            self._merge_episode_start_frame = frame_index
        elif self._merge_episode_start_frame is None:
            self._merge_episode_start_frame = frame_index
        self._last_merge_frame = frame_index

    def note_match_event(self, frame_index: int, merge_frame: bool = False):
        """Record that this track matched a detection on the current frame."""
        if frame_index >= 0:
            if self._occluded:
                self._last_reacquired_frame = frame_index
            self._last_match_frame = frame_index
            if merge_frame:
                self._note_merge_episode(frame_index)
        self._occluded = False
        self._occlusion_start_frame = None

    def note_occlusion_event(self, frame_index: int, merge_related: bool = False):
        """Record that this track survived as an occluded participant."""
        if frame_index >= 0:
            if not self._occluded or self._occlusion_start_frame is None:
                self._occlusion_start_frame = frame_index
            self._last_occluded_frame = frame_index
            if merge_related:
                self._note_merge_episode(frame_index)
        self._occluded = True

    def clear_occlusion_event(self):
        """Clear live occlusion state while keeping history intact."""
        self._occluded = False
        self._occlusion_start_frame = None

    def has_recent_merge_context(self, frame_index: int, window: int) -> bool:
        """Whether this track was recently in a merge or occlusion episode."""
        recent_merge = (
            self._last_merge_frame >= 0
            and frame_index - self._last_merge_frame <= window
        )
        recent_occlusion = (
            self._last_occluded_frame >= 0
            and frame_index - self._last_occluded_frame <= window
        )
        return recent_merge or recent_occlusion

    def restore_continuity(self, snap: DormantSnapshot):
        """Restore temporal history from a dormant snapshot.

        Keeps identity continuity signals (pose/velocity/bbox history)
        while retaining the newly observed detection as the current state.
        """
        current_centroid = self._compute_centroid(self.keypoints, self.confidence)

        self.hits = max(self.hits, snap.hits, 1)
        self.age = max(self.age, snap.track_age + snap.age)
        self.time_since_update = 0
        self._occluded = snap.was_occluded
        self._shadow_streak = 0
        self._last_match_frame = snap.last_match_frame
        self._last_occluded_frame = snap.last_occluded_frame
        self._occlusion_start_frame = snap.occlusion_start_frame
        self._last_reacquired_frame = snap.last_reacquired_frame
        self._last_merge_frame = snap.last_merge_frame
        self._merge_episode_start_frame = snap.merge_episode_start_frame
        self._merge_episode_id = snap.merge_episode_id

        self._vx_history = deque(snap.vx_history, maxlen=20)
        self._pose_history = deque([
            {
                'kpts_norm': hist['kpts_norm'].copy(),
                'mask': hist['mask'].copy(),
                'aspect': float(hist['aspect']),
            }
            for hist in snap.pose_history
        ], maxlen=TRACKER_POSE_HISTORY_DEPTH)
        self.history = deque([pos.copy() for pos in snap.position_history], maxlen=30)
        self.confidence_history = deque(
            [conf.copy() for conf in snap.confidence_history],
            maxlen=max(1, self.smoothing_depth),
        )
        self.bbox_area_history = deque(snap.bbox_area_history, maxlen=30)

        self._smoothed_centroid = snap.smoothed_centroid.copy()
        alpha = CENTROID_OUTPUT_SMOOTHING
        self._smoothed_centroid = (alpha * current_centroid
                                   + (1.0 - alpha) * self._smoothed_centroid)

        self.history.append(current_centroid.copy())
        self.confidence_history.append(self.confidence.copy())
        self.bbox_area_history.append(float(self.bbox[2] * self.bbox[3]))

        kpts_norm = self.keypoints - current_centroid
        self._pose_history.append({
            'kpts_norm': kpts_norm.copy(),
            'mask': self.confidence > KEYPOINT_CONFIDENCE,
            'aspect': float(self.bbox[2]) / max(1.0, float(self.bbox[3])),
        })

        decayed_vel = snap.velocity * (TRACKER_DORMANT_VELOCITY_DECAY ** max(0, snap.age))
        self.kf.x[2:4] = decayed_vel.reshape(2, 1)
        self.kf.x[4:6] = 0.0
    
    def get_smoothed_confidence(self):
        """Get temporally smoothed confidence values."""
        if len(self.confidence_history) == 0:
            return self.confidence
        if len(self.confidence_history) == 1:
            return self.confidence_history[0]
        # Average across the history
        stacked = np.stack(list(self.confidence_history))
        return np.mean(stacked, axis=0)
    
    def set_smoothing_depth(self, depth):
        """Update smoothing depth (resizes confidence history)."""
        self.smoothing_depth = max(1, depth)
        # Create new deque with new maxlen, preserving recent values
        old_history = list(self.confidence_history)
        self.confidence_history = deque(maxlen=self.smoothing_depth)
        for conf in old_history[-self.smoothing_depth:]:
            self.confidence_history.append(conf)

    def get_centroid(self):
        """Get current estimated centroid."""
        return self.kf.x[:2].flatten()

    def get_smoothed_centroid(self):
        """Get EMA-smoothed centroid for jitter-free output."""
        return self._smoothed_centroid.copy()
    
    def get_last_known_position(self):
        """Get last measured position (not predicted)."""
        if len(self.history) > 0:
            return self.history[-1]
        return self.get_centroid()
    
    def get_velocity(self):
        """Get current velocity."""
        return self.kf.x[2:4].flatten()
    
    def get_speed(self):
        """Get speed magnitude."""
        return np.linalg.norm(self.get_velocity())

    def get_dominant_vx_direction(self, min_entries=5, min_confidence=0.6):
        """Return the dominant recent x-velocity direction.

        Uses the **older half** of ``_vx_history`` so that any corruption
        from a close-approach or merge zone is excluded.

        Returns:
            +1 (moving right), -1 (moving left), or 0 (unclear/too few data).
        """
        if len(self._vx_history) < min_entries:
            return 0
        # Use the older half of the buffer — least corrupted by merge
        n_old = max(3, len(self._vx_history) // 2)
        old_entries = list(self._vx_history)[:n_old]
        # Filter out near-zero velocities (< 1 px/frame) as noise
        signs = [int(np.sign(v)) for v in old_entries if abs(v) > 1.0]
        if len(signs) < 3:
            return 0
        n_pos = sum(1 for s in signs if s > 0)
        n_neg = sum(1 for s in signs if s < 0)
        total = n_pos + n_neg
        if total == 0:
            return 0
        if n_pos / total >= min_confidence:
            return 1
        if n_neg / total >= min_confidence:
            return -1
        return 0

    def trajectory_cost(self, det_kpts_norm, det_mask, det_aspect):
        """Compute how well a detection matches this track's pose history.

        Compares the detection's normalised skeleton against the last N
        stored skeletons with exponential recency weighting.  Also factors
        in aspect-ratio continuity.

        Returns a cost in the same scale as ``kpt_cost`` (mean per-joint
        pixel distance).  Returns ``None`` if insufficient history.
        """
        if len(self._pose_history) < 3:
            return None

        total_weight = 0.0
        weighted_cost = 0.0

        for i, snap in enumerate(self._pose_history):
            # Exponential recency: newest entry (last) gets highest weight
            age = len(self._pose_history) - 1 - i
            w = 0.7 ** age  # decay factor per frame into the past

            both = det_mask & snap['mask']
            n_both = int(np.sum(both))
            if n_both < 3:
                continue

            diffs = np.linalg.norm(
                det_kpts_norm[both] - snap['kpts_norm'][both], axis=1)
            frame_cost = float(np.mean(diffs))

            # Small aspect-ratio penalty (sudden shape change = likely wrong body)
            ar_diff = abs(det_aspect - snap['aspect'])
            frame_cost += ar_diff * 15.0  # scale to comparable magnitude

            weighted_cost += w * frame_cost
            total_weight += w

        if total_weight < 0.5:
            return None

        return weighted_cost / total_weight

    @property
    def avg_bbox_area(self):
        """Running average of bbox area for anti-merge detection."""
        if len(self.bbox_area_history) == 0:
            return float(self.bbox[2] * self.bbox[3])
        return float(np.mean(self.bbox_area_history))

    @property
    def is_established(self):
        """Whether this track has existed long enough to be reliable."""
        return self.hits >= TRACKER_ESTABLISHED_FRAMES

    @property
    def skeleton_quality(self) -> float:
        """0-1 score of skeleton reliability.

        Shadows typically have few visible keypoints with low confidence.
        Real people have many keypoints with high confidence.
        """
        n_valid = int(np.sum(self.confidence > KEYPOINT_CONFIDENCE))
        mean_conf = float(np.mean(self.confidence[self.confidence > KEYPOINT_CONFIDENCE])) if n_valid > 0 else 0.0
        # Normalize: 17 keypoints max, confidence max ~1.0
        kpt_ratio = min(1.0, n_valid / 10.0)  # 10+ keypoints = full score
        return 0.5 * kpt_ratio + 0.5 * mean_conf

    def get_normalized_skeleton(self):
        """Keypoints translated to centroid-origin for shape comparison.

        Returns (kpts_norm, mask) where kpts_norm is (17,2) with each
        valid keypoint expressed relative to the weighted centroid.
        This makes shape comparison position-independent — critical when
        two dancers overlap spatially but have different poses.
        """
        mask = self.confidence > KEYPOINT_CONFIDENCE
        centroid = self._compute_centroid(self.keypoints, self.confidence)
        kpts_norm = self.keypoints - centroid  # broadcast (17,2) - (2,)
        return kpts_norm, mask


@dataclass
class PendingTrackUpdate:
    """Deferred committed match awaiting post-policy application."""

    trk_idx: int
    det_idx: int
    kpts: np.ndarray
    conf: np.ndarray
    bbox: np.ndarray


@dataclass
class FrameUpdateContext:
    """Frame-scoped tracker state used during one update cycle."""

    merge_frame: bool = False
    pending_updates: list[PendingTrackUpdate] = field(default_factory=list)
    post_update_clamp_indices: set[int] = field(default_factory=set)


@dataclass
class ClosestTrackResult:
    """Nearest-track lookup result for an unmatched detection."""

    min_dist: float
    track: DancerTrack | None
    idx: int | None


class DancerTracker:
    """Multi-dancer tracker with Hungarian assignment."""
    
    def __init__(self):
        self.tracks = []
        self.frame_count = 0
        self.max_age = TRACKER_MAX_AGE
        self.min_hits = TRACKER_MIN_HITS
        self.velocity_weight = TRACKER_VELOCITY_WEIGHT
        self._smoothing_depth = 1  # Temporal confidence smoothing depth
        self._person_height_px = 150  # updated via set_person_height()

        # Scale-dependent thresholds — all derived from person_height_px.
        # Call set_person_height() to update; the master dial in config.py
        # is PERSON_HEIGHT_PX.  TRACKER_DISTANCE_THRESHOLD is only used as
        # the initial fallback before the app callback fires.
        self.distance_threshold = max(50, int(TRACKER_DISTANCE_THRESHOLD * 0.33))
        self.new_track_min_distance = max(20, int(TRACKER_DISTANCE_THRESHOLD * 0.33))
        self.duplicate_distance = max(10, int(TRACKER_DISTANCE_THRESHOLD * 0.17))

        # Dormant pool for re-ID after occlusion
        self._dormant: list[DormantSnapshot] = []
        self.dormant_max_age = TRACKER_DORMANT_MAX_AGE

        # Pairwise distance history for separation memory (Phase 2+4)
        self._pair_distances: dict[tuple[int, int], deque] = {}
        self._separation_penalty_weight = TRACKER_SEPARATION_PENALTY_WEIGHT
        self._velocity_prediction_influence = TRACKER_VELOCITY_PREDICTION_INFLUENCE

        # Frame dimensions for edge-exit detection (content area bounds)
        self._content_left: int = 0     # left edge of actual image content
        self._content_right: int = 0    # right edge of actual image content

        # Cascade suppression window (Phase 1c+)
        # After CASCADE_OCCLUSION_SWAP fires for an established track,
        # that track is suppressed from Pass 1 for N frames so the
        # tentative track retains priority and doesn't starve.
        self._cascade_suppressed: dict[int, int] = {}  # track_id → frames left

        # Structured event logger (Phase 0)
        self.logger = TrackingLogger(
            enabled=TRACKER_EVENT_LOG_ENABLED,
            filepath=TRACKER_EVENT_LOG_FILE,
            max_entries=TRACKER_EVENT_LOG_MAX_ENTRIES,
            flush_interval=TRACKER_EVENT_LOG_FLUSH_INTERVAL,
        )

    # ------------------------------------------------------------------
    # Person-height master dial
    # ------------------------------------------------------------------
    def set_person_height(self, height_px: int):
        """Derive all scale-dependent thresholds from expected person height.

        This is the **single knob** that adjusts tracker behaviour for
        capture distance.  Ratios are configured in config.py:

        * distance_threshold      = height × TRACKER_MATCH_GATE_RATIO
        * new_track_min_distance  = height × TRACKER_NEW_TRACK_GATE_RATIO
        * duplicate_distance      = height × TRACKER_DUPLICATE_GATE_RATIO
        """
        self._person_height_px = max(1, height_px)
        # Floor clamps are proportional so they never dominate the ratios
        floor = max(5, height_px // 10)  # 10 % of person height
        self.distance_threshold = max(floor, int(height_px * TRACKER_MATCH_GATE_RATIO))
        self.new_track_min_distance = max(floor // 2, int(height_px * TRACKER_NEW_TRACK_GATE_RATIO))
        self.duplicate_distance = max(floor // 5, int(height_px * TRACKER_DUPLICATE_GATE_RATIO))

    def set_frame_dimensions(self, width: int, pad_x: int = 0):
        """Store content-area boundaries for edge-exit detection.

        Called every frame from the pipeline so that changes in input
        resolution or YOLO ``imgsz`` take effect immediately.

        Args:
            width:  Total frame width in the coordinate space the
                    tracker operates in (``imgsz`` for the GPU path,
                    ``original_w`` for the CPU path).
            pad_x:  Horizontal letterbox padding on the left side.
                    Content area spans ``[pad_x, width - pad_x]``.
                    Zero for the CPU path (no letterbox).
        """
        self._content_left = pad_x
        self._content_right = width - pad_x

    def _is_near_edge(self, x: float) -> bool:
        """Return True if x-coordinate is inside the left or right edge zone."""
        content_width = self._content_right - self._content_left
        if content_width <= 0:
            return True  # unknown frame size → assume edge (safe default)
        edge_px = content_width * TRACKER_EDGE_ZONE_RATIO
        return (x < self._content_left + edge_px
                or x > self._content_right - edge_px)
    
    @property
    def smoothing_depth(self):
        return self._smoothing_depth
    
    @smoothing_depth.setter
    def smoothing_depth(self, value):
        """Set smoothing depth and update all existing tracks."""
        self._smoothing_depth = max(1, value)
        for track in self.tracks:
            track.set_smoothing_depth(self._smoothing_depth)
    
    def reset(self):
        """Reset all tracks, dormant pool, and separation memory."""
        self.tracks = []
        self._dormant = []
        self._pair_distances = {}
        self.frame_count = 0
        DancerTrack._id_counter = 0
        self.logger.reset()
    
    def _compute_centroid(self, keypoints, confidence, bbox):
        """Compute centroid from keypoints or bbox."""
        mask = confidence > KEYPOINT_CONFIDENCE
        if np.any(mask):
            weights = confidence[mask]
            points = keypoints[mask]
            return np.average(points, axis=0, weights=weights)
        return np.array([bbox[0] + bbox[2]/2, bbox[1] + bbox[3]/2])

    def _is_detection_in_crowded_zone(self, det_centroid: np.ndarray,
                                      tracks, close_dist: float) -> bool:
        """Whether a detection lies near multiple tracks."""
        n_nearby_tracks = sum(
            1 for track in tracks
            if np.linalg.norm(det_centroid - track.get_centroid()) < close_dist
        )
        return n_nearby_tracks >= 2

    def _mahalanobis_gate_allows(self, det_idx: int,
                                 det_centroid: np.ndarray,
                                 track: DancerTrack) -> bool:
        """Check whether a detection passes Mahalanobis gating for a track."""
        if TRACKER_MAHALANOBIS_GATE <= 0:
            return True

        innov = det_centroid - track.kf.x[:2].flatten()
        R_gate = np.eye(2) * TRACKER_MAHALANOBIS_GATE_NOISE
        S = track.kf.P[:2, :2] + R_gate
        try:
            S_inv = np.linalg.inv(S)
            maha_sq = float(innov @ S_inv @ innov)
        except np.linalg.LinAlgError:
            maha_sq = 0.0

        if maha_sq <= TRACKER_MAHALANOBIS_GATE:
            return True

        self.logger.log("MAHALANOBIS_GATE", {
            "det": det_idx,
            "track_id": track.track_id,
            "chi2": round(maha_sq, 1),
            "gate": TRACKER_MAHALANOBIS_GATE,
            "dist_px": round(float(np.linalg.norm(innov)), 1),
        })
        return False

    def _compute_position_cost(self, det_centroid: np.ndarray,
                               track: DancerTrack) -> tuple[float, np.ndarray, np.ndarray]:
        """Compute blended position cost and return supporting state."""
        predicted_pos = track.get_centroid()
        last_known_pos = track.get_last_known_position()
        velocity = track.get_velocity()

        velocity_adjusted = predicted_pos + velocity * self.velocity_weight
        dist_pred = np.linalg.norm(det_centroid - predicted_pos)
        dist_vel = np.linalg.norm(det_centroid - velocity_adjusted)
        dist_last = np.linalg.norm(det_centroid - last_known_pos)
        vpi = self._velocity_prediction_influence
        w_pred = 0.4 + 0.2 * vpi
        w_vel = 0.2 + 0.2 * vpi
        w_last = 0.4 - 0.4 * vpi
        pos_cost = w_pred * dist_pred + w_vel * dist_vel + w_last * dist_last
        return float(pos_cost), last_known_pos, velocity

    def _compute_keypoint_cost(self, det_kpts_norm, mask_det,
                               track: DancerTrack) -> tuple[float, int]:
        """Compute normalized skeleton mismatch cost and visible-joint count."""
        kpt_cost = 0.0
        mask_trk = track.confidence > KEYPOINT_CONFIDENCE
        both = mask_det & mask_trk
        n_both = int(np.sum(both))
        if n_both >= 3:
            trk_kpts_norm, _ = track.get_normalized_skeleton()
            diffs = np.linalg.norm(
                det_kpts_norm[both] - trk_kpts_norm[both], axis=1)
            kpt_cost = float(np.mean(diffs))
        return kpt_cost, n_both

    def _compute_separation_penalty(self, det_centroid: np.ndarray,
                                    track: DancerTrack, tracks) -> float:
        """Penalty for assigning a detection near a historically separate track pair."""
        sep_penalty = 0.0
        for other_track in tracks:
            if other_track is track:
                continue
            pair_key = self._pair_key(track.track_id, other_track.track_id)
            if pair_key not in self._pair_distances:
                continue
            hist = self._pair_distances[pair_key]
            if len(hist) < 5:
                continue
            avg_sep = float(np.mean(hist))
            if avg_sep < self.distance_threshold * 0.5:
                continue
            other_pos = other_track.get_centroid()
            dist_to_other = np.linalg.norm(det_centroid - other_pos)
            if dist_to_other < avg_sep * 0.6:
                penalty = ((avg_sep - dist_to_other)
                           * self._separation_penalty_weight)
                if track.is_established and other_track.is_established:
                    penalty *= TRACKER_ESTABLISHED_SEP_BOOST
                sep_penalty = max(sep_penalty, penalty)
        return float(sep_penalty)

    def _compute_direction_penalty(self, det_centroid: np.ndarray,
                                   last_known_pos: np.ndarray,
                                   velocity: np.ndarray,
                                   track: DancerTrack,
                                   is_crowded: bool) -> float:
        """Penalty for implausible direction reversals in crowded zones."""
        if not (is_crowded and track.is_established):
            return 0.0

        implied_vel = det_centroid - last_known_pos
        implied_speed = np.linalg.norm(implied_vel)
        curr_speed = np.linalg.norm(velocity)
        if not (curr_speed > self.distance_threshold * 0.05
                and implied_speed > self.distance_threshold * 0.05):
            return 0.0

        cos_sim = float(np.dot(velocity, implied_vel) / (curr_speed * implied_speed))
        if cos_sim < 0:
            return abs(cos_sim) * self.distance_threshold * 2.0
        if cos_sim < 0.5:
            return (0.5 - cos_sim) * self.distance_threshold * 0.5
        return 0.0

    def _combine_assignment_cost(self, pos_cost: float, kpt_cost: float,
                                 size_cost: float, traj_cost,
                                 is_crowded: bool, n_both: int) -> float:
        """Blend position, pose, size, and trajectory into one assignment cost."""
        if is_crowded:
            if n_both >= 5:
                if traj_cost is not None:
                    tw = TRACKER_TRAJECTORY_WEIGHT
                    pw = TRACKER_CLOSE_POS_WEIGHT * (1.0 - tw)
                    kw = TRACKER_CLOSE_KPT_WEIGHT * (1.0 - tw)
                    sw = TRACKER_CLOSE_SIZE_WEIGHT * (1.0 - tw)
                    return (pw * pos_cost
                            + kw * kpt_cost
                            + sw * size_cost
                            + tw * traj_cost)
                return (TRACKER_CLOSE_POS_WEIGHT * pos_cost
                        + TRACKER_CLOSE_KPT_WEIGHT * kpt_cost
                        + TRACKER_CLOSE_SIZE_WEIGHT * size_cost)
            if n_both >= 3:
                base_cost = (0.25 * pos_cost + 0.50 * kpt_cost
                             + 0.25 * size_cost)
                if traj_cost is not None:
                    base_cost = 0.6 * base_cost + 0.4 * traj_cost
                return base_cost * 1.3
            return (0.85 * pos_cost + 0.15 * size_cost) * 1.5

        if n_both >= 3:
            return 0.40 * pos_cost + 0.45 * kpt_cost + 0.15 * size_cost
        return 0.85 * pos_cost + 0.15 * size_cost

    def _compute_dynamic_match_threshold(self, track: DancerTrack) -> float:
        """Dynamic acceptance threshold for a candidate detection-track match."""
        track_speed = track.get_speed()
        time_bonus = min(
            track.time_since_update * self.distance_threshold * 0.04,
            self.distance_threshold * 0.3,
        )
        return float(self.distance_threshold + track_speed * 1.0 + time_bonus)

    def _is_suspicious_merge_candidate(self, track: DancerTrack, bbox,
                                       det_centroid: np.ndarray) -> bool:
        """Whether a matched detection should be rejected as a merged body."""
        if not (track.is_established and len(track.bbox_area_history) >= 5):
            return False
        det_area = bbox[2] * bbox[3]
        if det_area <= track.avg_bbox_area * TRACKER_MERGE_SIZE_RATIO:
            return False

        close_dist = self.distance_threshold * TRACKER_CLOSE_PROXIMITY_RATIO
        nearby = sum(
            1 for other_track in self.tracks
            if np.linalg.norm(det_centroid - other_track.get_centroid()) < close_dist
        )
        if nearby < 2:
            return False

        self.logger.log("ANTI_MERGE", {
            "track_id": track.track_id,
            "det_area": round(det_area),
            "avg_area": round(track.avg_bbox_area),
            "ratio": TRACKER_MERGE_SIZE_RATIO,
        })
        if TRACKER_DEBUG:
            print(f"[TRACKER] Anti-merge rejected: "
                  f"det_area={det_area:.0f} > "
                  f"{TRACKER_MERGE_SIZE_RATIO}x "
                  f"avg={track.avg_bbox_area:.0f}")
        return True
    
    def _compute_cost_matrix(self, detections, tracks):
        """Compute assignment cost matrix.
        
        Cost blends four signals to disambiguate when centroids alone
        are ambiguous (= ID-swap scenario):
        
        1. **Position cost** — weighted blend of distances to predicted,
           velocity-adjusted, and last-known positions.
        2. **Keypoint-shape cost** — mean distance between **centroid-
           normalised** co-visible keypoints (position-independent body
           shape comparison).
        3. **Bbox-size cost** — absolute height difference.
        4. **Separation penalty** — discourages cross-matching bodies
           that have historically been far apart (known-separate).
        
        When a detection is near multiple tracks (close-dancing), the
        cost weights shift to favour skeleton shape over position,
        because position is ambiguous but body shape differs.
        """
        if len(detections) == 0 or len(tracks) == 0:
            return np.empty((len(detections), len(tracks)))
        
        cost_matrix = np.zeros((len(detections), len(tracks)))
        close_dist = self.distance_threshold * TRACKER_CLOSE_PROXIMITY_RATIO
        
        for d, (kpts, conf, bbox) in enumerate(detections):
            det_centroid = self._compute_centroid(kpts, conf, bbox)
            det_height = bbox[3]  # bbox (x, y, w, h)
            
            # Pre-compute detection's normalised skeleton
            mask_det = conf > KEYPOINT_CONFIDENCE
            det_centroid_kpt = det_centroid  # already weighted centroid
            det_kpts_norm = kpts - det_centroid_kpt  # (17,2) relative
            
            # Check if this detection is in a crowded zone
            # (near multiple tracks → must rely on shape)
            is_crowded = self._is_detection_in_crowded_zone(
                det_centroid, tracks, close_dist)
            
            for t, track in enumerate(tracks):
                if not self._mahalanobis_gate_allows(d, det_centroid, track):
                    cost_matrix[d, t] = 1e6
                    continue

                pos_cost, last_known_pos, velocity = self._compute_position_cost(
                    det_centroid, track)
                kpt_cost, n_both = self._compute_keypoint_cost(
                    det_kpts_norm, mask_det, track)
                
                # --- 3. Bbox-size cost ---
                trk_height = track.bbox[3]
                size_cost = abs(det_height - trk_height)
                
                sep_penalty = self._compute_separation_penalty(
                    det_centroid, track, tracks)
                dir_penalty = self._compute_direction_penalty(
                    det_centroid, last_known_pos, velocity, track, is_crowded)
                
                # --- Combined cost with adaptive weights ---
                # Phase 2: trajectory cost (pose history match)
                traj_cost = None
                if is_crowded and len(track._pose_history) >= 3:
                    det_aspect = float(bbox[2]) / max(1.0, float(bbox[3]))
                    traj_cost = track.trajectory_cost(
                        det_kpts_norm, mask_det, det_aspect)

                base_cost = self._combine_assignment_cost(
                    pos_cost, kpt_cost, size_cost, traj_cost,
                    is_crowded, n_both)
                
                cost_matrix[d, t] = base_cost + sep_penalty + dir_penalty
        
        return cost_matrix

    # ------------------------------------------------------------------
    # Post-cascade occlusion swap (Phase 1c)
    # ------------------------------------------------------------------
    def _check_occlusion_cascade_swaps(self, detections, frame_ctx,
                                        matched_det, matched_trk,
                                        matched_pairs_log):
        """Swap assignments when an exiting established track steals a
        detection from a nearby tentative track during a detection merger.

        **Pattern detected** (confirmed at frame 366, slot 6):
        An established track E is exiting while a tentative track T is
        stationary nearby.  YOLO merges them into one detection.  Pass 1
        gives the det to E (established-first priority).  T gets nothing
        and slowly dies → ID swap.

        **Fix**: after both cascade passes, check if any unmatched tentative
        track T is near a detection that an established track E claimed.
        If E is moving significantly faster than T (exiting behaviour) and
        both are in close proximity, swap: give the det to T, un-assign E.
        E will age out normally (it was leaving anyway).
        """
        if len(detections) >= len(self.tracks):
            return  # No detection dropout → no merger → nothing to swap

        close_dist = self.distance_threshold * TRACKER_CLOSE_PROXIMITY_RATIO

        # Find unmatched tentative tracks
        unmatched_tent = [
            idx for idx, t in enumerate(self.tracks)
            if idx not in matched_trk and not t.is_established
        ]
        if not unmatched_tent:
            return

        # Collect established matches from pending_updates
        est_matches = [
            (upd_i, update.trk_idx, update.det_idx)
            for upd_i, update in enumerate(frame_ctx.pending_updates)
            if self.tracks[update.trk_idx].is_established
        ]
        if not est_matches:
            return

        swaps_to_apply = []  # (tent_trk_idx, upd_i, est_trk_idx, det_idx)
        claimed_pending = set()

        for tent_idx in unmatched_tent:
            tent_track = self.tracks[tent_idx]
            tent_pos = tent_track.get_centroid()
            tent_speed = tent_track.get_speed()

            best_swap = None
            best_closeness = float('inf')

            for upd_i, est_trk_idx, det_idx in est_matches:
                if upd_i in claimed_pending:
                    continue

                est_track = self.tracks[est_trk_idx]
                est_pos = est_track.get_centroid()
                est_speed = est_track.get_speed()

                # Criterion 1: est and tent are in close proximity (overlapping)
                if np.linalg.norm(est_pos - tent_pos) > close_dist:
                    continue

                # Criterion 2: est is moving significantly faster than tent
                # (passing through / exiting)
                if est_speed < tent_speed * 1.5 + 3.0:
                    continue

                # Criterion 3: tentative track is near the claimed detection
                det_centroid = self._compute_centroid(*detections[det_idx])
                tent_det_dist = float(np.linalg.norm(tent_pos - det_centroid))
                if tent_det_dist > close_dist:
                    continue

                if tent_det_dist < best_closeness:
                    best_closeness = tent_det_dist
                    best_swap = (upd_i, est_trk_idx, det_idx)

            if best_swap is not None:
                swaps_to_apply.append((tent_idx, *best_swap))
                claimed_pending.add(best_swap[0])

        # Apply swaps
        for tent_idx, upd_i, est_trk_idx, det_idx in swaps_to_apply:
            tent_track = self.tracks[tent_idx]
            est_track = self.tracks[est_trk_idx]
            kpts, conf, bbox = detections[det_idx]

            # Register the established track for cascade suppression
            self._cascade_suppressed[est_track.track_id] = TRACKER_CASCADE_SUPPRESSION_FRAMES

            self.logger.log("CASCADE_OCCLUSION_SWAP", {
                "from_est_id": est_track.track_id,
                "to_tent_id": tent_track.track_id,
                "det": det_idx,
                "est_speed": round(est_track.get_speed(), 1),
                "tent_speed": round(tent_track.get_speed(), 1),
                "suppression_frames": TRACKER_CASCADE_SUPPRESSION_FRAMES,
            })
            if TRACKER_DEBUG:
                print(f"[TRACKER] Cascade occlusion swap: det {det_idx} "
                      f"from est #{est_track.track_id} "
                      f"(speed={est_track.get_speed():.1f}) "
                      f"→ tent #{tent_track.track_id} "
                      f"(speed={tent_track.get_speed():.1f})")

            # Swap the pending update: tentative gets the detection
            frame_ctx.pending_updates[upd_i] = PendingTrackUpdate(
                trk_idx=tent_idx,
                det_idx=det_idx,
                kpts=kpts,
                conf=conf,
                bbox=bbox,
            )

            # Mark tentative for post-update velocity clamp (applied after
            # deferred .update() so the clamp isn't undone by Kalman gain).
            frame_ctx.post_update_clamp_indices.add(tent_idx)

            # Update matched sets
            matched_trk.discard(est_trk_idx)
            matched_trk.add(tent_idx)

            # Update log entries
            matched_pairs_log[:] = [
                p for p in matched_pairs_log
                if not (p["track_id"] == est_track.track_id
                        and p["det"] == det_idx)
            ]
            det_centroid = self._compute_centroid(kpts, conf, bbox)
            tent_dist = float(np.linalg.norm(
                tent_track.get_centroid() - det_centroid))
            matched_pairs_log.append({
                "det": det_idx,
                "track_id": tent_track.track_id,
                "cost": round(tent_dist * 0.4, 1),  # approx pos_cost
            })

    # ------------------------------------------------------------------
    # Post-merge direction swap (Phase 1d)
    # ------------------------------------------------------------------
    def _check_merge_direction_swaps(self, detections, frame_ctx,
                                      matched_det, matched_trk,
                                      matched_pairs_log):
        """Swap assignments when tracks emerge from a merge on the wrong side.

        **Pattern detected** (confirmed at frames 297-305, slot 6):
        Two established tracks A (going LEFT) and B (going RIGHT) converge
        and merge into a single YOLO detection.  B grabs the merged
        detection; A goes OCCLUDED.  A's Kalman prediction drifts
        rightward (constant-acceleration model).  When they separate,
        A matches the RIGHT detection (B's body) and B matches the LEFT
        detection (A's body) → silent ID swap.

        **Fix**: After cascade passes, check pairs of tracks in
        pending_updates.  If at least one was occluded (merge exit), they
        have opposite dominant velocity directions, their matched
        detections are close (just separated), and the assignment is
        direction-reversed, swap them back.
        """
        if len(frame_ctx.pending_updates) < 2:
            return

        close_dist = self.distance_threshold * TRACKER_CLOSE_PROXIMITY_RATIO

        swaps = []
        used = set()

        for i in range(len(frame_ctx.pending_updates)):
            if i in used:
                continue
            update_i = frame_ctx.pending_updates[i]
            trk_i = update_i.trk_idx
            det_i = update_i.det_idx
            kpts_i = update_i.kpts
            conf_i = update_i.conf
            bbox_i = update_i.bbox
            track_i = self.tracks[trk_i]
            dir_i = track_i.get_dominant_vx_direction()
            if dir_i == 0:
                continue
            det_pos_i = self._compute_centroid(kpts_i, conf_i, bbox_i)

            for j in range(i + 1, len(frame_ctx.pending_updates)):
                if j in used:
                    continue
                update_j = frame_ctx.pending_updates[j]
                trk_j = update_j.trk_idx
                det_j = update_j.det_idx
                kpts_j = update_j.kpts
                conf_j = update_j.conf
                bbox_j = update_j.bbox
                track_j = self.tracks[trk_j]

                # Criterion 1: shared recent merge or occlusion context
                if not self._tracks_share_recent_merge_context(track_i, track_j):
                    continue

                dir_j = track_j.get_dominant_vx_direction()
                if dir_j == 0:
                    continue

                # Criterion 2: opposite dominant velocity directions
                if dir_i == dir_j:
                    continue

                det_pos_j = self._compute_centroid(kpts_j, conf_j, bbox_j)

                # Criterion 3: detections are close (just separated from merge)
                det_gap = abs(det_pos_i[0] - det_pos_j[0])
                if det_gap > close_dist:
                    continue

                # Criterion 4: assignment is direction-reversed
                # LEFT-going track should match the LEFT detection, etc.
                if det_pos_i[0] < det_pos_j[0]:
                    # det_i is LEFT, det_j is RIGHT
                    left_dir, right_dir = dir_i, dir_j
                else:
                    left_dir, right_dir = dir_j, dir_i

                # Reversed = LEFT det assigned to RIGHT-going track (+1)
                #            AND RIGHT det assigned to LEFT-going track (-1)
                if left_dir == +1 and right_dir == -1:
                    swaps.append((i, j))
                    used.add(i)
                    used.add(j)
                    break

        # Apply swaps
        for i, j in swaps:
            update_i = frame_ctx.pending_updates[i]
            update_j = frame_ctx.pending_updates[j]
            trk_i, det_i, kpts_i, conf_i, bbox_i = (
                update_i.trk_idx, update_i.det_idx, update_i.kpts,
                update_i.conf, update_i.bbox)
            trk_j, det_j, kpts_j, conf_j, bbox_j = (
                update_j.trk_idx, update_j.det_idx, update_j.kpts,
                update_j.conf, update_j.bbox)
            track_i = self.tracks[trk_i]
            track_j = self.tracks[trk_j]

            # Swap: track i gets detection j, track j gets detection i
            frame_ctx.pending_updates[i] = PendingTrackUpdate(
                trk_idx=trk_i,
                det_idx=det_j,
                kpts=kpts_j,
                conf=conf_j,
                bbox=bbox_j,
            )
            frame_ctx.pending_updates[j] = PendingTrackUpdate(
                trk_idx=trk_j,
                det_idx=det_i,
                kpts=kpts_i,
                conf=conf_i,
                bbox=bbox_i,
            )

            # Mark both for post-update velocity clamp (applied after
            # deferred .update() so the clamp isn't undone by Kalman gain).
            frame_ctx.post_update_clamp_indices.add(trk_i)
            frame_ctx.post_update_clamp_indices.add(trk_j)

            self.logger.log("MERGE_DIRECTION_SWAP", {
                "track_a_id": track_i.track_id,
                "track_b_id": track_j.track_id,
                "dir_a": int(track_i.get_dominant_vx_direction()),
                "dir_b": int(track_j.get_dominant_vx_direction()),
                "merge_episode_a": track_i._merge_episode_id,
                "merge_episode_b": track_j._merge_episode_id,
            })
            if TRACKER_DEBUG:
                print(f"[TRACKER] Merge direction swap: "
                      f"#{track_i.track_id} (dir={track_i.get_dominant_vx_direction():+d}) "
                      f"↔ #{track_j.track_id} (dir={track_j.get_dominant_vx_direction():+d})")

            # Update matched_pairs_log
            matched_pairs_log[:] = [
                p for p in matched_pairs_log
                if p["track_id"] not in (track_i.track_id, track_j.track_id)
            ]
            matched_pairs_log.append({
                "det": det_j, "track_id": track_i.track_id, "cost": -1.0,
            })
            matched_pairs_log.append({
                "det": det_i, "track_id": track_j.track_id, "cost": -1.0,
            })

    # ------------------------------------------------------------------
    # Assignment pass (used by cascaded matching)
    # ------------------------------------------------------------------
    def _run_assignment_pass(self, detections, det_indices, trk_indices,
                             matched_det, matched_trk, matched_pairs_log,
                             frame_ctx: FrameUpdateContext | None = None,
                             defer_updates: bool = False):
        """Run one pass of Hungarian assignment on detection/track subsets.

        Computes the cost matrix for the given subsets, runs
        ``linear_sum_assignment``, applies threshold + anti-merge checks,
        and updates the matched sets in-place.

        Args:
            detections:       Full detection list (indexed by det_indices).
            det_indices:      List of detection indices to consider.
            trk_indices:      List of self.tracks indices to consider.
            matched_det:      Set[int] — already-matched detection indices
                              (updated in-place).
            matched_trk:      Set[int] — already-matched track indices
                              (updated in-place).
            matched_pairs_log: List — (det, track_id, cost) triples for
                              the FRAME_SUMMARY event (updated in-place).
            frame_ctx:       Optional frame-scoped update context.
            defer_updates:   When True, store committed updates in the
                              frame context instead of applying them
                              immediately. This allows post-cascade swap
                              checks before Kalman state changes.
        """
        if not det_indices or not trk_indices:
            return

        subset_tracks = [self.tracks[i] for i in trk_indices]
        subset_dets = [detections[d] for d in det_indices]

        cost_matrix = self._compute_cost_matrix(subset_dets, subset_tracks)
        if cost_matrix.size == 0:
            return

        # Sanitize: NaN / inf entries crash linear_sum_assignment
        if not np.all(np.isfinite(cost_matrix)):
            cost_matrix = np.nan_to_num(
                cost_matrix, nan=1e6, posinf=1e6, neginf=1e6
            )

        row_idx, col_idx = linear_sum_assignment(cost_matrix)

        for row, col in zip(row_idx, col_idx):
            actual_det = det_indices[row]
            actual_trk = trk_indices[col]
            track = self.tracks[actual_trk]
            dynamic_thresh = self._compute_dynamic_match_threshold(track)

            if cost_matrix[row, col] < dynamic_thresh:
                kpts, conf, bbox = detections[actual_det]
                det_centroid = self._compute_centroid(kpts, conf, bbox)
                if self._is_suspicious_merge_candidate(track, bbox, det_centroid):
                    matched_det.add(actual_det)
                    continue

                cost_val = round(float(cost_matrix[row, col]), 1)
                self._commit_match(
                    trk_idx=actual_trk,
                    det_idx=actual_det,
                    kpts=kpts,
                    conf=conf,
                    bbox=bbox,
                    matched_det=matched_det,
                    matched_trk=matched_trk,
                    matched_pairs_log=matched_pairs_log,
                    frame_ctx=frame_ctx,
                    defer_update=defer_updates,
                    cost_val=cost_val,
                    log_event="MATCH",
                    log_data={
                        "threshold": round(dynamic_thresh, 1),
                        "is_established": track.is_established,
                    },
                )
            else:
                self.logger.log("MATCH_REJECTED", {
                    "det": actual_det,
                    "track_id": track.track_id,
                    "cost": round(float(cost_matrix[row, col]), 1),
                    "threshold": round(dynamic_thresh, 1),
                    "time_since_update": track.time_since_update,
                })
                if TRACKER_DEBUG:
                    print(f"[TRACKER] Match rejected: "
                          f"cost={cost_matrix[row, col]:.1f} > "
                          f"thresh={dynamic_thresh:.1f} "
                          f"(t_miss={track.time_since_update})")

    def _commit_match(self, trk_idx, det_idx, kpts, conf, bbox,
                      matched_det, matched_trk, matched_pairs_log,
                      frame_ctx: FrameUpdateContext | None = None,
                      defer_update: bool = False, cost_val=None,
                      log_event="MATCH", log_data=None):
        """Commit a detection→track assignment through one shared path."""
        track = self.tracks[trk_idx]

        matched_det.add(det_idx)
        matched_trk.add(trk_idx)

        if defer_update:
            if frame_ctx is None:
                raise ValueError("frame_ctx is required when defer_update=True")
            frame_ctx.pending_updates.append(PendingTrackUpdate(
                trk_idx=trk_idx,
                det_idx=det_idx,
                kpts=kpts,
                conf=conf,
                bbox=bbox,
            ))
        else:
            self._apply_track_update(trk_idx, kpts, conf, bbox, frame_ctx)

        event_data = {
            "det": det_idx,
            "track_id": track.track_id,
        }
        if cost_val is not None:
            event_data["cost"] = cost_val
        if log_data:
            event_data.update(log_data)
        self.logger.log(log_event, event_data)

        matched_pairs_log.append({
            "det": det_idx,
            "track_id": track.track_id,
            "cost": cost_val if cost_val is not None else -1.0,
        })

    def _apply_track_update(self, trk_idx, kpts, conf, bbox,
                            frame_ctx: FrameUpdateContext | None):
        """Apply an accepted match and stamp frame-level continuity metadata."""
        track = self.tracks[trk_idx]
        merge_frame = frame_ctx.merge_frame if frame_ctx is not None else False
        track.update(kpts, conf, bbox, merge_frame=merge_frame)
        track.note_match_event(self.frame_count, merge_frame=merge_frame)

    def _tracks_share_recent_merge_context(self, track_a: DancerTrack,
                                           track_b: DancerTrack) -> bool:
        """Check whether two tracks likely emerged from the same recent merge."""
        window = max(4, min(TRACKER_POSE_HISTORY_DEPTH, 12))
        if not track_a.has_recent_merge_context(self.frame_count, window):
            return False
        if not track_b.has_recent_merge_context(self.frame_count, window):
            return False

        merge_a = track_a._last_merge_frame
        merge_b = track_b._last_merge_frame
        if merge_a >= 0 and merge_b >= 0:
            return abs(merge_a - merge_b) <= window

        occ_a = track_a._last_occluded_frame
        occ_b = track_b._last_occluded_frame
        if occ_a >= 0 and occ_b >= 0:
            return abs(occ_a - occ_b) <= window

        return True

    # ------------------------------------------------------------------
    # Dormant pool re-identification
    # ------------------------------------------------------------------
    def _try_resurrect(self, keypoints, confidence, bbox, det_centroid,
                       frame_ctx: FrameUpdateContext) -> 'DancerTrack | None':
        """Check the dormant pool for a matching snapshot.

        Criteria (AND logic):
        1. Position: detection centroid within gate of the dormant
           snapshot's **velocity-projected** position.  Gate is widened
           for recently-dormant or previously-occluded tracks.
        2. Size: bbox height within 40 % of the snapshot's bbox height.
        3. Shape (when >= 3 co-visible keypoints): mean keypoint distance
           < gate * 0.5.

        Resurrected tracks are **immediately confirmed** (hits set to
        ``min_hits``) so they appear without the usual warm-up delay.

        Returns:
            A resurrected ``DancerTrack`` or ``None``.
        """
        if not self._dormant:
            return None

        det_height = float(bbox[3])
        best_idx = None
        best_score = float('inf')

        for i, snap in enumerate(self._dormant):
            # --- Position gate (velocity-projected) ---
            projected = snap.projected_position()
            dist = np.linalg.norm(det_centroid - projected)

            # Widen gate for recently-dormant or occluded tracks:
            base_gate = self.distance_threshold
            multiplier = 1.0
            if snap.age < 30:
                multiplier += 0.5
            if snap.was_occluded:
                multiplier += 0.5
            if not snap.exited_from_edge:
                multiplier += (TRACKER_CENTER_EXIT_RESURRECT_BOOST - 1.0)
            
            # Cap maximum distance expansion so they don't jump across the entire stage
            gate = base_gate * min(multiplier, 2.5)

            if dist > gate:
                continue
                
            # Prevent edge entrants from instantly stealing tracks that disappeared in the center
            is_edge_det = self._is_near_edge(float(det_centroid[0]))
            if not snap.exited_from_edge and is_edge_det:
                # If they walked to the edge while occluded, allow it, but require MUCH stricter distance.
                if dist > base_gate:
                    continue

            # --- Size gate ---
            # Center-exited/occluded: wider tolerance (body may look
            # different after partial occlusion)
            if snap.bbox_height > 0:
                if not snap.exited_from_edge or snap.was_occluded:
                    size_lo, size_hi = 0.5, 1.5  # ±50 %
                else:
                    size_lo, size_hi = 0.6, 1.4  # ±40 %
                height_ratio = det_height / snap.bbox_height
                if height_ratio < size_lo or height_ratio > size_hi:
                    continue

            # --- Keypoint-shape gate ---
            # IMPORTANT: Shape check MUST use the base_gate, not the expanded projection gate!
            # The expanded gate is only to catch rapid position drift. Shape must remain heavily 
            # restricted to prevent identity theft by differently-posed dancers.
            kpt_gate_thresh = base_gate * (0.8 if not snap.exited_from_edge else 0.5)
            
            mask_det = confidence > KEYPOINT_CONFIDENCE
            mask_snap = snap.confidence > KEYPOINT_CONFIDENCE
            both = mask_det & mask_snap
            n_both = int(np.sum(both))
            if n_both >= 3:
                kpt_dist = float(np.mean(
                    np.linalg.norm(keypoints[both] - snap.keypoints[both], axis=1)
                ))
                if kpt_dist > kpt_gate_thresh:
                    continue
                score = 0.5 * dist + 0.5 * kpt_dist
            else:
                score = dist

            # Prefer center-exited dormants (they're almost certainly
            # still in frame and just occluded) — halve the score
            if not snap.exited_from_edge:
                score *= 0.5

            if score < best_score:
                best_score = score
                best_idx = i

        if best_idx is None:
            return None

        snap = self._dormant.pop(best_idx)

        # Create fresh track with the new detection data but the OLD id.
        # Temporarily set the class counter back so the constructor uses
        # the dormant ID directly, avoiding wasted ID numbers.
        saved_counter = DancerTrack._id_counter
        DancerTrack._id_counter = snap.track_id - 1
        new_track = DancerTrack(keypoints, confidence, bbox, self.smoothing_depth)
        # Restore counter — only advance past old value if needed
        DancerTrack._id_counter = max(saved_counter, new_track.track_id)
        new_track.restore_continuity(snap)
        new_track.note_match_event(self.frame_count, merge_frame=frame_ctx.merge_frame)
        new_track.hits = max(self.min_hits, snap.hits)  # immediately confirmed

        self.logger.log("RESURRECT", {
            "track_id": snap.track_id,
            "dormant_age": snap.age,
            "score": round(best_score, 1),
            "was_occluded": snap.was_occluded,
            "edge_exit": snap.exited_from_edge,
        })
        if TRACKER_DEBUG:
            print(f"[TRACKER] Resurrected track #{snap.track_id} from dormant "
                  f"(dormant_age={snap.age}, score={best_score:.1f}, "
                  f"occluded={snap.was_occluded}, "
                  f"edge_exit={snap.exited_from_edge})")

        return new_track

    def update(self, detections, frame_number: int | None = None):
        """
        Update tracker with new detections.
        
        Args:
            detections: List of (keypoints, confidence, bbox) tuples
            frame_number: External frame number (from overlay) so that
                log entries match the frame shown on screen.  When None,
                the tracker increments its own counter (legacy path).
        
        Returns:
            List of DancerTrack objects for confirmed tracks
        """
        frame_ctx = self._begin_frame_update(frame_number)
        self._predict_tracks_for_frame(detections, frame_ctx)

        matched_det = set()
        matched_trk = set()
        matched_pairs_log = []

        self._run_matching_phase(
            detections,
            matched_det,
            matched_trk,
            matched_pairs_log,
            frame_ctx,
        )
        self._resolve_unmatched_detections(
            detections,
            matched_det,
            matched_trk,
            matched_pairs_log,
            frame_ctx,
        )
        self._apply_occlusion_aging(matched_trk, frame_ctx)
        self._finalize_track_lifecycle()

        confirmed = self._collect_confirmed_tracks()
        self._log_frame_summary(detections, matched_pairs_log)
        return confirmed

    def _begin_frame_update(self, frame_number: int | None = None):
        """Prepare frame counters and suppression state for a new update."""
        if frame_number is not None:
            self.frame_count = frame_number
        else:
            self.frame_count += 1
        self.logger.set_frame(self.frame_count)

        # Tick down cascade suppression counters
        expired_sup = [tid for tid, ttl in self._cascade_suppressed.items()
                       if ttl <= 1]
        for tid in expired_sup:
            del self._cascade_suppressed[tid]
        for tid in list(self._cascade_suppressed):
            self._cascade_suppressed[tid] -= 1
        # Also remove stale entries for tracks no longer active
        active_ids = {t.track_id for t in self.tracks}
        stale_sup = [tid for tid in self._cascade_suppressed
                     if tid not in active_ids]
        for tid in stale_sup:
            del self._cascade_suppressed[tid]
        if self._cascade_suppressed:
            self.logger.log("CASCADE_SUPPRESSED", {
                "suppressed": {tid: ttl for tid, ttl in self._cascade_suppressed.items()},
            })
        return FrameUpdateContext()

    def _predict_tracks_for_frame(self, detections, frame_ctx: FrameUpdateContext):
        """Run track prediction and compute frame-scoped merge state."""
        # Predict
        for track in self.tracks:
            track.predict()

        # Merge-frame detection: fewer detections than active tracks
        # means YOLO merged bodies → skip pose history recording.
        frame_ctx.merge_frame = len(detections) < len(self.tracks)

    def _run_matching_phase(self, detections, matched_det, matched_trk,
                            matched_pairs_log, frame_ctx: FrameUpdateContext):
        """Run the primary track↔detection assignment phase."""
        all_det_indices = list(range(len(detections)))

        # Match — cascaded assignment (Phase 1)
        # Pass 1: established tracks get first pick of all detections.
        # Pass 2: tentative tracks match remaining detections.
        # This prevents a newly-spawned tentative track from stealing a
        # detection that belongs to an established dancer.
        # matched_pairs_log is emitted in FRAME_SUMMARY.

        if TRACKER_CASCADED_MATCHING:
            # Defer .update() calls so we can run the occlusion swap
            # check before committing Kalman state changes.
            # --- Pass 1: established tracks ---
            # Exclude tracks under cascade suppression — they lost a
            # swap and should not steal the detection back in Pass 1.
            est_indices = [i for i, t in enumerate(self.tracks)
                           if t.is_established
                           and t.track_id not in self._cascade_suppressed]
            self._run_assignment_pass(
                detections, all_det_indices, est_indices,
                matched_det, matched_trk, matched_pairs_log,
                frame_ctx=frame_ctx, defer_updates=True)

            # --- Pass 2: tentative tracks + suppressed established ---
            # Suppressed established tracks participate in Pass 2 alongside
            # tentative tracks so they can still match if detections remain.
            tent_indices = [i for i, t in enumerate(self.tracks)
                            if (not t.is_established
                                or t.track_id in self._cascade_suppressed)
                            and i not in matched_trk]
            remaining_dets = [d for d in all_det_indices
                              if d not in matched_det]
            self._run_assignment_pass(
                detections, remaining_dets, tent_indices,
                matched_det, matched_trk, matched_pairs_log,
                frame_ctx=frame_ctx, defer_updates=True)

            # --- Post-cascade occlusion swap check (Phase 1c) ---
            if TRACKER_CASCADE_OCCLUSION_SWAP:
                self._check_occlusion_cascade_swaps(
                    detections, frame_ctx,
                    matched_det, matched_trk, matched_pairs_log)

            # --- Post-merge direction swap check (Phase 1d) ---
            if TRACKER_MERGE_DIRECTION_SWAP:
                self._check_merge_direction_swaps(
                    detections, frame_ctx,
                    matched_det, matched_trk, matched_pairs_log)

            # Apply all deferred updates
            for update in frame_ctx.pending_updates:
                self._apply_track_update(
                    update.trk_idx,
                    update.kpts,
                    update.conf,
                    update.bbox,
                    frame_ctx,
                )

            # Clamp velocity & acceleration on swapped tracks AFTER the
            # Kalman update.  If done before, the Kalman gain recomputes
            # velocity from the innovation (predicted vs measured) and
            # undoes the clamp.
            for trk_idx in frame_ctx.post_update_clamp_indices:
                self.tracks[trk_idx].kf.x[2:] = 0.0
        else:
            # Single-pass matching (legacy behaviour)
            self._run_assignment_pass(
                detections, all_det_indices,
                list(range(len(self.tracks))),
                matched_det, matched_trk, matched_pairs_log,
                frame_ctx=frame_ctx)

    def _resolve_unmatched_detections(self, detections, matched_det,
                                      matched_trk, matched_pairs_log,
                                      frame_ctx: FrameUpdateContext):
        """Handle unmatched detections via create/resurrect/late-match rules."""
        # Create new tracks — use separate, tighter gate for new-track
        # creation vs. matching so two close dancers can coexist.
        for d, (kpts, conf, bbox) in enumerate(detections):
            if d not in matched_det:
                det_centroid = self._compute_centroid(kpts, conf, bbox)

                closest = self._find_closest_track(det_centroid)

                # Gate 1: far enough from every track → new person (or resurrect)
                # Center detections need a tighter gate to avoid ghost splits
                creation_gate, is_edge_det = self._get_creation_gate(det_centroid)
                if closest.min_dist > creation_gate:
                    resurrected = self._try_resurrect(
                        kpts, conf, bbox, det_centroid, frame_ctx)
                    if resurrected is not None:
                        self.tracks.append(resurrected)
                    else:
                        self._create_new_track(
                            kpts, conf, bbox, det_centroid,
                            closest.min_dist, creation_gate, is_edge_det, frame_ctx)
                    continue

                if (closest.track is not None
                        and closest.idx is not None
                        and closest.idx not in matched_trk
                        and self._try_force_update_unmatched_detection(
                            det_idx=d,
                            kpts=kpts,
                            conf=conf,
                            bbox=bbox,
                            det_centroid=det_centroid,
                            closest=closest,
                            matched_det=matched_det,
                            matched_trk=matched_trk,
                            matched_pairs_log=matched_pairs_log,
                            frame_ctx=frame_ctx)):
                    continue

                if closest.track is not None and closest.min_dist < self.duplicate_distance:
                    # Very close to an already-matched track → duplicate, drop
                    self.logger.log("DUPLICATE_IGNORED", {
                        "near_track_id": closest.track.track_id,
                        "dist": round(closest.min_dist, 1),
                    })
                    if TRACKER_DEBUG:
                        print(f"[TRACKER] Ignoring duplicate near track "
                              f"#{closest.track.track_id}: dist={closest.min_dist:.1f}")
                    continue

                if not self._try_fallback_update_unmatched_detection(
                        det_idx=d,
                        kpts=kpts,
                        conf=conf,
                        bbox=bbox,
                        det_centroid=det_centroid,
                        closest=closest,
                        matched_det=matched_det,
                        matched_trk=matched_trk,
                        matched_pairs_log=matched_pairs_log,
                        frame_ctx=frame_ctx):
                    self.logger.log("AMBIGUOUS_IGNORED", {
                        "near_track_id": closest.track.track_id if closest.track else None,
                        "dist": round(closest.min_dist, 1),
                    })
                    if TRACKER_DEBUG:
                        print(f"[TRACKER] Ignoring ambiguous det near track "
                              f"#{closest.track.track_id if closest.track else 'None'}: dist={closest.min_dist:.1f}")

    def _find_closest_track(self, det_centroid: np.ndarray) -> ClosestTrackResult:
        """Return the active track whose last known position is nearest a detection."""
        min_dist = float('inf')
        closest_track = None
        closest_track_idx = None
        for idx, track in enumerate(self.tracks):
            last_pos = track.get_last_known_position()
            dist = float(np.linalg.norm(det_centroid - last_pos))
            if dist < min_dist:
                min_dist = dist
                closest_track = track
                closest_track_idx = idx
        return ClosestTrackResult(min_dist=min_dist,
                                  track=closest_track,
                                  idx=closest_track_idx)

    def _select_force_update_target(self, det_centroid: np.ndarray,
                                    matched_trk, closest: ClosestTrackResult):
        """Prefer an unmatched occluded track over the nearest unmatched track."""
        if closest.track is None or closest.idx is None:
            return None, None

        best_unmatched = closest.track
        best_unmatched_idx = closest.idx
        for idx, track in enumerate(self.tracks):
            if idx in matched_trk:
                continue
            dist = float(np.linalg.norm(det_centroid - track.get_last_known_position()))
            if (dist < self.distance_threshold
                    and track._occluded
                    and not best_unmatched._occluded):
                best_unmatched = track
                best_unmatched_idx = idx
        return best_unmatched, best_unmatched_idx

    def _try_force_update_unmatched_detection(self, det_idx: int, kpts, conf, bbox,
                                              det_centroid: np.ndarray,
                                              closest: ClosestTrackResult,
                                              matched_det, matched_trk,
                                              matched_pairs_log,
                                              frame_ctx: FrameUpdateContext) -> bool:
        """Try the preferred unmatched-track force-update path."""
        best_unmatched, best_unmatched_idx = self._select_force_update_target(
            det_centroid, matched_trk, closest)
        if best_unmatched is None or best_unmatched_idx is None:
            return False

        if TRACKER_DEBUG:
            print(f"[TRACKER] Force update track #{best_unmatched.track_id}: "
                  f"dist={closest.min_dist:.1f} occluded={best_unmatched._occluded}")
        self._commit_match(
            trk_idx=best_unmatched_idx,
            det_idx=det_idx,
            kpts=kpts,
            conf=conf,
            bbox=bbox,
            matched_det=matched_det,
            matched_trk=matched_trk,
            matched_pairs_log=matched_pairs_log,
            frame_ctx=frame_ctx,
            cost_val=round(closest.min_dist, 1),
            log_event="FORCE_UPDATE",
            log_data={
                "dist": round(closest.min_dist, 1),
                "occluded": best_unmatched._occluded,
            },
        )
        return True

    def _try_fallback_update_unmatched_detection(self, det_idx: int, kpts, conf, bbox,
                                                 det_centroid: np.ndarray,
                                                 closest: ClosestTrackResult,
                                                 matched_det, matched_trk,
                                                 matched_pairs_log,
                                                 frame_ctx: FrameUpdateContext) -> bool:
        """Try the broader unmatched-track fallback update path."""
        for idx, track in enumerate(self.tracks):
            if idx in matched_trk:
                continue
            dist = float(np.linalg.norm(det_centroid - track.get_last_known_position()))
            if dist >= self.distance_threshold:
                continue
            if TRACKER_DEBUG:
                print(f"[TRACKER] Fallback update track #{track.track_id}: dist={dist:.1f}")
            self._commit_match(
                trk_idx=idx,
                det_idx=det_idx,
                kpts=kpts,
                conf=conf,
                bbox=bbox,
                matched_det=matched_det,
                matched_trk=matched_trk,
                matched_pairs_log=matched_pairs_log,
                frame_ctx=frame_ctx,
                cost_val=round(dist, 1),
                log_event="FALLBACK_UPDATE",
                log_data={
                    "dist": round(dist, 1),
                },
            )
            return True
        return False

    def _get_creation_gate(self, det_centroid: np.ndarray) -> tuple[int, bool]:
        """Return new-track creation gate and whether the detection is at an edge."""
        is_edge_det = self._is_near_edge(float(det_centroid[0]))
        creation_gate = self.new_track_min_distance
        if not is_edge_det:
            creation_gate = int(creation_gate * TRACKER_CENTER_NEW_TRACK_GATE_MULT)
        return creation_gate, is_edge_det

    def _create_new_track(self, kpts, conf, bbox, det_centroid,
                          min_dist: float, creation_gate: int,
                          is_edge_det: bool,
                          frame_ctx: FrameUpdateContext):
        """Create and register a new active track for an unmatched detection."""
        new_id = DancerTrack._id_counter + 1
        self.logger.log("NEW_TRACK", {
            "track_id": new_id,
            "position": [round(float(det_centroid[0]), 1), round(float(det_centroid[1]), 1)],
            "min_dist": round(min_dist, 1),
            "gate": creation_gate,
            "is_edge": is_edge_det,
        })
        if TRACKER_DEBUG:
            print(f"[TRACKER] New track "
                  f"#{new_id}: "
                  f"min_dist={min_dist:.1f} > "
                  f"gate={creation_gate}")

        new_track = DancerTrack(kpts, conf, bbox, self.smoothing_depth)
        new_track.note_match_event(
            self.frame_count,
            merge_frame=frame_ctx.merge_frame,
        )
        self.tracks.append(new_track)

    def _apply_occlusion_aging(self, matched_trk,
                               frame_ctx: FrameUpdateContext):
        """Apply occlusion-aware aging to unmatched active tracks."""
        # ---- Occlusion-aware aging ----
        # For unmatched tracks whose predicted position is near a
        # *matched* track, the person is likely occluded (hidden behind
        # another dancer), not gone.  We slow their aging dramatically
        # so they survive the occlusion and can resume matching once
        # the occluder moves away.
        matched_positions = self._collect_matched_positions(matched_trk)

        for idx, track in enumerate(self.tracks):
            if idx in matched_trk:
                track.clear_occlusion_event()
                continue
            if self._is_track_near_matched_positions(track, matched_positions):
                self._mark_track_occluded(track, frame_ctx)
                continue
            track.clear_occlusion_event()

    def _collect_matched_positions(self, matched_trk) -> list[np.ndarray]:
        """Collect current centroids for matched tracks."""
        return [self.tracks[idx].get_centroid() for idx in matched_trk]

    def _occlusion_distance_for_track(self, track: DancerTrack) -> float:
        """Return the occlusion proximity radius for an unmatched track."""
        base_dist = self.distance_threshold * TRACKER_OCCLUSION_DISTANCE_RATIO
        return base_dist * (1.5 if track._occluded else 1.0)

    def _is_track_near_matched_positions(self, track: DancerTrack,
                                         matched_positions: list[np.ndarray]) -> bool:
        """Whether an unmatched track is close enough to a matched track to count as occluded."""
        pred_pos = track.get_centroid()
        current_occlusion_dist = self._occlusion_distance_for_track(track)
        for matched_pos in matched_positions:
            if np.linalg.norm(pred_pos - matched_pos) < current_occlusion_dist:
                return True
        return False

    def _apply_fractional_occlusion_aging(self, track: DancerTrack):
        """Undo full predict aging and reapply slowed occlusion aging."""
        track._fractional_age = (
            getattr(track, '_fractional_age', 0.0)
            + TRACKER_OCCLUSION_AGE_FACTOR
        )
        track.time_since_update -= 1
        if track._fractional_age >= 1.0:
            increments = int(track._fractional_age)
            track.time_since_update += increments
            track._fractional_age -= increments

    def _mark_track_occluded(self, track: DancerTrack,
                             frame_ctx: FrameUpdateContext):
        """Apply occlusion state, slowed aging, and logging for one track."""
        track.note_occlusion_event(
            self.frame_count,
            merge_related=frame_ctx.merge_frame,
        )
        self._apply_fractional_occlusion_aging(track)

        self.logger.log("OCCLUDED", {
            "track_id": track.track_id,
            "time_since_update": track.time_since_update,
        })
        if TRACKER_DEBUG:
            print(f"[TRACKER] Track #{track.track_id} occluded "
                  f"(t_miss={track.time_since_update})")

    def _finalize_track_lifecycle(self):
        """Expire, age, and clean up auxiliary tracker state."""
        self._retire_expired_active_tracks()
        self._age_and_prune_dormant_tracks()
        
        # Update pairwise distance memory (for separation penalty)
        self._update_pair_distances()

        # ---- Shadow track detection ----
        # A "shadow track" consistently moves in sync with a nearby
        # higher-quality track.  Kill it after SHADOW_TRACK_FRAMES
        # consecutive shadow-correlated frames.
        self._detect_shadow_tracks()

    def _effective_max_age_for_track(self, track: DancerTrack) -> tuple[int, bool]:
        """Return effective expiry age and edge-exit classification for a track."""
        last_x = float(track.get_last_known_position()[0])
        at_edge = self._is_near_edge(last_x) and track.time_since_update > 0
        effective_max_age = self.max_age
        if at_edge:
            effective_max_age = int(self.max_age * TRACKER_EDGE_EXIT_AGE_MULT)
        elif track.is_established:
            effective_max_age = int(self.max_age * TRACKER_ESTABLISHED_MAX_AGE_MULT)
        return effective_max_age, at_edge

    def _retire_track_to_dormant(self, track: DancerTrack, at_edge: bool):
        """Move an expired active track into the dormant pool with logging."""
        self._dormant.append(DormantSnapshot(track, exited_from_edge=at_edge))
        self.logger.log("DORMANT", {
            "track_id": track.track_id,
            "was_occluded": track._occluded,
            "is_established": track.is_established,
            "edge_exit": at_edge,
        })
        if TRACKER_DEBUG:
            print(f"[TRACKER] Track #{track.track_id} → dormant pool "
                  f"(was_occluded={track._occluded}, "
                  f"established={track.is_established}, "
                  f"edge_exit={at_edge})")

    def _retire_expired_active_tracks(self):
        """Apply active-track expiry policy and move expired tracks to dormant."""
        still_alive = []
        for track in self.tracks:
            effective_max_age, at_edge = self._effective_max_age_for_track(track)
            if track.time_since_update >= effective_max_age:
                self._retire_track_to_dormant(track, at_edge)
            else:
                still_alive.append(track)
        self.tracks = still_alive

    def _age_and_prune_dormant_tracks(self):
        """Advance dormant ages, log expirations, and prune expired snapshots."""
        for snap in self._dormant:
            snap.age += 1

        expired = [s for s in self._dormant if s.age >= self.dormant_max_age]
        if expired:
            for snap in expired:
                self.logger.log("DORMANT_EXPIRED", {"track_id": snap.track_id})
            if TRACKER_DEBUG:
                print(f"[TRACKER] Dormant expired: {[s.track_id for s in expired]}")

        self._dormant = [s for s in self._dormant if s.age < self.dormant_max_age]

    def _collect_confirmed_tracks(self):
        """Return tracks that are confirmed enough to expose externally."""
        confirmed = []
        for track in self.tracks:
            if track.hits >= self.min_hits or self.frame_count <= self.min_hits:
                confirmed.append(track)
        return confirmed

    def _log_frame_summary(self, detections, matched_pairs_log):
        """Emit the structured FRAME_SUMMARY event for the current frame."""
        # ---- Emit per-frame summary to structured log ----
        self.logger.log_frame_summary(
            n_detections=len(detections),
            n_tracks=len(self.tracks),
            track_states=[
                {
                    "id": t.track_id,
                    "centroid": t.get_centroid().tolist(),
                    "velocity": t.get_velocity().tolist(),
                    "hits": t.hits,
                    "t_miss": t.time_since_update,
                    "established": t.is_established,
                    "occluded": getattr(t, '_occluded', False),
                    "last_match_frame": getattr(t, '_last_match_frame', -1),
                    "last_occluded_frame": getattr(t, '_last_occluded_frame', -1),
                    "last_merge_frame": getattr(t, '_last_merge_frame', -1),
                    "merge_episode_id": getattr(t, '_merge_episode_id', 0),
                }
                for t in self.tracks
            ],
            n_dormant=len(self._dormant),
            matched_pairs=matched_pairs_log,
        )

    # ------------------------------------------------------------------
    # Separation memory helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _pair_key(id_a: int, id_b: int) -> tuple:
        """Canonical key for a pair of track IDs (order-independent)."""
        return (min(id_a, id_b), max(id_a, id_b))

    def _update_pair_distances(self):
        """Update rolling pairwise-distance history for all active tracks.

        Called once per frame after assignment.  The history feeds the
        separation penalty in ``_compute_cost_matrix``:

        * Bodies historically far apart → penalty discourages cross-match.
        * Bodies always close (shadow artifacts) → no penalty (lenient).
        """
        for i, track_a in enumerate(self.tracks):
            for j in range(i + 1, len(self.tracks)):
                track_b = self.tracks[j]
                key = self._pair_key(track_a.track_id, track_b.track_id)
                pos_a = track_a.get_centroid()
                pos_b = track_b.get_centroid()
                dist = float(np.linalg.norm(pos_a - pos_b))
                if key not in self._pair_distances:
                    self._pair_distances[key] = deque(
                        maxlen=TRACKER_SEPARATION_MEMORY_FRAMES)
                self._pair_distances[key].append(dist)

        # Prune stale pairs (both IDs gone from active + dormant)
        active_ids = {t.track_id for t in self.tracks}
        dormant_ids = {s.track_id for s in self._dormant}
        known = active_ids | dormant_ids
        stale = [k for k in self._pair_distances
                 if k[0] not in known and k[1] not in known]
        for k in stale:
            del self._pair_distances[k]

    def _detect_shadow_tracks(self):
        """Kill tracks that behave like shadows of another track.

        A shadow track is characterised by:
        1. Close to a higher-quality track (within SHADOW_PROXIMITY_RATIO
           × person-height-derived distance_threshold).
        2. Velocity direction is highly correlated (cosine similarity
           ≥ SHADOW_TRACK_VELOCITY_CORR).
        3. Lower skeleton quality than the parent track.

        Tracks that satisfy all three for SHADOW_TRACK_FRAMES consecutive
        frames are removed.
        """
        shadow_proximity = self.distance_threshold * SHADOW_PROXIMITY_RATIO
        to_kill = set()
        incremented = set()  # tracks whose shadow streak was bumped this frame

        for i, track_a in enumerate(self.tracks):
            if track_a.time_since_update > 0:
                continue  # only check freshly-matched tracks
            vel_a = track_a.get_velocity()
            speed_a = np.linalg.norm(vel_a)
            qual_a = track_a.skeleton_quality

            for j, track_b in enumerate(self.tracks):
                if j == i or track_b.time_since_update > 0:
                    continue
                # Check proximity
                dist = np.linalg.norm(track_a.get_centroid()
                                      - track_b.get_centroid())
                if dist > shadow_proximity:
                    continue

                vel_b = track_b.get_velocity()
                speed_b = np.linalg.norm(vel_b)

                # Need both to be moving to check correlation
                # Min speed scales with distance_threshold (~person height)
                min_speed = self.distance_threshold * 0.01
                if speed_a < min_speed or speed_b < min_speed:
                    continue

                # Cosine similarity of velocity vectors
                cos_sim = float(np.dot(vel_a, vel_b) / (speed_a * speed_b))
                if cos_sim < SHADOW_TRACK_VELOCITY_CORR:
                    continue

                # Both are close and moving the same way.
                # The one with lower skeleton quality is the shadow.
                qual_b = track_b.skeleton_quality
                if qual_a < qual_b:
                    shadow, parent = track_a, track_b
                elif qual_b < qual_a:
                    shadow, parent = track_b, track_a
                else:
                    continue  # equal quality — can't tell, skip

                shadow._shadow_streak += 1
                incremented.add(shadow.track_id)
                if shadow._shadow_streak >= SHADOW_TRACK_FRAMES:
                    to_kill.add(shadow.track_id)
                    self.logger.log("KILL_SHADOW", {
                        "track_id": shadow.track_id,
                        "parent_id": parent.track_id,
                        "shadow_quality": round(shadow.skeleton_quality, 2),
                        "parent_quality": round(parent.skeleton_quality, 2),
                        "cos_sim": round(cos_sim, 2),
                    })
                    if TRACKER_DEBUG:
                        print(f"[TRACKER] Shadow track #{shadow.track_id} killed "
                              f"(parent=#{parent.track_id}, "
                              f"qual={shadow.skeleton_quality:.2f} vs "
                              f"{parent.skeleton_quality:.2f}, "
                              f"cos={cos_sim:.2f})")

        # Reset shadow streak for tracks NOT flagged this frame
        for track in self.tracks:
            if track.track_id not in incremented:
                track._shadow_streak = 0

        # Kill shadow tracks
        if to_kill:
            self.tracks = [t for t in self.tracks
                           if t.track_id not in to_kill]
