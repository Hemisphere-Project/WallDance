# WallDance Roadmap

**Date:** 2026-06-10 (full code review; P3 + UX track U0–U5 are now **merged to `main`** — branch pointers below are historical)
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
| **P3** Motion-subsystem simplification | Detection | ✅ Done (merged to `main`) | One `MotionModel` (1 MOG2 + frame-diff), scored detection gate, merged YOLO/Motion-First, source-weighted measurement, simplified bridge; see §5 P3. **Slot-7 corrector relaxation deferred** (separate step). |
| **P4** Regression fixtures + transform tests | Both | ✅ Core done | Replay harness ([replay.py](../application/tests/replay.py)) + golden drop/ghost/swap fixtures (residence1-solo slots 3&4, opt-in `WD_RUN_REPLAY=1`) + transform tests ([test_transforms.py](../application/tests/test_transforms.py)). Broaden the footage set next. |
| Tests + CI | Maint. | ✅ CI live | GitHub Actions runs `tests/` on push/PR (import-light); replay regression is opt-in/GPU. |
| Typed config validation + versioning | Maint. | 🟡 Largely done (U2) | [config_schema.py](../application/src/config_schema.py): v2 profiles, migration, range clamps on load. Remaining: cross-field checks; surface warnings in the GUI (today console-only) |
| `app.py` decomposition | Maint. | ⬜ Not started | Grew to ~4456 ln (was ~3031 at audit) |
| Launcher update safety | Maint. | ⬜ Not started | Force-sync can clobber local field tweaks |
| Model-artifact footprint | Maint. | ✅ Resolved (verified 2026-06-10) | `models/` is gitignored; `.git` ≈ 69 MB. (8 GB on disk is untracked working data.) |
| Startup project picker | Enhancement | ✅ Done | §7B; shipped (`config_store.rename_project`/`delete_project`, modal picker, Enter-launch) |
| **Production UX track (U0–U5)** | UX | ✅ Merged to `main` | See [UX_PLAN.md](UX_PLAN.md): expert mode, lighting profiles, calib1 (scene/servo/joint sweep), calib2 (evidence pool/imgsz), sensitivity macro. Numeric rules provisional → re-fit on annotated footage |

### 4.1 Decisions + sequenced next steps (full review, operator-arbitrated 2026-06-10)

**Decisions locked:**

