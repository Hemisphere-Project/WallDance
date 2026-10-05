# 03 — Architecture, code health and deployment safety audit

**Repo:** `/data/WallDance` @ `a49d0f2` (main == origin/main, last commit 2026-07-14; last fetch 2026-10-05 20:05)
**Date:** 2026-10-05 · **Scope:** `application/src` (29,142 LOC, 46 modules), `application/tests`, `launcher/`, installers, `extra/`
**Method:** read-only. Generated an AST import graph, ran `git log --numstat --follow`, `uvx vulture`, `uvx radon` and a coverage overlay (`uv run --no-sync --with coverage`; I diffed the venv package list before and after and it was unchanged). I ran the launcher's git logic in isolated dulwich sandboxes against both the current source and the code bundled in the shipped exe. I decompressed the exe's PyInstaller archive, did a test-deserialize of a TensorRT engine, and ran `uv pip compile` dry resolutions.
Raw artefacts are in `tmp_analysis/audit-2026-10/architecture/` (`imports.txt`, `churn_follow.txt`, `gm/scenarios*.py|txt`, `resolve_*.txt`, `cov_report.txt`, `vulture*.txt`, `radon_cc_D.txt`, `forkA_threads_state.md`, `forkB_health_docs.md`, `forkC_coverage.md`).

Tags: **[V]** verified by reading code or running something · **[S]** reproduced in a scratch simulation · **[O]** opinion/inference. Severity runs P0 (field data or show loss now) → P1 (show-loss class, needs a trigger) → P2 (maintainability) → P3.

Suite run: `uv run --no-sync python -m pytest tests -q` → **352 passed, 8 skipped, 6.9 s** [V], as expected. The 8 skips are: the whole launcher test module (dulwich is not in the app venv, so its 13 tests are skipped as one) and 7 `WD_RUN_REPLAY`-gated footage tests.

---

## Executive summary

1. **P0, prod safety. The launcher on prod probably runs pre-fix updater code that overwrites local work; it does not stash it.** [V][S]
   - The only committed binary, `launcher/release/WallDanceLauncher.exe` (38e15bb, 2026-03-26), bundles the *old* `git_manager`. Decompiled evidence: the docstring reads "return True if local HEAD is behind"; there is no `UpdateStatus`, `DirtyWorkingTreeError` or `can_fast_forward`.
   - The June 11 safety fix (188d2f7) exists in source only.
   - With that exe, any `HEAD != origin/main` triggers an "update available" prompt. That includes prod simply being *ahead* with unpushed commits, i.e. no push needed. One **Yes** then:
     - destroys uncommitted edits to tracked files (unrecoverable);
     - orphans local commits (the reset is not written to the reflog);
     - leaves upstream-deleted files lying around.
2. **P0, prod safety. A push that touches `install.bat` or `application/pyproject.toml` silently re-installs the whole stack on prod at next launch.** [V]
   - Both installers delete `uv.lock` and float every dependency. Every dependency in `pyproject.toml` has only a lower bound (`tensorrt>=10`, `ultralytics>=8.3.0`, `torch>=2.10`); the only upper bounds are `numpy<2.0` (ignored after install), `onnx<2.0.0` and `onnxruntime-gpu<1.24`.
   - Then `uv pip install --upgrade torch torchvision` re-resolves numpy to **2.5.2 today**, overriding `numpy<2.0`.
   - **This is already visible on the dev box.** Its venv was recreated today (2026-10-05 20:19). The 2026-06-08 `.engine` files no longer deserialize under TRT 11.3.0.99 ("Serialization Version 243 vs 244"), so the app would quietly run PyTorch rather than the tuned TRT path.
3. **P0/P1. Closing the launcher window kills a running show within about 5 s.** [V code + S]
   - The app's stdout is a pipe read by the launcher (`launcher/process_runner.py:64-80`).
   - The app prints a `[Budget]` line every 5 s (`runtime/main_loop.py:1051`).
   - Once the launcher exits, the next print raises BrokenPipe/OSError, which is uncaught in the main loop.
4. **P1. There is no exception boundary on the frame path, no `finally`, no persistent log and no auto-restart.** [V]
   - Any CUDA, TRT, tracker or OSC error ends the process. An OSC target of `255.255.255.255` raises `PermissionError` on the first send [S].
   - Shutdown is skipped on a crash: the AVI is not finalised and the tracker-log tail is lost.
   - There are 404 `print()` calls and no `logging` module. `faulthandler.enable()` is never called.
5. **P1. No CI signal exists.** [V][S]
   - `ci.yml` cannot pass as written: `scipy`/`filterpy`/`ultralytics` are missing, so collection stops with 5 errors.
   - A GitHub API query made during the audit (coverage fork, not re-checked by me) returned 0 recorded runs. Either way, there is no CI signal.
   - Coverage is **30 %**. `app.py`, `main_loop.py`, `model_controller.py`, `config_manager.py`, `osc_output.send_*`, the recorder and the camera paths are at **0–18 %**.
   - The goldens are 3 aggregate-count fixtures. They are TRT-locked with no version stamp, and nothing checks identities frame by frame.
6. **P1. Engine lifecycle has no version awareness.**
   - Engine names are `<model>_<imgsz>.engine`, with no TRT, GPU or ultralytics stamp.
   - `build_engines.bat` skips *existing* (stale) engines.
   - An incompatible engine falls back to PyTorch, and the readiness gate reports FAIL but does not block.
   - On Windows nothing guards against sleep or Windows Update reboots, and the GPU power limit resets on reboot.
7. **P2. Architecture is sound at the seams, but logic is heavy inside a few big files.**
   - There are no import cycles. The layering rules of DECOMPOSITION_PLAN hold, apart from one `core → camera` edge.
   - GUI→runtime goes through the command queue for 82 of 84 callbacks.
   - The weight sits in `tracker.py` (3.6k lines, a single class), `gui.py` (2.9k, growing), `app.py` (2.2k, missing its ≤800 target) and the CC-65 config applier.
8. **P2. Replay and live apply config differently, so part of the tuning work rests on a false premise.** [V]
   - `tests/replay.py:240-260` sets `max_age` *before* `set_tracking_mode`, which resets it to 60 on motion_first.
   - As a result known-N's `tracker_max_age` search does nothing on motion_first scenes (inferred: every candidate runs as max_age=60).
   - ROADMAP §4 calls `TrackingMode` "vestigial", but 4 of 12 projects and 5 of 12 scenarios run `motion_first`.
