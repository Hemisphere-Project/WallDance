# Other ways to two stable points: brainstorm, ranking and quick experiments

**Date:** 2026-10-06 (evening) · **Status:** for Thomas's review, nothing applied to code · **Scope:** approaches we
could add to, or pivot to, for the goals restated in [ROADMAP.md](ROADMAP.md) §0, with the hardware already at hand.
Read with [PLAN_25M_2026-10.md](PLAN_25M_2026-10.md) (this week) and [ROADMAP_v2_REVIEW.md](ROADMAP_v2_REVIEW.md)
(§1 measurements). Scripts and outputs are in `/tmp/wd-brainstorm/` on dev37 (listed in §4, tmpfs: copy them before a
reboot if they matter).

Tags used below: **[measured]** = run on dev37 today on the April footage, numbers in §4 · **[calculated]** = geometry or
arithmetic from known specs · **[literature]** = textbook or vendor values not checked on our material ·
**[speculation]** = reasoned, not tested.

---

## 1. Verdict

The scene is fixed, the wall is mostly plain, and N is known, yet the pipeline finds dancers almost only through YOLO,
which is blind to one or both dancers on a large share of frames at this blur and noise: on the white-wall duo, YOLO
at the app's τ = 0.25 sees both dancers on only 52 % of frames and neither on 10 %; on the textured duo it sees nobody
at conf ≥ 0.5 on 71 % of frames [measured]. A clean-plate foreground signal (current frame minus the empty wall,
downscaled 4×) finds both dancers on essentially every frame of the white-wall footage (by-eye audit) and keeps two
points on 99 % of the textured take. A deliberately crude known-N tracker
fed **only** by that foreground beats the shipped slot stream on its own yardstick: on-dancer 0.96 to 0.97 against
0.74 on the white wall and 0.93 to 0.94 against 0.89 on the textured wall, with 6 to 11 % held points instead of 23 to
26 %, and no hole longer than 1.9 s [measured, reference = YOLO ≥ 0.5]. The same signal vetoes 97 % of YOLO's
detections on the known fixed ghost spots and removed no detection on a moving body [measured]. So the three moves I
would put first are:
1. Both IR projectors at the lens. This is physical, already in tonight's protocol, and it removes the shadow ghosts and the shadow bias, and gives the belt its best chance. Check the body brightness as well as the belt on slot 8.
2. Clean-plate foreground as an output-side ghost veto and known-N presence source next to the slot layer. Prototype it offline on tonight's takes (slot 1 is the empty wall).
3. An explicit "always N" demo mode, so TouchDesigner never loses a point while both dancers are on the wall.

A rope-pendulum predictor cuts the error of a held point by a third to 70 % on swings of 0.5 to 2 s [measured, aerial
take] and is cheap. It is the next step once the anchors are known. Detection-side "look deeper" crops and swapping the
tracker for ultralytics' trackers are not worth it this week [measured / reasoned].

Honest limits:
- All of this was measured on April footage, lit off-axis.
- The positional reference is YOLO itself.
- The clean plate was the take's own median or a causal running median. A true empty-wall plate exists only in tonight's takes.
- The foreground point is a biased body proxy: shadows pull it down by about 0.1 h on this footage.

---

## 2. Ranked approaches

Impact is on the goals in order: G1 continuous, stable two-point stream at 25 m · G2 scaling to 30-40 m, darker,
smaller · L lighter laptop load · O easy operator settings. Effort is engineering days to a replay-gated, switchable
feature. Compute is on the show laptop.

