# 03-Yolo11m - Multi-Person Pose Detection

Real-time multi-person pose detection using YOLO11m-pose, optimized for low-light environments.

## Features

- **Multi-person detection**: Tracks multiple people simultaneously
- **Low-light enhancement**: CLAHE-based image enhancement (toggle with 'e' key)
- **Color-coded skeletons**: Each person gets a unique color
- **Fast inference**: YOLO11m provides good speed/accuracy balance

## Requirements

- Python 3.10+
- CUDA-compatible GPU (recommended) or CPU
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
- `e` - Toggle low-light enhancement

## Model Variants

You can change the model in `main.py`:

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| `yolo11n-pose.pt` | 6MB | Fastest | Lower |
| `yolo11s-pose.pt` | 23MB | Fast | Good |
| `yolo11m-pose.pt` | 50MB | Medium | Better |
| `yolo11l-pose.pt` | 87MB | Slower | High |
| `yolo11x-pose.pt` | 136MB | Slowest | Highest |

## Output

- 17 keypoints per person (COCO format)
- Skeleton visualization with colored bones
- Person count display
