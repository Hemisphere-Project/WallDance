# Subsystem map — gui-operator

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

The DearPyGui operator surface and its seam to the runtime. Workflow as implemented:
1. Launch: camera probe, GUI build, then a modal project picker (Launch/Enter, Rename, Delete, New). A "Model Loading" modal follows, and a "Build TensorRT / Use PyTorch" prompt if the engine is missing.
2. The app is already in RUN: `system_state` defaults to RUN in both GUI and runtime, so there is no go-live step.
3. Rail "1 Rig": config version, camera source, mirror/rotate, IDS crop/gain/exposure, ROI (double-click the preview, drag), exclusion-mask paint, rig sheet, phone-monitor QR.
4. Rail "2 Calibrate": record an empty take per lighting, play it, press CALIBRATE (about 20 s), answer the modal "Save to project / Keep this session only". Also "Capture empty wall" and readiness rows, which run automatically on entry.
5. Rail "3 Live" (panel titled "6 - Go Live"): STANDBY/RUN, two 0-100 dials, a 10-control "Dancer IDs" block, remote-control gate, overlay toggles.
Always visible: top-bar status chips, a one-line alerts strip, the recordings bar (LIVE/REC, 9 slots, IMPORT, transport) and a stats bar. A floating "Advanced" drawer holds the numeric knobs and "Expert tools". The UI is English only.

## Architecture

- `application/src/gui.py`: `WallDanceGUI` creates the DPG context and owns about 60 thin `_on_*` callbacks that forward to a `callbacks` dict. It also owns the `sync_checkbox/slider/combo/input` name-to-tag maps, `update_stats`, all dialogs (picker, save, load, issue report, calibration result, QR, TRT prompt, model-loading), the single-slot toast, the alerts strip and `_recompute_layout`.
- `application/src/gui_builder.py`: pure builders (`build_ui`, top bar, phase rail, `build_phase_rig/wall/live`, recordings bar, Advanced drawer sections, `build_expert_tools`), themes and `scaled()`.
- `application/src/ui/adapter.py`: `DpgUiAdapter` holds the two translation tables. `_build_callbacks` maps about 95 GUI callbacks to typed `runtime.api` commands via `RuntimeAPI.submit`; `_build_event_handlers` maps about 45 events to GUI methods. It also owns the global key handler, mouse handler registration and the render pumps (`render_frame` vs `render_frame_raw`).
- `application/src/ui/roi_mask_editor.py`: `RoiMaskEditor` handles ROI drag and mask paint and draws overlays into the preview with cv2. It writes `settings.roi_*` and the processor's exclusion cells directly, not through commands.
- `application/src/ui/wizard_state.py` and `calibrate_all_wizard.py`: a pure state machine plus its DPG renderer for a "Calibrate All" modal. It is still wired into the event path but has no entry button.
- `gui_constants.py` (palette, layout) and `gui_icons.py` (Font Awesome codes).

## Data flow

Operator to runtime: a widget callback on the DPG callback thread calls `gui._on_X`, which calls `callbacks['on_x']`, an adapter lambda that does `api.submit(Command)` into a locked deque. `MainLoop._tick` drains the deque at the top of each tick on the main thread and the app handler mutates settings or the processor.

Runtime to operator: a handler or the loop calls `bus.publish(Event)`, a synchronous fan-out on the publisher's thread. The adapter's handler table then calls `gui.sync_*`, `show_*` or `update_*`, which call `dpg.set_value` / `configure_item`.

Per frame: a `StatsTick` event drives `gui.update_stats` (about 45 DPG calls plus NVML queries). A `PreviewFrame` event drives `gui.update_frame` (resize, BGR to RGBA float32 into a persistent buffer, raw-texture upload). `dpg.render_dearpygui_frame()` runs at the tick tail, so UI render rate equals pipeline rate (vsync off).

ROI and mask bypass the queue: the main loop polls the mouse and calls `RoiMaskEditor`, which mutates `settings.roi_*` and the exclusion mask directly.

GUI-local state that never reaches the runtime: active phase, expert mode, picker selection, wizard step, and the GUI's own copies of `_system_state` and the overlay flags.

## Threading and state

