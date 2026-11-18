# Subsystem map — detection-pipeline

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

Turn one camera frame into the OSC dancer stream. Per frame: ROI crop, GPU enhancement (CLAHE on Y + gamma), letterbox to a square YOLO tensor, Ultralytics pose inference (TensorRT engine or PyTorch fallback), detection clean-up (size gate, dedupe, motion cross-validation, exclusion mask), Kalman/Hungarian tracking in letterbox space, conversion back to camera pixels, identity slots / output smoothing, OSC emit, plus a rate-limited preview image. It also owns model loading/switching and the mirror/rotate input transform.

## Architecture

All paths are under application/src/.

- `FrameProcessor` (core/pipeline.py:403-2554; 94 methods, 9 properties). Entry points `process` (CPU frame) and `process_gpu_direct` (IDS tensor) -> `_run_yolo_and_track` -> `_post_yolo_chain` -> `_emit`. It owns `GpuPipeline`, `PoseRunner`, `MotionModel` and its `mog2-feed` executor, `ExclusionMaskBuilder`, `BackgroundSubtractor`, `IdentitySlots`, `OutputSmoother`, `CameraStallClock`, the belt / foreground / plate-capture hooks and the height guard. It holds references to `DancerTracker`, `OSCSender`, the YOLO model and `ImageEnhancer` (used only as the holder of clahe_clip / gamma).
- `GpuPipeline` (core/gpu_pipeline.py): `GpuEnhancer` (kornia / `FastClahe`, denoise, greyscale), `GpuResizer`, `_prepare_yolo_input` (letterbox), preview rate limiter and download. `GpuPipelineSettings` is re-copied every frame by `FrameProcessor._sync_gpu_settings`.
- `PoseRunner` (core/yolo_runner.py): calls the Ultralytics predictor's preprocess / inference / postprocess directly.
- `ModelManager` (core/model_manager.py): .pt download, TRT export, engine test inference, PyTorch fallback. Driven by `ModelController` (runtime/model_controller.py): GUI callbacks set pending flags, the main loop drains them, loads block the main thread with the GUI pumped and the camera closed.
- Coordinate helpers: `_TrackerSpace`, `_LetterboxMotionProxy`, `_unscale_letterbox`, `_BeltHook`.
- `InputTransform` (core/input_transform.py): D4 mirror/rotate applied at the source. core/visualization.py: overlays and `scaled_roi_rect`.

## Data flow

IDS show path; config.py:107,119 give 20 fps and a 1528x1528 mono crop.

1. Acquisition thread: raw copy, unpack to mono8.
2. Main, `read_gpu` (camera/ids_camera.py:1498-1590): pinned copy, H2D on a dedicated stream then synchronize, float, expand to (1,3,H,W); also a CPU GRAY2BGR copy.
3. `_start_motion_feed`: ROI slice and channel-0 copy back to mono, submitted to the worker (gamma LUT, blur, resize, MOG2, frame-diff).
4. `GpuPipeline.process_gpu_tensor`: ROI crop (view), rgb->ycbcr, CLAHE(Y), pow, clone, ycbcr->rgb, bilinear letterbox padded with 0.5. On preview frames: resize, uint8, `.cpu()` (~1.5 MB).
5. `PoseRunner`: `im.max()` (a sync), half(), engine inference, NMS.
6. `_extract_detections`: three small D2H copies (keypoints, xyxy, conf). Then height guard, size gate and dedupe, all in letterbox space.
7. Await the motion feed.
8. `_post_yolo_chain`: crossval gate, exclusion, cold blobs, `tracker.update`, `_unscale_letterbox` to camera px, box EMA, identity slots (belt, foreground), `_emit`. Returns the tracker tracks for the preview; OSC carries `last_emitted`.

Budget estimate from code, not measured, against a 50 ms period:
- camera read 1.5-2.5 ms
- motion crop ~1 ms
- enhance + letterbox 2-4 ms of GPU time, hidden in the next sync
- YOLO x-pose@1280 roughly 10-20 ms on TRT (tests/detect_cache.py:4 cites ~65 ms for the slow path)
- extract / guard / dedupe 1-3 ms
- gates + tracker 1-4 ms
- slots / foreground / OSC 1-4 ms
- preview frames +3-6 ms
- MOG2 worker 8-20 ms, on the critical path only when it outlasts YOLO (`mog2_wait`)

That is about 25-40 ms on TRT; PyTorch does not fit.

## Threading and state

Threads:
- IDS acquisition thread (camera).
- One main thread that runs GUI callbacks, camera read, the whole pipeline, OSC and render in series.
- One persistent `mog2-feed` worker (`ThreadPoolExecutor(max_workers=1)`, pipeline.py:458), submitted before the GPU stage and awaited after detection extraction (pipeline.py:753-755).
- A transient daemon thread per model load (model_controller.py:519).
- OpenCV's pool, restored to `CV2_NUM_THREADS=4` after `import ultralytics` zeroed it.

