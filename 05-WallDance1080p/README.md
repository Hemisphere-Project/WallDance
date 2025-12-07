# WallDance 1080p

Multi-person pose detection system optimized for **vertical wall dancers** in **low-light outdoor** conditions.

## Features

- **DearPyGui Control Panel**: Modern GUI with sliders, buttons, and real-time video
- **Small figure detection**: 2-3x upscaling for 50m wide scenes
- **Low-light enhancement**: Adaptive CLAHE + gamma correction
- **Multi-person tracking**: Kalman filter + Hungarian algorithm
- **OSC output**: Real-time data for VJ/lighting software
- **6 dancers**: Optimized for up to 6 simultaneous performers

---

## Quick Start

```bash
# Install
chmod +x install.sh run.sh
./install.sh

# Run
./run.sh
```

---

## GUI Controls

The application features a modern DearPyGui control panel with:

### Detection Settings
- **YOLO Model**: Select model variant (n=fastest, s, m, l, x=most accurate)
- **FP16 Half Precision**: Enable half-precision inference (~20-30% faster on CUDA)
- **Frame Skip**: Skip N frames between YOLO inference (0=none, higher=faster)
- **Confidence slider**: Adjust YOLO detection threshold (0.1-0.9)
- **Max Persons**: Limit tracked dancers (1-12)

### Enhancement Settings
- **Enable Enhancement**: Toggle CLAHE + gamma
- **CLAHE Clip Limit**: Adjust contrast boost (1.0-6.0)
- **Gamma Correction**: Adjust brightness (0.5-2.5)

### Upscaling
- **Upscale Factor**: Slider + quick buttons (1x, 1.5x, 2x, 2.5x, 3x)

### Visualization
- **Skeleton**: Toggle skeleton lines
- **Keypoints**: Toggle joint circles
- **Bounding Box**: Toggle person boxes
- **Motion Trails**: Toggle movement history
- **Dancer IDs**: Toggle ID labels

### Tracker
- **Distance Threshold**: Match distance in pixels
- **Max Age**: Frames before lost track deletion
- **Reset Tracker**: Clear all track IDs

### OSC Output
- **Enable OSC**: Toggle OSC sending
- **Target IP/Port**: Configure destination

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Q` | Quit |
| `E` | Toggle enhancement |
| `S` | Toggle skeleton |
| `K` | Toggle keypoints |
| `B` | Toggle bounding box |
| `T` | Toggle motion trails |
| `I` | Toggle dancer IDs |
| `R` | Reset tracker |
| `+/-` | Adjust upscale factor |

---

## Configuration

All parameters are in **`config.py`**. Key settings:

### Image Processing

```python
UPSCALE_FACTOR = 2.0        # 1.0=native, 2.0=4K, 3.0=6K
                            # Higher = better small detection, slower

