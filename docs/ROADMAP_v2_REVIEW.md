# ROADMAP v2 — critical review and revised plan

**Date:** 2026-10-06 (evening) · **Status:** 🟡 for Thomas's review · **Scope:** what remains of
[ROADMAP_v2_DRAFT.md](ROADMAP_v2_DRAFT.md) after the 2026-10-06 work on `remote-ops` (`da66a9a`), challenged
with replays run today on dev37.
**Evidence base:** the audit ([AUDIT_2026-10.md](AUDIT_2026-10.md), [audit-2026-10/](audit-2026-10/)), the
2026-10-06 commits (CONT-6 identity slots + One-Euro + belt fallback, CONT-1/10, TEST-1, PERF pass, MRK-0/1/2,
remote ops, launcher channel), and the measurements in §1 (scripts in `tmp_analysis/review-2026-10/`).

**Field targets (Thomas, 2026-10-06):** this week a hangar wall at ~25 m, 2 dancers hanging, 2 × PIR130 850 nm
projectors (60°, ~36 W); production at 30–40 m, bigger scene, smaller dancers, less light, portrait camera.
Default yolo11x-pose@1280. Output = the centroid to TouchDesigner, which drops its video when an id vanishes.
Goals in order: continuous centroid · stable but fast · no ghosts · lighter load (TD shares the laptop) · use the IR
belt · easy on-site settings.

---

## 0. Summary

1. **CONT-6 does what it was built for.** On the 4.3 min aerial take the OSC stream goes from 94.5 % coverage,
   15 ids and 98 holes to **99.7 %, 1 id, 1 hole of 0.76 s**; on the duo and wall-hang takes coverage gains
   13–26 points with no extra ids. TouchDesigner's "video drops when an id vanishes" problem is addressed at the
   output. Where the tracker starves (the floor take, 57.7 % coverage) the layer lifts it to 75.6 % but a
   **24 s hole remains**: 743 uncovered frames have a track on the dancer stuck in warm-up. **CONT-4a stays.**
2. **It hides id churn, not position errors.** Coasting is a hole-plug, not tracking: on a fast swing the held
   point is 0.27 h off after 0.5 s and ~0.8 h off after 1 s (§1.4). About 2 % of "live" points sit on a blob-fed
   track that has drifted off the body (§1.3), and on the textured wall (tango-H2) the layer coasts 52 % of the
   time and binds a second slot to wall stains. **Ghost control and the tracker's YOLO-vs-motion merge (CONT-2/3)
   are still the real fixes; the slot layer is the safety net under them.**
3. **The belt contributed nothing on the 2026-10-05 takes** (belt on = belt off, state `belt` on < 0.2 % of frames,
   one 33-frame false hold on an off-axis take). At ~190 px dancers the belt peaked at ~30 DN, below the 60 DN
   detector floor; only dancers ≥ 350 px lit it. The IR budget says a 120 DN belt at 25 ms needs **×3.6 of today's
   light at 25 m and ×9 at 40 m**, and that is optimistic. **Tonight's projector-offset take (slot 8 vs 4) decides
   whether the belt is a real second source or an opportunistic one** (§1.6, D19).
4. **Lag is the next output-quality lever, and it is cheap.** Feeding the One-Euro with the raw Kalman centroid
   while still binding on the smoothed one removes 30–60 ms of lag on fast moves and cuts the fast-move error from
   0.135 to 0.107 h, for ~1.6× the jitter at rest (still below the pre-CONT-6 stream). Output-only, a few lines
   (§1.5, D18).
5. **Perf on the laptop is a GPU-thermal story, not a CPU story.** x@1280 is 21 ms of GPU per 50 ms frame on the
   RTX 5080 Laptop (42 % duty, 46.9 fps engine-only), the GPU sat at 84–87 °C and 1.6–2.0 GHz this morning, and
   TD renders on the same GPU. Headroom at x@1280 exists on paper; it must be **measured with TD running** (slot 9
   tonight). x@960 / l@1280 halve the GPU time if accuracy allows. On the CPU side PoseRunner is worth 2.8 ms a
   frame, and the tracker costs 15 ms p50 / 26 ms p95 at two dancers on a textured wall on dev37 (≈ 6 / 10 ms on
   the laptop), mostly motion-bridge connected-component queries (§1.7).
6. **PoseRunner is statically compatible with the laptop's ultralytics 8.4.63** (same predictor members and
   signatures, identical `LoadTensor` check); the gate stays a runtime A/B on the laptop (`WD_POSE_RUNNER=0`),
   detections byte-equal over a take (§1.8).
7. **Production at 30–40 m is an optics + IR problem first.** The 8 mm lens gives 117 px at 40 m (floor 110); the
   body needs ×2–6 of today's IR at 40 m, the belt ×9; a full 1528 px ROI letterboxed into 1280 leaves 98 net px,
   so the ROI must be tight or imgsz 1536 (61 % GPU duty). Portrait changes coverage, not dancer pixels; a
   rectangular engine only pays if the production ROI is tall and narrow (§4).
8. **Release path:** `remote-ops` touches neither `pyproject.toml` nor the installers, so its push is a plain
   fast-forward with no reinstall. Ship it first; ship `pinned-install` second, in a supervised window (§5).
9. **Of the remaining roadmap:** CONT-2/9 and CONT-3 stay (before production); CONT-4 is half-superseded by the
   slot layer's hidden-follow + coast and stays only for warm-up starvation in low light; CONT-5/11, SEAM-1/2 and
   the extremity-marker tiers MRK-5/6 become nice-to-have; MRK-4 (static glint map) and MRK-7 (belt-only hold of a
   static dancer) stay, gated on tonight's belt result; DOC consolidation becomes MODE_EMPLOI first (§3, §6).

---

## 1. Measurements (dev37, TRT x@1280 engines built here, `remote-ops` @ `da66a9a`)

Reproduce: `tmp_analysis/review-2026-10/run_replays.sh` (replays, slots ON), then `summarize.py`,
`slot_sweep.py`, `gap_inject.py`, `diagnose.py`, `variants2.py`, `variant_drift.py` on the timelines, and
`run_belt_eval.sh` for the belt. All offline sweeps re-run only the slot layer (`tests/slot_replay.py`), which is
deterministic and takes seconds, so every number below is a one-command re-check. **Caveat:** dev37's engines
are a TRT 11.3 / onnxruntime-FP16 build; the laptop's are TRT 11.0 built by ultralytics 8.4.63. Numbers are a
dev reference; the laptop replays (`wdremote --slot dev replay … --trt --score`) are the gate.

### 1.1 Tracker stream vs emitted (slot) stream, per scenario

