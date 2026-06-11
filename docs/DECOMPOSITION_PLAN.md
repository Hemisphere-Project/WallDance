# app.py Decomposition Plan

**Date:** 2026-06-11
**Companion:** [GUI_STACK_AUDIT.md](GUI_STACK_AUDIT.md) (the audit that motivated this), [ROADMAP.md](ROADMAP.md) ("`app.py` decomposition" maintainability item).
**Decisions locked in (operator, 2026-06-11):** stay in Python; keep DearPyGui for now (targeted fixes shipped — toast thread race, centered-modal helper, preview texture path, `gui_constants.py`); GUI toolkit migration deferred; a **remote tablet client for the calibration phases + quality feedback** is wanted later, while main operation stays on the desktop.

---

## 1. Goal

Split the `WallDanceApp` god-object (~4.8k lines, ~170 methods, one class) into a **headless core + runtime controllers + thin UI adapter**, with a typed **command/event seam** between runtime and UI, such that:

1. Every extraction is behavior-neutral, landable independently, and gated by the existing safety net (222 headless tests + opt-in replay goldens).
2. The DPG GUI becomes *a client* of the runtime rather than its co-owner — which makes a future toolkit swap a client-only change and a GUI crash recoverable without killing the show pipeline.
3. The seam is designed from day one for a **second client: the tablet calibration UI** (commands restricted to calibration + quality feedback; telemetry shared with the existing phone monitor).

Non-goals this round: changing detection behavior, swapping the GUI toolkit, the GUI tag-string registry cleanup (separate later item), process isolation (enabled, not implemented).

## 2. Current state (anchors)

- `app.py` — `WallDanceApp` at `app.py:169`, `run()` main loop at `app.py:4109`, `main()` at `app.py:4836`. Section landmarks (line numbers drift with ongoing roadmap work; use as orientation): init ~171–358, GUI-config wiring ~371–527, camera retry/swap ~528–691, ROI/mask editor ~692–1159 (~30 methods), config persistence ~1160–1625, enhancement callbacks ~1631–1712, sensitivity ~1739–1776, model/TRT ~1981–2931, calibration flows ~2035–2357, playback/recording ~2389–3406, ops ~3498–3723, main loop ~4071–4800.
- **Seams that already exist and must be exploited, not rebuilt:**
  - `pipeline.FrameProcessor.process()` — already headless (the replay harness proves it); settings injected via `ProcessingSettings`.
  - `gui.WallDanceGUI(config, callbacks)` — the GUI already receives a *callbacks dict*; the command seam formalizes this dict instead of inventing new wiring.
  - Camera frame-callback interface (`camera_manager.set_frame_callback`).
  - `web_monitor.py` — an existing out-of-process telemetry consumer (MJPEG + focus/lighting); the tablet client extends this pattern.
  - `config_schema.py` typed validation; `tracking_logger` JSONL events.
- Safety net: 19 headless suites (~3.3k lines, 222 passing), `tests/replay.py` + golden scenarios re-founded on the annotated corpus (`WD_RUN_REPLAY=1`), CI on push/PR.

## 3. Target layout

```
application/src/
├── core/                  # headless, no dpg import anywhere in the tree
│   ├── pipeline.py        # FrameProcessor (moved)
│   ├── tracker.py
│   ├── motion_model.py / motion_detector.py
│   ├── calibration.py / calib2.py / sensitivity_macro.py
│   ├── gpu_pipeline.py / enhancer.py / background.py
│   ├── osc_output.py / visualization.py
│   └── config.py / config_schema.py / config_store.py / tracking_logger.py
├── runtime/
│   ├── session.py         # WallDanceApp's surviving skeleton: wiring + lifecycle
│   ├── main_loop.py       # run() extracted: tick functions, explicit ordering
│   ├── camera_controller.py
│   ├── model_controller.py    # model load/switch + TRT build orchestration
│   ├── recording_controller.py# slots, encoder/decoder, playback state machine
│   ├── calibration_flows.py   # CALIBRATE / DANCERS orchestration state machines
│   ├── config_manager.py      # project/profile/version persistence flows
│   ├── ops.py                 # ops_monitor wiring, readiness, watchdog
│   └── api.py                 # RuntimeAPI commands + EventBus (the seam, §4)
├── ui/                    # everything that imports dpg
│   ├── gui.py / gui_builder.py / gui_constants.py / gui_icons.py
│   ├── roi_mask_editor.py     # the ~30 ROI/mask methods from app.py
│   └── adapter.py             # event-stream → dpg sync calls; commands ← callbacks
├── services/
│   └── web_monitor.py     # phone monitor today; tablet calibration client later
├── camera/
│   ├── ids_camera.py / camera_manager.py  # UnifiedCamera + OpenCV fallback
└── main.py                # unchanged thin entrypoint
```

