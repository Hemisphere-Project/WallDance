# 02 — IR retroreflective markers at ankles + wrists: design, fusion, Phase 0a protocol

**Stream:** IR markers (1 of 5 audit streams) · **Date:** 2026-10-05 · **Mode:** read-only audit, scratch prototypes only
**Inputs read:** `docs/TRACKING_ROBUSTNESS.md`, `docs/OPTICS.md`, `docs/OSC_CONTRACT.md`, `docs/ROADMAP.md` §3.3, `docs/CORPUS_ANALYSIS.md`, `tmp_analysis/marker_spike.py`, `core/video_recorder.py`, `camera/ids_camera.py`, `runtime/recording_controller.py`, `core/gpu_pipeline.py`, `core/pipeline.py`, `core/tracker.py`, `core/calibration.py`, `runtime/calibration_flows.py`, `core/config.py`, `tests/replay.py`, `tests/detect_cache.py`.
**Scratch artefacts:** `tmp_analysis/audit-2026-10/markers/` — `marker_eval.py` (harness prototype), `pose_dump.py`, `synth_markers.py`, `pose_stats.py`, `out/` (summaries, contact sheets, montages), `poses/` (pose dumps).
Tags: **[FACT]** = read in code or measured here · **[HYP]** = design hypothesis / literature value to be verified in Phase 0a.

---

## 0. Executive summary

