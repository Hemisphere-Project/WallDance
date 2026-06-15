# WallDance — Knob-simplification analysis (TUNING.md Phase E)

**Date:** 2026-06-10 · **Branch:** `p3-motion-simplification`
**Inputs:** `tests/sensitivity.py` (E1) over the Phase-A objective + Phase-B cache,
on the two seed scenarios (`residence1-solo` slots 3 & 4).

> **Scope note (2026-06-12):** this analysis predates the 12-scenario corpus.
> Its per-knob verdicts were since re-measured where it matters — confidence
> span + macro (Phase 2 ⑦), imgsz/model selection (Phase 2b: target 110
> validated, dark-scene target 45, model = largest yolo11 tier in budget,
> per-scene τ moves to Phase 3) — see ROADMAP §4.2. Treat the tables below as
> the historical single-clip-era evidence, not current guidance.
>
> **Forward knob governance + data-fit ranges now live in [OPERATOR_V2.md](OPERATOR_V2.md)
> Track S (governance table) + Track G (the 12-corpus cross-parameter test that re-validates
> these tiers).** This doc stays the historical evidence + the `KNOBS E1/E2/finding #2`
> rationale referenced by code comments.

Serves the ROADMAP north star — *"rig the camera, aim the IR light, press one
calibration button, get robust detection for the whole show, without tuning
knobs per venue"* — by answering, with measurements: **which knobs does a user
actually need, which should Go-Live calibration auto-derive, and which earn no
control at all?**

---

## E1 — Measured sensitivity (one-at-a-time)

Each knob swept in isolation from the project baseline; *impact* = range of the
field-priority score across its sweep; *gain* = improvement available by moving
that knob alone. Lower score = better. **slot 3 scored 0.0 for every knob and
every value** — it is robustly solved — so all numbers below are driven by the
hard slot-4 (single fast aerial dancer) scene.

| knob | impact | best score | @value | gain vs base | tier |
|------|-------:|-----------:|--------|-------------:|------|
| `confidence` | **0.0771** | 0.0640 | 0.25 | 0.0415 | **USER** (sensitivity macro) |
| `crossval_skel_min_kpts` (θ_s) | 0.0275 | 0.0780 | 10 | 0.0275 | internal / calibrate-candidate |
| `mog2_var_threshold` | 0.0132 | 0.0923 | 8 | 0.0132 | CALIBRATE (already) |
| `person_height_px` | 0.0105 | 0.0950 | 200 | 0.0105 | CALIBRATE (already) |
| `crossval_skel_min_conf` (θ_s) | 0.0004 | 0.1051 | 0.55 | 0.0004 | FIXED |
| `mog2_scale` | 0.0000* | 0.1055 | — | 0.0000* | CALIBRATE (co-tune w/ var) |
| `motion_sensitivity` | 0.0000 | 0.1055 | — | 0.0000 | FIXED |
| `crossval_motion_min_ratio` (θ_m) | 0.0000 | 0.1055 | — | 0.0000 | FIXED |
| `tracker_max_age` | 0.0000 | 0.1055 | — | 0.0000 | FIXED |
| `tracker_smoothing` | 0.0000 | 0.1055 | — | 0.0000 | FIXED |

(baseline mean 0.1055; full data in `tests/sensitivity_result.json`, regenerable.)

### Three findings that shape the proposal

1. **`confidence` is the master dial.** It alone moves the score most (drops↔ghosts):
   0.25 recovers slot-4 drops (0.211→0.128), 0.50 makes them worse (0.282). It is
   *not* cleanly auto-derivable — the right point depends on the IR lighting and on
   the operator's drop-vs-ghost preference (ROADMAP P1.3: raise it once IR coverage
   improves). → it is the one knob that earns a user control.

2. **OAT is first-order and missed a real interaction.** `mog2_scale` reads 0.0000
   here, yet it was the biggest win in Phase C's *joint* coordinate descent
   (`scale=0.7` cut slot-4 0.18→0.126). Reason: scale only helps once
   `var=8` wakes the MOG2 silhouette; from the baseline (`var≈16`, inert MOG2)
   changing scale does nothing. **Lesson: use OAT (this file) to decide which knobs
   earn a *user surface*; use `tune.py`'s joint search to actually set values.**
   Do not delete a "0.000" knob from the *code* on OAT evidence alone.

