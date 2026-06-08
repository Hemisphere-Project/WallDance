> ⚠️ **Archived 2026-06-08 — condensed into and superseded by [docs/ROADMAP.md](../ROADMAP.md) §6.** Kept for the full findings detail; do not edit. (Relative links are one level off after the move.)

# WallDance Audit

Date: 2026-03-26

## Scope

This audit covers the current WallDance repository as checked in on 2026-03-26, with emphasis on:

- application runtime architecture and processing flow
- tracking, camera, model-loading, recording, and OSC logic
- install and launcher behavior
- maintainability, operational risk, and documentation quality

The codebase is in a generally healthy state: it appears production-focused, has strong fallback behavior, and contains substantial domain-specific engineering work around camera stability and tracking. The main issues are not obvious correctness failures; they are concentrated in testability, configuration governance, architecture concentration, updater safety, documentation drift, and repository hygiene.

## Executive Summary

Overall assessment: solid and field-oriented, but carrying meaningful engineering debt.

What is working well:

- The runtime has a clear end-to-end flow: capture, optional enhancement, detection, tracking, rendering, OSC, recording.
- Hardware-specific work is thoughtful, especially the IDS camera path and documented stall investigation.
- The project handles degraded environments reasonably well through fallback paths for IDS, CUDA, TensorRT, and generic cameras.
- The tracker is sophisticated and intentionally engineered for this use case rather than being a generic off-the-shelf wrapper.

What is risky:

- There is effectively no automated safety net: no tests and no CI.
- The app is operationally complex but still concentrated in a few very large modules.
- Configuration volume is high and mostly unvalidated.
- The launcher updater can overwrite local state by force-syncing to remote.
- Documentation has drifted behind the code in a few user-visible places.
- The repository currently carries a very large amount of binary model artifacts, which will increasingly hurt cloning, storage, and release hygiene.

## Progress Update (2026-06-08)

Work landed since the audit, with the findings it touches:

- **First automated tests now exist** — `application/tests/test_calibration.py` (15 tests) + `application/tests/conftest.py`. This is the first dent in **Finding #1 (no tests / no CI)**: there is now a `tests/` package and a `pytest` path that runs green. CI is still absent; config/model/tracker/OSC tests (the suggested deliverables) are still to come.
- **New isolated, testable module** `application/src/calibration.py` (`SceneCalibrator`, `ExclusionMaskBuilder`) — pure logic, no GUI/camera coupling. A deliberate counter-example to **Finding #2 (concentration in large modules)**: new behavior went into a small dedicated module instead of growing `app.py`/`pipeline.py`.
- **Go-Live scene calibration (ROBUSTNESS_PLAN P2)** — a dedicated **Calibrate** button (bottom bar) measures `PERSON_HEIGHT_PX` + height ratios from YOLO detections, picks the MOG2 `varThreshold` by an **empirical background false-positive sweep** (not a formula — MOG2 self-normalises), and reports exposure stability + FPS. Apply-then-confirm with explicit save. This makes several formerly hand-tuned constants **measured, logged, and per-project** rather than global guesses — partial, concrete progress against **Finding #3 (config governance)** for those keys.
- **Auto exclusion mask (ROBUSTNESS_PLAN P1.4)** — scenery/ghost grid cells masked at calibration; rejects ghost detections at the source with a track-proximity guard. Validated mechanically (0 cells on clean footage; not yet exercised on ghost-heavy footage).
- **New persisted project-config keys** — `person_height_min_ratio`, `person_height_max_ratio`, `mog2_var_threshold`, `exclusion_grid`, `exclusion_cells` (in `_get_saveable_config`/`_apply_config_without_model`). These should be folded into the typed config model + validation called for in **Finding #3**.

Net: the reliability backlog (tests, config governance) has started to move, and new features were added without deepening module concentration. The big remaining audit items (CI, typed/validated config, `app.py` decomposition, launcher safety, model-artifact footprint) are unchanged.

## System Overview

Current architecture is coherent and understandable:

1. `application/src/main.py` is a thin entrypoint.
2. `application/src/app.py` owns orchestration of camera lifecycle, model management, GUI interaction, tracking, OSC, and recording/playback.
3. `application/src/pipeline.py` encapsulates enhancement, YOLO inference, duplicate filtering, motion bridge integration, tracking, and timing.
4. `application/src/tracker.py` implements the core identity logic, including Kalman prediction, Hungarian assignment, dormant resurrection, swap handling, shadow suppression, and motion bridge behavior.
5. `application/src/ids_camera.py` and `application/src/camera_manager.py` provide the industrial camera path plus standard OpenCV fallback.
6. `application/src/model_manager.py` manages `.pt` and TensorRT `.engine` lifecycle.
7. `application/src/video_recorder.py` and `application/src/tracking_logger.py` provide non-trivial operational tooling for reproducibility and offline review.
8. `launcher/` provides a separate Windows-oriented bootstrap/update GUI.

