# Tracking robustness — the next leap (IR markers) · weighted perspective + plan

**Date:** 2026-06-16 · **Status:** 🟡 DIRECTION AGREED, de-risk-first. Gated on a physical
spike (Phase 0a, operator-owned). Not built. Companion analysis: `tmp_analysis/marker_spike.py`.

## Context — the question this answers

WallDance reliably struggles to keep a **continuous** track of dancers in hard conditions (static
poses, inverted aerials, textured/bright backgrounds, low-SNR IR). YOLO catches up eventually and
we can shave ghosts / improve bridging, but tracks aren't robust enough for continuity. The
question: **is there margin left tweaking the current system, or do we need a bigger move?**

**Verdict: the current toolset is near its ceiling.** The system is a *tower of compensations*
(CLAHE, gamma, MOG2, frame-diff, cross-val, cold-blob, motion-bridge, frozen-ghost, exclusion
paint) all patching one root weakness — a **COCO-RGB pose model run on IR aerials**. The corpus
shows hard *walls*, not soft gaps: 2/5 scenes "not dial-solvable", CLAHE *hurts* on noisy-near-
black, **static dancers structurally never acquire a track**, and **G3 measured that higher imgsz
*hurts*** on dark/IR (resolution is not the bottleneck). Stacking more post-processing buys little.

**Reframe:** stop making per-frame detection more reliable (near its ceiling); **make the system
robust to unreliable detection** — detection becomes one noisy measurement, not the sole driver.
The real margin is at the **signal source**, not the post-processing middle.

## Pivotal requirements (operator, 2026-06-16)

- **Load-bearing OSC output = position + identity** (the EMA centroid + a stable `track_id`); the
  17-keypoint skeleton is published but **nice-to-have**, not load-bearing. (See `OSC_CONTRACT.md`.)
- **Costume is controllable** — subtle, audience-invisible IR markers are acceptable.
- **Worst pains = drops/discontinuity + ghosts + static-never-acquired** (NOT identity swaps).
- **Usage = a new venue per show, NO on-site training data** (daytime rehearsal is hard to record;
  live at night under IR). So per-venue training is impossible; any model work trains *once* on the
  accumulated corpus.
- **Fine-tune appetite = only if clearly justified.** Test targets = HANGAR + TOGO projects.

## Direction (ranked)

### 1. PRIMARY — IR retroreflective markers
The single move that hits **all three** stated pains and matches the position+identity output:
- **Drops/continuity** → a saturated IR retro-point basically never drops.
- **Ghosts** → a marker is a **positive "this IS a dancer" signal** the system lacks today (it only
  has *negative*/exclusion signals). Texture/glint has no marker → rejected.
- **Static** → a marker is detectable with **zero motion** → the structural static dead-end
  dissolves.

**Why it's safe to bet on:** retroreflection is **directional** — bright back toward the IR source
at the camera; the off-axis audience sees ~nothing. Audience-invisibility is physics, not luck.
**Additive, no regression:** markerless operation = today's pipeline exactly.

**For the dancers.**
- *Material:* 3M Scotchlite-class retroreflective — tape, sew-on fabric, or **retroreflective
  thread** in a seam (least obtrusive); glass-bead or prismatic both return IR strongly.
- *Size:* at the rig envelope (8 mm lens ≈ 5.8 mm/px @20 m, ≈11 mm/px @40 m) a **3–5 cm** marker is
  ~5–9 px near / ~3–4 px far; retroreflectors **saturate**, so even 3–4 px is a solid blob. Use
  ~8–10 cm only at the far end.
- *Placement:* **one marker at harness / centre-of-mass** = the position+identity spine; add a
  **second (front+back)** so a rotation hiding one still shows the other (self-occlusion is the main
  physical risk). Light, sewn/taped to the existing harness, no snag risk.
- *Number/identity:* one per dancer solves continuity/static; **coded constellations** only if
  identity-through-crossings (the parked swap case) is needed later.

**For the software (new `core/marker_detector.py` + fusion in `tracker.py`; OSC unchanged).**
- *Marker stage:* `cv2.threshold` (high) → connected-components → bright-blob centroids. No
  GPU/YOLO; reuse the existing blob infra (`motion_detector.py`).
- *Fix-glint rejection:* the existing **exclusion mask** for static scene reflections + a
  size/brightness floor; optional temporal/coded disambiguation later.
- *Fusion:* marker centroid = **high-confidence measurement** (tiny R) fed alongside YOLO/motion
  into `tracker.update`. A marker-fed track is **confirmed-real** (ghost reject), **acquires when
  static**, and **never drops** while visible. Marker occluded → coast on today's pipeline (no
  regression).
- *Identity:* marker→track by proximity (Hungarian, as today) or decode coded markers.
- *Calibration:* a per-venue marker-brightness threshold (fits the existing calib flow).

