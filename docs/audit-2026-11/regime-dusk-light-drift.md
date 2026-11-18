# Production regime — dusk-light-drift

*Third pass, run after the owner confirmed this regime is part of the production choreography. The real chain (dedupe, cross-validation, tracker, identity slots, OSC) was driven on CPU with synthetic detections at the shipped defaults. This proves the logic path and its time constants. It does not say how often the path is taken on real footage.*

**Severity for production: high** · confidence medium

## What the shipped code does

In RUN nothing follows the light except the clean plate.

Calibration: exposure/gain (servo, ROI median 70), gamma, CLAHE, MOG2 var/scale, the plate with its noise sigma, the empty-wall YOLO verdict and the dial anchors are written once. ExposureServo has one caller (calibration_flows.py:260), so the raw wall level falls with the daylight (70 to ~21 DN at 0.31x).

YOLO: confidence stays 0.15 and the dial cannot go lower (seed equals SENS_CONF_MIN). The enhancement is the one that was ghost-free on the dusk wall; a later Calibrate tries the incumbent first, so it never steps up.

Crossval: a detection survives on frame-diff (15 DN on the gamma-lifted gray, 2 % of the box), or 8 keypoints averaging 0.45, or a live track nearby. A dim or still dancer at night can have none and is dropped as a ghost.

Motion: the low-light switch (post-gamma mean < 55) doubles MOG2 var and moves three thresholds. With gamma 0.8 or less it is on from Calibrate; with 1.0-1.54 it flips mid-fade.

Plate: brightness-normalised (clamp 0.25-4x) and slowly blended outside protected dancers (~25 s), so it follows a 20-60 min fade. Its threshold is frozen in DN while body contrast shrinks. A change faster than a few minutes, or a restart (the live plate is never saved), makes it stale: support() returns 1.0, and ghost veto, foreground hold, belt backing and the entry waiver switch off together, unlogged. On remote-ops it stays stale; on release it re-blends in ~35 s. A slot held by foreground alone is unprotected, so the update absorbs a still dancer.

Slots and OSC add no light rule; they inherit holes, or ghost points once the veto is off.

Monitoring: light_changed fires once outside 0.667-1.5x, with a remedy that needs an empty wall. No brightness alert; fps is unaffected; the Show/Rehearsal switch does not carry the plate; the height guard gets no samples once head/ankle keypoints fall under 0.5.

## Rules that decide the outcome

