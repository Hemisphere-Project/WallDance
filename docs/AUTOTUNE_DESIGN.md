# WallDance — Auto-tune / auto-detection / settings-exposure design

**Date:** 2026-06-16 · **Status:** DRAFT for operator decision. Grounded in the
12-corpus Track-G findings, the calibration code, and the 2026-06-16 per-project
best-effort batch (which empirically measured what each knob actually does).

The question this answers: *for every adjustable value — what reliably sets it
(formula / measurement / sweep / operator), should the user ever see it, and what
better workflow + measurements + conditional UX falls out.*

---

## 0. The one idea — five buckets of "determinability"

Every knob falls into exactly one of these, and that decides how we treat it:

| Bucket | Meaning | How we should handle it |
|--------|---------|--------------------------|
| **Formula** | a closed-form function of a measurement | compute silently, never show |
| **Deterministic measurement** | read directly off the footage (median/percentile) | compute silently, show as status |
| **Empirical sweep** | no formula — try values, keep the best by *detection* | auto-run a bounded sweep; show result, allow nudge |
| **Operator knowledge** | a fact only the human has (stage, count, taste) | **must stay exposed** |
| **Fixed** | design-time constant, corpus-validated inert | hide in Advanced (read-mostly) |

**The punchline:** only **~7 things are operator-knowledge**. Everything else is
auto-deducible. The current UI exposes ~17 user knobs + a deep Advanced drawer;
the determinability analysis says the live surface can be far smaller.

---

## 1. Parameters in hierarchical order of need

Ordered by the **coupling chain** (each tier depends on the ones above). "Set by"
= the reliable source; "Expose?" = should the operator see/touch it.

### Tier 0 — Frame the world (operator knowledge — must expose, comes first)
| Param | Set by | Expose? |
|-------|--------|---------|
| **Profile** (show/rehearsal) | operator picks the live lighting condition | **Yes — essential** |
| **ROI** (stage crop) | operator draws stage bounds (also the cheapest net-px lever) | **Yes — essential (Phase ①)** |
| **Exclusion mask** | operator paints known dead zones (auto-detect removed, decision 5) | **Yes — essential (Phase ①)** |
| **max_persons** | operator knows the show's dancer count | **Yes — essential** |
| **OSC ip/port** | operator's TouchDesigner host | **Yes — essential** |

### Tier 1 — Sensor (live servo, brightness-driven — auto, live-only)
| Param | Set by | Expose? |
|-------|--------|---------|
| **exposure_us** | servo toward brightness target, capped by blur budget | No — auto; **disable if no live IDS** |
| **gain_db** | servo's 2nd lever after exposure maxes; clip backs it off | No — auto; disable if no live IDS |

### Tier 2 — Enhance (the brightness/contrast chain)
| Param | Set by | Expose? |
|-------|--------|---------|
| **gamma** | **Formula** `seed_gamma(brightness)` → mid-gray, clamp 0.8–4.0 (relaxed 2026-06-16), noise-cap 1.8 | No — auto; show "IR-limited" when it saturates at 4.0 |
| **brightness_threshold** | deterministic from scene brightness regime | No — auto |
| **CLAHE (clahe_clip)** | **Empirical sweep** over dancer frames by detection — *no formula* (two equally-dark scenes wanted opposite values) | No — auto-sweep; nudge in Advanced |

### Tier 3 — Motion floor (background false-positive control)
| Param | Set by | Expose? |
|-------|--------|---------|
| **mog2_var_threshold** | **Sweep** (FP-rate sweep); window/dancer-invariant (G6) | No — auto |
| **mog2_scale** | **Sweep**, jointly with var | No — auto |

### Tier 4 — Dancer-dependent detection (needs dancers in frame)
| Param | Set by | Expose? |
|-------|--------|---------|
| **person_height_px** | **Measurement** — median of detection heights | Status only (drives everything; flags small/far) |
| **height min/max ratio** | **Measurement** — p05/p95 spread | No — auto |
| **yolo_imgsz** | **Formula** — smallest preset meeting net-height target (110 / 45-dark); oversizing hurts | No — auto; show "small/far → tighten ROI" advisory |
| **confidence (Dial A)** | **Seed** (p05 box-conf) + **operator** drops-vs-ghosts taste — cardinal, per-scene, inverts | **Yes — the one live detection nudge** |
| **blur_budget_ms** | **Formula** from p95 dancer speed | No — auto (feeds the servo) |
| **yolo_model tier** | **Sweep** (capacity vs FPS) — advisory only today | Optional (Advanced) |