9. **Seams for the other streams.** Marker measurements and a continuity/identity layer both fit without splitting the tracker:
   - a typed `Measurement` record at `tracker.update`, to replace the `(kpts, conf, bbox)` tuple and its "all-zero conf = motion blob" convention;
   - an explicit output stage (identity → smoother → OSC) lifted out of `FrameProcessor._post_yolo_chain`.
10. **Keep as-is:**
    - Python + DearPyGui;
    - the `runtime/api` seam;
    - the append-only config history;
    - the GPU-only Track P evidence base;
    - `ops_monitor`'s pure check functions;
    - `ids_camera.py`, which is field-hardened (1 commit since June).

    Do not split the tracker, enforce `app.py` ≤800, or remove `TrackingMode` until ID-level goldens exist.

---

## 1. Architecture map

### 1.1 Component diagram (data path first; ↓ = calls/owns)

```
main.py ─► app.WallDanceApp  (composition root, 2.2k; 98 bus.publish sites; _cb_* command handlers; config applier CC=65)
   │
   ├─► runtime/main_loop.MainLoop  (one thread; tick = pumps → ui_input → acquire → process → preview → events → record → render)
   │      ├─ runtime/api.RuntimeAPI  (71 Command classes, queued; drained ONCE per tick)   + EventBus (44 Event classes, sync fan-out)
   │      ├─ controllers: camera_controller · recording_controller · model_controller · config_manager · calibration_flows · roi_state
   │      └─ services/web_monitor  (ThreadingHTTPServer 0.0.0.0:8080, MJPEG phone monitor)
   │
   ├─► acquisition:  camera/ids_camera.IDSCamera (IDS Peak, acquisition thread, GPU-direct read_gpu on main)
   │                 camera/camera_manager.CameraManager (OpenCV capture thread)  ── recorder callback ─► core/video_recorder
   │
   ├─► core/pipeline.FrameProcessor  (1.8k)
   │      ├─ core/gpu_pipeline (kornia CLAHE/gamma, letterbox; zero-copy)   [core/background bg-subtract: dormant]
   │      ├─ YOLO11x-pose via ultralytics (TRT .engine from core/model_manager; PT .pt fallback)
   │      ├─ core/motion_model.MotionModel ──.detector shim──► core/motion_detector (MOG2 + frame-diff)  [mog2-feed worker thread]
   │      └─ _post_yolo_chain  (shared by live AND detect-cache replay — the key seam):
   │             crossval(motion) → exclusion mask → cold motion blobs → core/tracker.DancerTracker.update
   │             → finalize (unscale) → [L=1: box EMA | L>1: core/output_smoother RTS] → core/osc_output.OSCSender (UDP)
   │
   ├─► core/tracker.DancerTracker (3.6k; Kalman+Hungarian; 3 swap correctors; dormant/resurrect; shadow/dup/takeover merge;
   │                                motion bridging)  ─► core/tracking_logger (tracking_events.jsonl)
   ├─► core/ops_monitor (HealthMonitor on main + LoopWatchdog thread; readiness check_* functions)
   ├─► calibration math: core/calibration, core/calib2, core/sensitivity_macro
   ├─► config: core/config (242 constants) · core/config_schema (v2 + migrate) · core/config_store (projects/<p>/<p>_<ts>.json)
   └─► ui/adapter (only DPG↔seam translator) ─► gui.GUI (2.9k) + gui_builder (1.6k) + ui/{wizard_state, calibrate_all_wizard, roi_mask_editor}

Runtime-invoked "tools" living in tests/: replay.py (dry-run), calibrate_segment.py (CLAHE sweep), known_n.py (tune)  ← subprocesses
Deployment: launcher/WallDanceLauncher.exe (dulwich sync → install.bat → run.bat → uv run --no-sync python src/main.py)
```

### 1.2 Dependency directions (generated, `imports.txt`) [V]

- There are **no import cycles** among the 46 modules. Fan-in: `core.config` is imported by 21 modules, `runtime.api` and `camera.ids_camera` by 5 each. Fan-out: `app` imports 26 internal modules, `core.pipeline` 10, `runtime.main_loop` 7.
- Layer edges:

  | From | To | Edges |
  |---|---|---|
  | app | core / runtime / camera / ui / services / gui | 11 / 9 / 2 / 2 / 1 / 1 |
  | runtime | core | 16 |
  | runtime | camera | 4 |
  | runtime | services | 1 |
  | ui | runtime / gui / gui_builder | 5 / 1 / 1 |
  | gui_builder | runtime.api (SystemState only) | 1 |
  | camera | core.config | 2 |

- **There is one layering wart:** `core/calibration.py:70` imports `IDS_EXPOSURE_MIN_FPS` and `max_exposure_for_fps` from `camera.ids_camera`.
- Every rule in DECOMPOSITION_PLAN §4 holds:
  - `core/`, `camera/` and `services/` never import `runtime/`, `ui/` or `gui`;
  - `runtime/` never imports `ui/`;
  - there are no `dpg.` calls in `app.py`.

### 1.3 Threads and processes (details: `forkA_threads_state.md` §1) [V]

| Thread | Start site | Role | Stop | Risk |
|---|---|---|---|---|
| Main | `app.py:2158` → `main_loop.py:190` | tick, command drain, all DPG rendering, IDS `read_gpu`, YOLO, tracker, OSC | `_shutdown` **not in `finally`** | any uncaught exception ends the show |
| DPG callback | DPG internal | 82 of 84 callbacks are `api.submit` | – | ROI/mask editor handlers mutate settings directly, without a lock (low) |
| CameraCapture | `camera_manager.py:217` | OpenCV grab plus recorder callback | `join(1.0)`, then the handle is released anyway | native crash possible on Windows (low) |
| IDSAcquisition | `ids_camera.py:974` | WaitForFinishedBuffer → memcpy | `KillWait` + `join(2.0)` | exits after 100 errors; the main loop reconnects (OK) |
| RecordingEncoder / playback decoder | `video_recorder.py:303` / `:572` | AVI write / decode | sentinel + join | a writer-open failure is silent; ≤300 queued frames are dropped at stop |
| mog2-feed | `pipeline.py:368` (pool of 1) | MOG2 in parallel with YOLO | `fut.result()` per frame | clean handoff |
| Model load | `model_controller.py:493/555` | load or export engine | main polls and pumps the UI | clean |
| OpsWatchdog | `ops_monitor.py:527` | hang >10 s → stack dump | Event + join | diagnostic only, no recovery |
| WebMonitor | `web_monitor.py:175/182` | HTTP + one thread per request | `shutdown()` | no auth, read-only |
| OpsReadiness / DryRun / CalibSweep / KnownNTune | `app.py:1770/1879/1953/2082` | worker threads; the last three launch `subprocess.run(sys.executable tests/…)` (≤5/15/40 min, own CUDA context) | none | publish DPG toasts off the main thread; not single-flight; not blocked from RUN; child not killed on quit; Apply targets the *current* project |

