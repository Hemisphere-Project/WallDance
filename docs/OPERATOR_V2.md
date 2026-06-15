# WallDance — Operator & Calibration v2 (roadmap)

**Date:** 2026-06-15 · **Status:** DRAFT for operator review. Forward plan for the
*operator-experience / calibration / setting-governance* layer. The detection
**algorithm** is done (ROADMAP §5 P0–P4, §4.2 Phase 2/2b); this track is about making
it **operable and trustworthy on a new show**, which is where the remaining field pain
now lives.

**Companion / supersedes-forward:** extends [ROADMAP.md](ROADMAP.md) §4.2 Phase 3–4 and
[UX_PLAN.md](UX_PLAN.md) U0–U5 (shipped). Folds in the deferred items of
[CALIB_DETECTION_FIX_PLAN.md](CALIB_DETECTION_FIX_PLAN.md) (U-a/b/c done; case-3 CLAHE
sweep in flight — see §6 coordination). Grounded in the 2026-06-15 five-dimension audit
(UX, settings, calibration, coupling, benchmark capability).

**Operator decisions locked 2026-06-15:**
1. UX = **linear phase rail** (the operator timeline becomes the UI; the 8-section panel
   demotes to an *Advanced* drawer).
2. Recording/playback = **setup/rehearsal tool** (off the live surface, into a drawer).
3. Live detection control = **two dials** (drops↔ghosts + gap-bridging) — the second dial
   must be *validated + range-fit* by the cross-parameter test before it ships (Track G).
4. Benchmarks = **cheap-first, gated** (~30-min validation on the highest-leverage axis,
   report, then ask before any larger sweep).
5. Exclusion mask = **manual-only** — drop auto-detect/auto-apply entirely (it overfits a
   spatially-narrow calibration window vs the dancers' real use of the stage, and already bit
   back on `texture-duo`). Operator paints known dead zones; **masked cells stay visible at all
   times**, not only in edit mode. (Reverses ROADMAP P1.4 "auto exclusion on Go-Live".)
6. **Output/OSC layer** = a dedicated post-tracker stage with **two decoupled controls**: a
   *box-clamp-to-last-known-YOLO-size* toggle, and a *smoothing-depth* slider that buys
   coherence/ghost-suppression at the cost of latency (operator sets per show). See Track X.
7. **Canonical operator workflow** fixed (incl. a *short-install fallback*: calibrate on the
   first live moments as dancers enter); **live interaction is minimized** — the operator has
   other things to handle, so live = nudge a couple of dials, nothing more.

**Prerequisites for a fresh implementer (read first):** [ROADMAP.md](ROADMAP.md) §4.2 (the
detection algorithm is *done*; this track makes it operable), [UX_PLAN.md](UX_PLAN.md) U0–U5
(**already shipped** — multi-profile, config schema v2, two-pass calib, sensitivity macro; do not
re-ship), [CALIB_DETECTION_FIX_PLAN.md](CALIB_DETECTION_FIX_PLAN.md) (cases 1/2/3 — the
box-flicker / flying-ghost / CLAHE findings this track builds on), and `MEMORY.md`. **Before
touching code, read §8 (autonomy map) + the ask-first triggers. Run scope = autonomous batch 1
only** (§8) unless told otherwise.

**Supporting reference (data + tools, not plans):** [CORPUS_ANALYSIS.md](CORPUS_ANALYSIS.md) +
[../projects/CORPUS_NOTES.md](../projects/CORPUS_NOTES.md) (measured scene physics + the 12-slot
corpus map), [OPTICS.md](OPTICS.md) (lens/distance envelopes), [TUNING.md](TUNING.md) (the
replay/tune/scoring toolchain Track G drives), [KNOBS.md](KNOBS.md) (historical knob evidence →
governed forward by Track S/G), [GUI_STACK_AUDIT.md](GUI_STACK_AUDIT.md) (DPG-vs-PySide decision),
[DECOMPOSITION_PLAN.md](DECOMPOSITION_PLAN.md) (core/runtime/ui split, Phases 0–4 done). The
2026-06-15 five-dimension audit + readiness validation that produced this doc were distilled into
it; their raw outputs are ephemeral (not committed).

---

## 0. The reframe — derive everything from one operator timeline (the "spine")

Today the work is scattered: a panel of 8 sections, 4 calibration buttons, KNOBS tiers,
detection cases, U-a/b/c, Phase 3/4. The unzoom: **stop treating UI, calibration,
settings, and tests as separate piles. Derive all four from one canonical operator
procedure — the spine:**

> **① Rig & Frame → ② Profile → ③ Aim (IDS servo) → ④ Calibrate (dancers) → ⑤ Verify → ⑥ Go Live**

- **The UI *is* the spine** (linear phase rail, decision 1).
- **Calibration steps map 1:1** to ③–④; ⑤ is the readiness/dry-run gate.
- **Every setting is classified by *which phase sets it* and *who owns it*** (Fixed / servo /
  calibrate / user) — §4.
- **The cross-parameter test validates the spine's dependency hierarchy** — what must be set
  before what, and what is genuinely user-tunable — §5.

North star unchanged (ROADMAP §0): *rig, aim IR, press calibration, monitor — set-and-forget,
one explicit logged calibration, not continuous auto-tuning.*

### 0.1 The calibration seam is the *signal*, not scene-vs-dancer (refined 2026-06-15)

The Calib1 (Scene) / Calib2 (Dancers) split was a **build-phase decomposition, not the
end-state UX.** The clean axis is *what signal each knob needs*:

| Knob | Signal | Live IDS? | Dancers in frame? |
|------|--------|-----------|-------------------|
| exposure / gain | blur-vs-brightness, drives the camera | **yes** | no (clear-ish view) |
| MOG2 var / scale | background false-positive rate | no | no (best clean) |
| ~~exclusion mask~~ | **operator knowledge of dead zones (manual, decision 5)** | no | n/a |
| gamma | raw scene brightness | no | no |
| **CLAHE** | **YOLO detection quality** | no | **yes** |
| person_height / imgsz / confidence | dancer detections | no | **yes** |

Only the **IDS servo** is fundamentally different (it physically drives the live camera, early,
during rigging). Everything else is footage-derivable, and the detection-dependent knobs
(CLAHE, height, imgsz, confidence) all want dancers in frame. **End-state = ③ Aim (servo,
autonomous, early) + ④ one "Calibrate with dancers" pass** that derives gamma, var, clean-plate,
CLAHE, height, imgsz, confidence over the evidence pool (**exclusion stays manual — decision 5**).
The current two-button Calib1/Calib2 is the **transitional** form; the `ALL` wizard already
chains servo→scene→dancers at the UX layer, so the operator-facing flow barely changes — the
merge unifies the *engine* beneath it. **Coupling fact (verified, pipeline.py:1184):** the motion
feed uses `enhancer.gamma` (fixed LUT, no adaptive CLAHE) → **gamma is shared** (var is scored on
the post-gamma gray) while **CLAHE is decoupled** (YOLO-input only). So a unified pass must derive
**gamma → var/clean-plate → CLAHE/height/imgsz/conf** in that order.

---

## 1. The operator spine (canonical procedure for a new show)

**The operator's real procedure (fixed 2026-06-15):**
> rig camera → **manual focus** → **empty-scene calib** → **record rehearsal samples** →
> **calibrate on those captured situations** → **run show**.

*Short-install fallback:* if there's no time to record rehearsal, **skip the recording pass and
run the dancer calibration on the first live moments** as the dancers enter. Once live, the
operator only nudges a dial or two — they have other things to handle.

