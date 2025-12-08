"""
Lightweight multi-person tracker using Kalman Filter + Hungarian Algorithm.
Tracks persons across frames based on pose keypoint positions.

TUNABLE PARAMETERS (cursors):
-----------------------------
In PoseTracker.__init__:
  - max_age: Frames to keep a lost track (higher = more persistent, but more ghosts)
  - min_hits: Detections needed before track is confirmed (higher = less false tracks)
  - distance_threshold: Max pixels between prediction and detection for matching
                        (higher = tolerates faster movement, but may merge people)

In PoseTrack.__init__ (Kalman filter tuning):
  - R (measurement noise): Higher = smoother but slower response
  - Q (process noise): Higher = faster response but more jitter
  - P (initial covariance): Higher = less trust in initial state

For fast movement scenarios, increase:
  - distance_threshold (try 200-400)
  - Q (process noise) for faster velocity adaptation
  - max_age to keep tracks during brief occlusions
"""

import numpy as np
from scipy.optimize import linear_sum_assignment
from filterpy.kalman import KalmanFilter
from collections import deque


class PoseTrack:
    """Single person track with Kalman filter for position smoothing."""
    
    _id_counter = 0
    
    def __init__(self, keypoints, confidence, 
                 process_noise=1.0, measurement_noise=5.0):
        """
        Initialize a new track.
        
        Args:
            keypoints: (17, 2) array of (x, y) positions
            confidence: (17,) array of confidence scores
            process_noise: Q - higher = faster adaptation to velocity changes
            measurement_noise: R - higher = smoother but slower response
        """
        PoseTrack._id_counter += 1
        self.track_id = PoseTrack._id_counter
        self.keypoints = keypoints.copy()
        self.confidence = confidence.copy()
        self.hits = 1
        self.age = 0
        self.time_since_update = 0
        
        # Store last velocity for prediction during occlusion
        self.last_velocity = np.array([0.0, 0.0])
        
        # History for visualization
        self.history = deque(maxlen=30)
        
        # Initialize Kalman filter for centroid tracking
        # State: [x, y, vx, vy, ax, ay] (position, velocity, acceleration)
        self.kf = KalmanFilter(dim_x=6, dim_z=2)
        
        # State transition matrix (constant acceleration model)
        dt = 1.0  # time step
        self.kf.F = np.array([
            [1, 0, dt, 0,  0.5*dt**2, 0],
            [0, 1, 0,  dt, 0,         0.5*dt**2],
            [0, 0, 1,  0,  dt,        0],
            [0, 0, 0,  1,  0,         dt],
            [0, 0, 0,  0,  1,         0],
            [0, 0, 0,  0,  0,         1]
        ])
        
        # Measurement matrix (we only observe position)
        self.kf.H = np.array([
            [1, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0]
        ])
        
        # Measurement noise - how much we trust detections
        self.kf.R = np.eye(2) * measurement_noise
        
        # Process noise - how much velocity/acceleration can change
        q = process_noise
        self.kf.Q = np.array([
            [q*0.1, 0,     0,     0,     0,     0],
            [0,     q*0.1, 0,     0,     0,     0],
            [0,     0,     q*1.0, 0,     0,     0],
            [0,     0,     0,     q*1.0, 0,     0],
            [0,     0,     0,     0,     q*2.0, 0],
            [0,     0,     0,     0,     0,     q*2.0]
        ])
        
        # Initial covariance - uncertainty in initial state
        self.kf.P = np.eye(6) * 100.0
        self.kf.P[2:4, 2:4] *= 10.0  # Higher uncertainty for velocity
        self.kf.P[4:6, 4:6] *= 10.0  # Higher uncertainty for acceleration
        
        # Initialize state with centroid
        centroid = self._compute_centroid(keypoints, confidence)
        self.kf.x = np.zeros((6, 1))
        self.kf.x[:2] = centroid.reshape(2, 1)
        
        self.history.append(centroid)
    
    def _compute_centroid(self, keypoints, confidence, thresh=0.3):
        """Compute weighted centroid of visible keypoints."""
        mask = confidence > thresh
        if not np.any(mask):
            return np.mean(keypoints, axis=0)
        
        weights = confidence[mask]
        points = keypoints[mask]
        centroid = np.average(points, axis=0, weights=weights)
        return centroid
    
    def predict(self):
        """Predict next state using Kalman filter."""
        self.kf.predict()
        self.age += 1
        self.time_since_update += 1
        
        # Store velocity for external use
        self.last_velocity = self.kf.x[2:4].flatten()
        
        return self.kf.x[:2].flatten()
    
    def update(self, keypoints, confidence):
        """Update track with new detection."""
        self.keypoints = keypoints.copy()
        self.confidence = confidence.copy()
        self.hits += 1
        self.time_since_update = 0
        
        # Update Kalman filter with new centroid
        centroid = self._compute_centroid(keypoints, confidence)
        self.kf.update(centroid.reshape(2, 1))
        
        self.last_velocity = self.kf.x[2:4].flatten()
        self.history.append(centroid)
    
    def get_state(self):
        """Get current estimated position."""
        return self.kf.x[:2].flatten()
    
    def get_velocity(self):
        """Get current estimated velocity."""
        return self.kf.x[2:4].flatten()
    
    def get_speed(self):
        """Get current speed (magnitude of velocity)."""
        return np.linalg.norm(self.get_velocity())


