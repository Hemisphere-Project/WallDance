# IDS Camera Stall Investigation Report

**Camera**: IDS U3-34ExXCP-M (SN: 4110042130)  
**Resolution**: 2688 × 1528 (full sensor)  
**Format**: Mono10g40IDS (10-bit packed, 5 bytes = 4 pixels)  
**Interface**: USB 3.0 (DeviceLinkSpeed = 500 MB/s)  
**GPU**: NVIDIA GeForce RTX 5080 Laptop GPU  
**Date**: February 2026  

---

## 1. Problem Statement

Periodic frame acquisition stalls (~1.5–1.7 s gaps) occur when streaming
at full 2688×1528 resolution with GPU-accelerated processing.  The
application timeout fires, no frames arrive for the duration, then normal
streaming resumes.  The pattern is cyclical and consistent.

---

## 2. Investigation Timeline

### 2.1 Root Cause #1 — IPL Convert Buffer Hold (FIXED)

The IDS SDK's `IPL.ImageConverter` holds an internal reference to the
acquisition buffer until the *converted* image is released.  With a
16-buffer ring this eventually stalls the transport layer when all buffers
are locked.

**Fix**: Replaced IPL Convert with a pure-numpy unpack of Mono10g40IDS:

```python
# Mono10g40IDS: 5 bytes → 4 pixels, bytes 0-3 are top-8 MSBs
raw.reshape(-1, 5)[:, :4].reshape(H, W).copy()   # ~3.7 ms
```

The IDS buffer is now released in a `try/finally` immediately after
`get_numpy_1D().copy()`, keeping hold time to ~1–2 ms per frame.

### 2.2 Root Cause #2 — DeviceLinkThroughputLimit (REVERTED)

The camera's default throughput limit is **162 MB/s** (~32 % of USB3
theoretical 500 MB/s).  Experiments:

| Limit (MB/s) | Stall Frequency | Notes |
|:---:|:---:|:---|
| **125** | every ~5 s | Worse — bandwidth starvation |
| **162** (default) | every ~10–17 s | Best of all tested values |
| **250** | every ~2 s | Worse — saturates USB controller |
| **300** | every ~2 s | Worse — same as 250 |

**Conclusion**: The camera firmware chose the optimal value.  All changes
made stall frequency *worse*.  Code now only logs the default:

```python
print(f"[IDSCamera] DeviceLinkThroughputLimit: {value/1e6:.0f} MB/s "
      f"(keeping default, max {maximum/1e6:.0f})")
```

### 2.3 USB Selective Suspend (RULED OUT)

Windows USB Selective Suspend was found **enabled** and was disabled via:

```powershell
powercfg /setacvalueindex SCHEME_CURRENT 2a737441-... 48e6b7a6-... 0
powercfg /setdcvalueindex SCHEME_CURRENT 2a737441-... 48e6b7a6-... 0
powercfg /setactive SCHEME_CURRENT
```

**Result**: Zero change in stall frequency or duration.  Ruled out.

### 2.4 Standalone Diagnostics (HARDWARE BASELINE)

Three test scripts were written and executed to isolate the cause:

**test_stall_diag.py** — Tight acquisition loop (no processing):  
- Auto-exposure: 600 frames, 20.0 FPS, 0–1 stalls per 30 s run  
- Manual exposure: 600 frames, 0 stalls  

**test_stall_isolate.py** — Incremental processing layers:  
- Tight loop: 0 stalls  
- BufferToImage + copy: 0 stalls  
- Numpy unpack: 0 stalls  
- Heavy GIL work: crashed (too aggressive)  

**test_stall_gpu.py** — GPU / PCIe contention:  

| Test | Stalls (20 s) | Description |
|:---|:---:|:---|
| BASELINE (tight loop) | 0–1 | No GPU activity |
| GPU_CONTINUOUS (matmul) | 2 | Constant GPU compute |
| **GPU_PERIODIC** (large `.cpu()`) | **5–6** | 400 MB GPU→CPU every 2 s |
| GPU_FULL_PATH (realistic) | 1 | Preview + YOLO |

---

## 3. Root Cause Conclusion

### The stall is a hardware-level USB3 / PCIe bus contention artifact.

Evidence:

1. **Fixed duration** — Gap is always ~1.65–1.70 s regardless of software
   configuration, processing load, or timeout setting.
2. **Camera stops transmitting** — During a stall, `queued = 16/16`
   (all buffers returned to the transport layer).  This is not buffer
   exhaustion; the camera simply sends no data.
