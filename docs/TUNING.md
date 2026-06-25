# WallDance — Autonomous Detection-Tuning Capability (plan)

> **Status: toolchain built (A–F done) and in use.** The cross-parameter test that builds on
> this harness (scoring/replay/detect_cache/tune/sensitivity/overlay) is **Track G in
> [ROADMAP.md](ROADMAP.md) §3** (G1–G6 done). This doc = the methodology + tool inventory reference.

**Date:** 2026-06-09
**Status:** **ALL phases A–F DONE** (see "## Progress" below). The end-to-end
loop — trustworthy objective → fast cache → search → visual diagnosis → knob
analysis → algorithm fix → re-measure — is built and validated on
residence1-solo. The main thing left is **broader labelled footage** (§2 corpus
gap), which gates how far the conclusions generalise. Plan / handoff doc —
written so a *fresh* Claude session can execute it without the originating
conversation's context.
**Branch:** everything is now on **`main`** (the `p3-motion-simplification` and
`ux-track` branches were merged 2026-06-10) — `motion_model.py`, the scored gate,
the replay harness and the goldens are all in the main tree. The broader-footage
corpus is sequenced as the **top priority** in ROADMAP §4.1 (operator-arbitrated).

---

## 0. Goal

Make the agent able to, against a recorded scene/slot, **run the software,
score detection quality against ground truth, search the setting space, diagnose
failures visually, and recommend (a) good per-scene settings, (b) a simplified
user-facing knob set, (c) algorithm robustness fixes** — autonomously, then
report. The user will point it at various recorded situations.

---

## Progress (as built — 2026-06-09)

**Phase A (trustworthy objective) — DONE.**
- `tests/scenarios/{residence1-solo_slot3,_slot4}.json` + `scenarios/README.md`
  (schema). Ground truth **visually verified** against the footage (montage +
  strong gamma/CLAHE brightening of the near-black IR), not inferred from the
  detector. Both **N=1 constant**. Slot4 has three genuine drop regions (dancer
  present, unreported): abs **1515-1524, 1643-1654, 1764-1786**; slot3 clean.
- `tests/scoring.py` (pure stdlib, 14 unit tests in `tests/test_scoring.py`).
  Scores the **OSC-faithful reported-count** timeline (`len(process() tracks)`)
  vs N. Field-priority weights: drop+ghost dominate, fragmentation lower, ID
  instability lowest and **bounded** (swaps acceptable). `warmup` excludes the
  mid-recording cold-start confirmation lag (≈`TRACK_WARMUP_THRESHOLD`=15).
  `score_multi()` aggregates across scenarios (mean+worst) for C3.
- `tests/replay.py` gains `--scenario/--timeline/--score` (timeline split out of
  the lean golden summary → P3 goldens unchanged).
- **Measured:** slot3 score **0.0** (clean); slot4 **0.211**, drop_rate 0.158
  (45 missed dancer-frames / 2.28s, longest drop 1.17s), ghost_rate 0. On this
  footage **drops are the residual pain, ghosts are already controlled** in the
  OSC output — matches §2's prediction.

**Phase B (YOLO detect-pass cache) — DONE.**
- `pipeline._track_detections()` extracted (post-YOLO path) so live + cache share
  it; `_cache_capture` hook records pre-gate detections + raw motion gray.
- `tests/detect_cache.py`: `build` (one full pass) + `replay` (skip YOLO).
  `replay.py --cache` builds-on-first-use then replays. Cache key = YOLO
  front-end params; gate/motion/tracker stay tunable. Caches live in
  `tests/cache/` (gitignored). Opt-in equivalence test
  `tests/test_detect_cache.py` (cache replay **== full replay**, frame- and
  metric-identical incl. IDs).
- **Determinism fix:** `DancerTrack._id_counter` (global) is now `reset()` at
  every replay entry point — required for any in-process multi-replay (C's
  search) to get stable IDs.
- **Bonus, bit-identical, helps the LIVE show:** vectorised
  `MotionDetector.frame_diff_blob_in_bbox` (textured-wall frame-diff fragments
  into ~1e3 components/bbox → it called `np.linalg.norm` ~1e6×/replay, every
  frame in production). Golden runtime 102s→67s from this alone.