No `multiprocessing`, `asyncio`, `signal`, `atexit` or excepthooks are used [V].

### 1.4 Command/event seam [V]

- **GUI → runtime: consistent.** `ui/adapter._build_callbacks` has 84 entries, and 82 of them are `submit(api.X)`. Commands execute at one point per tick (`api.drain`, `api.py:881-898`), and handler exceptions are printed and contained.
- **Runtime → GUI: consistent apart from the documented synchronous ports.** There are 110 `bus.publish` sites. The ports are the TRT prompt, the `render_frame` pump and `consume_layout_change`.
- **Bypasses:**
  - `ui/roi_mask_editor.py` is driven directly by runtime code, through about 20 private calls from `main_loop.py` and `app.py:571` holding `gui=lambda`.
  - Worker threads publish events.
  - `main_loop.py` makes **38** `app.<controller>._private` accesses. As a result `LoopHost` is nominal, not a real interface.
- **Verdict [O]:** the seam does its job, which is keeping DPG out of runtime/core. Keep it. Do not extend it to the tablet client until there is a reason to.

### 1.5 Where config state lives [V]

There are **eight copies:**
1. `core/config.py` constants (242). These are bound by value at import, and nothing writes to them at runtime.
2. `ProcessingSettings` (37 fields, `pipeline.py:83`).
3. App attributes that duplicate settings (`osc_enabled`, `sensitivity`, `gap_bridging` (write-only)…).
4. Component-internal copies. `GpuPipelineSettings` is re-synced every frame, and `OSCSender.enabled` is seeded from the constant `OSC_ENABLED` (`osc_output.py:17`), not from the live toggle.
5. `ConfigManager._profiles` (show/rehearsal) plus `calibration_state`.
6. DearPyGui widget values. They are synced via `ControlSync`, and the widget's default comes from a GUI literal that disagrees with `config.py` on 8 keys (e.g. model `yolo11m` vs `yolo11x`, `tracker_max_age` 20 vs 45).
7. On disk: `projects/<p>/<p>_<YYYYmmdd_HHMMSS>.json` (append-only history), `_safe_defaults.json` and `last_project.txt`.
8. Tuning results waiting for Apply (`_calib_sweep_seed`, `_known_n_tuned`).

There are also **two appliers:** `app._apply_config_without_model` (`app.py:1023`, CC 65) and `tests/replay.py:_build_processor` (`:156-276`, used by the goldens, tune, known-N, sensitivity and detect_cache). They already diverge (§2.6).

Precedence: the newest file by mtime → `migrate` (v1→v2 only) → `flatten` → `validate_flat`, which covers 19 ranges plus imgsz, ROI and exclusion → apply per key, `if "k" in config` → the live objects become the truth → save serialises the live state (79 keys).

**Gaps:**
- A missing key inherits the *previous project's* live value rather than the default.
- There is no rename map and no "newer than this app" guard.
- Writes are not atomic, and there is no fallback to the previous valid save (§3.6).

---

## 2. Health metrics

### 2.1 Size × churn hotspots (`git log --follow`, `churn_follow.txt`; CC from radon) [V]

| File | LOC | Commits (since Jun 1) | Lines churned | Worst CC | Coverage |
|---|---:|---:|---:|---|---:|
| `app.py` | 2211 | 104 (42) | 12,352 | `_apply_config_without_model` **65 (F)** | **0 %** |
| `gui.py` | 2911 ↑ | 75 (33) | 4,398 | `update_stats` 34 (E) | 15 % |
| `core/tracker.py` | 3577 ↑ | 37 (12) | 5,591 | `_merge_takeover_duplicates` 29 | 44 % |
| `gui_builder.py` | 1610 ↑ | 68 (28) | 5,393 | – | 29 % |
| `core/pipeline.py` | 1810 | 54 (25) | 4,367 | `_filter_duplicate_detections` 39 (E) | 25 % |
| `core/config.py` | 876 | 67 (30) | 1,200 | – (242 constants) | 100 % (import) |
| `camera/ids_camera.py` | 1994 | 19 (**1**) | 2,988 | `_configure_camera` 46 (F) | 13 % |
| `runtime/main_loop.py` | 1171 | 4 (4) | 1,199 | `_tick_acquire` 39, `_tick_preview` 34, `_tick_process` 32 | **0 %** |
| `runtime/api.py` | 899 | 12 (12) | 927 | – | 100 % |

- Radon finds 24 functions at rank D or worse (6 E, 4 F). MI is 0.00 for `app`, `ids_camera`, `pipeline`, `tracker` and `gui`.
- Since the 2026-06-08 audit, `gui.py` grew from 1925 to 2911 lines and `tracker.py` from 2427 to 3577. `app.py` went down from its 4856 peak to 2211, but has grown by 410 since Phase 4.
- Activity: commits per month were 57 (Feb), 38 (Mar), 14 (Apr), **154 (Jun)** and 1 (Jul). Authors: tangonomade 160, maigre 119.

### 2.2 Dead code [V] (`forkB_health_docs.md` §1)

- `vulture --min-confidence 60` gives 133 hits. About **45 functions / about 322 lines (≈1.1 %)** are truly dead after grep checks:
  - 16 unwired `gui.py` `_on_*` handlers;
  - `pipeline._to_local`, `_to_letterbox` and `_enhance_gray_for_motion`;
  - the enhancer's CPU API;
  - `model_manager.model_exists`, `get_available_models` and `get_model_info`;
  - two IDS auto-toggle callbacks in `camera_controller.py:410,435`;
  - `app.gap_bridging`, which is write-only.
- **Dormant features weigh more than dead functions:**
  - `bg_subtract` has 55 references and is disabled in all 164 saved configs.
  - The auto-exclusion builder is unreachable, because `start_exclusion_calibration` is never called.
  - The CPU enhancer is still there.
- **CPU-mode leftovers after Track P are actively misleading:**
  - The launcher says "WallDance will run in CPU-only mode (lower FPS but functional)" (`launcher/gui.py:270-289`).
  - The installers offer `--cpu` and print "Continuing in CPU mode" (`install.bat:165`).
  - The app shows a "Running on CPU" toast (`main_loop.py:254-262`).
  - But `FrameProcessor.process` **raises `RuntimeError`** without the GPU path (`pipeline.py:456-461`). So "CPU mode" means a crash at the first RUN frame.

### 2.3 Tuning constants [V]

