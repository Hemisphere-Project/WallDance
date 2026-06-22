# WallDance — Engineering Record (historical)

**Status:** Historical record. Superseded-forward by **[../ROADMAP.md](../ROADMAP.md)** (the
single live roadmap). This file is the *full blow-by-blow* of the shipped detection algorithm,
the corpus-analysis phases, the resolved-bug prose, the tracker decision lessons, and the
environment findings. **Do not plan new work from here** — ROADMAP is the source of truth.

It exists because code comments anchor to these labels — `ROADMAP bug #N`, `P0`/`P1.4`/`P2`/`P3`,
`§3a`/`§3b`, `§4.2 Phase 2 ②`/`⑧`, `§7B` — and a reader following one of them wants the detail.
ROADMAP keeps a condensed index of the same anchors; the prose lives here.

> Provenance: extracted 2026-06-22 from the historical sections of the old combined ROADMAP.md
> (dated 2026-06-10). Wording preserved; only the forward pointers were removed.

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

Each arrow is individually reasonable. The **sum** is ~90 interacting constants in
[config.py](../../application/src/core/config.py), most fit to the **p99 of a single 700-frame
clip** (slot 7, tango-phone). That is overfitting — a new venue lands outside the fitted
distribution, so it "needs tuning." **That is the setup-time pain, by construction.**

**Strategic implication:** the cheapest intervention is at the *top* of the cascade. Better IR →
higher confidence → raise the YOLO threshold → most ghosts die at the source → the lower layers
become optional. (The 2026-06 conclusion in TRACKING_ROBUSTNESS goes further: the tower is near
its ceiling and the next leap is a *positive* signal — IR markers — at the source.)

---

## 3. The two reframes

**3a. Stop fighting ID swaps so hard.** The OSC consumer only needs *positions + rough identity*,
yet the most fragile, most overfit code is the swap-correction machinery
(`_check_occlusion_cascade_swaps`, `_check_merge_direction_swaps`, `_check_two_opt_swaps`, the
slot-7 `TRACKER_MAHALANOBIS_GATE=16.27` / `TRACKER_MAX_DISPLACEMENT_RATIO=0.5` gates). Keep a
simple identity layer (Kalman + Hungarian + sane gates); **relax or disable** the post-hoc
correctors; accept occasional swaps. *(Acted on 2026-06-11 — §4.2 Phase 2 ⑧: the three correctors
sit behind one default-off switch `TRACKER_SWAP_CORRECTORS`; measured net-harmful on the corpus.)*

**3b. Separate the ghost axis from the drop axis.** Ghosts want a **high** confidence threshold;
drops want a **low** one. A single threshold cannot win under uneven IR. Decouple: reject ghosts
**by location + stationarity** (exclusion mask), keep confidence **low** enough to catch
awkward/still/far dancers, and let **better IR** lift the whole distribution so one threshold
cleanly separates real from ghost.

All field answers point the same way: **better lighting + spatial ghost rejection +
calibrate-on-Go-Live** attack ghosts, drops, and setup *at once*, without per-session tuning.

---

## 4. Shipped detection algorithm (P0–P4)

### P0 — Remove friction, measure reality — ✅ Done
Smartphone MJPEG monitor with a variance-of-Laplacian focus score (peak-hold + zoomed center
inset) and a lighting readout (brightness, clip %, luma histogram, uniformity with the darkest
tile marked). Solves "set focus and aim IR from 2 m away." Toggle via `WEB_MONITOR_ENABLED`.
([services/web_monitor.py](../../application/src/services/web_monitor.py).)

### P1.3 — Add IR coverage (hardware), then raise confidence — ⛔ Hardware-blocked *(forward)*
Rig illuminators for *even* coverage (MOG2 hates gradients more than darkness), raise
`YOLO_CONFIDENCE`, and measure the ghost drop on a recorded ghost-heavy session. The raw IR is
near-black today (calibration measured scene brightness ≈ 5/255). *Tracked forward in ROADMAP
(Hardware-blocked).*

### P1.4 — Auto exclusion mask on Go-Live — ✅ Done, then ⚠ reversed to manual
`ExclusionMaskBuilder` ([core/calibration.py](../../application/src/core/calibration.py))
accumulates, over a 16×10 normalized grid during the Calibrate window, MOG2 foreground (tiled
clean mask) + the positions of *kept* skeletons; a cell is masked if it moves in ≥30% of frames
but holds a skeleton in ≤2% (scenery/ghost). Validated on real ghosts 2026-06-11 (facade-ghosts
ghost 1.117→0.514 at zero drop cost) but can eat dancers on heavy-texture scenes.
**Reversed 2026-06 (OPERATOR_V2 decision 5):** auto-build/auto-apply dropped entirely — it
overfits the spatially-narrow calibration window vs the dancers' real stage use (bit back on
`texture-duo`). Exclusion is now **manual paint only** (always-visible overlay); Aim derives
servo + gamma + var + clean-plate, never a mask.

