# WallDance - Technical Specifications

**Project:** Multi-Person Pose Detection for Wall Dancers  
**Version:** 1.0  
**Last Updated:** December 6, 2025  
**Status:** Prototype Phase

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
10.  [Technical Challenges & Solutions](#10-technical-challenges--solutions)
11.  [Prototype Status](#11-prototype-status)
12.  [Future Enhancements](#12-future-enhancements)
13.  [Proposed Improvements (Dec 2025)](#13-proposed-improvements-dec-2025)

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

| Component | Specification | Rationale |
|---|---|---|
| Camera | Sony Alpha 7 (or equivalent) | Low-light sensitivity, clean 1080p output |
| Resolution | 1920×1080 (Full HD) | Balance of coverage and detail |
| Frame Rate | 30 FPS | Standard capture rate |
| Output | Clean HDMI / SDI | Via capture card to PC |
| Lens | Wide-angle (24-35mm equiv.) | Cover 50m scene from safe distance |
| Mounting | Fixed tripod/rigging | Stable, unobstructed view |

**Calculated Figure Size:**

-   At 1080p covering 50m width: 1920px / 50m = 38.4 px/m
-   Average dancer height (1.7m): 1.7m × 38.4 = **~65 pixels**
-   This is below optimal detection threshold (~100px), requiring upscaling

### 3.2 Processing Hardware

| Component | Minimum | Recommended | Notes |
|---|---|---|---|
| GPU | RTX 3070 | RTX 3090 / RTX 4080 | CUDA compute for inference |
| VRAM | 8 GB | 24 GB | Model + upscaled frames |
| CPU | 8-core | 16-core | Pre/post processing |
| RAM | 16 GB | 32 GB | Frame buffers |
| Storage | SSD | NVMe SSD | Fast model loading |

### 3.3 Capture Interface

| Option | Latency | Quality | Cost | Pros | Cons |
|---|---|---|---|---|---|
| Elgato Cam Link 4K | ~100ms | Good | $130 | USB plug-and-play, portable, widely available | Higher latency, USB bandwidth limits, occasional driver issues |
| Blackmagic DeckLink | ~30ms | Excellent | $200+ | Lowest latency, professional SDI/HDMI, rock-solid drivers | Requires PCIe slot, higher cost, fixed installation |
| AVerMedia Live Gamer | ~50ms | Good | $150 | Good balance, PCIe reliability, gamer-focused features | Middle-ground on all specs, less pro features than Blackmagic |
| Magewell Pro Capture | ~20ms | Excellent | $300+ | Ultra-low latency, SDK support, multi-input options, Linux drivers | Premium price, overkill for simple setups |

### 3.4 Machine Vision Cameras (Direct USB3/GigE)

These cameras connect directly to the PC without a capture card, providing lower latency and higher control.

| Option | Interface | Resolution | FPS | Cost | Pros | Cons |
|---|---|---|---|---|---|---|
| FLIR Blackfly S USB3 | USB3 Vision | Up to 5MP | 30-160 | $400-800 | Very low latency (~5ms), Spinnaker SDK, global shutter options, excellent Linux support | Requires SDK integration, no standard webcam interface |
| Basler ace 2 Basic | USB3/GigE | Up to 5MP | 30-120 | $300-500 | Low latency, Pylon SDK, good value, reliable industrial quality | SDK learning curve, basic feature set |
| Basler ace 2 Pro | USB3/GigE | Up to 5MP | 30-120 | $500-900 | Ultra-low latency, advanced features (PTP sync, chunk data), SFP+ GigE option | Higher cost, more complex setup |

**Notes:**
- Machine vision cameras bypass HDMI/SDI capture entirely
- USB3 Vision provides ~5-10ms glass-to-RAM latency
- GigE Vision allows cable runs up to 100m (vs 5m for USB3)
- Requires camera SDK (Spinnaker, Pylon) instead of OpenCV VideoCapture
- Global shutter recommended for moving subjects (no rolling shutter artifacts)

### 3.5 Low-Light Machine Vision Cameras (Recommended for Night Performance)

For outdoor night performances, standard machine vision sensors struggle. The following cameras use specialized low-light sensors (Sony Starvis or large-pixel Global Shutter) optimized for dark conditions.

#### Recommended Low-Light Models

| Brand | Model | Sensor | Pixel Size | Form Factor | Pros | Cons |
|---|---|---|---|---|---|---|
| **IDS** | uEye+ U3-3860CP | Sony IMX462 (Starvis 2) | 2.9µm | Metal C-Mount | **Best low-light sensor**, NIR sensitivity, rugged, modern ids_peak SDK | Less common brand |
| Basler | ace U acA1920-40uc | Sony IMX249 (Pregius GS) | 5.86µm | Metal C-Mount | Huge pixels = clean low-light, Global Shutter (no motion blur), proven Pylon SDK | Not Starvis, but excellent |
| Basler | dart daA1920-30uc | Sony IMX290 (Starvis 1) | 2.9µm | Board/S-Mount | Cheapest Starvis option, tiny form factor | Requires S-mount adapter, board-level |
| FLIR | BFS-U3-21S4C-C | Sony IMX290 (Starvis 1) | 2.9µm | Metal C-Mount | Starvis in robust case, Spinnaker SDK | Often backordered |
| FLIR | BFS-U3-31S4C-C | Sony IMX265 (Global Shutter) | 3.45µm | Metal C-Mount | High dynamic range, no motion blur | Not Starvis, moderate low-light |

#### Sensor Technology Comparison

| Sensor Type | Example | Low-Light Performance | Motion Handling | Best For |
|---|---|---|---|---|
| **Sony Starvis 2** | IMX462 | ⭐⭐⭐⭐⭐ Excellent | Rolling shutter | Maximum darkness, NIR lighting |
| Sony Starvis 1 | IMX290 | ⭐⭐⭐⭐ Very Good | Rolling shutter | Dark scenes, budget option |
| Sony Pregius (Large Pixel) | IMX249 | ⭐⭐⭐⭐ Very Good | ✅ Global Shutter | Moving subjects in low light |
| Standard Global Shutter | IMX265 | ⭐⭐⭐ Good | ✅ Global Shutter | Moderate darkness with motion |

#### Recommendations by Priority

1. **Best Overall (if open to IDS brand):** IDS uEye+ U3-3860CP
   - Sony IMX462 (Starvis 2) is the best low-light sensor available
   - Standard C-mount, rugged metal case
   - Modern `ids_peak` SDK works well on Linux

2. **Best Basler Option:** ace U acA1920-40uc
   - Sony IMX249 with huge 5.86µm pixels
   - Often cleaner than Starvis in moderate darkness
   - Global Shutter eliminates motion blur on dancers
   - Avoids board-level dart form factor hassle

3. **Best FLIR Option:** Blackfly S BFS-U3-21S4C-C
   - Sony IMX290 Starvis in standard metal case
   - Robust and field-proven
   - Note: Check availability (often backordered)

**Key Insight:** Large pixel sensors (IMX249: 5.86µm) can outperform smaller Starvis pixels (IMX462: 2.9µm) in moderate darkness by collecting more light per pixel with less noise. Global Shutter is a major advantage for capturing moving dancers.

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
05-WallDance1080p/
├── main.py              # Application entry point, main loop
├── gui.py               # DearPyGui control panel
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

| Stage | Target | Measured | Notes |
|---|---|---|---|
| Capture | <50ms | ~30-100ms | Depends on capture card |
| Enhancement | <5ms | ~3ms | GPU accelerated CLAHE |
| Upscale | <5ms | ~2ms | GPU resize |
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

| Task | Status | Notes |
|---|---|---|
| Basic MoveNet skeleton detection | ✅ | 01-MoveNet |
| MMPose integration | ✅ | 02-MMPose (torch 2.4.x compatibility) |
| YOLO11-pose multi-person | ✅ | 03-Yolo11m |
| Kalman+Hungarian tracking | ✅ | 04-RTMPose |
| Integrated solution | ✅ | 05-WallDance1080p |

### Phase 2: Optimization (Current)

| Task | Priority | Status | Est. Effort |
|---|---|---|---|
| Fine-tune detection confidence | High | 🔄 | 2h |
| Tune tracker for real scene | High | 🔄 | 4h |
| Test with actual camera setup | High | ⬜ | 4h |
| Profile and optimize bottlenecks | Medium | ⬜ | 8h |
| Add recording/playback mode | Medium | ⬜ | 4h |

### Phase 3: Production Hardening

| Task | Priority | Status | Est. Effort |
|---|---|---|---|
| Robust error handling | High | ⬜ | 4h |
| Auto-reconnect camera | High | ⬜ | 2h |
| Configuration file (YAML) | Medium | ⬜ | 2h |
| Logging system | Medium | ⬜ | 2h |
| Systemd service integration | Low | ⬜ | 2h |
| Health monitoring endpoint | Low | ⬜ | 4h |

### Phase 4: Advanced Features

| Task | Priority | Status | Est. Effort |
|---|---|---|---|
| 4K input support | Medium | ⬜ | 4h |
| Multi-camera stitching | Low | ⬜ | 16h |
| 3D pose estimation | Low | ⬜ | 24h |
| Gesture recognition | Low | ⬜ | 16h |
| Web dashboard | Low | ⬜ | 12h |

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

## 11. Prototype Status

### 11.1 Implemented Prototypes

| Prototype | Purpose | Status | Key Learning |
|---|---|---|---|
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

**Goal:** Keep frame data on GPU from capture to inference, eliminating CPU-GPU transfers.

**Current Pipeline (CPU-bound):**
```
Camera → CPU (OpenCV) → CPU (CLAHE) → CPU (Gamma) → CPU (Upscale) → GPU (YOLO) → CPU (Results)
         ↑ copy        ↑ process    ↑ process     ↑ process       ↑ upload     ↑ download
```

**Proposed Pipeline (GPU-native):**
```
Camera → GPU (DMA-BUF) → GPU (CUDA CLAHE) → GPU (CUDA Gamma) → GPU (CUDA Resize) → GPU (TensorRT) → GPU (Results)
         ↑ zero-copy    ↑ kernel           ↑ kernel           ↑ kernel            ↑ zero-copy      ↑ stays on GPU
```

**Implementation Stages:**

| Stage | Current | Proposed | Savings |
|---|---|---|---|
| Capture | OpenCV (CPU) | V4L2 DMA-BUF / GStreamer NVMM | ~10ms |
| CLAHE | cv2.createCLAHE (CPU) | cv2.cuda.createCLAHE / CuPy kernel | ~3ms |
| Gamma | cv2.LUT (CPU) | CuPy elementwise kernel | ~1ms |
| Upscale | cv2.resize (CPU) | cv2.cuda.resize / torch.nn.functional | ~2ms |
| Inference | PyTorch FP16 | TensorRT FP16/INT8 | ~15-25ms |
| **Total** | **~70-100ms** | **~30-50ms** | **~40-50ms** |

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

**Priority Implementation Order:**
1. **TensorRT export** - Easiest win, 2× inference speedup
2. **GPU CLAHE** - Fix existing unused cv2.cuda.createCLAHE code
3. **CuPy gamma** - Simple kernel, eliminates CPU LUT
4. **GStreamer capture** - Replaces OpenCV VideoCapture
5. **Full pipeline integration** - Connect all GPU stages

---

## 13. Proposed Improvements (Dec 2025)

### 13.1 UI Usability
- ✅ Start/Stop camera button next to camera selector (implemented).
- ✅ Status badges in top bar: camera, OSC, model, FPS (implemented).
- Tooltips for key sliders (confidence, imgsz, frame skip, FP16) and inline numeric value display (todo).
- “Safe defaults” button to restore a known-good preset for on-site recovery (todo).
- Searchable project/config dropdowns when many presets exist (todo).

### 13.2 UI & Preview Performance
- ✅ Preview downscale slider (0.3–1.0) already present.
- ✅ Preview on/off (pause preview) already present; processing/OSC continue when off.
- ✅ Preview texture uploads capped to ~15 FPS to reduce GPU/UI load (implemented).
- “Low-impact preview” toggle (could combine downscale + throttle into one control) (todo).

### 13.3 Detection/Tracking Robustness (Low Light, Long Distance)
- Two-stage exposure logic: when brightness is low, force enhancement and slightly lower detection confidence.
- Temporal confidence smoothing to keep detections alive briefly on dips.
- Dynamic NMS/IoU tuned for small boxes to reduce duplicate detections at long distance.
- Brightness/contrast watchdog that raises gamma/CLAHE when the scene darkens.
- Per-track quality score: freeze/hold OSC output for low-quality tracks instead of dropping IDs.

### 13.4 Detection Performance (Robustness First)
- Auto model step-down only when FPS < target and confidence > floor; otherwise keep mid model.
- Auto imgsz downshift when GPU load >90% while keeping confidence threshold unchanged.
- Optional ROI cropping to skip sky/ground pixels and cut inference cost.
- Cache resized frames during frame-skip cycles to avoid repeated upscales.

### 13.5 Additional Features
- Offline replay mode: load video files and emit OSC for QA without a live camera.
- Logging/export: per-frame metrics (fps, brightness, latency, track counts) to CSV.
- Alerting: notifications on OSC send failure or camera disconnect; optional auto-retry.
- Model checksum/display: show model file hash and load time to verify correct weights on-site.

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
|---|---|
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

---

*This document serves as the authoritative technical specification for the WallDance project. Update this document as requirements evolve and implementations progress.*