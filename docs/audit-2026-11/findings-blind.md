# Blind-scan findings (119)

*First pass of the 2026-11-18 audit: ten readers, one per subsystem, code only. Each finding is a LEAD. 61 of them were re-checked against the code afterwards; the verdict is printed under the finding and the full check is in [defects-verified.md](defects-verified.md). A finding with no verdict line was not re-checked.*

Ids are `<subsystem>#<n>`; the plan ([PLAN_CORDEE_2026-11.md](../PLAN_CORDEE_2026-11.md)) cites them.

## runtime-core

### runtime-core#1 — A persistent error in a cosmetic stage ends the show, and recovery is entirely manual

severity high · reader's confidence high · robustness

**Evidence.** application/src/runtime/main_loop.py:279-290: `_guarded_tick` counts every exception raised in `_tick` outside the process try-block into `_tick_fail_streak` and skips the rest of the tick, including `_tick_render`. Nothing renders in that path, unlike `_on_frame_error` at :456-459. At :483-488 the loop sets `app.running=False` once the streak reaches OPS_TICK_ERROR_EXIT_STREAK=50 (application/src/core/config.py:1133), giving exit code 3 (:264-267). OSC for the frame has already been sent inside `_tick_process` (application/src/core/pipeline.py:1054-1081) before `_tick_preview`, `_tick_events`, `_tick_record` and `_tick_render` run (main_loop.py:522-528). The streak is counted in ticks: about 2.5 s at 20 fps for post-acquire stages, but a few milliseconds for a pre-acquire stage (`_tick_pumps`, `_tick_ui_input`), which has no frame wait between failures. `_tick_preview` can also end a tick with no render at :967 and :977. After the exit, launcher/gui.py:251-256 only re-enables a 'Restart' button; main_loop.py:320-328 then shows the project picker unless WALLDANCE_AUTOLAUNCH_LAST is set, and no script in the repo sets it. `LoopWatchdog` 'takes no recovery actions' (application/src/core/ops_monitor.py:566-575).

**Why it matters.** A drawing or stats bug on one odd track (overlay, ball sizer, recording UI) freezes the GUI for about 2.5 s and then deliberately stops a session that was still emitting correct OSC. The technician must notice, click Restart, pick the project and wait for the engine load. A true hang never exits at all. This works against 'reliable output with little intervention'.

**Suggestion.** Guard each stage separately with a policy. Preview, events, record and render failures should disable that stage (or fall back to a bare render), raise a persistent alert and never exit. Always attempt a render in the except path, and count streaks in seconds. Only acquire/process should escalate, and should first try an in-process remedy (tracker/output reset, then model reload) at half the streak. Have exit 3, an unclean sentinel, or a watchdog hang while in RUN trigger an automatic relaunch on the last project (the launcher sets WALLDANCE_AUTOLAUNCH_LAST when last_crash.json exists), keeping the picker for cold starts.

### runtime-core#2 — The output path runs on the GUI thread, so whatever blocks DPG or a command handler blocks OSC

severity high · reader's confidence high · paradigm

**Evidence.** application/src/runtime/main_loop.py:509-528 is one serial chain. At :1198-1204 there is one `dpg.render_dearpygui_frame()` per processed frame (vsync off, application/src/gui.py:3113). At :1052 `PreviewFrame` goes to gui.py:1640-1667 (resize, cvtColor, float32 conversion, texture upload) inline. At :1114 `StatsTick` is published every frame to gui.py:1687 onward (about 40 dpg calls). application/src/runtime/api.py:1149-1167 executes handlers inline in the tick. Examples: `_cb_check_readiness` (application/src/app.py:2314-2335, documented as about 0.3 s including the OSC probe; its remote class is SAFE, so it is callable in RUN, application/src/services/remote_api.py:58); the issue-report PNG and JSON writes (app.py:2083-2107); `RefreshCameras` (closes the live camera, sleeps 0.5 s, probes 10 OpenCV indices, application/src/runtime/camera_controller.py:178-219). The reconnect attempt is inline at main_loop.py:576-583. The TRT modal wait runs inside the tick (application/src/runtime/model_controller.py:360-364). application/src/core/camera_stall.py:11-13 states that a gap the main loop made itself is not bridged.

**Why it matters.** OSC cadence and the slot hold clocks depend on GUI work and on whatever the operator or a remote client clicks. A 0.3 s handler is six lost frames at 20 fps, and the D34 keep-alive cannot run while the loop is blocked. Any GUI-side stall is a show stall. A native window move/size loop may be one more case; I did not verify that.

**Suggestion.** The command queue and event bus already make the split possible. Run acquire, process and OSC (plus the drain) on a pipeline thread; let the DPG thread render at its own rate and consume events from a thread-safe queue, latest-only for PreviewFrame and StatsTick. Interim steps: move the readiness probe, camera open/refresh and file writes to workers; throttle StatsTick to about 5 Hz; send the keep-alive from a timer whenever nothing has been emitted for more than three frame periods.

### runtime-core#3 — The D34 stall bridge stops at 2.5 s, not at its 5 s cap: the main loop tears the IDS camera down first

severity high · reader's confidence medium · robustness

**Evidence.** application/src/runtime/main_loop.py:691-696: before each read, `cameras._ids_stream_timed_out()` being true leads to `_stall_not_live()` (which forgets the stall, application/src/core/camera_stall.py:75-81), then `_mark_camera_unavailable(close_active=True)` and a retry in 0.5 s. The timeout is `_ids_disconnect_timeout_s = 2.5` (application/src/runtime/camera_controller.py:77), tested against `get_last_acquired_age_s()` at :148-156. The stall clock would accept a gap up to CAMERA_STALL_MAX_S = 5.0 (application/src/core/config.py:136; camera_stall.py:60-62). The config comment (:133) says the laptop's camera stalls about 1.7 s, 0.7 to 2.9 times a minute. The keep-alive is only called from the frame-wait path (main_loop.py:744). The reopen happens on the main thread in `_tick_pumps` (:576-583). The default slot hold is IDENTITY_SLOTS_COAST_S = 2.0 (config.py:323). Not checked: whether field logs show stalls above 2.5 s, and how long an IDS reopen takes.

**Why it matters.** For any stall in the 2.5 to 5 s range the code closes and reopens a camera that would have resumed by itself, stops the keep-alive, and lets the hold clocks run through an outage of at least 3 s against a 2 s hold. Slot ids are then lost. USB camera stalls are one of the owner's named field conditions.

**Suggestion.** Derive the disconnect timeout from CAMERA_STALL_MAX_S (cap plus a margin) or make it one constant. Treat 'stream silent, device still present, no `has_error`' as a stall to keep bridging, and reserve teardown for device errors or silence beyond the cap. Run the reconnect off the main thread so the keep-alive and the UI continue. Add a replay gate with a 3 s injected stall.

### runtime-core#4 — A project switch loads the empty-wall plate from the previous project's folder

severity high · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/runtime/config_manager.py:268 calls `self.apply_config(config)`; `_current_project` is only updated afterwards at :271. Inside the apply, application/src/app.py:1497-1498 calls `_load_project_plate()`, whose `_plate_dir()` (app.py:1839-1841) is built from `self.configs._current_project`. `_load_project_plate` (:1860-1870) sets `fg_plate_path` to `<that dir>/plates/latest.npz`, or clears the plate if the file is absent. The initial value is 'default' (config_manager.py:102) and nothing else sets it before the switch; grep shows :271 is the only assignment on that path. Not run; no test covers plate loading on a project switch.

**Why it matters.** On a cold start the app looks in default/plates, so the project's saved empty-wall snapshot is not loaded. Switching A to B gives B the plate of A, or none. The plate is the evidence behind the ghost veto and the dancer hold. After a mid-show restart it cannot be recaptured because the wall is not empty, so the show runs the rest of the night without it, or with another wall's plate.

**Suggestion.** Set `_current_project` (and the recorder project) before `apply_config`, or pass the project name into the apply and `_plate_dir`. Add a test: switch project, then assert `settings.fg_plate_path` is under the new project's directory. Log one line at load naming the plate path and project so a mismatch is visible.

### runtime-core#5 — The project switch is not transactional: an exception wedges the loop silently, and a failed model load leaves mixed state

severity medium · reader's confidence high · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/runtime/config_manager.py:172 sets `models._model_loading = True`. It is cleared only on the explicit return paths at :192, :260 and :320; the `finally` at :145-149 covers the watchdog only. application/src/runtime/model_controller.py:474 has the same shape (cleared at :608, :619, :683). By contrast application/src/runtime/recording_controller.py:495-521 uses try/finally for its latch. In the loop, the pending switch is cleared before the call (application/src/runtime/main_loop.py:558-562), so after an exception `_guarded_tick` reports one tick error and every later tick takes the idle branch at :594-597 and succeeds, resetting the streak. `camera.state.is_open` was set False at config_manager.py:178-179. The health monitor mutes fps and no-detection alerts when the model is not ready (application/src/core/ops_monitor.py:412-413) and after 15 s raises 'Camera down - check USB3/power' (:525-534), which is wrong. On a model-load failure (:258-263) the profiles (:197-198), `_trt_requested` (:208), `settings.imgsz` (:215-216) and `current_config_path` (:160) already belong to the new project while everything else is the old one; there is no toast and the picker is already closed.

**Why it matters.** Any bug or bad value during a switch leaves a live GUI with a RUN badge, no processing, no OSC and a misleading camera alert, until someone thinks to load a project again. After a failed load, a Save writes the old project's values with the new project's profile bundle.

**Suggestion.** Wrap the switch in try/finally that clears the latch and reopens the camera. Parse the new config into locals and commit profiles, imgsz, path and TRT intent only after the model load succeeds. On failure, show a toast and reopen the picker. Add a health alert for 'RUN but pipeline idle for more than N s' (model not ready or loading, without a busy bracket).

### runtime-core#6 — RUN/STANDBY has two unsynchronised copies; the Go-Live check never fires on a normal start; STANDBY can still emit OSC

severity medium · reader's confidence high · ux

**Re-checked: confirmed.**

**Evidence.** The GUI keeps its own `_system_state` (application/src/gui.py:316, 858-886) and the runtime a mirror (application/src/app.py:521, 1056-1062). The only sync is GUI to runtime: application/src/ui/adapter.py:411-413 maps `StateChanged` to `lambda e: None`. `SetState` is a remote CONTROL command (application/src/services/remote_api.py:61) and is allowed from STANDBY without the operator toggle (:142-144). Both copies start in RUN, and the readiness check runs only on a STANDBY to RUN edge (app.py:2227-2232). OSC is gated by `bool(self.osc) and settings.osc_enabled` only (application/src/core/pipeline.py:1027), while the loop forces processing in STANDBY during Calibrate, the DANCERS run and plate capture (application/src/runtime/main_loop.py:813-816). The state docstring says STANDBY means no OSC (application/src/runtime/api.py:46-55).

**Why it matters.** After a remote SetState the operator's badge and buttons can show RUN while nothing is sent to the wall, or STANDBY while OSC flows. In the usual flow (start, pick project, already RUN), and after every crash restart, the pre-show readiness toast never appears. A calibration with people on the wall in STANDBY drives the media server.

**Suggestion.** Add a GUI method that sets the state visuals without firing the callback and bind it to `StateChanged`. Run the readiness report once when the first project load completes in RUN. Put an explicit output gate in the processor (emit only in RUN, or when a calibration is explicitly allowed to emit) instead of relying on whether the loop calls `process`.

### runtime-core#7 — Config save and apply are separate hand-written lists that have drifted, and the offline tools use a third

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** `_apply_config_without_model` reads `tracker_swap_correctors`, `crossval_skel_min_kpts`, `crossval_skel_min_conf`, `crossval_motion_min_ratio` (application/src/app.py:1457-1467) and `dial_b_relevant` (:1503). `_get_saveable_config` (:1218-1313) writes none of them. Known-N Apply puts the crossval knobs in the file (:2617-2637), but the next Save rebuilds the file from the saveable dict (application/src/runtime/config_manager.py:346-350, 372-374), so they are dropped. `output_smoothing_l` and `box_clamp_enabled` have operator commands (app.py:871-873, 1779-1790) but appear in neither save, apply nor the GUI seed (:755-837). application/tests/replay.py:196-351 is a separate applier that also handles `box_clamp_enabled` (:261), `belt_backing`, `entry_min_travel_h`, `auto_height`, `height_guard` (:284-287) and `tracker_own_height_gates` (:311-312), which the live path ignores. The comment at app.py:1447-1452 records that this drift already shipped once. 'Full config' is inferred from sentinel keys (:1322), and partial applies still reset ROI edit mode (:1560-1561) and re-show Dial B (:1503).

**Why it matters.** A project tuned offline does not run with the same settings live. A manual save after a known-N apply silently reverts the tuned gates on the next load. Output smoothing and box clamp reset on every restart and leak between projects.

**Suggestion.** Use one declarative key table (key, getter/setter, shared or profile scope, default on full load, GUI sync name) to drive save, apply, the GUI seed and replay's `_build_processor`. Add a round-trip test asserting that every key the apply reads is in the saveable dict. Pass an explicit `mode` (project, profile or patch) instead of inferring it from keys.

### runtime-core#8 — Legacy commands are not validated, handlers mutate before they fail, and remote policy is checked at submit rather than at execution

severity medium · reader's confidence medium · security

**Evidence.** The module docstring says commands are 'validated on construction' (application/src/runtime/api.py:7-10), but `SetSensitivity`, `SetConfidence`, `SetMotionSensitivity`, `SetGapBridging` (:97-115), `SetPersonHeight`, `SetImgsz`, `SetTrackerMaxAge`, `SetMog2Scale` (:293-310) and `SetOscTarget` (:536-539) have no `__post_init__`. The remote path builds them with `ctype(**args)` and no type coercion (application/src/services/remote_api.py:269-274). `_cb_confidence_change` assigns `settings.confidence = value` before `float(value)` can raise (application/src/app.py:1716-1719); `tracker.max_age = value` is assigned raw (:1974-1975). Policy is evaluated on the HTTP thread at submit (remote_api.py:251-255), and `RuntimeAPI.drain` (api.py:1149-1167) executes without re-checking. A failed handler only prints (:1162-1166) and the client still gets 200 'queued'. Not checked: how the pipeline behaves with a string confidence or a zero height or scale.

**Why it matters.** The GUI sliders were the only range check. The remote client (a script or an agent) can leave a wrong-typed or out-of-range value in live settings; if that makes every frame raise, the error streak ends the session. `SetState run` followed by `LoadModel` in one batch both pass the STANDBY-only check and the model load then runs in RUN.

**Suggestion.** Validate type and range in `__post_init__`, reusing the limits from `config_schema.validate_flat`. Make handlers convert first and mutate after. Tag commands with their origin and re-check policy at dispatch. Publish a `CommandResult(ok, error)` event so both the GUI and the remote client learn that a command failed.

### runtime-core#9 — Ops alerts are single-slot 8 s toasts; only `light_changed` stays visible

severity medium · reader's confidence high · ux

**Evidence.** application/src/ui/adapter.py:34 sets `STRIP_ALERTS = ("light_changed",)`. At :345-348 every `Alert` becomes an 8 s toast, and only strip kinds are pushed to the alerts strip. application/src/gui.py:2929-2952 keeps one `toast_window`, deleted and replaced by any later toast. Loop errors alert at most once per 10 s (application/src/runtime/main_loop.py:470-482) and health alerts re-fire every 120 s (application/src/core/config.py:1124). The alert kinds are `camera_down`, `fps_drop`, `gpu_temp`, `height_stale`, `no_detection`, `over_cap`, `frame_error`, `tick_error`, `osc_send`. Toast expiry runs only in `gui.render_frame()` (gui.py:3140-3144), which the tick tail and the model-load pump do not call (they use `render_frame_raw`).

**Why it matters.** A technician who is not looking at the laptop during those 8 s, or who triggers any other toast, never sees that frames are failing, OSC sends are failing or fps dropped. The strip mechanism with clear events already exists but carries one kind.

**Suggestion.** Send every ops alert to the strip until its condition clears (HealthMonitor already produces `cleared`; add clears for frame_error, tick_error and osc_send). Keep a short alert history with timestamps and a count badge. Mirror active alerts on the phone monitor page.

### runtime-core#10 — Offline tune and replay jobs are fire-and-forget subprocesses that RUN and Quit do not stop

severity medium · reader's confidence high · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/app.py:2337-2352 with :2411-2417 (dry-run, 300 s timeout), :2436-2452 with :2493-2499 (sweep, 900 s), :2566-2582 with :2593-2598 (known-N, 2400 s): each starts a daemon thread running `subprocess.run` with no stored handle and no 'already running' flag. The STANDBY gate is checked only at start. `_cmd_set_state` (:1056-1062) does not look at running jobs. `_shutdown` (application/src/runtime/main_loop.py:402-443) does not terminate them; daemon threads die with the process but the child process does not. The three scripts are under application/tests (app.py:2410, 2492, 2592).

**Why it matters.** A known-N tune of up to 40 minutes, started during setup, keeps competing for the GPU after the operator presses RUN, and survives a quit and restart. A double click starts two. Operator-facing buttons depend on files in a tests directory that a release step could reasonably exclude.

**Suggestion.** Add one JobRunner that owns the Popen handles: single-flight per job type, cancel (or an explicit confirmation) when entering RUN, terminate in `_shutdown`, and a `job_running` field in the stats and remote status. Move the three entry scripts to a shipped tools package.

### runtime-core#11 — The seam is nominal: the loop reads controller internals, and the command queue is one of several mutation paths

severity medium · reader's confidence high · paradigm

**Evidence.** application/src/runtime/main_loop.py:105-178: `LoopHost` lists 45 attributes typed `Any`. The loop additionally uses `app._remote` (:534), `_remote_status` (:542), `_output_latency_pub` (:1154), `_sync` (:1300), `_light_ratio` (:1385), and 40 distinct `app.<controller>._private` members (for example :558-561, :571-573, :813-815, :862-864), with 33 `getattr`/`hasattr` fallbacks. ROI and mask mouse handlers are registered on the DPG callback thread (application/src/ui/adapter.py:82-90) and write `settings.roi_*` and the exclusion grid there (application/src/ui/roi_mask_editor.py:398-405, 443-463), while the main thread polls the same handlers (main_loop.py:604-606; roi_mask_editor.py:283-300) with no lock. The editor calls the GUI directly through `gui=lambda: self.ui.gui` (application/src/app.py:630-636), bypassing the bus. Worker threads publish events (app.py:2231 and 2310, 2348 and 2424, 2447 and 2507, 2577 and 2606) that create and delete DPG items off the main thread (application/src/gui.py:2929-2948), despite the comment at gui.py:2950-2951.

**Why it matters.** The 'single execution point' and 'DPG-safe seam' claims do not hold. A renamed private attribute degrades silently through a `getattr` default. ROI edits can be read half-written by the pipeline. A readiness toast at Go-Live can race the render or be lost. A second client cannot observe ROI or mask changes. None of this is caught by the import-direction tests.

**Suggestion.** Give each controller a public `poll()` returning 'tick consumed' so the loop stops reading pending flags. Deliver ROI and mask input only through the main-thread poll or through commands, and drop the duplicate handler registrations. Add `bus.post()` (a thread-safe queue drained in the tick) for non-main publishers, with a debug assert on the thread id in `publish` and `drain`. Make `LoopHost` exact and narrow, then test the fake against it.

### runtime-core#12 — The IDS camera is never closed at shutdown, and 'closing the camera' for a model load only flips a legacy flag

severity low · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/runtime/main_loop.py:426-428 and :441: the camera step releases only `app.camera.cap` (the legacy OpenCV capture); `unified_camera.close()` is never called in `_shutdown`, so teardown relies on `IDSCamera.__del__` (application/src/camera/ids_camera.py:381-387). application/src/app.py:371-373 and application/src/runtime/config_manager.py:175-179 call `self.camera.close()`, which is `CameraManager.close` (application/src/camera/camera_manager.py:306-313) and does not touch the UnifiedCamera, despite the comment at application/src/runtime/model_controller.py:476. `camera.state.is_open` is hand-synced in several places (application/src/runtime/camera_controller.py:113, 191, 282, 311; app.py:1109). Not checked: what the IDS SDK does when the process exits mid-acquisition.

**Why it matters.** On the production IDS path the acquisition thread and USB stream are still live when the interpreter finalises, which risks a non-zero exit or a camera left busy for the restart. During a TRT build the camera keeps streaming although the code intends otherwise, and the flag the loop, the health monitor and the UI trust can disagree with the real device.

**Suggestion.** Add `step("camera close", app.unified_camera.close)` before the UI destroy. Use one camera facade that owns `is_open` and exposes an explicit `pause()` (stop acquisition) and `resume()` for model loads, and retire the mirrored `CameraManager.state`.

## detection-pipeline

### detection-pipeline#1 — The person-height size gate only runs when a frame has two or more detections

severity high · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/pipeline.py:2387-2389: `_filter_duplicate_detections` starts with `if len(detections) <= 1: return detections`. The min/max height gate sits below it at 2397-2409 and is the only place the ratios are applied (the other users of person_height_min/max_ratio are config plumbing and an ops alarm at runtime/main_loop.py:1310). application/src/core/auto_height.py:5-6 records the field symptom: 'real dancers dropped by the size gate in frames with two detections'. `_guard_person_height` (pipeline.py:1414-1444) reasons about what 'the size gate rejects' whether or not the gate ran.

**Why it matters.** A lone detection of any size reaches the motion/skeleton gate and the tracker: a tiny far ghost on an empty wall, or a technician filling the lens. The same object is dropped as soon as a second detection appears, so ghost behaviour flips with the detection count. Every single-person test take runs with the gate off, so solo footage cannot validate person_height_px or the ratios, and a wrong height only starts dropping dancers when the second dancer enters.

**Suggestion.** Move the size gate into its own step that runs unconditionally before the dedupe early return, or, if a lone detection is meant to be exempt, state that in code and make the height guard use the same rule. Add a unit test with one oversize and one undersize detection, and replay a solo take with the gate active to see what it changes.

### detection-pipeline#2 — TensorRT intent has several sources of truth, and recovery depends on exception text and operator clicks

severity medium · reader's confidence high · robustness

**Evidence.** application/src/runtime/model_controller.py:
- `model_manager.use_tensorrt` is assigned only at :95 (constant) and :320 (rebuild). `_cb_trt_toggle` (261-301) and the project switch (application/src/runtime/config_manager.py:207-208, 235) never update it, yet `_cb_model_change` (:196) and the post-load bookkeeping (:633-636, :646) read it. After TRT is switched off, picking another model still takes the TRT branch.
- An engine/imgsz mismatch is handled by catching `AssertionError` and matching three substrings (:108-111; application/src/runtime/main_loop.py:849-866).
- Warmup uses a 640x640 numpy frame (:461-463, :600-601, :656-657), not the live call (CUDA tensor at imgsz through PoseRunner).
- `_prompt_trt_build_sync` loops until a click (:250-252).

application/src/core/model_manager.py:
- The .pt is checked and downloaded before the engine is considered (:169-179), and the downloaded object is discarded.
- Engines are keyed by name and imgsz only (:113-135). A stale engine is found only by the load-time test inference (:254-267) and then falls back to PyTorch.

**Why it matters.** The code itself calls PyTorch 3-7x slower (model_controller.py:117-118), which does not hold 20 fps. The remaining ways to land there need a non-developer to notice a banner and wait 2-5 minutes for a rebuild, or leave an unattended restart sitting on a modal. Offline with the .pt missing, a valid engine cannot be loaded at all. The inconsistent flag makes the checkbox, the project setting and actual behaviour disagree.

**Suggestion.** Keep one engine-state record in ModelController (requested backend, loaded backend, reason) and derive `use_tensorrt`, the checkbox and the banner from it. Compare the engine's input size with settings.imgsz before inference instead of parsing an assertion. Key engine files by TRT version and GPU, and have the launcher or pre-show readiness build missing engines for the configured (model, imgsz). Skip the .pt step when a valid engine exists. Warm up through `processor._run_yolo` with a zero CUDA tensor at imgsz. Give the startup prompt a timeout default.

### detection-pipeline#3 — Dedupe runs before any track knowledge and splices keypoints between detections; the tracker's position is the keypoint mean

severity medium · reader's confidence medium · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/pipeline.py:
- 2411-2429: every undersize detection within 1.5 x person height of a valid one donates each keypoint whose confidence is higher. There is no check that the donor keypoint lies in or near the recipient box.
- 2434-2471: a detection with fewer than 8 valid keypoints or mean confidence below 0.5 is deleted if a strong skeleton is within 1.5 x person height.
- 2473-2492 with `_bbox_contains` (2538-2547): a box whose centre is inside a larger box and under half its area is suppressed and its confident keypoints are written into the larger one in place.
- None of these consult tracks; crossval (2296-2320) and exclusion (1959-1962) do.

