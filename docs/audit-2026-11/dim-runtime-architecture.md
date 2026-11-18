# Dimension — runtime-architecture

*Context-aware auditor's report, followed by an adversarial reviewer's check of its six most consequential claims. Where the two disagree, the reviewer's reading is the safer one.*

## Verdict

For a show system, "one process, one loop, GUI in the loop" fits rigging and calibrating and is wrong for the show phase. The product is a continuous stream, yet it runs on the thread that renders DearPyGui, executes every operator and remote command and waits on modal prompts, and nothing outside the process watches it. Verified in code: no watchdog acts on a hang; nothing relaunches after exit 3, a native crash or a reboot; a restart returns a different state from the one running (no empty-wall plate, RUN although D13 says STANDBY). October's hygiene work is real, but it ends at "exit cleanly", which without a supervisor means a dark wall until a human arrives.

Recommendation: option (b), built as the first half of (a). About one week: supervisor with relaunch into the last project, hang-to-exit, per-stage failure policy, restart-state fixes. Then an engine thread, so the GUI only views. The two-process split (a) is the clean end state, but it breaks the preview texture path, the ROI editor and the synchronous prompts: 3 to 5 weeks. Start it only if a remote UI is wanted or a DearPyGui native crash is seen in the field.

## Since the October audit

October audited a49d0f2; `remote-ops` is about 140 commits later. Shipped and verified in code: `finally` shutdown with isolated steps, frame and tick boundaries, OSC send guard, crash marker and running sentinel, persistent log with faulthandler, atomic config with newest-valid fallback, CI with a Windows job, release channel and launcher hardening (in source), pinned install. Not shipped: launcher auto-restart and STANDBY re-entry (both in ARCH-6; the second is recorded as shipped), ARCH-5, 7, 10, 11, 14-17. New surface: the remote API is a second client of the seam and adds remote command handlers on the show thread (commands 71 to 94, events 44 to 47; main_loop.py 1171 to 1568 lines, app.py 2211 to 2722). Identity slots and D34 put wall-clock hold timers on the output, so a 2 s main-thread block can now cost an id.

## Details

Paths: `application/src/` + `runtime/` (main_loop, config_manager, model_controller, camera_controller), `core/` (pipeline, ops_monitor, identity_slots, camera_stall, config, config_schema), `services/` (app_log, remote_api), `ui/adapter.py`, `app.py`, `gui.py`; `launcher/` and `tmp_analysis/` are under ``.

### 1. ARCH-1..19, verified at d2ecfde

| ID | Status | Code evidence |
|---|---|---|
| 1 | process, not verifiable | `origin/main` still a49d0f2; `origin/release` exists |
| 2 | partly | backup ref, move-aside, deletions (`launcher/git_manager.py:176-235`); BEHIND prompt still bare `askyesno` (`launcher/gui.py:410`); app output still a pipe (`process_runner.py:26-37`); tracked exe is March's |
| 3 | shipped (app side) | `_Tee` drops a dead console (`app_log.py:44-48`) |
| 4 | partly | pinned `uv pip sync` (`install.bat:72,151`); pyproject still CPU index; reinstall trigger ignores the pin (`git_manager.py:237-240`) |
| 5 | open | no stamp in `model_manager.py` |
| 6 | partly | finally, boundaries, OSC guard, faulthandler, marker; no auto-restart, no STANDBY re-entry (`app.py:521`), no clear on exit |
| 7 | open | no `SetThreadExecutionState`; no AC / power-plan / reboot rows |
| 8 | partly | atomic, newest-valid; no newer-than-app guard (`config_schema.py:207`); switch not transactional |
| 9 | partly | ubuntu + windows jobs, run after the push; green not checked |
| 10, 11, 14-17 | open | replay keeps its applier; no `Measurement` type; jobs at `app.py:2348,2447,2577`; 15 deferred by decision |
| 12, 13 | partly | `identity_slots.py`, emit still `pipeline.py:1057`; `tests/golden/emitted` |
| 18 | partly | README still says "CPU fallback" |
| 19 | shipped in source | `DEFAULT_BRANCH="release"`; installed exe not verifiable |

