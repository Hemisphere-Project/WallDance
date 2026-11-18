# Subsystem map — motion-calibration

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

Supplies the motion evidence around YOLO and the procedures that set the light-coupled parameters.

Motion: one slow MOG2 ("is there a silhouette here?") plus raw frame differencing ("is it moving now?"). They feed three consumers: the ghost gate on YOLO detections, cold detection of dancers YOLO missed, and the tracker's gap bridging.

Calibration:
- CALIBRATE (operator flow, empty wall, about 20 s): exposure/gain servo on live IDS, gamma seed, MOG2 varThreshold x scale sweep, CLAHE from measured noise, clean plate, then a YOLO check on the empty wall that steps the enhancement down until no person is seen.
- DANCERS evidence pool (expert only since D29): pooled person height and ratios, imgsz, confidence seed, blur budget.
- Two operator dials (0-100, 50 = calibrated seed) mapped onto confidence/varThreshold and bridge sensitivity.
- A guided "Calibrate All" wizard chaining both passes; no widget opens it today.

Everything was read from code only; nothing was executed.

## Architecture

- MotionDetector (application/src/core/motion_detector.py): owns one cv2 MOG2 (detectShadows on, learning rate 0.001), the fg and opened "clean" masks, a full-resolution raw frame pair for frame-diff, and static-cell counters. API: feed_preprocessed, detect, extract_local_motion_blob, motion_ratio_in_bbox, frame_diff_blob_in_bbox.
- MotionModel (core/motion_model.py): thin wrapper plus a Welford noise sigma. FrameProcessor owns the single instance; pipeline and tracker bypass its surface via `.detector` and `_clean_mask`.
- core/calibration.py (pure logic): SceneCalibrator (window, 24-model sweep), ExposureServo, seed_gamma / seed_clahe / cap_gamma_for_noise, scene_report_stats, ExclusionMaskBuilder (auto-collection unused; manual paint only).
- core/calib2.py: SubjectCollector, SubjectPool (JSON runs under projects/<name>/calib2/), aggregate, select_imgsz, advise_model.
- runtime/calibration_flows.py: CalibrationFlows, the state machines stepped once per processed frame by the main loop. Applies results through processor, enhancer and CameraController callbacks; talks to the GUI via the CalibrationUiPort protocol.
- Neighbours it drives: EmptyWallCheck (core/empty_wall.py), CleanPlate / ForegroundDetector (core/foreground.py).
- core/sensitivity_macro.py: pure dial maps; the app owns seeds and anchors.
- ui/wizard_state.py + ui/calibrate_all_wizard.py: state machine and DPG renderer.
- core/background.py: legacy static-reference subtractor applied before YOLO in GpuPipeline, default off, still in the GUI.

## Data flow

Runtime: raw CPU frame -> ROI crop -> mono gray -> enhancer gamma LUT (no CLAHE; pipeline `_fixed_gamma_for_motion`) -> MotionModel.feed -> preprocess (Gaussian 3x3, or median 5 + Gaussian 5x5 when mean < 55; INTER_AREA to mog2_scale) -> MOG2.apply -> mask (0/127/255) and clean mask. The un-blurred full-resolution gray pair is kept for frame-diff.

Consumers:
- Ghost gate: frame-diff fraction >= 0.02 at 15 DN, or strong skeleton, or near a live track.
- Cold blobs: detect() -> frame-diff gate -> tracker.
- Tracker bridge: local MOG2 blob -> presence ratio -> frame-diff at 6 DN.

CALIBRATE:
1. Live IDS only: servo drives ROI median to 70 +/- 12 (exposure up to the 25 ms blur budget, then gain).
2. Gamma seed toward 110.
3. 90-frame window: 8 var x 3 scale MOG2 candidates scored by median 8x5-tile foreground fraction <= 0.5 %; noise sigma; report stats.
4. Apply var, scale, CLAHE; cap gamma at 1.8 if sigma > 4.
5. 40-frame plate, saved at once.
6. Empty-wall check: incumbent enhancement first, then a downward ladder; 120 clean frames to pass.
7. Report dialog, optional Save.
Playback variant: exposure/gain from the take's .meta, no servo.

DANCERS: 240-frame run (heights, box conf, speed, fps) -> JSON + 12 JPEGs -> pooled aggregate -> height, ratios, confidence, blur budget; imgsz on explicit Apply.

## Threading and state

Threads:
- MOG2 feed runs on the pipeline's single motion worker; the future is awaited before crossval and the tracker read the masks.
- All calibration stepping runs on the main loop thread after each processed frame: the 24-model sweep (every 2nd frame), plate and JPEG writes, every apply.
- Commands arrive through the RuntimeAPI queue. No locks; safety rests on that await point.

