# Dimension — field-robustness

*Context-aware auditor's report, followed by an adversarial reviewer's check of its six most consequential claims. Where the two disagree, the reviewer's reading is the safer one.*

## Verdict

Low intervention is not true today. Detection is fair; recovery is almost always a person at the laptop. Of the 20 events, the app recovers alone only from the transport ones: a USB stall under 2.5 s (D34, still unreleased), a longer stall or an unplug through a blocking reopen that can lose the ids, an OSC target restart, and a stale person height. Crash, exception streak, hang and reboot all end at a launcher button, an update prompt and a project picker. A hang is neither recovered nor diagnosed: the watchdog's stack dump fails under the log tee (reproduced here). The most likely show condition, light falling from dusk to night, gets one alert; its documented remedy replaces the live stream with a looping recording and resets every id. Alerts are 8 s toasts, the local GUI has no show lock, and durations are frame counts that stretch when the GPU throttles. Five behaviours must become automatic, each bounded, logged and reversible: supervised restart, outage bridging through reconnects, a slow brightness follow at the sensor, snapshot re-base on an empty wall, and a seconds-based time base. Enhancement, thresholds, ROI and exclusions stay with the explicit Calibrate.

## Since the October audit

Shipped since the audit: the per-tick exception boundary, crash marker, faulthandler and log tee (ARCH-3, ARCH-6), atomic config history (ARCH-8), the release-channel launcher with dirty check and backup refs (ARCH-2), the dependency pin (D3), remote ops, the height guard, the empty-wall snapshot with gain normalisation, the light-change alert and the empty-wall YOLO check (D29). Built but not released: the D34 stall hold and the stale-snapshot fix (`remote-ops`, 4 commits ahead of `release` 63f4989). Still open from October: launcher auto-restart (ARCH-6 item 6), ARCH-7 sleep / Windows Update / power rows, ARCH-5 engine stamps, the stall root cause, the rig USB-pull validation (TODO.md:30). New since: D34's 5 s cap contradicts the 2.5 s reopen; 0e125cc makes a stale snapshot permanent; ROADMAP.md:68 records "crash marker (-> STANDBY)" but no code implements the STANDBY part (app.py:521 boots in RUN).

## Details

"Human" = waits for a person. Paths are under application/src unless prefixed. Read, not run, except the watchdog dump; reopen and model-load times are unmeasured.

