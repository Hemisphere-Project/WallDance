# WallDance - Technical Specifications

**Project:** Multi-Person Pose Detection for Wall Dancers  
**Version:** 2.0  
**Last Updated:** March 26, 2026  
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
| Lens | Tamron M118FM08 | 8mm, 1/1.8", C-Mount, F1.8 | Wide FOV (~50Â° HFOV), bright aperture for low-light |
| IR Filter | MidOpt BP850-25.4 | 850nm bandpass, C-Mount | Blocks projector light, passes IR illumination |
| Resolution | 2688Ã—1520 (4MP native) | Native 4MP, can crop/bin to 1080p | High resolution for distant subjects |
| Frame Rate | 30-60 FPS | Configurable via SDK | Adjustable based on exposure needs |
| Interface | USB3 Vision | Direct to PC, no capture card | Low latency (~5-10ms), SDK control |
| Mounting | Fixed tripod/rigging | Stable, unobstructed view | Weather housing recommended |

**Calculated Figure Size:**

-   At 1080p covering 50m width: 1920px / 50m = 38.4 px/m
-   Average dancer height (1.7m): 1.7m Ã— 38.4 = **~65 pixels**
-   At 4MP (2688px) covering 50m: 2688px / 50m = 53.8 px/m â†’ **~91 pixels**
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
| F1 | Multi-person detection (up to 6) | Critical | âœ… Implemented |
| F2 | 17-keypoint skeleton extraction | Critical | âœ… Implemented |
| F3 | Persistent ID tracking across frames | Critical | âœ… Implemented |
| F4 | Low-light image enhancement | High | âœ… Implemented |
| F5 | OSC output protocol | High | âœ… Implemented |
| F6 | Real-time visualization | Medium | âœ… Implemented |
| F7 | Configurable parameters | Medium | âœ… Implemented |
| F8 | Resolution upscaling | High | âœ… Implemented |
| F9 | DearPyGui control panel | Medium | âœ… Implemented |
| F10 | Runtime model switching | Medium | âœ… Implemented |
| F11 | FP16 half-precision inference | Medium | âœ… Implemented |
| F12 | Frame skip option | Low | âœ… Implemented |
| F13 | TensorRT acceleration | High | âœ… Implemented |
| F14 | Auto model download | Medium | âœ… Implemented |

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
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”     â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚   IDS Camera      â”‚â”€â”€â”€â”€â–¶â”‚     WallDance        â”‚
â”‚ (U3-34E0XCP, USB3)â”‚     â”‚     Application      â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                     â”‚
                    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                    â”‚                    â–¼                        â”‚
                    â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
                    â”‚  â”‚  Enhancer   â”‚â”€â–¶â”‚   Detector   â”‚â”€â–¶â”‚  Tracker   â”‚  â”‚
                    â”‚  â”‚(CLAHE+Î³ GPU)â”‚  â”‚ (YOLO11 TRT) â”‚  â”‚(Kalman+Hung)â”‚ â”‚
                    â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”˜ â”‚
                    â”‚                                           â”‚        â”‚
                    â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”              â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â” â”‚
                    â”‚  â”‚    OSC       â”‚â—€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”‚  Visualization  â”‚ â”‚
                    â”‚  â”‚   Output     â”‚              â”‚     Display     â”‚ â”‚
                    â”‚  â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜              â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜ â”‚
                    â”‚         â”‚                   WallDance Application  â”‚
                    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                              â”‚
                              â–¼
                    â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
                    â”‚   OSC Receivers         â”‚
                    â”‚  - VJ Software          â”‚
                    â”‚  - Lighting DMX         â”‚
                    â”‚  - Projection Mapping   â”‚
                    â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### 5.2 Software Stack