State:
- MotionDetector: MOG2 model, masks, raw pair, base varThreshold, scale. Reset on scale change, tracker reset, input-transform change; not on gamma change.
- MotionModel: Welford accumulators that grow from the last reset.
- CalibrationFlows: `_calibrating`, `_servo`, `_wall_check`, `_wall_conf`, `_pre_enhancement`, `_scene_pending`, `calibration_state` (which phase set which knob and when), `_last_calib1_noise_sigma` (reused for 600 s), `blur_budget_ms`.
- App: confidence seed, var anchor, dial positions.

Persistence:
- 14 lighting-profile keys (exposure, gain, gamma, CLAHE, var, scale, confidence, dial and anchors, exclusion) are written only on explicit Save.
- The plate goes to plates/latest.npz immediately; Calib2 runs are written immediately.
- The plate and calibration_state are shared across profiles.

All windows are frame counts (90 / 240 / 120 / 40), so durations follow the actual fps (IDS acquisition 20 fps).

## Failure handling

At calibration time:
- Start refused (no model, no frames): toast.
- Second press cancels with a toast; exposure, gain and seeded gamma are not rolled back.
- Servo failures ("step limit", "at exposure+gain limits - add IR", "too bright at minimum") appear only as a line in the report dialog.
- Sweep with no candidate under 0.5 %: var 120 at scale 0.5, `var_saturated`, 6 s toast.
- Empty-wall check with no clean rung: weakest rung kept, 8 s toast naming x, y.
- Plate capture exception: toast, loop continues.
- No timeouts: a stalled camera or paused playback leaves "Calibrating N%" until frames resume or the operator cancels.

At runtime:
- light_changed alert: ROI/plate brightness median over 10 s outside 1/1.5 to 1.5, once per episode; needs a plate.
- Plate status stale / size: foreground veto silently disabled (support() returns 1.0).
- Height guard auto-adopts person height; height_stale alert.
- Readiness rows: save age > 24 h, empty wall, dancer size.

Not watched: MOG2 false-positive level, frame-diff noise floor, exposure clipping or darkness, YOLO ghosts after calibration. No automatic re-calibration of exposure, gamma, CLAHE or var. Console lines `[Calibrate]`, `[EmptyWall]`, `[Calib2]` go to the app log.

## Tunables

About 75-80 named constants in application/src/core/config.py feed this subsystem: AUTOCAL_* (32, including 5 unused EXCL_*), AUTOCAL2_* (13), SENS_* (6), MOTION_BRIDGE_MOG2_* and MIN_AREA (6), MOTION_LOWLIGHT_* (3), frame-diff (4), LIGHT_CHANGE_* (3), plus a few singles.

About 35 inline magic numbers on top:
- motion_detector.py: peak-diff 8, kernels 3 / 5, static cell 32 px, max blob 25 % of frame, height 0.3-2.5x, aspect 0.15-3.0, merge gap 0.5 h.
- calibration.py: focus < 50, uniformity < 0.25, clip >= 250, servo factors 1.3-2.5 / 0.4-0.8 / 3 dB, min exposure 200 us.
- empty_wall.py: 120 frames, settle 4, margin 0.8, gamma floor 0.8, CLAHE steps 1.5 / 1.0.
- foreground.FgParams: 13 fields.

Units are mixed: 8-bit DN on gamma-lifted gray (MOG2, frame-diff) versus raw DN (servo, plate); frames (comments assume 20, 24, 25 or 30 fps in different places); px in original versus downscaled space; dB; us / ms.

Calibrated values per project: about 14 persisted (exposure, gain, gamma, CLAHE, var, scale, var anchor, confidence, confidence seed, height, min/max ratio, imgsz, blur budget), the plate file and 2 dials.

Dependency chain: blur budget -> servo cap -> exposure/gain -> brightness -> gamma seed -> (var, scale, sigma) -> CLAHE and gamma cap; confidence -> wall-check limit -> final gamma/CLAHE -> MOG2 input and low-light regime; sigma -> Calib2 imgsz target.

## Interfaces with neighbours

