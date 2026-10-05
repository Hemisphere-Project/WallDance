# 04 — Runtime performance & latency audit (WallDance show path)

Stream 4 of 5 (perf). Read-only on the repo source. All harnesses, logs and raw JSON are in
`tmp_analysis/audit-2026-10/perf/` (`runs/*.json`, `cpu_micro_run2.json`, `runs/gpu_micro_1.json`,
`runs/engine_bench_1.json`, `engines/build*.log`). Tags: **[MEAS]** measured here,
**[PROD]** read from a real RTX 5080-laptop log, **[EST]** derived estimate, **[CODE]** read from source.

> **Measurement caveat (read first).** The dev box was **heavily contended** for the whole session. Load
> average was 13–35 on 8 threads (the continuity and markers agents ran 3–4 Python processes), and other
> processes kept the GPU 15–75 % busy. Every timed run records its `load_before` and `nvidia_smi` stats in its JSON.
> - **GPU-graph-bound numbers are trustworthy.** These are the TRT engine forward times measured with CUDA events.
> - **CPU-bound numbers are inflated 1.5–3× at p50.** For those I quote the **min / p10** of micro-benchmarks
>   (robust to contention). I anchor every projection to the one real prod log in the repo (§2.1), not to this box.

---

## 0. Executive summary

1. **[PROD] On the show laptop, the CPU motion feed is on the critical path, not YOLO.** The only prod timing log
   (`application/tests/golden/decomp-phase0/timing-baseline.log`, RTX 5080 laptop, playback of hangar-whitebg2
   slot 4) shows these p50 values over 72 budget lines:
   - TRT `yolo11x-pose_960`: `yolo` = 11.6 ms
   - MOG2 + frame-diff worker: `mog2_feed` = 19.3 ms
   - `enhance` = 4.6 ms
   - `track` = 2.5 ms
   - `process_wall` = 31.6 ms (p95 35.8)

   The main thread waits roughly 7 ms per frame for the motion thread (`pipeline.py:613-616`). The fps (19.7) is
   **source-bound**: the 49 ms exposure / 19.7 fps recording, and the IDS camera behaves the same way. Compute is not the limit.
2. **[MEAS] OpenCV runs single-threaded in the whole app.** `import ultralytics` calls `cv2.setNumThreads(0)`
   (`.venv/.../ultralytics/utils/__init__.py:176`). `core/pipeline.py:16` imports it, and the app never restores
   the thread count. Restoring threads makes the motion feed **bit-identical** (verified over 80 frames: MOG2 mask,
   clean mask, diff pair and noise σ all equal). It cut the feed from 49 → 34 ms (min) on this box: MOG2 20.2 → 8.9 ms,
   INTER_AREA resize 9.2 → 3.9 ms. This is the cheapest latency win available: **≈ −5…−8 ms/frame on prod, no golden change**.
3. **[MEAS] The TRT 11 venv cannot build FP16 engines the app's way.** `uv.lock` pins tensorrt 11.3.0.99 and ultralytics 8.4.173.
   - **The June engines are dead.** They fail to deserialize: serialization tag 243 ≠ 244.
   - **`model.export(format="engine", half=True)` now fails.** It needs `nvidia-modelopt` (TRT 11 is strongly typed), and modelopt is not in the venv.
   - **`ModelManager` would then silently fall back to PyTorch** (`model_manager.py` load/export `except` paths).

   On this box, eager PyTorch x@1280 takes ≈ 100 ms of inference against **12.5 ms** for TRT. On prod this would push
   the frame past the 50 ms budget. I rebuilt x/l @ 960/1280 with a scratch-only ONNX-FP16 workaround (§9).
4. **[MEAS] Isolated TRT 11 FP16 GPU forward on the RTX 3090** (CUDA-graph replay, p50):

   | Model | 960 | 1280 |
   |---|---|---|
   | yolo11x | 9.1 ms | 12.5 ms |
   | yolo11l | 4.5 ms | 7.9 ms |

   **[EST] On prod every one of these is ≤ the 19 ms motion feed.** So moving x@960 → x@1280, or swapping x ↔ l,
   costs ~0 wall time today. The imgsz/model choice should be made **purely on accuracy** (Phase 2b) until the motion feed gets faster.
5. **[MEAS] Ultralytics hides ~4–5 ms/frame of avoidable work per call** (min, this CPU). Measured cost:
   - **6 device-wide `cuda.synchronize`** per frame (`ultralytics/utils/ops.py:74`).
   - **A full-image `im.max()` sync** (`data/loaders.py:669`).
   - **A ~4.9 MB D2H copy of the whole letterboxed input every frame** (`models/yolo/detect/predict.py:69`).
     WallDance never reads `orig_img`. This copy is the biggest PCIe transfer per frame, which matters for the
     IDS USB3/PCIe stall story.
   - **A `'half' is deprecated` warning printed to stderr every frame** (`pipeline.py:587`; 300 warnings per
     300-frame replay). On a Windows console this is a blocking-I/O hazard.
6. **[MEAS] kornia CLAHE is launch-bound, not GPU-bound.** Per frame it issues 218 kernel launches, a 64-iteration
   per-tile `histc` loop, and ~7 host syncs, for **1.85 ms of GPU time**. Wall time is 13.5 ms (min) on this box and
   4.6 ms on prod. A batched or fused CLAHE would bring it to ~1–2 ms.
7. **[EST] Glass → OSC latency (IDS, L=1) ≈ 80 ms p50 / ~110 ms p95.** The terms are:
   - ½ exposure: 25 ms
   - readout/USB: ~10 ms
   - unpack: ~3 ms
   - poll pickup: ~7 ms
   - upload: ~2.5 ms
   - processing: ~31 ms

   The OSC **centroid** carries an extra ~1 frame (≈50 ms) of EMA lag (`CENTROID_OUTPUT_SMOOTHING=0.5`,
   `tracker.py:495-497`), so its effective latency is ≈130 ms. The biggest levers are exposure (hardware/IR), the
   centroid filter, the motion critical path, and event-driven pickup. Achievable: **~55 ms glass→OSC, ~60 ms effective centroid**.
8. **[MEAS] Live-app extras are small on prod but not free.** All are per preview frame at ≤ 10 fps (box min):
   - preview ROI compose + resize: 6.8 ms
   - GUI RGBA-float conversion: 3.9 ms
   - two full-frame `.copy()` review stashes per frame
   - web-monitor JPEG: 5.6 ms, on HTTP threads only

   Recording is the outlier: **FFV1 BGR encode costs 145 ms/frame (min) on this CPU vs 61 ms for mono FFV1**. Mono
   FFV1 decodes to byte-identical BGR (verified), so mono recording is a free 2.4× cut in encoder CPU.
