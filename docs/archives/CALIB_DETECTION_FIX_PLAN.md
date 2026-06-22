# Calibration & Detection Fix Plan

> **⚠ ARCHIVED 2026-06-22.** Detection case 1/2/3/4 study record — all cases closed/deferred and
> the case-3 CLAHE sweep has since shipped (in-app `_cb_run_calib_sweep`). Forward work is in
> **[../ROADMAP.md](../ROADMAP.md)**. Kept as the root-cause reference. **Do not plan new work from
> here.** *(Internal links may point at the pre-move `docs/` layout.)*

> **▶ Forward calibration/detection work now lives in [OPERATOR_V2.md](OPERATOR_V2.md)** — the
> deferred items here are folded in: case-3 CLAHE → Track C (C-now sweep) + Track G G2; case-1
> box-flicker + case-2 flying-ghost → Track X (output box-clamp + fixed-lag smoother). This doc
> stays the **case 1/2/3/4 study record** (and the prerequisite reading for that history).

**Date:** 2026-06-13
**Origin:** Operator in-app pass (2026-06-13) over the §4.2 Phase-1 adopted configs surfaced live detection issues that the headless Phase-1 "PASS" verdicts did not predict. Root cause of the mismatch is a **validation-path divergence** (below), plus a handful of genuine code/seed issues. Companion: [ROADMAP.md](../ROADMAP.md) §4.2, [CORPUS_ANALYSIS.md](../CORPUS_ANALYSIS.md).

---

## 0. Validation context — READ FIRST

- **GPU+TRT is the production path and therefore the validation base.** Almost every show runs the TensorRT FP16 engine on the GPU path. All compare/validate/gate work for detection-quality changes runs **GPU + TRT**, not CPU and not GPU-`.pt`.
- **Why the headless PASS lied:** `tests/replay.py` defaults to `use_gpu_path=False` (the CPU `_process_cpu` path) and loads `.pt` only (`replay.py:179`); its own header admits results are *not* the TensorRT/fp16 production run. The CPU↔GPU parity test only ever claimed **87% count agreement / 53 px p95 on the bridge regime**. So a CPU-path PASS was never a promise about live GPU+TRT behaviour — and the operator's complaints all cluster in the motion/bridge regime where the paths diverge most.
- **Also stale:** the Phase-1 scores (2026-06-10) predate Phase 2 ①–⑧ + 2b (06-11/06-12), which changed tracker/detection *behaviour* on top of the same configs. The live code is not the code that produced the PASS.
- **CPU goldens stay** — but only as a byte-parity tripwire, not as the quality oracle.
- **Test discipline (operator, 2026-06-13):** targeted single slot / situation first; **no long/coverage runs until a fix looks right**, then ask for a coverage-run confirmation.

---

## 1. Root causes (verified in code)

