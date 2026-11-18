# Subsystem map — tests-harness

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

Everything that verifies WallDance away from a live show. Under application/tests: about 95 pytest files (806 test functions by grep) and a headless replay harness that pushes recorded IR takes through the real FrameProcessor (replay.py), scores them against per-scenario ground truth (scoring.py, continuity.py, output_quality.py), caches the YOLO pass for knob searches (detect_cache.py, tune.py, known_n.py), freezes goldens (golden/*.json trio, golden/decomp-phase0 archive, golden/emitted slot-stream fixtures), soaks the pipeline (soak.py) and rehearses the operator Calibrate flow on takes (flow_check.py). CI (.github/workflows/ci.yml) runs the import-light part on Ubuntu py3.10/3.12 and Windows py3.12; GPU, footage and TensorRT checks are opt-in on a rig (WD_RUN_REPLAY=1). The directory is also product code: the app's Dry-run, Auto-tune and Known-N buttons subprocess tests/replay.py, tests/calibrate_segment.py and tests/known_n.py.

## Architecture

- replay.py: `_build_processor(config, ...)` hand-maps a flat config onto ProcessingSettings, DancerTracker and ImageEnhancer; `replay_recording()` decodes with cv2, calls `proc.process()` per frame, returns a summary (tracker event-log counters via analyze_session.collect_stats/classify_tracks) plus `per_frame` rows: `reported/ids` (tracker stream), `emitted` (identity-slot stream), `ref` (that frame's YOLO dets), `int` (internal tracks).
- scenarios/*.json (19): window, expected_count, pinned config snapshot, recording fingerprint, pass line.
- Metrics, stdlib only: scoring.py (count score, evaluate_pass), continuity.py (C1-C10, window pass rate), output_quality.py (jitter, lag, id switches, on_dancer; tracker vs emitted).
- detect_cache.py pickles per-frame dets + motion gray and replays through FrameProcessor.replay_gpu_cached; tune.py (ScenarioEnv, Tuner, coord/grid/random); known_n.py (6-knob search, saves into the project).
- sweep_project.py, calibrate_project.py, calibrate_segment.py, batch_projects.py: subprocess replay.py and scrape its stdout.
- emitted_golden.py + slot_replay.py run IdentitySlots over stripped timelines.
- Gates: test_regression_replay.py (trio), replay_sweep.py (byte compare), test_emitted_golden.py (CI), soak.py, flow_check.py (real CalibrationFlows, HTML report).
- Callers outside tests: app.py (three buttons), extra/wdremote.py (`pytest`, `replay`), extra/wdslot.py (`release-check`).

## Data flow

Take (.avi + .meta) -> cv2.VideoCapture -> InputTransform -> FrameProcessor.process (GPU enhance -> YOLO .pt or TRT engine -> crossval/exclusion/cold blobs -> tracker -> identity slots -> emit) -> per-frame rows -> timeline JSON -> scoring / continuity / output_quality -> printed blocks and a lean summary JSON. The summary is compared to golden/*.json (7 counters, absolute tolerances) or byte-compared to golden/decomp-phase0; sweep tools parse the printed SCORE / PASS LINE blocks and may save a config into projects/<p>/.

Cache path: one TRT pass -> tests/cache/*.pkl (about 188 MB per 300 frames) -> replay_gpu_cached -> same rows, about 10x faster.

Emitted path: a timeline recorded once on dev37 -> stripped .json.gz fixture -> IdentitySlots.update -> stream compared at +/-1 px, plus KPI floors.

Live sessions: TrackingLogger JSONL -> analyze_session.py.

Manifest N comes from operator contact sheets at a 20-frame stride; positions come from the same run's YOLO detections (pseudo ground truth).

## Threading and state

Mostly single-threaded scripts. Importing replay.py may os.execv the interpreter once to fix LD_LIBRARY_PATH (replay.py:32-56), so GPU tests and sweep tools run it as a subprocess; this also isolates CUDA. soak.py has one daemon StallSentinel thread that dumps stacks.

Determinism: the tracker's global id counter is reset per replay, the output stage runs on an injected `frame/fps` clock, and grep finds no wall-clock calls in tracker.py, identity_slots.py, motion_detector.py, belt_detector.py, foreground.py or auto_height.py.

State on disk: tests/cache/*.pkl (gitignored; 1.2 GB of June pickles present), golden/, scenarios/, outputs under tmp_analysis/ (soak, batch_projects, the known_n oracle), plates written into projects/<p>/plates by flow_check.py, timestamped config saves by known_n.py and batch_projects.py (they restore last_project). Every script mutates sys.path at import. Recordings, models and projects are untracked and machine-local.

## Failure handling

Loud where it was designed: replay.py refuses to run without a config (SystemExit, :143-149, :828-833), on a fingerprint mismatch (:153-179), a missing engine (:226-229) or an inactive GPU path (:547-549). evaluate_pass fails a line whose metric was not computed (scoring.py:294-303). Rig tests skip with stated reasons (`-rs` in CI).

Silent where tools were chained: sweep_project._run swallows every exception and returns (None, None) (:52-59); batch_projects turns that into a 9.9 sentinel with no guard, while calibrate_segment aborts on all-9.9 (:102-107). test_emitted_golden skips the exact compare when numpy/scipy differ from the stamp. `wdslot release-check --tests` prints a failure and still returns 0.

soak.py: FAIL on a sentinel stall, RSS slope or fps degradation; writes a partial summary on Ctrl+C. No pytest markers or tiers: gating is importorskip plus the WD_RUN_REPLAY env var.

## Tunables

About 60 constants, all module-level in the test files.
- scoring.py: 4 weights (drop 1.0, ghost 1.0, frag 0.3, id 0.1); pass lines live per manifest (class A 0.05 / 0.05 / 1.0 s, class B 0.10 / 0.15 / 2.0 s, S none).
- continuity.py: tol_h 0.75 body heights, ref min_conf 0.5, window 300 frames, long-span line (coverage >= 0.98, 0 gaps >= 1 s, <= 5 gaps/min, <= 1.5 ids/dancer, spatial >= 0.97).
- output_quality.py: 7 thresholds in body heights, frames and h/s.
- test_regression_replay.py: 6 absolute tolerances plus 5 % relative.
- test_emitted_golden.py: 6 fixtures x 3 KPI floors, 1 px position tolerance.
- known_n.py: 6 knobs / 24 values; tune.py: 4 knobs; CLAHE and motion grids in the sweep tools.
- detect_cache.py: 20 REBUILD_KEYS.
- soak.py: 50 MB/h RSS slope after 30 min, -15 % fps, 120 s stall, 2000-frame chunks.
- flow_check.py: FPS 20 hard-coded, hole >= 1 s, jump > 1 h in 3 frames or > 2 h in 10.
- analyze_session.py: real >= 20 MATCH events, ghost < 5.
- Per manifest: warmup 15 frames, fps, exclude_spots in px.

## Interfaces with neighbours

- Pipeline: process() returns the tracker stream; FrameProcessor.last_emitted is what OSC sends. Any consumer that wants show output must read `emitted`. replay_gpu_cached (application/src/core/pipeline.py:825-859) is a third hand-kept copy of the live post-YOLO tail; its equivalence is checked only on the rig, for reported/ids of two scenarios (test_gpu_cache_fidelity.py:49-54), not for the emitted stream.
- App: application/src/app.py:2410-2416 runs Dry-run without --trt, so it uses the PyTorch path, needs .pt weights on the laptop, and shows the operator real/marginal/ghost track counters (application/src/gui.py:2988-2996) that are easy to read as dancer counts. app.py:2592-2598 runs Known-N, which writes about 188 MB of detect cache per confidence value into tests/cache.
- Recorder: .meta carries actual_fps = frames/elapsed only; replay assumes evenly spaced frames.
- Motion: replay starts MOG2 cold at the window start with a dancer in frame (replay.py:570-574; learn rate 0.001, history 500) and excludes only 15 frames, so 300-frame windows may be scored against a background state the live show does not have. Mechanism read from code; magnitude not measured.
- Detect cache: the key (application/tests/detect_cache.py:57-93) has no engine identity, code version or recording fingerprint. tune.ScenarioEnv (application/tests/tune.py:56-65) skips check_fingerprint, the manifest `video` override, fill_detection_defaults and max_dancers that replay.py:822-852 applies.
- Tracking logger: golden summaries depend on the event names MATCH, NEW_TRACK, MAHALANOBIS_GATE, DORMANT, RESURRECT (application/analyze_session.py:134-157).
- Remote ops: `wdremote pytest` cannot enable WD_RUN_REPLAY; `wdslot release-check --tests` uses a POSIX venv path, so it runs on a dev box only.
- The harness writes outside tests/: tmp_analysis (soak.py:190-192, batch_projects.py:33; known_n.py:47 reads an oracle there), projects/<p>/plates (flow_check.py:153-157), and project config saves (known_n.py:93-112).

## Strengths

- Scenario manifests pin a full config snapshot and a recording fingerprint, and replay refuses to fall back to defaults (replay.py:130-179); this came from a real incident where goldens were orphaned by a project reorganisation.
- The cost tiers are well designed: stdlib-only scoring/continuity/output_quality unit-test instantly; the detect-pass cache has its own equivalence tests (test_gpu_cache_fidelity.py, test_detect_cache.py:95-114); emitted fixtures rerun the slot layer in milliseconds without GPU or footage.
- Replays are built to be deterministic: injected frame clock (replay.py:376-385), track-id reset, no wall clock in the core modules, and a bit-identity test for the OpenCV thread-count change (test_cv2_threads_bit_identity.py).
- Metric design is self-critical: on_dancer exists because C8 flattered streams that drop out when the dancer is hard (output_quality.py:365-370); the long-span suite exists because 300-frame windows hid the continuity problem (scenarios/README.md:79-89); evaluate_pass fails on a metric nobody measured (scoring.py:272-274).
- Fault-injection tests target real field failures with fake clocks and fakes instead of hardware: per-frame exception boundary, crash marker and isolated shutdown (test_main_loop_guards.py), camera stall with and without D34 side by side (test_camera_stall.py:184-313), IDS stall classification from frame ids and GenTL counters (test_ids_stream_diag.py), OSC send failure reduced to one alert.
- CI is deliberately import-light and includes a Windows job for the launcher's git-update safety (ci.yml:42-66); static wiring tests catch GUI callback, command-registration and layering breaks without DearPyGui (test_runtime_api.py:263-329).
- Field failure modes are encoded as named fixtures with stated intent: injected static ghost, bdx-s5 static figure, wallhang-still (test_emitted_golden.py:41-49).
- D33 default-versus-stored semantics are covered end to end, including a subprocess probe that replay builds the same processor (test_d33_defaults.py:77-112, 273-309).
- calibrate_segment.py:102-107 refuses to emit a seed when every sweep score is the failure sentinel.

## Questions the reader could not answer from code

- When, and on which machine, were test_regression_replay and test_gpu_cache_fidelity last run green? The x-pose 1280 and 960 engines in models are dated 2026-10-05, the trio goldens 2026-06-24, and the test's own docstring says to re-baseline on any TensorRT bump.
- Is stable per-dancer identity (slot k keeps driving dancer k's video) now a show requirement, or is "rough identity, swaps acceptable" (scoring.py:10-14, id weight 0.1) still the contract? This decides whether duo swap labels and an identity metric are worth building.
- Are the Auto-tune, Known-N and Dry-run buttons meant for the on-site technician? Auto-tune cannot currently succeed and Known-N only works for projects named in committed manifests; if they are developer tools, should they leave the operator GUI?
- Is dev37's stack (numpy 2.2.6 and TRT 11.3 per the fixture stamps) deliberately different from the laptop's pinned stack, and which box is the reference for goldens?
- Will the October field takes (projects mur25m-night-common, mur25m-ceinture-0610 and mur30m-0710 exist locally; no manifest references them) enter the corpus, and who labels N and positions?
- Is CI enabled and green on origin/release today, and is release ever moved before CI has finished?
- Is re-pinning the corpus to the D33 defaults (the CONT-2 item named in the long-span manifests) planned before the next show?
- Are replay_sweep.py with golden/decomp-phase0, replay_report.py and analyze_log.py still in use, or can they be deleted?
- Do you need to reproduce a stall that happened inside a recorded take offline? That requires per-frame timestamps in recordings.
