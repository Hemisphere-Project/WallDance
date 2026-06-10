# WallDance Roadmap

**Date:** 2026-06-09 (P3 motion-subsystem simplification landed on branch `p3-motion-simplification`)
**Status:** Single source of truth. Merges and supersedes `ROBUSTNESS_PLAN.md` (detection/setup north star), `AUDIT.md` (maintainability), `P3_FUSION_SIMPLIFICATION.md` (P3 design), and the tracker history in `TRACKING_PLAN.md` — all now under [archives/](archives/).
**Companion:** [TODO.md](TODO.md) — the granular build / hardware checklist (a different altitude; not duplicated here).

Two parallel tracks run through this roadmap: **Detection & Setup** (make it robust and set-and-forget) and **Maintainability** (make it safe to evolve). They are sequenced together in §4.

---

## 0. North star

A WallDance operator should: rig the camera, aim the IR light, press **one calibration button**, and get robust detection for the whole show — without tuning knobs per venue. "Set and forget" = **one explicit, logged calibration, then stable**, *not* continuous silent auto-tuning.

The system is excellent engineering that historically optimized for the wrong target (zero ID-swaps on one clip). This roadmap re-points it at the real field pains.

---

## 1. Field constraints (operator-confirmed 2026-06-08)

| Question | Answer | Consequence |
|----------|--------|-------------|
| Worst field pains | **Ghosts, drops, setup time** (NOT ID swaps) | Stop optimizing swaps to zero; spend the budget on ghosts/drops/setup |
| Scene stability | **Fixed per show, re-rigged often** | Calibrate-on-Go-Live is the right model; in-show background modeling + auto exclusion masks are safe |
| IR hardware appetite | **Willing to add illuminators** | Weight root-cause (better SNR) over software compensation |
| OSC consumer needs | **Positions + rough identity OK** | Occasional ID swaps are acceptable; pose/centroid quality matters more than identity permanence |

---

## 2. Diagnosis — the compensation cascade

The detection stack accreted as a chain where each layer patches the weakness below it:

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

Each arrow is individually reasonable. The **sum** is ~90 interacting constants in [config.py](../application/src/config.py), most fit to the **p99 of a single 700-frame clip** (slot 7, tango-phone). That is overfitting — a new venue lands outside the fitted distribution, so it "needs tuning." **That is the setup-time pain, by construction.**

**Strategic implication:** the cheapest intervention is at the *top* of the cascade. Better IR → higher confidence → raise the YOLO threshold → most ghosts die at the source → the lower layers become optional.

---

## 3. The two reframes

**3a. Stop fighting ID swaps so hard.** The OSC consumer only needs *positions + rough identity*, yet the most fragile, most overfit code is the swap-correction machinery (`_check_occlusion_cascade_swaps`, `_check_merge_direction_swaps`, `_check_two_opt_swaps`, the slot-7 `TRACKER_MAHALANOBIS_GATE=16.27` / `TRACKER_MAX_DISPLACEMENT_RATIO=0.5` gates). Keep a simple identity layer (Kalman + Hungarian + sane gates); **relax or disable** the post-hoc correctors; accept occasional swaps. Simultaneously more robust *and* easier to set up.

**3b. Separate the ghost axis from the drop axis.** Ghosts want a **high** confidence threshold; drops want a **low** one. A single threshold cannot win under uneven IR. Decouple: reject ghosts **by location + stationarity** (auto exclusion mask), keep confidence **low** enough to catch awkward/still/far dancers, and let **better IR** lift the whole distribution so one threshold cleanly separates real from ghost.

All field answers point the same way: **better lighting + spatial ghost rejection + calibrate-on-Go-Live** attack ghosts, drops, and setup *at once*, without per-session tuning.

---

## 4. Status at a glance