- **Speed finding that revises the plan's premise:** YOLO does *not* dominate
  this footage — the **motion MOG2/frame-diff feed does**. Cache replay is
  ~53 ms/frame vs ~125 ms/frame live → **~2-3×, not 10-100×**. For a faster
  search the next lever is the motion feed (tiered motion cache when sweeping
  only tracker params, or further `motion_detector`/`motion_model` vectorisation)
  — not YOLO. Profile lives in the Phase B commit message.

**Phase C (search harness) — DONE.**
- C1: θ_s/θ_m levers wired into the harness `_build_processor` (config keys
  `crossval_skel_min_kpts/conf`, `crossval_motion_min_ratio`); `replay.py --set
  KEY=VALUE` for arbitrary overrides (`--var` kept as shorthand).
- C2: `tests/tune.py` — coordinate-descent (default) / grid / random; eval via
  B's cache (`reuse_grays` memoises the PNG decode); ranked output; best config
  written. Each eval resolves the cache for its *merged* config, so post-YOLO
  levers reuse one cache/scenario and a front-end param (e.g. `confidence`)
  rebuilds on demand. Search logic unit-tested (`tests/test_tune.py`, no GPU).
- C3: `scoring.score_multi` mean across scenarios is the objective → can't win by
  overfitting one scene.
- **Demonstrated:** coord over both seeds, 25 evals, converged in 1 pass:
  baseline mean 0.1055 → best **0.0629** (`mog2_var_threshold=8, mog2_scale=0.7`);
  slot4 0.211→0.126, slot3 stays 0.0 (generalises). Confirmed on the full
  pipeline. **It's a drops↔ghosts TRADE** (slot4 missed 45→6 but ghosts 0→15):
  aggressive cold-blob seeding recovers the fast aerial dancer *and* spawns
  spurious 2nd tracks; equal weights make it a net win. **Don't ship blind** —
  (a) confirm the drop/ghost weighting (`tune.py --weights`), (b) Phase-D overlay
  to verify the recovered/ghost frames are the real dancer, (c) `mog2_var` is
  normally P2-calibration-driven and `mog2_scale=0.7` raises the dominant
  motion-feed cost.

**Phase D (diagnosis & visualisation) — DONE.**
- D-prep: opt-in per-frame track spatial detail via `replay.per_frame_record`
  (track_details flag, default off → A/B timelines + cache-equivalence unchanged).
- D1+D2: `tests/overlay.py` — authoritative tracks from the cache replay (or
  `--full`) drawn on the brightened recording (boxes + IDs + bridged marker +
  ROI + `rep/N/status` header); auto-flags count≠N frames (warmup-aware) into a
  contact-sheet montage (+context) and optional MP4. `tests/test_overlay.py`
  (flag logic, no GPU). Outputs in `tests/overlays/` (gitignored).
- **Closed the loop on the C tradeoff (visually verified):** slot4 baseline = 45
  DROP frames (dancer present, no box). Tuned (`mog2_var=8, mog2_scale=0.7`) = 21
  flagged (6 drop + 15 over). The 15 ghosts are a **real spurious track**, not a
  score artifact: a duplicate spawns on the dancer (~f1766) then **freezes on
  empty wall at (681,995) and goes bridged** while the real dancer (track 1)
  swings right (690→811→913 px). Root cause now visible → **Phase F target:
  stale-track / duplicate suppression after aggressive cold-blob seeding.**

**Phase E (knob-simplification) — DONE.**
- E1: `tests/sensitivity.py` — OAT sweep over the objective on B's cache, ranks
  knobs by score impact (reuses tune's Tuner; unit-tested). Measured (seed
  scenarios; **slot3=0.0 for every knob → slot4 drives all**): `confidence` 0.077
  (master drops↔ghosts dial, best @0.25) ≫ `crossval_skel_min_kpts` 0.028 >
  `mog2_var_threshold` 0.013 > `person_height` 0.011 ≫ everything else ≤0.0004
  (`mog2_scale`, `motion_sensitivity`, θ_m, `tracker_max_age/smoothing` = 0.000).
