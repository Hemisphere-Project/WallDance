"""
Multi-person pose detection with RTMPose-m and lightweight tracking.
Uses Kalman filter + Hungarian algorithm for consistent person IDs across frames.
"""

import cv2
import numpy as np
from mmpose.apis import MMPoseInferencer
from tracker import PoseTracker

# Skeleton connections (COCO format - 17 keypoints)
SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4),  # Face
    (5, 6), (5, 7), (7, 9),          # Left arm
    (6, 8), (8, 10),                 # Right arm
    (5, 11), (6, 12), (11, 12),      # Torso
    (11, 13), (13, 15),              # Left leg
    (12, 14), (14, 16)               # Right leg
]

# Colors for different track IDs (BGR)
COLORS = [
    (0, 255, 0),    # Green
    (255, 0, 0),    # Blue
    (0, 0, 255),    # Red
    (255, 255, 0),  # Cyan
    (255, 0, 255),  # Magenta
    (0, 255, 255),  # Yellow
    (128, 255, 0),  # Lime
    (255, 128, 0),  # Orange
    (128, 0, 255),  # Purple
    (0, 128, 255),  # Sky blue
]

# Thresholds
KPT_THRESH = 0.3


def get_color(track_id):
    """Get consistent color for track ID."""
    return COLORS[track_id % len(COLORS)]


def draw_skeleton(frame, keypoints, confidence, color, track_id, velocity=None, draw_trail=None):
    """Draw skeleton and track info for a single person."""
    h, w = frame.shape[:2]
    
    # Draw trail if available
    if draw_trail is not None and len(draw_trail) > 1:
        points = list(draw_trail)
        for i in range(1, len(points)):
            alpha = i / len(points)  # Fade effect
            thickness = max(1, int(2 * alpha))
            pt1 = tuple(map(int, points[i-1]))
            pt2 = tuple(map(int, points[i]))
            cv2.line(frame, pt1, pt2, color, thickness)
    
    # Draw skeleton connections
    for start_idx, end_idx in SKELETON:
        if confidence[start_idx] > KPT_THRESH and confidence[end_idx] > KPT_THRESH:
            x1, y1 = keypoints[start_idx]
            x2, y2 = keypoints[end_idx]
            pt1 = (int(x1), int(y1))
            pt2 = (int(x2), int(y2))
            cv2.line(frame, pt1, pt2, color, 2)
    
    # Draw keypoints
    for i, (x, y) in enumerate(keypoints):
        if confidence[i] > KPT_THRESH:
            cv2.circle(frame, (int(x), int(y)), 4, color, -1)
            cv2.circle(frame, (int(x), int(y)), 5, (255, 255, 255), 1)
    
    # Draw track ID and speed near head
    if confidence[0] > KPT_THRESH:  # Nose
        x, y = keypoints[0]
        speed = np.linalg.norm(velocity) if velocity is not None else 0
        label = f"ID:{track_id}"
        if speed > 5:  # Show speed if moving
            label += f" v:{speed:.0f}"
        cv2.putText(frame, label, (int(x) - 20, int(y) - 20),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)


def main():
    print("Loading RTMPose-m model...")
    
    # Initialize MMPose inferencer with RTMPose-m (uses 'human' preset)
    inferencer = MMPoseInferencer(
        pose2d='human',  # RTMPose medium model for human pose
        device='cuda'
    )
    print("Model loaded!")
    
    # Initialize tracker with tuned parameters for fast movement
    # ============================================================
    # TUNABLE CURSORS - adjust these for your scenario:
    # ============================================================
    tracker = PoseTracker(
        max_age=15,            # Frames to keep lost track (increase if brief occlusions)
        min_hits=2,            # Hits to confirm track (increase to reduce false tracks)
        distance_threshold=250, # Max match distance in pixels (increase for fast movement)
        velocity_weight=0.5,   # How much to trust velocity prediction (0-1)
        process_noise=2.0,     # Kalman Q: higher = faster velocity adaptation
        measurement_noise=3.0  # Kalman R: higher = smoother but slower response
    )
    
    # Open webcam
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Webcam not found.")
        return
    
    show_trails = True
    
    print("Starting pose detection with tracking.")
    print("Press 'q' to quit, 't' to toggle trails, 'r' to reset tracks")
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        # Run pose estimation
        result_generator = inferencer(frame, return_vis=False)
        result = next(result_generator)
        
        # Extract detections
        detections = []
        if 'predictions' in result and len(result['predictions']) > 0:
            for pred in result['predictions'][0]:
                keypoints = np.array(pred['keypoints'])  # (17, 2)
                scores = np.array(pred['keypoint_scores'])  # (17,)
                detections.append((keypoints, scores))
        
        # Update tracker
        tracked_poses = tracker.update(detections)
        
        # Draw tracked poses
        for track_id, keypoints, confidence, history, velocity in tracked_poses:
            color = get_color(track_id)
            trail = history if show_trails else None
            draw_skeleton(frame, keypoints, confidence, color, track_id, velocity, trail)
        
        # Draw info
        cv2.putText(frame, f"Tracked: {len(tracked_poses)}", (20, 40),
                   cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        cv2.putText(frame, f"Trails: {'ON' if show_trails else 'OFF'}", (20, 80),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        cv2.imshow('RTMPose + Tracker', frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('t'):
            show_trails = not show_trails
            print(f"Trails: {'ON' if show_trails else 'OFF'}")
        elif key == ord('r'):
            tracker.reset()
            print("Tracker reset")
    
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
