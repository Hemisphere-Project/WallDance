# Remote ops — working on the prod laptop from a dev box

**Date:** 2026-10-06 · **Status:** 🟢 tooling built on branch `remote-ops` (dev37), not yet on the laptop.
**Why:** the show laptop is reached over the Hemisphere tailnet. Sometimes the link is fast, but at rehearsal
venues it is slow 4G tethering, where syncing GBs of FFV1 recordings is impractical. Two ways of working:

| Link | Way of working | Tools |
|---|---|---|
| **Fast** (≳ 50 Mbit/s) | **Mirror, then run locally**: pull configs, sessions, takes and the corpus to dev37; replay/tune here | `wdremote plan` → `wdremote pull` |
| **Slow** (4G) | **Run on the laptop, read results**: replays/scripts/tests run there; only JSON, logs, contact sheets and small clips come back | `wdremote replay / py / run / pytest`, `clip --pull` |
| **Live app** (any link) | **Drive the running app**: record, playback, readiness, calibration, dials; status/events/logs/snapshot | `wdremote status / events / record / cmd / logs / snapshot` |

`wdremote probe` measures the link and suggests a mode.

---

## 1. One-time setup on the laptop (Thomas, admin PowerShell)

1. **Tailscale** up on the Hemisphere tailnet. Note the machine name (e.g. `wd-prod`).
2. **OpenSSH Server**, reachable **only from the tailnet**:
   ```powershell
   Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
   Set-Service sshd -StartupType Automatic; Start-Service sshd
   Get-NetFirewallRule -Name *OpenSSH* | Remove-NetFirewallRule
   New-NetFirewallRule -Name wd-ssh-tailnet -DisplayName "SSH (tailnet only)" -Direction Inbound `
     -Protocol TCP -LocalPort 22 -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
3. **Key auth.** Put dev37's public key (`~/.ssh/mgr-26.pub`) into
   `C:\ProgramData\ssh\administrators_authorized_keys` if the Windows user is an admin, or
   `%USERPROFILE%\.ssh\authorized_keys` otherwise. Write it with `Add-Content` (ANSI), not
   `>`/`Out-File` (UTF-16, which sshd rejects), and lock the admin file down:
   `icacls <file> /inheritance:r /grant Administrators:F /grant SYSTEM:F`.
4. Leave the default shell as **cmd.exe**: `wdremote` builds cmd.exe command lines.
5. **uv's Python junction** (Windows 11 24H2+). An SSH session refuses to follow a junction made by a
   non-elevated process ("point de montage non approuvé" / untrusted mount point), and the venv's base
   Python is reached through uv's `cpython-3.12-windows-x86_64-none` junction. `doctor` then shows
   `python … rc=103` ("No Python at …"). Recreate that junction from an **admin** PowerShell (same target;
   nothing changes for the desktop session). uv recreates it, untrusted again, when it installs a new 3.12
   patch release:
   ```powershell
   cd $env:APPDATA\uv\python
   cmd /c rmdir cpython-3.12-windows-x86_64-none
   cmd /c mklink /J cpython-3.12-windows-x86_64-none "$env:APPDATA\uv\python\cpython-3.12.12-windows-x86_64-none"
   ```
   Junctions that `wdremote deploy` creates from the SSH session (DEV slot) are trusted.

## 2. One-time setup on the dev box

```bash
python extra/wdremote.py --host <user>@<tailnet ip> --root C:/WallDance/WallDance \
    setup --identity ~/.ssh/mgr-26  # root = the launcher's checkout
python extra/wdremote.py doctor        # ssh, shell, venv python, git CLI present?
python extra/wdremote.py inventory     # git state, stack versions, engines, projects (saved as JSON)
```

The config lives in `~/.config/walldance/remote.json`. The first connection pins the laptop's
host key (`StrictHostKeyChecking=accept-new`); a changed key later fails loudly. The API token is cached next to it as
`remote_token.<host>` (0600).

## 3. Mirror mode (fast link)

