# app.py Decomposition Plan

**Date:** 2026-06-11
**Companion:** [GUI_STACK_AUDIT.md](GUI_STACK_AUDIT.md) (the audit that motivated this), [ROADMAP.md](ROADMAP.md) ("`app.py` decomposition" maintainability item).
**Decisions locked in (operator, 2026-06-11):** stay in Python; keep DearPyGui for now (targeted fixes shipped — toast thread race, centered-modal helper, preview texture path, `gui_constants.py`); GUI toolkit migration deferred; a **remote tablet client for the calibration phases + quality feedback** is wanted later, while main operation stays on the desktop.

**Status (2026-06-11):**
- Phase 0 ✅ `ee38cbd` — tag `decomp-phase0`; replay archive for all 12 scenarios in `tests/golden/decomp-phase0/` (determinism re-run byte-identical → gates compare bytes, not tolerances); 6.5-min playback timing baseline (FPS p50 19.7 source-capped, process_wall p50 31.7 ms / p95 38.4 ms).
- Phase 1 ✅ `02837a5` — 19→`core/`, 2→`camera/`, 1→`services/`; sys.modules-aliasing shims at old paths (delete once in-flight branches land); launcher update cycle verified on a scratch clone.
- Phase 2 (1) ✅ `e21218c` — `RecordingController` → `runtime/recording_controller.py` (19 methods, narrow-port Protocols). Operator manual smoke (§6.3) still owed for the Phase 2 commits.
- Phase 2 (2) ✅ `1df82c3` — `ModelController` → `runtime/model_controller.py` (9 methods + the two run() drain blocks; controller owns model/TRT state; dpg pumping behind `ModelUiPort.render_frame`).
- Phase 2 (3) ✅ `6f57e66` — `CameraController` → `runtime/camera_controller.py` (21 methods: retry/backoff, IDS↔OpenCV swap, refresh, ids_* parameter cache; camera objects stay app-owned/injected). Found: the IDS gain/exposure auto-toggle callbacks are dead (no GUI wiring).
- Phase 2 (4) ✅ `4c63baf` — `ConfigManager` → `runtime/config_manager.py` (21 methods: project switch orchestration, save/load, profiles, picker, safe defaults; owns ConfigStore/current-project/profiles/pending-switch). `_get_saveable_config`/`_apply_config_without_model` stay app-side as injected callables — they dissolve into the Phase 3 seam.
- Phase 2 (5) ✅ `04c7ee7` — `CalibrationFlows` → `runtime/calibration_flows.py` (10 methods: Calib1 servo/window/apply + Calib2 evidence pool; owns the calibrating flags + blur_budget_ms; math in core/ untouched).
- Phase 2 (6) ✅ `1a33a40` — `RoiMaskEditor` → `ui/roi_mask_editor.py` (34 methods, dpg allowed there) + `runtime/roi_state.py` (source size + effective rect for headless consumers). **Phase 2 complete**: app.py 4856 → 2656 lines; six controllers behind narrow ports.
- Operator quick smoke ✅ 2026-06-11 over the Phase 2 commits. Verified en route: the exclusion mask gates YOLO detections *and* cold-motion blobs but is anti-spawn by design — detections near a confirmed track survive (`pipeline._apply_exclusion` near-track guard), so a live dancer is never amputated by painting their zone.
- Phase 3 ✅ — the command/event seam. `runtime/api.py`: 60 typed commands (validated dataclasses, thread-safe queue, drained at **one** main-loop point) + 36 events (JSON-serializable except `PreviewFrame`) + `EventBus` fan-out; `SystemState` moved here (runtime-authoritative mirror now gates processing/readiness; gui_builder re-exports). `ui/adapter.py`: the only events→dpg / dpg-callbacks→commands translation point; absorbs app.py's keyboard handler, viewport setup, render pumps (`render_frame` wraps toast-expiry, `render_frame_raw` is the bare loop-tail/model-pump render — split preserved), `is_running`/teardown. app.py is the composition root with **zero dpg references**; the six Phase-2 UI ports publish events. Kept *off* the bus by design (documented in api.py): the blocking TRT prompt + render pump (model controller spins the main thread until the operator answers — a queued command could never drain), and the per-tick `consume_layout_change` query. Dialog responses are commands (report-card Save → `SaveConfig`, slot-history pick → `PlaySlotRecording`, calib2 Apply/Clear → `ApplyCalib2`/`ClearCalib2Pool`) so the calibration vocabulary is tablet-transportable. Found dead: gui's `on_max_persons_change` (app never provided it) — left unwired, guarded by the static coverage test. Gates: suite 286/7 (58 new seam tests incl. callback-coverage both directions, command-registration completeness, §4 import-purity lints), 12-scenario replay **byte-identical** to `decomp-phase0`, replay JSONL event log identical mod the `SESSION_START` timestamp, 75 s boot smoke (FPS ~30 @4x, Alert→toast path live, no EventBus/handler failures).
- Operator §6.3 full manual pass ✅ 2026-06-12 over Phase 3 (the riskiest phase for UI state-sync passed clean; one-tick command latency was the expected behavior delta).
- Phase 4 ✅ — main-loop isolation. `runtime/main_loop.py`: `MainLoop` drives the whole session (`_startup` → tick loop → `_shutdown`); the `run()` body moved **verbatim** into named tick stages (pumps → ui-input → acquire → process → preview → events → record → render; the old in-loop `continue` paths became early stage returns; statement order unchanged — proven by a normalized statement-stream diff vs HEAD where only stage plumbing and two always-true `'in locals()'` guards differ). Loop-only helpers moved along with their cadence/diag state (ops heartbeat + alert emit, GPU-stats cadence, spike/stall loggers, height-ruler + frame-number overlays); shared state stays on the app behind `LoopHost` (the explicit Protocol inventory of the loop's app surface) and `UiClientPort` (what any UI client must provide — DPG today, tablet later). app.py 2691 → 1801 lines (composition root: wiring, command handlers, config appliers). Gates: suite 286/7; 12-scenario replay **byte-identical** to `decomp-phase0`; 390 s pinned timing run (imgsz 960, speed 1.0) — FPS p50 19.7 = baseline, per-frame keys within noise of a **same-day HEAD A/B control** (process_wall p50 34.0 vs 33.6, p95 40.8 vs 44.5; the yolo/enhance elevation vs the 6/11 baseline reproduces on HEAD ⇒ thermal, GPU 70 °C / SM 2670 of 3090 MHz post-run); plus a speed-4 stress run, all markers green. 30-min soak chunk (19.5k frames): **leak signals clean** (RSS slope +28.4 MB/h, CUDA 0.0, 0 stalls) but verdict FAIL on the fps-trend criterion (first→last quartile sag) — attributed to the same sustained-load thermal sag and **diff-independent by construction** (soak.py/replay.py never import app/main_loop). Owed: a cool-machine soak re-run (fold into the 4 h ops soak, ROADMAP §4.1 step 5). Note: the soak harness exercises the pipeline, not the loop — a MainLoop-driving soak variant is a possible follow-up.
- Desktop **"Calibrate All" wizard** ✅ 2026-06-12 — the queued post-Phase-3 follow-up, built as a **second, GUI-local client of the calibration command/event vocabulary** (engines untouched; only the UX merges, per the locked decision). `ui/wizard_state.py`: renderer-free state machine (intro → scene run → report card → dancers run(s) → pool review → apply → save; operator actions return seam commands, seam events drive transitions; run-end detection rides `CalibProgress(None)` + the same-drain success event — no timers; "Add another run" loops the accumulative pool; a rejected proposal holds the review step). `ui/calibrate_all_wizard.py`: dpg modal renderer, opened by the new **ALL** button beside CALIBRATE/DANCERS. The adapter routes `CalibProgress`/`CalibReportCard`/`Calib2PoolChanged` through the wizard first and falls back to the classic dialogs when it is closed — both entry points coexist; shortcuts are suppressed while the wizard is open (picker pattern). The state machine is the **Phase 5 tablet wizard's core verbatim** (a websocket renderer would serialize the same commands and feed the same events). Gates: suite 301/7 (15 state-machine tests + a static wizard-command registration check; the callback-coverage tests pin the ALL wiring both directions), 12-scenario replay byte-identical (UI-only change), 75 s boot smoke green. Known overlap: a pool apply that changes imgsz queues a TRT reload whose modal stacks over the wizard's APPLIED step — same behavior as the classic dialog flow; verify in the operator pass.
- Operator §6.3 quick smoke ✅ 2026-06-12 over Phase 4 (playback session on `1_TANGO_HANGAR-texturedbg`, pass clean). **Phases 0–4 fully gated** — automated + operator. Remaining from Phase 4: the cool-machine soak re-run only.
- Next: a manual pass of the wizard flow (incl. live-rig servo phase + the TRT-reload-modal-over-APPLIED overlap check); cool-machine 4 h soak (ROADMAP §4.1 step 5); Phase 5 (tablet client) when wanted.
- Post-Phase 3 follow-up (operator, 2026-06-11): desktop **"Calibrate All"** wizard chaining Calib1 → report card → Calib2 → pool review over the command seam — desktop precursor of the Phase 5 tablet wizard. Keep the two calibration engines separate (different stage directions, cadence, trust models); merge only the UX.

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
2. Replay sweep ([tests/replay_sweep.py](../application/tests/replay_sweep.py), byte-compare vs `tests/golden/decomp-phase0/`) — **tiered by what the diff can reach** (operator-agreed 2026-06-12): `ui/`/`gui*`/docs-only diffs **skip it** (replay imports none of that code — the suite's import lints + callback-coverage tests are the real gate); `runtime/`/`app.py` diffs run `--golden` (the 3-scenario trio, ~2 min import/wiring smoke); `core/` or config-default diffs and **phase boundaries** run the full 12 (~7 min) — the corpus exists exactly for those.
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