- **242** UPPER_CASE constants, not ~90. The ~90 figure refers to the detection subset.
- 236 are read in `src`, and 165 of those by live-path modules. Only `DENOISE_STRENGTH` and `PREVIEW_DISPLAY_SCALE` are dead.
- Governance is weak:
  - Only 57 of the 242 are named in any doc.
  - `KNOBS.md` is archived, but `.gitignore:47` still points to it.
  - The "Track S governance table" exists only in `docs/archives/OPERATOR_V2.md`.
  - There is no tier marker (operator / calibrated / expert / fixed).
- Defaults have **seven homes:** `config.py`, `ProcessingSettings`, `_get_gui_config`, `_get_saveable_config`, 46 GUI literal fallbacks, the replay fallbacks, and `config_schema._RANGES`.

### 2.4 Error handling and logging on the live path [V] (`forkA_threads_state.md` §5)

- **Caught:**
  - command handlers and bus subscribers;
  - camera read and IDS errors, which go to reconnect;
  - the health tick;
  - web-monitor start;
  - motion-feed errors;
  - only the TRT *input-size-mismatch* AssertionError (`main_loop.py:605-623`).
- **Uncaught, so the process exits:**
  - CUDA/TRT errors (Track P removed the fallback, `pipeline.py:510`);
  - any tracker exception (its only catch is `LinAlgError`, `tracker.py:1041-1044`);
  - **OSC `sendto` errors**, since `osc_output.py:65-134` has no try [S: a broadcast target gives PermissionError];
  - errors in preview, events, record and pumps, including a project switch, which runs *outside* `drain` (`main_loop.py:328-332`).
- There is no `finally` in `MainLoop.run` and no handler in `app.main()`.
- **Logging:**
  - 404 `print()` calls and zero `logging` usage;
  - no `faulthandler.enable()`;
  - 14 bare `except:` (13 in `ids_camera.py`), 130 `except Exception`, about 50 `except…: pass`;
  - the only persistent artefacts are `tracking_events.jsonl` and `issues.jsonl`;
  - **stdout exists only in the launcher's text box.**

### 2.5 Test coverage map [V] (`forkC_coverage.md`)

**Line coverage is 30 %** (15,174 statements). None of the 352 default tests touches real footage or the model.

| Behaviour | Status |
|---|---|
| Ghost gate, warmup, max-persons, takeover merge, MotionModel, output smoother, calibration, calib2, schema `migrate()`, ConfigStore, ops readiness + watchdog | **unit** (83–96 %) |
| Tracker association / ID assignment | **partial unit** (`update` 90 % but synthetic, well-separated detections; `trajectory_cost` 5 %) |
| 3 swap correctors (1–2 %), resurrect/`restore_continuity` (2–3 %), motion bridging / blob fusion (2–6 %), pipeline orchestration (1–8 %), gpu_pipeline/enhancer | **replay-only** (needs `WD_RUN_REPLAY` + GPU + engine + recordings) |
| Main-loop tick, OSC message format, camera capture/reconnect, recorder, web monitor, TRT build/mismatch, real config load/save via `config_manager`, calibration step state machines | **untested** |
| GUI phases | build-smoke only, under headless DPG with mocks; callback behaviour not tested |
| Launcher git manager | 13 tests, **skipped** in the app venv; in CI blocked by the collection errors |

**Goldens** (`tests/golden/*.json`, 3 × 300–600 frames):
- They compare aggregate counters only, e.g. `real_tracks` ±0, `swap_count` ±2, `avg_detections` ±5 % (`test_regression_replay.py:57-65`).
- They store no TRT, torch, ultralytics or driver version.
- The test checks only that the engine *file exists* (`:85-87`).
- **Nothing checks track identity frame by frame**, so an identity-continuity regression with stable counts passes.

**CI** (`.github/workflows/ci.yml`):
- Ubuntu only, Python 3.10/3.12, unpinned `numpy opencv-python-headless pytest dulwich`.
- **Collection stops with 5 errors** (`scipy`/`filterpy` imported at `tracker.py:7-8`) [S]. With collection errors allowed: 1 failed / 298 passed / 14 skipped / 5 errors.
- There is no Windows job, even though the launcher is Windows-only.

### 2.6 Duplicated logic [V]

1. **Two config appliers** that already disagree:
   - `tests/replay.py:240-260` sets `tracker.max_age` and then calls `set_tracking_mode`. On motion_first that resets it to `MOTION_FIRST_BRIDGE_MAX_FRAMES=60` (`tracker.py:924-929`), and the comment at `replay.py:254` claims the opposite order.
   - The live app does mode first, then max_age (`app.py:1118-1129`).
   - So `known_n.py`'s `tracker_max_age ∈ [30,45,60,90]` search is a no-op on motion_first scenes, and it writes back values it never evaluated [O on impact].
2. Three xywh-IoU implementations with different clamping: `pipeline.py:1469`, `pipeline.py:1778`, `tracker.py:1150`.
3. Three confidence-weighted centroid copies: `tracker.py:294`, `tracker.py:1013`, `pipeline.py:1765`.
4. The installer logic is written twice (`install.bat` 234 lines, `install.sh` 159 lines), and the launcher re-parses installer output by regex (`launcher/gui.py:11-50`).

### 2.7 Doc claims re-checked [V] (details in `forkB_health_docs.md` §4–5)

- **ROADMAP §1, "CI (352 tests) ✅":** wrong. CI cannot pass, and 352 is the local count.
- **ROADMAP §1, "launcher update safety ✅":** true in source only, not in the shipped exe.
- **ROADMAP §4, "`TrackingMode` vestigial":** wrong. It is live in 4 of 12 project configs and 5 of 12 scenarios, there is no GUI control left for it, and it changes min_hits, max_age and the MOG2 learning rate.
- **ROADMAP §4, "`tracker_smoothing` has no config key":** wrong. It is saved, applied and used.
- The other §4 dead-knob claims hold: auto-exclusion is inert, bg_subtract is dead, and `tracker_max_age` is defaulted in three places (45/20/45).
- **AUDIT.md (2026-06-08):**
  - Fixed: #5, #6, #7 and B.
  - Partial: #1 (CI broken), #2 (gui and tracker grew), #3 and #8.
  - #4 (launcher): fixed in source, not deployed.
  - Still holds: #9, #10 and A.
- **DECOMPOSITION_PLAN:**
  - Phases 0–4 done; Phase 5 (tablet) not started.
  - `runtime/session.py` and `runtime/ops.py` were never created.
  - `gui*.py` was never moved into `ui/`.
  - The `app.py` ≤800 target was missed (2211 lines).
