# WallDance — Check & Test procedure

**Date:** 2026-06-16 · Plain-language test plan for confirming WallDance works
before a show, and for the **joint recorded-case tuning loop** we run together.

There are three kinds of testing here, do them in this order:

1. **Desk checks** — at the computer with recorded footage, no camera needed.
2. **Joint recorded-case test** — the repeatable "watch → adjust → re-run" loop
   on the 12 recorded cases (the heart of tuning; needs no rig).
3. **On-rig checks** — the things only the real camera + dancers can prove.

Companion docs: [NEW_SHOW.md](NEW_SHOW.md) (the live field checklist),
[OPERATOR_V2.md](OPERATOR_V2.md) (design), [OSC_CONTRACT.md](OSC_CONTRACT.md)
(what TouchDesigner receives).

> **All commands below run from the `application/` folder**, using the bundled
> Python: `./.venv/Scripts/python.exe ...`

---

## Part 1 — Desk checks (no camera)

### 1.1 The app starts and the rail works
- Launch the app. Confirm the **phase rail** ① Rig → ② Profile → ③ Aim →
  ④ Calibrate → ⑤ Verify → ⑥ Go Live is across the top.
- Click each phase: the right-hand panel should swap to that phase only, and the
  clicked phase highlights. Each phase shows a one-line plain status.
- Top-right **⚙ Advanced** opens the old full control panel (power-user knobs).
- Bottom **🎞 Recordings** bar shows LIVE/REC + slots + transport, always visible.

### 1.2 Recordings + the Verify dry-run
- Load a recording into a slot, play it back in **STANDBY**.
- Go to **⑤ Verify** → press **Check readiness**. Confirm the rows render with
  ok / warn / fail colours (camera, FPS, TensorRT engine, OSC, calibration age,
  disk, GPU temp). Nothing here blocks Go-Live — it's a pre-flight glance.
- Press **Dry-run last recording**. It replays ~600 frames in the background and
  reports a track/drop/swap summary. Confirm it returns a sensible summary.

### 1.3 Output into TouchDesigner — the two output controls
With a recording playing in **RUN** (YOLO + OSC on) and TouchDesigner listening
on `127.0.0.1:9000`:
- **Box-clamp** (default ON): watch a dancer through a detection gap (e.g. an
  aerial). The reported box should stay a **steady dancer-sized rectangle**, not
  flicker fat/thin. Toggle it OFF to see the old flicker for comparison.
- **Smooth L** (default 1): raise it 1 → 6. The box should get **calmer** but lag
  more (each step ≈ 50 ms more delay; the on-screen preview never lags). Pick the
  L that looks good to your generative video. `/walldance/meta/latency_ms` tells
  the consumer how far behind real-time the stream is.

### 1.4 The two dials re-anchor honestly
- In **⑥ Go Live**, move **Dial A (Drops↔Ghosts)** and **Dial B (Gap-bridging)**.
  Both sit at **50 = calibrated**.
- Open **⚙ Advanced**, change the raw `confidence` (or `motion_sensitivity`)
  slider directly. Confirm the matching dial **re-centres at 50 with a toast** —
  it must never silently disagree with the applied value.

### 1.5 The automated safety net (run before trusting any change)
- Unit suite (fast, ~8 s): `./.venv/Scripts/python.exe -m pytest -q`
  → expect **342 passed, 7 skipped**.
- Golden replay (needs footage, which is present on this machine):
  `WD_RUN_REPLAY=1 ./.venv/Scripts/python.exe -m pytest tests/test_regression_replay.py -v`
  → the golden trio (`hangar-floor`, `hangar-aerial`, `texture-aerial`) must
  match their baselines. Run this after **any** detection/tracking change.

---

## Part 2 — Joint recorded-case test (the tuning loop)

This is the protocol for "test together on recorded cases and adjust". It needs
**no rig** — all 12 cases are recorded and on this machine.

### Step A — Baseline: where does each case stand?
Run the scored sweep over all 12 cases on the **show path** (GPU+TRT):

```
./.venv/Scripts/python.exe ../tmp_analysis/baseline_20260616/sweep.py
```

This prints, and writes to `tmp_analysis/baseline_20260616/table.md`, a per-case
table: **drop-rate / ghost-rate / longest-drop vs that case's pass line →
PASS / FAIL**. PASS cases are done; FAIL/borderline cases are the work list.

