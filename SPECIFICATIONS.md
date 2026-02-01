# WallDance - Technical Specifications

**Project:** Multi-Person Pose Detection for Wall Dancers  
**Version:** 1.6  
**Last Updated:** December 9, 2025  
**Status:** Production

---

## Table of Contents

1.  [Executive Summary](#1-executive-summary)
2.  [Project Context](#2-project-context)
3.  [Hardware Requirements](#3-hardware-requirements)
4.  [Functional Requirements](#4-functional-requirements)
5.  [Technical Architecture](#5-technical-architecture)
6.  [Detection & Tracking Pipeline](#6-detection--tracking-pipeline)
7.  [Output Protocols](#7-output-protocols)
8.  [Performance Targets](#8-performance-targets)
9.  [Implementation Roadmap](#9-implementation-roadmap)
10. [Technical Challenges & Solutions](#10-technical-challenges--solutions)
11. [Application Status](#11-application-status)
12. [Future Enhancements](#12-future-enhancements)
13. [Proposed Improvements](#13-proposed-improvements-dec-2025)
14. [GPU Path Implementation](#14-gpu-path-implementation)
- [Appendix A: Dependencies](#appendix-a-dependencies)
- [Appendix B: OSC Testing](#appendix-b-osc-testing)
- [Appendix C: Troubleshooting](#appendix-c-troubleshooting)

---

## 1. Executive Summary

WallDance is a real-time computer vision system designed to detect and track multiple dancers performing on a large vertical surface (wall) during outdoor night performances. The system extracts 2D pose skeletons and sends tracking data via OSC protocol to downstream systems (VJ software, lighting controllers, interactive projections).

### Key Challenges

-   **Large scene coverage**: 50m wide performance area
-   **Small figure size**: Dancers appear ~65 pixels tall at 1080p
-   **Low-light conditions**: Outdoor night performance with minimal ambient lighting
-   **Multi-person tracking**: Up to 6 dancers with stable ID persistence
-   **Real-time processing**: Target 10-30 FPS for responsive interaction

---

## 2. Project Context

### 2.1 Performance Environment

| Parameter | Value | Notes |
|---|---|---|
| Scene Width | 50 meters | Horizontal span of performance wall |
| Scene Height | ~20-30 meters | Vertical climbing area |
| Number of Dancers | Up to 6 | Simultaneous performers |
| Lighting Conditions | Dark / Night | Outdoor, minimal ambient light |
| Performance Type | Wall climbing/dancing | Vertical surface, rotated body orientations |

### 2.2 Use Cases

1.  **Live VJ Integration**: Real-time dancer positions/poses drive visual effects
2.  **Lighting Control**: Dynamic lighting follows performers
3.  **Interactive Projection**: Projected graphics react to dancer movements
4.  **Performance Recording**: Capture pose data for post-production
5.  **Analytics**: Movement analysis for choreography refinement

---

## 3. Hardware Requirements

### 3.1 Camera System

**Production Hardware (Purchased Feb 2025):**

| Component | Model | Specification | Rationale |
|---|---|---|---|
| Camera | IDS U3-34E0XCP-M-GL Rev.1.2 | 4MP Sony IMX664 Starvis 2, Monochrome | Excellent low-light, USB3 Vision, industrial grade |
| Lens | Tamron M118FM08 | 8mm, 1/1.8", C-Mount, F1.8 | Wide FOV (~50° HFOV), bright aperture for low-light |
| IR Filter | MidOpt BP850-25.4 | 850nm bandpass, C-Mount | Blocks projector light, passes IR illumination |
| Resolution | 2688×1520 (4MP native) | Native 4MP, can crop/bin to 1080p | High resolution for distant subjects |
| Frame Rate | 30-60 FPS | Configurable via SDK | Adjustable based on exposure needs |
| Interface | USB3 Vision | Direct to PC, no capture card | Low latency (~5-10ms), SDK control |
| Mounting | Fixed tripod/rigging | Stable, unobstructed view | Weather housing recommended |

**Calculated Figure Size:**

-   At 1080p covering 50m width: 1920px / 50m = 38.4 px/m
-   Average dancer height (1.7m): 1.7m × 38.4 = **~65 pixels**
-   At 4MP (2688px) covering 50m: 2688px / 50m = 53.8 px/m → **~91 pixels**
-   This is below optimal detection threshold (~100px), requiring upscaling
-   **Production camera (IDS 4MP)** improves native resolution by ~40%

### 3.2 Processing Hardware

**Production Hardware (Purchased Feb 2025):**

| Component | Model | Specification | Notes |
|---|---|---|---|
| Laptop | ASUS ROG Strix SCAR 16 G635LW | Gaming laptop, portable | Field deployment ready |
| GPU | NVIDIA RTX 5080 (Laptop) | 16GB VRAM, Blackwell architecture | Latest generation, excellent inference |
| CPU | Intel Core Ultra 9 275HX | 24-core | High-performance mobile CPU |
| RAM | 32 GB DDR5 | Standard config | Sufficient for all workloads |
| Storage | NVMe SSD | 1TB+ | Fast model loading |

**General Requirements:**

| Component | Minimum | Recommended | Notes |
|---|---|---|---|
| GPU | RTX 3070 | RTX 4080+ / RTX 5080 | CUDA compute for inference |
| VRAM | 8 GB | 16 GB+ | Model + upscaled frames |
| CPU | 8-core | 16-core+ | Pre/post processing |
| RAM | 16 GB | 32 GB | Frame buffers |
| Storage | SSD | NVMe SSD | Fast model loading |

### 3.3 Capture & Camera Options

For detailed hardware purchasing recommendations (capture cards, machine vision cameras, low-light sensors), see [HARDWARE_GUIDE.md](HARDWARE_GUIDE.md).

**Note:** The production setup uses USB3 Vision (IDS camera) which bypasses capture cards entirely, providing lower latency and direct SDK control.

---

## 4. Functional Requirements

### 4.1 Core Features

| ID | Feature | Priority | Status |
|---|---|---|---|
| F1 | Multi-person detection (up to 6) | Critical | ✅ Implemented |
| F2 | 17-keypoint skeleton extraction | Critical | ✅ Implemented |
| F3 | Persistent ID tracking across frames | Critical | ✅ Implemented |
| F4 | Low-light image enhancement | High | ✅ Implemented |
| F5 | OSC output protocol | High | ✅ Implemented |
| F6 | Real-time visualization | Medium | ✅ Implemented |
| F7 | Configurable parameters | Medium | ✅ Implemented |
| F8 | Resolution upscaling | High | ✅ Implemented |
| F9 | DearPyGui control panel | Medium | ✅ Implemented |
| F10 | Runtime model switching | Medium | ✅ Implemented |
| F11 | FP16 half-precision inference | Medium | ✅ Implemented |
| F12 | Frame skip option | Low | ✅ Implemented |
| F13 | TensorRT acceleration | High | ✅ Implemented |
| F14 | Auto model download | Medium | ✅ Implemented |

### 4.2 Detection Requirements

| Requirement | Target | Notes |
|---|---|---|
| Minimum figure size | 50px height | After upscaling |
| Keypoint confidence | >0.3 | Filter low-confidence points |
| Detection confidence | >0.25 | YOLO threshold |
| Orientation support | Any | Dancers may be upside-down, sideways |

### 4.3 Tracking Requirements

| Requirement | Target | Notes |
|---|---|---|
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
                    ┌─────────────────────────────┴─────────────────────────────┐
                    │                             ▼                             │
                    │  ┌─────────────┐    ┌──────────────┐    ┌──────────────┐  │
                    │  │  Enhancer   │───▶│   Detector   │───▶│   Tracker    │  │
                    │  │ (CLAHE+γ)   │    │  (YOLO11m)   │    │(Kalman+Hung) │  │
                    │  └─────────────┘    └──────────────┘    └──────┬───────┘  │
                    │                                                │          │
                    │  ┌─────────────────────────────────────────────┼───────┐  │
                    │  │                                             ▼       │  │
                    │  │  ┌──────────────┐              ┌──────────────────┐ │  │
                    │  │  │    OSC       │◀─────────────│  Visualization   │ │  │
                    │  │  │   Output     │              │     Display      │ │  │
                    │  │  └──────┬───────┘              └──────────────────┘ │  │
                    │  │         │                                           │  │
                    │  └─────────┼───────────────────────────────────────────┘  │
                    │            │                   WallDance Application      │
                    └────────────┼──────────────────────────────────────────────┘
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
|---|---|---|---|
| Runtime | Python | 3.10+ | Main application |
| ML Framework | PyTorch | 2.4.1+cu121 | GPU inference |
| Detection | Ultralytics YOLO11 | Latest | Pose estimation |
| Tracking | FilterPy + SciPy | Latest | Kalman filter, Hungarian algorithm |
| Image Processing | OpenCV | 4.x | Enhancement, upscaling |
| GUI | DearPyGui | 2.1+ | GPU-accelerated control panel |
| OSC | python-osc | Latest | Network output |
| Package Manager | uv | Latest | Fast dependency management |

### 5.3 Module Structure

```
application/
├── src/
│   ├── main.py          # Application entry point
│   ├── app.py           # Main application orchestrator
│   ├── gui.py           # DearPyGui control panel
│   ├── gui_builder.py   # UI component builders
│   ├── config.py        # Configuration parameters
│   ├── config_store.py  # Project/config persistence
│   ├── enhancer.py      # Low-light enhancement (CLAHE + gamma)
│   ├── tracker.py       # Kalman filter + Hungarian tracking
│   ├── osc_output.py    # OSC message formatting
│   ├── visualization.py # Drawing helpers, overlays
│   ├── camera_manager.py# Camera handling
│   ├── model_manager.py # YOLO model loading/switching
│   ├── pipeline.py      # Processing pipeline
│   └── video_recorder.py# Recording functionality
├── assets/              # Icons, fonts
└── pyproject.toml       # Dependencies

# Workspace root scripts:
├── run.sh               # Launch application
├── install.sh           # Install dependencies
└── build_engines.sh     # Build TensorRT engines
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

All models are selectable at runtime via the GUI dropdown.

| Model | Size | Speed (RTX 3090) | Accuracy | Recommended For |
|---|---|---|---|---|
| yolo11n-pose | 2.5M | 45+ FPS | Good | Testing, low-power, max FPS |
| yolo11s-pose | 9M | 35+ FPS | Better | Balanced, good starting point |
| **yolo11m-pose** | 25M | 25+ FPS | **Best** | **Production default** |
| yolo11l-pose | 50M | 15+ FPS | Excellent | High accuracy needs |
| yolo11x-pose | 100M | 10+ FPS | Maximum | Offline processing |

### 6.2.1 Performance Optimization Options

| Option | Speedup | Notes |
|---|---|---|
| **TensorRT Acceleration** | +50-100% | Toggle in GUI, requires engine build (2-5 min first time) |
| **FP16 Half Precision** | +20-30% | Toggle in GUI, minimal accuracy loss |
| **Frame Skip** | N+1× fewer inferences | Reuses last tracking result for skipped frames |
| **Smaller Model** | 2-4× faster | yolo11n vs yolo11m |

### 6.2.2 TensorRT Engine System

TensorRT engines provide significant inference speedup (2×+) but are tied to specific input sizes.

**Engine Naming Convention:**
- Engines are named `{model}_{imgsz}.engine` (e.g., `yolo11m-pose_960.engine`)
- This allows multiple engines for different input sizes
- Engines are GPU-specific and must be rebuilt on different hardware

**GUI Controls:**
- **TRT Checkbox**: Enable/disable TensorRT for the current model
- If engine exists for current imgsz → switches immediately
- If engine missing → prompts to build (2-5 minutes)
- Engine built with FP16 for optimal speed/accuracy balance

**Build Process:**
1. User enables TRT checkbox in MODEL section
2. If no engine for current imgsz, prompt appears
3. GPU stats update during build (VRAM usage visible)
4. Engine saved to `models/` directory
5. Model automatically switches to TRT engine

**Automatic Fallback:**
- If TensorRT not installed → checkbox disabled, toast shown
- If engine load fails → falls back to PyTorch model
- On startup with saved TRT config but missing engine → uses PyTorch

### 6.3 Keypoint Schema (COCO 17-point)

```
        0: Nose
       /       \
     1   2  (L/R Eye)
     /         \
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

-   Distance = Euclidean distance between predicted track position and detection centroid
-   Velocity-adjusted prediction: `predicted_pos + velocity × weight`
-   Dynamic threshold based on track speed

**Assignment:**

-   Optimal bipartite matching using `scipy.optimize.linear_sum_assignment`
-   Unmatched detections → new tracks
-   Unmatched tracks → increment age, delete if age > max_age

---

## 7. Output Protocols

### 7.1 OSC Message Format

**Base Address:** `/walldance/`

| Address | Arguments | Type | Description |
|---|---|---|---|
| `/walldance/count` | `[n]` | int | Number of active dancers |
| `/walldance/dancer/<id>/centroid` | `[x, y]` | float | Normalized 0-1 |
| `/walldance/dancer/<id>/bbox` | `[x, y, w, h]` | float | Normalized 0-1 |
| `/walldance/dancer/<id>/velocity` | `[vx, vy]` | float | Normalized per frame |
| `/walldance/dancer/<id>/keypoints` | `[x0,y0,c0, ...]` | float | 51 values (17×3) |
| `/walldance/clear` | `[1]` | int | Reset signal |

**Coordinate System:**

-   Origin: Top-left (0, 0)
-   X: 0 (left) → 1 (right)
-   Y: 0 (top) → 1 (bottom)

### 7.2 OSC Configuration

| Parameter | Default | Notes |
|---|---|---|
| IP Address | 127.0.0.1 | Target receiver |
| Port | 9000 | Standard OSC port |
| Protocol | UDP | Low latency |

### 7.3 Future Protocol Options

| Protocol | Use Case | Complexity |
|---|---|---|
| **OSC** | VJ/Audio software | ✅ Implemented |
| MQTT | IoT, distributed systems | Medium |
| WebSocket | Web-based visualizers | Medium |
| DMX/ArtNet | Direct lighting control | High |
| NDI | Video streaming with metadata | High |

---

## 8. Performance Targets

### 8.1 Latency Budget

| Stage | Target | Current | Notes |
|---|---|---|---|
| Capture | <50ms | ~30-100ms | Depends on capture card |
| Enhancement | <5ms | ~8-12ms | Target: GPU CLAHE (see Section 14) |
| Upscale | <5ms | ~3-5ms | Target: GPU resize (see Section 14) |
| YOLO Inference | <40ms | ~30-50ms | RTX 3090, 2× upscale |
| Tracking | <2ms | ~1ms | CPU, lightweight |
| OSC Send | <1ms | <1ms | UDP, no confirmation |
| **Total** | **<100ms** | **~70-150ms** | Glass-to-glass |

### 8.2 Frame Rate Targets

| Upscale | Resolution | Target FPS | Achieved FPS |
|---|---|---|---|
| 1.0× | 1920×1080 | 30+ | ~35 |
| 1.5× | 2880×1620 | 25+ | ~28 |
| **2.0×** | 3840×2160 | 20+ | **~22** |
| 2.5× | 4800×2700 | 15+ | ~16 |
| 3.0× | 5760×3240 | 12+ | ~12 |

### 8.3 Resource Utilization (RTX 3090)

| Resource | Typical Usage | Peak |
|---|---|---|
| GPU Compute | 60-80% | 95% |
| VRAM | 4-6 GB | 8 GB |
| CPU | 15-25% | 40% |
| RAM | 2-3 GB | 4 GB |

---

## 9. Implementation Roadmap

### Phase 1: Prototyping ✅ COMPLETE

Prototypes in `prototypes/` folder explored MoveNet, MMPose, YOLO11, and RTMPose tracking. The final integrated solution is in `application/`.

### Phase 2: Optimization ✅ COMPLETE

- ✅ Detection confidence tuning
- ✅ Tracker tuning for real scenes
- ✅ Recording/playback mode
- ✅ TensorRT acceleration
- ✅ FP16 inference

### Phase 3: Production Hardening (Current)

- ✅ JSON configuration persistence
- ✅ Project/preset management
- 🔄 Robust error handling
- 🔄 Auto-reconnect camera
- ⬜ Logging system
- ⬜ Health monitoring

### Phase 4: Advanced Features (Future)

- ⬜ 4K input support
- ⬜ Multi-camera stitching
- ⬜ 3D pose estimation
- ⬜ Web dashboard

---

## 10. Technical Challenges & Solutions

### 10.1 Small Figure Detection

**Challenge:** Dancers appear 65 pixels tall at 1080p, below YOLO optimal range (100px+).

**Solution:** Runtime upscaling before inference.

-   2× upscale: 65px → 130px (good detection)
-   Trade-off: Increased GPU load, reduced FPS
-   Configurable via `UPSCALE_FACTOR` parameter

**Alternative approaches considered:**

| Approach | Pros | Cons |
|---|---|---|
| Higher resolution camera | Native quality | Bandwidth, cost |
| **Upscaling** | Flexible, cheap | GPU load |
| Tiled detection | Full resolution | Complexity, boundary issues |
| Custom trained model | Optimized for small | Training data needed |

### 10.2 Low-Light Performance

**Challenge:** Outdoor night performance with minimal lighting.

**Solution:** Adaptive image enhancement pipeline.

1.  **Brightness detection**: Calculate mean brightness
2.  **CLAHE**: Contrast-limited adaptive histogram equalization
3.  **Gamma correction**: Brighten dark regions
4.  **Auto-toggle**: Skip enhancement if scene is bright enough

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

1.  **6-state Kalman filter**: Track position + velocity + acceleration
2.  **Velocity-weighted prediction**: Anticipate where dancer will be
3.  **Dynamic distance threshold**: Allow larger jumps for fast movers
4.  **Extended track lifetime**: Keep lost tracks 20 frames before deletion

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

-   No pre-rotation needed
-   Keypoint order remains consistent regardless of body orientation
-   Bounding box computed from keypoints if needed

### 10.5 PyTorch/CUDA Compatibility

**Challenge:** System cuDNN 9.0.0 vs PyTorch bundled cuDNN 9.8.0 mismatch.

**Solution:** Pin to torch 2.4.1+cu121.

```toml
[dependencies]
torch = { version = "2.4.1+cu121", source = "pytorch" }
```

---

## 11. Application Status

### 11.1 Prototypes Summary

Prototypes in `prototypes/` explored different approaches:
- **01-MoveNet**: Single-person baseline (simple but limited)
- **02-MMPose**: MMPose ecosystem (complex deps, slower)
- **03-Yolo11m**: Multi-person detection (best single-shot)
- **04-RTMPose**: Tracking integration (Kalman filter essential)

The production application is in `application/`.

### 11.2 Current Best Configuration

```python
# application/src/config.py
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

### 11.2.1 Runtime Performance Options (GUI)

| Setting | Default | Range | Effect |
|---|---|---|---|
| Model | yolo11m-pose | n/s/m/l/x | Speed vs accuracy |
| FP16 | OFF | ON/OFF | +20-30% FPS |
| Frame Skip | 0 | 0-4 | Skip N frames between inference |

### 11.3 Known Limitations

| Limitation | Impact | Mitigation |
|---|---|---|
| 1080p input only | Fixed | Support 4K in Phase 4 |
| Single camera | Limited coverage | Multi-cam in Phase 4 |
| 2D pose only | No depth | 3D estimation in Phase 4 |
| UDP OSC | No delivery guarantee | Add TCP option |
| Fixed scene | No auto-calibration | Manual config |
| CPU frame copy | Extra latency | V4L2 DMA-BUF zero-copy |

---

## 12. Future Enhancements

### 12.1 Near-Term (1-3 months)

| Enhancement | Description | Benefit |
|---|---|---|
| 4K input | Support 3840×2160 capture | Better native resolution |
| Recording mode | Save raw + pose data | Replay, analysis |
| Config file | YAML/JSON settings | No code changes |
| OSC bundles | Batch messages per frame | Reduced network overhead |
| V4L2 DMA-BUF | Zero-copy GPU capture | 5-15ms latency reduction |

### 12.2 Medium-Term (3-6 months)

| Enhancement | Description | Benefit |
|---|---|---|
| Multi-camera | Stitch 2-3 cameras | Wider/taller coverage |
| Depth estimation | Monocular depth | Z-axis movement |
| Gesture recognition | Classify poses/actions | Higher-level events |
| Web dashboard | Browser-based config/monitor | Remote management |
| GStreamer NVMM | Hardware-accelerated pipeline | Lower CPU, better throughput |
| TensorRT export | YOLO → TensorRT engine | 2-3× faster inference |
| Full GPU pipeline | Zero-copy capture to inference | ~20-30ms latency reduction |

### 12.3 Long-Term (6-12 months)

| Enhancement | Description | Benefit |
|---|---|---|
| 3D pose estimation | Multi-view triangulation | True 3D positions |
| Action recognition | Temporal pose analysis | Dance move detection |
| Edge deployment | Jetson Orin / similar | Standalone unit |
| ML-based tracking | DeepSORT / ByteTrack | Better re-ID |

### 12.4 V4L2 DMA-BUF Zero-Copy Capture

**Current Architecture:**
- OpenCV `VideoCapture` → CPU memory → NumPy → GPU upload
- 2-3 memory copies per frame
- ~10-20ms overhead

**Proposed Architecture:**
- V4L2 DMA-BUF → Direct GPU memory (CUDA/NVMM)
- Zero CPU copies
- ~5-15ms latency savings

**Implementation Options:**

| Option | Complexity | Performance | Notes |
|---|---|---|---|
| GStreamer + nvv4l2camerasrc | Medium | Excellent | NVIDIA-optimized, well-documented |
| PyV4L2 + CuPy DMA-BUF | High | Excellent | Maximum control, complex integration |
| pycuda + V4L2 direct | High | Excellent | Low-level, requires CUDA expertise |

**GStreamer Pipeline Example:**
```
v4l2src device=/dev/video0 !
video/x-raw,format=UYVY,width=1920,height=1080,framerate=30/1 !
nvvidconv !
video/x-raw(memory:NVMM),format=BGRx !
appsink
```

**Requirements:**
- NVIDIA GPU with NVMM support
- GStreamer 1.x with nvvidconv plugin
- Capture card with V4L2 DMA-BUF export (Magewell, Blackmagic)
- Linux kernel 4.x+ with DMA-BUF subsystem

### 12.5 TensorRT Optimization ✅ IMPLEMENTED

**Status:** Fully integrated with GUI controls and automatic management.

**Implementation Details:**
- TRT checkbox in MODEL section enables/disables TensorRT
- Engines are imgsz-specific: `{model}_{imgsz}.engine`
- Automatic build prompt when engine doesn't exist
- GPU/VRAM stats update during engine build
- Graceful fallback to PyTorch if TensorRT unavailable

**Measured Performance Gains:**

| Mode | RTX 3090 FPS | Latency | Notes |
|---|---|---|---|
| PyTorch FP32 | ~25 | ~40ms | Baseline |
| PyTorch FP16 | ~32 | ~31ms | FP16 checkbox enabled |
| **TensorRT FP16** | ~55-65 | ~15-18ms | **TRT checkbox enabled** |
| TensorRT INT8 | ~80 | ~12ms | Future (requires calibration) |

**GUI Workflow:**
1. Select model from dropdown (e.g., yolo11m-pose)
2. Set desired imgsz (e.g., 960)
3. Enable TRT checkbox
4. If engine exists → immediate switch
5. If engine missing → build prompt appears
6. Click "Yes" → engine builds (2-5 minutes, GPU stats visible)
7. Engine saved as `yolo11m-pose_960.engine`

**Engine Management:**
```python
# Engine path includes imgsz
engine_path = f"{model_name}_{imgsz}.engine"
# e.g., yolo11n-pose_640.engine, yolo11n-pose_960.engine

# Multiple engines can coexist for different sizes
models/
├── yolo11n-pose.pt
├── yolo11n-pose_640.engine
├── yolo11n-pose_960.engine
├── yolo11m-pose.pt
└── yolo11m-pose_960.engine
```

**Configuration Persistence:**
- `use_tensorrt` flag saved in project configs
- On startup: loads saved TRT preference
- If engine missing for saved imgsz → falls back to PyTorch

**Considerations:**
- Engine is GPU-specific (must rebuild for different GPU)
- Engine is imgsz-specific (different engine per input size)
- First inference after load is slow (engine warmup)
- INT8 requires calibration dataset (future enhancement)

### 12.6 Full GPU Processing Pipeline

> **Note:** Detailed implementation plan has been moved to **Section 14: GPU Path Implementation**. This section preserved for reference code examples.

**Goal:** Keep frame data on GPU from capture to inference, eliminating CPU-GPU transfers. See Section 14 for phased implementation plan and status tracking.

**Current vs Target Pipeline:** See Section 14.1 for analysis.

**Reference Code Examples:**

**CuPy CLAHE Kernel Example:**
```python
import cupy as cp

# Upload frame to GPU once
gpu_frame = cp.asarray(frame)

# Convert BGR to YCrCb on GPU
gpu_ycrcb = cv2.cuda.cvtColor(gpu_frame, cv2.COLOR_BGR2YCrCb)

# Apply CLAHE on Y channel (GPU)
gpu_clahe = cv2.cuda.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
gpu_y = gpu_ycrcb[:, :, 0]
gpu_clahe.apply(gpu_y, gpu_y)

# Gamma correction kernel
gamma_kernel = cp.ElementwiseKernel(
    'uint8 x, float32 inv_gamma',
    'uint8 y',
    'y = (uint8)(powf((float)x / 255.0f, inv_gamma) * 255.0f)',
    'gamma_correction'
)
gpu_frame = gamma_kernel(gpu_ycrcb, 1.0/1.2)

# Zero-copy to PyTorch for YOLO
torch_frame = torch.as_tensor(gpu_frame, device='cuda')
```

**OpenGL Shader Alternative (for preview):**
```glsl
// Fragment shader for real-time gamma + contrast
uniform sampler2D frame;
uniform float gamma;
uniform float contrast;

void main() {
    vec4 color = texture2D(frame, gl_TexCoord[0].xy);
    // Gamma correction
    color.rgb = pow(color.rgb, vec3(1.0 / gamma));
    // Contrast (simplified CLAHE approximation)
    color.rgb = (color.rgb - 0.5) * contrast + 0.5;
    gl_FragColor = color;
}
```

---

## 13. Proposed Improvements (Dec 2025)

### 13.1 UI Usability
- ✅ Start/Stop camera button next to camera selector (implemented).
- ✅ Status badges in top bar: camera, OSC, model, FPS (implemented).
- ✅ Tooltips for sliders and UI elements with explanatory text (implemented).
- ✅ "Safe defaults" button (rotate icon) next to save: click to load, Ctrl+click to save safe defaults per project (implemented).
- ✅ Compact Detection section: Max Persons and Person Height on same row (implemented).
- ✅ Top bar TRT/PT badge with tooltip explaining engine types (implemented).
- Searchable project/config dropdowns when many presets exist (todo).

### 13.2 UI & Preview Performance
- ✅ Preview downscale slider (0.3–1.0) already present.
- ✅ Preview on/off (pause preview) already present; processing/OSC continue when off.
- ✅ Preview texture uploads capped to ~15 FPS to reduce GPU/UI load (implemented).
- "Low-impact preview" toggle (could combine downscale + throttle into one control) (todo).

### 13.3 Video Playback Features
- ✅ Threaded video decoder with frame buffer for smooth playback (implemented).
- ✅ Playback speed control: x0.25, x0.5, x0.75, x1, x1.5, x2, x4 (implemented).
- ✅ Pause/resume playback with keyboard shortcut (Space) (implemented).
- ✅ Frame stepping: next/prev frame with arrow keys or buttons (implemented).
- ✅ Font Awesome icons for playback controls (implemented).

### 13.4 Detection/Tracking Robustness (Low Light, Long Distance)
- Two-stage exposure logic: when brightness is low, force enhancement and slightly lower detection confidence (todo).
- ✅ Temporal confidence smoothing slider (1-10 frames) to stabilize detections (implemented).
- Dynamic NMS/IoU tuned for small boxes to reduce duplicate detections at long distance (todo).
- Brightness/contrast watchdog that raises gamma/CLAHE when the scene darkens (todo).
- Per-track quality score: freeze/hold OSC output for low-quality tracks instead of dropping IDs (todo).

### 13.5 Detection Performance (Robustness First)
- Auto model step-down only when FPS < target and confidence > floor; otherwise keep mid model (todo).
- Auto imgsz downshift when GPU load >90% while keeping confidence threshold unchanged (todo).
- Optional ROI cropping to skip sky/ground pixels and cut inference cost (todo).
- Cache resized frames during frame-skip cycles to avoid repeated upscales (todo).

### 13.6 Additional Features
- ✅ Offline replay mode: load video files and emit OSC for QA without a live camera (implemented).
- Logging/export: per-frame metrics (fps, brightness, latency, track counts) to CSV (todo).
- Alerting: notifications on OSC send failure or camera disconnect; optional auto-retry (todo).
- Model checksum/display: show model file hash and load time to verify correct weights on-site (todo).

---

## 14. GPU Path Implementation

This section documents the plan and progress for implementing a full GPU processing path to minimize CPU↔GPU memory transfers and maximize throughput.

> **Cross-reference:** Section 8.1 (Latency Budget) lists target timings assuming GPU-accelerated enhancement and resize. These targets will be achieved by completing the phases below.

### 14.1 Current Pipeline Workflows

The pipeline now supports two processing paths depending on CUDA availability and `USE_GPU_PATH` config flag.

#### CPU Path (Fallback)
```
Camera Frame (CPU numpy array)
    ↓
[1] Enhancement (CPU: CLAHE, Gamma LUT via OpenCV)
    ↓
[2] Upscale (CPU: cv2.resize)  
    ↓
[3] YOLO Inference (GPU - internal upload by Ultralytics)
    ↓
[4] Extract Detections (CPU: .cpu().numpy())
    ↓
[5] Tracking (CPU: Kalman filter, Hungarian algorithm)
    ↓
[6] Visualization (CPU: cv2.line, cv2.circle, cv2.putText)
    ↓
[7] Preview Texture (CPU→GPU upload via DearPyGui)
```

#### GPU Path (When CUDA Available)
```
Camera Frame (CPU numpy array)
    ↓
[1] Upload to GPU (GpuFrame wrapper, cv2.cuda.GpuMat)
    ↓                                            ╭───────────────╮
[2] Enhancement (GPU: cv2.cuda.createCLAHE,      │ Stays on GPU! │
    cv2.cuda.cvtColor, cv2.cuda.LUT)             ╰───────────────╯
    ↓
[3] Upscale (TODO: cv2.cuda.resize)  ← currently still CPU
    ↓
[4] YOLO Inference (GPU - with CPU input, TODO: zero-copy)
    ↓
[5] Extract Detections (CPU: .cpu().numpy())
    ↓
[6] Tracking (CPU: Kalman filter, Hungarian algorithm)
    ↓
[7] Visualization (CPU: cv2.line, cv2.circle, cv2.putText)
    ↓
[8] Preview Texture (CPU→GPU upload via DearPyGui)
```

#### Phase Status Summary
| Phase | Component | CPU Path | GPU Path | Status |
|-------|-----------|----------|----------|--------|
| 1 | Frame Buffer | numpy array | GpuFrame/Tensor | ✅ Done |
| 2 | Enhancement | cv2 CLAHE/LUT | Kornia CLAHE/Gamma | ✅ Done |
| 3 | Resize | cv2.resize | torch.nn.functional | ✅ Done |
| 4 | YOLO Input | numpy→GPU | Tensor (Zero-Copy) | ✅ Done |
| 5 | Visualization | cv2 drawing | Shader/GPU | ⬜ Future |

#### Interface Indicators
The GUI displays GPU/CPU status for each pipeline step in the timing breakdown:
- **G** prefix = GPU path active (e.g., "G Enh: 3ms")
- **C** prefix = CPU path (e.g., "C Enh: 12ms")
- Color coding: green (<threshold), yellow (moderate), red (slow)
- Multiple GPU↔CPU transfers per frame (3-4 round trips)
- Visualization drawing is CPU-bound (but only affects preview)

### 14.2 Implementation Phases

#### Phase 1: GPU Frame Buffer (Foundation)
**Status:** ✅ Implemented  
**Goal:** Keep frames on GPU memory, avoid CPU↔GPU ping-pong  
**Risk:** Low - doesn't change processing logic  
**Files:** `pipeline.py`, `gpu_buffer.py`, `config.py`

**Implementation:**
- Created `GpuFrame` wrapper class using `cv2.cuda.GpuMat`
- Added `USE_GPU_PATH` config flag (default True, auto-fallback if CUDA unavailable)
- Pipeline uploads frame to GPU once at start
- Downloads to CPU only when needed (currently: enhancement, resize, YOLO input)
- Added timing for upload phase

**Tasks:**
1. ✅ Create `GpuFrame` wrapper class using `cv2.cuda.GpuMat`
2. ✅ Upload camera frame to GPU once at start of pipeline
3. ✅ Download to CPU only when needed (visualization, recording)
4. ✅ Add `to_gpu()` / `to_cpu()` methods for explicit transfers
5. ⬜ Benchmark: measure transfer time savings (pending testing)

**API Design:**
```python
class GpuFrame:
    def __init__(self, cpu_frame=None, gpu_mat=None):
        self._cpu = cpu_frame
        self._gpu = gpu_mat
    
    def to_gpu(self) -> cv2.cuda.GpuMat: ...
    def to_cpu(self) -> np.ndarray: ...
    def is_on_gpu(self) -> bool: ...
```

---

#### Phase 2: GPU Enhancement ✅
**Status:** Implemented  
**Goal:** CLAHE + Gamma on GPU  
**Files:** `enhancer.py`, `pipeline.py`

**Implementation:**
- Rewrote `enhancer.py` with new `Enhancer` class supporting both CPU and GPU paths
- GPU enhancement uses `cv2.cuda.createCLAHE()`, `cv2.cuda.cvtColor()`, `cv2.cuda.LUT()`
- Works in LAB color space for proper luminance enhancement
- Automatic fallback to CPU if any GPU operation fails
- `ImageEnhancer` class provides backward compatibility for legacy API
- `EnhancerSettings` dataclass for clean parameter passing

**Key Classes:**
```python
@dataclass
class EnhancerSettings:
    enabled: bool = False
    clahe_clip: float = 2.0
    clahe_grid: int = 8
    gamma: float = 1.0

class Enhancer:
    def enhance(self, frame: GpuFrame | np.ndarray, settings: EnhancerSettings) -> GpuFrame | np.ndarray
    def _enhance_gpu(self, frame: GpuFrame, settings: EnhancerSettings) -> GpuFrame
    def _enhance_cpu(self, frame: np.ndarray, settings: EnhancerSettings) -> np.ndarray

class ImageEnhancer(Enhancer):  # Backward compatible
    def enhance(self, frame) -> (enhanced, status_dict)
    def enhance_simple(self, frame) -> enhanced
    def get_status() -> {"brightness": value}
```

---

#### Phase 3: GPU Resize
**Status:** Not started  
**Goal:** Upscale on GPU  
**Risk:** Low - straightforward CUDA call  
**Files:** `pipeline.py`

**Tasks:**
1. Replace `cv2.resize()` with `cv2.cuda.resize()`
2. Use `cv2.INTER_LINEAR` (fast) or `cv2.INTER_CUBIC` (quality)
3. Chain with Phase 2: enhanced GPU frame → resized GPU frame
4. Feed directly to YOLO (Ultralytics accepts GPU tensors)

**Code change:**
```python
if self.settings.upscale_factor != 1.0:
    gpu_resized = cv2.cuda.resize(gpu_enhanced, (new_w, new_h), 
                                   interpolation=cv2.INTER_LINEAR)
```

---

#### Phase 4: Zero-Copy YOLO Input (Completed)
**Status:** Implemented  
**Goal:** Pass GPU tensor directly to Ultralytics YOLO  
**Files:** `pipeline.py`, `gpu_pipeline.py`  
**Implementation:**
- `GpuPipeline.process()` returns a pre-processed `torch.Tensor`
- `pipeline.py` passes this tensor directly to `model()`
- Eliminates the costly `numpy` → `GPU` upload inside YOLO

---

#### Phase 5: Advanced Features (Future)
**Status:** Planned  
**Goal:** High-resolution support and further optimization  

**A. 4K / Smart Tiling Inference**
- **Problem:** Small targets at 4K resolution are lost when downscaled to 640x640.
- **Solution: "Smart Tiling"**
    1. **Global Watch:** Run fast detection on downscaled full frame (e.g., 1280px) to find general activity.
    2. **Active Tiling:** Divide 4K frame into overlapping tiles (e.g., 960px with 25% overlap).
    3. **Selective Inference:** Only run high-res inference on tiles that:
        - Contain a tracked dancer (from previous frame).
        - Contain a potential detection (from Global Watch).
    4. **Merge:** Stitch results back to global coordinates using NMS.
    - *Note:* This is more robust than simple ROI cropping as it handles new entrants via the Global Watch and doesn't rely on precise ROI prediction.

**B. Temporal Denoising (Implemented)**
- **Goal:** Reduce sensor noise in low-light conditions.
- **Method:** Weighted moving average on GPU tensor (`out = (1-α)*last + α*new`).
- **Status:** Implemented in `gpu_pipeline.py` with Progressive Enhancement logic (fades out based on brightness).

**C. Zero-Copy Capture (DALI)**
- **Goal:** Avoid CPU decoding of video stream.
- **Method:** Use NVIDIA DALI to decode video directly to GPU memory.
- **Benefit:** Removes the last major CPU bottleneck (video decoding).

---

### 14.3 Implementation Schedule

| Phase | Effort | Impact | Dependencies | Status |
|-------|--------|--------|--------------|--------|
| 1: GPU Buffer | 2-3h | Foundation | None | ✅ Implemented |
| 2: GPU Enhancement | 3-4h | High | Phase 1 | ✅ Implemented |
| 3: GPU Resize | 1h | Medium | Phase 1 | ✅ Implemented |
| 4: Zero-Copy YOLO | 4-6h | High | Phases 1-3 | ✅ Implemented |
| 5: ROI Inference | Future | High | Phase 4 | ⬜ Planned |

### 14.4 Safety Measures

1. **Feature flag:** `USE_GPU_PATH = True/False` in `config.py`
2. **Fallback:** Auto-detect CUDA availability, graceful CPU fallback
3. **Memory monitoring:** Track GPU memory usage, warn if approaching limit
4. **A/B testing:** Compare FPS/latency between CPU and GPU paths
5. **Per-phase toggle:** Enable phases individually for debugging

### 14.5 Expected Performance Gains

| Metric | Current | After Phase 2 | After Phase 4 |
|--------|---------|---------------|---------------|
| Enhancement | 8-12ms | 2-4ms | 2-4ms |
| Upscale | 3-5ms | <1ms | <1ms |
| GPU↔CPU transfers | 3-4/frame | 1-2/frame | 1/frame |
| Total latency | 40-60ms | 30-45ms | 25-35ms |
| Estimated FPS gain | baseline | +20-30% | +40-50% |

### 14.6 Technical Notes

**OpenCV CUDA Requirements:**
- OpenCV must be compiled with CUDA support (`cv2.cuda.getCudaEnabledDeviceCount() > 0`)
- Pre-built pip packages typically lack CUDA; may need custom build
- Alternative: use `opencv-contrib-python` with CUDA or build from source

**Memory Considerations:**
- GPU memory usage increases with frame buffer on GPU
- At 1080p: ~6MB per frame (BGR uint8)
- With upscale 2x: ~24MB per frame
- Keep ≤3 frames on GPU simultaneously to stay under 100MB overhead

**Stream Synchronization:**
- Use `cv2.cuda.Stream` for async operations
- Sync before CPU access: `stream.waitForCompletion()`
- Enables overlapping GPU operations with CPU work

### 14.7 Reference Implementation Code

**Stage-by-Stage Savings (Target):**

| Stage | Current | Target | Savings |
|---|---|---|---|
| Capture | OpenCV (CPU) | V4L2 DMA-BUF / GStreamer NVMM | ~10ms |
| CLAHE | cv2.createCLAHE (CPU) | cv2.cuda.createCLAHE | ~6-8ms |
| Gamma | cv2.LUT (CPU) | CuPy elementwise kernel | ~1ms |
| Upscale | cv2.resize (CPU) | cv2.cuda.resize | ~2ms |
| Inference | PyTorch FP16 | TensorRT FP16 | ~15-25ms |
| **Total** | **~70-100ms** | **~30-50ms** | **~40-50ms** |

**CuPy Gamma Kernel Example:**
```python
import cupy as cp

gamma_kernel = cp.ElementwiseKernel(
    'uint8 x, float32 inv_gamma',
    'uint8 y',
    'y = (uint8)(powf((float)x / 255.0f, inv_gamma) * 255.0f)',
    'gamma_correction'
)
# Apply: gpu_frame = gamma_kernel(gpu_frame, 1.0/1.2)
```

**Zero-Copy PyTorch Bridge:**
```python
# Convert cv2.cuda.GpuMat to PyTorch tensor without CPU copy
import torch

# Option 1: Via CuPy (requires dlpack)
cupy_array = cp.asarray(gpu_mat)
torch_tensor = torch.as_tensor(cupy_array, device='cuda')

# Option 2: Direct pointer (advanced, requires matching memory layout)
# torch.cuda.memory.caching_allocator_alloc(size)
```

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

-   CUDA 12.1+
-   cuDNN 8.x or 9.x (bundled with torch)
-   Linux (Ubuntu 22.04+ recommended) or Windows 10/11
-   GStreamer (optional, for RTSP sources)

---

## Appendix B: OSC Testing

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

## Appendix C: Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| No camera found | Wrong index | Try CAMERA_INDEX = 1, 2, ... |
| Low FPS (<10) | High upscale | Reduce UPSCALE_FACTOR |
| Missing detections | Dark scene | Increase CLAHE_CLIP_LIMIT |
| ID swaps | Fast movement | Increase TRACKER_DISTANCE_THRESHOLD |
| Ghost tracks | False detections | Increase YOLO_CONFIDENCE |
| CUDA OOM | Large upscale | Reduce UPSCALE_FACTOR or use smaller model |

---

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2025-12-06 | AI/Human collaboration | Initial specification |
| 1.1 | 2025-12-08 | AI/Human collaboration | Video recording system, UI improvements |
| 1.2 | 2025-12-08 | AI/Human collaboration | TensorRT integration with GUI controls |
| 1.3 | 2025-12-09 | AI/Human collaboration | Restructured: hardware guide split out, paths updated, cleanup |
| 1.4 | 2025-12-09 | AI/Human collaboration | Video playback (threaded decoder, speed control, pause/step), tooltips, safe defaults, smoothing slider |
| 1.5 | 2025-12-09 | AI/Human collaboration | GPU Path Implementation plan (Section 14) |
| 1.6 | 2025-12-09 | AI/Human collaboration | GPU Path Completed (Phase 1-4), Temporal Denoising, ROI Roadmap |
| 1.7 | 2025-12-09 | AI/Human collaboration | UI Refinement: Moved Denoise to PREPROC row |
| 1.8 | 2026-02-01 | AI/Human collaboration | Production hardware purchased: IDS U3-34E0XCP camera, Tamron 8mm lens, MidOpt BP850 filter, ASUS ROG SCAR 16 (RTX 5080) |

---

*This document serves as the authoritative technical specification for the WallDance project. Update this document as requirements evolve and implementations progress.*