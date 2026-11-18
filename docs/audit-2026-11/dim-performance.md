# Dimension — performance

*Context-aware auditor's report, followed by an adversarial reviewer's check of its six most consequential claims. Where the two disagree, the reviewer's reading is the safer one.*

## Verdict

The October pass moved the bottleneck. On the laptop the CPU motion feed is off the critical path (mog2_wait <= 0.9 ms even at x@800); YOLO is on it. At the D27 recipe (x@1280, confidence 0.15) I project 29-33 ms from frame pickup to OSC for one dancer and 33-37 ms for two, plus a 5-8 ms GUI tail: 9-13 ms and 5-10 ms of median headroom per 50 ms, none at p95. That is before TouchDesigner takes GPU time, and the GPU already ran 84-87 °C alone, above the 83 °C KPI. Every laptop [Budget] line in the repo is at imgsz 800; x@1280 with TD has never been measured. dev37 is a poor proxy: its CPU is 2.5x slower but its GPU about 1.5x faster at x@1280 (12.4-15.9 ms here, 21.3 ms there), so it inverts the bottleneck. The largest unshipped lever is a rectangular engine for the strip ROI the field used (1304x566: 57 % of the tensor is padding; about -11 ms and half the GPU duty). Then: D18 for centroid lag (-30 to -60 ms), event-driven pickup (-5 ms), foreground off the main thread (-2 ms), bridge queries (-2 to -4 ms at two dancers).

## Since the October audit

Shipped, verified in code: PERF-1 cv2 threads (pipeline.py:100-103), PERF-3 PoseRunner (yolo_runner.py; 319 fast calls of 320 here), PERF-4 bit-exact batched CLAHE (fast_clahe.py), the motion feed submitted first on a persistent worker (pipeline.py:607-611), PERF-8 in part (15 fps cap, texture-scale ROI preview, 60 Hz idle render), PERF-11 in part ([Budget] keys mog2_wait, dpg_render, gui_stats), PERF-0 through the D3 pin, PERF-14 as D27. PERF-10 is done in fact: the laptop runs Python 3.12.12, yet ROADMAP §5 still lists it. Built but OFF: PERF-9 as D18 (config.py:332). Not shipped: PERF-2 (motion_detector.py:204-212 and :83, motion_model.py:105), PERF-5 (main_loop.py:748), PERF-6, PERF-7 and the mono half of PERF-4 (ids_camera.py:1539, 1590; gpu_pipeline.py:389-420), the perf_counter and logger-thread parts of PERF-11, PERF-12, PERF-13. Still missing: any laptop measurement at x@1280 after the pass, and any with TD.

## Details

**Frame budget (ms, p50).** dev37: TRT x@1280, D27 recipe + take-median plate, 300 frames, replay path, box load 5-9 (CPU stages up to ~1.3x high). Laptop real: `tmp_analysis/field/night-0610/*.log` (live IDS, solo, 1137 lines, before the perf pass) and `tmp_analysis/laptop_0810/03_live_*.log` (playback, release c30b6e9), both imgsz 800. Laptop x@1280 is a projection (fps_table 21.3 ms; dev37 CPU / 2.5).

| Stage | dev37, 1 dancer | dev37, 2 dancers | Laptop real, x@800 | Laptop x@1280, 1 / 2 dancers |
|---|---|---|---|---|
| camera read | 1.9-5.2 emulated | – | 1.6 | 1.6 |
| enhance + letterbox | 8.0 (GPU ~2) | 8.3 | 4.1, then 0.5-1.0 with FastClahe | ~1 |
| YOLO whole call | 15.9 (min 12.4) | 15.2 | 8.4 / 7.5 | 18-21 |
| extract, dedupe, crossval, cold blobs | 4.9 | 6.0 | 0.2 (extract only) | ~2.2 |
| motion worker; main-thread wait | 31-41; 4-8 | 25; 0 | wait <= 0.9 | wait 0 |
| tracker.update | 8.4 (p95 20) | 17.6 (p95 27) | 0.9 (p95 10.5) | 3.4 / 7 (p95 8 / 11) |
| slots, foreground, belt, OSC | 7.7 (fg 5.5) | 8.8 (fg 5.7) | not logged | 3.2 / 3.5 |
| **process_wall (pickup to OSC)** | 65 (p10 55) | 64 | 19.5 (p95 30.9); 17.6-18.5 after the pass | **29-33 / 33-37** |
| GUI tail (preview, render, stats) | – | – | 5.7-7.6 | 5-8 |
| **loop, of 50 ms** | – | – | ~27 | **37-41 / 40-45** |

