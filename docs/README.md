# WallDance docs — index

The front door to `docs/`. WallDance is a Python computer-vision system that tracks dancers under
IR for live shows and emits their positions over OSC. Project root code lives in
[`../application/src/`](../application/src/); the user-facing overview is the
[repo README](../README.md).

**One roadmap, one record:** forward work lives in **[ROADMAP.md](ROADMAP.md)**; the detailed
shipped history lives in **[archives/ENGINEERING_RECORD.md](archives/ENGINEERING_RECORD.md)**.
Everything else is reference, operator procedure, or archived design.

---

## Start here

| If you want to… | Read |
|------------------|------|
| Know what's next / the plan | **[ROADMAP.md](ROADMAP.md)** — single source of truth (Now / Next / Later / Simplification / Hardware) |
| Run a real show | **[NEW_SHOW.md](NEW_SHOW.md)** — operator field playbook (the ①→⑥ phase rail) |
| Understand the OSC output | **[OSC_CONTRACT.md](OSC_CONTRACT.md)** — wire-level `/walldance/*` contract |
| See the big bet | **[TRACKING_ROBUSTNESS.md](TRACKING_ROBUSTNESS.md)** — IR retroreflective markers (the next leap) |

---

## Live docs

| Doc | Role | Status |
|-----|------|--------|
| [ROADMAP.md](ROADMAP.md) | **The roadmap** — forward plan + condensed shipped index | 🟢 Live (2026-06-22) |
| [TODO.md](TODO.md) | Build / hardware checklist (phase inventory + procurement) | 🟢 Live |
| [TRACKING_ROBUSTNESS.md](TRACKING_ROBUSTNESS.md) | IR-marker direction; gated on a physical spike (Phase 0a) | 🟢 Live, not built |
| [OSC_CONTRACT.md](OSC_CONTRACT.md) | Canonical `/walldance/*` output contract (box-clamp, L-driven stream, latency) | 🟢 Live |
| [NEW_SHOW.md](NEW_SHOW.md) | Operator field playbook from the spine | 🟢 Live |
| [CHECK_TEST.md](CHECK_TEST.md) | Pre-show + recorded-case test procedure | 🟢 Live |

## Reference

| Doc | Role | Status |
|-----|------|--------|
| [CORPUS_ANALYSIS.md](CORPUS_ANALYSIS.md) | Measured scene physics + the re-founded regression corpus (12 manifests) | 📘 Reference |
| [TUNING.md](TUNING.md) | The replay / tune / scoring toolchain (Phases A–F) | 📘 Reference |
| [OPTICS.md](OPTICS.md) | Camera + lens working envelopes (distance / dancer-px) | 📘 Reference |
| [AUTOTUNE_DESIGN.md](AUTOTUNE_DESIGN.md) | Knob-determinability analysis (design rationale; its §5 gaps have all since shipped — see ROADMAP §3.2) | 📘 Reference, largely historical |
| [GUI_STACK_AUDIT.md](GUI_STACK_AUDIT.md) | Stay-Python / keep-DearPyGui decision (no PySide6) | 📘 Decision record |

## Historical / archived (`archives/`)

Superseded design + investigation docs. Kept because code comments anchor to their labels and for
provenance. **Do not plan new work from these.**

| Doc | Was | Superseded by |
|-----|-----|---------------|
| [archives/ENGINEERING_RECORD.md](archives/ENGINEERING_RECORD.md) | Full shipped-detection record (P0–P4, corpus phases, bugs #1–14, tracker lessons, env findings) | the index in ROADMAP §6 |
| [archives/OPERATOR_V2.md](archives/OPERATOR_V2.md) | The operator/calibration/output forward plan (Tracks O/X/C/S/G/D/P) | folded into ROADMAP §3 |
| [archives/UX_PLAN.md](archives/UX_PLAN.md) | Shipped section-panel UX + two-pass calibration rationale (U0–U5) | ROADMAP / OPERATOR_V2 (phase rail) |
| [archives/DECOMPOSITION_PLAN.md](archives/DECOMPOSITION_PLAN.md) | `app.py` → core/runtime/ui/camera/services decomposition (Phases 0–4 done) | done; shim deletion → ROADMAP §4 |
| [archives/KNOBS.md](archives/KNOBS.md) | Single-clip-era knob-sensitivity evidence (TUNING Phase E) | Track S governance (ROADMAP) |
| [archives/TRACK_X_SMOOTHER.md](archives/TRACK_X_SMOOTHER.md) | Fixed-lag/RTS output-smoother design (core shipped; dual-tap/case-2 removed) | OSC_CONTRACT §B |
| [archives/CALIB_DETECTION_FIX_PLAN.md](archives/CALIB_DETECTION_FIX_PLAN.md) | Detection case 1/2/3/4 study record (all closed) | OPERATOR_V2 → ROADMAP |
| [archives/AUDIT.md](archives/AUDIT.md) | Full maintainability audit (2026-06-08) | ROADMAP §6 / ENGINEERING_RECORD |
| [archives/ROBUSTNESS_PLAN.md](archives/ROBUSTNESS_PLAN.md) | Original detection north star | ROADMAP / ENGINEERING_RECORD |
| [archives/TRACKING_PLAN.md](archives/TRACKING_PLAN.md) | Full tracker decision log + lessons | ENGINEERING_RECORD §6 |
| [archives/P3_FUSION_SIMPLIFICATION.md](archives/P3_FUSION_SIMPLIFICATION.md) | Full P3 motion-fusion design | ENGINEERING_RECORD §4 (P3) |
| [archives/HARDWARE_GUIDE.md](archives/HARDWARE_GUIDE.md) · [archives/SPECIFICATIONS.md](archives/SPECIFICATIONS.md) | Hardware guide · old technical specs | OPTICS.md / current code |
| [archives/IDS_CAMERA_STALL_INVESTIGATION.md](archives/IDS_CAMERA_STALL_INVESTIGATION.md) · [archives/IDS_STALL_CONCLUSIONS.md](archives/IDS_STALL_CONCLUSIONS.md) | IDS USB3 stall investigation + conclusions | resolved in `ids_camera.py` |
| [archives/audit_report.md](archives/audit_report.md) · [archives/tiling_plan.md](archives/tiling_plan.md) · [archives/LEGACY_pre-project-proposal_fr.md](archives/LEGACY_pre-project-proposal_fr.md) | Older audit · rejected tiling plan · French project proposal | — |
