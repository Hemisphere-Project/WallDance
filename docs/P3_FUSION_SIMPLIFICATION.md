# P3 — Detection-Fusion Simplification (design / analysis)

**Date:** 2026-06-08
**Status:** **Design only — no code changes yet.** Implementation is intentionally
deferred to avoid colliding with the concurrent **P2 "Go-Live scaffolding"** agent
(we share one working tree; see the Collision Map below).
**Parent:** [ROBUSTNESS_PLAN.md](ROBUSTNESS_PLAN.md) §P3.

---

## ⚠️ Collision map with P2 (Go-Live) — read first

There is **one shared worktree** (no isolation), so P3 and P2 must not edit the
same files concurrently. The overlap is large because both touch the motion path:

| File | P2 (Go-Live) likely needs | P3 needs | Verdict |
|------|---------------------------|----------|---------|
| `application/src/motion_detector.py` | `reset()`, `feed()`, a background **noise-σ estimate** during calibration | restructure into a single unified model | **HIGH overlap** |
| `application/src/pipeline.py` | drive calibration through `FrameProcessor`; read detection heights | rewrite the crossval + motion wiring | **HIGH overlap** |
| `application/src/config.py` | add calibration constants | **remove** ~30 crossval/bridge constants | **HIGH overlap** |
| `application/src/app.py` | Go-Live button / state / calibration flow | (none) | P2 only |
| `application/src/tracker.py` | (none expected) | bridge tiers + swap correctors | P3 only-ish |

**Rule for now:** P3 stays as this document. **Do not** edit `motion_detector.py`,
`pipeline.py`, or `config.py` until P2 has landed and is committed — or until we
agree a frozen `MotionModel` API both sides build against (see "Coordination").

---

## 1. What the motion subsystem looks like today

Three conceptually different jobs are tangled across three files and ~90 constants:

