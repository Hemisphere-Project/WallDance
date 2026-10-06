# IR markers: Phase 0a analysis runbook (MRK-1 / MRK-2)

**Date:** 2026-10-06 · **Status:** 🟢 tooling built and tested on dev37, not yet on the laptop · **Spec:** [audit-2026-10/02-ir-markers.md](audit-2026-10/02-ir-markers.md) §6 (protocol, M1–M9) and §7 (harness).

The marker takes are recorded on the prod laptop, which is often reachable only over 4G. So **the analysis runs on the laptop** and only small JSON summaries and contact sheets come back. The same commands also run locally on dev37 after a `wdremote pull`.

| Piece | Where | What |
|---|---|---|
| Harness CLI | `tmp_analysis/marker_eval.py` | Modes `info`, `floor`, `assoc`, `centroid`, `occlusion`, `sweep`, `all`, `phase0a`; synthetic injection; PASS / FAIL / NO-GO per §6.3 metric |
| Harness library | `tmp_analysis/marker_evallib.py` | Detector (§2.2), glint map, persistence, Tier-A association (§3.1), metric accumulators, gap study, gates, `.meta` v2 parsing, contact sheets |
| Model library | `application/src/core/marker_model.py` | Offset-vote estimator, unlabelled slot tracking, R(n, k) noise table → Kalman R, marker physics, disc/streak renderer. Pure numpy; for the future fusion code too |
| Tests | `application/tests/test_marker_model.py`, `test_marker_eval.py` | 39 fast tests (~10 s, no GPU) + 1 GPU test opt-in with `WD_RUN_REPLAY=1` |

---

## 1. Remote invocation (laptop, any link)

`wdremote py` uploads the script **and every `--with` file into one flat scratch dir**, runs it with the laptop venv (cwd `application/`), and fetches `$WD_REMOTE_OUT` back to `tmp_analysis/remote-runs/<stamp>/out/`. **`--with` must come before the script**: everything after the script name goes to the script.

```bash
WITH="--with tmp_analysis/marker_evallib.py --with application/src/core/marker_model.py"
EVAL="python extra/wdremote.py py $WITH tmp_analysis/marker_eval.py --"
P=markers-0a-<venue>                      # the Phase-0a project name on the laptop
POSES=../tmp_analysis/markers0a           # per-take pose store on the laptop (cwd = application/)
```

- The harness finds the repo root from the cwd, not from its own path, so it works from the scratch dir.
- The uploaded `marker_model.py` takes precedence over the checkout's copy, so the laptop does **not** need this branch.
- `summary.json → helpers` records which files were imported.

### 1.1 Did the takes land, and with provenance? (seconds, ~2 KB back)

```bash
$EVAL info --project $P
```

One line per take: frames, real fps, codec, size, exposure, gain, AE/AG, IR offset, camera distance, markers. It also prints the shoot-brief **warnings** (§1.5):
- AE/AG not Off;
- exposure > 25 ms;
- illuminator > 10 cm off the lens axis;
- codec not FFV1;
- recorder drops;
- an old `.meta`.

It lists the **missing** setup-sheet facts (§6.1). For takes without a v2 `.meta`, pass them by hand on any mode: `--rig illuminator_offset_cm=8 --rig camera_distance_m=18`.

### 1.2 The whole §6.2 take plan in one run (good link)

```bash
$EVAL phase0a --project $P --trt --threshold auto --poses-dir $POSES          # + --harness if the costume has one
```

1. Slot 1 (empty stage) runs in `floor` mode. It builds the glint map from the first 600 frames (30 s), measures `max_natural` outside the map, and picks `T = clamp(max(120, 2·max_natural), ≤ 240)`, rounded up to the 10-DN grid.
2. Slots 2–8 then run in `all` mode with that glint map and T.

The result is `phase0a.json` with the aggregated go/no-go, plus one sub-dir per take. Missing slots are skipped. If the slot mapping differs from §6.2, use `--slots floor=1,static=2,holds=3,aerial=4,fast=5,occlusion=6,duo=7,ladder=8`. If the slot holds several takes, add `--take N` (default `-1` = newest).