Pinned manifests (`tests/scenarios/`), pseudo-GT = YOLO detections (C8 reference). `cov` C1 coverage · `onD`
share of reference dancer positions with an emitted point within 0.75 h over **all** frames · `spat` C8 spatial
validity · `coast` share of emitted frames in state *coasting* · `jit` RMS 2nd-difference at rest, % of h · `lag`
on fast frames vs the raw YOLO centroid · `fastE` emitted→YOLO distance on fast frames (h) · `sw` pseudo-GT id
switches.

| take (frames, N) | stream | cov | onD | spat | ids | holes | longest | coast | jit % | lag ms | fastE | sw |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hangar-aerial-full (5088, 1) | tracker | 0.945 | 0.959 | 0.988 | 15 | 98 | 1.57 s | – | 2.96 | 43 | 0.111 | 31 |
| | **emitted** | **0.997** | 0.964 | **0.966** | **1** | **1** | 0.76 s | 3.1 % | 1.24 | 61 | 0.135 | 0 |
| white-duo (400, 2) | tracker | 0.752 | 0.790 | 0.848 | 11 | 13 | 3.43 s | – | 4.52 | 48 | 0.120 | 11 |
| | emitted | 0.884 | **0.737** | **0.758** | 2 | 1 | 3.53 s | **24 %** | 1.89 | 54 | 0.141 | 7 |
| texture-duo (400, 2) | tracker | 0.644 | 0.906 | 0.906 | 7 | 21 | 3.63 s | – | 1.35 | – | 0.133 | 12 |
| | emitted | 0.904 | 0.906 | 0.906 | 2 | 1 | 3.63 s | **31 %** | 0.75 | – | 0.153 | 10 |
| texture-wallhang-clip (400, 1) | tracker | 0.608 | 0.895 | 1.000 | 1 | 30 | 3.03 s | – | 4.31 | – | – | 0 |
| | emitted | 0.839 | 0.947 | 1.000 | 1 | 1 | 3.13 s | 8.7 % | 2.21 | – | – | 0 |
| texture-aerial (600, 1) | tracker | 0.952 | 0.859 | 0.897 | 3 | 7 | 0.50 s | – | 1.47 | 60 | 0.130 | 4 |
| | emitted | 0.980 | **0.830** | **0.855** | 1 | 1 | 0.61 s | 6.6 % | 0.66 | 86 | 0.151 | 0 |
| blur-runner (400, 1→2) | tracker | 0.965 | 0.981 | 0.981 | 2 | 9 | 0.20 s | – | 1.30 | 33 | 0.061 | 0 |
| | emitted | 0.998 | 0.976 | 0.978 | 2 | 1 | 0.05 s | 3.6 % | 0.49 | 58 | 0.067 | 0 |
| hangar-floor-full (9674, 1) | tracker | 0.577 | 0.785 | 1.000 | 3 | 69 | **29.9 s** | – | 1.88 | 59 | 0.082 | 0 |
| | emitted | 0.756 | 0.858 | 0.972 | 1 | 13 | **24.5 s** | **32 %** | 0.65 | 85 | 0.100 | 0 |
| tango-H2 s9 (1766, textured, 1 dancer, no manifest) | tracker | – | – | – | 16 | – | – | – | 2.02 | – | 0.157 | 24 |
| | emitted (cap 2) | – | – | – | 2 | – | – | **52 %** | 4.00 | – | 0.152 | 12 |
| tango-H s8 (1504, textured) | tracker / emitted | – | – | – | 4 / 1 | | | | 7 % | 2.0 / 0.51 | | | 1 / 0 |

Readings:
- **Coverage and id count are solved at the output** on every take. The long-span class-A line (coverage ≥ 0.98,
  0 holes ≥ 1 s, ≤ 1.5 ids) passes on the aerial take for the first time.
- **C8 spatial validity falls below the 0.97 line on the aerial take (0.966) and drops on white-duo and
  texture-aerial with slots on.** The layer keeps emitting while the point is not on the body: coasting rows and
  live rows on drifted tracks (§1.3). This is the price of continuity and it is small on the white wall; on
  textured walls it is not (tango-H2: 52 % coasting, a second slot on stains, 68 live points on no detection).
- **Coasting share is the honest number for TD**: 3 % on the aerial take, 24–31 % on the duo takes, 52 % on the
  textured wall. A quarter of the duo stream is a held point.

### 1.2 Where the remaining holes come from (`diagnose.py`)

Uncovered reference dancer-frames with slots on, by cause:

| take | uncovered | reported track on the dancer, slot not bound | internal track on the dancer, still in warm-up | no tracker track at all |
|---|---|---|---|---|
| hangar-aerial-full | 120 (3.6 %) | 53 | 48 | 19 |
| white-duo | 104 (26 %) | 24 | **73** | 7 |
| texture-aerial | 69 (17 %) | 13 | **52** | 4 |
| blur-runner | 13 (2.4 %) | 3 | 10 | 0 |
| hangar-floor-full | 786 (14.2 %) | 14 | **743** | 29 |

**Tracker warm-up is now the largest cause of uncovered frames** (the slot layer can only bind reported tracks;
a re-born track needs ~0.75 s). The second cause is an established track the layer refused (its slot was coasting
elsewhere, or the duplicate/entry rules). Blindness is a minority. A naive fix, binding warm-up tracks inside the
slot gate, was tested offline and **rejected**: it drops on-dancer rates everywhere (aerial 0.967 → 0.92–0.95,
white-duo 0.74 → 0.58–0.63, wall-hang 0.95 → 0.68), because warm-up tracks die and leave the slot dangling. The
right fix is tracker-side (CONT-4a continuation ≠ birth, `tracker_intermittent_confirm` per scene via known-N).

### 1.3 "Live" points off the dancer (ghosts taking a slot?)

Live emitted points farther than 0.75 h from every reference dancer, with references present:

| take | off-dancer live points | of which on *some* YOLO detection (a 3rd/low-conf body) | on **no** detection | bound track had a fresh skeleton |
|---|---|---|---|---|
| hangar-aerial-full | 90 / 4901 | 6 | 84 | **0** |
| white-duo | 147 / 516 | 60 | 87 | **0** |
| texture-aerial | 43 / 540 | 1 | 42 | **0** |
| blur-runner | 59 / 584 | 13 | 46 | **0** |