| Item | Track | Status | Notes |
|------|-------|--------|-------|
| **P0** Smartphone monitor + focus/lighting | Detection | ✅ Done | [web_monitor.py](../application/src/web_monitor.py); MJPEG + variance-of-Laplacian focus + uniformity/clip/histogram |
| **P1.3** Add IR, raise confidence | Detection | ⛔ Hardware-blocked | Needs illuminators rigged, then measure ghost drop on a recording |
| **P1.4** Auto exclusion mask | Detection | ✅ Done | Built in the Calibrate window; validated (0 ghost cells on clean footage); **untested on real ghosts** |
| **P2** Go-Live auto-calibration | Detection | ✅ Done | Height/ratios + empirical FP-sweep varThreshold + exposure/FPS report; apply-then-save |
| **P3** Motion-subsystem simplification | Detection | ✅ Done (branch `p3-motion-simplification`) | One `MotionModel` (1 MOG2 + frame-diff), scored detection gate, merged YOLO/Motion-First, source-weighted measurement, simplified bridge; see §5 P3. **Slot-7 corrector relaxation deferred** (separate step). |
| **P4** Regression fixtures + transform tests | Both | ✅ Core done | Replay harness ([replay.py](../application/tests/replay.py)) + golden drop/ghost/swap fixtures (residence1-solo slots 3&4, opt-in `WD_RUN_REPLAY=1`) + transform tests ([test_transforms.py](../application/tests/test_transforms.py)). Broaden the footage set next. |
| Tests + CI | Maint. | ✅ CI live | GitHub Actions runs `tests/` on push/PR (import-light); replay regression is opt-in/GPU. |
| Typed config validation + versioning | Maint. | ⬜ Not started | New calibration keys need a schema (§6) |
| `app.py` decomposition | Maint. | ⬜ Not started | Grew to ~3990 ln (was ~3031 at audit) |
| Launcher update safety | Maint. | ⬜ Not started | Force-sync can clobber local field tweaks |
| Model-artifact footprint | Maint. | ⬜ Not started | ~2.4 GB of binaries in-repo |
| Startup project picker | Enhancement | ✅ Done | §7B; shipped (`config_store.rename_project`/`delete_project`, modal picker, Enter-launch) |
| **Production UX track (U0–U5)** | UX | ✅ Built (branch `ux-track`) | See [UX_PLAN.md](UX_PLAN.md): expert mode, lighting profiles, calib1 (scene/servo/joint sweep), calib2 (evidence pool/imgsz), sensitivity macro. Numeric rules provisional → re-fit on annotated footage |

---

## 5. Roadmap — Detection & Setup

### P0 — Remove friction, measure reality — ✅ Done
Smartphone MJPEG monitor with a variance-of-Laplacian focus score (peak-hold + zoomed center inset) and a lighting readout (brightness, clip %, luma histogram, uniformity with the darkest tile marked). Solves "set focus and aim IR from 2 m away." Toggle via `WEB_MONITOR_ENABLED`.

### P1 — Attack ghosts + drops at the root

**P1.3 — Add IR coverage (hardware), then raise confidence.** ⛔ Pending hardware. Rig illuminators for *even* coverage (MOG2 hates gradients more than darkness), raise `YOLO_CONFIDENCE`, and measure the ghost drop on a recorded ghost-heavy session. The raw IR is near-black today (calibration measured scene brightness ≈ 5/255) — this is the root cause in §2 made concrete.

**P1.4 — Auto exclusion mask on Go-Live.** ✅ Done. `ExclusionMaskBuilder` ([calibration.py](../application/src/calibration.py)) accumulates, over a 16×10 normalized grid during the Calibrate window, MOG2 foreground (tiled clean mask) + the positions of *kept* skeletons; a cell is masked if it moves in ≥30% of frames but holds a skeleton in ≤2% (scenery/ghost). Collected + applied at **both** crossval call sites via `FrameProcessor._exclusion_step` (GPU: letterbox scale/pad, `roi_local=True`; CPU: original space, `roi_local=False`), reusing the existing tracker→mask transform — no new coordinate code. Rejection is **guarded by proximity to a confirmed track** (a real dancer in a masked region is protected). Persisted as `exclusion_grid`/`exclusion_cells`. **Validated on playback: 0 ghost cells on clean tango footage (correct, no false masking); still needs a ghost-heavy recording to see it exclude.**

### P2 — Make setup automatic — ✅ Done
A dedicated **CALIBRATE** button (bottom bar, by STANDBY/RUN) runs a short window with YOLO forced on — works live **or during recording playback** — and sets the biggest manual knobs, then leaves them fixed. Apply-then-confirm: values apply to the session, a result dialog offers **Save to project** vs **Keep session**. Core: `SceneCalibrator` in [calibration.py](../application/src/calibration.py); `AUTOCAL_*` in config.py; persisted via `_get_saveable_config`/`_apply_config_without_model`; unit-tested.

| Knob | Source |
|------|--------|
| `PERSON_HEIGHT_PX` + min/max ratios | median + p05/p95 of YOLO detection heights |
| MOG2 `varThreshold` | **empirical background false-positive sweep** (see below) |
| Report (no apply) | exposure stability (σ/μ), achieved FPS, post-CLAHE noise σ diagnostic |