- **README:** the file map lists flat paths that were deleted on 2026-06-22. It omits `runtime/` and `ui/`, and still advertises a "CPU fallback".
- **GUI_STACK_AUDIT:** the Option-0 fixes landed, but its deployment advice ("release tags + `uv sync --frozen`", `:22`, `:118`) was **not** adopted, and DPG code kept growing.

---

## 3. Deployment and ops risks

### 3.1 Launcher auto-update: exact behaviour

**Mechanics (both versions)** [V]:
- Repo URL: `https://github.com/Hemisphere-Project/WallDance.git` (`launcher/main.py:114`).
- Checkout location: `<exe dir>\WallDance` (`main.py:73,113`), e.g. `C:\WallDance\WallDance`.
- At each launch: `porcelain.fetch("origin")`, then compare the local `HEAD` with the remote `refs/heads/main`.
- **Only `main` is considered.** Pushes to any other branch are invisible to both versions.
- "Update" means:
  - set `refs/heads/main` **and** `HEAD` to `refs/remotes/origin/HEAD` (falling back to `origin/main`);
  - rebuild the index and the working tree with `dulwich.index.build_index_from_tree` (`git_manager.py:111-140`).

  It is a hard reset implemented by hand. There is **no `git stash` anywhere** in the launcher (grep). The "git stash shit" in `TODO-me` was a manual workaround.
- Afterwards, `install.bat` runs only if `install.bat` or `application/pyproject.toml` changed (`git_manager.py:142-148`); then `run.bat`.

**A. Shipped exe** (`launcher/release/WallDanceLauncher.exe`, 22 MB, Python 3.14, committed 2026-03-26, code = `f51a231`). Decompressed PYZ: `git_manager` contains "Force-sync…", "return True if local HEAD is behind"; `gui` contains `has_updates`; none of `UpdateStatus`, `DirtyWorkingTreeError`, `can_fast_forward` [V].

| Prod state | What the old exe does [S = `gm/scenarios_output.txt`] |
|---|---|
| Clean, behind | Prompt → Yes → fast-forward. Fine. |
| **Ahead (unpushed commits), no push from Thomas** | `local != remote` → **"A new version of WallDance is available. Do you want to update?"** → Yes → `main` moved *back* to origin; local commits orphaned. The reset is **not** logged in the reflog: the last reflog entry is still "commit: prod-only commit", so recovery is only possible via that sha or `git fsck` [S]. |
| **Uncommitted edits to tracked files** (e.g. `core/config.py` tuned on prod) | No dirty check. **Overwritten with the remote version, unrecoverable**: `X=42` became `X=1` [S]. |
| Diverged (Thomas pushed while prod had commits) | Same as "ahead": commits orphaned and edits destroyed [S]. |
| Files added by local commits | Stay on disk as *untracked* files [S]. |
| Files deleted upstream | Stay on disk as untracked strays [S]. |
| `projects/`, `models/`, recordings (gitignored, untracked) | Untouched, unless upstream ever adds a tracked file at the same path. |

**B. Current source** (`188d2f7`, not built into any exe in the repo):

| Prod state | What the current code does |
|---|---|
| Up to date / unknown (offline) | Start the app. |
| **Ahead** | "Local version is ahead… skipping update." The update is skipped forever, logged only in the launcher box. |
| Behind/diverged **+ dirty tracked files** | Refuse, with a warning dialog listing the files (`gui.py:358-379`). If the dirty check itself fails, it fails safe. |
| Behind, clean | Plain Yes/No prompt → fast-forward. |
| **Diverged, clean** | A destructive prompt whose text says "PERMANENTLY DISCARD the local commits". The buttons are plain **Yes/No**: `ask_choice_sync` ignores `yes_text`/`no_text` (`gui.py:415-424`). Yes → commits orphaned, with no backup ref and no reflog entry. |
| **Checked out on a non-`main` branch** | Classified against `main`. On update, `HEAD` is a symref, so writing `HEAD` moves **that branch** to `origin/main`, and its commits are orphaned [S]. |
| Untracked local file at a path upstream now tracks | Silently overwritten. By design the dirty check ignores untracked files [S]. |
| `refs/remotes/origin/HEAD` stored as a stale direct sha instead of a symref | Update resets to the stale commit while reporting BEHIND [S]. This is low likelihood: both dulwich and git clones write a symref [S]. |

**C. Other launcher facts** [V]:
- **The app's stdout is a launcher-owned pipe.** If the launcher window is closed (it stays visible by default, `gui.py:94`), the app dies on its next print (§2.4, `forkA` A-H1).
- Exit code 0 closes the launcher. A non-zero exit shows a "Restart" button. **There is no auto-restart.**
- The CPU-mode dialog (`gui.py:270-289`) is now false.
- The launcher build is not reproducible:
  - `requirements.txt` is unpinned (`dulwich>=0.22`, others bare);
  - `build.bat` uses `pip install -r` against the latest versions;
  - the spec hard-codes `C:\Users\tango\...`;
  - no version or commit is stamped in the exe.
- `_DIRTY_EXEMPT` is now vestigial: both files are untracked (`git ls-files`).
- The launcher safety tests run nowhere: they are skipped locally and CI is red.

**Can it lose or hide operator data?**
- Configs in `projects/` are safe. They are gitignored, and neither version touches untracked paths unless a tracked file appears at the same path.
- **Code-level operator or dev work is at risk.** With the old exe, tracked-file edits are destroyed and commits are orphaned, possibly on a prompt that appears with no push at all.
- With the new code, commits are orphaned only behind an explicit (but poorly labelled) prompt, and a non-main branch can be moved.

### 3.2 Install reproducibility and pinning [V]

- **No lock in git.** `uv.lock` is in `.gitignore`, and both installers delete it each run (`install.bat:70`, `install.sh:66`).
- **Every pin is a floor:**
  - `ultralytics>=8.3.0`, `torch>=2.10.0`, `torchvision>=0.25.0`, `tensorrt>=10.0.0`, `kornia>=0.8.2`, `onnxslim>=0.1.71`, `opencv-python>=4.8.0`.
  - The only upper bounds are `numpy<2.0`, `onnx<2.0.0` and `onnxruntime-gpu<1.24` (`application/pyproject.toml:8-35`).
  - uv itself is unpinned (`pip install -U uv`), and Python floats between 3.10 and 3.12.
