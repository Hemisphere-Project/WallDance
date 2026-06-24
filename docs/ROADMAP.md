# WallDance Roadmap

**Date:** 2026-06-22 · **Status:** Single source of truth for *forward work*. Index of all docs:
**[README.md](README.md)**.

This is the one place for "what's next." It merges and supersedes the forward content of the old
`OPERATOR_V2.md` (Tracks O/X/C/S/G/D/P), the `TODO.md` build/hardware phases, the open
`AUTOTUNE_DESIGN.md` gaps, and the `TRACKING_ROBUSTNESS.md` IR-marker direction. The detailed
**shipped record** (P0–P4, corpus Phase 0–2b, resolved bugs #1–#14, tracker lessons, env findings)
moved to **[archives/ENGINEERING_RECORD.md](archives/ENGINEERING_RECORD.md)** — §6 keeps a
condensed index so code comments that cite `ROADMAP bug #N` / `P3` / `§3a` / `Track O` still
resolve here.

> **Track labels (O/X/C/S/G/D/P)** are referenced throughout the code (`gui_builder.py`
> "OPERATOR_V2 Track O", `pipeline.py` "Track X", etc.). They are preserved in §3 below — that is
> now their canonical home.

---

## 0. North star

A WallDance operator should: rig the camera, aim the IR light, press **one calibration button**,
and get robust detection for the whole show — **without tuning knobs per venue**. "Set and forget"
= one explicit, logged calibration, then stable, *not* continuous silent auto-tuning.

The **detection algorithm is done** (§6 P0–P4, corpus Phase 2/2b). Two frontiers remain:
1. **Operability** — make the shipped algorithm trustworthy on a *new* show: a linear operator
   spine, a unified calibration pass, settings governance, the output/OSC layer. (Tracks O/C/S/X/G,
   mostly shipped; the remaining correctness + engine work is §3.)
2. **The next leap** — the current detection tower is near its ceiling (§2). The durable win is a
   **positive signal at the source: IR retroreflective markers** (§3.3, the marquee research bet).

---

## 1. Where we are (shipped subsystems)

| Subsystem | State | Where |
|-----------|-------|-------|
| Detection algorithm P0–P4 | ✅ Shipped + corpus-validated (Phase 2/2b) | §6; [archives/ENGINEERING_RECORD.md](archives/ENGINEERING_RECORD.md) |
| `app.py` decomposition (core/runtime/ui/camera/services + command/event seam) | ✅ Phases 0–4 done; app.py = composition root (~2.1k ln) | [archives/DECOMPOSITION_PLAN.md](archives/DECOMPOSITION_PLAN.md) |
| Operator UX v2 — linear **phase rail** (① Rig → ⑥ Live), drawers, status strip, two dials | ✅ Shipped (Track O) | `gui_builder.py`, `gui.py` |
| Two-pass calibration (Aim/servo + Dancers pool), in-app **CLAHE×conf sweep**, manual exclusion paint | ✅ Shipped (Track C) | `runtime/calibration_flows.py`, `core/calibration.py`, `core/calib2.py` |
| Output/OSC layer — box-clamp + single **L-driven `/walldance/dancer/*`** stream (CV-Kalman + RTS smoother) | ✅ Shipped (Track X) | `core/output_smoother.py`, `core/osc_output.py`; contract: [OSC_CONTRACT.md](OSC_CONTRACT.md) |
| Phase ⑤ Verify (readiness panel + subprocess dry-run) + operator playbook | ✅ Shipped | [NEW_SHOW.md](NEW_SHOW.md) |
| Ops: readiness gate, HealthMonitor, LoopWatchdog, camera auto-recovery, 4 h soak | ✅ Shipped (4 h PASS 2026-06-13) | `core/ops_monitor.py`, `tests/soak.py` |
| Cross-parameter test (Track G, G1–G6) | ✅ Done 2026-06-15 — dials validated, CLAHE no-formula, governance table | §3, `tmp_analysis/g1…g6/` |
| Config schema v2 + validation + versioning, launcher update safety, CI (352 tests) | ✅ Shipped | `core/config_schema.py`, `tests/`, `.github/workflows/ci.yml` |

---

## 2. The strategy in brief

The detection stack is a **compensation cascade** — each layer (low conf → ghost flood →
motion crossval → bridge → frozen-ghost gate → exclusion) patches the weakness below it; the sum is
~90 interacting constants overfit to one clip. Two reframes followed (full prose:
[ENGINEERING_RECORD §2–§3](archives/ENGINEERING_RECORD.md)):

- **§3a — stop fighting ID swaps.** The OSC consumer needs *position + rough identity*; the
  post-hoc swap correctors were the most fragile, most overfit code → disabled by default
  (`tracker_swap_correctors`, Phase 2 ⑧).
- **§3b — separate the ghost axis from the drop axis.** Ghosts want high confidence, drops want
  low; one threshold can't win under uneven IR → reject ghosts by location/stationarity, keep
  confidence low, lift the whole distribution with better IR.

**2026-06 conclusion (TRACKING_ROBUSTNESS):** the cascade is **near its ceiling** — 2/5 hard scenes
are not dial-solvable, CLAHE *hurts* on noisy-near-black, higher imgsz *hurts* on dark/IR (G3), and
static dancers structurally never acquire a track. Stacking more post-processing buys little.
**The margin is at the signal source** — hence the IR-marker bet (§3.3).

---

## 3. Forward plan

Ordered by readiness. Every behavior-touching item is **replay-gated** (goldens / 12-scenario
scores decide, not eyeballs); pure-UI items need app-smoke only. Cross-lane items (calibration
engine, tracker core, OSC contract) need a heads-up + explicit go before code.

### 3.1 NOW — small, ready, replay-gated (one noted commit each)

| Item | Track | Notes |
|------|-------|-------|
| ~~**Calib correctness fixes**~~ ✅ **done 2026-06-22** | C | Height ownership (Calib2 = sole writer; Calib1 height now diagnostic-only); apply-gate **warn-banner** on `var_saturated` (non-blocking toast); **stale flag** now ORs in profile/lighting mismatch (not just ROI drift); **imgsz reload** now surfaces failure (was print-only) + an actionable no-engine message; **noise-σ unify** (Aim's clean-scene σ reused by a following Calib2 within `AUTOCAL2_NOISE_REUSE_S=600s`). `calibration_state` provenance added (Aim/Dancers source + ts/epoch per knob), **persisted through the config (shared key) + surfaced in the Aim "Last calibrated" line** on phase entry / after each calibration (Track S, ✅ done 2026-06-23). Goldens byte-identical (calib-flow only); flow + schema-roundtrip tests added. *(`tracker_intermittent_confirm` wiring — ✅ done, `app.py:1125`.)* |
| ~~**Calib2 pool: subset preview + quiet-apply**~~ ✅ **done 2026-06-22** | C | Toggling a pool checkbox now recomputes the proposal over the **checked subset** and **quiet-applies** it live (instant detection knobs + in-place proposal text refresh, no result-modal); the imgsz/engine reload stays on the explicit **Apply selected** (modal + save). New `ApplyCalib2(quiet=True)` command + `Calib2ProposalUpdated` event on the seam; initial proposal now matches the default (non-stale) checkboxes. Goldens byte-identical; +3 seam/flow tests. |
| **Track G harness gaps** | G | `--frame-skip` flag on `replay.py`/`tune.py` — ✅ **already built** (verified 2026-06-22; `replay.py:498`, `tune.py:201`). Remaining: promote the **0.05-floor τ cache + re-apply hook** (Phase 2b used a one-off) so confidence is cheap; `calibration_state`-aware scenario configs for per-tier pins. |
| **Phase 0a — IR-marker physical spike** ⭐ | D | **OPERATOR-OWNED, ~½ day — THE GATE for the next leap.** Retroreflective tape/thread on a test harness recorded under show IR; measure return brightness/saturation vs background, smallest reliable size, self-occlusion behaviour, visible-light invisibility, fixed-glint false positives. **Go/no-go on markers.** (Phase 0b software prototype already done: `tmp_analysis/marker_spike.py` — glint floor is low, markers look separable.) |

### 3.2 NEXT — deliberate, larger

> **▶ Active sequence (decided 2026-06-23): Track P → known-N (K1).** Known-N's search must
> optimize the *show* path; on the CPU cache it mis-estimates the bridge/motion knobs it tunes
> (the G1 finding). So **Track P goes first** (gives a GPU+TRT evidence base), then K1 builds on it.

| Item | Track | Notes |
|------|-------|-------|
| **Known-N calibration productization** — 🟢 **K1 + K3 done 2026-06-24** | C / Phase 3 | **K1 ✅ `tests/known_n.py`** — per-project joint coord-descent over **τ + θ_s + θ_m + tracker_max_age** on the GPU+TRT cache (Track P), Phase-2b-oracle-seeded τ (`analysis.json` cells), before/after report + timestamped project write-back (`confidence`→active profile; θ_s/θ_m/max_age→shared). **Subsumes per-scene τ ownership** (the weak ⑤a box-conf seed — median regret +0.056, 91 % clamp-pinned) **+ the θ_s/θ_m/max_age per-scene writer** (AUTOTUNE gap #2 / G4: carry 0.03–0.07 on multi-dancer/occlusion/static — internal, never a user dial). Validated on real projects (`texturedbg` 0.613→0.509 via max_age; `testflou` τ 0.2→0.55 — opposite knobs, the predicted per-scene τ inversion). **K3 ✅ calib-time imgsz dark-probe** — `calib2.aggregate` takes an injected `imgsz_probe` that runs YOLO at the two candidate presets (110-px vs 45-px pick) on the saved Calib2 frames and picks empirically; **supersedes the σ>4 heuristic** (σ is the fallback — no probe / TRT-fixed-imgsz / probe error). Wired in `calibration_flows` (PyTorch-only, explicit-apply-only — previews stay cheap). Verified: 10 unit tests (write-back + oracle + seam both directions + σ-fallback + helper + builder guards); the live probe needs on-rig validation (real Calib2 frames). **Remaining: optional GUI surface** (CLI-first shipped). |
| **Unified calibration engine** (C-next) | C | Collapse the engine to **Aim (servo, autonomous, early) + one Calibrate-with-dancers pass** deriving, in coupling order: **gamma (brightness) → var + clean-plate → CLAHE + height + imgsz + confidence (detection-derived) → blur budget**, over the evidence pool (exclusion stays manual). Deliberate joint design — the engine is load-bearing. G6 verified `var` is window-invariant (one dancers pass can derive it); clean-plate *pixel* recovery (skeleton-sparing robust median) is the unbuilt piece. Cross-lane (calib engine owner). |
| **Track P — collapse 3 evidence paths → 2 (GPU-only)** ✅ **COMPLETE 2026-06-24** | P | **Staged migration, operator-approved 2026-06-23.** **Stage 1 ✅ DONE 2026-06-24:** (1a, additive) GPU/TRT detect-cache — `_run_yolo_and_track` capture hook + `replay_gpu_cached` + `build_cache_gpu`/`replay_from_cache_gpu`; **fidelity proven** = direct `--trt`, 0 mismatched on all 3 goldens (`test_gpu_cache_fidelity.py`, gated). (1b, IRREVERSIBLE checkpoint) **golden trio re-baselined onto TRT** (`test_regression_replay` runs `--trt`; goldens now **engine/driver-locked**; texture-aerial real 2→4 etc. — the CPU goldens weren't the show path). **Stage 2A ✅ DONE 2026-06-24:** `ScenarioEnv` (tune + sensitivity) re-pointed onto the GPU/TRT cache (smoke green). **⇒ known-N (K1) is now UNBLOCKED** — the search runs on the show path + goldens are on TRT. **Stage 2B ✅ DONE 2026-06-24:** deleted the CPU-only surface (~360 LOC: `_process_cpu`/`_track_detections`/`_offset_detections`/`_identity_scaled_track`/`_OffsetMotionProxy` + the CPU dispatch/fallbacks in `process()`/`process_gpu_direct` + `_is_cuda_kernel_compat_error`/`_disable_gpu_path_and_fallback`) + the CPU detect-cache fns + the parity test + `--cpu` mode; `replay`/`tune`/`overlay` default to GPU. All gated tests green (goldens 3/3 + fidelity 2/2 + detect-cache full-vs-cache on TRT); unit 354. **Stage 3 ✅ DONE 2026-06-24:** the readiness `check_tensorrt` gate is now **fail** (was warn) when TRT is requested but inactive — distinguishing engine-MISSING (→ build it) from present-but-not-loaded; `_build_readiness_report` feeds engine presence. **⇒ Track P COMPLETE** (GPU+TRT is the single evidence base; ~700 net LOC removed across 2B). |
| **Logging & diagnostics** | TODO P8 | Per-show timestamped log folder; CSV metrics (FPS/latency/brightness/track-count/dropped); snapshot profile on Go-Live (reproducibility); end-of-show summary. Builds on the existing JSONL session logging. |

### 3.3 LATER — research-first, nothing committed

> **⚠ RESEARCH-FIRST (operator directive 2026-06-15).** Each lead is gated by a scoped
> investigation that (a) measures the benefit through the full pipeline vs the pass lines,
> (b) is replay-gated, (c) clears an explicit go/no-go **before any implementation.** Default =
> don't touch the pipeline.

**⭐ The next leap — IR retroreflective markers** (Track D primary; full plan
[TRACKING_ROBUSTNESS.md](TRACKING_ROBUSTNESS.md)). A marker is the **positive "this IS a dancer"
signal** the system lacks today — it hits all three pains at once: continuity (a saturated retro
point ~never drops), ghosts (texture has no marker), and **static** (detectable with zero motion).
Additive/no-regression (markerless = today's pipeline). Audience-invisibility is physics
(retroreflection is directional). **Gated on Phase 0a (§3.1).** Phase 1 build = new
`core/marker_detector.py` (threshold→blobs→centroids) + high-confidence fusion in `tracker.py` +
per-venue brightness threshold in the calib flow; OSC contract unchanged.

**Secondary — corpus-trained IR detector** (reframes the old Track D #1 fine-tune). Train **once**
on the accumulated IR corpus → a domain-general IR-aerial *person/centroid* detector (boxes, not
17-kpt — cheaper to label, and position+identity is what continuity needs). No on-site data is
needed/possible (new venue per show). Bounded feasibility = Phase 0c (`tmp_analysis/marker_spike.py`
neighbourhood; not yet run). Ranks below markers (lifts the floor, doesn't solve *static* as
cleanly).

**Track D SNR / detection-quality leads** (ranked by leverage; each research-gated):
2. Motion-gated temporal denoise (average static regions, keep movers sharp; upgrade the naive
   whole-frame EMA — stay frame-independent on the motion feed, bug #1).
3. Native-bit-depth tone-map (curve in Mono10/12, quantize last — near-free SNR in the 1–5/255
   regime).
4. Optical-flow coherence on the motion path (sparse LK = "coherent motion?" — stronger ghost/real
   discriminator + better bridge prediction).
5. Clean-plate static-person path (`background.py` exists, dormant — pairs with C-next).
6. IR-PSF sharpening / mild deconvolution (Tamron glass isn't IR-corrected → soft IR focal plane).
7. 3-frame difference (cleaner moving-object isolation; cheap).
8. Motion-ROI tiling for small-far dancers (pairs with the 4K-tiling TODO).
9. Surface untuned knobs: NMS/IoU, keypoint-conf floor, latency-tolerant multi-scale/TTA.

*(Rejected: edge/gradient as a YOLO input — COCO-RGB domain mismatch. Demoted: constrained-dynamics
"pendulum" prior, ROI spot-recheck standalone, 4×640 tiling, radio/UWB beacons, learned/LSTM
motion — reasons in TRACKING_ROBUSTNESS.)*

**Track X X-4 — steady high-rate OSC resampling** (resample the smoothed trajectory to a fixed
output rate regardless of YOLO cadence; output-side interpolation — unbuilt).

**TODO Phase 9 enhancements** (lower priority): OSC **status broadcasting**
(`/walldance/status/{state,fps,tracks,errors}` + heartbeat); TouchOSC bidirectional control;
standalone OSC record/playback tool; video+OSC synchronized record for offline replay; IDS crop
ratio buttons; standard-webcam path auto-detect; check-for-updates on startup; 4K tiling; rotate
playback (90°); Nuitka/Windows launcher build.

### 3.4 Hardware-blocked

- **P1.3 — Add IR illuminators, then raise confidence.** Rig for *even* coverage, raise
  `YOLO_CONFIDENCE`, measure the ghost drop on a recorded ghost-heavy session. The root cause in §2
  made concrete (raw IR ≈ 5/255 today).
- **Procurement still needed:** IP66 camera housing · mounting hardware (tripod/rigging). *(Camera,
  lens, IR filter, illuminator, laptop, USB3 extension already purchased — see [TODO.md](TODO.md).)*

---

## 4. Simplification path

Explicit cleanup backlog (deduplicated from the old Tracks/perf-backlog). None blocks a show;
do alongside adjacent work, replay-gated where behavior-touching.

| Item | Win | Risk |
|------|-----|------|
| ~~**Track P (3→2 GPU collapse)**~~ ✅ **DONE 2026-06-24** | ~700 net LOC gone (CPU front-end + dual coord transforms + parity test); "test what you ship" | Completed as a staged migration (Stage 1 GPU cache → 1b TRT golden re-baseline → 2A harness re-point → 2B CPU deletion → 3 engine gate). Full record in **§3.2 → Track P**. |
| ~~**Delete the 22 import shims**~~ ✅ **done 2026-06-22** | Removed the `application/src/*.py` → `core/`/`camera/`/`services/` aliasing layer (22 files) | Migrated 51 bare-name import sites (app.py/gui.py + 18 test files) to package paths first; app/gui/main import clean, 351 tests + goldens byte-identical |
| **Remove the `TrackingMode` enum** (P3 merged — detection no longer bifurcates) | Deletes a vestigial toggle | ⚠ **bigger than it looks (71 refs** across config/tracker/pipeline/motion_detector/app, incl. hot code) — own focused effort, not a quick cleanup; confirm the learn-rate path first |
| **Remove `MotionModel.detector` compat shim** (P3-deferred consumer migration) | One fewer indirection in the motion feed | Consumer-migration refactor in the motion feed (`pipeline` crossval/bridge views read `motion_model.detector`) — own effort; migrate consumers first |
| **Retire dead knobs** (Track S "drop" tier) | auto-exclusion builder (already inert), `bg_subtract` → clean-plate path (Track D #5), duplicated `tracker_max_age` defaults, `tracker_smoothing` (G4: truly Fixed, no config key) | Low — read-mostly removals |
| **Perf backlog (opportunistic, replay-gated):** ~~P-1 mono-aware gray path~~ ✅ + ~~P-2 persistent motion-feed worker~~ ✅ **done 2026-06-23** (P-1: IDS `mono_raw` fast path takes the single channel — bit-identical since R==G==B; P-2: one `ThreadPoolExecutor` worker replaces the per-frame `threading.Thread`). Remaining: P-3 frame-diff resolution cap · P-4 blur-after-downscale · P-7 tiered motion cache (dev-loop) · P-8 Python 3.12 venv · P-9 calib-window sweep pruning | ms/frame + dev-loop speed | done two were bit-identical (goldens 3/3 + GPU parity green); P-3/P-4 re-baseline goldens; P-8 re-verify GPU wheels |
| **P-5 OSC bundling** — ⏸ **PARKED** (consumer-gated) | One timestamped bundle/frame → TouchDesigner gets an atomic frame instead of 1+4n datagrams | Needs the TouchDesigner patch to accept OSC bundles (out of reach now). Internally bit-identical; revisit when the consumer side can change |
| **`install.sh` cu130 fallback footgun** | Driver reports CUDA True while ops crash → fallback never triggers | Low-priority hardening |

---

## 5. Open bugs

Full prose: [ENGINEERING_RECORD §8](archives/ENGINEERING_RECORD.md). **The §5 cluster is now clear.**

*Fixed 2026-06-23 (bit-identical; goldens 3/3 byte-identical):*
- **#3** — `_extract_transfer_timing` now reset at the top of `_extract_detections`, so a
  zero-detection frame reports no stale GPU→CPU transfer timing.
- **#12a** — removed the dead `new_imgsz != settings.imgsz` reload term in `_execute_project_switch`
  (always False; PT applies imgsz at call time, TRT is force-reloaded below).
- **#12b** — the scene-calibration toast now says "keep the stage clear" (Aim is a clear-stage pass;
  height is Calib2-owned), consistent with the servo toast.
- **#12f** — the ROI-rect re-application in `_apply_config_without_model` is now guarded on ROI-key
  presence, so a partial profile-bundle apply (profile switch) leaves the shared ROI untouched.

*(Earlier: #12d — the `config.py` OSC comment matches the wire format `/walldance/dancer/centroid
[id,x,y]`, fixed 2026-06-22.)*

---

## 6. Historical record — condensed index (anchors → ENGINEERING_RECORD)

Code comments cite these labels. Full prose:
**[archives/ENGINEERING_RECORD.md](archives/ENGINEERING_RECORD.md)**.

**Shipped milestones:** **P0** smartphone monitor + focus/lighting · **P1.4** exclusion mask
(shipped, then reversed to manual paint — decision 5) · **P2** Go-Live auto-calibration · **P3**
motion-subsystem simplification (one `MotionModel`, frame-diff = the ghost killer) · **P4**
regression fixtures + transform tests · **§7B** startup project picker. Corpus **Phase 0** (12
manifests) · **Phase 1** (per-project config repair) · **Phase 2** ①–⑧ (warmup intermittent /
takeover merge / MAX_PERSONS cap / manual exclusion / calib2 amendments / sensitivity span /
corrector relaxation) · **Phase 2b** (imgsz×model grid — target 110, dark 45, keep yolo11).

**Resolved bugs (index):** #1 CLAHE-noise motion feed (gamma-only) · #2 two-MOG2 collapse · #4
frame-diff stale-pair cap · #5 transform tests · #6 calib save semantics · #7 `_safe_defaults`
listing · #8 sensitivity var-anchor ratchet · #9 letterbox pad @ scale=1 · #10 GPU/CPU post-YOLO
unification · #11 box-conf seed · #12c MAX_PERSONS cap · **#12d OSC comment drift (fixed
2026-06-22)** · #13 replay config pinning · #14 warmup intermittent-confirm. *(Still open: #3, #12a,
#12b, #12f — see §5.)*

**Tracker lessons (§8):** post-hoc swap correction is fragile (pre-assignment gates are robust);
merge-frame inflation is the #1 identity killer; Kalman velocity amplifies on convergence; skeleton
similarity masks centroid jumps; the JSONL log is essential. Key gates kept:
`TRACKER_MAHALANOBIS_GATE=16.27`, `TRACKER_MAX_DISPLACEMENT_RATIO=0.5`,
`TRACKER_CLOSE_PROXIMITY_RATIO=0.35`.

---

## 7. Doc map

See **[README.md](README.md)** for the full index. Live companions to this roadmap:

| Doc | Role |
|-----|------|
| [TODO.md](TODO.md) | Build/hardware checklist (phase inventory + procurement) |
| [TRACKING_ROBUSTNESS.md](TRACKING_ROBUSTNESS.md) | The IR-marker next-leap plan (§3.3) |
| [OSC_CONTRACT.md](OSC_CONTRACT.md) | Wire-level `/walldance/*` output contract (canonical) |
| [NEW_SHOW.md](NEW_SHOW.md) | Operator field playbook (the spine ①→⑥) |
| [CORPUS_ANALYSIS.md](CORPUS_ANALYSIS.md) | Measured scene physics + the regression corpus |
| [TUNING.md](TUNING.md) · [OPTICS.md](OPTICS.md) · [CHECK_TEST.md](CHECK_TEST.md) | Tuning toolchain · lens envelopes · pre-show test procedure |
| [GUI_STACK_AUDIT.md](GUI_STACK_AUDIT.md) | Stay-Python/DPG decision record |
| [archives/ENGINEERING_RECORD.md](archives/ENGINEERING_RECORD.md) | Full shipped-detection record (P0–P4, bugs, lessons) |
| archives/ (OPERATOR_V2, UX_PLAN, DECOMPOSITION_PLAN, KNOBS, TRACK_X_SMOOTHER, CALIB_DETECTION_FIX_PLAN, …) | Superseded design docs |

**Key code:** [app.py](../application/src/app.py) (composition root) ·
[core/pipeline.py](../application/src/core/pipeline.py) (enhance→YOLO→motion→track) ·
[core/tracker.py](../application/src/core/tracker.py) (identity) ·
[core/calibration.py](../application/src/core/calibration.py) +
[core/calib2.py](../application/src/core/calib2.py) (calibration) ·
[core/osc_output.py](../application/src/core/osc_output.py) (OSC) ·
[core/config.py](../application/src/core/config.py) (constants).