Event | Detection | Automatic response | Recovery | Human must | Code
---|---|---|---|---|---
Power cut, reboot | "unclean exit" toast at next start | none, no autostart | human | log in, launcher, update prompt, picker | launcher/install_manager.py:23; main_loop.py:320-328
Exception streak | tick boundary, alert every 10 s | frame skipped; 50 in a row (2.5 s) exits 3; cosmetic stages count | human | Restart button, picker | main_loop.py:279-290, 462-488; launcher/gui.py:251-256
Native crash | faulthandler stack in the log | none | human | same | app_log.py:84
Main-loop hang | watchdog at 10 s, console line; stack dump fails (run here) | none, OSC silent | human | kill the process, relaunch | ops_monitor.py:594-600
USB stall < 2.5 s | stall clock, > 150 ms | D34 (unreleased): holds paused, OSC re-sent at 20 Hz | next frame | nothing | camera_stall.py:56-107; main_loop.py:744
Stall 2.5-5 s | IDS timeout at 2.5 s pre-empts the 5 s cap | stall forgotten, blocking reopen, holds run, REC callback lost | 3 s + reopen | nothing | camera_controller.py:77, 148-156; main_loop.py:691-696
Stall > 5 s | same | reopen loop, no alert while each open succeeds | when frames return | replug if it loops | camera_controller.py:98-101; ops_monitor.py:524-536
Unplug, replug | 100 errors or timeout; alert after 15 s | retries forever, backoff 1-5 s; exposure, gain, crop re-applied | 5 s after replug | replug, restart REC | main_loop.py:671-684; camera_controller.py:103-108, 382-409
Camera absent at boot | open fails | same retry; boot opens OpenCV index 0 first | 5 s after plug | plug | main_loop.py:296-301; config_manager.py:284-287
Dusk to night | light alert: 10 s median outside x1.5 | snapshot gain 0.25-4, slow update; exposure, gamma frozen | none | Calibrate: empty wall, or an empty take whose looping playback replaces live | ops_monitor.py:502-522; foreground.py:123, 247; main_loop.py:636; video_recorder.py:564
One IR projector fails | only if the ROI median leaves the band | foreground off silently above 25 % change | none | notice, repair | foreground.py:227-230, 257-261
Show light in IR band | same two tests | same; frame-diff thresholds fixed | none | remove the light | motion_detector.py:520-529
GPU throttle | 85 C for 30 s; fps alert only under half the baseline | none; frame constants stretch: age 45, warm-up 15, bridge 80, ghost 100, entry 3/12 | none | Performance mode, cooling | ops_monitor.py:426-445; config.py:175, 201, 838, 989
TensorRT missing / stale | file check / test inference | missing: modal prompt blocks; stale: silent PyTorch, banner | 2-5 min build | click Build or Rebuild | model_controller.py:228-259; model_manager.py:251-267
Disk full in REC | none at runtime | none; write errors printed | none | stop REC, free space | video_recorder.py:163-168; app.py:2291
Windows Update, sleep, lid | none | none (no execution-state call) | as reboot or replug | Windows settings | ARCH-7
OSC target down, TD restart | OSError only | datagram dropped, alert every 10 s | when TD returns | nothing | osc_output.py:50-72
Wrong key or control | none, no show lock | E flips enhancement silently; slot click starts playback; Calibrate overwrites the snapshot | manual | undo by hand | ui/adapter.py:278-293; app.py:1890-1896
Stale snapshot / height | height guard; snapshot: crop or > 25 % | height adopted in memory; snapshot off silently | height 5-15 s | Calibrate after each re-rig | pipeline.py:1385-1451; foreground.py:238-261
Update prompt offline, bad release | fetch error reads "no updates" | installed version starts; stalled link blocks, no timeout | none | bad release: developer over SSH | launcher/git_manager.py:114, 126-127, 176; launcher/gui.py:410

Must be automatic (bound; log; undo):
- Restart: 3 per 10 min; crash marker; none needed.
- Outage bridge: 5 s; `[CameraStall]`; key `camera_stall_hold`.
- Brightness follow: +/-12 dB, 1 dB per 5 s; every step; calibrated anchor kept, freeze toggle.
- Snapshot re-base: empty wall only; one file per re-base; restore action.
- Time base: 8-25 fps; each change; project key.

## Findings

### 1. Nothing restarts or unblocks the app: crash, hang and reboot each wait for a person at up to five points

severity high · confidence high · status known open · effort M · prior id: ARCH-6 (item 6), D13

**Evidence.** launcher/gui.py:245-256: run.bat runs once; a non-zero exit only enables a Restart button. main_loop.py:320-328: modal picker unless WALLDANCE_AUTOLAUNCH_LAST or --project, which nothing sets (repo grep). model_controller.py:228-259: a missing engine blocks on a modal prompt. ops_monitor.py:594-600: the watchdog prints one line; its faulthandler dump fails under app_log.py:29 `_Tee` (run here: "UnsupportedOperation fileno", 0 bytes written). app.py:521 boots in RUN; no code implements D13's STANDBY (grep). No autostart, no SetThreadExecutionState (repo grep).

**Recommendation.** Launcher show mode: relaunch on a non-zero exit with WALLDANCE_AUTOLAUNCH_LAST=1 and no update check, at most 3 times in 10 minutes. App: `faulthandler.dump_traceback_later(hang_s, exit=True, file=<log handle>)`, re-armed each tick, cancelled in push_busy. No modal on a resume (PyTorch plus banner). Logon task with battery flags; SetThreadExecutionState.