### 2. What delays or stops OSC (sent in `_tick_process`, `pipeline.py:1054`, main thread)

- **Until a human acts:** STANDBY (remote `SetState` too); window X (exit 0, launcher closes, `launcher/gui.py:248-250`); TRT prompt, unbounded (`model_controller.py:250,360`); a raising project switch (finding 6); playback in RUN (the take is emitted); hang; exit 3.
- **Seconds:** project switch and model reload from the project/config combos, imgsz, TRT toggle or rebuild, safe defaults, all ungated locally (`config_manager.py:407-408`, `app.py:1926-1949`); camera refresh, select and reconnect inline (`camera_controller.py:178-219`, `main_loop.py:576-583`); the 2.5 s IDS teardown under the 5 s D34 cap (`camera_controller.py:77`).
- **Sub-second:** readiness, 0.25 s per "2 Calibrate" click; issue report; save; `dpg_render` 213-238 ms, three times in one laptop evening (`tmp_analysis/field/night-0610/walldance_20261006_201756.log:623,947,2619`). Title-bar drag: inferred from the Win32 modal loop, not tested.
- **Offline tools:** thread + subprocess, STANDBY-gated at start only; they compete, they do not block.
- **Watchdogs that act:** camera reopen, exit-3 streak, TRT-mismatch reload, D34 keep-alive (only while the loop polls). None restarts the process or ends a hang.

### 3. Supervision

- **(a) streak:** 50 failures, marker, shutdown, exit 3. Launcher shows Restart (wait 1), fetches, asks to update if `release` moved (wait 2), picker (wait 3), TRT prompt if no engine (wait 4). Then RUN, without readiness and without the plate.
- **(b) native crash:** no shutdown (take not finalised), sentinel only; then as (a).
- **(c) hang:** one log line after 10 s; process stays alive, so no Restart button; a human must kill it.
- **(d) reboot:** nothing autostarts (`launcher/install_manager.py:23-38`); login and shortcut, then as (a).

### 4. State

- **Held twice:** RUN/STANDBY (`gui.py:316`, `app.py:521`), camera open (legacy mirror), `osc_enabled`, project identity (set after the apply). Active profile and live tweaks are memory-only.
- **Outside the queue:** ROI/mask handlers on the DPG thread; worker-thread publishes into DPG (`app.py:2310`); the pipeline writes `person_height_px` (`pipeline.py:1450`); `main_loop.py` reads 40 controller privates.


## Findings

### 1. Nothing restarts the app: exit 3, a crash, a reboot or an accidental close wait for a human at up to four points

severity high · confidence high · status known open · effort M · prior id: ARCH-6 (6)

**Evidence.** launcher/gui.py:245-256: run.bat runs once; a non-zero exit only enables 'Restart', exit 0 closes the launcher. Restart repeats the network fetch and the Yes/No update prompt (launcher/gui.py:174,193). main_loop.py:320-328 shows the picker unless --project or WALLDANCE_AUTOLAUNCH_LAST is set; grep finds nothing that sets either, and no logon autostart (install_manager.py:23-38 makes a desktop shortcut only). model_controller.py:250-252: the TRT prompt has no timeout. Recovery time was not measured.

**Recommendation.** Add a supervised show mode to the launcher: relaunch on non-zero exit with backoff (3 in 10 min), `run.bat --project <last>`, no fetch or update prompt on a relaunch, a 20 s default on the TRT prompt, a single-instance lock, a logon task. Ask the owner about D13 first.

**Risk.** Low: launcher and startup path only. Each exit path (0, 2, 3, kill) needs one test on Windows.

### 2. A hang is never recovered and leaves no diagnosis: the watchdog only prints, and its stack dump fails under the log tee