application/src/core/tracker.py:547 and 367-377: the Kalman measurement is the confidence-weighted mean of visible keypoints.

Also pipeline.py:2346-2349: the crossval motion EMA starts from 0 for a cell with no detection last frame, so a detection entering a new cell is credited 0.65 x its measured motion.

**Why it matters.** Two dancers within 1.5 body heights is a duet. In dim IR at distance, mean keypoint confidence under 0.5 is plausible for a real dancer, who is then removed before the tracker can use its own knowledge that a confirmed track is there. Spliced keypoints from a shadow fragment or a second person move the measured centroid, which is the position sent over OSC. I did not measure how often this fires on footage.

**Suggestion.** Make these rules track-aware or move them after association: never suppress a weak detection that sits on a confirmed track other than the strong neighbour's; accept a donor keypoint only inside the recipient box plus a margin. Log per-frame counts for shadow, containment and small-merge so replays show how often each fires on duo takes. Initialise the crossval EMA with the first raw value.

### detection-pipeline#4 — The display gamma slider is an input of the motion model, with no reset or recalibration when it changes

severity medium · reader's confidence medium · robustness

**Re-checked: partly.**

**Evidence.** application/src/core/pipeline.py:1786 and 1820-1837: `_fixed_gamma_for_motion` applies `self.enhancer.gamma` to the gray fed to MOG2 and frame-diff. application/src/app.py:1637-1641: the gamma slider sets `enhancer.gamma` and only requests a reprocess. The MOG2 learn rate is 0.001 (application/src/core/config.py:842). The varThreshold calibration measures noise on this post-gamma gray (`get_last_motion_gray`, application/src/runtime/calibration_flows.py:383). `MotionModel` has its own `fixed_gamma` parameter (application/src/core/motion_model.py:64) that the pipeline never uses (`MotionModel()` at pipeline.py:436-438).

**Why it matters.** A gamma change to make the picture or YOLO better is a global level step for a background model that takes on the order of 1000 frames (about 50 s at 20 fps) to re-converge. During that time the silhouette masks behind bridging and cold blobs are unreliable, and the calibrated varThreshold no longer matches the noise, because gamma changes noise amplitude in the dark. Changing light is the condition under which the technician reaches for this slider. I did not measure the size of the effect on footage.

**Suggestion.** Decouple the two: give the motion model its own gamma, set by Calibrate and stored with the varThreshold (the `MotionModel.fixed_gamma` parameter already exists), and leave the display gamma for YOLO and preview only. If they must stay coupled, reset MOG2 and mark the motion calibration stale when the slider moves.

### detection-pipeline#5 — One affine transform is hand-written in at least seven places, its parameters travel inside the timing dict, and tracker state lives in a space that changes with ROI and imgsz

severity medium · reader's confidence high · paradigm

**Evidence.** application/src/core/pipeline.py: the tracker <-> ROI-local <-> camera transform appears in
- `_LetterboxMotionProxy` (210-325, three copy-pasted blob rescale blocks)
- `_TrackerSpace.blobs_to_tracker` (355-370)
- inline for raw detections (741-745)
- `_exclusion_norm_xy` (1919-1938)
- inline in `_crossval_motion_filter` (2328-2340)
- `_unscale_letterbox` (2064-2141)
- `_zone_ok_fn` (1470-1480)
- as literals `1.0, 0, 0, 0, 0, True` in `_gate_cold_blobs` (1812-1814)

Comments cite bugs #9 and #10 from this duplication. application/tests/test_transforms.py:115 'replicates the inline crossval transform' rather than calling it.

Letterbox and ROI geometry reach the tracker stage through `gpu_timing.get('letterbox', {})` / `get('roi', {})` with identity defaults (713-730). The proxy takes its scale from separate attributes set conditionally (775-780). There are two `_resolve_roi` with different return shapes (pipeline.py:894-907 tuple; application/src/core/gpu_pipeline.py:551-574 dict). The tracker reset is called only from the manual reset, a mirror/rotate change and a playback restart (application/src/app.py:895, 1135, 2016), not on ROI or imgsz change.

**Why it matters.** Every new evidence source needs one more hand-written copy; an error here shows up as ghosts or misplaced exclusion with no exception. Because Kalman state and the crossval cell memories are in letterbox pixels, changing ROI or imgsz during setup leaves seconds of tracks in the wrong space. Geometry in a dict typed as timings fails silently if a key is renamed.

**Suggestion.** Have GpuPipeline return a frozen geometry object (roi, scale, pads, imgsz) with to_tracker / to_roi / to_frame / to_mask_norm methods for points and boxes. Build the motion proxy, exclusion mapping, crossval and unscale from it and delete the inline math. Compare the geometry with the previous frame's and reset tracker and cell state when it changes. Longer term, track in ROI or camera pixels and convert detections once at extraction, so tracker state does not depend on imgsz.

### detection-pipeline#6 — With output smoothing L > 1, an empty wall is never reported on OSC

severity medium · reader's confidence high · correctness

**Re-checked: partly.**

**Evidence.** application/src/core/pipeline.py:1065-1071: on the lagged branch `send_frame` is called only `if lagged_tracks:`. At L = 1 (1061-1064) `send_frame(out_tracks, ...)` runs with an empty list too, and application/src/core/osc_output.py:181-182 then sends `/walldance/count [0]`. `OutputSmoother.process` returns [] both during warm-up and once every tail is flushed (application/src/core/output_smoother.py:283-325). L is operator-settable (application/src/app.py:1782).

**Why it matters.** After the last dancer leaves at L > 1, the last count the media server received is still 1 or more with the old id. If it keys video on count or ids, the last dancer's content stays up until someone returns. The two smoothing modes have different end-of-presence contracts, which nobody downstream would expect. I did not check whether L > 1 is used in shows or how the media server consumes /count.

**Suggestion.** Tell warm-up apart from empty (for example, the smoother has run at least L steps since reset) and send the empty frame in the second case. Add a test that the last message after all tracks end is count 0 for L = 1 and L = 4.

### detection-pipeline#7 — The detect cache is captured after the size gate and dedupe and freezes the person height, yet the tuner sweeps person_height_px over it

severity medium · reader's confidence high · testing

**Re-checked: confirmed.**

**Evidence.** application/src/core/pipeline.py:748 runs `_filter_duplicate_detections` (size gate and dedupe at the build-time height); the capture hook is called afterwards at 793-795. application/tests/detect_cache.py:179 stores `int(space.person_height)`, and `replay_gpu_cached` (pipeline.py:834-835) uses that stored value for crossval, exclusion and the tracker. Only the cold-blob call reads the current `settings.person_height_px` (pipeline.py:975). The height guard does not run in replay. REBUILD_KEYS (detect_cache.py:57-65) has no person-height keys, while the docstring (19-21) lists 'person height' as tunable from the cache, and application/tests/tune.py:43-50 sweeps `person_height_px: [120, 148, 200]` as a cache-tunable lever.

**Why it matters.** A cache sweep of person height changes only the cold-blob path. The size gate, dedupe distances, crossval cells and tracker gates stay at the build-time value, so the search reports sensitivities that the live show will not reproduce. Tuning results feed the show configuration.

**Suggestion.** Capture raw detections before the height guard and dedupe, plus the letterbox scale, and run those steps inside `replay_gpu_cached` with the person height computed from settings. Otherwise add person_height_px, both ratios, height_guard and auto_height to REBUILD_KEYS and correct the docstring. Extend the cache fidelity test to compare cache and direct runs at a changed person height.

### detection-pipeline#8 — The stage timings behind [Budget] and [PerfSpike] do not measure where GPU time goes

severity medium · reader's confidence medium · performance

**Evidence.** application/src/core/gpu_pipeline.py:610-672 and 711-769 time upload, enhance, yolo_resize and preview with `time.time()` around asynchronous CUDA launches, so they record launch time only. The enhance work is paid at the first sync: `.cpu()` in the preview download (gpu_pipeline.py:188) on preview frames, otherwise `im.max()` in application/src/core/yolo_runner.py:115, and is booked as `preview_download_sync` or `yolo`. Production is Python 3.12 on Windows (application/requirements-prod.txt:4), where `time.time()` is, as far as I know, the coarse system-tick clock; pipeline.py uses `perf_counter`. Everything after `timing["track"]` (pipeline.py:994) — finalize, height learning, slots, belt, OSC send — is untimed, and `fg` is measured but not printed (application/src/runtime/main_loop.py:1457-1461).

**Why it matters.** These logs are the only field evidence for where the 50 ms goes. As written, `enhance` looks nearly free, `yolo` and the preview download absorb its cost, and a few milliseconds of post-tracker work appear only as the gap between process_wall and the listed stages. Optimisation and imgsz/model choices made from these numbers will be aimed at the wrong stage. The Windows clock granularity is from memory, not checked on the laptop.

**Suggestion.** Use `perf_counter` throughout gpu_pipeline.py. Add a diagnostic flag that synchronises the device after each GPU stage for truthful attribution, off by default. Add `post` (finalize through emit) and `fg` to the budget line. Use a monotonic clock in `_preview_due` as well, so a wall-clock step cannot freeze the preview.

### detection-pipeline#9 — FrameProcessor is the pipeline, the detection filters, the output stage and three calibration tools in one object, with three different ways for a setting to take effect

severity medium · reader's confidence high · maintainability

**Evidence.** application/src/core/pipeline.py:403-2554 holds 94 methods and about 60 attributes: GPU orchestration, YOLO call, motion worker, dedupe and gates, exclusion-mask API (1990-2034), height guard (1385-1451), slots (1140-1187, 1482-1580), belt (1197-1256), plate capture and foreground (1261-1372), smoother (1582-1669), stall keep-alive (1095-1138), OSC emit.

Settings take effect three ways:
- read every frame (`_sync_gpu_settings` 861-892, PoseRunner key)
- pushed by a dedicated setter (`set_motion_sensitivity` 1879-1883, `configure_identity_slots` 1140-1168)
- latched at lazy construction (`belt_backing`, `entry_min_travel_h` at 1496-1497)

`update_settings` (552-555) drops unknown keys silently, bypasses the setters, and has no caller. clahe_clip and gamma are read from the CPU `ImageEnhancer` (870-871). The pipeline writes the operator's `person_height_px` (1383, 1450). The L = 1 box EMA mutates the tracks in place before the slot layer reads them (1039-1050), so slot inputs depend on L.

Dead or duplicate code: `_enhance_gray_for_motion` (1686-1717, no caller); proxy methods `detect`, `_to_local`, `_to_letterbox`, `mask_stats_in_region` (the tracker calls four others, tracker.py:2922-3004); `roi_local=False` branches for the removed CPU path; two IoU functions (2212, 2521); near-identical `process` and `process_gpu_tensor` (application/src/core/gpu_pipeline.py:588-690, 692-787).

**Why it matters.** A fix for one field problem has to be made inside a 2500-line class where stages share mutable state, so side effects across stages are easy to introduce and hard to see in review. A setting changed by the wrong route silently does not reach the component that uses it, which becomes operator confusion on site.

**Suggestion.** Keep FrameProcessor as a short orchestrator and split along the existing seams: front end (GPU + YOLO + extract), detection filter as pure functions over (detections, geometry, motion view, tracks), output stage (slots, smoother, keep-alive, emit), scene evidence (belt, foreground, plate) and height estimator. Give settings one application path, such as per-key handlers invoked on change, and move clahe_clip / gamma into ProcessingSettings. Delete the dead code listed and merge the two GpuPipeline process bodies.

### detection-pipeline#10 — The YOLO tensor is always a padded square, so any non-square ROI or sensor crop spends inference on grey

severity medium · reader's confidence medium · performance

**Evidence.** application/src/core/gpu_pipeline.py:807-860: `_prepare_yolo_input` scales by `min(imgsz/w, imgsz/h)` and pads to imgsz x imgsz with 0.5. `ModelManager.imgsz` is a single int, and engines are exported and named for a square size (application/src/core/model_manager.py:64-81, 113-125, 208-214). `PoseRunner` already accepts any stride-32 shape (application/src/core/yolo_runner.py:110-111). The default sensor crop is square, 1528x1528 (application/src/core/config.py:119-122), so with no ROI the padding is zero.

**Why it matters.** YOLO is the largest item in the frame budget. For a 16:9 region about 44% of the tensor is padding; for a 3:1 wall strip about 67%. That compute could go to a shorter frame time or, more useful for far small dancers, to more pixels per dancer at the same cost (for example 1920x640 is fewer pixels than 1280x1280). The benefit depends on the ROI shapes used on site, which I could not determine from code.

**Suggestion.** If shows use wide ROIs, support rectangular static engines: derive (h, w) from the ROI aspect rounded up to stride 32, key engine files by both dimensions, and let `_prepare_yolo_input` pad only to that rectangle. Measure TRT time for 1280x1280 against the matching rectangle on the show laptop before committing.

### detection-pipeline#11 — Avoidable per-frame work on the main thread: mono carried as three channels, and height statistics recomputed at 20 Hz

severity low · reader's confidence medium · performance

**Evidence.** - application/src/camera/ids_camera.py:1539 and 1544-1548 build a BGR copy of the mono frame on every `read_gpu`; application/src/core/pipeline.py:1746-1748 then copies channel 0 back out for the motion feed.
- On the GPU the mono frame is expanded to three channels (ids_camera.py:1590) and run through rgb->ycbcr, a full clone and ycbcr->rgb (application/src/core/gpu_pipeline.py:389-420); for R=G=B these conversions add nothing.
- pipeline.py:1401-1420 with application/src/core/auto_height.py:43-52, 81, 91-99: every frame the 60 s follow window is re-binned and sorted in Python and three weighted quantiles are computed; the 10 s window is sorted twice (pipeline.py:1403, 1437).
- pipeline.py:2108 transforms the 30-point trail of every track each frame whether or not a preview is drawn.
- pipeline.py:2473-2492: the pairwise merge is O(n^2) Python calls.

**Why it matters.** My estimate, not measured: 2-4 ms per frame of memory traffic and conversions before the GPU stage starts, plus 1-2 ms of Python statistics once the 60 s window is full with two dancers. That is small against 50 ms, but it sits on the one thread that also runs YOLO, OSC and the GUI. The dedupe cost grows quadratically if a low confidence in the dark yields many detections.

**Suggestion.** Expose the mono8 frame from the camera and feed it to the motion worker directly, doing the ROI crop inside the worker. Keep one channel on the GPU through enhancement and letterbox and expand to three at the YOLO tensor. Evaluate the height guard at about 1 Hz. Build the trail lazily for the preview only. Cap or vectorise the dedupe.

### detection-pipeline#12 — The motion feed is left in flight when YOLO or extraction raises, and the worker is never shut down

severity low · reader's confidence high · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/core/pipeline.py:614-619 and 671-677 await the feed if the GPU stage raises ('never leave a feed in flight'). `_run_yolo`, `_extract_detections`, `_guard_person_height` and `_filter_duplicate_detections` (719-749) run before the await at 753-755 with no try/finally. The TRT size-mismatch `AssertionError` is raised exactly there and is followed by a model reload (application/src/runtime/main_loop.py:849-866). `_motion_executor` (pipeline.py:458) has no shutdown call.

**Why it matters.** The single worker serialises feeds, so the next frame is still correct. In the window after such an exception, though, main-thread code that touches the motion model (tracker reset, scale change, calibration reads) can run while the worker is mid-feed, and the feed's own exception is never observed. The window is narrow, but the invariant the code states is not actually held.

**Suggestion.** Wrap the body of `_run_yolo_and_track` up to the await in try/finally that calls `_await_motion_feed()`, and add a `close()` on FrameProcessor that shuts the executor down, called from the main-loop shutdown steps.

## tracker

### tracker#1 — The tracker has no time base: every lifetime, gate and decay is per processed frame, and a camera stall is invisible to it

severity high · reader's confidence medium · robustness

**Evidence.** application/src/core/tracker.py:330-348 builds the Kalman transition with `dt = 1.0` and fixed per-frame Q/R; `update()` (2340-2399) takes no timestamp. Per-frame constants: coast friction 0.9/0.5 (447-449), bridge friction and covariance x1.5 per frame (459-465), velocity clamp 0.67 heights/frame (474-475), displacement gate 0.475 heights/frame from the last measured position (1446-1453), expiry `time_since_update >= max_age` (3314), warm-up 15 frames, frozen-ghost 3 frames and 0.03 heights/frame (application/src/core/config.py:989-990, 1067-1071). The comments assume three different rates: 15 fps (config.py:175, 181), 20 fps (config.py:882, 1000), 25 fps (config.py:980). The neighbours already run on seconds with stall compensation (application/src/core/pipeline.py:1086-1099, 1400; application/src/core/auto_height.py:68-88). tracker.py contains no reference to the stall clock.

**Why it matters.** At 10 fps (dark, long exposure) the warm-up delay doubles to 1.4 s and a dancer covers twice the distance per frame against the same gates. After a USB stall the next frame is treated as one frame later: a dancer who moved more than 0.475 heights fails the displacement gate; within about 0.83 heights the force-update path rescues the id, beyond that a new id is minted and must warm up for 14 frames. Not checked: whether the camera delivers a burst of buffered frames after a stall, and the real FPS spread on the show laptop.

**Suggestion.** Pass elapsed time into `update()` (the pipeline already has the stall-compensated clock). A first step that leaves replays identical at nominal rate: compute k = dt / nominal dt, and for k > 1.5 run the predict and ageing step k times and scale the displacement gate and match threshold by k for that frame. Then move lifetimes (max age, dormant age, warm-up, bridge caps) to seconds converted at runtime, and add a replay test that decimates a recording to half rate and asserts the same ids.

### tracker#2 — Association gates are advisory: an unmatched detection is forced onto the nearest unmatched track regardless of the Mahalanobis, displacement and cost gates

severity high · reader's confidence high · paradigm

**Re-checked: confirmed.**

**Evidence.** tracker.py:2566-2636: a detection the Hungarian passes rejected is, if within the creation gate of the nearest track (0.55 heights, x1.5 away from the left/right edges; config.py:199, 295), forced onto it (`_try_force_update_unmatched_detection`, 2673-2708); otherwise any unmatched track within 0.95 heights takes it, first in list order rather than nearest (2717-2741). The docstring at 2748-2752 states that this overrides the cost and Mahalanobis gates and cites a field failure. `_forced_update_allowed` (1342-1352) vetoes only when own-height gates are active, the track is established, was fed last frame, and the global height is more than twice its own. Close-accept (2036) also accepts a gate-blocked 1e6 pair within 0.2 heights.

**Why it matters.** For a single dancer missed by YOLO on one frame, any false detection within about 0.8 to 0.95 heights is written into the dancer's track. With measurement noise of 2 px squared the Kalman position snaps to it, and `history[-1]`, the anchor for the next frame's displacement gate, becomes the ghost. The tuned gates only choose between competing assignments; they almost never reject. The in-code note 'lost for 13 s' (1345-1346) shows this already cost a field take, and the existing fix covers only the mis-scaled-height case.

**Suggestion.** Replace the three sequential rescue rules with one second-stage assignment over unmatched detections and unmatched tracks, with a single explicit gate in the track's own-height units and nearest-wins. Keep two hard vetoes: no rescue for a track fed last frame when the jump exceeds the displacement gate, and no rescue by a zero-keypoint or low-quality detection onto a skeleton-backed track. Log which gate was overridden.

### tracker#3 — `_merge_duplicate_tracks` deletes one of two real dancers in close contact and cancels occlusion ageing

severity high · reader's confidence medium · correctness

**Re-checked: confirmed.**

**Evidence.** tracker.py:3616-3688: any two established tracks with `time_since_update <= 1` (3631-3632) whose centroids stay within 0.3 x the match gate (about 0.285 heights; config.py:249-250) for 8 consecutive frames are merged; the lower-hits track is removed (3686-3688) with no state transfer and no dormant snapshot. There is no check that the two tracks were fed by two different skeletons on the same frame, which is the discriminator the newer takeover merge uses as a veto (3741-3751). An occluded track also qualifies: `_apply_fractional_occlusion_aging` (3230-3238) holds its `time_since_update` at 0 or 1 for roughly the first 20 occluded frames. After the merge, the second dancer cannot get a track while within the creation gate of the survivor: the detection is DUPLICATE_IGNORED or AMBIGUOUS_IGNORED (2608-2636). No unit test references this method.

**Why it matters.** In a duet, an embrace, a lift or one dancer passing in front of the other for 0.4 s at 20 fps destroys one identity even while YOLO sees both bodies. That dancer reappears only after separating by about 0.83 heights, under a new id, after a 14-frame warm-up. The occlusion-ageing rule (keep the hidden dancer ten times longer) is undone by this rule in the first second of the occlusion. I read the mechanism but did not replay a contact take, and the identity-slot layer may mask part of the effect.

**Suggestion.** Reset the pair streak on any frame where both tracks have `_frames_since_skeleton == 0` (two skeletons on one frame means two people), reuse the `_pair_cofed` veto, and exclude tracks flagged `_occluded`. Add tests mirroring test_tracker_takeover_merge.py: a co-fed pair never merges; an alternately fed pair does.

### tracker#4 — The Kalman filter is not acting as an estimator, and absolute-pixel constants break the 'person height is the single dial' model

severity medium · reader's confidence high · paradigm

**Evidence.** tracker.py:345-348: R = 2 px squared and Q = diag(0.25, 0.25, 2.5, 2.5, 5, 5) on a constant-acceleration model, while config.py:426-435 says YOLO centroid jitter is 10-50 px and that R is 'tuned for smoothing'. Consequences in code: the Mahalanobis gate needs its own noise term of 700 px squared (1146-1148), which makes it a fixed radius of about 107 px (square root of 16.27 x 700) for any converged track; a velocity clamp, NaN resets and friction (447-488); a position cost hedged across predicted, velocity-shifted and last-measured positions that counts velocity twice (1176-1188); velocity overwritten after a bridge (3128-3136) and zeroed after swaps (2556-2557); a separate EMA for output (572-574). The 'less trusted' motion measurements multiply R by 1.5 to 4 (2136, 3110-3120), giving 3-8 px squared, while the bridge inflates the covariance by 1.5 per frame (464), so the gain stays near 1. Tracker space is the letterboxed YOLO input on the GPU path (application/src/core/pipeline.py:333-337), so these pixel values change meaning with imgsz, ROI and camera distance. Other absolute values: 3.0 and 1.0 px/frame (1564, 779), aspect difference x 15 (825), 0.1 px/frame (197).

**Why it matters.** The filter differentiates noisy centroids instead of smoothing them, so its predictions are unreliable. That is the root reason for at least six compensating rules and for the gates being overridden (finding 2). The Mahalanobis radius is about 1.3 heights for an 85 px dancer, 0.2 heights for a 500 px close-up and 2.4 heights for a 45 px dancer, so behaviour shifts with the venue set-up even when the person height is right.

**Suggestion.** Behind a flag and under the replay goldens: a constant-velocity model, R = (0.07 x own height) squared for skeletons and (0.3 x own height) squared for blobs, Q from a maximum acceleration in heights per second squared, and a dt-aware transition. Then gate on the real innovation covariance and delete the 700 px squared term, the three-point position hedge and the velocity clamp. Add a scale-invariance test: multiply all coordinates and the person height by 2 and assert identical ids.

### tracker#5 — `time_since_update` carries three meanings; slowing occlusion ageing by decrementing it changes unrelated rules

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** tracker.py:3230-3238 slows ageing by undoing the predict increment, so an occluded track sits at 0 after each frame for about 10 frames, then at 1 for the next 10. Readers that take the value to mean 'was fed last frame': `predict()` applies coast friction, acceleration damping and warm-up decay only when it is above 0 (447-454), so an occluded track extrapolates its constant-acceleration state undamped for about 10 frames (the docstring at 1651-1652 describes that drift); the displacement gate (1446-1448) stays anchored to a stale last-measured position; `_detect_shadow_tracks` treats the track as freshly matched (3543-3550); `_merge_duplicate_tracks` (3632); the merge-frame count (2449-2452); and the upstream cross-validation bypass (application/src/core/pipeline.py:1960-1961, 2303-2306). The bridge also resets it to 0 (3140). Tracks created this frame are not in `matched_trk`, so 3193-3200 can flag a newborn track as occluded. The BUG-2 comment (3224-3229) records an earlier failure of the same encoding.