**Risk.** A restart loop on a persistent fault (hence the cap). Resuming into RUN contradicts D13 and needs the owner's word.

### 2. No way to follow a light change with dancers on the wall; the documented remedy takes the live stream down

severity high · confidence high · status new · effort M

**Evidence.** ops_monitor.py:233-234, 502-522: one `light_changed` alert; the remedy is Calibrate on the empty wall, live or on an empty take. main_loop.py:636-659: a playing take replaces the camera; recording_controller.py:187-191 resets tracker and slots; video_recorder.py:564 loops; Calibrate never returns to live (grep calibration_flows.py). TERRAIN_DEMO_FR.md:84 says "le spectacle continue". camera_controller.py:411-444: exposure and gain are manual; nothing re-drives them in RUN. Only foreground.py:123, 247-251 follows light, unlogged. PLAN §C.12: the scene fell ~3x at 21:33.

**Recommendation.** Run the existing ExposureServo (core/calibration.py:229-361, ROI median) as a slow follower in RUN: 1 dB per 5 s, +/-12 dB around the calibrated anchor, every step logged, anchor never overwritten, a freeze toggle, a persistent "add IR" alert at the bound. First step: a manual "Relight" button (servo only, no snapshot, no wall check).

**Risk.** Sensor noise rises with gain, so the snapshot threshold must scale with it. A camera write in RUN may provoke a stall: test with extra/ids_stall_ab.py first. No footage with a real light ramp exists to prove it.

### 3. The 2.5 s reopen undercuts the 5 s stall bridge; the reopen blocks the loop, drops the recording and never alerts when it loops

severity high · confidence medium · status new · effort M · prior id: D34 (PLAN §C.16)

**Evidence.** camera_controller.py:77, 148-156: an IDS stream silent for 2.5 s is closed; main_loop.py:691-696 forgets the stall first, so camera_stall.py:62's 5 s cap is unreachable and the holds run through the outage. The reopen runs on the main loop (main_loop.py:576-583). camera_controller.py:98-101 clears the down state at each successful open, so ops_monitor.py:524-536 never fires on a reopen loop. ids_camera.py:373, 1979: a new IDSCamera has no recording callback. tests/test_camera_stall.py:346 stubs the timeout; TODO.md:30: USB-pull validation pending. Reopen time not measured.

**Recommendation.** One constant: reopen only after CAMERA_STALL_MAX_S. Treat a reconnect inside a bound as a stall: keep `last_frame`, keep the keep-alive between reopen steps, credit the silence on the first frame back. Alert on 2 reopens in 60 s. Re-wire the recording callback in `_open_ids`. Add a fake-clock reconnect test, then release D34.

**Risk.** If stalls over 2.5 s never recover unaided, waiting 5 s only delays the reopen: the laptop's stall log must decide. The reconnect path has never been validated on the rig.

### 4. Alerts are 8 s toasts; no persistent health state, no absolute fps or disk check, and readiness never runs after a restart

severity medium · confidence high · status new · effort S

**Evidence.** ui/adapter.py:33-34, 345-348: every alert is an 8 s toast; only `light_changed` stays in the strip. ops_monitor.py:392-397: a kind re-fires every 120 s. ops_monitor.py:426-445: the fps alert is relative to the session's own baseline; OPS_MIN_SHOW_FPS serves the readiness row only (app.py:2252). Readiness runs on STANDBY->RUN (app.py:2229-2232) but the app boots in RUN (app.py:521). Free disk is read only there (app.py:2291-2294); FFV1 writes ~55 GB/h (config.py:777-792). Nothing is sent to TouchDesigner.

**Recommendation.** Keep every active condition in the strip until it clears (AlertCleared exists), with one worst-state chip. Add to the 1 Hz tick: an absolute fps floor, free disk (stop REC under the fail floor), snapshot validity, reopen count. Run readiness once after the first project load. Offer an opt-in `/walldance/status` heartbeat.

