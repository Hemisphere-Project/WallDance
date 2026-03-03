"""
Multi-person tracker using Kalman Filter + Hungarian Algorithm.
Optimized for wall dancers with potentially rotated orientations.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment
from filterpy.kalman import KalmanFilter
from collections import deque
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
    TRACKER_CASCADED_MATCHING,
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
                 'exited_from_edge')

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
        
        # Smoothed centroid for output (EMA, does not affect tracking)
        centroid = self._compute_centroid(keypoints, confidence)
        self._smoothed_centroid = centroid.copy()
        
        # History for visualization
        self.history = deque(maxlen=30)
        
        # Confidence history for temporal smoothing
        self.confidence_history = deque(maxlen=max(1, smoothing_depth))
        
        # Bbox area history for anti-merge detection (Phase 4)
        self.bbox_area_history = deque(maxlen=30)
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
    
    def update(self, keypoints, confidence, bbox):
        """Update track with new detection."""
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
        
        # Update smoothed centroid (EMA for jitter-free output)
        alpha = CENTROID_OUTPUT_SMOOTHING
        self._smoothed_centroid = (alpha * centroid
                                   + (1.0 - alpha) * self._smoothed_centroid)
    
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
            n_nearby_tracks = sum(
                1 for tr in tracks
                if np.linalg.norm(det_centroid - tr.get_centroid()) < close_dist
            )
            is_crowded = n_nearby_tracks >= 2
            
            for t, track in enumerate(tracks):
                # --- 0. Mahalanobis gating (Phase 1) ---
                # Use Kalman covariance to reject physically impossible
                # assignments.  After predict(), kf.P is the predicted
                # covariance.  If the detection is too far from the
                # track's predicted position (chi² > gate), set cost to
                # INF so Hungarian never picks this pair.
                if TRACKER_MAHALANOBIS_GATE > 0:
                    innov = det_centroid - track.kf.x[:2].flatten()
                    # Use inflated noise for the gate so that normal
                    # YOLO jitter (10-50px) passes while teleports
                    # (>120px) are still blocked.  Kalman R is tuned
                    # for smoothing (R=2) and collapses S to ~4px²,
                    # which is far too tight for gating purposes.
                    R_gate = np.eye(2) * TRACKER_MAHALANOBIS_GATE_NOISE
                    S = track.kf.P[:2, :2] + R_gate               # 2×2
                    try:
                        S_inv = np.linalg.inv(S)
                        maha_sq = float(innov @ S_inv @ innov)
                    except np.linalg.LinAlgError:
                        maha_sq = 0.0  # degenerate covariance — skip gate
                    if maha_sq > TRACKER_MAHALANOBIS_GATE:
                        cost_matrix[d, t] = 1e6
                        self.logger.log("MAHALANOBIS_GATE", {
                            "det": d,
                            "track_id": track.track_id,
                            "chi2": round(maha_sq, 1),
                            "gate": TRACKER_MAHALANOBIS_GATE,
                            "dist_px": round(float(np.linalg.norm(innov)), 1),
                        })
                        continue

                predicted_pos = track.get_centroid()
                last_known_pos = track.get_last_known_position()
                velocity = track.get_velocity()
                
                # --- 1. Position cost with velocity prediction influence ---
                velocity_adjusted = predicted_pos + velocity * self.velocity_weight
                dist_pred = np.linalg.norm(det_centroid - predicted_pos)
                dist_vel  = np.linalg.norm(det_centroid - velocity_adjusted)
                dist_last = np.linalg.norm(det_centroid - last_known_pos)
                vpi = self._velocity_prediction_influence
                w_pred = 0.4 + 0.2 * vpi
                w_vel  = 0.2 + 0.2 * vpi
                w_last = 0.4 - 0.4 * vpi
                pos_cost = w_pred * dist_pred + w_vel * dist_vel + w_last * dist_last
                
                # --- 2. Normalised keypoint-shape cost ---
                # Compare body shapes relative to each centroid so that
                # two overlapping dancers are distinguished by *pose*,
                # not by absolute position.
                kpt_cost = 0.0
                mask_trk = track.confidence > KEYPOINT_CONFIDENCE
                both = mask_det & mask_trk
                n_both = int(np.sum(both))
                if n_both >= 3:
                    trk_kpts_norm, _ = track.get_normalized_skeleton()
                    diffs = np.linalg.norm(
                        det_kpts_norm[both] - trk_kpts_norm[both], axis=1)
                    kpt_cost = float(np.mean(diffs))
                
                # --- 3. Bbox-size cost ---
                trk_height = track.bbox[3]
                size_cost = abs(det_height - trk_height)
                
                # --- 4. Separation penalty (known-separate bodies) ---
                sep_penalty = 0.0
                for other_track in tracks:
                    if other_track is track:
                        continue
                    pair_key = self._pair_key(track.track_id,
                                              other_track.track_id)
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
                        # Boost for established pairs
                        if track.is_established and other_track.is_established:
                            penalty *= TRACKER_ESTABLISHED_SEP_BOOST
                        sep_penalty = max(sep_penalty, penalty)
                
                # --- 5. Directional Momentum Penalty ---
                # Heavily penalizes a detection if assigning it would mean
                # an established track suddenly reversing direction in a crowd!
                dir_penalty = 0.0
                if is_crowded and track.is_established:
                    implied_vel = det_centroid - last_known_pos
                    implied_speed = np.linalg.norm(implied_vel)
                    curr_speed = np.linalg.norm(velocity)
                    
                    if curr_speed > self.distance_threshold * 0.05 and implied_speed > self.distance_threshold * 0.05:
                        cos_sim = float(np.dot(velocity, implied_vel) / (curr_speed * implied_speed))
                        if cos_sim < 0:
                            # Reversal (-1.0 to 0) -> strongly punish!
                            dir_penalty = abs(cos_sim) * self.distance_threshold * 2.0
                        elif cos_sim < 0.5:
                            # Sharp turn -> mild punish
                            dir_penalty = (0.5 - cos_sim) * self.distance_threshold * 0.5
                
                # --- Combined cost with adaptive weights ---
                if is_crowded:
                    if n_both >= 5:
                        # Close dancing with strong skeleton: shape dominates
                        base_cost = (TRACKER_CLOSE_POS_WEIGHT * pos_cost
                                     + TRACKER_CLOSE_KPT_WEIGHT * kpt_cost
                                     + TRACKER_CLOSE_SIZE_WEIGHT * size_cost)
                    elif n_both >= 3:
                        # Close dancing but weak skeleton: blend but add
                        # uncertainty penalty to discourage cross-matching
                        base_cost = (0.25 * pos_cost + 0.50 * kpt_cost
                                     + 0.25 * size_cost)
                        base_cost *= 1.3  # coherence penalty
                    else:
                        # Close dancing, no skeleton: position + size only,
                        # heavily penalised → strongly discourage swap
                        base_cost = (0.85 * pos_cost + 0.15 * size_cost)
                        base_cost *= 1.5  # no skeleton = very uncertain
                elif n_both >= 3:
                    # Normal: balanced weights
                    base_cost = (0.40 * pos_cost + 0.45 * kpt_cost
                                 + 0.15 * size_cost)
                else:
                    base_cost = 0.85 * pos_cost + 0.15 * size_cost
                
                cost_matrix[d, t] = base_cost + sep_penalty + dir_penalty
        
        return cost_matrix

    # ------------------------------------------------------------------
    # Assignment pass (used by cascaded matching)
    # ------------------------------------------------------------------
    def _run_assignment_pass(self, detections, det_indices, trk_indices,
                             matched_det, matched_trk, matched_pairs_log):
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
            track_speed = track.get_speed()

            # Dynamic threshold: base + velocity bonus + time bonus
            time_bonus = min(
                track.time_since_update * self.distance_threshold * 0.04,
                self.distance_threshold * 0.3,
            )
            dynamic_thresh = (self.distance_threshold
                              + track_speed * 1.0
                              + time_bonus)

            if cost_matrix[row, col] < dynamic_thresh:
                kpts, conf, bbox = detections[actual_det]

                # Anti-merge: reject if detection bbox is suspiciously
                # large for this established track.
                if (track.is_established
                        and len(track.bbox_area_history) >= 5
                        and bbox[2] * bbox[3]
                            > track.avg_bbox_area * TRACKER_MERGE_SIZE_RATIO):
                    det_centroid = self._compute_centroid(kpts, conf, bbox)
                    close_dist = (self.distance_threshold
                                  * TRACKER_CLOSE_PROXIMITY_RATIO)
                    nearby = sum(
                        1 for tr in self.tracks
                        if np.linalg.norm(
                            det_centroid - tr.get_centroid()) < close_dist
                    )
                    if nearby >= 2:
                        self.logger.log("ANTI_MERGE", {
                            "track_id": track.track_id,
                            "det_area": round(bbox[2] * bbox[3]),
                            "avg_area": round(track.avg_bbox_area),
                            "ratio": TRACKER_MERGE_SIZE_RATIO,
                        })
                        if TRACKER_DEBUG:
                            print(f"[TRACKER] Anti-merge rejected: "
                                  f"det_area={bbox[2]*bbox[3]:.0f} > "
                                  f"{TRACKER_MERGE_SIZE_RATIO}x "
                                  f"avg={track.avg_bbox_area:.0f}")
                        matched_det.add(actual_det)
                        continue

                matched_det.add(actual_det)
                matched_trk.add(actual_trk)
                track.update(kpts, conf, bbox)
                cost_val = round(float(cost_matrix[row, col]), 1)
                self.logger.log("MATCH", {
                    "det": actual_det,
                    "track_id": track.track_id,
                    "cost": cost_val,
                    "threshold": round(dynamic_thresh, 1),
                    "is_established": track.is_established,
                })
                matched_pairs_log.append({
                    "det": actual_det,
                    "track_id": track.track_id,
                    "cost": cost_val,
                })
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

    # ------------------------------------------------------------------
    # Dormant pool re-identification
    # ------------------------------------------------------------------
    def _try_resurrect(self, keypoints, confidence, bbox, det_centroid) -> 'DancerTrack | None':
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
        new_track.hits = self.min_hits       # immediately confirmed

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
        if frame_number is not None:
            self.frame_count = frame_number
        else:
            self.frame_count += 1
        self.logger.set_frame(self.frame_count)

        # Predict
        for track in self.tracks:
            track.predict()
        
        # Match — cascaded assignment (Phase 1)
        # Pass 1: established tracks get first pick of all detections.
        # Pass 2: tentative tracks match remaining detections.
        # This prevents a newly-spawned tentative track from stealing a
        # detection that belongs to an established dancer.
        matched_det = set()
        matched_trk = set()
        matched_pairs_log = []  # for FRAME_SUMMARY

        all_det_indices = list(range(len(detections)))

        if TRACKER_CASCADED_MATCHING:
            # --- Pass 1: established tracks ---
            est_indices = [i for i, t in enumerate(self.tracks)
                           if t.is_established]
            self._run_assignment_pass(
                detections, all_det_indices, est_indices,
                matched_det, matched_trk, matched_pairs_log)

            # --- Pass 2: tentative tracks vs remaining detections ---
            tent_indices = [i for i, t in enumerate(self.tracks)
                            if not t.is_established and i not in matched_trk]
            remaining_dets = [d for d in all_det_indices
                              if d not in matched_det]
            self._run_assignment_pass(
                detections, remaining_dets, tent_indices,
                matched_det, matched_trk, matched_pairs_log)
        else:
            # Single-pass matching (legacy behaviour)
            self._run_assignment_pass(
                detections, all_det_indices,
                list(range(len(self.tracks))),
                matched_det, matched_trk, matched_pairs_log)
        
        # Create new tracks — use separate, tighter gate for new-track
        # creation vs. matching so two close dancers can coexist.
        for d, (kpts, conf, bbox) in enumerate(detections):
            if d not in matched_det:
                det_centroid = self._compute_centroid(kpts, conf, bbox)
                
                # Check distance to ALL tracks
                min_dist = float('inf')
                closest_track = None
                closest_track_idx = None
                for idx, track in enumerate(self.tracks):
                    last_pos = track.get_last_known_position()
                    dist = np.linalg.norm(det_centroid - last_pos)
                    if dist < min_dist:
                        min_dist = dist
                        closest_track = track
                        closest_track_idx = idx
                
                # Gate 1: far enough from every track → new person (or resurrect)
                # Center detections need a tighter gate to avoid ghost splits
                creation_gate = self.new_track_min_distance
                is_edge_det = self._is_near_edge(float(det_centroid[0]))
                if not is_edge_det:
                    creation_gate = int(creation_gate * TRACKER_CENTER_NEW_TRACK_GATE_MULT)
                if min_dist > creation_gate:
                    resurrected = self._try_resurrect(kpts, conf, bbox, det_centroid)
                    if resurrected is not None:
                        self.tracks.append(resurrected)
                    else:
                        # Deadlock fix: If a detection is far enough from active tracks
                        # and failed resurrection validations, allow it to spawn a new
                        # track instantly rather than waiting for shape-mismatched
                        # dormant tracks to expire.
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
                        self.tracks.append(
                            DancerTrack(kpts, conf, bbox,
                                        self.smoothing_depth))
                elif closest_track is not None and closest_track_idx not in matched_trk:
                    # Close to an unmatched track → force-update it
                    # Prefer occluded tracks (they've been waiting for this)
                    best_unmatched = closest_track
                    best_unmatched_idx = closest_track_idx
                    for idx2, t2 in enumerate(self.tracks):
                        if idx2 in matched_trk:
                            continue
                        d2 = np.linalg.norm(det_centroid - t2.get_last_known_position())
                        if (d2 < self.distance_threshold
                                and t2._occluded
                                and not best_unmatched._occluded):
                            best_unmatched = t2
                            best_unmatched_idx = idx2
                    self.logger.log("FORCE_UPDATE", {
                        "track_id": best_unmatched.track_id,
                        "dist": round(min_dist, 1),
                        "occluded": best_unmatched._occluded,
                    })
                    if TRACKER_DEBUG:
                        print(f"[TRACKER] Force update track #{best_unmatched.track_id}: "
                              f"dist={min_dist:.1f} occluded={best_unmatched._occluded}")
                    best_unmatched.update(kpts, conf, bbox)
                    matched_trk.add(best_unmatched_idx)
                elif closest_track is not None and min_dist < self.duplicate_distance:
                    # Very close to an already-matched track → duplicate, drop
                    self.logger.log("DUPLICATE_IGNORED", {
                        "near_track_id": closest_track.track_id,
                        "dist": round(min_dist, 1),
                    })
                    if TRACKER_DEBUG:
                        print(f"[TRACKER] Ignoring duplicate near track "
                              f"#{closest_track.track_id}: dist={min_dist:.1f}")
                else:
                    # Moderately close — try any remaining unmatched track
                    for idx, track in enumerate(self.tracks):
                        if idx not in matched_trk:
                            last_pos = track.get_last_known_position()
                            dist = np.linalg.norm(det_centroid - last_pos)
                            if dist < self.distance_threshold:
                                self.logger.log("FALLBACK_UPDATE", {
                                    "track_id": track.track_id,
                                    "dist": round(dist, 1),
                                })
                                if TRACKER_DEBUG:
                                    print(f"[TRACKER] Fallback update track #{track.track_id}: dist={dist:.1f}")
                                track.update(kpts, conf, bbox)
                                matched_trk.add(idx)
                                break
                    else:
                        self.logger.log("AMBIGUOUS_IGNORED", {
                            "near_track_id": closest_track.track_id if closest_track else None,
                            "dist": round(min_dist, 1),
                        })
                        if TRACKER_DEBUG:
                            print(f"[TRACKER] Ignoring ambiguous det near track "
                                  f"#{closest_track.track_id if closest_track else 'None'}: dist={min_dist:.1f}")
        
        # ---- Occlusion-aware aging ----
        # For unmatched tracks whose predicted position is near a
        # *matched* track, the person is likely occluded (hidden behind
        # another dancer), not gone.  We slow their aging dramatically
        # so they survive the occlusion and can resume matching once
        # the occluder moves away.
        matched_positions = []
        for idx in matched_trk:
            matched_positions.append(self.tracks[idx].get_centroid())

        occlusion_dist = self.distance_threshold * TRACKER_OCCLUSION_DISTANCE_RATIO

        for idx, track in enumerate(self.tracks):
            if idx in matched_trk:
                track._occluded = False
                continue
            # Track is unmatched — check if it's near any matched track
            pred_pos = track.get_centroid()
            near_matched = False
            
            # Widen the radius slightly if they were already occluded to 
            # account for them coasting past the occluder while hidden.
            current_occlusion_dist = occlusion_dist * (1.5 if track._occluded else 1.0)
            
            for mpos in matched_positions:
                if np.linalg.norm(pred_pos - mpos) < current_occlusion_dist:
                    near_matched = True
                    break
            if near_matched:
                track._occluded = True
                # Undo the full aging from predict() and apply fractional aging
                # predict() already incremented time_since_update by 1
                track._fractional_age = getattr(track, '_fractional_age', 0.0) + TRACKER_OCCLUSION_AGE_FACTOR
                track.time_since_update -= 1
                if track._fractional_age >= 1.0:
                    increments = int(track._fractional_age)
                    track.time_since_update += increments
                    track._fractional_age -= increments
                
                self.logger.log("OCCLUDED", {
                    "track_id": track.track_id,
                    "time_since_update": track.time_since_update,
                })
                if TRACKER_DEBUG:
                    print(f"[TRACKER] Track #{track.track_id} occluded "
                          f"(t_miss={track.time_since_update})")
            else:
                track._occluded = False

        # Move expired tracks to the dormant pool (for later re-ID)
        # Established tracks get a longer max_age — they've proven they
        # are real people and deserve more time to survive occlusion.
        still_alive = []
        for t in self.tracks:
            effective_max_age = self.max_age
            last_x = float(t.get_last_known_position()[0])
            at_edge = self._is_near_edge(last_x) and t.time_since_update > 0
            if at_edge:
                # Edge tracks: skip established bonus, apply edge mult
                # They left the scene — no need to linger on screen
                effective_max_age = int(
                    self.max_age * TRACKER_EDGE_EXIT_AGE_MULT)
            elif t.is_established:
                # Center/established: full bonus for occlusion survival
                effective_max_age = int(
                    self.max_age * TRACKER_ESTABLISHED_MAX_AGE_MULT)
            if t.time_since_update >= effective_max_age:
                # Did this track leave near an edge, or vanish in the center?
                self._dormant.append(DormantSnapshot(t, exited_from_edge=at_edge))
                self.logger.log("DORMANT", {
                    "track_id": t.track_id,
                    "was_occluded": t._occluded,
                    "is_established": t.is_established,
                    "edge_exit": at_edge,
                })
                if TRACKER_DEBUG:
                    print(f"[TRACKER] Track #{t.track_id} → dormant pool "
                          f"(was_occluded={t._occluded}, "
                          f"established={t.is_established}, "
                          f"edge_exit={at_edge})")
            else:
                still_alive.append(t)
        self.tracks = still_alive

        # Age out the dormant pool
        for snap in self._dormant:
            snap.age += 1
        expired = [s for s in self._dormant if s.age >= self.dormant_max_age]
        if expired:
            for s in expired:
                self.logger.log("DORMANT_EXPIRED", {"track_id": s.track_id})
            if TRACKER_DEBUG:
                print(f"[TRACKER] Dormant expired: {[s.track_id for s in expired]}")
        self._dormant = [s for s in self._dormant if s.age < self.dormant_max_age]
        
        # Update pairwise distance memory (for separation penalty)
        self._update_pair_distances()

        # ---- Shadow track detection ----
        # A "shadow track" consistently moves in sync with a nearby
        # higher-quality track.  Kill it after SHADOW_TRACK_FRAMES
        # consecutive shadow-correlated frames.
        self._detect_shadow_tracks()

        # Return confirmed tracks
        confirmed = []
        for track in self.tracks:
            if track.hits >= self.min_hits or self.frame_count <= self.min_hits:
                confirmed.append(track)

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
                }
                for t in self.tracks
            ],
            n_dormant=len(self._dormant),
            matched_pairs=matched_pairs_log,
        )

        return confirmed

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
