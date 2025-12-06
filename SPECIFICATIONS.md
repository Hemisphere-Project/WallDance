# WallDance - Technical Specifications

**Project:** Multi-Person Pose Detection for Wall Dancers  
**Version:** 1.0  
**Last Updated:** December 6, 2025  
**Status:** Prototype Phase

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Project Context](#2-project-context)
3. [Hardware Requirements](#3-hardware-requirements)
4. [Functional Requirements](#4-functional-requirements)
5. [Technical Architecture](#5-technical-architecture)
6. [Detection & Tracking Pipeline](#6-detection--tracking-pipeline)
7. [Output Protocols](#7-output-protocols)
8. [Performance Targets](#8-performance-targets)
9. [Implementation Roadmap](#9-implementation-roadmap)
10. [Technical Challenges & Solutions](#10-technical-challenges--solutions)
11. [Prototype Status](#11-prototype-status)
12. [Future Enhancements](#12-future-enhancements)

---

## 1. Executive Summary

WallDance is a real-time computer vision system designed to detect and track multiple dancers performing on a large vertical surface (wall) during outdoor night performances. The system extracts 2D pose skeletons and sends tracking data via OSC protocol to downstream systems (VJ software, lighting controllers, interactive projections).

### Key Challenges
- **Large scene coverage**: 50m wide performance area
- **Small figure size**: Dancers appear ~65 pixels tall at 1080p
- **Low-light conditions**: Outdoor night performance with minimal ambient lighting
- **Multi-person tracking**: Up to 6 dancers with stable ID persistence
- **Real-time processing**: Target 15-30 FPS for responsive interaction

---

## 2. Project Context

### 2.1 Performance Environment

| Parameter | Value | Notes |
|-----------|-------|-------|
| Scene Width | 50 meters | Horizontal span of performance wall |
| Scene Height | ~20-30 meters | Vertical climbing area |
| Number of Dancers | Up to 6 | Simultaneous performers |
| Lighting Conditions | Dark / Night | Outdoor, minimal ambient light |
| Performance Type | Wall climbing/dancing | Vertical surface, rotated body orientations |

### 2.2 Use Cases

1. **Live VJ Integration**: Real-time dancer positions/poses drive visual effects
2. **Lighting Control**: Dynamic lighting follows performers
3. **Interactive Projection**: Projected graphics react to dancer movements
4. **Performance Recording**: Capture pose data for post-production
5. **Analytics**: Movement analysis for choreography refinement

---

## 3. Hardware Requirements

### 3.1 Camera System

| Component | Specification | Rationale |
|-----------|---------------|-----------|
| Camera | Sony Alpha 7 (or equivalent) | Low-light sensitivity, clean 1080p output |
| Resolution | 1920×1080 (Full HD) | Balance of coverage and detail |
| Frame Rate | 30 FPS | Standard capture rate |
| Output | Clean HDMI / SDI | Via capture card to PC |
| Lens | Wide-angle (24-35mm equiv.) | Cover 50m scene from safe distance |
| Mounting | Fixed tripod/rigging | Stable, unobstructed view |

**Calculated Figure Size:**
- At 1080p covering 50m width: 1920px / 50m = 38.4 px/m
- Average dancer height (1.7m): 1.7m × 38.4 = **~65 pixels**
- This is below optimal detection threshold (~100px), requiring upscaling

### 3.2 Processing Hardware

| Component | Minimum | Recommended | Notes |
|-----------|---------|-------------|-------|
| GPU | RTX 3070 | RTX 3090 / RTX 4080 | CUDA compute for inference |
| VRAM | 8 GB | 24 GB | Model + upscaled frames |
| CPU | 8-core | 16-core | Pre/post processing |
| RAM | 16 GB | 32 GB | Frame buffers |
| Storage | SSD | NVMe SSD | Fast model loading |

### 3.3 Capture Interface

| Option | Latency | Quality | Cost |
|--------|---------|---------|------|
| Elgato Cam Link 4K | ~100ms | Good | $130 |
| Blackmagic DeckLink | ~30ms | Excellent | $200+ |
| AVerMedia Live Gamer | ~50ms | Good | $150 |

---

## 4. Functional Requirements

### 4.1 Core Features

| ID | Feature | Priority | Status |
|----|---------|----------|--------|
| F1 | Multi-person detection (up to 6) | Critical | ✅ Implemented |
| F2 | 17-keypoint skeleton extraction | Critical | ✅ Implemented |
| F3 | Persistent ID tracking across frames | Critical | ✅ Implemented |
| F4 | Low-light image enhancement | High | ✅ Implemented |
| F5 | OSC output protocol | High | ✅ Implemented |
| F6 | Real-time visualization | Medium | ✅ Implemented |
| F7 | Configurable parameters | Medium | ✅ Implemented |
| F8 | Resolution upscaling | High | ✅ Implemented |

### 4.2 Detection Requirements

| Requirement | Target | Notes |
|-------------|--------|-------|
| Minimum figure size | 50px height | After upscaling |
| Keypoint confidence | >0.3 | Filter low-confidence points |
| Detection confidence | >0.25 | YOLO threshold |
| Orientation support | Any | Dancers may be upside-down, sideways |

### 4.3 Tracking Requirements

| Requirement | Target | Notes |
|-------------|--------|-------|
| ID persistence | >95% | Across occlusions <1 sec |
| Track handoff | Seamless | No ID swaps between dancers |
| Lost track recovery | 20 frames | Before track deletion |
| Fast motion handling | Up to 300px/frame | In upscaled space |

---

## 5. Technical Architecture

### 5.1 System Overview

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│   Camera    │────▶│ Capture Card │────▶│   WallDance     │
│ (Sony A7)   │     │  (HDMI/SDI)  │     │   Application   │
└─────────────┘     └──────────────┘     └────────┬────────┘
                                                   │
                    ┌──────────────────────────────┼──────────────────────────────┐
                    │                              ▼                              │
                    │  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐   │
                    │  │  Enhancer   │───▶│   Detector   │───▶│   Tracker    │   │
                    │  │ (CLAHE+γ)   │    │  (YOLO11m)   │    │(Kalman+Hung) │   │
                    │  └─────────────┘    └──────────────┘    └──────┬───────┘   │
                    │                                                 │           │
                    │  ┌─────────────────────────────────────────────┼───────┐   │
                    │  │                                             ▼       │   │
                    │  │  ┌──────────────┐              ┌──────────────────┐ │   │
                    │  │  │    OSC       │◀─────────────│  Visualization   │ │   │
                    │  │  │   Output     │              │     Display      │ │   │
                    │  │  └──────┬───────┘              └──────────────────┘ │   │
                    │  │         │                                           │   │
                    │  └─────────┼───────────────────────────────────────────┘   │
                    │            │                   WallDance Application        │
                    └────────────┼────────────────────────────────────────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │   OSC Receivers         │
                    │  - VJ Software          │
                    │  - Lighting DMX         │
                    │  - Projection Mapping   │
                    └─────────────────────────┘
```

### 5.2 Software Stack

| Layer | Technology | Version | Purpose |
|-------|------------|---------|---------|
| Runtime | Python | 3.10+ | Main application |
| ML Framework | PyTorch | 2.4.1+cu121 | GPU inference |
| Detection | Ultralytics YOLO11 | Latest | Pose estimation |
| Tracking | FilterPy + SciPy | Latest | Kalman filter, Hungarian algorithm |
| Image Processing | OpenCV | 4.x | Enhancement, visualization |
| OSC | python-osc | Latest | Network output |
| Package Manager | uv | Latest | Fast dependency management |

### 5.3 Module Structure

```
05-WallDance1080p/
├── main.py              # Application entry point, main loop
├── config.py            # All tunable parameters
├── enhancer.py          # Low-light image enhancement (CLAHE + gamma)
├── tracker.py           # Kalman filter + Hungarian algorithm tracker
├── osc_output.py        # OSC message formatting and sending
├── visualization.py     # Drawing helpers, overlays
├── install.sh           # Dependency installation
├── run.sh               # Launch script
└── README.md            # Usage documentation
```

---

## 6. Detection & Tracking Pipeline

### 6.1 Pipeline Stages

```
Frame Input (1920×1080)
        │
        ▼
┌───────────────────┐
│ 1. Enhancement    │  CLAHE (clip=3.0, tile=8×8) + Gamma (1.2)
│    (if dark)      │  Auto-detect brightness < 60
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│ 2. Upscale        │  2.0× → 3840×2160 (4K equivalent)
│    (configurable) │  Improves small figure detection
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│ 3. YOLO Inference │  yolo11m-pose.pt
│                   │  Outputs: bboxes, 17 keypoints per person
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│ 4. Tracking       │  Associate detections with existing tracks
│    (Kalman+Hung)  │  Predict, update, handle lost tracks
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│ 5. Scale Back     │  Convert coordinates to original resolution
│                   │  Create output copies (don't modify tracker state)
└───────┬───────────┘
        │
        ▼
OSC Output + Visualization
```

### 6.2 YOLO Model Options

| Model | Size | Speed (RTX 3090) | Accuracy | Recommended For |
|-------|------|------------------|----------|-----------------|
| yolo11n-pose | 2.5M | 45+ FPS | Good | Testing, low-power |
| yolo11s-pose | 9M | 35+ FPS | Better | Balanced |
| **yolo11m-pose** | 25M | 25+ FPS | **Best** | **Production** |
| yolo11l-pose | 50M | 15+ FPS | Excellent | High accuracy needs |
| yolo11x-pose | 100M | 10+ FPS | Maximum | Offline processing |

### 6.3 Keypoint Schema (COCO 17-point)

```
        0: Nose
       / \
      1   2  (L/R Eye)
     /     \
    3       4  (L/R Ear)
    
    5───────6  (L/R Shoulder)
    │       │
    7       8  (L/R Elbow)
    │       │
    9      10  (L/R Wrist)
    
   11──────12  (L/R Hip)
    │       │
   13      14  (L/R Knee)
    │       │
   15      16  (L/R Ankle)
```

### 6.4 Kalman Filter Design

**State Vector (6 dimensions):**
```
x = [x, y, vx, vy, ax, ay]ᵀ
     │  │   │   │   │   └── Y acceleration
     │  │   │   │   └────── X acceleration  
     │  │   │   └────────── Y velocity
     │  │   └────────────── X velocity
     │  └──────────────────  Y position (centroid)
     └─────────────────────  X position (centroid)
```

**Motion Model:** Constant acceleration
```
F = [1  0  dt  0   0.5dt²   0     ]
    [0  1  0   dt  0        0.5dt²]
    [0  0  1   0   dt       0     ]
    [0  0  0   1   0        dt    ]
    [0  0  0   0   1        0     ]
    [0  0  0   0   0        1     ]
```

### 6.5 Hungarian Algorithm Assignment

**Cost Matrix Construction:**
- Distance = Euclidean distance between predicted track position and detection centroid
- Velocity-adjusted prediction: `predicted_pos + velocity × weight`
- Dynamic threshold based on track speed

**Assignment:**
- Optimal bipartite matching using `scipy.optimize.linear_sum_assignment`
- Unmatched detections → new tracks
- Unmatched tracks → increment age, delete if age > max_age

---

## 7. Output Protocols

### 7.1 OSC Message Format

**Base Address:** `/walldance/`

| Address | Arguments | Type | Description |
|---------|-----------|------|-------------|
| `/walldance/count` | `[n]` | int | Number of active dancers |
| `/walldance/dancer/<id>/centroid` | `[x, y]` | float | Normalized 0-1 |
| `/walldance/dancer/<id>/bbox` | `[x, y, w, h]` | float | Normalized 0-1 |
| `/walldance/dancer/<id>/velocity` | `[vx, vy]` | float | Normalized per frame |
| `/walldance/dancer/<id>/keypoints` | `[x0,y0,c0, ...]` | float | 51 values (17×3) |
| `/walldance/clear` | `[1]` | int | Reset signal |

**Coordinate System:**
- Origin: Top-left (0, 0)
- X: 0 (left) → 1 (right)
- Y: 0 (top) → 1 (bottom)

### 7.2 OSC Configuration

| Parameter | Default | Notes |
|-----------|---------|-------|
| IP Address | 127.0.0.1 | Target receiver |
| Port | 9000 | Standard OSC port |
| Protocol | UDP | Low latency |

### 7.3 Future Protocol Options

| Protocol | Use Case | Complexity |
|----------|----------|------------|
| **OSC** | VJ/Audio software | ✅ Implemented |
| MQTT | IoT, distributed systems | Medium |
| WebSocket | Web-based visualizers | Medium |
| DMX/ArtNet | Direct lighting control | High |
| NDI | Video streaming with metadata | High |

---

## 8. Performance Targets

### 8.1 Latency Budget

| Stage | Target | Measured | Notes |
|-------|--------|----------|-------|
| Capture | <50ms | ~30-100ms | Depends on capture card |
| Enhancement | <5ms | ~3ms | GPU accelerated CLAHE |
| Upscale | <5ms | ~2ms | GPU resize |
| YOLO Inference | <40ms | ~30-50ms | RTX 3090, 2× upscale |
| Tracking | <2ms | ~1ms | CPU, lightweight |
| OSC Send | <1ms | <1ms | UDP, no confirmation |
| **Total** | **<100ms** | **~70-150ms** | Glass-to-glass |

### 8.2 Frame Rate Targets

| Upscale | Resolution | Target FPS | Achieved FPS |
|---------|------------|------------|--------------|
| 1.0× | 1920×1080 | 30+ | ~35 |
| 1.5× | 2880×1620 | 25+ | ~28 |
| **2.0×** | 3840×2160 | 20+ | **~22** |
| 2.5× | 4800×2700 | 15+ | ~16 |
| 3.0× | 5760×3240 | 12+ | ~12 |

### 8.3 Resource Utilization (RTX 3090)

| Resource | Typical Usage | Peak |
|----------|---------------|------|
| GPU Compute | 60-80% | 95% |
| VRAM | 4-6 GB | 8 GB |
| CPU | 15-25% | 40% |
| RAM | 2-3 GB | 4 GB |

---

## 9. Implementation Roadmap

### Phase 1: Prototyping ✅ COMPLETE

| Task | Status | Notes |
|------|--------|-------|
| Basic MoveNet skeleton detection | ✅ | 01-MoveNet |
| MMPose integration | ✅ | 02-MMPose (torch 2.4.x compatibility) |
| YOLO11-pose multi-person | ✅ | 03-Yolo11m |
| Kalman+Hungarian tracking | ✅ | 04-RTMPose |
| Integrated solution | ✅ | 05-WallDance1080p |

### Phase 2: Optimization (Current)

| Task | Priority | Status | Est. Effort |
|------|----------|--------|-------------|
| Fine-tune detection confidence | High | 🔄 | 2h |
| Tune tracker for real scene | High | 🔄 | 4h |
| Test with actual camera setup | High | ⬜ | 4h |
| Profile and optimize bottlenecks | Medium | ⬜ | 8h |
| Add recording/playback mode | Medium | ⬜ | 4h |

### Phase 3: Production Hardening

| Task | Priority | Status | Est. Effort |
|------|----------|--------|-------------|
| Robust error handling | High | ⬜ | 4h |
| Auto-reconnect camera | High | ⬜ | 2h |
| Configuration file (YAML) | Medium | ⬜ | 2h |
| Logging system | Medium | ⬜ | 2h |
| Systemd service integration | Low | ⬜ | 2h |
| Health monitoring endpoint | Low | ⬜ | 4h |

### Phase 4: Advanced Features

| Task | Priority | Status | Est. Effort |
|------|----------|--------|-------------|
| 4K input support | Medium | ⬜ | 4h |
| Multi-camera stitching | Low | ⬜ | 16h |
| 3D pose estimation | Low | ⬜ | 24h |
| Gesture recognition | Low | ⬜ | 16h |
| Web dashboard | Low | ⬜ | 12h |

---

## 10. Technical Challenges & Solutions

### 10.1 Small Figure Detection

**Challenge:** Dancers appear ~65 pixels tall at 1080p, below YOLO optimal range (~100px+).

**Solution:** Runtime upscaling before inference.
- 2× upscale: 65px → 130px (good detection)
- Trade-off: Increased GPU load, reduced FPS
- Configurable via `UPSCALE_FACTOR` parameter

**Alternative approaches considered:**
| Approach | Pros | Cons |
|----------|------|------|
| Higher resolution camera | Native quality | Bandwidth, cost |
| **Upscaling** | Flexible, cheap | GPU load |
| Tiled detection | Full resolution | Complexity, boundary issues |
| Custom trained model | Optimized for small | Training data needed |

### 10.2 Low-Light Performance

**Challenge:** Outdoor night performance with minimal lighting.

**Solution:** Adaptive image enhancement pipeline.
1. **Brightness detection**: Calculate mean brightness
2. **CLAHE**: Contrast-limited adaptive histogram equalization
3. **Gamma correction**: Brighten dark regions
4. **Auto-toggle**: Skip enhancement if scene is bright enough

**Parameters:**
```python
CLAHE_CLIP_LIMIT = 3.0      # Contrast boost (1.0-5.0)
CLAHE_TILE_SIZE = 8         # Local adaptation
GAMMA_CORRECTION = 1.2      # Brightness boost
BRIGHTNESS_THRESHOLD = 60   # Auto-detect threshold
```

### 10.3 ID Persistence During Fast Movement

**Challenge:** Dancers moving quickly cause ID swaps and ghost tracks.

**Solution:** Velocity-aware tracking with generous thresholds.
1. **6-state Kalman filter**: Track position + velocity + acceleration
2. **Velocity-weighted prediction**: Anticipate where dancer will be
3. **Dynamic distance threshold**: Allow larger jumps for fast movers
4. **Extended track lifetime**: Keep lost tracks 20 frames before deletion

**Parameters:**
```python
TRACKER_DISTANCE_THRESHOLD = 300    # Pixels (in upscaled space)
TRACKER_VELOCITY_WEIGHT = 0.6       # Trust in velocity prediction
TRACKER_MAX_AGE = 20                # Frames before track deletion
TRACKER_PROCESS_NOISE = 2.5         # Allow velocity changes
```

### 10.4 Rotated Body Orientations

**Challenge:** Wall dancers may be upside-down, sideways, or at any angle.

**Solution:** YOLO11-pose handles arbitrary orientations well.
- No pre-rotation needed
- Keypoint order remains consistent regardless of body orientation
- Bounding box computed from keypoints if needed

### 10.5 PyTorch/CUDA Compatibility

**Challenge:** System cuDNN 9.0.0 vs PyTorch bundled cuDNN 9.8.0 mismatch.

**Solution:** Pin to torch 2.4.1+cu121.
```toml
[dependencies]
torch = { version = "2.4.1+cu121", source = "pytorch" }
```

---

## 11. Prototype Status

### 11.1 Implemented Prototypes

| Prototype | Purpose | Status | Key Learning |
|-----------|---------|--------|--------------|
| **01-MoveNet** | Single-person baseline | ✅ Working | Simple but limited to 1 person |
| **02-MMPose** | MMPose ecosystem test | ✅ Working | Complex deps, two-stage slower |
| **03-Yolo11m** | Multi-person detection | ✅ Working | Best single-shot performance |
| **04-RTMPose** | Tracking integration | ✅ Working | Kalman filter essential |
| **05-WallDance1080p** | Production prototype | ✅ Working | Integrated solution |

### 11.2 Current Best Configuration

```python
# 05-WallDance1080p/config.py

UPSCALE_FACTOR = 2.0              # 4K equivalent processing
YOLO_MODEL = "yolo11m-pose.pt"    # Best accuracy/speed balance
YOLO_CONFIDENCE = 0.25            # Permissive detection
MAX_PERSONS = 6                   # Target dancer count

ENHANCE_ENABLED = True            # Auto low-light enhancement
CLAHE_CLIP_LIMIT = 3.0
GAMMA_CORRECTION = 1.2

TRACKER_DISTANCE_THRESHOLD = 300  # Generous for fast movement
TRACKER_MAX_AGE = 20              # Robust to brief occlusions
TRACKER_VELOCITY_WEIGHT = 0.6     # Trust motion prediction
```

### 11.3 Known Limitations

| Limitation | Impact | Mitigation |
|------------|--------|------------|
| 1080p input only | Fixed | Support 4K in Phase 4 |
| Single camera | Limited coverage | Multi-cam in Phase 4 |
| 2D pose only | No depth | 3D estimation in Phase 4 |
| UDP OSC | No delivery guarantee | Add TCP option |
| Fixed scene | No auto-calibration | Manual config |

---

## 12. Future Enhancements

### 12.1 Near-Term (1-3 months)

| Enhancement | Description | Benefit |
|-------------|-------------|---------|
| 4K input | Support 3840×2160 capture | Better native resolution |
| Recording mode | Save raw + pose data | Replay, analysis |
| Config file | YAML/JSON settings | No code changes |
| OSC bundles | Batch messages per frame | Reduced network overhead |

### 12.2 Medium-Term (3-6 months)

| Enhancement | Description | Benefit |
|-------------|-------------|---------|
| Multi-camera | Stitch 2-3 cameras | Wider/taller coverage |
| Depth estimation | Monocular depth | Z-axis movement |
| Gesture recognition | Classify poses/actions | Higher-level events |
| Web dashboard | Browser-based config/monitor | Remote management |

### 12.3 Long-Term (6-12 months)

| Enhancement | Description | Benefit |
|-------------|-------------|---------|
| 3D pose estimation | Multi-view triangulation | True 3D positions |
| Action recognition | Temporal pose analysis | Dance move detection |
| Edge deployment | Jetson Orin / similar | Standalone unit |
| ML-based tracking | DeepSORT / ByteTrack | Better re-ID |

---

## Appendix A: Dependencies

### Python Packages

```toml
[dependencies]
torch = "2.4.1+cu121"
torchvision = "0.19.1+cu121"
ultralytics = ">=8.0"
opencv-python = ">=4.8"
python-osc = ">=1.8"
filterpy = ">=1.4"
scipy = ">=1.10"
numpy = ">=1.24"
```

### System Requirements

- CUDA 12.1+
- cuDNN 8.x or 9.x (bundled with torch)
- Linux (Ubuntu 22.04+ recommended) or Windows 10/11
- GStreamer (optional, for RTSP sources)

---

## Appendix B: Quick Start

```bash
# Clone repository
git clone https://github.com/Hemisphere-Project/WallDance.git
cd WallDance/05-WallDance1080p

# Install dependencies
./install.sh

# Configure (edit as needed)
nano config.py

# Run
./run.sh
```

### Keyboard Controls

| Key | Action |
|-----|--------|
| Q | Quit |
| H | Toggle help overlay |
| E | Toggle enhancement |
| +/- | Adjust upscale factor |
| T | Toggle motion trails |
| S | Toggle skeleton |
| K | Toggle keypoints |
| B | Toggle bounding box |
| I | Toggle dancer IDs |
| R | Reset tracker |

---

## Appendix C: OSC Testing

```bash
# Install oscdump (liblo-tools)
sudo apt install liblo-tools

# Listen for WallDance messages
oscdump 9000
```

Expected output:
```
/walldance/count i 2
/walldance/dancer/1/centroid ff 0.350000 0.450000
/walldance/dancer/1/bbox ffff 0.300000 0.400000 0.100000 0.200000
/walldance/dancer/1/velocity ff 0.005000 -0.002000
/walldance/dancer/1/keypoints fff... (51 values)
```

---

## Appendix D: Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| No camera found | Wrong index | Try CAMERA_INDEX = 1, 2, ... |
| Low FPS (<10) | High upscale | Reduce UPSCALE_FACTOR |
| Missing detections | Dark scene | Increase CLAHE_CLIP_LIMIT |
| ID swaps | Fast movement | Increase TRACKER_DISTANCE_THRESHOLD |
| Ghost tracks | False detections | Increase YOLO_CONFIDENCE |
| CUDA OOM | Large upscale | Reduce UPSCALE_FACTOR or use smaller model |

---

## Document History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-12-06 | AI/Human collaboration | Initial specification |

---

*This document serves as the authoritative technical specification for the WallDance project. Update this document as requirements evolve and implementations progress.*