- **A fresh Windows resolve today** (`resolve_win_today.txt`): torch 2.14.1, tensorrt 11.3.0.99, ultralytics 8.4.173, kornia 0.8.3, numpy 1.26.4 at sync time. The dev box got kornia 0.8.2 at 20:20 today, so even same-day installs differ.
- **The torch step overrides the project constraint.** After `uv sync`, the installers run `uv pip install --upgrade torch torchvision --index-url …/cu130` (`install.bat:91`). That bypasses `pyproject.toml`, and `--upgrade` applies to every dependency. Resolving it for Windows today gives **numpy 2.5.2** (`resolve_torch_cu130.txt`); the dev box got 2.2.6 earlier. `uv pip check` cannot catch this, because the project is a virtual project and is not installed.
- **The lock describes the wrong torch.** `[tool.uv.sources]` points torch at the **CPU** index (`pyproject.toml:37-44`), so `uv.lock` holds `torch 2.14.1+cpu`. Any `uv sync`, or a bare `uv run`, puts CPU torch back. Since Track P, CPU torch means `RuntimeError` on the first RUN frame (`pipeline.py:456-461`). `run.bat` correctly uses `--no-sync`, but nothing protects against a manual `uv run`.
- **A transient failure can uninstall the IDS SDK.** `uv sync … --extra ids` falls back to `uv sync …` without ids on *any* failure (`install.bat:78-83`). `uv sync` is exact, so a network hiccup **uninstalls the IDS SDK** on the IDS rig, with only a message saying "This is normal on laptops".
- **Ultralytics can install packages at runtime.** `YOLO_AUTOINSTALL` is set only in `extra/build_engines.*`, not for the app. Ultralytics 8.4.173's export path requires `onnxslim>=0.1.82` while `pyproject.toml` allows ≥0.1.71. So on a venv below that, a GUI "Rebuild TRT" would pip-install into the live venv [V code; O trigger].
- **Trigger chain:** a push touching `pyproject.toml` → the launcher's update → `install.bat` → every package floats to latest → TRT major/minor bump → engines are incompatible.

### 3.3 TensorRT engine lifecycle [V]

- **Naming:** `models/<model>_<imgsz>.engine` (`model_manager.py:101-113`). There is no TRT, GPU-SM or ultralytics stamp. The ultralytics metadata header shows the dev-box engines came from ultralytics 8.4.61 (2026-06-08).
- **Load:** an engine that exists is loaded, then warmed up on a 64×64 dummy (`model_manager.py:229-245`). If that fails, the app falls back to `.pt` on CUDA:
  - toasts "TRT engine incompatible — using PyTorch" (`model_controller.py:638-645`);
  - shows a red banner "TensorRT OFF" (`model_controller.py:301`);
  - the readiness `check_tensorrt` reports **fail but does not block** (`ops_monitor.py:113-133`, commit c9d0957).
- **Repair:**
  - the banner's "Rebuild" deletes and re-exports one engine, taking 2–5 min (`model_controller.py:277`);
  - `extra/build_engines.bat:92-95` **skips any existing file**, stale ones included.
- **Live demonstration on the dev box:** `yolo11n-pose_640.engine`, built 2026-06-08, fails `deserialize_cuda_engine` under TRT 11.3.0.99 with "Current Version: 244, Serialized Engine Version: 243". The prod laptop gets the same outcome after any reinstall.
- **Goldens:** documented as "engine/driver-locked — re-baseline on any TensorRT or GPU-driver bump" (`test_regression_replay.py:7-8`). They record no versions, so a drifted environment either skips or compares numbers from a different engine.

### 3.4 Windows-specific risks [V unless marked]

1. **Sleep or display-off mid-show.** No `SetThreadExecutionState` anywhere in the code (grep). With no keyboard input during a show, the power plan alone decides whether the laptop sleeps.
2. **Windows Update auto-reboot.** Only listed as a low-priority wish ("- Desactiver mise à jour auto Windows", `TODO-me`). It is not in `NEW_SHOW.md` or `CHECK_TEST.md`, and no readiness row covers it.
3. **Driver updates.** An NVIDIA driver update via Windows Update or the NVIDIA App invalidates the goldens per the repo's own doc. [O] It may break CUDA/TRT compatibility.
4. **GPU power limit.** `extra/gpu_limiter.bat` needs admin rights and the limit "resets on reboot" (`:89`). Nothing re-applies or checks it.
5. **USB3 stalls on the IDS camera.**
   - Detection and reconnect exist (`ids_camera.py:1105`; TODO.md auto-reconnect is ✅, but the "rig USB-pull validation" is still pending).
   - [O] The power plan's "USB selective suspend" is not checked.
6. **AC power.** Nothing checks it; there is no readiness row.
7. **Firewall prompt.** The web monitor binds `0.0.0.0:8080` (`web_monitor.py:175`), which triggers the Windows Firewall prompt on first run; an operator clicking "Cancel" silently kills the phone monitor [O].
8. **Native crashes.** There is no `faulthandler`, and the camera handle is released after a timed-out join (`camera_manager.py:194-206`).

### 3.5 Crash recovery mid-show [V]

1. On a crash, `run.bat` exits non-zero, and the launcher shows its log with a **Restart** button. Someone has to click it.
2. On restart, `PROJECT_PICKER_ON_START = True` (`config.py:577`) shows the picker. Auto-load of the last project happens only with the `WALLDANCE_AUTOLAUNCH_LAST` environment variable or `--project`.
3. The operator then picks the project → the model loads (TRT deserialize) → Go-Live.
4. Nothing tells the app the previous session ended abnormally while in RUN.
5. If the latest config file is truncated, which non-atomic writes make possible, auto-load exits with "Failed to load project". There is no fallback to the previous save, although the history is kept (`main_loop.py:243-245`).

### 3.6 Config/schema migration safety [V]

- Only v1 → v2 is handled (`config_schema.py:83-105`), and a future version is treated as v2 without a warning.
- Unknown keys are dropped on the next save. Missing keys inherit the previous project's live values. Only 19 keys are range-checked; other keys are assigned raw.
- Writes use `open("w")` + `json.dump`, with no temp file + `os.replace` and no fsync (`config_store.py:124,212,243,282`). Saves within the same second overwrite each other.
- "Load safe defaults" with a model or imgsz change writes a new timestamped project config as a side effect (`config_manager.py:476-482`).

---

## 4. Ranked recommendations

Effort: S < 1 day, M = 1–4 days, L > 1 week. Order: prod safety first, then show-loss classes, then structure.