- Pipeline: the motion gray is the enhancer gamma applied to raw (application/src/core/pipeline.py:1820-1837), so the enhancement subsystem's gamma is also the motion model's transfer function. Pipeline and tracker reach into `motion_model.detector` and `_clean_mask` rather than the MotionModel surface.
- Tracker bridge: assumes shadow-class pixels are silhouette; uses a 6 DN frame-diff threshold where crossval uses 15. All bridge and warm-up limits are frame counts, so their durations change with fps (20 fps IDS, lower under load).
- Identity slots / foreground: the plate is captured by Calibrate before the empty-wall verdict. A stale plate disables the ghost veto silently (support() returns 1.0).
- Camera: the servo writes through the CameraController callbacks, which also switch auto exposure/gain off. Three different exposure ceilings coexist: 25 ms blur budget, 50 ms for 20 fps acquisition, 66.7 ms manual clamp.
- Config / profiles: the plate and `calibration_state` are shared across lighting profiles; loading `mog2_scale` resets MOG2.
- Ops monitor: the readiness "calibration" row is file age only; the light alert needs a plate.
- Model controller: an imgsz applied from Calib2 can fall back to PyTorch when no engine exists.
- Main loop: `process_wall_ms` is used as fps (excludes camera wait). Calibration stepping, including 24 MOG2 models every second frame, runs on the main thread and slows the loop during the window.
- Remote API: StartCalibration is a CONTROL-level command with no state guard.
- Packaging: Auto-tune shells out to application/tests/calibrate_segment.py from production code (application/src/app.py:2492-2499), so tests/ must ship on the show laptop.

## Strengths

- The var x scale choice is an empirical joint sweep scored with a dancer-robust statistic (median grid-tile foreground fraction), with an explicit saturated flag and toast. The reasoning for rejecting a sigma formula is written next to the constants (application/src/core/config.py:608-620; core/calibration.py:835-862, 894-920).
- ExposureServo is pure, hardware-free logic with a clear authority order (exposure up to the blur budget, then gain; back off gain first). It drives the ROI median rather than the frame mean, and its clipping rule is built not to oscillate with the dark branch. The field regression that motivated it is documented and covered by named tests (core/calibration.py:229-361; config.py:634-648).
- The empty-wall YOLO check closes the loop on the failure that actually occurred (over-brightening makes YOLO see people on the wall): zero tolerance per rung, incumbent tried first, margin below live confidence, confidence restored on cancel (core/empty_wall.py; runtime/calibration_flows.py:479-536).
- Frame-diff has a staleness cap so a frozen frame pair stops reporting the last motion event forever. The label-selection hot paths were vectorised while preserving exact tie-breaking, with the profiling rationale in comments (core/motion_detector.py:251-266, 478-518, 583-608).
- The clean plate is the most principled reference in the area: raw gray, noise-relative threshold, gain normalisation, stale frames rejected and never blended in, selective update with protect boxes. A 1 Hz light-ratio alert works in standby and RUN and fires once per episode (core/foreground.py:117-134, 232-318; core/ops_monitor.py:502-522).
- Calib2 runs carry provenance (ROI, imgsz, lighting profile, confidence kind) with stale flags, legacy units are handled explicitly, and the imgsz pick is bounded by an FPS budget from a per-rig measured table with a rig advisory instead of a silent fallback (core/calib2.py:57-106, 228-268, 368-483).
- Measurement cores (SceneCalibrator, ExposureServo, EmptyWallCheck, calib2.aggregate, WizardState, sensitivity_macro) are separated from orchestration behind a UI Protocol. Seven test files, about 1,950 lines, cover them.
- The height guard (auto re-measure with ownership and spread rules, plus a stale alert) is a working example of the self-healing pattern the other calibrated values lack (core/pipeline.py:1385-1451; config.py:360-394).
- Playback calibration is faithful to the sensor: recordings default to lossless FFV1 (config.py:777) and the take's exposure/gain are restored from its .meta before measuring (runtime/calibration_flows.py:268-280).
- The dial persists its calibrated anchor separately from the live macro output, so a save with the dial off-centre cannot ratchet the calibrated varThreshold (application/src/app.py:1298-1303, 1350-1356).

## Questions the reader could not answer from code

- Is any projector or video light visible through the IR band-pass, so that wall content changes the camera image? That decides whether MOG2 and frame-diff see content as motion, and what the shadow class should mean.
- What light trajectory does a typical show have (dusk into night within one show, lamps added)? Is a bounded automatic exposure/gamma follow-up acceptable, given the stated "no silent auto-tuning" principle?
- Should CALIBRATE be possible in RUN at all, including through the remote API, or is STANDBY-only acceptable?
- Is the D29 flow final (single CALIBRATE; Calib2, Auto-tune and known-N expert-only)? If so, are the Calibrate All wizard and the blur-budget feedback meant to be removed?
- With the clean plate in place, what do the replays show for each bridge tier's contribution (MOG2 local blob, presence, frame-diff, plate hold)? Is MOG2 still earning its calibration surface?
- Were the corpus-tuned varThreshold values obtained with the low-light x2 multiplier active, i.e. are the documented numbers implicitly doubled?
- How often do `var_saturated` or noise sigma > 4 occur on the show rig at high gain, and what does the background frame-diff fraction look like there?
- Which TensorRT engines (imgsz presets) are prebuilt on the show laptop?
- Are the show/rehearsal lighting profiles still used on site, given the "one empty take per lighting" workflow?
- Do the IR illuminators cast dancer shadows on the wall (off-axis), or is lighting near-coaxial?