**Risk.** More persistent alerts can teach the operator to ignore them: `no_detection` fires on a legitimately empty stage and should be gated first.

### 5. No show lock: one click in RUN replaces the live stream with a looping take, and Calibrate overwrites the snapshot before Save

severity medium · confidence high · status new · effort S · prior id: ARCH-17 (covers typing only)

**Evidence.** No RUN check in recording_controller.py or calibration_flows.py (grep `system_state`: none); only import, dry-run, auto-tune and known-N have one (app.py:993, 2343, 2442, 2572). A slot click starts playback (recording_controller.py:488-518), which loops (video_recorder.py:564). Calibrate in RUN rewrites exposure and gain (calibration_flows.py:351-358), lowers confidence (:496-497) and writes plates/latest.npz before Save (app.py:1890-1896). ui/adapter.py:278-293: E flips enhancement with no toast. Quit exits 0, so the launcher closes too (launcher/gui.py:248-250).

**Recommendation.** Add a show lock, set by a toggle in 3 Live and by the launcher's show mode: source switch, Calibrate, project, model, image size, ROI, crop and single-key shortcuts are refused locally, as remote_api.py:140-144 already refuses them remotely. Unlock by holding a button 2 s. Quit in RUN asks for confirmation.

**Risk.** A lock the operator cannot open under stress: keep one obvious unlock, and leave Hold and Stability adjustable.

### 6. Durations are frame counts with no time base: throttling or a PyTorch fallback stretches every tracker window, silently

severity medium · confidence medium · status new · effort M

**Evidence.** core/tracker.py never reads fps (grep: 0 hits). config.py:175 TRACKER_MAX_AGE 45 ("~3 sec at 15 FPS"), :181 dormant 150, :201 ghost age 100, :838 bridge 80, :989 warm-up 15; identity_slots.py:249-254: entry 3 reports, 12 hits, a skeleton within 60 frames; foreground.py:133: snapshot update every 10 frames. A stale engine falls back to PyTorch silently (model_manager.py:251-267), "3-7x slower" (ops_monitor.py:138). GPU alert at 85 C (config.py:1121); the KPI is 83 C. The effect on continuity is not measured.

**Recommendation.** Declare these durations in seconds and convert them once a second from the measured loop fps, clamped to 8-25 fps, logging each change, behind a project key; at 20 fps the values are today's. Add the absolute fps alert. Measure first: replay a duo take at 10 and 14 fps against the flow gates.

**Risk.** Tracker goldens are byte-compared: takes recorded at other rates change and need re-baselining on the rig.

### 7. The empty-wall snapshot switches off silently, never heals once stale, and a re-rig below 25 % change is not detected

severity medium · confidence high · status new · effort S · prior id: PLAN §C.15 (records the stays-stale behaviour)

**Evidence.** foreground.py:238-240, 257-261: another crop, or more than 25 % of the ROI differing, invalidates the frame; `support` then returns 1.0 (:162-163), so the ghost veto and the long foreground hold vanish. The status text is published only on load, toggle and capture (app.py:1857-1909). Since 0e125cc a stale snapshot stays stale (PLAN §C.15). `stale_frames`, `gain` and `fg_ratio` are read nowhere outside foreground.py (grep). latest.npz is written without the atomic helper (foreground.py:76-79). One snapshot serves both lighting profiles (app.py:1860-1870).

**Recommendation.** Sample snapshot validity at 1 Hz; show a persistent state when under 80 % over 10 s. When stale for 20 s and no slot has emitted for 20 s, re-base automatically into plates/auto_<stamp>.npz, logged, leaving latest.npz as calibrated, with a "restore calibrated snapshot" action. Log gain and fg_ratio in FRAME_SUMMARY.

**Risk.** A still dancer YOLO cannot see, with no slot, would be absorbed by a re-base: also require no dancer-sized foreground blob and no frame-diff motion.

### 8. The launcher gates every start on the network, prompts on every start, and a bad release has no way back on site