```bash
python extra/wdremote.py probe                 # ~Mbit/s over ssh -> suggested mode
python extra/wdremote.py plan --probe -v       # per tier: files, size, ETA
python extra/wdremote.py pull --tier P0        # configs, sessions, issues, calib2 json, logs, .meta
python extra/wdremote.py pull --tier P1 --since 2026-10-01   # fresh field / marker takes
python extra/wdremote.py pull --tier P2        # recordings pinned by tests/scenarios (corpus)
python extra/wdremote.py bundle                # git bundle --all of the laptop checkout
```

- **Tiers:**
  - **P0**: state and text (compressed over SSH, ~10×).
  - **P1**: recordings newer than `--since`.
  - **P2**: the scenario corpus.
  - **P3**: the rest, including the live `tracking_events.jsonl`, which only grows.
- **Resumable:** an interrupted pull continues where it stopped (`sftp get -a`). `--bwlimit <Kbit/s>` caps it,
  for example while a show is running. `--max-gb` stops adding files beyond a budget.
- Files are mirrored into this repo's `projects/` with their mtimes. A local **symlink** in the way blocks the
  write unless you pass `--follow-symlinks`. dev37's `projects/3_TANGO_HANGAR-whitebg2 → residence1-solo` alias
  should be removed before pulling the real project.

## 4. Remote-run mode (slow link)

```bash
python extra/wdremote.py replay hangar-aerial --trt --score          # summary JSON back
python extra/wdremote.py replay texture-duo --trt --timeline --logs  # + timeline + session logs
python extra/wdremote.py py tmp_analysis/audit-2026-10/continuity/drive.py -- build --scenario ...
python extra/wdremote.py pytest -k recording
python extra/wdremote.py run -- python -c "import tensorrt; print(tensorrt.__version__)"
```