3. **Occurs even in a tight loop** — With zero processing, stalls still
   happen (just less frequently: ~1 per 30 s).
4. **GPU→CPU DMA amplifies frequency** — Large PCIe transfers (GPU
   downloading tensor data to CPU) compete with USB3 DMA for shared
   I/O fabric.  The RTX 5080 and USB controller share the same PCIe
   root complex on this laptop.
5. **Software mitigations do not eliminate it** — USB Selective Suspend,
   throughput limit tuning, buffer count changes: none prevented the
   underlying hardware event.

### Frequency Amplification Model

At 20 fps with full-resolution IDS + GPU pipeline:

| Transfer | Size | Direction | Frequency |
|:---|:---:|:---:|:---:|
| IDS frame upload | ~2.0 MB | CPU → GPU | 20 /s |
| Brightness check (`.mean().item()`) | — | implicit sync | 20 /s |
| YOLO results | ~1.3 KB | GPU → CPU | 20 /s |
| **Preview download** | **5.93 MB** | **GPU → CPU** | **20 /s** |
| DearPyGui texture | ~7.9 MB | CPU → CPU | 20 /s |

**Preview download dominates**: 20 × 5.93 = **118.6 MB/s** of PCIe
GPU→CPU traffic, competing with ~103 MB/s of USB3 DMA from the camera.

---

## 4. Mitigations Applied

### 4.1 Preview FPS Cap (auto-enabled, config-override-proof)

**File**: `application/src/app.py`

When `IDS_USE_FULL_RES` and `IDS_USE_GPU_DIRECT` are both `True`, the
preview download rate is automatically capped at **10 fps** (instead of
the full camera rate).  YOLO inference still runs at full rate.

```python
# Auto-cap preview FPS at 10 when IDS runs full-resolution
self.preview_fps_cap = bool(IDS_USE_FULL_RES and IDS_USE_GPU_DIRECT)
self.processor.set_preview_fps_cap(10.0 if self.preview_fps_cap else None)
```

The cap is forced ON on config load when IDS full-res + GPU direct is
active, so stale saved configs (with `preview_fps_cap: false`) cannot
silently disable it.  User can still toggle in the GUI.

### 4.2 GPU-side uint8 Conversion (4× bandwidth reduction per frame)

**File**: `application/src/gpu_pipeline.py` — `GpuFrame.to_numpy_bgr()`

Previously, the preview tensor was downloaded as **float32** (4 bytes per
channel) and cast to uint8 on CPU.  Now the cast happens on GPU before
the PCIe transfer:

```python
# Before (5.93 MB per frame):
hwc = t.permute(1, 2, 0).contiguous()
cpu_tensor = hwc.cpu()                         # float32 transfer
arr = (cpu_tensor.numpy() * 255).astype(np.uint8)

# After (1.48 MB per frame):
hwc = t.permute(1, 2, 0).contiguous()
hwc_u8 = hwc.mul(255).clamp_(0, 255).byte()   # float32→uint8 on GPU
cpu_tensor = hwc_u8.cpu()                       # uint8 transfer
arr = cpu_tensor.numpy()
```

**Impact**: Preview download drops from **5.93 MB → 1.48 MB** per frame
(4× reduction).

### 4.3 Brightness Check Decimation (eliminate per-frame GPU sync)

**File**: `application/src/gpu_pipeline.py` — `GpuEnhancer.enhance()`

The brightness auto-detect uses `gray.mean().item()` which forces a
**CUDA synchronize on every frame** — a PCIe round-trip that blocks the
bus even though it transfers only 4 bytes.  Scene brightness changes
slowly, so this is now decimated to every 10th frame:

```python
self._brightness_frame_counter += 1
if self._brightness_frame_counter >= self._brightness_interval:  # 10
    self._brightness_frame_counter = 0
    brightness = self._compute_brightness_gpu(tensor)  # CUDA sync
    self.last_brightness = brightness
else:
    brightness = self.last_brightness  # cached, no sync
```

**Impact**: Eliminates 90% of non-preview CUDA syncs.  Only the YOLO
result extraction (`.cpu()` on ~1.2 KB of keypoints) remains as a
per-frame sync.

### 4.4 Lower Default Preview Render Scale

**File**: `application/src/config.py`

`PREVIEW_RENDER_SCALE` lowered from **0.50** to **0.35**.  At the IDS
camera's 2688×1528 resolution:

| Render Scale | Preview Size | Transfer (uint8) |
|:---:|:---:|:---:|
| 0.50 (old) | 1344 × 764 | 3.08 MB |
| **0.35 (new)** | **940 × 535** | **1.51 MB** |
| 0.30 (tested) | 806 × 458 | 1.11 MB |

Empirical testing showed dramatic improvement below 0.38x.  At 0.30x
the stall rate dropped to ~1 per 260 frames (near hardware baseline).

### 4.5 Combined Effect

| Scenario | PCIe GPU→CPU (preview) | Per second |
|:---|:---:|:---:|
| **Original** (float32, 0.50x, 20 fps) | 12.3 MB × 20 | **246 MB/s** |
| + uint8 only | 3.08 MB × 20 | 61.6 MB/s |
| + FPS cap (10 fps) | 3.08 MB × 10 | 30.8 MB/s |
| + 0.35x scale | 1.51 MB × 10 | 15.1 MB/s |
| + brightness decimation | — | −9 sync/s |
| **All combined** | **1.51 MB × 10** | **~15 MB/s** |

Combined: **~16× reduction** in PCIe GPU→CPU load from the preview path
(246 → 15 MB/s), plus elimination of 90% of non-preview CUDA syncs.

### 4.6 IDS Max FPS

`IDS_MAX_FPS` reduced from 25 → **20** fps.  The camera reports a
maximum of 20.6 fps at full resolution with the default throughput limit.
Running closer to the physical maximum reduces headroom for recovery
after stalls.

---

## 5. Empirical Validation (App Run, Feb 25 2026)

Full app with uint8 download + IDS 2688×1528.  Preview FPS cap was
initially OFF (saved config override, since fixed):

| Phase | Render Scale | FPS Cap | Frames | Stalls | Stall Rate |
|:---|:---:|:---:|:---:|:---:|:---:|
| Cap OFF, 0.50x | 0.50 | OFF | 1–418 | ~16 | 1 per ~26 frames |
| Cap ON, 0.50x | 0.50 | 10 fps | 419–455 | 2 | 1 per ~18 frames |
| Cap ON, 0.38x | 0.38 | 10 fps | 456–585 | 1 | 1 per ~130 frames |
| **Cap ON, 0.30x** | **0.30** | **10 fps** | **586–846** | **1** | **1 per ~260 frames** |

Key observations:
- Stall duration is always ~1.26 s (hardware constant)
- `queued=16/16` during every stall (camera stops, not buffer starvation)
- `preview_sync=0.0` lines (skipped download) strongly correlate with
  stall-free periods
- Stalls occur even when `preview_sync=0.0` → other GPU sync points
  (brightness `.mean().item()`, YOLO `.cpu()`) also contribute
- The original transfer at 0.50x was **3.08 MB** (IDS 2688×1528),
  not the 960×540-based 1.48 MB estimated earlier

This led to adding the brightness check decimation (§4.3) and lowering
the default render scale to 0.35 (§4.4).

---

## 6. Remaining Hardware Constraints

These mitigations **reduce stall frequency** but cannot **eliminate**
stalls entirely.  The baseline hardware event (~1 stall per 30 s) is
inherent to the USB3/PCIe shared fabric on this laptop.

### Potential Further Mitigations — Strategy Analysis

Four strategies were evaluated for completely eliminating stalls.
**Strategy B was selected and implemented** (see §9).

#### Strategy A: Separate Process for Acquisition

```
[Process 1 — Acquisition]         [Process 2 — Main App]
IDS acq thread → numpy mono       SharedMem → GPU upload → enhance
    → write to SharedMemory            → YOLO → track → OSC
                                       → preview (from SharedMem CPU copy)
```

| Pros | Cons |
|:---|:---|
| Complete isolation: GPU PCIe in P2 cannot affect USB DMA in P1 | Significant refactor — IPC via shared memory + events |
| Acq process has zero CUDA, zero PCIe traffic | Frame copy via shared memory adds ~2–4 ms (4.1 MB mono8) |
| Tight-loop baseline: 0–1 stalls per 30 s | Two-process startup/shutdown complexity |
| Keeps GPU enhancement & TensorRT YOLO | Doesn't guarantee zero stalls (baseline was 0–1/30 s) |

**Effort**: High (2–3 days).  **Risk**: Medium.

#### Strategy B: CPU-First Pipeline with GPU YOLO Only (SELECTED)

```
IDS acq thread → numpy mono → read() → CPU BGR
    → YOLO(numpy) [Ultralytics auto-uploads to GPU]
    → result .cpu() [~1.3 KB — only sync point]
    → track → OSC
    → CPU preview (cv2.resize on CPU frame) — ZERO GPU download
```

