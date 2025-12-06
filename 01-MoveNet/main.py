import cv2
import numpy as np
import tensorflow as tf
import tensorflow_hub as hub
import time

# 1. Load MoveNet Lightning (Fast, Single Pose) from TF Hub
print("Loading MoveNet Lightning...")
# Note: The first run will download the model (approx 30MB) to /tmp or local cache
model = hub.load("https://tfhub.dev/google/movenet/singlepose/lightning/4")
movenet = model.signatures['serving_default']
print("Model loaded!")

# Threshold
SCORE_THRESH = 0.3      # Confidence to draw a keypoint

def run_inference(frame_bgr):
    """Pre-process and run MoveNet inference."""
    # Resize and pad to 192x192 (MoveNet Lightning input)
    img = tf.image.resize_with_pad(tf.expand_dims(frame_bgr, axis=0), 192, 192)
    input_image = tf.cast(img, dtype=tf.int32)
    
    # Run model
    outputs = movenet(input_image)
    # Output shape: [1, 1, 17, 3] -> (y, x, score)
    keypoints = outputs['output_0'].numpy()[0, 0, :, :]
    return keypoints

def draw_skeleton(frame, keypoints):
    """Draw keypoints and skeleton connections."""
    h, w, _ = frame.shape
    color = (0, 255, 0)  # Green
    
    # MoveNet keypoint indices
    # 0: nose, 1: left_eye, 2: right_eye, 3: left_ear, 4: right_ear,
    # 5: left_shoulder, 6: right_shoulder, 7: left_elbow, 8: right_elbow,
    # 9: left_wrist, 10: right_wrist, 11: left_hip, 12: right_hip,
    # 13: left_knee, 14: right_knee, 15: left_ankle, 16: right_ankle
    
    # Define skeleton connections (bones)
    connections = [
        (0, 1), (0, 2), (1, 3), (2, 4),   # Face
        (5, 6), (5, 7), (7, 9),           # Left arm
        (6, 8), (8, 10),                  # Right arm
        (5, 11), (6, 12), (11, 12),       # Torso
        (11, 13), (13, 15),               # Left leg
        (12, 14), (14, 16)                # Right leg
    ]
    
    # Draw connections (bones)
    for start_idx, end_idx in connections:
        y1, x1, score1 = keypoints[start_idx]
        y2, x2, score2 = keypoints[end_idx]
        
        if score1 > SCORE_THRESH and score2 > SCORE_THRESH:
            cx1, cy1 = int(x1 * w), int(y1 * h)
            cx2, cy2 = int(x2 * w), int(y2 * h)
            cv2.line(frame, (cx1, cy1), (cx2, cy2), color, 2)
    
    # Draw keypoints
    for y, x, score in keypoints:
        if score > SCORE_THRESH:
            cx, cy = int(x * w), int(y * h)
            cv2.circle(frame, (cx, cy), 5, color, -1)
    
    return frame

def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Webcam not found.")
        return
    
    print("Starting loop. Press 'q' to quit.")
    while True:
        ret, frame = cap.read()
        if not ret: break

        # Inference
        # Convert BGR to RGB for the model
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        keypoints = run_inference(rgb_frame)

        # Visualization
        frame = draw_skeleton(frame, keypoints)

        cv2.imshow('MoveNet Pose Detection', frame)
        
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