CLAHE_CLIP_LIMIT = 3.0      # Contrast enhancement strength (1-5)
GAMMA_CORRECTION = 1.2      # Brightness boost (1.0=none, 1.5=bright)
BRIGHTNESS_THRESHOLD = 60   # Auto-enhance below this brightness
```

### Detection

```python
YOLO_MODEL = "yolo11m-pose.pt"  # n/s/m/l/x variants available
YOLO_CONFIDENCE = 0.25          # Detection threshold
MAX_PERSONS = 6                 # Maximum dancers
```

### Performance Options (runtime adjustable)

| Option | Values | Effect |
|--------|--------|--------|
| Model | yolo11n/s/m/l/x-pose | Smaller = faster, larger = more accurate |
| FP16 | ON/OFF | ~20-30% faster on CUDA GPUs |
| Frame Skip | 0-4 | 0=process all, 1=every 2nd, 2=every 3rd... |

### Tracking

```python
TRACKER_MAX_AGE = 20            # Frames to keep lost track
TRACKER_MIN_HITS = 2            # Hits to confirm track
TRACKER_DISTANCE_THRESHOLD = 300  # Match distance (pixels)
```

### OSC Output

```python
OSC_ENABLED = True
OSC_IP = "127.0.0.1"
OSC_PORT = 9000
```

---

## OSC Messages

All coordinates are **normalized (0-1)** relative to frame dimensions.

| Address | Arguments | Description |
|---------|-----------|-------------|
| `/walldance/count` | `[n]` | Number of tracked dancers |
| `/walldance/dancer/<id>/centroid` | `[x, y]` | Dancer center position |
| `/walldance/dancer/<id>/bbox` | `[x, y, w, h]` | Bounding box |
| `/walldance/dancer/<id>/velocity` | `[vx, vy]` | Movement velocity |
| `/walldance/dancer/<id>/keypoints` | `[x0,y0,c0, ...]` | 17 keypoints (51 floats) |
| `/walldance/clear` | `[1]` | Tracker was reset |

### Keypoint Order (COCO)

```
0: nose          5: left_shoulder   10: right_wrist   15: left_ankle
1: left_eye      6: right_shoulder  11: left_hip      16: right_ankle
2: right_eye     7: left_elbow      12: right_hip
3: left_ear      8: right_elbow     13: left_knee
4: right_ear     9: left_wrist      14: right_knee
```

---

## Tuning Guide

### For Very Dark Scenes

```python
CLAHE_CLIP_LIMIT = 4.0      # Increase contrast
GAMMA_CORRECTION = 1.5      # Brighten more
BRIGHTNESS_THRESHOLD = 80   # Enhance more often
YOLO_CONFIDENCE = 0.2       # Accept weaker detections
```

### For Very Small Figures

```python
UPSCALE_FACTOR = 3.0        # More upscaling (slower)
YOLO_MODEL = "yolo11l-pose.pt"  # Larger model
```

### For Fast Movement

```python
TRACKER_DISTANCE_THRESHOLD = 400
TRACKER_VELOCITY_WEIGHT = 0.7
TRACKER_PROCESS_NOISE = 3.0
```

### For Stable/Slow Movement

```python
TRACKER_DISTANCE_THRESHOLD = 200
TRACKER_MIN_HITS = 3
TRACKER_MEASUREMENT_NOISE = 5.0
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Camera (1080p)                         │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Adaptive Enhancement                            │
│   ┌─────────────┐  ┌────────────┐  ┌──────────────────┐     │
│   │ Brightness  │→ │   CLAHE    │→ │ Gamma Correction │     │
│   │  Detection  │  │            │  │                  │     │
│   └─────────────┘  └────────────┘  └──────────────────┘     │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   Upscale (2x default)                       │
│                   1080p → 4K equivalent                      │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    YOLO11m-pose                              │
│              Multi-person pose detection                     │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Kalman + Hungarian Tracker                      │
│   ┌──────────────┐  ┌────────────────┐  ┌──────────────┐    │
│   │   Predict    │→ │    Match       │→ │   Update     │    │
│   │   (Kalman)   │  │  (Hungarian)   │  │   Tracks     │    │
│   └──────────────┘  └────────────────┘  └──────────────┘    │
└─────────────────────────┬───────────────────────────────────┘
                          ▼
         ┌────────────────┴────────────────┐
         ▼                                 ▼
┌─────────────────┐               ┌─────────────────┐
│ DearPyGui Panel │               │   OSC Output    │
│  (Video + GUI)  │               │  (python-osc)   │
└─────────────────┘               └─────────────────┘
```

---

## Files

```
05-WallDance1080p/
├── config.py         # All tunable parameters
├── main.py           # Main application
├── gui.py            # DearPyGui control panel
├── enhancer.py       # Low-light image enhancement
├── tracker.py        # Kalman + Hungarian tracking
├── osc_output.py     # OSC message sender
├── visualization.py  # Drawing helpers
├── install.sh        # Setup script
├── run.sh            # Run script
└── README.md         # This file
```

---

## Performance

Expected FPS on RTX 3090 with 1080p input:

| Upscale | Resolution | FPS (yolo11m) | FPS (yolo11n) |
|---------|------------|---------------|---------------|
| 1.0x    | 1920×1080  | 40-50         | 60-80         |
| 1.5x    | 2880×1620  | 30-40         | 50-60         |
| 2.0x    | 3840×2160  | 20-30         | 35-45         |
| 3.0x    | 5760×3240  | 12-18         | 20-28         |

### Performance Boost Options

| Optimization | Speedup | Trade-off |
|--------------|---------|----------|
| FP16 enabled | +20-30% | Minimal accuracy loss |
| Frame skip 1 | +50-100% | Tracks interpolated |
| Frame skip 2 | +100-200% | More interpolation |
| yolo11n instead of m | +80-100% | Lower accuracy |

---

## Troubleshooting

### No detection at all
- Check camera is working: `ffplay /dev/video0`
- Reduce `YOLO_CONFIDENCE` to 0.15
- Increase `UPSCALE_FACTOR`

### Detections flicker
- Increase `TRACKER_MIN_HITS` 
- Increase `TRACKER_MAX_AGE`
- Reduce `YOLO_CONFIDENCE` slightly

### IDs keep changing
- Increase `TRACKER_DISTANCE_THRESHOLD`
- Increase `TRACKER_VELOCITY_WEIGHT`
- Check if lighting causes detection gaps

### Too slow
- Enable **FP16** in Detection panel (~20-30% faster)
- Increase **Frame Skip** to 1 or 2 (skip frames between YOLO inference)
- Switch to faster model: **yolo11n-pose** or **yolo11s-pose**
- Reduce `UPSCALE_FACTOR`