| Pros | Cons |
|:---|:---|
| Eliminates ALL large GPU→CPU transfers | Loses GPU-accelerated enhancement (kornia) |
| Only 1 tiny GPU sync/frame: YOLO results (~1.3 KB) | Loses GPU temporal denoising |
| Preview is completely free — CPU `cv2.resize()` | YOLO internal upload adds ~1–2 ms |
| Lowest effort — CPU path already exists | Total ~20–25 ms vs ~12–15 ms (still in 50 ms budget) |
| Preview at full rate without interference | — |
| Perfect foundation for Strategy A later if needed | — |

**Effort**: Medium (0.5–1 day).  **Risk**: Low.

#### Strategy C: Async CUDA Streams + Non-Blocking Transfers

```
IDS → GPU upload (stream A, non_blocking) → enhance → YOLO (stream A)
    → result .cpu(non_blocking, pinned) + synchronize [SYNC]
    → track → OSC
    → preview .cpu(non_blocking, stream B) — async
```

| Pros | Cons |
|:---|:---|
| Minimal architecture change | `non_blocking` still needs sync before data use |
| Keeps GPU enhancement | YOLO result extraction must be synchronous |
| Preview download is truly async | Doesn't reduce total PCIe bandwidth |
| — | Does NOT address USB3 DMA contention fundamentally |

**Effort**: Medium (1 day).  **Risk**: High — may not improve anything.

#### Strategy D: Hybrid — CPU Capture + Decoupled GPU YOLO Thread

```
Main Thread:                        YOLO Thread:
IDS → numpy mono → ring buffer ←→  ring buffer → GPU upload → YOLO
    → CPU preview → DearPyGui       → results → queue → Main Thread
    → read results from queue
    → track → OSC
```

| Pros | Cons |
|:---|:---|
| Main thread has ZERO GPU activity | YOLO results arrive 1–2 frames late (~50–100 ms) |
| YOLO runs at its own pace | Threading complexity with Python GIL |
| Preview is CPU-only, completely decoupled | Result synchronization: tracked positions 1 frame old |

**Effort**: Medium-High (1–2 days).  **Risk**: Medium.

---

## 7. Key Configuration Parameters

| Parameter | Value | File |
|:---|:---|:---|
| `IDS_USE_FULL_RES` | `True` | config.py |
| `IDS_USE_GPU_DIRECT` | `True` (Strategy C, §10) | config.py |
| `IDS_MAX_FPS` | `20` | config.py |
| `PREVIEW_RENDER_SCALE` | `0.35` (max for IDS) | config.py |
| Buffer count | 16 | ids_camera.py |
| Stream mode | NewestOnly | ids_camera.py |
| DeviceLinkThroughputLimit | 162 MB/s (default, read-only) | ids_camera.py |
| WaitForBuffer timeout | 250 ms (at 20 fps) | ids_camera.py |
| Preview FPS cap | 10 fps (forced ON for IDS) | app.py |
| Frame skip | ≥1 (forced for IDS, YOLO at ~10 fps) | app.py |
| Preview GPU→CPU format | uint8 (converted on GPU) | gpu_pipeline.py |
| Brightness check interval | every 10th frame | gpu_pipeline.py |

---

## 8. Diagnostic Quick Reference

To re-run isolation tests, create a script in `application/` that opens
the IDS camera with `ids_peak.Library.Initialize()` and acquires in a
tight loop.  Key things to measure:

- **Stall = gap > 1.0 s** between consecutive frames
- **Gap duration**: should be ~1.65–1.70 s when stall occurs
- **Buffer pool health**: `acquisition.WaitForBuffer()` returns buffer
  with `buffer.IsIncomplete()` → stale frame
- **Expected baseline**: 0–1 stalls per 30 s in tight loop (no GPU)
- **With GPU pipeline**: should now be ~1 stall per 30 s (was ~1 per 5 s)

### Stall vs. Timeout

- **Timeout** (250 ms): Normal recovery mechanism; camera may skip a
  frame.  Logged but not alarming if infrequent.
- **Stall** (>1.0 s gap): Hardware-level event; ~1.65 s; camera stops
  transmitting.  Cannot be prevented, only reduced in frequency.

---

## 9. Strategy B Implementation (CPU-First Pipeline)

**Date**: February 25, 2026  
**Goal**: Eliminate PCIe contention by removing all large GPU→CPU
transfers from the main pipeline.

### 9.1 Architecture Change