Headroom at 20 fps: 9-13 ms (one dancer) and 5-10 ms (two) at p50, none at p95: the x@800 night log has 51+ frames over 50 ms in 95 min. TouchDesigner time-slices the same GPU against a 42 % duty inference: a patch using 30 % of GPU time would add 6-9 ms per frame (estimate), and the 1.98 to 1.6 GHz clock drop seen in the balanced plan 5 ms. dev37 caveat: CPU ~2.5x slower per thread; GPU not equal: x@1280 takes 12.4-15.9 ms here, 21.3 ms on the laptop.

**Glass to OSC** (x@1280; 25 ms exposure as in the October configs; `IDS_MAX_FPS` caps 20 fps): half exposure 12.5 + readout/USB ~10 + unpack ~2.5 + poll pickup ~5.5 (up to 12) + read 1.6 + process 29-33 = **61-65 ms p50, ~90 p95** (+12 at 49 ms exposure). Output smoothing adds 55-85 ms on fast moves: **120-150 ms effective centroid lag**.

**Ranked (laptop gain; risk; gate).**
1. Rectangular engine for a non-square ROI (P-9): YOLO -11 ms at 1304x566 (-5 at 4:3), GPU duty 42 to ~21 %; medium; laptop fps + temperature, re-baselined goldens.
2. D18 raw centroid into the One-Euro: -30 to -60 ms centroid lag; medium (jitter x1.6); by eye on TD, slot_sweep.
3. Event-driven frame pickup (PERF-5): -5 ms mean, -10 p95; low; pickup-delay log.
4. Foreground + belt off the main thread: -1.5 to -2.5 ms; low; TEST-2 byte-equal.
5. Bridge frame-diff at half resolution, bound tracks only: -2 ms, -4 p95 at two dancers; medium; golden trio, TEST-1/2.
6. Vectorised cost matrix: -1 ms, -3 p95; medium; timelines byte-equal.
7. Preview download after the OSC emit: -1 ms on 3 frames in 4; low; test_preview_path.
8. Mono end to end (PERF-7, 1-channel enhance, PERF-6): -1 ms, half a core while recording; low; torch.equal on footage.
9. PERF-2 motion feed: 0 ms latency today, 2-3 ms of a core; medium; only once YOLO < 10 ms.

Harness, logs and raw JSON: `tmp_analysis/audit-2026-11/performance/`; `git status` clean after every run.

## Findings

### 1. x@1280 with two dancers leaves single-digit headroom on the laptop, and it has never been measured there, with or without TD

severity high · confidence medium · status known open · effort S · prior id: PLAN_25M B5 / REVIEW F6

**Evidence.** Every laptop [Budget] line in the repo is at imgsz 800: tmp_analysis/field/night-0610/walldance_20261006_201756.log (live, solo) gives process_wall 19.5 ms p50 / 30.9 p95 with yolo 8.4, and at least 51 frames over 50 ms in 95 min. fps_table_laptop.json puts x@1280 at 21.3 ms against 9.3 at 800. D27's confidence 0.15 doubles tracker.update here (4.1 to 8.4 ms, one dancer). PLAN_25M §C.12: the slot-9 take with TD was not shot. The GPU ran 84-87 °C alone (REVIEW §1.7); the KPI is < 83 °C.

**Recommendation.** Before any optimisation, spend 10 minutes on the laptop: replay a 2026-10-07 take and run live at x@1280 with the D27 settings, first alone, then with the TD patch rendering. Capture [Budget], [PerfSpike] and nvidia-smi clocks, power, temperature and throttle reasons. That number gates the rectangular engine, the bridge work and the x@960 fallback.