| # | Phase | Operator does | System does | Re-run model |
|---|-------|---------------|-------------|--------------|
| ① | **Rig & Frame** | mount camera + IR; **manual focus** from the stage via phone monitor (P0); draw stage ROI; **paint known dead zones** (balcony / reflective wall / doorway) | phone MJPEG + focus/uniformity/darkest-tile readout; **mask overlay always visible** | as needed |
| ② | **Profile** | pick / create **Show** (night) / **Rehearsal** (day) | atomic bundle apply incl. IDS HW | per condition |
| ③ | **Aim & empty scene** (live, clear stage) | press; read brightness/blur + scene report; adjust IR/focus; press again | exposure/gain servo (blur-capped) → gamma (brightness) → MOG2 var×scale → **clean-plate capture** | **idempotent** (replaces scene config in profile) |
| ④ | **Calibrate dancers** | **(A, preferred)** record rehearsal run(s) → review pool → Apply · **(B, fallback)** run live on show-open | pool dancer evidence → **CLAHE (detection)** + person_height + ratios + imgsz + confidence seed + blur budget | **accumulative** (pool, Apply once) |
| ⑤ | **Verify** | glance at readiness; optional dry-run on last recording | readiness check (FPS / TRT / OSC / calib-age / disk / config-vs-scene) + optional replay score | per show |
| ⑥ | **Go Live** | monitor; nudge the **two detection dials** / output controls only if needed | full YOLO + OSC; health alerts | continuous |

*Transitional (today):* ③–④ are the two-button **Calib1 / Calib2** split; the `ALL` wizard
already chains servo→scene→dancers, so the end-state engine merge (§3.3) keeps the same button
count.

**Design rules the spine enforces:**
- Order is **guided + softly enforced**: ④ warns if ③ never ran; ③ refuses to clobber a pool
  mid-review; the fallback (B) is one tap inside ④.
