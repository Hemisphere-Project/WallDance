"""
Visualization helpers for WallDance.
Draws skeleton, keypoints, bounding boxes, and trails on video frames.
"""

import cv2
import numpy as np
from core.config import SKELETON, DANCER_COLORS, KEYPOINT_CONFIDENCE


def get_dancer_color(track_id):
    """Get consistent color for dancer ID."""
    return DANCER_COLORS[(track_id - 1) % len(DANCER_COLORS)]


def draw_dancer(frame, track, show_skeleton=True, show_keypoints=True,
                show_bbox=True, show_trail=True, show_id=True, thickness_scale: float = 1.0):
    """Draw single dancer visualization."""
    # Normalize scale to avoid vanishing or oversized strokes when preview is scaled
    scale = max(0.3, thickness_scale)
    color = get_dancer_color(track.track_id)
    keypoints = track.keypoints
    confidence = track.confidence
    
    # Draw trail
    if show_trail and len(track.history) > 1:
        points = list(track.history)
        for i in range(1, len(points)):
            alpha = i / len(points)
            thickness = max(1, int(3 * alpha * scale))
            pt1 = tuple(map(int, points[i-1]))
            pt2 = tuple(map(int, points[i]))
            cv2.line(frame, pt1, pt2, color, thickness)
    
    # Draw bounding box
    # Draw bounding box
    is_bridged = getattr(track, 'is_bridged', False)
    if show_bbox:
        x, y, w, h = track.bbox
        bbox_thickness = max(1, int(2 * scale))
        cv2.rectangle(frame, (int(x), int(y)), (int(x+w), int(y+h)), color, bbox_thickness)
    
    # Draw skeleton (skip for bridged tracks — keypoints are frozen)
    if show_skeleton and not is_bridged:
        for start_idx, end_idx in SKELETON:
            if confidence[start_idx] > KEYPOINT_CONFIDENCE and confidence[end_idx] > KEYPOINT_CONFIDENCE:
                x1, y1 = keypoints[start_idx]
                x2, y2 = keypoints[end_idx]
                cv2.line(frame, (int(x1), int(y1)), (int(x2), int(y2)), color, max(1, int(2 * scale)))
    
    # Draw keypoints (skip for bridged tracks — keypoints are frozen)
    if show_keypoints and not is_bridged:
        for i, (x, y) in enumerate(keypoints):
            if confidence[i] > KEYPOINT_CONFIDENCE:
                radius = max(2, int(4 * scale))
                outline_radius = max(radius + 1, int(5 * scale))
                cv2.circle(frame, (int(x), int(y)), radius, color, -1)
                cv2.circle(frame, (int(x), int(y)), outline_radius, (255, 255, 255), 1)
    
    # Draw ID label
    if show_id:
        bbox = track.bbox
        box_x, box_y = int(bbox[0]), int(bbox[1])

        label = f"D{track.track_id}" + ("[M]" if is_bridged else "")

        # Position label above the top edge of the bounding box
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = max(0.4, 0.7 * scale)
        font_thickness = max(1, int(2 * scale))
        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, font_thickness)
        label_x = box_x
        label_y = box_y - max(4, int(6 * scale))  # just above top edge
        # Dark background for readability
        cv2.rectangle(frame,
                      (label_x - 1, label_y - th - 2),
                      (label_x + tw + 2, label_y + baseline + 1),
                      (0, 0, 0), -1)
        cv2.putText(frame, label, (label_x, label_y), font,
                    font_scale, color, font_thickness)