**Risk.** None in the measurement. Without it the 20 fps / 83 °C KPI is unverified and every perf choice below is ranked on a projection.

### 2. The square letterbox spends more than half of each inference on padding for the strip ROI used in the field

severity high · confidence medium · status known open · effort M · prior id: P-9

**Evidence.** gpu_pipeline.py:828-858 pads every ROI to imgsz x imgsz with 0.5. The laptop's `default` project ROI is 1304x566 (projects/default), so 57 % of the x@1280 tensor is padding. The laptop fps table is close to linear in pixels (9.3 / 12.6 / 21.3 ms at 800 / 960 / 1280): 1280x576 interpolates to ~10.4 ms. Probe here on a white-duo strip, rectangular against square: 67 of 72 detections matched, centre difference p50 0.5 % of height, confidence difference p50 0.02. yolo_runner.py:110 already accepts stride-32 rectangles.

**Recommendation.** Re-gate P-9 on ROI aspect (beyond ~1.3:1), not on 'the ROI exceeds imgsz'. Build 1280x576 and 1280x960 engines on the laptop DEV slot, add them to the fps table, read the GPU temperature, then re-baseline the goldens. Key engines by height and width (model_manager.py:113-125); `_TrackerSpace.frame_width` must take the tensor width.

**Risk.** Detections shift slightly (padding context), so the engine-locked goldens move. One more engine to build per ROI shape; a ROI changed after Calibrate needs a matching engine or a fallback to the square one.

### 3. Two-dancer tracker time is the motion bridge's connected-component queries on a full-resolution frame difference, then a Python cost matrix

severity medium · confidence high · status known open · effort M · prior id: P-8 / PLAN_25M A10

**Evidence.** white-duo, D27, dev37: tracker.update 17.6 ms p50 / 27 p95 (about 7 / 11 on the laptop): `_lazy_bridge_with_motion` 6.5 ms, matching 6.8 ms of which `_compute_cost_matrix` 4.8. Per frame: 7.6 `frame_diff_blob_in_bbox` calls, 9.4 `connectedComponentsWithStats`, and (cProfile, texture-duo) 185 `np.linalg.norm` and 15 `np.linalg.inv`. motion_detector.py:83 sets the diff scale to min(1, 2 x mog2_scale): full resolution at any scale from 0.5. tracker.py:2947-3033 queries every unmatched track with enough hits, tentative ghosts included. Same shares as REVIEW §1.7: nothing shipped.

**Recommendation.** Cap the frame-difference pair at half resolution and run the frame-diff tier only for slot-bound or established tracks; then vectorise the cost matrix with a cached inverse covariance per track. Gate each on the golden trio, TEST-1/TEST-2 and `track` p95 at two dancers on the laptop. CONT-11 (retire the bridge) removes the first item entirely.

**Risk.** Tracker behaviour changes (bridge holds on small motion). The keep-as-is rule 'no tracker split before id-level goldens' applies: stay inside the existing functions.

### 4. The clean-plate foreground and the belt hook run in series on the main thread before the OSC send, outside the [Budget] line

severity medium · confidence high · status new · effort S

**Evidence.** pipeline.py:1523-1526 calls `_belt_hook` and `_fg_frame` inside `_run_identity_slots`, before `_emit` (1054). foreground.py:47 converts the whole ROI to float32, then area-resizes by 4. Measured here: fg 5.5-6.1 ms p50 (4.0 min, about 1.6-2.4 on the laptop), slots total 7-10 ms on a duo, belt `detect_near` 2 ms on the textured wall. The same foreground on the GPU, with the frame already there: 0.7-1.3 ms including the mask download. main_loop.py:1457-1461 prints no `fg` and nothing after `track`.

**Recommendation.** Compute `ForegroundDetector.process` on the motion worker, which already holds the same raw gray and is idle before YOLO returns: same function, same input, so the emitted stream should stay byte-equal (TEST-2). Add `fg` and a `post` key (finalize to emit) to [Budget]. Move it to the GPU only with the foreground-first work.