3. **Most knobs are inert on this footage.** `motion_sensitivity`, θ_m, θ_s-conf,
   `tracker_max_age`, `tracker_smoothing` move the score by ≤0.0004. They do not
   earn a user control.

---

## E2 — Proposed minimal user-facing surface (~3 controls)

The goal is **one calibrate button + one preference dial + spatial setup** — no
per-venue numeric tuning.

### Tier 1 — USER (the only things an operator touches)

1. **CALIBRATE button** *(exists)* — one short window, auto-derives the scene knobs
   (Tier 2), apply-then-save. This is the "set" of set-and-forget.
2. **Detection sensitivity** *(new macro, one slider)* — collapses the drops↔ghosts
   dial into a single 0–100 control, **led by `confidence`**. Default = the value
   CALIBRATE picks for the scene; the operator nudges **up** if they see ghosts
   (stricter) or **down** if they see drops (looser). See mapping below.
3. **ROI / framing + exclusion** *(exists, via setup + calibrate's exclusion mask)*
   — spatial, not numeric.

### Tier 2 — CALIBRATE-derived (hidden; set once on Go-Live)

| param | derivation rule | status |
|-------|-----------------|--------|
| `person_height_px` + min/max ratios | median + p05/p95 of YOLO detection heights | **done** |
| `mog2_var_threshold` | empirical background-FP sweep | **done** |
| `mog2_scale` | **recommend: co-sweep with `var`** (they interact, finding #2) under a motion-feed-cost budget — `scale=0.7` raises the dominant motion-feed cost (Phase B), so trade it against the prod-laptop FPS budget | **proposed** |
| `confidence` default | seed the sensitivity macro from the calibrate window (e.g. set so ghost-rate ≈ target), then let the operator nudge | **proposed** |

### Tier 3 — FIXED (remove from any user surface; keep as internal constants)

`motion_sensitivity`, `crossval_motion_min_ratio` (θ_m), `crossval_skel_min_conf`,
`tracker_max_age`, `tracker_smoothing` — ≤0.0004 impact; no control, no exposure.

**Watch:** `crossval_skel_min_kpts` (θ_s) showed 0.0275 impact (`kpts=10` helped
slot-4). Keep it internal for now, but it's a candidate to (a) revisit as a
default or (b) let CALIBRATE consider — pending broader footage before changing a
global default off one scene.

### The "detection sensitivity" macro

One operator-facing slider `s ∈ [0,100]`, default = calibrated midpoint:

* **primary:** `confidence` decreases as `s` rises (more detections → fewer drops,
  more ghosts) and increases as `s` falls (stricter → fewer ghosts, more drops).
* **secondary, only at the high (loose) end:** lower `var` toward 8 to wake MOG2
  cold-blob recovery. **Caveat:** that recovery currently also spawns the
  duplicate/stale-track ghost Phase D exposed — so pair this with the Phase-F
  stale-track suppression before turning it up by default.

This is the single dial the field asked for, and it keeps the operator reasoning
in their own terms ("too many ghosts" / "losing the dancer"), not in `var`/θ units.

---

## Caveats (what would change these conclusions)

- **Corpus.** slot 3 is trivial (0.0 everywhere); slot 4 is the only signal. The
  ranking is essentially "what helps one hard aerial scene." Multi-dancer /
  `yolo_first` / YOLO-dropout / small-far footage could promote currently-inert
  knobs (θ_m and the tracker params plausibly matter with swaps/occlusion). The
  Tier-3 "FIXED" verdict means *hide from the user*, **not** *delete from code*.
- **OAT vs joint.** Finding #2: pruning decisions from OAT; value-setting from
  `tune.py`. `mog2_scale` is hidden from the user (calibrate-derived) precisely
  because it only pays off jointly with `var`.
- **`confidence` is lighting-coupled.** Its best value tracks IR coverage
  (ROADMAP P1.3), which is why it is a calibrate-seeded, operator-nudgeable macro
  rather than a fixed number.
