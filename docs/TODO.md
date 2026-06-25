# WallDance Production TODO

> Granular build / hardware checklist. For strategy, the forward plan, and the shipped-detection record see **[ROADMAP.md](ROADMAP.md)** (the single roadmap; doc index in [README.md](README.md)).

Last reviewed 2026-06-10; **the forward software plan is superseded by [ROADMAP.md](ROADMAP.md) §3** — treat this file as the hardware / procurement + phase-inventory checklist. Phases 1–6 shipped (inventory below). Since this review the software backlog advanced well past it: calibration + signal fixes, the GPU/CPU **path collapse to GPU-only** (Track P, 2026-06-24 — superseded the old "unification": the CPU path is now *deleted*), and **known-N calibration** (K1 search + K3 dark-probe + phase-④ GUI, 2026-06-25) are all done in ROADMAP §3.2. The live items still owned here: the **on-rig labelled corpus**, the **Phase 7 ops cluster**, and hardware.

> **Operator UX track (U0–U5) shipped** — expert mode, lighting profiles (Show/Rehearsal), the CALIBRATE (scene) + DANCERS (subject) two-pass calibration, and the one-dial sensitivity macro. Full design in **[archives/UX_PLAN.md](archives/UX_PLAN.md)**; numeric calibration rules are provisional pending an annotated-footage re-fit.

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

**Goal:** Reliable long-run unattended operation. *The main open operational cluster — **elevated to step ⑤ of the ROADMAP §4.1 sequence** (2026-06-10 review: a USB3 stall at minute 40 is worse than any ghost). Detail lives here.*

- ✅ Pre-Go-Live **"show readiness" check** (2026-06-11) — `[Readiness]` console block + toast on STANDBY→RUN: camera FPS, TRT active vs fallback, OSC connected-UDP probe, calibration age + active profile, recording disk space, GPU temp. Best-effort, never blocks RUN ([ops_monitor.py](../application/src/ops_monitor.py) + `_run_readiness_check` in app.py; also closes part of Phase 8)
- 🟡 Runtime diagnostics: budget breakdowns every 5s, spike logging, IDS counters
- ✅ Auto-reconnect camera on disconnect (2026-06-11) — the recovery gap is closed: a capture/acquisition error while the camera is still marked open (e.g. the IDS acquisition thread dying after 100 consecutive errors) used to idle the loop forever; it now funnels into `_mark_camera_unavailable` + the existing retry/backoff machinery. *Rig USB-pull validation pending*
- ✅ Watchdog + alert user (2026-06-11) — `LoopWatchdog` daemon reports main-loop hangs (faulthandler stack dump, busy-suppressed during model/TRT loads); prolonged camera-down escalates to a re-ringing `[Alert]` toast after `OPS_CAMERA_DOWN_ALERT_S`. Recovery actions stay in the main loop; the watchdog only observes
- ✅ FPS drop / no-detection / GPU temp alerts (2026-06-11) — `HealthMonitor` 1 Hz tick: rolling-baseline FPS drop, zero-tracked-in-RUN, GPU temp, each with sustain windows + cooldowns; alerts go to console `[Alert]` + toast + `OPS_ALERT` JSONL event. `OPS_*` constants in config.py
- ✅ Long-run stability test (4+ hours) — harness shipped ([tests/soak.py](../application/tests/soak.py): chunked looped playback, progress.jsonl, stall sentinel, RSS/CUDA-slope SUMMARY.md verdict). **4 h run PASS 2026-06-13** from a cool machine (`tmp_analysis/soak_20260612_234512`: 276k frames / 138 chunks / 28 playback loops, 0 stalls, RSS slope +1.4 MB/h, CUDA 0.0, fps mean 19.13 min 18.33 — flat, no sag); adjudicates the 06-12 30-min chunk's fps-trend FAIL as thermal, not a leak/regression

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
- 🟡 Fix preview / window sizing issues — modal centering (client-area + re-center on resize), toast thread race, and preview-upload cost (~4.7×) fixed 2026-06-11; remaining DPI/window-sizing limits are upstream DearPyGui behavior (see [GUI_STACK_AUDIT.md](GUI_STACK_AUDIT.md))
- ⬜ Check standard webcam path (OpenCV source auto-detection)
- ⬜ Check for updates on startup

**Processing:**
- ✅ Manual **scene-mask editor** (2026-06-11, ROADMAP §4.2 Phase 2 ④) — paint-style cell editor on the preview; operator overlays survive Calib1 re-runs
- ~~Target 1920+ imgsz optimization~~ — dropped 2026-06-12: the Phase 2b benchmark measured imgsz oversizing *worsening* quality past the dancer-size knee (and inverting on dark scenes); 1920 stays the top preset, the ROI is the lever for more net px (see [OPTICS.md](OPTICS.md))
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
- ✅ **Operator UX (U0–U5)** — expert mode, lighting profiles, CALIBRATE/DANCERS two-pass calibration, sensitivity macro. See [archives/UX_PLAN.md](archives/UX_PLAN.md).
- ✅ **Static background subtraction** — `BackgroundSubtractor` + `BG_SUBTRACT_ENABLED` (off by default; now expert-only). Was Phase 9 "Remove static background."
- ✅ **Interactive ROI editing** — now via double-click on the preview + corner drag. The ROI half of Phase 9 "ROI / scene mask editing"; the *scene-mask* half is now automatic via [ROADMAP.md](ROADMAP.md) P1.4 (a manual editor remains, listed under Processing above).

---

## Hardware Status

**Purchased (Feb 2025):** ✅ Camera IDS U3-34E0XCP-M-GL (4MP IMX664 Starvis 2 mono) · ✅ Lens Tamron M118FM08 (8mm, C-mount) · ✅ IR filter MidOpt BP850-25.4 (850nm) · ✅ Laptop ASUS ROG Strix SCAR 16 (RTX 5080) · ✅ USB3 active extension (10–20m) · ✅ 850nm IR illuminator (30–60W)

**Still needed:** ⬜ IP66 camera housing · ⬜ Mounting hardware (tripod/rigging)