9. **[EST] `mog2_scale` is a hidden fps lever.** The feed costs 27 / 51 / 80 ms (min, single thread, box) at scale
   0.5 / 0.7 / 0.99. Pinned scenarios use 0.99 (hangar-floor, texture-wallhang). Prod at 0.99 is ≈ 31 ms motion →
   `process_wall` ≈ 43–45 ms, close to the 50 ms frame budget once the GUI tail is added.
10. **[MEAS] An IR-marker stage costs** (§8):
    - **On GPU:** ≈ 0.8–1.0 ms (threshold + max-pool NMS + sparse D2H, 1.5–4 MP).
    - **On CPU:** ≈ 2–3.6 ms min sparse (threshold + `findNonZero`), but 14.5–27 ms single-thread for a full-frame `connectedComponentsWithStats`.

    **Cross-stream warning:** the markers stream (02 §2.1) proposes running the detector inside the motion worker.
    That worker *is* the prod critical path, so the marker cost lands 1:1 on latency unless PERF-2 lands first,
    or the detector runs in the IDS acquisition thread instead.

---

## 1. Setup, commands, contention

- **Box:** i7-3770K (Ivy Bridge, no AVX2: OpenCV uses SSE4/AVX dispatch only), RTX 3090 (sm_86), Linux, Python 3.10.19.
  Libraries: torch 2.14.1+cu130, TensorRT 11.3.0.99, ultralytics 8.4.173, kornia 0.8.2, numpy 2.2.6, OpenCV 4.11.0
  (pthreads backend, 8 threads by default before ultralytics is imported).
- **Workload:** `tests/scenarios/hangar-aerial.json` → `3_TANGO_HANGAR-whitebg2` slot 4 (symlink to
  residence1-solo), frames 1500–1814, 15 warm-up + 300 timed.
  - Recording: 1488×1528 FFV1.
  - ROI: 1296×1195.
  - Brightness: raw mean 6.2, mean 43.9 after γ 2.2. That is below the low-light threshold of 55, so the
    **median5 + gauss5 + var×2 low-light branch is active** (`motion_detector.py:205-209`).
  - The scene has one dancer; mean reported tracks = 0.96.
- **Harness:** `perf/perf_harness.py`. It builds the real `FrameProcessor` exactly like `tests/replay.py`
  (`replay._build_processor` + `scenario_config`), then monkeypatch-wraps every stage. Two modes:
  - `natural`: no extra syncs. This gives the true wall cost.
  - `sync`: `torch.cuda.synchronize()` bracketing on GPU stages.

  It attaches a real `OSCSender` (UDP to 127.0.0.1:59999), starts the tracker JSONL logger, and samples `nvidia-smi` every 250 ms.

  Command (from `application/`):
  `.venv/bin/python tmp_analysis/audit-2026-10/perf/perf_harness.py --trt --model yolo11x-pose --imgsz 1280 --mode natural --osc --out tmp_analysis/audit-2026-10/perf/runs/trt_x1280_nat_a.json`.
  Batches are in `perf/run_matrix.sh` with `matrix{1,2,3}.txt`; logs are in `runs/matrix*.log`.

  `tests/replay.py` itself has no per-stage timers, so I used it only for the golden check:
  `uv run --no-sync python tests/replay.py --scenario tests/scenarios/hangar-aerial.json --trt --score --log-dir tmp_analysis/audit-2026-10/perf/logs/replay_trt11_x1280`.
  It took 2 min 03 s at load ≈ 30 and the result was **PASS, class A**.
- **Other scripts:**
  - `perf/engine_bench.py`: isolated inference.
  - `perf/cpu_micro.py`: CPU micro-benchmarks on real frames. Stats are min/p10/p50, `--reps 60`.
  - `perf/gpu_micro.py`: GPU micro-benchmarks.
  - `perf/enhance_prof.py`: `torch.profiler` of the kornia enhance.
  - `perf/sync_sites.py`: monkeypatches `Tensor.cpu/item/__bool__/...` and `cuda.synchronize` to enumerate sync call sites.
  - `perf/build_engines.py`: engine rebuild.

---

## 2. Measured budget tables

### 2.1 [PROD] RTX 5080 laptop anchor (the only real prod timing in the repo)

**Source.** `application/tests/golden/decomp-phase0/timing-baseline.log`, dated 2026-06-11 (decomp Phase 0 baseline).
It is a playback of `3_TANGO_HANGAR-whitebg2` slot 4 on Windows. The log loads **`yolo11x-pose_960.engine`** (line 35).
The sibling `timing-baseline-summary.json` says "TRT yolo11x-pose 1280", but that is **mislabelled**.

**Method.** I parsed the 72 `[Budget]` lines after the first, warm-up line:

| Stage (`[Budget]` key) | p50 ms | mean | p95 | max |
|---|---|---|---|---|
| `process_wall` (upload → OSC send) | **31.6** | 31.2 | 35.8 | 40.0 |
| `yolo` (whole ultralytics call, TRT10 FP16 x@960) | 11.6 | 11.6 | 12.9 | 13.2 |
| `mog2_feed` (submit → await returns; includes the YOLO overlap) | **19.3** | 19.4 | 23.0 | 23.6 |
| `enhance` (kornia CLAHE+γ, not synced) | 4.6 | 4.6 | 5.6 | 5.9 |
| `track` / `tracker_update` (61 lines where > 0.1) | 2.5 | 2.8 | 6.0 | 8.4 |
| `mog2_cvt` | 1.0 | 1.0 | 1.5 | 1.6 |
| `extract_cpu_total` | 0.2 | 0.2 | 0.4 | 0.5 |
| `preview_download` / `preview_draw` / `preview_upload` (≤10 fps) | 1.0 / 1.0 / 3.0 | | 1.7 / 1.5 / 3.5 | |
| FPS (source-paced) | 19.7 | 19.70 | min 19.4 | |

**How to read it:**
- `mog2_feed > yolo` means the motion worker finishes ~7.7 ms after YOLO+extract. Those 7.7 ms are pure critical-path time.
- 149 `[PerfSpike] total≥35 ms` lines fired, throttled to ≤1/s. So roughly every second at least one frame crosses 35 ms.
- `dpg_render` never appears in `[Budget]` even though it is in `budget_keys`. It is written into `app.timing` at
  `main_loop.py:926`, after the budget print at `:796`/`:1051`, and the next tick overwrites `app.timing`. **The GUI
  tail has never been measured on prod** (see the Questions section).