severity high · confidence high · status new · effort S · prior id: 03-architecture §1.3 (diagnostic only)

**Evidence.** ops_monitor.py:594-600 calls faulthandler.dump_traceback(file=sys.stderr) inside `except Exception: pass`; sys.stderr is _Tee(io.TextIOBase) with no fileno (app_log.py:29-82). Reproduced on dev37: UnsupportedOperation: fileno, so no stacks are written. The hang line is printed, never published. The watchdog starts after startup (main_loop.py:261) and is muted without limit inside busy brackets (config_manager.py:145, model_controller.py:435). A hung process never exits, so the launcher never offers Restart.

**Recommendation.** Arm `faulthandler.dump_traceback_later(15, exit=True, file=<log file handle>)` on every beat; inside busy brackets re-arm it with a ceiling taken from measured TRT build times. It runs without the GIL, writes the stacks and ends the process, which hands recovery to the supervisor. Test it with the tee installed.

**Risk.** Low. A legitimate block longer than its ceiling would be killed; the forced exit skips shutdown, so a running take is not finalised.

### 3. The OSC stream shares a thread with DearPyGui and every command handler; gaps the loop makes are silent and stay on the hold clocks

severity high · confidence high · status new · effort M · prior id: GUI_STACK_AUDIT option 2

**Evidence.** main_loop.py:509-528 is one serial chain; OSC leaves in _tick_process (pipeline.py:1054). camera_stall.py:11-13,60: a gap the loop made gets no keep-alive and stays on the hold clocks, so past coast_s (2 s) a slot not re-measured on the first frame is lost (identity_slots.py:1061). Laptop log 2026-10-06: dpg_render 238, 213 and 217 ms (lines 623, 947, 2619), the first beside a preview resize. Readiness blocks 0.25 s on every '2 Calibrate' click (gui.py:537-541, app.py:2314-2335). Title-bar drag: inferred, not tested.

**Recommendation.** Now: run the readiness report on its existing worker, put timeouts on the prompts. Next: an engine thread for acquire, process, OSC and the command drain, with DearPyGui on the main thread reading latest-only preview and stats. First check on the laptop that a thread keeps running while the title bar is held.

**Risk.** Medium: settings and events cross a thread boundary; D34 and slot timing must be re-verified on replays. Whether DearPyGui releases the GIL during render was not checked.

### 4. A restart does not restore the show: empty-wall plate looked up under the previous project, RUN instead of the decided STANDBY, no readiness check

severity high · confidence high · status new · effort S · prior id: D13

**Evidence.** config_manager.py:268 applies the config and only :271 sets _current_project. _load_project_plate (app.py:1860-1870, sole caller :1498) builds its path from _current_project (app.py:1839-1841), still 'default' on a cold start (config_manager.py:102): the plate is cleared, so ghost veto and foreground hold are off and cannot be recaptured with dancers on the wall. app.py:521 boots in RUN and the marker only toasts (main_loop.py:490-493), while ROADMAP.md:68,235 record 'restart into STANDBY' as shipped. Read, not run; no test covers it.

**Recommendation.** Set `_current_project` and the recorder project before `apply_config`, and test that the plate path sits under the new project. Write a small `session.json` (active profile, state) on change. Decide the post-crash state and implement it. Run the readiness report after the first project load.

**Risk.** Low. The reorder touches every project load; one config_manager test covers it.

### 5. A persistent failure in any stage ends the session, including cosmetic stages that run after OSC was sent; the streak is counted in ticks

severity medium · confidence high · status new · effort S · prior id: ARCH-6 (3)

**Evidence.** main_loop.py:279-290: any exception in _tick outside processing increments _tick_fail_streak and skips the rest of the tick, render included; :483-488 exits at 50 (config.py:1133). Preview, events, record and render run after the OSC send (:522-528), so a drawing or stats bug stops a healthy stream after about 2.5 s. A stage before acquire has no wait between failures and would reach 50 in milliseconds. tests/test_main_loop_guards.py:259 pins the behaviour. No trigger was reproduced.