| Layer | Technology | Version | Purpose |
|---|---|---|---|
| Runtime | Python | 3.10+ | Main application |
| ML Framework | PyTorch | 2.10+ | GPU inference |
| Detection | Ultralytics YOLO11 | Latest | Pose estimation |
| Tracking | SciPy | Latest | Kalman filter, Hungarian algorithm |
| GPU Processing | Kornia | Latest | GPU enhancement (CLAHE, gamma) |
| Image Processing | OpenCV | 4.x | CPU fallback, video I/O |
| GUI | DearPyGui | 2.1+ | GPU-accelerated control panel |
| OSC | python-osc | Latest | Network output |
| Package Manager | uv | Latest | Fast dependency management |

### 5.3 Module Structure

```
application/
â”œâ”€â”€ src/
â”‚   â”œâ”€â”€ main.py          # Application entry point
â”‚   â”œâ”€â”€ app.py           # Main application orchestrator
â”‚   â”œâ”€â”€ gui.py           # DearPyGui control panel
â”‚   â”œâ”€â”€ gui_builder.py   # UI component builders
â”‚   â”œâ”€â”€ gui_icons.py     # Icon/theme helpers
â”‚   â”œâ”€â”€ config.py        # Configuration parameters
â”‚   â”œâ”€â”€ config_store.py  # Project/config persistence
â”‚   â”œâ”€â”€ enhancer.py      # Low-light enhancement (CLAHE + gamma)
â”‚   â”œâ”€â”€ tracker.py       # Kalman filter + Hungarian tracking
â”‚   â”œâ”€â”€ tracking_logger.py # Structured JSONL event logger
â”‚   â”œâ”€â”€ osc_output.py    # OSC message formatting
â”‚   â”œâ”€â”€ visualization.py # Drawing helpers, overlays
â”‚   â”œâ”€â”€ camera_manager.py# Camera handling (OpenCV)
â”‚   â”œâ”€â”€ ids_camera.py    # IDS Peak SDK camera + UnifiedCamera
â”‚   â”œâ”€â”€ model_manager.py # YOLO model loading/switching
â”‚   â”œâ”€â”€ pipeline.py      # Processing pipeline (CPU + GPU paths)
â”‚   â”œâ”€â”€ gpu_pipeline.py  # Zero-copy GPU pipeline (Kornia)
â”‚   â”œâ”€â”€ background.py    # Static background subtraction
â”‚   â”œâ”€â”€ motion_detector.py # MOG2 foreground blob detector
â”‚   â””â”€â”€ video_recorder.py# Recording functionality
â”œâ”€â”€ assets/              # Icons, fonts
â””â”€â”€ pyproject.toml       # Dependencies

# Workspace root scripts:
â”œâ”€â”€ run.sh / run.bat     # Launch application
â”œâ”€â”€ install.sh / install.bat # Install dependencies
â””â”€â”€ projects/            # Per-project configs and recordings

# Extra scripts:
â””â”€â”€ extra/
    â”œâ”€â”€ build_engines.sh   # Build TensorRT engines (Linux)
    â”œâ”€â”€ build_engines.bat  # Build TensorRT engines (Windows)
    â”œâ”€â”€ gpu_limiter.sh     # Set NVIDIA GPU power limit (Linux)
    â””â”€â”€ gpu_limiter.bat    # Set NVIDIA GPU power limit (Windows)
```

---

## 6. Detection & Tracking Pipeline

### 6.1 Pipeline Stages