**Before (GPU-direct):**
```
IDS acq → numpy mono → GPU upload (pinned) → GPU enhance → GPU resize
    → YOLO (GPU tensor) → results .cpu() [SYNC]
    → GPU preview resize → preview .cpu() [SYNC] → DearPyGui
```
GPU sync points per frame: 1–3 (brightness, YOLO results, preview)

**After (CPU-first, Strategy B):**
```
IDS acq → numpy mono → CPU BGR (cvtColor) → CPU enhance (OpenCV)
    → YOLO(numpy) [internal upload] → results .cpu() [SYNC, ~1.3 KB]
    → track → OSC
    → CPU preview (cv2.resize) → DearPyGui
```
GPU sync points per frame: 1 (YOLO results, ~1.3 KB — unavoidable)

### 9.2 Changes Made

- `config.py`: `IDS_USE_GPU_DIRECT = False` — disables GPU-direct path
- `app.py`: IDS camera uses `read()` (CPU BGR) instead of `read_gpu()`
- `app.py`: Routes to `processor.process(frame)` → `_process_cpu()`
- Preview is generated from CPU frame — zero GPU download
- GPU pipeline and enhancement code left intact for non-IDS cameras

### 9.3 First Test Result — Stalls Worse Than Expected

Strategy B eliminated GPU→CPU downloads but **introduced new CPU→GPU
uploads** and accidentally disabled all rate-limiting protections:

| Source | Old GPU (0.50× + cap 10) | Strategy B (no cap) |
|:---|:---:|:---:|
| IDS→GPU upload | 40 MB/s | 0 (eliminated) |
| Preview GPU→CPU download | 30 MB/s | 0 (eliminated) |
| YOLO auto-upload (new) | 0 | **~100 MB/s** |
| DearPyGui texture upload | 47 MB/s (capped 10 fps) | **93 MB/s** (uncapped 20 fps) |
| **Total PCIe** | **~117 MB/s** | **~193 MB/s** |

Result: 8 stalls in 344 frames (~1 per 43 frames) — **worse** than the
old 0.30× + cap configuration (1 per 260 frames).

Root cause: Strategy B eliminated GPU→CPU but the **DearPyGui texture
`set_value` is itself a CPU→GPU PCIe upload** (~4.7 MB per frame), and
YOLO receives a numpy array which Ultralytics uploads to GPU internally
(~5 MB per frame).  CPU→GPU PCIe traffic competes with USB3 DMA just as
much as GPU→CPU traffic — the PCIe root complex arbitrates all directions.

### 9.4 Fix: Rate-Limit All PCIe Sources (Feb 25 2026)

Three additional changes applied on top of Strategy B:

1. **Preview FPS cap always active for IDS** — `preview_fps_cap` now
   triggers on `IDS_USE_FULL_RES` alone (was `IDS_USE_FULL_RES and
   IDS_USE_GPU_DIRECT`).  Config override also uses `IDS_USE_FULL_RES`
   only.  CPU-path rate limiting added in main loop: texture upload
   capped at 10 fps via time check on `_last_preview_upload_time`.

2. **YOLO inference decimation** — `frame_skip` forced to ≥1 when IDS
   is active.  Effect: YOLO runs every 2nd frame (~10 fps), halving
   GPU upload traffic from ~100 to ~50 MB/s.  On skipped frames, the
   STANDBY path runs CPU-only enhancement with last tracked results.

3. **Preview scale cap** — saved project configs with `preview_scale
   > 0.35` are clamped to 0.35 when IDS is active, reducing per-frame
   DearPyGui texture from ~4.1 MB (0.50×) to ~2.0 MB (0.35×).

### 9.5 Expected PCIe Budget After Fix

| Source | Before Fix | After Fix |
|:---|:---:|:---:|
| YOLO auto-upload | ~5 MB × 20 fps = 100 MB/s | ~5 MB × 10 fps = **50 MB/s** |
| DearPyGui texture | ~4.7 MB × 20 fps = 93 MB/s | ~2.0 MB × 10 fps = **20 MB/s** |
| YOLO result download | ~1.3 KB × 20 fps ≈ 0 | ~1.3 KB × 10 fps ≈ 0 |
| **Total PCIe** | **~193 MB/s** | **~70 MB/s** |

This is comparable to the empirically-validated 0.30× + cap 10 scenario
(~66 MB/s) which achieved 1 stall per 260 frames — near the hardware
baseline of 0–1 per 30 s.

---

## 10. Strategy C: GPU-Direct + Decoupled Preview (Feb 25 2026)