To score a single case (e.g. one that failed):
```
./.venv/Scripts/python.exe tests/replay.py \
    --scenario tests/scenarios/hangar-aerial.json \
    --start 1500 --frames 300 --trt --score
```

### Step B — For each failing / borderline case (together)
1. **I report the numbers** (drop / ghost / longest vs the limit) and what's
   driving the miss (drops vs ghosts).
2. **You watch the behaviour** — play that recording in RUN and watch the OSC /
   skeletons / box in TouchDesigner.
3. **We nudge one lever** and re-score the same case:
   - losing the dancer (drops too high) → raise **Dial A** / **Dial B**, or for
     dark scenes the real fix is **CLAHE** (see roadmap — auto-tune being built).
   - too many ghosts → lower **Dial A**, or **paint a mask** over the dead spot.
   - You can test a knob without touching the UI via replay:
     `... --set confidence=0.4 --set motion_sensitivity=0.7 --score`
4. **Re-run Step A** to confirm the change didn't regress the cases that passed.

### Step C — Go / no-go
You call each case **go** or **not-go** against its pass line. The aim is every
A/B case green; S (stress) cases have no hard line — judgement call.

---

## Part 3 — On-rig checks (camera + dancers)

Only the real rig can prove these:

- **① Rig & mask** — mount camera + IR; **manual focus** from the phone monitor;
  draw the stage ROI; **paint dead zones**. Confirm masked cells stay dimmed at
  all times and survive a recalibration.
- **③ Aim loop** — on a clear stage press **Aim**, read brightness/blur/scene
  report, adjust IR + focus, press again. Confirm it's a clean iterative loop and
  the report is readable.
- **④ Calibrate** — (A) record a rehearsal run → review pool → Apply; and the
  **short-install fallback (B)**: skip recording, run the dancer pass live on
  show-open as dancers enter, re-Apply as evidence builds. Confirm it warns if ③
  never ran.
- **⑥ Live dials feel** — under real lighting, do A/B feel right and is "only
  nudge a dial or two" actually enough?
- **Recovery** — pull the USB cable mid-run; confirm the camera auto-reconnects
  and the watchdog alerts (this is the one ops path unit tests can't reach).
- **On-rig recording session** — capture ghost-heavy / multi-dancer / dropout
  footage on the real IDS+IR rig to grow the corpus (everything numeric re-fits
  against real rig footage).

---

## Reference

### Scene-class pass lines
| Class | Scenes | Drop | Ghost | Longest drop |
|-------|--------|------|-------|--------------|
| **A** indoor rigged | hangar, white-bg, texture | ≤ 0.05 | ≤ 0.05 | ≤ 1.0 s |
| **B** outdoor / uncontrolled | outdoor-night/sitter, dark-crowd | ≤ 0.10 | ≤ 0.15 | ≤ 2.0 s |
| **S** stress | facade-ghosts | no hard line | — | — |

### The 12 recorded cases & what each stresses
`hangar-floor` (easy solo) · `hangar-aerial` (aerial drops) · `texture-aerial`
(aerial + textured bg) · `texture-duo` (two dancers + ghosts) · `texture-wallhang`
· `white-duo` (duo on white) · `white-walkers` (small/far) · `blur-runner`
(defocus) · `outdoor-night` (dark) · `outdoor-sitter` (static person) ·
`dark-crowd` (dark + crowd) · `facade-ghosts` (ghost flood, stress).

### Commands cheat-sheet
| Goal | Command (from `application/`) |
|------|-------------------------------|
| Unit tests | `./.venv/Scripts/python.exe -m pytest -q` |
| Golden replay | `WD_RUN_REPLAY=1 ./.venv/Scripts/python.exe -m pytest tests/test_regression_replay.py -v` |
| Baseline sweep (all 12) | `./.venv/Scripts/python.exe ../tmp_analysis/baseline_20260616/sweep.py` |
| Score one case | `./.venv/Scripts/python.exe tests/replay.py --scenario tests/scenarios/<name>.json --start <s> --frames <n> --trt --score` |
| Try a knob on a case | add `--set confidence=0.4 --set motion_sensitivity=0.7` |