| Case | Symptom (operator) | Verified mechanism | Location |
|---|---|---|---|
| **1** | Motion box >> YOLO box; hard size flicker when bridging YOLO↔motion; "present in most footages"; testflou dancer↔shadow switch | `update()` adopts the measurement box wholesale (`self.bbox = np.array(bbox)`). Kalman R protects the **centroid** from coarse blobs but **nothing protects box dimensions**. Motion box = `cv2.boundingRect` of MOG2 silhouette, shadow-inflated when `include_shadows=True` (IR path) | `tracker.py:442`, `motion_detector.py:367`, `detect(include_shadows=...)` |
| **2** | TOGO-day "flying ghost": a YOLO ghost vanishes but a motion box stays and flies, "often from the ghost spot toward top-left" | **Diagnosed + dynamically confirmed (GPU+TRT, full slot 9 @ live conf 0.4); fix DEFERRED.** The frozen-ghost report gate (`_collect_confirmed_tracks`, tracker.py:3140) suppresses a skeleton-stale track only if also near-stationary (`speed < 0.03·person_height_px ≈ 6 px/frame`); "moving ⇒ real" is the loophole. JSONL evidence: flyer tracks fire `GHOST_FROZEN_SUPPRESSED` on slow frames but report on fast ones (same track). **Correction to the first hypothesis:** the flyers are *not* presence-coasting — they're `MOTION_BRIDGE_LOCAL_SUPPORT` (blob-following: id6 90 blob/2 YOLO, id19 90 blob/22 YOLO). Blob-bridging resets `time_since_update=0`, so a skeleton-stale track latches onto *ambient* motion (passing dancer / shadow / foliage) and never ages out. **Top-left** because `predict()` clamps only negative edges (tracker.py:415-420): top-left flyers linger at the corner, other headings exit off-screen. So the real discriminator is duration-since-real-skeleton, not blob-presence. **Deferred (operator, 2026-06-13):** any cap tight enough to kill the ghost risks dropping real long-YOLO-gap dancers (aerial = 1-in-3 frame detection, bug #14) — the case-1 drop-regression trap. Field priority drops>ghosts ⇒ leave it. Revisit only with a corpus-measured separating N. | `tracker.py:3140` (gate), `:415` (clamp), `:2932` (timer reset) |
| **3** | Calib1 CLAHE too high (noise); gamma "always 2.2" | **Measured (GPU+TRT) — conclusion: CLAHE needs an empirical Calib1 sweep, not a formula.** Gamma *is* a clamp artifact (`AUTOCAL_GAMMA_BOUNDS=(0.8,2.2)`, pins at 2.2 below ~40/255; minor lever — 1.8 best on dark-crowd). CLAHE is the dominant drop lever and is a **noise-vs-contrast tradeoff**: dark-crowd best at CLAHE **1.0/off** (drop 0.90→0.20), hangar-floor best at **2.5** (CLAHE off → drop 0→0.47). **No simple formula predicts it:** the two scenes are *equally dark* (brightness 4.3 vs 5.1) with opposite needs → brightness fails; hangar-floor is *noisier* (temporal 1.22 vs 0.30) yet wants more CLAHE → noise has the wrong sign. The real signal is high-freq structure (laplacian 5.6 vs 0.6) but it conflates structure/noise. **Same lesson as varThreshold (ROADMAP P2: "empirical, not formula").** Also: the catastrophic 4.9/5.9 CLAHE in live configs is NOT from Calib1 (`seed_clahe` caps at 2.5) nor the macro — it's manual expert-slider / stale configs; re-running Calib1 already lowers them. **Fix = add a CLAHE sweep to Calib1's sweep machinery** (try off/1.5/2.5/4.0, pick by YOLO count×conf in the calib window). | `calibration.py:81-99`, `config.py:467-472` |
| **4** | texturedbg slot4 "lost detection after Calib2"; whitebg2 floor hard drops f1500/2000/2100 | Calib2 over-tightening (imgsz/conf); likely couples to #1/#3. **Needs targeted GPU+TRT replay on the flagged frames** | `calib2.py`, project configs |

## 2. UX findings

- **Profile (show/rehearsal) toggle exists but is invisible.** Wiring present (`adapter.py:181/311`, `on_profile_switch→SwitchProfile`, `ActiveProfile→set_active_profile`); calibration is already profile-scoped (`calibration_flows` passes `_active_profile` to Calib2). **Confirmed in data:** whitebg3's config holds two fully distinct calibrations — `show` (gamma 2.2 / clahe 2.5 / conf 0.25) and `rehearsal` (gamma 0.76 / clahe 4.5 / conf 0.14), `active_profile=show`. So dual calibration works and persists; the gap is purely a visible **day/rehearsal ↔ night/show** toggle + calibration-linkage. Note: the gamma-2.2 pin lands on the **show/night** profile — the production-critical one.
- **Calib2 pool is reachable only by re-running** — `show_calib2_dialog` is the only render; no standalone view/manage path.
- **Calib2 is opaque** — the accumulating/stacking-pool mental model isn't surfaced.

---

## 3. Plan

### Phase 0 — make the harness GPU+TRT-capable (prerequisite)
- **Engines already built** (the full `yolo11{n,s,m,l,x}-pose_{640..1920}.engine` FP16 grid is on disk under gitignored `models/` — the earlier "none built" reading was a gitignore-respecting search artifact). No build needed for the yolo11x slots.
- Add a TRT load hook to `replay.py` behind an explicit `--trt` flag: load `<model>_<imgsz>.engine` + `use_fp16=True` + `use_gpu_path=True`. Keep the existing `--gpu-path` (FP32 `.pt`) intact so the bug-#10 parity test is unchanged; `.pt`/CPU remain the parity tripwire.
- **imgsz trap:** replay defaults imgsz to 1280 (`config.get("yolo_imgsz", 1280)`), but production default is `YOLO_IMGSZ=800` and whitebg3 runs `yolo_imgsz=960` — always pass the slot's real imgsz so the engine matches.