### Tier 5 — Gap bridging (conditional — only matters on drop-heavy scenes)
| Param | Set by | Expose? |
|-------|--------|---------|
| **motion_sensitivity (Dial B)** | **Sweep**, but **inert** on clean/solo/bright; helps only drop-heavy dark/textured/aerial — never changed a pass/fail | **Hide unless drops detected** (see §3) |

### Tier 6 — Per-scene tracker gates (known-N class — internal)
| Param | Set by | Expose? |
|-------|--------|---------|
| **tracker_max_age, θ_s (skel_min_kpts), θ_m (motion_min_ratio)** | **Sweep**, only matter on multi-dancer/occlusion/static-sitter (G4); set by a future Phase-3 known-N search | No — internal; **θ_s/θ_m have no calib writer yet (gap)** |
| **tracker_intermittent_confirm** | per-scene switch — **documented but unwired** (reads the global) | No — internal; **wiring gap (Track C)** |
| **tracker_swap_correctors** | default off; per-scene re-enable | No — internal |

### Tier 7 — Output (operator preference — expose, no detection coupling)
| Param | Set by | Expose? |
|-------|--------|---------|
| **box-clamp** (default ON) | operator preference (stable reported box) | **Yes — output control** |
| **output_smoothing L** | operator latency-vs-coherence taste | **Yes — output control** |
| **centroid smoothing** | Fixed (0.5) | No — Fixed |

### Fixed (≈50 constants + a few above)
Tracker/Kalman/bridge/warmup/Mahalanobis/OPS constants, `crossval_skel_min_conf`,
NMS/IoU, keypoint-conf floor — corpus-validated inert (G4). Advanced, read-mostly.

---

## 2. What genuinely **cannot** be auto-deduced (the irreducible surface)

Just these — the facts only the human has, plus the one live judgment call:

1. **Profile** — which lighting is live.
2. **ROI** — where the stage is.
3. **Exclusion mask** — where the dead zones are.
4. **max_persons** — how many dancers.
5. **OSC target** — where TouchDesigner listens.
6. **Output prefs** — box-clamp + smoothing L (downstream taste).
7. **Dial A / confidence** — the live drops-vs-ghosts judgment (auto-*seeded*, but the final call is the operator's, and it's the cardinal scene-dependent lever).

Everything else can be set by the calibration engine. That is the whole reduction.

---

## 3. The better workflow — *condition-aware single Calibrate*

Replace the rigid Calib1(empty)/Calib2(dancers) split with **one Calibrate that
measures the scene first, then routes** — running only the sweeps that matter and
surfacing only the knobs that matter.

**Step A — Measure the scene (cheap, all from buffers we already fill):**
brightness, noise σ, **contrast (luma std + p2–p98 spread)**, **background
edge-density (Laplacian)**, **motion-energy (frame-diff density)**, height,
YOLO-drop-rate.

**Step B — Route deterministically:**
- gamma ← brightness formula · var/scale ← FP-sweep · height ← measure · imgsz ←
  formula · blur ← speed formula · confidence ← p05 seed.
- **CLAHE ← detection sweep**, *direction-seeded* by contrast+edge-density (flat-dark → aggressive; textured/edgy-dark → low/off), so the sweep is narrower.

**Step C — Detect conditions and adapt (the new part):**

| Detected condition | How (cheap signal) | What it does |
|--------------------|--------------------|--------------|
| **IR-under-lit** | gamma saturates at 4.0 / single-digit brightness | flag "add IR — brightening exhausted"; stop pushing gamma |
| **Too noisy for MOG2** | var-sweep finds no clean pair | warn-banner; lean on YOLO not motion |
| **Small/far dancers** | imgsz target unmet (height-starved) | rig advisory: tighten ROI / lens — no software fix |
| **Clean / static** | low motion-energy + low drop-rate | **hide Dial B** (provably inert); skip blur calc |
| **Drop-heavy / gappy** | high YOLO-drop rate | **show + seed Dial B higher** (the only place it helps) |
| **Multi-dancer / occlusion** | pooled count >1 / crossings | enable the known-N tracker-gate tuning |
| **No live IDS** | playback / non-IDS source | disable servo controls; surface short-install fallback |
| **Ghost-prone** | high static edge-density / fixed-spot clusters | optional consult-only ghost heatmap for mask painting |

**Step D — Expose only §2** + the auto-results as plain status lines.