| ID | Sev | What | Effort | Risk | Gate |
|---|---|---|---|---|---|
| **ARCH-1** | P0 | **Reconcile prod before *any* push to `main`** (procedure, no code). On prod: (1) inventory with `git status`, `git log origin/main..HEAD`, `git stash list`, `git branch -vv`; (2) back up with `git bundle create prod-YYYYMMDD.bundle --all` plus a zip of `projects/` and `models/`; (3) commit the WIP and push it to a branch Thomas names; (4) integrate on dev; (5) only then let prod fast-forward. **Until then, do not push to `main`.** The launcher looks only at `main`, so pushing elsewhere is invisible to both versions. If `main` must move, tell the operator to answer **No** to "update?" prompts. | S | none | `git log origin/main..HEAD` empty on prod; bundle file exists |
| **ARCH-2** | P0 | **Ship a launcher built from current source, and harden it.** (a) Write a backup ref (`refs/walldance/pre-update-<ts>`) and a reflog entry before moving `main`. (b) Refuse when `HEAD` is not `refs/heads/main`. (c) Refuse, or rename to `.bak`, untracked files the new tree would overwrite. (d) Delete files removed upstream. (e) Use real "Discard and Update / Keep Local" buttons, a custom dialog in place of `askyesno`. (f) Pin `requirements.txt` and stamp the commit sha into the exe title. (g) Read app output from a log file, not a pipe (pairs with ARCH-3). | M | the exe is the field updater; build on Windows | `test_launcher_git_manager.py`, extended with the 4 scenarios in `gm/scenarios.py`, on a **windows-latest** CI job; manual smoke on a copy of the prod checkout |
| **ARCH-3** | P0 | **Decouple the app from the launcher's stdout.** `run.bat` redirects to `logs\walldance_<date>.log`, and the launcher tails that file. Also make the app's stdout write-safe: a tee to file plus swallowing `BrokenPipeError`/`OSError` on the console stream. | S | low | Windows: start via the launcher, close the launcher window, and the app keeps running for 10 min |
| **ARCH-4** | P0 | **Freeze the dependency set.** (1) Snapshot prod's `uv pip freeze`, TRT version and driver as the baseline. **Do not** force dev's newer stack onto prod. (2) Point `[tool.uv.sources]` at the **cu130** index (GPU-only per Track P) and restrict `[tool.uv] environments` to win32/linux. (3) Pin exactly `torch`, `torchvision`, `tensorrt*`, `ultralytics`, `onnx`, `onnxslim`, `onnxruntime-gpu`, `kornia`, `numpy`, and decide on `numpy<2` (Q5). (4) **Commit `uv.lock`**. Installers run `uv sync --frozen --extra gpu [--extra ids]`; remove the `del uv.lock` and the `uv pip install --upgrade torch` step. (5) Choose the IDS extra explicitly (detect IDS Peak or a marker file), never by failure fallback. (6) Set `YOLO_AUTOINSTALL=0` and `YOLO_OFFLINE=1` in `run.bat`/`run.sh`. (7) Add `required-version` for uv. | M | a resolver change on the field machine; do it once, on a spare Windows box first | fresh install on a spare Windows box → `uv pip freeze` identical to the lock → engines load → goldens pass |
| **ARCH-5** | P0/P1 | **Engine lifecycle.** (1) Stamp engines with a sidecar or name (TRT version, GPU name + SM, ultralytics version, imgsz, half). (2) A stamp mismatch is treated as *missing*, with a "Rebuild engines (n min)" prompt, rather than a quiet PyTorch fallback. (3) `build_engines.*` rebuilds on mismatch instead of skipping. (4) Ship a TRT timing cache (GUI_STACK_AUDIT:118). (5) Add the version stamp to the goldens, and have the regression test skip (or fail loudly) when it differs. | M | low | unit test on stamp/mismatch logic; manual: change the TRT version → a "stale engine" prompt, not a PT run |
| **ARCH-6** | P1 | **Live-path crash hygiene.** (1) `try/finally` in `MainLoop.run`, so shutdown always finalises the AVI, flushes the tracker log and sends `/walldance/clear`. (2) Guard OSC sends (catch `OSError`, rate-limited alert, continue). (3) A per-tick boundary in `_tick_process` that counts consecutive exceptions and alerts rather than dying on a single transient error, while still exiting on a persistent fault. (4) `faulthandler.enable(log_file)`. (5) The `logging` module with a rotating file. (6) Launcher auto-restart on non-zero exit, plus a crash marker so the app reloads the last project into **STANDBY** (Q8). | M | changes failure semantics on the hot path | new headless main-loop tick test (fake camera + stub detector that raises), OSC broadcast-target test, goldens byte-identical |
| **ARCH-7** | P1 | **Windows show-mode.** (1) `SetThreadExecutionState(ES_CONTINUOUS\|ES_SYSTEM_REQUIRED\|ES_DISPLAY_REQUIRED)` while a session is open. (2) New readiness rows as pure `check_*` functions, like the existing ones: AC power, active power plan, pending-reboot / Windows Update registry keys, GPU power limit vs target, USB selective suspend. (3) An operator checklist in NEW_SHOW: pause updates, active hours, "exclude drivers from WU" policy, NVIDIA App auto-update off. | S | none | unit tests for the check functions; on-rig readiness screenshot |
| **ARCH-8** | P1 | **Config durability.** (1) Atomic writes (tmp + `os.replace`). (2) Load-latest falls back to the newest *valid* file. (3) A missing key resets to its default, with a logged list of defaulted keys. (4) A "config newer than app" warning. (5) Run the project switch inside the guarded command drain. | S | low (load path) | truncated-newest-file test; v1-file round trip through `config_manager` (currently 0 % covered) |
| **ARCH-9** | P1 | **Make CI real.** Install `scipy filterpy` (and gate the one ultralytics-importing test), pin `numpy` consistently with the project, add a `windows-latest` job running the launcher tests, and require a green CI run before any push to the field branch. | S | none | green CI |
| **ARCH-10** | P1/P2 | **One config applier**, `core/apply_config.py::apply_detection_config(proc, tracker, cfg)`, used by both `app._apply_config_without_model` and `tests/replay._build_processor`. This fixes the max_age/tracking_mode order bug that blinds known-N on motion_first scenes, and cuts the CC-65 function. | M | touches every project load | goldens byte-identical, plus a **parity test**: for every saved project config, the app applier and the replay applier produce identical tracker/processor state |
| **ARCH-11** | P2 (enabler for the markers stream) | **Typed `Measurement` at the tracker boundary.** Replace `(kpts, conf, bbox)` plus the "all-zero conf ⇒ motion blob" convention (`tracker.py:1991-1996`, `:2690`) with a dataclass carrying `source ∈ {yolo, motion_blob, marker}`, `noise_mult` (the per-source Kalman R that already exists for motion blobs), an optional `identity_hint`, and `box_conf`. Marker measurements fuse in `_post_yolo_chain` before `tracker.update`. The detect-cache must capture them too, so replays stay meaningful. | M | pure refactor first, behaviour later | goldens byte-identical for the refactor commit; tracker unit tests |
| **ARCH-12** | P2 (enabler for the continuity stream) | **Extract the output stage** (finalize → identity/continuity → box EMA or RTS smoother → OSC) from `_post_yolo_chain` (`pipeline.py:757-876`) into `core/output_stage.py`. Input: confirmed tracks plus tracker lifecycle events (merge, dormant, resurrect). Output: a performer-ID'd stream. The identity layer then plugs in there without touching the 3.6k-line tracker. | M | low | new OSC-format test, `output_smoother` tests, goldens |
| **ARCH-13** | P2 | **Identity-level goldens.** Store per-frame `track_id → GT person` assignments (or ID-switch counts from `scoring.py`) on the 3 goldens, so continuity and marker changes are measured, not judged by eye. This is the prerequisite for ARCH-11, 12 and 15. | M | none | — |
| **ARCH-14** | P2 | **Remove Track-P leftovers and dead code in one sweep:** CPU flags and messages in the installers, the launcher, the GUI badge and the README; about 45 dead functions; `gap_bridging`; the 2 dead constants; the enhancer CPU API. Decide separately whether `bg_subtract` and auto-exclusion go. | S–M | low | unit suite + GUI smokes + goldens |
| **ARCH-15** | P2 | **Split `tracker.py` along its existing phases:** association/gates, the 3 swap correctors (~600 lines), lifecycle (aging/dormant/resurrect), duplicate/shadow/takeover merge, and motion bridging into mixins or helper modules, with `DancerTracker` kept as the facade. **Only after ARCH-10, 11 and 13.** | L | high if done early | ID goldens + count goldens identical |
| **ARCH-16** | P2 | **Background jobs:** single-flight, warn or block entering RUN while one runs, kill the child on quit, bind Apply to the project that was tuned. Move the runtime-invoked scripts from `tests/` to `application/tools/`. | S–M | low | unit tests on the job state machine |
| **ARCH-17** | P3 | Suppress single-key shortcuts while an `input_text` has focus (`ui/adapter.py:219-265`; check on hardware first). | S | low | GUI smoke |
| **ARCH-18** | P3 | **Doc truth-up** (docs stream): ROADMAP §1 CI claim, §4 `TrackingMode`/`tracker_smoothing` claims, §5 vs §6 bug status; regenerate the README file map; a knob-tier table for the 242 constants. | S | none | — |
| **ARCH-19** | P2 | **Safe update channel.** The launcher tracks a release ref (e.g. a `field` branch or `release-*` tags that Thomas cuts after CI plus an on-rig check) instead of `main`, and shows the release name. `main` becomes free for development. (GUI_STACK_AUDIT §D already recommends tags plus `uv sync --frozen`.) Thomas creates and moves the refs himself. | S (after ARCH-2) | process change | first release cut from a green CI run, installed on a spare machine |