```
Camera Frame
       â”‚
       â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ 1. GPU Upload     â”‚  Upload to GPU once (zero-copy for IDS)
â””â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚
       â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ 2. Enhancement    â”‚  Kornia CLAHE + Gamma (GPU)
â”‚    (if dark)      â”‚  Auto-detect brightness, progressive blend
â””â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚
       â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ 3. YOLO Inference â”‚  YOLO11 via TensorRT or PyTorch
â”‚    (zero-copy)    â”‚  GPU tensor passed directly to model
â””â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚
       â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚ 4. Tracking       â”‚  Cascaded matching, Mahalanobis gate,
â”‚    (Kalman+Hung)  â”‚  displacement gate, swap correction,
â”‚                   â”‚  MOG2 motion bridge for lost tracks
â””â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
       â”‚
       â–¼
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

Default `imgsz`: **800** (configurable via GUI: 640, 800, 960, 1280, 1536, 1920).

### 6.2.1 Performance Optimization Options

| Option | Speedup | Notes |
|---|---|---|
| **TensorRT Acceleration** | +50-100% | Toggle in GUI, requires engine build (2-5 min first time) |
| **FP16 Half Precision** | +20-30% | Toggle in GUI, minimal accuracy loss |
| **Frame Skip** | N+1Ã— fewer inferences | Reuses last tracking result for skipped frames |
| **Smaller Model** | 2-4Ã— faster | yolo11n vs yolo11m |

### 6.2.2 TensorRT Engine System

TensorRT engines provide significant inference speedup (2Ã—+) but are tied to specific input sizes.

**Engine Naming Convention:**
- Engines are named `{model}_{imgsz}.engine` (e.g., `yolo11m-pose_960.engine`)
- This allows multiple engines for different input sizes
- Engines are GPU-specific and must be rebuilt on different hardware

**GUI Controls:**
- **TRT Checkbox**: Enable/disable TensorRT for the current model
- If engine exists for current imgsz â†’ switches immediately
- If engine missing â†’ prompts to build (2-5 minutes)
- Engine built with FP16 for optimal speed/accuracy balance

**Build Process:**
1. User enables TRT checkbox in MODEL section
2. If no engine for current imgsz, prompt appears
3. GPU stats update during build (VRAM usage visible)
4. Engine saved to `models/` directory
5. Model automatically switches to TRT engine

**Automatic Fallback:**
- If TensorRT not installed â†’ checkbox disabled, toast shown
- If engine load fails â†’ falls back to PyTorch model
- On startup with saved TRT config but missing engine â†’ uses PyTorch

### 6.3 Keypoint Schema (COCO 17-point)

```
        0: Nose
       /       \
     1   2  (L/R Eye)
     /         \
   3       4  (L/R Ear)

    5â”€â”€â”€â”€â”€â”€â”€6  (L/R Shoulder)
    â”‚       â”‚
    7       8  (L/R Elbow)
    â”‚       â”‚
    9      10  (L/R Wrist)

   11â”€â”€â”€â”€â”€â”€12  (L/R Hip)
    â”‚       â”‚
   13      14  (L/R Knee)
    â”‚       â”‚
   15      16  (L/R Ankle)
```

### 6.4 Kalman Filter Design

**State Vector (6 dimensions):**

```
x = [x, y, vx, vy, ax, ay]áµ€
     â”‚  â”‚   â”‚   â”‚   â”‚   â””â”€â”€ Y acceleration
     â”‚  â”‚   â”‚   â”‚   â””â”€â”€â”€â”€â”€â”€ X acceleration  
     â”‚  â”‚   â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ Y velocity
     â”‚  â”‚   â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ X velocity
     â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€  Y position (centroid)
     â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€  X position (centroid)
```

**Motion Model:** Constant acceleration

```
F = [1  0  dt  0   0.5dtÂ²   0     ]
    [0  1  0   dt  0        0.5dtÂ²]
    [0  0  1   0   dt       0     ]
    [0  0  0   1   0        dt    ]
    [0  0  0   0   1        0     ]
    [0  0  0   0   0        1     ]