1. **[FACT] The recordings are faithful to the live marker signal, and the brief's premise needs correcting.** The live path is **not** Mono10/12: the app always picks **Mono8** (`camera/ids_camera.py:565-570`, `UnifiedCamera` passes `prefer_high_bit_depth=False` at `:1622`). Slot recordings are **lossless FFV1** copies of exactly that Mono8 frame, before ROI/gamma/CLAHE. I checked B==G==R on all 5 IDS recordings on this box. So the incoming test footage *is* what a live marker stage would see, bit for bit. Three gaps remain: exposure/gain/AE state is not recorded, frames still queued when a take is stopped are dropped (30 frames on whitebg2 s4), and the container says 30 fps while the true rate (~19.8) lives only in `.meta`.
2. **[FACT→HYP] Exposure is what Thomas most needs to know before the shoot.** The saved configs of the corpus hangar projects show **~51.6 ms exposure, 27.8 dB gain**, and the scene still sits at a mean of ~5/255. Wrist and ankle markers will therefore record as **motion streaks**. A streak only spends part of the exposure on each pixel, and an off-axis illuminator weakens the retro return. Together these can pull a fast marker **below saturation** (worked numbers in §1.6). So: run Aim, **switch auto-exposure/auto-gain OFF**, keep exposure ≤ 25 ms (Aim's blur budget), put the **illuminator against the lens**, and write down the settings for every take.
3. **[FACT] The glint floor on marker-less footage is zero at T ≥ 160:** 0 candidates in 7,041 sampled frames from 5 IDS recordings, except one frame. That one frame is itself informative: a **natural, one-frame, saturated 8×6 px glint on a walking person's clothing** (`default` s6 f806, peak 255, 30 saturated pixels, at a scene mean of only 5.4/255). **But the zero floor is an exposure artefact.** Natural maxima are 22–113 DN, which leaves only 2.3–6× headroom to 255. Aim's servo target is mean 70 (`config.py:467`), about 4–14× brighter than the corpus. On today's dim rig the servo would probably stop at its gain limit: a 25 ms cap + 36 dB is only ≈1.2× the corpus setting of 51.6 ms + 27.8 dB. But with the planned extra IR (ROADMAP P1.3), or in a brighter venue, it *will* push toward 70. So the marker threshold must be **calibrated per venue at the show exposure**. The fixed 245–254 threshold suggested by Phase 0b does not transfer.
4. **Detector [FACT cost, HYP design].** Run it on the CPU, on the raw ROI gray the pipeline already extracts, **inside the existing motion-feed worker** (it runs in parallel with YOLO, `pipeline.py:558-578`). Steps: threshold + early-out, then sparse connected components, then blob features. Measured on the old dev i7 under load 14: **0.4–0.65 ms** for an empty frame and **≈4.7 ms single-thread** with 8 injected streak markers. It needs no GPU sync, no cv2.cuda (absent on this box) and no detect-cache format change: the cache already stores the raw gray (`tests/detect_cache.py:135-142`).
5. **[FACT, synthetic study on 6,960 real skeleton frames from 4 takes: aerial, floor, 2 wall climbs] Extremity markers are precise points but biased, articulated stand-ins for the centroid.** The plain mean of the 4 markers sits **0.15–0.17 H** from the tracker's centroid (median). With per-marker offsets learned while YOLO is present and frozen at the drop, the centroid error on the **fast aerial swing** (`hangar-aerial` take) over a YOLO gap is:

   | Gap | 4 markers | 3 | 2 | 1 | Today (hold last position) |
   |---|---|---|---|---|---|
   | 0.25 s (5 frames) | **0.063 H** | 0.079 | 0.10 | 0.13 | 0.186 |
   | 0.5 s (10 frames) | **0.074 H** | 0.10 | 0.14 | 0.19 | 0.283 |
   | 1 s (20 frames) | **0.10 H** | 0.13 | 0.14 | 0.20 | 0.36 |

   That is a 2.5–3.8× improvement with 3–4 markers. On the **slow scenes** (tango-H/H2 wall climbs, `hangar-floor`), holding the last position is already ~0.03–0.06 H, and the simulated markers do **no better** with 3–4 markers and *worse* with ≤ 2. **So the positional gain depends on the scene; the gain that holds everywhere is existence/keep-alive.** A single marker is risky (aerial p90 0.29–0.59 H). Velocity extrapolation (today's KF coast) is *worse* than holding the last position on both scene types.
6. **What this changes in the plan.** Markers give *existence/confirmation* (ghost rejection, static acquisition, keep-alive) at a **moderate** Kalman R (≈20–110× YOLO's R=2), **not** the "tiny R" that `TRACKING_ROBUSTNESS.md` assumed for a centre-of-mass marker. **Adding one harness marker is the best-value change to the costume.** In the synthetic study the hip-midpoint marker alone reaches 0.042–0.070 H on the aerial (0.018–0.037 H on the climbs), as good as or better than all 4 extremities, and 5 markers reach 0.033–0.085 H. It also measures the centroid directly, so it gets a near-YOLO R. Recommended layout: **4 extremity cuffs + 1 harness marker (front, optionally back).**
7. **Fusion has three tiers.**
   - (A) **YOLO present:** Hungarian match of blobs to keypoints {9,10,15,16}. This confirms the track, vouches for a weak detection at the crossval gate (so static dancers stop being rejected) and learns the per-slot offsets; keypoint refinement is optional.
   - (B) **YOLO gap:** track each marker "slot" through the gap. Identical markers get mislabelled in **26 %** of gaps, and this costs only ≈0.005–0.02 H. Then feed the offset-vote centroid with R(n, gap), take precedence over the motion bridge, and exempt the track from the frozen-ghost gate.
   - (C) **No YOLO at all:** a ≥2-marker constellation that appeared after Aim and persists becomes a provisional marker-only track. This is how a static dancer gets acquired.
8. **Tracker interface (to agree with the continuity stream).**
   - `update(..., markers=None)`: `None` or an empty list must be byte-identical to today.
   - A measurement-source abstraction replaces the current "all-zero confidence ⇒ motion" convention (`tracker.py:1994`).
   - A new `_frames_since_marker`; the frozen-ghost gate uses `min(fss, fsm)`.
   - Output-only `ScaledTrack` fields.

   **OSC stays unchanged.** Two optional additive messages (`/walldance/dancer/source`, `/walldance/dancer/markers`) sit behind a flag and need operator confirmation.
9. **Phase 0a go/no-go (§6):**
   - recall ≥ 0.95 on unoccluded markers at the farthest show distance;
   - ≤ 0.01 moving false positives per frame on an empty stage after the glint map;
   - ≥ 1 marker visible on ≥ 90 % of dancer-frames in aerial choreography;
   - centroid error with ≥ 2 markers at gaps ≤ 1 s: median ≤ 0.10 H, p90 ≤ 0.30 H;
   - invisible to the audience **including near the projector/front-light axis**.
10. **Build order (§8).** MRK-0 (recorder `.meta` with exposure/gain + tail-drain fix + the shoot brief) should land **before** the shoot. The eval harness, synthetic injection and the detector skeleton can be built before the footage arrives. Fusion is gated on the Phase 0a go.

---

## 1. Recording fidelity

### 1.1 The data path [FACT]

| Stage | What happens | Where |
|---|---|---|
| Sensor readout | IMX664 has a 12-bit ADC, but the app selects **Mono8 first** ("ALWAYS prefer Mono8"). The packed Mono10/12 fallbacks keep only the top 8 bits. | `ids_camera.py:549-570`, `:1186-1200` |
| Bit-depth preference | `UnifiedCamera._open_ids` sets `prefer_high_bit_depth=False` | `ids_camera.py:1622` |
| Camera settings at open | AE **and** AG Continuous; AE upper limit 45 ms; **UserSet1** loaded (in-camera gamma/LUT/hot-pixel settings unknown) | `ids_camera.py:1619-1620`, `config.py:93,107`, `ids_camera.py:516-524` |
| Live → GPU | mono8 → pinned upload → `/255` → 3-channel float | `ids_camera.py:1306-1346` |
| Live → motion feed | `get_last_cpu_frame()` (= `_mono_to_bgr_cpu(mono8)`) is `raw_frame`; a single channel goes to `gray_for_motion` (P-1 `mono_raw`) | `main_loop.py:592-596`, `pipeline.py:569-575` |
| Recording | The acquisition thread passes `_mono_to_bgr_cpu(frame)`, i.e. **the same mono8**, to the recorder callback | `ids_camera.py:1117-1121`, `:1300-1304`; `recording_controller.py:282-285` |
| Encoder | `RECORDING_CODEC="FFV1"` (lossless), `.avi`, VideoWriter on its own thread; size = camera state w×h (no resize); nominal fps = `CAMERA_FPS` 30, real fps → `.meta` | `config.py:597`, `video_recorder.py:200-205,313-332,359-370`, `app.py:414-416`, `recording_controller.py:361-362` |
| Enhancement / ROI | GPU crop → gamma/CLAHE → letterbox, all **after** capture, so **not baked into recordings** | `gpu_pipeline.py:587-600` |

### 1.2 Measured on disk [FACT]

| Recording | Codec / pix_fmt / bits | W×H | Container fps vs `.meta` | B==G==R | Notes |
|---|---|---|---|---|---|
| whitebg2 s3 (= residence1-solo s3) | ffv1 / bgra / 8 | 1488×1528 | 30 vs **19.82** | yes | 9674 = 9674 frames |
| whitebg2 s4 (golden `hangar-aerial`) | ffv1 / bgra / 8 | 1488×1528 | 30 vs **19.70** | yes | `.meta` 5118 vs **5088 written → 30 tail frames lost** |
| tango-H s8 | ffv1 / bgra / 8 | 1488×1528 | 30 vs 19.80 | yes | 1505 vs 1504 |
| tango-H2 s9 | ffv1 / bgra / 8 | 1488×1528 | 30 vs 19.82 | yes | exposure **drifts within the take** (p99.9 ≈ 80 at f220 → ≈ 30 later), consistent with AE on |
| default s6 | ffv1 / bgra / 8 | 1920×1204 | 30 / no meta | yes | a different crop ratio |
| kxkm*, default s1 (Dec 2025 / Feb 2026) | **mjpeg / yuvj420p** | 1920×1080, 1632×1400 | – | – | older default codec; do **not** use MJPG for marker work |

- **Storage [FACT]:** about 770 KB/frame, i.e. ≈ 15 MB/s or **≈ 55 GB/hour** at 20 fps.
- **Tail loss mechanism [FACT]:** `stop_recording` sets `_recording_running=False` *before* queueing the sentinel (`video_recorder.py:342-346`), and the encoder loop `while self._recording_running` (`:218`) exits at once, so whatever is still queued is discarded. Separately, a full queue (300 frames) drops frames mid-take, logged only as a console print (`:326-332`).

### 1.3 Does a small saturated blob survive?

- **FFV1: yes [FACT].** It is lossless, and with three identical planes there is no chroma subsampling issue.
- **MJPG q100 [HYP]:** luma is kept at full resolution and chroma is neutral for mono content, so subsampling is harmless. DCT ringing around a 255 point on a ~5 DN background is small but non-zero; it changes the area above threshold by about a pixel and leaves peak and centroid essentially intact. Acceptable as a fallback, never preferred.
- **Gamma clipping:** none in recordings, since gamma is applied downstream. The open question is **in-camera** processing from UserSet1: a gamma/LUT would compress highlights, and on-device **hot-pixel correction could erase 1–2 px points** (a far marker). Both would affect live and recorded frames equally, but they change marker appearance, so ask (§10, Q1).

### 1.4 Live vs recording: what differs [FACT]

1. **Pixels are identical** (same mono8; FFV1 lossless; replay `process()` gives the same gray because R==G==B, `pipeline.py:569-575`).
2. **Frame cadence differs.** Recordings hold *every* acquired frame. Live processing is newest-only and can skip frames under load, while replay processes all of them. Temporal marker parameters (link gates, persistence windows) must therefore be in **per-second units** or robust to skipped frames.
3. **Camera state is not recorded.** Exposure, gain, AE/AG flags and illuminator setup cannot be recovered from a take; the saved project config may postdate it.
4. **Recordings are pre-ROI.** Markers outside the operator ROI are recorded but would be ignored live. That is fine.
5. **Future caveat:** if Track D #3 (native-bit-depth tone-map) moves the live path to Mono12, the recorder must move to 16-bit gray FFV1 as well, or recordings stop being faithful.

### 1.5 Recommended settings for the marker shoot (send to Thomas before the shoot)

1. **Codec:** make sure the laptop's `config.py` has `RECORDING_CODEC = "FFV1"`; the console prints `[Recorder] Codec: FFV1`. Have **≥ 100 GB free**.
2. **Exposure:** on the empty stage under show IR, press **Aim** (the servo caps exposure at the 25 ms blur budget, `config.py:466`). Then **uncheck auto-exposure and auto-gain** so the values are frozen. **Photograph the camera drawer** (exposure µs, gain dB) for every take. If you do not use Aim, still cap exposure at ≤ 25 ms; 50 ms smears wrists.
3. **Illuminator:** mount it **as close to the lens axis as physically possible** (≤ 10 cm offset if you can). Record one take with the usual mounting position for comparison. Note the offset in cm and the camera→stage distance.
4. **Focus** under IR with the web-monitor focus score; marker size and peak depend on IR focus.
5. **Stopping a take:** wait **~2 s after the action** before stopping, because the encoder tail is dropped. Keep takes ≤ 3 min (≈ 2.7 GB/min).
6. **Same crop/ROI and lens as the show.** Use the 8 mm unless the show uses the 6 mm.
7. **IDS Cockpit:** list what **UserSet1** sets (gamma, LUT, hot-pixel correction, black level, digital gain).
8. **Photos:** markers in daylight, plus a **phone-flash photo taken right next to the lens** (shows the retro return), plus material, sizes and placement.
9. **Take plan:** follow §6; one take per slot with the history kept; write the slot→take mapping on paper.

### 1.6 Marker physics at this rig [FACT formula / HYP materials]

- **Pixel scale (8 mm lens, 2.9 µm pixels):** 0.3625·D mm/px, so **7.25 mm/px at 20 m and 14.5 mm/px at 40 m**. `TRACKING_ROBUSTNESS.md` states 5.8 and 11 mm/px, which **understates by about 25 %**; it follows from `OPTICS.md`'s own `4690/D` px per 1.7 m.

| D (m) | mm/px | 3 cm cuff | 5 cm | 8 cm | Illuminator offset 30 cm → α | offset 10 cm → α |
|---|---|---|---|---|---|---|
| 10 | 3.6 | 8.3 px | 13.8 | 22 | 1.7° | 0.57° |
| 20 | 7.25 | 4.1 | 6.9 | 11 | 0.86° | 0.29° |
| 30 | 10.9 | 2.8 | 4.6 | 7.3 | 0.57° | 0.19° |
| 40 | 14.5 | 2.1 | 3.4 | 5.5 | 0.43° | 0.14° |

- **Retro return depends on the observation angle α [HYP, literature].** The coefficient of retroreflection falls steeply with α. Approximate ISO 20471 minimum values for class-2/3 garment retro, from memory and to be verified: ~330 cd·lx⁻¹·m⁻² at 0.2°, ~250 at 0.33°, ~25 at 1°, ~10 at 1.5°. Against white diffuse paper (ρ≈0.8), a marker is ≈ 3.9·RA times brighter, so ~1300× at 0.2° but only ~40× at 1.5°. **For a fixed illuminator offset, closer dancers mean a larger α and a relatively weaker retro return.**
- **Streak saturation model [HYP].** A marker of size d px moving L px during the exposure spends only `d/(d+L)` of the exposure on each pixel. Take a wrist at 2–5 m/s at 20 m: L = 7–17 px at 25 ms, or 14–34 px at 50 ms. A 4 px marker then has a dwell fraction of 0.11–0.36. With the white wall at ≈13 DN (whitebg2 p99.9), the expected streak level is:

  | Observation angle α | Streak level |
  |---|---|
  | ≤ 0.33° | ~1,800–18,000 DN, saturated with large margin |
  | ~1° | ~140–360 DN, borderline at T=160 |
  | ~1.5° | ~55–180 DN, **below T=160** |

  **Conclusion:** keep the illuminator close to the lens, keep exposure ≤ 25 ms, use an adaptive threshold, and expect **streaks rather than dots** (so no circularity filter). All of this gets measured in Phase 0a takes S8/S9.

---

## 2. Detector design (`core/marker_detector.py`, new)

### 2.1 Placement: CPU, raw gray, inside the motion worker (recommended)

| Option | For | Against | Verdict |
|---|---|---|---|
| **(a) CPU in the motion-feed worker on `gray_for_motion`** | Raw, native resolution, ROI-cropped, already extracted (`pipeline.py:558-578`). Runs in parallel with YOLO (`_submit_motion_feed`, `:1015-1019`; awaited at `:613-616`), so it is **off the critical path**. Deterministic. **Detect-cache compatible**: the cache stores this gray as a PNG (`detect_cache.py:135-142`) and `replay_gpu_cached` re-feeds it (`pipeline.py:657-665`), so marker runs replay from the existing TRT caches at no extra cost. | CPU cost (measured below) | **Choose** |
| (b) GPU torch threshold + nonzero on the raw tensor before `enhance` (`gpu_pipeline.py:587-592`) | 0.6–2.8 ms incl. download (measured, contended GPU) | `nonzero()` forces a stream sync before YOLO; two code paths (`process` vs `process_gpu_tensor`); not visible to the CPU detect-cache | Fallback only if the CPU budget fails |
| (c) on the YOLO letterbox input | – | Downscaled (a 4 px marker becomes ~2–3 px at imgsz 1280) and enhanced (CLAHE) | Reject |

Blobs come out in ROI-local original pixels as `MotionBlob`-compatible objects and are mapped to tracker space by the existing `_TrackerSpace.blobs_to_tracker` (`pipeline.py:296-311`). Sub-pixel float centroids survive the letterbox scale.

### 2.2 Algorithm

1. **Early-out:** `cv2.threshold(gray, T)` + `countNonZero`; if empty, return `[]`.
2. **Sparse labelling:** `findNonZero`, then group the points through a **4 px cell grid** with `connectedComponents` on the small cell image. This joins bloom halves and streak gaps. Per-blob features come from `bincount`: area, `n_sat` (==255), peak, **intensity-weighted sub-pixel centroid** (optionally over a lower "skirt" threshold for precision), and second moments giving elongation and **orientation**.
3. **Physical gates scaled by dancer size.** Expected marker diameter `d ≈ marker_cm/170 · person_height_px`, plus 1–3 px of bloom. Area must lie in `[2, k·(d+3)·(d+3+L_max)]`, where `L_max` is the streak allowance from the slot's predicted speed × exposure × fps. **No circularity filter**, since streaks are expected. When the slot is tracked, the streak orientation should match its motion direction; this is a soft cost, not a hard gate.
4. **Temporal persistence:** greedy/Hungarian chain linking with a gate in per-second units. A *new* chain must appear in **≥ 2 of the last 3 frames** before it can drive anything on its own. That kills one-frame specular flashes like `default` s6 f806. A blob matched to a YOLO wrist/ankle keypoint this frame is accepted immediately.
5. **Static glint map (fine, 8 px cells, dilated by 1).** It is built during **Aim** (empty stage) from all blobs at `T_lo`. At runtime, blobs inside it are flagged `static` and ignored unless matched to a YOLO extremity keypoint (a dancer's hand on a glinting rail). Optional runtime learning: a chain with < 1 px drift for > 30 s and no track within 1 H is added with decay, and logged.
6. **Exclusion-mask reuse:** the operator grid (16×10, `config.py:562`) has cells of ~93×153 px. That is far too coarse to *delete* marker blobs, because it would delete the markers of any dancer crossing that cell. Reuse it only to **veto marker-only track creation** in excluded cells, which matches the semantics `_apply_exclusion` gives YOLO detections (`pipeline.py:1214`).

### 2.3 Threshold auto-calibration (fits the Aim/Calibrate flows)

- **Aim (`calibration_flows.py:190-326`).** After the servo converges, the existing collection window also records `max_natural` (the empty-stage 99.99th percentile and maximum outside the glint map). Then `T_marker = clamp(max(120, 2·max_natural), ≤ 240)`. If `max_natural ≥ 200`, show a **warning**: "8-bit marker separability low: reduce gain/exposure or mask bright fixtures".
  - The servo's clip guard (>0.5 % of pixels ≥ 250, `calibration.py:233-246`, `config.py:469`) is **not** tripped by markers: 4 markers × N dancers × ~50 px ≈ 0.01 % of the frame.
- **Calibrate-with-dancers (Calib2 pool).** With markers visible, measure blob peak, area and count per dancer, and the blob↔keypoint residual distribution. This proposes the association gate and validates `T_marker`: ≥ 95 % of associated blobs must clear T. It also proposes "markers-required" mode when every confirmed dancer shows markers.
- Persist `marker_threshold`, the glint map and the gate in `calibration_state` provenance, the same way the other Aim/Calib2 knobs are stamped.

### 2.4 Cost [FACT, dev box i7-3770K, load ≈ 14 on 8 threads, RTX 3090 shared]

| Variant (1488×1528 uint8) | ms |
|---|---|
| cv2 threshold + CC, full frame (multi-thread, first run) | 6.3–17.0 (13.5 at 1920×1204) |
| same, single-thread under load | 31.5 |
| threshold + `countNonZero` early-out (empty frame) | **0.4–0.65** |
| threshold → `findNonZero` → 4 px cell CC → bincount features (8 streak markers, single-thread) | **4.7** |
| GPU torch threshold + nonzero + download | 0.6–2.8 |

At the full 4.1 MP sensor (2688×1528, used only for wide stages) every pass scales by about 1.8×. That gives ~1 ms early-out and ~5–9 ms sparse on this old CPU. The full-frame CC (up to ~55 ms single-thread) is the variant to avoid. The prod laptop CPU is much faster single-thread, and the stage runs concurrently with YOLO. **Budget gate:** p95 ≤ 2 ms on the prod laptop, and no change in total frame latency beyond ±1 ms (replay timing).

### 2.5 False-positive catalogue

| Source | Appearance | Rejection |
|---|---|---|
| Fixed fixtures (lit panels, CCTV IR LEDs at 850 nm, tungsten fixtures, safety tape, glossy windows) | Static; can be large; `default` s6 panel max 126 → gone at T≥160 | Aim glint map + T calibration |
| Illuminator reflections in glass/metal truss | Static saturated points | Glint map |
| **Harness hardware** (carabiners, descenders, rings) | Moving with the dancer; specular, **intermittent**, compact, near the pelvis | Persistence + position relative to the skeleton (pelvis vs extremity slots). Note: a hardware glint near the hips is a *decent* centroid proxy, but never let it fill an extremity slot |
| Clothing retro/specular (crew, audience front rows) | One-frame flashes (`default` s6 f806, 39 px, peak 255, on a walker) | ≥ 2/3-frame persistence, body support, exclusion veto on acquisition |
| Eyes (bright pupil at 850 nm on-axis) | Sub-pixel at ≥ 10 m [HYP] | Area floor |
| Hot pixels at high gain | 1 px, fixed | Glint map; area ≥ 2 |
| Rope | Diffuse, not retro; stays well below T (whitebg2 max 22–41) | Threshold |

---

## 3. Fusion design (ankle + wrist markers)

### 3.0 Why the extremity layout changes the plan [FACT, synthetic study]

`TRACKING_ROBUSTNESS.md` assumed a marker at the centre of mass, i.e. a near-direct centroid measurement with "tiny R". With wrist and ankle markers:

- The tracker's measurement is the **confidence-weighted mean of all visible keypoints** (`tracker.py:294-304`). Extremities are offset from it by a **pose-dependent** amount.
- The YOLO wrist/ankle keypoints on IR aerials are **noisy**. Matching the 4 extremity points between consecutive frames without labels still gives p50/p90/p99 displacements of **0.09/0.37/0.74 H per frame**, and **21 %** of consecutive frame pairs show a left/right/limb label flip. Markers would therefore fix extremity jitter and L/R flips in the skeleton. It also means the association gate must be generous.
- **Today's continuity on the `hangar-aerial` take** (full 5,088 frames, PyTorch GPU path, pinned config): only **53 %** of reported dancer-frames are skeleton-fed, and 25 track ids are created for one dancer. Skeleton gaps on the long tracks are **p50 2, p90 6, p99 19, max 28 frames**; 58 % of gap-frames fall in gaps ≤ 5 frames, 37 % in gaps of 6–20 and 6 % in gaps > 20. This is exactly the regime markers address.

**Synthetic study** (`synth_markers.py`). Markers were simulated **at the YOLO keypoints 9, 10, 15, 16** on 2,650 real aerial skeleton frames (H ≈ 204–214 px), giving 2,251 gap samples. Each anchor frame t0 freezes the model, and the centroid at t0+k is estimated. Values are median / p90 error ÷ H. Markers are **unlabelled**: slots are re-associated frame by frame through the gap with a Hungarian nearest-neighbour step, so this is realistic for identical dots. Because the "ground truth" is YOLO's own centroid at t0+k, it carries YOLO jitter, and the numbers are conservative.

| Gap k (frames @20 fps) | Today: hold | Today: KF-like velocity decay | 4 markers (offset vote) | 3 | 2 | 1 | 1 harness marker | 4 + harness |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.068/0.148 | 0.068/0.172 | **0.043/0.111** | 0.051/0.142 | 0.062/0.192 | 0.073/0.292 | 0.042/0.099 | **0.033/0.084** |
| 2 | 0.102/0.229 | 0.102/0.254 | **0.048/0.128** | 0.061/0.167 | 0.076/0.232 | 0.092/0.358 | 0.045/0.117 | 0.037/0.097 |
| 5 | 0.186/0.461 | 0.185/0.452 | **0.063/0.134** | 0.079/0.193 | 0.100/0.272 | 0.130/0.467 | 0.062/0.130 | 0.049/0.106 |
| 10 | 0.283/0.708 | 0.338/0.773 | **0.074/0.171** | 0.103/0.234 | 0.137/0.332 | 0.189/0.553 | 0.070/0.174 | 0.059/0.143 |
| 20 | 0.362/1.06 | 0.443/1.08 | **0.102/0.185** | 0.126/0.242 | 0.144/0.369 | 0.200/0.590 | 0.053/0.157* | 0.085/0.150* |

\* N = 34 samples.

Other findings from the study:

- **Without an offset model, markers are useless as a centroid.** The plain mean of the visible markers is 0.15 H off with 4 markers and 0.37 H off with 1, even at k=0.
- **A similarity transform does not beat the frozen per-slot offsets** for n = 2 (0.10–0.16 vs 0.06–0.14). It roughly ties for n = 3–4.
- **Labelled vs unlabelled offsets differ by only 0.005–0.02 H**, even though **26 %** of unlabelled gaps end with at least one slot mislabelled. Swaps happen when two extremities are close, so their effect on the centroid is small. They *do* matter for skeleton refinement (L/R).
- **Which pair is visible matters:** a wrist pair and an ankle pair perform similarly with offsets (0.13–0.15 H at k=10–20); a mixed wrist+ankle pair is slightly better.
- **Merging:** two markers of the same dancer are within 0.031 H (≈ 6–7 px) of each other 5 % of the time, and within 0.049 H 10 % of the time. Expect merged blobs, so a constellation may show 3 blobs instead of 4.

**The same study on the slow wall-climb takes** (tango-H s8 + tango-H2 s9; 2,281 skeleton frames, H median 327/449 px, 2,065 gap samples, median / p90 ÷ H):

| Gap k | Hold | Velocity decay | 4 markers | 3 | 2 | 1 | 1 harness | 4 + harness |
|---|---|---|---|---|---|---|---|---|
| 1 | **0.020/0.045** | 0.029/0.071 | 0.021/0.057 | 0.024/0.065 | 0.028/0.080 | 0.032/0.109 | **0.018/0.043** | 0.017/0.047 |
| 5 | **0.028/0.064** | 0.071/0.193 | 0.028/0.067 | 0.032/0.083 | 0.038/0.103 | 0.044/0.145 | 0.020/0.053 | 0.023/0.053 |
| 10 | 0.038/0.083 | 0.104/0.297 | **0.033/0.084** | 0.041/0.097 | 0.047/0.121 | 0.057/0.178 | 0.028/0.066 | 0.027/0.066 |
| 20 | 0.055/0.142 | 0.169/0.404 | **0.039/0.075** | 0.047/0.104 | 0.058/0.143 | 0.075/0.220 | 0.037/0.073 | 0.034/0.072 |

How to read it:

- The climber barely moves (centroid speed p50 0.019 H/frame vs 0.069 on the aerial). Holding the position wins for short gaps, and the extremity estimate only overtakes it at ≥ 10–20 frames.
- Two effects make the simulation pessimistic. The simulated markers carry **YOLO keypoint noise** (real retro blobs are sub-pixel). The "truth" is YOLO's own jittery centroid.
- Practical consequence: **feed markers through the Kalman filter with an R taken from this table, and let the filter arbitrate against its own prediction.** Do not override the prediction the way the motion bridge does.
- **Similarity fits fail badly on an ankle-only pair** (0.24–0.67 H), so never use a similarity fit with 2 markers. **Ankles are the better pair while climbing** (0.024–0.056 H vs wrists 0.035–0.073 H).
- **Merges are frequent while climbing.** Two markers of one dancer are within 0.007 H 5 % of the time and within 0.012 H 10 % of the time (hands or feet sharing a hold), so constellations will often show 2–3 blobs. Unlabelled slot swaps occur in 36 % of gaps.
- Skeleton feed today: tango-H 94 % of reported frames, gaps ≤ 6 frames; **tango-H2 56 %**, gaps p99 16, max 61 frames, 14 track ids.

**Floor take** (`hangar-floor` window, frames 1000–3999; 2,029 skeleton frames; 1,093 gap samples). This scene looks like the climbs:

- Hold: 0.030 / 0.038 / 0.063 H at k = 5 / 10 / 20.
- 4 markers: 0.026 / 0.036 / 0.059 H. 2 markers: 0.037 / 0.054 / 0.083 H, worse than hold.
- 4 + harness: 0.021 / 0.027 / 0.040 H, which beats hold.
- YOLO sees all 4 extremities on only **50 %** of floor skeleton frames (vs 97 % aerial, 81–92 % climbs). Floor work hides limbs, so expect frequent marker occlusion there; this is where the harness marker pays off.
- 89 % skeleton-fed, gaps ≤ 7 frames, one track id.

### 3.1 Tier A — YOLO fires (confirm, vouch, learn, optionally refine)

1. **Association.** For each YOLO detection, predict its extremity points from keypoints 9, 10, 15, 16. Use them even at low confidence, down to 0.1, because the marker is the arbiter. Run **one global Hungarian** over (detections × 4 slots) × blobs with cost `dist / gate`.
   - `gate = max(4 px, g·H) + 0.5·L_streak`. Start with g ≈ 0.20 H; Phase 0a tunes it from the residual distribution.
   - A blob within the gate of two detections at a similar cost is **contested**: it counts as presence for both and is used for learning by neither.
2. **Vouching (pipeline level, before the tracker).** Add a fourth keep-reason, **MARKER**, to the scored gate `_crossval_motion_filter` (`pipeline.py:1498-1520`), which today keeps a detection on SKELETON, MOTION or TRACK. A static, weak-skeleton dancer fails all three today and is rejected. With ≥ 1 matched marker (≥ 2 for a new track) it is kept. Exclusion stays authoritative, since it is the operator's manual intent.
3. **Confirmation (tracker).** `marker_hits += n` and `_frames_since_marker = 0`. Optionally, credit warmup faster for a marker-confirmed track (the integral and the window credit, `config.py:761-789`); this is replay-gated.
4. **Learning.** Per slot, keep an offset `o_i = C − m_i`: an EMA over the last few YOLO frames, or the last frame only, to be decided on the Phase 0a data. Also keep the slot position and velocity from the blob chain, the last full constellation and H.
5. **Refinement (optional flag, off by default).** Replace keypoint *i* with the blob centroid and raise its confidence to ≥ 0.9. Because `_compute_centroid` would then change, this alters the `/centroid` and `/keypoints` values. Treat it as a separate, operator-confirmed behaviour change.

### 3.2 Tier B — YOLO gap (keep alive, position from the constellation)

1. **Slot prediction.** `last pos + chain velocity`, with a gate that grows with the gap. Run a global Hungarian over the free blobs, i.e. those not used in Tier A.
2. **Estimate.** `ĉ = mean_i∈S (m_i + o_i)` (the "offset vote"). With 2+ visible markers, use a median-of-votes variant to resist an outlier slot.
   - **n = 1:** use it only if the slot's chain has been continuous since the last YOLO frame, and blend 50/50 with the hold position. Short-gap p50 drops from 0.073 to 0.058 H (p90 0.16 vs 0.29).
   - **n = 0:** coast exactly as today.
3. **Kalman update** through a new measurement path, `apply_measurement(source="marker", R = σ²(n,k))`:
   - Take σ from the table above: Rayleigh σ ≈ p50/1.18 × H in tracker space.
   - Example: H_trk ≈ 175 px at imgsz 1280; n = 4, k ≤ 5 gives σ ≈ 6–9 px, so R ≈ 40–90 px², ≈ **20–45×** `TRACKER_MEASUREMENT_NOISE=2.0` (`config.py:160`). For n = 2, R ≈ 85–220 px² (40–110×). R grows with k, per the table.
   - **No velocity override**, unlike the motion bridge (`tracker.py:2952-2963`).
   - The filter arbitrates by itself. On slow scenes (wall climb) its prediction is already ~0.03–0.04 H and the marker update barely moves it. On fast swings (aerial) the marker term dominates after a few frames. A synthetic aerial gap takes today's hold error of 0.28 H at 10 frames down to 0.07–0.14 H.
4. **Bookkeeping.**
   - Set `time_since_update = 0`. **Do not** reset `_frames_since_skeleton`, `_last_yolo_wh`, the keypoints or the pose history; box-clamp and the RTS smoother rely on them meaning "real skeleton".
   - Update the smoothed centroid with an EMA, as the bridge does (`tracker.py:2986-2990`).
   - Marker-relayed tracks are added to `matched_trk`, so the motion bridge (`_lazy_bridge_with_motion`, `tracker.py:2239-2240`) skips them. **Markers take precedence over MOG2 blobs.**
5. **Frozen-ghost gate** (`tracker.py:3175-3184`). It must test `min(fss, fsm)`. Otherwise a static, marker-fed dancer whose skeleton is stale gets suppressed as a "frozen ghost", which is precisely the case markers exist to save.

### 3.3 Tier C — no YOLO at all (static and never-acquired dancers)

1. **Free constellations.** Single-linkage clusters of free blobs (not static, ≥ 2/3-frame persistent) within a body radius. **[HYP] Start at 1.3 H** (a spread aerial pose can put wrist and ankle ~1.3 H apart); the linkage threshold is ~0.8 H between nearest members. Confirm from Phase 0a pairwise-distance statistics.
2. **What separates a static dancer from a fixed glint:**
   - the blobs are **not in the Aim-time glint map**, because they appeared after the empty-stage calibration;
   - **≥ 2 blobs** in a body-plausible geometry (pairwise distances 0.05–1.3 H);
   - sub-pixel **sway** over seconds (a hanging body is never perfectly still) [HYP];
   - optional MOG2/frame-diff body support.
3. **Provisional marker-only track.** Spawn it after **≥ 1 s** of persistence and outside exclusion cells. Its initial centroid is the plain mean of the markers (bias 0.15–0.37 H), so it starts with a large R. It is **reported** after its confirmation window, or immediately when YOLO lands on it; YOLO then adopts it and learns the offsets.
4. **"Markers-required" mode** (opt-in, per show: "every performer wears markers"). A YOLO track that has had ≥ 2 s of skeleton frames with its extremities inside the frame and **never** a marker match is suppressed from the report. This is the strongest ghost killer, but it is risky while markers are occluded for long periods (curled poses), so it needs long windows and replay gating.

### 3.4 Two dancers, crossings, identity

- **Intermixing.** With the global Hungarian over all tracks' slots plus the **contested-blob rule**, a crossing degrades to today's coasting instead of inducing a swap. When every slot of a dancer is contested, that dancer coasts. Tier C clustering is suppressed within 1.5 H of any confirmed track.
- **Limbs crossing within one dancer** cause slot swaps. They hurt refinement, not the centroid (synthetic: 26 % of gaps, < 0.02 H of extra error).
- **Coded markers: not worth it now.** Identity swaps are not a stated pain (`ROADMAP.md` §3a), and coding by count or size is fragile under blur, bloom, merging and occlusion. If Phase 0a duo takes show marker-attributable swaps, the cheapest robust code is a **per-dancer harness pattern** (1 vs 2 vertical dots, 10 cm apart). The harness is rigid and blurs least. Avoid coding the extremities.

### 3.5 Failure modes

| Failure | Effect | Mitigation |
|---|---|---|
| Fast-limb streak drops below T | Slot lost for a few frames | Adaptive T, ≤ 25 ms exposure, illuminator near the lens; slot chain tolerates ≤ 3-frame gaps |
| Self-occlusion (curled, back to camera) | n ↓ (most risky n = 1) | Cuffs (cylindrical, visible from most angles) + **harness front/back** marker |
| Two markers merge (hands together) | n ↓ by one, merged centroid | Area ≫ expected → mark "merged", vote with both slots' offsets at half weight |
| Hardware glint mistaken for a marker | Wrong offset vote | Pelvis-zone blobs never fill extremity slots; persistence |
| Wrong offsets after a big pose change during a long gap | Error grows ~0.10–0.14 H at 1 s | R grows with k; offsets re-learn on the next YOLO frame |
| Marker-only ghost (crew clothing, bystander) | False dancer | ≥ 2-blob constellation, ≥ 1 s persistence, exclusion veto, `MAX_PERSONS` cap |

---

## 4. Tracker interface needed (to coordinate with the continuity stream)

**New module (pure, no tracker knowledge):**

```python
@dataclass
class MarkerBlob:                 # ROI-local original px, MotionBlob-compatible (centroid, bbox, area)
    centroid: np.ndarray; bbox: np.ndarray; area: int
    peak: int; n_sat: int; elong: float; angle: float
    chain_id: int; chain_age: int; static: bool

class MarkerDetector:
    def detect(self, gray: np.ndarray) -> list[MarkerBlob]          # runs in the motion worker
    threshold: int; glint_map: Optional[np.ndarray]
    def begin_glint_calibration(self) -> None; def finish_glint_calibration(self) -> GlintResult
```

**What the tracker must expose or accept (shared seam):**

1. `DancerTracker.update(detections, frame_number=None, motion_detector=None, motion_blobs=None, markers=None)`. Markers arrive already mapped to tracker space through `space.blobs_to_tracker`. **Contract:** `markers is None or []` means **no state mutation and no log events**. Goldens and the markerless path stay byte-identical even with the feature enabled.
2. **A measurement-source abstraction.** It replaces the implicit "all-zero confidence ⇒ motion source ⇒ R×4" rule (`tracker.py:1991-1997`): `DancerTrack.apply_measurement(xy, *, source: {'yolo','marker','motion_blob','bridge'}, r_mult, integral_credit, window_credit, resets_skeleton_age: bool)`. The continuity stream probably wants the same thing for its own bridge/coast work, so define it **once**.
3. **Per-track state:** `marker_slots[4]` (pos, vel, offset EMA, chain_id, last_seen), `_frames_since_marker`, `marker_hits`, `marker_confirmed`, `last_constellation`.
4. **Hook order inside `update()`** (`tracker.py:2207-2246`):
   - `_run_matching_phase` (YOLO keeps primacy)
   - → `_associate_markers_to_matched` (Tier A)
   - → `_resolve_unmatched_detections`
   - → `_relay_unmatched_with_markers` (Tier B; added to `matched_trk`)
   - → `_lazy_bridge_with_motion` (skips marker-relayed tracks)
   - → `_acquire_from_marker_constellations` (Tier C)
   - → aging / lifecycle → `_collect_confirmed_tracks`, with the frozen gate using `min(fss, fsm)` and the optional markers-required gate.

   One subtlety: cold-blob synthetic detections are fused *into* `detections` (`tracker.py:2214-2215`) and can win the main assignment. For a track whose only match this frame is a zero-confidence synthetic, the marker measurement should replace the blob measurement. Implement this by deferring synthetic updates through the existing `defer_update`/`PendingTrackUpdate` mechanism (`tracker.py:1947-1956`) until after the marker relay.
5. **Pipeline-level hook:** `_crossval_motion_filter` gets `marker_support` (MARKER keep-reason). Tier A association therefore has to be computable **before** the tracker. Put it in a small pure helper shared by the pipeline and the tracker.
6. **Output-only `ScaledTrack` fields** (`pipeline.py:127`, `_unscale_letterbox` `:1325-1401`): `source`, `n_markers`, `frames_since_marker`, `marker_points` (original space). The RTS smoother (`pipeline.py:878-929`) may use `source` as a quality input (a marker frame is not a skeleton anchor but is better than a coast). That is the continuity stream's call.
7. **Logger events** for replays: `MARKER_ASSOC`, `MARKER_RELAY`, `MARKER_CONTESTED`, `MARKER_ACQUIRE`, `MARKER_GLINT_LEARNED`.

**A side finding for the continuity stream [FACT, synthetic].** Constant-velocity extrapolation with 0.9 decay (a proxy for the KF coast in `tracker.py:372-374`) is **worse than holding the last position**:

- aerial pendulum, gaps ≥ 10 frames: p50 0.338 vs 0.283 H, p90 0.773 vs 0.708 H;
- wall climbs, already at 5 frames: 0.071 vs 0.028 H; at 10 frames: 0.104 vs 0.038 H.

Velocity estimated from jittery YOLO centroids is mostly noise. This is worth a replay-gated look at coasting with near-zero velocity.

---

## 5. OSC options

- **Default: contract unchanged** (`OSC_CONTRACT.md` §A/§B). Markers only change *values*: centroids exist through gaps, and dancers appear that today are never reported. Keypoint refinement (§3.1 step 5) would change `/keypoints` and `/centroid` values, so it stays behind its own flag.
- **Optional additive messages (NEED OPERATOR CONFIRMATION, plus a new `OSC_CONTRACT.md` §D):** a config flag `osc_marker_extras` (default **off**) enables:
  - `/walldance/dancer/source [id, src, n_markers]`, with `src` ∈ {0 skeleton, 1 marker, 2 motion, 3 coast}. This is useful beyond markers: a consumer can fade or soften visuals while a dancer is coasting.
  - `/walldance/dancer/markers [id, x0,y0,v0, x1,y1,v1, x2,y2,v2, x3,y3,v3]`: slots LW, RW, LA, RA, normalized like `/keypoints`; `v` is visibility 0/1. **L/R may be wrong** for unlabelled markers.
- **Caveat:** each message adds n datagrams per frame (no bundling; P-5 is parked). Keep both opt-in.

---

## 6. Phase 0a protocol for the incoming footage

### 6.1 Setup sheet (fill in once per session)

- Venue, camera→stage distance (m), lens, crop/ratio.
- Illuminator model and power, **offset from the lens axis (cm)**, aim.
- Exposure µs / gain dB after Aim; AE/AG **off**.
- UserSet1 contents.
- Marker material (brand/type, glass bead vs prismatic, silver vs "IR glint" black), size, attachment (cuff vs patch), placement photos.
- Dancer heights; harness hardware (shiny or taped).

### 6.2 Takes (≈ 2 h; project `markers-0a-<venue>`; one take per slot, history allowed)

| Slot | Take | Duration | Purpose → metric |
|---|---|---|---|
| 1 | **Empty stage** at show exposure (rope, harness and rigging present), then a crew member *without* markers walking through | 60 s + 30 s | Glint floor, glint map, natural moving glints → M2, M3 |
| 2 | **Static reference:** dancer facing camera / ¾ / side / back (0, 45, 90, 135, 180°) at **near, mid and far** positions (stage front/back, or wall bottom/top); 5 s each. Optionally a **marker test card** (1, 2, 3, 5, 8 cm, both materials) at near and far | ~3 min | Size/peak vs distance and angle; smallest reliable size → M3, M4 |
| 3 | **Static holds** on wall/rope: inverted, hanging, curled; include a 10 s fully-still hold | 60 s | Static acquisition (Tier C) → M5, M8 |
| 4 | **Representative aerial choreography** | 2–3 min | Main recall/precision, occlusion gaps → M1, M5, M6 |
| 5 | **Fast moves:** kicks, arm swings, spins, drops | 60 s | Streaks, saturation under blur → M1, M3 |
| 6 | **Self-occlusion:** arms behind the back, side-on, legs wrapped on the rope, back to camera | 60 s | Gap lengths, n-visible distribution → M5 |
| 7 | **Two dancers** (if possible): side by side, crossing paths at 1 m and in contact, partnering | 2 min | Intermixing, contested blobs, swaps → M7 |
| 8 | **Exposure ladder:** take-4-like movement at show exposure ×1, ×0.5, ×0.25 (gain fixed); plus the **usual illuminator mount** vs **against the lens** | 4×20 s | Saturation margin vs blur and observation angle → M3 |
| 9 | **Visible-light invisibility** (not a WallDance recording): phone video from audience positions, **including next to the projector or front lights**, with show lighting/projection on; plus a phone-flash photo | – | M9 |

### 6.3 Metrics and go/no-go

| ID | Metric (from harness §7) | Go | No-go / rethink |
|---|---|---|---|
| M1 | Per-marker recall: unoccluded marker-frames with a blob within the gate (pseudo-GT = YOLO kpt proxy + operator spot-check) | ≥ 0.95 overall; ≥ 0.90 in take 5 (fast); at the **farthest show distance** | < 0.85 at the far distance → larger markers / illuminator change |
| M2 | Precision: empty-stage moving false positives per frame after the glint map; non-marker blobs attached to a dancer as a % of marker blobs | ≤ 0.01 / frame; ≤ 5 % | > 0.05 / frame |
| M3 | Saturation margin: % of associated blobs with peak = 255 and `n_sat` ≥ 2; empty-stage `max_natural` vs T | ≥ 95 % saturated at show exposure; `max_natural` ≤ 0.6·T | Streaks unsaturated at the show exposure → exposure/illuminator constraint |
| M4 | Blob area vs distance; smallest marker size with area ≥ 4 px at the far distance | Documented table; size choice | – |
| M5 | Per-dancer frames with ≥ 1 / ≥ 2 markers visible; length of runs with 0 markers | ≥ 1 visible on ≥ 90 % of aerial dancer-frames; 0-marker runs p50 ≤ 10, p90 ≤ 20 frames | ≥ 1 visible on < 75 % → add a harness front/back marker |
| M6 | Centroid error vs the YOLO centroid (offset vote, unlabelled) by n and gap k (the `synth_markers.py` method on *real* blobs), **reported next to the hold baseline of the same take** | n ≥ 2, k ≤ 20: median ≤ 0.10 H and p90 ≤ 0.30 H; at k ≥ 10, not worse than hold | median > 0.15 H, or worse than hold at k ≥ 10 on a fast take |
| M7 | Duo: contested-blob rate; slot assignments to the wrong dancer | 0 wrong-dancer slot uses after the contested rule | Any systematic cross-assignment |
| M8 | **End-to-end (Phase-2 gate):** replay with markers on vs off on takes 3/4/7 — drop rate, longest drop, ghost rate, id count, static acquisition latency | drop rate −50 %, longest drop −50 %, ghost rate not up, id count not up, static dancer reported < 2 s | Any ghost increase |
| M9 | Visible invisibility (director/operator judgement) | Invisible from all audience positions **incl. near the projector/front-light axis** | Visible → IR-only ("glint") retro material, or move the projector/front light |

**Phase 0a go** = M1, M2, M3, M5, M6 and M9 pass. M7 is required only if duos are in the show; M8 is the Phase 2 gate.

---

## 7. Eval harness spec (+ prototype results)

### 7.1 Spec

Extend `tmp_analysis/marker_spike.py` into `tmp_analysis/marker_eval.py`, then promote it to `tests/marker_eval.py` once it is used in gates. The prototype lives at `tmp_analysis/audit-2026-10/markers/marker_eval.py`.

- **Inputs:** `--project/--slot` or `--video`; window `--start/--frames/--stride`; `--thresholds`; `--primary`; optional `--roi` (taken from the project config); `--poses` = a pose source, one of:
  1. a GPU/TRT detect cache (`tests/cache/*.pkl`; the dets and the raw gray are already in it);
  2. a live pipeline run (`pose_dump.py` style through `replay._build_processor`);
  3. none, for the empty-stage floor.
- **Per frame (JSONL):** blobs with all §2.2 features + chain id + static flag; tracks (id, keypoints, confidence, fss, centroid); association (blob → track/slot, residual, contested); and, during skeleton gaps, the Tier-B estimate.
- **Modes:**
  - `floor`: brightness tail, glint chains fixed/moving (what the prototype does today);
  - `assoc`: M1/M2/M7 + residual histogram → gate;
  - `centroid`: M6 on real blobs (port of `synth_markers.py`);
  - `occlusion`: M5;
  - `sweep`: T × gate × persistence grid → PR curves;
  - `synth`: inject rendered markers (saturated discs or streaks along the keypoint velocity) onto marker-less recordings at YOLO keypoints, giving pre-footage tests of the detector and fusion.
- **Outputs:** `summary.json` (the tables above); **contact sheets** by distance bin and per take (brightest candidates and per-slot crops, the existing `gen_gt_sheets.py` style, so the operator can verify pseudo-GT quickly); an optional overlay video (reuse `tests/overlay.py` style).
- **Ground truth:** YOLO-keypoint proxy on skeleton frames plus operator verification on sheets (20-frame stride), as in the corpus GT method. A small hand-clicked set (~200 frames) on takes 5–7 calibrates the proxy.

### 7.2 Prototype results on marker-less footage (this box) [FACT]

| Recording (frames sampled) | Scene mean | Natural max (median / max) | 99.99th pct (median / max) | T=100: frames ≥ 1 candidate | T ≥ 160 |
|---|---|---|---|---|---|
| whitebg2 s3, floor (1,935 @ stride 5) | 5.1 | 22 / 47 | 15 / 17 | 0 % | 0 % |
| whitebg2 s4, aerial (1,696 @ stride 3) | 5.1 | 22 / 41 | 15 / 16 | 0 % | 0 % |
| tango-H s8, wall climb (1,504) | 13.8 | 41 / 57 | 34 / 44 | 0 % | 0 % |
| tango-H2 s9, wall climb, AE drifting (883 @ stride 2) | 16.8 | 48 / **113** | 41 / 96 | **8.2 %** (15 moving chains: costume/wall at the brighter exposure) | 0 % |
| default s6, curtain stage, 4 people (1,023) | 5.4 | 126 / 255 | 88 / 93 | 88.7 % (a lit panel: one fixed object read as jittering chains) | **0.1 %**: 1 frame, one 39 px saturated clothing glint on a walker |

- **Headroom** (255 / natural max) is **2.3–6×**. Aim's servo target is mean 70 vs these 5–17, about 4–14× brighter. On today's rig the servo likely tops out at its gain limit (≈1.2× the corpus setting). With more IR (P1.3) it will approach that target, so **separability must be re-measured at the servo's output exposure**.
- A fixed bright object yields "moving" chains through centroid jitter of 5–14 px. Static classification must use area/shape and a glint map, not just a 4 px drift test.
- `kxkm*` recordings are colour MJPG at 1920×1080 / 3840×2160 (OpenCV/external cameras, no BP850 IR filter), so not the IDS rig. In one frame each: mean 60–120, **12–19 % of pixels ≥ 160, 1,071–1,547 components ≥ 160**. This is a useful negative control. A brightness-threshold marker detector only works with the **850 nm band-pass + dark scene + IR illuminator** regime; under visible stage light it is hopeless. The full scan was stopped because it was impractically slow on this many components.
- Synthetic-study and cost results are in §3.0 and §2.4.

---

## 8. Phased build plan

Every behaviour change is replay-gated. **G0** (applies to all phases): goldens byte-identical with markers **disabled**, *and* with markers **enabled** on marker-less footage. The second part proves the "no blobs ⇒ no mutation" contract. The 12-scenario scores must not change.

| ID | Item | Phase | Effort | Before footage? | Gate |
|---|---|---|---|---|---|
| **MRK-0** | **Recorder fidelity:** write exposure_us, gain_db, AE/AG flags, pixel format, ROI, crop ratio and app version into `.meta` (+ start/stop timestamps); **drain the encoder queue on stop**; log queue-overflow drops into `.meta`. Plus the shoot brief (§1.5/§6) to Thomas | 0a | **S** | **Yes — ideally before the shoot** | Unit test on `.meta`; recorder-only, so goldens are untouched |
| MRK-1 | Eval harness `tmp_analysis/marker_eval.py` (`floor`/`assoc`/`centroid`/`occlusion`/`sweep`, contact sheets) from the prototype | 0a | S–M | Yes | Runs on the corpus; reproduces §7.2 |
| MRK-2 | Synthetic study → library: the offset-vote estimator + R(n,k) table, extended to all corpus scenes; **marker injection** renderer (discs/streaks at YOLO keypoints, interpolated through gaps) for pre-footage end-to-end tests | 0a | S–M | Yes | Numbers in §3.0 reproducible |
| MRK-3 | `core/marker_detector.py` + motion-worker hook + `blobs_to_tracker` mapping; disabled by default (`markers_enabled=false`) | 1 | M | Yes (validated on injected markers) | G0; cost p95 ≤ 2 ms on prod; M1/M2 on Phase-0a takes |
| MRK-4 | Glint map + `T_marker` auto-calibration in Aim; Calib2 validation (gate, sizes); `calibration_state` provenance | 1/3 | M | Partly (needs footage to tune) | Aim-derived T within ±10 % of the hand-tuned T on Phase-0a takes; flow tests |
| MRK-5 | Tier A: association helper + MARKER keep-reason at crossval + track confirmation + offset learning (refinement behind its own flag) | 2 | M | Skeleton yes; tuning after | G0; M7; static-dancer take: detection kept |
| MRK-6 | Tier B: slot tracking + offset vote + `apply_measurement(source="marker")` + frozen-gate `min(fss, fsm)` + bridge precedence | 2 | M–L | Skeleton yes (injected); tuning after | **M8** on takes 3/4/7 + injected corpus; ghost rate not up |
| MRK-7 | Tier C: marker-only provisional acquisition + exclusion veto (+ markers-required mode, off) | 2 | M | After | Static-hold take reported < 2 s; empty/crew takes: 0 false dancers |
| MRK-8 | OSC extras (`/source`, `/markers`), off by default; `OSC_CONTRACT.md` §D | 2 | S | Yes | **Operator confirmation**; contract test |
| MRK-9 | UX: marker overlay in the preview (circles coloured by track), ⑤ Verify readiness row ("markers seen 4/4 · glint spots masked · T=…"), separability warning | 3 | S–M | Partly | App smoke |
| MRK-10 | (Deferred) coded harness pattern for identity | – | L | No | Only if M7 shows marker-attributable swaps |

Interface work (§4, the measurement-source abstraction) should be co-designed with the continuity stream **before** MRK-5/6. Also prefer **TRT detect caches** for every gate (Track P); markers replay from the cached gray.

---

## 9. Risks

1. **Illuminator geometry and exposure (highest).** An off-axis illuminator combined with ~50 ms exposure can leave fast markers unsaturated (§1.6). Mitigation: shoot brief, ladder take, adaptive T.
2. **8-bit headroom at the calibrated exposure.** Natural highlights approach 255 when Aim brightens the scene ×4–14. Mitigation: T calibrated at Aim, separability warning; worst case, mask bright fixtures or lower the gain (a trade-off against YOLO).
3. **Projection / front light makes markers visible** to part of the audience (retro sends light back toward its source). Mitigation: M9 check near the projector; IR-only retro material [HYP: covert "IR glint" tapes exist; verify that they retroreflect at 850 nm and look dark in visible light].
4. **Self-occlusion in aerial work** (curled and back-to-camera poses). Mitigation: cuffs rather than patches; harness front + back marker.
5. **YOLO keypoint noise** (p90 0.37 H/frame extremity displacement) makes Tier-A gates wide, and contested blobs rise with two dancers. Mitigation: gates tuned on Phase 0a residuals; contested rule.
6. **Unrecorded camera state** makes Phase-0a footage hard to interpret. Mitigation: MRK-0 or a manual take sheet.
7. **Scope creep into the tracker core** (a 3.6 k-line, load-bearing file). Mitigation: a single measurement-source seam shared with the continuity stream; G0 byte-identity contract.
8. **On-device processing** (UserSet1 hot-pixel correction) erases 1–2 px far markers. Mitigation: Q1, test-card take.
9. **Recorder frame drops** under FFV1 load on long takes (queue 300). Mitigation: MRK-0 logs drops; keep takes ≤ 3 min.

---

## 10. Questions for Thomas / operator

**Before the shoot**

1. What does **UserSet1** configure (in-camera gamma/LUT, hot-pixel correction, black level, digital gain)? Can it stay as is for the shoot?
2. Will the test footage be recorded **at show exposure with AE/AG off** (after Aim), with settings noted per take? Can **MRK-0** (`.meta` with exposure/gain + tail drain) land before the shoot?
3. **Illuminator mounting:** can it sit right next to the lens (≤ 10 cm)? What is the usual offset and the camera→stage distance at the test venue?
4. **Marker spec:** cuffs or patches? Size, material (glass-bead silver, prismatic, IR-only black)? Can we add **one harness marker (front, and back if possible)**? The synthetic study says a single harness marker is worth as much as all four extremities.
5. Is there a **projector or front light** near the audience during the show? Its position decides whether markers stay invisible (M9).

**After the shoot**

6. Are duos (crossings, partnering) part of the show? This decides whether M7 is required.
7. Can "markers-required" mode be the norm (every performer marked every show)? It is the strongest ghost killer.
8. For OSC, is a `/walldance/dancer/source` quality flag useful to the TouchDesigner patch? Are marker points wanted? Both are optional and off by default.
9. Is keypoint refinement from markers acceptable? It changes the `/keypoints` and `/centroid` values on skeleton frames.