**Risk.** Low. `update_plate` and the slot protection boxes stay on the main thread; the worker must finish before the await, which it does with margin at x@1280.

### 5. Effective centroid lag is 120-150 ms; the two levers that cost no GPU are built or small, and both wait

severity medium · confidence medium · status known open · effort S · prior id: D18 / PERF-5 / PERF-9

**Evidence.** config.py:307 keeps the tracker EMA at 0.5 and config.py:332 the One-Euro input at 'smoothed': 55-85 ms of lag on fast moves against 26-33 ms with variant B (REVIEW §1.5), key built, D18 open. main_loop.py:744-749 polls the camera with sleep(0.01) plus an idle render: about 5.5 ms mean, up to 12, before a frame is picked up. gpu_pipeline.py:755-775 downloads the preview (`.cpu()`, 1.0 ms on the laptop) before YOLO on three frames in four.

**Recommendation.** Decide D18 on the TD screen with a fast swing: it is the only lever worth tens of milliseconds. Replace the poll by a `threading.Condition` the acquisition thread notifies, with the 10 ms sleep as timeout. Keep the preview tensor and download it after `_emit`.

**Risk.** D18 raises jitter at rest about 1.6x (2 % of height). The Condition wait must still run the stall keep-alive and the idle render on timeout.

### 6. The CPU motion feed is off the laptop's critical path; PERF-2 and PERF-7 no longer buy latency there

severity low · confidence medium · status known open · effort S · prior id: P-8 (PERF-2, PERF-7) / CONT-11

**Evidence.** Laptop, release c30b6e9 (tmp_analysis/laptop_0810/03_live_*.log): `mog2_wait` is printed in 10 of 56 lines and never exceeds 0.9 ms at x@800, where the GPU stage is about 10 ms; x@1280 doubles that stage. `mog2_feed` (pipeline.py:759) is submit-to-await, so it reads max(worker, GPU stage), not worker time. Worker here, 1296x1195 at scale 0.7: 17.7 ms min (median5 at full resolution 6.0, MOG2 4.9, Welford 2.5; motion_detector.py:204-212, motion_model.py:105), about 7 ms of one laptop core.

**Recommendation.** Read `mog2_wait`, not `mog2_feed`, in ROADMAP P-8's condition; it already answers 'not critical'. Keep PERF-2 (blur after the resize: -4.4 ms here) for the day a rectangular or smaller engine brings YOLO near 10 ms, or for CPU heat. Retiring the bridge (CONT-11) leaves frame-diff plus the plate: 1-1.7 ms on the GPU.

**Risk.** The evidence is one ROI (1304x566) at imgsz 800; a full-height ROI at mog2_scale 0.99 could still outlast YOLO. Check `mog2_wait` in the x@1280 capture.

### 7. Stage timers mislead: 0.5 ms clock steps on the laptop, GPU work billed to the next sync, and dev37 inverts the bottleneck

severity low · confidence high · status known open · effort S · prior id: PERF-11

**Evidence.** gpu_pipeline.py:610-672 and 711-769 time stages with time.time(); in the laptop night log `enhance` clusters on 3.0 / 3.5 / 4.0 / 4.5 / 5.0 (213 lines at 4.0, 162 at 4.5) while perf_counter keys are continuous. Sync-mode run here: enhance 5.8 ms is launch time, the GPU work is about 2 ms and lands in `yolo` at yolo_runner.py:115. REVIEW §1.7 states GPU parity, yet x@1280 costs 12.4-15.9 ms here and 21.3 ms on the laptop. tracking_logger.py:290 still flushes on the main thread (13 ms max here).

**Recommendation.** Use perf_counter in gpu_pipeline.py, print `fg` and `post`, and state in TUNING.md that dev37 is CPU-bound where the laptop is GPU-bound, so perf gates run on the laptop DEV slot. Move the 5 s logger flush to a writer thread.

**Risk.** None for the timers. A writer thread must keep the per-show log order (one queue, one writer).

### 8. Mono carried as three channels, per-frame copies and per-frame NVML cost about 1 ms on the laptop in total