1. **Ghost rejection** — "is this YOLO box a real dancer or scenery?"
   → `pipeline._crossval_motion_filter()` ([pipeline.py:1255](../application/src/pipeline.py#L1255)), a **7-step** decision tree: BYPASS → weak-skeleton → MOTION → HYSTERESIS → CONFIDENT → REACQUIRE → REJECT, plus MOG2 warmup, per-cell EMA memory, and death-spiral escape (`_crossval_no_track_frames`).
2. **Gap bridging** — "keep a confirmed track alive when YOLO blinks."
   → tracker `_lazy_bridge_with_motion` → `_bridge_unmatched_with_motion` (Hungarian on contour blobs) → `_bridge_with_local_motion_support` (3 tiers: `extract_local_motion_blob` → presence ratio → `frame_diff_blob_in_bbox`) ([tracker.py:2547](../application/src/tracker.py#L2547)), with progressive Kalman-R inflation + velocity friction + warmup scoring.
3. **Cold detection** (motion-first) — "find dancers YOLO misses entirely."
   → tracker `_fuse_motion_blobs` synthetic detections ([tracker.py:2491](../application/src/tracker.py#L2491)).

Feeding all of this: **two full MOG2 models run every frame** for one signal,
differing only in learning rate —
`bridge_motion_detector` (slow, 0.001) + `crossval_motion_detector` (fast, 0.005)
— in `pipeline._feed_motion_detectors()` ([pipeline.py:1021](../application/src/pipeline.py#L1021), instances at [pipeline.py:353](../application/src/pipeline.py#L353)).

### Why two MOG2 models exist
The jobs want opposite learning rates: **bridging wants SLOW** (a paused dancer
must stay "foreground" for seconds) while **crossval wants FAST** (lighting drift
should be absorbed so it doesn't read as motion). That genuine tension is the
*reason* for the duplication — so collapsing it needs a different signal for the
"is it moving right now?" question, not just one averaged learning rate.

---

## 2. Target design

**One** motion model + **source-weighted measurements** feeding the existing
Kalman/Hungarian tracker — replacing the 7-step tree and the 3-tier bridge with a
single scored gate.

### 2a. Single motion model (`MotionModel`)
Keep **one slow MOG2** (foreground/silhouette for bridging) and answer the
fast "moving now?" question with **frame differencing**, which is *already
implemented* (`frame_diff_blob_in_bbox`, `_prev_raw/_curr_raw`). Frame-diff is
inherently fast-adapting (no learning rate), so it removes the need for the second
MOG2 entirely.

Proposed surface (small, stable — also what P2 can depend on):
```
MotionModel.feed(gray_fixed)            # once/frame; gray decoupled from display CLAHE (bug §5.1)
MotionModel.reset()
MotionModel.noise_sigma() -> float      # for P2 auto-threshold + auto-calibration
MotionModel.foreground_blob(roi, ...)   # MOG2 silhouette (bridging / cold detect)
MotionModel.recent_motion(roi) -> float # frame-diff ratio (ghost rejection / "moving now")
```

### 2b. One scored gate instead of two decision trees
Each candidate detection gets a **motion-evidence score** and each lost track a
**bridge-evidence score** from the *same* model. Ghost rejection collapses to:

> keep a detection if **strong skeleton** (high kpts+conf) **OR** recent motion in
> its box **OR** it overlaps a live track — else reject.

That single rule subsumes today's BYPASS / MOTION / HYSTERESIS / CONFIDENT /
REACQUIRE branches. Bridging collapses to: feed the MOG2 blob (or frame-diff
fallback) as a **position-only measurement with a higher Kalman R**, instead of
three bespoke tiers.

### 2c. Let P1.4 do most of the ghost work
The **auto exclusion mask** (P1 / TRACKING_PLAN "Phase 4", moved upstream) rejects
scenery *by location*, which is where most ghosts come from. Once it lands,
crossval shrinks to "is this detection inside a masked dead zone?" — the elaborate
motion tree becomes largely unnecessary.

---

## 3. Staged, collision-aware implementation plan

| Stage | Work | Touches | Safe to start now? |
|-------|------|---------|--------------------|
| 0 | This doc + P4 regression fixtures from recorded sessions (golden drop/ghost/swap counts) so any refactor is measurable | new files only | **Yes** |
| 1 | New `motion_model.py` implementing the §2a surface over a single MOG2 + frame-diff (no wiring yet) | **new file** | **Yes** (new file ≠ collision) |
| 2 | Route crossval + bridge through `MotionModel`; delete the 2nd MOG2 | `pipeline.py`, `motion_detector.py` | **No — after P2** |
| 3 | Replace the 7-step tree + 3-tier bridge with the scored gate; retire redundant constants | `pipeline.py`, `tracker.py`, `config.py` | **No — after P2** |
| 4 | Relax/disable swap correctors + slot-7 gates (ROBUSTNESS_PLAN §3a) | `tracker.py`, `config.py` | After P2 |

Stages **0 and 1 are collision-free** and worth doing immediately: lock behavior
with fixtures, and build the unified model as a standalone module that nothing
imports yet.

## 4. Coordination with the P2 agent
To let both proceed in parallel, freeze this minimal contract on the motion side
and have **P2 depend only on it** (P3 will preserve it):
`feed(gray)`, `reset()`, `noise_sigma()`, `detect(...)`. If P2 needs the background
noise floor for auto-thresholding, add `noise_sigma()` to the *current*
`MotionDetector` as an additive, non-breaking method — that one small addition is
the only `motion_detector.py` change worth making before P2 lands, and it benefits
both.

## 5. Constants retired once P3 + P1.4 land (for reference)
Most of the `MOTION_CROSSVAL_*` family (bypass/hysteresis/sticky/reacquire/confident
thresholds), the duplicate `MOTION_*_MOG2_LEARN_RATE`, and several
`MOTION_BRIDGE_*` tier knobs collapse into ~3 user-facing values
(motion sensitivity, min blob size, bridge max-frames). Full list to be enumerated
at Stage 3 against the then-current `config.py`.

---

**Bottom line:** P3 is well-scoped and high-value (kills the biggest source of
fragility + ~30 knobs), but its core files overlap P2. Do Stages 0–1 now if
desired; hold Stages 2–4 until Go-Live is committed or the `MotionModel` contract
is agreed.
