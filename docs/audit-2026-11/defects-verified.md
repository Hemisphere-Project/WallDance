# Verified defect claims (61)

*Second pass: five verifiers re-read the cited code for 61 blind-scan claims, reproduced them where a CPU-only reproduction was cheap, and judged the realistic field impact with the project context. Result: 53 confirmed, 7 partly, 1 by design, 0 refuted.*

Reproduction scripts: `tmp_analysis/audit-2026-11/verify_*/`.

## runtime+config

### runtime-core#4 — A project switch loads the empty-wall plate from the previous project's folder

**Verdict: confirmed.** Already in the docs: no

**Evidence.** config_manager.py:268 calls apply_config(config); _current_project is assigned only afterwards at :271. Inside the apply, app.py:1497-1498 calls _load_project_plate(), whose _plate_dir() (app.py:1839-1841) reads configs._current_project, 'default' at construction (config_manager.py:102). _load_project_plate has no other caller. Field log tmp_analysis/laptop_0810/03_live_walldance_20261008_125451.log:67,84: project 'default', plate loaded from projects\default\plates\latest.npz, so the laptop's use of 'default' has masked the bug so far.

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc4.py (real ConfigManager switch + real WallDanceApp._plate_dir/_load_project_plate, fakes around, temp projects dir). Printed: "cold start -> ShowA: project_during_apply='default' project_after='ShowA' fg_plate_path=''"; "switch -> ShowB: project_during_apply='ShowA' fg_plate_path=ShowA/plates/latest.npz"; "reload ShowB: fg_plate_path=ShowB/plates/latest.npz". Housekeeping for the whole pass: `git status --short` printed nothing after every run. Unrelated to my scripts: an empty gitignored folder tmp_analysis/remote-runs/20261118-102250 appeared at 10:22:50; only extra/wdremote.py creates such folders and none of my scripts import it.

**Field impact.** Every start on a named project (the 'New' button of 63f4989 makes that normal) reads default/plates: on the laptop a 10-07 plate sits there, otherwise none. Ghost veto and foreground-backed hold then use another scene or are off, and cannot be recaptured mid-show. Visible only as a status line or a light alert.

**Fix sketch.** Set _current_project (and the recorder project) before apply_config, or pass the project name into _plate_dir. Add a test that a switch leaves settings.fg_plate_path under the new project's folder, and log the plate path with the project at load.

### runtime-core#5 — The project switch is not transactional: an exception wedges the loop, and a failed model load leaves mixed state

**Verdict: confirmed.** Already in the docs: partly: docs/audit-2026-10/03-architecture.md §2.4 (line 236) and ARCH-8 item 5 (line 429) note the switch runs outside the guarded drain; the stuck _model_loading latch and the mixed state are not recorded

**Evidence.** config_manager.py:172 sets _model_loading=True and :178-179 marks the camera closed; they are cleared only on the return paths :192, :260, :320; the finally at :145-149 only pops the watchdog. main_loop.py:558-562 clears the pending switch before the call, so after one caught exception every tick idles at :594-597. Profiles (:197-198), TRT intent (:208) and imgsz (:215-216) are committed before the model load; :258-263 restores none and shows no toast. Not fully silent: one tick_error toast is raised.

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc5.py (real MainLoop._guarded_tick, ConfigManager, HealthMonitor; simulated clock). File with "model": null: "simulated 40.0 s = 2502 ticks, idle renders = 2501; _model_loading = True; app.running = True; tick_fail_streak now = 0; alerts: tick_error (AttributeError: 'NoneType' object has no attribute 'replace'), camera_down 'Camera down for 15s - check USB3/power'". Same outcome through the real _apply_config_without_model with "crossval_skel_min_kpts": "four" (ValueError). tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc5b.py (model load returns False): "switch ok: False | toasts: [] | _current_project = ShowA | _active_profile = rehearsal (ShowB's) | settings.imgsz = 960 | _trt_requested = True"; the following Save wrote ShowB's 'show' bundle into ShowA's new file.

**Field impact.** Needs a bad value in a project file or a raising call during a load: rare with app-written files, plausible with hand-edited or tool-written ones. Result: RUN badge, frozen preview, no OSC and a wrong 'Camera down' alert until someone loads a project again. The operator sees no cause.

**Fix sketch.** Wrap the switch body in try/finally that clears _model_loading and reopens the camera; parse the new config into locals and commit profiles, imgsz, path and TRT intent only after the model load succeeds. Toast the failure, reopen the picker, and add a 'RUN but pipeline idle for N s' health alert.

### runtime-core#6 — RUN/STANDBY has two unsynchronised copies; the Go-Live check never fires on a normal start; STANDBY can still emit OSC

**Verdict: confirmed.** Already in the docs: partly: ROADMAP.md:161 lists '/walldance/clear on STANDBY' as a small fix; D13 (ROADMAP.md:235) says 'restart into STANDBY' while the code always boots in RUN. The remote desync, OSC during a STANDBY Calibrate and the missing check on a normal start are not recorded

**Evidence.** ui/adapter.py:411-413 maps StateChanged to `lambda e: None`; the only sync is GUI to runtime (adapter.py:140-141). services/remote_api.py:61 and :142-144: SetState is CONTROL, free from STANDBY. Both copies boot in RUN (app.py:521, gui.py:316) and the Go-Live check needs a non-RUN to RUN edge (app.py:2227-2232). OSC is gated only by `bool(self.osc) and settings.osc_enabled` (pipeline.py:1027) while main_loop.py:813-816 processes in STANDBY during Calibrate, the dancers run and plate capture. Nuance: the on-demand check also runs on entering rail phase 'wall' (gui.py:537-541).

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc6.py (real DpgUiAdapter, EventBus, RuntimeAPI and WallDanceApp._cmd_set_state; fake GUI object). Printed: "boot: runtime=RUN gui._system_state=RUN readiness runs=0"; "remote SetState('standby'): runtime=STANDBY gui._system_state=RUN gui calls=[] readiness runs=0"; "remote SetState('run'): runtime=RUN gui calls=[] readiness runs=1". The OSC-in-STANDBY part was not reproduced: read only.

**Field impact.** Remote SetState is routine in developer sessions: the operator's badge can then show RUN with no OSC, or STANDBY with OSC flowing, and nothing corrects it until the next click. After any restart the Go-Live toast never appears. A STANDBY Calibrate with people on the wall drives TD.

**Fix sketch.** Bind StateChanged to a GUI setter that updates the badge and buttons without firing the callback. Run the readiness report once after the first project load in RUN, and gate OSC emission on RUN explicitly in the processor (with an explicit exception if a calibration should emit).

### runtime-core#7 — Config save and apply are separate hand-written lists that have drifted, and the offline tools use a third

**Verdict: confirmed.** Already in the docs: partly: ARCH-10 'one config applier' (docs/audit-2026-10/03-architecture.md:431, AUDIT_2026-10.md:105, ROADMAP.md:170) covers replay's own applier, and CONTEXT §7 lists tracker_own_height_gates; the keys Save drops are not recorded

**Evidence.** The apply reads tracker_swap_correctors and three crossval_* keys (app.py:1457-1467) and dial_b_relevant (:1503); none is in _get_saveable_config (app.py:1222-1313), and Save rebuilds the file from that dict (config_manager.py:346-350, :374). box_clamp_enabled and output_smoothing_l are in neither. tests/replay.py:261, :284-287, :311-312 applies box_clamp_enabled, belt_backing, entry_min_travel_h, auto_height, height_guard, tracker_own_height_gates; grep finds none of the last five in app.py or runtime/. Partial applies still run app.py:1503 and :1560-1561.

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc7.py. AST of the two real functions: "saved keys: 75  keys the apply reads: 79; read by apply, NOT saved: ['crossval_motion_min_ratio', 'crossval_skel_min_conf', 'crossval_skel_min_kpts', 'dial_b_relevant', 'tracker_distance', 'tracker_swap_correctors']" (tracker_distance is a legacy no-op); "saved, NOT read by apply: ['fp16', 'use_tensorrt']". Then the real _cb_apply_known_n followed by the real Save: crossval_skel_min_kpts 10 -> '<absent>', crossval_motion_min_ratio 0.04 -> '<absent>'.

**Field impact.** Latent today: none of the 198 project files mirrored on dev37 carries these keys, and Known-N and auto-tune sit in Expert tools (D29). If used, the tuned gates disappear at the next Save and the next restart runs code defaults with no sign. Box clamp and L resetting is harmless (shipped defaults, D30).

**Fix sketch.** Add the five keys to _get_saveable_config and a round-trip test asserting that every key the apply reads survives load then save. Longer term (ARCH-10): one key table that drives save, apply and replay's _build_processor.

### runtime-core#10 — Offline tune and replay jobs are fire-and-forget subprocesses that RUN and Quit do not stop

**Verdict: confirmed.** Already in the docs: yes, open: ARCH-16 (docs/audit-2026-10/03-architecture.md:437 'single-flight, warn or block entering RUN while one runs, kill the child on quit ... move the scripts from tests/'; AUDIT_2026-10.md:200; ROADMAP.md:171, :190)

**Evidence.** app.py:2343-2349, :2442-2448 and :2572-2578 check STANDBY once and start a daemon thread; the workers call subprocess.run with 300/900/2400 s timeouts (:2413-2417, :2495-2499, :2595-2598), keep no handle and no running flag. _cmd_set_state (:1056-1062) and _shutdown (main_loop.py:402-443) ignore jobs. The scripts resolve to application/tests/ (:2410, :2492, :2592).

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc10.py (real launchers on a fake app, worker body replaced by a sleep). Printed: "STANDBY, after 2x Known-N + sweep + dry-run: ['CalibSweep', 'DryRunReplay', 'KnownNTune', 'KnownNTune']"; "after SetState('run'): state = RUN jobs still running: [same four]"; "a new job in RUN is refused: True". Same subprocess-in-daemon-thread pattern across a parent exit on Linux: the child was still alive with ppid 1. Windows behaviour not run.

**Field impact.** Only if an expert tool is started during setup and RUN is pressed before it ends (Known-N: up to 40 min). It then shares the GPU with YOLO and TD, costing fps and heat, and outlives a quit. No indication to the operator. Unlikely on a show night, likelier in remote tuning sessions.

**Fix sketch.** As ARCH-16: one JobRunner that owns the Popen handles, single-flight per job type, warns or cancels on entering RUN, terminates children in _shutdown and exposes job_running in the status. Move the three entry scripts out of tests/.

### runtime-core#12 — The IDS camera is never closed at shutdown, and 'closing the camera' for a model load only flips a legacy flag

**Verdict: confirmed.** Already in the docs: no

**Evidence.** _shutdown (main_loop.py:426-441) releases only app.camera.cap; neither it nor _cb_quit (app.py:2133-2136) calls unified_camera.close(), leaving IDSCamera.__del__ (camera/ids_camera.py:381-387) with a daemon acquisition thread (:1074). _ModelCameraAdapter.close (app.py:371-373) and config_manager.py:175-179 call CameraManager.close (camera/camera_manager.py:306-313) and flip state.is_open only. Not checked: the IDS SDK's behaviour at process exit. The 10-08 laptop logs end with a normal 'WallDance stopped.'

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc12.py. Real MainLoop._shutdown() on a recording fake: "calls: ['api.drain', 'tracker.logger.close', 'watchdog.stop', 'recorder.close', 'legacy camera.cap.release', 'ui.destroy']; UnifiedCamera touched: False". Real _ModelCameraAdapter.close(): "calls: ['legacy CameraManager.close'] | legacy flag is_open = False | UnifiedCamera.is_open = True". The consequences on the real IDS device were not reproduced (no camera here).

**Field impact.** No harm observed: field logs show clean stops. The risk named (exit fault, camera busy at restart) is unproven. During a TRT build or project load the IDS keeps streaming while the app's flag says closed; harmless for frames, but the flag the loop and health monitor trust disagrees with the device.

**Fix sketch.** Add a unified_camera.close() step to _shutdown before the UI destroy. Give the camera a single owner of is_open with explicit pause/resume for model loads instead of the mirrored legacy flag.

### config-ops#3 — Save silently drops five keys that load applies: known-N tuning and hand-set tracker switches vanish at the next Save

**Verdict: confirmed.** Already in the docs: no (the code comment at app.py:1257-1260 records the same class of bug fixed for two other keys; the docs do not list these five)

**Evidence.** Load applies tracker_swap_correctors and three crossval_* keys (app.py:1457-1467) and dial_b_relevant (:1503); the saved dict (app.py:1222-1313) has none. tests/known_n.py:57-58 searches crossval_skel_min_kpts and crossval_motion_min_ratio; _cb_apply_known_n (app.py:2628-2637) writes them to the file; _cb_apply_calib_sweep (:2539-2546) writes dial_b_relevant. Save passes only saveable_config() to structure (config_manager.py:346-350), which is where they are lost (structure itself keeps whatever it is given). app.py:1257-1260 shows the same class fixed for two keys.

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc7.py (real ConfigStore, ConfigManager, _cb_apply_known_n, Save). Printed: "2. after Known-N Apply {'crossval_skel_min_kpts': 10, 'crossval_motion_min_ratio': 0.04, ...}"; "3. + hand-set swap correctors {... 'tracker_swap_correctors': True ...}"; "4. after the next Save {'crossval_skel_min_kpts': '<absent>', 'crossval_motion_min_ratio': '<absent>', 'tracker_swap_correctors': '<absent>', 'tracker_intermittent_confirm': True, 'tracker_ghost_skeleton_age': 15}".

**Field impact.** Latent: no project file mirrored on dev37 (198) holds any of the five keys, and the tools that write them are in Expert tools (D29). If Known-N is used on a show project, its gates silently revert at the first Save plus restart; the JSON history looks normal.

**Fix sketch.** Add the five keys to _get_saveable_config and a round-trip test (load a file holding every key the apply reads, save, compare). Better: have ConfigManager carry unknown shared keys of the loaded file through structure() so an unknown key is preserved, not dropped.

### config-ops#4 — The active lighting profile and all live changes exist only in memory until a manual Save; a restart can come back on the wrong profile

**Verdict: confirmed.** Already in the docs: no

**Evidence.** _cb_profile_switch (config_manager.py:352-370) sets _active_profile and applies, never saves; a load takes active_profile from the file (:198). The only file writers are :374, :623 and app.py:2546, :2636. Also memory-only until Save: the guard-adopted height (pipeline.py:1446-1451), box clamp (app.py:1786-1788), IDS auto flags (camera_controller.py:414, :438). No dirty/unsaved/autosave in the config path (grep). Caveat: the profile switch now sits in Expert tools (gui_builder.py:578-580, :755-764; D29).

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_co4.py (real ConfigManager and ConfigStore; saved on REHEARSAL with 8000 us / 0 dB). Printed: "after the switch: active='show' live exposure=50000 us gain=24.0 dB | project files before/after: 1/1"; "after a restart: ok=True active='rehearsal' applied exposure=8000 us gain=0.0 dB gamma=1.0".

