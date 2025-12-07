"""
Visualization helpers for WallDance.
Draws skeleton, keypoints, bounding boxes, and trails on video frames.
"""

import cv2
import numpy as np
from config import SKELETON, DANCER_COLORS, KEYPOINT_CONFIDENCE


def get_dancer_color(track_id):
    """Get consistent color for dancer ID."""
    return DANCER_COLORS[(track_id - 1) % len(DANCER_COLORS)]


def draw_dancer(frame, track, show_skeleton=True, show_keypoints=True, 
                show_bbox=True, show_trail=True, show_id=True):
    """Draw single dancer visualization."""
    color = get_dancer_color(track.track_id)
    keypoints = track.keypoints
    confidence = track.confidence
    
    # Draw trail
    if show_trail and len(track.history) > 1:
        points = list(track.history)
        for i in range(1, len(points)):
            alpha = i / len(points)
            thickness = max(1, int(3 * alpha))
            pt1 = tuple(map(int, points[i-1]))
            pt2 = tuple(map(int, points[i]))
            cv2.line(frame, pt1, pt2, color, thickness)
    
    # Draw bounding box
    if show_bbox:
        x, y, w, h = track.bbox
        cv2.rectangle(frame, (int(x), int(y)), (int(x+w), int(y+h)), color, 2)
    
    # Draw skeleton
    if show_skeleton:
        for start_idx, end_idx in SKELETON:
            if confidence[start_idx] > KEYPOINT_CONFIDENCE and confidence[end_idx] > KEYPOINT_CONFIDENCE:
                x1, y1 = keypoints[start_idx]
                x2, y2 = keypoints[end_idx]
                cv2.line(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
    
    # Draw keypoints
    if show_keypoints:
        for i, (x, y) in enumerate(keypoints):
            if confidence[i] > KEYPOINT_CONFIDENCE:
                cv2.circle(frame, (int(x), int(y)), 4, color, -1)
                cv2.circle(frame, (int(x), int(y)), 5, (255, 255, 255), 1)
    
    # Draw ID label
    if show_id:
        # Compute centroid from bbox
        bbox = track.bbox
        centroid_x = bbox[0] + bbox[2] / 2
        centroid_y = bbox[1] + bbox[3] / 2
        
        # Compute speed from velocity
        speed = np.linalg.norm(track.velocity)
        
        label = f"D{track.track_id}"
        if speed > 5:
            label += f" v:{speed:.0f}"
        
        # Position label above centroid
        label_pos = (int(centroid_x) - 20, int(centroid_y) - 30)
        cv2.putText(frame, label, label_pos, cv2.FONT_HERSHEY_SIMPLEX, 
                   0.7, color, 2)
