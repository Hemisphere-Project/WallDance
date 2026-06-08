# WallDance — Detection Robustness & Set-and-Forget Plan

**Date:** 2026-06-08
**Status:** Active. Supersedes the *zero-swap / slot-7* framing of [TRACKING_PLAN.md](TRACKING_PLAN.md) (which remains valid as a history of the tracker's association internals, but is **no longer the north star**).
**Companion:** [AUDIT.md](AUDIT.md) (maintainability: tests/CI, config validation, launcher safety, repo size — still endorsed).

---

## 0. North star

A WallDance operator should be able to: rig the camera, aim the IR light, press **one calibration button**, and get robust detection for the whole show — without tuning knobs per venue. "Set and forget" = **one explicit, logged calibration, then stable**, *not* continuous silent auto-tuning.

The current system is excellent engineering but optimizes for the wrong target. This plan re-points it.

---

## 1. Field constraints (confirmed with operator, 2026-06-08)

These reweight everything below. They are recorded in agent memory under `walldance-field-constraints`.

| Question | Answer | Consequence |
|----------|--------|-------------|
| Worst field pains | **Ghosts, drops, setup time** (NOT ID swaps) | Stop optimizing swaps to zero; spend the budget on ghosts/drops/setup |
| Scene stability | **Fixed per show, re-rigged often** | Calibrate-on-Go-Live is the right model; in-show background modeling + auto exclusion masks are safe |
| IR hardware appetite | **Willing to add illuminators** | Weight root-cause (better SNR) over software compensation |
| OSC consumer needs | **Positions + rough identity OK** | Occasional ID swaps are acceptable; pose/centroid quality matters more than identity permanence |

---

## 2. Diagnosis: the compensation cascade

The detection stack has accreted as a chain where each layer patches the weakness of the one below:

```
Poor / uneven IR lighting on big scenes        ← root cause
  → low YOLO contrast & confidence
    → YOLO_CONFIDENCE lowered to 0.25 (to catch awkward poses)
      → background ghost floods (trees, balcony, wall paint, shadows)
        → MOTION_CROSSVAL layer (reject non-moving ghosts via MOG2)
          → crossval kills real dancers who stand still
            → BYPASS + CONFIDENT + HYSTERESIS + STICKY exceptions
              → crossval "death spirals" (0 confirmed tracks)
                → REACQUIRE escape mode
                  → motion bridge (survive YOLO gaps)
                    → bridge Kalman drift
                      → velocity friction + progressive R inflation + presence/frame-diff tiers
```

Each arrow is individually reasonable. The **sum** is ~90 interacting constants in [config.py](../application/src/config.py) (GUI exposes ~50 live controls), most fit to the **p99 of a single 700-frame clip** (slot 7, tango-phone). That is overfitting — acknowledged in TRACKING_PLAN's own "Lessons Learned." A new venue lands outside the fitted distribution, so it "needs tuning." **That is the setup-time pain, by construction.**

**Strategic implication:** the cheapest intervention is at the *top* of the cascade. Better IR → higher confidence → raise the YOLO threshold → most ghosts die at the source → whole lower layers become optional.

---

## 3. The two reframes that fall out of §1

### 3a. Stop fighting ID swaps so hard
The OSC consumer only needs *positions + rough identity*. Yet the most fragile, most overfit code in the repo is the swap-correction machinery: `_check_occlusion_cascade_swaps`, `_check_merge_direction_swaps`, `_check_two_opt_swaps`, plus the `TRACKER_MAHALANOBIS_GATE=16.27` and `TRACKER_MAX_DISPLACEMENT_RATIO=0.5` gates fit to slot-7. TRACKING_PLAN itself calls post-hoc swap correction "inherently fragile."

**Action:** keep a simple identity layer (Kalman + Hungarian + sane gates), **relax or disable** the post-hoc swap correctors, and reclaim the complexity budget. Accept occasional swaps. This makes the system simultaneously more robust *and* easier to set up.

### 3b. Separate the ghost axis from the drop axis
Ghosts want a **high** confidence threshold; drops want a **low** one. A single threshold cannot win under uneven IR. Decouple:

- **Reject ghosts by location + stationarity** (auto exclusion mask; scenery does not move like a person) — not by confidence.
- **Keep confidence low enough** to catch awkward / still / far / edge dancers (drops).
- **Let better IR lift the whole confidence distribution** so a single threshold cleanly separates real from ghost.

All three field answers point the same way: **better lighting + spatial ghost rejection + calibrate-on-Go-Live** attack ghosts, drops, and setup *at once*, without per-session tuning.

---

## 4. Roadmap

### P0 — Remove friction, measure reality  *(started 2026-06-08)*
1. **Smartphone monitor + focus score.** Lightweight MJPEG server (stdlib, no new deps) streaming the existing downscaled preview to a phone on the LAN/laptop-hotspot, with a variance-of-Laplacian focus number + peak-hold bar + zoomed center inset. Solves "set focus from 2 m away." → `application/src/web_monitor.py`.
2. **Lighting readout on the same view.** Brightness, clip hi/lo %, luma histogram, and a **uniformity** metric with the darkest-tile marked — so illuminators are aimed for *even* coverage, not just brightness (MOG2 hates gradients more than dark). Same module.

> **Status: DONE (2026-06-08).** Shipped in `application/src/web_monitor.py`, plus a top-bar **QR button** to open it from a phone. Beyond the prototype it gained a **Focus mode**: auto histogram-stretch / manual-gain brightening for the dark IR image, yellow **focus peaking**, and a responsive **focusness gauge** with peak-hold (sharp→peak, defocus→~0). Toggle via `WEB_MONITOR_ENABLED` in config.py; opens on `http://<laptop-ip>:8080/`. Open the port on the host firewall: `sudo ufw allow 8080/tcp`.

### P1 — Attack ghosts + drops at the root
3. **Add IR coverage** (hardware), then raise `YOLO_CONFIDENCE` and measure the ghost drop on a recorded ghost-heavy session.
4. **Auto exclusion mask on Go-Live.** Grid cells with persistent MOG2 motion but ~never a confirmed skeleton → masked. Safe because the scene is fixed per show. Replaces most of what `_crossval_motion_filter` does today, at the source. (This is TRACKING_PLAN "Phase 4 ghost suppression," moved upstream.)

### P2 — Make setup automatic  *(in progress — separate agent, 2026-06-08: "Go-Live scaffolding")*
5. **Auto-calibrate** on Go-Live: `PERSON_HEIGHT_PX` = median YOLO detection height (set min/max ratios from the spread); MOG2 `varThreshold` from measured background noise σ; report exposure convergence + achieved FPS. Removes the biggest manual knobs. Keep it explicit and logged.

> Status: **scaffold implemented (2026-06-08).** A dedicated **Calibrate** button (Show Settings) runs a short collection window with YOLO forced on — works live *or during recording playback* — and sets `PERSON_HEIGHT_PX` (median detection height), `person_height_min/max_ratio` (p05/p95 of the spread), and the MOG2 base `varThreshold` ((N·σ)² from the median per-pixel temporal-noise σ), plus an exposure-stability / achieved-FPS report. Apply-then-confirm: values apply to the session, the operator sees a result dialog and chooses **Save to project** vs **Keep session**. Persists via `_get_saveable_config` / `_apply_config_without_model`. Core in [calibration.py](../application/src/calibration.py) (`SceneCalibrator`), `AUTOCAL_*` knobs in config.py, unit-tested in [tests/test_calibration.py](../application/tests/test_calibration.py). Background-noise σ is decoupled from the per-frame adaptive low-light path via `MotionDetector.set_var_threshold`. **Next:** this collection phase is the hook P1.4 (auto exclusion mask) builds on; and validate the σ→varThreshold constant on a ghost-heavy recording.

### P3 — Simplify (now that root causes are handled)  *(design: [P3_FUSION_SIMPLIFICATION.md](P3_FUSION_SIMPLIFICATION.md))*
6. **Collapse the two per-frame MOG2 models** ([bridge @0.001 + crossval @0.005](../application/src/pipeline.py#L353)) into one signal; fold crossval + bridge into **source-weighted Kalman measurements** (one association step, not a 7-step + 3-tier decision tree).
7. **Decommission/relax** the swap correctors and slot-7-derived gates (per §3a); replace with a few generalizable rules.

### P4 — Lock it in
8. **Regression fixtures** from 2–3 recorded sessions (the JSONL logging + [analyze_session.py](../application/analyze_session.py) already exist — this is half-built). Add tests for the ROI→letterbox→unscale coordinate transforms and config validation (per AUDIT.md).

### Progress log
- **2026-06-08**
  - **P0 DONE** — smartphone web monitor + Focus mode + top-bar QR button.
  - **Startup crash fixed** — `kornia_rs` AVX2 SIGILL on the dev box (see §5b).
  - **All TensorRT engines rebuilt** for **yolo11 and yolo26** (n/s/m/l/x × {640,800,960,1280,1536,1920}) on TRT 11.0.0.114; the stale-engine breakage is resolved. `build_engines.{sh,bat}` now fetch/harvest yolo26; yolo26 weights moved into `models/`.
  - **P2 started** by a separate agent (Go-Live scaffolding).
  - **P3 design** captured in [P3_FUSION_SIMPLIFICATION.md](P3_FUSION_SIMPLIFICATION.md) (analysis only — implementation deferred to avoid colliding with the P2 agent; see that doc's collision map).
  - Still open: yolo26↔yolo11 A/B on a recorded session; P1 (IR + auto exclusion mask); P3/P4 implementation.

---

## 5. Bugs & smells found during the audit

| # | Severity | Location | Issue |
|---|----------|----------|-------|
| 1 | Medium (design) | [pipeline.py `_enhance_gray_for_motion`](../application/src/pipeline.py#L988) | Per-frame adaptive CLAHE+gamma is applied to the gray frame **before** MOG2. CLAHE amplifies noise differently each frame, fighting MOG2's stationary-background assumption; and an *enhancement* slider silently changes *tracking* behavior. Feed MOG2 a fixed (linear / fixed-gamma) gray, decoupled from display enhancement. |
| 2 | Medium | [pipeline.py:353](../application/src/pipeline.py#L353) | Two full MOG2 models run every frame for one signal (differ only in learn rate). Redundant CPU + redundant tuning surface. |
| 3 | Low | [pipeline.py:1218](../application/src/pipeline.py#L1218) | `_extract_transfer_timing` is assigned inside the per-detection inner loop and never set on zero-detection frames (stale timing). Cosmetic. |
| 4 | Verify | [motion_detector.py:237](../application/src/motion_detector.py#L237) | Frame-diff buffer (`_curr_raw`/`_prev_raw`) only advances when `peak_diff > 8`, so during a static stretch both buffers go stale. Probably intentional; confirm it doesn't mis-bridge. |
| 5 | Verify (untested) | `_LetterboxMotionProxy` / `_OffsetMotionProxy`, [`_crossval_motion_filter`](../application/src/pipeline.py#L1360) | The ROI→letterbox→unscale transform chain is correct as far as traced, but has zero tests — a classic off-by-a-transform hazard. Cover in P4. |

---

## 5b. Environment / install findings

- **`kornia_rs` SIGILL on non-AVX2 CPUs (FIXED 2026-06-08).** `kornia` transitively imports `kornia_rs` (Rust image I/O) via `kornia.io`. Its prebuilt wheel (0.1.14) is compiled with AVX2 and crashes the whole process with `Illegal instruction` (SIGILL, exit 132) on CPUs without AVX2 — the **dev box is an Ivy-Bridge i7-3770K (AVX only)**. The crash happens at `gpu_pipeline.py` import, right after the `[Enhancer] … CUDA available` print, and is uncatchable by `try/except ImportError`. WallDance never uses kornia file I/O, so the fix stubs `kornia_rs` in `sys.modules` before importing kornia ([gpu_pipeline.py](../application/src/gpu_pipeline.py#L67)). The GPU pipeline + kornia GPU-CLAHE remain fully functional. Harmless on the production RTX 5080 laptop (which has AVX2). Verified: app boots to runtime with `GPU pipeline active`.
- **`install.sh` always installs the `cu130` torch index** and only falls back to older CUDA wheels when `torch.cuda.is_available()` is *False*. On the dev box this is fine (driver 580.159 supports CUDA 13.0). Noted as a latent footgun: if a target machine's driver is older than its `cu130` requirement, `is_available()` can still report `True` while CUDA ops crash — the fallback ladder would never trigger. Lower priority than the kornia fix.

## 6. Open questions still worth answering

- Typical dancer **size range** (px) and **count** across venues — does one config plausibly generalize, or do we need a small per-scale preset set?
- How bad are the ghost floods quantitatively (ghosts/minute) on a representative bad scene? Sets the bar for P1.4.
- Current setup ritual end-to-end (who, how long, which knobs touched) — confirms which manual steps P2 must eliminate first.
- What does "good enough for a show" mean numerically (acceptable missed-dancer seconds, acceptable ghost rate)? Gives the regression fixtures (P4) a pass/fail line.