- **Methodology finding:** OAT is first-order and MISSED `mog2_scale` (0.000 alone,
  but the biggest win in C's *joint* search — only helps once `var=8` wakes MOG2).
  → OAT decides which knobs earn a *user surface*; `tune.py` joint search sets
  values; never delete a "0.000" knob from code on OAT alone.
- E2: `docs/KNOBS.md` — proposes the ~3-control surface: **USER** = CALIBRATE
  button + one "detection sensitivity" macro (confidence-led) + ROI/exclusion;
  **CALIBRATE-derived** = height/ratios + var (done) + recommend var↔scale
  co-tuning; **FIXED** = the inert knobs (hide from user, keep as constants).

**Phase F (robustness payoff) — DONE.**
- Fixed the failure mode D exposed: the duplicate/stale-track **frozen ghost**.
  Diagnosis (reading the actual track objects, not just counts): the ghost is
  *not* skeleton-less — it had the dancer earlier (hits~50), then lost it and
  **froze on the wall**, sustained by recurring cold-blob matches. Discriminator
  = skeleton-stale **AND** stationary (a gap-bridged dancer moves; a still dancer
  gets skeletons).
- Fix (`tracker.py`, **report boundary only** → golden metrics untouched):
  `DancerTrack._frames_since_skeleton` (predict++ / update-resets-on-pose);
  `_collect_confirmed_tracks` suppresses skeleton-stale + slow tracks
  (`TRACKER_GHOST_SKELETON_AGE=3`, `TRACKER_GHOST_FROZEN_SPEED_RATIO=0.03`,
  master `TRACKER_REPORT_REQUIRES_SKELETON`). Unit-tested
  (`tests/test_tracker_ghost_gate.py`).
- **Re-measured:** slot3 0.0→0.0, slot4 baseline 0.211→0.211 (gate inert on tame
  configs — golden still passes), slot4 `var8/scale0.7` 0.126→**0.111** (ghost
  frames 15→6; ~4 ghost-*masked* "recoveries" now honestly counted as drops,
  6→10). Overlay confirms the frozen-wall ghost is gone.
- **Honest residual:** a duplicate still *moving* toward its freeze point (~6
  frames) is spared — count+speed can't separate it from a real moving dancer;
  needs spatial GT (A3) or duplicate-track merging.  **Resolved 2026-06-11:**
  the takeover merge (`tracker._merge_takeover_duplicates`, ROADMAP §4.2
  Phase 2 ②) absorbs these via the pair co-fed-history discriminator.

**The loop is complete on this footage.** Highest-leverage next input = **broader
labelled footage** (§2: YOLO-dropout, multi-dancer, `yolo_first`, small/far) —
without it, C/E/F conclusions (and the FIXED-knob verdicts in `docs/KNOBS.md`)
are slot4-specific. Then: productise the known-N calibration (A+C in the Go-Live
UI), and consider duplicate-track merging for the moving-duplicate residual
*(shipped 2026-06-11 as the Phase 2 ② takeover merge)*.

## 1. What already exists (the P3 foundation — reuse, don't rebuild)

| Tool | What | Invocation |
|------|------|-----------|
| [tests/replay.py](../application/tests/replay.py) | Headless replay of a recording through the real GPU `process()` path (Track P: GPU-only; pass `--trt` for the show path) → drop/ghost/swap/track metrics (via `analyze_session.collect_stats`). Applies the project's tuned config like `_apply_config_without_model`. Self-bootstraps `LD_LIBRARY_PATH` (system cuDNN shadows torch's and aborts otherwise). | `python tests/replay.py --project residence1-solo --slot 4 --start 1500 --frames 300 [--var 16] --out /tmp/x.json` |
| [tests/known_n.py](../application/tests/known_n.py) | Known-N per-project calibration (K1): joint coord-descent over τ / θ_s / θ_m / `tracker_max_age` vs a project's labelled scenarios on the GPU+TRT cache (oracle-seeded τ), writing a timestamped project save. Also reachable from the phase-④ "Tune (known-N)" GUI button. | `python tests/known_n.py --project <name> [--dry-run]` |
| [tests/test_regression_replay.py](../application/tests/test_regression_replay.py) | Opt-in golden regression (slots 3 & 4). | `WD_RUN_REPLAY=1 python -m pytest tests/test_regression_replay.py` |
| [analyze_session.py](../application/analyze_session.py) | JSONL session log → stats/report (`collect_stats`, `classify_tracks`: real ≥20 hits / marginal 5–19 / ghost <5). | `python analyze_session.py <session_dir> --json` |
| `tests/golden/*.json` | Golden metric snapshots (the current "intended behavior" baseline). | — |