Three kinds of thread touch DPG.
1. Main thread: the whole tick (command drain, acquire, YOLO/track/OSC, events, render). Any slow command handler stalls capture, OSC and UI together.
2. DPG callback thread: every widget callback, the global key handler and the registered mouse handlers. These mostly `submit()`, but also mutate DPG directly (phase switch, picker inline prompts, dialogs, STANDBY/RUN visuals). The mouse move/release handlers mutate `RoiMaskEditor` and processor state.
3. Worker threads (`OpsReadiness`, `DryRunReplay`, the sweep and known-N workers, `VideoImport`, `ImportFileDialog`): `EventBus.publish` is synchronous, so their Toast/result events run GUI code on the worker thread.
There are no locks and no `dpg.mutex()` anywhere.

The runtime is authoritative for settings; widgets are views refreshed by `ControlSync(kind, name, value)` through hand-maintained name-to-tag dicts. Second copies that can diverge:
- `gui._system_state`: the `StateChanged` event is a no-op in the adapter.
- `gui.config[show_*]` overlay flags.
- `box_clamp_checkbox`: never synced or saved.
- `gui.config`: a build-time snapshot.
- `_current_project` and `_current_config_timestamp`.
Modals are deleted and recreated per show; the picker refreshes in place to avoid DPG's stacked-modal problem.

## Failure handling

Contained well:
- `EventBus.publish` and `RuntimeAPI.drain` catch and print subscriber and handler exceptions.
- Nearly every DPG access is guarded by `does_item_exist`.
- Unknown events warn once.
- The import dialog falls back from tkinter to the DPG file dialog.
- A previous crash is surfaced as a 15 s toast.
- TRT fallback has a red banner over the preview with a Rebuild button; CPU fallback has a badge whose tooltip gives reason and action.
- `light_changed` is a persistent strip entry with a remedy and is cleared when the condition ends.

Weak:
- Every other health alert is one 8 s toast in a single-slot window, re-rung at most every 120 s.
- Camera stalls and Go-Live readiness results go to the console.
- The OSC and CAM chips show configuration, not health.
- Modal waits (picker, TRT prompt, calibration result) are unbounded.
- The window close button ends the session with no confirmation and no unsaved-changes indicator.
- The local GUI has no RUN-time gating equivalent to the remote API's policy table.
- Toast expiry only runs on wait-path renders.

## Tunables

Layout: `application/src/gui_constants.py` holds 13 palette colours and 7 layout numbers: `CONTROL_PANEL_WIDTH` 370, viewport 1340x900 with min 900, `TOAST_POS` (15, 38) unscaled, `LAYOUT_H_PAD` 28, `LAYOUT_V_MARGIN` 120, `LAYOUT_V_FALLBACK` 240 (unscaled px).

Environment variables: `WALLDANCE_UI_SCALE`, `WD_EXPERT`, `WALLDANCE_AUTOLAUNCH_LAST`.

Hard-coded in `gui.py`:
- DPI tiers: logical width >= 3200 gives 2.0, >= 2200 gives 1.25.
- FPS colour ramp 10 to 19 fps.
- Stage-time colours: YOLO 40/80 ms, enhance 10/30 ms, preview 5/15 ms.
- GPU temperature 70/85 C, VRAM 50/80 %.
- Save indicator 2 s.

Hard-coded in `gui_builder.py` (widget ranges and steps):
- sensitivity and gap bridging 0-100, step 5
- stability 0-3
- hold 0-10 s
- max dancers 1-8
- person height 20-800 px
- gain 0-30 dB
- exposure 100 us to max
- CLAHE 1-6
- gamma 0.5-2.5
- confidence 0.1-0.9

Hard-coded in `ui/roi_mask_editor.py`: ROI grab band max(12 px, 2.5 %), minimum new ROI 5 %, mask overlay alpha 0.4 / 0.18.

Roughly 50 magic numbers plus about 100 inline RGB tuples outside the palette. Toast durations (2-15 s) and colours are scattered over about 60 call sites in the runtime. Alert routing is one tuple, `STRIP_ALERTS = ("light_changed",)`, in `ui/adapter.py`.

## Interfaces with neighbours

- Runtime to GUI threading: `EventBus.publish` is synchronous on the caller thread, and `app.py` / `recording_controller.py` workers publish Toast, ImportProgress and result events directly. The GUI assumes the main thread. Marshal in the adapter or the bus.
- `HealthMonitor` (`core/ops_monitor.py`) emits eight or more alert kinds but a cleared signal for only `light_changed`. The GUI strip needs cleared events for all kinds to become persistent.
- Remote API (`services/remote_api.py`):
  - `SetState` changes runtime state the GUI never mirrors.
  - `StartCalibration` raises a `no_close` Save/Keep modal on the operator's screen that no remote command can dismiss.
  - `CheckReadiness` is 'safe' but blocks the frame loop for about 0.25 s.
  - The remote policy table has no local equivalent.