### What to do this week (minimal, no app-behaviour change)

1. ARCH-1: prod inventory and backup.
2. ARCH-3: the stdout pipe.
3. ARCH-2 + ARCH-19: launcher rebuild and channel.
4. ARCH-4 step 1: capture prod's freeze.
5. ARCH-7 operator checklist: Windows Update, sleep, power.

---

## 5. Keep as-is (explicitly; the system is in field test)

- **Python + DearPyGui** (GUI_STACK_AUDIT decision). No GUI rewrite during field test.
- **The `runtime/api` command/event seam.** GUI→runtime is consistent and there is a single drain point. Don't build the tablet client or move `gui*.py` into `ui/` just for tidiness.
- **`app.py` as the composition root at about 2.2k lines.** Drop the ≤800 target for now. Only extract the config applier (ARCH-10).
- **Append-only, timestamped config history.** It is the best safety property in the config layer. Add atomic writes; don't redesign it.
- **`core/config.py` constants as import-time values**, with no runtime monkeypatching. Don't introduce a settings framework; document the tiers instead.
- **Track P (GPU+TRT is the only evidence path)** and the detect-cache/replay harness architecture (`_post_yolo_chain` shared by live and replay).
- **`ops_monitor`:** pure `check_*` functions with 94 % coverage. Extend it (ARCH-7); don't restructure it.
- **`camera/ids_camera.py`** (2k lines, CC-46 configure, 13 bare `except:`): 1 commit since June and field-hardened stall/reconnect. Leave it alone unless a field bug points there.
- **`MotionModel.detector` shim:** harmless until the motion subsystem is next touched.
- **`TrackingMode`:** keep it (it is live config for 4 projects) until a replay-gated migration decides otherwise.
- **The dulwich-based launcher approach** (no git install needed on Windows). Fix its semantics and redeploy it; don't replace it.
- **The tracker monolith:** no split before ID-level goldens exist (ARCH-13).

---

## 6. Questions for Thomas

1. **Which launcher exe does the prod desktop shortcut start?** Please send its path, size and date, and say whether it is the committed 2026-03-26 build or a local rebuild after June 11. This decides whether finding #1 is live.
2. **Is the directory where the June–July Claude sessions ran on prod the same as the launcher's `<exe dir>\WallDance`?** Which branch is checked out there, and what do `git status`, `git log origin/main..HEAD` and `git stash list` show? (`origin/main` has not moved since 2026-07-14, `a49d0f2`.)
3. **Prod's actual stack:** please send `uv pip freeze`, the NVIDIA driver version and the `models/*.engine` dates. Has `install.bat` re-run on prod since the TRT goldens were re-baselined (2026-06-24)? This becomes the lock baseline for ARCH-4.
4. **The dev-box venv was recreated today (2026-10-05 20:19)** and now has TRT 11.3, ultralytics 8.4.173 and numpy 2.2.6. Was that intentional? It invalidated the June engines here, which may affect the perf stream's measurements.
5. **Why `numpy<2.0`?** Is it still needed? The dev box runs 2.2.6 with the full suite green, and the installers already override it.
6. **IDS extra on prod:** should the installer fail loudly rather than fall back without the IDS SDK?
7. **Update channel:** OK to point the launcher at a `field` branch or `release-*` tags that you cut, instead of `main`?
8. **After a crash mid-show:** auto-resume into STANDBY with the last project (operator re-arms RUN), or straight back into RUN?
9. **Laptop management:** is the prod laptop Windows Pro, so Group Policy can block auto-restart and driver updates? Is it on AC and on a known power plan during shows?
10. **`motion_first` in 4 projects** (`residence1-solo`, `3_TANGO_HANGAR-whitebg2`, `tango-H`, `tango-H2`): intended per-scene choice or legacy leftovers? This decides ROADMAP §4's "remove `TrackingMode`".