### 2.2 [MEAS] This box — per-stage p50 (ms), 300 frames, natural mode unless noted

| Stage | TRT x@1280 a / b | TRT x@1280 **sync** | TRT x@960 a / b | TRT l@1280 | TRT l@960 | PT-FP32 x@1280 | PT-FP16 x@1280 |
|---|---|---|---|---|---|---|---|
| `g_upload` (BGR → pinned → GPU float, flip) | 6.8 / 8.8 | 10.2 | 8.7 / 11.0 | 14.8 | 14.6 | 4.0 | 9.0 |
| `g_enhance` (kornia, launch-bound) | 27.6 / 26.8 | 25.9 | 26.9 / 32.0 | 47.8 | 42.2 | 29.1 | 31.5 |
| `g_letterbox` | 0.15 | 0.54 | 0.15 | 0.15 | 0.15 | 0.14 | 0.15 |
| **`yolo_call`** (whole `model()`) | **37.1 / 33.9** | 32.4 | **25.7 / 28.3** | 33.1 | 28.0 | **121.6** | 118.1 |
|  ↳ `y_inference` (ultralytics Profile) | 21.5 / 17.7 | 18.1 | 11.4 / 12.2 | 12.0 | 9.9 | 101.9 | 97.3 |
|  ↳ `y_postprocess` (NMS + Results, CPU) | 10.6 / 10.8 | 9.7 | 9.8 / 10.9 | 14.4 | 12.7 | 13.8 | 12.6 |
|  ↳ `y_origimg_d2h` (unused `orig_img`) | 4.0 / 4.5 | 3.8 | 2.9 / 3.2 | 5.3 | 1.7 | 4.3 | 3.8 |
|  ↳ `y_tensor_check_max` | 0.7 / 0.3 | 0.4 | 0.3 | – | – | 1.0 | 0.3 |
| `extract_d2h` (kpts/boxes `.cpu()`) | 0.28 | 0.25 | 0.26 | – | – | 0.26 | 0.27 |
| **`mw_total`** (motion worker, CPU) | **74.8 / 109** | 75.7 | **95.3 / 118** | 174.5 | 174.5 | 62.7 | 128.6 |
| `motion_wait` (main thread blocked on worker) | 38.6 / 75.7 | 42.4 | 67.0 / 89.0 | 141 | 150 | 0.0 | 6.7 |
| `post_yolo` (crossval → tracker → OSC) | 10.8 / 15.2 | 10.4 | 7.7 / 9.6 | 20.4 | 14.3 | 8.3 | 15.1 |
|  ↳ `pc_tracker_update` | 6.2 / 9.4 | 5.9 | 2.2 / 2.4 | 11.4 | 4.9 | 4.4 | 8.1 |
|  ↳ `pc_osc_send` (1 dancer) | 0.8 | 0.8 | 0.8 | – | – | 0.75 | 0.8 |
| **`process_wall`** p50 / p95 | 129.7/194.5 · 177.8/288.1 | 131.3/204.2 | 152.1/248.9 · 188.2/324.5 | 275.8/539.9 | 266.0/502.2 | 166.3/196.2 | 205.8/400.9 |
| load avg at start / GPU util mean | 15 / 73 % · 21 / 45 % | 15 / 75 % | 19 / 44 % · 19 / 41 % | 32 / 23 % | 35 / 25 % | 15 / 92 % | 13 / 63 % |

**How to read this table:**
- **The `a`/`b` repeats show the noise.** The same config at load 15 vs 21 moved `process_wall` p50 by 37 %.
  Almost all of that is the CPU motion worker.
- **The l@* rows ran at load 32–35.** Their CPU columns are not comparable. Use §2.3 for l-vs-x.
- **On this box the motion worker is the critical path in every TRT config.** Main-thread `motion_wait` is
  39–150 ms. With PyTorch, YOLO (~120 ms) hides the worker instead (`motion_wait` ≈ 0).
- **Sync vs natural ≈ equal for TRT.** Ultralytics already device-syncs 6× per call, so there is almost no CPU/GPU overlap left to lose.
- **Extra configs** (`runs/*.json`):
  - `trt_x1280_live` (preview at the 10 fps cap + OSC): preview download p50 2.8 ms (ROI-sized 4.6 MB D2H).
  - `trt_x1280_serialmotion` (motion run inline): no measurable GIL penalty from the parallel worker beyond the noise.
  - `trt_x1280_cv8` (cv2 threads = 8): no gain *at load 25* (no idle cores). See §2.4 for the min-based gain.
- **The tracker JSONL logger flush runs every 5 s on the main thread:** max 23 ms (`tracking_logger.py:46,165`).

### 2.3 [MEAS] Isolated inference (`perf/engine_bench.py`, 100 reps, input 1×3×S×S fp32 on GPU)

| Config | **GPU fwd p50 / min** (CUDA events) | Full ultralytics `model(tensor)` call **min** / p50 | PyTorch eager fwd FP32 / FP16 **min** |
|---|---|---|---|
| yolo11x-pose @960 | **9.11 / 7.80** | 12.85 / 21.5 | 33.5 / 34.9 |
| yolo11x-pose @1280 | **12.48 / 11.57** | 15.76 / 27.1 | 52.3 / 43.9 |
| yolo11l-pose @960 | **4.51 / 4.39** | 7.68 / 9.5 | 33.9 / 34.6 |
| yolo11l-pose @1280 | **7.90 / 7.09** | 11.86 / 22.6 | 33.6 / 34.1 |

- **Ultralytics adds ≈ 4–5 ms (min) on top of the GPU forward** on this CPU (pre/post, NMS, syncs, `orig_img` D2H).
- **Eager PyTorch is launch-bound on this CPU.** FP32 = FP16, and l = x at 960. Its ~33 ms floor is Python/kernel-launch cost.
- **TRT is 3.7–7× faster than eager** at the same model and size.
- **Model scaling:**
  - x@1280 / x@960 = 1.37× in GPU time, less than the 1.78× pixel ratio (fixed overheads).
  - l@1280 (7.9 ms) is cheaper than x@960 (9.1 ms).

### 2.4 [MEAS] CPU micro-benchmarks (`perf/cpu_micro.py --reps 60`, real frames, **min ms**; `cpu_micro_run2.json`)

