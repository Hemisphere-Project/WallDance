# WallDance Production TODO

Progress tracker — updated Feb 2026.

---

## Phase 1 — IDS Camera Integration ✅

**Goal:** Get the production camera working before UI work.

- ✅ Install IDS Peak SDK on laptop
- ✅ Create `ids_camera.py` wrapper (acquisition, exposure, gain controls)
  - `IDSCamera` class with low-latency buffer strategy (newest-frame-only, 3 buffers)
  - `UnifiedCamera` class for transparent IDS/OpenCV switching
  - Mono10/12 support with fast numpy-only unpacking
- ✅ Handle Mono10/12 → Mono8 → BGR conversion for YOLO pipeline
  - GPU-accelerated Mono8 → BGR expansion
  - `read_gpu()` method returns `(1,3,H,W)` GPU tensor directly
- ✅ Integrate with app.py and pipeline.py
  - `process_gpu_direct()` in pipeline for zero-copy IDS path
  - `process_gpu_tensor()` in gpu_pipeline for pre-uploaded tensors
  - Camera refresh lists both OpenCV and IDS cameras
  - Main loop auto-selects GPU direct path when IDS active
- ✅ Test end-to-end: IDS camera → detection → OSC output
- ✅ Fallback path: keep OpenCV VideoCapture for dev/testing with webcam
  - `UnifiedCamera` auto-detects and falls back gracefully
  - Use "auto" source for automatic IDS→OpenCV fallback
- ✅ IDS on-device ROI crop with pixel-budget model and runtime ratio slider
- ✅ USB3 stall detection with buffer pool health monitoring (400ms threshold)
- ✅ UserSet loading on startup
- ✅ Runtime exposure/gain control with auto enable/disable

| Path | Upload | Convert | Total overhead |
|------|--------|---------|----------------|
| OpenCV | ~2ms | ~1ms (BGR→RGB flip) | ~3ms |
| IDS GPU Direct | ~0.3ms | 0ms (already RGB) | ~0.3ms |

---

## Phase 2 — GUI Hierarchy & Controls ✅

**Goal:** Single GUI with clear hierarchy — live controls prominent, tweaky settings secondary.

- ✅ Reorganize GUI into collapsible sections:
  - **Show Settings** — person height, max dancers, confidence
  - **Visualization** — compact S/K/B/T/I toggle buttons
  - **Input** — camera source, exposure, gain
  - **Enhancement** — CLAHE, gamma, brightness threshold, lite/force modes
  - **Model** — model selection, imgsz, TensorRT toggle
  - **Preview** — window scale, FPS cap
  - **Tracker** — tuning parameters (distance, velocity, smoothing, etc.)
  - **OSC** — target IP/port
- ✅ Top bar: project combo, version combo, save/load, safe defaults, GPU stats, status badges
- ✅ Status badges: CAM ON/OFF, camera type [IDS]/[CV], OSC ON/OFF, model name, engine type [TRT]/[PT], FPS, GPU util/temp/power/VRAM
- ✅ System state badge (Standby / Run)
- ✅ Compact visualization toolbar (S/K/B/T/I) with keyboard shortcuts
- ✅ Bottom bar: STANDBY/RUN buttons, recording controls, playback controls
- ✅ DPI-aware scaling (auto-detect + `WALLDANCE_UI_SCALE` env override)
- ✅ Toast notifications for transient messages
- ✅ Keyboard shortcuts: Q E T S K B I P R Ctrl+S
- ✅ Model loading progress modal with animated bar
- ✅ TensorRT build prompt dialog

---

## Phase 3 — Run Mode & State Control ✅

**Goal:** Simple 2-state system with clear visual feedback.

| State    | YOLO | OSC | Enhancement | Preview |
|----------|------|-----|-------------|---------|
| Standby  | OFF  | OFF | ON          | ON      |
| Run      | ON   | ON  | ON          | ON      |

- ✅ `SystemState` enum: Standby / Run
- ✅ Skip YOLO inference in Standby (just show enhanced preview)
- ✅ Button styling: active state = highlighted color, inactive = greyed out
- ✅ App starts in Run state by default
- ⬜ OSC status messages for monitoring:
  - `/walldance/status/state` — "standby" or "run"
  - `/walldance/status/fps`, `/status/tracks`

---

## Phase 4 — Model & Inference Pipeline ✅

**Goal:** Flexible model management with GPU-optimized inference.

