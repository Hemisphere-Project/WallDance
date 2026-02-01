# WallDance Production TODO

A streamlined plan to bring WallDance from research GUI to production-ready show tool.

---

## Phase 1 — IDS Camera Integration

**Goal:** Get the production camera working before UI work.

- ✅ Install IDS Peak SDK on laptop
  - Download from: https://en.ids-imaging.com/ids-peak.html
  - Install Python wheel: `pip install ids-peak ids-peak-ipl`
- ✅ Create `ids_camera.py` wrapper (acquisition, exposure, gain controls)
  - `IDSCamera` class with low-latency buffer strategy (newest-frame-only, 3 buffers)
  - `UnifiedCamera` class for transparent IDS/OpenCV switching
  - Mono10/12 support for maximum IR dynamic range
- ✅ Handle Mono10/12 → Mono8 → BGR conversion for YOLO pipeline
  - IDS IPL for Mono10/12 → Mono8 (preserves dynamic range)
  - GPU-accelerated Mono8 → BGR expansion (`_mono8_to_gpu_bgr`)
  - `read_gpu()` method returns `(1,3,H,W)` GPU tensor directly
- ✅ Integrate with app.py and pipeline.py
  - `process_gpu_direct()` in pipeline for zero-copy IDS path
  - `process_gpu_tensor()` in gpu_pipeline for pre-uploaded tensors
  - Camera refresh lists both OpenCV and IDS cameras
  - Main loop auto-selects GPU direct path when IDS active
- ⬜ Test end-to-end: IDS camera → detection → OSC output
- ✅ Fallback path: keep OpenCV VideoCapture for dev/testing with webcam
  - `UnifiedCamera` auto-detects and falls back gracefully
  - Use "auto" source for automatic IDS→OpenCV fallback

**Latency path comparison:**
| Path | Upload | Convert | Total overhead |
|------|--------|---------|----------------|
| OpenCV | ~2ms | ~1ms (BGR→RGB flip) | ~3ms |
| IDS GPU Direct | ~0.3ms | 0ms (already RGB) | ~0.3ms |

**Deliverable:** working IDS camera pipeline

---

## Phase 2 — GUI Hierarchy Reorganization

**Goal:** Single GUI with clear hierarchy — live controls prominent, tweaky settings secondary.

### Layout Concept

\`\`\`
┌─────────────────────────────────────────────────────────┐
│  TOP BAR: Project | Profile | GPU stats | State badge   │
├─────────────────────────────────────────────────────────┤
│                                                         │
│   ┌─────────────────────────────────────┐               │
│   │         VIDEO PREVIEW               │               │
│   │    (with overlay: FPS, tracks)      │               │
│   └─────────────────────────────────────┘               │
│                                                         │
│   ══════════════ LIVE CONTROLS ══════════════           │
│   [ STANDBY ]  [ ▶ LIVE ]  [ ⏸ PAUSE ]                  │
│   Camera: ✓ Ready   OSC: 192.168.1.50:9000 ✓            │
│                                                         │
│   ─────────── SHOW SETTINGS (per-venue) ───────────     │
│   Person Height: [====|====] 150px                      │
│   Max Dancers:   [6 ▾]                                  │
│   Confidence:    [====|====] 0.25                       │
│                                                         │
│   ─────────── ADVANCED ▼ (collapsed) ───────────        │
│   (Tracker params, CLAHE, Gamma, Model selection...)    │
│                                                         │
└─────────────────────────────────────────────────────────┘
\`\`\`

### Tasks

- ⬜ Reorganize GUI into 3 tiers:
  1. **Live Controls** — always visible, large buttons: Standby/Live/Pause, status indicators
  2. **Show Settings** — visible, per-venue adjustments: person height, max dancers, confidence, OSC target
  3. **Advanced** — collapsible section: tracker tuning, enhancement params, model/imgsz, debug flags
- ⬜ Add system state badge in top bar (Setup / Standby / Live / Paused / Error)
- ⬜ Grey out or disable Advanced section when in Live mode (optional safety)
- ⬜ Move visualization toggles (skeleton, bbox, trails) to a compact toolbar or submenu

**Deliverable:** cleaner single-view GUI with production-first hierarchy

---

## Phase 3 — Live Mode & OSC Gating

**Goal:** Clear operational states with OSC output control.

### System States

| State    | OSC Output | Description |
|----------|------------|-------------|
| Setup    | OFF        | Configuring camera/scene, not ready |
| Standby  | OFF        | Ready, waiting for show start |
| Live     | ON         | Show running, full OSC streaming |
| Paused   | OFF        | Temporarily stopped (break, issue) |
| Error    | OFF        | Camera lost, GPU fail, etc. |

### Tasks

- ⬜ Implement state machine in `app.py`
- ⬜ OSC output gated by state (only send in Live)
- ⬜ Add OSC status messages for TouchOSC monitoring:
  - `/walldance/status/state` — current state string
  - `/walldance/status/fps`, `/status/tracks`, `/status/latency`
- ⬜ "Go Live" button with optional confirmation
- ⬜ Auto-pause on camera disconnect or critical error

**Deliverable:** state-controlled OSC output with TouchOSC status feed

---

## Phase 4 — Show Profiles

**Goal:** Save/load per-venue configurations quickly.

- ⬜ Define "Show Profile" schema:
  - Venue name, date
  - Camera source + resolution
  - Person height calibration
  - Detection thresholds (confidence, max persons)
  - OSC target (IP, port)
  - Model + imgsz selection
- ⬜ Save/Load profile buttons in top bar
- ⬜ "Last used profile" auto-load on startup
- ⬜ Profile includes only show-varying settings (not hardware-specific like GPU path)

**Deliverable:** quick venue switching with saved profiles

---

## Phase 5 — Robustness & Watchdog

**Goal:** Reliable long-run operation.

- ⬜ Auto-reconnect camera on disconnect
- ⬜ Watchdog warnings:
  - FPS drop below threshold (e.g., <10 FPS)
  - No detections for N seconds (configurable)
  - GPU temperature/memory alerts (optional)
- ⬜ Long-run stability test (4+ hours)
- ⬜ Rotate video playback support (90°) for testing

**Deliverable:** stable unattended operation

---

## Phase 6 — Logging & Diagnostics

- ⬜ Per-show timestamped log folder
- ⬜ CSV metrics: FPS, latency, brightness, track count, dropped frames
- ⬜ Snapshot profile on "Go Live" for reproducibility
- ⬜ Simple end-of-show summary (optional)

**Deliverable:** post-show diagnostics and debugging data

---

## Phase 7 — Future Enhancements

- ⬜ ROI / scene mask editing
- ⬜ Target 1920+ imgsz optimization
- ⬜ Tiling for 4K inference
- ⬜ REC with OSC out for offline replay
- ⬜ TouchOSC bidirectional control (receive state commands)

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