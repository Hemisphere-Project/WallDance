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

A **REMOTE** chip shows in the top bar while a client is active (**REMOTE\*** when control in RUN is allowed).
Every command and refusal is in the app log.

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
