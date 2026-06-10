# WallDance Production TODO

> Granular build / hardware checklist. For strategy, status, and the detection/maintainability roadmap, see **[ROADMAP.md](ROADMAP.md)** (the source of truth).

Last reviewed 2026-06-10. Phases 1–6 are shipped (inventory below); active work is **Phases 7–9 + hardware**.

> **Operator UX track (U0–U5) shipped** — expert mode, lighting profiles (Show/Rehearsal), the CALIBRATE (scene) + DANCERS (subject) two-pass calibration, and the one-dial sensitivity macro. Full design in **[UX_PLAN.md](UX_PLAN.md)**; numeric calibration rules are provisional pending an annotated-footage re-fit.

---

## Shipped ✅ (Phases 1–6)

Core application is built and in production use — inventory, not active work (detail in git history):

- **Phase 1 — IDS camera integration:** `ids_camera.py` (Mono10/12→BGR, low-latency newest-frame buffers, GPU-direct `read_gpu`), `UnifiedCamera` IDS↔OpenCV fallback, on-device ROI crop, USB3 stall detection, UserSet load, runtime exposure/gain.
- **Phase 2 — GUI:** collapsible sections (Show/Visualization/Input/Enhancement/Model/Preview/Tracker/OSC), status badges, compact S/K/B/T/I toolbar, DPI-aware scaling, toasts, keyboard shortcuts, model-load modal, TRT build prompt.
- **Phase 3 — Run mode:** `SystemState` Standby/Run gating (YOLO + OSC off in Standby), button theming. *(The open OSC-status sub-item moved to Phase 9 → OSC.)*
- **Phase 4 — Model pipeline:** YOLO 8/11/26 pose, TensorRT FP16 export + runtime TRT↔PT switch, GPU zero-copy enhance→resize→YOLO, progressive enhancement, temporal denoise, greyscale.
- **Phase 5 — Recording & playback:** 9 slots/project, threaded encoder (MJPG/FFV1/mp4v), variable-speed playback, frame-step, slot history.
- **Phase 6 — Project system:** `projects/` dirs, timestamped JSON versioning, per-project safe defaults, last-project memory, full project switch.

---

## Phase 7 — Robustness & Watchdog

**Goal:** Reliable long-run unattended operation. *The main open operational cluster — not covered by [ROADMAP.md](ROADMAP.md); reliability work lives here.*

- 🟡 Stall detection + diagnostics logging (detection only, no auto-recovery)
- 🟡 Runtime diagnostics: budget breakdowns every 5s, spike logging, IDS counters
- 🟡 Auto-reconnect camera on disconnect (reconnect *state* + status UI exist; recovery logic incomplete)
- ⬜ Watchdog auto-recovery (restart camera, alert user)
- ⬜ FPS drop / no-detection / GPU temp alerts
- ⬜ Long-run stability test (4+ hours)

---

## Phase 8 — Logging & Diagnostics

**Goal:** Post-show diagnostics and debugging data. *Builds on the existing JSONL session logging; feeds [ROADMAP.md](ROADMAP.md) P4 regression fixtures.*

- 🟡 Console diagnostics (budget breakdowns, spike logging, stall heartbeats)
- ⬜ Per-show timestamped log folder
- ⬜ CSV metrics: FPS, latency, brightness, track count, dropped frames
- ⬜ Snapshot profile on "Go Live" for reproducibility — *pairs with the P2 Go-Live calibration log (ROADMAP §5)*
- ⬜ End-of-show summary

---

## Phase 9 — Future Enhancements

**UI / UX:**
- ⬜ IDS crop ratio increment/decrement buttons
- ⬜ Fix preview / window sizing issues
- ⬜ Check standard webcam path (OpenCV source auto-detection)
- ⬜ Check for updates on startup

**Processing:**
- ⬜ Manual **scene-mask editor** — complements the *auto* exclusion mask ([ROADMAP.md](ROADMAP.md) P1.4); lets the operator hand-paint/edit dead zones
- ⬜ Target 1920+ imgsz optimization
- ⬜ Tiling for 4K inference
- ⬜ Rotate video playback support (90°) for testing

**OSC & Integration:**
- ⬜ OSC **status broadcasting** — `/walldance/status/{state,fps,tracks,errors}`, heartbeat (nothing exists yet; also closes the Phase 3 monitoring sub-item)
- ⬜ TouchOSC bidirectional control (receive state commands)
- ⬜ Standalone OSC record/playback tool
- ⬜ Video recording with synchronized OSC data for offline replay

**Packaging:**
- ⬜ Proper Windows launcher / Nuitka build

---

## Recently completed (were open here, verified done in code)

- ✅ **Startup project picker** — no silent auto-load; last project highlighted, Enter to launch; rename/delete (`config_store.rename_project`/`delete_project`). Was Phase 9 / ROADMAP §7B.
- ✅ **Operator UX (U0–U5)** — expert mode, lighting profiles, CALIBRATE/DANCERS two-pass calibration, sensitivity macro. See [UX_PLAN.md](UX_PLAN.md).
- ✅ **Static background subtraction** — `BackgroundSubtractor` + `BG_SUBTRACT_ENABLED` (off by default; now expert-only). Was Phase 9 "Remove static background."
- ✅ **Interactive ROI editing** — now via double-click on the preview + corner drag. The ROI half of Phase 9 "ROI / scene mask editing"; the *scene-mask* half is now automatic via [ROADMAP.md](ROADMAP.md) P1.4 (a manual editor remains, listed under Processing above).

---

## Hardware Status

**Purchased (Feb 2025):** ✅ Camera IDS U3-34E0XCP-M-GL (4MP IMX664 Starvis 2 mono) · ✅ Lens Tamron M118FM08 (8mm, C-mount) · ✅ IR filter MidOpt BP850-25.4 (850nm) · ✅ Laptop ASUS ROG Strix SCAR 16 (RTX 5080) · ✅ USB3 active extension (10–20m) · ✅ 850nm IR illuminator (30–60W)

**Still needed:** ⬜ IP66 camera housing · ⬜ Mounting hardware (tripod/rigging)