| Stage | Rule | Where | Effect | By design |
|---|---|---|---|---|
| calibration / camera | Exposure and gain are written once by the Calibrate servo (ROI median 70 +/- 12) and never driven in RUN | application/src/runtime/calibration_flows.py:260-262, 344-358; core/config.py:640-641; runtime/camera_controller.py:411-444 | The raw wall level falls with the daylight (70 to ~21 DN at 0.31x). Every threshold expressed in absolute DN downstream drifts against the image. | yes |
| detection (YOLO threshold, operator dial) | Confidence fixed at 0.15; the Drops/Ghosts dial interpolates from the seed to SENS_CONF_MIN, and the D33 seed equals that minimum | application/src/core/pipeline.py:820; core/sensitivity_macro.py:48-52; core/config.py:54, 726, 1045-1047 | macro_to_settings(100, 0.15, 16) returns confidence 0.15 (run here). The operator has no live lever to admit weaker boxes at night; dial 75-100 only lowers MOG2 var 16 to 8. | unclear |
| calibration (enhancement) | Empty-wall YOLO check tries the incumbent gamma/CLAHE first and its ladder only steps down; the verdict holds for the light at Calibrate | application/src/core/empty_wall.py:89-104, 119-125; runtime/calibration_flows.py:247-250, 486-497 | YOLO gets at night the enhancement that was ghost-free on the dusk wall. A re-Calibrate cannot raise it. Whether false persons appear on the darker wall is not checked (needs footage). | yes |
| detection filter (motion cross-validation) | Keep a detection if frame-diff ratio >= 0.02 (x1.2 in low light) at a fixed 15 DN on the gamma-lifted full-resolution gray, OR >= 8 keypoints with mean >= 0.45, OR it is near a live motion-confirmed track | application/src/core/pipeline.py:2287-2294, 2344-2382; core/motion_detector.py:520-576; core/config.py:962-971 | Synthetic: moving bodies 30 % darker / 15 % brighter than the wall fail the 15 DN test from 0.83x light (gamma 0.8), 0.65x (1.0), 0.31x (1.54). With stand-in keypoints at 0.35, 1748 detections were rejected in 90 s and two of three dancers never got a point. | no |
| motion model | Low-light switch at post-gamma ROI mean < 55: median 5 + Gaussian 5 preprocess, MOG2 var x2, min blob area x1.8, crossval ratio x1.2; no hysteresis | application/src/core/motion_detector.py:204-209, 269-273, 355-357; core/pipeline.py:2291-2294; core/config.py:944-950 | Gamma 0.8 / 0.73: already on at Calibrate (mean 51.8 / 44.7), so the calibrated var is doubled all show. Gamma 1.0 / 1.27 / 1.54: flips at 0.77x / 0.51x / 0.34x. At the flip: one toggle and no MOG2 flood in the synthetic run (blind lead not reproduced). | unclear |
| foreground: gain normalisation and threshold | gain = median(frame / plate) clamped to [0.25, 4]; threshold = max(2.0, 4 x sigma measured at capture x sqrt(max(1, gain))) | application/src/core/foreground.py:71-72, 117-123, 246-253 | Uniform dimming is tolerated down to 0.25x (stale at 0.20x). The threshold stays 2.6-5 DN while body contrast falls with the light, so the relative contrast a body needs is 3.2x higher at 0.31x. A low-contrast dancer's support went 0.40 to 0.08 over a 20 min ramp. | yes |
| foreground: stale test | More than 25 % of the ROI differs: frame flagged 'plate stale', FgFrame invalid; support() returns 1.0 and the slot layer receives fg = None | application/src/core/foreground.py:126, 159-163, 257-261; core/identity_slots.py:788, 543-546, 608, 1003-1004, 1288 | Ghost veto, foreground hold, belt backing and entry waiver switch off together, with no log line and no alert. With IR constant and daylight fading, the plate as captured is stale by 0.88x-0.68x true light (once 20-40 % of the daylight is gone). | yes |
| foreground: selective update | Every 10 frames blend 2 % toward frame / gain outside the protect boxes; a stale frame is never blended (0e125cc, remote-ops only); the updated plate lives in memory only | application/src/core/foreground.py:130-134, 186, 237, 292-318; core/pipeline.py:1310-1323, 1527-1528; docs/PLAN_25M_2026-10.md C.15 | Follows 60 / 20 / 5 min fades to 0.31x with 0 stale frames; a 2 min fade is stale from 0.50x. After a fast change remote-ops stays stale (608 of 610 analysed frames), release 63f4989 recovers in ~35 s. A restart reloads the captured file, which is stale at night. | yes |
| foreground: protect list | Only LIVE slots, backed belts and tracks with a skeleton <= 20 frames old are protected from the update | application/src/core/pipeline.py:1453-1468 | A still dancer YOLO has lost (slot in state fg) is blended into the plate: body evidence gone after 23-78 s at Calibrate light, 3.5-58 s at 0.31x, and an imprint blob of 0.05-0.24 h2 stays 8-60 s where the dancer was. | no |
| identity slots: ghost veto | Entry, rebind and yield are refused when the foreground share of the 0.45 h x h box is under 0.10; continuation on the same track is exempt | application/src/core/identity_slots.py:356, 543-546, 936, 976, 1182 | Plate valid: a static false-person probe is vetoed (0 ghost frames, 516-605 vetoes per 90 s). Plate stale: the probe holds a point in 67-92 % of frames and, with all 3 slots emitting, static-yield gave the hanging real dancer's id to the newcomer (hole 42 s). | yes |
| ops monitor | light_changed: 1 Hz ROI median of frame / snapshot as captured, 10 s median (>= 5 samples), band [1/1.5, 1.5], one alert per episode, kept in the alerts strip | application/src/core/ops_monitor.py:229-234, 502-522; core/config.py:357-359; runtime/main_loop.py:1352-1386, 636-659; ui/adapter.py:34, 345-348 | Fires about 10 min into a 20 min fade to 0.31x. The text keeps the first ratio. Both remedies are impossible in RUN: Calibrate needs an empty wall, and playing the empty take replaces the live camera. A plate that is stale between 0.88x and 0.667x raises nothing. | yes |
| lighting profiles | Show / Rehearsal switch applies 14 light-coupled keys (exposure, gain, gamma, CLAHE, MOG2, confidence, dial, exclusion); the plate and calibration_state are shared | application/src/core/config_schema.py:32-47; runtime/config_manager.py:352-370; app.py:1860-1870 | A manual dusk-to-night step exists for the 14 keys (Expert tools), but plates/latest.npz belongs to whichever profile was calibrated last. A night plate used from dusk was stale for 2227 of 2520 analysed frames on remote-ops. | unclear |