There are no locks. Safety rests on the submit/await bracket and on the single worker serialising feeds. The worker mutates `MotionModel`, `_last_motion_gray` and `_motion_gamma_lut`; the main thread touches motion state only after the await, on the normal path.

State:
- `ProcessingSettings` is one mutable dataclass shared by reference between the app and the processor (app.py:589-591). GUI, config, calibration and the pipeline itself (height guard, pipeline.py:1450) all write it.
- `FrameProcessor` carries about 60 attributes of cross-frame state: crossval cell memories, box EMA, slots, smoother, stall clock, belt static map, plate detector, three AutoHeight windows, last OSC frame.
- Tracker state and the crossval cell dicts live in ROI-local letterbox pixels, so they are only meaningful for the current ROI and imgsz.
- The executor is never shut down (one processor per process today).

## Failure handling

What exists:
- Frame boundary in the main loop (runtime/main_loop.py:448-488): an exception skips the frame, raises a rate-limited operator alert, and exits cleanly after a failure streak.
- No CPU fallback: `process()` raises without a GPU pipeline (pipeline.py:592-595).
- `PoseRunner`: a fast-path exception or a None result falls back to the regular Ultralytics call; it disables itself with a log line if the regular call works where the fast one raised; `WD_POSE_RUNNER=0` is a kill switch.
- `FastClahe` falls back to kornia; a kornia RuntimeError/ValueError falls back to gamma only (gpu_pipeline.py:402-409).
- A motion-feed exception is printed, not raised (pipeline.py:1760-1771).
- Belt, foreground, plate capture and the static map each degrade to a logged no-op.
- TRT: the engine gets a test inference at load; failure means PyTorch plus banner and toast; a warmup failure reloads the .pt; an imgsz mismatch is caught as an `AssertionError` by message substring and queues a reload (main_loop.py:849-866).

Gaps:
- The feed is left in flight if YOLO or extraction raises (pipeline.py:719-755 is unguarded; the GPU stage at 614-619 is guarded).
- Missing `letterbox` / `roi` keys default silently to identity geometry (pipeline.py:713-730).
- `raw_frame=None` silently disables motion, belt and foreground for that frame.
- At L>1 nothing is sent when the wall is empty.
- A missing engine at startup blocks on a modal until someone clicks.

## Tunables

- `ProcessingSettings`: 42 fields (pipeline.py:108-170). Units are mixed: px in camera space, ratios of person height, seconds, frames, 0-255 levels.
- `GpuPipelineSettings`: 19 fields, mostly mirrored each frame.
- About 45 constants imported from core/config.py: MOTION_CROSSVAL_*, SHADOW_*, HEIGHT_GUARD_*, IDENTITY_SLOTS_*, BELT_STATIC_*, CAMERA_STALL_*.
- Enhancement clahe_clip / gamma live on the `ImageEnhancer` object, not in settings. MOG2 scale and varThreshold live on `MotionModel`.

Inline magic numbers, not in config:
- Dedupe: 0.3 x ph centroid, 0.1 x ph keypoint, 1.5 x ph small-merge radius, IoU 0.7, containment area ratio 0.5 (pipeline.py:2395-2547).
- Crossval track bypass: 0.6 x ph gate, IoU 0.1, 16 px minimum cell (2236, 2300, 2365).
- Exclusion guard 0.6 x ph (1958).
- Enhancer: fade_range 40, brightness check every 10 frames, clahe grid 8 (gpu_pipeline.py:231, 302; pipeline.py:533).
- Letterbox pad value 0.5 (gpu_pipeline.py:858).
- Slot protection: fss <= 20, 1.0 s belt backing, 0.6h x 1.2h boxes (pipeline.py:1460-1466).
- `_out_dt` cap 0.25 s; height window >= 40 samples (1402).
- Stall clock built with 1/20 s (484).
- Model load: 64x64 validation frame and 640x640 warmup frame.

Environment switches: `WD_POSE_RUNNER`, `WD_ENGINE_DIR`.

## Interfaces with neighbours

