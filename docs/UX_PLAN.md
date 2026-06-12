# WallDance — Production UX plan (operator track)

**Date:** 2026-06-10 · **Status:** U0–U5 **merged to `main`** (built on branch
`ux-track`: U0 0d7382c, U2 c034956, U3 2234d67, U4 9ab4f09, U5 e1d4831; U1 picker
pre-existed at f2e5e8c). Numeric rules ship provisional — re-fit on annotated
footage (§6). **Review decisions 2026-06-10 (ROADMAP §4.1):** Calib1 is
**camera + lighting only** — its leftover person-height measurement moves to
Calib2 (which owns all subject knobs), and the contradictory calib1 toasts get
fixed with it; calibration "Save to project" must write a normal timestamped
project save (safe-defaults stays a separate explicit action — ROADMAP bug #6).
**Serves:** ROADMAP §0 north star — *rig, aim IR, press one calibration button, monitor*.
**Decisions locked (operator, 2026-06-10):** two lighting profiles per project ·
full knob panels kept behind a hidden Expert mode · calibration may drive IDS
exposure/gain (with a motion-blur exposure cap) · build the UX track now,
re-validate calibration rules when annotated footage arrives.

---

## 1. Target operator flow

1. **Rig** — camera + IR. Focus from the stage via the phone monitor (P0).
2. **CALIB SCENE** (empty stage, live only) — iterative during rigging:
   click → check preview/report → adjust focus or IR → click again. Idempotent.
3. **CALIB DANCERS** (1–4 people, live **or** recording) — accumulative:
   pool evidence across several runs/situations, then apply once.
4. **Live** — status strip + *one* sensitivity slider + view toggles + OSC.

The operator never sees a numeric pipeline knob. The developer still can
(Expert mode).

---

## 2. The two calibrations (different UX by design)

### Calib1 — Scene (rigging phase)

*Empty stage, live camera only, ~15 s window, stateless: each run fully
replaces the previous scene values in the **active lighting profile**.*

Pipeline per run:

| # | Step | Method |
|---|------|--------|
| 1 | Exposure/gain | Servo IDS exposure up to the **blur budget** (default ≤ 25 ms until calib2 refines it), then analog gain to hit target median brightness without clipping. Starvis2 = favor analog gain over long exposure. |
| 2 | Gamma/CLAHE seed | From the resulting luma histogram (refined later by calib2). |
| 3 | MOG2 `varThreshold` + `mog2_scale` | Existing empirical FP sweep, extended to a **joint** var×scale sweep (KNOBS finding #2: they only pay off together) under the FPS budget. |
| 4 | Exclusion mask | Existing `ExclusionMaskBuilder` accumulation. |
| 5 | Clean plate | Capture background reference (dormant `background.py` becomes internal-only; future deeper processing). |
| 6 | Report card | Focus score (reuse web-monitor variance-of-Laplacian), brightness, clip %, **uniformity + darkest tile → "aim IR here" hint**, noise σ, achieved FPS, blur-budget status. Pass/warn per line. |

UX: one button **CALIB SCENE** in the bottom bar (replaces/extends the current
CALIBRATE). Result = compact report card; values apply to session + save to the
active profile (apply-then-save, as P2 does today). Re-click loop is the
expected usage during focus/IR adjustment, so the window must stay short and
the report instantly readable.

FPS policy: **floor 15 fps, target ≥ 20 fps** — the binding constraint is
motion blur (exposure ≤ ~25 ms), which keeps fps above the floor by
construction; tracker/frame-diff constants assume ~20 fps.

### Calib2 — Dancers (subject phase)

*1–4 dancers on stage; runs on **live feed or playback**; **accumulative**:
each run appends an evidence sample; the operator applies the pooled result.*

Evidence pool: `projects/<name>/calib2/<timestamp>.json` per run — detection
height samples, confidence histogram, a small buffer of sampled frames
(for the gamma/CLAHE sweep), source (live / recording slot), active profile,
ROI geometry at capture time.

Derived on **Apply** (over all included runs):

| Output | Rule | Written to |
|--------|------|-----------|
| `person_height_px` + min/max ratios | median + p05/p95 of pooled heights | project (shared) |
| `yolo_imgsz` auto-select | smallest imgsz with `person_height_px × imgsz / max(roi_w, roi_h) ≥ target`, capped by FPS budget. Target = 110 px (corpus-validated, Phase 2b) or 45 px in the high-noise/dark regime (the imgsz curve inverts there); fps predicted from the per-rig engine table (`models/fps_table.json`) when present, else imgsz⁻²; plus a report-only model advisory (largest yolo11 tier in budget) | project (shared) |
| Gamma + CLAHE | offline sweep on the pooled sampled frames, maximize mean person-confidence (motion feed is gamma-only — Bug #1 fix — so this cannot poison MOG2) | active profile |
| Sensitivity seed | confidence such that ghost-rate ≈ target on pooled frames (KNOBS E2) | active profile |
| Blur budget refinement | px/ms speed estimate from pooled track velocities → tighter exposure cap for the next calib1 | project (shared) |

UX: **CALIB DANCERS** button opens a small pool dialog: list of runs with
include-checkboxes (`3 runs · 1 240 samples · 2 recordings`), **Add run**
(live window or current playback), **Apply**, **Clear**. Pool is flagged stale
if ROI/camera geometry changed materially since capture (px heights shift).

Pooling across *situations* (different costumes, counts, distances, recorded
slots) is the point: the sweet spot comes from the pooled distribution, not
one pass.

---

## 3. Lighting profiles (Rehearsal / Show)

Two named profiles per project; one switch in the top bar; switching applies
the whole bundle atomically **including IDS hardware settings**. Calibrations
write into the **active** profile. No silent auto-adaptation — calibrate each
condition once.

| Per-profile (lighting-coupled) | Shared per-project (geometry/semantic) |
|---|---|
| `ids_exposure_us`, `ids_gain_db` | camera source/type, `ids_ratio`, ROI |
| `gamma`, `clahe_clip` | `person_height_px` + ratios |
| `mog2_var_threshold`, `mog2_scale` | model, `yolo_imgsz`, `use_tensorrt` |
| exclusion mask (shadows move with light) | greyscale, enhance enable |
| clean plate reference | OSC settings, max dancers |
| sensitivity (confidence) value | blur budget |

Config schema v2: `profiles: {show: {...}, rehearsal: {...}}, active_profile`;
migration wraps an old flat config as the `show` profile. This is the natural
moment to land **typed config validation + versioning** (ROADMAP §6 item).

---

## 4. Control panel — Simple vs Expert

**Simple (default):**
- **Input** — camera pick, profile switch, phone-monitor URL/QR.
- **ROI** — Enable + Reset; **double-click preview toggles edit**; corner-drag
  only; read-only `x,y w×h` text (numeric inputs removed).
- **Scene** — CALIB SCENE + last report card summary.
- **Dancers** — CALIB DANCERS + pool status.
- **Detection** — Person height (slider, calib2-seeded) + **Sensitivity macro**
  (replaces raw confidence; confidence-led; secondary low-`var` arm at the loose
  end is safe now that Phase F's frozen-ghost gate landed).
- **Enhancement** — Enable (A/B) + Greyscale only. Always-on semantics
  (`force` behavior internal); Threshold/Force controls removed.
- **OSC**, **View toolbar** — unchanged.
- **TRT banner** — red band over preview when TensorRT was requested but fell
  back (`model_manager` already records the reason) + **Rebuild engine** button;
  yellow while exporting.

**Expert (hidden; keyboard chord + `WD_EXPERT=1`, off by default):** today's
full sections — raw confidence, tracker max age, motion sensitivity, mog2
sliders, Background section, brightness threshold, manual exposure/gain.
KNOBS Tier-3 verdict: hidden ≠ deleted.

**Gone from all UI:** Tracking Mode combo (P3 merged the pipelines; enum is
config-compat only).

**Startup project picker** (ROADMAP §7B): modal, projects by last-save date,
last project highlighted + Enter-launches, prominent **NEW**, Rename/Delete
(add `rename_project`/`delete_project` to `config_store`), auto-launch escape
hatch for kiosk boot.

---

## 5. Build phases

| Phase | Scope | Size | Notes |
|-------|-------|------|-------|
| **U0** | Expert-mode scaffold; remove Tracking Mode combo; Background → expert-only; Enhancement → Enable+Greyscale; ROI double-click + drag-only; TRT banner + rebuild | S | Branches off `p3-motion-simplification` (tracking-mode removal assumes P3) |
| **U1** | Startup project picker + rename/delete + kiosk flag | S–M | Independent |
| **U2** | Config schema v2: profiles + validation/versioning + top-bar switch (atomic apply incl. camera HW) | M | Before calibs (they write into profiles) |
| **U3** | Calib1 scene pass: exposure/gain servo w/ blur cap, joint var×scale sweep, report card, idempotent re-run UX | M–L | Extends `SceneCalibrator` |
| **U4** | Calib2 subject pass: evidence pool (live + playback), height/ratios, imgsz auto-select, gamma/CLAHE sweep, sensitivity seed, pool dialog | L | Seeding rules re-validated on annotated footage |
| **U5** | Sensitivity macro slider + Detection-section collapse | M | Mapping tuned on annotated footage |

Each phase: replay fixtures (`WD_RUN_REPLAY=1`) stay green; calibration-derived
values logged like P2 (explicit + auditable). U4/U5 numeric rules ship with
provisional constants and get re-fit when the annotated recordings arrive
(TUNING.md loop).

---

## 6. Open items

- Exact blur-budget default (25 ms) is provisional — re-fit on annotated
  footage. ~~Net-input height target (~110 px)~~ — **measured 2026-06-12**
  (ROADMAP §4.2 Phase 2b): 110 validated (knee medians 83–102 px, flat across
  model tiers); dark/noisy scenes invert the curve → `AUTOCAL2_NET_HEIGHT_TARGET_DARK=45`
  behind the ⑤b noise-σ condition.
- Expert-mode chord choice (single keys E/T/S/K/B/I/P are taken).
- Whether calib2's gamma/CLAHE sweep needs GPU batching to stay snappy
  (~12 frames × ~9 combos).
- ~~Merge timing of `p3-motion-simplification` into `main`~~ — done (2026-06-10).
- ~~`calib2.select_imgsz` lacks the FPS-budget cap §2 specifies~~ — done
  2026-06-11 (ROADMAP §4.2 Phase 2 ⑤c); extended 2026-06-12 with the per-rig
  engine fps table + model advisory (Phase 2b / P-6).
- ~~Calib2's confidence-seed pooling averages keypoint confs~~ — done
  2026-06-11 as the box-conf seed (ROADMAP §4.2 Phase 2 ⑤a). Phase 2b then
  measured the seed rule weak grid-wide → per-scene τ moves to the known-N
  search (ROADMAP §4.2 Phase 3).