severity low · confidence high · status known open · effort M · prior id: PERF-7 / PERF-4 / PERF-6

**Evidence.** ids_camera.py:1590 makes a contiguous 3-channel float frame (28 MB), :1539 a BGR copy, pipeline.py:1746 copies channel 0 back out, gpu_pipeline.py:389-420 runs rgb-to-ycbcr, a clone and ycbcr-to-rgb on R=G=B. Measured here: read 1.13 to 1.03 ms; enhance + letterbox 3.57 to 2.88 ms synced on one channel, output bit-identical on two ROIs of one frame; CPU round trip 1.3 ms. gui.py:1728 issues four NVML queries on every StatsTick (main_loop.py:1114): 0.04 ms here, and the laptop's whole `gui_stats` is 0.3 ms.

**Recommendation.** Low priority. Do it once as 'mono end to end' together with PERF-6 (mono FFV1, which frees about half a core while recording): expose the mono8 frame, crop before the float conversion, one-channel CLAHE, expand at the YOLO tensor. Delete the call at gui.py:1728; the 1 Hz GpuStats event already refreshes it.

**Risk.** Low. Check torch.equal against the current path on real footage. Recordings become mono files that playback and replay must read (decode was verified byte-identical in October).

## Paradigm-level change proposed

Take YOLO off the critical path of the output. Today every OSC sample waits for one x-large pose pass on a square tensor (18-21 ms of about 30 on the laptop, 42 % GPU duty, 84-87 °C) to produce a centroid. The frame is already on the GPU: a plate foreground plus a frame difference there costs 1-1.7 ms (measured here), and the brainstorm measured foreground-only presence beating the shipped stream on the April duos. The paradigm: a camera-clocked fast lane (GPU foreground, slot filter, OSC; about 5 ms of compute, glass to OSC near 35 ms) carries position and presence; YOLO becomes asynchronous evidence that births, splits and vetoes slots. It can then run rectangular, smaller or every second frame without the 8-21 points of presence that MULTIPASS §3.1 lost by running the whole pipeline at 10 fps. MOG2, the motion bridge and their component queries retire (CONT-11). Cost: two to three weeks, plus the two-dancer ground truth (t-020) as the entry ticket, which is also what the 'no tracker split before id-level goldens' rule asks for. Risks: touching dancers merge into one blob, and a stale plate, where the fall-back is today's path. Cheapest intermediate step, with no behaviour change: compute the foreground on the worker, record each slot's foreground centroid beside the YOLO one in the replay timeline (`--internal` already carries `fg`), and score lag and agreement on the 2026-10-07 takes and the duos. In parallel the rectangular engine halves the GPU duty now.

## Questions for the owner

- What does the TouchDesigner patch cost on the GPU (output resolution, frame rate, GPU time per frame), and is there any [Budget] or nvidia-smi capture with it running beside WallDance at x@1280?
- Which exposure is the show's: 25 ms, as in the October project configs, or about 50 ms? At 25 ms the 20 fps is the IDS_MAX_FPS cap, not the exposure, and half-exposure latency drops from 25 to 12.5 ms.
- What ROI shape do you expect at the 30-40 m portrait venue (aspect ratio, and is the sensor crop itself portrait)? It sets which rectangular engines to build.
- Is Lenovo Performance mode with the chassis raised now the standing setup, and what GPU temperature and clock does it give with WallDance alone at x@1280?
- Does the operator need the preview at 15 fps during a show? It costs 5-8 ms on three frames in four on the thread that also sends OSC.

## Adversarial review

The report is reliable on code facts and overreaches on what follows from them. Every file:line I opened matched, and it labels its laptop x@1280 figures as projections. Two of the six claims hold (the bridge cost and the foreground on the main thread); four are weakened.

