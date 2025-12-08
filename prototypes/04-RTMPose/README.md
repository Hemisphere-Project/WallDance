# 04-RTMPose - Pose Detection with Tracking

Real-time multi-person pose detection using RTMPose-m with lightweight Kalman + Hungarian tracking.

## Features

- **RTMPose-m**: Fast and accurate pose estimation from OpenMMLab
- **Persistent IDs**: Kalman filter + Hungarian algorithm for consistent tracking
- **Motion trails**: Visualize movement history (toggle with 't')
- **Color-coded**: Each tracked person gets a unique color

## Requirements

- Python 3.10+
- CUDA 12.1 compatible GPU
- Webcam

## Installation

```bash
chmod +x install.sh run.sh
./install.sh
```

## Usage

```bash
./run.sh
```

### Controls

- `q` - Quit
- `t` - Toggle motion trails
- `r` - Reset tracker (clear all track IDs)

## How Tracking Works

1. **Detection**: RTMPose-m detects all persons and their keypoints
2. **Prediction**: Kalman filter predicts where each tracked person should be
3. **Association**: Hungarian algorithm matches detections to tracks by distance
4. **Update**: Matched tracks update their state; unmatched detections start new tracks
5. **Cleanup**: Tracks not seen for `max_age` frames are removed

## Configuration

In `main.py`, you can adjust tracker parameters:

```python
tracker = PoseTracker(
    max_age=30,        # Frames to keep track without detection
    min_hits=3,        # Hits needed to confirm a track
    iou_threshold=150  # Max distance (pixels) for matching
)
```

## Architecture

```
main.py         - Main application loop
tracker.py      - Kalman filter + Hungarian tracker implementation
```