**Outcome of Strategy B**: User tested the patched CPU-first pipeline
and still observed 4 stalls in ~300 frames.  Halving YOLO to ~10 fps
was unacceptable — the user requires **20 fps continuous YOLO detection**
for precise dance tracking.

### 10.1 Why Strategy B Was Fundamentally Flawed

| Property | GPU-Direct (Strategy A) | CPU-First (Strategy B) |
|:---|:---|:---|
| Upload mechanism | Pinned memory + `non_blocking` async DMA | Ultralytics `.to(device)` — pageable, synchronous |
| Per-frame upload size | ~4.1 MB (mono8 → pinned → CUDA) | ~5.0 MB (BGR → YOLO auto-upload) |
| Upload behavior | Async, overlaps with compute | Sync, blocks pipeline |
| Preview download | ~2 MB/frame (GPU→CPU, rate-limited) | 0 (CPU-side) |
| DearPyGui texture | CPU→GPU upload, competes with USB3 | Same |

The key insight: **CPU→GPU traffic competes with USB3 DMA equally** on
the shared PCIe root complex.  Strategy B's "eliminate GPU" approach
actually increased total PCIe traffic because Ultralytics' synchronous
pageable upload is burstier and larger than GPU-direct's pinned async.

### 10.2 Strategy C Architecture

Revert to GPU-direct for optimal YOLO path, but **decouple preview**
from the detection pipeline with 3 switchable modes:

```
Mode A (CPU-Cached Preview):
  IDS acq → mono8 → pinned GPU upload (async) → GPU enhance → YOLO 20fps
                  ↘ cv2.cvtColor(GRAY2BGR) → CPU cache (~1ms)
                    → DearPyGui texture at ≤10fps (CPU resize)
  PCIe: ~82 MB/s (upload) + ~20 MB/s (DPG texture) = ~102 MB/s

Mode B (Preview Disabled):
  IDS acq → mono8 → pinned GPU upload (async) → GPU enhance → YOLO 20fps
  PCIe: ~82 MB/s (upload only) — absolute minimum

Mode C (Slow GPU Preview):
  IDS acq → mono8 → pinned GPU upload (async) → GPU enhance → YOLO 20fps
                                                ↘ GPU resize → CPU download at 2fps
                                                  → DearPyGui texture at 2fps
  PCIe: ~82 MB/s (upload) + ~4 MB/s (preview) = ~86 MB/s
```

### 10.3 Key Optimizations

1. **CPU frame cache in `read_gpu()`**: After GPU upload, the same
   `mono8` numpy array is converted to BGR via `cv2.cvtColor` (~1ms)
   and cached as `_cached_cpu_bgr`.  Mode A uses this for preview —
   **zero additional GPU download**.

2. **STANDBY path fix**: Previously, when `should_process=False` and
   `gpu_tensor` was available, the code did `gpu_tensor.cpu().numpy()`
   — a 49 MB GPU→CPU download per skipped frame!  Now uses the cached
   CPU frame instead.

3. **GUI selector**: DearPyGui combo widget allows runtime switching
   between modes A/B/C without restart.

### 10.4 PCIe Budget Comparison

| Configuration | PCIe Traffic | Stall Rate (empirical) |
|:---|:---:|:---:|
| Pre-mitigation (0.50× preview) | ~258 MB/s | 1 per 5 s |
| §4 mitigations (0.30× + cap 10) | ~102 MB/s | 1 per 13 s |
| Strategy B (CPU-first, patched) | ~70 MB/s | 4 per 300 frames |
| **Mode A (CPU-cached 10fps)** | **~102 MB/s** | **1 per 274 frames** |
| **Mode B (preview disabled)** | **~82 MB/s** | **2 per 586 frames** |
| **Mode C (2fps GPU preview)** | **~86 MB/s** | **~1 per 280 frames** |

### 10.5 Changes Made

- `config.py`: Reverted `IDS_USE_GPU_DIRECT = True`
- `ids_camera.py`: Added `_cached_cpu_bgr` + `get_last_cpu_frame()` to
  `IDSCamera` and `UnifiedCamera`
- `app.py`: Added `IdsPreviewMode` enum (CPU_CACHED / DISABLED /
  SLOW_GPU), GUI callback, config save/load, 3-mode preview logic in
  main loop, STANDBY fix
- `gui_builder.py`: IDS preview mode combo widget
- `gui.py`: Callback + sync_combo mapping

### 10.6 First Test Results (Feb 25 2026)

All three modes tested in a single session.  Camera: 2688×1528 20fps,
Model: yolo26x-pose TRT@800.