**Field impact.** The dusk profile scenario needs the expert switch, off the operator rail since D29, so unlikely. The general case is real: an unsaved Calibrate result, a moved slider or a guard-adopted height is lost at any crash-restart, and the app resumes in RUN on older values with no 'unsaved' hint.

**Fix sketch.** Write session facts (active profile, adopted height) to a small atomic per-project session file on change, or autosave on profile switch and on Calibrate accept. Add an 'unsaved changes' chip that compares saveable_config() with the last loaded or saved dict.

### config-ops#5 — The watchdog's stack dump silently fails under the log tee, and a Python-thread watchdog cannot fire while the GIL is held

**Verdict: confirmed.** Already in the docs: no; docs/audit-2026-10/03-architecture.md:134 still describes the watchdog as 'hang >10 s -> stack dump'

**Evidence.** ops_monitor.py:594-600: the default hang handler calls faulthandler.dump_traceback(file=sys.stderr, all_threads=True) inside `except Exception: pass`. services/app_log.py:29-68 and :81-82 replace sys.stderr with _Tee(io.TextIOBase), which has no fileno(). app.py:735 uses the default handler and the tee is on by default (config.py:589). crash_marker.py:101 points the reader to a faulthandler dump. The GIL half is a general CPython property; which IDS, TensorRT or DearPyGui calls hold the GIL was not checked.

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_co5.py (app_log.install into a temp dir, Python 3.10.19). Printed: "sys.stderr.fileno() -> UnsupportedOperation fileno"; "direct dump_traceback(file=sys.stderr) -> UnsupportedOperation fileno"; after LoopWatchdog._default_on_hang(12.3) the log file holds only the header and "[Alert] Main loop hang: no heartbeat for 12.3s - thread stacks follow"; "stack frames in the log: False". Watchdog with hang_s=1: "3 s block releasing the GIL (sleep): [('hang', 1.0, 1.0), ('resume', 3.0)]"; "11.7 s block holding the GIL (one C call): [('hang', 11.66, 11.67), ...]", i.e. reported only after the block ended.

**Field impact.** A main-loop hang has no recovery, and since the tee shipped (10-06) its only diagnostic is an empty promise: one alert line, no stacks. Nobody notices until the first field hang, which then cannot be attributed to camera SDK, CUDA or GUI. Cost: a lost diagnosis, not a lost show.

**Fix sketch.** Pass the real log file handle (exposed by app_log) to faulthandler.dump_traceback, or arm faulthandler.dump_traceback_later from the 1 Hz ops tick, which runs in a C thread without the GIL. Add a test that installs the tee and asserts the hang handler writes a stack.

### config-ops#6 — A project switch is not transactional: an exception mid-way leaves the app idling with the camera closed, and inactive profile bundles are applied unvalidated

**Verdict: confirmed.** Already in the docs: partly, as runtime-core#5: docs/audit-2026-10/03-architecture.md line 236 and ARCH-8 item 5 note the switch runs outside the guarded drain; the unvalidated inactive bundle is not recorded

**Evidence.** First half as runtime-core#5 (config_manager.py:172-179, :196-216, :268; main_loop.py:558-562, :594-597). Second half: validate_flat runs only on the flattened active profile (config_manager.py:199); both bundles are stored raw (:197); _cb_profile_switch sets _active_profile at :359, then applies the raw bundle at :362-365. _BOOL_KEYS has four keys (config_schema.py:83), so bool(config['roi_enabled']) at app.py:1535 reads 'false' as True. Nuance: 'camera closed' is the legacy flag only; the IDS object is not closed (runtime-core#12).

**Reproduction.** Wedge: see runtime-core#5 (tmp_analysis/audit-2026-11/verify_runtime_config/repro_rc5.py). Second half: tmp_analysis/audit-2026-11/verify_runtime_config/repro_co6.py (real ConfigManager + real applier on a mock app; the same bad values in both bundles). Printed: four '[Config]' warnings for the active bundle (gamma 99 clamped to 4.0, confidence 'abc' dropped, ...); "stored inactive bundle 'rehearsal': {'gamma': 99, 'confidence': 'abc', 'mog2_var_threshold': 'x', 'ids_exposure_us': 50000000.0}"; "profile switch -> raised: ValueError: could not convert string to float: 'x' | _active_profile = rehearsal"; "settings.confidence left by the partial apply: 'abc'"; "roi_enabled applied as True from the file's 'false'".

**Field impact.** App-written files carry valid bundles, so this needs a hand-edited or tool-written file, or a range tightened between releases. Then: a frozen load, or a half-applied profile whose label is wrong and whose next Save mixes bundles. Low likelihood, hard to diagnose on site.

**Fix sketch.** Run validate_flat on every bundle at load and before a profile switch, and set _active_profile only after the apply succeeds. Put the switch body in try/finally as in runtime-core#5.

### config-ops#8 — 'Latest config' is chosen by file mtime and project identity by _meta.project, so rename, copy or restore can load an old save or write into another project

**Verdict: confirmed.** Already in the docs: no; docs/audit-2026-10/03-architecture.md:164 states the mtime precedence as a fact and tests/test_config_store.py:111-124 pins it as intended

**Evidence.** list_config_files sorts by (mtime, name) (config_store.py:137-143). rename_project rewrites every config with atomic_write_json in os.listdir order (:296-309), so all mtimes become 'now' in listing order. infer_project_from_config prefers _meta.project over the folder (:350-356) and feeds _current_project (config_manager.py:271), hence recordings, plates and saves. The readiness save-age row reads the same mtime (app.py:2272-2275). tests/test_config_store.py:111-124 pins 'mtime decides' but never renames after several saves.

**Reproduction.** tmp_analysis/audit-2026-11/verify_runtime_config/repro_co8.py (real ConfigStore; saves marker 1 and 2 in 'test', rename to Show, save marker 3, rename to Show2). With an alphabetical listdir (as NTFS returns): "after rename test -> Show: latest = marker 2" (correct), "after rename Show -> Show2: latest = ('test_20261118_103304.json', 2) (newest save is marker 3)". With this machine's own listdir order the first rename already gave marker 1. Folder copy: "picker lists: ['ShowA', 'ShowB']; launch 'ShowB' -> file ShowB/ShowA_20261118_103303.json -> infer_project_from_config = 'ShowA'".

**Field impact.** Needs a second rename after new saves, or a duplicated folder: plausible now that the laptop's long-used 'default' project will be renamed or copied per venue. A restart then silently loads an older calibration, or edits to the copy land in the original. Nothing on screen shows it.

**Fix sketch.** Order saves by the timestamp suffix of the filename with _meta.saved_at as tie-break, and derive the project from the folder when the file is under the projects directory, which removes the per-file rewrite on rename. Show folder and file in the load toast.

### config-ops#10 — Durations are configured in frames with five different fps assumptions and are not rescaled, so hold and age windows stretch when fps drops

**Verdict: confirmed.** Already in the docs: partly: the stall case is known (CONTEXT §7 'Kalman treats a 1.7 s stall as one frame'; PLAN_25M_2026-10.md:572); the general frames-versus-seconds drift is not recorded

**Evidence.** config.py:175 TRACKER_MAX_AGE = 45 '~3 sec at 15 FPS'; :181-182 '~10 sec at 15 FPS'; :350 '2 s at 20 fps'; :600 '~3s @30fps'; :680 '~10 s @ 24 fps'; :980 '~1.2 s at 25 fps'; :1000 '~2 s @ 20 fps'; CAMERA_FPS = 30 (:25) against IDS_MAX_FPS = 20 (:107). core/tracker.py has no 'fps' match; DancerTracker.update (tracker.py:2340) takes no time argument and the Kalman uses dt = 1.0 (:330). Newer constants are in seconds (IDENTITY_SLOTS_COAST_S, config.py:323).

**Reproduction.** not reproduced: read only (grep -c -i fps core/tracker.py = 0; the seven config.py comment lines printed as quoted)

**Field impact.** The live stream is source-bound near 20 fps, so counts are stable in normal running. The drift bites when fps sags (heat, TD load; the alert fires only below half the baseline) and when constants tuned on 24-30 fps April footage are trusted at 20: longer ghost and warm-up windows, unnoticed.

**Fix sketch.** Define one nominal fps, express tracker durations in seconds and convert them to frames at tracker reset from the measured rate. Have the readiness check warn when live fps deviates from nominal by more than about 15 %.

## pipeline+tracker

### detection-pipeline#1 — The person-height size gate only runs when a frame has two or more detections

**Verdict: confirmed.** Already in the docs: Stated as a fact, never as a defect or a decision: core/config.py:361, core/auto_height.py:5-6, PLAN_25M_2026-10.md §C.12 (l.343), §C.13 (l.408), §C.14 (l.462-466), OSC_CONTRACT.md:292-293 ("frames with >= 2 detections"). No test references _filter_duplicate_detections.

**Evidence.** pipeline.py:2388-2389 returns before the gate at 2397-2409 (called at :748). No other code gates on the ratios: the height guard (1414-1444) and the ops alarm (main_loop.py:1310) only count. The early return dates from 2025-12 (aa27a1a) and carries no comment on intent. One overstatement: solo takes do meet the gate on multi-box frames (PLAN_25M §C.14: 18-33 of 44-62 frames).

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/dp1_sizegate.py (real FrameProcessor, model=None). Person height 120, gate 36-300 px: lone 600 px box -> kept; lone 18 px box -> kept; each with a 120 px dancer in view -> dropped; 600 + 18 px -> []. Full chain (gate -> replay_gpu_cached -> identity slots -> recording OSC client): a lone 600 px person gets `/walldance/count [1, 1]` from frame 16; with a dancer in view it never gets a track. Repo untouched: `git status --short` empty after every run of this pass.

**Field impact.** Every solo passage runs ungated; the gate bites only when two boxes coexist. A wrong height (the known walk-out adoption) is invisible in solo rehearsal, then drops both dancers on duo frames. All October validation was solo. The operator sees flicker with no cause shown.

**Fix sketch.** Take the size gate out of the dedupe and run it for any detection count, before the `<= 1` return; or write the lone-detection exemption down and make the height guard and alarm use the same rule. Add a unit test and replay one solo take with the gate active before changing the default.

### detection-pipeline#3 — Dedupe runs before any track knowledge and splices keypoints between detections; the tracker's position is the keypoint mean

**Verdict: confirmed.** Already in the docs: Only the step itself: audit-2026-10/01-continuity.md §1.1 (l.80, "small/far figures die here"). The track-blindness, the keypoint splice and the EMA start are not recorded; CONT-3 covers blob-vs-YOLO tiering, not this.

**Evidence.** pipeline.py:2411-2429: an undersize detection within 1.5 ph donates every higher-confidence keypoint, no position check. 2434-2471: fewer than 8 keypoints or mean confidence < 0.5 is deleted within 1.5 ph of a strong skeleton. 2473-2492 with 2538-2547: containment splice in place. The function has no tracker access. tracker.py:547, 367-377: the measurement is the confidence-weighted keypoint mean; its EMA is the OSC centroid (pipeline.py:2114). 2346-2348: a new cell starts at 0.65 x raw motion.

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/dp3_dedupe.py. A 30 px box 1.2 h from a dancer: 17/17 keypoints replaced, measured centroid +1.20 h (with 2 better keypoints: +0.21 h, box unchanged). Full-size dancer B at mean keypoint confidence 0.45: deleted at 1.0 h and 1.4 h from A, kept at 1.6 h or at confidence 0.55; B with 7 confident keypoints: deleted. Contained 0.6 h box: suppressed, A's centroid moves 12 / 28 px. Two confirmed tracks, B weak for one frame: 1 detection reaches the tracker. Frequency on footage: not measured.

**Field impact.** In a duo closer than 1.5 body heights, a weakly seen dancer (dim IR, partial occlusion) loses the detection before the tracker; fragment boxes pull the centroid. Frequency on show footage is not measured (two-dancer takes unprocessed, t-020). Seen as a drop or a twitch.

**Fix sketch.** Give the dedupe the confirmed-track positions: never suppress a detection that sits on a track other than the strong neighbour's, and accept a donor keypoint only inside the recipient box plus a margin. Log per-frame counts of each rule in FRAME_SUMMARY, then measure them on the duo takes.

### detection-pipeline#4 — The display gamma slider is an input of the motion model, with no reset or recalibration when it changes

**Verdict: partly.** Already in the docs: The coupling is documented as intended: TUNING.md l.215 ("motion is fed a gamma-only gray ... Don't undo this"), audit-2026-10/01-continuity.md l.173, calibration_flows.py:430-433. The missing MOG2 reset and the stale motion calibration after a gamma change are not recorded.

**Evidence.** Holds: pipeline.py:1786, 1829-1837 apply `enhancer.gamma` to the MOG2 / frame-diff gray; app.py:1637-1641 only sets it, as do the config load (app.py:1403) and Calibrate's ladder (calibration_flows.py:470-477); no motion reset. `MotionModel.fixed_gamma` (motion_model.py:64) is unused (pipeline.py:436-438). The luma of that gray also flips the low-light switch (motion_detector.py:204-209, 269-273). Does not hold: re-convergence is ~110 frames, not ~1000, and the varThreshold mismatch did not show.

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/dp4_gamma.py (real FrameProcessor motion feed, synthetic dark wall, sigma 2 DN, var 16 @ scale 0.7). Gamma 1.0 -> 1.5 and 0.73 -> 1.8: MOG2 mask 100 % foreground and presence ratio 1.000 in a dancer box for 111 frames (5.5 s at 20 fps), frame-diff 1.000 on the switch frame only, motion-gray luma crosses 55 (low-light var x2 turns off); foreground back to 0.00 % afterwards. Gamma 1.0 -> 1.1: no effect.