This is a good separation conceptually. The weakness is that several of these responsibilities are still too concentrated in a few large files.

## Strengths

### 1. Strong domain-specific engineering

The project is clearly optimized for a real deployment scenario rather than a demo. The IDS camera path, crop/exposure management, pinned-memory upload path, and documented stall analysis in `docs/IDS_CAMERA_STALL_INVESTIGATION.md` and `docs/IDS_STALL_CONCLUSIONS.md` show high practical rigor.

### 2. Good fallback strategy across the stack

Fallback behavior exists at multiple layers:

- IDS camera to OpenCV fallback in `application/src/ids_camera.py`
- GPU path to CPU path in `application/src/pipeline.py`
- TensorRT to PyTorch fallback in `application/src/model_manager.py`
- Windows and Linux install scripts that attempt to self-heal PyTorch/CUDA mismatches

This is one of the strongest parts of the codebase.

### 3. Operational tooling is better than average

`application/src/tracking_logger.py`, `application/src/video_recorder.py`, and the session-oriented playback flow in `application/src/app.py` are valuable. They make the tracker diagnosable instead of opaque.

### 4. Tracker work is sophisticated and well documented internally

The tracker logic is complex, but it is clearly the result of iterative field tuning and analysis, and `docs/TRACKING_PLAN.md` captures much of the reasoning.

## Findings

### Critical

#### 1. No automated test suite and no CI

Evidence:

- `application/pyproject.toml` declares `pytest` in a dev group, but the repository has no test files.
- No `.github` workflow directory is present.
- No `pytest.ini` or comparable test configuration is present.

Impact:

- Regression risk is high, especially for tracker behavior, configuration changes, and fallback paths.
- Refactors in `app.py`, `tracker.py`, and `pipeline.py` are much riskier than they need to be.
- Manual validation burden remains high for every change.

Recommended fix:

- Add `application/tests/` and start with focused tests around config validation, model fallback behavior, tracking scenarios, and OSC message formatting.
- Add CI to run at least unit tests and lightweight import/smoke checks.

### High

#### 2. Architecture concentration in a few very large modules

Evidence:

- `application/src/app.py`: about 3031 lines
- `application/src/gui.py`: about 1925 lines
- `application/src/tracker.py`: about 2427 lines

Impact:

- The app is harder to reason about than it needs to be.
- Callback behavior, playback state, camera state, and model state are likely to become increasingly fragile as features are added.
- Review cost and onboarding cost are higher than necessary.

Recommended fix:

- Split `app.py` into stateful services or controllers:
  - runtime controller
  - playback/recording controller
  - model loading controller
  - session/logging controller
- Keep GUI rendering and GUI state mutation separate.
- Do not rewrite the app wholesale; perform progressive extraction around clear seams.

#### 3. Configuration sprawl with weak validation

Evidence:

- `application/src/config.py` contains a very large number of constants across camera, enhancement, tracker, logging, recording, and IDS-specific behavior.
- `application/src/config_store.py` loads and saves JSON configs without schema validation or compatibility handling.

Impact:

- Invalid or stale configs can slip through silently.
- Rationale for many values exists in comments and docs, but not in enforceable code.
- Backward compatibility for saved project configs will become harder over time.

Recommended fix:

- Introduce a typed config model and validator layer.
- Validate ranges and invariants on load.
- Add config versioning and migration hooks for saved project files.
- Separate user-tunable config from expert-only/internal config.

#### 4. Launcher updater is destructive by design

Evidence:

- `launcher/git_manager.py` updates by moving local refs and rebuilding the working tree from remote HEAD.
- The implementation is effectively a force-sync and does not protect local modifications.

Impact:

- A local install with manual changes can lose those changes unexpectedly.
- This is especially risky in an operational art-tech environment where field tweaks may happen outside normal source control hygiene.

Recommended fix:

- Refuse to update when the working tree is dirty.
- Show a clear UI prompt before destructive update operations.
- Prefer an explicit release/update channel over repo-hard-sync for end-user installs.

### Medium

#### 5. Documentation drift around config storage paths

Evidence:

- `README.md` still says configs are stored under `configs/<project>/...` and references `configs/last_project.txt`.
- Actual code uses `projects/` and `projects/last_project.txt` in `application/src/config_store.py`.

