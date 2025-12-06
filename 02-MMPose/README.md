# 02-MMPose - Pose Detection with MMPose

Real-time pose detection using OpenMMLab's MMPose library with RTMPose model.

## Requirements

- Python 3.10+
- CUDA 12.1 compatible GPU (for GPU acceleration)
- Webcam

## Installation

```bash
./install.sh
```

This will:
1. Create a Python virtual environment with uv
2. Install PyTorch 2.4.0 with CUDA 12.1 support
3. Install mmcv with prebuilt CUDA ops
4. Install mmdet and mmpose

## Usage

```bash
./run.sh
```

Press `q` to quit.

## Notes

- The first run will download model weights (~150MB)
- Uses RTMPose-M model for human pose estimation
- For CPU-only usage, change `device='cuda'` to `device='cpu'` in `main.py`