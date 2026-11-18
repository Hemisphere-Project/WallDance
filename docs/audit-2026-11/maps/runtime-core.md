# Subsystem map — runtime-core

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

Composition root plus session driver. `WallDanceApp` (application/src/app.py, 2722 lines, 90 methods of which 46 are `_cb_*`) builds every object (camera pair, enhancer, tracker, FrameProcessor, recorder, five controllers, HealthMonitor, LoopWatchdog), registers about 95 typed commands, and still owns config save/apply (75 saved keys), OSC lifecycle, the empty-wall plate, readiness checks, issue reports and three offline-tool launchers. `MainLoop` (application/src/runtime/main_loop.py) runs startup, the tick loop and shutdown on the main thread; it is the only caller of the pipeline and of the DPG render. application/src/runtime/api.py defines the command/event vocabulary, `RuntimeAPI` (queue drained once per tick) and `EventBus` (synchronous fan-out). application/src/ui/adapter.py translates DPG callbacks into commands and events into `WallDanceGUI` calls. application/src/runtime/roi_state.py holds the drawn versus effective ROI. application/src/main.py only calls `app.main()`.

## Architecture

`WallDanceApp` owns `settings` (ProcessingSettings), the `system_state` mirror, OSC target/enable, preview geometry, overlay flags, sensitivity-macro seeds, rig sheet, input transform, frame stashes and fps/timing. Eight private adapter classes (`_ModelUiAdapter`, `_ConfigUiAdapter`, `_CameraUiAdapter`, `_CalibrationUiAdapter`, `_RecordingUiAdapter`, `_ModelCameraAdapter`, `_RecordingCameraAdapter`, `_RecordingSessionAdapter`) each wrap the whole app and turn controller port calls into bus events.

The controllers (ModelController, CameraController, ConfigManager, RecordingController, CalibrationFlows) own pending flags and state machines, and are handed each other plus app callables.

`MainLoop(app: LoopHost)._tick` is: `_ops_heartbeat`, `api.drain`, `_tick_remote`, `_tick_pumps`, `_tick_ui_input`, `_tick_acquire`, `_tick_process`, `_tick_preview`, `_tick_events`, `_tick_record`, `_tick_render`. A `_Tick` dataclass carries per-iteration locals.

`LoopHost` and `UiClientPort` are `typing.Protocol`s that are never checked: 45 attributes typed `Any`, and the loop reads 40 distinct private members of the controllers.

`DpgUiAdapter` owns `WallDanceGUI` and `CalibrateAllWizard`. Three calls stay synchronous: `show_tensorrt_prompt`, `render_frame`/`render_frame_raw`, `consume_layout_change`. `RoiMaskEditor` (ui/) is held by the app and driven directly by the loop. `RemoteApi` (services/) is the second client of the same vocabulary.

## Data flow

**Frames.** The camera thread (IDS acquisition or OpenCV capture) fills a newest-frame slot. `_tick_acquire` reads it (`unified_camera.read_gpu()`/`read()`, or `recorder.read_frame()` in playback), clamps the ROI to the source and stashes `_last_raw_frame`. `_tick_process` calls `FrameProcessor.process_gpu_direct`/`process` (enhance, YOLO, motion, tracker, identity slots); OSC is sent inside it (`_emit`, application/src/core/pipeline.py:1057-1081). It returns tracks, display frame and timing. `_tick_preview` composes the ROI canvas, pushes the clean frame to WebMonitor, draws overlays and publishes `PreviewFrame` (adapter, then `gui.update_frame` texture upload). `_tick_events` publishes `StatsTick`, `OutputLatency`, `BgStatus`. `_tick_record` refreshes the recording UI (frames are written on the camera callback thread). `_tick_render` calls `dpg.render_dearpygui_frame()`.

**Commands.** The DPG callback thread, the wizard and RemoteApi HTTP threads call `RuntimeAPI.submit` (lock + deque). `drain()` at tick start runs the handler lambdas from `_register_command_handlers`, which mutate controllers/settings and publish `ControlSync`, `Toast` and similar events to the DPG adapter and the RemoteApi ring buffer.

**Config.** `ConfigManager._execute_project_switch` loads, migrates and validates the file, loads the model, then calls `WallDanceApp._apply_config_without_model(flat)`. Save is `_get_saveable_config()` through `config_schema.structure`. A second hand-written applier lives in application/tests/replay.py for the offline tools.

## Threading and state

**Main thread:** all tick stages, command handlers, config apply, model-load orchestration (GUI pumped inside it), camera open/retry/refresh, DPG render, bus fan-out.

**DPG callback thread:** GUI callbacks (submit only), key handler, the TRT prompt choice, and the ROI/mask mouse handlers, which mutate `settings.roi_*` and the exclusion grid directly.

**Other threads:**
- Camera acquisition thread: newest-frame slot plus the recording callback.
- Playback decoder thread: events deferred through `_drain_pending_playback_event`.
- Model-load daemon thread: progress through a `queue.Queue`.
- `OpsWatchdog` daemon: reports only.
- Daemon workers `OpsReadiness`, `DryRunReplay`, `CalibSweep`, `KnownNTune` (each a `subprocess.run`), video import, RemoteApi and WebMonitor HTTP threads. These call `bus.publish` on their own thread.