**Motion feed decomposition, ROI 1296×1195, low-light branch, `mog2_scale` 0.7:**

| Op (code) | 1 thread (app today) | 8 threads | Note |
|---|---|---|---|
| γ LUT (`pipeline.py:1098`) | 1.25 | 0.88 | |
| `np.mean` brightness (`motion_detector.py:205`) | 1.34 | 1.14 | |
| medianBlur 5 @ full res (`:207`) | 9.39 | 9.06 | median5 is not parallelised |
| GaussianBlur 5 @ full res (`:208`) | 2.60 | 1.47 | |
| resize INTER_AREA ×0.7 (`:212-213`) | **9.15** | 3.88 | non-integer AREA is the slow path |
| MOG2 apply @0.7, shadows on (`:276`) | **20.23** | **8.86** | largest single op |
| clean mask `==255` + open3 (`:280-281`) | 0.76 | 0.82 | |
| Welford noise σ (`motion_model.py:105,243`) | 3.07 | 2.94 | feeds only `calib2` (`calibration_flows.py:557`) |
| frame-diff pair: `diff_scale`=min(1, 2×0.7)=1.0 → full-res `absdiff().max()` (`motion_detector.py:83,253`) | 0.24 | 0.24 | P-3 mainly helps the per-bbox CC queries |
| **Whole `MotionModel.feed`** | **49.1** | **34.1** | bit-identical outputs (verified) |
| P-4: median5 + gauss5 *after* ×0.7 | 5.89 (vs 12.0) | 5.33 | ≈ −6 ms; changes the signal → re-baseline |

**`mog2_scale` sweep, single thread, min:** 0.5 → 26.9 ms (integer AREA fast path), 0.7 → 50.6 ms, 0.99 → 80.4 ms.

**Other per-frame CPU work (min, 1 thread):**
- **IDS path:**
  - Mono10g40 unpack (`ids_camera.py:1189-1193`, a numpy strided gather): 6.27 ms @1528² and 11.15 ms @4 MP.
  - `_cached_cpu_bgr` GRAY→BGR (`:1295`): 0.77 ms.
  - Main loop `raw_frame[...,0]` contiguous (`pipeline.py:573`): 1.64 ms (ROI).
  - Full-frame BGR `.copy()` (`main_loop.py:626-628, 787`; `video_recorder.py:324`): 0.64 ms each.
- **Preview** (main thread, ≤10 fps):
  - `_compose_roi_preview` canvas + `cv2.resize` (`roi_mask_editor.py:312-329`, `main_loop.py:724-736`): 6.83 ms.
  - `gui.update_frame` BGR→RGBA→float32, 1017×1044 (`gui.py:1451-1458`): 3.89 ms.
  - `dpg.set_value`: 0.003 ms (zero-copy; the texture upload happens inside `render_dearpygui_frame`, which I could not measure without a viewport).
  - `draw_dancer`: 0.34 ms per dancer.
  - Web monitor `update_frame` copy: 0.25 ms. JPEG q70: 5.6 ms (HTTP thread, only with a phone connected).
- **OSC `send_frame`:** 0.54 ms for 1 dancer, 2.23 ms for 4 (python-osc builds 1+4n datagrams).
- **Recording encoder thread, per frame at 1488×1528:**
  - FFV1 BGR (current, `config.py:597`): **144.7 ms**
  - FFV1 mono: **61.4 ms**
  - MJPG BGR: 31.5 ms

  Mono FFV1 decodes byte-identical to the BGR source. File size is about the same (0.74 vs 0.77 MB/frame), because
  FFV1's channel decorrelation already removes the R=G=B redundancy, so the gain is CPU only.
- **FFV1 decode** (playback/replay): 6.5 ms min (multi-threaded FFmpeg).

### 2.5 [MEAS] GPU micro (`perf/gpu_micro.py`, p50 under load ≈ 32 — indicative)

- **kornia enhance** (`gpu_pipeline.py:359-403`), profiled with `perf/enhance_prof.py`:
  - **218 kernel launches/frame**, including **64 `histc` calls** (a per-tile Python loop inside kornia).
  - **~7.2 `cudaStreamSynchronize`/frame**.
  - **GPU kernel time 1.85 ms**, against a wall time of 13.5 ms (min) / 40 ms (p50).
  - A mono-only variant (CLAHE on 1 channel, no greyscale expand or YCbCr round-trip) is ~21 % less wall time.
- **Ultralytics `convert_torch2numpy_batch`** (the unused `orig_img`): 1.7 ms @960, 6.8 ms @1280 (p50). This is
  4.9 MB of pageable D2H plus permute, mul and byte work on the GPU. `im.max()` check: 0.13 ms.
- **GPU "motion-lite"** (running-average background + frame diff + 3×3 open, torch): 0.44 ms @1296×1195, 0.70 ms @4 MP.
- **Marker stage:** see §8.

---

## 3. Sync points & CPU hot spots (file:line)

**Host↔device sync sites per frame** (`perf/sync_sites.py`, PyTorch and TRT, x@1280, 20 frames; the profiler cross-check counted 6.2 `cudaDeviceSynchronize` + 12.7 `cudaStreamSynchronize` + 19.7 `cudaMemcpyAsync` per frame):

| # / frame | Site | What | Avoidable? |
|---|---|---|---|
| 6.0 | `ultralytics/utils/ops.py:74` (`Profile.__enter__/__exit__` ×3) | **device-wide** `torch.cuda.synchronize()`. It also waits on the IDS upload stream. | Yes: call `AutoBackend` + NMS directly, or patch `Profile` |
| 1.0 | `ultralytics/data/loaders.py:669` (`LoadTensor._single_check`) | `im.max() > 1` → full reduction + `__bool__` sync **before** inference | Yes |
| 1.0 | `ultralytics/models/yolo/detect/predict.py:69` → `ops.py:720` | **4.9 MB D2H of the whole letterboxed input** (`orig_img`, never used by WallDance) | Yes (biggest PCIe transfer per frame) |
| ≈0.45 ×3 | `core/pipeline.py:1430, 1435, 1436` | keypoints / boxes / conf `.cpu()` (only when there are detections) | Merge into 1 D2H (tiny) |
| 0.1 | `core/gpu_pipeline.py:348` | brightness `.mean().item()`, decimated 1/10 | Fine |
| ≈7.2 | inside kornia `equalize_clahe` (`gpu_pipeline.py:390`) | per-tile histc/index syncs | Yes (batched CLAHE) |
| IDS only | `camera/ids_camera.py:1336` | `upload_stream.synchronize()` after a 2.3 MB H2D | Keep (correctness), or use event-wait |
| preview ≤10 fps | `core/gpu_pipeline.py:178` | ROI-sized uint8 D2H: 4.6 MB at ROI res, because `preview_target` = ROI size when ROI is on (`:613-617`, `:718-722`), then a CPU compose + downscale | Resize on GPU to the texture size first (~0.5–1 MB) |

