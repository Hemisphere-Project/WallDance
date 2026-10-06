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
3. **Key auth.** Put dev37's public key (`~/.ssh/id_ed25519.pub`) into
   `C:\ProgramData\ssh\administrators_authorized_keys` if the Windows user is an admin, or
   `%USERPROFILE%\.ssh\authorized_keys` otherwise.
4. Leave the default shell as **cmd.exe**: `wdremote` builds cmd.exe command lines.

## 2. One-time setup on the dev box

```bash
python extra/wdremote.py setup --host wd-prod --root C:/WallDance/WallDance   # the launcher's checkout
python extra/wdremote.py doctor        # ssh, shell, venv python, git CLI present?
python extra/wdremote.py inventory     # git state, stack versions, engines, projects (saved as JSON)
```

The config lives in `~/.config/walldance/remote.json`. The API token is cached next to it as
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
python extra/wdremote.py commands                     # allowlist + policy class of each command
python extra/wdremote.py logs -f                      # the app log (logs/walldance_<stamp>.log)
python extra/wdremote.py snapshot                     # current preview JPEG
python extra/wdremote.py clip projects/<p>/recordings/slot_3_x.avi --start 900 --frames 200 --scale 0.5 --pull
```

**Policy** (Thomas, 2026-10-06: "full, guarded"):

| Class | Examples | Allowed |
|---|---|---|
| safe | `CheckReadiness`, `SetRigSheet`, overlays | always |
| control | `SetState`, `StartRecordingSlot`, `PlaybackControl`, dials, `StartCalibration`, `SaveConfig`… | in STANDBY; in **RUN only while the operator ticks *Allow remote control during RUN*** (phase 6 Live, GUI-only) |
| heavy | `RunKnownNTune`, `RunCalibSweep`, `RunDryRunReplay`, model/engine/project loads, clips | STANDBY only |
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
  the lens, camera distance/height, markers, notes.

`slot_N_<stamp>.avi.camlog.jsonl` samples exposure, gain, AE/AG and temperature about once a second during the
take, because auto-exposure drifts within takes.

## 7. Getting this code onto the laptop (decision D1: release branch)

Nothing is pushed from dev boxes. The sequence, once the laptop is on the tailnet:
1. `wdremote inventory` + `wdremote bundle`: back up the laptop's git state first (ARCH-1).
2. Reconcile any laptop-only commits into the dev branch.
3. Rebuild the launcher so it tracks the **release** branch, keeps a backup ref before any reset, and uses real
   "Keep local / Discard" buttons (ARCH-2/19). It is built on Windows over SSH and replaces the 2026-03-26 exe.
4. Thomas pushes the release branch himself. The new launcher fast-forwards the laptop at the next start.

**Until step 3, never push `main`**: the old exe treats any difference from `origin/main` as an update and
hard-resets the checkout.