**Lesson — varThreshold is chosen empirically, not by formula.** A first `(N·σ)²`-from-noise map was tried and discarded: it saturated at a clamp either way (raw σ0.69→16, post-CLAHE σ4.23→120) because **MOG2 self-normalises** — `varThreshold` thresholds the Mahalanobis distance `(I−μ)²/σ²_model`, and MOG2 *learns* σ²_model, so a pixel-σ→varThreshold map is dimensionless. Replaced with a sweep: each candidate runs as its own MOG2 over the window, scored by the **median grid-tile foreground fraction** (robust to the dancer minority — no bbox/letterbox transform), picking the lowest candidate under `AUTOCAL_FP_TARGET` else the highest + a `saturated` flag. On dark tango footage it picked **varThreshold=16 @ 0.01% FP** — *more* sensitive than the old default 40, with evidence ghosts stay low. Noise/FP are measured on the **actual MOG2-input gray** (`FrameProcessor.get_last_motion_gray`); brightness on the raw frame. (Caveat: calibration MOG2 models use history=window(90) vs production 500 — watch early-show behaviour.)

### P3 — Simplify the motion subsystem — ✅ Done (branch `p3-motion-simplification`)

**Was:** three jobs across three files and ~90 constants — ghost rejection (`_crossval_motion_filter`, a 7-step tree), gap bridging (tracker `_lazy_bridge_with_motion`, a 3-tier cascade), cold motion-first detection (`_fuse_motion_blobs`) — fed by **two full MOG2 models per frame** (`bridge` @0.001 + `crossval` @0.005, differing only in learn rate).

**Now:** **one** `MotionModel` (one slow MOG2 silhouette + frame-diff "moving now?", surface `feed/reset/noise_sigma/foreground_blob(s)/foreground_ratio/recent_motion(_blob)`) feeding **source-weighted measurements** into the existing Kalman/Hungarian tracker. Key result: **frame-diff — not MOG2 foreground — is the ghost killer** (static textured background + lighting drift read as MOG2 foreground but show no frame-to-frame change). Each result below was measured on residence1-solo slots 3 & 4 via the replay harness.

| Stage | Done | Result |
|-------|------|--------|
| 0 | Replay harness + golden fixtures + transform tests (P4) | measurable refactor |
| 1 | `motion_model.py` over one MOG2 + frame-diff (unwired) | unit-tested |
| 2 | One `MotionModel` replaces both MOG2 (compat view shim) | **bit-identical** (2nd MOG2 was redundant) |
| 3a | Scored gate (skeleton OR frame-diff motion OR live-track) + **Bug #1 fix** (gamma-only feed, no adaptive CLAHE) — coupled | **swaps 18→0 / 5→0, ghosts↓, dancer retained** |
| 3b | Merge YOLO/Motion-First (§7A), gated cold detection, source-weighted R, removed redundant global-blob Hungarian bridge | no regression |
| 3c/3d | varThreshold self-adapts via calibration (no retune); retired the orphaned `MOTION_CROSSVAL_*`/bridge-helper constants | bit-identical |