severity medium · confidence medium · status new · effort M

**Evidence.** launcher/git_manager.py:114: `porcelain.fetch` on the start path, no timeout; dulwich 1.2.17 (launcher/requirements-win.lock:9) sets one only when configured (client.py:4387-4394, 4437-4439). launcher/gui.py:145 disables the button meanwhile; :410 asks a bare Yes/No on every start, crash restarts included. git_manager.py:126-127 with gui.py:223-224: moving `release` back is ignored; the backup refs (:176-177) have no reader. :237-240: the reinstall trigger omits requirements-prod.txt. extra/wdslot.py:384-391: the launcher task keeps Windows default settings (stop on battery, 72 h; not checked on the laptop).

**Recommendation.** Start the installed version at once; fetch in the background with an 8 s deadline and offer the update at the next start. Add a `hold` flag in launcher.json for show periods. Add "Start previous version" from the backup refs. Stamp the venv with a pin-file hash. Register both tasks with battery flags, no time limit.

**Risk.** The exe does not update itself: each launcher change needs `wdremote launcher build` and `install` on the laptop. The stalled-fetch case was read from dulwich's source, not reproduced on Windows.

## Paradigm-level change proposed

Treat the OSC stream as a supervised service and the light as a controlled variable, instead of an app a technician operates and a calibration snapshot that ages.

1. A show mode owned by the launcher. It starts the last project without a prompt and relaunches on any non-zero exit (at most 3 times in 10 minutes). The app exits by itself on a hang (`faulthandler.dump_traceback_later(exit=True)`). In show mode the GUI is locked and every modal has a default.

