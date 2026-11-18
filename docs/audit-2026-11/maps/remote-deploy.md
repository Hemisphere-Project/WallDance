# Subsystem map — remote-deploy

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

Everything between "developer pushes a commit" and "show laptop runs it", plus remote operation of the running app. Four parts: (1) the Windows launcher exe (clone / update from a `release` branch with dulwich, run install.bat, run run.bat); (2) the install/run scripts and dependency pinning; (3) two in-app HTTP servers: a loopback-only, token-protected remote ops API and an unauthenticated phone MJPEG monitor on the LAN; (4) the developer tools wdremote / wdslot, which reach the laptop over SSH on a tailnet to pull data, run code on the laptop's own stack, deploy a side-by-side DEV tree, drive the live app through an SSH tunnel, and build/install the launcher exe.

## Architecture

LAUNCHER (PyInstaller exe, customtkinter): launcher/main.py (SetupDialog, resolve_branch: env WALLDANCE_BRANCH > launcher.json > "release") -> gui.py LauncherGUI._sequence_thread: clone, or check_updates -> prompt -> GitManager.update() (git_manager.py, dulwich only, no git CLI) -> install.bat when needs_install -> ProcessRunner.run_bat(run.bat), stdout piped into a Tk textbox. Its only state: the in-memory needs_install flag, git refs (refs/walldance/pre-update-<stamp>), launcher.json.

SCRIPTS: install.bat/.sh have two modes: pinned (`uv pip sync requirements-prod.txt`) when that file exists, else `uv sync` + `uv pip install --upgrade torch torchvision --index-url <cuXXX>`. run.bat = import preflight + `uv run --no-sync python src/main.py`.

IN-APP (created in MainLoop._startup, main_loop.py:356-396): RemoteApi (ThreadingHTTPServer 127.0.0.1:8765, bearer token in ~/.walldance/remote_token, POLICY allowlist safe/control/heavy/never -> RuntimeAPI.submit; 2000-event ring buffer; files/logs/snapshot/clip endpoints). WebMonitor (ThreadingHTTPServer 0.0.0.0:8080: /, /stream.mjpg, /snapshot.jpg, /stats.json, /control).

DEV SIDE: extra/wdremote.py (SshTransport = ssh/sftp subprocesses, stdlib agents piped over stdin to the laptop's venv python, tiered resumable pull, run/pytest/py/replay, bundle, freeze-lock, ApiSession = `ssh -L` tunnel + token). extra/wdslot.py (DEV slot `<root>-dev` = plain tree sharing venv/models/projects by junction, GUI launch through an interactive scheduled task, release-check, launcher build/install).

## Data flow

RELEASE PATH: dev commit -> optional `wdremote deploy <ref>` (tar of changed blobs into the DEV slot) + `wdremote --slot dev pytest/replay` on the laptop's stack -> `wdremote release-check` (local diff flags, prints the push command) -> owner runs `git push origin <sha>:refs/heads/release` -> GitHub CI runs after the push (ubuntu + windows, numpy<2, no torch) -> at the launcher's next start: porcelain.fetch(origin) -> UP_TO_DATE / BEHIND / AHEAD / DIVERGED / UNKNOWN -> dirty check -> Yes/No prompt -> update(): backup ref, move colliding untracked files aside, move branch ref + HEAD, rewrite every tracked file, delete removed files -> install.bat only if install.bat or pyproject.toml changed -> run.bat -> app (starts in RUN, project picker unless WALLDANCE_AUTOLAUNCH_LAST=1).

REMOTE CONTROL: wdremote -> token read once over SSH and cached -> `ssh -L` to 127.0.0.1:8765 -> POST /api/v1/command -> check_policy(state, operator toggle) -> dataclass construction -> RuntimeAPI queue -> executed at the main loop's single drain point. Events: EventBus.publish -> RemoteApi._on_event (publisher's thread) -> ring buffer -> long-poll. /status = dict published by the main loop every 0.5 s.

PHONE: main loop copies each clean preview frame into WebMonitor; HTTP threads compute focus/lighting stats, draw the overlay and JPEG-encode on demand (cache keyed by frame id + render version).

DATA BACK TO DEV: wdremote manifest agent -> tar.gz stream for files <=4 MB, sftp with resume for the rest.

## Threading and state