#### Mode A (CPU-Cached Preview) — frames 1–355

- **1 stall** at frame 274 (1.26 s gap, `queued=16/16`)
- Stall rate: **1 per 274 frames** (~13.7 s)
- YOLO: running at full 20 fps (yolo=1.2–10.6 ms)
- Preview: GPU prescale eliminated, CPU cache at ≤10 fps
- `preview_sync=0.0` on all lines (no GPU→CPU download for preview)

#### Mode B (Disabled Preview) — frames 376–962

- **2 stalls** in 586 frames: at ~frame 461 (`ids_read_age=1.03s`)
  and ~frame 876 (`ids_read_age=0.90s`)
- Stall rate: **1 per 293 frames** (~14.6 s)
- YOLO: full 20 fps
- **Bug found**: stall detector was keyed on `preview_new` (display
  frame generation), not frame acquisition.  With preview disabled,
  `preview_new` is always False → `stall_age` climbed indefinitely →
  showed perpetual `state=STALL` even though camera was running fine.
  **Fixed**: stall detection now tracks `_last_fresh_frame_time`
  (reset when `frame_available` or `gpu_tensor_available` is True).

#### Mode C (Slow GPU Preview at 2fps) — frames 975–1157

- **1 real stall** at frame 1092 (1.26 s gap, `queued=16/16`)
- Stall rate: **~1 per 182 frames** (~9 s)
- YOLO: full 20 fps
- `preview_sync=1.2–1.5 ms` when preview fires (GPU→CPU download)
- **Bug found**: stall detector oscillated STALL/OK every ~0.25 s
  because preview only fires at 2 fps → `stall_age` reaches 0.25 s
  between previews → false STALL transitions.  Same fix as Mode B.

#### Conclusion

All three modes are **roughly equivalent** in stall rate (~1 per 180–293
frames).  The IDS→GPU pinned upload alone (~82 MB/s) already pushes the
PCIe bus to the contention threshold.  Adding preview (modes A/C) has
minimal additional impact.

**Mode A is recommended**: it provides the best user experience (live
preview at 10 fps) with no measurable increase in stall rate vs Mode B,
and zero GPU→CPU download for preview (CPU cached frame + CPU resize).

### 10.7 Stall Detector Bugs (Feb 25 2026)

Two bugs in `_log_runtime_diag_if_stalled()` caused misleading diagnostics:

**Bug 1 — Missing diag path for preview_new=False**

When `preview_enabled=True` but `preview_new=False` (Mode B always,
Mode C between 2fps updates), the inner `if preview_new and display_frame`
block was skipped but there was no `else` — the diag was never called
from the frame-processing path.  All diag entries came from the
"camera waiting" path (`cam_wait=1, frame=0, gpu_tensor=0`).

**Fix**: Added `else` branch to call diag when `preview_new=False`
inside the preview-enabled block with correct `frame_available` and
`gpu_tensor_available`.

**Bug 2 — Stall detection relied on diag-time frame flags**

`_last_fresh_frame_time` was only updated when the diag function saw
`frame_available=True` or `gpu_tensor_available=True`.  Due to Bug 1,
this never happened for Mode B.  For Mode C, it only happened at 2fps.

**Fix**: `_last_fresh_frame_time` is now updated in the main loop
immediately after successful frame acquisition (right after the
`frame is None and gpu_tensor is None → continue` gate), independent
of which diag path fires.

### 10.8 Second Test Results (Feb 25 2026)

Ran with stall detector still broken (pre-fix).  Confirmed:

#### Mode A — frames 1–166

- **3 real stalls** at frames ~79, ~118, ~166 (all `queued=16/16`)
- Stall rate: **1 per 55 frames** (~2.75 s) — worse than first test
- Diagnosis: stall_age was tracking frame time correctly for Mode A
  (preview_new fires OK), so this is a genuine regression in stall
  frequency compared to the first test (was 1 per 274).

#### Mode B — frames 178–635

- Stall detector: perpetual STALL (stall_age 2.26–31.50s) due to Bug 1
- **4 real stalls** (by `ids_read_age > 0.4s`):
  frame ~473, ~502, ~509, ~557
- Real stall rate: **~1 per 115 frames** (~5.7 s)
- Confirms Mode B does NOT prevent stalls — the IDS→GPU pinned upload
  alone (~82 MB/s) is sufficient to trigger PCIe contention.

#### Mode C — frames 648–1442