- **Rectangular engine:** its top lever rests on a ROI (1304x566) used for one evening in the `default` project. The 25 m wall configs with the D27 recipe use 1322x1275, where the gain is about zero.
- **D18:** it understates the jitter cost. With D27 the jitter KPI already forced Stability to 1.23 on the laptop, and D18 multiplies jitter by 1.6.
- **dev37 as proxy:** the "GPU 1.5x faster" figure compares two different harnesses; like for like it is nearer 1.1-1.25x.
- **Rethink:** it restates the brainstorm's foreground-first idea without that document's caveats (shadow-biased centroid, fixed N, pre-D27 baseline).

The largest gap is that camera stalls, and the repo's own finding that GPU load amplifies them, are absent from a performance review whose ranking is in single milliseconds.

Good catches worth keeping:
- Every October project config has 25 ms exposure, not the roughly 50 ms assumed in the brief.
- PERF-10 is done on the laptop (Python 3.12.12) but still listed in ROADMAP §5.
- The `time.time()` stage timers quantise to 0.5 ms on the laptop.
- The foreground cost is not itemised in [Budget].

I did not run GPU replays, so the dev37 stage numbers are taken from the auditor's raw JSON, which matches the report. The repo is clean (`git status --short` empty).

### Claims checked

- **weakened** — Verdict: the October pass moved the laptop bottleneck from the CPU motion feed to YOLO; x@1280 projects to 29-33 ms (one dancer) and 33-37 ms (two) from pickup to OSC with single-digit headroom; x@1280 has never been measured on the laptop; dev37's GPU is about 1.5x faster, so it inverts the bottleneck.  
  Direction holds; laptop x@1280 was measured before the pass, the GPU gap is nearer 1.1-1.25x than 1.5x, and every post-pass x@1280 figure is a projection from dev37 runs taken at box load 5-9.  
  Evidence: Holds: tmp_analysis/laptop_0810/03_live_walldance_20261008_125451.log and 03_live_walldance_20261008_125946.log print mog2_wait in 10 of 56 [Budget] lines, max 0.9 ms, yolo p50 7.5-7.8 ms, imgsz=800 (line 42). My recount of tmp_analysis/field/night-0610/walldance_20261006_201756.log gives process_wall p50 19.5 / p95 31.0 and yolo 8.4 over 1137 lines. Against 'never measured': docs/ROADMAP_v2_REVIEW.md:249-252 reports laptop replays at x@1280 on 2026-10-06 (40-44 ms frames, yolo 16-23, tracker 6-17, 84-87 °C), before the perf merge; tmp_analysis/remote-runs/20261006-111329/summary.json shows imgsz 1280, path trt. Against '1.5x': the laptop's 21.3 ms comes from extra/measure_engine_fps.py:30-39, a full ultralytics call on a numpy image, not the pipeline's GPU-tensor call; at 800 that table gives 9.3 ms where the live call measures 7.5 ms. The 10-08 log loads default_20261007_203027.json (line 38): roi_enabled false, mog2_scale 0.5, not the 1304x566 ROI the report names as its evidence.
- **weakened** — The largest unshipped lever is a rectangular engine for the strip ROI the field used (1304x566: 57 % of the x@1280 tensor is padding; about -11 ms and half the GPU duty).  
  Gain is about 0 ms on the 25 m wall project, 4-6 ms at full frame, and 11 ms only on a ROI used once; it is not the largest lever for Priority 1.  
  Evidence: Code facts hold: application/src/core/gpu_pipeline.py:828-858 pads to a square; application/src/core/yolo_runner.py:110-111 accepts stride-32 rectangles; application/src/core/model_manager.py:113-125 keys engines by one size; application/src/core/pipeline.py:772 sets frame_width from imgsz. The premise does not: 1304x566 appears only in projects/default/default_20261006_210310.json and _210822.json (one evening, imgsz 800). The 25 m wall configs that carry the D27 recipe at 1280 use ROI 1322x1275: tmp_analysis/laptop_0710/projects/mur25m-ceinture-0610/mur25m-ceinture-0610_20261007_142834.json and _164233.json, projects/mur25m-night-common/mur25m-night-common_20261007_183018.json. That is about 3 % padding. The 2026-10-07 night takes ran with the ROI off at 1776x1304 / 1872x1240 (25-33 % padding). No rectangular TensorRT engine was built; the parity probe (tmp_analysis/audit-2026-11/performance/rect_probe.py) is PyTorch FP16 on a synthetic strip and left 5 of 72 detections unmatched.