**Recommendation.** Give each stage a policy: a failing preview, events, record or render stage is disabled with a persistent alert and never exits; always attempt a bare render; count streaks in seconds. Keep the exit for acquire and process only, and only once a supervisor relaunches.

**Risk.** Low: no pipeline change, goldens unaffected. The pinned test must be rewritten.

### 6. The project switch is not a transaction: an exception leaves the loop idling under a RUN badge with a wrong alert, and state is committed before the model load

severity medium · confidence medium · status new · effort M · prior id: ARCH-8 (5)

**Evidence.** config_manager.py:172 sets _model_loading; it is cleared only at :192, :260 and :320, and the one finally (:146-149) pops the watchdog. Path, profiles, TRT intent and imgsz are replaced at :160, :197-198, :208, :215-216, before the model load at :258. main_loop.py:558-562 clears the pending switch first, so after a raise every tick idles at :594-597 and the failure streak resets. ops_monitor.py:525-534 then reports 'Camera down'. _cb_profile_switch sets _active_profile before applying (:359-365). No failing input was constructed.

**Recommendation.** Parse and validate into locals, load the model, then commit path, profiles, imgsz, project and plate together. A `try/finally` clears the latch and reopens the camera; on failure, toast the reason and keep the previous project running. Add an alert for 'RUN but pipeline idle for N s'.

**Risk.** Medium: every project load goes through it, and config_manager had no test coverage in October. Write the test first.

### 7. Show protection lives in the remote API, not in the command seam: the local GUI can stop the stream with one click, and policy is checked at submit, not at drain

severity medium · confidence high · status new · effort M

**Evidence.** services/remote_api.py:56-96 classifies 93 of the 94 commands (heavy = STANDBY only) and checks them on the HTTP thread at submit (:251-255), not at drain. Locally only four handlers test the state (app.py:993, 2343, 2442, 2572): project and config combos, imgsz, TRT toggle and rebuild, camera refresh and safe defaults run in RUN without confirmation (config_manager.py:407-408 sets _model_loading at once). gui.py has no exit callback, so the window X ends the loop (main_loop.py:262) with exit 0.

**Recommendation.** Move the policy table into `RuntimeAPI`: tag each submit with its source and check at drain. Apply heavy and never to the local GUI in RUN behind a visible show lock, and confirm on window close. One table then serves the GUI, the remote API and any later client.

**Risk.** Low in code. It changes what the operator can click in RUN, so it needs a line in the French operator note.

### 8. State is held twice and changed outside the queue: the operator's RUN badge can be wrong, and DearPyGui is touched from worker threads

severity medium · confidence medium · status new · effort M · prior id: 03-architecture §1.4 (bypasses only)

**Evidence.** gui.py:316,858-886 and app.py:521,1056-1062 each hold RUN/STANDBY; adapter.py:411-413 maps StateChanged to a no-op while remote SetState is allowed (remote_api.py:61), so badge and output can disagree. camera.state.is_open is a hand-synced mirror of unified_camera.is_open (main_loop.py:578,664,673). Unchanged since October (03-architecture 1.3-1.4): ROI and mask handlers write settings on the DPG thread (adapter.py:82-90); workers publish Toasts that delete and create DPG windows (app.py:2310, gui.py:2929-2948); main_loop.py reads 40 controller privates.

**Recommendation.** Make the GUI a view: buttons only submit, visuals are set in the `StateChanged` handler. Add `bus.post()` (a queue drained in the tick) for non-main publishers, with a thread assert in `publish`. Route ROI edits through `SetRoiRect` and `ExcludeAt`. All three are prerequisites of any engine/UI split.

**Risk.** Low to medium: ROI drag feel may change if edits go through commands. The consequences of the cross-thread DPG calls are inferred, not observed.