**Environment:** venv at `application/.venv`. Two verified hosts (2026-06-10):
the Linux dev box (python3.10, torch 2.12+cu130, RTX 3090; `source
.venv/bin/activate`, cuDNN `LD_LIBRARY_PATH` fix automatic inside `replay.py`)
and the **Windows show laptop** (python3.12, torch 2.12+cu130, RTX 5080;
`.venv/Scripts/python.exe`, the LD bootstrap is a no-op) — the full harness
(survey, replay, scoring, calib sweeps, goldens) runs on both.

---

## 2. Carry-over facts the fresh session MUST know

- **Field priorities (operator-confirmed):** ghosts, drops, and setup time are
  the real pains; **ID swaps are acceptable** (OSC needs positions + rough
  identity only). Score functions must weight ghosts/drops ≫ swaps.
- **Metrics today are *proxies*.** `avg_detections` falling can be ghost-removal
  *or* dancer-loss; `zero_detection_frames` rising can be ghost-removal in
  dancer-absent frames *or* real drops. We disambiguated by hand. **This is
  exactly why Phase A (ground truth) is the keystone** — don't tune against
  proxies.
- **Footage status (updated 2026-06-10):** the corpus was annotated (38 slots,
  `projects/CORPUS_NOTES.md`), fully analyzed ([CORPUS_ANALYSIS.md](CORPUS_ANALYSIS.md)),
  and the scenario set **re-founded**: golden trio `hangar-floor`/`hangar-aerial`
  (ex `residence1-solo` slots 3/4 — same files, configs were lost in the project
  reorganisation) + `texture-aerial`, plus 7 tuning manifests + 2 drafts covering
  the multi-dancer / aerial / ghost / small-far / static-person gaps. Manifests now
  **pin a frozen config snapshot + recording fingerprint** (`replay.scenario_config`)
  — never tune against a project-folder config that can be re-saved or renamed.
  Operator GT montage pass still pending on the non-golden manifests; the A–F
  conclusions below (and KNOBS.md FIXED verdicts) re-validate against the broader
  set once it's verified.