- **weakened** — Effective centroid lag is 120-150 ms; D18 (raw centroid into the One-Euro) is the only lever worth tens of milliseconds (-30 to -60 ms, jitter x1.6 = 2 % of height); event-driven pickup saves 5 ms.  
  D18 multiplies a jitter that already forced Stability up to 1.23 in the field, so it trades one KPI for a lag that is not a KPI miss; it stays the owner's open decision by eye on TD.  
  Evidence: Code facts hold: application/src/core/config.py:307 (EMA 0.5) and :332 ('smoothed'); application/src/runtime/main_loop.py:744-748 (poll with sleep 0.01); application/src/camera/ids_camera.py:1519-1527 (frame hand-off is a polled flag); application/src/core/gpu_pipeline.py:755-775 (preview download before YOLO). But the KPI 'lag <= 80 ms' is the smoothing lag `lag_ms` (docs/PLAN_25M_2026-10.md:22), met today at 61-75 ms. With the D27 settings on the white duo, jitter at rest is 3.16 % of height at Stability 0.5, and 1.25 is the first value that meets the 1.5 % target (same file, lines 296-304). The laptop's last saved config runs stability 1.23 and hold 6 s (tmp_analysis/laptop_0810/03_live_walldance_20261008_125451.log:55). The '2 % of height' the report quotes is the pre-D27 aerial take (docs/ROADMAP_v2_REVIEW.md:193). The readout, unpack and poll terms of the 120-150 ms are estimates; no pickup-delay log exists.
- **holds** — Two-dancer tracker time is the motion bridge's connected-component queries on a full-resolution frame difference, then a Python cost matrix (17.6 ms p50 / 27 p95 on dev37; nothing shipped since the review).  
  The laptop figure (7 / 11 ms) is dev37 divided by 2.5 under load and sits on the owner's own gate (ROADMAP P-8, PLAN A10: only if track p95 > 10 ms with TD), still unmeasured; halving the diff resolution also changes fixed-DN thresholds, not only speed.  
  Evidence: application/src/core/tracker.py:2947-3033: every unmatched track passing line 2949 (established, already bridged, or enough hits) is queried, frame-diff tier at 3004. application/src/core/motion_detector.py:83 sets the diff scale to min(1, 2 x scale), and set_scale (89-96) never recomputes it, so it is always full resolution; one connectedComponentsWithStats per query at :578. Raw run tmp_analysis/audit-2026-11/performance/runs/wduo_d27.json: tracker_update p50 17.56 / p95 27.19, frame-diff 5.6 ms over 7.55 calls, 9.4 component calls per frame. git log shows no commit to motion_detector.py or motion_model.py after 2026-10-06 14:00.
- **holds** — The clean-plate foreground and the belt hook run in series on the main thread before the OSC send, outside the [Budget] line; computing the foreground on the motion worker keeps the stream byte-equal and saves 1.5-2.5 ms.  
  The cost is inside process_wall, only un-itemised; foreground.py:21 puts it at 1-2 ms on the laptop (under 1 ms on a strip ROI), so 'medium' is generous, and replay_gpu_cached (pipeline.py:832-833) needs the same change to stay byte-equal.  
  Evidence: application/src/core/pipeline.py:1523-1526 calls _belt_hook and _fg_frame inside _run_identity_slots, before _emit at :1054. application/src/core/foreground.py:47 converts the ROI to float32 then area-resizes. The foreground input is the raw ROI gray already handed to the worker (pipeline.py:797 and 1746-1752). application/src/runtime/main_loop.py:1457-1461 has no 'fg' key although pipeline.py:1051-1052 already fills timing['fg'].