**Why it matters.** Every new rule keyed on this counter inherits side effects nobody intended, which is how one rule ends up undoing another. It also makes `t_miss` in the field log misleading for occluded and bridged tracks.

**Suggestion.** Give each track an explicit state (tentative, confirmed, coasting, occluded, bridged) and separate fields: frames since the last measurement (never decremented), a float age budget consumed at 1.0 or 0.1 per frame, and a fed-this-frame flag. Convert the readers one at a time under the goldens.

### tracker#6 — A live track re-acquired after a detection gap must re-warm from near zero, while a resurrected dormant id is emitted immediately

severity medium · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** tracker.py:450-454 decays the warm-up integral by 0.8 per missed frame down to 0; the cap is threshold + 5 (534-535), so a confirmed track stops being emitted after 7 missed frames and, after about 25 misses, needs 14 consecutive matches to be emitted again (`emission_gate`, 126-129). `_try_resurrect` restores the integral to the threshold for a previously emitted id (2318-2322), but there is no equivalent for an active track that kept its id through the gap; established tracks stay active for 135 frames (3288-3289).

**Why it matters.** For a still or slow dancer whose skeleton drops out for 1 to 6 s in the dark (no motion, so no bridge), the return is delayed a further 0.7 s at 20 fps or 1.4 s at 10 fps although the tracker never lost the id. A longer absence (track expired, then resurrected) comes back faster than a medium one. Added to the gap itself this can exceed the 2 s slot coast. The integral's hysteresis is described as replay-proven (config.py:992-996); I did not check whether this variant was among those tried.

**Suggestion.** For tracks with `_ever_reported`, restore the integral to the threshold when the re-match is a real skeleton within the close-accept distance (the resurrection logic applied to live tracks), and measure it against the false-positive-burst scenarios that motivated the decay.

### tracker#7 — Synthetic motion-blob detections earn full skeleton credit, contradicting the documented bridge policy

severity medium · reader's confidence medium · correctness

**Re-checked: partly.**

**Evidence.** tracker.py:2859-2862 turns a cold blob into a detection with zero keypoint confidence. When it matches, `DancerTrack.update()` (531-536) adds a hit, +1.0 to the warm-up integral and a 1.0 credit to the intermittent-path window, the same as a YOLO skeleton; only R is multiplied (2132-2138). The bridge path deliberately gives +0.4 and no window credit (3143-3163; config.py:879-882, 1000-1002: 'bridge credit here incubates ghosts'). So a blob-only track is established after 15 frames (843-845: first pick in the cascade, three times the lifetime, immune to shadow kill), ranks in the report cap by `hits` that the comment calls 'cumulative real matches' (3415-3426), and never trips the hit-rate ghost rule (3322-3323). Only the frozen-ghost gate (126-133) stands between a moving non-person blob and emission after about 0.7 s. Related: the blob-persistence reset at 2799-2801 is unreachable because `update()` skips fusion when there are no blobs (2367-2368), so the 3-frame persistence requirement can be met by non-consecutive frames.