**Conditions axis (no-dancers / dancers / known-N / live / recorded):**
- **No dancers (empty/clear stage):** gamma, var/scale, clean-plate, + the live servo.
- **Dancers present (live or recorded):** height, imgsz, CLAHE-sweep, confidence-seed, blur.
- **Known-N (operator gives count):** unlocks the per-scene tracker gates + sharper confidence/threshold decisions.
- **Live-only:** exposure + gain (physically drive the sensor) — everything else is **recorded-OK** (that's exactly the short-install fallback).

---

## 4. Measurements — what to add (all cheap, reuse existing buffers)

We already measure: brightness+CV, noise σ, MOG2 FP-rate, height percentiles,
focus (var-of-Laplacian), clip %, uniformity, FPS, box-conf, dancer speed.

**Add (recommended):**
| Measurement | Cost | Makes more deterministic | Honest limit |
|-------------|------|--------------------------|--------------|
| **Contrast** (luma std + p2–p98) | free | *routes* CLAHE direction; discriminates the two-equally-dark-opposite-CLAHE case | routes direction, not the clip value |
| **SNR** (spread ÷ noise σ) | free | unifies the 3 separate `noise>4.0` gates into one better-conditioned signal | still needs a calibrated cut-point |
| **Background edge-density** (Laplacian) | cheap | ghost-proneness prior → CLAHE direction + mask seeding | prior, not a setter; measure on empty stage |
| **Motion-energy** (frame-diff density) | cheap (half-present) | **gates whether Dial B is even worth showing/tuning** | gates relevance, not a value |
| **Histogram black-floor %** | free | *guards* the gamma decision (under-exposed vs dark-background) | guard, not a setter |

**Reject: white-balance / channel ratios** — the rig is **mono IR**; there is no
chroma (channels are identical). Meaningless here.

> Key honesty: these turn magic-number thresholds and blind sweeps into
> **routers / priors / guards / gates** and shrink the CLAHE search — they do
> **not** turn CLAHE or confidence into closed-form setters. CLAHE still needs the
> detection sweep; confidence still needs the operator.

### 4b. Offline proof results (2026-06-16) — which detectors are actually reliable

Probed all 9 HANGAR/TOGO scenarios (`tmp_analysis/scene_probe_20260616/`) and
cross-checked the cheap signals against the known batch outcomes. Verdict:

**✅ RELIABLE (deterministic — build):**
- **IR-limited / brightening-exhausted** ← `gamma_want = ln(b/255)/ln(110/255) > 4.0`. Fires cleanly on the two genuinely near-black scenes only (hangar 4.65, outdoor-night 6.24).
- **Small/far dancers** ← re-derived `person_height_px` small + imgsz target unmet (whitebg3 = 99 px).
- **Too-noisy-for-MOG2** ← `var_saturated` (exists).
- **No-live-IDS** ← backend check.
- **Dial-B worth showing** ← **YOLO drop-rate** from the calibrate detection pass.

**❌ DISPROVEN (do NOT build as auto-routers):**
- **contrast / edge-density → CLAHE direction** — busted. hangar (spread 11, dark) → CLAHE 6.0; outdoor-night (spread 13, dark) → CLAHE 1.0/off. Near-identical cheap signatures, opposite CLAHE → the per-scene detection **sweep is irreducible**.
- **motion-energy → Dial-B gating** — busted. All scenes are low-motion (0–0.6 %, slow tango) on consecutive-frame measurement, independent of whether Dial B helped. Gate Dial B on **drop-rate**, not motion-energy.

**SNR / black-floor:** secondary; black-floor tracks darkness (refines brightness),
SNR was not a clean discriminator on n=9. Not load-bearing. **WB still rejected (mono IR).**

Net: the condition-detector's reliable inputs are **brightness, detection-pass
drop-rate, re-derived height, var_saturated, and the camera backend** — all
already computed or cheap. The proposed contrast/edge/motion routers are dropped.

---

## 5. Gaps surfaced (things that should exist but don't)

1. **CLAHE detection-sweep engine is UNBUILT** — calibration still seeds a crude
   noise-based 1.5/2.5. This is the single biggest auto-tune gap (the dominant
   drop lever, no formula). *(Proven offline this session; the in-app sweep is the
   productization step.)*
2. **θ_s / θ_m have no calibration writer or UI** — exposed on settings, read in
   the gate, but nothing sets them per scene (the Phase-3 known-N search).
3. **`tracker_intermittent_confirm` is unwired** — documented per-scene but reads
   the global; the aerial/dark win is unreachable per scene (Track C).
4. **No scene-condition detector** — the cheap routing signals in §4 aren't
   computed/surfaced, so the workflow can't adapt or hide inert knobs yet.
5. **gamma seed clamp** — ✅ already relaxed 2.2→4.0 this session.

---

## 6. Decision menu (what we could build, independently)

- **(A) Condition-detector + adaptive UX** — compute the §4 signals once at
  Calibrate; hide Dial B when clean/static, disable servo without live IDS, flag
  IR-limited / small-far / too-noisy. *Highest UX leverage, output-only-ish.*
- **(B) In-app CLAHE detection sweep** — wire the proven offline sweep into
  Calibrate (seeded by contrast+edge-density). *Highest detection leverage.*
- **(C) Slim the live surface to §2** — phase ⑥ shows Dial A + output controls +
  (conditionally) Dial B; everything else becomes auto status + Advanced.
- **(D) Unified condition-aware Calibrate** — fold Calib1/Calib2 into the single
  routed pass (§3). *Biggest, the C-next engine refactor.*
- **(E) Phase-3 known-N tuning + wire θ_s/θ_m + intermittent_confirm** — the
  multi-dancer/occlusion gates. *Helps the failing duo projects.*

---

## 7. Operator constraints + corrected build #2 (2026-06-16)

### 7.1 Operating principles (operator, 2026-06-16) — bind everything below
- **P1 — one calibration per session.** A show's conditions are stable (fixed
  stage, maybe a slight offset between sessions). Calibrate once, it holds; no
  continuous re-tuning.