## 4. The command/event seam (`runtime/api.py`)

Designed now because the tablet client depends on it; consumed in-process by DPG first.

**Commands** (dataclasses, validated, queued to the main loop — never executed on a caller thread):
`SetSensitivity`, `SetPersonHeight`, `ToggleEnhance/Greyscale/OSC/Preview`, `SetRoi`, `EditMask`, `SwitchProfile(show|rehearsal)`, `SetState(standby|run)`, `StartCalibration`, `StartDancersRun`, `ApplyCalib2(selection)`, `ClearCalib2Pool`, `LoadModel/ToggleTRT`, `SelectSource/Slot`, `PlaybackControl`, `SaveConfig/LoadConfig/SwitchProject`, `Quit`.

**Events** (fan-out to all subscribers; the DPG adapter is subscriber #1):
`StateChanged`, `StatsTick` (fps/latency/gpu/tracks), `CameraStatus`, `EngineStatus` (TRT/PT, fallback banner), `CalibProgress`, `CalibReportCard`, `Calib2PoolChanged`, `ConfigLoaded/Saved`, `Alert` (ops), `Toast`, `PreviewFrame` (handle/ndarray — DPG adapter consumes in-process; web clients get the MJPEG path instead).

**Rules:**
- `core/` never imports `runtime/` or `ui/`. `runtime/` never imports `ui/`. `ui/adapter.py` is the only place that translates events→dpg and dpg-callbacks→commands.
- Commands are executed at a single point in the main loop tick (same thread-safety model as today, made explicit).
- Every event is JSON-serializable except `PreviewFrame` — that keeps the tablet/web client a pure transport problem (websocket) later, no redesign.
- Calibration quality feedback (report card, evidence-pool labels, readiness results) are events, not GUI strings — `calibration.py` already produces structured report data; keep it structured through the seam and render in the client.

## 5. Phases

Each phase lands as one or more small commits, each gated (§6). Order minimizes conflict with the parallel roadmap work, which concentrates in `app.py`/`calibration`/`tracker`: the pure moves come first, `app.py` surgery is batched and scheduled in quiet windows between roadmap items.

### Phase 0 — Baseline freeze (~0.5 day)
- Tag the current state; run the full replay golden set and archive the outputs as the comparison baseline; record a timing baseline (5-min live-loop budget log) to detect orchestration regressions.

### Phase 1 — Mechanical package moves (~2 days)
- Create `core/`, `camera/`, `services/` packages; move the headless modules listed in §3. **Leave import-shim stubs at the old paths** (`pipeline.py` → `from core.pipeline import *`) so in-flight roadmap branches and the launcher keep working; delete shims in a follow-up once branches land.
- No logic edits. Gate: tests + replay byte-identical, launcher update cycle still works.

### Phase 2 — Controller extraction from `WallDanceApp` (~4–5 days)
Peel cohesive method clusters into controller classes (constructor-injected with exactly what they need — **never the app instance**; where a cluster needs app services, define a narrow `Protocol`). Order = lowest coupling first:

1. `RecordingController` (~21 methods, self-contained state machine + threads)
2. `ModelController` (model load/switch, TRT build prompts/progress)
3. `CameraController` (retry/backoff, IDS↔OpenCV swap, exposure servo entry points)
4. `ConfigManager` (save/load/version/profile flows; pairs with `config_store`)
5. `CalibrationFlows` (CALIBRATE/DANCERS orchestration; keep `core/calibration.py` math untouched)
6. `RoiMaskEditor` → `ui/roi_mask_editor.py` (mouse/drag/paint state + preview compose; the small runtime-side state stays in a tiny `RoiState`)

Each extraction: move methods verbatim → rename `self.` references to injected deps → one commit. Gate after each: tests + replay + manual smoke (§6).

### Phase 3 — The seam (~3 days)
- Add `runtime/api.py` (commands/events as in §4).
- Convert the existing `callbacks` dict passed to `WallDanceGUI` into command emissions; convert `gui.sync_*` / `update_*` push calls into events consumed by `ui/adapter.py`.
- The ~80 direct `dpg.*` calls in `app.py` move behind the adapter.
- Gate: tests + replay + full manual pass (this is the riskiest phase for UI state-sync bugs); diff the JSONL event log of a replay run against baseline.

### Phase 4 — Main-loop isolation (~2 days)
- Extract `run()` into `runtime/main_loop.py` as explicit tick stages (acquire → process → record → events → render), `WallDanceApp` shrinks to wiring + lifecycle (~target ≤800 lines).
- Gate: timing baseline within noise of Phase 0; soak harness smoke (chunked, with progress output per the ops conventions).

### Phase 5 — Tablet calibration client (separate effort, ~1–2 weeks, when wanted)
- Extend `services/` with a small FastAPI/websocket service exposing **only** the calibration + quality-feedback command subset and the event stream; reuse the MJPEG preview from `web_monitor`. UI: calibration wizard (CALIBRATE → report card → DANCERS → pool review → Apply) + readiness/quality dashboard. Desktop stays the operating console.
- This phase is the payoff of Phase 3 and needs no further `app.py` surgery.

## 6. Verification protocol (every gated step)

1. `pytest tests -q` (222 passing today; no new skips).
2. `WD_RUN_REPLAY=1` golden scenarios — metrics identical to the Phase 0 archive.
3. Manual smoke checklist (~10 min): project picker → load project; STANDBY→RUN readiness toast; CALIBRATE on a recording + report card dialog; DANCERS → pool dialog → Apply; playback slot + speed + frame-step; ROI drag + mask paint; model switch + TRT prompt; save/load config; profile switch; toast expiry; window resize (modals re-center); quit.
4. Launcher update cycle on a scratch clone (dirty-file check, force-sync, reinstall trigger).
5. Phase 4 only: 30-min soak chunk, RSS/CUDA slope verdict from the existing harness.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Parallel roadmap commits to `app.py` collide with Phase 2/3 surgery | Phases 1 is conflict-free (new files + shims); batch each Phase 2 extraction into a quiet window; rebase extraction commits, never long-lived branches |
| Hidden ordering dependencies in `run()` (camera retry vs render vs watchdog) | Phase 4 moves code verbatim into named ticks before any reordering; timing baseline comparison |
| Callback conversions silently drop a GUI sync path | Phase 3 gate includes the full manual smoke + replay JSONL event diff |
| Controllers re-coupled by passing the app instance | Code-review rule: constructors take narrow Protocols/values only; `core/` import-linted against `dpg`/`runtime` |
| Import shims linger forever | Tracked follow-up with a removal deadline once roadmap branches land |

## 8. Effort & sequencing

~12–14 focused days for Phases 0–4 (3–5 calendar weeks alongside show work). Suggested sequencing per ROADMAP: after the current §4.2 Phase 2 detection cluster stabilizes — Phase 1 can start immediately though, since it does not touch `app.py` logic. Phase 5 is scheduled independently, when the tablet workflow is wanted.

## 9. What this unlocks

- Tablet calibration + quality feedback client (Phase 5) with no further core surgery.
- GUI toolkit migration (PySide6 or other) reduced to rewriting `ui/` against a stable command/event API.
- Optional GUI process isolation (show-reliability: UI crash ≠ pipeline crash).
- Per-controller unit tests for the ~50 currently untestable GUI-adjacent flows.
- `app.py` ≈ 4.8k → ≤800-line session skeleton.