2. A light follower. The existing ExposureServo (ROI median; exposure up to the blur budget, then gain) runs slowly in RUN, so the raw image stays at the calibrated brightness and gamma, CLAHE, MOG2 and the frame-diff thresholds stay in the regime Calibrate measured. Bounds: 1 dB per 5 s, +/-12 dB around the calibrated anchor (the snapshot's own 0.25-4 range). Each step goes to the log, an OPS event and the camlog. The anchor is never overwritten, Save stores the anchor, a toggle freezes the follower. At the bound: a persistent "add IR" alert, the physical fix the owner prefers.

Cost: about two weeks, plus a night take with a real light ramp to prove the follower. A camera write during RUN must first be shown not to provoke stalls (`extra/ids_stall_ab.py`).

Cheapest step: the launcher sets WALLDANCE_AUTOLAUNCH_LAST and relaunches (about a day), and a manual "Relight" button runs the servo alone with dancers on the wall: explicit, logged, re-runnable, no empty wall needed.

## Questions for the owner

- During a show, is a technician always within reach of the laptop, or must it recover alone (multi-day installation)? Are Windows auto-login, a Startup entry, sleep and lid settings and an Update pause set on the laptop? None of them is in the repo.
- D13 says a crash restarts into STANDBY; the code boots in RUN and nothing restarts it. Once auto-restart exists, which do you want after a crash in RUN: back to RUN alone, or STANDBY until someone re-arms?
- What does the TouchDesigner patch do when OSC goes silent (hang, crash, reconnect), as opposed to receiving count 0: hold the video or drop it? Would it accept a status heartbeat?
- Does a show run through dusk into night, as on 2026-10-06 (about 3x darker)? Would you accept a slow, logged exposure/gain follow bounded to +/-12 dB around the calibrated value, with a freeze switch?
- Does any show light, or the video projector itself, emit in the 850 nm band on the wall?
- Has a stall longer than 2.5 s ever recovered by itself on the laptop, and how long does an IDS reopen take there (log lines "IDS stream stalled, reconnecting silently" to the next "Opened")?
- What does the "WallDance DEV" desktop icon start: run.bat directly, the scheduled task, or the launcher? It decides which battery and 3-day limits apply.
- Is REC left on during shows (FFV1, about 55 GB/h on the disk TouchDesigner shares)?

## Adversarial review

The report's code reading is mostly accurate: every mechanism I opened is there, and the watchdog dump failure reproduces. Its conclusions run ahead of the evidence. One claim holds, five are weakened, none is refuted.

- **Holds:** nothing restarts the app, and a hang leaves no stack. This is the one finding to act on as written, within D13.
- **Light:** the case for a follower rests on a single 3x step between two takes four minutes apart, in a hangar. The repo's own replays show that mismatch cost about nothing on solo takes. The real defect is the remedy text, which sends the operator to a looping take.
- **Stall bridge:** the 2.5 s boundary is written into PLAN section C.16 and the OSC contract; it is not a new contradiction. Typical stalls (1.7 s) sit under it.
- **Time base:** frame counts are a tracker property. The slot holds that decide OSC presence are in seconds.
- **Rethink:** the show-mode half is sound if it respects D13. The continuous exposure follower reopens the owner's explicit-calibration choice and is not a reuse of the existing servo.

Not checked: reopen and model-load times on the laptop, the dulwich fetch timeout, and the remote-tool interlocks. The repo is unchanged (git status clean); my one Python check wrote only under $TMPDIR.

### Claims checked

- **weakened** — VERDICT: low intervention is not true today; the app recovers alone only from transport events; crash, exception streak, hang and reboot all end at a launcher button, an update prompt and a project picker; durations are frame counts; five behaviours must become automatic.  
  The core thesis stands (a person is needed after a crash, hang or reboot). The list of five "must be automatic" behaviours does not: one contradicts D13 and three rest on effects nobody measured.  
  Evidence: Holds: a non-zero exit only enables a Restart button (launcher/gui.py:248-256); the picker shows unless an env var or CLI flag is set (application/src/runtime/main_loop.py:320-328); a previous crash gives a 15 s toast only (main_loop.py:490-493); app and GUI boot in RUN (application/src/app.py:521, application/src/gui.py:316). Not supported: (a) the update prompt is reached only when the status is BEHIND (launcher/gui.py:178, 191-193), not on every restart. (b) A stall under 2.5 s is not clean: D34 is unreleased (origin/release..remote-ops = 4 commits), and docs/PLAN_25M_2026-10.md:677-678 and :569-577 record the track lost after a stall (Kalman runaway) and a second point for 6.8 s. (c) Frame counts are true of the tracker (application/src/core/config.py:175, 181, 201, 838, 989), but the slot holds that decide OSC presence are in seconds (application/src/core/identity_slots.py:239, 314-319, 325, 349, 361). (d) "Dusk to night": the bright take s3 is stamped 21:28:55 and the dark take s4 21:33:10 (tmp_analysis/flowcheck/report.py:24-26), a step in under 5 minutes, in a hangar (PLAN_25M:311); about 2 h after sunset by my own calendar estimate, not from the repo. (e) D13 (docs/AUDIT_2026-10.md:295, docs/ROADMAP.md:235): the owner chose STANDBY plus an operator re-arm after a crash.
- **weakened** — RETHINK: treat OSC as a supervised service (launcher show mode: autolaunch, relaunch 3 times in 10 min, hang exit, GUI lock) and the light as a controlled variable (the existing ExposureServo as a slow follower in RUN, 1 dB per 5 s, +/-12 dB); about two weeks; cheapest step = autolaunch plus a manual Relight button.  
  Keep the cheap half: autolaunch and relaunch (into STANDBY unless the owner says RUN), and an explicit Relight. The continuous follower reopens a settled choice on one unexplained light step.  
  Evidence: Show mode matches ARCH-6 item 6 and ARCH-7 (docs/audit-2026-10/03-architecture.md:427-428), but a relaunch into RUN contradicts D13 (AUDIT_2026-10.md:295); the report says so. Follower: the existing servo drives to an absolute ROI median of 70 +/- 12 in 3 dB gain steps and 1.3-2.5x exposure steps, then stops (application/src/core/calibration.py:310-345; config.py:640-652). It has no anchor-relative target and no 1 dB step, so this is a new control law, not a reuse. Calibration is tied to a fixed exposure and gain: Calibrate on a take first restores the take's values "or the enhancement and the plate picked on it do not match live" (application/src/runtime/calibration_flows.py:268-273), and the snapshot threshold uses the noise measured at capture (application/src/core/foreground.py:252). The repo already advises against sensor auto brightness: "decocher auto-exposure et auto-gain" (AUDIT_2026-10.md:151-152), "auto-exposure drifts within takes" (docs/REMOTE_OPS.md:146-147). CONTEXT section 3: explicit calibration, not continuous auto-tuning; physical fixes preferred. The two-week estimate has no basis I could check.
- **holds** — FINDING 1 (high, known_open): nothing restarts or unblocks the app; crash, hang and reboot each wait for a person at up to five points; the watchdog's stack dump fails under the log tee; no code implements D13's STANDBY.  
  Correctly tagged known_open (ARCH-6 item 6); the dump failure is new and confirmed. Unlisted risk: dump_traceback_later(exit=True) skips the finally at main_loop.py:274-276, so a recording is not finalised and no crash marker is written.  
  Evidence: run.bat runs once and a non-zero exit only enables Restart (launcher/gui.py:245-256). Picker gate at main_loop.py:320-328; WALLDANCE_AUTOLAUNCH_LAST is read there and set nowhere (grep of *.py, *.bat, *.sh, *.json). The missing-engine prompt spins until a click (application/src/runtime/model_controller.py:228-259). Watchdog: application/src/core/ops_monitor.py:594-600 calls faulthandler.dump_traceback(file=sys.stderr), and sys.stderr is the _Tee from application/src/services/app_log.py:29, 81-82. Reproduced here: UnsupportedOperation('fileno'), swallowed; the log gained only the "[Alert] Main loop hang" line and no stack. No STANDBY on restart: app.py:521, gui.py:316, and no SetState on any startup path (grep). No SetThreadExecutionState (grep).
- **weakened** — FINDING 2 (high, new): no way to follow a light change with dancers on the wall; the documented remedy takes the live stream down; fix = ExposureServo follower in RUN.  
  The harmful remedy text (alert and French note) is the real defect and a small fix. The repo's own night replays do not support a "high" need for a follower.  
  Evidence: Holds: the remedy text says "play this lighting's empty take and Calibrate on it" (ops_monitor.py:233-234); a playing take replaces the camera (main_loop.py:636-659); playback start resets the tracker (application/src/runtime/recording_controller.py:187-191, app.py:2013-2016); playback loops (application/src/core/video_recorder.py:562-570); calibration_flows.py has no go_live call (grep); docs/TERRAIN_DEMO_FR.md:84 says "le spectacle continue". Against the severity: the snapshot follows brightness by itself, gain 0.25-4x plus a slow update (foreground.py:120-134, 246-252). The repo measured the cost of the 3x mismatch: the bright calibration on the dark takes gives coverage 0.976 / 0.916 / 0.903 / 0.822, against 0.975 / 0.901 / 0.903 / 0.858 with the dark calibration (PLAN_25M_2026-10.md:585-593; labels in tmp_analysis/flowcheck/night_flow.sh:3-4). Solo takes only.
- **weakened** — FINDING 3 (high, new): the 2.5 s reopen undercuts the 5 s stall bridge; the reopen blocks the loop, drops the recording and never alerts when it loops; D34's 5 s cap contradicts the 2.5 s reopen.  
  The facts are right; "new", "contradicts" and "high" are not. Exposure is the rare stall over 2.5 s. The silent reopen loop and the lost REC callback are the parts worth fixing.  
  Evidence: Mechanics hold. The timeout is 2.5 s (application/src/runtime/camera_controller.py:77, 148-156); the loop forgets the stall, then closes (main_loop.py:691-696; application/src/core/camera_stall.py:75-81); the reopen is synchronous in the pump (main_loop.py:576-583); a successful open clears the reconnecting flag (camera_controller.py:98-101, 139-141), so the 15 s camera_down clock resets each cycle (ops_monitor.py:525-536); a new IDSCamera has no frame callback (application/src/camera/ids_camera.py:373, 1977-1978, 2148-2151); the test stubs the timeout (application/tests/test_camera_stall.py:346). But the boundary is the documented contract, not a new contradiction: PLAN_25M_2026-10.md:647 and docs/OSC_CONTRACT.md:336-337 both say "the app reopens an IDS stream silent for 2.5 s ... a camera loss, as before". Observed stalls are 1.65-1.7 s (ROADMAP.md:108), under the timeout. Reopen duration: not checked (needs the laptop).
- **weakened** — FINDING 6 (medium): durations are frame counts with no time base; throttling or a PyTorch fallback stretches every tracker window silently; fix = declare in seconds and convert from the measured fps (one of the five must-be-automatic behaviours).  
  A longer max_age at low fps favours presence (D5); only warm-up and entry get slower. This is a "measure first" item, and it collides with the byte-compared goldens and the keep-as-is tracker decision.  
  Evidence: Holds for the tracker: application/src/core/tracker.py never mentions fps (grep, 0 hits); config.py:175 (45), :181 (150), :201 (100), :838 (80), :989 (15); slot entry gates in frames (identity_slots.py:249-254); snapshot update every 10 frames (foreground.py:133). Not for the output layer: coast_s, hold_min_s, hold_rep_s, edge_hold_s, weak_max_s, hidden_max_s, belt_max_s and fg_max_s are seconds (identity_slots.py:239, 267, 314-319, 325, 349, 361). No fps collapse is on record: the 4 h soak reads mean 19.13, min 18.33 fps (docs/TODO.md:34). The report itself says the effect on continuity is not measured.

### Missed or out of proportion

- The most frequent field event gets the least weight. Stalls run 0.7-2.9 per minute, and only at the landscape crops: 0 in 28 min at 1488x1528 (docs/ROADMAP.md:108). The cheapest gains are releasing D34 (4 commits) and the N-15 test (direct USB 3 port, crop). The event table says a short stall needs "nothing", while PLAN_25M_2026-10.md:569-577 and :677-678 record the track lost after a stall and a second point for 6.8 s (known open: Kalman treats the stall as one frame).
- Restart is not resume. Config is saved only on an explicit Save (app.py:941; calibration_flows.py:557, 820); the height guard's adopted height lives in memory (application/src/core/pipeline.py:1446-1451); plates/latest.npz is written at Calibrate, before Save (app.py:1893-1894). An automatic relaunch can therefore pair a new snapshot with old exposure and enhancement, and a stale height. The supervised-restart proposal does not address this.
- "0e125cc makes a stale snapshot permanent" misreads PLAN section C.15. Before the fix the update fed itself and the snapshot stayed stale to the end (1043 of 1065 frames); with the fix the foreground returns when the scene returns (from frame 319; stale frames 1043 -> 384) (PLAN_25M_2026-10.md:606-616). Only a scene that really changed stays stale, which the doc records as deliberate. The fix is also unreleased, so the laptop still runs the self-feeding update.
- The GPU temperature alert sits inside the laptop's normal range: 85 C for 30 s, re-fired every 120 s (config.py:1121-1124), against 84-87 C observed (CONTEXT section 1). It would ring through a normal show. Making alerts persistent, as finding 4 recommends, would pin a permanent red state unless this threshold is settled first.
- No recovery path has been exercised on the rig: USB-pull validation is pending (docs/TODO.md:30), the stall test stubs the timeout (test_camera_stall.py:346), and D34 is unverified on night takes (CONTEXT section 4). The report adds five automatic behaviours on top of untested ones. A fault-injection pass on the DEV slot (pull USB, kill the process, stale engine, full disk) should come first.