**CPU hot spots on the per-frame critical path:**
- **Motion feed** (`pipeline.py:1034-1049` → `motion_model.py:101-106` → `motion_detector.py:193-281`). Single
  threaded because of `ultralytics/utils/__init__.py:176`. Cost: median5 + gauss5 at full ROI res, a slow
  non-integer INTER_AREA, MOG2 with shadow detection, and Welford σ every frame. Measured in §2.4.
- **kornia CLAHE** (`gpu_pipeline.py:376-403`). It also round-trips a 3-channel float32 tensor
  (`greyscale` → `expand` at `:417`, then `rgb_to_ycbcr`, `clone` at `:400`, `ycbcr_to_rgb`) for a mono camera.
- **Ultralytics postprocess:** ~10 ms (box) of NMS + `Results` construction for ≤ a few people.
- **Tracker** (`tracker.py:2187` `update`; per detection×track Python loop in `_compute_cost_matrix` `:1252-1300`;
  per-track motion queries with `connectedComponentsWithStats` in `motion_detector.py:490-560`, `:399`). With
  cProfile (`runs/pt32_x1280_profile.prof`), the post-YOLO chain is about 9 ms/frame on the box with 1 dancer:
  - `tracker.update`: 52 %
  - `frame_diff_blob_in_bbox` / CC: 18 %
  - python-osc message building: 17 %

  Prod is 2.5 ms (p95 6). **[EST]** It should grow ≈ +1–1.5 ms per extra dancer on prod. I could not measure
  multi-dancer scenarios here: the white-walkers, white-duo and texture-duo recordings are not on this box.
- **IDS acquisition thread** (`ids_camera.py:1073-1124`): copy + strided unpack (6.3 ms box) + per-frame
  `cvtColor(GRAY2BGR)` for the recorder callback (`:1120`). Then the main thread does another GRAY2BGR for
  `_cached_cpu_bgr` (`:1295`) and a channel-0 extraction back to mono (`pipeline.py:573`). That is three conversions
  of the same mono pixels each frame.
- **`_last_review_frame = display_frame.copy()`** every processed frame (`main_loop.py:626`), plus a second copy per preview (`:787`).
- **GIL:** the worker's heavy work is cv2/numpy, which release the GIL. The serial-vs-parallel run showed no GIL
  penalty above the noise. On this box the contention is for **cores**, not the GIL. On prod the real issue is
  that the worker uses one core because of the cv2 thread setting.
- **Per-frame stderr:** `half=` passed on every call (`pipeline.py:587`) triggers `ultralytics/cfg/__init__.py:617-622`
  → `LOGGER.warning` **once per frame** (300 lines per 300-frame replay, `runs/replay_trt11_x1280.log`). This only
  happens with ultralytics 8.4.x. On Windows a console write can block (QuickEdit selection freezes the process).

---

## 4. Latency estimate (camera glass → OSC send), IDS live path, L = 1

| Term | p50 | p95 | Basis |
|---|---|---|---|
| Mid-exposure → end of exposure (E/2, E = 49.3 ms pinned) | 24.7 | 24.7 | [CODE] scenario `ids_exposure_us` |
| Sensor readout + USB3 transfer (2.92 MB Mono10g40 at ~350 MB/s) | ~10 | ~15 | [EST] |
| Acquisition thread: copy + unpack + publish newest (`ids_camera.py:1081-1115`) | ~2.5 | ~4 | [MEAS] 6.3 ms box → ÷2.5 prod |
| Main-loop pickup: polling with `render_frame()` + `sleep(0.01)` when no frame (`main_loop.py:511-515`); the loop phase-locks to the camera because busy ≈ 40 < 50.7 ms | ~7 | ~15 | [EST] |
| `read_gpu`: pinned copy, H2D, stream sync, float, 3-channel expand, cached BGR (`ids_camera.py:1260-1346`) | ~2.5 | ~4 | [EST] |
| `input_fps_cap` sleep (`main_loop.py:522-530`, 50 ms cap vs 50.7 ms camera period) | ~0–1 | ~10 | [CODE]; up to 50 ms if the camera runs faster than 20 fps |
| `process_gpu_direct` until `osc.send_frame` (`pipeline.py:870`, the end of the chain) | **31.6** | **35.8** | [PROD] `process_wall` |
| UDP send (localhost) | <0.5 | <1 | [MEAS] 0.54 ms box |
| **Glass → OSC (keypoints, box position)** | **≈ 79** | **≈ 110** | |
| + centroid EMA α = 0.5 (`config.py:278`, `tracker.py:495-497`): ≈1 frame group delay on a ramp | +50 | +50 | [CODE] |
| + box-size EMA α = 0.5 at L=1 (`config.py:284`, `pipeline.py:931-965`) | +~1 frame on size only | | [CODE] |
| + L > 1 fixed-lag RTS (`pipeline.py:871-874`) | +L × 50.7 | | [CODE] |

**Not WallDance's latency, but part of the show's total:** TouchDesigner's 60 Hz frame pacing (0–16 ms) and the projector/display latency.

**Levers, with the achievable target:**
- Exposure: ≤25 ms, as the markers stream also wants for streaks → −12 ms.
- Motion off the critical path (PERF-2) → −7 ms.
- Ultralytics overheads (PERF-3) → −2…−3 ms.
- CLAHE (PERF-4) → −3 ms.
- Event-driven frame pickup instead of `sleep(0.01)` polling → −5 ms mean.
- Centroid output from the KF estimate (`centroid_raw`) or an α-β filter instead of a plain EMA (PERF-9) → −50 ms on `/centroid`.

Target: **glass→OSC ≈ 50–55 ms p50 and effective centroid ≈ 55–60 ms**, versus ~79 / ~130 ms today.

---

## 5. Projection to the RTX 5080 laptop

**Ratios (anchor = §2.1 prod log vs the box):**
- **GPU forward ≈ 1:1.** Prod `yolo` x@960 (whole call, TRT10) = 11.6 ms. That is the same as the box's best whole
  call (12.85 ms) with a box GPU forward of 7.8–9.1 ms, which suggests ~7–8 ms of GPU work + ~3–4 ms of ultralytics
  overhead on prod. A power-limited GB203 laptop part running TRT FP16 is roughly at parity with the 3090.