**Deliberate deviation (evidence-driven):** the "collapse the bridge to one position-only measurement" target was **not** fully taken — the harness showed the presence + frame-diff bridge tiers actively prevent drops (the #1 field pain), so they were kept; only the genuinely-redundant global-blob Hungarian was removed.

**Deferred (not in P3):** removing the `MotionModel.detector` compat shim (a consumer-migration refactor); relaxing the slot-7 swap correctors (§3a); broadening the golden footage set (currently single-dancer motion_first only — needs a YOLO-dropout / multi-dancer / yolo_first clip to exercise the relay + cold-detection paths the current corpus leaves bit-identical).

### P4 — Lock it in
Regression fixtures from 2–3 recorded sessions (the JSONL logging + `analyze_session.py` already exist — this is half-built): golden drop/ghost/swap counts so refactors are measurable. Add tests for the ROI→letterbox→unscale coordinate transforms (a classic off-by-a-transform hazard, currently untested) and config validation. First tests landed ([test_calibration.py](../application/tests/test_calibration.py)).

---

## 6. Roadmap — Maintainability (audit track)

Condensed from the full audit (now [archives/AUDIT.md](archives/AUDIT.md)). The codebase is field-oriented and solid; the debt is in testability, config governance, architecture concentration, and updater/install safety — not obvious correctness failures.

**Progress since the audit (2026-06-08):** first tests now exist (dents the no-tests finding); new behavior went into an isolated, testable `calibration.py` rather than growing the big modules; several formerly-global tuning constants are now measured/logged/per-project. The large items below are unchanged.

| Priority | Item | Status | Note |
|----------|------|--------|------|
| 1 | Minimal test suite + **CI** | 🟡 tests started, no CI | Add config/model/tracker-scenario/OSC tests; wire CI smoke + unit |
| 1 | Typed config **validation + versioning** | ⬜ | Validate ranges on load; migrate saved project files; fold in the new `person_height_*_ratio` / `mog2_var_threshold` / `exclusion_*` keys |
| 1 | Launcher update safety | ⬜ | `launcher/git_manager.py` force-syncs to remote HEAD — refuse on dirty tree, prompt before destructive update |
| 1 | `run.sh` hardcoded `python3.10` lib path | ⬜ | Discover venv layout dynamically (pyproject allows 3.10–3.12) |
| 2 | Decompose `app.py` (~3990 ln) into controllers | ⬜ | Runtime / playback / model-loading / session services; modules have *grown* since the audit |
| 2 | Tracker scenario tests from known-hard sessions | ⬜ | Surround the tracker with reproducible tests before simplifying it (§8) |
| 2 | README / `projects/` layout doc | 🟡 verify | The old `configs/` drift appears resolved in README; re-check and document the `projects/` structure |
| 3 | In-repo model-artifact footprint (~2.4 GB) | ⬜ | Move to release assets / LFS |
| 3 | Unify install/update logic (scripts vs launcher) | ⬜ | One canonical policy, thin wrappers |
| 3 | Consolidate stale docs | 🟡 in progress | *This roadmap is part of that work* |

> **Operational reliability** — camera auto-reconnect, watchdog auto-recovery, FPS/temp/no-detection alerts, and a 4h stability test — is the largest open *ops* cluster. It is tracked in [TODO.md](TODO.md) Phase 7 (different altitude from this roadmap), not duplicated here.

---

## 7. Requested enhancements (backlog, 2026-06-08)

### A. Simplify the YOLO-First / Motion-First duality
`TrackingMode` (`YOLO_FIRST` / `MOTION_FIRST`) was a user-facing toggle that bifurcated the pipeline. **✅ Merged in P3 Stage 3b:** motion blobs are now always candidates (gated by frame-diff + exclusion) fed through one scored path — no bifurcation. The `TrackingMode` enum still exists for config/learn-rate compatibility but no longer changes the detection logic; fully removing it is a follow-up cleanup.

### B. Startup project picker (no silent auto-load)
Today the app auto-loads the last project (`config_store.read_last_project()` / `last_project.txt`); there is no launch-time way to choose or manage projects, and after a mid-show crash the operator is dropped straight back into auto-load.

**Requested behavior** — on start, do **not** auto-load; open a modal picker:
- **Projects ordered by last-save date**, most recent first (newest config mtime per project — `get_latest_config_in_project` / `project_history`).
- **Last project highlighted**; **Enter launches it** — the fast crash-recovery path to the last state.
- Per-project: **Launch**, **Rename**, **Delete** (delete behind a confirmation prompt).

Implementation notes: `config_store` already has `list_projects`/`project_history`/`latest_for_project`/`read_last_project`/`save`/`sanitize_project_name`; **`rename_project` + `delete_project` must be added** (move/remove `projects/<name>/`, fix `last_project.txt`). Reuse the existing GUI modal pattern; launch via the existing full project-switch path. Good place to **validate config on load** (§6) and surface stale configs. Keep an env/flag **escape hatch to auto-launch** the last project for unattended/kiosk boot.

---

## 8. Tracker — lessons + key gates (condensed)

The tracker is sophisticated and intentionally engineered (Kalman + cascaded Hungarian + dormant resurrection + post-hoc swap correctors). Full decision log, architecture, and slot-7 tuning history are in [archives/TRACKING_PLAN.md](archives/TRACKING_PLAN.md). The durable lessons:

1. **Post-hoc swap correction is inherently fragile** — timing-dependent on flag states; one fix triggers false positives elsewhere. Pre-assignment gates (Mahalanobis, displacement) are more robust. *(§3a wants to relax the correctors entirely.)*
2. **Merge-frame inflation is the #1 silent killer of identity** — ghost tracks from scenery count as "active" → `n_det < n_tracks` fires almost every frame → all tracks get merge context → swap detectors misfire. Count only established, recently-matched tracks. *(P1.4's spatial ghost rejection attacks this at the source.)*
3. **Kalman velocity amplifies during convergence** — two approaching tracks spike each other's velocity, making a tight Mahalanobis gate reject the correct match. Keep the gate generous; use a displacement cap for teleport protection.
4. **Skeleton similarity can mask centroid jumps** — a track can match a body-similar detection 75px away; the displacement gate enforces a hard centroid cap.
5. **The JSONL event log is essential** — every diagnostic insight came from `FRAME_SUMMARY` + per-session logs (`tracking_logger.py`).

Key gates (slot-7-derived — candidates for relaxation per §3a): `TRACKER_MAHALANOBIS_GATE=16.27`, `TRACKER_MAX_DISPLACEMENT_RATIO=0.5`, `TRACKER_CLOSE_PROXIMITY_RATIO=0.35`.

---

## 9. Bugs & smells

| # | Severity | Location | Issue |
|---|----------|----------|-------|
| 1 | ✅ Fixed (P3 3a) | `pipeline._feed_motion_detectors` | Adaptive CLAHE before MOG2 amplified noise per-frame → frame-diff read it as fake motion (admitted a ghost on slot 4). Now feeds **gamma-only** (fixed, frame-independent) gray. The harness proved this fix is inseparable from the scored gate. (`_enhance_gray_for_motion` is now dead — sweep with the compat-shim cleanup.) |
| 2 | ✅ Fixed (P3 2) | one `MotionModel` | Collapsed the two MOG2 into one slow model + frame-diff; bit-identical → the 2nd MOG2 was redundant. |
| 3 | Low | `_extract_transfer_timing` | Assigned inside the per-detection loop; stale on zero-detection frames. Cosmetic. |
| 4 | Verify | `motion_detector` frame-diff buffer | `_curr_raw`/`_prev_raw` only advance when `peak_diff > 8`; confirm it doesn't mis-bridge during static stretches. (Now the primary ghost/relay signal — re-check on the broader footage set.) |
| 5 | ✅ Covered (P3 0) | ROI→letterbox→unscale transform chain | Now under [test_transforms.py](../application/tests/test_transforms.py) (round-trip + crossval/exclusion transform). |

---

## 10. Environment / install findings

- **`kornia_rs` SIGILL on non-AVX2 CPUs (FIXED).** `kornia.io` pulls `kornia_rs`, whose AVX2 wheel crashes the process (`Illegal instruction`, exit 132) on the dev Ivy-Bridge i7-3770K. Fixed by stubbing `kornia_rs` in `sys.modules` before importing kornia ([gpu_pipeline.py](../application/src/gpu_pipeline.py)). GPU CLAHE/pipeline unaffected; harmless on the RTX 5080 laptop (has AVX2).
- **`install.sh` always installs the `cu130` torch index** and only falls back when `torch.cuda.is_available()` is False. Latent footgun: an older driver can report `True` while CUDA ops crash, so the fallback ladder never triggers. Lower priority.

---

## 11. Open questions

- Typical dancer **size range** (px) and **count** across venues — does one config generalize, or do we need a small per-scale preset set? (P2 now *measures* this per show.)
- How bad are ghost floods quantitatively (ghosts/minute) on a representative bad scene? Sets the bar for P1.3/P1.4 — **needs a ghost-heavy recording**.
- Current setup ritual end-to-end (who, how long, which knobs) — confirms which manual steps remain after P2.
- What does "good enough for a show" mean numerically (acceptable missed-dancer seconds, acceptable ghost rate)? Gives the P4 fixtures a pass/fail line.

---

## 12. Doc map

| Doc | Role |
|-----|------|
| **ROADMAP.md** (this) | Single source of truth — strategy, status, plan |
| [UX_PLAN.md](UX_PLAN.md) | Production operator UX — two-calibration design, profiles, build phases U0–U5 |
| [TODO.md](TODO.md) | Granular build / hardware checklist (live) |
| [archives/ROBUSTNESS_PLAN.md](archives/ROBUSTNESS_PLAN.md) | Original detection north star (merged here) |
| [archives/AUDIT.md](archives/AUDIT.md) | Full maintainability audit (condensed in §6) |
| [archives/P3_FUSION_SIMPLIFICATION.md](archives/P3_FUSION_SIMPLIFICATION.md) | Full P3 fusion design (condensed in §5 P3) |
| [archives/TRACKING_PLAN.md](archives/TRACKING_PLAN.md) | Full tracker decision log + lessons (condensed in §8) |
| archives/ (older) | IDS stall investigation, specifications, hardware guide, legacy proposal |

**Key code:** [app.py](../application/src/app.py) (orchestration), [pipeline.py](../application/src/pipeline.py) (enhance→YOLO→motion→track), [tracker.py](../application/src/tracker.py) (identity), [calibration.py](../application/src/calibration.py) (Go-Live calibration + exclusion mask), [motion_detector.py](../application/src/motion_detector.py) (MOG2), [web_monitor.py](../application/src/web_monitor.py) (phone monitor), [config.py](../application/src/config.py) (constants).
