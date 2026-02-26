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
    KEYPOINT_CONFIDENCE
)

# Set to True for detailed tracking debug output
TRACKER_DEBUG = False


class DormantSnapshot:
    """Frozen snapshot of a track that has left the active pool.

    Stored in the dormant ("graveyard") pool so that if the same person
    reappears we can resurrect the original track ID instead of minting
    a new one.  Matching uses position + bbox height + keypoint shape.
    """
    __slots__ = ('track_id', 'last_position', 'keypoints', 'confidence',
                 'bbox_height', 'age')

    def __init__(self, track: 'DancerTrack'):
        self.track_id: int = track.track_id
        self.last_position: np.ndarray = track.get_last_known_position().copy()
        self.keypoints: np.ndarray = track.keypoints.copy()
        self.confidence: np.ndarray = track.confidence.copy()
        self.bbox_height: float = float(track.bbox[3])
        self.age: int = 0  # frames since entering dormant pool


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
        self.smoothing_depth = smoothing_depth
        
        # History for visualization
        self.history = deque(maxlen=30)
        
        # Confidence history for temporal smoothing
        self.confidence_history = deque(maxlen=max(1, smoothing_depth))
        
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
        
        self.kf.P = np.eye(6) * 100.0
        self.kf.P[2:4, 2:4] *= 10.0
        self.kf.P[4:6, 4:6] *= 10.0
        
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
        self.kf.predict()
        self.age += 1
        self.time_since_update += 1
        
        # Clamp velocity to prevent runaway predictions
        # Max reasonable velocity: ~100 pixels/frame (fast dancer movement)
        MAX_VELOCITY = 100.0
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
        self.hits += 1
        self.time_since_update = 0
        
        # Store confidence for temporal smoothing
        self.confidence_history.append(confidence.copy())
        
        centroid = self._compute_centroid(keypoints, confidence)
        self.kf.update(centroid.reshape(2, 1))
        self.history.append(centroid)
    
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