- **P2 — calibration evidence is PARTIAL.** A rehearsal / show-opening segment
  won't cover the whole choreography or the full dancer count. ⇒ auto-derived
  values are **robust best-effort SEEDS, not final**; use the operator's
  **full-show known-N** (not the sample's observed count) for any count logic;
  always keep the **live nudge** (Dial A) and **Advanced** reachable for the
  situations the sample didn't show.
- **P3 — limited corpus ⇒ don't over-constrain.** Anything that might need
  special tuning for a situation not in the corpus stays **reachable in Advanced**
  (moved off the primary surface, **never deleted or hard-fixed**). "Hide when
  inert" = demote to Advanced, not remove.

### 7.2 CLAHE sweep — the cheap proxy is UNSAFE (proven), use the pass-line sweep
Offline-proving the *mechanism* (not just the value) showed a raw-YOLO sweep over
sparse pooled frames **cannot** reproduce the CLAHE verdict: on outdoor-night it
picks CLAHE 6.0 (both as raw count and count-aware/over-count-penalised), but the
pass-line truth is ~1.0 — because high-CLAHE's failure on noisy scenes is a
**temporal ghost/drop phenomenon through the tracker**, invisible in isolated
frames. (hangar, clean, is fine either way.) The sparse-sweep code was reverted.

⇒ **The in-app CLAHE sweep must run the full pass-line metric** (enhance → YOLO →
motion-gate → tracker → score vs known-N) over a **contiguous recording** — i.e.
exactly what `tests/sweep_project.py` / `replay.py --set clahe_clip --score` do
offline. No cheap shortcut exists.

### 7.3 The Calibrate workflow (operator proposal 2026-06-16, adopted)
**Calibrate → "Capture a segment" (shoot a fresh rehearsal / show-opening clip)
OR "Use a recording slot" (existing footage) → confirm dancer count N → calculate
values.** The segment/slot is the shared input for:
- the **deterministic** derivations (gamma/var/scale/height/imgsz/conf-seed/blur —
  already work over a Calib2 window), AND
- the **CLAHE pass-line sweep** (full pipeline ×{1,1.5,2.5,4,6} vs known-N, run as
  a **background subprocess** like the existing Phase-⑤ dry-run — not a sync apply).

Result = a best-effort **seed** (per P2), applied but **adjustable in Advanced** +
the live Dial A. This generalises: the same "scored sweep over a segment/slot vs
known-N" mechanism is how any sweep-class knob (CLAHE, later the per-scene tracker
gates / model tier) gets tuned in-app.

**Known-N source (decided 2026-06-16):** the operator **confirms N for the
segment** ("how many dancers in this clip?") — the actual count in the
captured/selected footage, used to score the sweep. The full-show max stays
separate (`max_persons`). Auto-estimate from the segment was rejected (P2: a
partial sample under-counts vs the show).