**Every off-body "live" point is a blob-fed track (no skeleton this frame) that has drifted.** No slot bound to a
YOLO-confirmed ghost spot on the white-wall takes: the establishment rules (confirmed + streak + hits + recent
skeleton + exclusion zone) hold there. The state `live` therefore overstates confidence on 2–17 % of live frames.
A drift guard (do not follow a skeleton-stale track that moved > 0.5–0.75 h from its last skeleton position) was
tested offline: ≤ +0.4 pt on-dancer, neutral or negative elsewhere — **not worth shipping**. The root is the
tracker's YOLO-vs-motion merge (audit §2 d, CONT-3), which CONT-6 does not touch. On the textured wall the
anti-ghost rules do fail (tango-H2: 16 internal ids, two slots busy, 12 switches), as the audit predicted:
**exclusion painting / CONT-2 is mandatory on textured venues**.

### 1.4 Hold vs drift during a loss (`gap_inject.py`, aerial take)

Synthetic losses of k frames injected into the slot layer at 305 anchors (fast = YOLO centroid speed > 0.5 h/s
over the previous 5 frames). Error of the coasted output vs the tracker's fresh-skeleton centroid at t0+k,
median / p90 in dancer heights:

| velocity decay τ | fast k=5 (0.25 s) | fast k=10 (0.5 s) | fast k=20 (1 s) | slow k=5 | slow k=10 | slow k=20 |
|---|---|---|---|---|---|---|
| hold (τ = 0) | 0.231 / 0.51 | 0.444 / 1.05 | 0.857 / 2.67 | **0.064** / 0.19 | **0.170** / 0.59 | 0.567 / 1.72 |
| **τ = 0.25 s (shipped)** | **0.134** / 0.29 | **0.274** / 0.73 | 0.783 / 2.23 | 0.072 / 0.25 | 0.189 / 0.53 | 0.578 / 1.65 |
| τ = 0.5 s | 0.120 / 0.25 | 0.290 / 0.66 | 0.874 / 1.85 | 0.075 / 0.26 | 0.198 / 0.50 | 0.520 / 1.61 |
| constant velocity | 0.131 / 0.28 | 0.397 / 0.74 | 1.168 / 1.89 | 0.073 / 0.27 | 0.238 / 0.53 | 0.660 / 1.69 |

- The shipped decay is a good compromise: ~40 % better than a pure hold on fast swings at 0.25–0.5 s, ~10 % worse
  than a hold on slow phases. The audit's "hold beats velocity" verdict was about the Kalman velocity; the slot's
  EMA velocity on the smoothed centroid is better behaved.
- **Beyond ~0.5 s nothing coasts well**: at 1 s the point is ~0.8 h off on a fast move and ~0.55 h off on a slow
  one. `coast_s = 2 s` is right for TD's continuity (coverage: 0.5 s → 0.989, 1 s → 0.994, 2 s → 0.997 on the
  aerial take; 0.80 → 0.85 → 0.88 on white-duo; nothing beyond 2 s), but what TD receives in the second half of
  a 2 s coast is a frozen point, not a dancer. The floor take is the exception that proves it: coverage keeps
  rising with the coast (0.5 s → 0.628, 2 s → 0.756, 5 s → 0.815) while spatial validity falls (0.997 → 0.953),
  because a starving tracker leaves the slot nothing but a stale point to hold. A `/dancer/state` consumer can fade it; a blind consumer cannot.
  The second measurement source (belt) or fewer tracker losses (CONT-2/3) is what fixes the *position*.

### 1.5 One-Euro defaults and the lag budget (`slot_sweep.py`, `variants2.py`)

| variant (aerial take) | jitter at rest % h | lag on fast moves | fast error (h) | on-dancer |
|---|---|---|---|---|
| tracker EMA stream (pre-CONT-6) | 2.96 | 43 ms | 0.111 | 0.959 |
| slots, Stability 0 | 1.76 | 53 ms | 0.126 | 0.967 |
| slots, Stability 0.25 | 1.49 | 57 ms | 0.130 | 0.967 |
| **slots, Stability 0.5 (default)** | **1.26** | **61 ms** | 0.135 | 0.967 |
| slots, Stability 0.75 | 1.07 | 67 ms | 0.141 | 0.966 |
| slots, Stability 1.0 | 0.92 | 73 ms | 0.148 | 0.966 |
| raw KF centroid as candidate position (bind + filter) | 2.42 | 0 ms | 0.104 | 0.966 (white-duo **−0.11**, texture-duo −0.06) |
| **B: bind on smoothed, filter the raw centroid, Stability 0.75** | 2.02 | **26 ms** | **0.111** | 0.967 (unchanged everywhere) |
| B, Stability 1.0 | 1.68 | 33 ms | 0.115 | 0.967 |

- The One-Euro at 0.5 adds **+6 to +26 ms** of lag on top of the tracker's EMA (43–60 ms); the doc's "+15…25 ms"
  holds. Total output-smoothing lag on fast moves is **~55–85 ms**, on top of ~80 ms glass→OSC: the centroid TD
  sees trails a fast move by roughly 140–170 ms.
- The Stability knob only trades 10–20 ms. **The lever is the EMA** (`CENTROID_OUTPUT_SMOOTHING = 0.5`, one frame
  of group delay) feeding the filter. Replacing the candidate *position* by the raw centroid breaks binding on the
  duo takes (gates and duplicate checks see jumps). **Variant B keeps the smoothed centroid for binding and feeds
  the One-Euro with the raw one**: binding metrics identical, lag −30…−60 ms, fast error −18 %, jitter at rest
  1.6× (2.0 % of h ≈ 4 px at 190 px) — still below the pre-CONT-6 stream. On texture-aerial lag goes 86 → 31 ms,
  on white-duo 54 → 29 ms. This is PERF-9 done at the right place, output-only, a few lines in
  `candidate_from_track` / `_run_identity_slots`. **Decision D18**: judged by eye on the TD side.
- Other knobs: `hidden_max_s` (following a frozen-gated track) is a wash (+0.6 pt on-dancer on aerial, −3.8 pt on
  white-duo, 8.7 → 23.5 % coasting on the wall-hang clip without it) — keep. `entry_max_fss = 60` matters on the
  wall-hang clip (coverage 0.84 → 0.74 at 20) — keep. Loosening establishment to hits ≥ 4 / streak 2 gives
  +1.7 pt on white-duo and nothing elsewhere — not worth the ghost risk.

### 1.6 The belt's real contribution (2026-10-05 Bordeaux takes, `belt_eval.py`, replays belt on/off)

Replays of slots 4/5/6/8 at x@1280 with the belt hook on and off are **metric-identical** (coverage, ids, lag,
jitter). State `belt` fired on 4, 1, 0 and 33 frames respectively; the 33-frame run is at the end of slot 8, an
**off-axis take where the belt is invisible to the camera**: a false belt held a slot for 1.7 s. On texture-duo
(April footage, no belt at all) the hook also "held" a slot for 9 frames. `belt_eval` on the on-axis takes
(exposure 49.3 ms / 27.8 dB assumed from the project config, v1 `.meta`):