### 2. SECONDARY — corpus-trained IR detector (bounded feasibility spike, "only if justified")
The no-on-site-data usage makes this **cleaner**: train **once on the accumulated corpus** (already
IR, varied distance/focus/brightness) → a **domain-general** IR-aerial detector. This *inverts* the
"narrowing" worry — corpus diversity = generalisation, exactly the "new venue every night" need.
Given the large COCO-RGB→IR shift, even a few hundred–~1–2 k labelled frames usually lifts detection
materially. **Because the output is position+identity, precise 17-kpt labels aren't required** — a
better *person/centroid detector* (boxes, not skeletons) is cheaper to label and is what continuity
needs; keep YOLO-pose for opportunistic articulation. Auto-label bootstrap from current
high-confidence detections + manual correction of hard frames. **Ranks below markers** (complements
by lifting the floor; doesn't solve *static* as cleanly). This is the same idea as `OPERATOR_V2.md`
Track D #1, reframed for the position+identity goal.

### Rejected / demoted (with reasons)
- **Constrained dynamics / "pendulum" prior — DEMOTED.** Dance is chaotic: a controlled **drop
  through the rope** is vertical free-fall (no arc model predicts it), sharp reversals violate the
  constraint, and **one track splitting into two** is a *data-association* problem, not dynamics. A
  hard constraint adds mis-prediction risk for small gain; the existing constant-accel Kalman
  already covers smooth *short*-gap prediction; **and markers make gap-prediction moot** (you see
  the marker, you don't predict it).
- **ROI spot re-check — folds in, not standalone.** Re-running the failing YOLO in a smaller box
  re-fails (G3: cropping-for-resolution won't rescue it). Only valuable when *driven by a strong
  cue* — which the **marker** now provides directly. No separate work item.
- **4×640 tiling — OUT** (G3: resolution isn't the bottleneck; 4× cost + boundary-split + stitching
  for ≤0 gain).
- **Radio/UWB/BLE beacons — PARKED** (heavier/invasive on aerial performers, coarse, position-not-
  pose; their one niche is identity-swaps, which is *not* a current pain).
- **Learned/LSTM motion — OUT** (needs training data we lack; physics/markers beat it).

## De-risk-first path
- **Phase 0a — marker physical spike (OPERATOR-OWNED, ~half day) — THE GATE.** Retroreflective
  tape/thread on a test harness, recorded **under the actual show IR illumination**; measure return
  brightness/saturation vs background, smallest reliable size, behaviour at self-occlusion angles,
  visible-light invisibility, and fixed-glint false positives. **Go/no-go on markers.**
- **Phase 0b — marker software prototype (DONE — `tmp_analysis/marker_spike.py`).** Threshold/blob
  detector; reports the bright-blob distribution. Re-run it on the Phase-0a clip for marker
  precision/recall.
- **Phase 0c — fine-tune feasibility estimate (NOT YET RUN, optional).** Auto-label a few hundred
  corpus frames + a tiny train/eval on held-out HANGAR/TOGO → an **effort-vs-gain number** for the
  Secondary direction.
- **Phase 1 — build the validated winner:** `core/marker_detector.py` (marker stage) + fusion into
  `tracker.py` (high-confidence measurement + confirmed-real gate + static acquisition);
  `pipeline.py` detection chain adds the stage; per-venue brightness threshold in the calib flow.
  OSC contract unchanged.

## Phase-0b finding (2026-06-16) — the glint floor is low, markers look separable

`tmp_analysis/marker_spike.py` run on existing **marker-LESS** IR footage, sweeping a high
brightness threshold. The question: how many near-saturated bright spots occur *naturally* (the
false-positive floor a real marker must clear)?

| Scene | brightest natural pixel | near-saturated blobs/frame @ T=245–254 |
|---|---|---|
| HANGAR-texturedbg *(worst, ghost-prone)* | max-gray ~157 (rare 255) | **~97 % of frames ZERO**; ~3 % ≥1 |
| HANGAR-whitebg2 *(aerial)* | max-gray **30–40** | **0 % — 100 % clean** |
| TOGO-night | max-gray ~120 | **0 % — 100 % clean** |

**Read:** natural IR content tops out at 40–157 while a retroreflector saturates near **255** → a
high threshold has a near-zero false-positive floor, and even the worst textured scene is 97 % clean
(the rare hits are area/exclusion-mask-filterable). **This de-risks the "fixed-glint false
positives" unknown.** It confirms *separability* (headroom), **NOT** *detectability* — whether a real
marker reaches near-saturation at show distance/angle/defocus is the physical Phase-0a question.

## Verification (when built)
- **Phase 0a/0b:** marker reliably segmentable at a fixed IR threshold across the test recording
  (precision/recall); audience-invisible in a visible-light check; self-occlusion gaps short enough
  to bridge.
- **Phase 1:** a replay-gated **continuity metric** on **HANGAR + TOGO** hard clips (+ a
  static-subject clip) — fraction of ground-truth dancer-frames with a correct continuous track —
  improves vs the current build; **goldens stay byte-identical for the markerless path** (markers
  are additive, output-only w.r.t. the OSC contract).

## Critical files (Phase 1)
- new `application/src/core/marker_detector.py` — the marker stage.
- `application/src/core/tracker.py` — fuse marker as a high-confidence measurement (the generic
  Kalman stays; no pendulum).
- `application/src/core/pipeline.py` — add the marker stage to the per-frame detection chain.
- `application/src/core/motion_detector.py` — reuse the blob/threshold infra.
- `application/src/core/calibration.py` + the calib flow — per-venue marker-brightness threshold.
- `application/src/core/osc_output.py` — **unchanged** (output stays position+identity).

## See also
`OPTICS.md` (rig + IR), `OSC_CONTRACT.md` (output = position+identity), `OPERATOR_V2.md` Track D
(#1 fine-tune), `CORPUS_ANALYSIS.md` (the hard-scene walls + G3 imgsz finding).