## Paradigm-level change proposed

Make the output path the innermost, most protected part of the system instead of one stage of the GUI loop. Target: an engine (camera, pipeline, slots, OSC, command drain) that never calls DearPyGui and never waits for a human; a UI that is only a client; a supervisor that owns start, relaunch and hang-kill.

What the seam already gives: 94 typed commands and 47 events with one drain point; RemoteApi serialising them over HTTP with a policy table; `/status.t` refreshed by the loop every 0.5 s, a ready liveness signal; a 7-method `UiClientPort` that tests already satisfy with a fake; `run.bat --project`; exit codes 0/2/3 and a crash marker.

Ladder, each step useful alone:
1. S, about a week: supervisor, hang-to-exit, stage policy, restart-state fixes (findings 1, 2, 4, 5).
2. S: extend the D34 keep-alive to loop-made gaps from a small sender thread, once D34 is verified on night takes.
3. M, 1-2 weeks: engine thread. Preview and prompts stay in-process, so nothing the operator sees breaks.
4. L, 3-5 weeks: engine process. It breaks the PreviewFrame ndarray (shared memory, or the 15 fps MJPEG), RoiMaskEditor (13 loop calls, DPG mouse polling; `SetRoiRect` and `ExcludeAt` exist), the TRT prompt and render pump, about nine dialog commands, and app.py's three gui/ui imports.

Cheapest intermediate step: step 1; it touches nothing on the October keep list. Steps 3 and 4 revisit the single-thread assumption, on evidence that appeared since: the remote API, the slots, D34, the measured render stalls.

## Questions for the owner

- During a show, is someone at the laptop within seconds, or must the system recover alone? If the relaunch becomes automatic, does D13 (restart into STANDBY, the operator re-arms) still hold, or should a crash mid-show return straight to RUN?
- What does the TouchDesigner patch do when OSC goes silent (no packets), as opposed to frozen coasting points, and after how long does it drop the video? This decides whether a keep-alive during loop blocks is enough.
- Is the WallDance window moved, resized, minimised or clicked during a show? A one-minute laptop test settles the drag question: in RUN, hold the title bar for 5 s, then look for an OSC gap in TD and a `[PerfSpike] dpg_render` line in the log.
- Is anything configured on the laptop outside the repo: auto-login, launcher at logon, sleep disabled, Windows Update paused? Which launcher build does its log print ('Launcher build: ...')?
- Is the laptop online at venues? The launcher fetches from GitHub before every start, including a restart after a crash, and prompts if `release` has moved.
- Has a hang, an exit 3 or a native crash happened in the field? Only one laptop app log (2026-10-06, DEV slot) is mirrored on dev37, and it shows none.
- Do you want to operate the full GUI remotely (developer over the tailnet)? That is the main thing only the two-process split gives; without it the engine thread is enough.

## Adversarial review

Three of the six claims hold and three are weakened; none is refuted. The supervision findings are sound and two are now reproduced rather than read: the watchdog's stack dump fails under the log tee, and a project load looks for the empty-wall plate under the previous project.

What does not hold up is the weight put on the thread and process split. Its only measured evidence is three 0.2 s render spikes in a 95-minute DEV session, against 2 s holds, and the engine-thread step is sized at 1-2 weeks against roughly 300 synchronous UI touchpoints in the handlers. Step 1 (supervisor, hang-to-exit, restart-state fixes) is justified as it stands; the per-stage failure policy bundled with it changes a shipped October behaviour and needs the owner's nod. Steps 3 and 4 should wait for a laptop test of the title-bar drag and for D34 to be released and verified.

Nothing was written to the repo: `git status --short` is empty, and the scripts I ran wrote only under `$TMPDIR`.

### Claims checked