| take | belt found at the hips on skeleton frames | rescue on YOLO-lost frames | false positives off-dancer / frame | note |
|---|---|---|---|---|
| slot 5 (dancers ≥ 350 px, body saturated) | 50 % | **19 %** | 0.97 | works only because the dancer is close |
| slot 6 (show-like, 120–350 px dancers 0 % hits) | 7.1 % | 1.4 % | 0.014 | belt window peak ~31 DN at 120–200 px, torso 9 DN |
| slot 7 | 1.6 % | 1.3 % | 0.0 | |

**At show distance the belt was below the detector floor (60 DN) on 2026-10-05.** Whether that is the projector
offset (the retro return falls ~30× between 0.3° and 1.5° of observation angle, i.e. 14 cm vs 65 cm off the lens
axis at 25 m), the material, or both, is exactly what tonight's slot 8 ("projecteur à son emplacement habituel"
vs glued to the lens) answers. The IR budget from the close-dancer samples (optimistic: it attributes them to 25 m):

| target | 25 m, 25 ms, ≤ 24 dB | 30 m | 40 m |
|---|---|---|---|
| belt peak ≥ 120 DN | **×3.6** of today's IR (≈ 8 PIR130) | ×5.1 (11) | **×9.1 (19)** |
| body ≥ 20 DN (YOLO) | ×0.85–2.5 | ×1.2–3.6 | ×2.2–6.3 |

Detector cost is not the issue (gated mode 0.05–0.7 ms p50, ≤ 3.6 ms p95 per frame on dev37 with 2 threads).
The pipeline also runs it with `static=None`: no glint map, so a fixed bright spot near a coasting slot's
predicted waist can hold it for up to 8 s (`belt_max_s`); the `belt_min_learn = 5` consistency rule is the only
guard. **Verdict:** the belt is a measured hypothesis, not a shipped capability. Keep it ON (it costs nothing
when dark), wire the static map (MRK-4-lite), and decide its future on tonight's takes (D19).

### 1.7 Perf headroom at x@1280 with TouchDesigner (laptop facts + dev37)

Laptop, this morning (`tmp_analysis/remote-mirror/fps_table_laptop.json`, `remote-runs/*/out/res.json`):

| engine (RTX 5080 Laptop, engine-only) | fps | ms / inference | GPU duty at 20 fps |
|---|---|---|---|
| yolo11x-pose @960 | 79.3 | 12.6 | 25 % |
| **yolo11x-pose @1280** | **46.9** | **21.3** | **42 %** |
| yolo11x-pose @1536 | 32.6 | 30.7 | 61 % |
| yolo11l-pose @1280 | 74.1 | 13.5 | 27 % |
| yolo11l-pose @1536 | 54.1 | 18.5 | 37 % |

- During the morning replays the GPU ran at **37–46 % utilisation, 1.98 GHz, 42–73 W, 84–87 °C** (second
  session; the first sat at 1.6 GHz and 75 °C in the balanced power plan) and the Python process at 2.3–2.5
  cores of 32. The 40–44 ms frames seen then (yolo 16–23, CPU MOG2 16–23, tracker 6–17 ms) predate the perf pass
  (cv2 threads restored, PoseRunner, bit-exact batched CLAHE, motion feed submitted first, 15 fps preview cap).