## Reproduction

CPU only, synthetic images, real classes. Scripts and outputs are in tmp_analysis/audit-2026-11/dusk/ (scene.py: textured wall, door, two constant IR hot spots plus a fading daylight gradient, shot + read noise, wall at 70 DN at Calibrate).

fg_dusk.py (ForegroundDetector, shipped FgParams):
- Uniform gain, plate as captured: valid from 1.0x to 0.25x, stale at 0.20x.
- IR constant, daylight 30/50/70/80 % of the light, as a step: stale by 0.88/0.80/0.72/0.68x.
- Same as ramps with the shipped update and 3 protected dancers: 60, 20 and 5 min to 0.31x give 0 stale frames (5 min: stray blobs up to 0.57 h2); 2 min is stale from 0.50x; 30 s from 0.76x.
- 5 s change, then 5 min of night: remote-ops stale 608/610 analysed frames; release 63f4989 foreground.py 71/610 (~35 s).
- Unprotected still dancer: evidence gone at 23-78 s (1.0x), 3.5-58 s (0.31x).

motion_dusk.py (MotionDetector): low-light flip at 0.34/0.51/0.77x for gamma 1.54/1.27/1.0, already on at 0.8 and 0.73. The frame-diff gate fails for the lower-contrast moving bodies from 0.83x (gamma 0.8), 0.65x (1.0), 0.31x (1.54).

chain_dusk.py: tests/replay._build_processor with DETECTION_DEFAULTS (not default: max_dancers 3, height 130, gamma 0.8, var 16), then height guard, dedupe, replay_gpu_cached. 90 s, three dancers (one hangs 25 s, one enters from the top), a static false-person probe. YOLO is a stand-in.
- Plate followed, YOLO perfect: coverage 0.97-0.99 at 1.0/0.65/0.31x, 0 ghost frames, ids stable.
- Plate followed, YOLO fading: 0.65x longest hole 1.5 s, one id switch; 0.31x (keypoints 0.35) 1748 crossval rejections, two dancers never emitted.
- Plate as captured (stale 100 %): probe point in 1209/1800 frames, hanging dancer hole 42.45 s (chain_events.py: a 'yield' at 33.35 s); fading at 0.65x: holes 7.65 s and 16.3 s.

veto_dusk.py: a body within ~10 % of the wall at 0.31x with a 5 DN threshold has support >= 0.10 in 2 % of frames.

These prove logic paths, not field rates: YOLO's response to a 3x darker frame and the real IR/daylight patterns are not measured. git status was clean after the runs.

## Existing coverage

Unit: application/tests/test_foreground.py covers a uniform step to half light on a flat wall, the selective update on a lamp region (update_every 1), light_ratio (3 tests) and no-update-on-stale (3 tests). tests/test_ops_monitor.py:491-537 covers the alert (once per episode, band edges, re-arm on a new snapshot, readiness row). tests/test_sensitivity_macro.py, test_empty_wall.py and test_calibration*.py cover the pure logic of the dial, the ladder and the servo.

Not covered by any test: a ramp, a non-proportional change, how long the plate stays stale, a plate reload after restart, the low-light flip in the chain, crossval under dimming.