### P2 — Make setup automatic — ✅ Done
A dedicated **CALIBRATE** button runs a short window with YOLO forced on (live **or** during
recording playback) and sets the biggest manual knobs, then leaves them fixed. Apply-then-confirm:
values apply to the session, a result dialog offers **Save to project** vs **Keep session**.
Core: `SceneCalibrator` in [core/calibration.py](../../application/src/core/calibration.py);
`AUTOCAL_*` in config.py.

| Knob | Source |
|------|--------|
| `PERSON_HEIGHT_PX` + min/max ratios | median + p05/p95 of YOLO detection heights |
| MOG2 `varThreshold` | **empirical background false-positive sweep** (see lesson below) |
| Report (no apply) | exposure stability (σ/μ), achieved FPS, post-CLAHE noise σ diagnostic |

**Lesson — varThreshold is chosen empirically, not by formula.** A first `(N·σ)²`-from-noise map
was tried and discarded: it saturated at a clamp either way (raw σ0.69→16, post-CLAHE σ4.23→120)
because **MOG2 self-normalises** — `varThreshold` thresholds the Mahalanobis distance
`(I−μ)²/σ²_model`, and MOG2 *learns* σ²_model, so a pixel-σ→varThreshold map is dimensionless.
Replaced with a sweep: each candidate runs as its own MOG2 over the window, scored by the
**median grid-tile foreground fraction** (robust to the dancer minority), picking the lowest
candidate under `AUTOCAL_FP_TARGET` else the highest + a `saturated` flag. On dark tango footage
it picked **varThreshold=16 @ 0.01% FP** — *more* sensitive than the old default 40. (Caveat:
calibration MOG2 models use history=window(90) vs production 500 — watch early-show behaviour.)

### P3 — Simplify the motion subsystem — ✅ Done (merged to `main`)

**Was:** three jobs across three files and ~90 constants — ghost rejection
(`_crossval_motion_filter`, a 7-step tree), gap bridging (tracker `_lazy_bridge_with_motion`, a
3-tier cascade), cold motion-first detection (`_fuse_motion_blobs`) — fed by **two full MOG2
models per frame** (`bridge` @0.001 + `crossval` @0.005, differing only in learn rate).

**Now:** **one** `MotionModel` (one slow MOG2 silhouette + frame-diff "moving now?", surface
`feed/reset/noise_sigma/foreground_blob(s)/foreground_ratio/recent_motion(_blob)`) feeding
**source-weighted measurements** into the existing Kalman/Hungarian tracker. Key result:
**frame-diff — not MOG2 foreground — is the ghost killer** (static textured background + lighting
drift read as MOG2 foreground but show no frame-to-frame change).

| Stage | Done | Result |
|-------|------|--------|
| 0 | Replay harness + golden fixtures + transform tests (P4) | measurable refactor |
| 1 | `motion_model.py` over one MOG2 + frame-diff (unwired) | unit-tested |
| 2 | One `MotionModel` replaces both MOG2 (compat view shim) | **bit-identical** (2nd MOG2 was redundant) |
| 3a | Scored gate (skeleton OR frame-diff motion OR live-track) + **Bug #1 fix** (gamma-only feed) | **swaps 18→0 / 5→0, ghosts↓, dancer retained** |
| 3b | Merge YOLO/Motion-First, gated cold detection, source-weighted R, removed redundant global-blob Hungarian bridge | no regression |
| 3c/3d | varThreshold self-adapts via calibration; retired orphaned `MOTION_CROSSVAL_*`/bridge-helper constants | bit-identical |

**Deliberate deviation (evidence-driven):** the "collapse the bridge to one position-only
measurement" target was **not** fully taken — the presence + frame-diff bridge tiers actively
prevent drops, so they were kept; only the genuinely-redundant global-blob Hungarian was removed.

**Deferred (not in P3):** removing the `MotionModel.detector` compat shim (a consumer-migration
refactor — still owed); broadening the golden footage set (done in §4.2 Phase 0).