- **CPU ≈ 2.5× faster per thread on prod, with AVX2.** Box motion-feed min is 49 ms; prod logs 19.3 ms (which also
  includes the overlap). Box tracker ≈ 6 ms vs prod 2.5 ms. Box enhance (launch-bound) 13.5 ms min vs prod 4.6 ms.

**Projected prod p50, ms, playback/IDS, 1 dancer, scene as hangar-aerial:**

| Config | YOLO call | Motion feed | Critical path max(YOLO+extract, motion) | `process_wall` | Headroom vs 50.7 ms frame |
|---|---|---|---|---|---|
| TRT x@960 (today) | 11.6 [PROD] | 19.3 [PROD] | motion | **31.6 [PROD]** | ~19 ms minus the GUI tail |
| TRT x@1280 | ~15–16 | 19.3 | motion | ~32–34 | ~17 |
| TRT l@1280 | ~10.5 | 19.3 | motion | ~31 | ~19 |
| TRT l@960 | ~7 | 19.3 | motion | ~31 | ~19 |
| TRT x@1280, `mog2_scale` 0.99 | ~15–16 | ~31 | motion | **~43–45** | ~6 → fps dips likely with preview on |
| TRT x@1280 + PERF-1 (cv2 threads) | ~15–16 | ~12–13 | YOLO | ~28–30 | ~21 |
| + PERF-3 + PERF-4 | ~13 | ~12 | YOLO | ~22–24 | ~27 |
| **PyTorch fallback** x@1280 FP16 (TRT build failed, §9) | ~30–40 | 19.3 | YOLO | **~45–55** | ≤0 → 15–19 fps + spikes |

**Laptop power behaviour:**
- Laptop GPUs ignore `extra/gpu_limiter.*`. That script is a 280 W desktop-PSU tool, and `nvidia-smi -pl` is
  usually blocked on mobile parts.
- **Dynamic Boost shares one package budget between the CPU and GPU.** The prod bottleneck is CPU (single-threaded
  motion + Python), so a CPU-heavy frame can steal GPU clocks and vice versa.
- **Moving work off the CPU or spreading it** (PERF-1/2) helps both.
- The 4 h soak (`docs/TODO.md:33`, mean 19.13 fps, min 18.33) and the 30-min "thermal" fps-trend fail fit a
  source-bound loop with ~15 ms of slack. **Losing that slack** (`mog2_scale` 0.99, more dancers, PyTorch fallback,
  hot room) is where 20 fps breaks first.
- Capture throttle reasons on prod (see the Questions section).

**IDS stall coupling [HYP]:** `docs/archives/IDS_STALL_CONCLUSIONS.md` §2.2 ties the ~1.7 s USB3 stalls to PCIe DMA.
Per frame the app moves this much over PCIe:
- H2D: 2.3 MB (mono)
- D2H: **4.9 MB (ultralytics `orig_img`, useless)**
- Preview D2H: 4.6 MB at ≤10 fps (ROI-res)

Removing the two avoidable D2H transfers cuts PCIe traffic by ~2/3. This is cheap to A/B with
`docs/archives/IDS_STALL_CONCLUSIONS.md`'s isolation harness.

---

## 6. Ranked optimizations

**Gains are for prod unless marked. "Golden" = impact on `tests/golden` replay summaries.**