Footage: the 2026-10-06 night project replays a bright empty-wall plate (21:16) against takes ~3x darker (PLAN_25M C.12, C.15 rows 's1 s2c..s7', plate gain 0.25-0.36). That is a step between takes with one walker. Goldens outdoor-night and dark-crowd have static light. No take has the light changing inside it; no dark take has two dancers.

## What the technician and TouchDesigner see

Operator: about halfway through the fade (0.667x) one 8 s red toast and a strip line, "The light is 0.66x the empty-wall snapshot: Calibrate on the empty wall...". The number never updates. The "empty wall" status line refreshes only on operator actions, a stale plate raises nothing, fps and GPU look normal. The advice cannot be followed with dancers on the wall, and playing the empty take takes the camera off the show. The dial's loose half does nothing to confidence.

TouchDesigner: points hold while YOLO holds. Later, still or newly entering dancers can lose or never get a point (video dropped, D5). If the plate is stale, a point can sit on a wall figure.

## Smallest fix inside the current design

An opt-in "light follow", set at Calibrate. In RUN, when the existing 10 s light ratio leaves +/-15 %, step the camera gain by 0.5 dB (at most one step per 10 s, +12 dB in total, never past the camera maximum, with the servo's clip check). Log every step and show "+4.5 dB since Calibrate" in the strip with a Revert button. Gain is multiplicative, which the plate normalisation already absorbs (verified 0.25-4x), and it keeps the DN thresholds where they were calibrated.

Companions: save the live plate each minute and reload it on restart; re-anchor after 30 s stale at a steady gain (logged); protect foreground-held slots from the update; re-measure plate sigma online; waive the veto for a track that travelled 0.25 h; keep the alert ratio current, add "foreground off", drop the empty-wall remedy in RUN; let the dial go below 0.15.

Stay manual: exposure time, gamma/CLAHE, MOG2, confidence seed, plate capture, profiles.

## What a re-founded estimator would do

Treat light as logged state rather than a frozen constant: exposure, gain and a two-coefficient illumination fit per frame (an IR-only plate and a daylight plate, coefficients solved on unprotected pixels). A non-proportional fade or a step is then two numbers with timestamps.

Foreground becomes a typed measurement in sigma units of an online per-pixel noise estimate, so its sensitivity does not shrink with the light. Skeleton confidence enters as a likelihood that widens gates, never as a hard 0.45 or 15 DN admission test.

With known N and time-based persistence, a dancer who was present and has not been seen leaving stays present while the foreground still shows a body there. Weak evidence at night lowers certainty instead of deleting the point. Ghost rejection comes from N and the foreground likelihood.

Calibration captures the two bases once per venue. The bounded gain follow is part of the estimator, not a patch.

## Footage that would confirm or refute it

1. One continuous EMPTY-wall take from Calibrate at dusk to full night (30-60 min, exposure and gain fixed, camera log on). It gives the real light-ratio curve, how non-proportional the change is (whether and when the captured plate goes stale), noise against light, and YOLO false persons on the empty wall as it darkens.
2. One continuous take over the same fade with three dancers: a hang of 10 s or more, an entry from the top, contact. Calibrate at dusk only. Score it at three light levels.
3. Optional: an IR-only empty-wall take at full night the evening before, to evaluate a night plate or a two-plate model.

## Questions for the owner

- When is Calibrate done relative to sunset, and what share of the wall light is daylight then? Is the ~3x fall of 2026-10-06 typical for the show?
- Can an empty-wall Calibrate be done at full night (IR only) the evening before, on the same rig?
- Is any light in the 850 nm band switched on during the piece (halogen or sodium street or architectural lamps), giving steps rather than a fade?
- Is a bounded automatic camera-gain follow acceptable (opt-in at Calibrate, every step logged, a Revert button), or must gain stay frozen?
- What exposure and gain did the night takes run at (the rig sheet is empty), and is there gain headroom? Would more IR make daylight a minority of the wall light at show start?
- Can the app restart mid-show (crash, D13)? If so the followed plate must be saved, because the captured one is stale at night.
- Should 0e125cc (a stale plate stays stale until re-capture) be released as is, given that the light changes in every show?
- What do the dancers wear, and how does it compare with the wall under 850 nm? A body close to the wall's brightness falls under the frozen foreground threshold at night.
