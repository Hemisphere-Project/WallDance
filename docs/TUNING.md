# WallDance — Autonomous Detection-Tuning Capability (plan)

**Date:** 2026-06-09
**Status:** Plan / handoff doc. Written so a *fresh* Claude session can execute it
without the originating conversation's context.
**Branch:** this builds on `p3-motion-simplification` (the P3 motion-subsystem
rewrite). Check that branch out first — `motion_model.py`, the scored gate, the
replay harness, and the goldens all live there, not on `main`.

---

## 0. Goal

Make the agent able to, against a recorded scene/slot, **run the software,
score detection quality against ground truth, search the setting space, diagnose
failures visually, and recommend (a) good per-scene settings, (b) a simplified
user-facing knob set, (c) algorithm robustness fixes** — autonomously, then
report. The user will point it at various recorded situations.

---

## 1. What already exists (the P3 foundation — reuse, don't rebuild)

| Tool | What | Invocation |
|------|------|-----------|
| [tests/replay.py](../application/tests/replay.py) | Headless replay of a recording through the real CPU `process()` path → drop/ghost/swap/track metrics (via `analyze_session.collect_stats`). Applies the project's tuned config like `_apply_config_without_model`. Self-bootstraps `LD_LIBRARY_PATH` (system cuDNN shadows torch's and aborts otherwise). | `python tests/replay.py --project residence1-solo --slot 4 --start 1500 --frames 300 [--var 16] --out /tmp/x.json` |
| [tests/test_regression_replay.py](../application/tests/test_regression_replay.py) | Opt-in golden regression (slots 3 & 4). | `WD_RUN_REPLAY=1 python -m pytest tests/test_regression_replay.py` |
| [analyze_session.py](../application/analyze_session.py) | JSONL session log → stats/report (`collect_stats`, `classify_tracks`: real ≥20 hits / marginal 5–19 / ghost <5). | `python analyze_session.py <session_dir> --json` |
| `tests/golden/*.json` | Golden metric snapshots (the current "intended behavior" baseline). | — |

**Environment:** venv at `application/.venv` (python3.10, torch 2.12+cu130, RTX
3090). Always `cd application && source .venv/bin/activate`. The cuDNN
`LD_LIBRARY_PATH` fix is automatic inside `replay.py`; if you invoke torch
directly, prepend `application/.venv/lib/python*/site-packages/nvidia/*/lib`.

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
- **Footage caveat (critical):** the only labeled footage is `residence1-solo`
  slots 3 & 4 — **single dancer, `motion_first`, poor light, mildly textured**.
  On it, the P3 Stage 3b changes (merged modes, source-weighted R, bridge
  simplification) came back **bit-identical** → the **relay, cold-detection, and
  `yolo_first` paths are essentially unexercised**. Broader footage is needed:
  **YOLO-dropout** (dancer present, YOLO blinks), **multi-dancer**, **`yolo_first`**,
  **small/far dancers**. Safe to re-run slots 3 & 4 autonomously.
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