| ID | Change | Expected gain | Effort | Risk | Golden |
|---|---|---|---|---|---|
| **PERF-0** | **Fix the TRT 11 engine path.** Pick one of: (a) add `nvidia-modelopt[onnx]>=0.44` to the lock; (b) pin `tensorrt<11` / the ultralytics version the June engines were built with; (c) ship the ONNX→FP16 workaround (§9) in `model_manager`/`build_engines`. Make "engine present but undeserializable" and "export failed" a **loud red alert and a show-blocker**, not a silent PyTorch fallback (`model_manager.py` load/export `except` paths). | Avoids a 3–7× inference regression (≥ +20–30 ms/frame) | S | Low | TRT goldens are per-engine anyway: re-baseline on the rebuilt engine (hangar-aerial: `gate_rejections` 79→77, `zero_detection_frames` 16→15, still PASS A) |
| **PERF-1** | `cv2.setNumThreads(4–8)` right after `from ultralytics import YOLO` (`pipeline.py:16`) and in any other ultralytics-importing entry point | Motion feed −30 % (box min 49→34): prod ≈ 19→12–13 ms, **process_wall ≈ −6…−7 ms**, latency −7 ms | XS | Low (oversubscription vs torch threads: cap at 4) | **None.** Bit-identical masks, diff and σ verified |
| **PERF-2** | Motion feed cost: (a) P-4 blur after downscale (−6 ms box); (b) ×0.5 INTER_AREA fast path or INTER_LINEAR instead of ×0.7 AREA (−8 ms box); (c) P-3 frame-diff cap (cheap diff, cheaper per-bbox CC); (d) decimate or gate the Welford σ (−3 ms box) — `calib2` reads it, so decide its semantics; (e) longer term, a GPU motion-lite model (0.44 ms measured) | Prod motion → ~6–9 ms combined with PERF-1. YOLO becomes the critical path at every model choice | S–M | Med (signal changes) | (a)(b)(c)(e) **re-baseline**; (d) none for replay |
| **PERF-3** | Bypass ultralytics per-call overhead: call `model.predictor.model` (AutoBackend, CUDA graph) + `ops.non_max_suppression` directly, or patch `convert_torch2numpy_batch`/`Profile`/`_single_check`. Drop the per-call `half=` (it triggers the per-frame deprecation warning) | −2…−4 ms/frame prod; −6 device syncs; −4.9 MB D2H per frame (stall hypothesis); no per-frame stderr | S | Low–Med (track ultralytics API drift) | None if NMS args are identical (verify byte-equality on the TRT golden) |
| **PERF-4** | CLAHE: batched-histogram CLAHE (one `scatter_add`/`bincount` over the 64 tiles), or cache the LUTs every N frames. Run it on the 1-channel mono tensor and expand to 3 channels only in the letterbox (`gpu_pipeline.py:376-417`) | −2…−3 ms prod (1.85 ms GPU vs 4.6 ms wall); 7 fewer syncs | M | Med | Re-baseline unless bit-exact with kornia |
| **PERF-5** | Latency plumbing: event-driven frame wait (a `Condition` set by the acquisition thread) instead of `sleep(0.01)` polling (`main_loop.py:511-515`); `input_fps_cap` via camera `AcquisitionFrameRate` rather than a sleep after acquisition (`:522-530`) | −5 ms mean / −10 ms p95 latency | S | Low | None |
| **PERF-6** | Mono recording: `VideoWriter(..., isColor=False)` + write the mono frame (skip GRAY2BGR at `ids_camera.py:1120` and the copy at `video_recorder.py:324`) | Encoder CPU −58 % (145→61 ms box), −6.8 MB/frame of copies, less GIL/CPU pressure while recording | S | Low (decode is byte-identical, verified) | None (new recordings only) |
| **PERF-7** | IDS path: pass the mono frame straight to motion (no GRAY2BGR + channel-0 round-trip, `ids_camera.py:1295` / `pipeline.py:573`); upload mono and crop ROI **before** float conversion; no 3-channel float32 full frame (`ids_camera.py:1341-1346`); unpack Mono10g40 on the GPU or with `np.lib.stride_tricks` | −3…−4 ms CPU/frame prod, −28 MB GPU writes/frame | S–M | Low | None (IDS path is not in replay) |
| **PERF-8** | Preview: resize on the GPU to the texture size (incl. ROI canvas compose) before D2H; drop the per-frame `_last_review_frame` copy (copy lazily on demand, `main_loop.py:626-628`) | −2…−4 ms per preview frame, −4 MB D2H per preview | S | Low | None |
| **PERF-9** | Centroid output: emit the KF/α-β estimate (`centroid_raw`, `pipeline.py:1381-1385`) or a velocity-compensated EMA on `/centroid` at L=1 | **−~50 ms effective centroid latency** | S | Med (jitter trade-off; needs the operator's eye) | None (output-only) |
| **PERF-10** | Python 3.12 venv (P-8) | ~10–20 % on Python-bound parts (postprocess, tracker, OSC): −1…−2 ms | M | Med (wheels) | Should be identical; verify |
| **PERF-11** | Move the tracker JSONL `flush()` (5 s cadence, `tracking_logger.py:165`) to a writer thread; fix `[Budget]` so `dpg_render`/`gui_stats` are reported; use `perf_counter` instead of `time.time` (`pipeline.py:466,511,563,581,603,813`) | Removes a 5 s periodic spike (≤23 ms box); makes prod logs useful | XS | Low | None |
| **PERF-12** | Async pipelining: GPU stage for t+1 overlapped with the tracker/preview of t | Throughput only; latency is unchanged or +1 stage. Not needed at 20 fps with ≥15 ms slack | L | High (ordering, goldens) | Re-baseline if any order changes |
| **PERF-13** | TRT INT8 / FP8 (Blackwell) via modelopt with IR-footage calibration | GPU fwd ×1.3–1.6. Pointless while CPU is the critical path | M | **High** (dark-IR keypoint confidence shifts → known-N thresholds/seeds) | Re-baseline + re-run known-N calibration |
| **PERF-14** | Model choice x@1280 ↔ l@1280 ↔ x@960 | All ≤ the motion feed on prod (§5). **Choose on accuracy (Phase 2b), not fps.** l@1280 (7.9 ms GPU) < x@960 (9.1 ms) | – | – | Re-baseline per choice |

**Not worth it now:**
- CUDA graphs for TRT: ultralytics 8.4.173 already captures fixed-shape TRT engines into a CUDA graph (`nn/backends/tensorrt.py:111-125`).
- Pinned memory / `non_blocking`: already used (`gpu_pipeline.py:757-763`, `ids_camera.py:1317-1338`).
- NVDEC for playback: FFV1 isn't NVDEC-decodable, and FFV1 decode is 6.5 ms min multi-threaded.
- numba for the tracker: 2.5 ms on prod.

---

## 7. Live-app costs not in replay (prod, amortised per processed frame)

| Item | Where | Cost | Notes |
|---|---|---|---|
| Preview GPU resize + D2H (ROI-res) | main, ≤10 fps | 1.0 ms [PROD] | 4.6 MB D2H. PERF-8 |
| ROI compose + resize + draw + overlays | main, ≤10 fps | ~3–4 ms [EST from 6.8 + 0.3n box] | `preview_draw` 1.0 [PROD] excludes compose |
| GUI `update_frame` (RGBA float32, 17 MB buffer) | main, ≤10 fps | 3.0 ms [PROD `preview_upload`] | Plus the GPU texture upload inside `render_dearpygui_frame`: **unmeasured** |
| `render_dearpygui_frame` | main, every tick | unknown | Never logged (§2.1). Ask for it |
| `StatsTick`, ops heartbeat, recording UI every 10 frames | main | <1 ms | |
| `_last_review_frame.copy()` ×1–2 | main, every frame | ~0.3–0.5 ms | PERF-8 |
| Recording (FFV1 BGR) | acquisition thread (GRAY2BGR + copy) + encoder thread | encoder ~50–60 ms/frame on a prod core [EST] | Close to 1 core at 20 fps. Mono → ~25 ms. Queue maxsize 300 × 6.8 MB = **2 GB** worst-case RAM if the encoder falls behind (`video_recorder.py:78`) |
| Web monitor | HTTP threads, only when a phone is connected, ≤15 fps | JPEG q70 ~2–3 ms + Laplacian/overlay | GIL-light (cv2) |

Amortised over the 20 fps loop, the GUI/preview tail is ~3–5 ms/frame plus the unknown DPG render. This tail plus `process_wall` (31.6 ms) sets the real loop period. It must stay under 50.7 ms.

---

## 8. IR-marker stage cost

**Measured on this box** (`cpu_micro_run2.json`, `runs/gpu_micro_1.json`; frame = real IDS gray or 4.1 MP synthetic with 12 saturated 5×5 blobs):

| Variant | 1528×1528 (today's IDS crop) | 2560×1600 (≈ full 4.1 MP sensor) |
|---|---|---|
| CPU `threshold` + `connectedComponentsWithStats`, full frame, 1 thread (min) | 14.5 ms | 26.8 ms |
| same, 8 threads (min) | 2.7 ms | 7.9 ms |
| CPU on ½-res (min, 1 / 8 threads) | 4.0 / 1.5 ms | 6.9 / 1.8 ms |
| CPU `threshold` + `findNonZero` (sparse seeds; then CC on tiny windows) (min) | **2.1 ms** | **3.6 ms** |
| GPU torch: threshold + 7×7 max-pool NMS + `nonzero` + D2H ≤256 seeds (p50, contended) | **0.77 ms** | **1.0 ms** |
| GPU threshold + full-mask D2H (p50) | 2.1 ms | 5.6 ms |
| kornia `connected_components` (iterative) | 31 ms | 18 ms → not viable |

**Prod projection [EST, CPU ÷2.5]:**
- CPU sparse: ≈ 0.8–1.5 ms. Full-frame CC single-thread: ≈ 6–11 ms.
- GPU NMS: ≈ 0.5–1 ms. It adds one `nonzero` sync, which is cheap if placed right after the existing ultralytics syncs.

**Placement advice:**
- **Do not put it on the motion worker until PERF-1/2 land.** That worker *is* the prod critical path (19.3 > 11.6 ms), so every marker ms becomes a latency ms.
- **Best CPU home:** the IDS acquisition thread. It already holds the mono8 frame (`ids_camera.py:1086-1115`),
  runs off the main thread one frame ahead, and has no D2H.
- **Best GPU home:** right after `_mono_to_gpu_bgr` on the raw mono tensor, *before* the float/3-channel expand.

---

## 9. Engine / TensorRT-version findings

1. **The June engines are dead under the rebuilt venv.** `YOLO('models/yolo11x-pose_1280.engine')` →
   `Serialization assertion stdVersionRead == kSERIALIZATION_VERSION failed ... Current Version: 244, Serialized Engine Version: 243`.
   In the app that falls through `ModelManager.load_model`'s `except` → PyTorch, with only `_tensorrt_fallback_reason` set.
2. **The app's own rebuild fails under TRT 11.** Two paths do `model.export(format='engine', half=True, device=0)`
   (`model_manager.py` export path, `extra/build_engines.sh:112-115`). With TensorRT 11.3 (strongly typed, no FP16
   builder flag), ultralytics 8.4.173 routes FP16 through `modelopt_quantize_onnx` → `check_requirements("nvidia-modelopt[onnx]>=0.44")`
   → **`ModuleNotFoundError: No module named 'modelopt'`** (`engines/build.log`, first attempt). `uv.lock` pins
   `tensorrt 11.3.0.99` and `ultralytics 8.4.173` and does not include modelopt. **Any machine re-synced from the
   current lock — including the show laptop — will silently run PyTorch.**
3. **Scratch-only workaround used here** (`perf/build_engines.py`):
   - I monkeypatched `ultralytics.utils.export.engine.modelopt_quantize_onnx` → `onnxruntime.transformers.float16.convert_float_to_float16(model, keep_io_types=True)`.
     `onnxruntime-gpu` 1.23.2 is already in the venv, and FP32 I/O matches AutoCast's `keep_io_types=True` contract.
   - I exported from a scratch copy of the `.pt`, then moved the engine into `models/<base>_<N>.engine`.
   - **Build times** (on an i7-3770K under load 14–35; TRT 11 "compiler backend"): x@1280 517 s, x@960 553 s, l@1280 545 s, l@960 579 s.

   Engine sizes grew a lot:

   | Engine | TRT 10 (June) | TRT 11 (rebuilt) |
   |---|---|---|
   | x@1280 | 297 MB | 978 MB (933 MiB on load; +215 MiB context) |
   | x@960 | – | 604 MB |
   | l@1280 | 132 MB | 638 MB |
   | l@960 | – | 385 MB |

   This conversion is not identical to modelopt AutoCast (AutoCast keeps outlier-range nodes in FP32), so treat
   accuracy as "close", not "same".
4. **Golden impact.** `tests/replay.py --scenario hangar-aerial --trt` on the rebuilt x@1280 engine:
   - `gate_rejections` 79→77, `zero_detection_frames` 16→15. Everything else is equal to `tests/golden/hangar-aerial.json`.
   - **PASS class A** (drop 4.2 %, ghost 0, longest drop 0.61 s).

   TRT goldens are therefore **engine-specific**: any TRT version, conversion tool or GPU change re-baselines them.
   Track the engine hash next to the golden.
5. **Per-rig fps table is missing.** `models/fps_table.json` does not exist on this box, so calib2's FPS-budget cap
   and model advisory run without data. `extra/measure_engine_fps.py` also measures with a uint8 numpy input
   (letterbox + H2D), which is not the app's tensor path. Re-measure on prod with the rebuilt engines.
   `perf/engine_bench.py` shows the tensor-path method.
6. **Already present:** the ultralytics TRT backend captures fixed-shape engines into a CUDA graph (`nn/backends/tensorrt.py:111-125`), so "CUDA graphs" is already done for TRT.

---

## 10. Questions for Thomas

1. **What venv is on the show laptop now** (`tensorrt`, `ultralytics`, `torch` versions)? Do its engines still
   load? A one-liner checks it: `YOLO('models/yolo11x-pose_1280.engine')(np.zeros((1280,1280,3),np.uint8))`. If it
   was re-synced since June, it is on PyTorch today. Are you OK with adding `nvidia-modelopt[onnx]` to the lock, or
   would you rather pin TRT 10?
2. **Laptop CPU model, and the Windows power mode / plugged-in state during shows** (Dynamic Boost behaviour)?
3. **Capture from a real IDS show run** (5–10 min, with the projectors and TouchDesigner running on the same laptop if that is the setup):
   - the console `[Budget]`, `[PerfSpike]` and `[IDSCamera] USB3 stall` lines
   - `nvidia-smi --query-gpu=timestamp,utilization.gpu,clocks.sm,power.draw,temperature.gpu,clocks_throttle_reasons.active --format=csv -lms 500`
   - `typeperf "\Processor(_Total)\% Processor Time"` (or Task Manager per-core)

   The `[Budget]` line never shows `dpg_render` today (a bug, §2.1). Could you run once with a print of `_dpg_render_ms` (`main_loop.py:922`)?
4. **How much glass→OSC latency is acceptable artistically?** Is the 1-frame centroid EMA lag (~50 ms) intended, or should `/centroid` be predictive?
5. **Is `mog2_scale` 0.99 still used in any live project?** It costs ~+12 ms/frame on prod (§2.4) for the same frame-diff signal.
6. **Is TouchDesigner on the same laptop?** It competes for the same CPU/GPU power budget and for PCIe.
7. **Exposure:** can the show run at ≤25 ms with more IR (markers stream §1.5)? That is −12 ms latency plus less motion blur, for free.