- Each phase has **one primary action** + a **plain-language status line** ("Scene: calibrated
  12 min ago · Pool: 3 runs, height 180 px, imgsz 960").
- Numeric pipeline knobs never appear on the spine — **Advanced drawer** only.
- **Exclusion is a manual paint sub-step of ①** (always-visible overlay), never an auto
  side-effect of ③ (decision 5).

---

## 2. Track O — Operator UX v2 (linear phase rail)

**Decision 1 = linear rail.** The control surface becomes the spine; the section-stack
becomes an Advanced drawer.

### 2.1 Layout
- **Top bar:** Project ▾ · Profile (Show/Rehearsal) ▾ · unified **status chip group** (Cam /
  OSC / FPS / Engine / state) — one row, plain meanings, fallback states explicit.
- **Phase rail** (the new primary control): ① Rig · ② Profile · ③ Aim · ④ Calibrate · ⑤
  Verify · ⑥ Live, each showing **done / pending / count** state. Clicking a phase opens its
  panel on the right.
- **Right panel = current phase only:** one primary action + that phase's minimal controls +
  status. (e.g. ④ shows `[Record run]`, pool list, `[Apply]`.)
- **Advanced drawer (`⚙`):** today's 8 sections verbatim, behind one disclosure — for the
  developer/power user. Replaces the binary Expert-mode dump with a single drawer; the truly
  internal knobs stay hidden inside it.
- **Recordings drawer (decision 2):** LIVE/REC + 10 slots + playback transport move here, off
  the live surface. Calib ④ can still pull a slot as an evidence source.

**Layout mockup (batch-1 scaffold target — confirm before full build).** Two states shown: a
calibration phase (④ selected) and the live phase (⑥). The rail, drawers, and status strip are
the same in both; only the right panel swaps with the selected phase.

```
 CALIBRATE state (phase ④ selected)                LIVE state (phase ⑥ selected)
┌──────────────────────────────────────────┐    ┌──────────────────────────────────────────┐
│ Project: TOGO-night ▾   Profile: SHOW ▾   │    │ Project: TOGO-night ▾   Profile: SHOW ▾   │
│ CAM●IDS  OSC●  19fps  [TRT]  ● STANDBY    │    │ CAM●IDS  OSC●  19fps  [TRT]  ● RUN        │
├──────────────────────────────────────────┤    ├──────────────────────────────────────────┤
│ ①Rig✓ ②Prof✓ ③Aim✓ │④Calibrate│⑤Verify ⑥Live│  │ ①✓ ②✓ ③✓ ④✓(3 runs) ⑤✓ │⑥ LIVE ▶│        │
├───────────────────────────┬──────────────┤    ├───────────────────────────┬──────────────┤
│                           │ ④ CALIBRATE   │    │                           │ ⑥ LIVE        │
│                           │ dancers       │    │                           │               │
│                           │ [▶ Record run]│    │      VIDEO PREVIEW        │ Drops↔Ghosts  │
│      VIDEO PREVIEW        │ ──or──        │    │      (skeletons/IDs)      │  [────●────]  │
│   (live / playback)       │ source: slot4▾│    │                           │ Gap-bridging  │
│                           │ Pool: 3 runs  │    │                           │  [──●──────]  │
│                           │  h180 imgsz960│    │                           │ ─ output ─    │
│                           │ [Apply] [Clear]    │                           │ box-clamp [x] │
│                           │ status: pool  │    │                           │ smooth L[●1 ] │
│                           │  applied 2m   │    │                           │ View: S K B T I│
├───────────────────────────┴──────────────┤    ├───────────────────────────┴──────────────┤
│ ⚠ Alerts: (none)                          │    │ ⚠ Alerts: GPU 78°C                        │
│ ⚙ Advanced ▸        🎞 Recordings ▸        │    │ ⚙ Advanced ▸        🎞 Recordings ▸        │
└──────────────────────────────────────────┘    └──────────────────────────────────────────┘
```
*(Manual exclusion paint lives in ① Rig & Frame alongside the stage-ROI tool; masked cells
render dimmed on the preview at all times. Box-clamp + smoothing controls are stubs in batch-1
— the toggle is wired but the fixed-lag behavior is 🟡/🔴, built later. The Advanced drawer holds
today's 8 sections verbatim.)*

### 2.2 Live-show surface (phase ⑥) — two dials (decision 3)
- **Dial A — "Drops ↔ Ghosts"** (confidence-led, range fit by Track G; today 0.15–0.65).
- **Dial B — "Gap bridging"** (motion/bridge aggressiveness). **Validated by G1 (2026-06-15) →
  KEEP.** On the production path (GPU+TRT), in the gappy/aerial regime, raising it **monotonically
  reduces drops (~22% relative on hangar-aerial @conf 0.5: drop 0.046→0.035) at zero ghost/id
  cost**; safely **inert** on clean/static/SNR-limited scenes. Ship it as a **monotonic
  "fewer drops" slider, calibrated-seeded** (higher seed where Calib2 sees detection gaps); it's a
  modest fine-tune, not a dramatic lever. *(The old 2-slot OAT "≈0 impact" verdict is overturned;
  and the CPU-grid's non-monotonic "sweet spot" was a path artifact that did not survive TRT — it's
  monotonic on the real path, so a linear slider is correct. See `tmp_analysis/g1/SUMMARY.md`.)*
- Both seeded by Calib2 at "50"; a raw-knob change in Advanced must **not silently de-anchor**
  them (today it does) — show a toast + visible re-anchor.
- **Output controls (decision 6, see Track X)** — *output-domain*, distinct from the two
  *detection* dials: a **box-clamp** toggle (report last-known-YOLO-size box) + an
  **output-smoothing (latency)** slider. These plus the two dials are the *only* things an
  operator touches live, and only if they choose to.

### 2.3 Status unification
Collapse the 15+ badges + inline texts + modal report cards into: (a) the top-bar chip group
(health), (b) per-phase status lines (calibration state), (c) one **Alerts strip** for
warnings (GPU temp, TRT fallback, OSC down, config-vs-scene mismatch, height-stale).

### 2.4 Cleanups bundled here (audit-flagged)
- ✅ **Toast thread-safety — already fixed** (render-loop expiry, gui.py:2545-2548/2594-2597).
  Verify only; no work.
- ✅ **`_centered_modal` factory — already exists + used 9×** (gui.py). Verify no hand-rolled
  centering remains; otherwise no work.
- Remove the **dead `motion_sensitivity` slider** from its current home (it becomes Dial B
  *only if* Track G validates it; otherwise gone). **Gated on G1.**
- Kill the **STANDBY/RUN two-button ambiguity** → phase ⑥ is "Go Live / Stop" with a clear
  "what turns on" line (YOLO + OSC).

### 2.5 Exclusion = manual paint, always visible (decision 5)
- Paint-style cell editor on the preview, reachable in phase ① (framing). The operator marks
  known dead zones (balcony, reflective wall, doorway, bystander strip).
- **Masked cells render at all times** (dimmed overlay), not only in edit mode — the operator
  always knows what's blinded.
- **No auto-detect, no auto-apply** (decision 5). *Optional, low-priority, build-only-if-cheap:* a
  **ghost-hint heatmap** (where persistent fixed-spot detections cluster) the operator may
  *consult* while painting — it never masks anything. **Ship it only if it's a few lines reading
  data the pipeline already produces; if it grows into real complexity, skip it** — not worth a
  feature that bites back on later dev (operator's call 2026-06-15). Pure manual is the floor.

---

## 3. Track C — Calibration v2 (when/where each step happens + correctness fixes)

The two-calibration model is right; the gaps are **ordering, persistence, gates, and the
unbuilt enhancement sweep.**

### 3.1 Procedure changes
- **Single guided "Calibrate" flow is the default** (the existing `CalibrateAllWizard`
  becomes phases ③→④ of the rail). The 4 separate buttons (CALIBRATE/DANCERS/POOL/ALL) collapse
  into the rail; POOL becomes "review pool" inside ④.
- **Two dancer-calibration entry modes** (workflow + decision 7): **(A)** record rehearsal
  run(s) → pool → Apply (preferred); **(B) short-install fallback** — run the pass *live on
  show-open* as dancers enter. The pool must accept a **short live in-show window** gracefully:
  apply a usable result fast from few frames, keep accumulating, let the operator re-Apply as
  more evidence arrives without disrupting the running show.
- **Exclusion leaves the scene pass** (decision 5): ③ Aim derives servo + gamma + var +
  clean-plate only. Exclusion is a manual phase-① paint step (Track O §2.5) — no auto-build.
- **Soft ordering enforcement:** ④ warns if ③ (Aim/servo) never ran in this profile; ③ blocks
  if a pool review is open. (audit: today `_cb_calib2` only checks Calib1 isn't *currently*
  running.)

### 3.2 Correctness fixes (audit-found; confirm each in code before fixing)
| Fix | Problem | Action |
|-----|---------|--------|
| **Height ownership** | Calib1 measures+writes `person_height_px`, Calib2 also writes it; UX_PLAN says Calib2 owns it | Make Calib1 height **diagnostic-only** (for MOG2 scaling/report), Calib2 the sole writer; tag source in `calibration_state` |
| ~~`blur_budget_ms` not persisted~~ | **NOT a bug (verified 2026-06-15):** saved (app.py:987) + restored on project load (app.py:1185-1186). *Optional polish:* seed `calibration_flows` init from config for the pre-load window — low value | — |
| **No apply gates** | Calib1 applies even on `height_ok=False` / `var_saturated=True` | Warn-banner (don't hard-block; idempotent design) before silent application |
| **Stale flag too narrow** | Only checks ROI drift; ignores profile/lighting mismatch | Add profile-mismatch flag to the pool dialog |
| **imgsz reload silent-fails** | TRT export failure leaves config≠engine, no feedback | Surface success/failure; offer fallback imgsz |
| **noise-σ window mismatch** | Calib1 window σ vs live `motion_model.noise_sigma()` drive the dark-target (110 vs 45 px) differently | Record `noise_sigma_live` in Calib1 result; Calib2 reuses it if Calib1 just ran |
| **`tracker_intermittent_confirm` unwired** | Per-scene switch documented but `tracker.py:792` reads the global constant, not project config | Plumb the project key → tracker (and add to `PROFILE_KEYS`/schema) so bug-#14's aerial/dark win is reachable per-scene |
| **Calib2 pool: selection ↔ proposal/apply** | The phase-④ inline pool's "Pooled proposal" aggregates **all** pooled runs, not the checkbox selection, and Apply is a manual button (operator UI feedback 2026-06-15) | Add a `calibration_flows` path to recompute the proposal for the **checked subset** and publish it (live preview on toggle), plus a **quiet apply** (no result-modal) so a selection change auto-applies. *Operator-surface hooks already shipped:* the inline pool renders `Calib2PoolChanged`, so the calib side only needs the subset-preview + quiet-apply |

### 3.3 The unified-calibration merge (phased) — owns the gamma/CLAHE gap

CLAHE is the **dominant drop lever** (case-3: 0.20 vs 0.93) with **no formula** (equally-dark
scenes want opposite CLAHE), and its offline sweep is **deferred/unbuilt**. Because CLAHE is a
*detection* knob that wants dancers in frame (§0.1), the fix and the seam are the same move.
**Phased, with the merge as the explicit target** (agreed with the parallel calib agent
2026-06-15):

**Phase C-now — CLAHE → detection sweep over pooled dancer frames** *(parallel agent owns the
engine).* Sweep CLAHE on Calibrate's pooled frames, pick by detection. Keep **gamma
brightness-driven, relax the 2.2 clamp** (case-3: gamma is a minor lever; not worth the
coupling cost of detection-optimizing it). Low-risk, validated, and every brick is reusable in
the merge. **G2 measured the ranges (2026-06-15, GPU+TRT):** search **`clahe_clip ∈ {1.0(off),
1.5, 2.5, 4.0, 6.0}`**, pick by detection (`avg_detections` / YOLO count×conf) **per scene** —
optima span off→≥4.0 and can be U-shaped (4 dark scenes → 4 different optima → **no formula**);
**gamma-stays-formula confirmed**. (`tmp_analysis/g2/SUMMARY.md`.) Track G *supplied* these
ranges; it does not duplicate the sweep.

**Phase C-next — unified calibration engine** *(deliberate joint design pass).* Collapse the
engine to **Aim (servo, autonomous, early) + one Calibrate-with-dancers pass** deriving, in
coupling order: **gamma (brightness) → var + clean-plate (on chosen-gamma gray) → CLAHE +
height + imgsz + confidence (detection-derived) → blur budget**, all over the evidence pool.
(Exclusion is manual, out of this chain — decision 5.) Three things the merge must get right
(flagged by the calib agent):
1. **Clean-plate background.** Empty stage is the gold standard for MOG2 / static-person
   recovery. A dancers-present pass must recover it via robust median + skeleton-sparing, and
   should opportunistically use the dancer-free opening seconds. → **de-risk first with Track G
   G6.** (Doubly relevant given the short-install fallback, where ③ may be skipped: the pass
   must grab a clean-ish plate from the show's opening frames.)
2. **Cadence / trust.** Scene params (var, exclusion, gamma) are stable per rig; dancer params
   (height, conf) re-pool more often. The pool schema must **tag which knob came from which
   run** so dancer evidence can be re-pooled *without* re-deriving scene params.
3. **Refactor risk.** The calibration engine is load-bearing → its own design pass, not a
   rushed bolt-on. The unified-engine spec is the calib agent's deliverable; Track G feeds it
   the derivation-order + clean-plate evidence (G2/G6).

---

## Track X — Output / OSC layer (box coherence + fixed-lag smoothing)

**Principle (case-1 + the operator's framing):** keep the *internal* motion box **large**
(load-bearing for the bridge gate + MAX_VELOCITY — shrinking it in the tracker regressed drops),
and make the *reported* representation coherent at a **dedicated stage between tracker and
OSC/preview**. Today OSC sends a **raw `bbox`** (the fat motion box → flickers) plus an
EMA-smoothed `centroid`; this stage closes that gap. **Two decoupled controls (decision 6):**

- **Box-clamp toggle** — when a track is motion-bridged, report a box of the **last-known YOLO
  size** at the bridged **centroid** (position-from-motion, size-from-YOLO-memory). Internal box
  untouched → fixes the case-1 size flicker outright. On by default; off = raw blob extent.
- **Smoothing-depth slider (latency)** — drives a **fixed-lag (look-ahead) buffer** of L frames,
  **default L = 1 (minimal latency)**, raised by the operator on demand (decision 2026-06-15). At
  L = 1 the stage is effectively today's causal stream (box-clamp + light de-jitter only); raising
  L buys, for ~L/fps of latency (each +frame ≈ 50 ms at 20 fps — operator's per-show call):
  1. **Trajectory de-jitter** via a proper fixed-lag / RTS smoother (the acausal cousin of the
     Kalman already running) — smoothing without the causal filter's lag-vs-noise tradeoff.
  2. **Retroactive bridge correction** — once YOLO re-acquires, the bridged segment is corrected
     *backward*, so a bridged path is clean in hindsight.
  3. **Case-2 flying-ghost fix, made safe** — suppress a bridged segment only if, looking L
     frames ahead, it *never re-acquired a real skeleton*. Real aerials re-acquire within the
     window (1-in-3 frames) → kept; ambient-motion flyers never do → dropped. The latency budget
     turns the deferred "too risky" causal fix into a robust one.
  4. **Steady high-rate OSC** — resample the smoothed trajectory to a fixed output rate
     regardless of YOLO cadence (output-side interpolation — the useful kind; input-frame
     interpolation does **not** help detection).

  *Items 2–3 (retroactive correction, case-2 suppression) only engage once L is raised past a
  few frames; at the default L = 1 the stage is just box-clamp + light de-jitter, so the default
  experience is low-latency and the heavier behavior is strictly opt-in.*

**Dual tap:** offer a **live causal tap** (zero-lag, for latency-sensitive consumers) *and* the
**smoothed lagged tap**, and **publish the latency** so TouchDesigner can compensate.

**Why low-risk + high-leverage:** lives entirely at the output boundary; touches **neither the
detector nor the tracker internals** (the case-1 lesson). One module resolves case-1 (box),
case-2 (flying ghost), trajectory smoothing, and OSC cadence. **Ownership:** operator-facing
controls + integration = this track; the fixed-lag/RTS smoother core is a good **joint spec**
with the engine agent (they offered to scope it). See §6.

---

## Track D — Detection-quality root-cause backlog (ranked; mostly corpus / R&D)

Both agents converged: the cheapest *durable* wins are at the **input boundary** (raise SNR / the
model's competence) so the compensation cascade (ROADMAP §2) becomes optional.

> **⚠ RESEARCH-FIRST, NOTHING COMMITTED (operator directive 2026-06-15).** Be *very* conservative
> here: do **not** introduce badly-scoped pipeline variability now. Each lead below is a
> **research item**, gated by a scoped investigation that must (a) measure the benefit through the
> full pipeline against the pass lines, (b) be replay-gated, and (c) clear an explicit go/no-go
> **before any implementation**. The default for every item is *don't touch the pipeline*. These
> live in the roadmap as future leads, ranked by expected leverage — not as a build queue.

1. **Fine-tune the pose model on the IR corpus — highest leverage, but DEFERRED.** Every
   downstream knob exists to compensate for a COCO-RGB-trained model seeing near-black IR dancers;
   fine-tuning would lift the whole confidence distribution (ROADMAP §2/§3b). **Blocked: not
   enough samples / labeling yet** (operator 2026-06-15) — revisit once the corpus has enough
   labeled IR frames. Keep as the marquee future improvement; engine-agent territory when unblocked.
2. **Motion-gated temporal denoise.** Fixed-camera near-black + high gain = noisy; that noise is
   what CLAHE amplified into drops. Average the *static* regions across frames, leave *moving*
   pixels sharp (gate by frame-diff) → SNR for YOLO + attacks the static/aerial drop. The
   existing `denoise` is a naive whole-frame EMA (smears movers) → upgrade. Stay
   **frame-independent on the motion feed** (bug #1).
3. **Native-bit-depth tone-map.** IDS is Mono10/12 but we expand to 8-bit early; a gamma LUT on
   already-quantized 8-bit can't recover shadow detail quantization threw away. Curve in native
   depth, **quantize last** — a near-free SNR win in the 1–5/255 regime.
4. **Optical-flow coherence on the motion path.** Frame-diff = "did this pixel change?"; sparse LK
   flow = "is this *coherent* motion?" — stronger ghost/real discriminator + a velocity field
   that improves the bridge prediction. Sparse LK is cheap.
5. **Clean-plate static-person path.** MOG2 absorbs a stationary dancer into background (the
   TOGO-sitter drop); a fixed clean-plate (capturable in ③; `background.py` exists, dormant)
   doesn't forget. The robustness layer §6.2 wanted; half-built.
6. **IR-PSF sharpening / mild deconvolution.** The Tamron glass isn't IR-corrected (rig memory) →
   soft IR focal plane. A kernel tuned to the measured IR blur recovers real detail. Exotic but
   physically motivated (fixing optical loss, not inventing data).
7. **3-frame difference** (vs 2-frame) — cleaner moving-object isolation; cheap.
8. **Motion-ROI tiling for small-far dancers** — crop YOLO to motion blobs at higher effective
   resolution (pairs with the TODO 4K-tiling item).
9. **Surface untuned knobs:** NMS/IoU (crowded), keypoint-confidence floor, latency-tolerant
   **multi-scale / TTA** (fuse two imgsz or a flip — robustness for the 0.4–1.8× in-scene size
   spread, at N× cost).

**Rejected:** edge/gradient *as a YOLO input* (COCO-RGB domain mismatch → detection collapses).
Edges *are* useful as a **scene-characterization signal** (laplacian/gradient to pick
enhancement — case-3's real discriminator) — in the calibration brain, not the pipeline input.

---

## Track P — collapse to a GPU-only evidence base (3 paths → 2)

**Status:** agreed 2026-06-15. Deliberate future track (after batch-1; coordinate with the calib
agent — touches the cache/golden assumptions). **Motivation:** the dual CPU/GPU paths cost real
predictability — **G1 proved the cheap CPU-cache grid *mis-estimated* a bridge knob vs production
GPU+TRT.** The divergence is entirely in *preprocessing* (cv2 vs kornia CLAHE + letterbox
interpolation); the post-YOLO chain is already unified (bug #10). Three configs today: **#1 CPU**
(cv2 enhance + GPU YOLO), **#2 GPU+PT FP32**, **#3 GPU+TRT FP16** (production).

**Ops reality driving the design:** one machine is **dev+prod** (RTX 5080 mobile); a second is
**debug / remote-assist** (RTX 3090 desktop, used when the 5080 is live). No non-GPU machine.

**End-state (3 → 2):**
1. **Drop CPU path (#1) entirely.** Removes ~270–400 LOC of CPU-only orchestration (`_process_cpu`,
   `_track_detections`, `_offset_detections`, `_identity_scaled_track`, `_OffsetMotionProxy`), the
   **dual coordinate-space transforms** (full-frame vs ROI-local letterboxed — the bug #5/#9 class,
   gone by construction), and the CPU↔GPU **parity test** (moot with one preprocessing path).
2. **TRT (#3) = single production + evidence base.** Tuning + goldens run on GPU+TRT via
   `detect_cache` → *exact* "test what you ship": **eliminates the FP32→FP16 proxy gap and G1's
   mandatory TRT spot-check step** (the cache freezes TRT detections → replays deterministic).
   Re-baseline goldens only on engine rebuild (TRT/driver bump) — trivial on one prod machine.
3. **Keep PT FP32 (#2) as a thin shared-code fallback/debug backend** — NOT a tuning/golden target.
   Post-#1 it's a one-point YOLO-backend toggle on the shared GPU pipeline (near-free). Earns its
   keep via: (a) **live-show graceful degradation** (TRT engine bad → PT-on-GPU, show survives — the
   current `model_manager` fallback), and (b) **instant 3090 debug** (`.pt` loads on any GPU, no
   per-machine engine build — TRT engines are per-GPU + per-TRT-version, non-portable from the 5080).
4. **Startup/readiness engine gate.** Upgrade the existing `ops_monitor.check_tensorrt` +
   `model_manager` auto-export into a **hard pre-show gate**: validate prod engines present + match
   (model/imgsz/GPU/TRT-version); offer the existing auto-build as a *blocking* "build now"
   (build_engines style). #2 stays the in-show net if something slips.

**Why not 3 → 1 (drop #2 too):** marginal — post-#1, #2 is a backend toggle, so deleting it saves
~one branch + the TRT↔PT switch UI while forfeiting the live-show fallback *and* the 3090
instant-debug ergonomics (both load-bearing in the ops model above). Keep #2.

**Real costs (accept knowingly):** goldens move byte-identical(cv2-CPU) → TRT-engine-locked
(deterministic run-to-run on a fixed engine for *code* changes; re-baseline on engine rebuild;
cross-machine 3090 → tolerance only). *Pre-commit check:* regenerate one golden twice on the TRT
cache and confirm byte-stability before relying on it. Lose the `--cpu` GPU-free app mode
(irrelevant — no non-GPU machine; YOLO-on-CPU is unusably slow anyway).

**Migration (bounded, one-time):** re-baseline 12 goldens on the TRT cache; repoint
`detect_cache.build_cache` + `tune`/`sensitivity` to the GPU/TRT path (invalidates old CPU caches);
retune `test_regression_replay` tolerances; delete CPU-path code + parity test; harden the engine
gate; update README (`--cpu`), TUNING/ROADMAP env notes, `replay.py` (`--gpu-path`/`use_gpu_path`
default/removed). **Companion to Track G:** once done, every Track-G run is trustworthy by
construction (no proxy gap).

---

## 4. Track S — Setting governance (what's fixed / calibrated / user, ranged from data)

**Problem (audit):** ~140 params, ~17 user-facing; 50+ tracker constants frozen at
module-import (no per-project override without restart); KNOBS tiers measured on **only 2
slots**. We need a single governance table where every actionable knob has: **owner** (Fixed /
Calib1 / Calib2 / User) · **default** · **data-fit range** · **where surfaced**. Track G
produces the data; this table is the deliverable.

### 4.1 Proposed governance (★ = re-confirm/range-fit in Track G)
**G5 — finalized 2026-06-15 from G1 (dials) + G2 (CLAHE) + G4 (tier re-validation).** ✅ = measured.

| Tier | Knobs | Owner | Surfaced |
|------|-------|-------|----------|
| **User (live)** | Dial A confidence ✅(G1: cardinal, per-scene, spans 0.15–0.65, inverts); Dial B gap-bridging ✅(G1: monotonic "fewer drops", calibrated-seeded); **output box-clamp toggle + smoothing slider** (Track X); ROI/stage; **manual exclusion paint** (decision 5); profile switch | User | Phase ⑥ + ① |
| **Aim / servo** (live, early) | exposure, gain | servo | Aim report card |
| **Calibrate / scene-signal** (no dancers needed) | gamma ✅(G2: stays brightness formula, clamp relaxed), mog2 var×scale, clean-plate, brightness_threshold | calibrate | report card |
| **Calibrate / dancer-signal** | CLAHE ✅(G2: per-scene sweep {1.0…6.0}, **no formula**, pick by detection — unbuilt), person_height + ratios, imgsz (+model advisory), confidence seed ✅, blur budget | calibrate | pool dialog |
| **Calibrate / per-scene (known-N, Phase 3)** ✅(G4) | `tracker_max_age`, `crossval_skel_min_kpts` (θ_s), `crossval_motion_min_ratio` (θ_m) — scene-dependent (0.03–0.07 on multi-dancer/occlusion + static-sitter; inert on easy scenes). Set by the Phase-3 joint search, **never a user surface**. θ_m motion-coupled → TRT-confirm | known-N calib | none (internal) |
| **Fixed (internal)** ✅(G4: inert all-corpus) | `crossval_skel_min_conf`, `tracker_smoothing`; + the ~50 tracker/bridge/warmup constants, Kalman Q/R, swap correctors (off); NMS/IoU + keypoint-conf floor (Track-D candidates) | Fixed | Advanced drawer (read-mostly) |
| **Drop / retire** | **auto-exclusion builder** (decision 5); `tracking_mode` UI (P3 merged); `bg_subtract` → clean-plate path (Track D); duplicated `tracker_max_age` defaults. *(`motion_sensitivity` is **not** dropped — it's now Dial B.)* | — | removed |

### 4.2 Structural fixes
- **`calibration_state` metadata** in config: which value came from which phase + when → drives
  "stale, re-calibrate?" prompts, the readiness config-vs-scene line, **and the ③ Aim panel's
  "Last calibrated: …" line + applied-parameter influence** (operator UI feedback 2026-06-15). *The
  Aim panel already shows a `aim_last_calib_text` placeholder + the static influence list
  (exposure/gain → gamma → MOG2 → clean-plate), awaiting this metadata to fill the timestamp + the
  actual per-parameter values.*
- **Module-import constants → per-project where a knob earns it** (only the ones Track G shows
  matter; the rest stay compile-time). Avoids restart-to-retune.
- **Do NOT split display vs motion gamma.** Verified: the motion feed uses `enhancer.gamma`
  (pipeline.py:1184) so gamma is shared and var/exclusion are scored on the post-gamma gray.
  Keep gamma **brightness-driven** (case-3: minor lever) and **derive it before var/exclusion**
  in the unified pass — splitting it isn't worth the coupling cost.
- Add safety **clamps/ranges** for any knob promoted out of Fixed.

---

## 5. Track G — the cross-parameter test (ground truth for §2–§4)

**Goal:** for every actionable knob, decide **fixed vs calibrated vs user**, its **data-fit
range**, and its **cross-influences** — by measuring through the full pipeline against the
12-scenario pass lines, **cheaply**.

### 5.1 The hierarchy (set-order; each tier depends on the ones above)
| Tier | Knobs | Cache cost | Notes |
|------|-------|-----------|-------|
| **T0 Sensor** | exposure, gain | n/a (live HW) | not in recorded-corpus sweep — servo, validated on rig |
| **T1 Enhance** | gamma × CLAHE | **rebuild** (front-end) | dominant drop lever; no formula (case-3) |
| **T2 Geometry** | person_height × imgsz (× ROI) | **rebuild** | coupled via net-height (110 / 45-dark) |
| **T3 Threshold** | confidence | rebuild, **or cheap via 0.05-floor cache + re-apply** | master dial (Dial A) |
| **T4 Motion** | mog2 var × scale; **bridge/motion_sensitivity (Dial B)** | **cheap** (post-YOLO, from cache) | var×scale only matter jointly (KNOBS #2) |
| **T5 Tracker/gate** | θ_s/θ_m, warmup, swap correctors | **cheap** | mostly Fixed; re-validate inertness on 12-corpus |

**The cost lever:** post-YOLO tiers (T4/T5) replay from one cache at ~10 ms/frame (memoised
grays); front-end tiers (T1–T3) need cache rebuilds (~125 ms/frame). So the test sweeps T4/T5
exhaustively, and does only **coarse** T1–T3 grids on a **representative subset** with
**frame-skip**. (Phase 2b already proved big cache sweeps work; motion-feed — not YOLO —
dominates cost, so a future motion-gray cache tier would speed T5-only searches.)

### 5.2 Representative subset (5 slots, ~42% of corpus, ~2.4× speedup)
`hangar-floor` (clean A) · `hangar-aerial` (aerial drop A) · `texture-duo` (ghost+duo A) ·
`dark-crowd` (dark B) · `outdoor-sitter` (static B). Validated by: run the chosen winner on the
**full 12** via cache and check the per-scenario spread is within tolerance.

### 5.3 Cheap-first run (decision 4) — validate the two-dial design
**The first measurement, ~30 min, gated:** answer *"is Dial B real, and what is each dial's
range?"* — which directly de-risks decision 3.

> **✅ DONE 2026-06-15 (~16 min). Verdict (full record: `tmp_analysis/g1/SUMMARY.md`):**
> Dial A confirmed cardinal (per-scene, spans 0.2→0.5, direction inverts → calibrate + nudge).
> **Dial B (gap-bridging) = KEEP**, as a *monotonic* calibrated-seeded "fewer-drops" dial — on
> GPU+TRT it cuts aerial drops ~22% at zero ghost/id cost, inert elsewhere (modest fine-tune).
> 2/5 scenes (texture-duo, dark-crowd) are not dial-solvable → validates manual exclusion + Track D.
> **Load-bearing methodological finding:** the cheap CPU-cache grid **mis-estimates bridge/motion
> knobs** in the low-conf aerial regime (it found a non-monotonic "sweet spot" that fully reversed
> on TRT). The CPU grid is reliable for **front-end knobs (Dial A)** but every **post-YOLO
> bridge/motion finding MUST be GPU+TRT-confirmed** — step 3 below is not optional. (Frame-skip
> pre-check was skipped — the `--frame-skip` flag isn't built yet; ran full-frame, still ~16 min.)
1. **Frame-skip safety pre-check** (one slot, ~3 min): score `hangar-aerial` at stride 1 / 2 /
   4. Adopt the largest stride whose score stays within ±0.02 of full.
2. **Two-dial grid** (`tune.py --strategy grid`, cache-backed) on the 5-slot subset:
   `confidence ∈ {0.20, 0.35, 0.50}` × `motion_sensitivity ∈ {0.25, 0.55, 0.85}` ×
   `mog2_var_threshold ∈ {8, 16, 40}`. Score via `score_multi` (mean + worst) vs pass lines.
   - Output A: does `motion_sensitivity` move the score **independently of confidence**, and is
     the effect **monotone with a usable range**? → keep/scope/drop Dial B.
   - Output B: confirm Dial A's range (corpus-wide best-τ span around the seed).
   - Output C: confirm var×scale only matters when var is awake (KNOBS #2) — informs whether
     var belongs in Dial B at all.
3. **GPU+TRT spot-check** the winner on 1–2 slots (`replay.py --trt`) — the bridge regime is
   where CPU↔GPU diverge most (detection-fix plan); confirm the cheap CPU-cache verdict holds.
4. **Report + gate:** present results; **ask before** any larger sweep (T1 enhance grid, T2
   geometry, full-corpus governance pass).

### 5.4 Gated escalation ladder (each only on operator go)
- **G1 ✅ done (2026-06-15)**: two-dial validation → Dial A cardinal, Dial B keep (monotonic,
  calibrated-seeded); CPU-grid mis-estimates bridge knobs, TRT-confirm mandatory. (Frame-skip
  safety not run — flag unbuilt.)
- **G2 ✅ done (2026-06-15, GPU+TRT direct)** — CLAHE on 4 dark scenes → **4 different optima**
  (dark-crowd off/low; hangar-floor high ≥4.0; outdoor-night mid/U-shaped; aerial ~flat) ⇒ **no
  formula, sweep per scene.** Hand-off to Phase-C-now: search `clahe_clip ∈ {1.0,1.5,2.5,4.0,6.0}`,
  pick by detection (`avg_det`/count×conf); **gamma-stays-formula confirmed** (held pinned, CLAHE
  alone gave the full spread). Run direct on GPU+TRT — CLAHE is the cv2↔kornia divergent knob
  (CPU cache would mislead). Record: `tmp_analysis/g2/SUMMARY.md`.
- **G3 ✅ done (2026-06-15, narrow re-confirm on current code + TRT)** — dark-scene inversion holds
  on the live path: low imgsz wins on dark IR (dark-crowd→640, hangar-aerial→640, outdoor-night→960
  U-shaped); re-validates Phase-2b's dark-target (net-height 45 / low imgsz), no drift. **Bonus:**
  hangar-aerial's *pinned* imgsz 1280 is too high (640 → drop .19→.03) → its calib imgsz should drop.
  Bright-scene knee = trust Phase-2b (corpus is overwhelmingly dark). `tmp_analysis/g3/SUMMARY.md`.
- **G4 ✅ done (2026-06-15)** — candidate-Fixed knob OAT on the 5-slot subset **splits the Fixed
  tier**: `crossval_skel_min_conf` + `tracker_smoothing` inert on all 5 (truly Fixed); but
  `tracker_max_age`, θ_s-kpts, θ_m carry **0.03–0.07 on multi-dancer/occlusion + static-sitter**
  (inert on easy scenes) → **per-scene "known-N (Phase 3)" class**, not user dials, not deletable.
  Confirms KNOBS' own "FIXED = hide-not-delete" caveat. θ_m motion-coupled → TRT-confirm in Phase 3.
  Record: `tmp_analysis/g4/SUMMARY.md`.
- **G5 ✅ done (2026-06-15)** — governance table finalized in §4.1 from G1/G2/G4.
- **G6 ✅ done (2026-06-15)** — var/scene-stats recovery: the MOG2 var-sweep is
  **window/dancer-invariant** (var 8@0.7 identical at an early window vs the dancers-present window
  on all 4 recordings; FP ~0 both) → **C-next's one dancers pass can derive `var` directly; no
  empty-stage pass needed** for the var/scene-signal half. **Scope:** the clean-plate *pixel*
  recovery (skeleton-sparing robust median) needs the **unbuilt** C-next mechanism (background.py
  only snapshots) → handed to the calib agent to validate when building, not faked here.
  `tmp_analysis/g6/SUMMARY.md`.

### 5.5 Harness gaps to close (small, enables the above)
- A `--frame-skip` flag on `replay.py`/`tune.py` (cheap exploration).
- 0.05-floor cache + τ re-apply hook so confidence is cheap (Phase 2b used a custom hook;
  promote it).
- `calibration_state`-aware scenario configs so governance runs use per-tier pins.

---

## 6. Coordination & sequencing

**Ownership seam (proposed, mirrors the code's core/ vs ui/ split):**
- **Parallel calib agent → the calibration *engine* + detection-quality root-cause.** Phase
  C-now (CLAHE detection sweep + relax gamma clamp), Phase C-next (unified Aim+Calibrate
  derivation engine: order, pool-knob-tagging, clean-plate, servo handoff, two-button migration),
  and **Track D leads when unblocked** (fine-tune deferred — no labels yet; SNR items
  engine-leaning, research-first).
- **This track → the *operator surface* + governance + measurement + the output boundary.**
  Track O (rail/drawers/status/crash-fix), Track S (governance), Track G (cross-parameter test),
  and **Track X operator controls + integration** (box-clamp toggle + smoothing slider). Track G
  **supplies** the CLAHE ranges (G2) and clean-plate verdict (G6) to the engine; it never edits
  calib code.
- **Joint design passes:** (a) the **unified calib engine** (C-next; engine agent authors, Track
  G data feeds it); (b) the **Track-X fixed-lag / RTS smoother core** (engine agent offered to
  scope it; this track owns the controls + OSC integration).
- **Rule:** neither side edits `calibration.py`/`calib2.py` sweep logic — or the tracker core —
  without a heads-up.

**Recommendation to the calib agent's question** ("ship CLAHE increment now, or straight to the
unified redesign?"): **ship the increment now + treat the merge as the next deliberate effort.**
Going straight in hits the gamma-coupling + clean-plate issues mid-build (their own warning); the
increment is a validated, reusable first brick, and G2 + G6 give the redesign data, not guesses.

**Suggested order (cheap → expensive, UX → data):**
1. **G1 cheap-first run** (gated) — settles whether Dial B (gap-bridging) is real → decision 3.
2. **CLAHE increment** ships in parallel (engine agent); G2 feeds its ranges when run.
3. **Track O scaffold + Track X box-clamp** — phase rail + drawers + manual-exclusion overlay +
   status unification + toast/crash fix, and the **box-clamp toggle (case-1 fix, cheap)**. No
   detection risk; replay goldens stay green; output stage touches only the OSC/preview boundary.
4. **Track C correctness fixes** §3.2 (small, high-value; each replay-gated).
5. **Track-X fixed-lag smoother** — once the smoother core is jointly specced; brings the case-2
   flying-ghost fix + retroactive bridge correction (latency-budgeted, dual-tap).
6. **G2–G4 + G6** as the operator grants benchmark time → **G5 governance table** → finalize §4.1.
7. **Phase C-next unified engine** — deliberate joint design once G2/G6 land.
8. **Track D** = research-first, **nothing committed** — each lead needs a scoped, replay-gated
   benefit validation + explicit go/no-go before any build; fine-tune (#1) is **deferred**
   (insufficient labeled data). **Not part of the autonomous implementation run.**
9. **Phase ⑤ Verify** = wire the readiness check + a one-click dry-run replay into the rail;
   write `docs/NEW_SHOW.md` from the spine (closes ROADMAP §4.2 Phase 4).

## 8. Execution readiness — autonomy map (for an ultracode implementation run)

Labels every near-term item so an autonomous implementer knows its lane: 🟢 **autonomous**
(well-defined, replay/smoke-verifiable, low regression risk — build + verify without asking),
🟡 **checkpoint** (build, but stop to show results / confirm a default / confirm-in-code before
merge or the next step), 🔴 **ask-first** (ambiguous, design, cross-agent, or high-risk — confirm
scope first). Track D is **out of scope** for the autonomous run entirely.

| Item | Track | Class | Why / verification |
|------|-------|-------|--------------------|
| Phase rail + Advanced/Recordings drawer scaffold | O | 🟢\* | UI-only, no pipeline. **\*checkpoint a layout mockup + file-ownership first** (`gui_builder.build_phase_rail()`? section→Advanced-drawer mapping; update `gui.py:_section_headers`/`_check_section_exclusion` for moved sections). Then app-smoke + goldens unchanged |
| Status unification (chip group + **alerts strip**) | O | 🟢 | UI-only. Alerts strip must exist so the imgsz-fail/TRT/health fixes can surface |
| Masked-cells-always-visible overlay | O | 🟢 | builds on existing `ui/roi_mask_editor.py` (`RoiMaskEditor`, `toggle_exclusion_cell`); render dimmed grid outside edit mode. UI-only |
| `--frame-skip` flag on replay/tune | G | 🟢 | isolated test tooling; unit-check vs full. **Prerequisite for G1** (missing today) |
| ~~Toast thread-safety fix~~ | O | ✅ done | already render-loop-expired (gui.py:2545-2548/2594-2597) — verify only |
| ~~Single `_centered_modal` factory~~ | O | ✅ ~done | `_center_modal()` exists + used 9× (gui.py) — verify no hand-rolled centering remains |
| Track X **box-clamp toggle** (default on) | X | 🟡 | **output-only path REQUIRED:** add a per-track *last-YOLO w/h* field (set when source=YOLO), clamp ONLY at the `ScaledTrack`/OSC boundary — **never mutate `DancerTrack.bbox`** (tracker.py:2894 sets it during bridge; feeds gating `bbox[3]` + MAX_VELOCITY — the case-1 trap). Verify internal gating byte-identical with/without toggle |
| `motion_sensitivity` → **Dial B** (keep) | O/S | 🟡 | **G1 resolved: KEEP** as a monotonic calibrated-seeded "fewer drops" dial. Build with the two-dial live surface (needs OSC contract + rail) — **still not in batch 1** |
| ~~`blur_budget_ms` persistence~~ | C | ✅ not-a-bug | already saved+restored (app.py:987/1185-1186). Optional: also seed `calibration_flows` init from config so a *fresh* session before load matches — low value |
| Track C correctness fixes (height ownership, apply gates, stale-flag, imgsz-fail feedback, noise-σ unify, `tracker_intermittent_confirm` wiring) | C | 🟡 | each small but touches calib write-paths / schema / tracker — confirm-in-code first, replay-gated, **heads-up to calib agent** |
| **G1 cheap benchmark** | G | 🟡 | gated on GPU-free; *produces a decision* (Dial B) → run, report, wait |
| Dial A/B finalization (labels, ranges) | O/S | 🟡 | gated on G1 + G2 ranges |
| Governance table finalize (§4.1) | S | 🟡 | gated on G2–G4 |
| Track X **fixed-lag / RTS smoother** core | X | 🔴 | trajectory/identity semantics + latency contract + **joint spec with engine agent** — design before code |
| Unified calib engine (C-next) | C | 🔴 | cross-agent, engine-owned, joint design |
| Any edit to `calibration.py`/`calib2.py` sweep logic or tracker core | — | 🔴 | cross-agent (calib agent owns) — heads-up + confirm |
| Exclusion ghost-hint heatmap | O | 🔴 | build only if trivially cheap — judge/ask |
| Track D leads | D | 🔴 | research-first, go/no-go each — **excluded from the autonomous run** |

**Ask-first triggers (rules the autonomous agent follows):**
- **STOP + confirm** before touching: tracker core (identity/gates), pipeline detection logic,
  `calibration.py`/`calib2.py` sweep logic, config-schema profile keys, or the **OSC message
  contract**.
- **STOP** if a change can't be **replay-gated** (goldens can't verify it).
- **Confirm the default** for anything user-visible at show time (a dial range/default, the
  latency default, what OSC emits).
- **Heads-up to the calib agent** before editing any engine-seam file.
- **Ask** on any item this doc leaves open (§7) or marks ambiguous.

**Definition of done (per item):** unit tests green · app smoke clean · **then the gate matching
the item's blast radius:** *pure-UI items* → smoke only (no replay needed); *output-boundary
items* (box-clamp) → replay on 1–2 bridged clips, internal metrics byte-identical; *config/logic
items* → `replay_sweep` (golden trio or full per item), expect a **documented golden re-baseline**
if behavior changes on purpose. One small commit per item with the measured/smoke result in the
message (the P2/Phase-2 discipline).

**Suggested autonomous batch 1 (pure UI refactor — goldens guaranteed green):** the Track O rail
scaffold (*after* the layout-mockup checkpoint), Advanced/Recordings drawers, status unification +
alerts strip, the masked-cells-always-visible overlay, and the `--frame-skip` flag. Verify by
**app smoke + unit tests only** (no detection change). Touches neither the detector/tracker core,
the calib engine, nor the OSC contract → runs end-to-end without stopping.

**Explicitly NOT in batch 1** (each has a real gate, per the 2026-06-15 readiness review):
- **Box-clamp** (🟡) — needs the output-only design above first (don't alias `DancerTrack.bbox`)
  *and* the OSC contract written down.
- **`motion_sensitivity` slider** removal/repurpose (🟡) — gated on **G1** (is Dial B real?).
- **Track C correctness fixes** (apply-gate warn-banner, imgsz-fail feedback + alerts strip,
  stale-flag, noise-σ, `tracker_intermittent_confirm` wiring) — config/logic, replay-gated,
  heads-up to the calib agent; they **re-baseline goldens** → separate noted commits.
- **OSC contract** (🔴, prerequisite for Track X / Dial B) — write down what `/walldance/dancer/*`
  reports pre/post box-clamp, the latency model, and whether the consumer wants the causal tap,
  the lagged tap, or both (today: bbox raw, centroid EMA-smoothed). A short `docs/OSC_CONTRACT.md`.

**Suggested autonomous batch 2 (output & live-control surface) — unblocked by G1/G2 (2026-06-15).**
Operator-surface lane (the calib agent owns `calibration.py`/`calib2.py` + Track C/D in parallel —
don't touch those). Build in order, **checkpoint after the first two**:
1. **`docs/OSC_CONTRACT.md`** (prerequisite) — current + planned `/walldance/dancer/*` semantics,
   box-clamp effect on bbox, latency model, causal-vs-lagged dual tap. Locked defaults: smoothing
   **L=1**, box-clamp **ON**. STOP for confirmation before changing anything OSC emits.
2. **Track X box-clamp toggle (default on)** — *output-only*: add a per-track last-YOLO w/h field
   (set when source==YOLO), clamp ONLY at the `ScaledTrack`/OSC/preview boundary, **never mutate
   `DancerTrack.bbox`** (tracker.py:2894 sets it during bridge → feeds gate + MAX_VELOCITY, the
   case-1 trap). Verify: replay goldens byte-identical + reported bbox stable on a bridged clip
   (`replay.py --trt`, hangar-aerial). **→ checkpoint here** (show OSC contract + box-clamp plan +
   goldens before continuing).
3. **Output smoothing slider (causal, default L=1)** — box-size EMA on the reported box; record
   latency in OSC_CONTRACT. The deep **fixed-lag/RTS smoother + retroactive correction + case-2
   suppression stay 🔴** (joint design w/ the engine agent) — OUT of batch-2.
4. **Two-dial live surface (phase ⑥)** — Dial A confidence ✅(G1: per-scene 0.15–0.65, inverts) +
   Dial B gap-bridging ✅(G1: monotonic "fewer drops", calibrated-seeded); split
   `sensitivity_macro` into two legible dials; raw-knob change must NOT silently de-anchor (toast +
   visible re-anchor). Output controls (2)/(3) on the same surface, separated from the detection dials.

DoD: UI items = app smoke; box-clamp = goldens byte-identical + bridged-clip check. Branch
`operator-v2-batch2`, per-item commits. **Still gated after batch-2:** fixed-lag/RTS smoother (🔴
joint w/ engine agent); Track-C fixes + unified C-next engine (calib agent); Track P (3→2 collapse);
Track D (research-first).

> **✅ SHIPPED 2026-06-15 (branch `operator-v2-batch2`).**
> 1. **`docs/OSC_CONTRACT.md`** — current `/walldance/*` + planned box-clamp / L=1 latency / dual-tap.
> 2. **Box-clamp (default ON)** — output-only; per-track `_last_yolo_wh`, clamp at the finalize
>    boundary, `DancerTrack.bbox` untouched. Gate = `_frames_since_skeleton>0` (operator-confirmed;
>    `is_bridged`-only would miss the cold-blob flicker — verified). **Goldens 3/3 byte-identical;**
>    GPU+TRT hangar-aerial: ON-vs-OFF `--out` identical, within-gap size jitter 10–14 px → **0 px**.
> 3. **Output smoothing slider (causal L=1)** — box-size EMA, `alpha = base/L`; latency table in
>    OSC_CONTRACT §B.2. Acausal fixed-lag/RTS stays 🔴.
> 4. **Two-dial surface (phase ⑥)** — `sensitivity_macro` split into Dial A (`macro_to_settings`,
>    confidence, Drops↔Ghosts) + Dial B (`bridge_macro_to_settings`, motion_sensitivity, monotonic
>    Gap-bridging, span 0.25–0.85). Raw Advanced knobs re-anchor their dial at 50 with a **toast**
>    (never silent). Output controls grouped separately under "Output".
>
> **Verify:** unit 326 passed / 7 skipped (+ headless DPG phase-⑥ build smoke); goldens green.
> **Deferred (NOT built):** Dial B *gap-derived* calib seeding (engine/calib lane — ships with a
> default seed today); fixed-lag/RTS smoother + retroactive correction + case-2 (🔴 joint).

---

## 7. Open items / to confirm
- ✅ `blur_budget_ms` persistence settled (works — app.py:987/1185-1186). ✅
  `tracker_intermittent_confirm` confirmed unwired (tracker.py:792 reads the global) — fix in §3.2.
- **OSC contract (🔴 prerequisite for Track X / Dial B):** write `docs/OSC_CONTRACT.md` — current
  `/walldance/dancer/*` semantics, box-clamp pre/post behavior, latency model, and whether the
  consumer wants the causal tap, the lagged tap, or both. (Track X defaults chosen: **L=1,
  box-clamp on**.)
- Confirm the 0.05-floor τ-filter hook still exists (Phase 2b) before relying on it for cheap T3.
- Decide Advanced-drawer vs delete for each Fixed knob after G4.
- **Exclusion ghost-hint heatmap:** build only if trivially cheap, else pure manual (operator
  leaning manual).
- **Track D #1 (fine-tune):** deferred — needs enough labeled IR frames / a labeling pass first.
- `NEW_SHOW.md` authored once the rail + Verify phase exist (dry-run on 2 projects via playback).
