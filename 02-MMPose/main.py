from mmpose.apis import MMPoseInferencer
import cv2

# Initialize the pose estimator
inferencer = MMPoseInferencer(
    pose2d='human',
    device='cuda'  # or 'cpu' if no GPU
)

# Open webcam
cap = cv2.VideoCapture(0)

while True:
    ret, frame = cap.read()
    if not ret:
        break
    
    # Run pose estimation (returns a generator)
    result_generator = inferencer(frame, return_vis=True)
    result = next(result_generator)
    vis_frame = result['visualization'][0]
    
    # Display skeleton and keypoints
    cv2.imshow('Pose Detection', vis_frame)
    
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()