- ✅ Model manager supporting YOLO 8/11/26 pose variants (n/s/m/l/x)
- ✅ Auto-download models via ultralytics
- ✅ TensorRT FP16 export with size-specific engine naming
- ✅ Runtime TRT ↔ PT switching with graceful fallback
- ✅ GPU pipeline: zero-copy upload → kornia CLAHE/gamma → GPU resize → YOLO
- ✅ GPU-direct path for IDS camera (skip CPU→GPU upload entirely)
- ✅ Progressive enhancement with brightness-based blend factor
- ✅ Temporal denoising (GPU exponential moving average)
- ✅ Greyscale mode (mono camera simulation on GPU)
- ✅ Preview rate limiting (skip GPU→CPU download when not needed)
- ✅ CUDA kernel fallback to CPU on compatibility errors

---

## Phase 5 — Recording & Playback ✅

**Goal:** Record and replay sessions for debugging and rehearsal review.

- ✅ Video recording with 9 slots per project
- ✅ Threaded encoder (queue-based, non-blocking)
- ✅ Multi-codec: MJPG, FFV1 (lossless), mp4v
- ✅ Threaded playback with variable speed (0.25x–4x)
- ✅ Pause, frame-by-frame forward/backward navigation
- ✅ Slot history with timestamped recordings (Ctrl+click for history menu)
- ✅ Bottom bar: LIVE/REC buttons, 10 slot buttons, playback status

---

## Phase 6 — Project System ✅

**Goal:** Per-venue configuration management.

- ✅ Project directories under `projects/`
- ✅ Timestamped JSON config versioning with `_meta` section
- ✅ Per-project safe defaults (click to load, Ctrl+click to save)
- ✅ Last project memory across sessions (`last_project.txt`)
- ✅ Full project switch (stop recording → close camera → load config → reload model → reopen camera → update UI)

---

## Phase 7 — Robustness & Watchdog

**Goal:** Reliable long-run unattended operation.

- 🟡 Stall detection + diagnostics logging (detection only, no auto-recovery)
- 🟡 Runtime diagnostics: budget breakdowns every 5s, spike logging, IDS counters
- ⬜ Auto-reconnect camera on disconnect
- ⬜ Watchdog auto-recovery (restart camera, alert user)
- ⬜ FPS drop / no-detection / GPU temp alerts
- ⬜ Long-run stability test (4+ hours)

---

## Phase 8 — Logging & Diagnostics

**Goal:** Post-show diagnostics and debugging data.

- 🟡 Console diagnostics (budget breakdowns, spike logging, stall heartbeats)
- ⬜ Per-show timestamped log folder
- ⬜ CSV metrics: FPS, latency, brightness, track count, dropped frames
- ⬜ Snapshot profile on "Go Live" for reproducibility
- ⬜ End-of-show summary

---

## Phase 9 — Future Enhancements

**UI / UX:**
- ⬜ IDS crop ratio increment/decrement buttons
- ⬜ Fix preview / window sizing issues
- ⬜ Check standard webcam path (OpenCV source auto-detection)
- ⬜ Check for updates on startup

**Processing:**
- ⬜ Remove static background (background subtraction)
- ⬜ ROI / scene mask interactive editing
- ⬜ Target 1920+ imgsz optimization
- ⬜ Tiling for 4K inference
- ⬜ Rotate video playback support (90°) for testing

**OSC & Integration:**
- ⬜ Standalone OSC record/playback tool
- ⬜ Video recording with OSC data for offline replay
- ⬜ TouchOSC bidirectional control (receive state commands)
- ⬜ OSC status broadcasting (heartbeat, state, FPS, errors)

**Packaging:**
- ⬜ Proper Windows launcher / Nuitka build

---

## Hardware Status ✅

**Purchased (Feb 2025):**
- ✅ Camera: IDS U3-34E0XCP-M-GL Rev.1.2 (4MP Sony IMX664 Starvis 2, Monochrome)
- ✅ Lens: Tamron M118FM08 (8mm, 1/1.8", C-Mount)
- ✅ IR Filter: MidOpt BP850-25.4 (850nm bandpass)
- ✅ Laptop: ASUS ROG Strix SCAR 16 G635LW-RW075W (RTX 5080)
- ✅ USB3 Active Extension Cable (10-20m)
- ✅ 850nm IR Illuminator (30-60W)

**Still Needed:**
- ⬜ IP66 Camera Housing
- ⬜ Mounting hardware (tripod/rigging)