class PoseTracker:
    """
    Multi-person pose tracker using Hungarian algorithm for assignment
    and Kalman filter for state estimation.
    
    TUNABLE PARAMETERS:
    -------------------
    max_age: int (default: 15)
        Maximum frames to keep a track without new detections.
        - Increase if people briefly disappear (occlusion, detection miss)
        - Decrease to remove ghost tracks faster
        
    min_hits: int (default: 2)
        Minimum detections before a track is confirmed/displayed.
        - Increase to reduce false positive tracks
        - Decrease for faster track initialization
        
    distance_threshold: float (default: 250.0)
        Maximum distance (pixels) for matching detection to track.
        - Increase for faster movement or lower framerate
        - Decrease if tracks are merging between nearby people
        
    velocity_weight: float (default: 0.5)
        How much to weight velocity prediction in matching.
        - Increase if movement is predictable/linear
        - Decrease if movement is erratic
        
    process_noise: float (default: 2.0)
        Kalman Q - how quickly to adapt to velocity changes.
        - Increase for fast/erratic movement
        - Decrease for smooth/slow movement
        
    measurement_noise: float (default: 3.0)
        Kalman R - how much to trust raw detections.
        - Increase for noisy detections (smoother output)
        - Decrease for responsive tracking
    """
    
    def __init__(self, 
                 max_age=15, 
                 min_hits=2, 
                 distance_threshold=250.0,
                 velocity_weight=0.5,
                 process_noise=2.0,
                 measurement_noise=3.0):
        """
        Initialize tracker.
        
        Args:
            max_age: Maximum frames to keep track without detection
            min_hits: Minimum hits before track is confirmed
            distance_threshold: Maximum distance for matching (pixels)
            velocity_weight: Weight for velocity-based prediction in matching
            process_noise: Kalman filter process noise (Q)
            measurement_noise: Kalman filter measurement noise (R)
        """
        self.max_age = max_age
        self.min_hits = min_hits
        self.distance_threshold = distance_threshold
        self.velocity_weight = velocity_weight
        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.tracks = []
        self.frame_count = 0
    
    def reset(self):
        """Reset tracker state."""
        self.tracks = []
        self.frame_count = 0
        PoseTrack._id_counter = 0
    
    def _compute_distance_matrix(self, detections, tracks):
        """
        Compute cost matrix based on centroid distance with velocity prediction.
        
        Args:
            detections: List of (keypoints, confidence) tuples
            tracks: List of PoseTrack objects
            
        Returns:
            Distance matrix of shape (num_detections, num_tracks)
        """
        if len(detections) == 0 or len(tracks) == 0:
            return np.empty((len(detections), len(tracks)))
        
        cost_matrix = np.zeros((len(detections), len(tracks)))
        
        for d, (kpts, conf) in enumerate(detections):
            det_centroid = self._compute_centroid(kpts, conf)
            
            for t, track in enumerate(tracks):
                # Get predicted position (already advanced by predict())
                predicted_pos = track.get_state()
                
                # Also consider velocity-adjusted position for fast movement
                velocity = track.get_velocity()
                velocity_adjusted_pos = predicted_pos + velocity * self.velocity_weight
                
                # Use the closer of the two predictions
                dist_predicted = np.linalg.norm(det_centroid - predicted_pos)
                dist_velocity = np.linalg.norm(det_centroid - velocity_adjusted_pos)
                
                # Weight by time since last update (penalize old tracks less for distance)
                time_factor = 1.0 + track.time_since_update * 0.1
                
                cost_matrix[d, t] = min(dist_predicted, dist_velocity) * time_factor
        
        return cost_matrix
    
    def _compute_centroid(self, keypoints, confidence, thresh=0.3):
        """Compute weighted centroid of visible keypoints."""
        mask = confidence > thresh
        if not np.any(mask):
            return np.mean(keypoints, axis=0)
        
        weights = confidence[mask]
        points = keypoints[mask]
        return np.average(points, axis=0, weights=weights)
    
    def update(self, detections):
        """
        Update tracker with new detections.
        
        Args:
            detections: List of (keypoints, confidence) tuples
                       keypoints: (17, 2) array of (x, y) positions
                       confidence: (17,) array of confidence scores
        
        Returns:
            List of (track_id, keypoints, confidence, history, velocity) for confirmed tracks
        """
        self.frame_count += 1
        
        # Predict new locations for existing tracks
        for track in self.tracks:
            track.predict()
        
        # Compute cost matrix
        cost_matrix = self._compute_distance_matrix(detections, self.tracks)
        
        # Hungarian algorithm for assignment
        matched_det = set()
        matched_trk = set()
        
        if cost_matrix.size > 0:
            row_indices, col_indices = linear_sum_assignment(cost_matrix)
            
            for row, col in zip(row_indices, col_indices):
                # Dynamic threshold based on track's velocity
                track_speed = self.tracks[col].get_speed()
                dynamic_threshold = self.distance_threshold + track_speed * 2.0
                
                if cost_matrix[row, col] < dynamic_threshold:
                    matched_det.add(row)
                    matched_trk.add(col)
                    # Update matched track
                    kpts, conf = detections[row]
                    self.tracks[col].update(kpts, conf)
        
        # Create new tracks for unmatched detections
        for d, (kpts, conf) in enumerate(detections):
            if d not in matched_det:
                # Check if this detection is far from all existing tracks
                # to avoid creating duplicate tracks
                is_new = True
                det_centroid = self._compute_centroid(kpts, conf)
                
                for track in self.tracks:
                    track_pos = track.get_state()
                    if np.linalg.norm(det_centroid - track_pos) < self.distance_threshold * 0.5:
                        is_new = False
                        break
                
                if is_new:
                    new_track = PoseTrack(
                        kpts, conf,
                        process_noise=self.process_noise,
                        measurement_noise=self.measurement_noise
                    )
                    self.tracks.append(new_track)
        
        # Remove old tracks
        self.tracks = [t for t in self.tracks if t.time_since_update < self.max_age]
        
        # Return confirmed tracks
        results = []
        for track in self.tracks:
            if track.hits >= self.min_hits or self.frame_count <= self.min_hits:
                results.append((
                    track.track_id,
                    track.keypoints,
                    track.confidence,
                    track.history,
                    track.get_velocity()
                ))
        
        return results