```

### 6.5 Multi-Stage Association

**Cost Matrix Construction:**

-   Cascaded matching: established tracks matched first, then tentative/suppressed
-   Mahalanobis gate (chiÂ² > 16.27, df=2) blocks implausible pairs
-   Displacement gate (max 0.5Ã— distance threshold) prevents centroid jumps
-   Weighted cost: position + skeleton shape + size + IoU + trajectory + separation + direction
-   Per-track merge zones detect close-proximity situations and apply specialized weights

**Assignment:**

-   Optimal bipartite matching using `scipy.optimize.linear_sum_assignment`
-   Post-assignment swap correction: occlusion swap, merge-direction swap, 2-opt swap
-   Unmatched detections â†’ force-update / fallback / resurrect from dormant / new track
-   Unmatched tracks â†’ MOG2 motion bridge (up to 80 frames) or age toward dormant

### 6.6 Motion Bridge (MOG2)

Bridges YOLO detection gaps using background subtraction foreground blobs:

| Tier | Condition | Update source | Keypoints |
|------|-----------|---------------|-----------|
| 1 | YOLO matched | Full detection | Live |
| 2 | No YOLO, blob available | MOG2 blob centroid | Frozen from last YOLO |
| 3 | No YOLO, no blob | Kalman-only prediction | Frozen |

Blobs never create new tracks or resurrect dormant ones. Progressive Kalman noise: RÃ—2 (1-10f), RÃ—4 (11-30f), RÃ—8 (31-80f).

---

## 7. Output Protocols

### 7.1 OSC Message Format

**Base Address:** `/walldance/`

| Address | Arguments | Type | Description |
|---|---|---|---|
| `/walldance/count` | `[n, id0, id1, ...]` | int | Count + active track IDs |
| `/walldance/dancer/centroid` | `[id, x, y]` | int, float | Normalized 0-1 |
| `/walldance/dancer/bbox` | `[id, x, y, w, h]` | int, float | Normalized 0-1 |
| `/walldance/dancer/velocity` | `[id, vx, vy]` | int, float | Normalized per frame |
| `/walldance/dancer/keypoints` | `[id, x0,y0,c0, ...]` | int, float | 1 + 51 values (id + 17Ã—3) |
| `/walldance/clear` | `[1]` | int | Reset signal |

**Coordinate System:**

-   Origin: Top-left (0, 0)
-   X: 0 (left) â†’ 1 (right)
-   Y: 0 (top) â†’ 1 (bottom)

### 7.2 OSC Configuration

| Parameter | Default | Notes |
|---|---|---|
| IP Address | 127.0.0.1 | Target receiver |
| Port | 9000 | Standard OSC port |
| Protocol | UDP | Low latency |

### 7.3 Future Protocol Options

| Protocol | Use Case | Complexity |
|---|---|---|
| **OSC** | VJ/Audio software | âœ… Implemented |
| MQTT | IoT, distributed systems | Medium |
| WebSocket | Web-based visualizers | Medium |
| DMX/ArtNet | Direct lighting control | High |
| NDI | Video streaming with metadata | High |

---

## 8. Performance Targets

### 8.1 Latency Budget

| Stage | Target | Typical | Notes |
|---|---|---|---|
| Capture | <10ms | ~0.3ms (IDS GPU direct) | IDS USB3 zero-copy; ~3ms OpenCV |
| Enhancement | <5ms | ~2-4ms | GPU Kornia CLAHE+gamma |
| YOLO Inference | <30ms | ~15-18ms | TensorRT FP16, imgsz 800 |
| Tracking | <2ms | ~1ms | CPU, lightweight |
| OSC Send | <1ms | <1ms | UDP, no confirmation |
| **Total** | **<50ms** | **~20-25ms** | Glass-to-OSC (GPU path) |

### 8.2 Frame Rate Targets

Frame rate depends on model, imgsz, and inference backend (TRT vs PyTorch).

| Model | imgsz | Backend | Typical FPS (RTX 5080) |
|---|---|---|---|
| yolo11n-pose | 800 | TensorRT | ~60+ |
| yolo11s-pose | 800 | TensorRT | ~50+ |
| yolo11m-pose | 800 | TensorRT | ~35-40 |
| yolo11m-pose | 1280 | TensorRT | ~20-25 |
| yolo11l-pose | 800 | TensorRT | ~20-25 |
| yolo11x-pose | 1280 | TensorRT | ~10-15 |

### 8.3 Resource Utilization (RTX 5080 Laptop)

| Resource | Typical Usage | Peak |
|---|---|---|
| GPU Compute | 40-60% | 90% |
| VRAM | 2-4 GB | 8 GB |
| CPU | 10-20% | 35% |
| RAM | 2-3 GB | 4 GB |

---

## 9. Implementation Roadmap

### Phase 1: Prototyping âœ… COMPLETE

Prototypes in `prototypes/` folder explored MoveNet, MMPose, YOLO11, and RTMPose tracking. The final integrated solution is in `application/`.

### Phase 2: Optimization âœ… COMPLETE

- âœ… Detection confidence tuning
- âœ… Tracker tuning for real scenes
- âœ… Recording/playback mode
- âœ… TensorRT acceleration
- âœ… FP16 inference

### Phase 3: Production Hardening âœ… COMPLETE

- âœ… JSON configuration persistence
- âœ… Project/preset management
- âœ… Full GPU processing pipeline (zero-copy)
- âœ… IDS industrial camera integration
- âœ… Advanced tracker (cascaded matching, motion bridge)
- âœ… Structured tracking event logger
- ðŸŸ¡ Stall detection + diagnostics logging (detection only, no auto-recovery)
- â¬œ Auto-reconnect camera on disconnect
- â¬œ Long-run stability test (4+ hours)

### Phase 4: Advanced Features (Future)

- â¬œ Tiling for 4K inference
- â¬œ Multi-camera stitching
- â¬œ 3D pose estimation
- â¬œ Web dashboard

---

## 10. Technical Challenges & Solutions

### 10.1 Small Figure Detection

**Challenge:** Dancers appear 65-91 pixels tall at native resolution, below YOLO optimal range (100px+).

**Solution:** Configurable `imgsz` parameter (default 800, up to 1920) controls the internal
resolution YOLO operates at. Higher imgsz = better small-figure detection at the cost of FPS.
The IDS 4MP camera (2688Ã—1520) provides ~91px native dancer height, improved from 1080p.

**Alternative approaches considered:**

| Approach | Pros | Cons |
|---|---|---|
| Higher resolution camera | Native quality | Bandwidth, cost |
| **Higher imgsz** | Flexible, GPU-only cost | GPU load |
| Tiled detection | Full resolution | Complexity (planned) |
| Custom trained model | Optimized for small | Training data needed |

### 10.2 Low-Light Performance

**Challenge:** Outdoor night performance with minimal lighting.

**Solution:** GPU-accelerated adaptive enhancement pipeline (Kornia on CUDA).

1.  **Brightness detection**: Calculate mean brightness (decimated to every 10th frame)
2.  **CLAHE**: Contrast-limited adaptive histogram equalization (GPU)
3.  **Gamma correction**: Brighten dark regions (GPU)
4.  **Progressive blend**: Smooth transition based on brightness level
5.  **Temporal denoising**: Optional GPU exponential moving average
6.  **Auto-toggle**: Skip enhancement if scene is bright enough

**Parameters:**

```python
CLAHE_CLIP_LIMIT = 3.0      # Contrast boost (1.0-5.0)
GAMMA_CORRECTION = 1.2       # Brightness boost
BRIGHTNESS_THRESHOLD = 60    # Auto-detect threshold
DENOISE_STRENGTH = 0.0       # Temporal denoising (0 = off)
```

### 10.3 ID Persistence During Fast Movement

**Challenge:** Dancers moving quickly cause ID swaps and ghost tracks.

**Solution:** Multi-stage hardened association pipeline.

1.  **6-state Kalman filter**: Track position + velocity + acceleration
2.  **Cascaded matching**: Established tracks matched first, then tentative
3.  **Mahalanobis gate**: Statistical distance gate (chiÂ² = 16.27, df=2)
4.  **Displacement gate**: Caps per-frame centroid jump (0.5Ã— threshold)
5.  **Post-assignment swap correction**: Occlusion swap, merge-direction swap, 2-opt swap
6.  **Per-track merge zones**: Only nearby tracks get merge context
7.  **MOG2 motion bridge**: Keeps lost tracks alive up to 80 frames via foreground blobs
8.  **Dormant pool**: Tracks sleep for 150 frames before permanent deletion; can resurrect

**Key parameters:**

```python
TRACKER_MAHALANOBIS_GATE = 16.27       # ChiÂ² gate (99.97%)
TRACKER_MAX_DISPLACEMENT_RATIO = 0.5   # Centroid jump cap
TRACKER_CLOSE_PROXIMITY_RATIO = 0.35   # Merge zone trigger
TRACKER_MAX_AGE = 45                   # Frames before dormant
TRACKER_DORMANT_MAX_AGE = 150          # Frames in dormant pool
MOTION_BRIDGE_MAX_FRAMES = 80          # MOG2 bridge duration
```

### 10.4 Rotated Body Orientations

**Challenge:** Wall dancers may be upside-down, sideways, or at any angle.

**Solution:** YOLO11-pose handles arbitrary orientations well.

-   No pre-rotation needed
-   Keypoint order remains consistent regardless of body orientation
-   Bounding box computed from keypoints if needed

### 10.5 PyTorch/CUDA Compatibility

**Challenge:** New GPU architectures (e.g. RTX 50-series `sm_120`) may not be supported by pinned PyTorch builds.

**Solution:** `install.bat` auto-detects GPU compatibility and tries PyTorch wheel indexes in order: `cu130`, `cu129`, `cu128`, `cu126`, `cu124`. Manual override available via PyTorch selector at https://pytorch.org/get-started/locally/.

```toml
[dependencies]
torch = ">=2.10.0"
torchvision = ">=0.25.0"
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
YOLO_MODEL = "yolo11m-pose.pt"    # Best accuracy/speed balance
YOLO_IMGSZ = 800                  # Default inference resolution
YOLO_CONFIDENCE = 0.25            # Permissive detection
MAX_PERSONS = 6                   # Target dancer count
USE_TENSORRT = True               # TensorRT by default
USE_GPU_PATH = True               # Full GPU pipeline
ENHANCE_ENABLED = True            # Auto low-light enhancement
CLAHE_CLIP_LIMIT = 3.0
GAMMA_CORRECTION = 1.2
TRACKER_MAX_AGE = 45              # Robust to brief occlusions
TRACKER_VELOCITY_WEIGHT = 0.6     # Trust motion prediction
MOTION_BRIDGE_ENABLED = True      # MOG2 gap bridging
IDS_USE_GPU_DIRECT = True         # Zero-copy IDS path
IDS_MAX_FPS = 20                  # Preview cap for PCIe stability
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
| Single camera | Limited coverage | Multi-cam in future |
| 2D pose only | No depth | 3D estimation in future |
| UDP OSC | No delivery guarantee | Add TCP option |
| USB3/PCIe stalls (IDS) | ~1.65s gaps under GPU load | Preview FPS cap, CUDA stream, stall detection |
| Visualization CPU-bound | Preview-only impact | GPU shaders (future) |