- **`py`** uploads a local script (plus `--with` extra files) into `<root>/tmp_analysis/remote/<stamp>/`
  (gitignored, so the launcher's dirty check never sees it). It runs the script with the laptop's venv, cwd
  `application/`, with `WD_REMOTE_OUT` pointing at a scratch `out/` dir. That `out/` is then fetched to
  `tmp_analysis/remote-runs/<stamp>/`. Write results there.
- **Heavy jobs compete with a running show.** Run them in STANDBY, or with the app closed.

## 5. Live app (in-app remote API)

The app serves a small HTTP API, `services/remote_api.py`, on **127.0.0.1:8765 only**. `wdremote` reaches it
through an SSH port-forward, so nothing listens on the venue network and there is no firewall prompt. A bearer
token is created on first start in `%USERPROFILE%\.walldance\remote_token`; `wdremote` reads it over SSH.

```bash
python extra/wdremote.py status                       # state, project, fps, tracks, engine, recorder, rig
python extra/wdremote.py events -f --types Alert,Toast,ReadinessResult,CalibReportCard
python extra/wdremote.py cmd CheckReadiness
python extra/wdremote.py record start --slot 3 ; python extra/wdremote.py record stop
python extra/wdremote.py cmd SetRigSheet field=f_number value=2.8
python extra/wdremote.py cmd ImportVideoToSlot slot=4 path="C:/Users/<u>/Videos/take.mov"   # STANDBY
python extra/wdremote.py cmd SetInputTransform mirror=true rotation=90
python extra/wdremote.py commands                     # allowlist + policy class of each command
python extra/wdremote.py logs -f                      # the app log (logs/walldance_<stamp>.log)
python extra/wdremote.py snapshot                     # current preview JPEG
python extra/wdremote.py clip projects/<p>/recordings/slot_3_x.avi --start 900 --frames 200 --scale 0.5 --pull
```

**Policy** (Thomas, 2026-10-06: "full, guarded"):

| Class | Examples | Allowed |
|---|---|---|
| safe | `CheckReadiness`, `SetRigSheet`, overlays | always |
| control | `SetState`, `StartRecordingSlot`, `PlaybackControl`, dials, `StartCalibration`, `SaveConfig`, `SetInputTransform`… | in STANDBY; in **RUN only while the operator ticks *Allow remote control during RUN*** (phase 6 Live, GUI-only) |
| heavy | `RunKnownNTune`, `RunCalibSweep`, `RunDryRunReplay`, model/engine/project loads, clips, `ImportVideoToSlot` | STANDBY only |
| never | `Quit`, delete/rename project, dialogs | never; anything not listed is denied |
| support | `SupportMessage` (§5c) | any state, RUN included, while the project key `support_line` is on; the RUN tick plays no part |

A **REMOTE** chip shows in the top bar while a client is active (**REMOTE\*** when control in RUN is allowed).
Every command and refusal is in the app log.

## 5b. Watching the app over a slow link (`wdwatch`, S1a)

`extra/wdwatch.py` reads the live app every ~30 s through `wdremote`'s tunnel and token and logs it on the
dev box. It only reads: four GET endpoints (`ping`, `status`, `events`, `snapshot.jpg`), never a command.

```bash
python extra/wdwatch.py watch                           # supervision (7–8/12): cues printed
python extra/wdwatch.py watch --notify "<cmd>"          # ... and each cue given to <cmd> on stdin
python extra/wdwatch.py watch --record-only --hours 10  # the show (12/12): logs only, contacts no one
python extra/wdwatch.py summary                         # the one-screen summary of the latest run
kill -USR1 <pid>                                        # the same screen in the watcher's terminal
```

- **A poll**: `/status` (state, project, engine, fps, camera, recorder; the last StatsTick's health figures and
  the build's health line when it has one; the live hit rate), the events since the last poll (alerts, toasts,
  readiness, state and camera changes), one snapshot.
- **The live hit rate** (`hit_rate` in `/status`): the share of the foreground's dancer groups with a detection
  box on them, pooled over the last 60 s, sampled at the status cadence (2 Hz). The app scores it with the
  settings swipe's referee (`core/swipe.py`), so there is one referee; a build without that module has no
  `hit_rate` key (absent, never 0). `rate` is null while no dancer group is seen (foreground off, no plate,
  an empty wall); `skipped` counts frames whose foreground was invalid.
- **A slow link**: each poll has a byte budget (`--budget-kb`, 40 KB). The app shrinks the snapshot
  (`/snapshot.jpg?w=320&q=50&max_bytes=N`, a few KB) to what status and events left; an answer larger than its
  room is not read. A poll slower than `--slow-s` (10 s) doubles the interval and shrinks the next snapshots;
  fast polls undo it. Never the video stream. A build without `snapshot_fit` in its status gets no snapshot
  unless `--full-snapshots` (still within the budget).
- **Unattended**: a dropped link, a laptop asleep or an app restart is a line in the log. The tunnel is
  reopened with a back-off (60, 120, 240 s, then every 300 s) and the watch resumes by itself; a restarted app
  is noticed and its events are read from its start.
- **The log**: `tmp_analysis/watch/<YYYY-MM-DD_HHMMSS>/`: `polls.jsonl` (each poll with the full status, plus
  `link`, `restart`, `cue`, `start` and `stop` lines), `snapshots/`, `summary.txt` (rewritten after each poll).
- **Record-only**: `--notify` is refused and cues go to the log only. In both modes the client has no POST.

## 5c. The support line: a message on the laptop's screen until it is read (S1b)

The one channel from the supervising session to the technician during the rehearsal (7–8/12): a short message
on the laptop's screen that stays until he clicks its button. The session drafts each message and sends it only
once Thomas has said yes. `services/support_line.py`; the client is `wdremote` (`wdwatch` never sends).

```bash
python extra/wdremote.py support-ack                                        # the line: on/off, on screen, waiting, read
python extra/wdremote.py support-send "Relance Calibrate, s'il te plaît"   # prints the message's id
python extra/wdremote.py support-send --file msg.txt --wait 600            # ... and waits until it is read
python extra/wdremote.py support-ack 143502-1 --wait 300                   # read? after how long (exit 0 read, 2 not yet)
python extra/wdremote.py events --types SupportMessageShown,SupportMessageQueued,SupportMessageAcked
```

- **What governs it: the project key `support_line`, not *Allow remote control during RUN*.** A message changes
  nothing in the show, and turning the key on in a project is the consent. Off by default (`core/config.py`
  `SUPPORT_LINE = False`); a project turns it on with `"support_line": true` in its config file (no GUI; the
  operator's saves keep it). On in the rehearsal project only; off in every other project, and on 12/12. Key on:
  accepted in any state, RUN included, ticked or not. Key off: `403 SupportMessage refused: the support line is
  off in this project (project key support_line)`, and nothing shows. `/status` says which (`support_line.enabled`).
- **On the screen**: a yellow band at the top of the preview: `MESSAGE DU SUPPORT`, the time it came, how many
  more wait, the text, and one button, `J'ai lu`. It stays until that click. It is not a window: no modal, no
  popup, no text field (the shortcuts and every control keep working), and the frame loop never waits on it. It
  sits in the preview's panel, pushing the picture down by its height (its bottom is clipped while it shows),
  never over the phase panel (readiness rows, the Live controls), the alerts strip or the bottom bar. A click
  within 1 s of a message coming on screen is ignored, so a double click never reads the next one unseen.
- **A second message waits its turn** (first in, first out) and never replaces the one on screen: each is read
  and acknowledged on its own, so the session knows which were read. The band says `encore N message(s) après
  celui-ci`. At most 5 wait behind the one on screen; one more is refused (429).
- **The acknowledgement** is the event `SupportMessageAcked` in the ring (`/api/v1/events`): `msg_id`, `text`,
  `sent_at`, `shown_at`, `acked_at` (the click), `seconds` (from sending to the click), `on_screen_s`, all on the
  laptop's clock. `/status` carries `support_line`: `enabled`, the message `showing`, the `waiting` ids and the
  last ten `read` with their `seconds`, so an acknowledgement older than the ring is still found. Only the
  operator acknowledges: `AckSupportMessage` is never allowed remotely.
- **The text**: plain text, at most 280 characters after cleaning (longer is refused, never cut: send two);
  markup (`<b>`, `&eacute;`) is refused; line breaks and tabs become spaces; other control and format characters
  are removed. The app's font (DearPyGui's default, ProggyClean) draws all of Latin-1, so every French accent,
  `« »` and `ç` show as typed. What it lacks is transliterated visibly and listed in the answer's `notes`:
  `’ ‘` → `'`, `“ ”` → `"`, `…` → `...`, `– —` → `-`, `œ Œ` → `oe OE`, `€` → `EUR`, thin spaces → a space; any
  other character → `?`, never a box.
- **Quoting**: give the text as **one** argument in double quotes (French apostrophes), or `--file F` (`-` =
  stdin), which a session sending drafted text should prefer.
- **The log**: each message sent, refused, shown, queued and acknowledged is a line in the app log
  (`[RemoteApi] SupportMessage …`, `[Support] message … on screen`, `… acknowledged after … s`), and each step
  an event in the ring. `wdwatch` logs the `support_line` block of every status it reads.

## 6. What every recording now carries (MRK-0)

Each `slot_N_<stamp>.avi.meta` holds:
- `actual_fps` and `frames` (frames written; the encoder now drains its queue on stop, so no tail loss);
- codec, size, start/stop time, queued/dropped counts;
- the camera snapshot at start and `at_stop`: exposure, gain, AE/AG, pixel format, ROI, black level, gamma,
  temperature, serial/firmware, UserSet;
- app commit/branch, project, config file, profile, engine (model/imgsz/TRT active), the full live config;
- the **rig sheet** (phase 1 Rig → *Rig sheet*): lens, f-number, focus, filter, IR light and its offset from
  the lens, camera distance/height, markers, notes. A field nobody entered is **absent** (unknown): the code no
  longer pre-fills the 8 mm lens (takes before 2026-10-08 claim `Tamron M118FM08 (8 mm)` / `focal_mm` 8 although
  the laptop runs the 6 mm M118FM06; a saved sheet still exactly at that pre-fill loses lens/focal on load).
  A new project (*Start blank*) inherits the camera rig of the last used project: camera, crop ratio, exposure,
  gain, mirror/rotation and the on-camera sheet fields (lens, focal, f-number, filter, IR light + offset); the
  scene (ROI, mask, gamma/CLAHE/MOG2, sensitivity, empty-wall plate, distances/focus/markers/notes) starts fresh.

`slot_N_<stamp>.avi.camlog.jsonl` samples exposure, gain, AE/AG and temperature about once a second during the
take, because auto-exposure drifts within takes.

**Mirror / rotate (REQ-5).** Slot files hold the **raw** sensor frames. `input_transform` in the `.meta` records
the Mirror/Rotate that was live at REC; playback applies the project's *current* setting (phase 1 Rig > Input),
like the live camera, so ROI, mask and calibration always match the screen. A toast says so when a take was
recorded under a different setting. The offline tools (`tests/replay.py`, `calibrate_segment.py`, `known_n.py`,
`detect_cache.py`) do not apply it yet: the app warns when Dry-run / Auto-tune / Known-N start with it on.

**Imported takes (REQ-1, IMPORT on the recordings bar or `ImportVideoToSlot`).** The file becomes the slot's
newest take; older takes stay in the Ctrl+click history. `.avi` / `.mp4` are byte-copied; other containers
(`.mov`, `.mkv`, `.m4v`, `.webm`...) are transcoded to MJPG `.avi` (quality `IMPORT_TRANSCODE_QUALITY`), because
the slot list and the replay tools only read `.avi` / `.mp4` and OpenCV's MJPG encoder exists in every build.
A file OpenCV cannot decode is refused. The `.meta` has `actual_fps`, `frames`, `meta_version: 2`,
`source: "import"` and `imported_from` (original path, size, mtime, mode, source codec/fps/frames, and the
source's own `.meta` when it is a WallDance take).

## 6b. Phase 0a analysis (IR-marker takes)

The marker takes are analysed **on the laptop** by `tmp_analysis/marker_eval.py` (MRK-1/2). Only the
summaries and small contact sheets come back. The full runbook is **[MARKERS_PHASE0A.md](MARKERS_PHASE0A.md)**:
metric definitions, per-take commands, local equivalents and the pre-footage synthetic checks.

```bash
WITH="--with tmp_analysis/marker_evallib.py --with application/src/core/marker_model.py"   # --with BEFORE the script
python extra/wdremote.py py $WITH tmp_analysis/marker_eval.py -- info --project markers-0a-<venue>        # provenance check
python extra/wdremote.py py $WITH tmp_analysis/marker_eval.py -- phase0a --project markers-0a-<venue> \
    --trt --threshold auto --poses-dir ../tmp_analysis/markers0a                                         # all of §6.2 -> go/no-go
```

`info` returns ~2 KB, one take ~150–300 KB, and `phase0a` ~1–1.5 MB (~200 KB with `--sheet 0 --overview 0`). On a
flaky link, run the takes one by one (runbook §1.3): one SSH session spans the whole run.

## 7. Delivery: DEV slot, release channel, launcher (decision D1)

Two code trees on the laptop:

| Slot | Path | Managed by | Purpose |
|---|---|---|---|
| **LIVE** | `<launcher dir>\WallDance` | the launcher (git, `release` branch) | what the operator runs for shows |
| **DEV** | `<launcher dir>\WallDance-dev` | `wdremote deploy` (plain files + `DEPLOYED.json`) | try any branch within minutes |

DEV shares LIVE's venv, `models/` and `projects/` through junctions. A deploy only sends the tracked files
that changed: a full first deploy is about 300 files / 1.3 MB, and a typical branch update is a few KB.
Recordings and configs saved from DEV are real project data; `deploy --isolated-projects` avoids that.

```bash
python extra/wdremote.py deploy <branch|sha> [--dry-run]     # rollback = deploy the previous ref
python extra/wdremote.py --slot dev pytest -x                # tests with the laptop's own stack
python extra/wdremote.py --slot dev replay hangar-aerial --trt --score
python extra/wdremote.py slot run --slot dev -- --project <p> --slot 3    # GUI on the laptop desktop
python extra/wdremote.py status ; python extra/wdremote.py events -f      # drive / watch (API)
python extra/wdremote.py slot stop --slot dev                # STANDBY -> graceful quit
python extra/wdremote.py slot status                         # LIVE commit, DEV ref, running apps
python extra/wdremote.py release-check <ref> --tests         # promotion preview + push command
python extra/wdremote.py launcher status|build --ref <ref>|install
```

- **Interactive start.** `slot run` starts the app through an *interactive scheduled task*, because SSH
  sessions cannot show a GUI. The SSH user must be the logged-on desktop user. Only one app runs at a time
  (camera and ports are exclusive).
- **Control in RUN.** DEV runs start with remote control during RUN enabled and graceful remote quit allowed.
  LIVE runs never allow remote quit and start gated by the operator toggle.
- **Promotion.** `release-check` shows commits ahead/behind (fast-forward or diverged for the launcher) and
  flags reinstall triggers (`install.bat`, `pyproject.toml`), launcher source, OSC contract and tracking-core
  changes. Thomas then runs the printed `git push origin <sha>:refs/heads/release`. The launcher
  fast-forwards LIVE at its next start, or immediately with `slot run --slot live --via-launcher`.

## 8. Online-window playbook

**The first window (in this order: the 2026-03-26 exe hard-resets LIVE on any difference from `origin/main`):**

| # | Step | Command | Time on 4G / fast |
|---|---|---|---|
| 0 | Thomas: Tailscale up, OpenSSH (§1), key in | — | 5 min once |
| 1 | Connect + sanity | `setup …`, `doctor` | 1 min |
| 2 | **What is there** (git state, stack, engines, launcher) | `inventory --freeze`, `launcher status` | 1 min |
| 3 | **Back up LIVE** | `bundle` (≈ .git size, tens of MB) | 5–15 min / 1 min |
| 4 | Pull state + text (configs, sessions, issues, logs) | `plan --probe`, `pull --tier P0` | depends; compressed |
| 5 | **Replace the launcher** (pinned build on the laptop, `.bak` kept, `launcher.json` → release) | `launcher build --ref <tested ref>`, `launcher install` | 5–10 min (downloads ~50 MB of build deps on the laptop) |
| 6 | Try the new code without touching LIVE | `deploy <ref>`, `--slot dev pytest`, `--slot dev replay … --trt --score` | 1 min + run time |
| 7 | GUI check on the laptop | `slot run --slot dev -- …`, `status`, `cmd CheckReadiness`, `logs`, `slot stop` | 5 min |
| 8 | Marker takes (if shot) | analyse on the laptop (`py tmp_analysis/marker_eval.py …`) or `pull --tier P1` on a fast link | — |

After step 5, a push to `main` can no longer reset the laptop. Until Thomas publishes `release`, the new
launcher simply reports "channel not found" and starts LIVE as it is.

**Later short windows (≈ 15 min):** `slot status` → `deploy <ref>` → `--slot dev pytest -x` → the one
replay/check that matters → `pull --tier P0 --since <date>`. Prepare refs and commands *before* the window
opens; nothing is built or decided while connected.

**If LIVE has laptop-only edits** (step 2 shows a dirty tree): the new launcher refuses to update over them,
which is safe. Commit them on the laptop (`run -- git commit -am "field edits"`), bundle, reconcile on dev37,
and release a version that contains them.

**First-time Windows checks** (cannot be tested on dev37): cmd.exe quoting of `run`/`replay`, sftp
`/C:/…` paths, junction creation, the interactive task actually showing the GUI, the launcher's new
two-button dialog, and IDS node reads into `.meta` on the real camera.