- **weakened** — Verdict: "one process, one loop, GUI in the loop" is wrong for the show phase; nothing outside the process watches it; a restart returns a different state.  
  The supervision half is solid. The architectural half rests on untested scenarios (title-bar drag, heavy clicks in RUN). D13 (`ROADMAP.md:235`) already accepts a human re-arm after a crash, and `GUI_STACK_AUDIT.md:20,97` already carries "headless core: GUI crash ≠ show crash" as the direction, so this is a restated plan.  
  Evidence: The facts hold. `ops_monitor.py:593-600` only prints on a hang. `launcher/gui.py:245-256` runs run.bat once and only enables "Restart". `app.py:521` boots in RUN. A stubbed `_execute_project_switch` on `mur30m-0710` showed `_current_project == 'default'` when the config is applied.

The word "wrong" is not supported by measurement. The only laptop log (`tmp_analysis/field/night-0610/walldance_20261006_201756.log`, DEV slot, 1138 `[Budget]` lines) has three `dpg_render` spikes of 213-238 ms. Line 623 follows a `GUI resize_preview`; line 2619 is three lines before "WallDance stopped", during playback. The slot hold is 2.0 s (`config.py:323`, `identity_slots.py:1061`) and the KPI hole is 1 s. The log shows no hang, exit 3 or crash.
- **holds** — Finding 1: nothing restarts the app; exit 3, a crash, a reboot or an accidental close wait for a human at up to four points.  
  Correctly marked known_open (ARCH-6 item 6, `03-architecture.md:427`). Four waits is the upper bound; the usual case is two (Restart, picker). The picker is today's de facto D13 gate, so auto-relaunch with `--project` needs the STANDBY re-entry first; the report does say to ask.  
  Evidence: `launcher/gui.py:104,144` binds Restart to `start_sequence`, which re-runs the fetch and the BEHIND prompt (`:162-193`, `:406-414`). `:248-256` closes the launcher on exit 0 and only enables Restart otherwise. `main_loop.py:320-328` with `config.py:757` shows the picker unless `--project` or the env var is set; grep finds no setter in `launcher/` or `run.bat`. `install_manager.py:23-38` makes a shortcut only. `model_controller.py:250-252` loops without a timeout.
- **holds** — Finding 2: a hang is never recovered and leaves no diagnosis; the watchdog's stack dump fails under the log tee.  
  Two details for the fix. Re-arming `dump_traceback_later` cost 0.7 ms per call on dev37 (2000 calls), so arm it at 1 Hz, not on every beat. Cancel it before `_shutdown` (`main_loop.py:274-276`), or a slow recording finalise would be killed.  
  Evidence: Reproduced on dev37 with `app_log.install()` active: `faulthandler.dump_traceback(file=sys.stderr)` raises `UnsupportedOperation: fileno`, and after `LoopWatchdog._default_on_hang(12.3)` the log holds only the `[Alert]` line, no stacks. `_Tee` has no `fileno` (`app_log.py:29-68`); the exception is swallowed at `ops_monitor.py:597-600`. `app.py:735` builds `LoopWatchdog()` with no handler. Busy brackets have no ceiling (`config_manager.py:145-149`, `model_controller.py:435-439`).
- **weakened** — Finding 3: the OSC stream shares a thread with DearPyGui and every command handler; loop-made gaps are silent and stay on the hold clocks.  
  The "now" advice to run readiness on its existing worker contradicts `app.py:2317-2319` (made synchronous to avoid a cross-thread DPG write) and the report's own finding 8. The 0.25 s probe (`config.py:1099`) fires on the Calibrate tab, not in a show. The blocks of 2 s or more that it lists are model or project reloads, which an engine thread does not remove.  
  Evidence: The structure is as described: `main_loop.py:509-528` is one chain, OSC is sent at `pipeline.py:1054-1063`, and `camera_stall.py:60` removes only a silence the loop polled through.