- **Output:** about 1–1.5 MB for 8 takes (summaries ~20 KB each, sheets 25–95 KB, two overview frames per take). Add `--sheet 0 --overview 0` for ~200 KB.
- **Runtime:** dominated by FFV1 decode and YOLO. Measured on dev37 under load (shared GPU, load ~25):
  - ~5 frames/s with the TRT pipeline;
  - ~11 frames/s when poses are reused.

  A 3-min take (3,600 frames) therefore takes ~12 min the first time and ~5–6 min afterwards. The laptop (RTX 5080, newer CPU) should be at least as fast. The first run fills `$POSES`; later runs reuse it and skip YOLO.

> ⚠️ `wdremote py` keeps one SSH session open for the whole run, so **a 4G drop kills the job**. On a flaky link, use the per-take runs below; each takes a few minutes.

### 1.3 Per take (flaky 4G)

```bash
$EVAL floor     --project $P --slot 1 --threshold auto                                      # glint map, T, M2, M3 (floor)
$EVAL all       --project $P --slot 2 --trt --glint-slot 1 --threshold auto --poses-dir $POSES   # static ref: M1 far bin, M3, M4 table
$EVAL all       --project $P --slot 3 --trt --glint-slot 1 --threshold auto --poses-dir $POSES   # static holds: M5 (and M8 later)
$EVAL all       --project $P --slot 4 --trt --glint-slot 1 --threshold auto --poses-dir $POSES   # aerial: M1, M2, M3, M5, M6
$EVAL assoc     --project $P --slot 5 --trt --glint-slot 1 --threshold auto --poses-dir $POSES   # fast: M1 fast >= 0.90, M3
$EVAL occlusion --project $P --slot 6 --trt --glint-slot 1 --threshold auto --poses-dir $POSES   # self-occlusion: M5
$EVAL assoc     --project $P --slot 7 --trt --glint-slot 1 --threshold auto --poses-dir $POSES   # duo: M7
$EVAL assoc     --project $P --slot 8 --trt --glint-slot 1 --threshold auto --poses-dir $POSES   # exposure ladder: M3 per exposure (camlog)
```

`--glint-slot 1` rebuilds the glint map and auto-T from the empty-stage take each time (300 decodes, a few seconds). Each run returns about 150–300 KB.

**Re-analysis without YOLO** (thresholds, gate, harness slot): once `$POSES` holds a take's poses, every later run on the same window reuses them:

```bash
$EVAL sweep --project $P --slot 4 --glint-slot 1 --poses-dir $POSES --thresholds 120,140,160,180,200,230 --gates 0.1,0.15,0.2,0.3
$EVAL all   --project $P --slot 4 --glint-slot 1 --poses-dir $POSES --threshold 140 --gate-h 0.25 --harness
```

`sweep` gives a recall / FP table and `pr_curve.png`, plus the best operating point with FP ≤ 0.01 / frame.

**If the laptop has no TRT engine** for the take's model/imgsz (`models/<model>_<imgsz>.engine`), drop `--trt`. The pipeline then uses the `.pt` weights (slower). **If a TRT detect cache exists** for the window (`tests/detect_cache.py build`), `--poses cache` replays it through the tracker with no YOLO and no video decode. Caches are about 0.6 MB per frame, so pose dumps (`--poses-dir`, about 1–2 MB per take) are the better store.

## 2. Local equivalents (dev37)

After `python extra/wdremote.py pull projects/$P` (see [REMOTE_OPS.md](REMOTE_OPS.md) §3):

```bash
cd application
.venv/bin/python ../tmp_analysis/marker_eval.py info    --project $P
.venv/bin/python ../tmp_analysis/marker_eval.py phase0a --project $P --trt --threshold auto --poses-dir ../tmp_analysis/markers0a
.venv/bin/python ../tmp_analysis/marker_eval.py all     --project $P --slot 4 --trt --glint-slot 1 --threshold auto --poses-dir ../tmp_analysis/markers0a
```

- Without `--out`, results go to `tmp_analysis/marker_eval_out/<mode>_<take>_s<start>/`. Under `wdremote`, they go to `$WD_REMOTE_OUT`.
- A worktree needs `projects`, `models` and `application/.venv` symlinked to the main checkout.
- Dry-run the remote path on dev37: `python3 extra/wdremote.py --local --host local --root $PWD py $WITH tmp_analysis/marker_eval.py -- info --project $P`.

## 3. Reading the results