1. **Calibration persistence** — the calibration result dialog's "Save to project" must write a
   normal **timestamped project save** (what the picker / startup loads). `_safe_defaults.json`
   stays a *separate, explicit* action. (Today it silently writes safe-defaults only → a
   calibrate→Save→restart loses the calibration; bugs #6/#7.)
2. **Calib1 scope = camera + lighting only** (exposure/gain servo, gamma/CLAHE seed, var×scale
   sweep, exclusion mask, report card). The person-height measurement still living in
   `SceneCalibrator` moves out — **Calib2 owns all subject-derived knobs**. Fix the contradictory
   operator toasts at the same time (bug #12b).
3. **Corpus first** — recording + labeling broader footage outranks all other open work (it gates
   the provisional U4/U5 constants, the KNOBS "FIXED" verdicts, and the unexercised P3
   relay/cold/`yolo_first` paths).
4. **Full GPU/CPU post-YOLO unification** (not just a parity test) — see step 4 below for the
   risk plan.

**Sequence:**

| # | Step | Why this order |
|---|------|----------------|
| 1 | **Corpus**: on the real IDS rig, record ghost-heavy / multi-dancer / YOLO-dropout / `yolo_first` / small-far sessions into slots; label known-N scenarios (cheap: count per frame range, TUNING Phase A schema) | Keystone — everything numeric downstream re-fits against it |
| 2 | **Persistence fixes**: bugs #6 (save semantics), #7 (`_safe_defaults` hijacks "latest"), #8 (sensitivity var-anchor ratchet) — **✅ done 2026-06-10** (see §9) | Small diffs, high operator value — the "last mile" of the P2/U3/U4 investment |
| 3 | **Signal fixes**: bug #9 (letterbox pad @ scale=1) + bug #4 (frame-diff stale pair cap), each validated on replay + new transform test cases — **✅ done 2026-06-10** (see §9) | Correctness of the primary ghost/relay signal on the quiet Starvis2 scenes we are building toward |
| 4 | **Unify the post-YOLO path** (bug #10): extract one transform-parameterized chain (gate → exclusion → cold blobs → tracker → OSC) consumed by both `_process_cpu` and the GPU path. Risk plan: (a) land a CPU↔GPU parity replay test *first* (same frames through both paths, compare timelines) so the refactor is measurable; (b) goldens must stay bit-identical on the CPU path; (c) the letterbox proxies stay — only the orchestration unifies — **✅ done 2026-06-10** (see §9 bug #10) | Removes the "tuned path ≠ show path" blind spot; bug #9 lives in exactly this duplication |
| 5 | **Ops cluster** (TODO Phase 7, elevated): camera auto-recovery, watchdog, FPS/no-detection alerts, **4 h soak test**; plus a pre-Go-Live "show readiness" line (camera FPS, TRT active vs fallback, OSC reachable, calibration age + profile, disk space) | A USB3 stall at minute 40 is worse than any ghost; detection got the recent budget, ops did not |
| 6 | **Performance backlog** (§10) — opportunistic, replay-gated | None of it blocks a show today; do alongside 3–5 where touching the same code |

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

### P3 — Simplify the motion subsystem — ✅ Done (merged to `main`)

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
| 1 | Minimal test suite + **CI** | ✅ CI live | [.github/workflows/ci.yml](../.github/workflows/ci.yml) (3.10 + 3.12 matrix); 123 unit tests green 2026-06-10. Remaining: tracker-scenario + OSC tests; replay regression stays opt-in/GPU |
| 1 | Typed config **validation + versioning** | 🟡 largely done (U2) | `config_schema.py` v2: profiles, migration, range clamps. Remaining: cross-field checks; show load warnings in the GUI (console-only today) |
| 1 | Launcher update safety | ⬜ | `launcher/git_manager.py` force-syncs to remote HEAD — refuse on dirty tree, prompt before destructive update. (`check_updates` also reports "behind" when local is *ahead* — update would discard local commits) |
| 1 | `run.sh` hardcoded `python3.10` lib path | ⬜ | Discover venv layout dynamically (pyproject allows 3.10–3.12); same work item as the §10 Python-3.12 bump |
| 2 | Decompose `app.py` (~4456 ln) into controllers | ⬜ | Runtime / playback / model-loading / session services; modules have *grown* since the audit |
| 2 | Tracker scenario tests from known-hard sessions | ⬜ | Surround the tracker with reproducible tests before simplifying it (§8) |
| 2 | README / `projects/` layout doc | 🟡 verify | The old `configs/` drift appears resolved in README; re-check and document the `projects/` structure |
| 3 | In-repo model-artifact footprint | ✅ resolved | Verified 2026-06-10: `models/` gitignored, `.git` ≈ 69 MB |
| 3 | Untrack committed junk | ⬜ | `application/merge_dbg.log`, `application/src/tracking_events.jsonl` are tracked → perpetually-dirty tree when running; `git rm --cached` + gitignore |
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
| 3 | Low | `_extract_transfer_timing` | Assigned inside the per-detection loop; stale on zero-detection frames. Cosmetic. (Re-confirmed 2026-06-10, pipeline.py ~1425.) |
| 4 | ✅ Fixed (2026-06-10) | `motion_detector.feed_preprocessed` | `_curr_raw`/`_prev_raw` advance only when the *global* peak diff > 8 — on a clean static stretch the pair froze and `frame_diff_blob_in_bbox` reported the **last motion event's diff indefinitely** (frame-diff is the primary ghost-killer *and* relay signal; quiet Starvis2 + even-IR scenes are the at-risk regime). Now a frames-since-advance counter (`_diff_pair_age`, cap `MOTION_DIFF_PAIR_MAX_AGE_FRAMES=30`) zeroes the report past the cap, keeping the slow-mover accumulate bridge under it; `reset()` also clears the pair; age surfaced in `bridge_diagnostics`. Unit-tested (freeze→cap→revive); goldens unchanged on replay (noise self-heals on dirty footage, as predicted — the cap's positive effect needs the clean-scene corpus). |
| 5 | ✅ Covered (P3 0) | ROI→letterbox→unscale transform chain | Now under [test_transforms.py](../application/tests/test_transforms.py) (round-trip + crossval/exclusion transform). The `scale == 1.0` + nonzero-pad gap found 2026-06-10 is closed with the bug #9 fix (parametrized cases + a ground-truth pad test). |
| 6 | ✅ Fixed (2026-06-10) | `app.py _apply_calibration` → `_cb_save_safe_defaults` | The calibration result dialog's "Save to project" wrote `projects/<name>/_safe_defaults.json`, **not** a timestamped project save — calibrate → Save → restart **lost the calibration**. Now both calibration dialogs (calib1 + calib2 apply) route `on_save` to `_cb_save_config` (normal timestamped save, what startup/picker load); safe-defaults stays a separate explicit action. |
| 7 | ✅ Fixed (2026-06-10) | `config_store.get_latest_config_in_project` / `project_history` / `list_projects_by_date` | `_safe_defaults.json` matched the `.json` listing, and "latest" was a reverse **name** sort: uppercase letters sort before `_`, so for capitalized project names `_safe_defaults.json` won → startup silently loaded safe defaults. Now `list_config_files()` skips `_`-prefixed files and sorts by **mtime** (robust to renamed projects); all listing paths go through it; regression-tested. *Migration note: a project holding only `_safe_defaults.json` (possible artifact of bug #6) no longer appears in the picker — its safe defaults remain loadable in-app once the project is current.* |
| 8 | ✅ Fixed (2026-06-10) | sensitivity-macro persistence (`_get_saveable_config` / `_apply_config_without_model`) | The saved `mog2_var_threshold` is the live **macro output**, and on load it became the macro **anchor** → one save while loose permanently ratcheted the calibrated var away. Now `sensitivity_var_anchor` is persisted separately (profile-scoped in `config_schema`, like `sensitivity_conf_seed`) and restored on load; older configs keep the previous fallback. Also fixed the related mismatch: an expert confidence change (which recenters the dial at 50) now restores varThreshold to the anchor. |
| 9 | ✅ Fixed (2026-06-10) | `pipeline._crossval_motion_filter` + `_exclusion_norm_xy` | `(x − pad)/scale if scale != 1.0 else x` **dropped the letterbox pad when `lb_scale == 1.0`** — whenever the ROI long side equals imgsz with nonzero pad on the short axis (e.g. 1280×720 ROI @ imgsz 1280 → pad_y 280); gate + exclusion sampled the motion mask off by the pad (GPU path only). Both sites now subtract pad unconditionally (matching `_unscale_letterbox`; the `MotionConsumerShim` was already correct). CPU path bit-identical (scale 1, pad 0 → identity); goldens pass. test_transforms gained the scale=1+pad cases + a ground-truth pad-subtraction test (closes the bug #5 gap). |
| 10 | ✅ Fixed (2026-06-10) | `pipeline._run_yolo_and_track` (GPU) vs `_track_detections` (CPU) | The GPU path hand-duplicated the post-YOLO chain — all replay/golden/tuning evidence validated the CPU copy while the show ran the GPU copy (bug #9 lived in exactly this duplication). Now **one `_post_yolo_chain`** (gate → exclusion → cold blobs → tracker → finalize → OSC) parameterized by `_TrackerSpace` (person height, scale/pad/roi/roi_local tracker↔mask transform, frame width) serves both wrappers; the letterbox/offset proxies stay, only orchestration unified. Risk plan executed in order: (a) CPU↔GPU **parity replay test landed first** ([test_gpu_cpu_parity.py](../application/tests/test_gpu_cpu_parity.py), `WD_RUN_REPLAY=1`; measured baseline slot 3 = 100% count agreement / 7 px p95, slot 4 = 87% / 53 px — the bridge-regime gap is now pinned and re-measurable); (b) CPU path verified **metric-exact** against the pre-refactor baseline on both slots; (c) goldens + parity green post-refactor. `replay.py` gained `--gpu-path` / `--details` for ad-hoc GPU-path replays. |
| 11 | Low | `app.py _step_calib2` → `calib2.aggregate` | The pooled confidence seed averages **all 17** keypoint confs incl. invisible ≈0 ones → biased low, usually pinned at the 0.15 clamp (seed near-constant, the p05−margin rule never bites). Use the visible-only mean (`conf > KEYPOINT_CONFIDENCE`), consistent with the scored gate. (Also note: it seeds a *box*-confidence threshold from *keypoint* confidences — acceptable as the documented provisional stand-in, but the visible-only fix is what makes it meaningful.) |
| 12 | Low (cluster) | misc | (a) `_execute_project_switch` step 7 compares `new_imgsz != settings.imgsz` *after* step 6 already assigned it — always False, masked by the TRT special-case; (b) contradictory Calib1 toasts: servo path says "keep the stage clear", non-IDS path says "keep dancers in frame" (resolve per §4.1 decision 2); (c) `MAX_PERSONS` documented "max dancers to track" but enforced nowhere; (d) config.py OSC comment documents `/walldance/dancer/<id>/centroid [x,y]` but the code sends `/walldance/dancer/centroid [id,x,y]` — consumer-facing drift, fix the comment (or the schema); (e) `calib2.select_imgsz` ignores the FPS budget UX_PLAN specifies — it can pick 1920 and tank FPS (see §10 P-6); (f) profile-switch reuses `_apply_config_without_model` with a partial profile bundle — works, but the unconditional ROI block shows the contract is fragile (document or split). |

---

## 10. Performance backlog (review pass 2026-06-10)

**Context (measured, TUNING Phase B):** the **motion feed (MOG2 + frame-diff) dominates CPU
cost, not YOLO** — cache replay ≈ 53 ms/frame vs ≈ 125 ms live on the dev box; the frame-diff
component-selection vectorisation alone cut golden runtime 102 s → 67 s. So the levers below are
ranked by what they do to the motion feed and to per-frame fixed costs. **Every behavior-touching
item is replay-gated** (goldens / scenario scores decide, not eyeballs); pure-orchestration items
(P-1, P-2, P-5) should be bit-identical.

| # | Item | What / where | Expected win | Risk |
|---|------|--------------|--------------|------|
| P-1 | **Mono-aware gray path** | IDS mono is expanded mono→BGR ([ids_camera.py](../application/src/ids_camera.py) ~1304, 4 MP→12 MP) and the pipeline then collapses BGR→gray again every frame for the motion feed (`mog2_cvt`). When the source is mono (or greyscale mode), pass the single channel through (`frame[:,:,0]` view) instead of two full-frame conversions | A few ms/frame + memory traffic, every frame | None (identical pixels) |
| P-2 | **Persistent motion-feed worker** | `_feed_motion_detectors` spawns a **new `threading.Thread` every frame** (both CPU + GPU paths, pipeline.py). Replace with one long-lived worker (queue/Event), same join semantics | Removes per-frame spawn cost + scheduler jitter + GC churn | Low (same sync points) |
| P-3 | **Frame-diff resolution cap** | `_diff_scale = min(1.0, 2 × mog2_scale)` → at the shipped scales (0.5/0.7) the frame-diff path runs at **full resolution**: absdiff + threshold + connectedComponents per queried box, plus a full-frame absdiff for the peak-advance gate *every* frame. Cap at e.g. 0.75 | ~2× on the dominant signal's cost | θ_m ratios shift → must re-validate on replay (drops/ghosts scores) |
| P-4 | **Blur after downscale in `preprocess`** | Low-light path runs `medianBlur(5)` + `GaussianBlur` at full ROI res *before* the INTER_AREA downscale (which already averages). Downscale first, blur small | ~2–4× on that step | Not bit-identical → golden re-baseline; replay-validate |
| P-5 | **OSC bundling** | `OscBundleBuilder` is imported but unused; today 1 + 4n datagrams per frame. One timestamped bundle per frame | Fewer syscalls; **consumer gets an atomic frame** (real consistency win for TouchDesigner) | Consumer must accept bundles (standard OSC) |
| P-6 | **imgsz auto-select FPS budget** (bug #12e) | `calib2.select_imgsz` picks the smallest preset meeting the 110 px net-height target with **no FPS-budget cap**, though UX_PLAN specifies one — on a wide ROI it can pick 1920 and tank the show FPS. Add the cap: measure per-imgsz cost at calib time, or model cost ∝ imgsz² from the current measured FPS | Prevents a silent worst-case regression | None — it's a missing guard |
| P-7 | **Tiered motion cache for the tuning loop** | Already noted in TUNING Phase B: when sweeping tracker-only params, cache the MOG2/frame-diff outputs too (dev-loop speed, not show speed) | Search iterations ~53 → ~15 ms/frame (est.) | Cache-key discipline |
| P-8 | **Python 3.12 venv** | pyproject already allows `<3.13`; interpreter is ~5–10 % faster than 3.10 on this kind of code. Same work item as the §6 `run.sh` hardcoded-`python3.10` fix | ~5 % across all CPU paths | Dependency wheels re-verify (torch/cu130, kornia stub, IDS bindings) |
| P-9 | **Calib-window sweep pruning** | The var×scale sweep runs 24 MOG2 models every 2nd frame during the 15 s window — calibration-time only. Prune candidates that already exceed the FP target mid-window if the dip bothers operators | Calibration-window smoothness only | None |

Non-items (checked, fine as-is): tracker O(n²) loops (n ≤ ~6), preview path (rate-limited,
0.35 render scale, cached-CPU standby path), threaded recorder, web-monitor per-client encode.

---

## 11. Environment / install findings

- **`kornia_rs` SIGILL on non-AVX2 CPUs (FIXED).** `kornia.io` pulls `kornia_rs`, whose AVX2 wheel crashes the process (`Illegal instruction`, exit 132) on the dev Ivy-Bridge i7-3770K. Fixed by stubbing `kornia_rs` in `sys.modules` before importing kornia ([gpu_pipeline.py](../application/src/gpu_pipeline.py)). GPU CLAHE/pipeline unaffected; harmless on the RTX 5080 laptop (has AVX2).
- **`install.sh` always installs the `cu130` torch index** and only falls back when `torch.cuda.is_available()` is False. Latent footgun: an older driver can report `True` while CUDA ops crash, so the fallback ladder never triggers. Lower priority.

---

## 12. Open questions

- Typical dancer **size range** (px) and **count** across venues — does one config generalize, or do we need a small per-scale preset set? (P2 now *measures* this per show.)
- How bad are ghost floods quantitatively (ghosts/minute) on a representative bad scene? Sets the bar for P1.3/P1.4 — **needs a ghost-heavy recording**.
- Current setup ritual end-to-end (who, how long, which knobs) — confirms which manual steps remain after P2.
- What does "good enough for a show" mean numerically (acceptable missed-dancer seconds, acceptable ghost rate)? Gives the P4 fixtures a pass/fail line.

---

## 13. Doc map

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
