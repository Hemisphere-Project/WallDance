"""
Visualization helpers for WallDance.
"""

import cv2
import numpy as np
from config import (
    SKELETON, DANCER_COLORS, KEYPOINT_CONFIDENCE,
    SHOW_SKELETON, SHOW_KEYPOINTS, SHOW_BBOX, SHOW_TRAILS, SHOW_ID
)


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
    
    # Draw ID
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


def draw_info_overlay(frame, fps, num_dancers, enhancement_status, upscale_factor, settings):
    """Draw information overlay with current settings."""
    h, w = frame.shape[:2]
    
    # Semi-transparent background for info panel
    overlay = frame.copy()
    cv2.rectangle(overlay, (10, 10), (320, 180), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)
    
    # Title
    cv2.putText(frame, "WallDance", (20, 35), 
               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
    
    # Stats
    y = 60
    cv2.putText(frame, f"FPS: {fps:.1f}", (20, y), 
               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    cv2.putText(frame, f"Dancers: {num_dancers}", (150, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    
    # Settings with toggle indicators
    y += 28
    enh_on = enhancement_status['enhanced']
    cv2.putText(frame, f"[E] Enhance: {'ON' if enh_on else 'OFF'}", (20, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255) if enh_on else (100, 100, 100), 1)
    cv2.putText(frame, f"Brightness: {enhancement_status['brightness']:.0f}", (180, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
    
    y += 22
    cv2.putText(frame, f"[+/-] Upscale: {upscale_factor:.1f}x", (20, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    
    y += 22
    trail_on = settings.get('trails', True)
    cv2.putText(frame, f"[T] Trails: {'ON' if trail_on else 'OFF'}", (20, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255) if trail_on else (100, 100, 100), 1)
    
    skel_on = settings.get('skeleton', True)
    cv2.putText(frame, f"[S] Skel: {'ON' if skel_on else 'OFF'}", (160, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255) if skel_on else (100, 100, 100), 1)
    
    y += 22
    bbox_on = settings.get('bbox', True)
    cv2.putText(frame, f"[B] Box: {'ON' if bbox_on else 'OFF'}", (20, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255) if bbox_on else (100, 100, 100), 1)
    
    kpts_on = settings.get('keypoints', True)
    cv2.putText(frame, f"[K] Kpts: {'ON' if kpts_on else 'OFF'}", (130, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255) if kpts_on else (100, 100, 100), 1)
    
    ids_on = settings.get('ids', True)
    cv2.putText(frame, f"[I] IDs: {'ON' if ids_on else 'OFF'}", (240, y),
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255) if ids_on else (100, 100, 100), 1)


def draw_help_overlay(frame):
    """Draw full keyboard shortcut help overlay."""
    h, w = frame.shape[:2]
    
    # Semi-transparent full overlay
    overlay = frame.copy()
    box_w, box_h = 400, 320
    box_x = (w - box_w) // 2
    box_y = (h - box_h) // 2
    cv2.rectangle(overlay, (box_x, box_y), (box_x + box_w, box_y + box_h), (30, 30, 30), -1)
    cv2.rectangle(overlay, (box_x, box_y), (box_x + box_w, box_y + box_h), (0, 200, 255), 2)
    cv2.addWeighted(overlay, 0.9, frame, 0.1, 0, frame)
    
    # Title
    cv2.putText(frame, "KEYBOARD SHORTCUTS", (box_x + 80, box_y + 35), 
               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
    cv2.line(frame, (box_x + 20, box_y + 50), (box_x + box_w - 20, box_y + 50), (0, 200, 255), 1)
    
    shortcuts = [
        ("Q", "Quit application"),
        ("H", "Toggle this help"),
        ("", ""),
        ("E", "Toggle enhancement"),
        ("+/-", "Adjust upscale factor"),
        ("", ""),
        ("T", "Toggle motion trails"),
        ("S", "Toggle skeleton"),
        ("K", "Toggle keypoints"),
        ("B", "Toggle bounding box"),
        ("I", "Toggle dancer IDs"),
        ("", ""),
        ("R", "Reset tracker"),
    ]
    
    y = box_y + 75
    for key, desc in shortcuts:
        if key == "":
            y += 8
            continue
        cv2.putText(frame, key, (box_x + 30, y), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(frame, desc, (box_x + 100, y), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1)
        y += 22
    
    # Footer
    cv2.putText(frame, "Press H to close", (box_x + 130, box_y + box_h - 15), 
               cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