LAUNCHER: Tk main thread; one daemon sequence thread; one daemon reader thread per .bat. Dialogs are marshalled with after() + threading.Event. No single-instance guard for the launcher or the app; closing the launcher window leaves the app running (app_log's tee tolerates the dead pipe), and a second launcher start then runs an update check and a second app.

REMOTE API: one threading.Condition guards the event deque, the latest-value map and the status dict. _on_event runs on the main loop thread for every bus event and does a json dumps+loads round trip before deciding whether to store it; that includes the per-tick StatsTick (remote_api.py:205-219, main_loop.py:1114). HTTP threads (daemon, thread per request, no timeouts) read snapshots and enqueue commands. Policy is evaluated on the HTTP thread at submit time and the command executes later at drain. _jobs and _last_client are unlocked (benign). Clip jobs are daemon threads with no concurrency cap.

WEB MONITOR: _lock for the latest-frame slot, _enc_lock for the JPEG cache; stats cache and focus peak are unlocked, so concurrent clients can each re-render the same frame. HTTP/1.1 keep-alive, one thread per connection, no cap, no socket timeout.

WDREMOTE: stateless per call; local caches in ~/.config/walldance (remote.json, remote_token.<host>). On the laptop: <root>/tmp_analysis/remote/<stamp> scratch dirs, .wdslot.json + DEPLOYED.json in the DEV slot, persistent scheduled tasks WallDance-dev / -live / -launcher.

## Failure handling

LAUNCHER: fetch failure or missing channel -> UNKNOWN -> logged as "No updates available" and the installed version starts. Dirty tracked files -> update refused with a dialog (fails safe if the dirty check itself errors). update() exception -> error dialog, then run.bat is started anyway. install.bat failure -> Retry / Exit dialog; the pending install is not remembered across launcher restarts. run.bat exit code != 0 -> "WallDance closed" + a Restart button that waits for a human; exit 0 -> launcher exits. No timeout on the fetch, no auto-restart, no hang detection, no boot autostart.

APP SIDE: both servers are best-effort (a bind failure prints and disables the server). Command handler exceptions are caught in RuntimeAPI.drain. The main loop exits with code 3 after 50 consecutive tick/frame failures and with 2 on startup failure; LoopWatchdog only logs hangs. The launcher does not interpret these codes.

REMOTE API: 401 / 403 / 400 JSON for auth, policy and constructor errors. Malformed query ints or a non-object JSON body raise inside the handler; socketserver contains that per request.

WDREMOTE: ssh BatchMode (never prompts); agent failure -> SystemExit with stdout/stderr tails; pulls are resumable and report incomplete files; `slot stop` falls back from API Quit to Stop-Process -Force. Deploy validates tar member paths and syntax-checks the tree.

## Tunables

About 45, scattered, mostly undocumented outside comments.

core/config.py:570-590: WEB_MONITOR_ENABLED/PORT 8080/HOST 0.0.0.0/JPEG_QUALITY 70/MAX_FPS 15; REMOTE_API_ENABLED/HOST 127.0.0.1/PORT 8765/TOKEN_FILE/STATUS_INTERVAL_S 0.5 s; APP_LOG_KEEP 40; OPS_TICK_ERROR_EXIT_STREAK 50 frames.

Environment variables: WALLDANCE_BRANCH, WALLDANCE_AUTOLAUNCH_LAST, WALLDANCE_SKIP_TORCH_AUTOFIX, WALLDANCE_PIN_DRY_RUN, UV_HTTP_TIMEOUT (300 s default in pinned mode), WD_REMOTE_API_PORT, WD_REMOTE_ALLOW_QUIT, WD_REMOTE_CONTROL, WD_SLOT, WD_REMOTE_CONFIG/HOST/ROOT. Files: launcher.json {"branch"}, ~/.config/walldance/remote.json (host, root, os, python, bwlimit_kbit, api_port, dev_root, ssh_opts).

Hard-coded: repo URL (launcher/main.py:115); reinstall trigger list (git_manager.py:237-240); _DIRTY_EXEMPT; torch index order cu130..cu124; 500 ms / 2 s launcher delays; event buffer 2000; long-poll cap 60 s; log tail cap 20000 lines x 256 B; clip cap 6000 frames, scale 0.1-1.0; SMALL_FILE_BYTES 4 MB; ssh ServerAliveInterval 15 s; tunnel wait 15 s; HTTP timeout 70 s; scheduled-task ExecutionTimeLimit 3 days; web monitor center_frac 0.4, grid 8x5, peak decay 0.995, percentiles 2/98, gamma 0.6, gain 1-16; gpu_limiter default 280 W (written for the dev RTX 3090, not the laptop).

## Interfaces with neighbours

- App start state: the runtime starts in RUN (app.py:521) and blocks on the project picker unless WALLDANCE_AUTOLAUNCH_LAST=1 or --project is passed (main_loop.py:308-339). Any supervision built into the launcher has to set one of these.
- Exit codes 2 and 3 and logs/last_crash.json (main_loop.py:241-277) are produced by the runtime but not interpreted by the launcher.
- Command validation lives in runtime/api.py dataclasses and in GUI widget limits; the remote API inherits only the former. Policy is checked at submit time on an HTTP thread and executed at the next drain, and long 'heavy' jobs accepted in STANDBY rely on each handler to re-check state (the import handler does, app.py:994-997; I did not check the others).
- RemoteApi._on_event serialises every bus event on the publishing (main loop) thread, including the per-tick StatsTick with the timing dict, even when no remote client has ever connected; that cost is counted in gui_stats_ms (main_loop.py:1113-1136). Cheap fix: keep the event object and serialise lazily in status().
- core/version.py reads .git/HEAD and caches it, so after a half-applied launcher update the app, recordings and /status report the new commit for a mixed tree.
- extra/build_engines.bat never runs measure_engine_fps.py (the .sh does, build_engines.sh:178-182), so models/fps_table.json, which calib2's imgsz budget and model advisory read (calibration_flows.py:717), is not produced on the Windows laptop by the documented script.
- Engine builders skip an engine by file name only (build_engines.bat:166-170). After a TensorRT or ultralytics pin change, stale engines must be detected at load by the model controller; I did not review that.
- The DEV slot shares projects/ with LIVE by default, so config-store forward compatibility (release code reading files written by a newer branch) is a deployment concern.
- wdremote leaves scratch directories, bundles (a full `git bundle --all` per call), clips and launcher .bak files on the laptop with no cleanup command; clip output for a file directly under a shared root lands outside the roots (remote_api.py:368-370).
- CI runs after the push to `release`, on numpy<2 without torch, so it is a report on a different stack, not a gate.
- extra/gpu_limiter.sh/.bat default to 280 W 'for RTX 3090 on a 750 W PSU': a dev-box tool, not provisioning for the RTX 5080 laptop.

## Strengths

- Remote API trust model is sound and small: loopback bind, bearer token compared with secrets.compare_digest, reached only through an SSH tunnel; explicit four-class allowlist with deny-by-default (remote_api.py:56-96, 124-145); the operator's 'allow remote control in RUN' toggle is deliberately absent from the allowlist, and a test asserts every command is classified.
- Commands from any client are executed at one point on the main thread, and handler exceptions are contained (runtime/api.py:1149-1167), so a failing remote command cannot raise into the show loop directly.
- Launcher update safety was clearly reworked after an incident: separate `release` channel so dev pushes cannot reach the laptop; AHEAD is never offered an update; dirty tracked files refuse the update and a failing dirty check is treated as dirty (gui.py:183-189); a backup ref is written; other branches are untouched; untracked collisions are moved aside; the destructive dialog has real button labels and a safe default. Covered by tests that also run on windows-latest in CI.
- Pinned install mode freezes the stack actually verified on the laptop (`uv pip sync`, UV_HTTP_TIMEOUT raised after a measured slow-link failure), and release-check warns when a release would trigger a reinstall without the pin file (wdslot.py:475-479).
- wdremote is careful about the live checkout: every git call uses --no-optional-locks and diff-files so it never rewrites .git/index behind the launcher (wdremote.py:297-299, 1069-1093); scratch output goes to an ignored directory; pulls are tiered, resumable and bandwidth-limitable for 4G.
- The DEV slot lets a candidate branch run pytest and replays on the laptop's real stack and GPU without touching the launcher's checkout; deploy sends only changed blobs, validates tar member paths and syntax-checks before declaring success.
- Process plumbing details show field experience: interactive scheduled task to get a desktop session from SSH, PowerShell -EncodedCommand to avoid cmd/ssh quoting, process query that excludes itself, launcher install that refuses while the launcher runs and keeps a .bak.
- The app log tee survives a dead console (launcher closed), a crash marker is written, and shutdown runs in a finally with isolated steps, so remote diagnosis after a failure is possible.
- build_engines builds the default engine first and reads model/imgsz from core/config.py, so an interrupted grid build still leaves a show-ready machine and the script cannot drift from the app.

## Questions the reader could not answer from code

- Which launcher build is on the show laptop today (the launcher log prints 'Launcher build: ...')? Is the March exe from launcher/release/ installed on any machine that could be used for a show?
- Is the laptop normally online at show sites, and who answers the update prompt there? Is there an agreed rule such as 'no updates on show day', or is that left to the technician?
- Is unattended operation a requirement (multi-day installation, recovery after a power cut with nobody at the laptop), or is a technician always within reach? This decides how much supervision/autostart is worth building.
- Has a from-scratch pinned install ever been run on a blank Windows machine (uv-managed Python 3.12.12, tensorrt_cu13_libs, ids-peak, the cu130 index), or only as a no-op sync on the laptop the file was frozen from? Is there a spare laptop?
- What does the media server do when OSC stops because the app died, and does anything outside the laptop alert a human?
- Are automated (Claude) sessions allowed to run wdremote against the laptop during show windows? If yes, the missing run-state interlock matters more.
- Who can push to `release` on GitHub, and is the branch protected? That push is the only gate between a commit and code plus install.bat executing on the laptop.
- How is the laptop's sshd exposed (tailnet interface only?), is the SSH user an administrator, and who else is on the tailnet?
- Is the tracked file TODO-me edited on the laptop? wdremote's pull plan fetches it from prod (wdremote.py:900-902), and a local edit to a tracked file blocks every launcher update.
- Is the phone monitor needed during RUN, or only during camera setup?
- Is `wdremote slot run --slot live --via-launcher` used to start the app that then runs a show, or only for testing an update?