There are no locks on app state; safety rests on "everything on main" plus the GIL.

**Latches:** `models._model_loading` (set from eight or more sites), `recording.source_transitioning`, `configs._pending_project_switch`, `cameras._pending_camera_refresh`, `cameras._next_camera_retry_time`.

**Duplicated state:** SystemState (GUI and app), `osc_enabled` (app and settings), `camera.state` (legacy CameraManager mirror of UnifiedCamera, synced by hand).

Several attributes are created lazily through `getattr`: `_remote_t`, `_ball_sizer`, `app._output_latency_pub`, `app._calib_sweep_seed`.

## Failure handling

- **Tick boundary.** `_guarded_tick` catches any exception outside processing into `_tick_fail_streak`. `_tick_process` has its own try/except (`_on_frame_error`, `_proc_fail_streak`). Both go to `_on_failure`: traceback plus an `Alert` at most every 10 s, and a clean exit (code 3 plus `logs/last_crash.json`) at 50 consecutive failures.
- **Session boundary.** `run()` writes the crash marker on any BaseException and re-raises. `finally` runs `_shutdown` (eight isolated steps: command drain, tracker log, watchdog, web monitor, remote, recorder, camera, UI) and removes the `session_running.json` sentinel. A leftover sentinel means a native or unclean exit; the next start prints it and shows a 15 s toast.
- **Seam.** `RuntimeAPI.drain` and `EventBus.publish` wrap each handler/subscriber in try/except and only print.
- **Camera.** `has_error`, a read failure or 2.5 s of IDS silence leads to `_mark_camera_unavailable` and a retry (0.5 s, then backoff 1 to 5 s). The D34 stall clock keeps OSC alive, but only from the frame-wait path.
- **Model.** A TRT input-size AssertionError queues a model reload; a failed load falls back to PyTorch.
- **Watchdog.** 10 s without a beat produces a stack dump and nothing else; `push_busy`/`pop_busy` bracket model load and project switch.
- **Startup.** Failure exits with code 2. The launcher only offers a manual "Restart" on a non-zero exit.

## Tunables

**Config constants.** About 80 imported from application/src/core/config.py (55 in app.py, 23 in main_loop.py). The ones that govern this subsystem:
- `OPS_TICK_ERROR_EXIT_STREAK` = 50, counted in ticks, not seconds
- `OPS_TICK_ERROR_ALERT_INTERVAL_S` = 10
- `OPS_WATCHDOG_HANG_S` = 10, `OPS_WATCHDOG_POLL_S` = 1
- `REMOTE_API_STATUS_INTERVAL_S` = 0.5
- `CAMERA_STALL_PERIODS` = 3, `CAMERA_STALL_MAX_S` = 5
- `PROJECT_PICKER_ON_START`, `WEB_MONITOR_*`, `REMOTE_API_*`

**Environment variables:** `WALLDANCE_AUTOLAUNCH_LAST`, `WD_REMOTE_API_PORT`, `WD_REMOTE_ALLOW_QUIT`, `WD_REMOTE_CONTROL`.

**Inline magic numbers (about 30):**
- main_loop.py: idle render 1/60 s; sleeps of 16, 5, 33, 10 and 1 ms; stall diagnostic 0.25 s; preview fallback cap 0.1 s; spike thresholds 20/35 ms; budget log every 5 s; ops and GPU-stats ticks 1 s; recording UI every 10 frames; reconnect delay 0.5 s.
- camera_controller.py: IDS disconnect timeout 2.5 s; retry backoff 1 to 5 s, factor 1.5.
- app.py: `_DRYRUN_FRAMES` 600; subprocess timeouts 300/900/2400 s; sweep 500 frames; input FPS cap fixed at 20 fps; viewport 1340x900; toast durations.
- api.py validation ranges: max dancers 1..16, stability 0..3, coast 0..10 s, static release 0..60 s, plate frames 9..400, exclude radius 0..4, slots 1..9.

**Per-project config:** 75 keys written by `_get_saveable_config`, about 61 `in config` branches in `_apply_config_without_model`.

## Interfaces with neighbours

**Camera.** The loop gates on `camera.state.is_open` (legacy mirror) while `UnifiedCamera.is_open` is the truth. `_reapply_ids_settings` falls back to auto exposure/gain through `getattr(self, 'ids_exposure_auto', True)` when a project never set them (application/src/runtime/camera_controller.py:387-399). Startup runs camera discovery (10 OpenCV index probes plus IDS enumeration) and opens the default, non-project camera before the GUI and project exist (application/src/runtime/main_loop.py:296-301), then the project switch reopens it. That is restart time spent before any output.

**Pipeline.** `app.py` and `main_loop.py` reach into `processor._plate_capture`, `_fg_detector`, `_fg_loaded_path`, `last_emitted`, `height_guard_event`. `settings` is written by the pipeline (auto height), by the DPG thread (ROI) and by handlers, with no lock. A project load does not reset the tracker, slots or MOG2.

