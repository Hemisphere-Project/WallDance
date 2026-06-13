# Calibration & Detection Fix Plan

**Date:** 2026-06-13
**Origin:** Operator in-app pass (2026-06-13) over the §4.2 Phase-1 adopted configs surfaced live detection issues that the headless Phase-1 "PASS" verdicts did not predict. Root cause of the mismatch is a **validation-path divergence** (below), plus a handful of genuine code/seed issues. Companion: [ROADMAP.md](ROADMAP.md) §4.2, [CORPUS_ANALYSIS.md](CORPUS_ANALYSIS.md).

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
| **2** | TOGO-day "flying ghost": a YOLO ghost vanishes but a motion box stays and drifts/translates around | Cold-blob synthetic detections fused as tracks; a track that never had a skeleton can be relayed by wandering motion blobs, centroid drifts under inflated R. Candidate Phase-2 interaction (correctors off ⑧ + takeover-merge ②). **Lifecycle not yet pinned — needs targeted GPU+TRT replay** | `tracker.py:~2174` (cold fuse), bridge path |
| **3** | Calib1 CLAHE too high (noise); gamma "always 2.2" | **Confirmed clamp artifacts.** `seed_gamma` clamps to `AUTOCAL_GAMMA_BOUNDS=(0.8, 2.2)`; algebra → any raw mean < ~40/255 pins at 2.2, and IR scenes measure ~5/255. `seed_clahe` is a binary 2.5/1.5 (σ>4) pick — too blunt; even 1.5 likely too high for verydark | `calibration.py:81-99`, `config.py:467-472` |
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

## 5. Status
- 2026-06-13: plan drafted from operator feedback; root causes 1 & 3 verified in code; 2 & 4 mechanism located, lifecycle/knob pending targeted replay. Next: Phase 0 (harness TRT) → case 1.