The consequence is unmeasured. The cited stalls are 0.21-0.24 s against a 2 s hold; one is at a resize and one at exit. "A loop-made gap is not a stall" is D34's stated design (`camera_stall.py:11-13`, PLAN_25M §C.16). D34 is unreleased: `git branch --contains e3ad7de` lists only `remote-ops`.
- **holds** — Finding 4: a restart does not restore the show: the plate is looked up under the previous project, the app boots in RUN instead of the decided STANDBY, and no readiness check runs.  
  The plate bug is wider than the title: every A to B switch loads A's plate or none. It went unseen because the capture path uses the right folder (`app.py:1881`) and each night included a Calibrate. `ROADMAP.md:235` says only that the marker shipped; `:68` "(→ STANDBY)" is the misleading line.  
  Evidence: Plate: a stubbed run shows `project_at_apply='default'`, `full_config=True`, `project_after='mur30m-0710'`. `projects/mur30m-0710/plates/latest.npz` exists and `projects/default/plates/latest.npz` does not, so `app.py:1866-1869` clears the plate. Order: `config_manager.py:268` then `:271`; sole caller `app.py:1498`.

RUN: `app.py:521`; `_previous_crash` is only printed and toasted (`main_loop.py:253-254`, `:490-493`). Readiness runs only on a non-RUN to RUN edge (`app.py:2229-2232`).
- **weakened** — Rethink: make the output path the protected core, in four steps (supervisor, keep-alive thread, engine thread in 1-2 weeks, engine process in 3-5 weeks); step 1 touches nothing on the keep list.  
  "Nothing the operator sees breaks" in 1-2 weeks is not supported. Step 2 reverses D34's explicit rule and needs the GIL, which the report left unchecked. The per-stage policy in step 1 also rewrites the shipped ARCH-6 item 3 behaviour pinned by `test_main_loop_guards.py:259`. The trigger evidence for steps 3 and 4 is the 0.2 s spikes.  
  Evidence: The counts check out: 94 commands, 47 events, a 7-method `UiClientPort` (`main_loop.py:88-102`), `/status.t` at 0.5 s (`config.py:585`, `app.py:1021`). Step 1 fits the keep list (`03-architecture.md:464`: fix the launcher, don't replace it).

Step 3 is under-sized. Handlers touch the UI synchronously: 144 `self.ui.` calls in the four controllers, 106 `bus.publish` and 49 `_sync(` in `app.py`, a synchronous `EventBus.publish` (`api.py:1110-1116`), and 18 `render_frame` pumps outside the main loop.

### Missed or out of proportion

- Proportion: the only output interruption measured in the field is the USB camera stall (1.65-1.7 s, 0.7-2.9 per minute, CONTEXT §3). The laptop runs `origin/release` 63f4989, which has the slots but not D34, so each stall already silences OSC and burns the hold. Releasing and verifying D34 should be step 0 of the ladder; the report rates four unobserved failure classes high instead.
- The app log has no timestamps: `_Tee.write` writes lines raw (`app_log.py:36-49`) and the laptop log lines carry none. No hang, restart or render spike can be timed or matched to TouchDesigner. The report cites line numbers without noting this.
- OSC is not gated by RUN/STANDBY. `pipeline.py:1027` checks `osc_enabled` only, while `main_loop.py:813-816` forces processing in STANDBY during Calibrate, the dancers pass and plate capture; `api.py:50-51` says STANDBY sends no OSC. The report's section 2 lists STANDBY as stopping the stream.
- An external start path already exists and can kill the show. `extra/wdslot.py:344-364` registers the app as an interactive scheduled task with a 3-day execution limit. The `--via-launcher` variant (`:384-389`) passes no settings, so Windows defaults would apply (stop on battery, 72 h; not verified on the laptop). `REMOTE_OPS.md:212` advertises that variant for applying a release at once.
- The launcher's fetch on every start and restart has no timeout: `launcher/git_manager.py:114` calls `porcelain.fetch(repo, "origin")` bare. On 4G tethering a stalled connection could delay the restart before any prompt. This is inferred; the dulwich default was not checked.