- `RoiMaskEditor` lives in `ui/` but writes `settings.roi_x/y/w/h` (four separate assignments) and the processor's exclusion cells directly, partly from the DPG callback thread (move and release handlers), while the pipeline reads them on the main thread. It bypasses the command queue the rest of the UI uses.
- `StartCalibration` and `StartDancersRun` are toggle commands (start or cancel). The GUI's CANCEL label depends on `CalibProgress` arriving, so a late click or a remote call can invert the intent.
- The 0.25 s readiness block (and any slow command handler) delays the next camera read. Whether the camera-stall detector (`processor.note_camera_frame`) then counts a GUI-induced gap as a camera stall was not checked.
- `main_loop.py:811-812` assumes Calibrate runs in STANDBY; the GUI boots in RUN and never suggests STANDBY.
- Messages from calibration, ops_monitor and config_manager reference GUI locations that no longer exist ('re-Aim', 'Advanced > Person height', 'Model panel', 'see console').
- `StatsTick` is published every frame to all subscribers, including the remote API's event intake.
- `_get_gui_config` omits `camera_type` and `box_clamp_enabled`, so those widgets start from defaults until the first stats tick.

## Strengths

- Clean command/event seam: every widget callback becomes a typed command through a locked queue drained at one point of the tick, and every runtime push is an event. `ui/adapter.py` holds two flat 1:1 tables, exceptions on both sides are contained, and unknown events warn once.
- `ui/wizard_state.py` is a renderer-free state machine: actions return commands, events are fed in, no timers, and it has a headless test. This is the right pattern for any guided flow.
- Fallback states are explicit and actionable: a red TRT banner over the preview with a Rebuild button, a CPU-fallback badge whose tooltip gives reason and action, and readiness rows with concrete remedies (engine file name; 'narrow the ROI ... long side X -> <= Y px'; an honest UDP-probe caveat).
- The two live dials are well designed: 0-100 with 50 meaning calibrated, Dial B auto-hidden when calibration finds it inert, and raw expert knobs re-anchor the dial with a visible toast ("never silent").
- The ROI/mask editor has field-hardened ergonomics: generous grab bands, a stray click cannot replace the ROI (5 % minimum new box), drawn and effective ROI are kept separate, leaving the Rig phase exits edit modes, and masked cells stay dimmed on the preview.
- DPG pitfalls are documented where they were hit: toast expiry by deadline on the render path instead of timer threads, picker rename/delete rendered inline to avoid stacked modals, set_value-only updates for worker-posted results, and a modal recentring registry.
- Tooltips on the Live panel are written as symptom then action ("Video drops in TD when a dancer vanishes? Raise it."), and the picker's delete states its consequence and needs an inline confirm.
- Per-frame preview upload reuses one float32 buffer with no temporaries, idle renders are capped at 60 Hz, and GUI stats cost is measured into the timing dict.
- There are headless DPG smoke tests per panel plus a whole-window build test that asserts retired widgets are gone and moved ones still exist.

## Questions the reader could not answer from code

- Is booting straight into RUN (YOLO and OSC live from project load, through rigging and calibration) deliberate for unattended recovery? If so, should the rail say so and should Calibrate ask for STANDBY, given the main-loop comment that 'the Calibrate phase is used in Standby'?
- After a crash or exit code 3, does the launcher restart the app automatically, and is anyone expected to be at the laptop to answer the project picker? Would a countdown auto-launch of the last project be acceptable?
- How often does lighting change during a performance, and who recalibrates? Is processing a looped empty take while in RUN (camera acquisition stopped, OSC from the recording) really the intended mid-show remedy?
- Who is meant to open Advanced and 'Expert tools' on site: the technician, or only the developer (locally or through the remote API)? This decides whether Model/TensorRT, OSC target and the tune jobs should sit behind expert mode.
- What language do the on-site technicians work in? The UI and all alerts are English only, with pipeline jargon (MOG2, varThreshold, CLAHE, letterbox px).
- Is the Calibrate All wizard meant to come back, or should it be deleted now that the dancers pass has left the operator flow?
- Should 'Box clamp' persist with the project? Today it is session-only, not synced, and resets to ON on restart.
- Is there an operator need for a 'reset tracker / IDs' button? The callback and command exist but no widget is bound; only the remote API can issue it.
- What are the show laptop's display resolution and Windows scaling? The viewport is a fixed 1340x900 times scale with no clamp to the work area and no maximise, so at some scale settings the bottom bars (alerts, recordings) would open off-screen.