**Field impact.** A large gamma move (Advanced slider, profile switch, Calibrate's ladder) blinds MOG2 for about 5.5 s: every unmatched track is presence-bridged, and for one frame every box passes the motion gate. Small moves do nothing. Rare mid-show; nothing tells the operator.

**Fix sketch.** Store a motion gamma with the MOG2 calibration (`MotionModel.fixed_gamma` already exists) and leave the display gamma to YOLO and the preview. If they stay coupled, reset the motion model and mark the motion calibration stale when gamma moves by more than a small step.

### detection-pipeline#6 — With output smoothing L > 1, an empty wall is never reported on OSC

**Verdict: partly.** Already in the docs: D30 only (PLAN_25M_2026-10.md §C.12-C.13 l.377, 385; OSC_CONTRACT.md l.14-16; gui_builder.py:1032-1034): the L slider left LIVE and L = 1 is the operator output. The missing count 0 at L > 1 is not recorded.

**Evidence.** Holds: pipeline.py:1065-1071 sends only `if lagged_tracks:`; at L = 1 (1061-1064) the empty list is sent and osc_output.py:181-182 emits count [0]. Does not hold: "operator-settable". Since D30 no widget calls `_on_output_smoothing_change`; L > 1 is reachable only by the remote `SetOutputSmoothing` (remote_api.py:65) and the replay tools, and it is not saved in the config.

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/dp6_empty.py (full post-YOLO chain, slots on, recording OSC client; one dancer 3 s, then an empty wall 6 s). L=1: 180 `/walldance/count` messages, count [0] from frame 90 to the end. L=4: 74 messages, the last at frame 93 = `[1, 1]`, zero count-0 messages although both slots are `lost`.

**Field impact.** None on the show path today: L is fixed at 1 and returns to 1 at every restart. Only a remote SetOutputSmoothing above 1 would leave TD holding the last dancer after the wall empties.

**Fix sketch.** Either remove the L > 1 emit branch along with the slider (D30 made it tool-only), or send the empty frame once the smoother has flushed its tails. Add a test that the last message after all dancers leave is count 0 for L = 1 and L = 4.

### detection-pipeline#7 — The detect cache is captured after the size gate and dedupe and freezes the person height, yet the tuner sweeps person_height_px over it

**Verdict: confirmed.** Already in the docs: no. TUNING.md l.54-55 still says the hook records "pre-gate detections" and l.119 ranks `person_height` from a cache sweep; the October audit built its own pre-dedupe cache (audit-2026-10/01-continuity.md l.294) without recording why.

**Evidence.** pipeline.py:748 gates and dedupes; the capture hook runs after it (793-795). detect_cache.py:171-186 stores those detections and `int(space.person_height)`; replay_gpu_cached (834-843) feeds that height to crossval, exclusion and the tracker; only line 975 reads settings. The height guard (747) is outside the replayed chain. REBUILD_KEYS (57-65) has no height key, the docstring (19-21) calls it tunable, tune.py:49 and sensitivity.py:45 sweep [120, 148, 200].

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/dp7_real.py: the tuner's own `detect_cache.replay_from_cache_gpu` on a synthetic 80-frame cache with person_height_px 120 / 148 / 200: per-frame timelines (tracks + internal + emitted) byte-identical, 49333 bytes each. tmp_analysis/audit-2026-11/verify_pipeline_tracker/dp7_cache.py: a cache built at 120 has lost a 400 px box; replays at 148 and 200 keep tracker height 120 and match gate 114 px; a real run at 200 keeps both boxes, gate 190 px, two tracks.

**Field impact.** Developer-side only: person-height results from tune.py and sensitivity.py (TUNING.md's 0.011 ranking) reflect the cold-blob path alone. The operator's known-N tune does not search height and the live height comes from the guard. Wrong tuning advice, not a show failure.

**Fix sketch.** Capture raw detections before the height guard and dedupe and run both inside replay_gpu_cached with the height from settings. Until then add person_height_px, both ratios and height_guard to REBUILD_KEYS, correct the docstring, and extend test_gpu_cache_fidelity with a changed height.

### detection-pipeline#12 — The motion feed is left in flight when YOLO or extraction raises, and the worker is never shut down

**Verdict: confirmed.** Already in the docs: no

**Evidence.** pipeline.py:614-619 and 671-677 await the feed when the GPU stage raises. `_run_yolo`, `_extract_detections`, `_guard_person_height` and `_filter_duplicate_detections` (719-749) run before the await at 753-755 with no try/finally; main_loop.py:849-866 catches the TRT size AssertionError raised there. `_motion_executor` (458) has no shutdown call in src. The next submit (1757) overwrites the future, so a feed exception is never read.

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/dp12_feed.py: `_run_yolo` stubbed to raise the TRT size AssertionError, feed slowed to 0.3 s. After the exception `_motion_future` is still set, done=False, worker log = [feed start]; `motion_model.reset()` ran on MainThread while the feed was still running; the feed then finished on the reset model (detector frame_count=1). The 0.3 s feed is artificial; a real feed is 8-25 ms.

**Field impact.** Narrow: needs a YOLO or extraction exception (engine or imgsz switch, CUDA fault) while the 8-25 ms feed runs, then a main-thread motion reset inside that window. Worst case one frame of inconsistent mask; no crash seen. The operator would not notice.

**Fix sketch.** Wrap `_run_yolo_and_track` up to the await in try/finally calling `_await_motion_feed()`, and add a `FrameProcessor.close()` that shuts the executor down from the main-loop shutdown steps.

### tracker#2 — Association gates are advisory: an unmatched detection is forced onto the nearest unmatched track regardless of the Mahalanobis, displacement and cost gates

**Verdict: confirmed.** Already in the docs: Described, not flagged as open: docstrings at tracker.py:2748-2752 and 1342-1346 (two field failures cited), audit-2026-10/01-continuity.md §1.2 items 2-3 (CLOSE_ACCEPT at cost 1e6, force-update). No roadmap item removes it; CONT-3 and CONT-5 are neighbours.

**Evidence.** tracker.py:2581-2606: an unmatched detection inside the creation gate (0.55 h, x1.5 away from the edges, 2753-2761) is forced onto the nearest unmatched track (2673-2708); otherwise 2717-2741 gives it to the first unmatched track in list order within 0.95 h. `_forced_update_allowed` (1342-1352) vetoes only own-height-scaled established tracks. Line 2036 accepts a 1e6 pair under 0.2 h. Overstated sentence: the gates do reject when the track is already matched, or beyond 0.83 h.

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/t2_force.py (real DancerTracker, H=120): dancer missed + ghost at 0.6 h / 0.8 h -> DISPLACEMENT_GATE, MATCH_REJECTED, FORCE_UPDATE; KF x jumps 0.55 / 0.74 h and the last-measured anchor sits on the ghost; ghost at 0.9 h -> NEW_TRACK; dancer also detected -> AMBIGUOUS_IGNORED. tmp_analysis/audit-2026-11/verify_pipeline_tracker/t2_fallback.py: FALLBACK_UPDATE to track 1 at 100 px (displacement gate 57 px) although track 2 was at 80 px. Existing laptop log projects/1_TANGO_HANGAR-texturedbg/sessions/20261006_114035_slot5/tracking_events.jsonl (imgsz 640, older code): 93 FORCE_UPDATE + 37 FALLBACK_UPDATE in 2629 frames.

**Field impact.** Likely wherever a ghost sits near a dancer (the top field pain): one missed frame plus a false box within 0.8 body heights moves the tracker point by half a body, then snaps back. Seen as a twitch, rarely an id loss; the slot filter damps it.

**Fix sketch.** Replace the three rescue rules by one second-stage assignment over unmatched detections and unmatched tracks, one gate in the track's own-height units, nearest wins. No rescue past the displacement gate for a track fed last frame, none by a zero-keypoint detection onto a skeleton-backed track; log which gate was overridden.

### tracker#3 — `_merge_duplicate_tracks` deletes one of two real dancers in close contact and cancels occlusion ageing

**Verdict: confirmed.** Already in the docs: The rule is listed without the risk in audit-2026-10/01-continuity.md §1.2 item 6 (l.109). The takeover-merge docstring (tracker.py:3718-3720) claims contact protection only for that newer rule. No test references the method.

**Evidence.** tracker.py:3626-3688: an established pair, both `time_since_update <= 1` (3631-3632), centroids within 0.3 x match gate (0.285 h) for 8 frames: the lower-hits track is removed, with no state transfer, no dormant snapshot and no co-fed check (the takeover merge has one, 3741-3751). Occlusion ageing (3230-3238) keeps a hidden track at <= 1 for ~20 frames, so it qualifies. Afterwards 2608-2636 ignores the partner's detection inside the creation gate.

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/t3_merge.py: two dancers co-fed 26+ frames, both skeletons every frame, 0.25 h apart -> TRACK_MERGED at contact frame 7, dormant []; partner AMBIGUOUS_IGNORED until 0.85 h, new id 3, two ids back at 1.4 h; 0.32 h apart -> no merge; hidden partner -> merged at frame 7. tmp_analysis/audit-2026-11/verify_pipeline_tracker/t3_slots.py (slots on, no plate): 1 / 3 / 6 s contact -> 1.40 / 3.40 / 6.40 s with one id on OSC. Real footage, stored Oct-8 timelines tmp_analysis/stale_plate/older/white-duo-full_fix_v5.json and texture-duo-full_fix_v5.json (merges inferred from track disappearance): 21 in 170 s, 3 on a slot-bound track, none followed by fewer than 2 emitted ids within 3 s.

**Field impact.** Bad when it fires: one dancer's id is gone for the contact plus about 0.4 s and TD drops that video (D5). It needs two separate skeletons under 0.285 h for 0.4 s; on the April duo proxy it never cost an emitted id. Unknown at 25 m (t-020 unprocessed).

**Fix sketch.** Reset the pair streak on any frame where both tracks received a real skeleton, reuse the `_pair_cofed` veto, and skip tracks flagged `_occluded`. Add tests mirroring test_tracker_takeover_merge.py: a co-fed pair never merges, an alternately fed pair does.

### tracker#5 — `time_since_update` carries three meanings; slowing occlusion ageing by decrementing it changes unrelated rules

**Verdict: confirmed.** Already in the docs: Only the tsu = -1 symptom: BUG-2 in audit-2026-10/01-continuity.md (l.58, 104, 526), fixed under CONT-1 (comment at tracker.py:3224-3229). The remaining overloading is not recorded.

**Evidence.** tracker.py:3230-3238 undoes the predict increment, so every reader of `== 0` / `<= 1` sees a fed track: predict() friction, acceleration damping and warm-up decay (447-454), the displacement gate (1446-1448), the shadow test (3543-3550), the co-located merge (3632), the merge-frame count (2449-2452) and the crossval bypass (pipeline.py:2304). The bridge also sets it to 0 (3140). Newborn tracks are not in `matched_trk` (3193-3199).

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/t5_tsu.py: a dancer hidden beside a partner: tsu 0 for 10 frames, then 1; vx stays -4.00 px/frame and warm-up 20.0 for 11 frames (control with nobody near, 11 misses: vx -1.39, warm-up 12.0, tsu 11); still emitted for 11 frames while extrapolating 40 px; drifts into the partner and is TRACK_MERGED at hidden frame 20; tsu <= 5 (crossval bypass) on 19 of 19 hidden frames. A newborn 0.9 h from a matched track: NEW_TRACK then OCCLUDED, `_occluded=True`.

**Field impact.** Indirect but real in duo crossings: the hidden dancer's track coasts undamped toward the partner and is eaten by the co-located merge within about 1 s; field logs show t_miss 0-1 for a track unseen for a second. The operator sees an id loss, with a misleading log.

**Fix sketch.** Keep a `frames_since_measurement` counter that is never decremented, a separate float age budget (1.0 or 0.1 per frame) and a fed-this-frame flag. Move the readers over one at a time under the goldens, starting with predict() and the co-located merge.

### tracker#6 — A live track re-acquired after a detection gap must re-warm from near zero, while a resurrected dormant id is emitted immediately

**Verdict: confirmed.** Already in the docs: Known open in substance: ROADMAP.md P-4 / CONT-4a (l.141), ROADMAP_v2_REVIEW.md F5 (l.338, "every re-birth costs 0.75 s") and l.355 (hidden-follow + coast cover short gaps), audit-2026-10/01-continuity.md §1.3 (l.116-117). The live-versus-resurrected asymmetry itself is not written down.

**Evidence.** tracker.py:450-454 decays the integral by 0.8 per miss down to 0 (cap 20, 534-535); emission needs 15 (126-129, 3359). `_try_resurrect` restores it for a previously emitted id (2318-2322); a live track has no equivalent and stays active for 135 frames (3288-3289). The slot layer's hidden-follow covers a re-acquisition only while the slot is not yet lost (identity_slots.py:871-873).

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/t6_rewarm.py. Tracker, still dancer: gap 8 frames -> re-emitted +1 frame; 20 -> +11; 30 to 134 -> +14 frames (0.70 s); 136 and 200 -> RESURRECT, emitted at once. Emitted stream (slots on, no plate, no belt): gaps up to 1.5 s -> no hole; gaps of 1.95 to 6.0 s -> first point 14 frames after the dancer is seen again; gaps of 6.8 s and 10 s -> +0 frames.

**Field impact.** A still dancer whom YOLO loses for 2 to 6.7 s in the dark returns 0.7 s late (1.4 s at 10 fps) once the slot has gone lost; a longer absence returns at once. The clean-plate hold covers it when a valid plate is loaded. Seen as a late re-appearance.

**Fix sketch.** For a track with `_ever_reported`, restore the integral to the threshold when a real skeleton re-matches within the close-accept distance (the resurrect rule applied to live tracks). Fold it into CONT-4a and gate it on the false-positive-burst scenarios that motivated the decay.

### tracker#7 — Synthetic motion-blob detections earn full skeleton credit, contradicting the documented bridge policy

**Verdict: partly.** Already in the docs: Yes at tracker level: audit-2026-10/01-continuity.md l.116 ("+1 per match (synthetic matches included)") and §2(d) l.208-218; CONT-3 and SEAM-1 (AUDIT_2026-10.md l.100, typed measurement with credits); ROADMAP_v2_REVIEW.md F4 (blob-fed drift reported live). The unreachable persistence reset is not recorded.

**Evidence.** Holds: tracker.py:2859-2862 builds zero-confidence detections; `update()` (531-536) gives them a hit, +1.0 integral and a 1.0 window credit; only R is scaled (2135-2138), while a bridge gets +0.4 and 0.0 (3157-3163). `is_established` counts those hits (843-845). 2367-2368 skips fusion without blobs, so the reset at 2799-2801 never runs. Does not hold: emission. The slot entry rule needs a recent real skeleton (identity_slots.py:44-49, 532-539).

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/t7_synth.py: blob-only object at 0.05 h/frame, zero YOLO detections: hits 58, warm-up 20.0, window credits 40, established at frame 16, reported by the tracker from frame 13; at 0.02 h/frame the frozen gate holds it. Blob present on frames 0, 4, 8 only -> synthetic detection at frame 8. Same moving blob through the identity slots: tracker reported it on 67 of 80 frames, OSC count >= 1 on 0 frames.

**Field impact.** With stable ids on (D24 default) a blob-born track never reaches OSC. The cost sits in the tracker stream that replays score and the preview shows, and in blob-fed drift of an already bound dancer (known, CONT-3). A ghost point appears only with slots switched off.

**Fix sketch.** Carry the feed source on the measurement (SEAM-1) and credit synthetic matches like bridge frames (+0.4, no window credit). Base `is_established` and the report cap on skeleton hits, and clear `_motion_blob_cells` on blob-free frames.

### tracker#8 — Resurrection is unreachable where it matters most, and its 'shape' gate is an absolute-position gate

**Verdict: confirmed.** Already in the docs: Half: audit-2026-10/01-continuity.md §1.2 item 7 (l.111) and §2(b) l.180 ("reachable only when the det is far from every active track, so zombies block it"). The absolute-coordinate keypoint gate and the damped velocity projection are not recorded.

**Evidence.** tracker.py:2581-2590: `_try_resurrect` runs only beyond the creation gate of every active track (x1.5 away from the edges, 2760-2761). 2239-2249 widen the position gate up to 2.5x; 2277-2288 compare raw image keypoints against 0.5-0.8 x the base gate (the cost matrix uses centroid-normalised ones); fewer than 3 shared keypoints skips the check (2290-2291). `projected_position` (195-207) uses the velocity left after 0.9-per-frame coasting (447-448).

**Reproduction.** tmp_analysis/audit-2026-11/verify_pipeline_tracker/t8_resurrect.py: snapshot after a 6 px/frame walk: |v| = 0.0000, projection = last position; position gate 285 px (2.38 h), keypoint gate 91 px (0.76 h). Skeleton at 0.5 / 0.7 h -> RESURRECT, emitted that frame; at 0.8 / 1.5 / 2.2 h -> NEW_TRACK; zero-confidence blob at 1.5 h -> RESURRECT; arms-up pose at 0.5 h -> RESURRECT. Beside a tracked partner: 0.3 / 0.5 / 0.8 h -> AMBIGUOUS_IGNORED (no track at all); 0.9 h -> NEW_TRACK id 3, id 1 left dormant.

**Field impact.** A dancer re-emerging within 0.83 body heights of the partner has no track at all until they separate, then a new tracker id and about 0.7 s of warm-up. The slot layer re-uses the lost slot id, so TD sees a longer hole, not a new id. Likely in duo work.

**Fix sketch.** Offer dormant snapshots as candidates before the creation-gate decision when the snapshot was occluded or left in the centre, and compare centroid-normalised keypoints. Require a real skeleton to resurrect an emitted id and drop the velocity projection.

## slots+calibration

### identity-output#2 — A dancer occluded by, or in contact with, the partner loses its id by design

**Verdict: confirmed.** Already in the docs: Partly. PLAN_25M_2026-10.md:278-287 (§C.11) counts the frames lost 'after a merge' (19 of 739 on the white duo, 5 of 2638 on the textured duo) and ROADMAP.md:123 says the remaining two-point loss is 'startup + merges'. The rule itself is in identity_slots.py:50-56 and :280-282. The refusal to re-bind inside 0.5 h is not listed as a loss cause (PLAN §C.2 only has 'other (duplicate rule, ...) 11 %').

**Evidence.** identity_slots.py:899-904 is_duplicate (0.5 h) gates same-track (:913), rebind (:936, :943) and entry (:976, :984). :867-870 a tsu>0 track is no measurement. :1235-1238 a non-live slot within 0.25 h of a live one is dropped after 0.5 s, whatever hold it earned; :1239-1240 two non-live slots merge at once; :1213 clears last_key. The rule is deliberate (test_identity_slots.py:145-177 keep both ids only when both have skeletons). Overstated: re-entry takes one frame when the tracker kept the track (hits and streak persist), not a full warm-up.

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_slots2.py and tmp_analysis/audit-2026-11/verify_slots_calibration/r_slots.py (IdentitySlots alone, the params pipeline.py:1488-1497 builds, 20 fps). B unreported 0.2 h from live A: event 'lost, why merged' at +0.60 s although its hold was 2.0 s. Both unreported 0.2 h apart: one id dropped on the first frame. B back with a fresh skeleton at 0.30 h and 0.45 h for 1 s each: not re-emitted; at 0.55 h: 'entry' after 1 frame. A new track 0.4 h from A, up to 100 hits, fresh skeletons: never bound in 5 s. Control, both with skeletons at 0.2 h for 3 s: two ids throughout. Repo git status clean after all runs.

**Field impact.** Needs real contact: centroids under 0.25 h (about 45 cm) with one dancer unmeasured for over 0.5 s; then no id until they are 0.5 h apart. Small on the April duos (2.6 % and 0.2 % of frames), but each event makes TD drop a video (D5). No show-like duo baseline exists. Visible as a vanished ball.

**Fix sketch.** Give an unmeasured slot inside a live slot's contact zone its own 'contact' hold (ride the live slot with its last offset, with a long cap) instead of merging at 0.5 s, and give the both-non-live branch the same hold. Allow a bind inside dup_bind_h when both tracks carry a fresh skeleton. Gate it on the duo replays: seconds with an id missing while both dancers are on the wall.

### identity-output#3 — The empty-wall foreground acts as a hard, untimed, unlogged veto computed on the raw image with a noise level frozen at capture

**Verdict: confirmed.** Already in the docs: Partly. The plate-update rule and its ~25 s time constant are in PLAN_25M §C.12 (afff826), and the §C.15 side note records that an unprotected person is blended in. The veto having no escape and no log line: no. Counter-evidence on impact: PLAN §C.10 (no veto near a moving body on the April duos) and §C.12 (dark night take s2c at 100 % with the plate).

**Evidence.** identity_slots.py:543-546: support < 0.10 returns False and only bumps counters['fg_veto'] (no _event), with no time or confidence term; not called on continuation (:846-886) or same-track (:908-918). foreground.py:252-253: threshold max(2, 4 x plate.sigma x sqrt(gain)), sigma fixed at capture (:71-72); the input is the raw ROI gray (pipeline.py:716, :797, :1359-1364). pipeline.py:1453-1468 protects live and backed-belt slots and tracks with fss <= 20, so an fg-held slot is blended (foreground.py:292-318). The 25 s is the time constant, not the absorption time.

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_slots.py part F: a moving track with fresh skeletons at confidence 0.9 and a fake foreground returning support 0.05, for 10 s: 0 frames with an id, 0 SLOT_EVENTs, counters fg_veto = 396. Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_plate.py (ForegroundDetector defaults, 20 fps, synthetic wall): an unprotected still body of +6 / +12 / +30 DN has no blob and support under 0.10 after 27.3 / 44.6 / 67.6 s; inside a protect box, never (120 s). Not checked: the foreground share of real dancers on footage.

**Field impact.** Unmeasured: the lowest foreground share of a real dancer is recorded nowhere. The 25 m night takes showed no blocking; the risk grows at 30-40 m (smaller, dimmer bodies) or after a light drop inside the alert band. If it bites, a dancer has no id while it lasts: a T-box without a ball, nothing in the logs.

**Fix sketch.** Log a rate-limited SLOT_EVENT 'fg_veto' with the measured share, and let about 1 s of consecutive fresh skeletons above the live confidence override the veto. Add reputed fg-held slots to _fg_protect (they already have the 30 s cap) and record the minimum and median foreground share of bound dancers per session, so 0.10 can be checked on the night and duo takes.

### identity-output#5 — Foreground evidence switches off silently mid-show; its status line is refreshed only on operator actions, and the remedy needs an empty wall

**Verdict: confirmed.** Already in the docs: Partly. PLAN_25M §C.15 'Behaviour change to know' records that a stale snapshot stays stale until it is re-captured and names the readiness row and the light alert as the prompts. Not recorded: nothing watches staleness live, the status line is event-driven, and a geometry-only change raises no alert.

**Evidence.** foreground.py:257-261 invalidates the frame when over 25 % of the ROI differs; stale_frames (:184, :258, :262) has no reader in src although the module doc (:16-19) promises a re-capture hint. identity_slots.py:788 then drops the veto, the fg hold and the foreground belt backing. app.py:1857-1909 publishes the status text only on project load, toggle and capture. Readiness reads plate_status on demand (app.py:2285), i.e. the last frame only. main_loop.py:1352-1386 with ops_monitor.py:502-522 alert only when the brightness ratio leaves [1/1.5, 1.5].

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_fg5.py (ForegroundDetector on a synthetic textured wall, then the ops_monitor band test). Camera nudged 12 px, same light: valid frames 0/100, reason 'plate stale', fg_ratio 0.84, stale_frames 100, light_ratio 1.00, light_changed alert False, support() 1.00 (veto off). Light x0.8, same framing: 100/100 valid, no alert. The status-line and readiness wiring were read, not run.

**Field impact.** Triggers: camera or lens nudged after Calibrate, a large uneven light change, someone close to the lens. Holds then fall from 30 s (foreground) to 1.5-2 s and the ghost veto is off while the panel still says 'empty wall: ready'. Plausible once per installation; the operator cannot see it.

**Fix sketch.** Feed the share of valid foreground frames over the last 10 s into the ops tick, raise a persistent strip alert when it stays low, and refresh the status line from that tick. When the plate stays stale with no slot emitting, re-base the live plate automatically, since the wall is empty by the slot layer's own account.

### identity-output#6 — The empty-wall YOLO check can only step the enhancement down, and a failed run pins the weakest setting for later Calibrates

**Verdict: confirmed.** Already in the docs: The keep-first rule: yes, by design (empty_wall.py:111-114, ROADMAP.md:47, PLAN_25M §C.13, D28/D29; tests/test_calibration_flows.py:235-243). The failed run becoming the kept setting: no. TERRAIN_DEMO_FR.md:50-52 tells the operator that 'Kept gamma ...' means good.

**Evidence.** empty_wall.py:152-166: a rung fails on its first frame holding a raw detection >= 0.8 x confidence; when all fail, the last (weakest) rung is the result. calibration_flows.py:510 applies it, :530-531 stamps it, nothing restores the starting enhancement. :249-250: the next Calibrate takes the current enhancement as 'keep' whenever 'gamma' is stamped (always, after :452); empty_wall.py:121-125 tries it first, :160-163 keeps it. calibration_state persists (app.py:1307, :1323-1324). The ladder (:89-104) only descends from the seed.

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_wall.py (CalibrationFlows with the MagicMock wiring of tests/test_calibration_flows.py). Incumbent 1.30 / 2.5, a person at confidence 0.6 on every frame: 40 frames, clean=False, result gamma 0.80 / CLAHE 1.0, stamped. Next Calibrate on a clean wall with seed 1.50 / 2.5: keep-first candidate (0.8, 1.0), 124 frames, 'Kept the current gamma 0.80 / CLAHE 1.0', kept_current=True. Control without an incumbent: 1.50 / 2.5. A 15-frame passer-by: 1.30 becomes 1.17 with clean=True.

**Field impact.** One Calibrate with anyone in view leaves gamma 0.8 / CLAHE 1.0 within 2 s; a 0.75 s walk-through costs rungs with a 'clean' verdict. Later clean Calibrates keep it, and the operator note reads 'Kept gamma' as good. Low-light recall stays reduced until someone moves the sliders. Likely on a solo install.

**Fix sketch.** On a not-clean result, restore the enhancement the check started from, do not stamp it, and report 'someone is on the wall' when detections sit above the live confidence on every rung. Store a 'wall check passed' flag with the stamp, pass keep only when it is set, and re-test a failed rung once before stepping down.

### identity-output#7 — The OSC stream has no frame boundary and no end-of-stream, and the wire differs from what the code documents

**Verdict: confirmed.** Already in the docs: Mostly. No bundling: OSC_CONTRACT §A.1. Box centred on the centroid and last skeleton while held: §D.1, by design (test_identity_slots_pipeline.py:58-60). The six states: §D.2 lists them, so only the code docstring and the tooltip are stale. /walldance/clear on STANDBY: known open (ROADMAP §5 small fixes, CONTEXT §7). Not recorded: the age of a never-used slot, and OSC during a STANDBY Calibrate.

**Evidence.** osc_output.py:181-185 with :114-143: count, then four datagrams per dancer; no bundle (imports :17-18 unused), no frame id. :152-156 and gui_builder.py:1127-1128 list four states; identity_slots.py:99-105 emits six. pipeline.py:1185-1186: age = clock minus state_since (default 0.0). send_clear is called only at app.py:1991 (_cb_tracker_reset). main_loop.py:808-816 forces processing in STANDBY during Calibrate and plate capture; pipeline.py:1027 gates OSC on osc_enabled only, then at the check's 0.8 x confidence (calibration_flows.py:496-497).

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_fg5.py: FrameProcessor(model=None) on the live clock, one dancer, max_dancers 2: slot_states() = [(1, 'live', 0.081), (2, 'lost', 8328.585)] while time.perf_counter() = 8328.601. The other items: not reproduced: read only.

**Field impact.** For a centroid-only TD patch the missing bundle rarely matters on loopback. Without an end-of-stream, TD keeps the last points on STANDBY or camera loss. A Calibrate in STANDBY can send ghost ids to TD at 0.8 x confidence. The age and state names matter only once /dancer/state (D22, off) is used.

**Fix sketch.** Send each frame as one OSC bundle with a frame counter, and one explicit empty frame (count 0) on STANDBY, camera loss, stall cap and exit. Gate OSC on RUN, send age 0 for a never-used slot, and derive the docstring and the tooltip from one state list.

### identity-output#11 — The slot layer claims seconds, but about ten thresholds are frame counts tuned at 20 fps and the motion step is clamped

**Verdict: confirmed.** Already in the docs: No for the slot layer (CONTEXT §7 knows only the tracker's frame time base). PLAN_25M §C.16 (lines 639-640) states that the slot's velocity and prediction 'see the 1.7 s': the code clamps both to 0.25 s, and only the One-Euro sees the real gap. No test covers the velocity after a gap.

**Evidence.** Frame counts: identity_slots.py:249-254, :285, :304, :333-334, :351, :822; pipeline.py:1465; foreground.py:133; config.py:341. identity_slots.py:794-796 clamps dt to 0.25 s and :733 divides the whole displacement by it; that s.vel feeds the edge-exit test (:1128-1133). The jump allowance (:859-861) adds only credited silence. Overstated: after an unbind the widened rebind gate (:932-934) usually re-binds in the same frame, and the OSC velocity comes from the One-Euro (:1092-1095), which sees the real dt.

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_slots.py parts D and E. Walker at 0.30 h/s, 1.7 s gap (credited stall, and main-loop freeze): slot velocity 1.17 h/s on the first frame back (OSC velocity 0.33); track lost on the next frame 0.2 h from the border: granted hold 0.3 s, id out after 0.30 s; the same walk without a gap: 2.0 s. Freeze with a dancer at 2 h/s: 1.2 s gives nothing; 1.8 s gives 'unbind_jump', then the id absent for one frame 0.25 s later; 2.5 h/s and 2.2 s: id absent on the first frame, 'entry' on the next.

**Field impact.** Stalls come 0.7-2.9 times a minute, but the wrong hold needs a track loss within about 3 frames after one, inside 0.25 h of the ROI border, moving outward; belt and foreground holds usually pre-empt it. Frame-count windows double only if the laptop falls to about 10 fps. Low; not noticeable.

**Fix sketch.** Divide by the unclamped motion dt (or skip the velocity update after a gap longer than max_dt_s) and widen the jump check by the real elapsed motion time. Pass the frame period into update() and express the skeleton-age and streak thresholds in seconds.

### motion-calibration#1 — No way to follow a light change while dancers are on the wall: light-coupled values are frozen and the only response is an alert whose remedy needs an empty stage

**Verdict: partly.** Already in the docs: Partly. The frozen model is the owner's decision (config.py:596-599, CONTEXT §3). Keep-first is D28/D29 (ROADMAP.md:47, PLAN_25M §C.13). The playback remedy is in TERRAIN_DEMO_FR.md:54. 'Light changes during a show (sunset)' is a LATER item in ROADMAP §5 (lines 157-159), about the alert only. A dancer-tolerant re-light step is proposed nowhere.

**Evidence.** Holds: Calibrate is the one entry point (calibration_flows.py:203-295); servo, sweep and sigma use medians (calibration.py:168-202, :856-862, :886-892), only the plate and EmptyWallCheck need an empty wall; camera writes switch auto off (camera_controller.py:411-444); keep-first means gamma/CLAHE cannot rise on a re-Calibrate (empty_wall.py:121-125, :160-163). Does not hold: the remedy does not need an empty stage, ops_monitor.py:233-234 offers playing that lighting's empty take; the alert fires once but stays in the strip (:504-505). It needs a plate.

**Reproduction.** Ran pytest -p no:cacheprovider -q tests/test_calibration_flows.py -k 'keeps_the_projects or ends_with_the_empty or records_the_enhancement': 3 passed (the suite asserts that a scene pick of 1.8 / 1.5 is discarded for the incumbent 0.73 / 2.5 when it passes). tmp_analysis/audit-2026-11/verify_slots_calibration/r_wall.py run 2 shows the same with a weak incumbent: 0.80 / 1.0 kept over a 1.50 / 2.5 seed. The alert path: read only.

**Field impact.** Dusk to night, or an IR lamp failing mid-show, is plausible outdoors (the night project fell about 3x). With a pre-recorded empty take the remedy costs about 30 s of live tracking; an unplanned change has no remedy while dancers are on the wall. The operator gets one persistent alert.

**Fix sketch.** Split out a 'relight' step (servo, var x scale sweep, gamma re-seeded from the measured light ratio) that tolerates dancers and is offered when light_ratio leaves its band; keep the plate and the YOLO check as the empty-wall step. Store the wall brightness at which the incumbent enhancement passed and keep it only inside a band of that value.

### motion-calibration#3 — Calibrate is not transactional: no stage or RUN precondition, partial state after cancel, weakest enhancement kept when a person is in view, plate written before the operator saves

**Verdict: confirmed.** Already in the docs: No. The operator note only asks for an empty wall (TERRAIN_DEMO_FR.md:45). One nuance: the remote StartCalibration is refused in RUN unless the operator enabled remote control (remote_api.py:60, :142-143), so 'no RUN precondition' concerns the local button.

**Evidence.** calibration_flows.py:227-241: the preconditions are a loaded model and frames, no system_state check (Auto-tune has one, app.py:2442-2446). Cancel (:210-226) restores neither exposure/gain (:272-273, :351-358) nor the seeded gamma (:322-330), and does not stop the plate capture. :457-461 starts that capture before the wall check; app.py:1893-1895 writes plates/latest.npz at once, whatever the dialog choice (gui.py:2838-2848). :496-497 lowers the live confidence to 0.8 x during the check. Person in view: see identity-output#6.

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_wall.py: the plate capture is requested at apply, before the verdict (['calibrate']); live confidence 0.15 becomes 0.12 during the check and returns after; with a person in view, 40 frames (2 s) end at gamma 0.80 / CLAHE 1.0; cancel after a playback start: gamma 0.73 seeded to 3.03 and still 3.03 after cancel, exposure 25000 and gain 36 written and never undone. RUN gating and the plate write: read only.

**Field impact.** A non-developer who presses Calibrate with someone in frame, cancels, or presses it in RUN ends within seconds with a weaker enhancement, a contaminated plate on disk and changed exposure. After 'Keep this session only' and a restart, the old config loads with the new plate. Likely once per install; one dialog line shows it.

**Fix sketch.** Snapshot exposure, gain, gamma, CLAHE, var, scale and the plate at start, and restore them on cancel or on a not-clean result. Hold the new plate as pending and commit it with the config Save. Require STANDBY or an explicit confirmation on the local button, as the remote policy already does.

### motion-calibration#5 — The var x scale sweep scores a different MOG2 than production runs, and a hard low-light switch then doubles the result

**Verdict: confirmed.** Already in the docs: No. config.py:608-615 gives the reason for an empirical sweep; nothing records that its model differs from production's. The x2 low-light multiplier appears only as a config comment (config.py:950-952).

**Evidence.** calibration.py:734-739 and :858: MOG2(history=90, automatic learning rate), fed every 2nd frame; :849-855 resize only; :859 counts mask == 255. Production: Gaussian 3x3, or median 5 + Gaussian 5x5 below mean 55, before the resize (motion_detector.py:192-215); learningRate 0.001 (:275); varThreshold x2 below 55 (:269-273), min area x1.8 (:355-356), crossval ratio x1.2 (pipeline.py:2291-2294), no hysteresis. The mask's consumers count >= 127 (pipeline.py:974-978, tracker.py:2963-2984).

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_mog2.py and tmp_analysis/audit-2026-11/verify_slots_calibration/r_mog2b.py (synthetic wall with iid noise; SceneCalibrator, then MotionDetector with its pick; median-tile false positives). Raw sigma 6: sweep 0.20 %, production 0.00 % at 45 to 1200 frames. Sigma 16, bright: sweep 0.20 % (var 16), production 1.19 % at 45 frames, 0.92 % at 90, 0.13 % at 300, 0.01 % at 1200 (counting >= 127: 2.37 / 1.86 / 0.27 %). Sigma 24: 0.26 % against 3.35 / 2.93 / 0.52 %. Dark scene (mean 40): production 0.00 % with var doubled, 8 to 16 and 16 to 32. Synthetic noise only; no footage.

**Field impact.** At normal noise the sweep is pessimistic, so the cost is sensitivity (a calibrated 8 runs as 16 in dark scenes), not ghosts. At heavy noise in a scene bright enough to miss the low-light switch, production floods for 15-60 s after each MOG2 reset. The report-card percentage is not what runs. Invisible to the operator.

**Fix sketch.** Build the sweep from MotionDetector itself (same preprocess, same label set, production learning rate after a fast warm-up) and add a test comparing the two false-positive rates on synthetic noise. Calibrate and latch the low-light regime per calibration, with hysteresis, instead of a per-frame x2 at mean 55.

### motion-calibration#6 — Empty-wall check: the advised remedy (paint an exclusion) cannot make it pass, and the tested confidence margin is narrower than the operator dial

**Verdict: partly.** Already in the docs: No; the opposite is written. TERRAIN_DEMO_FR.md:50-52 tells the operator to paint an exclusion and redo Calibrate, and tests/test_empty_wall.py:61 asserts the message. The 0.8 margin 'leaves room for the dial' is stated in empty_wall.py:16-17 and PLAN_25M §C.13.

**Evidence.** Holds: last_raw_dets has one writer, pipeline.py:741-745, right after _extract_detections; the exclusion is applied later (:965, :1953-1976); calibration_flows.py:506 feeds it unfiltered, while empty_wall.py:78-79 and calibration_flows.py:534-535 advise painting an exclusion. Narrower than stated: the limit is 0.8 x the live confidence (empty_wall.py:115); with the D33 default 0.15 it is 0.12 and the dial floor is 0.15 (config.py:726), so the dial never leaves the tested range. It does for a seed above 0.19.

**Reproduction.** not reproduced: read only (the flows harness mocks the processor, so a run would prove nothing). Arithmetic on sensitivity_macro.py:48-52: seed 0.25 (the setting the field once ran) gives limit 0.20, and the dial goes below it above position 75; seed 0.35 gives 0.28, crossed at 67.5; seed 0.15 gives 0.12, never crossed.

**Field impact.** A venue with a person-like object in the ROI (the door equipment of the night project): the operator follows the note, paints, recalibrates, and gets the same failure and the weakest enhancement again. Removing the object or tightening the ROI does work. Likely on cluttered venues; noticed, not solvable alone.

**Fix sketch.** In _step_wall_check, drop raw detections whose centre falls in an excluded cell (the pipeline's _zone_ok_fn) before feeding the check, or change the three messages to 'remove it or tighten the ROI'. Store the tested limit with the calibration and toast when the dial goes below it.

### motion-calibration#7 — ExposureServo clamps its internal exposure/gain to the caps without commanding the camera

**Verdict: confirmed.** Already in the docs: No. audit-2026-10/02-ir-markers.md:93 states the opposite as fact ('the servo caps exposure at the 25 ms blur budget'), and its line 13 records corpus hangar configs saved at about 51.6 ms.

**Evidence.** calibration.py:266-270 clamps the starting exposure/gain to the caps in memory only. In band it finishes (:344) without a command; the dark branch (:312-318) sees 'at cap' and raises gain; result() reports the internal values (:357-361). calibration_flows.py:260-262 builds the servo from cameras.ids_exposure_us and :346-358 only forwards commands. Manual exposure is allowed up to 66.7 ms (ids_camera.py:116-135, camera_controller.py:428-437). Only the too-bright branch moves exposure. No test starts above the cap (test_calibration.py:391-404, :513-517).

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_servo.py (ExposureServo with a simulated camera). Camera at 51.6 ms / 10 dB, in band: commands [], camera really at 51.6 ms, report 'Exposure: 25.0 ms gain 10.0 dB ... (converged, 0 steps)'. Dark scene: six gain commands up to 28 dB, camera still at 51.6 ms, report 25.0 ms. Budget 12 ms with the camera at 25 ms: report 12.0 ms, no command. Control at 20 ms: 20.0 ms.

**Field impact.** Corpus hangar projects were saved at about 51.6 ms, twice the 25 ms budget: a live Calibrate there keeps the blur, raises gain instead, and prints 25.0 ms. Blur costs YOLO recall on fast moves. The report card hides it; only the exposure slider shows the real value.

**Fix sketch.** Emit an initial exposure or gain command whenever the constructor clamped a value, and report the values read back from the camera controller. Add a unit test that starts above the cap.

### motion-calibration#9 — Lighting profiles switch only part of the calibration: the plate and the calibration provenance are shared

**Verdict: confirmed.** Already in the docs: No. Context that lowers it: D29 moved lighting profiles to Advanced > Expert tools (PLAN_25M §C.13, ROADMAP.md:47), and the operator flow is one empty take per lighting plus Calibrate (TERRAIN_DEMO_FR.md:45-54).

**Evidence.** config_schema.py:32-47: 14 profile keys, neither the plate nor calibration_state. app.py:1860-1870: one plates/latest.npz per project, loaded only for a full config (:1497-1498); a profile bundle has no 'model' / 'camera_source', so :1319-1324 leaves calibration_state alone, on purpose per its comment. config_manager.py:352-370 applies the bundle and seeds a never-calibrated profile from a copy. calibration_flows.py:249-250 keys keep-first on the shared stamp; :176-193 builds 'Last calibrated' from it.

**Reproduction.** Ran tmp_analysis/audit-2026-11/verify_slots_calibration/r_profile.py (config_schema only): split_profile() gives a bundle of the lighting keys; calibration_state, fg_plate, fg_enabled, model, camera_source and person_height_px stay shared; the bundle is not a full config (no plate reload, calibration_state untouched); structure() seeds 'rehearsal' as a copy of 'show' (True). The ConfigManager switch itself: read only.

**Field impact.** Only when someone uses Show/Rehearsal from Expert tools or the remote SwitchProfile: the plate then belongs to the other lighting (the light alert fires only beyond 1.5x), and each Calibrate overwrites the plate the other profile needs. Low since D29; the operator would not see why the foreground misbehaves.

**Fix sketch.** Either retire the profile switch, since D29 replaced it by one empty take per lighting, or store the plate and calibration_state per profile (plates/<profile>/latest.npz), reload the plate on a switch and key keep-first on the active profile's own stamp.

## camera+gui

### camera-recording#1 — The 2.5 s IDS reopen watchdog pre-empts the 5 s stall bridge; the reopen is synchronous and a reopen loop raises no alert (same claim as runtime-core#3 and identity-output#4)

**Verdict: confirmed.** Already in the docs: partly: docs/PLAN_25M_2026-10.md:647 and docs/OSC_CONTRACT.md:335-338 state that an IDS stream silent for 2.5 s is reopened and counts as a camera loss. Neither says that this makes the 5 s cap unreachable on the IDS, nor that a reopen loop raises no alert.

**Evidence.** application/src/runtime/camera_controller.py:77,148-156: IDS silent more than 2.5 s (hard-coded) = timed out; non-IDS sources return False (:149-150). application/src/runtime/main_loop.py:691-696 then forgets the stall clock, closes the camera and retries in 0.5 s; the reopen runs synchronously in _tick_pumps (:576-583). So CAMERA_STALL_MAX_S = 5.0 (application/src/core/config.py:136) is reachable only for OpenCV sources. application/src/core/ops_monitor.py:525-536: camera_down needs 15 s continuously down; each successful open resets it. application/tests/test_camera_stall.py:346 stubs the watchdog to False.

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_stall_vs_watchdog.py: real MainLoop._tick_pumps/_tick_acquire, CameraController and FrameProcessor slot stage; fake camera and clock. Assumed, not measured: a reopen costs 1.0 s and the camera stays silent until the stall ends. Stalls of 1.7 s and 2.4 s: 0 close, keep-alive to the end, hold clocks paused 1.60 / 2.30 s, id kept. Stalls of 2.6 / 3.5 / 4.5 s: close/open 1/1, keep-alive stops at 2.46 s, stall.offset 0.00, first frame after 4.04 / 4.04 / 4.55 s, id 1 missing on 10 of the next 30 frames when the tracker needed 0.5 s to re-find the dancer (id kept when the same track is there on the first frame). Watchdog raised to 6 s: the 3.5 and 4.5 s stalls are bridged (68 / 88 re-sends), id kept. tmp_analysis/audit-2026-11/verify_camera_gui/repro_reopen_loop_alert.py: a camera that opens but never delivers, real HealthMonitor at 1 Hz, 10 min: 99 to 169 reopen cycles, 'alerts raised: NONE'. git status --short stayed empty after every run of this pass.

**Field impact.** Routine stalls are 1.65-1.7 s (the four 2026-10-07 camlogs show none longer), so D34 covers them; the 2.5-5 s band is unobserved, one 23.6 s hole was seen. Then: OSC silent, GUI frozen during the reopen, ids lost unless the tracker still holds the dancer. A dead-but-opening camera loops without any alert.

**Fix sketch.** Make the reopen threshold a config constant at or above CAMERA_STALL_MAX_S (or lower the cap to 2.5 s and say so), and reopen only on has_error, a failed read, or silence beyond the cap; add a main-loop test that uses the real watchdog. Count reopens and raise camera_down on N reopens in M minutes or on 'open but no frame for X s'.

### camera-recording#3 — The recording callback lives on the IDSCamera instance, so any reconnect ends the take without a message

**Verdict: confirmed.** Already in the docs: no

**Evidence.** application/src/camera/ids_camera.py:2148-2153: UnifiedCamera.set_frame_callback forwards to the current camera object and stores nothing (a no-op when none is open); :373 and :1979: every open builds a new IDSCamera with _frame_callback = None. application/src/runtime/camera_controller.py:262-322 re-applies exposure and gain after a reopen, not the callback; the only callers are application/src/runtime/recording_controller.py:339,364,412,421. The recorder stays in RECORDING. One clause of the claim is wrong: a crop change keeps the same IDSCamera (ids_camera.py:895-972), so the callback survives it.

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_callback_and_golive.py: real IDSCamera.__init__/set_frame_callback, UnifiedCamera, CameraController, RecordingController, app._RecordingCameraAdapter and VideoRecorder (FFV1 in a temp dir); SDK faked. Take started, 20 frames, watchdog-style close plus _attempt_camera_connect, 60 more frames: 'camera #2 (new object: True), callback set = False, recorder.is_recording = True, recording_frames = 20'; .meta: 'frames = 20 (camera delivered 80), error = None'. REC pressed while the camera is closed: 'is_recording = True, recording_frames = 0', .meta frames = 0.

**Field impact.** Any reopen during REC truncates the take: a stall past 2.5 s, a USB error, or the reopen that follows every playback->LIVE, so a remote 'record slot N' sent while a take plays should record almost nothing (inferred, not run end to end). Only sign: the frame counter stops; .meta shows no error. Costs footage, not the show.

**Fix sketch.** Store the callback on UnifiedCamera and apply it in _open_ids and _open_opencv; refuse REC while no camera is open. Alert when recording_frames stops advancing for more than 1 s during RECORDING and write a reconnect marker into the camlog.

### camera-recording#4 — Playback to LIVE trips the 2.5 s watchdog on the next tick because the timestamp predates the pause, so the camera is fully reopened

**Verdict: confirmed.** Already in the docs: partly: docs/ROADMAP.md:51 and the comment at application/src/runtime/camera_controller.py:400-404 record 'a silent reconnect after playback' and attribute it to the acquisition restart failing (PEAK GCWritePort -1001). The stale-timestamp cause is not recorded.

**Evidence.** application/src/runtime/recording_controller.py:509 stops IDS acquisition for playback; :344 and application/src/runtime/main_loop.py:650-654 restart it. start_acquisition (application/src/camera/ids_camera.py:1048-1104) never touches state.last_frame_time (written only at :1210) and frame_count stays above 0, so application/src/runtime/camera_controller.py:151-156 returns True on the next tick (main_loop.py:691-696 runs before the read) for any playback longer than 2.5 s, even when the restart succeeded. Not checked: whether the -1001 failure also occurs on the laptop.

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_callback_and_golive.py, case #4: real stop_acquisition, start_acquisition, get_last_acquired_age_s and _ids_stream_timed_out with a fake datastream. After a 3.0 s pause: 'start_acquisition() -> True, is_acquiring = True, frame_count = 100, last-acquired age = 3.01 s', 'next tick: _ids_stream_timed_out() = True', 'after 1 new frame: False'. After a 1.0 s pause: False.

**Field impact.** Every LIVE after reviewing or calibrating on a take (the D29 flow) costs a hidden full reopen with the GUI frozen; its duration on the laptop is not measured. Not during a show unless the playback remedy is used mid-show. It caused the 2026-10-06 crop flip (fixed) and drops a recording callback set at that moment.

**Fix sketch.** Reset the watchdog reference in start_acquisition (last_frame_time = now, or track 'acquiring since') and count frames per acquisition. If the restart really fails on this camera, make the reopen explicit with a visible 'reopening camera' status instead of leaving it to the watchdog.

### camera-recording#5 — Model-load and project-switch 'close the camera' act on the unused legacy CameraManager; the reopen after a model load has no retry

**Verdict: confirmed.** Already in the docs: no

**Evidence.** application/src/app.py:557-565: with UnifiedCamera, app.camera is a spare CameraManager 'for compatibility with camera state'. app.py:371-373 and application/src/runtime/config_manager.py:175-179 close that object and clear its flag; the IDS keeps streaming under 'Pausing camera during model load' (application/src/runtime/model_controller.py:476-480). app.py:375-376 reopens through _open_camera (model_controller.py:612,623,687; config_manager.py:194,262): no retry is scheduled and the pump needs _next_camera_retry_time > 0 (application/src/runtime/main_loop.py:576-583). Project-switch step 13 (config_manager.py:287) does use _attempt_camera_connect. app.py:1563-1567 restarts acquisition for an unchanged ratio.

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_model_reopen.py: real app._ModelCameraAdapter, CameraController, UnifiedCamera, CameraManager and MainLoop pumps; SDK faked. After port.close(): 'unified.is_open=True acquiring=True (IDS closes so far: 0) legacy.state.is_open=False'. One failed open inside reopen_and_flush: '_next_camera_retry_time=0.0 _camera_reconnecting=False', then '60 s of main loop: 1819 ticks, open attempts made = 0'. The same failure through _attempt_camera_connect is retried and the camera is open within 2 s.

**Field impact.** Only on a model / image-size / TensorRT change from the Advanced drawer or remotely (setup time, not a show), and only if the immediate close-then-open fails; how often is unknown. Then the camera stays down until Refresh; the 15 s alert says 'check USB3/power'. The operator notices (no image) but is pointed at the wrong cause.

**Fix sketch.** Route reopen_and_flush and the two project-switch error paths through _attempt_camera_connect. Give CameraController pause() and resume() that act on the active device, and make update_crop_ratio a no-op when the size would not change.

### camera-recording#6 — actual_fps is frames over the encoder thread's lifetime, and playback and replay take their clock from it

**Verdict: partly.** Already in the docs: no (docs/REMOTE_OPS.md:133 only lists actual_fps as a .meta key)

**Evidence.** application/src/core/video_recorder.py:144,170-176: actual_fps = frames_written / (encoder thread start to stop sentinel drained). Holds: a lagging encoder makes it the encoder's throughput; playback paces on it (:645-674). Goes too far: replay uses the scenario manifest's fps first (application/tests/replay.py:704-711), and the 11 v2 sidecars under projects show no lag (0 dropped, drain after stop <= 0.04 s). Their real bias is camera stalls averaged into one rate: the mur30m-0710 takes read 18.66-19.52 for a 20 fps camera. measured_fps is the node value (application/src/camera/ids_camera.py:1836).

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_recorder_playback.py 6: real VideoRecorder and _RecordingJob, 100 frames fed at 20 fps. Writer instant: actual_fps 20.194. Writer slowed to 15 fps: 'capture 19.99 fps over 4.95 s -> .meta actual_fps = 14.776 (dropped 0)'. Writer instant with one 1.7 s gap: 15.034. Inline scan of the 11 distinct v2 sidecars: max encoder drain after stop 0.039 s, min actual_fps 18.661 (1802 frames in 96.55 s).

**Field impact.** On the laptop the encoder keeps up, so the error is the stall share: up to -6.7 % on the 2026-10-07 takes. In-app playback then runs 7 % slow and a replay without a manifest fps stretches every seconds-based parameter by that much, while the stalls themselves vanish. Invisible, small against other tuning uncertainty.

**Fix sketch.** Stamp monotonic time per accepted frame in put() (first and last for the rate, or a per-frame sidecar) and store the camera node fps next to it, with a warning in .meta when the two differ by more than about 2 %. Rename the snapshot's measured_fps to configured_fps.

### camera-recording#8 — Playback frame numbers drift after a start offset or a step, and the loop reset is not ordered with the frame stream

**Verdict: confirmed.** Already in the docs: no

**Evidence.** application/src/core/video_recorder.py:527,606: the decoder labels frames with a private counter starting at 1, whatever start_frame was (:692-694,717-721); next_frame/prev_frame take the label from the file position (:824,843) but the decoder resumes with its own; they share the VideoCapture with the decoder thread, no lock, no paused check (:807-843; the step buttons are always enabled, application/src/gui_builder.py:1294-1309). 'target' is unbound at :718 when the container reports 0 frames (read only). The 'loop' event is queued (:576-580) before the wait for consumption (:589). seek_frame (:845) has no caller.

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_recorder_playback.py 8: real VideoRecorder and RecordingController, FFV1 takes carrying the file index in the pixels. play-at 100 with pause-at 150: 'first frames (label, real file index): (100, 100), (1, 101), (2, 102), (3, 103)', 'auto-pause fired at label 150 = file frame 250'. Pause, 5 steps, resume: '(41, 46), (42, 47), (43, 48) -> label is 5 frames behind the file'. Loop with a consumer at 20 ms/frame: '[27, 28, 29, RESET(loop), 0, 1, 2]'; at 80 ms/frame: '[26, 27, 28, RESET(loop), 29, 0, 1]'.

**Field impact.** Developer review only (in-app playback, --play-at-frame / --pause-at-frame, ISSUE reports): frame numbers in logs and reports can point at the wrong frame, and whenever the pipeline is slower than the take's pace (likely on dev37) one end-of-take frame is processed after each tracker reset. No effect on the live show.

**Fix sketch.** Publish (frame, file index from CAP_PROP_POS_FRAMES, epoch) from the decoder and reset the tracker when the main loop consumes a frame of a new epoch. Serialize reader access with a lock, reject a step unless paused, initialise target, and delete seek_frame.

### camera-recording#11 — Incomplete buffers are delivered as normal frames, and an unpack failure is a no-frame loop that only the reopen watchdog sees

**Verdict: confirmed.** Already in the docs: no

**Evidence.** application/src/camera/ids_camera.py:221,1330-1334: IsIncomplete is read, counted and printed; :1186-1217 store and forward the frame regardless. :1421-1431: _unpack_raw returns None with one printed line per frame for an unknown format or a size mismatch; the loop neither counts it nor stops (:1188), has_error() stays False and frame_count stays 0, so application/src/runtime/camera_controller.py:152-155 reopens 2.5 s after each open. Not checked: whether ids_peak hands this camera incomplete buffers; the local .meta and camlog sidecars predate the incomplete counter.

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_incomplete_unpack.py: real IDSCamera start_acquisition, _acquisition_loop, _track_frame, _unpack_raw and read; SDK faked. Every 5th buffer flagged IsIncomplete: 'buffers from the stream: 72, flagged incomplete: 14, frames stored: 71, frames read by the main loop: 71'. Pixel format Mono12: 'frames stored: 0, has_error() = False, _acquire_error = None, unknown format lines printed: 143, reopen watchdog after 3.0 s = True'.

**Field impact.** Unknown frequency: USB transfer errors were seen, but no incomplete count exists for any local take. A half-old frame would look like motion and could seed a ghost around a stall. The unpack loop needs another camera body or UserSet (pixel format); it would then cycle reopen forever with log spam and no alert.

**Fix sketch.** Drop incomplete buffers (count them, so the stall clock treats them as a missing frame). Validate pixel format and payload size once at open and surface one operator-readable error instead of looping.

### gui-operator#1 — Single-letter shortcuts have no text-focus guard: E toggles enhancement and P freezes the preview without any message

**Verdict: confirmed.** Already in the docs: yes, known_open: ARCH-17 (P3, 'check on hardware first') in docs/audit-2026-10/03-architecture.md:438 and under 'Small fixes' in docs/ROADMAP.md:163

**Evidence.** application/src/ui/adapter.py:250-297: _handle_key returns early only for the issue dialog, the wizard and the project picker (:252-271; the comment at :260-263 says typing in the rename field triggered shortcuts). Bare E submits ToggleEnhance(None, quiet=True), P TogglePreview(None, quiet=True); application/src/app.py:1166-1173,1197-1203 flip the setting with a console print and a checkbox sync only. Unguarded inputs: rig sheet (application/src/gui_builder.py:699-710), Save name (application/src/gui.py:2176-2181), OSC IP (gui_builder.py:1497-1502). Not checked on hardware: that DPG delivers the keys while an input has focus.

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_gui_threads.py keys: the real DpgUiAdapter._handle_key on a headless DPG context, fed the keys of 'IR pass filter': 7 commands, among them 'TogglePreview(enabled=None, quiet=True)' and 'ToggleEnhance(enabled=None, quiet=True)', plus ids, skeleton and trails overlay toggles; 'is_item_active/is_item_focused used: False'. Key delivery during real text focus was not reproduced (no viewport).

**Field impact.** Likely, given the picker comment: the operator note asks for rig-sheet updates and the app boots in RUN. 'PIR130' or 'BP850' contains P: preview freezes; an odd number of E's turns enhancement off, costing detections on a dark wall. The only trace is a checkbox in the closed Advanced drawer.

**Fix sketch.** In _handle_key ignore unmodified keys while any text or number input is active (dpg.is_item_active over the known tags, or a focus flag) or a modal is open. Move E and P behind Ctrl or drop them, and toast every keyboard toggle.

### gui-operator#3 — The light-change remedy puts a running show on a looped recording with no auto-return and no alert

**Verdict: confirmed.** Already in the docs: the remedy itself is the documented flow (docs/TERRAIN_DEMO_FR.md:47,54,84; D29 in docs/PLAN_25M_2026-10.md:376). Its effect on a running show is not recorded, and TERRAIN_DEMO_FR.md:84 says '(le spectacle continue)'. docs/ROADMAP.md:157 keeps 'light changes during a show' open for the alert's reference only.

**Evidence.** application/src/core/ops_monitor.py:233-234 and application/src/gui_builder.py:776-781 tell the operator to play the lighting's empty take and Calibrate. application/src/runtime/main_loop.py:636-659: while playing, the tick reads the take, not the camera; nothing gates OSC on playback (main_loop.py:813-816), so in RUN the take's stream goes to TouchDesigner. It loops (application/src/core/video_recorder.py:563-571); application/src/runtime/calibration_flows.py never goes live; ops_monitor.py:412-413 mutes the fps and no-detection alerts. Wrong detail: 'SLOT n' is hidden while playing (application/src/gui.py:1207-1212); the signs are the progress counter and button tints.

**Reproduction.** Inline script, real HealthMonitor.tick at 1 Hz for 30 min in RUN with nobody tracked: live camera -> no_detection at 30 s, 150 s, 270 s, 390 s; playback_active=True -> 'NO ALERT'. The endless loop is visible in tmp_analysis/audit-2026-11/verify_camera_gui/repro_recorder_playback.py 8 (the take replays until stopped). The end-to-end GUI flow was not run.

**Field impact.** Light changes mid-show are a named condition (sunset). Following the note, the dancers' ids vanish from OSC the moment the empty take plays (TD drops its video), for 30 s at best; the note never says to press LIVE, and if it is forgotten the show stays on the recording with no alert.

**Fix sketch.** Show a persistent 'PLAYBACK - not live' banner and raise an alert whenever the state is RUN and the source is a take for more than N seconds; return to LIVE automatically after a Calibrate started from a take. Correct '(le spectacle continue)' in TERRAIN_DEMO_FR and label takes with their lighting.

### gui-operator#5 — STANDBY/RUN is held twice and the adapter drops StateChanged, so a remote state change leaves the operator's badge and buttons wrong

**Verdict: confirmed.** Already in the docs: no (the comment at application/src/ui/adapter.py:411-413 calls the no-op deliberate: 'the GUI flips its own STANDBY/RUN visuals on click')

**Evidence.** application/src/gui.py:858-886: set_system_state writes gui._system_state, the badge and the button themes, then submits SetState. application/src/app.py:1056-1062 sets app.system_state and publishes StateChanged; application/src/ui/adapter.py:413 maps it to lambda e: None. Remote SetState is a 'control' command (application/src/services/remote_api.py:61), exposed as 'wdremote state run|standby' (extra/wdremote.py:1549). gui.py:945 gates IMPORT on the GUI copy. Overlay buttons: gui.py:836-838 against :1870-1877 (theme only). box_clamp_checkbox has no sync entry and is not saved (application/src/gui_builder.py:1149-1153).

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_gui_threads.py state: real EventBus, the DpgUiAdapter handler table and WallDanceGUI.set_system_state on a headless DPG context. StateChanged('standby') published as app.py does: 'runtime state = standby; gui._system_state = RUN, badge text = RUN'. After a local click: 'gui._system_state = STANDBY, badge text = STANDBY'.

**Field impact.** Live scenario: the note has the operator tick 'Allow remote control during RUN', and 'wdremote state standby' precedes every heavy remote job. The laptop then shows RUN in green while nothing is sent to TouchDesigner; only the missing skeletons hint at it. A local click on RUN repairs it.

**Fix sketch.** Make the GUI a view: the buttons only submit SetState, and the badge, themes and gui._system_state are set in the StateChanged handler. Do the same for the overlay buttons, and add box_clamp to the sync map and the saved config.

### gui-operator#6 — An unattended restart waits on the project picker and, when an engine is missing, on the TensorRT prompt

**Verdict: partly.** Already in the docs: partly: the picker on restart and the manual launcher Restart are in docs/audit-2026-10/03-architecture.md section 3.5 (lines 399-405), with ARCH-6(6) as the planned fix; D13 settles 'restart into STANDBY, the operator re-arms'. The TensorRT prompt and the calibration modal are not recorded.

**Evidence.** Holds: application/src/runtime/main_loop.py:308-328 with application/src/core/config.py:757: nothing loads until a human answers the picker (no launcher sets WALLDANCE_AUTOLAUNCH_LAST or --project; launcher/gui.py:256 is a manual Restart). application/src/runtime/model_controller.py:228-259 and :340-365: the TensorRT prompt spins with no timeout and no is_running check, and the tick (command drain, remote API) does not run meanwhile. Does not hold: the calibration-result modal (application/src/gui.py:2795-2848) is no_close but the loop and OSC keep running behind it, and it plays no part in a restart.

**Reproduction.** not reproduced: read only

**Field impact.** After a crash or power cut mid-show nothing comes back without a person at the laptop: launcher Restart, then the picker. A human re-arming is the accepted model (D13), but only the crash marker shipped. The TensorRT prompt adds a wait only when the engine file is missing.

**Fix sketch.** Implement ARCH-6(6): when a crash marker is present, load the last project after a visible countdown, into STANDBY as D13 says unless the owner changes that. Give the TensorRT prompt a recommended default, a timeout and an is_running check.

### gui-operator#7 — The readiness check runs on the frame-loop thread and holds it about 0.25 s, on every click of rail '2 Calibrate' and on a remote CheckReadiness in RUN

**Verdict: confirmed.** Already in the docs: no. The code states the choice: application/src/app.py:2317-2319 'Runs synchronously on the command/main-loop thread (the DPG-safe seam, ~0.3 s incl. the OSC probe)'. docs/REMOTE_OPS.md:122 lists CheckReadiness as 'safe, always'.

**Evidence.** application/src/gui.py:536-541: opening rail '2 Calibrate' submits CheckReadiness each time; its comment says 'cheap, off-thread'. application/src/app.py:2314-2335 runs the report inside the tick's command drain; application/src/core/ops_monitor.py:158-184 waits the full OPS_OSC_PROBE_TIMEOUT_S = 0.25 (application/src/core/config.py:1099) when a UDP listener exists and stays silent. application/src/services/remote_api.py:58 classes CheckReadiness 'safe'. The Go-Live path uses a thread (app.py:2229-2232). Tooltip: '~0.3 s; never blocks RUN' (application/src/gui_builder.py:827).

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_readiness_block.py: real probe_osc_udp on loopback. Silent UDP listener present: 'probe wall time per call (ms): [256, 251, 250, 254, 252]'. Nothing listening: fail in 0.1 ms. OSC disabled: skip in 0.0 ms. The full _build_readiness_report (NVML, disk) was not timed.

**Field impact.** TouchDesigner listens on the same laptop, so every click on '2 Calibrate' in RUN, and every remote CheckReadiness, freezes the points for about 5 frames (0.25 s) and skips those camera frames. No id is lost and D34 does not count it as a stall; a visible hitch, not an outage.

**Fix sketch.** Build the report on the existing OpsReadiness thread and hand the rows to the tick through a small queue, or skip the probe while the sender has sent within the last second. Do not auto-run it on phase entry while in RUN.

### gui-operator#10 — Toast events published by background workers run DPG window code on the worker thread

**Verdict: confirmed.** Already in the docs: partly: docs/audit-2026-10/03-architecture.md:136 lists 'publish DPG toasts off the main thread' as a risk of the worker threads; no action item covers it (ARCH-16 is single-flight and kill-on-quit).

**Evidence.** application/src/runtime/api.py:1110-1116: EventBus.publish calls subscribers on the caller's thread. Toasts come from workers: OpsReadiness at every STANDBY->RUN (application/src/app.py:2231,2310), DryRunReplay (:2425), the sweep (:2508), KnownNTune (:2607), VideoImport (application/src/runtime/recording_controller.py:595-602,639-662). application/src/gui.py:2929-2948: show_toast deletes 'toast_window' and opens a 'with dpg.window' on DPG's global container stack; :2950-2951 and :2978-2979 state the rule being broken. EventBus catches the exceptions, so the visible result is a lost toast and a traceback.

**Reproduction.** tmp_analysis/audit-2026-11/verify_camera_gui/repro_gui_threads.py threads: a Toast published from a thread named OpsReadiness ran show_toast on 'OpsReadiness'. Stress: the real WallDanceGUI.show_toast body from the main thread and one worker, 3000 calls each, headless DPG, no render: 'calls completed: worker 2014, main 328', 'exceptions: 3658' (SystemError in delete_item 3288x, 'No container to pop' 369x, add_text 1x), 'DPG items alive: before 0, after 371'. No native crash in this run; the race with render_dearpygui_frame could not be tested headless.

**Field impact.** One collision needs a worker toast within about a millisecond of a main-thread toast or build: rare per event, but Go-Live fires one every time and health alerts toast on the main thread. Typical cost is a lost toast or an orphaned item; a native crash is possible and not shown.

**Fix sketch.** In DpgUiAdapter._on_event, when the caller is not the main thread, append the event to a deque that the tick or the render pump drains. Workers then never touch DPG.

## deploy+tests

### remote-deploy#2 — The reinstall trigger ignores the pin file and is not persisted, so code and venv can silently diverge

**Verdict: confirmed.** Already in the docs: no. The trigger list is only described as-is (docs/REMOTE_OPS.md §7 line 210; docs/audit-2026-10/03-architecture.md §3.1 line 315). Commit e7ba3da records that a failed install stops the launch, not that the next start skips the install.

**Evidence.** launcher/git_manager.py:237-240 sets needs_install only for install.bat or application/pyproject.toml; install.bat:72,151 installs from application/requirements-prod.txt. extra/wdslot.py:439,475 has the same list. launcher/gui.py:109 initialises needs_install=False per process; it is set at :156/:210, read at :229-233 and never stored. run.bat:19-25 checks only that .venv exists and that cv2, torch import. gui.py:303-304 promises a re-run that no code performs. application/tests/test_launcher_git_manager.py:244-254 covers the pin changing only together with install.bat.

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/launcher_repro.py (real git_manager.py and gui.py sequence, dulwich 1.2.17, fake Tk and fake .bat). A1, release changing only requirements-prod.txt, answer Yes: 'Update complete.' then 'RUN run.bat', install.bat never run; wdslot.classify_change('application/requirements-prod.txt') -> None. A2, install.bat exit 1 then 'Exit (fix later)'; a new launcher process logs 'No updates available.' and runs run.bat. git status --short empty after all runs.

**Field impact.** Bites at the next dependency bump authored off the laptop, or after any failed or abandoned install (a pinned-sync dry run already failed on café wifi on 2026-10-07, commit e7ba3da). New code then runs on the old venv with no warning; the operator sees nothing until an import or runtime error.

**Fix sketch.** Have install.bat write a stamp (hash of requirements-prod.txt, install.bat and pyproject.toml) after its verify step, and have the launcher or run.bat run install.bat whenever the stamp is missing or differs. Add requirements-prod.txt to wdslot.classify_change, and add launcher tests for 'pin only' and 'install failed, relaunch'.

### remote-deploy#3 — update() moves HEAD before writing the tree, and a failed update is launched anyway and never repaired

**Verdict: confirmed.** Already in the docs: no

**Evidence.** launcher/git_manager.py:202-203 moves the release ref and HEAD, then :218-225 calls dulwich build_index_from_tree, which writes changed files in path order and the index last (dulwich 1.2.17 index.py:2602-2658; 'Suitable only for fresh clones'). launcher/gui.py:220-222 catches the exception, shows 'Update Failed', does not return, and reaches run.bat at :245. check_updates returns UP_TO_DATE once HEAD equals the remote (:122); dirty_files() is consulted only for BEHIND/DIVERGED (gui.py:178-189). core/version.py:40 reports .git/HEAD.

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/launcher_repro.py A3: one tracked file made read-only (stands for a Windows lock or a full disk; not reproduced on Windows). Start 1: 'Failed to update: [Errno 13] Permission denied' -> error dialog -> 'RUN run.bat'. After: HEAD = new release, a_first.txt = v2, zz/late.txt = v1. New GitManager: check_updates() -> UP_TO_DATE, dirty_files() -> ['a_first.txt', 'zz/late.txt']. Next start: 'No updates available.'. Next release: 'Update Skipped - Local Changes'; update() -> DirtyWorkingTreeError.

**Field impact.** Low probability: it needs a write failure during the seconds of an update (disk filled by recordings, antivirus lock, power cut). Then the show runs an unidentified mix of two versions reported as the new commit, and every later update is refused until a developer repairs the tree over SSH. The operator sees one error dialog.

**Fix sketch.** Write the tree first and move the refs last, or drop a marker holding the backup ref before step 3 and remove it after step 5. At start, if the marker exists or the tree is dirty while UP_TO_DATE, re-materialise HEAD; never launch after a failed update without restoring the backup ref.

### remote-deploy#4 — The launcher blocks app start on the network and on an uninformed Yes/No update prompt

**Verdict: confirmed.** Already in the docs: partly. The plain Yes/No prompt is the intended delivery path (decision D1; docs/audit-2026-10/03-architecture.md §3.1 table B; docs/ROADMAP.md N-13). The fetch without a timeout and the misleading 'No updates available.' are not recorded anywhere.

**Evidence.** launcher/git_manager.py:114 calls porcelain.fetch on the start path; dulwich sets a timeout only when http.timeout is configured or passed (client.py:4387-4394, 4437-4439) and the launcher sets none. gui.py:145 disables the button for the whole sequence. check_updates prints the reason to stdout and returns UNKNOWN (:119, :130); gui.py:225-226 then logs 'No updates available.'. gui.py:410 is a bare askyesno with no content, asked at every start including Restart (gui.py:144-146).

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/launcher_repro.py A4. Origin pointed at a loopback socket that accepts and never answers: the sequence thread is 'STILL BLOCKED' at 'Checking for updates on channel release...' after 5, 15 and 30 s, run.bat never reached. Origin with connection refused: 'No updates available.' after 0.5 s, the real error ('Max retries exceeded ... Connection refused') only on stdout. Windows connect and retry timings not measured.

**Field impact.** The laptop is on 4G tethering at venues. A link that connects but stalls leaves the launcher on a spinner with no skip, so the app does not start; the operator notices but has no remedy except cutting the network. A Yes minutes before doors can start a multi-GB reinstall.

**Fix sketch.** Start the installed version without waiting: run the fetch in a thread with a hard deadline (http.timeout written at clone plus a join of about 8 s) and offer the update at the next start. Log the real state ('offline, running <sha>') and add a launcher.json hold so on-site starts never prompt.

### remote-deploy#6 — A bad release cannot be rolled back on site: moving `release` backwards is ignored and the backup refs have no restore path

**Verdict: confirmed.** Already in the docs: partly. docs/audit-2026-10/03-architecture.md §3.1 table B records 'Ahead: skipped forever'; docs/ROADMAP_v2_REVIEW.md §5 step 8 names the backup ref plus a manual venv copy as the rollback, a developer procedure. No doc says the launcher itself cannot restore.

**Evidence.** launcher/git_manager.py:124-127 returns AHEAD when local HEAD descends from origin/release; gui.py:223-224 logs 'ahead of the server - skipping update'. update() writes refs/walldance/pre-update-<stamp> (:175-178) and gui.py:200-201 says 'it can be restored', but no code outside tests reads those refs; GitManager exposes only check_updates, clone, current_version, dirty_files, is_cloned, update. install.bat:151 syncs the venv in place. Never updating when ahead is deliberate (application/tests/test_launcher_git_manager.py:98-102).

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/launcher_repro.py A5: bad release taken; remote `release` reset to the previous commit; next start logs 'Local version is ahead of the server - skipping update.' and runs run.bat; local HEAD stays 76b20529bd (bad), a_first.txt = BAD; refs/walldance/pre-update-20261118-104615 exists and nothing reads it.

**Field impact.** Only after a bad release is accepted on site. The technician has no way back; the developer needs the tailnet (deploy the old ref to the DEV slot and run it, or a revert commit plus internet and a Yes). Since TD drops video on any loss, a bad tracking release costs the session until then.

**Fix sketch.** Add 'Start previous version' to the launcher: list the backup refs, re-materialise that tree offline and remember the choice so the same update is not offered again. For releases that change dependencies, rename .venv to .venv.prev before the sync so the rollback covers both.

### remote-deploy#8 — Remote commands are constructed without type or range validation and can poison per-frame state

**Verdict: confirmed.** Already in the docs: no

**Evidence.** application/src/services/remote_api.py:269-273: the only argument check is ctype(**args). In runtime/api.py 49 of the 66 commands with fields have no __post_init__ (SetTrackerMaxAge :304, SetPersonHeight :294, SetOscTarget :537) although the docstring says 'validated on construction' (:8). app.py:1974-1975 stores the raw value; core/tracker.py:3285-3289 computes with it every frame. Correction: a bad value saved with SaveConfig is clamped or dropped at the next load for the keys in config_schema._RANGES (:52-79, :300-317), so persistence is limited; the live path uses no such table.

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/api_repro.py: wdremote._parse_kv(['value=3O']) -> {'value': '3O'}; submit SetTrackerMaxAge -> (200, queued); also 200 for value 0, -5, SetPersonHeight -300, SetOscTarget ip=12 port='nine', SetMaxDancers '2' (99 -> 400). After drain tracker.max_age = '3O'; the next 60 tracker.update calls with one dancer: 60 raised 'TypeError: can't multiply sequence by non-int of type float'. GET /events?since=abc and POST body [1,2]: connection closed without a response; Range 0-999999 on a 10-byte file: IncompleteRead.

**Field impact.** Needs a remote session (developer or agent) and a typo. In STANDBY nothing gates it and the value carries into RUN: with a dancer tracked every frame fails, no OSC for 2.5 s, then exit code 3 (main_loop.py:483-488). The operator sees a frame-error alert, then the app closes.

**Fix sketch.** In RemoteApi.submit, coerce each argument against the type hints command_schema() already computes and answer 400 on a mismatch; reuse config_schema._RANGES for the numeric commands so the remote API, the GUI and the config loader share one range table. Turn bad query values and non-object bodies into a 400 and clamp Range.

### remote-deploy#9 — `slot run --via-launcher` registers its scheduled task with default settings: stopped on battery, 72 h limit

**Verdict: confirmed.** Already in the docs: no. docs/REMOTE_OPS.md §7 describes the interactive scheduled task and recommends --via-launcher (line 212); nothing on the task settings.

**Evidence.** extra/wdslot.py:384-391 registers 'WallDance-launcher' with -Action and -Principal only; the sibling windows_launch_script (:360-362) passes -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries and an explicit 3-day ExecutionTimeLimit. release-check advertises --via-launcher (:495-496). application/tests/test_wdslot.py:145-153 covers only the run.bat variant. Not checked on Windows: that the laptop's Task Scheduler defaults are 'stop on battery' and 72 h (Microsoft's documented defaults), and that ending the task also ends the app, a child of the launcher.

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/slot_repro.py (cmd_slot_run with a fake transport, EncodedCommand decoded). --via-launcher -> "Register-ScheduledTask -TaskName 'WallDance-launcher' -Action $a -Principal $p -Force": has -Settings False, AllowStartIfOnBatteries False, DontStopIfGoingOnBatteries False. --slot live and --slot dev -> has -Settings True, both battery switches, 'New-TimeSpan -Days 3'. Windows behaviour not reproduced.

**Field impact.** Only when the show app was started remotely with --via-launcher; the operator's desktop shortcut is unaffected. Then a dropped power brick or a generator changeover could end launcher and app with no restart, and a session started through either remote path ends after 3 days. Rare; total loss of output when it happens.

**Fix sketch.** Build both task registrations through one helper that always passes -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero), and extend test_windows_launch_script_is_interactive_task to the launcher variant.

### remote-deploy#11 — The DEV slot shares LIVE's venv, models, projects (by default), API port and token

**Verdict: by design.** Already in the docs: yes: docs/REMOTE_OPS.md §7 lines 188-190 ('DEV shares LIVE's venv, models/ and projects/ through junctions ... Recordings and configs saved from DEV are real project data; deploy --isolated-projects avoids that') and line 205 ('Only one app runs at a time (camera and ports are exclusive)'). The shared engine folder and the untestable pin change are not spelled out.

**Evidence.** extra/wdslot.py:48-49 SHARED_DIRS = application/.venv, models ('never copied'); apply() :170-177 junctions them, and projects unless --isolated-projects (default False). cmd_slot_run :370-382 refuses a second app and sets no WD_REMOTE_API_PORT; runtime/main_loop.py:377,384 uses one token file and port 8765. core/config.py:17 puts engines in <root>/models, so a DEV rebuild writes LIVE's engines. docs/audit-2026-10/03-architecture.md §3.6: a config from a newer schema version loads as v2 without a warning.

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/slot_repro.py: SHARED_DIRS = ('application/.venv', 'models'); deploy default isolated_projects = False; the generated DEV launch script sets WD_SLOT, WD_REMOTE_ALLOW_QUIT, WD_REMOTE_CONTROL and no WD_REMOTE_API_PORT. Junction behaviour on Windows not reproduced.

**Field impact.** Intended trade: no second multi-GB venv, real project data for tests. Residual risk: a DEV session rebuilds or replaces the show's TRT engine, or saves a project config the release build reads differently, and a dependency change cannot be tried before it lands on LIVE. It would show as 'TensorRT OFF' or odd settings at the next start.

**Fix sketch.** Keep the sharing but give DEV its own engine folder (WD_ENGINE_DIR already exists) and make deploy warn or refuse when the candidate's requirements-prod.txt differs from LIVE's. Consider making isolated projects the default, with a copy of the selected project.

### remote-deploy#12 — Phone monitor is always on, bound to all interfaces without authentication, and its advertised address can be wrong on a show network

**Verdict: confirmed.** Already in the docs: partly: docs/audit-2026-10/03-architecture.md:135 ('no auth, read-only') and §3.4 item 7 (0.0.0.0:8080 firewall prompt); 04-performance.md:240 (frame copy 0.25 ms). Always-on open access is the design (web_monitor.py:4-8, ROADMAP P0). /control, the thread growth and the address choice are not recorded.

**Evidence.** core/config.py:570-572: enabled, 0.0.0.0:8080; runtime/main_loop.py:356-368 starts it unconditionally and :989-990 copies every preview frame. services/web_monitor.py:545-557 serves stream, snapshot, stats and /control to anyone; /control (:578-596) changes what get_jpeg() returns, which the remote API's /snapshot.jpg also serves (main_loop.py:381). ThreadingHTTPServer, HTTP/1.1, no handler timeout (:532-535). get_lan_ip (:40-52) takes the interface that routes to 8.8.8.8, else gethostbyname.

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/api_repro.py (monitor bound to loopback for the test): GET /snapshot.jpg with no credential -> 200 image/jpeg 10959 bytes; GET /control?focus=1 -> 200 and get_jpeg() output changed; 60 idle keep-alive clients -> 59 handler threads still alive 4 s later, handler timeout None; get_lan_ip() with no route to 8.8.8.8 -> 127.0.1.1 (Linux fallback; the Windows result was not checked).

**Field impact.** Low. On a venue LAN or tether anyone can watch the IR feed of the performers and switch the focus display; there is no path to tracker or OSC state. A wrong QR address only wastes setup time when the phone is not on the default-route network. Idle clients hold threads in the show process.

**Fix sketch.** Start the monitor from the QR dialog and stop it after idle minutes or on RUN unless pinned, and put a per-session token in the QR URL. Set a handler timeout and a client cap, and show one URL per interface address.

### tests-harness#3 — The golden regression test compares tracker-internal event counters, not what is reported or sent

**Verdict: confirmed.** Already in the docs: yes: docs/ROADMAP_v2_REVIEW.md §1.9 lines 303-304 ('compare tracker summaries ... prove nothing about what TD receives'), docs/AUDIT_2026-10.md:82 and docs/audit-2026-10/03-architecture.md:39 ('3 aggregate-count fixtures'), item ARCH-13. TEST-2 (emitted goldens, ROADMAP N-6) was the answer; it runs on fixtures, not on footage.

**Evidence.** application/tests/test_regression_replay.py:103-108 runs replay.py --scenario X --trt --out only; :57-65 and :116-127 compare real/ghost/marginal/total tracks, swap_count, zero_detection_frames and avg_detections. replay.py:659-697 builds them from the tracker JSONL through analyze_session.py:175-191 (tracks bucketed by MATCH count); :896 pops per_frame before --out. TRACKER_SWAP_CORRECTORS is False (core/config.py:441) and no trio manifest pins it: the three goldens hold swap_count 0. Windows are 300, 300 and 600 frames; the goldens date from a3f318d (2026-06-24).

**Reproduction.** Throwaway script on the first 6000 lines of a real tracker log (application/tracking_events.jsonl, untracked): the seven numbers as logged = {'real_tracks': 7, 'marginal_tracks': 1, 'ghost_tracks': 0, 'total_tracks': 8, 'swap_count': 11, 'zero_detection_frames': 3, 'avg_detections': 2.124}; after rewriting every FRAME_SUMMARY with emitted_slots = [] and adding a SLOT_EVENT 'lost' per frame: identical. The rig test itself was not run (GPU, footage).

**Field impact.** No direct show effect; it decides what a release can break unseen. A change in the report gate, identity slots, smoothing, box clamp, OSC emit or stall hold leaves the only footage gate green, and the operator would discover it as lost or jumping points in TouchDesigner.

**Fix sketch.** Store per scenario the score components, the continuity block and both streams' KPIs, and assert the manifest pass line plus a non-regression delta on the emitted stream; keep the counters as diagnostics. Add the two long-span takes so continuity is gated on footage, not only 300-frame windows.

### tests-harness#7 — Emitted-stream goldens: exact compare runs only on dev37, floors record current behaviour, and the fixture path omits the shipped evidence channels

**Verdict: confirmed.** Already in the docs: partly: the version-stamp skip is deliberate (commit b17069c: 'so pytest -x on the laptop's stack does not stop on float tie-breaks'); emitted pass lines for the long-span manifests are planned (docs/ROADMAP.md P-6; ROADMAP_v2_REVIEW §1.9 lines 312-315); PLAN_25M lines 546-548 notes the goldens passed unchanged. The missing evidence channels and the loose floors are not recorded.

**Evidence.** application/tests/test_emitted_golden.py:25-27 skips the exact compare when numpy/scipy differ from the stamp; all six goldens carry numpy 2.2.6 / scipy 1.15.3, CI pins numpy<2.0 (.github/requirements-ci.txt:4), the laptop 2.4.4 (requirements-prod.txt:44). FLOORS (:41-49) cover coverage, on_dancer, coasting. emitted_golden.py:115 calls slots.update(cands, t, hidden=...) where core/pipeline.py:1525 also passes belt, bounds, fg, t_motion; slot_replay.py:44-49 never sets zone_ok, hip, conf. Corrections: floors sit 0.7-5.3 points under current KPIs (claim: 0.5-2); six slot commits on 10-07 and two on 10-08, not eight on 10-07.

**Reproduction.** tmp_analysis/audit-2026-11/verify_deploy_tests/th_repro.py and th7.py (CPU, fixtures only). Exact compare: 6 run on dev37; 0 run, 6 skipped with the laptop's or CI's versions. white-duo-full golden: coverage 0.8646, gap_max 8.43 s, 13 id switches, against its manifest's coverage_min 0.98 and 0 gaps >= 1 s. Floors stay green on 6/6 fixtures with stability 0 (jitter +7 to +40 %), with filter_input raw (jitter 0.752 -> 4.215 % on texture-duo-win) and with smart_hold off (no KPI moves at all).

**Field impact.** Indirect. Off dev37 the slot layer is guarded by three floors per fixture; the demo KPI's jitter, longest hole and id switches are not floored, and the foreground, belt and box-confidence paths (default ON) are in no golden. A regression there reaches the laptop and shows as jittery or dropped points.

**Fix sketch.** Drive the fixtures through candidate_from_track and one slot-driving function shared with the pipeline, recording bounds and box confidence in the rows. Add floors for gap_max, id_switches and jitter, and run the +/-1 px compare on every stack, warning only on a proven tie-break difference.

### tests-harness#8 — CI skips the riskiest glue although it needs no GPU, including every camera-stall test

**Verdict: confirmed.** Already in the docs: no. docs/ROADMAP.md:68 lists 'ARCH-9 CI that can pass + Windows job' as shipped; docs/ROADMAP_v2_REVIEW.md:454 only notes that CI had never run before the first push to release.

**Evidence.** application/tests/test_camera_stall.py:107 has a module-level pytest.importorskip('torch') below six tests that import only core.camera_stall (stdlib only). Ten pipeline modules (102 tests) and ten GUI modules (22 tests) skip whole. Beyond the claim: tests/test_engine_dir_override.py:2 imports core.model_manager, which imports ultralytics unguarded (model_manager.py:14); it was added (f93aac6, 2026-10-06 13:27) after 'make CI pass' (d7b576c, 10:11) and is in origin/release. tests/test_auto_height.py:37 and tests/test_foreground.py:325 import core.pipeline inside tests, unguarded.

**Reproduction.** CI-like environment under tmp_analysis/audit-2026-11/verify_deploy_tests/ci_env (venv packages symlinked minus torch, ultralytics, dearpygui, kornia, tensorrt; python -S; numpy 2.2.6, not <2). Full export of origin/release 63f4989, the ci.yml command: 'Interrupted: 1 error during collection', '19 skipped, 1 error', exit 2, no test run. HEAD with that file ignored: 883 passed, 52 skipped, 15 failed (dev37: 1028 passed, 9 skipped). test_camera_stall.py alone: '1 skipped', 0 run of 14; its first 106 lines: 6 passed. git status --short empty afterwards.

**Field impact.** No show effect by itself. If Actions is enabled, CI on `release` has been red with zero tests run since its first push (not checked on GitHub: no network), so the D34 stall logic, the slot-to-OSC glue and the GUI build are verified on dev37 only, while camera stalls occur 0.7-2.9 times a minute.

**Fix sketch.** Guard the three unguarded imports (or move ScaledTrack and ProcessingSettings into a torch-free module), move the importorskip in test_camera_stall.py below the clock tests, and add a CI job with CPU torch, ultralytics and dearpygui. Read the first CI run on `release` and treat a collection error as a failed release gate.

### tests-harness#12 — The soak covers the pipeline on PyTorch only, not the application that runs for hours

**Verdict: confirmed.** Already in the docs: no. docs/TODO.md:33 and docs/ROADMAP.md:65 record the 4 h soak PASS of 2026-06-13 as the long-run evidence; it predates Track P (2026-06-24) and every October layer. No doc states what the soak leaves out.

**Evidence.** application/tests/soak.py:204-205 builds the processor with use_gpu_path=args.gpu and has no --trt option; replay.py:242 sets use_fp16=use_trt, so --gpu means FP32 PyTorch. The loop (:243-261) is cap.read + proc.process; replay.py:258 forces osc_enabled=False. The verdict (:335-348) reads RSS slope, fps trend and a 120 s stall sentinel. soak.py names no MainLoop, camera, recorder, DearPyGui, RemoteApi or WebMonitor. Without --gpu, core/pipeline.py:592-595 raises on the first frame, and tests/test_soak.py:26-33 passes no --gpu.

**Reproduction.** FrameProcessor(model=None, settings with use_gpu_path=False), which is what soak.py builds by default: gpu_path_active False; process() -> 'RuntimeError: GPU pipeline required (Track P removed the CPU fallback path); no GPU pipeline is active.' String scan of soak.py: '--trt' False; osc, MainLoop, RemoteApi, WebMonitor, recorder, dearpygui, camera all False. No soak was run (GPU, hours).

**Field impact.** The show runs for hours on TRT with the IDS camera, DearPyGui, OSC, the recorder and the tracking log, beside TouchDesigner at 84-87 C. None of that has had a long-run check since June; a leak or a reconnect loop would first appear mid-show as falling fps or a stall.

**Fix sketch.** Add --trt and make it the default so the soak runs the show backend. Add an app-level soak: the real app on looped playback with a scripted camera that injects stalls and reconnects, driven through the remote API, with the app's own alerts and crash marker as the verdict.