class DancerTracker:
    """Multi-dancer tracker with Hungarian assignment."""
    
    def __init__(self):
        self.tracks = []
        self.frame_count = 0
        self.max_age = TRACKER_MAX_AGE
        self.min_hits = TRACKER_MIN_HITS
        self.velocity_weight = TRACKER_VELOCITY_WEIGHT
        self._smoothing_depth = 1  # Temporal confidence smoothing depth

        # Scale-dependent thresholds — all derived from person_height_px.
        # Call set_person_height() to update; the master dial in config.py
        # is PERSON_HEIGHT_PX.  TRACKER_DISTANCE_THRESHOLD is only used as
        # the initial fallback before the app callback fires.
        self.distance_threshold = TRACKER_DISTANCE_THRESHOLD
        self.new_track_min_distance = max(30, int(TRACKER_DISTANCE_THRESHOLD * 0.33))
        self.duplicate_distance = max(15, int(TRACKER_DISTANCE_THRESHOLD * 0.17))

        # Dormant pool for re-ID after occlusion
        self._dormant: list[DormantSnapshot] = []
        self.dormant_max_age = TRACKER_DORMANT_MAX_AGE

    # ------------------------------------------------------------------
    # Person-height master dial
    # ------------------------------------------------------------------
    def set_person_height(self, height_px: int):
        """Derive all scale-dependent thresholds from expected person height.

        This is the **single knob** that adjusts tracker behaviour for
        capture distance.  All three thresholds scale linearly:

        * distance_threshold      = height × 1.2  (match gate)
        * new_track_min_distance  = height × 0.4  (create-track gate)
        * duplicate_distance      = height × 0.2  (ignore-duplicate gate)
        """
        self.distance_threshold = max(50, int(height_px * 1.2))
        self.new_track_min_distance = max(20, int(height_px * 0.4))
        self.duplicate_distance = max(10, int(height_px * 0.2))
    
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
        """Reset all tracks and dormant pool."""
        self.tracks = []
        self._dormant = []
        self.frame_count = 0
        DancerTrack._id_counter = 0
    
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
        
        Cost blends three signals so that skeleton shape and size help
        disambiguate when centroids alone are ambiguous (= ID-swap scenario):
        
        1. **Position cost** — weighted blend of distances to predicted,
           velocity-adjusted, and last-known positions.
        2. **Keypoint-shape cost** — mean distance between co-visible
           keypoints of the detection and the track's last keypoints.
        3. **Bbox-size cost** — absolute height difference between detection
           and track bbox.
        
        All three are in pixel units and combined as a weighted sum.
        """
        if len(detections) == 0 or len(tracks) == 0:
            return np.empty((len(detections), len(tracks)))
        
        cost_matrix = np.zeros((len(detections), len(tracks)))
        
        for d, (kpts, conf, bbox) in enumerate(detections):
            det_centroid = self._compute_centroid(kpts, conf, bbox)
            det_height = bbox[3]  # bbox (x, y, w, h)
            
            for t, track in enumerate(tracks):
                predicted_pos = track.get_centroid()
                last_known_pos = track.get_last_known_position()
                velocity = track.get_velocity()
                
                # --- 1. Position cost (weighted blend, not min) ---
                velocity_adjusted = predicted_pos + velocity * self.velocity_weight
                dist_pred = np.linalg.norm(det_centroid - predicted_pos)
                dist_vel  = np.linalg.norm(det_centroid - velocity_adjusted)
                dist_last = np.linalg.norm(det_centroid - last_known_pos)
                pos_cost = 0.5 * dist_pred + 0.3 * dist_vel + 0.2 * dist_last
                
                # --- 2. Keypoint-shape cost ---
                # Mean distance between co-visible keypoints of detection
                # vs. track.  Powerful disambiguator when two dancers are
                # close but their skeletons differ.
                kpt_cost = 0.0
                mask_det = conf > KEYPOINT_CONFIDENCE
                mask_trk = track.confidence > KEYPOINT_CONFIDENCE
                both = mask_det & mask_trk
                n_both = int(np.sum(both))
                if n_both >= 3:
                    diffs = np.linalg.norm(kpts[both] - track.keypoints[both], axis=1)
                    kpt_cost = float(np.mean(diffs))
                
                # --- 3. Bbox-size cost ---
                trk_height = track.bbox[3]
                size_cost = abs(det_height - trk_height)
                
                # --- Combined cost ---
                # Weights:  position=0.5  keypoints=0.35  size=0.15
                # When no co-visible keypoints exist, position dominates.
                if n_both >= 3:
                    cost_matrix[d, t] = 0.50 * pos_cost + 0.35 * kpt_cost + 0.15 * size_cost
                else:
                    cost_matrix[d, t] = 0.85 * pos_cost + 0.15 * size_cost
        
        return cost_matrix

    # ------------------------------------------------------------------
    # Dormant pool re-identification
    # ------------------------------------------------------------------
    def _try_resurrect(self, keypoints, confidence, bbox, det_centroid) -> 'DancerTrack | None':
        """Check the dormant pool for a matching snapshot.

        All three criteria must hold (AND logic):
        1. Position: detection centroid within ``distance_threshold`` of
           the dormant snapshot's last position.
        2. Size: bbox height within 40 % of the snapshot's bbox height.
        3. Shape (when ≥ 3 co-visible keypoints): mean keypoint distance
           < ``distance_threshold × 0.5``.

        If a match is found the dormant entry is consumed, a *new*
        ``DancerTrack`` is created with the fresh detection data, **but
        its ``track_id`` is overwritten** with the old ID so the OSC
        consumer sees continuity.

        Returns:
            A resurrected ``DancerTrack`` or ``None``.
        """
        if not self._dormant:
            return None

        det_height = float(bbox[3])
        best_idx = None
        best_score = float('inf')

        for i, snap in enumerate(self._dormant):
            # --- 1. Position gate ---
            dist = np.linalg.norm(det_centroid - snap.last_position)
            if dist > self.distance_threshold:
                continue

            # --- 2. Size gate (±40 %) ---
            if snap.bbox_height > 0:
                height_ratio = det_height / snap.bbox_height
                if height_ratio < 0.6 or height_ratio > 1.4:
                    continue

            # --- 3. Keypoint-shape gate ---
            mask_det = confidence > KEYPOINT_CONFIDENCE
            mask_snap = snap.confidence > KEYPOINT_CONFIDENCE
            both = mask_det & mask_snap
            n_both = int(np.sum(both))
            if n_both >= 3:
                kpt_dist = float(np.mean(
                    np.linalg.norm(keypoints[both] - snap.keypoints[both], axis=1)
                ))
                if kpt_dist > self.distance_threshold * 0.5:
                    continue
                # Score: blend of position + keypoint distance (lower is better)
                score = 0.5 * dist + 0.5 * kpt_dist
            else:
                # Not enough keypoints to compare shape — rely on position + size
                score = dist

            if score < best_score:
                best_score = score
                best_idx = i

        if best_idx is None:
            return None

        snap = self._dormant.pop(best_idx)

        # Create fresh track with the new detection data but the OLD id
        new_track = DancerTrack(keypoints, confidence, bbox, self.smoothing_depth)
        new_track.track_id = snap.track_id  # ← resurrect the old ID

        if TRACKER_DEBUG:
            print(f"[TRACKER] Resurrected track #{snap.track_id} from dormant "
                  f"(dormant_age={snap.age}, score={best_score:.1f})")

        return new_track

    def update(self, detections):
        """
        Update tracker with new detections.
        
        Args:
            detections: List of (keypoints, confidence, bbox) tuples
        
        Returns:
            List of DancerTrack objects for confirmed tracks
        """
        self.frame_count += 1
        
        # Predict
        for track in self.tracks:
            track.predict()
        
        # Match
        cost_matrix = self._compute_cost_matrix(detections, self.tracks)
        
        matched_det = set()
        matched_trk = set()
        
        if cost_matrix.size > 0:
            # Sanitize cost matrix: NaN/inf entries crash linear_sum_assignment
            if not np.all(np.isfinite(cost_matrix)):
                cost_matrix = np.nan_to_num(
                    cost_matrix, nan=1e6, posinf=1e6, neginf=1e6
                )
            row_idx, col_idx = linear_sum_assignment(cost_matrix)
            
            for row, col in zip(row_idx, col_idx):
                track = self.tracks[col]
                track_speed = track.get_speed()
                
                # Dynamic threshold: base + velocity bonus + time bonus
                # Time bonus is capped to prevent runaway expansion that
                # would accept cross-matches after a few missed frames.
                time_bonus = min(
                    track.time_since_update * 10.0,
                    self.distance_threshold * 0.5,
                )
                dynamic_thresh = self.distance_threshold + track_speed * 1.5 + time_bonus
                
                if cost_matrix[row, col] < dynamic_thresh:
                    matched_det.add(row)
                    matched_trk.add(col)
                    kpts, conf, bbox = detections[row]
                    track.update(kpts, conf, bbox)
                else:
                    # Debug: print why match failed
                    if TRACKER_DEBUG:
                        print(f"[TRACKER] Match rejected: cost={cost_matrix[row, col]:.1f} > thresh={dynamic_thresh:.1f} (t_miss={track.time_since_update})")
        
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
                if min_dist > self.new_track_min_distance:
                    resurrected = self._try_resurrect(kpts, conf, bbox, det_centroid)
                    if resurrected is not None:
                        self.tracks.append(resurrected)
                    else:
                        if TRACKER_DEBUG:
                            print(f"[TRACKER] New track #{DancerTrack._id_counter + 1}: "
                                  f"min_dist={min_dist:.1f} > gate={self.new_track_min_distance}")
                        self.tracks.append(DancerTrack(kpts, conf, bbox, self.smoothing_depth))
                elif closest_track is not None and closest_track_idx not in matched_trk:
                    # Close to an unmatched track → force-update it
                    if TRACKER_DEBUG:
                        print(f"[TRACKER] Force update track #{closest_track.track_id}: dist={min_dist:.1f}")
                    closest_track.update(kpts, conf, bbox)
                    matched_trk.add(closest_track_idx)
                elif closest_track is not None and min_dist < self.duplicate_distance:
                    # Very close to an already-matched track → duplicate, drop
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
                                if TRACKER_DEBUG:
                                    print(f"[TRACKER] Fallback update track #{track.track_id}: dist={dist:.1f}")
                                track.update(kpts, conf, bbox)
                                matched_trk.add(idx)
                                break
                    else:
                        if TRACKER_DEBUG:
                            print(f"[TRACKER] Ignoring ambiguous det near track "
                                  f"#{closest_track.track_id if closest_track else 'None'}: dist={min_dist:.1f}")
        
        # Move expired tracks to the dormant pool (for later re-ID)
        still_alive = []
        for t in self.tracks:
            if t.time_since_update >= self.max_age:
                self._dormant.append(DormantSnapshot(t))
                if TRACKER_DEBUG:
                    print(f"[TRACKER] Track #{t.track_id} → dormant pool")
            else:
                still_alive.append(t)
        self.tracks = still_alive

        # Age out the dormant pool
        for snap in self._dormant:
            snap.age += 1
        expired = [s for s in self._dormant if s.age >= self.dormant_max_age]
        if expired and TRACKER_DEBUG:
            print(f"[TRACKER] Dormant expired: {[s.track_id for s in expired]}")
        self._dormant = [s for s in self._dormant if s.age < self.dormant_max_age]
        
        # Return confirmed tracks
        confirmed = []
        for track in self.tracks:
            if track.hits >= self.min_hits or self.frame_count <= self.min_hits:
                confirmed.append(track)
        
        return confirmed