---

## 12. Future Enhancements

### 12.1 Near-Term

| Enhancement | Description | Benefit |
|---|---|---|
| Tiling inference | 2x1 grid for 4K input | Better pixel density at high res |
| Auto-reconnect camera | Detect disconnect, restart | Unattended operation |
| Per-show log folder | Timestamped metrics + configs | Post-show diagnostics |
| OSC status broadcast | Heartbeat, state, FPS, errors | Remote monitoring |
| Ghost track suppression | Kill low-confidence persistent tracks | Cleaner output |

### 12.2 Medium-Term

| Enhancement | Description | Benefit |
|---|---|---|
| Multi-camera | Stitch 2-3 cameras | Wider/taller coverage |
| Depth estimation | Monocular depth | Z-axis movement |
| Scene exclusion zones | Per-project configurable masks | Reduce false detections |
| Web dashboard | Browser-based config/monitor | Remote management |
| CSV metrics export | FPS, latency, brightness, track count | Analytics |

### 12.3 Long-Term

| Enhancement | Description | Benefit |
|---|---|---|
| 3D pose estimation | Multi-view triangulation | True 3D positions |
| Action recognition | Temporal pose analysis | Dance move detection |
| Edge deployment | Jetson Orin / similar | Standalone unit |

---

## Appendix A: Dependencies