- Camera: the pipeline's `raw_frame` is `unified_camera.get_last_cpu_frame()`, the BGR copy made inside the same `read_gpu()` call. The pairing with the GPU tensor holds only because both are called back to back on the main thread. A None there silently turns off motion, belt and foreground for the frame.
- Tracker: it works in ROI-local letterbox pixels and is given `set_person_height` and `set_frame_dimensions` every frame by the pipeline (pipeline.py:984-986). `app._cb_person_height_change` also calls `tracker.set_person_height` in camera pixels (app.py:1956), which is overwritten on the next frame. Its position measurement is the confidence-weighted keypoint mean, so anything the pipeline does to keypoints moves the output. Edge-exit logic receives only `pad_x`, not `pad_y`.
- Motion model: in low light, `MotionDetector.preprocess` runs median 5 and Gaussian 5x5 at full ROI resolution before downscaling (motion_detector.py:204-212), and `MotionModel._accumulate_noise` updates per-pixel Welford statistics every frame (motion_model.py:243-253). Both are on the worker that decides `mog2_wait`. Cost not measured.
- Identity slots: they receive tracks whose box sizes are EMA-smoothed at L = 1 but raw at L > 1. `belt_backing` and `entry_min_travel_h` are read only when the slot table is first built.
- OSC: `send_frame` is the only place a count is sent, so the pipeline's choice not to call it is visible to the media server.
- Main loop: pipeline, OSC and GUI render share one thread. The TRT mismatch recovery lives in the main loop and depends on Ultralytics' assertion text. `app._last_review_frame` copies the preview on every processed frame.
- Tuning harness: `_cache_capture_gpu` and `replay_gpu_cached` define what is tunable from a cache; see the detect-cache finding.
- Not verified: Ultralytics internals (sync behaviour of NMS, how an engine's imgsz overrides the call) are from memory, not from the installed package.

## Strengths

- PoseRunner (core/yolo_runner.py): the fast path runs only while the predictor state matches what the last regular call left (_fast_ready), returns None for inputs the regular path would treat differently, falls back per call, disables itself with a logged reason on divergence, and has an env kill switch. A careful way to depend on library internals; ultralytics is pinned in requirements-prod.txt.
- FastClahe (core/fast_clahe.py): bit-exact reimplementation of kornia's CLAHE with a batched histogram and device-cached indices, automatic fallback to kornia, a fallback counter, and a torch.equal test. The docstring explains why the original was launch-bound rather than GPU-bound.
- CPU and GPU overlap: the MOG2 feed is submitted before the GPU stage on a persistent worker and awaited only when the tracker needs it. The timing separates mog2_feed (overlapped window) from mog2_wait (time actually blocked), which is the right metric. OpenCV threads are restored after the ultralytics import, with a bit-identity test.
- D2H is deliberately minimal: preview download is rate-limited with a phase-keeping schedule (_preview_due), converted to uint8 on the GPU first, sized to the ROI footprint, and Standby preview uses the CPU frame with no download. Detections come back as three tiny copies.
- The output layers are strictly output-only: box clamp, box EMA, identity slots and the RTS smoother never mutate tracker state, and process() still returns the tracker's tracks. Replay goldens stay valid, and set_output_clock makes the output stage deterministic in replays. The D34 two-clock split (motion clock vs camera-live hold clock) is cleanly expressed in _hold_now.
- Optional evidence is fail-soft: belt detector, static-glint map, foreground plate and plate capture are each wrapped so a failure becomes one log line and a no-op, never a lost frame.
- One _post_yolo_chain is shared by the live path and the cached replay (replay_gpu_cached), parameterised by _TrackerSpace, so the tuning harness runs the same code as the show.
- The motion model is fed a fixed gray, never the adaptive-CLAHE display signal (_feed_motion_detectors), with the reasoning written down: per-frame CLAHE would fake frame-diff motion.
- TensorRT absence is loud rather than silent: missing-engine notice with the expected path, red preview banner, intent kept after a declined build so a save does not persist PyTorch, and a test inference at load that catches engines that deserialize but cannot run.
- InputTransform is applied where frames enter the app, identity returns the same object, and it carries the group algebra needed to move the ROI and exclusion mask across a transform change.

## Questions the reader could not answer from code

- Is skipping the size gate for a frame with a single detection intentional (pipeline.py:2388)? If so, what is the gate expected to do in a solo show?
- What ROI shapes and sensor crops are used on site: the square 1528x1528 crop with no ROI, or a wide strip along the wall? That decides whether rectangular engines are worth building.
- Is output smoothing L > 1 used in shows, and does the media server rely on /walldance/count reaching 0 (or on ids disappearing) to clear a dancer?
- Does the media server use keypoints, or only centroid and box? That sets how much the keypoint splicing in dedupe matters beyond its effect on the centroid.
- What do [Budget] lines show on the show laptop at 20 fps for yolo, mog2_feed, mog2_wait and process_wall? In particular, is mog2_wait above zero in the dark, meaning motion rather than YOLO sets the frame time?
- Are ROI, imgsz, model or gamma ever changed while dancers are on the wall, or only during setup? That sets the severity of the missing resets and of the gamma coupling.
- Is PyTorch an acceptable degraded show mode, or should a missing or stale engine block RUN until it is rebuilt? If it should block, should the build start without a prompt?
- Do two dancers at clearly different camera distances appear together? A single person_height_px and the 1.5 x height proximity rules assume they do not.