`summary.json` has these sections:
- `take`: window, ROI, fps, config source, and the exposure with its source;
- `provenance`: the `.meta` v2: camera (exposure, gain, AE/AG, pixel format, gamma, black level, UserSet), the camera at stop, the camlog min/median/max, the rig sheet, app commit, engine, config subset, `shoot_brief.missing` / `warnings`;
- `detector`: T, glint map, cost in ms;
- `floor`, `assoc`, `occlusion`, `centroid`, `sweep` and `inject`: one per metric family;
- `gates`: PASS / FAIL / NO-GO / N/A / INFO per metric, with the value and a note.

`phase0a.json` aggregates the gates as §6.3 prescribes:
- M1 pools the static, aerial and fast takes; its far bin comes from the static take and its fast check from the fast take;
- M2 combines the floor take's FP/frame with the aerial take's on-dancer blobs;
- M3 pools the saturation of the aerial, fast and static takes and compares it with the floor `max_natural`; the ladder take is reported per exposure;
- M5 and M6 come from the aerial take;
- M7 comes from the duo take.

**Phase 0a GO** = M1, M2, M3, M5 and M6 PASS, plus M9 by eye.

Definitions used (pseudo-ground truth = YOLO keypoints; the sheets are for operator verification):

| | Definition |
|---|---|
| unoccluded marker | skeleton frame (fss = 0), keypoint conf ≥ 0.5 (`--unocc-conf`), inside the analysed ROI |
| M1 hit | any blob within the gate `max(4 px, g·H) + 0.5·streak_len`, g = 0.20 (`--gate-h`). A merged blob serves both slots. `recall_assigned` is the strict one-blob-per-slot variant |
| far / fast | lowest third of H (dancer bbox height) / keypoint speed ≥ 0.2 H per frame (`--fast-h`) |
| M2 | empty stage: non-static blobs (outside the glint map) in a chain seen in ≥ 2 of the last 3 frames, per frame. Pose takes: unassigned blobs on a skeleton dancer as % of marker blobs. Blobs on a coasting (YOLO-gap) track are Tier-B candidates, not FPs |
| M3 | assigned blobs with peak = 255 and `n_sat` ≥ 2; `max_natural` ≤ 0.6·T |
| M5 | per dancer-frame: on skeleton frames, assigned + contested blobs; on gap frames, free blobs inside the track bbox (+ 0.2 H). 0-marker run lengths in frames |
| M6 | `synth_markers.py` method on real blobs. Offsets are frozen at an anchor with all 4 markers assigned; slots are re-associated **unlabelled** through k frames; the vote is compared with YOLO's centroid at t0 + k, reported next to `hold` from the same take. Cells with N < 50 are ignored |
| M7 | a non-contested assigned blob whose nearest extremity keypoint belongs to another dancer |

- **Contact sheets:**
  - `sheet_hits.jpg`: assigned blobs by distance bin (far, mid, near);
  - `sheet_misses.jpg`: unoccluded keypoints with no blob, with the gate circle. **Check these first: a visible but dim streak means T or exposure is the problem; nothing visible means occlusion or a pseudo-GT error;**
  - `sheet_fp.jpg`: persistent non-marker blobs;
  - `overview_f*.jpg`: whole frames;
  - `glint_map.png`;
  - `pr_curve.png` (`sweep` only).
- **The gate for MRK-5:** `assoc.M3_M4.proposed_gate_H` = 1.25 × p95 of the residual blob↔keypoint ÷ H.

## 4. Pre-footage checks (MRK-2), reproduced on dev37

**Synthetic study (§3.0), no video pass.** Markers are simulated at the YOLO keypoints of the audit's pose dumps:

```bash
.venv/bin/python ../tmp_analysis/marker_eval.py centroid --markers keypoints --poses dump:<aerial_s4.pkl>
.venv/bin/python ../tmp_analysis/marker_eval.py centroid --markers keypoints --poses dump:<tangoH_s8.pkl>,<tangoH2_s9.pkl>
```