- **Current detection architecture (post-P3):**
  - One `MotionModel` ([motion_model.py](../application/src/motion_model.py)) = one
    slow MOG2 (silhouette, bridging-only now) + frame-diff ("moving now?").
  - **Scored gate** (`pipeline._crossval_motion_filter`): keep a detection if
    **strong skeleton** (`θ_s`) **OR** recent **frame-diff motion** (`θ_m`) **OR**
    overlaps a **live track**; else reject. Exclusion-zone ghosts dropped
    upstream (P1.4). `θ_s/θ_m` are `ProcessingSettings` fields
    (`crossval_skel_min_kpts`, `crossval_skel_min_conf`,
    `crossval_motion_min_ratio`) → **calibration-tunable** (this is the Phase-A/C
    objective's main levers).
  - **Frame-diff — not MOG2 foreground — is the ghost killer** (static texture +
    lighting drift = MOG2 foreground but no frame-to-frame change). MOG2 is
    **saturated/inert at varThreshold ≥ 16** on the gamma-only feed; frame-diff
    carries ghost rejection *and* the relay.
  - **Bug #1 fixed:** motion is fed a **gamma-only** gray (fixed, no adaptive
    CLAHE) — feeding adaptive CLAHE re-introduces jitter that reads as fake
    motion. Don't undo this.
  - **varThreshold is calibration-driven** (P2 FP-sweep, per project) and
    self-adapts to the feed; not a hardcoded value to tune.
  - **Bridge:** the presence + frame-diff tiers are **kept on purpose** — the
    harness showed they prevent drops (the #1 pain). Don't collapse them away
    without footage proving it's safe.
- **Deferred (not done in P3):** removing the `MotionModel.detector` compat shim
  (consumer-migration refactor); relaxing the slot-7 swap correctors
  (`TRACKER_MAHALANOBIS_GATE`, `TRACKER_MAX_DISPLACEMENT_RATIO`, etc.).

---

## 3. The plan (phased)

### Phase A — Trustworthy objective (KEYSTONE, do first)
- **A1. Scenario manifest** — `scenarios/<name>.json` per recording: expected
  dancer count `N` (constant or per-frame-range), slot/start/frames window,
  condition tags (lighting, texture, count, dropout, scale, motion-speed). Seed
  with residence1-solo 3 & 4.
- **A2. Scoring function** — collapse per-frame *detected-vs-N* into one
  field-priority-weighted scalar (ghost-rate + drop-rate/missed-dancer-seconds
  dominate; count-error + ID-instability/fragmentation lower) **plus** the
  component breakdown. This replaces eyeballing proxies.
- **A3. (optional) sparse spatial labels** — a few annotated frames (dancer
  centroids) for spatial accuracy / dancer-vs-ghost disambiguation.

### Phase B — Fast iteration (YOLO-output caching, KEYSTONE)
YOLO (yolo11x @1280 ≈ 65 ms/frame) dominates; tracker/gate/motion tuning doesn't
change YOLO output.
- **B1. Detect-pass cache** — run YOLO once per `(model, imgsz, confidence,
  enhance, ROI)` over a recording; cache raw detections + the motion-input grays.
- **B2. Replay-from-cache** — re-run gate + motion + tracker from cache, skipping
  YOLO → **10–100× faster**, making search interactive. (Motion needs the grays;
  cache them. If sweeping only tracker params, the motion outputs can be cached
  too — tiered.)

### Phase C — Search harness
- **C1.** Generalize `replay.py` overrides to arbitrary `--set key=value` (today
  only `--var`).
- **C2.** `tune.py` — grid / random / **coordinate-descent** over a declared
  param space, scoring via Phase A on Phase B's cache; ranked output; best config
  written as a project file.
- **C3.** **Multi-scenario** scoring — rank by performance *across all*
  scenarios so settings **generalize** (avoid the slot-7 overfit sin).

### Phase D — Diagnosis & visualization
- **D1.** Overlay render (headless → annotated MP4 / sampled PNGs: boxes, IDs,
  kept/ghost flags, bridge state) so failures are *visible* and user-verifiable.
- **D2.** Auto-flag count≠N spikes / swaps / long drops → thumbnails + context.

### Phase E — Knob-simplification analysis
- **E1.** Sensitivity sweep: vary each knob in isolation across scenarios; rank
  by score impact (we already saw learn-rate & varThreshold near-inert on some
  footage).
- **E2.** Propose a minimal user-facing knob set + auto-derivation rules + one
  "sensitivity" macro. Serves the ROADMAP "~3 knobs" target and §6 config work.

### Phase F — Robustness loop (payoff)
Run the scenario suite → per-scene + generalizing settings → identify failure
modes (dropout, multi-dancer, scale) → propose *algorithm* tweaks (not just
settings) → re-measure.

**The known-N calibration feature** (a product feature: feed a recording with a
known count, auto-tune `θ_s/θ_m`/exclusion/varThreshold to match N with min
ghosts) falls out of A + C — it's the same objective + search, surfaced in the
Go-Live UI.

---

## 4. Sequencing & how to start

Do **A + B first** — they're the enablers; C/D/E/F are low-value without a
trustworthy objective and fast iteration. Then C → D → E → F.

**Fresh-session kickoff:** check out `p3-motion-simplification`, read this doc
and [ROADMAP.md](ROADMAP.md) §5 (P3) for the current architecture, skim
`tests/replay.py`. Start with **A1 + A2** (scenario manifest + scoring on
residence1-solo 3 & 4), then **B1 + B2** (YOLO cache). Keep using the
replay/golden harness; commit per phase with measured results (the P3 discipline:
every change validated on the scenarios, no proxy-only claims).