**Remote API.**
- Policy is checked at submit time only.
- `_on_event` serialises every event, including the per-frame `StatsTick`, on the publishing (main) thread even with no client.
- `BgStatus` (per frame when a BG reference exists) and `RecordingUi` (2 Hz) share the 2000-entry ring with alerts.
- There is no command-result event, and no ROI or mask state in `/status`.

**GUI.** One toast slot. SystemState is duplicated. `render_frame` and `render_frame_raw` have different side effects, and which one runs depends on loop timing.

**Ops monitor.** `live` gating mutes alerts in exactly the wedge states. All cadences and sustain timers use `time.time()` (main_loop.py:1096-1100, 1285-1288), so a wall-clock step, for example an NTP sync on a field laptop, pauses the health tick and fps readout or fires false sustained alerts. The D34 code uses `perf_counter`.

**Launcher.** The app's `print()` calls go synchronously into a pipe drained by the launcher's Tk thread; `_Tee` handles a dead pipe but not a full one. Restart is manual.

**Web monitor.** Started by `_startup` on 0.0.0.0:8080 without authentication, in contrast to the loopback-plus-token remote API.

**Watchdog.** `_prompt_trt_build_sync` reached from a drain handler (`NewProject`) neither beats nor sets busy, so a prompt left unanswered for 10 s logs a false hang with stack dumps.

## Strengths

- Crash hygiene is deliberate and tested: per-frame and per-tick boundaries, rate-limited alert plus traceback, `_shutdown` with isolated steps so the recording finalise always runs (main_loop.py:402-443), a crash marker written atomically with fsync and replace, plus a running-sentinel that separates a Python crash from a native or unclean exit (services/crash_marker.py); covered by tests/test_main_loop_guards.py.
- The command seam is sound: thread-safe submit, snapshot drain that bounds re-entrancy (api.py:1149-1156), duplicate-handler rejection, per-handler and per-subscriber exception isolation, and tests asserting that every command is registered, the adapter covers the GUI callback surface, and core/runtime never import ui or dpg (tests/test_runtime_api.py:263-325).
- The ops heartbeat runs first on every iteration including wait paths, so the camera-down alert keeps ringing; the watchdog has re-entrant busy brackets for legitimate long blocks; `[Diag]` logs STALL/OK transitions and `[Budget]` is printed after render so the GUI tail is included (the PERF-11 note).
- The camera-loss funnel covers the awkward case of a capture error while the camera is still marked open (main_loop.py:673-684, with the comment on the earlier 'idles forever with a green badge' bug), using backoff and a single reconnect state machine.
- Model loading runs in a worker thread with a progress queue so no GUI call is made off the main thread (model_controller.py:494-548), falls back to PyTorch, and a TRT input-size mismatch during processing queues a safe reload rather than counting as a crash (main_loop.py:849-866).
- The remote API is conservative: loopback plus bearer token, explicit safe/control/heavy/never classes with an allowlist, an operator-only 'allow control in RUN' toggle that cannot be set remotely, and path confinement to shared roots (services/remote_api.py:56-145, 277-297).
- Config apply distinguishes a full project load from a partial bundle, so absent keys get defaults rather than the previous project's values on a full load and are left alone on a profile switch (app.py:1322-1336, 1475-1496). Ordering constraints are commented where they bite (tracking_mode before tracker_max_age, app.py:1430-1441).
- Small, pure state objects where it matters: `RoiState` (drawn versus effective ROI, with the reason for the split documented) and `CameraStallClock` (times passed in, no camera or OSC dependency).
- Idle renders are throttled to 60 Hz with a stated rationale (main_loop.py:1218-1223), and the stdout tee keeps the app alive when the launcher window closes (services/app_log.py).

## Questions the reader could not answer from code

- Is starting in RUN deliberate, so the app resumes output after a restart? If so, should the readiness report run automatically after the first project load rather than only on a STANDBY to RUN click?
- After exit code 3 or a native crash, is the technician expected to click Restart and confirm the picker, or should the launcher relaunch on the last project by itself? WALLDANCE_AUTOLAUNCH_LAST is read but never set by any script in the repo.
- Is the 2.5 s IDS disconnect timeout meant to sit below CAMERA_STALL_MAX_S (5 s)? Do field logs show stalls longer than 2.5 s, and how long does the reopen that follows take?
- After an app start and project load, does the Aim panel show 'empty wall: none' until Calibrate is run again? That would confirm the plate is looked up under the previous project's folder.
- What does the media server do when the OSC stream simply stops (STANDBY, quit, crash, model reload) compared with an explicit clear? Nothing sends `send_clear` on those paths today.
- Is OSC output during a Calibrate or DANCERS run in STANDBY, and during playback of a take in RUN, intended?
- Are output smoothing (L greater than 1) or box clamp off ever used in a show? Neither is saved with the project.
- Is the remote API used while a show is live, and by what kind of client (a person, a script, an agent)? That determines how much command validation and a result event matter.
- Does moving, resizing or minimising the DearPyGui window on the show laptop pause the loop? Is the window ever touched during a show?
- Is application/tests always shipped to the show laptop? The Dry-run, Auto-tune and Known-N buttons execute scripts from it.