Impact:

- Users and operators will look in the wrong place when backing up, editing, or debugging project configs.
- This also weakens trust in the README.

Recommended fix:

- Update the README to match the current `projects/` layout.
- Add one short section describing the actual project directory structure: configs, recordings, sessions, issues.

#### 6. Linux runtime script contains a Python-version-specific path assumption

Evidence:

- `run.sh` builds `LD_LIBRARY_PATH` from `application/.venv/lib/python3.10/site-packages/nvidia`.
- `application/pyproject.toml` allows Python `>=3.10,<3.13`.

Impact:

- On Python 3.11 or 3.12, the Linux NVIDIA library path fix may silently fail.
- The app may still run, but the intended library precedence workaround becomes unreliable.

Recommended fix:

- Replace the hardcoded `python3.10` path with dynamic discovery based on the active venv layout.

#### 7. Binary model artifacts are too large to keep scaling this way

Evidence:

- `models/` currently contains about 2.26 GB of files.
- Additional model artifacts under `application/` add roughly another 121 MB.

Impact:

- Clone time, storage footprint, backups, and release packaging get heavier than necessary.
- Git history becomes harder to manage if these assets keep changing.
- It also complicates distribution boundaries between code and runtime assets.

Recommended fix:

- Move large model binaries to release assets, external storage, or Git LFS.
- Keep only the minimal default runtime set in-repo if necessary.
- Document the supported artifact acquisition path clearly.

#### 8. Tracking complexity is justified, but hard to safely evolve

Evidence:

- `application/src/tracker.py` is large and policy-heavy.
- `docs/TRACKING_PLAN.md` shows extensive tuning and nuanced state-machine behavior.

Impact:

- The tracker may be good now, but it is still expensive to maintain.
- Small changes can have second-order effects on swap handling, occlusion handling, and track lifecycle.

Recommended fix:

- Preserve the current approach, but formalize representative scenario tests from the tracking plan.
- Add a tracker parameter guide that maps each important constant to its role and risk.
- Reduce “tribal knowledge” dependence.

#### 9. Install and runtime logic is duplicated across scripts and launcher

Evidence:

- Root scripts handle Python/uv/PyTorch setup.
- `launcher/` also provides install/update/process-management logic separately.

Impact:

- Behavior can drift between CLI and launcher-based workflows.
- Bug fixes in operational flow may need to be applied in multiple places.

Recommended fix:

- Define one canonical install/update policy and keep wrappers thin.
- Centralize shared operational checks where practical.

### Low

#### 10. A few comments and docs are now stale relative to current behavior

Examples:

- `application/src/config.py` still describes `TRACKER_EVENT_LOG_FILE` as output in the working directory, but the app now also supports per-session logging via `tracking_logger.start_session()` from `application/src/app.py`.
- Existing `docs/audit_report.md` is narrower and partly outdated compared with the current codebase.

Impact:

- Not a runtime failure, but it slows maintenance and increases confusion.

Recommended fix:

- Treat docs as part of each non-trivial feature change.
- Prefer fewer authoritative docs over many partially overlapping ones.

## Logic-Specific Audit Notes

### Camera and hardware path

Assessment: strong, but inherently exposed to platform-level instability.

- The IDS path is well engineered and much better than a naive SDK wrapper.
- The stall investigation docs are a major asset.
- Remaining risk is likely hardware and bus contention more than application correctness.

Suggested improvement:

- Add explicit runtime health telemetry for stall frequency and camera-source mode so operators can tell whether they are in ideal or degraded operation.

### Processing pipeline

Assessment: good modular shape with pragmatic fallback handling.

- `application/src/pipeline.py` has a reasonable split between GPU and CPU processing.
- The fallback behavior on CUDA incompatibility is good.

Suggested improvement:

- Add a stable internal interface for timing/telemetry export so performance regressions can be measured automatically.

### Tracking logic

Assessment: powerful but expensive to maintain.

- Current sophistication is warranted by the problem.
- The next maturity step is not “simplify the tracker”; it is “surround the tracker with reproducible tests and parameter governance.”

### Recording and review flow

Assessment: one of the more valuable parts of the system.

- Session logging, slot playback, and issue capture are strong operational tools.
- This is a good foundation for future regression capture.

Suggested improvement:

- Formalize “golden scenarios” from recorded sessions and use them as repeatable validation assets.

### Launcher logic

Assessment: useful, but should be treated as a deployment product, not just a convenience script.