### Fix cases — targeted-slot-first, GPU+TRT
| # | Investigate (one slot, GPU+TRT) | Fix direction |
|---|---|---|
| 1 | whitebg3 slot 2 + testflou slot 6 — watch reported box dims in JSONL | In `update()`, when source is motion: keep the established YOLO box size (from `bbox_area_history`/last skeleton) and move only to the new centroid — do not adopt the blob extent |
| 2 | TOGO-day slot 9 — trace synthetic-only track lifetime in JSONL | Kill / refuse-to-bridge tracks that never had a skeleton sooner; tighten cold-blob promotion |
| 3 | CLAHE+gamma sweep on verydark slot 5 + 2 contrast scenes — score curve | Replace binary CLAHE seed with a measured curve + lower floor; revisit gamma target/clamp so dark scenes aren't all pinned at 2.2. **Confirm with sample testing per operator** |
| 4 | texturedbg slot 4 + whitebg2 floor f1500/2000/2100 | Diagnose which Calib2 knob over-tightens; likely ties to #1/#3 |

### Deferred enhancement (operator, 2026-06-13)
- **Reported/OSC box smoother** — an optional EMA on the reported box w/h to calm the *residual genuine* pose jitter (YOLO's own box varies ~1.4–3.7× frame-to-frame as the dancer changes shape). Best as a **tunable slider** (smoothness vs fidelity/lag, consumer-facing to TouchDesigner). Not a bug — the case-1 fix already brought bridge flips down to the YOLO-jitter floor. Build later.

### UX track (parallel, lower risk)
- **U-a:** standalone Calib2 pool view — inspect/remove runs without re-calibrating.
- **U-b:** surface the show/rehearsal profile toggle, label day/night, show which profile a calibration belongs to.
- **U-c:** make Calib2 legible — explain the accumulating pool, per-run contribution, what Apply changes.
- **U-d:** show calibration seeds (gamma/CLAHE/var/imgsz) in the result card for at-a-glance sanity (would have made the 2.2 pin obvious).

## 4. Verification protocol
- GPU+TRT is the default path for every detection-quality check.
- Targeted slot/situation first; coverage (12-scenario) run only on operator request once a fix looks right.
- CPU `.pt` goldens kept as a byte-parity tripwire only.

## 5. Status / outcomes log
- 2026-06-13: plan drafted from operator feedback; root causes 1 & 3 verified in code; 2 & 4 mechanism located.
- 2026-06-13: Phase 0 done — `replay.py --trt` (committed `07ee581`); GPU+TRT is the validation path.
- **Case 1 — CLOSED (deferred, Option B).** Bug confirmed + quantified on GPU+TRT (white-duo: bridge box-jump worst 5.55×, id1 motion box 1.69× the YOLO box). In-tracker fix (keep box size in `update()`) **regressed drops** because `self.bbox` also feeds the bridge gate + `MAX_VELOCITY`: GPU+TRT before/after sweep flipped **hangar-aerial PASS→FAIL** (drop 0.039→0.098), white-duo/texture-duo drops up (ghosts down). Reverted. The flicker is a *reporting* problem only; the fat blob box is load-bearing internally. **Operator chose B:** fold box-coherence into the deferred reported-box smoother slider (same output boundary), later. Key insight carried to case 2: the fat ghost box is what lets ghosts fly fast.
- **Case 2 — CLOSED (diagnosed, fix deferred).** See §1 row 2. Reproduced on GPU+TRT (full slot 9, live conf 0.4): 4 ghost flyers, blob-bridged (LOCAL_SUPPORT), leftward/top-left, riding ambient motion. First hypothesis (blob-corroboration gate) refuted by JSONL — flyers *are* blob-corroborated. Real lever = duration-since-skeleton, but that risks dropping real aerial long-gap bridges (case-1 trap). Operator: leave it; drops>ghosts. Diagnostic: `replay.py --log-dir` (committed).
- **Case 3 — MEASURED, fix scoped (deferred to a deliberate Calib1 change).** See §1 row 3. GPU+TRT gamma×CLAHE sweeps on dark-crowd + hangar-floor: CLAHE must be **swept empirically in Calib1** (no brightness/noise formula works — equally-dark scenes, opposite needs; measurement refuted both the operator's brightness idea and the existing noise-σ key). Gamma 2.2 clamp = minor secondary lever. The 4.9/5.9 in configs are manual/stale, not Calib1 output. **Quick win available now: re-run Calib1 on affected projects** (flushes stale CLAHE ≤2.5). Artifacts: `tmp_analysis/case3/grid_*.json`.
- **UX:** U-b (day/night profile label+tooltip) + U-c (Calib2 pool explainer) shipped (`1cfc9df`); U-d already satisfied. U-a (standalone pool view) still open.
- Next session: implement the Calib1 CLAHE sweep (case 3) and/or U-a — both deliberate, no blocker.
