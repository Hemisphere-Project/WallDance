# Subsystem map — config-ops

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

Defines every tunable of the pipeline and how a venue's settings are persisted, validated, migrated and re-applied: per-project JSON history, show/rehearsal lighting bundles, safe defaults and the last-project pointer. Also the operational safety net: Go-Live readiness checks, 1 Hz health alerts, a main-loop hang watchdog, the persistent stdout/stderr log, the crash / unclean-exit marker and code-version provenance. From the operator's seat it decides what a restart comes back to, and what the technician is told when something degrades.

## Architecture

- application/src/core/config.py: a flat module of 303 constants plus the TrackingMode enum and DETECTION_DEFAULTS; imported by name at about 34 sites, so values are fixed at import.
- core/config_schema.py: pure functions migrate / flatten / structure / split_profile / validate_flat / fill_detection_defaults; PROFILE_KEYS (14), _RANGES (25), rig-sheet sanitisers, inherited_rig.
- core/config_store.py: ConfigStore (projects/NAME/NAME_STAMP.json append-only history, _safe_defaults.json, last_project.txt), atomic_write_text/json, latest-valid lookup.
- runtime/config_manager.py: ConfigManager owns the store, _current_project, the _profiles bundles, _active_profile and the deferred _pending_project_switch. The two whole-app translators stay on WallDanceApp (app.py:1218 _get_saveable_config, app.py:1315 _apply_config_without_model) and are injected as callables.
- core/ops_monitor.py: check_* functions feeding ReadinessReport; HealthMonitor.tick() returning Alert objects; LoopWatchdog daemon thread.
- services/app_log.py: _Tee replaces sys.stdout/sys.stderr and writes logs/walldance_STAMP.log; faulthandler enabled into that file.
- services/crash_marker.py: session_running.json sentinel plus last_crash.json, archived as last_crash.reported.json at the next start.
- core/version.py: commit/branch read from .git, or DEPLOYED.json for a dev slot.
Runtime values live in ProcessingSettings (core/pipeline.py:109, 44 fields) and in tracker / enhancer / processor / camera-controller attributes.

## Data flow

Load: picker, --project or WALLDANCE_AUTOLAUNCH_LAST -> ConfigManager._execute_project_switch -> ConfigStore.load (newest valid file by mtime) -> migrate (v1 to v2, both bundles kept in _profiles) -> flatten (active profile over shared) -> validate_flat (clamp/drop, warnings to console plus one toast) -> fill_detection_defaults -> model load -> app._apply_config_without_model pushes values into ProcessingSettings, tracker, enhancer, processor, camera controller and the GUI (ControlSync events) -> remember_last_project.

Save: operator Save, the calibration wizard's confirm, known-N / auto-tune Apply -> app._get_saveable_config() (75 keys read back from live objects) -> structure() (live values become the active bundle, the inactive bundle is carried) -> ConfigStore.save (new timestamped file, atomic) -> last_project.txt.

Ops: MainLoop._ops_heartbeat every iteration: watchdog.beat(); at 1 Hz: OSC error poll, sentinel project note, raw-height samples, light-ratio median -> HealthMonitor.tick -> _emit_ops_alert: print "[Alert]" (tee -> log file and launcher pipe), api.Alert bus event (GUI toast 8 s; alerts strip only for light_changed; remote API ring buffer, pull only), tracker JSONL OPS_ALERT. Readiness runs on STANDBY->RUN (thread) or on demand.

Crash: begin_session reports the previous marker/sentinel (console block plus 15 s toast). Tick exceptions skip the frame; 50 in a row -> marker and exit code 3 -> launcher shows a Restart button.

## Threading and state