- The launcher has enough responsibility now that update safety and install policy deserve product-level attention.

## Prioritized Improvement Plan

### Priority 1

- Add a minimal automated test suite and CI.
- Add configuration validation and config versioning.
- Fix launcher update safety for dirty installs.
- Fix the Linux `run.sh` hardcoded Python version path.

### Priority 2

- Refactor `app.py` into smaller controllers/services.
- Create tracker scenario tests based on known difficult review sessions.
- Update README and align all storage-path documentation with `projects/`.

### Priority 3

- Reduce in-repo binary model footprint.
- Unify operational logic between scripts and launcher.
- Consolidate stale docs and clarify the authoritative operator workflow.

## Suggested Concrete Deliverables

1. `application/tests/test_config_validation.py`
2. `application/tests/test_model_manager.py`
3. `application/tests/test_osc_output.py`
4. `application/tests/test_tracker_scenarios.py`
5. `application/src/config_schema.py` or equivalent typed config module
6. README update for `projects/` layout and session logging
7. launcher update guard for dirty working trees
8. dynamic Linux venv library-path discovery in `run.sh`

## Requested Enhancements (2026-06-08)

Operator-requested work, captured here as backlog. Neither is implemented yet.

### A. Simplify the YOLO-First / Motion-First duality

Today `TrackingMode` (`YOLO_FIRST` / `MOTION_FIRST`, `application/src/config.py` + the GUI tracking-mode combo) is a user-facing toggle that bifurcates the detection/tracking pipeline:

- **YOLO_FIRST** — YOLO is the primary detector; MOG2 motion blobs only *bridge* gaps when YOLO drops.
- **MOTION_FIRST** — motion blobs are *primary* detections (eager blob detection) alongside YOLO.

The two modes double the reasoning surface, the tuning constants, and the code paths in `pipeline.py`, and force the operator to understand an internal architectural choice. **Goal: collapse to one coherent path** (or auto-select), removing the toggle and the divergent branches.

- This is the same complexity targeted by **ROBUSTNESS_PLAN §P3** (collapse the two per-frame MOG2 models; fold crossval + bridge into source-weighted Kalman measurements) and `docs/P3_FUSION_SIMPLIFICATION.md`, and it directly relieves **Finding #8 (tracking complexity is hard to evolve)**.
- The just-landed **auto exclusion mask (P1.4)** already replaces "most of what crossval does, at the source," which removes one motivation for the dual-mode machinery — a useful precondition.
- Sequencing: do this *after* P1.4 has been exercised on real ghost footage, so the simplification is validated against the case the modes were built for.

### B. Startup project picker (no silent auto-load)

Today the app auto-loads the last project on launch (`config_store.read_last_project()` / `last_project.txt`). There is no launch-time way to choose or manage projects, and after a mid-show crash the operator is dropped straight back into auto-load with no quick, deliberate path to the last known-good state.

**Requested behavior** — on start, do **not** auto-load; open a modal project picker:

- **Projects list ordered by last-save date**, most recent first (derive from the newest config mtime per project — `get_latest_config_in_project()` / `project_history()` already expose this).
- **Last project highlighted** (`read_last_project()`); pressing **Enter** launches it immediately — the fast crash-recovery path back to the last state.
- Per-project actions: **Launch**, **Rename**, **Delete** (delete behind a confirmation prompt).

Implementation notes:

- `config_store` already has `list_projects`, `project_history`, `latest_for_project`, `read_last_project`, `save`, `sanitize_project_name`. **`rename_project` and `delete_project` must be added** (move/remove the `projects/<name>/` directory, update `last_project.txt` if it pointed at the affected project; reuse `sanitize_project_name` for rename).
- Reuse the existing GUI modal pattern (`show_*_dialog` / `show_tensorrt_prompt`); launch a selected project through the existing full project-switch path in `app.py`.
- This is also the natural place to **validate the project config on load** and surface stale/invalid configs (ties to **Finding #3**), and to align with the `projects/` layout doc fix (**Finding #5**).
- Keep an escape hatch (env var or flag) to auto-launch the last project for unattended/kiosk startup, so the picker does not block a headless boot.

## Final Assessment

WallDance is not a fragile prototype anymore. It is a specialized production-oriented application with real engineering depth, especially in the tracker and camera stack.

The main recommendation is not to chase new features first. The right next step is to consolidate reliability and maintainability around what already works:

- add tests
- validate configs
- reduce architecture concentration
- make update/install behavior safer
- fix documentation drift

If those items are addressed, the project will be in a much stronger position to keep evolving without losing the hard-won behavior that already exists.