### 7.4 Unifying insight — one segment drives ALL of calibration
A single captured/selected segment is the universal calibration input. ONE
analysis over it yields, together:
- **deterministic** values (gamma/var/scale/height/imgsz/conf-seed/blur) — one pass;
- the **CLAHE pass-line sweep** — ×5 background passes vs N;
- the **condition signals for free**: brightness→IR-limited, height→small/far,
  YOLO-drop-rate→Dial-B-relevance, var_saturated→too-noisy.
So the condition-detector (build #1) and the CLAHE sweep (build #2) are the SAME
pass, not separate features.

### 7.5 Resolved design decisions (operator, 2026-06-16)
- **What the sweep tunes:** **CLAHE × confidence jointly**, with **Dial-B
  (motion_sensitivity)** and later the **per-scene tracker gates** riding for
  ~free. Cost structure: each CLAHE value needs one real YOLO pass (front-end,
  ×5 = the expensive part); capturing those passes at a **low conf-floor** makes
  every **post-YOLO** knob (confidence re-threshold, motion re-track, tracker
  gates) a cheap re-score on top. **Deterministic, NOT swept:** gamma (formula),
  imgsz (formula), person-height (measured), var/scale (FP-sweep), blur (formula).
- **Segment:** fixed-length auto-capture **~20–30 s** (~400–600 frames).
- **Total sweep time:** **~2–4 min** (5 CLAHE passes on GPU+TRT, engine already
  loaded; deterministic pass near-instant; post-YOLO re-scoring adds seconds).
- **Known-N / segment composition:** **constant N throughout the clip** (no
  entrances/exits — they bias the pass-line drop count). **Prelude instruction:**
  *"Have your N dancers all present throughout, moving fast and varied."*
- **UX:** deterministic values shown **immediately**; CLAHE sweep continues behind
  a **modal progress bar**; result card shows the **full CLAHE curve + condition
  flags**; **Dial B** appears on the live surface only when the segment's
  drop-rate flags it relevant (else Advanced, per P3).
- **Accumulate:** **replacement for v1** (within a profile, conditions are stable
  per P1; cross-condition averaging is what profiles separate). Deterministic
  dancer-evidence may still pool as today; only the CLAHE sweep is
  single-segment-replace. Revisit pooling only if replacement misses situations.

### 7.6 CLI bridge PROVEN (2026-06-16) + the consistency principle
`tests/calibrate_segment.py` is the headless proof of the flow (deterministic
derivation + pass-line CLAHE×conf sweep over a segment vs N). **Validated on the
clean case (hangar-floor):** gamma 4.0, height 284, CLAHE 6.0, conf swept 0.65→
**0.55** (score 0.170→**0.056, passes**) — reproduces *and beats* the batch; the
joint confidence sweep adds real value.

**Consistency principle (learned the hard way):** the derivation window must run
at the **gamma the runtime will actually use** (the noise-capped one), not the
uncapped seed. `calibrate_window` now pre-estimates noise from a few consecutive
frames and applies `cap_gamma_for_noise` *before* the window. On clean scenes
this is a no-op (gamma uncapped). On a noisy near-black scene the derived height
drops (outdoor-night 137→52) — and **52 is the correct value**: at the runtime
gamma 1.8 the near-black dancer only partially detects, so a height scaled to 52
*matches what the detector sees*. The batch's 137 was the inconsistency (derived
at a brighter gamma than runtime).

**Known limitation (documented, not chased):** noisy near-black scenes
(outdoor-night/TOGO-night, b≈1.3) are **IR-limited** — height/var derivation is
gamma-unstable and they fail regardless; the fix is **more IR**, not software.
The real Calib1 flow's *height* is already consistent (Calib2 runs post-cap);
only its *var-sweep* still runs at the over-bright window gamma on these
noisy-only scenes — a **minor documented caveat**, not worth invasive engine
surgery (those scenes need hardware anyway).

### 7.7 Next: the in-app Calibrate UI (drives the proven bridge)
Mirror the Phase-⑤ dry-run pattern (subprocess + daemon thread + DPG-safe
`set_value`): Phase-④ buttons [Capture segment]/[Use slot] + N input → run the
segment sweep (subprocess) behind a modal progress bar → result card (CLAHE curve
+ condition flags) → Apply as a profile-aware seed. DPG rule: `set_value` from the
daemon is OK; widget add/delete only on the main thread.