### P4 — Lock it in — ✅ Re-founded 2026-06-10
Replay harness ([tests/replay.py](../../application/tests/replay.py)) + transform tests
([tests/test_transforms.py](../../application/tests/test_transforms.py)). Goldens re-founded on the
annotated corpus (trio `hangar-floor`/`hangar-aerial`/`texture-aerial`; opt-in `WD_RUN_REPLAY=1`)
with **configs pinned in the scenario manifests** + recording fingerprints (see
[../CORPUS_ANALYSIS.md](../CORPUS_ANALYSIS.md) §5). 12 GT-verified manifests cover the
multi-dancer/aerial/ghost/small-far/static-person gaps.

---

## 5. Corpus-analysis follow-up (Phases 0–4)

Full plan + evidence in [../CORPUS_ANALYSIS.md](../CORPUS_ANALYSIS.md).

| Phase | Scope | Status |
|-------|-------|--------|
| **0 — Corpus re-founding** | Pinned-config scenario schema + loud-fail replay + fingerprints + pass lines (`scoring.evaluate_pass`); golden trio regenerated; 12 manifests | ✅ Done 2026-06-10 (operator GT pass; per-range labels for blur-runner / dark-crowd / white-walkers) |
| **1 — Project config repair** | Agent-run headless Calib1+Calib2 per rig project → timestamped saves + before/after replay | ✅ Done 2026-06-10 — 7/8 adopted; TOGO-night retained (brightening trades drops for static facade ghosts); details `tmp_analysis/phase1/SUMMARY.md` |
| **2 — Logic & constants** (small replay-gated diffs) | ① warmup intermittent-confirm (`tracker_intermittent_confirm`, bug #14) · ② duplicate-track takeover merge (`_merge_takeover_duplicates`) · ③ MAX_PERSONS report-boundary cap (bug 12c) · ④ exclusion mask default-on + manual editor · ⑤ calib2 amendments (box-conf seed bug #11, gamma noise cap, imgsz FPS budget, height-staleness alarm) · ⑥ static-person gate (skipped — condition not met) · ⑦ sensitivity-macro span re-fit · ⑧ slot-7 corrector relaxation (§3a) | ✅ Phase 2 done 2026-06-11 (full trail `tmp_analysis/phase2/SUMMARY.md`) |
| **2b — imgsz × model selection benchmark** | 710-cell grid (12 × 10 × 6), scored through the full pipeline vs pass lines | ✅ Done 2026-06-12 — **(a)** net-height target **110 validated**, oversizing hurts, dark scenes invert → `AUTOCAL2_NET_HEIGHT_TARGET_DARK=45`; **(b)** yolo26 **not** a free swap — keep yolo11; **(c)** joint (model, imgsz) rule landed (P-6 per-rig fps table `models/fps_table.json`); **(d)** ⑤a τ-seed weak grid-wide → τ ownership moves to Phase 3. TRT FP16 spot-check: drift ≤0.029. Full trail `tmp_analysis/phase2b/SUMMARY.md` |

**Scene-class pass lines:** A (indoor rigged) drop ≤ 0.05, longest ≤ 1.0 s, ghost ≤ 0.05 ·
B (outdoor/uncontrolled) 0.10 / 2.0 s / 0.15 · S (stress) no line.

---

## 6. Tracker — lessons + key gates (condensed)

The tracker is sophisticated and intentionally engineered (Kalman + cascaded Hungarian + dormant
resurrection + post-hoc swap correctors). Full decision log in
[TRACKING_PLAN.md](TRACKING_PLAN.md). The durable lessons:

1. **Post-hoc swap correction is inherently fragile** — timing-dependent on flag states; one fix
   triggers false positives elsewhere. Pre-assignment gates (Mahalanobis, displacement) are more
   robust. *(Confirmed 2026-06-11: Phase 2 ⑧ measured the correctors net-harmful — id churn
   improved everywhere when they came off — disabled all three by default behind
   `tracker_swap_correctors`.)*
2. **Merge-frame inflation is the #1 silent killer of identity** — ghost tracks from scenery count
   as "active" → `n_det < n_tracks` fires almost every frame → all tracks get merge context →
   swap detectors misfire. Count only established, recently-matched tracks.
3. **Kalman velocity amplifies during convergence** — two approaching tracks spike each other's
   velocity, making a tight Mahalanobis gate reject the correct match. Keep the gate generous; use
   a displacement cap for teleport protection.
4. **Skeleton similarity can mask centroid jumps** — a track can match a body-similar detection
   75px away; the displacement gate enforces a hard centroid cap.
5. **The JSONL event log is essential** — every diagnostic insight came from `FRAME_SUMMARY` +
   per-session logs (`tracking_logger.py`).

Key gates (slot-7-derived): `TRACKER_MAHALANOBIS_GATE=16.27`, `TRACKER_MAX_DISPLACEMENT_RATIO=0.5`,
`TRACKER_CLOSE_PROXIMITY_RATIO=0.35`. Phase 2 ⑧ (2026-06-11) deliberately kept these — per lessons
3–4 they are the robust layer; only the post-hoc correctors went off by default.

---

## 7. Requested enhancements — shipped

### A. Simplify the YOLO-First / Motion-First duality — ✅ Merged in P3 Stage 3b
`TrackingMode` (`YOLO_FIRST`/`MOTION_FIRST`) was a user-facing toggle that bifurcated the pipeline.
Motion blobs are now always candidates (gated by frame-diff + exclusion) fed through one scored
path. The `TrackingMode` enum still exists for config/learn-rate compatibility but no longer
changes the detection logic; **fully removing it is a follow-up cleanup** (tracked forward in
ROADMAP — Simplification path).

### B. Startup project picker (§7B) — ✅ Done
On start, do not auto-load; open a modal picker (projects by last-save date, last highlighted,
Enter launches; per-project Launch/Rename/Delete). `config_store.rename_project`/`delete_project`
added; env escape hatch retained for kiosk boot.

---

## 8. Resolved bugs (#1–#14) — full prose

| # | Severity | Location | Issue |
|---|----------|----------|-------|
| 1 | ✅ Fixed (P3 3a) | `pipeline._feed_motion_detectors` | Adaptive CLAHE before MOG2 amplified noise per-frame → frame-diff read it as fake motion (admitted a ghost on slot 4). Now feeds **gamma-only** (fixed, frame-independent) gray. The harness proved this fix is inseparable from the scored gate. |
| 2 | ✅ Fixed (P3 2) | one `MotionModel` | Collapsed the two MOG2 into one slow model + frame-diff; bit-identical → the 2nd MOG2 was redundant. |
| 3 | 🟡 Low (open) | `_extract_transfer_timing` | Assigned inside the per-detection loop; stale on zero-detection frames. Cosmetic. |
| 4 | ✅ Fixed (2026-06-10) | `motion_detector.feed_preprocessed` | `_curr_raw`/`_prev_raw` advanced only when the *global* peak diff > 8 — on a clean static stretch the pair froze and `frame_diff_blob_in_bbox` reported the last motion event's diff indefinitely. Now a frames-since-advance counter (`_diff_pair_age`, cap `MOTION_DIFF_PAIR_MAX_AGE_FRAMES=30`) zeroes the report past the cap. |
| 5 | ✅ Covered (P3 0) | ROI→letterbox→unscale transform chain | Now under `test_transforms.py` (round-trip + crossval/exclusion transform). The `scale == 1.0` + nonzero-pad gap is closed with the bug #9 fix. |
| 6 | ✅ Fixed (2026-06-10) | `_apply_calibration` → `_cb_save_safe_defaults` | The calibration result dialog's "Save to project" wrote `_safe_defaults.json`, **not** a timestamped project save — calibrate → Save → restart **lost the calibration**. Now both calibration dialogs route `on_save` to a normal timestamped save. |
| 7 | ✅ Fixed (2026-06-10) | `config_store.get_latest_config_in_project` | `_safe_defaults.json` matched the `.json` listing and "latest" was a reverse **name** sort → startup silently loaded safe defaults for capitalized project names. Now `list_config_files()` skips `_`-prefixed files and sorts by **mtime**. |
| 8 | ✅ Fixed (2026-06-10) | sensitivity-macro persistence | The saved `mog2_var_threshold` is the live **macro output**, and on load it became the macro **anchor** → one save while loose permanently ratcheted the calibrated var away. Now `sensitivity_var_anchor` is persisted separately and restored on load. |
| 9 | ✅ Fixed (2026-06-10) | `_crossval_motion_filter` + `_exclusion_norm_xy` | `(x − pad)/scale if scale != 1.0 else x` **dropped the letterbox pad when `lb_scale == 1.0`** (GPU path only). Both sites now subtract pad unconditionally; CPU path bit-identical. |
| 10 | ✅ Fixed (2026-06-10) | `_run_yolo_and_track` (GPU) vs `_track_detections` (CPU) | The GPU path hand-duplicated the post-YOLO chain; all replay/golden evidence validated the CPU copy while the show ran the GPU copy. Now **one `_post_yolo_chain`** parameterized by `_TrackerSpace` serves both. CPU↔GPU parity replay test landed first ([test_gpu_cpu_parity.py](../../application/tests/test_gpu_cpu_parity.py)). |
| 11 | ✅ Superseded (2026-06-11) | `_step_calib2` → `calib2.aggregate` | The pooled confidence seed averaged keypoint confs and seeded a *box*-confidence threshold from *keypoint* units. **Fixed by Phase 2 ⑤a**: box confidences threaded to `ScaledTrack.box_conf`; the seed pools those directly. |
| 12 | Low (cluster) | misc | (a) `_execute_project_switch` step 7 compares `new_imgsz != settings.imgsz` *after* step 6 already assigned it — always False (**open**); (b) contradictory Calib1 toasts (servo vs non-IDS path) (**open**); (c) ✅ MAX_PERSONS report-boundary cap (Phase 2 ③); (d) ✅ **fixed 2026-06-22** — config.py OSC comment now matches the wire format `/walldance/dancer/centroid [id,x,y]`; (e) ✅ `select_imgsz` FPS budget (Phase 2 ⑤c / P-6); (f) profile-switch unconditional ROI block is fragile (**document/split — open**). |
| 13 | ✅ Fixed (2026-06-10) | `tests/replay.py` config resolution | A missing/renamed project silently fell back to `config={}` (defaults). Now scenario manifests pin a frozen config snapshot + a recording fingerprint (hard-fail on mismatch); a bare `--project` lookup that finds nothing errors loudly. |
| 14 | ✅ Fixed (2026-06-11, per-scene) | `tracker.py` warmup scoring | Confirmation needs +1/hit vs −0.8/miss to reach 15 → a track can never confirm below ~45 % sustained detection rate. Shipped: integral untouched + an **intermittent path behind the per-scene switch `tracker_intermittent_confirm`** (wired through project config 2026-06; enable per scene via the known-N search). |

---

## 9. Performance backlog — done items

| # | Item | Status |
|---|------|--------|
| P-6 | **imgsz auto-select FPS budget** (bug #12e) | ✅ Done 2026-06-11 (`select_imgsz` rejects presets predicted under `AUTOCAL2_FPS_BUDGET`; rig advisory on height-target miss). Extended 2026-06-12: `extra/measure_engine_fps.py` (auto-run by build_engines.sh) measures per-(model, imgsz) engine fps → `models/fps_table.json`; calib2 prefers the measured cost curve |

*(The still-open performance items — mono-aware gray path, persistent motion worker, frame-diff
resolution cap, blur-after-downscale, OSC bundling, tiered motion cache, py3.12 venv, calib-window
sweep pruning — are tracked forward in ROADMAP.)*

---

## 10. Environment / install findings

- **`kornia_rs` SIGILL on non-AVX2 CPUs (FIXED).** `kornia.io` pulls `kornia_rs`, whose AVX2 wheel
  crashes the process (`Illegal instruction`, exit 132) on the dev Ivy-Bridge i7-3770K. Fixed by
  stubbing `kornia_rs` in `sys.modules` before importing kornia
  ([core/gpu_pipeline.py](../../application/src/core/gpu_pipeline.py)).
- **`install.sh` always installs the `cu130` torch index** and only falls back when
  `torch.cuda.is_available()` is False. Latent footgun: an older driver can report `True` while
  CUDA ops crash, so the fallback ladder never triggers. *(Tracked forward as a low-priority item.)*

---

## 11. Open questions — answered by the corpus analysis (2026-06-10)

Measured answers in [../CORPUS_ANALYSIS.md](../CORPUS_ANALYSIS.md) §8:

- **Size range / one config?** Median heights 100–1000 px across venues, in-scene spread 0.4–1.8× —
  one config cannot generalize; per-scene Calib2 is structurally required (imgsz 640→1536+).
- **Ghost flood magnitude:** 0.7–3 ghost-dets/frame on textured/outdoor scenes (8+/frame on the
  facade stress case), **60–95 % at fixed scene spots** → maskable.
- **Setup ritual cost:** replays with stale/bulk-copied configs drop 36–100 % of dancers on 6/7
  hard scenes — the calibration flow closes most of it.
- **"Good enough" numerically:** the scene-class pass lines, embedded per-manifest (`"pass"`,
  evaluated by `scoring.evaluate_pass`).
