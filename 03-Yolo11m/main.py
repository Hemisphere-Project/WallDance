import cv2
import numpy as np
from ultralytics import YOLO

# Load YOLO11m-pose model (medium size, good balance of speed/accuracy)
# Downloads automatically on first run (~50MB)
print("Loading YOLO11m-pose model...")
model = YOLO("yolo11m-pose.pt")
print("Model loaded!")

# Pose keypoint indices (COCO format - 17 keypoints)
# 0: nose, 1: left_eye, 2: right_eye, 3: left_ear, 4: right_ear,
# 5: left_shoulder, 6: right_shoulder, 7: left_elbow, 8: right_elbow,
# 9: left_wrist, 10: right_wrist, 11: left_hip, 12: right_hip,
# 13: left_knee, 14: right_knee, 15: left_ankle, 16: right_ankle

# Skeleton connections
SKELETON = [
    (0, 1), (0, 2), (1, 3), (2, 4),  # Face
    (5, 6), (5, 7), (7, 9),          # Left arm
    (6, 8), (8, 10),                 # Right arm
    (5, 11), (6, 12), (11, 12),      # Torso
    (11, 13), (13, 15),              # Left leg
    (12, 14), (14, 16)               # Right leg
]

# Colors for different persons (BGR)
COLORS = [
    (0, 255, 0),    # Green
    (255, 0, 0),    # Blue
    (0, 0, 255),    # Red
    (255, 255, 0),  # Cyan
    (255, 0, 255),  # Magenta
    (0, 255, 255),  # Yellow
    (128, 255, 0),  # Lime
    (255, 128, 0),  # Orange
]

# Confidence threshold
CONF_THRESH = 0.3
KPT_THRESH = 0.5


def enhance_low_light(frame):
    """Enhance frame for better detection in low-light conditions."""
    # Convert to LAB color space
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    
    # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l = clahe.apply(l)
    
    # Merge and convert back
    lab = cv2.merge([l, a, b])
    enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    
    return enhanced


def draw_skeleton(frame, keypoints, color, conf_thresh=KPT_THRESH):
    """Draw skeleton for a single person."""
    # keypoints shape: (17, 3) -> (x, y, confidence)
    
    # Draw connections
    for start_idx, end_idx in SKELETON:
        x1, y1, c1 = keypoints[start_idx]
        x2, y2, c2 = keypoints[end_idx]
        
        if c1 > conf_thresh and c2 > conf_thresh:
            pt1 = (int(x1), int(y1))
            pt2 = (int(x2), int(y2))
            cv2.line(frame, pt1, pt2, color, 2)
    
    # Draw keypoints
    for x, y, conf in keypoints:
        if conf > conf_thresh:
            cv2.circle(frame, (int(x), int(y)), 4, color, -1)
            cv2.circle(frame, (int(x), int(y)), 5, (255, 255, 255), 1)


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Webcam not found.")
        return
    
    # Try to set camera properties for low-light
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)  # Enable auto exposure
    
    use_enhancement = True  # Toggle with 'e' key
    
    print("Starting pose detection. Press 'q' to quit, 'e' to toggle low-light enhancement.")
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        # Apply low-light enhancement if enabled
        if use_enhancement:
            process_frame = enhance_low_light(frame)
        else:
            process_frame = frame
        
        # Run YOLO inference
        results = model(process_frame, conf=CONF_THRESH, verbose=False)
        
        # Process results
        for result in results:
            if result.keypoints is not None and len(result.keypoints) > 0:
                # Get keypoints data
                keypoints_data = result.keypoints.data.cpu().numpy()
                
                # Draw each detected person
                for i, kpts in enumerate(keypoints_data):
                    color = COLORS[i % len(COLORS)]
                    draw_skeleton(frame, kpts, color)
                
                # Show person count
                num_persons = len(keypoints_data)
                cv2.putText(frame, f"Persons: {num_persons}", (20, 40),
                           cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        
        # Show enhancement status
        status = "Enhancement: ON" if use_enhancement else "Enhancement: OFF"
        cv2.putText(frame, status, (20, 80),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        
        cv2.imshow('YOLO11 Multi-Person Pose Detection', frame)
        
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('e'):
            use_enhancement = not use_enhancement
            print(f"Low-light enhancement: {'ON' if use_enhancement else 'OFF'}")
    
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