| # | Approach | Impact | Effort | Laptop compute | Risk | Evidence | Horizon |
|---|---|---|---|---|---|---|---|
| 1 | **Both PIR130 at the lens axis** (≤ 10-20 cm, one above, one below or both beside), aimed at the rope zone | G1 high (no shadow ghosts, no shadow bias, belt ×25 vs 65 cm offset); G2 high | 0.5 h on site, no code | 0 | low-medium: body may get darker if the usual projectors stood much closer to the wall | [calculated] shadow offset ≤ 2 cm vs 0.8-1.6 m seen in April; [literature] retro ×25 | **this week** (tonight's protocol already does it) |
| 2 | **Clean-plate foreground evidence at the output**: ghost veto for slot binding, weak measurement for held slots, known-N re-acquisition anywhere; "Capture empty wall" step at Calibrate | G1 high; G2 high (works on small, noisy blobs); O medium (one button, automatic ghost handling instead of painting) | 1.5-2.5 d | ~3 ms CPU est., < 1 ms on GPU est. | medium: light changes, plate contamination, merged dancers, ropes | [measured] §4.3-4.6 | **offline proto this week**, live behind a key on Thomas's go (before the demo only if gated in time), else first after |
| 3 | **"Always N" demo contract**: N fixed = 2, a slot never vanishes while N dancers are on the wall, held slots re-acquire anywhere, `/dancer/state` lets TD fade | G1 high (TD never drops video); O high (one switch) | 0.5 d (knobs exist: `coast_s`, `rebind_any_after_s`) | 0 | medium without #2 (a held point can sit off-body), low with #2 | [measured] N-lock coverage 100 % with 6-11 % held points | **this week** |
| 4 | **Rope-pendulum prediction** for held points and rebind gates (anchor per rope, clicked once per show) | G1 medium (held points stay on the body through swings); G2 neutral | 1 d | ~0 | medium: climbing changes rope length, push-offs | [measured] error at 1 s: 0.48 vs 0.79 h (fast swings) | before production (this week only if holds stay frequent) |
| 5 | **Belt as a primary sensor**, if the on-axis belt saturates on slot 8: known-N belt points + YOLO/foreground verification, then shorter exposure | G1 high if it works; G2 high (retro does not need body light); L medium | 1-2 d (detector exists) | ~0.1-0.7 ms | high until slot 8 is read | [calculated] + 2026-10-05 belt at ~30 DN under mixed light | decide this week (D19), build before production |
| 6 | **Use YOLO's sub-τ boxes near a held slot** (ByteTrack's second association stage), zero extra GPU; **not** a second local crop pass | G1 medium | in progress (main session, output side) | 0 | low with the foreground veto, medium without (shadows score 0.7-0.9) | [measured] 48 % of misses already ≥ 0.10 in the full-frame pass; crops add little (§4.7) | this week (main session) |
| 7 | **Lighter YOLO once it is a verifier**: l@1280 (13.5 ms) or x@960 (12.6 ms) instead of x@1280 (21.3 ms), or YOLO every other frame | L high (−8 to −14 ms GPU per frame, cooler GPU with TD) | 0.5 d of replays per candidate | −35 to −65 % GPU | medium: accuracy on blurred bodies | laptop `fps_table.json` | before production (needs #2 first) |
| 8 | **Camera: shorter exposure (25-33 ms) at 20 fps, Mono8 kept**, once IR is on-axis and #2 carries presence | G1 medium (less blur for YOLO and the belt); G2 medium | 0 code (ladder take slot 7 exists) | 0 | medium: YOLO confidence falls with noise | [calculated] foreground noise σ ≈ 0.6 DN at 4× downscale vs tens of DN contrast | test on site after #1 |
| 9 | **Interior-birth prior** (a new dancer must come from a border) | G1 low | 0.5 d | 0 | medium: wrong after contact splits and restarts | [reasoned] fixed ghosts are static: the foreground veto (#2) tests that directly | fold into #2 as a mild rule |
| 10 | **Replace the tracker by ultralytics ByteTrack / BoT-SORT** | G1 low-negative | 2-3 d | ~0 | high: no known N, no motion fallback, tracks die after a buffer, ReID useless on mono identical dancers | [reasoned] | later at most (borrow the idea, see #6) |
| 11 | **Local high-res / low-τ YOLO crop pass on loss** | G1 low | 1 d | +6-8 ms GPU per crop | medium: false persons on rope + beam | [measured] §4.7 | rejected this week |

The rest is in §5.

---

## 3. The top approaches in detail

### 3.1 Both projectors at the lens axis (physical, this week)

**What.** Mount both PIR130 as close as the housings allow to the lens axis (one above and one below the lens, or one on each side, emitter centres ≤ 10-20 cm from the optical axis), both aimed at the zone the ropes cover rather than at the wall centre.

**Why it fits here.**
- *Shadows* [calculated]. A projector offset by s from the lens, both at distance D from the wall, throws the shadow of a dancer hanging d_w in front of the wall at an offset of s·d_w/(D − d_w) on the wall, as seen from the camera.

  | s (projector offset) | d_w = 0.5 m | d_w = 1 m | d_w = 3 m (swinging out) |
  |---|---|---|---|
  | 0.15 m (at the lens) | 0.3 cm | 0.6 cm | 2 cm |
  | 1 m | 2 cm | 4 cm | 13.6 cm |
  | 5 m (floor projector off to the side) | 10 cm | 21 cm | 68 cm (0.4 h) |

  At 25 m a pixel is ~9 mm, so an on-axis shadow sits entirely behind the dancer's own silhouette. The April white-wall footage shows soft shadows offset by 0.5 to 1 dancer height (0.8-1.6 m), which needs s·d_w around 20-40 m², i.e. projectors metres off-axis. Those shadows are what YOLO calls a person at 0.7-0.9 on the off-axis Bordeaux takes, they inflate every motion blob (§4.5 masks), and they bias the foreground centroid down by ~0.1 h (§4.3).
- *Belt* [literature, to verify on slot 8]. The observation angle at 25 m is 0.34° for 15 cm and 1.5° for 65 cm. With garment-grade retro (~250 cd·lx⁻¹·m⁻² at 0.33°, ~10 at 1.5°, values from the audit, unverified on our tape), that is ×25 more return. The 2026-10-05 belt peaks of ~30 DN at show distance under mixed, partly off-axis light would saturate with margin.
- *Cross-check of a claim in the brief.* Cross-polarisation is possible at 850 nm in principle with NIR-rated polarisers (wire-grid or NIR film; the usual visible film loses extinction above ~750 nm). It is still the wrong tool here: we have no polarisers, each one costs more than half the light, and glass-bead retro largely preserves polarisation, so crossing would also kill the belt.

**Costs and risks.**
- Half an hour on site. The protocol already asks for it (TOURNAGE §0).
- The real risk is a trade-off, not a failure: if the "usual" floor projectors stood much closer to the wall than the camera, moving them to the camera cuts the diffuse light on the body by (D_camera / D_usual)², so YOLO starves while the belt and the foreground gain. *Read slot 8 against slot 4 for body brightness and YOLO confidence, not only for the belt.*
- Glossy objects in the field (windows, metal truss) glare more on-axis. The belt static map (PLAN A6) covers the static ones.

**Test.**
- Tonight's slot 8 vs slot 4: belt DN (B2), body DN on the dancer boxes, YOLO conf histogram, and the shadow halo around the dancers in the clean-plate mask (§4.5 script on slot 1 + slot 4/8).
- Offline nothing more is needed.

**Replaces / complements.** It replaces the need for a shadow-suppression algorithm and complements every detector. It is the precondition for #5 (belt-primary) and makes #2 unbiased.

### 3.2 Clean-plate foreground evidence at the output (the main new idea)

**What.** Capture the empty wall once at calibration (tonight: slot 1). Each frame:
1. downscale the ROI 4× (area average);
2. take |frame − g·plate| with g = the robust frame/plate gain ratio;
3. threshold at k·σ_noise, open/close, connected components;
4. emit components with their mass centroid.

Three uses, output-side next to the slot layer and shaped like the existing belt hook (`BeltMeasure`):
- (a) **Ghost veto.** A tracker track or YOLO box with < 10 % foreground pixels in its box cannot establish or take a slot. Static figures, stains and the door figure have no foreground against the plate.
- (b) **Weak measurement.** A held (coasting) slot takes the nearest foreground point within its gate, corrected by an offset learned while YOLO is live, so it follows the body instead of freezing.
- (c) **Known-N re-acquisition.** A slot held for more than ~0.3 s takes the largest unexplained foreground point ≥ 1 h from the other slots, anywhere in the ROI. This fixes the "dancer reappears > 1.5 h away after a rope swing" cause (27 % of uncovered frames on the white wall).

The existing slow MOG2 mask in the motion worker (learning rate 0.001 / 0.0003, i.e. nearly static) could serve as the source instead of a new plate. A plate is better: it never absorbs a still dancer, and it can be re-captured by the operator.

**Why it fits.** All [measured] on the April duos, details in §4:
- *Detection.* Per frame and with no tracking at all, two foreground points with N = 2 put a point within 0.75 h of 92-94 % of the YOLO reference positions on the white wall. On 32 random frames where YOLO saw fewer than two dancers, the foreground stream covered 64/64 dancer-frames by eye; the shipped stream covered about 56/64 and emitted a single point in 13 of the 32 frames.
- *A crude tracker beats the shipped one.* An N = 2 tracker on foreground alone ("N-lock", ~150 lines) scores on-dancer 0.96-0.97 vs 0.74 shipped (white) and 0.93-0.94 vs 0.89 (textured, with gain normalisation). Held points fall to 6-11 % vs 23-26 %, the longest hole is 0-1.9 s vs 5.2-7.4 s, jitter at rest is 1.2 % h vs 4.6 % h on the white wall, and lag is 0-32 ms vs 57 ms.
- *Ghost veto.* The foreground support test removed 288 of the 296 YOLO detections at the two known ghost spots (97 %) plus 36 more at three other static spots (a stain, two pillar edges), and none within 0.5 h of a moving body.
- *Cost.* 7.3 ms p50 single-threaded on dev37's old CPU, 5.4 ms of it the downscale the motion worker already does. That is ~3 ms on the laptop's CPU [speculation from the 2.5× per-thread ratio], or well under 1 ms as a 4×4 average-pool + compare on the frame already on the GPU [speculation].
- *Scaling (G2).* The detector needs blobs, not skeletons. At 40 m a dancer is still ~29 px tall after the 4× downscale, and averaging 16 pixels divides sensor noise by 4. So it degrades much later than YOLO with distance and darkness [speculation, consistent with σ ≈ 0.6 DN after downscale against tens of DN of body contrast here].

**Failure modes seen today, and the fix for each.**

| failure | evidence | fix |
|---|---|---|
| Plate contaminated by dancers who dwelt somewhere (running median, or the take's own median when one dancer hangs at the pillar for half the take) | "hole ghost" blobs on empty wall: most of the 9/64 spurious audit points; most of the 44 look-deeper candidates no detector confirmed | a true empty-wall plate (slot 1, a Calibrate step); if updated live, update only outside the slots' bodies |
| Global light change | textured take ramps from 90 to 26 DN mean: on-dancer 0.70 | per-frame gain normalisation: 0.94 [measured]; re-capture prompt when the foreground ratio explodes (the existing `BackgroundSubtractor` already flags > 0.55) |
| Off-axis shadows inflate blobs and bias centroids (median +0.10 to +0.13 h downward, p90 0.46-0.49 h total offset vs the YOLO box centre) | §4.3, §4.5 masks | IR on-axis (#1); centroid weighted on the high-contrast core (already −0.03 h); offset learned while YOLO is live |
| Two dancers in contact give one blob | early white-duo frames | known-N split by weighted 2-means inside the blob. Only accept a separate second blob if it is heavy (e.g. ≥ 25 % of the first) or YOLO-supported; otherwise split the main one |
| Moving ropes are foreground (thin lines up to the anchor) | masks | larger opening kernel or thin-structure removal; ropes are 1-2 px wide after the 4× downscale |
| NIR-emitting show light (tungsten / halogen) or lamp video projectors sweeping the wall | not in our footage | [speculation] ask about the show's lights; LED fixtures and laser/LED projectors leak little at 850 nm behind the BP850 |

**Costs.**
- 1.5-2.5 days: foreground step in the motion worker or on the GPU; plate capture in Calibrate (this makes the GUI's "Aim captures the clean plate" text true, so D16 flips from "fix the text" to "build it"); slot-layer hook (veto, weak measurement, re-acquire); a sidecar so `slot_replay.py` can gate it; tests.
- Operator burden: one "empty wall" capture, which the Calibrate step already asks for (wall empty).

**Test.**
- Offline now: `nlock.py` / `rejstats.py` / `audit_sheet.py` (§4) on the April duos (done) and on the Bordeaux takes once A1 classifies them.
- Tomorrow on the laptop: the same scripts with tonight's slot 1 as the plate (`--bg plate`) on slots 4/5/6/9, through `wdremote py --with fg_lib.py`. Only JSON comes back.
- Gate: on-dancer, held share and longest hole vs the shipped stream, plus a by-eye audit of 32 hard frames per take.

**Replaces / complements.**
- Complements YOLO and the slot layer now.
- Before production it is the natural replacement for the tracker's blob-as-synthetic-detection path. That path makes 100 % of the off-body "live" points (REVIEW §1.3), and the foreground N-lock never feeds blobs into association. So it is a candidate simplification of CONT-3 / CONT-11, to be decided on tonight's takes.

### 3.3 An explicit "always N" mode for the demo

**What.**
- The operator says "2 dancers, both on the wall". The output then always carries ids 1 and 2.
- A slot that loses its measurement holds (state `coasting`) and may re-acquire anywhere after a short delay instead of vanishing after `coast_s`.
- `/walldance/dancer/state` (D22) lets the TD patch fade or soften a held point instead of dropping its video.

**Why it fits.**
- TD drops its video when a point vanishes (D5), and during the demo N is known and constant.
- The N-lock runs show 100 % two-point frames by construction, so what remains is the share of held points: 6-11 % with foreground evidence [measured], 34-40 % with YOLO alone [measured, N-lock "yolo"].
- The knobs exist (`coast_s`, `rebind_any_after_s`, which the main session is sweeping as "reacq").

**Costs and risks.**
- Half a day.
- Without #2 a held point can sit off the body for seconds, which is visible on TD, so "always N" and the foreground re-acquisition belong together.
- If a dancer really leaves the frame, her point stays at the border. Acceptable for a demo, not for a show with entries and exits (D4: N is a cap), so it must be a per-scene mode.

**TD side** (needs the patch owner):
- fade on `coasting`, with a fade time of 0.3-0.5 s;
- glide on re-acquisition jumps (the slot layer already snaps beyond 1 h).

Do not add TD-side smoothing: it adds lag on top of the One-Euro.

**Test.** `slot_replay.py --set coast_s=… --set rebind_any_after_s=0.3` on the full duo timelines, then `nlock.py` as the upper bound.

### 3.4 Rope-pendulum prediction while a point is held

**What.** Each rope hangs from a fixed, visible anchor (aerial take: a pulley on the beam at about (790, 130) px). While a slot is held:
1. take the rope length R = |position − anchor| at the last measurement;
2. take the angular speed from the last 5 frames;
3. integrate θ'' = −(g/R) sin θ, with g = 9.81 m/s² converted to px with the dancer height (1.7 m);
4. predict anchor + R·(sin θ, cos θ).

The same prediction re-centres the rebind gate.

**Why it fits** [measured, aerial take, 5088 frames, positions = YOLO ≥ 0.5 off the ghost spots, median / p90 error in dancer heights, fast = centroid speed > 0.5 h/s]:

| hold time | hold | shipped decay (τ 0.25 s) | constant velocity | **pendulum** |
|---|---|---|---|---|
| 0.25 s, fast | 0.33 / 0.63 | 0.26 / 0.56 | 0.29 / 0.68 | **0.18 / 0.46** |
| 0.5 s, fast | 0.52 / 1.13 | 0.45 / 1.00 | 0.61 / 1.19 | **0.30 / 0.69** |
| 1 s, fast | 0.89 / 2.27 | 0.79 / 2.02 | 1.44 / 2.59 | **0.48 / 1.10** |
| 2 s, fast | 2.05 / 3.93 | 2.00 / 3.96 | 3.53 / 6.71 | **0.62 / 1.59** |
| 1 s, slow | 0.60 / 1.57 | 0.59 / 1.54 | 0.67 / 1.55 | **0.30 / 0.63** |

The undamped pendulum beats damped and blended variants: the dancers keep their swing going. These errors are
against YOLO box centres, which are noisier than the skeleton centroid the review used (REVIEW §1.4), so compare
columns, not across documents.

**Costs and risks.**
- About 1 day: an anchor click per rope in the setup UI (a pixel on the rope top, or two pixels on the rope when the anchor is above the frame), the prediction in the slot layer, and tests.
- Climbing changes R (it is re-read at each measurement, so only the hold itself assumes a constant length).
- Pushing off the wall breaks the free swing, and swings toward or away from the camera are foreshortened.
- Two dancers on one rope, or ropes crossing, need the rope-to-slot assignment.
- A later bonus [speculation]: the rope also gives identity across crossings, since each dancer stays on her rope.

**Test.** `pendulum.py` on the aerial take (done). Next: on white-duo per dancer once the N-lock gives per-slot tracks, and on tonight's slots 3/4/5 with anchors clicked on the slot 1 frame.

**Horizon.** If #2 makes holds rare (6-11 % of points, short), this is second-order for the demo. It matters more at 30-40 m and in low light, where holds get longer.

### 3.5 The belt as the primary sensor (conditional on slot 8)

**What.** If the belt saturates on-axis at show distance:
- threshold + blob detection (the gated `belt_detector.py` already runs at 0.05-0.7 ms p50) yields one point per dancer at the waist, the best single-marker centroid proxy in the audit's synthetic study (0.04-0.07 h);
- feed it to a known-N assignment like the N-lock, with YOLO and the foreground as verifiers (a belt blob must sit on foreground and move);
- then lower the exposure (10-20 ms) to cut blur, because retro return does not need body light.

**Why.**
- With the projectors on-axis the expected return is ~25× the off-axis case [literature].
- Once the belt carries position, YOLO's starving at short exposure stops mattering for G1, and the light budget at 40 m becomes a belt budget (point source, 1/d² once each way) rather than a body budget.

**Risks.**
- Everything hinges on tonight's slot 8.
- Occlusion by arms, the rope or the other dancer (front + back belts help).
- Glints (static map A6).
- Two belts merging when the dancers touch: the known-N split as in #2.

**Test.** `belt_eval.py` on slots 4/8 (PLAN B2), then a belt N-lock with the same `nlock.py` structure fed by belt blobs.

**Horizon.** Decide this week (D19), build before production. It complements #2 rather than replacing it: the foreground sees bodies when belts are hidden, the belt pins the waist when bodies blur.

### 3.6 "Look deeper" after a loss, and the border-entry prior (the coordinator's two paradigms, detection side)

**Look deeper locally** [measured, §4.7]. On 120 positions where the clean plate saw a body that YOLO at τ = 0.25 had missed (white duo):

| pass | conf ≥ 0.10 | conf ≥ 0.25 | false persons on empty-wall controls (≥ 0.10) |
|---|---|---|---|
| the full-frame pass, read below τ | 48 % | 22 % | 0 % |
| local crop 2.5 h, imgsz 640 (≈1.24× native) | 61 % | 29 % | 0 % (11 % at ≥ 0.05) |
| local crop 2.5 h, imgsz 960 (≈2.3× native) | 46 % | 23 % | 11 % |

- About half of the misses are already in the full-frame output just under τ, so reading YOLO's own sub-τ boxes near the held slot (ByteTrack's second stage, which the main session is implementing at the output) costs zero GPU and gets most of the gain.
- A dedicated crop adds ~13 points, and by eye about half of its extra "rescues" were false persons on the hanging rope just below the beam (conf 0.24-0.74).
- Upsampling (960) does not help, so resolution is not what blinds YOLO at 25 m; blur, noise and pose are.
- On the laptop an extra x@640 call costs 7.7 ms engine-only (l@640 6.3 ms), so +35 to +70 % of the main 21 ms inference when one or two slots are held.
- Verdict: not this week. Revisit only at 40 m with tight ROIs, and only foreground-gated.

**When "look near the last position" is counter-productive** [reasoned from the footage]:
1. *Rope swings.* A dancer travels 1-3 h in 0.5 s, so the search must be centred on the pendulum prediction (#4), not on the last position.
2. *Two dancers in contact.* The local search finds the other dancer and both slots collapse onto one body. A measurement already explaining another slot must be excluded (the N-lock does this).
3. *A ghost or shadow next to the last position.* Off-axis shadows score 0.7-0.9 and hug the dancer; the local low-τ search latches onto them. The foreground veto does not remove shadows (they move), so it needs on-axis IR (#1).
4. *The dancer really left.* The local search then holds onto junk, and the hold must end (D4).

**Border-entry prior.**
- In this show, dancers are on the wall from the start or enter from the floor or the sides.
- The fixed ghosts (white wall: (1225, 519), (873, 487)) are interior and static. Fixed ghosts are static, so "has foreground against the plate" tests what an entry prior only approximates, and it decides on the first frame instead of after a long confirmation.
- The prior is counter-productive exactly where the demo is hard: after two dancers split from contact, the second one "appears" in the interior; after a restart or a long merge, every dancer is interior.
- So: trust interior births that are on foreground and near a held slot or a split. Keep longer confirmation only for interior births with no foreground support, or far from everything. That is the rule the main session is building at the output, with foreground support as its sharpest test.

### 3.7 Lighter compute and camera settings (once #1-#2 are in)

- **YOLO's job shrinks** once the foreground carries presence and re-acquisition: identity of "person" (veto), the offset that turns the foreground centroid into a body centroid, and the skeleton for `/keypoints`. On the laptop (engine-only, `fps_table.json`), x@1280 costs 21.3 ms, l@1280 13.5, x@960 12.6, m@960 7.3. Dropping to l@1280 or x@960 returns 8-9 ms of GPU per frame, roughly the TD headroom at 84-87 °C. Running YOLO on every other frame halves it again [speculation: the slot layer would need YOLO-free frames to be routine]. Test: replays per model with the N-lock scorer; accept a model if on-dancer and held share stay within 1 point.
- **Exposure.** At 49 ms the swinging dancers are smeared (the crops in §4.5). Once IR is on-axis and the foreground and belt carry presence, try 25-33 ms at the same 20 fps (tonight's slot 7 ladder gives the DN curve). The foreground survives the extra gain noise, since σ is ~0.6 DN after the 4× downscale here [measured]; YOLO loses some confidence, and the KPI decides.
- **Not worth it:** 40 fps (doubles YOLO cost and halves light per frame for gates the slot layer does not need); Mono12 (the 8-bit quantisation sits below the noise at 27.8 dB gain); 2×2 binning (188 → 94 px dancers, below YOLO's floor; the foreground downscales in software anyway).

---

## 4. Quick experiments run today (dev37, CPU unless noted, `nice -n 10`)

Inputs: the main session's TRT x@1280 replays with slots ON at `remote-ops` + the uncommitted slot-layer edit
(`white-duo-full`, 739 frames; `texture-duo-full`, 2638 frames; `aerial_full`, 5088 frames), copied to
`/tmp/wd-brainstorm/inputs/`. Metrics come from the repo's own `output_quality.compare_streams`, the same code as the
review and `slot_eval.py`. The reference is YOLO ≥ 0.5 (top-N, manifest ghost spots excluded). On-dancer counts
reference positions with an emitted point within 0.75 h, over all frames. The YOLO reference only exists where YOLO is
confident, so it scores the easy subset. The by-eye audit (§4.5) covers the hard subset.

### 4.1 How often YOLO sees the dancers (`refstats.py`)

| take | YOLO ≥ 0.5: frames with 0 / 1 / ≥ 2 dancers | YOLO ≥ τ (0.25 white, 0.10 textured): 0 / 1 / ≥ 2 |
|---|---|---|
| white duo (724 scored frames) | 216 / 303 / 205 | 74 / 270 / 380 |
| textured duo (2623) | 1872 / 622 / 129 | 584 / 986 / 1053 |

### 4.2 Baseline reproduced (`compare_streams` on the timelines)

| take | stream | 2 points | on-dancer | longest hole | held | jitter rest % h | lag ms | fast err h | id sw |
|---|---|---|---|---|---|---|---|---|---|
| white | tracker | 0.803 | 0.899 | 3.58 s | - | 5.37 | 46 | 0.121 | 30 |
| white | **slots (shipped)** | 0.910 | **0.741** | 5.15 s | **23.2 %** | 4.57 | 57 | 0.147 | 10 |
| textured | tracker | 0.774 | 0.924 | 3.43 s | - | 7.80 | n/a | 0.135 | 276 |
| textured | **slots (shipped)** | 0.941 | **0.892** | 7.37 s | **25.8 %** | 8.78 | n/a | 0.183 | 89 |

### 4.3 Stateless known-N foreground points (`fg_n2.py`, white duo, take-median plate)

Two points per frame (the two heaviest body-size components; one component split by weighted 2-means):
- 2 points on 100 % of frames.
- On-dancer: 0.92 (|d|-weighted) / **0.94** (core-weighted, `--core 8`); within 0.35 h: 0.73 / 0.78.
- Offset from the YOLO box centre: median dx 0.0 h, dy **+0.10 to +0.13 h** (down, the shadows); |offset| p50 0.15-0.18 h, p90 0.46-0.49 h.
- Body-size components per frame: 1 on 152 frames, 2 on 444, 3-4 on 142.

### 4.4 The N-lock tracker variants (`nlock.py`)

N fixed; slots never die; association uses Hungarian matching with a gate that grows while a slot is held; re-acquisition anywhere after 0.3 s; offset learning (foreground → YOLO) in the fusion variant; One-Euro at Stability 0.5 (the slot layer's own filter class). Parentheses mark numbers that are circular (the stream is fed by the reference itself).

| take | variant | plate | on-dancer | longest hole | held | jitter % h | lag ms | fast err h | id sw | ghost dets vetoed |
|---|---|---|---|---|---|---|---|---|---|---|
| white | **foreground only** | take median | **0.969** | 0 | 5.9 % | 0.49 | 32 | 0.207 | 22 | - |
| white | **foreground only** | causal 30 s | **0.961** | 0 | 9.1 % | 1.17 | 0 | 0.189 | 14 | - |
| white | foreground only | causal + gain norm | 0.968 | 0 | 7.3 % | 1.19 | 0 | 0.190 | 12 | - |
| white | YOLO ≥ 0.25 only | - | (0.864) | 0 | 33.7 % | 2.90 | (0) | (0.039) | 6 | - |
| white | YOLO verified + foreground fallback | causal + gain norm | (0.986) | 0 | 7.9 % | 2.86 | (0) | (0.037) | 16 | 325 |
| textured | foreground only | take median | 0.697 | 1.46 s | 7.4 % | 5.55 | n/a | 0.267 | 61 | - |
| textured | foreground only, frames < 2250 (before the light ramp) | take median | 0.940 | 1.46 s | 8.7 % | 5.92 | n/a | 0.208 | 58 | - |
| textured | **foreground only** | take median + gain norm | **0.942** | 1.51 s | 7.4 % | 6.53 | n/a | 0.202 | 73 | - |
| textured | foreground only | causal + gain norm | 0.931 | 1.87 s | 10.5 % | 6.88 | n/a | 0.217 | 93 | - |
| textured | YOLO ≥ 0.10 only | - | (0.944) | 0 | 40.5 % | 7.62 | n/a | (0.051) | 73 | - |
| textured | YOLO verified + foreground fallback | causal + gain norm | (0.961) | 0 | 15.2 % | 8.58 | n/a | (0.061) | 109 | 1780 |

Readings:
- The non-circular rows (foreground only) beat the shipped slots on on-dancer and held share on both walls, with no YOLO at all. Their two-point coverage is 100 % by construction.
- Their fast-move error vs YOLO (0.19-0.27 h) is worse than shipped (0.15-0.18 h): the shadow and blur bias of §4.3. The fusion variant's offset learning corrects it (0.04-0.06 h, circular, so read as "the offset is learnable").
- Jitter in the fusion variant is high (2.9 % / 8.6 % h): it switches between YOLO box centres and corrected foreground points. A real implementation should blend, not switch.
- The take-median plate is non-causal. The causal 30 s running median is realistic but suffers dwell contamination; tonight's empty wall is the real test.

### 4.5 By-eye audit of the hard frames (`audit_sheet.py`, `maskview.py`)

32 random white-duo frames (seed 7) where YOLO ≥ 0.25 saw fewer than two dancers (344 such frames of 739). Each was judged by eye with the same 0.75 h rule; a pair in contact counts as both covered by one point on the pair. Sheets: `wd_audit_0..3.jpg`.

| stream | dancer-frames covered | frames with only one point | points on neither dancer |
|---|---|---|---|
| foreground N-lock (causal plate) | **64 / 64** | 0 | 9 of 64 (all while the pair formed one blob: the forced second point went to a shadow lobe or a plate "hole ghost") |
| shipped slots | ~56 / 64 | 13 of 32 | 8 of 51 (a slot held near the top beam for frames ~369-423 while the right dancer was uncovered; a live slot on the rope top at frame 290) |

The masks (`wd_masks.png`) show the causal plate's hole ghosts where the dancers dwelt earlier, the soft shadow lobes under each dancer, and the moving ropes. Overlays: `wd_fg_ov1.jpg` (white), `td_ov.jpg` (textured: before gain normalisation the points sit on bright stains after the light ramp).

### 4.6 Foreground veto of YOLO detections (`rejstats.py`, white duo, causal plate)

A detection is vetoed when < 10 % of its box (0.45 h × h) is foreground:
- 1036 kept;
- **288 of 296 at the two manifest ghost spots vetoed (97 %)**;
- 36 vetoed at three other static spots (top-right stain and pillar ~(1405, 381) and ~(1343, 515), bottom-left pillar ~(177, 1184));
- **0 vetoed within 0.5 h of a foreground body point**.

Vetoed confidences: p50 0.35, p90 0.52, max 0.72.

### 4.7 "Look deeper" crops (`lookdeeper.py`, `ldfail.py`; the one GPU run: PyTorch yolo11x-pose FP16, 147 full frames + 294 crops)

- **Candidates:** 120 foreground points ≥ 0.75 h from every YOLO ≥ τ detection. **Controls:** 27 empty-wall crops with no foreground within 1.5 h. Enhancement approximated with cv2 (gamma 2.2 LUT + CLAHE 2.5).
- Results are in the §3.6 table.
- Of the 44 candidates no pass confirmed, the ones inspected were empty wall: plate hole ghosts, since the right dancer hangs by the pillar for half the take, so even the take median absorbs her. These are not missed dancers.
- Of the 17 crop-only rescues, 12 were inspected by eye: 6 real dancers and 6 false persons on the rope just below the beam.
- Timing on dev37 is not representative (shared GPU, PyTorch). Laptop engine-only figures are in §3.6.

### 4.8 Foreground cost (`fg_timing.py`)

Single thread, dev37 under load 14-16:
- downscale 5.4 ms p50 / 9.4 p95;
- diff + threshold + morphology 0.6 / 0.9;
- components + centroids 1.1 / 1.6;
- **total 7.3 / 14.2 ms**.

### 4.9 Rope pendulum (`pendulum.py`)

Table in §3.4. The anchor was set by hand from the frame (`--anchor 790,130`). An automatic anchor from 1.5 s circle fits found only one good fit on this take; use the click, or a Hough on the foreground rope lines later.

### Scripts and commands (all under `/tmp/wd-brainstorm/`, Python = `/data/WallDance/application/.venv/bin/python`)

| script | role |
|---|---|
| `sheet.py`, `crop.py` | brightened contact sheets / full-resolution crops |
| `decode_cache.py` | decode a take once to a 4× downscaled ROI stack (`.npy`, float16) |
| `fg_lib.py` | clean-plate foreground (plates: `global` take median, `causal` running median, `plate` from a frame range; `gain_norm`), known-N points, box support |
| `fg_extract.py`, `fg_n2.py` | first-pass foreground components; stateless known-N points scored vs the reference |
| `nlock.py` | the N-lock tracker (`--variant fg|yolo|yolo_verified|fusion`), scored with `output_quality.compare_streams`; `--dump` writes a timeline with an `emitted` block |
| `refstats.py`, `rejstats.py` | YOLO dancer counts per frame; foreground veto classification |
| `fg_overlay.py`, `audit_sheet.py`, `ov2.py`, `maskview.py` | overlays, hard-frame audit sheets, mask views |
| `lookdeeper.py`, `ldfail.py` | local crop vs full-frame low-τ test (GPU); crops of misses and rescues |
| `fg_timing.py` | foreground cost |
| `pendulum.py` | gap injection: hold / decay / constant velocity / pendulum |

```bash
cd /tmp/wd-brainstorm; PY=/data/WallDance/application/.venv/bin/python; I=inputs
REC=/data/WallDance/projects
$PY decode_cache.py $REC/4_TANGO_HANGAR-whitebg3/recordings/slot_2_20260403_101623.avi 137,232,1299,1139 4 wd_ds4.npy
$PY decode_cache.py $REC/1_TANGO_HANGAR-texturedbg/recordings/slot_5_20260401_161146.avi 120,210,1272,1190 4 td_ds4.npy
$PY refstats.py $I/white-duo-full.timeline.json $I/white-duo-full.json
$PY fg_n2.py wd_ds4.npy $I/white-duo-full.timeline.json $I/white-duo-full.json wd_n2_core8.json --core 8
$PY nlock.py wd_ds4.npy $I/white-duo-full.timeline.json $I/white-duo-full.json --variant fg --bg causal --gain_norm 1
$PY nlock.py td_ds4.npy $I/texture-duo-full.timeline.json $I/texture-duo-full.json --variant fg --bg global --gain_norm 1 --tau 0.10
$PY nlock.py wd_ds4.npy $I/white-duo-full.timeline.json $I/white-duo-full.json --variant fusion --bg causal --gain_norm 1
$PY rejstats.py wd_ds4.npy $I/white-duo-full.timeline.json $I/white-duo-full.json causal
$PY nlock.py wd_ds4.npy $I/white-duo-full.timeline.json $I/white-duo-full.json --variant fg --bg causal --dump wd_fgc_stream.json
$PY audit_sheet.py $REC/4_TANGO_HANGAR-whitebg3/recordings/slot_2_20260403_101623.avi wd_fgc_stream.json \
    $I/white-duo-full.timeline.json $I/white-duo-full.json wd_audit 32 7
LD_LIBRARY_PATH=$(ls -d /data/WallDance/application/.venv/lib/python*/site-packages/nvidia/*/lib | tr '\n' ':') \
  $PY lookdeeper.py $REC/4_TANGO_HANGAR-whitebg3/recordings/slot_2_20260403_101623.avi wd_ds4.npy \
    $I/white-duo-full.timeline.json $I/white-duo-full.json wd_lookdeeper.json 120
$PY fg_timing.py $REC/4_TANGO_HANGAR-whitebg3/recordings/slot_2_20260403_101623.avi
$PY pendulum.py $I/aerial_full.timeline.json /data/WallDance/application/tests/scenarios/hangar-aerial-full.json --anchor 790,130
```

The timelines come from `tests/replay.py --scenario <full manifest> --score --quality --trt --engine-dir
/data/WallDance/models/dev37 --timeline … --internal`. The full-take manifests are `inputs/*-full.json`: the window
manifests extended to the whole take, with N = 2 throughout, verified by take sheets in PLAN A1. On tonight's takes,
use `--bg plate --plate a:b` with the frame range of slot 1, decoded as a separate stack. The `.npy` stacks were
deleted after the run to free tmpfs; `decode_cache.py` rebuilds them in 1-3 min.

---

## 5. Considered and rejected (one line each)

- **Thermal camera, depth sensor, LiDAR:** not at hand, nothing bought before the demo.
- **Cross-polarisation:** works at 850 nm only with NIR polarisers we do not own, costs more than half the light, and would also suppress bead retro return.
- **Dark backdrop:** the venue's wall is given, and a bright plain wall is the best case for the foreground anyway.
- **Frame differencing as the primary detector:** a dancer hanging still vanishes; the clean plate does not have that hole.
- **Segmentation model (yolo11-seg) for a mass centroid:** same GPU cost as pose, and the foreground gives a mass centroid for free.
- **Higher-resolution YOLO tiles at 25 m:** 960 crops did not beat 640 (§4.7); at 25 m pixels are not the limit. Keep for 40 m with tight ROIs (REVIEW §4).
- **Local second YOLO crop pass on loss, this week:** +13 points over reading sub-τ boxes, half of it rope false positives, +7.7 ms per crop on the laptop.
- **Ultralytics ByteTrack / BoT-SORT as the tracker:** no known N, no motion fallback, dying tracks, camera-motion compensation and ReID useless here. Borrow the low-score second association instead (#6).
- **40 fps capture:** doubles YOLO cost and halves light per frame for little gain at these speeds.
- **Mono12 path:** quantisation is already below sensor noise at 27.8 dB gain. Keep Mono8, which the recordings are faithful to.
- **2×2 binning:** halves dancer pixels below YOLO's floor; the foreground downscales in software.
- **MOG2 shadow detection as the shadow fix:** a ratio heuristic tuned for colour; on mono IR the physical fix (#1) is simpler and total.
- **Binding warm-up tracks or a drift guard in the slot layer:** already measured and rejected in REVIEW §1.2-1.3.
- **TD-side smoothing:** adds lag on top of the One-Euro. Fades on `/dancer/state` yes, extra filtering no.
- **Moving projectors closer to the wall for more body light:** off-axis again (shadows, no belt). Only revisit if slot 8 shows YOLO starving on-axis, and then with narrower beams (HW-1) rather than distance.
- **Border-entry prior as a hard rule:** wrong after contact splits and restarts; foreground support tests the real property (static ghosts) directly.

---

## 6. What this changes in this week's plan (proposals)

1. **Tomorrow's laptop window (PLAN §B):** add a 10-15 min step after B3.
   - Run `nlock.py` / `rejstats.py` with slot 1 as the plate on slots 4/5/6/9 (`wdremote py --with fg_lib.py`; only JSON comes back).
   - Read slot 8 vs 4 for body DN, YOLO confidence and shadow halo, not only the belt.
2. **D16 flips:** build the clean-plate capture at Calibrate instead of fixing the GUI text. It is the input of #2.
3. **A foreground hook in the slot layer** (veto + weak measurement + re-acquire), output-only and replay-gated through a sidecar. It sits next to the main session's sub-τ and warm-up weak measurements, on Thomas's go. Ship it for the demo only if the gate on tonight's takes is clear; otherwise it is the first item after the demo, ahead of CONT-3.
4. **An "always N" switch for the demo** (#3), with `/dancer/state` ON if the TD patch can fade (D22).
5. **Drop from consideration this week:** a second local YOLO pass, a tracker swap, and camera mode changes beyond the slot 7 exposure ladder.