The output reproduces the audit tables **exactly**: 165 + 198 + 198 cells (aerial, wall climbs, floor) and the kinematics. Re-running tango-H s8 from the recording through the live pipeline (`--project tango-H --slot 8 --poses pipeline`, PyTorch path, project config, as `pose_dump.py` did) gives the same 258/258 cells. For example, aerial n = 4 at k = 1/5/10/20 gives 0.043/0.111, 0.063/0.134, 0.074/0.171, 0.102/0.185 H, against hold 0.068/0.148 … 0.362/1.043; the unlabelled swap rate is 26 %. `centroid.r_table` is that scene's paste-ready `marker_model.R_TABLE` row set. With `--poses pipeline` / `cache`, the same command extends the table to any corpus scene.

**Floor (§7.2).** `floor --project 3_TANGO_HANGAR-whitebg2 --slot 4 --stride 3 --roi none` gives mean 5.11, natural max median 22 / max 41, p99.99 15 / 16, and 0 % candidate frames at T ≥ 160. These are the §7.2 values.

**Synthetic injection on marker-less footage.** `--inject disc|streak` renders markers at the YOLO wrist and ankle keypoints (`--inject-harness` adds the hip midpoint), interpolated through YOLO gaps. A streak's length is keypoint speed × exposure × fps. Each pixel gets `bg·(1 - s) + level·s`, where s is the exact share of the exposure the swept disc covers it (dwell model §1.6). The summary adds the detector's true recall against the injected ground truth. On `hangar-aerial`:

| Run | Exposure / level | M1 recall (fast) | M3 saturated | Gate |
|---|---|---|---|---|
| full take, 5,088 frames | 25 ms / 2000 DN (on-axis) | 0.997 (0.985) | 96.8 % | M1/M2/M3/M5/M7 PASS; M6 n4 k10 0.061/0.161 vs hold 0.193 (fails only n2/n3 at k = 20) |
| window 1500+300 | 49 ms (corpus setting) / 2000 DN | 0.966 (0.851) | 90.9 % | M1, M3 FAIL: 22–53 px streaks |
| window 1500+300 | 49 ms / 400 DN (~1° off-axis) | 0.374 (0.113) | 47.1 % | M1 NO-GO, M5 NO-GO |

These match the §1.6 physics: keep exposure ≤ 25 ms and the illuminator on the lens axis. The rendered streak length comes from the keypoint motion between frames. Each marker is paired with the nearest previous marker, so YOLO's L/R label flips do not count, but YOLO keypoint jitter still does. The injected streaks are therefore longer than real ones, and these numbers are conservative.

`--write-video <take>.avi` also writes the injected take (FFV1 plus a v2 `.meta` marked `camera.source = synthetic`). This is the pre-footage input for the detector (MRK-3) and the fusion replays (MRK-6, M8). Running `phase0a` on such a synthetic project (slot 1 = a marker-less take, slot 4 = an injected one) exercises the whole plan end to end.

## 5. `core/marker_model.py` in brief

```python
from core import marker_model as mm
mm.track_centroid(kpts, conf)                    # the tracker's measurement definition
om = mm.OffsetModel.learn(C, markers, H)         # Tier A: offsets o_i = C - m_i (update() = EMA)
om.estimate(markers_now, hold=C_last)            # Tier B: offset vote (median with >= 3; n = 1 -> 50/50 with hold)
mm.SlotTracker(markers).step(points)             # unlabelled slot re-association through a gap
mm.marker_R(n, k, H, scene="aerial")             # Kalman R (px^2) from the R(n,k) table; sigma = p50/1.1774·H
mm.render_marker(gray, (x, y), diameter=d, level=2000, velocity=(vx, vy))   # synthetic disc / streak
```

## 6. Limits

- **The ground truth is YOLO's.** Keypoints are noisy (p90 0.37 H per frame on extremities), so M1/M6 are conservative, and the gate must come from the Phase-0a residuals. A hand-clicked set on takes 5–7 (§7.1) would calibrate the proxy; it is not built.
- **M8 (Phase 2, replay with markers on vs off) and M9 (visual) are not automatic.** M4 is reported as a table; choosing the marker size needs the test-card take.
- **Only the GPU/TRT detect-cache format is supported.** Pre-Track-P CPU caches are refused with a rebuild hint.
- **Detector cost** on dev37 under load: empty frame ~0.4–0.6 ms, 8 streaks ~3.5 ms single-thread. On the laptop, `detector.cost_ms` measures the MRK-3 budget (p95 ≤ 2 ms).