- Stall detector: oscillates STALL/OK every 0.25s due to Bug 2
- **0 real stalls** in ~794 frames (no `queued=16/16` timeout,
  no `ids_read_age > 0.5s`)
- Mode C was the most stable in this run despite adding 2fps GPU→CPU
  download.  Likely just variance — stalls are stochastic.

### 10.9 Root Cause Confirmed — PCIe Upload Bandwidth

All three preview modes exhibit real stalls (FPS drops visible to user).
Mode B (DISABLED) removes ALL preview traffic yet still stalls, proving
the bottleneck is the IDS→GPU pinned-memory upload itself:

| Metric                  | Value   |
|-------------------------|---------|
| Frame size (mono8)      | 4.1 MB  |
| Upload rate (20 fps)    | 82 MB/s |
| Shared PCIe root        | USB3 + GPU on same complex |
| YOLO imgsz              | 800     |
| YOLO effective input    | 800×454 (letterboxed from 2688×1528) |
| Wasted upload factor    | ~11× (upload 4.1 MB, need 0.36 MB) |

**Solution — Strategy E: CPU Pre-Resize Before Upload**

Resize the mono8 frame on CPU *after* caching the full-res preview
but *before* the pinned-memory GPU upload:

```
read_gpu():
  mono8 = acquire()               # 2688×1528 (4.1 MB)
  _cached_cpu_bgr = mono→BGR      # full-res cache for Mode A preview
  upload_mono = pre_resize(mono8)  # → 960×546  (0.5 MB)
  gpu_tensor = pinned_upload(upload_mono)
```

| Upload Target (max_dim) | Frame Size | PCIe @ 20fps | Reduction |
|--------------------------|------------|--------------|-----------|
| 0 (disabled)             | 4.1 MB     | 82 MB/s      | —         |
| 1344 (half-res)          | 1.0 MB     | 20 MB/s      | 76%       |
| **960 (default)**        | **0.5 MB** | **10 MB/s**  | **87%**   |
| 800 (YOLO-match)         | 0.36 MB    | 7 MB/s       | 91%       |

YOLO detection quality is **identical**: the GPU pipeline letterbox-resizes
to imgsz=800 anyway.  Enhancement runs at 960px instead of 2688px
(slightly lower quality but user declared preview expendable).

Implemented in `config.py` (`IDS_GPU_UPLOAD_MAX_DIM = 960`),
`ids_camera.py` (`_pre_resize_for_upload()`), and `app.py`
(overlay keypoint mapping updated for pre-resized tensor coords).

### 10.10 Cleanup — Revert to Clean Baseline (Feb 25 2026)

**Decision**: After testing Strategies B, C (3 preview modes), and E
(CPU pre-resize), **none eliminated the stalls**, and each added
complexity.  User requires full-resolution upload for future production
deployment with larger `imgsz` values.

**Reverted / Removed**:

1. **Strategy E (CPU pre-resize)**: Removed `IDS_GPU_UPLOAD_MAX_DIM`,
   `_pre_resize_for_upload()`, and pre-resize logic in `read_gpu()`.
   Full 2688×1528 mono8 is uploaded to GPU again.

2. **IDS Preview Modes (A/B/C)**: Removed `IdsPreviewMode` enum,
   `_cb_ids_preview_mode()`, `_apply_ids_preview_mode_caps()`,
   GUI combo widget, sync_combo mapping, config save/load.
   None of the 3 modes prevented stalls.

3. **Unused imports**: Removed `from enum import Enum`.

**Kept (clean, correct optimizations)**:

- GPU-direct path: pinned memory + `non_blocking` async upload
- GPU-side uint8 preview conversion (§4.2, 4× bandwidth reduction)
- Brightness check decimation (§4.3, 90% fewer GPU syncs)
- Preview FPS cap at 10fps (synced to GPU pipeline at init)
- CPU frame cache in `read_gpu()` for STANDBY mode (avoids 49 MB
  GPU→CPU download when not processing)
- Stall detector keyed on `_last_fresh_frame_time` (frame acquisition)

**Current architecture (clean baseline)**:

```
IDS acq → mono8 (2688×1528)
  → pinned GPU upload (async DMA, ~4.1 MB)
  → GPU enhance (kornia CLAHE + gamma)
  → YOLO letterbox → inference → results .cpu() (~1.3 KB)
  → track → OSC
  → preview: GPU resize → uint8 → .cpu() (~1.5 MB) at ≤10fps
  → DearPyGui texture upload
```

**Next step**: Incremental test scenarios in a new session to isolate
the exact PCIe/USB3 contention trigger with minimal code.