- **weakened** — Rethink: take YOLO off the critical path. A camera-clocked foreground lane (about 5 ms, glass to OSC near 35 ms) carries position and presence; YOLO becomes asynchronous evidence and can run rectangular, smaller or every second frame; two to three weeks plus the two-dancer ground truth.  
  A restatement of the brainstorm with its caveats left out; it buys glass-to-OSC latency, which is neither a KPI nor a field pain, at the price of centroid bias, and never addresses a GPU fast lane queueing behind an asynchronous YOLO pass on the same GPU.  
  Evidence: Checked and true: application/tests/replay.py:630-635 records foreground blobs under --internal; docs/MULTIPASS_REVIEW_2026-10.md:76-83 gives the 8-21 point loss at 10 fps. But the idea is BRAINSTORM items 2 and 7 (docs/BRAINSTORM_APPROACHES_2026-10.md:53, 58, 142), already placed 'before production (needs #2 first)'. The 0.96-0.97 vs 0.74 comparison is against the pre-D27 stream; with D27 the white duo reaches on-dancer 0.93 (docs/ROADMAP.md:116-117). The foreground-only rows have 100 % two-point coverage by construction with N fixed (BRAINSTORM:313), against D4. The foreground centroid is biased by shadows: +0.10-0.13 h, p90 0.46-0.49 h, fast error 0.19-0.27 h against 0.15-0.18 shipped (BRAINSTORM:291, 314), and the YOLO-corrected variant jitters 2.9-8.6 % of height (line 315) against a 1.5 % KPI. Plates were take-median or causal on April off-axis duos (line 316). MULTIPASS lines 8 and 20-27 conclude 'keep one x@1280 pass at 20 fps, no model swap'.

### Missed or out of proportion

- Camera stalls are the dominant latency event and the report never links them to GPU load. At 1.65-1.7 s and 0.7-2.9 per minute, 2-8 % of show time has no fresh frame, which dwarfs every item in its ranked list. docs/archives/IDS_STALL_CONCLUSIONS.md:13 and :37 (same laptop GPU, February) found GPU workload amplifies stalls 2-4x (YOLO at 1280: about 1.5 to 4-6 per minute) and line 97 advises fewer preview downloads; the October pass raised the preview to 15 fps (application/src/core/config.py:553). Halving the GPU duty, or moving transfers, should be gated on extra/ids_stall_ab.py --gpu (line 175). Not re-tested with the October code.
- The source is not a steady 20 fps at the landscape crop, so the 50 ms period the whole budget rests on is not constant. tmp_analysis/laptop_0810/05_stall_ab.json: one 3-minute run at 1776x1304 delivered 3128 frames (17.38 fps) with no stall flagged, against 19.99 fps at 1488x1536; the camera's maximum at that geometry is 24.4 fps (05_stall_ab_stdout.txt:13). The '20 fps sustained' KPI can fail at the camera before compute matters. One run only; whether it repeats is not checked.
- Operator-triggered blocks on the thread that sends OSC dominate the tail and are absent from the ranking. The readiness check runs synchronously on the loop thread for about 0.3 s (application/src/app.py:2314-2335, OSC probe timeout 0.25 s at application/src/core/config.py:1099), on every click of the Calibrate rail. The night log has a [PerfSpike] dpg_render=238.1ms. The report's 'about 90 ms p95' covers neither.
- The x@960 fallback it keeps as an option is not available at Priority 2. At about 30 m dancers are 92-100 px in YOLO's input at x@1280 (docs/ROADMAP.md:107), so 69-75 px at 960, below the 80-110 px knee (docs/MULTIPASS_REVIEW_2026-10.md:100); full-frame recall at 960 falls to 2 % on facade and 59 % on the textured duo (lines 94, 98). The stronger case for a rectangular engine is there (more pixels on the dancer at today's cost), though MULTIPASS lines 24-27 found x@1536 worse on the dark far-wall take.
- The measurement it asks for cannot give a p95 as specified. [Budget] prints one frame every 5 s and hides values of 0.1 ms or less (application/src/runtime/main_loop.py:1455-1466), so 10 minutes yield about 120 samples; a per-frame timing dump is needed. Its night-log baseline is also not 'live IDS, solo': the log mixes live, playback and Calibrate sessions across two projects (default, then mur25m-ceinture-0610 from line 1798).