- Config load, save and apply all run on the main-loop thread: UI and remote commands are queued and drained by RuntimeAPI.drain; a project switch is deferred through _pending_project_switch and executed by MainLoop._tick_pumps. _SAVE_LOCK serialises "pick a free timestamped name and publish it".
- LoopWatchdog: daemon thread "OpsWatchdog", polls every 1 s, reads _last_beat (monotonic); _busy_depth is lock-protected on write.
- Go-Live readiness: daemon thread "OpsReadiness" reads app state unsynchronised (best effort); the on-demand check runs synchronously on the main thread (about 0.3 s including the OSC probe).
- HealthMonitor: single-threaded; fed wall-clock time.time() from the main loop; state is a set of "since" timestamps, the fps sample list and a last-fired map.
- _Tee: one lock around file writes from any thread; the console write is outside the lock.
- Persistent state: project JSON history, _safe_defaults.json, last_project.txt, logs/*.log, session_running.json, last_crash(.reported).json (plus plates/latest.npz, owned by another subsystem).
- In memory only until an explicit Save: the active lighting profile, every live tweak, the height the guard adopted, box-clamp, IDS auto-exposure flags. RUN/STANDBY is not persisted; the app always boots in RUN (app.py:521).

## Failure handling

- Truncated or corrupt newest JSON: skipped, the previous valid save is used (console line only); a non-object file is rejected; an unreadable chosen file gives a toast and the camera is reopened.
- Bad values: 25 numeric keys clamped, junk dropped, ROI / mask / rotation / 4 bools checked, with warnings. The other ~35 saved keys and the inactive profile bundle are not validated.
- Save failure (OSError/TypeError/ValueError): red toast, history untouched (atomic temp + fsync + os.replace).
- Exception inside migrate or apply during a project switch: not caught in ConfigManager. The camera stays closed and _model_loading stays True, so the loop idles.
- Tick exception: frame skipped, rate-limited alert; 50 consecutive -> clean shutdown, crash marker, exit code 3.
- Hang: the watchdog prints one "[Alert]" line; the stack dump it attempts fails under the tee (finding 5); no recovery action.
- Native crash / kill / power loss: the sentinel yields an "unclean exit" report at the next start; faulthandler writes fatal-signal stacks into the log file.
- Nothing restarts the app. The launcher offers a Restart button on a non-zero exit, and a normal start stops at the project picker unless WALLDANCE_AUTOLAUNCH_LAST or --project is given.
- Health-tick exceptions are swallowed with a print. Logs are pruned by count (40) at start only, with no size cap.

## Tunables

- core/config.py: 303 module-level constants (65 TRACKER_, 50 MOTION_, 45 AUTOCAL/AUTOCAL2, 23 OPS_, 9 IDENTITY_, 8 HEIGHT_, ...). Roughly 50 have a per-project key; the rest are code-only.
- Per project: 75 keys saved (app.py:1222-1313), 14 of them per lighting profile (two bundles), plus 5 keys that are loaded but never saved.
- ProcessingSettings: 44 fields, some never persisted.
- Schema validation: 25 numeric ranges, imgsz preset snap, 4 bool keys, rotation/mirror, 6 ROI keys, mask shape, 11 rig-sheet fields.
- Ops thresholds (config.py:1098-1134):
  - fps below 0.5 x the 60 s median for 10 s
  - no tracks for 30 s
  - over cap for 10 s
  - height out of gate for 120 s (60 s window, at least 20 samples)
  - camera down for 15 s
  - GPU at or above 85 C for 30 s (5 s poll)
  - per-kind cooldown 120 s
  - light ratio outside 1/1.5 .. 1.5 on a 10 s median
  - watchdog 10 s hang / 1 s poll
  - 50 consecutive tick errors -> exit
- Readiness: 15 fps floor, save age 24 h, disk warn 60 GB / fail 10 GB, OSC probe 0.25 s.
- Logs: APP_LOG_KEEP 40 files; tracker JSONL 64 MB x 32 segments x 30 live sessions per project.
- Units are mixed: newer constants are in seconds, older ones in frames, with comments assuming 15, 20, 24, 25 or 30 fps.

## Interfaces with neighbours

- Replay harness: application/tests/replay.py:196-348 (_build_processor) is a second config-apply implementation. An absent key gets the code default there, but the previous in-memory value live. enhance_force defaults to False there and True live. It reads keys the live app never reads (auto_height, height_guard, belt_backing, entry_min_travel_h, tracker_own_height_gates, box_clamp_enabled, fg_plate_path). Tuning results depend on these matching.
- Camera: ids_exposure_auto / ids_gain_auto are not persisted. A load calls _cb_ids_exposure_change / _cb_ids_gain_change, which force manual mode with the cached values (10000 us / 0 dB if never set; camera_controller.py:70-71, 411-444). IDS_USER_SET 'UserSet1' loaded at open is a further source of truth for exposure and gain.
- Foreground: plates/latest.npz is written with np.savez_compressed directly (foreground.py:76-79), not atomically. A corrupt plate is logged to the console only, with no fallback to the newest plate_STAMP.npz, unlike the config store.
- Launcher: no relaunch, a blocking update prompt, an unbounded log textbox, and no arguments or environment passed to run.bat.
- Model controller: the TensorRT build prompt is modal in the startup path, and push_busy suppresses the watchdog for a model load with no upper bound.
- Tracker / output: frame-count constants are not rescaled by fps. HealthMonitor's no_detection counts tracker reports (app.last_tracked), not the OSC slot stream. There are two dancer caps (max_persons, max_dancers) and one alert.
- Remote API: alerts are available only by polling the event ring. /api/v1/ping could back an external supervisor.
- _apply_config_without_model publishes DialBVisible(config.get('dial_b_relevant', True)) on every partial apply (app.py:1503), so a profile switch or tune Apply re-shows Dial B regardless of the project's setting.

## Strengths

- Durable writes are done properly and tested: temp file in the same directory, fsync, os.replace with a Windows PermissionError retry (config_store.py:51-83), used for configs, last_project.txt and safe defaults; the crash marker has its own equivalent. Same-second saves take the next free second instead of overwriting (:202-213).
- Corrupt-file behaviour is deliberate: get_latest_config_in_project skips unparseable files newest-first and leaves them on disk for post-mortem (config_store.py:147-160), so a save cut by a power loss does not block auto-load.
- The ops logic is pure and injectable: readiness checks are functions returning CheckResult, HealthMonitor takes the clock and the GPU-stats function as arguments, LoopWatchdog.evaluate is a pure decision. test_ops_monitor.py covers sustain, cooldown, baseline reset and light episodes without a rig.
- Alert semantics show field thinking: the fps baseline is fed only by healthy samples so a slow decay cannot drag it down (ops_monitor.py:438-445); light_changed is one alert per episode, re-armed on a new snapshot, with an explicit AlertCleared; height_stale uses raw pre-gate heights because gated dancers never become tracks.
- Crash hygiene is layered: a per-tick exception boundary with a streak counter (transient = skip the frame, persistent = clean exit code 3), shutdown steps isolated so a failing teardown cannot skip the recording finalise (main_loop.py:402-443), a sentinel that distinguishes a Python exception from a kill or power loss, and faulthandler into the log file for native crashes.
- The stdout tee solves a real field failure (closing the launcher window used to kill the show through BrokenPipe on the next print) and never lets logging stop the app (app_log.py:36-49).
- Schema design keeps consumers simple: the app only sees flat dicts while lighting-coupled keys are stored per profile; the v1 to v2 migration seeds both profiles identically so behaviour is unchanged until the second is calibrated; validation clamps or drops with operator-visible warnings instead of failing the load.
- Several subtle persistence traps are already handled: use_tensorrt saves the intent, not the fallback state (app.py:1225-1227); the calibrated varThreshold anchor is saved separately from the live macro output (:1300-1303); D33 fills missing detection keys from one DETECTION_DEFAULTS dict and logs which ones.
- Readiness messages are honest and actionable: the OSC probe says 'listener cannot be confirmed' on a UDP timeout, the TensorRT row names the exact engine file to build, the disk row converts free GB into hours of the configured codec.
- Constants carry their evidence: most tunables in config.py cite the take, date and measured delta that justified the value, which makes later re-tuning auditable.

## Questions the reader could not answer from code

- How is the laptop actually run during a show period: started by the technician each evening, or left running for days or weeks? Is Windows auto-login, a Startup entry or power-on-after-AC-loss configured outside the repo, and is WALLDANCE_AUTOLAUNCH_LAST set on the show machine?
- Is the show laptop online at the venue? If so, a release pushed during a run blocks every relaunch on the launcher's update prompt.
- Are both lighting profiles used in production (rehearsal by day, show by night)? Who switches them, and is a Save expected afterwards?
- Are known-N or auto-tune Apply used on show projects, and is tracker_swap_correctors ever set by hand? That decides how much real tuning the dropped-keys bug has already erased.
- Are constants in config.py ever edited on the prod laptop? If so, which ones? Those are the candidates for a supported override.
- Do dancers routinely leave the wall for more than 30 seconds during the piece? What is the laptop's steady-state GPU temperature under show load, compared with the 85 C alert?
- Would the media server side accept a status or heartbeat message in the OSC contract, so it can fall back to a safe look without the technician?
- Which is the operator's 'how many dancers' knob: max_persons (tracker report cap, default 6, the one behind the over_cap alert) or max_dancers (slot cap, default 2)? Should a third dancer with max_dancers = 2 raise an alert?
- Are project folders ever copied, renamed more than once, zipped or restored between machines? That decides how exposed the mtime-based 'latest' is.
- Is FFV1 recording left on during shows, and who clears the disk? Is the safe-defaults button actually used by operators? Nothing falls back to it automatically.