- **Thermal, not utilisation, is the constraint.** 87 °C means the GPU is already clock-limited with WallDance
  alone in the balanced plan; TD's own rendering lands on the same package. Nothing on dev37 can measure that.
  The operator note asks for Lenovo Performance mode and a raised chassis; slot 9 tonight (3–5 min "comme le
  spectacle", TD running) is the first real measurement: `[Budget]` lines with the new `mog2_wait` key, and
  `nvidia-smi` clocks/temperature/throttle reasons.
- **dev37 stage split with the merged code** (perf harness, natural mode, TRT x@1280, 300 frames, p50 / p95 ms;
  i7-3770K is ~2.5× slower per thread than the laptop, GPU ≈ parity):

  | stage | hangar-aerial, 1 dancer | same, `WD_POSE_RUNNER=0` | texture-duo, 2 dancers |
  |---|---|---|---|
  | `process_wall` | 33.2 / 42.4 | 34.0 / 41.2 | **47.8 / 64.1** |
  | `yolo` (whole call) | **14.4** / 15.8 | **17.2** / 18.2 | 14.6 / 15.2 |
  | `mog2_feed` (submit → done) | 25.8 / 33.1 | 26.5 / 33.1 | 24.8 / 29.8 |
  | `enhance` (batched CLAHE) | 6.5 / 8.0 | 6.5 / 7.7 | 7.1 / 8.7 |
  | `post_yolo` (crossval → tracker → slots → OSC) | 6.7 / 11.9 | 6.6 / 11.6 | **22.9 / 35.8** |
  | ↳ `tracker_update` | 3.2 / 8.3 | 3.3 / 7.9 | **15.0 / 26.3** |
  | `motion_wait` (main thread blocked on the worker) | 1.9 / 6.6 | – | – |

  PoseRunner is worth **2.8 ms per frame** on dev37 (the regular call shows 4.7 ms of postprocess and 1.9 ms of
  `orig_img` download). The motion worker is still the critical path at 1 dancer (blur + resize 14 ms, MOG2 7 ms,
  Welford 2.5 ms on this CPU). **At 2 dancers on a textured wall the tracker is 15 ms p50 / 26 ms p95 here**
  (≈ 6 / 10 ms on the laptop, matching this morning's 6–17 ms): the cProfile shows 45 % in the motion bridge's
  per-track `frame_diff_blob_in_bbox` (7.5 `connectedComponentsWithStats` calls per frame), 26 % in the Python
  cost matrix, and the identity slots at 2.3 ms of which the belt hook is 1.6 ms (a lit textured wall gives it
  windows to analyse on every frame). So the "tracker perf" proposals have a measured target, in this order:
  the bridge's CC queries, then the cost matrix, then the belt hook's early-out when the slot is live and the
  belt is not needed.
- The model/imgsz choice is **free on the CPU side and expensive on the GPU side** (the opposite of the June
  picture): x@960 or l@1280 return ~8–9 ms of GPU per frame. At 25 m (188 px dancers) both keep > 110 net px;
  at 40 m only x/l@1280+ with a tight ROI do (§4).

### 1.8 PoseRunner on ultralytics 8.4.63 (static check against the laptop's version)

The 8.4.63 wheel was pulled from PyPI and diffed against the installed 8.4.173:
- `BasePredictor` has `_lock`, `done_warmup`, `batch`, `source_type` and the same `preprocess(im)`,
  `inference(im)`, `postprocess(preds, img, orig_imgs)` signatures; `stream_inference` leaves `self.batch` set
  after the loop (the fast path reads `self.batch[0]` in `construct_results`).
- `LoadTensor._single_check` is **byte-identical** (stride check, `im.max() > 1 + eps` rescale), so the fast
  path's own check matches.
- `DetectionPredictor.postprocess` accepts a list for `orig_imgs` and only reads shapes; `PosePredictor` differs
  by a docstring.
- Differences: 8.4.63 does **not** emit the per-frame `'half' is deprecated` warning (no stderr saving there),
  and `Profile` syncs the same six times per call, so the laptop gain is the syncs + the 4.9 MB D2H only.
**Residual risk is runtime-only** (predictor internals touched by something else between calls). Gate: on the
laptop DEV slot, `replay --trt --score` on hangar-aerial and white-duo with and without `WD_POSE_RUNNER=0`,
summaries and timelines byte-equal, then the `[Budget]` delta. Keep the kill switch documented for the operator
note (it is an environment variable: a `launcher.json`/`run.bat` toggle would be friendlier).

### 1.9 Test / golden strategy with output-only layers

- The golden trio and the 12-scenario sweep compare **tracker** summaries; CONT-6 is output-only, so they prove
  nothing about what TD receives. No golden covers the emitted stream today.
- TEST-1 gives the yardstick (C1–C10, long spans, `--quality` for field takes) and `slot_replay.py` makes the slot
  layer re-runnable offline in seconds from a committed timeline. **Proposal (TEST-2):** commit the
  `--score --internal` timelines of the golden trio + the two long spans (≈ 0.5–5 MB each, engine-stamped), add
  (a) an *emitted-stream golden* (per-frame slot ids, states and positions, exact match) and (b) a *quality
  floor* (coverage, on-dancer, spatial validity, jitter, lag, switches per scenario, ± tolerance) evaluated by
  `slot_replay` in the unit suite without a GPU. Any slot/filter change is then gated in seconds; tracker changes
  regenerate the timelines on the laptop and re-run both.
- Engine stamps (ARCH-5) are still missing; goldens remain per-engine. The two long-span manifests fail today on
  the tracker stream by design (CFG-1/CFG-2 pinned) and the emitted stream fails C8 by 0.004 — the pass lines need
  an *emitted* variant (coverage ≥ 0.98, on-dancer ≥ 0.95, spatial ≥ 0.95, coasting ≤ 10 %) rather than reusing
  the tracker's.

### 1.10 OSC id semantics for TouchDesigner

What changed on the wire with slots ON (default): ids are `1..max_dancers` for the whole show, `/count` lists
the emitted slot ids, a lost dancer keeps its id for `coast_s` then disappears, a returning or new dancer reuses
the lowest free id; message shapes unchanged; `/walldance/dancer/state` is opt-in. Risks to check with the patch
owner (D24): (a) any TD logic keyed on "a new id appeared = a new dancer" now sees id 1 reused by a different
dancer after an exit; (b) a 2 s hold means a dancer who leaves the wall keeps a frozen video for 2 s — desired per
D5, but the patch may want `/dancer/state` to fade it (D22); (c) with `L > 1` the RTS smoother now runs on slot
ids and no longer restarts on tracker churn (an improvement, but a behaviour change); (d) the `Stable IDs`
checkbox restores the old semantics live, so a fallback exists.

---

## 2. Findings, ranked by impact × risk

| # | Finding | Evidence | Risk if ignored | Action / measurement |
|---|---|---|---|---|
| F1 | **Belt unproven at show distance**; false holds possible | §1.6: 0 % hits on 120–350 px dancers, 33-frame false hold on an off-axis take, `static=None` | The field relies on a fallback that fires on glints and not on dancers; IR money spent on the wrong target | Tonight: slot 1 (empty) → static map, slot 8 offset A/B, slot 7 ladder → `belt_eval` on the laptop. Wire the static map. Decide D19. |
| F2 | **Coasting hides position errors**; C8 falls under the line; textured walls coast 52 % | §1.1, §1.4 | TD shows a frozen point 0.8 h off for up to 2 s; on textured venues half the stream is held | Keep `coast_s` 2 s; turn `/dancer/state` ON for TD (D22) or add a fade; ship CONT-2 (exclusion + τ) before any textured venue; CONT-3 before production |
| F3 | **Lag 55–85 ms of output smoothing** on fast moves | §1.5 | "Stable but fast" not met on fast swings | Variant B (raw centroid into the One-Euro), replay-gated, by eye (D18) |
| F4 | **Blob-fed drift reported as `live`** | §1.3: 100 % of off-body live points are skeleton-stale | TD trusts a point that is on the rope/floor; the state message cannot warn | CONT-3 (tracker); meanwhile expose skeleton age in `/dancer/state` or map `fss > 10` to a `weak` flavour |
| F5 | **Warm-up is the top remaining hole cause** | §1.2: 48–73 frames per take | Every re-birth costs 0.75 s of coast; low light multiplies re-births | CONT-4a + `tracker_intermittent_confirm` in known-N (CONT-2c), tracker-side; not the slot layer (tested, rejected) |
| F6 | **GPU thermals on the laptop** | §1.7: 84–87 °C at 42 % duty, balanced plan at 1.6 GHz | fps dips during the show when TD renders | Performance mode + chassis (done in the note); measure slot 9 with TD; readiness rows for power plan/AC/GPU temp (ARCH-7) |
| F7 | **No gate on the emitted stream** | §1.9 | A slot/filter regression ships unseen; goldens stay green | TEST-2 emitted goldens + quality floor from committed timelines |
| F8 | **Production optics/IR not sized** | §4 | A 40 m venue discovered on site | Ask the venue's wall size and camera spot now; venue_fit + IR budget; lens/beam decisions (D20) |
| F9 | **PoseRunner unverified on 8.4.63 at runtime** | §1.8 | Silent fallback to the regular call (fine) or wrong detections (unlikely, but the fast path bypasses the public API) | Laptop A/B byte-equality before the release |
| F10 | **Reinstall trigger on the second release** | §5 | `install.bat` runs `uv pip sync` on the show laptop; a wrong pin file uninstalls the IDS SDK | Two-step release; supervised window; bundle before |
| F11 | **Textured venues need exclusion** | §1.3 tango-H2 | Two slots on stains, 12 switches | Operator paints cells (in the note); CONT-9 proposals before production |
| F12 | **Perf items that the field will feel**: recording encoder (~50–60 ms/frame of a core during takes), tracker at 2 dancers 6–17 ms | audit PERF-6; laptop morning numbers | Takes during rehearsal steal a core while TD runs; tracker p95 grows with N | PERF-6 mono FFV1 (recordings only); tracker vectorisation only if p95 > 10 ms with TD running |

---

## 3. Remaining roadmap items: still needed after CONT-6 + belt?

| Item | Verdict | Horizon | Evidence | Real risk if skipped |
|---|---|---|---|---|
| **CONT-2 + CONT-9** exclusion proposed by calibration, τ down to 0.30–0.35, known-N searches `intermittent_confirm` / `ghost_skeleton_age`, fix hangar pins | **Keep** | this week (manual painting + ROI, already in the operator note) → before production (proposals, τ, pins) | tango-H2 52 % coasting + stain slots; audit s4_combo 94.2 → 99.5 % at the tracker | Every textured or lit-object venue degrades to half-held output; coasting cannot fix a tracker that is not looking |
| **CONT-3** YOLO-first tiered assignment, blob suppression inside YOLO boxes | **Keep** | before production (one replay-gated tracker set, with TEST-2 in place) | 100 % of off-body live points are blob-fed (§1.3); 41 % synthetic feeds; 326 discarded-skeleton frames (audit) | Frozen points "live" on the rope; worse in low light where blobs dominate |
| **CONT-4** continuation ≠ birth, frozen gate rebase, hold coasting | **Half superseded**: 4a stays, 4b/4c drop | before production (low light = the floor regime) | hidden-follow + 2 s coast ≈ output-side 4a for short gaps; with slots the floor take still has a **24.5 s hole** and 743 uncovered frames with a track on the dancer stuck in warm-up (§1.1, §1.2) | Long holes in dark scenes cannot be coasted; every re-birth costs 0.75 s |
| **CONT-5** bound tentative tracks | nice-to-have | — | slots hide the id churn; no slot bound to a YOLO ghost on white walls | internal churn only, unless duo swaps are traced to hijacks |
| **CONT-11** retire the bridge | nice-to-have | — | bridge feeds are 7 % of emitted frames; the slot layer makes duplicates harmless | — |
| **SEAM-1** typed measurement | nice-to-have | only if CONT-3 or marker Kalman fusion proceed | — | refactor without field value on its own |
| **SEAM-2** `core/output_stage.py` | nice-to-have | — | CONT-6 shipped inside `pipeline.py` (+261 lines) without it | maintainability only |
| **MRK-3** marker detector | **done as `belt_detector.py`** (gated mode) | — | — | — |
| **MRK-4** static glint map + threshold auto-calibration in Aim | **Keep (lite)** | this week: static map from the empty-stage take / Aim window into the hook (`static=None` today) | false belt holds (§1.6) | a glint holds a slot for up to 8 s |
| **MRK-5/6** extremity cuffs, Tier A/B Kalman fusion | **Drop to nice-to-have** | — | the field chose a belt (= the harness marker the audit valued most); slot-level hold already uses it | none while the belt is the marker |
| **MRK-7** marker-only acquisition of a static, never-seen dancer | **Keep, gated** | before production (low light makes YOLO miss still dancers) | audit (f): static dancers rejected by the scored gate; belt visibility tonight decides | a still dancer never appears |
| **MRK-8** OSC extras | done (`/dancer/state`) | — | — | — |
| **MRK-9** readiness row "belt seen", overlay | keep, small | this week → before production | operator cannot tell whether the belt is contributing | silent fallback |
| **Phase 0a** go/no-go on footage | **Run on tonight's takes** | this week | `belt_eval` + `marker_eval info` over 4G | — |
| **DOC** consolidation (DOC-15 → DOC-5, ARCHITECTURE, TESTING) | **Re-order**: MODE_EMPLOI (DOC-2) first; this review → ROADMAP v2 final; the rest nice-to-have | before production | "easy on-site settings" is a goal; two French session notes exist as seeds; ROADMAP.md still describes June | operator depends on Thomas for every setting |
| **ARCH-4 push** (D3 pin) + release | **Keep** | this week (two steps, §5) | laptop `requirements-prod.txt` frozen; `pinned-install` @ f3ff925 | an unpinned reinstall kills TRT (audit P0) |
| **ARCH-5** engine stamps, loud fallback | keep | before production | laptop has `nvidia-modelopt`, so rebuilds work there; still a silent PyTorch path on mismatch | a 3–7× inference regression nobody sees |
| **ARCH-7** show mode (sleep guard, readiness rows) | **Keep** | this week (operator checklist, in the note) → before production (rows) | 87 °C throttling observed in the balanced plan | fps dips, sleep mid-show |
| **ARCH-10** one config applier | nice-to-have | — | BUG-3 fixed in replay | — |
| **Tracker perf** (S⁻¹ cache, vectorised cost matrix, threaded logger) | **Keep, re-targeted** | before production if `track` p95 > 10 ms with 2 dancers and TD running | dev37: 15 / 26 ms p50/p95 at 2 dancers; 45 % motion-bridge CC queries, 26 % cost matrix, belt hook 1.6 ms (§1.7) | tracker + motion worker eat the CPU slack TD needs |
| **Rectangular portrait engine** | **Conditional** | before production only if the venue ROI is tall and narrow (> imgsz on one side) | letterbox pads to a square (`gpu_pipeline.py`); dancer px are lens-only (§4) | wasted network pixels on tall walls; otherwise nothing |
| **REQ-4 / CONT-8** small far figures | drop unless > 45 m | — | 8 mm gives ≥ 117 px at 40 m | — |
| **PERF-9** KF centroid output | **Superseded by variant B** | this week | §1.5 | lag stays |
| **PERF-6** mono FFV1 recording | keep | before production (sessions record) | encoder 145 → 61 ms/frame on dev37 (−58 %) | a core lost while recording with TD open |
| **PERF-5/8/2/4** latency plumbing, preview, motion feed, CLAHE | mostly done or nice-to-have | — | CLAHE done bit-exact; preview capped; motion first | — |
| **X-4** steady-rate OSC | nice-to-have | — | not requested | — |
| **REM-5** in-app job runner | nice-to-have | — | `wdremote py` covers it over SSH | — |

---

## 4. The production gap (30–40 m, portrait, low light): what must change, in order

**Facts (OPTICS.md, `extra/venue_fit.py`, 8 mm Tamron, 1.70 m dancer):**

| distance | dancer px (original) | net px, full 1528 ROI letterboxed to 1280 | net px, ROI ≤ 1280 px | GPU at 20 fps for ≥ 110 net px |
|---|---|---|---|---|
| 25 m (this week) | 188 | 157 | 188 | x@960 (25 %) or x@1280 (42 %) |
| 30 m | 156 | 131 | 156 | x@1280 |
| 35 m | 134 | 112 | 134 | x@1280 (tight) |
| 40 m | 117 | **98** | 117 | x/l@1280 with a tight ROI, else x@1536 (61 %) or l@1536 (37 %) |

The 6 mm lens gives 88 px at 40 m: out. A 12 mm lens would give 175 px at 40 m with a field of 0.52 × D ≈ 21 m
wide (crop); worth a quote if the production wall is ≤ 20 m wide and the camera spot is ≥ 35 m (D20).

**IR:** irradiance falls as 1/d²: 40 m receives 0.39× of 25 m from the same projectors. From §1.6: the body needs
×2–6 of today's IR at 40 m to stay where YOLO is reliable, the belt ×9. The 60° PIR130 beam covers a 46 m disc at
40 m; a 30° beam puts ~3.9× more on a 21 m disc, which is the cheapest lever. Exposure ≤ 25 ms (blur budget, less
latency) halves the light again; it is only affordable with the IR multiplied.

**Portrait:** rotating the camera trades a wide field for a tall one; dancer pixels do not change. The input
transform (REQ-5) and the producer-thread rotation exist. What changes is the **ROI shape**: a tall, narrow ROI
letterboxed into a square engine wastes most of the network (a 2688 px tall full-sensor portrait ROI into 1280
leaves 56 net px at 40 m). Either keep the ROI tight to the wall (≤ imgsz on the long side) or export a
rectangular engine for the ROI's aspect (ultralytics supports `imgsz=(h, w)` for TRT; `gpu_pipeline` letterbox
and `_TrackerSpace` assume a square today).

**Order:**
1. Get the venue numbers: wall W × H, camera distance, camera height, ambient light sources (projectors, exits).
   `venue_fit.py --stage WxH --distance D` gives the lens verdict in seconds.
2. IR plan from tonight's `belt_eval` budget: projector count × beam × offset; decide belt-grade vs body-grade IR
   (D19). Buy narrower beams before buying more units.
3. Model/imgsz by accuracy on the production take (Calib2 picks imgsz for ≥ 110 net px already); confirm the GPU
   duty with TD running.
4. ROI/engine shape: tight ROI first; rectangular engine only if the ROI's long side exceeds imgsz.
5. Calibration on site unchanged (Aim → Calibrate → exclusion), plus the static glint map from the empty wall.
6. Low light: expect more tracker losses → CONT-2 τ/exclusion and CONT-4a matter more there than at 25 m;
   `coast_s` may go to 3 s with `/dancer/state` ON.

---

## 5. Release path: what must be true before Thomas pushes `release` + `pinned-install`

Facts checked today: the laptop runs `main` = `a49d0f2` = `origin/main`, clean; the new launcher exe is installed
with channel `release` (not on GitHub yet, so it starts LIVE as is); `remote-ops` **does not touch**
`application/pyproject.toml`, `install.bat`, `install.sh` or `uv.lock`, so its push triggers **no reinstall**;
`pinned-install` (f3ff925) adds the installers' pinned mode and `requirements-prod.txt` frozen from the laptop's
own venv on 2026-10-06; the `remote-ops` commits carry no stray `Refs-37` trailers (nothing to strip).

Gates, in order:
1. **DEV slot on the laptop at the release candidate** (`wdremote deploy <sha>`; DEV is at `34318ac`, pre-CONT-6):
   `--slot dev pytest -x` on the laptop stack (py3.12, torch 2.12, TRT 11.0, ultralytics 8.4.63); on dev37 the
   merged code passes 830 / 9 skipped (Appendix A).
2. **TRT replays on the laptop**: hangar-aerial, white-duo, texture-duo `--trt --score` from DEV; PoseRunner A/B
   (`WD_POSE_RUNNER=0`) byte-equal summaries + timelines; `[Budget]` with `mog2_wait` ≈ 0 and the stage split.
3. **GUI smoke on the laptop desktop** (`slot run --slot dev`): phase 6 "Dancer IDs" block, REMOTE chip, rig
   sheet, Import, Mirror/Rotate, `/walldance/count` showing ids 1–2 in TD.
4. **TD patch confirmation** (D24): stable ids 1..N and the 2 s hold are what the patch expects; `/dancer/state`
   ON or OFF (D22).
5. `wdremote release-check <sha> --tests` clean: fast-forward from `a49d0f2`, no reinstall flag, OSC-contract
   and tracking-core changes acknowledged.
6. `wdremote bundle` (backup of LIVE's git) and `pull --tier P0` (configs, sessions) before the push.
7. **Push 1 = `remote-ops` tip → `refs/heads/release`.** The launcher fast-forwards LIVE at its next start (or
   `slot run --slot live --via-launcher`); backup ref `refs/walldance/pre-update-<stamp>` is written. The
   operator runs the next session on LIVE with stable ids.
8. **Push 2 = `pinned-install` merged on top, in a window where Thomas is online** (D21): the launcher runs
   `install.bat` → `uv pip sync requirements-prod.txt` (5–10 min, should be a no-op on a venv that produced the
   file; it removes anything not listed). Rollback = `launcher` backup ref + the old venv is untouched unless the
   sync fails midway, so keep `bundle` + a venv copy (`robocopy application\.venv …`) before.
9. After: engines load (TRT chip on), goldens on the laptop unchanged, the first LIVE `logs/walldance_<stamp>.log`
   pulled.

Not before the push: CI has never run on these branches (nothing is on GitHub); the Windows job (ARCH-9) runs
only once something is pushed. Pushing `release` is the first CI run as well — accept that, or push a throwaway
branch first (not `main`, not `release`).

---

## 6. Revised plan

### This week's sessions (hangar, 25 m, 2 dancers)

| # | Item | Why now | Gate / output |
|---|---|---|---|
| W1 | **Tonight's takes → tomorrow's analysis on the laptop** (`mur25m-ceinture-0610`): `belt_eval` with `--empty-slot 1` on slots 2–9 (ladder 7, offset 8), `replay --quality` on slot 9 with belt on/off, `[Budget]`/`nvidia-smi` during slot 9 with TD | F1, F6; everything about the belt and the IR plan hangs on it | belt-at-hips %, rescue %, FP/frame; IR multipliers; GPU clocks/temps with TD |
| W2 | **Release gates 1–6** (§5) on the DEV slot | F9, F10 | pytest green, replays byte-equal with/without PoseRunner, GUI smoke, release-check clean |
| W3 | **Push 1 (`remote-ops`)**; operator session on LIVE with stable ids; TD patch check | D5 realised | TD keeps its video through losses; `/count` ids 1–2 |
| W4 | **Output quick wins, output-only, replay-gated with the offline sweep:** (a) variant B raw-centroid input to the One-Euro behind a config key, default by eye (D18); (b) belt static map wired into the hook (from the empty-stage take, later Aim); (c) `/dancer/state` carries skeleton age or a `weak` flavour | F3, F1, F4 | `slot_sweep.py`/`variants2.py` tables unchanged on binding, lag down; false belt holds on slot 8 gone |
| W5 | **On-site settings** (already in TERRAIN_2026-10-07_FR): tight ROI, exclusion painting, Performance mode, Max dancers 2, Hold 2 s, Stability 0.5; remote `SetRoiRect`/`ExcludeAt` | F11, F6 | fewer ghosts; GPU < 80 °C |
| W6 | **Push 2 (`pinned-install`)** in a supervised window | D3 | engines load, suite green on the laptop |
| W7 | **TEST-2 seed**: commit today's `--score --internal` timelines for the golden trio + aerial-full; emitted-stream golden + quality floor as unit tests | F7 | the slot layer gated in seconds |

### Before production (30–40 m)

| # | Item | Depends on |
|---|---|---|
| P1 | Venue numbers → lens / ROI / imgsz decision (§4) and the IR plan (count, beam, offset) from W1's budget; D19/D20 | W1 |
| P2 | **CONT-2/9**: τ allowed down with exclusion, calibration proposes exclusion cells, known-N searches `intermittent_confirm` + `ghost_skeleton_age`, re-pin the hangar manifests (CFG-1/2); textured-wall guard (tango-H2 s9 as a scenario) | W7 |
| P3 | **CONT-3** as one replay-gated tracker change set (YOLO-first, blob suppression in boxes, never discard a YOLO det for a blob-fed track) | W7, P2 |
| P4 | **CONT-4a** continuation ≠ birth (an emitted track stays emitted while its last skeleton is ≤ ~1 s old), plus `tracker_intermittent_confirm` per scene — the floor take still holes for 24 s with slots on | P3 |
| P5 | **MRK-7-lite**: belt-only hold/acquisition of a still dancer through the slot layer, and the `belt seen` readiness row (MRK-9) | W1 says the belt is visible at show distance |
| P6 | **Engine stamps + loud fallback (ARCH-5)**; emitted-stream pass lines in the long-span manifests | W7 |
| P7 | **ARCH-7 readiness rows** (power plan, AC, GPU temperature, pending reboot) + **MODE_EMPLOI** (DOC-2) seeded from the two French notes; this review → ROADMAP v2 final (DOC-5) | — |
| P8 | **Perf with TD**: decide x@960 / l@1280 vs x@1280 by accuracy at the production distance; tracker vectorisation only if `track` p95 > 10 ms at 2 dancers; PERF-6 mono recording | W1 |
| P9 | Rectangular engine, only if the venue ROI is tall and narrow | P1 |

### Nice-to-have

SEAM-1/2 · ARCH-10/14/15/16 · CONT-5/11 · CONT-8 · MRK-5/6 (extremity fusion) · DOC-6/7 (ARCHITECTURE, TESTING)
· PERF-5/8/10/12/13 · X-4 · REM-5 · drift guard and warm-up rebind (both measured, both rejected; keep the scripts).

---

## 7. Decisions for Thomas

| # | Decision | Recommendation |
|---|---|---|
| D18 | Output lag: feed the One-Euro with the raw Kalman centroid (lag −30…−60 ms, fast error −18 %, jitter at rest 1.3 → 2.0 % of h at Stability 0.75)? | Build it behind a key, judge on the TD screen with a fast swing; default ON if the jitter is invisible |
| D19 | Belt: commit to belt-grade IR (×4 at 25 m, ×9 at 40 m) or keep it opportunistic and spend on body IR + narrower beams? | Decide after tonight's slot 8 (offset) and slot 7 (ladder); if the on-axis belt saturates at ≤ 10 cm offset, the budget shrinks by 5–10× and the belt is worth it |
| D20 | Production lens: 8 mm (117 px at 40 m, wall ≤ 31 m wide) or a 12 mm (175 px, wall ≤ 21 m)? | Needs the venue's wall width and camera spot |
| D21 | Release in two pushes (`remote-ops`, then `pinned-install`) or one? | Two; the second supervised |
| D22 | `/walldance/dancer/state` default ON for the show patch (so TD can fade a coasting dancer)? | ON if the TD patch owner will use it; it is additive |
| D23 | Textured venues: operator paints exclusion (now) vs calibration proposes cells (CONT-9, before production)? | Both; painting is in the note already |
| D24 | Keep `Stable IDs` ON by default on LIVE at push 1 (ids 1..N, 2 s hold reach TD immediately)? | Yes, with the checkbox as the fallback and the TD patch checked in W3 |
| D7 (open) | Exposure ≤ 25 ms for shows | Only with the IR multiplied (D19); otherwise YOLO starves |
| D8 (open) | Costume | Belt chosen; cuffs dropped unless Phase 0a says the belt fails |
| D9 (open) | Duos/crossings | Yes (slot 5 of tonight's protocol); slot swaps are acceptable per the field constraints, measured by `id_switches` |
| D13 (open) | Crash mid-show → STANDBY | Keep (crash marker shipped) |
| D17 (open) | TD on the same laptop | Yes; it is the perf constraint (§1.7) |

---

## Appendix A — unit suite on the merged code (dev37, 2026-10-06 14:19)

`pytest tests -q`: **828 passed, 9 skipped, 2 failed**; both failures are `test_yolo_runner.py` (PyTorch `.pt`
path) with `CUDNN_STATUS_SUBLIBRARY_LOADING_FAILED` (`libcudnn_engines_runtime_compiled.so.9` not on the library
path when the venv's Python is called directly). With `run.sh`'s `LD_LIBRARY_PATH` (the venv's `nvidia/*/lib`)
all four PoseRunner tests pass → **830 passed, 9 skipped**. A dev37 environment quirk, not a code fault; the
laptop run (§5 gate 1) is the one that counts. Worth a `conftest.py` fix later so the suite is one command.

## Appendix B — scripts

`tmp_analysis/review-2026-10/`: `run_replays.sh` (TRT replays, slots ON, timelines with internal tracks and
references), `summarize.py` (tracker vs emitted table), `slot_sweep.py` (stability, raw input, coast, hidden,
decay, establishment), `gap_inject.py` (hold vs drift), `diagnose.py` (hole and off-body attribution),
`variants2.py` (warm-up rebind, split raw filter), `variant_drift.py` (drift guard), `run_belt_eval.sh`
(belt + IR budget on the 2026-10-05 takes), `run_perf.sh`. Outputs go to `$WD_REVIEW_OUT` (default
`/tmp/wd-review`).
