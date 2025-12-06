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
    TRACKER_VELOCITY_WEIGHT, TRACKER_PROCESS_NOISE, TRACKER_MEASUREMENT_NOISE,
    KEYPOINT_CONFIDENCE
)


class DancerTrack:
    """Single dancer track with Kalman filter for position smoothing."""
    
    _id_counter = 0
    
    def __init__(self, keypoints, confidence, bbox):
        """
        Initialize a new track.
        
        Args:
            keypoints: (17, 2) array of (x, y) positions
            confidence: (17,) array of confidence scores
            bbox: (x, y, w, h) bounding box
        """
        DancerTrack._id_counter += 1
        self.track_id = DancerTrack._id_counter
        self.keypoints = keypoints.copy()
        self.confidence = confidence.copy()
        self.bbox = np.array(bbox)
        self.hits = 1
        self.age = 0
        self.time_since_update = 0
        
        # History for visualization
        self.history = deque(maxlen=30)
        
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
        return self.kf.x[:2].flatten()
    
    def update(self, keypoints, confidence, bbox):
        """Update track with new detection."""
        self.keypoints = keypoints.copy()
        self.confidence = confidence.copy()
        self.bbox = np.array(bbox)
        self.hits += 1
        self.time_since_update = 0
        
        centroid = self._compute_centroid(keypoints, confidence)
        self.kf.update(centroid.reshape(2, 1))
        self.history.append(centroid)
    
    def get_centroid(self):
        """Get current estimated centroid."""
        return self.kf.x[:2].flatten()
    
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
        self.distance_threshold = TRACKER_DISTANCE_THRESHOLD
        self.velocity_weight = TRACKER_VELOCITY_WEIGHT
    
    def reset(self):
        """Reset all tracks."""
        self.tracks = []
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
        """Compute assignment cost matrix."""
        if len(detections) == 0 or len(tracks) == 0:
            return np.empty((len(detections), len(tracks)))
        
        cost_matrix = np.zeros((len(detections), len(tracks)))
        
        for d, (kpts, conf, bbox) in enumerate(detections):
            det_centroid = self._compute_centroid(kpts, conf, bbox)
            
            for t, track in enumerate(tracks):
                predicted_pos = track.get_centroid()
                velocity = track.get_velocity()
                velocity_adjusted = predicted_pos + velocity * self.velocity_weight
                
                dist_pred = np.linalg.norm(det_centroid - predicted_pos)
                dist_vel = np.linalg.norm(det_centroid - velocity_adjusted)
                
                time_factor = 1.0 + track.time_since_update * 0.1
                cost_matrix[d, t] = min(dist_pred, dist_vel) * time_factor
        
        return cost_matrix
    
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
            row_idx, col_idx = linear_sum_assignment(cost_matrix)
            
            for row, col in zip(row_idx, col_idx):
                track_speed = self.tracks[col].get_speed()
                dynamic_thresh = self.distance_threshold + track_speed * 2.0
                
                if cost_matrix[row, col] < dynamic_thresh:
                    matched_det.add(row)
                    matched_trk.add(col)
                    kpts, conf, bbox = detections[row]
                    self.tracks[col].update(kpts, conf, bbox)
        
        # Create new tracks
        for d, (kpts, conf, bbox) in enumerate(detections):
            if d not in matched_det:
                det_centroid = self._compute_centroid(kpts, conf, bbox)
                is_new = True
                
                for track in self.tracks:
                    if np.linalg.norm(det_centroid - track.get_centroid()) < self.distance_threshold * 0.5:
                        is_new = False
                        break
                
                if is_new:
                    self.tracks.append(DancerTrack(kpts, conf, bbox))
        
        # Remove old tracks
        self.tracks = [t for t in self.tracks if t.time_since_update < self.max_age]
        
        # Return confirmed tracks
        confirmed = []
        for track in self.tracks:
            if track.hits >= self.min_hits or self.frame_count <= self.min_hits:
                confirmed.append(track)
        
        return confirmed