**Why it matters.** A moving object YOLO never recognises (a rope, a banner, a light sweep, a technician's shadow) can become a confirmed, established 'dancer' that outranks real tracks in pass 1 and in the report cap. Not checked: how strict the upstream cold-blob gate and the downstream slot reputation rules are.

**Suggestion.** Split `hits` into skeleton hits and blob hits; credit synthetic matches like bridge frames (+0.4, no window credit); base `is_established`, the report cap and the ghost rate on skeleton hits; clear `_motion_blob_cells` on blob-free frames.

### tracker#8 — Resurrection is unreachable where it matters most, and its 'shape' gate is an absolute-position gate

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** tracker.py:2581-2590: `_try_resurrect` runs only when the detection is beyond the creation gate of every active track, and that gate is 1.5 times larger away from the left/right edges (2760-2761), which is where the centre-exit snapshots it is meant to recover live. Inside it, the position gate is widened up to 2.5 times for recent, occluded or centre-exit snapshots (2239-2249), but the keypoint check at 2283-2288 compares raw image coordinates (not centroid-normalised as in 1199-1202) against 0.5 to 0.8 x the base gate. With three or more shared keypoints the widening is cancelled; with fewer, including zero-confidence blob detections, there is no shape check and the widest gate applies. `projected_position` (195-207) uses a velocity already damped by 0.9 per frame during the coast (447-448), so the projection is effectively the last measured position.

**Why it matters.** A dancer who re-emerges next to their partner cannot recover their id until about 0.83 heights away. Well-seen detections face the tightest gate and poorly seen ones the loosest. Each miss is a new id plus a 14-frame warm-up.

**Suggestion.** Offer dormant snapshots as candidates in the unmatched-detection stage before the creation-gate decision when the snapshot was occluded or exited in the centre; compare centroid-normalised shape; require a real skeleton to resurrect an emitted id; drop the velocity projection or store the pre-coast velocity.

### tracker#9 — The rules with the most interaction have no isolated tests, and tracker parameters are module globals

severity medium · reader's confidence high · testing

**Evidence.** The unit tests reference only `_collect_confirmed_tracks`, `_try_resurrect`, `_merge_takeover_duplicates`, `_apply_fractional_occlusion_aging`, `_apply_motion_bridge` and `_lazy_bridge_with_motion` (application/tests/test_tracker_*.py). Nothing calls `_compute_cost_matrix`, `_combine_assignment_cost`, `_resolve_unmatched_detections`, `_detect_shadow_tracks`, `_merge_duplicate_tracks`, `_is_suspicious_merge_candidate` or the presence and frame-difference tiers. About 100 constants are imported by name at module load (tracker.py:13-78) and about 160 lines carry bare numeric literals, so a rule can only be varied by monkeypatching. Track ids come from a class-level counter (213, 225-226) that `reset()` zeroes (1118) and `_try_resurrect` rewinds (2310-2314). The replay harness applies tracker keys through its own list (application/tests/replay.py:305-316); `tracker_own_height_gates` is honoured only there, not in application/src/app.py:1436-1458, despite the comment at tracker.py:947.

**Why it matters.** Coverage of the association and lifecycle rules rests on the golden replays under tests/golden (three scenario files plus an emitted-stream set, which I did not open). A threshold change can only be judged by re-scoring the corpus, which is how the rule conflicts in findings 2, 3, 5 and 8 went unnoticed. Replay and live can also diverge on a config key.

**Suggestion.** Introduce a `TrackerParams` dataclass passed to `DancerTracker` (defaults from config) and a per-tracker id allocator. Add invariant tests on synthetic trajectories: ids are unique; two tracks fed by distinct skeletons never merge; half-rate and double-scale invariance; a ghost at 0.6 heights does not move a fed track. Share one function that applies tracker config keys between the app and the replay harness.

### tracker#10 — Three swap correctors are off by default, documented as net-harmful, and contradict the cascade they sit on

severity medium · reader's confidence high · maintainability

**Evidence.** tracker.py:1499-1972 (about 475 lines) plus the deferred-update machinery (874-895, 2468-2557) exist for `_check_occlusion_cascade_swaps`, `_check_merge_direction_swaps` and `_check_two_opt_swaps`; config.py:441-454 records that they false-fire and that the corpus scores better without them. Defects if re-enabled: the cascade swap's precondition compares against all tracks (1517), the inflation the merge-frame logic was fixed to avoid (2443-2453); the 2-opt cost uses all tracks for crowding and separation (1835-1845) while the Hungarian matrix uses the pass subset (1421-1435, 1472-1473), and since Hungarian is already optimal within a pass, 2-opt can only act across passes (undoing 'established first') or on that inconsistency; the direction swap uses horizontal velocity only (764-791, 1717-1731); every swap zeroes velocity and acceleration (2556-2557). `_cascade_suppressed` is not cleared by `reset()` (1106-1119). The tests cover only the switch (application/tests/test_tracker_swap_correctors.py:68-87).

**Why it matters.** A per-scene key (`tracker_swap_correctors`) lets a technician or a tuning script turn on code known to suppress the real track, and the dormant paths add state and reading cost to every change in the matching phase.

**Suggestion.** If no project config enables the key, delete the three methods and the deferral (git keeps them). If duo contact needs correction, do it once in the identity-slot layer, which already owns the stable ids.

### tracker#11 — The assignment cost mixes pixels with a 0-1 IoU term and is compared with a pixel threshold

severity low · reader's confidence high · correctness

**Evidence.** tracker.py:1258-1277 returns 1 - IoU, in [0, 1]. `_combine_assignment_cost` (1284-1319) adds it with weight 0.10 or 0.05 to position, keypoint and size costs measured in pixels (tens of px), so the IoU term contributes at most 0.1 while its weight scales every other term by 0.90 or 0.95. The sum is compared with gate + speed + time bonus in pixels (1354-1362, 2036), after crowded-zone multipliers of 1.3 and 1.5 (1310-1312). The trajectory term adds aspect difference x 15 in fixed pixels (825).

**Why it matters.** `TRACKER_IOU_WEIGHT` does nothing except loosen the cost gate by 10%. The effective distance gate depends on the weight mix (about 1.24 heights for a position-only match of a stationary track, not the 0.95 the constant suggests), so any re-weighting silently moves the gate, and a tuning harness sweeping these weights would partly be fitting noise.

**Suggestion.** Normalise each term by the track height before blending (dimensionless cost and threshold), or drop the IoU term. Add one unit test per weight asserting that changing it changes a ranking.

### tracker#12 — In the motion bridge, the weakest tier (presence) pre-empts the frame-difference tier

severity low · reader's confidence medium · robustness

**Evidence.** tracker.py:2974-3000: when no MOG2 blob is found, a presence ratio on the raw mask with no coherence check (2982-2983; threshold about 0.8% of the box at the default sensitivity, 1053-1054) is tried before frame differencing (3002-3030), and the loop continues on success. `_apply_motion_presence_bridge` returns without bridging once the 15-frame streak cap is exceeded (3067-3070), but the caller still logs MOTION_BRIDGE_PRESENCE, sets `bridged_any` and skips the frame-difference tier. Presence frames also increment `bridge_frames` (3072), which triples the Mahalanobis gate (1158-1159) and counts toward the 80-frame cap (2894).

**Why it matters.** In a noisy dark image a track can sit in 'presence' on sensor speckle: it gets no position update, and after 15 frames it gets neither a bridge nor a chance at the stronger tier that returns a position. Not checked: how often raw-mask noise exceeds the threshold on site.

**Suggestion.** Try frame differencing before presence, have `_apply_motion_presence_bridge` return whether it bridged, and fall through to the next tier when it did not.

## identity-output

### identity-output#1 — Two stacked identity systems with split authority; the output layer is a second tracker that cannot feed back

severity high · reader's confidence high · paradigm

**Evidence.** application/src/core/identity_slots.py:3-7 states the purpose: hide the tracker's id churn (~30 new ids/min). :846-886: a slot follows its tracker key blindly; only a jump over 2 h unbinds it (:859-865), so tracker id swaps go straight to OSC. :920-949 and :957-990: re-identification is Hungarian on distance alone, so none of the tracker's shape, direction or swap logic informs it. :867-870: a track the tracker is only predicting is ignored and the slot coasts by itself. application/src/core/pipeline.py:1513-1520 reads tracker.tracks to keep following tracks the tracker's frozen-ghost gate hid on purpose (application/src/core/tracker.py:130-132); identity_slots.py:1016 emits them as 'live' for up to 3 s. Duplicated mechanisms: confirmation (tracker.py:107-133 vs identity_slots.py:532-558), cap (tracker.py:3421-3436, 6 by hits, vs max_dancers 2), duplicate removal (tracker.py:3522, :3616, :3690 vs identity_slots.py:899-904, :1218-1260), memory (max_age 45 frames and dormant 150 frames vs coast_s 2 s vs last_key kept indefinitely, :906-918), smoothing (Kalman, EMA 0.5, One-Euro). Belt and foreground measurements exist only in the slot (:1027-1046); the tracker never sees them.

**Why it matters.** For OSC the slots are authoritative on lifetime and re-identification, the tracker on frame-to-frame association. Every lost id has to be diagnosed across two state machines and two sets of constants. Fixes pile onto the top layer (belt, foreground, weak, smart hold, static guard, stall hold, all dated within days in the comments) because it cannot correct the layer below. Entry delays and hold times stack, and the two layers hold opposite policies for a still dancer without a skeleton.

**Suggestion.** Decide the authority explicitly. With N small and fixed per show, the slot is the natural single estimator: N persistent targets that associate detections, motion blobs, belt and foreground directly, with one gate, one confirmation rule and one hold horizon, and the current tracker reduced to short-horizon tracklets. A first step that leaves the goldens alone: a typed report from tracker to slots (reported / hidden / occluded / coasting, with the reason) instead of tracker.tracks and private attributes, and remove from the OSC path the tracker features the slots supersede.

### identity-output#2 — A dancer occluded by, or in contact with, the partner loses its id by design

severity high · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/core/identity_slots.py:899-904 is_duplicate: a candidate within 0.5 h of another emitting slot can never bind. It is applied to same-track re-entry (:912-913), rebind (:936, :943) and entry (:976, :984). :867-870: a bound track the tracker is only predicting (occluded) puts the slot into its own coast. :1061-1062: that coast ends on a flat timer (1.5-2 s). :1235-1238: a coasting, belt or foreground slot within 0.25 h of a live one is dropped after 0.5 s. :1239-1240: two non-live slots that close are merged at once, with no hold. A merged slot forgets its key (:1213), so the dancer returns through full entry (3 consecutive reports, 12 hits). Not checked: how often this fires on real duo takes. The comment at :280-282 reports that looser merging dropped real dancers; it says nothing about holding instead.

**Why it matters.** The stated field pain is that an id missing from OSC makes TouchDesigner drop its video (identity_slots.py:4-5, camera_stall.py:6-7). Lifts, embraces and crossings are exactly when YOLO and the tracker fail, and the code resolves that ambiguity by removing one id and refusing to re-acquire it until the dancers are 0.5 h apart. The tracker still holds the occluded track and the slot still remembers its key, yet the id leaves the wire.

**Suggestion.** Add an explicit contact hold: while a slot without a measurement predicts inside the contact zone of a live slot, do not run its expiry (give it a long, separate cap), emit it riding the live slot with its last relative offset, and give it its own state. Allow a rebind inside the zone when both the candidate and the live slot's track have a fresh skeleton, the two-bodies rule already used at :1242-1248. Give the both-non-live merge the same hold as the other branches. Measure on the duo replays: seconds with an id missing while both dancers are on the wall.

### identity-output#3 — The empty-wall foreground acts as a hard, untimed, unlogged veto computed on the raw image with a noise level frozen at capture

severity high · reader's confidence low · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/core/identity_slots.py:543-546: a candidate whose box holds under 10 % foreground cannot rebind or enter. Only a counter is incremented: no SLOT_EVENT, no timeout, no override by YOLO confidence. application/src/core/foreground.py:252-253: the mask is |raw - gain x plate| > max(2 grey levels, 4 x sigma x sqrt(gain)), with sigma measured once at capture (:71-72) on the raw un-enhanced ROI (application/src/core/pipeline.py:796-797), while YOLO sees the CLAHE/gamma-enhanced image. pipeline.py:1453-1468: slots held by foreground alone are not in the plate's protect list, so foreground.py:292-318 blends a still dancer YOLO cannot see into the live plate in roughly 25 s. The veto is skipped on continuation (:846-886) and same-track re-entry (:906-918), so it does not remove a ghost that already holds a slot. Not checked: the foreground share of real dancers on the darkest takes; nothing in the code records it.

**Why it matters.** In darkness, with a far dancer, or after the light has dropped, raw contrast against the wall can fall under the threshold while the enhanced image still gives YOLO a skeleton. That dancer then gets no id for as long as it lasts. The preview shows a tracker box without a D-ball, and neither the log nor the operator surface says why. A dancer absorbed into the plate while hanging still is vetoed the same way until they move.

**Suggestion.** Make the veto evidence with an escape: a candidate with several consecutive fresh skeletons above the live confidence overrides it after about 1 s. Log each veto as a SLOT_EVENT with the measured share and show 'vetoed by empty wall' in the ids readout. Protect foreground-held slots from the plate update (they already have a 30 s cap). Record the minimum and median foreground share of bound dancers per session, so the 0.10 threshold can be checked against the night takes.

### identity-output#4 — The 5 s stall bridge is pre-empted by the 2.5 s IDS reconnect timeout

severity medium · reader's confidence medium · robustness

**Evidence.** application/src/core/config.py:136 sets CAMERA_STALL_MAX_S = 5.0 and application/src/core/camera_stall.py:62 credits a silence up to that. application/src/runtime/camera_controller.py:77 and :148-156: _ids_stream_timed_out() is true once the last acquired frame is older than 2.5 s. application/src/runtime/main_loop.py:691-696 checks it before every read, calls _stall_not_live() (camera_stall.py:75-81, forget()), closes the camera and schedules a reconnect. After forget(), on_frame credits nothing (:60): the keep-alive stopped at 2.5 s and the hold clocks ran through the whole outage. Not checked: whether the IDS layer refreshes last_frame_time during a stall. tests/test_camera_stall.py exercises the 5 s cap on the clock alone.

**Why it matters.** The stall hold exists because a 1.7 s stall cost ids. A stall of 2.5 to 5 s, or any reconnect, still costs every moving dancer its id: on the first frame back, a slot whose track is not matched immediately has exceeded its hold and is dropped (identity_slots.py:1061-1062). The constant in config.py describes a guarantee the loop does not give.

**Suggestion.** Decide which one wins. Raise the reconnect timeout above the stall cap if stalls recover unaided, or lower the cap to what the loop allows. Better: treat a reconnect outage up to a bound like a stall (keep last_frame, keep the keep-alive running, credit the silence on the first frame after reopen), since a 4 s reconnect and a 4 s stall are the same event for the receiver. Add a main-loop test that uses the real timeout.

### identity-output#5 — Foreground evidence switches off silently mid-show; its status line is refreshed only on operator actions, and the remedy needs an empty wall

severity medium · reader's confidence high · ux

**Re-checked: confirmed.**

**Evidence.** application/src/core/foreground.py:257-261: when more than 25 % of the ROI differs, the frame is 'plate stale' and invalid; stale_frames (:184, :258) is read nowhere in application/src. application/src/core/identity_slots.py:788 then drops every foreground rule for that frame. application/src/core/pipeline.py:1269-1283 computes plate_status on demand, but application/src/app.py:1857-1909 publishes the text only on project load, toggle and capture, and the Go-Live check reads it once (:2285). application/src/runtime/main_loop.py:1352-1386 alerts only when the median brightness ratio leaves [0.67, 1.5], so a nudged camera or a local light change raises nothing. Stale frames are never blended (foreground.py:263-265), so a lasting change above 25 % never heals. foreground.py:126-127 assumes two dancers cover under 10 % of the ROI.

**Why it matters.** The hold behaviour of the show depends on plate validity: a still dancer YOLO loses is held up to 30 s with a valid plate and 1.5-2 s without, and ghosts are vetoed or not. That flips per frame at a threshold while the screen still says 'empty wall: ready'. The only remedy offered, Calibrate on the empty wall, is not available once dancers are on it.

**Suggestion.** Publish plate validity from the ops tick (share of valid frames over the last 10 s) and alert when it stays low. Use stale_frames: after some seconds stale with no slot emitting, re-base the live plate automatically, since the wall is empty by the slot layer's own account; with dancers present, re-base outside the protected boxes. Check the 25 % limit against the tightest ROI in use.

### identity-output#6 — The empty-wall YOLO check can only step the enhancement down, and a failed run pins the weakest setting for later Calibrates

severity medium · reader's confidence medium · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/empty_wall.py:159-170: when every rung shows a ghost frame, the result is the last (weakest) rung. application/src/runtime/calibration_flows.py:510 applies it and :530-531 stamps gamma and CLAHE as calibrated. On the next Calibrate, calibration_flows.py:249-250 takes the current enhancement as the one to keep whenever 'gamma' is stamped; empty_wall.py:121-125 tries it first and :160-163 keeps it when it passes. calibration_state is saved and loaded with the project (application/src/app.py:1307, :1323-1324). The check cannot tell a person from a ghost: one detection at 0.8 x confidence in any of 120 frames fails a rung (empty_wall.py:152-159). Read from both ends, not run.

**Why it matters.** A Calibrate with someone in view (a rigger, a dancer warming up) fails every rung and leaves gamma 0.8 with CLAHE at or under 1.0. The toast says to remove the object and calibrate again; doing so keeps the weak setting, because the weakest rung passes on a clean wall. Low-light recall, which is what the enhancement is for, stays reduced until someone moves the sliders by hand.

**Suggestion.** On a not-clean result, restore the enhancement the check started from, do not stamp it, and say so. Pass the keep-first candidate only when the previous check ended clean (store that flag with the stamp). When a detection sits above the live confidence on most frames of every rung, report 'someone is on the wall' instead of stepping down.

### identity-output#7 — The OSC stream has no frame boundary and no end-of-stream, and the wire differs from what the code documents

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/osc_output.py:114-143 and :181-185: one frame is 1 + 4N separate datagrams (count, then centroid, bbox, velocity, keypoints per dancer); the bundle builders imported at :17-18 are unused; there is no frame number or timestamp. :152-156 and application/src/gui_builder.py:1127-1128 document the state as live / belt / coasting / lost, but application/src/core/identity_slots.py:99-105 also emits 'fg' (the 30 s hold state) and 'weak'. application/src/core/pipeline.py:1181-1187: a slot never used reports an age equal to the clock value, because state_since defaults to 0. pipeline.py:1550: with slots on, the bbox is centred on the keypoint centroid, not on the body box. :1541-1542: a held slot sends its last skeleton with the original confidences. No terminal frame: application/src/app.py:2227-2232 (entering STANDBY only runs readiness), application/src/runtime/main_loop.py:671-696 (camera loss), camera_stall.py:87-90 (keep-alive ends at the cap); /walldance/clear goes out only on a tracker reset (app.py:1983-1991). main_loop.py:813-816: Calibrate and plate capture run the whole pipeline in STANDBY, so OSC is emitted there, at the lowered confidence of the wall check.

**Why it matters.** The receiver drops video on id loss, so the exact loss semantics are the product. Today 'gone' is either a count without the id or plain silence, depending on why output stopped, and a TouchDesigner patch written from the tooltip will not recognise 'fg'.

**Suggestion.** Send each frame as one OSC bundle with a frame counter. Define the state vocabulary in one place and derive the tooltip and docstring from it; send age 0 for a slot never used. Emit one explicit empty frame (count 0, states lost) on STANDBY, camera loss, stall cap and exit, or write into the contract that silence means hold. Gate OSC on RUN, or tag calibration frames.

### identity-output#8 — A large share of this layer is switched off or unreachable on the show path but still wired into it

severity medium · reader's confidence high · maintainability

**Evidence.** RTS path: application/src/gui_builder.py:1032-1034 says the L slider left the GUI because L>1 desynced OSC; L is not saved with the project, yet application/src/core/pipeline.py:1065-1071 and :1582-1633 still run it when SetOutputSmoothing arrives (application/src/app.py:871, application/src/services/remote_api.py:65). With slots on it is wrong in three ways: its input centroid_raw is the slot position fed by the tracker's EMA (pipeline.py:1556), against the no-cascade rule in application/src/core/output_smoother.py:6-9; keypoints are shifted twice (pipeline.py:1539-1541, then :1623-1630), leaving the skeleton off the centroid by the One-Euro lag; an empty release sends nothing (:1069), so the receiver never gets count 0. Slots: weak measurements (application/src/core/identity_slots.py:207-213, :1137-1172; pipeline.py:1525 never passes them), entry plausibility (:620-635), entry travel / person evidence / reappearance (:560-618; entry_min_travel_h has no config key), rebind-anywhere and hidden yield (:960-967, both set to 99 s), static release, raw filter inputs, params_from_config (:1474, tests only). application/src/core/marker_model.py (562 lines) is imported by no module under application/src.

**Why it matters.** About 30 of 83 SlotParams and three whole mechanisms do nothing in a show, but each is a branch in the 330-line update() that a maintainer has to reason about. The RTS path can be switched on remotely during RUN in a combination I found no test for (L>1 with slots on).

**Suggestion.** Take the L>1 branch out of _emit (keep OutputSmoother for the replay tools and let them feed it), or refuse L>1 while slots are on. Move the experiments the replays rejected into a separate params object or delete them; the replay numbers in the comments are the record. Move marker_model.py next to the tools that use it.

### identity-output#9 — Every gate is scaled by the bounding-box height, which changes with pose

severity medium · reader's confidence low · robustness

**Evidence.** application/src/core/identity_slots.py:1465 takes h = bbox height. It scales the jump check (:857-861), the rebind gate (:931-934), the duplicate radius (:902), the merge distance (:1228-1230), the static spread (:664), the foreground box (:544) and the One-Euro speed (:1091-1092). The tracker already keeps a pose-independent size (application/src/core/tracker.py:742-748, own_height from the torso) and the pipeline maintains person_height_px. Not checked on footage: how far the box height falls for horizontal or foreshortened bodies on the wall.

**Why it matters.** A dancer running along the wall or lying across it has a box perhaps a third as tall as when upright. Gates, duplicate radius and smoothing then shrink by the same factor for the same body. The rebind gate after an id churn is 1 h plus 2 h/s, which a horizontal dancer at normal speed can outrun; the slot then expires and the dancer re-enters under whichever id is free.

**Suggestion.** Carry a body-size scale on the candidate (the tracker's own_height, else person_height_px, else max(w, h)) and use it for every distance gate and for the One-Euro normalisation; keep the box height for the box output only. Replay the duo and wall-hang takes with both scales.

### identity-output#10 — Why a dancer has no id cannot be read from the screen or the logs, while the operator panel carries developer switches

severity medium · reader's confidence high · ux

**Evidence.** application/src/core/identity_slots.py:545, :549, :552, :556: rejections (foreground veto, entry waiting for travel, static block, implausible entry) only increment counters (:473-475) that nothing under application/src reads; _event (:691-692) is called for binds, drops, jumps and yields only. application/src/runtime/main_loop.py:1169-1184: the readout shows slot states and belt status. identity_slots.py:1016: a slot following a track the tracker hid is reported as 'live'. application/src/gui_builder.py:1107-1159: Smart hold, Ignore static figures and Box clamp say 'Leave it ON'. The IR belt defaults on (application/src/core/config.py:325) and runs gated queries every frame plus a global pass every 10 frames (application/src/core/pipeline.py:1223-1224, :1238) whether or not anyone wears a belt.

**Why it matters.** The person on site is not the developer. When a dancer shows a T-box but no D-ball, their only recourse is to change Hold or flip switches whose effect they cannot see, and a remote session reading the logs cannot tell a veto from a warm-up.

**Suggestion.** Log one SLOT_EVENT when a candidate's rejection reason changes (rate-limited per track) and show the best unbound candidate with its reason in the ids readout, for example 'T41 waiting 5/12 hits' or 'T41 vetoed by empty wall'. Add a 'hidden' state distinct from live. Move the leave-ON switches to Advanced or the project file. Make the belt opt-in per project unless belts are standard kit.

### identity-output#11 — The slot layer claims seconds, but about ten thresholds are frame counts tuned at 20 fps and the motion step is clamped

severity low · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/identity_slots.py:249-254 (consecutive reports, hits, skeleton age commented as '5 s' and '3 s'), :285, :304, :333-334 ('~1 s'), :351, :822 ('last second' = 20 samples); application/src/core/pipeline.py:1465; application/src/core/foreground.py:133 (plate update every 10 frames); application/src/core/config.py:341 ('~9 s at 20 fps'). identity_slots.py:794-796 clamps dt to 0.25 s and :733 divides the displacement by that clamped dt, so the first frame after any gap longer than 0.25 s yields a slot velocity several times too high; that velocity feeds the edge-exit test that cuts the hold to 0.3 s (:1128-1133). The jump allowance (:859-861) widens only for credited camera stalls, not for a gap the main loop made itself.

**Why it matters.** At 10-12 fps (dark scene, hot laptop) the anti-ghost windows double in wall time and entry takes twice as long. After a stall or a UI freeze, a moving dancer near a border can be given the exit hold, or be unbound as a teleport.

**Suggestion.** Pass the frame period into update() and express the skeleton-age and streak thresholds in seconds. Divide by the unclamped motion dt, or skip the velocity update after a long gap. Widen the jump check by the real elapsed motion time, not only by credited silence.

## motion-calibration

### motion-calibration#1 — No way to follow a light change while dancers are on the wall: light-coupled values are frozen and the only response is an alert whose remedy needs an empty stage

severity high · reader's confidence high · robustness

**Re-checked: partly.**

**Evidence.** application/src/core/ops_monitor.py:233-234 gives the remedy text ("Calibrate on the empty wall: live if the stage can be cleared, else play this lighting's empty take"); :502-522 fires the alert once per episode. application/src/core/config.py:351-359 notes the night project's light fell about 3x. application/src/runtime/calibration_flows.py:203-295 shows Calibrate is one monolithic sequence and the only entry point. Most of it is already robust to a dancer minority: the servo statistic is the ROI median (application/src/core/calibration.py:168-202), the sweep scores the median grid tile (:835-862), noise sigma is a median over pixels (:886-892). Only the plate capture and EmptyWallCheck need an empty wall. Separately, application/src/core/empty_wall.py:119-125, 159-163 with calibration_flows.py:247-250 try the incumbent gamma/CLAHE first and keep it whenever YOLO sees nobody, a test a too-weak enhancement always passes. application/src/runtime/camera_controller.py:411-444: any servo or manual write switches camera auto exposure/gain off; nothing re-drives exposure in RUN.

**Why it matters.** Dusk to night, or an IR lamp added or failing mid-show, changes the image by a large factor. YOLO confidence, the absolute-DN frame-diff thresholds and the MOG2 low-light switch all shift, and the technician gets one alert with no action available short of stopping the show or switching the source to playback. Because the incumbent enhancement is kept when it passes the empty-wall test, a re-Calibrate after the scene got darker cannot raise gamma/CLAHE; in practice the enhancement only moves down across re-calibrations.

**Suggestion.** Split Calibrate into two procedures. (a) "Relight": servo, gamma re-seed and var/scale sweep, allowed with dancers present, rate-limited, and offered (or auto-run within bounds) when the existing light_ratio leaves its band. (b) "Empty wall": plate plus YOLO check. Store the wall brightness at which the incumbent enhancement passed and keep it only when the current brightness is within a band of that; otherwise rescale from the measured light ratio.

### motion-calibration#2 — Frame-diff is the declared ghost-rejection signal but has no calibrated parameter: fixed DN thresholds on the full-resolution, gamma-lifted gray

severity high · reader's confidence medium · paradigm

**Evidence.** application/src/core/config.py:916-923, 965-971 call frame-diff "the ghost killer"; theta_m = 0.02 was "tuned on residence1-solo". application/src/core/motion_detector.py:520-529 defaults the threshold to 15 DN; application/src/core/pipeline.py:1808-1809 and 2344-2345 use that default; application/src/core/tracker.py:3004-3011 uses 6 DN (config.py:871-876). motion_detector.py:83-84 sets `_diff_scale = min(1.0, scale*2)`, which is 1.0 at the default scale and is never updated by set_scale (:89-96), so the diff runs un-blurred at full ROI resolution on the gamma-lifted gray (pipeline.py:1820-1837). application/src/core/calibration.py:886-892 measures noise sigma on every Calibrate but it is "diagnostic only" (:389-392). Nothing in application/src/runtime/calibration_flows.py sets `crossval_motion_min_ratio` (pipeline.py:124) or the DN thresholds; only config load and the expert known-N tune can (application/src/app.py:1466-1467). Estimate, not measured, assuming white noise: pure noise reaches a 2 % fraction above 15 DN at a pixel sigma of about 4.6 DN (about 2.3 at the calibrator's 0.5 scale); at the calibrator's own "noisy" threshold of sigma > 4 (config.py:666) it is about 18 %.

**Why it matters.** On noisy dark scenes the "moving now" test is satisfied by sensor noise in any box, so the crossval gate and the cold-blob gate pass everything and ghost rejection silently falls back to YOLO confidence. On quiet scenes a fixed 15 DN can miss a dim, far dancer. The calibration spends its effort on MOG2, the secondary signal, and nobody is told when the primary one is saturated.

**Suggestion.** In the same empty-wall window, accumulate the background frame-diff fraction per grid tile at production resolution for a few thresholds. Pick the smallest threshold whose background fraction is at most theta_m/4 (or set it as k times the measured diff sigma, as FgParams does). Persist it per lighting profile, add a "frame-diff saturated" verdict next to var_saturated, and run a 1 Hz check of the diff fraction outside track boxes to raise an alert when the floor rises.

### motion-calibration#3 — Calibrate is not transactional: no stage or RUN precondition, partial state after cancel, weakest enhancement kept when a person is in view, plate written before the operator saves

severity high · reader's confidence high · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/runtime/calibration_flows.py:227-241: the only preconditions are model loaded and frames flowing; there is no system-state check (the Auto-tune path has one, application/src/app.py:2442-2446) and application/src/services/remote_api.py:79 exposes StartCalibration. calibration_flows.py:210-226: cancel does not restore the exposure/gain the servo already wrote (:351-358) nor the seeded gamma (:322-330). application/src/core/empty_wall.py:164-166 with calibration_flows.py:509-512, 533-536: if YOLO sees a person on every rung (a rigger or dancer in frame), each rung fails at its first observed frame and the weakest rung (gamma 0.8, CLAHE down to 1.0) is applied. calibration_flows.py:457-461 with app.py:1890-1896: the plate capture starts before the wall-check verdict and is written to plates/latest.npz immediately, independent of the "Save to project" choice (:555-557); there is no config autosave. calibration_flows.py:496-497: live confidence is lowered to 0.8x for the whole check, also in RUN.

**Why it matters.** For a technician who is not the developer, one press with somebody in frame, or mid-show, trades a working enhancement for the weakest one within seconds and overwrites the plate with a contaminated one. A cancel leaves exposure and gamma changed. After "Keep session only" followed by a restart, the old exposure/gamma load together with the new plate.

**Suggestion.** Snapshot exposure, gain, gamma, CLAHE, var, scale and plate path at start and restore them on cancel or failure. Treat "person on every rung including the incumbent" as "stage not empty": abort, restore, discard the plate. Commit the plate in the same Save as the config (hold it as pending until then). Require STANDBY or an explicit confirmation when in RUN.

### motion-calibration#4 — The motion model's input is tied to the YOLO enhancement gamma, and the final gamma is chosen after var, scale and noise were measured

severity medium · reader's confidence high · paradigm

**Evidence.** application/src/core/pipeline.py:1773-1788, 1820-1837: the motion gray is the enhancer's gamma LUT applied to raw. MotionModel's own `fixed_gamma` stays 1.0 (application/src/core/motion_model.py:64; pipeline.py:436-437) although its contract says the gray is "decoupled from the display CLAHE/gamma path" (motion_model.py:27-32). Order in application/src/runtime/calibration_flows.py: seed gamma (:322-330, 364) -> window measuring var x scale and sigma (:370-394) -> apply var, scale, CLAHE (:418-429) -> cap gamma (:434-442) -> wall check tries the incumbent, then a downward ladder (:479-512). A gamma change does not reset MOG2 (application/src/app.py:1637-1641). The low-light regime keys on the post-gamma mean (application/src/core/motion_detector.py:204-209, 269-273).

**Why it matters.** The applied var/scale were measured under a gamma that is usually not the one kept, so the pair in use was never observed together. A decision made for YOLO reasons re-parameterises MOG2 and frame-diff. A large gamma step (ladder, slider, profile switch) shifts every pixel and can flood the mask for about 100 frames at learning rate 0.001. During that flood every lost track gets a full-box "silhouette" centred on its prediction (motion_detector.py:433-476; I did not read the tracker's remaining gate logic past tracker.py:3038).

**Suggestion.** Give the motion model its own input: raw gray, or a `motion_gamma` fixed once per calibration through the existing MotionModel.fixed_gamma and persisted separately. Then order the flow servo -> YOLO enhancement check -> motion sweep last, so the sweep sees the final signal. Noise-relative thresholds would remove the need for the lift.

### motion-calibration#5 — The var x scale sweep scores a different MOG2 than production runs, and a hard low-light switch then doubles the result

severity medium · reader's confidence medium · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/calibration.py:734-739, 858: candidates use history = 90 and OpenCV's automatic learning rate. Production passes learningRate = 0.001 (application/src/core/motion_detector.py:57-62, 275; application/src/core/config.py:840-842), where the per-mode variance adapts with a time constant of roughly 1,000 frames. So "MOG2 self-normalises" (config.py:608-610) holds for the sweep but not for production in the minute after each reset (scale change on apply, application/src/runtime/calibration_flows.py:421-422; tracker reset, application/src/app.py:1983-1988). calibration.py:849-855 only resizes, while production blurs first (motion_detector.py:192-215), despite the comment "exactly as the production motion model would" (calibration.py:840-842). calibration.py:859 counts mask == 255 only, while every live consumer counts >= 127 (config.py:850, 909; application/src/core/pipeline.py:974-978; application/src/core/tracker.py:2963-2984). motion_detector.py:269-273 doubles the calibrated threshold when the post-gamma ROI mean is below 55; the same flag multiplies min blob area by 1.8 (:355-357) and the crossval ratio by 1.2 (pipeline.py:2291-2294), with no hysteresis. At gamma 0.8 (the ladder floor) the 55 boundary corresponds to a raw median of about 75, inside the servo's accepted band 58-82 (config.py:640-641). The MOG2 behaviour is reasoned from OpenCV's implementation, not executed here.

**Why it matters.** The "background false-positives x %" on the report card is not production's rate. The dial's var floor of 8 is really 16 in the dark scenes the product targets. The same scene calibrated twice can land in different regimes, and light falling through 55 during a show flips four parameters at once at an uncontrolled moment.

**Suggestion.** Build the sweep from MotionDetector itself (same preprocess, same label set, production learning rate after a fast warm-up). Drop the x2 once a calibrated value exists, or calibrate and store both regimes and latch the regime per calibration with hysteresis. Add a test that the sweep's false-positive rate matches a production MotionDetector's on synthetic noise.

### motion-calibration#6 — Empty-wall check: the advised remedy (paint an exclusion) cannot make it pass, and the tested confidence margin is narrower than the operator dial

severity medium · reader's confidence high · correctness

**Re-checked: partly.**

**Evidence.** application/src/runtime/calibration_flows.py:504-506 feeds `processor.last_raw_dets`, which application/src/core/pipeline.py:735-745 builds straight after YOLO. The exclusion mask is applied to detections later (pipeline.py:965-967, 1953-1976). The messages nonetheless tell the operator to "remove the object or paint an exclusion there, then calibrate again" (application/src/core/empty_wall.py:76-79; calibration_flows.py:533-536). empty_wall.py:115 sets the limit at 0.8 x confidence, and calibration_flows.py:486-487 passes the live, dial-modified confidence. The dial reaches 0.15 (application/src/core/sensitivity_macro.py:48-52; application/src/core/config.py:725-726).

**Why it matters.** A technician who follows the instruction and recalibrates gets the same failure and the weakest enhancement again. With a seed of 0.35, the Drops/Ghosts dial leaves the tested region above about 67, with no notice. If Calibrate ran with the dial on the strict side, even dial 50 is outside what was tested.

**Suggestion.** In `_step_wall_check`, drop raw detections that fall in excluded cells before feeding the check. Derive the limit from the calibrated seed and the dial's reachable floor, or store the tested limit with the calibration and toast when the dial goes below it.

### motion-calibration#7 — ExposureServo clamps its internal exposure/gain to the caps without commanding the camera

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/calibration.py:266-270 clamps the starting values to the blur-budget cap and gain ceiling, but no command is issued for that. In the dark branch (:310-320) an internal exposure already "at cap" goes straight to gain steps. In band it finishes (:344). The result reports the internal values (:357-361). application/src/runtime/calibration_flows.py:260-262 constructs it from the camera's current values and :346-358 only applies returned commands. Manual exposure is allowed up to 66.7 ms (application/src/camera/ids_camera.py:116-135) against a 25 ms blur cap (application/src/core/config.py:633).

**Why it matters.** If the camera sits above the blur budget (a manual 40 ms, or after Calib2 lowered `blur_budget_ms`), it stays there while the report says "Exposure: 25.0 ms ... converged". The blur budget, stated as the binding constraint, is not enforced, and Calib2's blur feedback never takes effect when the scene is already in band.

**Suggestion.** Emit an initial exposure/gain command whenever construction clamped a value, report the values read back from the camera controller, and add a unit test that starts above the cap.

### motion-calibration#8 — MOG2 holds a still dancer for roughly 100 frames, not "indefinitely", and has no dancer-protected update; the clean plate already does that job

severity medium · reader's confidence medium · paradigm

**Evidence.** application/src/core/motion_detector.py:5-8 claims dancers "remain foreground indefinitely" at learning rate 0.001. With OpenCV's default background ratio of 0.9, a new mode turns background once its weight passes about 0.1: ln(0.9)/ln(0.999) is about 105 frames, about 5 s at the IDS 20 fps (reasoned from OpenCV's implementation, not run). `MOTION_BRIDGE_MOG2_HISTORY = 500` (application/src/core/config.py:840) is not used by OpenCV when a learning rate is passed. motion_detector.py:47-49 says shadow pixels (projection leak) are "rejected automatically" while every live consumer includes them (config.py:850-853, 909-911). application/src/core/foreground.py:117-134, 292-318 has a noise-relative threshold, gain normalisation and protect boxes. A third, legacy static subtractor is still wired before YOLO (application/src/core/background.py; application/src/core/gpu_pipeline.py:622-634).

**Why it matters.** A paused dancer YOLO cannot see (far, dark) loses the MOG2 silhouette after about 5 s. The same rate sets recovery from a light step (about 5 s of flooded mask). Three background references with separate capture buttons and staleness rules multiply what has to stay calibrated on site.

**Suggestion.** Choose one background reference. The plate's foreground mask could serve bridging and cold blobs, with frame-diff kept for "moving now" and MOG2 only as the no-plate fallback. That would retire the sweep, the low-light switch and the scale knob. Remove or hide the legacy BackgroundSubtractor and correct the two docstrings.

### motion-calibration#9 — Lighting profiles switch only part of the calibration: the plate and the calibration provenance are shared

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/config_schema.py:32-47: PROFILE_KEYS has 14 keys, without the plate or `calibration_state`. application/src/app.py:1860-1870: one plates/latest.npz per project, loaded only with a full config (:1497-1498). application/src/runtime/config_manager.py:352-370: a profile switch applies the bundle and seeds a never-calibrated profile from a copy of the other. app.py:1319-1324: `calibration_state` is shared. application/src/runtime/calibration_flows.py:249-250 treats the current enhancement as "the project's calibrated" one when `"gamma" in calibration_state`; :176-193 builds the "Last calibrated" line from the same dict.

**Why it matters.** After a show/rehearsal switch the plate belongs to the other lighting. A copied, never-calibrated gamma/CLAHE is treated as the incumbent and kept by the empty-wall check. The "Last calibrated" line shows an age for a profile that was never calibrated. Calibrating one profile overwrites the plate the other needs, so every switch requires a new Calibrate.

**Suggestion.** Store the plate path and `calibration_state` per profile (for example plates/<profile>/latest.npz), reload the plate on profile switch, and key the incumbent on the active profile's own stamp.

### motion-calibration#10 — The report card and dial state mislead in the nominal case, and calibration quality is not kept

severity low · reader's confidence high · ux

**Evidence.** application/src/core/calibration.py:440-443: on the required empty wall the card always prints "Person height: NOT measured ... kept current value"; with people present it prints a measured height with no warning (:435-439). A servo failure such as "at exposure+gain limits - scene still dark, add IR" (:319) is only a line (:218-226), with no toast, unlike var_saturated (application/src/runtime/calibration_flows.py:408-412). Focus, uniformity and clipping are computed on the whole frame, not the ROI, with absolute thresholds (calibration.py:133-165, 463-468; calibration_flows.py:384-389). The false-positive metric is the median tile (calibration.py:856-862), so a flickering minority of tiles is invisible and no worst tile is reported. Playback seeds gamma from the whole-frame mean (calibration_flows.py:283-285) while live uses the ROI median (:364). application/src/app.py:1744-1752 re-centres the dial to 50 without restoring confidence/var to the anchors (compare :1716-1724). The readiness "calibration" row is the newest save's mtime (app.py:2270-2284; application/src/core/ops_monitor.py:196-217), and `calibration_state` keeps only source and time (calibration_flows.py:142-148).

**Why it matters.** The technician cannot tell a good calibration from a marginal one at a glance: the nominal run shows a line that reads as a failure, and real failures are buried in a ten-line dialog. The pre-show check cannot tell either, because quality is not stored. After a calibration made with the dial off-centre, dial 50 no longer means "calibrated seed".

**Suggestion.** Lead the card with one verdict (OK, or needs attention plus the single action). Persist converged / saturated / wall-clean flags in `calibration_state` and show them in readiness. Compute stats on the ROI, add a worst-tile line, drop the height line from the scene pass, seed gamma from the ROI median in playback too, and apply the anchor values when re-centring the dial.

### motion-calibration#11 — Calib2 (expert) evidence is biased by the settings it is meant to choose

severity low · reader's confidence medium · correctness

**Evidence.** application/src/core/calib2.py:464-467: the seed is p05 of box confidences minus 0.05, but the samples come from confirmed tracks whose boxes already passed the live threshold (application/src/runtime/calibration_flows.py:617-627). The run does not record that threshold or the gamma (calib2.py:58-76). calib2.py:475-481 converts px/frame to px/ms with fps = 1000 / process_wall_ms (calibration_flows.py:628), which times only the processor call (application/src/runtime/main_loop.py:830-848), not the 20 fps camera cadence. calibration_flows.py:797-800 with application/src/app.py:1927-1952: an applied imgsz with no prebuilt engine falls back to PyTorch, and the presets 640-1920 (calib2.py:48) are not filtered by engine availability (I did not check which engines ship). The imgsz probe is disabled on TensorRT (calibration_flows.py:665-669), so on the show rig dark mode rests on sigma > 4: from Calib1 if under 600 s old, else from MotionModel.noise_sigma() (:706-716), an unbounded Welford since the last reset that also absorbs light drift (application/src/core/motion_model.py:243-253).

**Why it matters.** The confidence seed cannot go much below the threshold in force during the run. The blur budget shrinks by the ratio of processing rate to camera rate, pushing the next servo toward gain. The net-height target flips between 110 and 45 px (for example imgsz 1536 versus 640) on a statistic that drifts. Apply can move the show laptop off TensorRT.

**Suggestion.** Record confidence threshold, gamma and camera frame period in SubjectRun. Convert speed with the camera period. Restrict presets to those with an engine present. Use a windowed sigma or drop the live fallback.

### motion-calibration#12 — Dead and stale code in the subsystem, including an unreachable wizard

severity low · reader's confidence high · maintainability

**Evidence.** application/src/ui/calibrate_all_wizard.py (281 lines) and application/src/ui/wizard_state.py (200 lines), plus a 257-line test file: the only opener is application/src/gui.py:796-799, which no widget calls (no reference in gui_builder.py). The intro text predates the D29 flow (calibrate_all_wizard.py:146-153) and every stale run is labelled "framing changed" (:240-242). application/src/core/calibration.py:565-609 with 5 AUTOCAL_EXCL_* constants: `start_exclusion_calibration` (application/src/core/pipeline.py:1990-1991) has no caller. pipeline.py:1686-1717 `_enhance_gray_for_motion` is unused. pipeline.py:1896-1903 and calibration.py:390-392 still describe the motion gray as post-CLAHE. application/src/core/motion_model.py:16-36 says "nothing imports this module yet" and lists a frozen surface the pipeline and tracker bypass; `fixed_gamma` is never set. application/src/core/motion_detector.py:89-96 leaves `_diff_scale` (:83-84) untouched on a scale change; :126-141 ignores `include_shadows` with the clean mask. application/src/core/config.py:871-874 says the diff frames are "downscaled and blurred" while motion_detector.py:76-84 says the opposite.

**Why it matters.** About 500 lines of operator flow plus tests are maintained but unreachable. The stale comments point the next reader at the wrong signal (CLAHE noise, a frozen API) when reasoning about what is calibrated.

**Suggestion.** Either re-wire the wizard to the D29 flow or delete it with its tests. Remove the auto-exclusion collection and its constants. Finish the MotionModel migration or drop the wrapper claims, and fix the three stale comments.

## camera-recording

### camera-recording#1 — The 2.5 s reopen watchdog undercuts the 5 s stall bridge, and the reopen is a synchronous full teardown that never raises an alert when it loops

severity high · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** camera_controller.py:77,148-156: no acquired frame for 2.5 s (hard-coded) = timed out. main_loop.py:691-696: that calls _stall_not_live() (CameraStallClock.forget), closes the camera and schedules a retry in 0.5 s. config.py:134-136 and camera_stall.py:62,90 define a stall as bridgeable up to CAMERA_STALL_MAX_S = 5 s, so 2.5-5 s is unreachable for the IDS. The reopen runs on the main thread from _tick_pumps (main_loop.py:576-583): UnifiedCamera.close drops the only IDSCamera, whose __del__ takes the library refcount to 0 and calls Library.Close (ids_camera.py:381-387, 467-475, 2070-2074); the next open re-initializes the library, enumerates, loads UserSet1, reallocates 16 buffers, restarts, then re-applies exposure/gain. Each successful open resets backoff and _camera_reconnecting (camera_controller.py:98-101,139-142), and the camera_down alert needs 15 s of continuous 'down' sampled at 1 Hz (ops_monitor.py:524-536). The only main-loop stall test stubs _ids_stream_timed_out to False (test_camera_stall.py:346).

**Why it matters.** In-code comments put routine stalls at about 1.7 s, 0.8 s under the reopen threshold. A stall that runs past 2.5 s is turned into 2.5 s + 0.5 s + a full reopen with the hold clocks running, so identity holds (1.5-2 s) expire and the media server drops video, which is exactly what D34 was built to prevent. A camera that opens but delivers nothing cycles every few seconds with no operator alert, because it is 'open' part of each cycle. Not confirmed: how long a reopen takes on the show laptop and whether stalls longer than 2.5 s occur.

**Suggestion.** Derive the reopen threshold from CAMERA_STALL_MAX_S (reopen only after the bridge has given up) and keep both in config. Before reopening, tell 'stream silent' from 'device gone' (acquisition thread still timing out cleanly and a control-node read succeeding means wait). Hold one process-lifetime library reference so a reconnect does not close and re-init IDS peak. Move the reopen off the main thread so UI and OSC keep-alive continue. Count reopens and alert on N in M minutes.

### camera-recording#2 — A frame is a bare array: no capture time, frame id or epoch travels from camera to pipeline, recorder or playback

severity high · reader's confidence high · paradigm

**Evidence.** ids_camera.py:1190,1207 stamps perf_counter after copy+unpack into _latest_timestamp, which nothing reads; read()/read_gpu() return only pixels (1465-1542). The device timestamp and frame id (buffer_meta, 207-224) feed diagnostics only. The output stage clocks on time.perf_counter at processing time (pipeline.py:478,1501) and the offline replay installs frame/fps instead (pipeline.py:1086-1089). Live is newest-only and drops when the loop is late (ids_camera.py:1202-1203); playback never drops (video_recorder.py:582-590), and the code comment there says dropped frames change Kalman behaviour. The recorder stores every camera frame, not the ones the live loop consumed, and no per-frame times (camlog is 1 Hz), so stalls and drops disappear from a take.

**Why it matters.** Tuning is done on recordings, but a take cannot reproduce the two things that hurt in the field: stalls and late frames. Three clock definitions (live wall clock, in-app playback wall clock scaled by speed and backpressure, offline frame/fps) mean time-based parameters (holds, One-Euro, velocities) are exercised differently in each. Several concrete defects below (actual_fps, frame index drift, loop-reset ordering) are symptoms of the missing envelope.

**Suggestion.** Introduce one small Frame record (pixels, camera frame id, host monotonic capture time taken when WaitForFinishedBuffer returns, device timestamp, source epoch) returned by read/read_gpu/read_frame. Have the recording callback append frame id + both times to a per-take sidecar. Playback then paces from recorded times, can optionally replay stalls and the live drop pattern, and reports the true file index; the pipeline's output clock takes the frame's capture time so live, in-app playback and replay share one definition.

### camera-recording#3 — The recording callback lives on the IDSCamera instance, so any reconnect silently ends the take

severity high · reader's confidence high · robustness

**Re-checked: confirmed.**

**Evidence.** UnifiedCamera.set_frame_callback forwards to the current _ids_camera or _cv_camera and stores nothing (ids_camera.py:2148-2153); with no camera open it is a silent no-op. Every open builds a new IDSCamera with _frame_callback = None (ids_camera.py:373,1979). Nothing re-wires it after a reopen (camera_controller.py:262-322; the only callers are recording_controller.py:339,364,412,421). The recorder stays in RECORDING and the UI keeps showing it. start_recording_slot from playback calls _cb_rec_live then wires the callback at once (recording_controller.py:470-480), immediately before the reopen described in the next finding.

**Why it matters.** A take that spans a stall longer than 2.5 s, a USB glitch or a crop change stops receiving frames with no message; the .meta later shows a short or zero frame count. Those are the takes most wanted for stall analysis, and field recordings cost a show night. The remote one-shot 'record slot N' issued while a take is playing can produce an empty recording. REC pressed while the camera is reconnecting also records nothing.

**Suggestion.** Keep the callback on UnifiedCamera and apply it in _open_ids/_open_opencv. Refuse or queue REC when no camera is open. Have RecordingController watch recording_frames and raise an alert when it stops advancing for more than a second while recording; write a 'reconnect' marker into the camlog.

### camera-recording#4 — Playback to LIVE always ends in a full camera reopen because the silence watchdog reads a timestamp that predates the pause

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** _start_playback_safe stops IDS acquisition (recording_controller.py:509); _cb_rec_live and the end-of-playback path restart it (recording_controller.py:344, main_loop.py:650-654). start_acquisition does not touch state.last_frame_time (ids_camera.py:1048-1104; it is only written at 1210), and frame_count is still > 0, so on the very next tick _ids_stream_timed_out returns True for any playback longer than 2.5 s (camera_controller.py:151-156, main_loop.py:691-696; tick order main_loop.py:509-519). The comment at camera_controller.py:400-404 confirms the reopen happens in the field and attributes it to the restart failing with PEAK GCWritePort -1001. stop/start also skip the reference sequence (no flush of the output queue, no TLParamsLocked, no WaitUntilDone), which I could not test.

**Why it matters.** Going live after checking a take costs several blind seconds and a hidden reopen every time; that hidden reopen already caused one field incident (crop ratio reset, same comment) and drops the recording callback. The intended light-weight pause/resume does not exist in practice.

**Suggestion.** Reset the watchdog reference when acquisition (re)starts (set last_frame_time to now, or track 'acquiring since'). Flush the datastream on restart so a pre-pause frame cannot be delivered. If the restart genuinely cannot work on this camera, make the reopen explicit (_attempt_camera_connect with a visible 'reopening camera' status) instead of relying on the watchdog.

### camera-recording#5 — Two camera objects: flows that 'close the camera' act on the unused legacy one, and their reopen path has no retry

severity medium · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** app.py:557-565: with UnifiedCamera present, app.camera is a CameraManager kept 'for compatibility with camera state'. _ModelCameraAdapter.close and project-switch step 3 call app.camera.close() and set state.is_open False (app.py:371-373, config_manager.py:175-179), so the IDS keeps streaming through model loads and TRT builds despite the comment 'Pausing camera during model load' (model_controller.py:476-480). apply_config then runs update_crop_ratio on the still-open camera, which restarts acquisition even when the ratio is unchanged (app.py:1563-1567, ids_camera.py:895-972), and a full reopen follows anyway (config_manager.py:283-287). After a model load the reopen goes through _open_camera, not _attempt_camera_connect (app.py:375-376, model_controller.py:612,623,687; config_manager.py:194,262): on failure nothing schedules a retry, and the pump requires _next_camera_retry_time > 0 (main_loop.py:576-583), which a previous success reset to 0.

**Why it matters.** If the immediate close-then-open after a model switch fails once, the camera stays down until the technician presses refresh; the alert after 15 s says 'check USB3/power', which points at the wrong cause. Not confirmed: how often that open fails; the code documents the device being touchy on restart. The split state is also what makes these flows hard to reason about.

**Suggestion.** Route every open through _attempt_camera_connect. Give CameraController real pause()/resume() methods that act on the active device and use them in model and project flows. Make update_crop_ratio a no-op when the size would not change. Longer term, drop the second CameraManager and keep one camera state object.

### camera-recording#6 — actual_fps measures the encoder thread's lifetime, not the capture rate, and it is the clock for playback and replay

severity medium · reader's confidence medium · correctness

**Re-checked: partly.**

**Evidence.** video_recorder.py:144,170-176: actual_fps = frames_written / (time from encoder thread start until the queue is drained to the stop sentinel). If the encoder lags, the backlog drain is inside the interval and the result equals the encoder's throughput (20 fps camera, 15 fps encoder, 60 s take gives 15.0). Playback paces on it (645-674) and tests/replay.py:704-711 uses it for the frame clock. Default codec is FFV1 on 2.3 MP frames expanded to 3 channels (config.py:777; ids_camera.py:1214). Related: 'measured_fps' in the camera snapshot is the configured AcquisitionFrameRate node value, and a hard-coded 30.0 for OpenCV (ids_camera.py:1836, 2059, 2219). The existing tests only assert actual_fps > 0. Not checked: FFV1 throughput on the show laptop.

**Why it matters.** A wrong fps silently rescales every time-based parameter evaluated on that take (hold seconds, velocities, smoothing), so a tuning conclusion can be off by the encoder/camera ratio without any visible sign.

**Suggestion.** Stamp monotonic time in put() for the first and last accepted frame and compute fps from those (frames_queued - 1 over that span); store the camera's node fps alongside for cross-checking and warn in .meta when they differ by more than a few percent. Rename the snapshot field to configured_fps.

### camera-recording#7 — Recorder failures, backpressure and disk exhaustion never reach the operator

severity medium · reader's confidence high · robustness

**Evidence.** start_recording returns True before the writer exists; if cv2.VideoWriter fails to open on the encoder thread (FFV1 needs the FFmpeg backend, as test_recording_provenance.py:20-21 notes) the thread just drains the queue while write_frame keeps incrementing the UI counter (video_recorder.py:150-155, 463-472). Write exceptions and queue-full drops are print-only (114-123, 163-168); frames_dropped and job.error are referenced nowhere outside video_recorder.py. The queue is 300 frames of about 6.8 MB BGR, roughly 2 GB before the first drop (video_recorder.py:97). No free-space check at start and no auto-stop; disk is only checked in the Go-Live readiness report (app.py:2291) while FFV1 writes about 55 GB/h (config.py:786-792). Shutdown waits 60 s for finalization then exits (912-917); whether an AVI whose writer was never released is still decodable was not verified.

**Why it matters.** The technician can run a whole night 'recording' into nothing, or leave REC on until the system disk shared with the media server is full. A 2 GB RAM spike on the show laptop during a slow-disk episode competes with live tracking.

**Suggestion.** Report writer-open result back to start (wait briefly on an Event) and toast failures. Publish queue depth, drops and job.error on the bus and as an ops alert. Check free space at REC and once a minute, auto-stop above a floor. Record single-channel frames for mono sources (isColor=False) to cut queue memory and encode work by about 3x, and bound the queue by bytes. Consider segmenting long takes so a crash loses minutes, not the take.

### camera-recording#8 — Playback frame numbers drift after a start offset, step or resume, and the loop reset is not ordered with the frame stream

severity medium · reader's confidence medium · correctness

**Re-checked: confirmed.**

**Evidence.** The decoder thread labels frames with a private counter that starts at 1 (video_recorder.py:527,606) regardless of start_frame (692-694, 717-721), so --play-at-frame N yields labels N, 1, 2, ... and --pause-at-frame compares against them (recording_controller.py:305-311). next_frame/prev_frame set the label from the file position (824, 843) but the decoder resumes with its stale counter. Those methods and seek_frame use the same cv2.VideoCapture as the decoder thread without a lock or a paused check (807-868; command handler app.py:1160-1164). If the container reports 0 frames, 'target' is unbound at 718. Loop: the 'loop' event is queued before the decoder waits for the previous frame to be consumed (563-590); when processing is slower than the paced rate (speed above 1x or a slow pipeline) the main loop resets the tracker first (main_loop.py:565-568) and then consumes the last frame of the previous pass. The ordering part is reasoned from the code, not executed.

**Why it matters.** Frame numbers are the join key between the preview overlay, tracker logs and issue reports; an offset sends debugging to the wrong frame. A stale last frame after each reset seeds tracks at end-of-take positions, an artefact that can be mistaken for detection ghosts during in-app review.

**Suggestion.** Publish (frame, file_index, epoch) from the decoder, taking the index from CAP_PROP_POS_FRAMES; have the main loop reset when it consumes a frame with a new epoch instead of from a side queue. Serialize reader access with a lock and reject step/seek unless paused.

### camera-recording#9 — Startup opens OpenCV device 0 and probes ten indices before the project's camera is known

severity medium · reader's confidence high · ux

**Evidence.** CameraState.source defaults to str(CAMERA_INDEX) = '0' (camera_manager.py:22, config.py:22). MainLoop._startup runs _do_camera_refresh (IDS list, forced library close, 0.5 s sleep, then cv2.VideoCapture on indices 0-9 without stopping at the first miss: camera_controller.py:178-221, camera_manager.py:157-192) and then connects to source '0' (main_loop.py:296-301). The project's 'ids' source is only applied when the project loads, which needs another OpenCV-to-GenTL handover with fixed sleeps (ids_camera.py:2015-2024). The comment at main_loop.py:784-788 records a field incident from this ordering: the picker's 1920x1080 webcam frame drove the IDS exposure to its minimum.

**Why it matters.** Every cold start pays for device probing, opens the laptop webcam, and performs the handover the code itself describes as able to crash natively when both APIs see the same device. If index 0 does not open, the retry loop blocks the main thread for over a second every few seconds while the picker is on screen.

**Suggestion.** Do not open any camera until the project (or the last-used project's rig) names the source; persist the last source. Probe OpenCV indices only on an explicit Refresh, and stop at the first missing index.

### camera-recording#10 — The live IDS path and the reconnect state machine are the least tested code in the subsystem

severity medium · reader's confidence high · testing

**Evidence.** No file under application/tests references process_gpu_direct, process_gpu_tensor, _mono_to_gpu_bgr or read_gpu (grep); goldens and replay go through FrameProcessor.process (tests/replay.py:619). GpuPipeline.process and process_gpu_tensor are two hand-duplicated bodies of about 100 lines (gpu_pipeline.py:588-787). No test covers _ids_stream_timed_out, the retry pump, playback-to-live, callback survival across a reopen, or actual_fps under backlog; the one main-loop stall test stubs the watchdog (test_camera_stall.py:346).

**Why it matters.** Parity between what is tuned (playback) and what runs in the show (GPU-direct) rests on two copies staying in step by hand, and the recovery logic that decides blind time in the field has only ever been exercised on site.

**Suggestion.** Add one CUDA test that pushes the same mono frame through read_gpu-style upload + process_gpu_tensor and through process() and asserts identical YOLO input tensors and motion gray; better, fold the two bodies into one function taking an uploaded GpuFrame. Add a fake-camera test of the reconnect machine driven by a fake clock (stall 1.7 s, 3 s, 6 s, unplug, playback then live, recording across a reopen).

### camera-recording#11 — Incomplete buffers are delivered as normal frames, and an unpack failure produces a silent no-frame loop

severity low · reader's confidence low · robustness

**Re-checked: confirmed.**

**Evidence.** buffer_meta reads IsIncomplete and _track_frame counts and prints it (ids_camera.py:221, 1330-1334), but the loop stores and forwards the frame regardless (1186-1217). _unpack_raw returns None with a printed line per frame when the byte count does not match or the format is not one of three names (1421-1431); the loop neither counts that as an error nor stops, so the watchdog reopens every 3 s into the same state. Not checked: whether this camera in NewestOnly mode ever hands out incomplete buffers.

**Why it matters.** A partially transferred image (new rows over old ones) looks like motion to MOG2/frame-diff and can spawn a ghost exactly around USB trouble. The unpack case would be an endless reconnect loop with log spam and no explanation for the technician.

**Suggestion.** Skip incomplete buffers (count them, treat as a missing frame for the stall clock). Treat repeated unpack failure as a fatal configuration error surfaced once to the operator instead of a silent loop; validate pixel format and payload size at open.

### camera-recording#12 — The OpenCV fallback has no silence watchdog and tears down unsafely

severity low · reader's confidence medium · robustness

**Evidence.** _ids_stream_timed_out returns False for non-IDS sources (camera_controller.py:149-150); a cap.grab() that blocks leaves read() returning (True, None) indefinitely (camera_manager.py:79-83,137-140) and the camera counts as open, so neither reconnect nor the camera_down alert fires. _stop_capture_thread joins for 1 s, then open/close release the VideoCapture even if the thread is still inside grab (camera_manager.py:209-221, 257-259, 310-312). Other differences: fixed 1920x1080@30 request, fps assumed 30, no exposure/gain control, no stall diagnostics, BGR CPU path through process().

**Why it matters.** If a webcam or capture card is ever used as the field fallback when the IDS fails, a hung device freezes output with a green badge. Releasing a capture another thread is using is a known source of native crashes.

**Suggestion.** Apply the same 'no new frame for N s' watchdog to OpenCV sources using a last-frame time in CameraManager. Do not release the capture while the thread is alive; mark the object abandoned and build a new one. State in the UI that the fallback has no exposure control.

## config-ops

### config-ops#1 — No unattended recovery: after a power cut, crash or hang the show waits for a human at up to five points

severity high · reader's confidence high · robustness

**Evidence.** launcher/install_manager.py:23-38 creates only a desktop shortcut (no autostart). launcher/gui.py:193 and 406-414: a blocking messagebox.askyesno whenever the release branch is ahead. gui.py:245-256: run.bat is started with no arguments, and on a non-zero exit the launcher only re-enables a 'Restart' button. application/src/runtime/main_loop.py:320-328: the modal project picker is shown unless --project or WALLDANCE_AUTOLAUNCH_LAST is set (config.py:757 PROJECT_PICKER_ON_START = True; the launcher sets neither). application/src/runtime/model_controller.py:148-150 and runtime/config_manager.py:236-241: a missing TensorRT engine opens a modal build prompt in the startup path. application/src/core/ops_monitor.py:566-575 with app.py:735: the watchdog 'takes no recovery actions'. main_loop.py:483-488: a persistent failure exits with code 3 'so the launcher offers Restart'. The app-side pieces for auto-resume exist: the app boots in RUN (app.py:521) and config_store.py:147-160 falls back to the last valid save.

**Why it matters.** The stated goal is reliable output with as little technician intervention as possible. As built, a power blip or a native crash mid-show leaves the wall dark until someone reaches the laptop, starts the launcher, possibly answers an update prompt, picks the project, and possibly answers a TensorRT prompt. A main-loop hang is worse: the process stays alive, so nothing even offers a restart.

**Suggestion.** Add an explicit show mode. (a) A launcher setting that passes --project LAST (or sets WALLDANCE_AUTOLAUNCH_LAST) and defers the update prompt to a deliberate action. (b) The launcher relaunches on a non-zero exit with backoff and a cap (for example 3 restarts in 10 minutes, then stop and show the log). (c) The watchdog escalates: after N seconds of hang, write a crash marker of kind 'hang' and os._exit(4) so (b) takes over. (d) In show mode never prompt for a TensorRT build; fall back to PyTorch with the existing red banner. (e) Give the picker a countdown ('launching LAST in 10 s') as a softer default. (f) Put Windows auto-login, a Startup shortcut and power-on-after-AC-loss in the install checklist. Not checked: whether OS-level autostart is already configured outside the repo.

### config-ops#2 — The configuration has no single registry: five hand-maintained key lists and three different meanings of an absent key

severity high · reader's confidence high · paradigm

**Evidence.** application/src/core/config.py holds 303 module constants imported by name at about 34 sites; roughly 50 have a project key, the rest can only be changed by editing a tracked file. The per-project surface is written out by hand in: application/src/app.py:1222-1313 (75 keys saved), app.py:1315-1594 (an if-chain apply), application/src/core/config_schema.py:32-47, 52-79 and 83 (profile keys, 25 ranges, 4 bools), application/src/core/pipeline.py:109-170 (44 fields with their own literals, e.g. :125 brightness_threshold = 60), and application/tests/replay.py:239-348 (a second apply implementation, 'exactly as app._apply_config_without_model does'). Absent-key semantics differ: a live load keeps the previous project's in-memory value (app.py:1439 'if "tracker_max_age" in config'; the same for enhancement, mask, OSC, MOG2); D33 detection keys and slot keys get code defaults (config_schema.py:265-289, app.py:1478-1492); replay uses code defaults for everything and one differs (replay.py:245 enhance_force False vs app.py:547 True). One value has many holders: confidence lives in YOLO_CONFIDENCE (config.py:54), DETECTION_DEFAULTS (:1045-1046), ProcessingSettings, the sensitivity seed and dial (app.py:1359-1366), ConfigManager._profiles, both bundles of every timestamped file, _safe_defaults.json and the GUI slider, with three clamp ranges (schema 0.05-0.95, SENS_CONF 0.15-0.65, AUTOCAL2_CONF_BOUNDS). mog2_scale has three defaults: 0.5 (config.py:843), 0.75 (pipeline.py:1867), 0.7 preferred (config.py:628).

**Why it matters.** Every new knob must be added in four or five places, and the code already records misses (app.py:1447-1452 'the LIVE app dropped them', :1257-1258 BUG-4, and finding 3). What a project means depends on which project was loaded before it in the same session. A value tuned on replays is not guaranteed to mean the same live. On site, changing any non-project constant means editing config.py, which makes the tree dirty (the launcher then skips updates, launcher/gui.py:180-189) and is invisible to provenance (version.py reports only the commit).

**Suggestion.** Introduce one declarative table of project keys (name, type, default, range, scope shared or profile, getter, setter) and derive from it: the saved dict, the apply loop, validation, replay's processor build and the GUI sync. Define a full project load as defaults overlaid by the file, never previous memory. For the few constants the field really needs to touch, add an untracked site-override file, echoed in the log header. Add a 'dirty' flag to app_version().

### config-ops#3 — Save silently drops five keys that load applies: known-N tuning and hand-set tracker switches vanish at the next Save

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/app.py:1457-1467 applies tracker_swap_correctors, crossval_skel_min_kpts, crossval_skel_min_conf and crossval_motion_min_ratio; :1503 reads dial_b_relevant. None of them is in the saved dict (app.py:1222-1313). application/tests/known_n.py:58-59 tunes the crossval keys, and app.py:2634-2637 (Apply) writes them into the project file and applies them live. The next Save rebuilds the file from saveable_config only (application/src/runtime/config_manager.py:346-350; config_schema.structure keeps no unknown keys), so the newest file no longer contains them. app.py:1257-1260 shows the same bug class was already fixed for tracker_intermittent_confirm and tracker_ghost_skeleton_age. No test asserts that the applied keys are a subset of the saved keys.

**Why it matters.** A scene tuned with known-N, or a project where tracker_swap_correctors was set by hand for a contact duo, runs correctly until someone presses Save or confirms a calibration. After the next restart it silently runs on code defaults for the ghost/drop gates. Nothing on screen says so, and the JSON history looks normal.

**Suggestion.** Short term: add the five keys to _get_saveable_config. Add a round-trip test: for a file containing every key the apply path reads, load then save must reproduce them. Structurally: have ConfigManager keep the shared keys of the loaded file that the app does not know and merge them back in structure(), so an unknown key is preserved rather than dropped.

### config-ops#4 — The active lighting profile and all live changes exist only in memory until a manual Save; a restart can come back on the wrong profile

severity medium · reader's confidence high · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/runtime/config_manager.py:352-370: _cb_profile_switch changes _active_profile and applies the bundle but never saves; on load the profile comes from the file (:198). The only writers of a project file are config_manager.py:374 (operator Save or wizard confirm), :623 (safe defaults with a model change) and app.py:2546 / 2636 (tune Apply). Also memory-only: the height the guard adopted (application/src/core/pipeline.py:1446-1451), box-clamp (app.py:1786-1788), IDS auto-exposure / auto-gain flags (application/src/runtime/camera_controller.py:414, 438), and every slider moved since the last save. A grep for dirty / unsaved / autosave finds nothing in the config path.

**Why it matters.** If the operator switches REHEARSAL to SHOW at dusk without saving, a power cut or crash-restart reloads the file's active_profile: daytime exposure, gain, gamma, mask and confidence at night. The app boots straight into RUN, so it sends OSC from a mis-exposed image with no warning. Nothing shows the operator that the running state differs from what a restart would restore.

**Suggestion.** Persist the session-level facts separately from calibration: a small atomic projects/NAME/session.json (active_profile, time, optionally RUN/STANDBY) written on profile switch and read at load. Or autosave on profile switch. Add an 'unsaved changes' chip in the top bar, driven by comparing saveable_config() with the last loaded/saved dict at 1 Hz, and name the restored profile in a toast after a restart.

### config-ops#5 — The watchdog's stack dump silently fails under the log tee, and a Python-thread watchdog cannot fire while the GIL is held

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/ops_monitor.py:594-600: _default_on_hang prints 'thread stacks follow', then calls faulthandler.dump_traceback(file=sys.stderr, all_threads=True) inside 'except Exception: pass'. application/src/services/app_log.py:29-68: sys.stderr is replaced by _Tee(io.TextIOBase), which defines write / flush / isatty / encoding but no fileno(). faulthandler needs file.fileno(); TextIOBase.fileno raises UnsupportedOperation, which is swallowed. The tee is on by default (config.py:589, app.py:2661-2668) and app.py:735 uses the default handler. crash_marker.py:99-101 then tells the next start to 'check its log for a faulthandler dump'. tests/soak.py:153 uses the same call, but without the tee. This is from reading the code and CPython's behaviour, not from a run. Separately, LoopWatchdog._run is a Python thread: if the main loop is stuck in native code that holds the GIL, it never gets scheduled (not checked: which IDS / TensorRT / OpenCV calls release the GIL).

**Why it matters.** A hang is the one failure with no recovery (finding 1), and the stuck stack is the only diagnosis the watchdog exists to produce. As written, the log shows one alert line and nothing else, so a recurring field hang cannot be attributed to camera SDK, CUDA or GUI.

**Suggestion.** Pass the real log file handle to faulthandler (expose it from app_log), or give _Tee a fileno() that returns the log file's descriptor. Better: arm faulthandler.dump_traceback_later(OPS_WATCHDOG_HANG_S, file=LOG_FH) from the 1 Hz ops tick and cancel it in push_busy. It runs in a C thread without the GIL, and its exit=True option gives the forced-exit path that finding 1 needs. Add a test that installs the tee and asserts the hang handler writes a stack.

### config-ops#6 — A project switch is not transactional: an exception mid-way leaves the app idling with the camera closed, and inactive profile bundles are applied unvalidated

severity medium · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** application/src/runtime/config_manager.py:142-149: the only finally is the watchdog pop. :172 sets models._model_loading = True and :176-179 closes the camera. :196-209 (migrate, validate, config["model"]) and :268 (apply_config) have no try. Before the model load, :197-198 and :215-216 have already replaced _profiles, _active_profile and imgsz; the model-failure path (:258-263) does not restore them. At runtime the caller is application/src/runtime/main_loop.py:558-562; the tick boundary catches the exception, then :594-597 returns early forever because _model_loading is still True. The HealthMonitor then reports 'Camera down - check USB3/power' (ops_monitor.py:524-534), which is the wrong cause. Validation covers about 40 of 75 keys: only 4 bools (config_schema.py:83), so app.py:1535 bool("false") is True. The inactive bundle is stored raw (config_manager.py:197) and applied raw on a profile switch (:362-365), with _active_profile already changed at :359 before the apply can fail. I did not construct a failing input; this is from reading the paths.

**Why it matters.** A hand-edited or tool-written project file, or a hardware call that raises during apply, turns a project load into a frozen 'loading' app with a misleading alert. A failed profile switch leaves the label on the new profile with the old values live, so the next Save writes daytime values into the show bundle.

**Suggestion.** Snapshot _profiles, _active_profile, imgsz and camera state before the switch. Wrap steps 4-13 in try/except that restores them, clears _model_loading, reopens the camera, toasts the reason and re-shows the picker at startup. Run validate_flat on every bundle at load. Set _active_profile only after a successful apply. Generate type and bool validation from the key registry (finding 2).

### config-ops#7 — Most alerts are an 8-second toast: no persistent state, no signal to the media server, and two thresholds mis-fit a show

severity medium · reader's confidence high · ux

**Evidence.** application/src/runtime/main_loop.py:1410-1417: an alert is a print, a bus event and a tracker-log line. application/src/ui/adapter.py:33 and 345-348: the GUI shows a red toast for 8 s; only light_changed stays in the alerts strip (camera state and GPU temperature have their own badges). ops_monitor.py:392-397: a kind re-fires every 120 s. Nothing is sent over OSC (the /walldance addresses in core/osc_output.py carry no status), and the remote API only buffers events for polling. no_detection (ops_monitor.py:450-460) fires whenever zero tracks last 30 s in RUN, which includes a legitimately empty stage. fps_drop is relative to the session's own baseline (:427-437), and OPS_MIN_SHOW_FPS is used only by the readiness row (app.py:2252), so a session slow from the first frame never alerts. Readiness runs only on a STANDBY to RUN click (app.py:2229-2232) while the app boots in RUN (:521), so after a restart it never runs. The 'calibration' row measures the newest file's mtime with a 24 h warning (app.py:2272-2275, config.py:1101), which is 'last save', not 'last calibration'.

**Why it matters.** A technician who is not watching during those 8 seconds out of every 120 misses fps_drop, over_cap, height_stale, osc_send and frame_error entirely. A no-dancer alert that rings through every blackout trains people to ignore red toasts. The media server cannot fall back to a safe look when tracking is degraded, because it is never told.

**Suggestion.** Keep every active condition in the alerts strip until it clears (the AlertCleared mechanism already exists; extend it beyond light_changed) and add one worst-state health chip. Add an absolute fps floor alert in RUN. Gate no_detection on evidence of presence (foreground or motion present but no tracks) or make it an informational state. Run the readiness check once after the first project load in RUN. Compute calibration age from calibration_state timestamps. Consider an opt-in /walldance/status heartbeat (state, fps, camera ok).

### config-ops#8 — 'Latest config' is chosen by file mtime and project identity by _meta.project, so rename, copy or restore can load an old save or write into another project

severity medium · reader's confidence medium · correctness

**Re-checked: confirmed.**

**Evidence.** application/src/core/config_store.py:137-143 sorts by (mtime, name). rename_project (:296-309) rewrites every config with atomic_write_json in os.listdir order, re-stamping all mtimes within milliseconds. Files keep their old-name prefix, so after a second rename (or any rename on a filesystem with unordered listdir) the highest mtime can belong to an older save. application/tests/test_config_store.py:111-124 pins mtime ordering for a renamed project but never tests rename after several saves. config_store.py:350-356 with config_manager.py:271: _meta.project wins over the folder, so a project folder duplicated in Explorer loads as the original project, and saves, recordings and plates go to the original's folder. application/src/app.py:2272-2275: the readiness 'saved N h ago' also reads mtime, so a rename resets it to zero. Mechanics are from the code; how often projects are renamed twice, copied or restored in the field is unknown.

**Why it matters.** The project folder is the unit a technician will back up, duplicate for a new venue, or restore. Any of those can make a restart load a configuration other than the last one saved, or make edits to 'ShowB' land in 'ShowA', with nothing on screen to show it.

**Suggestion.** Order saves by the YYYYmmdd_HHMMSS suffix of the filename (prefix-independent, already unique through the next-free-second rule), with _meta.saved_at as a tie-break. Derive the project from the folder name when the file is under PROJECTS_DIR, which also removes the need to rewrite every file on rename. Show the folder and file in the load toast.

### config-ops#9 — The app log has no timestamps and no size rotation, and free disk is checked only at Go-Live

severity medium · reader's confidence high · robustness

**Evidence.** application/src/services/app_log.py:36-49: the tee writes raw print output with no time prefix (confirmed in logs/walldance_20261006_225451.log: only the header carries a time). :78-79 opens one file per process start; :98-106 prunes by count (40) at start only. application/src/runtime/main_loop.py:1448-1476 prints a [Budget] line every 5 s and [PerfSpike] up to once a second, with a 35 ms threshold on 'total' against a 50 ms frame period. The sample log is 27 KB for about 5.5 minutes, roughly 7 MB a day. crash_marker.py:109 overwrites the single last_crash.reported.json. The launcher inserts every line into a Tk textbox without trimming (launcher/gui.py:121-127). Disk: check_disk (ops_monitor.py:293-309) runs only inside the readiness report (app.py:2291-2294); HealthMonitor.tick has no disk input; a grep for disk_usage under src finds only readiness and video import. The default codec is FFV1 at about 55 GB/h (config.py:777, 792), and tracker JSONL retention allows about 2 GB x 30 live sessions per project (config.py:410-412).

**Why it matters.** After a bad night the log cannot answer 'when did the camera drop' without counting [Budget] lines. A multi-week session is one ever-growing file, read back whole by tools and held in the launcher's memory. A recording left running, or weeks of live sessions, can fill the disk with no alert until the next STANDBY to RUN click.

**Suggestion.** Prefix each line with HH:MM:SS.mmm in _Tee.write, at line starts. Roll the file by size or at local midnight and cap total bytes. Keep crash reports as last_crash.STAMP.json (newest N). Demote [Budget] and [PerfSpike] to once a minute, or only on change. Cap the launcher textbox to the last few thousand lines. Add a 1-per-minute free-disk check to the health tick: warn at the readiness threshold, and stop recording below the fail threshold.

### config-ops#10 — Durations are configured in frames with five different fps assumptions and are not rescaled, so hold and age windows stretch when fps drops

severity medium · reader's confidence medium · maintainability

**Re-checked: confirmed.**

**Evidence.** application/src/core/config.py:175 TRACKER_MAX_AGE = 45 ('~3 sec at 15 FPS'), :181 ('~10 sec at 15 FPS'), :350 ('2 s at 20 fps'), :600 ('~3s @30fps'), :680 ('~10 s @ 24 fps'), :980 ('~1.2 s at 25 fps'), :1000 ('~2 s @ 20 fps'), plus MOTION_BRIDGE_MAX_FRAMES (:838), TRACKER_GHOST_MIN_AGE (:201), TRACK_WARMUP_* and the MOTION_FIRST_* frame counts. CAMERA_FPS = 30 (:25) while IDS_MAX_FPS = 20 (:107). core/tracker.py contains no reference to fps (grep), so these counts are used as-is. Newer constants are already in seconds (IDENTITY_SLOTS_COAST_S, CAMERA_STALL_MAX_S, HEIGHT_GUARD_FOLLOW_S, OPS_*_S). I did not read tracker.py in full.

**Why it matters.** The real frame rate varies with imgsz, thermal state and exposure, and the fps alert only fires below half of the baseline. At 11 fps instead of 20, every track age, bridge length, ghost window and warm-up lasts almost twice as long in wall time: ghosts linger longer and confirmations take longer, with no alert. Values tuned on 25 or 30 fps recordings do not carry over to the 20 fps show stream.

**Suggestion.** Store durations in seconds and convert once to frames from the measured or nominal fps at tracker reset. At minimum, define one NOMINAL_FPS = 20, correct the comments against it, and have the readiness check warn when live fps deviates from it by more than about 15 %.

### config-ops#11 — The 1 Hz ops tick is gated on wall-clock time: a backward clock step silences health monitoring for the size of the step

severity low · reader's confidence medium · robustness

**Evidence.** application/src/runtime/main_loop.py:1285-1288: 'now = time.time(); if now - self._last_ops_tick < 1.0: return'. The same wall-clock value drives every sustain window and cooldown in HealthMonitor.tick (ops_monitor.py:405 onward) and the height and light sample windows (main_loop.py:1303-1305, 1380-1382). The watchdog correctly uses time.monotonic (ops_monitor.py:585, 607). Not checked: how large the clock corrections on the show laptop actually are.

**Why it matters.** After an unclean power loss a laptop can boot with a drifted clock and be corrected by Windows time sync minutes later. A backward step of D seconds stops alerts, the OSC error poll, light monitoring and the sentinel's project note for D seconds. A forward step makes every sustain window look satisfied at once.

**Suggestion.** Use time.monotonic() for the tick gate and for all sustain, cooldown and window arithmetic; keep time.time() only for values that are displayed or logged.

### config-ops#12 — Two operator-mistake guards are missing: project delete is an unsanitised permanent rmtree, and a second app instance is not detected

severity low · reader's confidence medium · robustness

**Evidence.** application/src/core/config_store.py:314-323: delete_project joins config_dir with the raw name and calls shutil.rmtree. An empty name resolves to the projects directory itself and '..' to the workspace. Today's callers pass a picker row name, and the remote API marks the command NEVER, so exposure is low; rename_project (:279-295) has the same unsanitised old_name. The picker offers Delete ('Removes all its configs + recordings', application/src/gui.py:2363-2380) at every launch behind one confirm. Separately, a grep finds no single-instance lock, and application/src/services/crash_marker.py:110-121 unlinks any existing sentinel without checking whether its pid is alive. A second launch therefore reports a false 'previous session crashed' and competes for the camera and ports 8080 / 8765. Not checked: how the IDS open fails in the second instance.

**Why it matters.** The technician sees the picker every day, and a mis-click removes a venue's calibration history and its recordings irreversibly. A double-click on the desktop shortcut produces two processes sending OSC, and a misleading crash toast.

**Suggestion.** In delete and rename, sanitise the name and require the resolved path's parent to be config_dir. Move the folder to projects/_trash/NAME_STAMP instead of deleting it, and purge old trash at start. At startup, if the sentinel's pid is alive, refuse to start (or focus the running instance) rather than reporting a crash.

## gui-operator

### gui-operator#1 — Single-letter shortcuts fire while typing in text fields and silently change the pipeline (E turns enhancement off, P freezes the preview)

severity high · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** `application/src/ui/adapter.py:250-297`: the global `dpg.add_key_press_handler` callback `_handle_key` returns early only when `issue_report_dialog` exists, the unreachable wizard is active, or the project picker is visible (252-271). Otherwise bare E submits `ToggleEnhance(enabled=None, quiet=True)` (278-279), P submits `TogglePreview(enabled=None, quiet=True)` (292-293), and S/K/B/T/I/C flip overlays. `application/src/app.py:1166-1173` and `1197-1203`: the quiet paths flip `settings.enhance_enabled` / `preview_enabled` with a console print and a checkbox sync only, no toast and no placeholder. Unguarded text inputs: the rig-sheet fields Lens/Filter/IR light/IR markers/Notes on rail 1 (`application/src/gui_builder.py:699-710`), the Save Configuration name field (`application/src/gui.py:2176-2181`) and the OSC IP field (`gui_builder.py:1497-1502`). The picker comment at `adapter.py:260-263` shows the authors already hit this for one field.

**Why it matters.** Typing a note such as 'IR pass filter' or a project name containing e or p toggles gamma/CLAHE enhancement or stops preview updates. The only trace is a checkbox inside the closed Advanced drawer. A stray key press on the show laptop during a performance does the same. In a dark scene, enhancement off means dropped dancers with nothing on screen explaining why; a frozen preview looks like a hang.

**Suggestion.** In `_handle_key`, ignore unmodified keys when any text input is active (track focus with `dpg.is_item_active` over the known input tags, or a generic 'a modal or input owns the keyboard' flag) and when any modal is open. Move pipeline-affecting shortcuts (E, P) behind Ctrl/Alt or remove them from the show build. Make every keyboard toggle raise a toast, and show enhancement state as a top-bar chip.

### gui-operator#2 — Health alerts are 8 s single-slot toasts; only one alert kind persists in the alerts strip

severity high · reader's confidence high · ux

**Evidence.** `application/src/ui/adapter.py:34` sets `STRIP_ALERTS = ("light_changed",)`. At 345-349 every `api.Alert` becomes `show_toast(..., duration=8.0)` and only `light_changed` is pushed to the strip. `application/src/gui.py:2929-2952`: `show_toast` deletes the existing `toast_window` first, so any later toast (dial re-anchor, import progress, 'Preview ON') replaces an alert, and two alerts in one 1 Hz health tick leave only the last. `application/src/core/ops_monitor.py:392-397` with `application/src/core/config.py:1124` (`OPS_ALERT_COOLDOWN_S = 120`): a sustained fps_drop, no_detection, over_cap, height_stale, gpu_temp or camera_down re-rings at most every 120 s. The strip's own docstring (`application/src/gui_builder.py:1181-1186`) lists GPU temp, TRT fallback, OSC down and height-stale as intended residents, with 'feeders ... gated'. `application/src/runtime/main_loop.py:479-482`: frame_error/tick_error alerts take the same toast path.

**Why it matters.** A technician who is not staring at the top-left of the preview during those 8 seconds never learns that tracking has had no dancers for 30 s, that OSC sends are failing, or that frames are being skipped on exceptions. The condition is then on screen about 7 % of the time. Camera-down and GPU temperature still have a persistent chip; the other kinds have none. This is the main channel by which the system tells a non-developer it is unhealthy.

**Suggestion.** Make the alerts strip the persistent home for every active condition. Push on Alert for all kinds and have `HealthMonitor` emit a cleared kind when each condition ends (it already does so for `light_changed` via `cleared` / `pop_cleared`). Colour the strip red while non-empty. Keep the toast as the attention-getter but queue toasts instead of replacing, and never let an informational toast evict an alert.

### gui-operator#3 — The light-change remedy puts a live show on a looped recording with no auto-return, no alarm and unnamed slots

severity high · reader's confidence medium · ux

**Re-checked: confirmed.**

**Evidence.** The phase-2 instructions (`application/src/gui_builder.py:776-781`) and the alert remedy (`application/src/core/ops_monitor.py:233-234`: 'else play this lighting's empty take and Calibrate on it') tell the operator to play a take when the stage cannot be cleared. `application/src/runtime/main_loop.py:636-659`: while `recorder.is_playing` the tick processes playback frames instead of the camera, and IDS acquisition is stopped until `go_live`. Playback loops at end of file (`application/src/core/video_recorder.py:564`). `application/src/runtime/calibration_flows.py` contains no `go_live` call after a playback calibration. `ops_monitor.py:412-413`: the `live` gate excludes playback, so the fps and no-detection alerts are suppressed. Slots are bare digits 1-9 (`gui_builder.py:1248-1256`); nothing records which slot holds which lighting. The only on-screen sign is 'SLOT n' in the recordings bar (`application/src/gui.py:1160-1162`). Not verified: whether OSC is muted elsewhere during playback (no gating was found in `main_loop.py`) and how often this is done mid-show.

**Why it matters.** Changing light is a named field condition. The remedy is a five-step manual procedure: remember the right slot, play, Calibrate, answer the Save/Keep modal, press LIVE. If the last step is forgotten, the system tracks an empty recorded wall for the rest of the show while every chip stays green.

**Suggestion.** Make it one action: a 'Recalibrate for this light' button that uses a labelled empty take (store a lighting label in the take's .meta and show it on the slot button), runs Calibrate and returns to LIVE automatically. Independently, show a large persistent 'PLAYBACK - not live' banner (reuse the TRT banner slot) whenever state is RUN and the source is a recording, and raise an alert if that lasts longer than a threshold.

### gui-operator#4 — No show lock: the app boots in RUN, the rail is navigation only, and the local GUI gates almost nothing that the remote API refuses in RUN

severity high · reader's confidence high · paradigm

**Evidence.** `application/src/app.py:521` and `application/src/gui.py:316` both default to `SystemState.RUN`, so rigging and calibration happen in RUN. The comment at `application/src/runtime/main_loop.py:811-812` assumes 'the Calibrate phase is used in Standby'. `gui.py:510-516`: `_on_phase_select` is 'Pure UI navigation'. `application/src/services/remote_api.py:56-96` classifies every command (heavy means STANDBY only, never means destructive), but locally only four handlers check state (`app.py:993`, `2343`, `2442`, `2572`: import, dry-run, sweep, known-N). Ungated, one-click and unconfirmed during RUN: the project combo (`gui.py:1058-1069` to `application/src/runtime/config_manager.py:410-416`, a full project switch and model reload), the config-version combo (`config_manager.py:418-424`), the safe-defaults icon (`application/src/gui_builder.py:731-741`; click loads, Ctrl+click overwrites; `config_manager.py:607-625` may trigger a model switch), model/imgsz/TensorRT, camera source, ROI Reset and mask 'Clear all'. No `set_exit_callback` exists in `gui.py:3097-3119`, so the window close button ends `while app.running and app.ui.is_running()` (`main_loop.py:262`) immediately.

**Why it matters.** The interlock exists for a remote developer but not for the person standing at the laptop during the show. A mis-click on a combo or the close button stops OSC output for the length of a model reload, or permanently. STANDBY/RUN is the only mode concept and nothing in the rail uses it.

**Suggestion.** Move the policy table out of `remote_api.py` into the command seam (tag `RuntimeAPI.submit` with a source) and apply the same heavy/never classes to GUI commands in RUN, behind a visible 'Show lock' toggle. The lock should disable or confirm those widgets and add a close confirmation. Decide explicitly whether launch should land in RUN; if yes, say so on the rail; if no, start in STANDBY and make 'RUN' the Go-Live step, with readiness rows shown.

### gui-operator#5 — STANDBY/RUN is held twice and the adapter drops the runtime's StateChanged, so a remote state change leaves the operator's badge and buttons wrong

severity medium · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** `application/src/gui.py:858-886`: `set_system_state` writes `gui._system_state`, the badge and button themes, then submits `SetState`. `application/src/app.py:1056-1062` sets `app.system_state` and publishes `StateChanged`. `application/src/ui/adapter.py:411-413` maps `api.StateChanged` to `lambda e: None`. `application/src/services/remote_api.py:61` allows remote `SetState` (control class). `gui.py:945` gates IMPORT on the GUI copy. Same pattern for overlays: `gui.py:836-838` computes the new value from `gui.config[show_*]`, while keyboard and project-load syncs only rebind the button theme (`gui.py:1870-1877`), so the next toolbar click sends the value the runtime already has. `box_clamp_checkbox` (`application/src/gui_builder.py:1149-1153`) has no entry in `sync_checkbox`, `app.py:1786-1790` never syncs it, and it is not in the saved config.

**Why it matters.** A remote developer who drops to STANDBY for a heavy job leaves the laptop showing a green RUN while nothing is sent to the media server. Remote ops is the active branch, so this is a live scenario. The reverse (GUI shows STANDBY while OSC flows) is equally possible.

**Suggestion.** Make the GUI a pure view of state: the STANDBY/RUN buttons only submit `SetState`, and badge and themes are set in the `StateChanged` handler. Do the same for overlays (submit `ToggleOverlay(name)` with `enabled=None`). Add a contract test asserting that every `ControlSync` name the runtime emits has a tag and every live-panel widget has a sync name.

### gui-operator#6 — An unattended restart cannot recover: picker, TRT prompt and calibration-result modals wait forever

severity medium · reader's confidence medium · robustness

**Re-checked: partly.**

**Evidence.** `application/src/runtime/main_loop.py:308-328` with `application/src/core/config.py:757` (`PROJECT_PICKER_ON_START = True`): nothing loads until a human presses Launch or Enter in the `no_close` modal (`application/src/gui.py:2214-2260`), unless `WALLDANCE_AUTOLAUNCH_LAST` or a CLI project is set. A grep of `launcher/*.py` found neither, but the launcher was not read. `application/src/runtime/model_controller.py:228-259` and `340-365`: `while user_choice["build_trt"] is None: render; sleep` has no timeout and no `is_running()` check; the first variant is reached on project load (`application/src/runtime/config_manager.py:241`). The prompt (`gui.py:2679-2699`) offers 'Build TensorRT (5-10 min)' or 'Use PyTorch' with no recommendation. The calibration result modal (`gui.py:2795-2848`) is `no_close` and also appears when calibration was started remotely.

**Why it matters.** After a crash or power blip mid-show (the loop exits with code 3 after a persistent fault), the relaunched app waits for input before producing any output. During the TRT wait the tick does not run, so no commands drain and the remote API is frozen too. The TRT question needs judgement the technician may not have, and the wrong answer is a 3-7x slower show (per the readiness text).

**Suggestion.** Add a visible countdown auto-launch of the last project (for example 20 s, cancelled by any input), immediate when a crash marker is present. Give the TRT prompt a stated recommended default and a timeout. Let the calibration modal be dismissed by a later command, or time out to 'keep session'.

### gui-operator#7 — The readiness check runs on the frame-loop thread and blocks about 0.25 s; it fires on every click of rail '2 Calibrate' and is 'safe' remotely in RUN

severity medium · reader's confidence high · performance

**Re-checked: confirmed.**

**Evidence.** `application/src/gui.py:535-541`: entering the wall phase submits `CheckReadiness` on every click. `application/src/app.py:2314-2335`: `_cb_check_readiness` 'runs synchronously on the command/main-loop thread ... ~0.3 s incl. the OSC probe'. `application/src/core/ops_monitor.py:158-184`: `probe_osc_udp` waits in `recv` for the full timeout when the listener does not answer, which is the normal case for a UDP media server. `application/src/core/config.py:1099`: `OPS_OSC_PROBE_TIMEOUT_S = 0.25`. `application/src/services/remote_api.py:58` classes `CheckReadiness` as safe, so it is allowed in RUN without the control gate. By contrast the Go-Live path runs the same report on a daemon thread (`app.py:2229-2232`). The button tooltip says 'never blocks RUN' (`application/src/gui_builder.py:826-827`).

**Why it matters.** Capture, tracking, OSC and render share this thread. Each look at the Calibrate panel during a show, or each remote readiness poll, freezes dancer positions for about five frames at 20 fps. The app is in RUN from launch, so this is the normal case.

**Suggestion.** Run `_build_readiness_report` on the existing worker thread and hand the rows to the main thread through a small queue drained in the tick (also needed for the cross-thread DPG finding below). Or skip the OSC probe when the sender has sent successfully within the last second. Do not auto-run on phase entry while in RUN.

### gui-operator#8 — Status chips show configuration rather than health; camera stalls and Go-Live readiness results go only to the console

severity medium · reader's confidence high · ux

**Evidence.** - OSC chip: `application/src/runtime/main_loop.py:1127` passes `osc_enabled=app.osc_enabled` and `application/src/gui.py:1771-1774` renders ON/green from that flag alone. It stays green in STANDBY (nothing sent) and while every send fails (the failure is only an 8 s toast via `main_loop.py:1388-1402`).
- CAM chip: `gui.py:1744-1749` uses `is_open`. A USB stall handled by the hold logic is a console print only (`main_loop.py:1241-1246`); no stall count or last-stall time reaches the GUI.
- Go-Live readiness (`application/src/app.py:2299-2312`): prints a console block and toasts '... 1 FAIL - see console' (`application/src/core/ops_monitor.py:88-98`). Rows are rendered only for the on-demand path (`app.py:2328-2333`).
- Config-load corrections also end in '- see console' (`application/src/runtime/config_manager.py:591-593`).
- Phase chips `phase_chip_*` are created empty and their updater `_update_phase_rail` does not exist (`application/src/gui_builder.py:593`, `620-621`).

**Why it matters.** The technician's at-a-glance row says OSC ON and CAM ON in exactly the situations the owner cares about (USB stalls, an unreachable media server). The detail that would make a failure actionable sits in a console the technician does not read.

**Suggestion.** - Drive the OSC chip from the sender (sending / idle in STANDBY / errors in the last N s, with target address).
- Add a stall counter and last-stall age next to CAM.
- Publish `ReadinessResult` from the Go-Live path and switch to the rows on FAIL.
- Use the empty phase chips for 'calibrated hh:mm' and 'readiness ok/warn/fail'.

### gui-operator#9 — The 'simple' surface is about 74 controls; expert mode hides only 10 knobs; the Live panel carries toggles whose tooltip says 'Leave it ON'

severity medium · reader's confidence high · ux

**Evidence.** Counted from `application/src/gui_builder.py`:
- top bar 2
- rail 4
- Rig 27 (11 of them rig-sheet fields)
- Calibrate 3
- Live 21
- recordings bar 17
That is about 74 interactive widgets before nudge buttons, plus 11 shortcuts and 2 preview gestures. The Advanced drawer adds 24 for everyone, including Model / Image Size / TensorRT, OSC target and the whole 'Expert tools (not in the operator flow)' header (`gui_builder.py:1212-1224`, not in `_expert_only_tags`). Expert mode (`application/src/gui.py:322-324`) gates three groups, 10 widgets. The Live panel's 'Dancer IDs' block (`gui_builder.py:1053-1175`) has 10 controls: Smart hold ('Leave it ON', 1139), Ignore static figures ('Leave it ON', 1148), Box clamp ('Leave ON', 1159), IR belt ('does nothing without [the detector module]', 1117-1119), Send /dancer/state (an integration choice), and Stability with a latency table. Rig exposes IDS crop ratio, gain and exposure that Calibrate also drives.

**Why it matters.** The panel text says 'Live, nudge a dial or two - nothing more', but the same panel offers eight more switches that change the OSC contract or identity behaviour. Controls that should never be touched on site are both judgement the technician lacks and a mis-click hazard. Jargon in operator-facing text (MOG2, varThreshold, CLAHE, YOLO input px) makes it worse.

**Suggestion.** Reduce Live to STANDBY/RUN, the two dials, Max dancers and the status lines. Move the leave-ON toggles, IR belt, /dancer/state and Stability/Hold to Advanced. Put `section_expert`, Model and OSC target behind expert mode. Collapse IDS gain/exposure/crop behind the gear by default with a 'set by Calibrate' note.

### gui-operator#10 — GUI code runs on worker threads against the code's own stated rule: Toast events from five background workers delete and create DPG windows

severity medium · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** `application/src/runtime/api.py:1110-1116`: `EventBus.publish` calls subscribers synchronously on the caller's thread. `api.Toast` is published from worker threads: `OpsReadiness` (`application/src/app.py:2231`, `2310`, fired at every STANDBY-to-RUN), `DryRunReplay` (about `app.py:2425`), the sweep worker (about `2508`), `KnownNTune` (about `2607`) and `VideoImport` progress/done/error (`application/src/runtime/recording_controller.py:595-602`, `639-662`). `application/src/gui.py:2929-2948`: `show_toast` does `delete_item("toast_window")` then `with dpg.window(...)`, using DPG's global container stack. The comments at `gui.py:2950-2951` ('deleting from a background thread races the render loop (DPG is not thread-safe)') and `gui.py:2978-2979` ('set_value is the safe cross-thread DPG op') state the rule being broken. No `dpg.mutex()` is used anywhere. DPG's internal locking was not verified, so the failure mode is inferred.

**Why it matters.** The team already moved toast expiry to the main thread for this reason. The same delete can still run concurrently with `render_dearpygui_frame` or with another thread's `with dpg.*` block (the main thread building the pool list or QR drawlist, the callback thread building a popup). Outcomes range from a lost toast or a duplicate-tag exception to items parented into the wrong window, or a native crash. One trigger is the Go-Live moment.

**Suggestion.** Marshal in one place: in `DpgUiAdapter._on_event`, if `threading.get_ident()` is not the main thread, append the event to a deque that `render_frame` / the tick drains. Workers then never touch DPG and the set_value-only convention is no longer needed.

### gui-operator#11 — Leftovers from earlier designs are visible to the operator, and about 650 lines of GUI code are unreachable

severity low · reader's confidence high · maintainability

**Evidence.** Operator-visible:
- The rail says '3 Live' (`application/src/gui_builder.py:582-586`) but the panel title is '6 - Go Live' (966) and the REMOTE tooltip says 'allowed in 6 Live' (436).
- The rig-sheet tooltip says 'A new project (Start blank)' (715) though the picker button is now 'New'.
- The Advanced tooltip lists 'Input' (634), which moved to Rig.
- Runtime messages use dead vocabulary: 're-Aim' (`application/src/runtime/calibration_flows.py:412`), 'Advanced > Person height' and 'Model panel' (`application/src/core/ops_monitor.py:497`, `139`).
- '+ new project' in the top bar means Save-As of the current settings (`application/src/gui.py:1060-1063`), while picker 'New' means defaults plus the last rig.

Dead code:
- `_on_calibrate_all` (`gui.py:796`) is bound to no widget, so `ui/calibrate_all_wizard.py` and `ui/wizard_state.py` (481 lines) are unreachable, yet every calibration event still routes through them (`application/src/ui/adapter.py:353-362`).
- `show_load_config_dialog` and helpers (`gui.py:2398-2567`) have no trigger.
- `_picker_action("blank")` (`gui.py:2293-2298`).
- Eight more unbound `_on_*` callbacks, including `_on_tracker_reset` and `_on_quit`.
- Hidden `path_*` tags are updated every frame (`gui.py:1817-1830`).
- The accordion's `_last_open_section = "section_input"` (`gui.py:344`) names a section no longer in the list.
- The DPG mouse-down handlers (`adapter.py:84`, `87`) can never pass their guard `app_data != mvMouseButton_Left` (`application/src/ui/roi_mask_editor.py:386`, `446`), because the mouse-down payload is `[button, seconds]` (DPG 2.3.1 demo.py:3235-3236). The main-loop polling (`roi_mask_editor.py:283-300`, `491-506`) is the real path, while the move and release handlers still fire on the callback thread.

**Why it matters.** A technician following 'see 6 Live' on a three-step rail loses trust. For maintainers, two ROI input paths, two 'new project' meanings and a dead wizard on the calibration event path make each change riskier. The whole-window smoke test cannot catch this, because its stub returns a no-op for any `_on_*` name (`application/tests/test_gui_full_build_smoke.py:24-27`).

**Suggestion.** One cleanup pass: renumber the panel and tooltip strings from the `PHASES` table; delete or re-home the wizard, the load-config dialog, the 'blank' branch and the unbound callbacks; remove the DPG mouse handlers and keep polling only; make the smoke test fail on callbacks the real `WallDanceGUI` does not define and on `_on_*` methods no builder references.

### gui-operator#12 — Toast expiry and the accordion only run on wait-path renders; GPU stats are queried through NVML every frame

severity low · reader's confidence medium · performance

**Evidence.** `application/src/gui.py:3127-3147`: toast expiry and `_check_section_exclusion` live in `gui.render_frame`. The tick tail and the model-load pump call `render_frame_raw` (`application/src/ui/adapter.py:103-105`, `application/src/runtime/main_loop.py:1202`, `595`). The wrapped render is reached only from wait paths (`main_loop.py:647`, `685`, `745`, `764`, `1259-1263`). With `newest_only`, the camera read returns `(True, None)` only when no new frame is waiting (`application/src/camera/ids_camera.py:1519-1527`), so when the loop is slower than the camera the wait path is never taken. Separately, `gui.py:1728` calls `update_gpu_stats()` inside `update_stats`, which runs on every `StatsTick` (`main_loop.py:1114`, every frame). That is four pynvml calls per frame (`gui.py:41-58`), although a 1 Hz `GpuStats` event exists for the same purpose (`main_loop.py:1265-1273`). The NVML cost on the show laptop was not measured.

**Why it matters.** In a degraded state (PyTorch fallback, thermal throttling) toasts, including red alert toasts, never clear, and the Advanced accordion stops collapsing. That is when the operator most needs accurate messages. The per-frame NVML and roughly 45 DPG calls are small but sit inside the frame budget shared with OSC.

**Suggestion.** Move toast expiry into the adapter's raw render path, or call the wrapped render at the tick tail, and drop the split. Remove the `update_gpu_stats()` call from `update_stats` and throttle the text-only parts of `update_stats` to about 5 Hz.

## remote-deploy

### remote-deploy#1 — Nothing supervises the app: a crash, hang or accidental quit ends the show until a human clicks through the launcher

severity high · reader's confidence high · paradigm

**Evidence.** launcher/gui.py:245-256: run.bat is run once; a non-zero exit only shows 'WallDance closed.' and enables a 'Restart' button; exit 0 destroys the launcher. Restart re-runs the whole sequence (gui.py:144-146), including the network update check and the modal update prompt. application/src/core/ops_monitor.py:566-575: LoopWatchdog 'reports main-loop hangs; takes no recovery actions'. runtime/main_loop.py:241-277: exit codes 2 (startup) and 3 (50 consecutive failures) exist but the launcher treats every non-zero the same. Repo-wide grep finds no logon/boot autostart, no restart-on-failure setting, no sleep prevention (SetThreadExecutionState/powercfg). The app has a kiosk hook (WALLDANCE_AUTOLAUNCH_LAST, main_loop.py:320-322; app starts in RUN, app.py:521) but nothing sets it.

**Why it matters.** The stated goal is reliable output with minimal technician intervention. Today every failure mode the code already detects (persistent CUDA/TRT fault, native crash, main-loop hang on a USB stall) converts into 'dead wall until someone notices, walks to the laptop, clicks Restart, answers the update prompt, picks the project and waits for the engine to load'.

**Suggestion.** Give the launcher a supervised show mode: after the first successful start, loop on run.bat with backoff when the exit code is non-zero, set WALLDANCE_AUTOLAUNCH_LAST=1 and skip the update check on those restarts. Add hang recovery outside the process: the launcher polls /api/v1/ping plus the status timestamp (or a heartbeat file touched by the watchdog beat) and kills/restarts when it is stale beyond the busy-bracketed limit. Offer an optional logon scheduled task (with battery settings set, see the --via-launcher finding) and document or script the power/sleep settings the laptop needs.

### remote-deploy#2 — The reinstall trigger ignores the pin file and is not persisted, so code and venv can silently diverge

severity high · reader's confidence high · correctness

**Re-checked: confirmed.**

**Evidence.** launcher/git_manager.py:237-240: needs_install is true only when install.bat or application/pyproject.toml changed; application/requirements-prod.txt, the file pinned mode actually installs from (install.bat:72, 151), is not in the list. extra/wdslot.py:438-440 classify_change has the same blind spot, so release-check does not flag it either. launcher/gui.py:109, 156, 210, 229-233: needs_install lives only in memory; after an update whose install fails or is abandoned ('Exit (fix later)', launcher closed, power loss) the next start sees UP_TO_DATE and goes straight to run.bat. run.bat:25 only checks `import cv2, torch`. First install has the same hole: clone succeeds, install is interrupted, the next start runs run.bat, gets 'not installed in application/.venv' and offers only 'Restart' (the comment at gui.py:158-159 says the shortcut lets the user 'resume later', but nothing resumes). The CPU-mode dialog text 'install will re-run automatically' (gui.py:303-304) is not true.

**Why it matters.** The next dependency bump (new ultralytics, TensorRT, a new package) shipped as a pin-file change will update the code and leave the old venv in place, with no error at install time. The laptop then fails at import or, worse, at runtime on a path only the show exercises. An interrupted reinstall on venue 4G produces the same mixed state on the following start.

**Suggestion.** Make the venv self-describing: install.bat writes `.venv/.wd-install-stamp` containing a hash of requirements-prod.txt + install.bat + pyproject.toml only after a successful verify step. The launcher (or run.bat) compares the stamp with the current tree on every start and runs install.bat when they differ or the stamp is missing. Drop the changed-files heuristic. Add requirements-prod.txt to classify_change, and add a launcher test for 'pin changed only' and 'install failed, relaunch'.

### remote-deploy#3 — update() moves HEAD before writing the tree, and a failed update is launched anyway and never repaired

severity high · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** launcher/git_manager.py:200-203 points the branch ref and HEAD at the new commit, then 218-225 calls dulwich build_index_from_tree, which writes files one by one and writes the index only at the end (dulwich 1.2.17 index.py:2594-2654; its docstring says 'Suitable only for fresh clones'; on error it leaves already-written files in place). If that step raises (disk full, a file locked by antivirus or a second instance) or the launcher is closed mid-update, HEAD is new and the tree is mixed. gui.py:220-222 catches the exception, shows 'Update Failed' and falls through to run.bat (no return). On the next start check_updates sees local_head == remote_head (git_manager.py:122) and reports UP_TO_DATE; dirty_files() is only consulted for BEHIND/DIVERGED (gui.py:178-189), so the mixed tree is never detected. When the next release arrives the tree is 'dirty' and every update is refused with a dialog telling the technician to 'restore the original files'. I did not reproduce this on Windows; the ordering and the fall-through are read from the code.

**Why it matters.** Low probability, but the result is a laptop running an unknown mixture of two versions while reporting the new commit (core/version.py reads .git/HEAD), followed by a permanently blocked update channel that only a developer over SSH can clear.

**Suggestion.** Write the tree first and move refs last, or keep the current order but write an `update-in-progress` marker (containing the backup ref) before step 3 and remove it after step 5. At every start, if the marker exists or dirty_files() is non-empty while UP_TO_DATE, re-materialise the tree from HEAD instead of launching. After a failed update, restore the backup ref and tree and do not start a mixed tree without an explicit choice.

### remote-deploy#4 — The launcher blocks app start on the network and on an uninformed Yes/No update prompt

severity medium · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** launcher/git_manager.py:114 porcelain.fetch(repo, 'origin') runs on the start path with no timeout; dulwich only applies one when http.timeout is configured or passed (dulwich 1.2.17 client.py:4387-4394, 4437-4439), so a connection that stalls after connect blocks forever at 'Checking for updates...' with the action button disabled (gui.py:145) and no skip. Offline, missing branch and fetch errors are all logged as 'No updates available.' (gui.py:225-226; the real reason is printed to an invisible stdout, git_manager.py:119, 130). When behind, gui.py:410 shows a bare askyesno 'A new version of WallDance is available. Do you want to update?' (Yes is the default button) with no information on what changes or whether a multi-GB reinstall follows; it is asked on every start, including a restart after a crash. I did not measure connect-failure times on Windows or how urllib3's default retries multiply them.

**Why it matters.** On-site links are exactly the bad kind (4G tether, cafe wifi, a venue LAN with a gateway and no internet). The technician can be stuck in front of a spinner before a show, or can accept an update minutes before doors and trigger a torch/TensorRT download.

**Suggestion.** Never gate start on the network: start the installed version immediately, run the fetch in the background with a hard deadline (set http.timeout in the checkout's .git/config at clone, plus a thread deadline of ~8 s) and offer the update for the next start. Report the three states truthfully ('offline, running <sha>'). Add a show lock (launcher.json {"hold": true} or a hold-until date) so on-site starts never prompt, and make the prompt say what will happen (commits, 'needs reinstall, about N GB').

### remote-deploy#5 — Show protection exists only inside the HTTP API; the SSH-side tools that do the heavy work have no 'is a show running?' interlock

severity medium · reader's confidence high · robustness

**Evidence.** remote_api.py:140-144 refuses heavy jobs and unconsented control in RUN 'because they compete with the show for the GPU/CPU'. But extra/wdremote.py:974-1066 (run, pytest, py, replay --trt) start GPU/CPU jobs on the laptop over SSH without looking at app state, default to the LIVE tree (--slot default 'live', wdremote.py:1463), and pull streams GBs of recordings off the same disk. extra/wdslot.py:408-435: `slot stop --slot live` sends Quit, LIVE answers 403 'never allowed remotely', and the code then prints 'graceful quit refused ... -> terminating' and runs Stop-Process -Force on the LIVE app, in RUN or not (the '*<root>*main.py*' pattern for the live root also matches the -dev tree). DEV and LIVE share the API port and token (wdslot.py:379-382 sets no port; main_loop.py:384), so `wdremote cmd ...` meant for a DEV session reaches LIVE if LIVE is the app that is up; nothing asserts status.app.slot. remote_api.py:4 names 'a Claude session' as an intended driver of this tool.

**Why it matters.** One wrong flag or one automated session running a replay during a performance drops the frame rate or kills the app, bypassing the careful policy the API enforces. The guard is in the wrong layer for the commands that are most dangerous.

**Suggestion.** Add one guard() in wdremote used by run/pytest/py/replay/pull/deploy/slot: query /api/v1/status through the tunnel and refuse when a LIVE app is in RUN unless --during-show is given. Never escalate `slot stop --slot live` to a kill without --kill. Make API subcommands take the target slot and compare it with status.app.slot before submitting. Give the DEV slot its own port via WD_REMOTE_API_PORT.

### remote-deploy#6 — A bad release cannot be rolled back on site: moving `release` backwards is ignored and the backup refs have no restore path

severity medium · reader's confidence high · robustness

**Re-checked: confirmed.**

**Evidence.** launcher/git_manager.py:124-127: if the laptop's HEAD is a descendant of origin/release the status is AHEAD and gui.py:223-224 logs 'Local version is ahead of the server - skipping update'. So resetting `release` to the previous good commit does nothing on a laptop that already took the bad one; only a new revert commit works, and that needs internet at the venue plus a Yes from the technician. update() does store refs/walldance/pre-update-<stamp> (git_manager.py:175-178) and the dialog promises 'it can be restored' (gui.py:200-201), but no launcher code reads those refs and the laptop may have no git CLI (wdremote.py:822-823). The venv is not snapshotted, so a release that also changed dependencies cannot be undone by code rollback alone.

**Why it matters.** The realistic bad day is: update accepted on site, tracking is worse or the app crashes, no developer reachable, poor connectivity. Today the technician has no way back to the version that worked yesterday.

**Suggestion.** Add 'Start previous version' to the launcher: list the backup refs (date + sha), re-materialise that tree offline, and remember the choice so the next start does not re-offer the same update. Mark a version known-good after a session that ran N minutes without a crash marker and keep the last known-good ref. For dependency-changing releases keep the previous venv (rename .venv to .venv.prev before sync) so rollback covers both.

### remote-deploy#7 — pyproject.toml does not describe the environment that runs; reproducibility rests on one hand-frozen Windows list and on never forgetting --no-sync

severity medium · reader's confidence medium · paradigm

**Evidence.** application/pyproject.toml:37-44 pins torch/torchvision to the CPU index and line 11 says numpy<2.0, while production runs torch 2.12.0+cu130 and numpy 2.4.4 (requirements-prod.txt:44, 88). uv.lock is gitignored (.gitignore:23) and deleted on every unpinned install (install.bat:78); install.bat:86-99 runs `uv sync` and then overrides it with `uv pip install --upgrade torch torchvision --index-url`. Every entry point must therefore pass --no-sync (run.bat:25, 32; extra/build_engines.sh:135 comment '--no-sync to keep CUDA torch'); a plain `uv run` or `uv sync` in application/ would move the venv back toward the CPU lock. requirements-prod.txt is an importlib.metadata dump of one Windows cp312 venv (wdremote.py:1118-1141): no hashes, no markers, test tools included. `uv pip sync` installs exactly that list, so install.sh's pinned branch (install.sh:66-86, which only filters pywin32 names) would remove the nvidia-* runtime wheels a Linux CUDA torch needs. The dev venv in this checkout is python3.10 / torch 2.14.1 / ultralytics 8.4.173 / tensorrt 11.3 / numpy 2.2.6 (site-packages listing) and CI installs numpy<2 (.github/requirements-ci.txt). I did not run any installer; the uv behaviour is from its documented semantics.

**Why it matters.** No machine other than the show laptop reproduces the show stack, so 'green on dev/CI' says little about the laptop, and a replacement laptop built from this file is an untested path at the worst possible moment. One innocent uv command on the laptop can remove CUDA torch and TensorRT without an error.

**Suggestion.** Make the uv project truthful: declare the CUDA and CPU torch indexes per extra/platform in [tool.uv.sources], commit a universal uv.lock, and install with `uv sync --frozen --extra gpu --extra ids`. That gives hashes and makes `uv run` safe. If the freeze file stays, generate it with `uv pip compile --generate-hashes --python-platform windows`, keep Linux on the unpinned path explicitly, and add a windows CI step that does a from-scratch `uv pip sync --dry-run` of the pin so resolvability is checked on every release.

### remote-deploy#8 — Remote commands are constructed without type or range validation and can poison per-frame state

severity medium · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** services/remote_api.py:269-273: the only argument check is `ctype(**args)`. Most command dataclasses have no __post_init__ (runtime/api.py:97-110, 304-310, 536-540), although the module docstring says 'validated on construction'. Handlers store raw values: app.py:1974-1976 `self.tracker.max_age = value`; app.py:2121-2127 stores ip/port before _init_osc. wdremote's _parse_kv (wdremote.py:1274-1284) turns anything that is not valid JSON into a string, so `cmd SetTrackerMaxAge value=3O` is accepted with 200 'queued'. tracker.py:3285-3289 then does arithmetic on max_age every frame; frame errors are skipped until OPS_TICK_ERROR_EXIT_STREAK = 50 (config.py:1133) and the session exits with code 3 (main_loop.py:483-488). Out-of-range but well-typed values (max_age 0, negative sizes) are accepted too, and SaveConfig is remotely allowed, so they can persist. In RUN this is gated by the operator toggle; in STANDBY it is not, and the value carries into RUN. Smaller instances of the same thinness: int()/float() on query strings (remote_api.py:494-495, 503) and a non-object JSON body raise inside the handler; Range 'bytes=a-b' is not clamped to the file size (562-573). I traced the max_age path by reading and did not execute it.

**Why it matters.** The remote channel bypasses the GUI's slider limits, so it can put the pipeline in states the operator surface cannot, and a typo in a remote session can end the session a second later.

**Suggestion.** Validate in RemoteApi.submit: coerce each argument with the type hints command_schema() already computes and reject mismatches with 400; add a per-command range table shared with the GUI slider definitions (or __post_init__ on the numeric commands) so both clients enforce the same limits. Wrap query parsing in a 400 path and clamp Range.

### remote-deploy#9 — `slot run --via-launcher` registers its scheduled task with default settings: stopped on battery, 72 h limit

severity medium · reader's confidence medium · robustness

**Re-checked: confirmed.**

**Evidence.** extra/wdslot.py:384-391 builds the WallDance-launcher task with only -Action and -Principal. The sibling path at wdslot.py:360-362 explicitly passes -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries, which shows the defaults were known to be harmful. Windows task defaults are 'do not start on batteries', 'stop if the computer switches to battery power' and a 72-hour execution limit. The same 3-day ExecutionTimeLimit is set on purpose for the WallDance-live / WallDance-dev tasks (wdslot.py:360). release-check advertises --via-launcher as the way to apply a release 'now' (wdslot.py:495-496). I did not verify the Task Scheduler defaults on the laptop or whether stopping the task kills the whole process tree there.

**Why it matters.** If the show app was started this way, a brief switch to battery (generator changeover, a kicked power brick) makes Task Scheduler end the task and with it the launcher and app. A multi-day installation started through either remote path is terminated after three days.

**Suggestion.** Use one helper for both task registrations and always pass a settings set with -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero). Extend test_windows_launch_script_is_interactive_task to cover the launcher variant.

### remote-deploy#10 — Two launcher generations exist; the only exe in the repo is the March build that follows main and hard-resets, and the exe never self-updates

severity medium · reader's confidence medium · maintainability

**Evidence.** launcher/release/WallDanceLauncher.exe is tracked and was last committed 2026-03-26 (git log 38e15bb). The launcher source at that time (git show f51a231:launcher/git_manager.py) tracks refs/heads/main, treats any HEAD != origin/main as an update and force-syncs without dirty or ahead checks. The release channel and safety logic (26bdbdd, 188d2f7) reach a machine only through `wdremote launcher build` + `install` over SSH (wdslot.py:500-573); wdslot.py:536 acknowledges 'old exe ignores it'. origin/main is 136 commits behind origin/release and has no requirements-prod.txt, so an old exe means July code with an unpinned resolve. launcher/build.bat (lines 19, 29) is a second, unpinned build path that bypasses the .spec and build_info.json, so its output reports 'unknown (pre-2026-10 exe)' (gui.py:62). GitManager.clone falls back to the default branch on any clone error, not only a missing channel (git_manager.py:75-84). I cannot tell from the code which exe is installed on the show laptop.

**Why it matters.** All the update-safety properties in this review depend on which binary is on the machine, and the binary a new or replacement laptop would most naturally get from the repo is the unsafe one.

**Suggestion.** Remove the committed exe (or replace it with a CI-built artifact from the .spec, published as a release asset) and delete build.bat. Have the app, which does get updated, read the launcher's build info (pass it through an env var from the launcher) and raise a readiness failure or alert when the launcher is older than a minimum version. Restrict the clone fallback to the 'branch not found' case.

### remote-deploy#11 — The DEV slot shares LIVE's venv, models, projects (by default), API port and token

severity medium · reader's confidence medium · robustness

**Re-checked: by design.**

**Evidence.** extra/wdslot.py:48-49 SHARED_DIRS = application/.venv and models, junctioned in apply() (170-172); projects is junctioned to LIVE's unless --isolated-projects is passed (173-177). So a candidate branch running in DEV writes configs, last_project.txt, sessions and recordings into the show's project folders and can rebuild or replace TensorRT engines in the shared models/. Because the venv is shared, a candidate that needs a different dependency set cannot be tested without changing LIVE's environment, so the first real exercise of a pin change is on LIVE at update time. I did not check how the config loader treats files written by newer code.

**Why it matters.** The slot exists to protect the show from experiments, yet by default the experiment has write access to the show's data and engines; a schema change on a feature branch can leave the release build unable to load tonight's project.

**Suggestion.** Default to isolated projects and copy the selected project into the DEV slot on deploy (opt in to sharing with a flag). Have DEV write rebuilt engines to its own models directory with read-through to LIVE's. Make deploy refuse, or create a separate venv, when the candidate's requirements-prod.txt differs from LIVE's.

### remote-deploy#12 — Phone monitor is always on, bound to all interfaces without authentication, and its advertised address can be wrong on a show network

severity low · reader's confidence high · security

**Re-checked: confirmed.**

**Evidence.** core/config.py:570-572 WEB_MONITOR_ENABLED = True, host 0.0.0.0, port 8080; started unconditionally in main_loop.py:356-368 and it keeps serving in RUN. services/web_monitor.py:545-557 serves the camera stream, snapshot and stats to anyone who can reach the laptop (venue LAN, tether, tailnet). /control is an unauthenticated state-changing GET (578-596) and also changes what the remote API's /snapshot.jpg returns, since both call get_jpeg(). protocol_version HTTP/1.1 with one thread per connection, no cap and no timeout (532-543). get_lan_ip() picks the interface that routes to 8.8.8.8 and otherwise falls back to gethostbyname (40-52), so on a closed show LAN or a multi-homed laptop the QR code (203-224) may point at an interface the phone is not on. The main loop copies every preview frame into the monitor whether or not a client exists (229-236; main_loop.py:989-990).

**Why it matters.** Low risk on a closed LAN, but it is an unauthenticated live camera feed of performers on whatever network the laptop joins, an unbounded-thread listener inside the show process, and a setup aid that can silently show the technician a dead URL (binding 0.0.0.0 may also raise a Windows Firewall prompt on first run).

**Suggestion.** Start the monitor when the operator opens the QR dialog and stop it after N idle minutes, or automatically when entering RUN unless pinned on. Put a random per-session path token in the QR URL. Enumerate interface addresses and show one QR per address, or let the operator choose. Cap concurrent clients and set a handler timeout. Skip the frame copy when no client has asked for a frame in the last few seconds.

## tests-harness

### tests-harness#1 — Nothing gates a release on the checks that can see field behaviour

severity high · reader's confidence high · paradigm

**Evidence.** .github/workflows/ci.yml:8-11 triggers only on push to main/release and on pull requests; the working branch remote-ops (140 commits ahead of origin/main) is not covered. launcher/git_manager.py:16 sets DEFAULT_BRANCH = "release" and the laptop syncs to it at the next launcher start, so the push that starts CI is also the deploy. extra/wdslot.py:480-497: `release-check --tests` prints "unit suite FAILED", still returns 0 and still prints the push command; :438-449 flags only core/tracker* and core/pipeline* as replay-gated (not identity_slots, motion_*, gpu_pipeline, config.py defaults, camera/, main_loop). application/tests/test_regression_replay.py:74-77 needs WD_RUN_REPLAY=1, and extra/wdremote.py:991-993 (`wdremote pytest`) passes no env, so a laptop run skips the goldens. Scenario pass lines are only printed (application/tests/replay.py:920-923); no test asserts one on footage. I could not see whether CI is enabled or green on origin.

**Why it matters.** A regression in drops, ghosts or identity reaches the show laptop unless the developer remembers the right manual replay and compares numbers by eye. The technician on site cannot diagnose it.

**Suggestion.** Add one command, e.g. `wdslot release-gate <ref>`, that runs the unit suite at the ref, deploys to the laptop DEV slot, runs the rig tier there with WD_RUN_REPLAY=1 (trio, cache fidelity, two long-span manifests with --score), writes a verdict file keyed by sha, and exits non-zero on failure. Print the push command only on success. Add remote-ops to the CI push branches. Widen classify_change to every module replay imports.

### tests-harness#2 — Four harness tools were broken by later refactors unnoticed; one is behind the operator's Auto-tune button

severity high · reader's confidence high · correctness

**Evidence.** Derived from code paths, not executed. (a) application/tests/soak.py:204-205 builds the processor with use_gpu_path=args.gpu (default False, :420); application/src/core/pipeline.py:592-595 raises "GPU pipeline required" since 2026-06-24 (blame 25d1b68e). The documented commands (soak.py:20-21) and application/tests/test_soak.py:26-33 pass no --gpu. (b) application/tests/calibrate_project.py:100 calls `_build_processor(..., use_trt=False)` and :158 calls proc.process: same raise. application/tests/calibrate_segment.py:84 calls it first, and application/src/app.py:2492-2499 runs calibrate_segment.py for Auto-tune (the button is still built, application/tests/test_gui_full_build_smoke.py:49). (c) application/tests/sweep_project.py:53-56 json-parses the text between "=== SCORE ===" and "=== PASS LINE ==="; application/tests/replay.py:915-919 now prints the CONTINUITY block there and :931-933 another block after the pass line, so :58-59 returns (None, None) for every run. application/tests/batch_projects.py:94-103, 129, 137, 145 turns that into 9.9 ties, picks the first grid value and saves by default (:170); today it dies earlier at (b). (d) application/tests/replay_sweep.py:89-107 byte-compares against golden/decomp-phase0, whose 12 summaries all contain "path": "cpu" while replay.py:656 writes "gpu" or "trt".

**Why it matters.** The soak, the per-project calibration, the full 12-scenario sweep and an operator button cannot succeed, and the 9 scenarios outside the trio have no working regression gate. It also shows the upper tiers are not being run. If (b) is fixed alone, batch_projects will write arbitrary CLAHE/confidence values into project configs.

**Suggestion.** Have replay.py write one machine-readable result file (`--result out.json` with score, pass verdict, continuity, both streams) and make every tool read that instead of scraping stdout. Make `_build_processor` default to the GPU path, or raise at construction when it is off. Add a no-GPU smoke test per tool that runs it against a stub replay and asserts a parsed, non-sentinel score. Retire or re-baseline decomp-phase0. Give batch_projects the all-9.9 guard calibrate_segment already has.

### tests-harness#3 — The golden regression test compares tracker-internal event counters, not what is reported or sent

severity high · reader's confidence high · testing

**Re-checked: confirmed.**

**Evidence.** application/tests/test_regression_replay.py:103-108 runs `replay.py --scenario X --trt --out` only (no --score, no --timeline); :57-65 and :116-127 compare real/ghost/marginal/total tracks, swap_count, zero_detection_frames and avg_detections. application/tests/replay.py:674-697 derives these from the tracker JSONL via application/analyze_session.py:175-191 (tracks bucketed by MATCH-event count: >=20, 5-19, <5) and from n_detections; replay.py:896 pops the per-frame timeline before writing --out. swap_count is structurally 0 because the correctors are off by default (application/tests/test_tracker_swap_correctors.py:1-7). Windows are 300, 300 and 600 frames. application/tests/scoring.py:5-8 itself calls two of these metrics conflated proxies.

**Why it matters.** A change to the report gate, identity slots, smoothing, box clamp, OSC emit or stall hold cannot move any of the seven numbers. A detection change that trades a drop for a ghost can stay inside the +/-1 and +/-2 bands. The only footage gate on the show path would stay green while the wall output got worse.

**Suggestion.** Store per scenario the score components, the continuity block and the emitted-stream KPIs for both streams. Assert the manifest pass line and a non-regression delta against the committed numbers, and keep the counters as diagnostics. Add hangar-aerial-full and white-duo-full so continuity is gated, not only 300-frame windows.

### tests-harness#4 — Score, pass line and every tuner optimise the tracker's pre-slot stream, not the stream OSC carries

severity high · reader's confidence high · paradigm

**Evidence.** application/src/core/pipeline.py:1044-1055: the slot layer produces out_tracks, `_emit(out_tracks, ...)` sends them, and the function returns scaled_tracks. application/tests/replay.py:619-623 records len(tracks) from process() as `reported`; replay.py:576-578 and application/tests/scoring.py:16-17 still describe that as exactly what OSCSender.send_frame emits. replay.py:912-923 computes SCORE, CONTINUITY and PASS LINE on those rows; the emitted stream only appears in the comparison table (:924-933). application/tests/tune.py:88-93 and application/tests/known_n.py:171-173 score rows built with track_details=False, so `emitted` is not even recorded (replay.py:495-500); application/tests/sweep_project.py:48-57 scrapes the same SCORE. IDENTITY_SLOTS_ENABLED is True (application/src/core/config.py:320). Example: fixture hangar-aerial-win has emitted coverage 1.0 while the tracker window scores drop_rate 0.042 in golden/decomp-phase0/hangar-aerial.run.log.

**Why it matters.** The slot layer adds entry filters, a 2 s hold and a dancer cap. Tracker-stream drop rate overstates drops the slots bridge and misses ghosts the hold keeps alive. Knobs such as tracker_max_age, intermittent confirm and ghost_skeleton_age are tuned, judged and saved into projects against a list the media server never receives.

**Suggestion.** Always record `emitted` in timeline rows. Give score_timeline, continuity_report and evaluate_pass a stream argument that defaults to emitted when slots are enabled. Print the pass verdict for the emitted stream. Make tune.py, known_n.py and the sweep tools minimise the emitted-stream score, and correct the two docstrings.

### tests-harness#5 — Goldens and fixtures pin pre-D33 configs; no committed gate runs the settings a new project ships with

severity high · reader's confidence high · testing

**Evidence.** application/tests/test_d33_defaults.py:312-317 asserts every manifest pins tracker_intermittent_confirm false, and application/tests/scenarios/README.md:27 states the corpus and its goldens did not move when the default flipped. DETECTION_DEFAULTS (application/src/core/config.py:1041-1050) is x@1280, yolo_first, confidence 0.15, intermittent ON. The trio pins confidence 0.5, 0.49 and 0.35 (e.g. application/tests/scenarios/hangar-aerial.json:55-57, 87) and half the corpus pins motion_first. The emitted fixtures were recorded from those pinned replays on 2026-10-06 (their `source` field). golden/*.json has not changed since a3f318d (2026-06-24) although tracker.py and pipeline.py changed on 10-07 and 10-08.

**Why it matters.** The regression suite and the product diverged by design on 2026-10-08. A regression specific to low confidence or intermittent confirm, which is the regime with the most scenery ghosts and the one the far-dancer show depends on, is invisible to every golden.

**Suggestion.** Add a shipped-defaults variant per scenario: keep only venue keys (ROI, gamma, CLAHE, person height, exclusion) and let fill_detection_defaults supply the rest at run time. Golden those and keep the pinned ones as history. Replace the pin test with one that requires at least the gated scenarios to carry no detection keys.

### tests-harness#6 — Ground truth is a count; identity swaps and ghost-for-dancer substitution are not measured

severity medium · reader's confidence medium · testing

**Evidence.** application/tests/scoring.py:143-155 derives deficit and excess from counts only; :159 and :193-198 define an id switch as a change of min(ids), so two dancers exchanging ids 1 and 2 is invisible. application/tests/continuity.py:281-291 (C6) compares id sets, with the same blindness. application/tests/output_quality.py:130-152 builds pseudo-GT tracklets that break on any missed frame and are greedy-linked through contact, so switches at crossings are not reliably counted (my reading; not exercised). application/tests/test_emitted_golden.py:52-59 floors omit id_switches (white-duo-full golden: 13). The position reference is the same run's YOLO detections (replay.py:354-373, continuity.py:113-155): C8 and on_dancer only count frames where YOLO >= 0.5 saw the dancer, and the reference moves with the config under test. The bdx-s5-static-ghost golden shows coverage 0.999 with on_dancer 0.80.

**Why it matters.** If each slot drives one dancer's video, a swap after a crossing is a visible show fault that no metric or gate can detect. The tuner's count objective rewards a config that reports a scenery ghost while the real dancer is lost, which is the dark, far-dancer failure. Position metrics go blind where YOLO does.

**Suggestion.** Label per-dancer positions sparsely (every 20th frame; the GT sheets already use that stride) on the two duo takes and one aerial take, and add an identity-aware metric: share of labelled frames where slot k is within tol_h of dancer k. Use the marker path (test_marker_eval.py) as a true reference on rehearsal takes. Add a spatial term to the tuner objective and id_switches and gap_max to the KPI floors.

### tests-harness#7 — Emitted-stream goldens: exact compare runs only on dev37, floors record current behaviour, and the fixture path omits the shipped evidence channels

severity medium · reader's confidence high · testing

**Re-checked: confirmed.**

**Evidence.** application/tests/test_emitted_golden.py:25-27 skips the exact compare when numpy/scipy differ from the stamp. All six goldens are stamped numpy 2.2.6 / scipy 1.15.3, while .github/requirements-ci.txt:4 and application/pyproject.toml pin numpy < 2.0, so it is skipped in every CI leg and on the laptop. FLOORS (:41-49) sit 0.5 to 2 points under current KPIs and below the manifests' own lines: white-duo-full scores coverage 0.8646 and gap_max 8.43 s against coverage_min 0.98 and zero gaps >= 1 s. application/tests/emitted_golden.py:98-123 with application/tests/slot_replay.py:36-67 re-implements the pipeline glue without bounds, belt, foreground, zone_ok or box confidence (compare application/src/core/pipeline.py:1482-1526 and application/src/core/identity_slots.py:1432-1471). Eight slot commits on 2026-10-07 (foreground, backed belt hold, confidence-weighted entry, re-entry prior) landed with no golden change; the last golden commit is 912e940 on 10-06.

**Why it matters.** In CI the slot layer is protected by four loose floors per fixture, so id switches or the longest gap can double unnoticed. The demo-proxy take fails its own show line while the suite is green. The foreground and belt paths, on by default, are covered by no golden.

**Suggestion.** Build the goldens under the pinned production stack and run the +/-1 px compare everywhere, downgrading to a warning only on a proven tie-break difference. Route fixtures through candidate_from_track and a shared slot-driving function used by the pipeline. Record bounds and box confidence in fixture rows. Split floors into a show line from the manifest (reported, allowed to fail visibly) and a no-regression ratchet.

### tests-harness#8 — CI skips the riskiest glue although it needs no GPU, including every camera-stall test

severity medium · reader's confidence high · testing

**Re-checked: confirmed.**

**Evidence.** application/tests/test_camera_stall.py:107 calls pytest.importorskip("torch") at module level, which skips the whole module, including the six pure CameraStallClock tests at :43-100 that import only core.camera_stall. The same pattern is in test_identity_slots_pipeline.py:12, test_box_clamp.py:13, test_transforms.py:15, test_output_smoothing.py:14, test_new_project_rig.py:22 and test_input_transform_app.py:16 (about 80 tests), although they build FrameProcessor(model=None, use_gpu_path=False) (test_camera_stall.py:144-150). Ten GUI smoke files skip on dearpygui (e.g. test_gui_full_build_smoke.py:9) although they only create a headless context. .github/requirements-ci.txt installs neither package. By grep, roughly 115 of 806 test functions never run in CI.

**Why it matters.** USB camera stalls are a named field condition, and the D34 hold logic, the slot-to-OSC glue and the whole-window GUI build are verified only on a developer machine. A duplicate DearPyGui tag or a broken output stage reaches the laptop with CI green.

**Suggestion.** Move the importorskip in test_camera_stall.py below the clock tests into a fixture, or split the file. Add one CI job with CPU torch, ultralytics and dearpygui (pyproject already declares the PyTorch CPU index) that runs the model-less pipeline tests and the GUI smoke tests, at least on Windows. Longer term, move ScaledTrack and ProcessingSettings into a torch-free module.

### tests-harness#9 — Replays run on a synthetic uniform clock; a stall inside a recorded take cannot be reproduced, and the D34 footage gates sit outside the suite

severity medium · reader's confidence medium · robustness

**Evidence.** application/tests/replay.py:376-385 sets the output clock to frame/fps; :704-716 takes fps from the manifest or the .meta actual_fps. application/src/core/video_recorder.py:170-176 and :186-205 store frames/elapsed and no per-frame timestamps. Stalls enter only through a hand-typed `--stall FRAME:SECONDS[:gap]` (replay.py:407-415, 588-607); grep finds no test or manifest that passes stalls. `git show --stat ac685e7` shows the D34 stall replays and gates were committed under tmp_analysis/stall_d34, not as pytest. I did not read the camlog sampler in recording_controller.py; it is described as exposure/gain telemetry.

**Why it matters.** The laptop's real 1.6-1.7 s stalls are flattened into a lower average fps on replay, so the tracker sees a teleport and the slot timers run on the wrong time. Stall behaviour on real footage is verified only by one-off scripts that will not run again.

**Suggestion.** Have the recorder write a per-frame sidecar (host monotonic time, IDS frame id and device timestamp, which ids_camera already reads for stall diagnostics). Let replay drive the output clock and note_camera_frame from it by default, falling back to frame/fps. Move the D34 gate into tests/ as a rig-gated pytest over one take with a real stall, plus a CI fixture with an injected one.

### tests-harness#10 — The tracker is tested through private methods; no test drives association with two dancers

severity medium · reader's confidence high · testing

**Evidence.** By grep, only application/tests/test_tracker_swap_correctors.py:83, 91 (correctors replaced by spies, one frame), test_cont10_logging.py:139, 183 and test_tracker_own_height.py:49-125 call DancerTracker.update(); the last two use one detection per frame. The other tracker tests call _collect_confirmed_tracks, _try_resurrect, _merge_takeover_duplicates, _lazy_bridge_with_motion and _apply_fractional_occlusion_aging, and set _warmup_score, _frames_since_skeleton, _ever_reported, kf.x[0] and frame_count = 10_000 directly (test_tracker_cont1.py:54, 74, 114, 163; test_tracker_takeover_merge.py:49-50, 123; test_tracker_max_persons.py:40-44). test_tracker_swap_correctors.py:6-7 says the correctors' internals are corpus-measured, not unit-tested. The IDS tests build object.__new__(IDSCamera) and set 14 private fields (test_ids_stream_diag.py:51-68).

**Why it matters.** The cost matrix, Hungarian passes, forced and fallback updates, creation gate and dormant/resurrect lifecycle in the 3,800-line tracker.py have no automatic net: rig replays are manual. Any internal refactor breaks dozens of tests without a behaviour change, which pushes towards rewriting tests alongside the code.

**Suggestion.** Add a synthetic-trajectory harness: scripted dancer paths rendered to detections with configurable miss rate, jitter, static ghosts and stalls, driving tracker.update plus IdentitySlots on CPU. Assert show-level invariants: N ids, no swap after a crossing, re-acquire within the hold, static ghost yields. Commit one small detect-pass fixture (dets plus motion gray at the motion scale) so replay_gpu_cached runs the whole post-YOLO chain in CI.

### tests-harness#11 — Replay builds its processor with a hand-mirrored copy of the app's config applier, which has already drifted twice

severity medium · reader's confidence medium · maintainability

**Evidence.** application/tests/replay.py:196-351 maps config keys onto ProcessingSettings and the tracker "exactly as app._apply_config_without_model does". Past drift: replay.py:330-336 (max_age applied before the mode switch) and application/src/app.py:1447-1461 ("The replay harness already applies these; the LIVE app dropped them, so a project tuned ... never took effect at show time"). Today replay reads tracker_own_height_gates, auto_height, height_guard, belt_backing and entry_min_travel_h (replay.py:284-287, 311-312), which grep does not find in app.py or runtime/config_manager.py. The app applies bg_subtract_enabled (app.py:1580-1581), which replay ignores although application/tests/detect_cache.py:64 keys caches on it. The key diff is a regex over the two functions; the app may apply some keys elsewhere.

**Why it matters.** "Replay equals what the show runs" holds by convention only. A setting validated offline with --set and stored in a project can be silently ignored live, which is the failure the app.py comment records.

**Suggestion.** Move the mapping into one function in core, e.g. apply_detection_config(processor, tracker, enhancer, flat_config), called by both app and replay with the GUI sync as an optional callback. Until then, add a test that feeds one config with every known key through both paths and diffs the resulting settings and tracker attributes.

### tests-harness#12 — The soak covers the pipeline on PyTorch only, not the application that runs for hours

severity medium · reader's confidence high · robustness

**Re-checked: confirmed.**

**Evidence.** application/tests/soak.py:204-205 has no --trt option, so even with --gpu it runs the FP32 PyTorch backend; :243-261 is a loop of cap.read and proc.process. application/tests/replay.py:258 forces osc_enabled=False. No camera thread, DearPyGui frame, recorder, RemoteApi, web monitor, event-log rotation or MainLoop is involved. The verdict (soak.py:335-348) looks at RSS slope, fps trend and a 120 s sentinel. The default invocation is also currently broken (second finding).

**Why it matters.** The long-run risks of a show night live elsewhere: the TRT execution context, IDS acquisition and reconnect, DPG textures, tracking-log growth, the OSC socket. "Soak PASS" would say little about those.

**Suggestion.** Add --trt and make it the default. Add an app-level soak mode that starts the real app headless on looped playback with a scripted fake camera that injects stalls and reconnects, driven through the remote API, and reads the app's own ops alerts and crash marker as the verdict.