### Python Packages

```toml
[dependencies]
torch = ">=2.10.0"
torchvision = ">=0.25.0"
ultralytics = ">=8.3.0"
opencv-python = ">=4.8"
python-osc = ">=1.8"
scipy = ">=1.10"
numpy = ">=1.24,<2.0"
dearpygui = ">=2.0"
filterpy = ">=1.4.5"

[project.optional-dependencies]
gpu = ["nvidia-ml-py", "tensorrt>=10.0", "kornia>=0.8.2", "onnx", "onnxslim", "onnxruntime-gpu"]
ids = ["ids-peak>=1.13", "ids-peak-ipl>=1.17"]
```

### System Requirements

-   CUDA 12.x+ (bundled with torch)
-   cuDNN (bundled with torch)
-   Windows 10/11 or Linux (Ubuntu 22.04+ recommended)
-   Python 3.10+ (< 3.13)
-   `uv` package manager

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
/walldance/count ii 2 1
/walldance/dancer/centroid if 1 0.350000 0.450000
/walldance/dancer/bbox iffff 1 0.300000 0.400000 0.100000 0.200000
/walldance/dancer/velocity iff 1 0.005000 -0.002000
/walldance/dancer/keypoints ifff... 1 (51 float values)
```

---

## Appendix C: Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| No camera found | Wrong index | Try CAMERA_INDEX = 1, 2, ... |
| Low FPS (<10) | Heavy model/imgsz | Use smaller model or lower imgsz |
| Missing detections | Dark scene | Increase CLAHE_CLIP_LIMIT or raise imgsz |
| ID swaps | Fast movement / close dancing | See TRACKING_PLAN.md for tuning |
| Ghost tracks | False detections | Increase YOLO_CONFIDENCE |
| CUDA OOM | Large imgsz + big model | Lower imgsz or use smaller model |
| TRT checkbox disabled | TensorRT not installed | Install with `pip install tensorrt` |
| USB3 stalls (IDS) | PCIe bus contention | Enable preview FPS cap, lower imgsz |
| CPU fallback shown | PyTorch/CUDA mismatch | Re-run install.bat, check GPU driver |

---

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2025-12-06 | AI/Human collaboration | Initial specification |
| 1.1 | 2025-12-08 | AI/Human collaboration | Video recording system, UI improvements |
| 1.2 | 2025-12-08 | AI/Human collaboration | TensorRT integration with GUI controls |
| 1.3 | 2025-12-09 | AI/Human collaboration | Restructured: hardware guide split out, paths updated, cleanup |
| 1.4 | 2025-12-09 | AI/Human collaboration | Video playback, tooltips, safe defaults, smoothing slider |
| 1.5 | 2025-12-09 | AI/Human collaboration | GPU Path Implementation plan |
| 1.6 | 2025-12-09 | AI/Human collaboration | GPU Path Completed (Phase 1-4), Temporal Denoising |
| 1.8 | 2026-02-01 | AI/Human collaboration | Production hardware: IDS camera, RTX 5080 laptop |
| 2.0 | 2026-03-26 | AI/Human collaboration | Major update: IDS camera integration, advanced tracker (Mahalanobis/displacement gates, cascaded matching, motion bridge), full GPU pipeline with Kornia, removed obsolete UPSCALE_FACTOR, cleaned completed items from future sections, updated all config values and diagrams |

---

*This document serves as the authoritative technical specification for the WallDance project. Update this document as requirements evolve and implementations progress.*
