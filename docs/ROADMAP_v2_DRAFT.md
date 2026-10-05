# WallDance Roadmap — v2 DRAFT (field-test edition)

**Date:** 2026-10-05 · **Status:** 🟡 **DRAFT for Thomas's review.** It will replace `ROADMAP.md` once the open
decisions ([AUDIT_2026-10.md §6](AUDIT_2026-10.md)) are answered and the doc consolidation (DOC-15 → DOC-5) is
applied. Until then, `ROADMAP.md` stays the canonical file, because code comments anchor to its labels.
**Evidence:** [AUDIT_2026-10.md](AUDIT_2026-10.md) and the stream reports in [`audit-2026-10/`](audit-2026-10/).
**History:** [archives/ENGINEERING_RECORD.md](archives/ENGINEERING_RECORD.md).

**ID prefixes (new work):**
- `OPS`: field operations, deployment
- `TEST`: oracle/gates
- `CONT`: continuity
- `SEAM`: shared tracker seams
- `MRK`: IR markers
- `REQ`: operator requests (TODO-me)
- `PERF`
- `ARCH`
- `DOC`
- `CAL`: calibration engine
- `HW`

**Never reuse** the legacy letters O/X/C/S/G/D/P or `ROADMAP bug #N`. They stay valid in code comments (Appendix A).

---

## 0. North star (unchanged) + field-test context

An operator rigs the camera, aims the IR, presses **one calibration button**, and gets robust detection for the whole show,
with no per-venue knob tuning.

**What changed in 2026-10:**
- WallDance is **in field test** with a non-developer, French-speaking operator on the Windows RTX 5080 laptop.
- The load-bearing output is still **position + rough identity** per dancer.
- The audit shows the next margin is in **continuity** (the tracker/report layer), not detection: drops are manufactured *after* YOLO sees the dancer.
- After that, **IR markers** (ankles + wrists, plus a harness marker if possible) for existence and keep-alive.

## 1. Status snapshot

| Subsystem | State |
|---|---|
| Detection P0–P4, corpus Phase 0–2b, Track P (GPU/TRT-only), known-N (K1/K3/GUI) | ✅ shipped (→ ENGINEERING_RECORD) |
| Operator phase rail ①→⑥, two-pass calibration, OSC contract + smoother, ops monitor + 4 h soak | ✅ shipped |
| Field test | 🟡 since ~2026-07, little written feedback; operator wishlist = `TODO-me` |
| Continuity on long spans | 🔴 aerial: 94.2 % coverage / 25 ids for 1 dancer; floor: 57.5 % (holes to 30 s) |
| Prod deployment safety | 🔴 shipped launcher exe can hard-reset prod; unpinned installs break TRT |
| Docs | 🔴 describe 2026-06-25; no operator manual |

## 2. How work flows

1. **Inputs:**
   - field reports: ISSUE/F8 during playback, `sessions/`, recordings of problem moments, and the config used;
   - `TODO-me`;
   - Thomas.
2. **Gates, from smallest to largest:**
   - unit suite;
   - golden trio (per-engine);
   - **TEST-1 continuity suite** (long spans, C1–C10);
   - 12-scenario sweep on prod.

   Behaviour changes are replay-gated. Tracker core, calibration engine and OSC contract changes need Thomas's go.
3. **Where code lands:**
   - dev boxes **never push** (prod auto-updater);
   - delivery follows decision **D1** (bundle/patch applied on prod → later a release channel, ARCH-19).

---

## 3. NOW — Phase 0: secure the field (this week)

| ID | Item | Owner | Effort | Notes |
|---|---|---|---|---|
| OPS-1 (ARCH-1) | **Retrieve + back up prod** (git bundle, `projects/`, freeze, engine dates, one IDS run's `[Budget]` lines) | Thomas | S | Checklist: AUDIT §3. **Never "Yes" to the launcher prompt meanwhile.** |
| OPS-2 | Reconcile prod code with `a49d0f2`; re-validate audit file:line findings | Claude | S | after OPS-1 |
| OPS-3 (ARCH-3) | App survives the launcher closing: `run.bat` → log file; BrokenPipe-safe stdout | Claude | S | Windows smoke |
| OPS-4 (ARCH-4/5, PERF-0) | **Freeze deps from prod's stack**: committed lock, `uv sync --frozen`, cu130 torch source, explicit IDS extra, `YOLO_AUTOINSTALL=0`; engines version-stamped; a mismatch is a loud rebuild prompt, not a silent PyTorch fallback | Claude | M | D3; test on a spare Windows box first |
| OPS-5 (ARCH-2 + ARCH-19) | Launcher rebuilt from source: backup ref before any reset, real "Discard / Keep local" buttons, refuse non-main, pinned build, sha in title; **track a release channel, not `main`** | Claude + Thomas (build on Windows) | M | D1 |
| OPS-6 (ARCH-7) | Show-mode on Windows: block sleep, readiness rows (AC, power plan, pending reboot / Windows Update, USB selective suspend) + operator checklist (= REQ-6) | Claude | S | |
| MRK-0 | Recorder `.meta` (exposure/gain/AE/AG/ROI/version), drain encoder tail on stop, log queue drops | Claude | S | before the marker shoot if it can reach prod; else paper take sheet |
| — | **Marker shoot brief** to the operator (FR) | Thomas | — | AUDIT §4 |

## 4. NOW — Phase 1: measure continuity (prerequisite for tracker work)

| ID | Item | Effort | Gate |
|---|---|---|---|
| TEST-1 | **Continuity suite:** C1–C10 metrics in `scoring.py` (coverage, gaps/min, gap distribution, ids/dancer, re-association, duplicates vs ghosts, spatial validity, acquisition latency, freeze share); full-length scenario manifests (`hangar-aerial-full`, `hangar-floor-full` + laptop long spans); per-frame emitted-id goldens; engine/TRT/ultralytics stamp on goldens; promote the 0.10-floor detect cache harness (`tmp_analysis/audit-2026-10/continuity/drive.py`) | S–M | n/a (tooling) |
| CONT-1 | Bugs: BUG-1 (resurrect restores warm-up only for previously emitted tracks), BUG-2 (`tsu` ≥ 0), BUG-3 (replay applies `tracker_max_age` after `tracking_mode`), BUG-4 (known-N/intermittent doc claim) | S | goldens byte-identical where claimed |
| CONT-10 | Diagnostic field logs: emitted id set + per-track source/warm-up/hide reason in `FRAME_SUMMARY`; per-show timestamped log folders + rotation | S | none |
| PERF-11 | `[Budget]` reports `dpg_render`; JSONL flush off the main thread; `perf_counter` timers | XS | none |

## 5. NEXT — Phase 2: field quick wins (low risk, calibration-level)

| ID | Item | Gain | Gate |
|---|---|---|---|
| CONT-2 + CONT-9 | Exclusion cells **proposed by calibration** (recurring fixed detections), operator accepts; τ allowed down to 0.30–0.35 with exclusion; known-N searches `tracker_intermittent_confirm` + frozen-gate skeleton age; fix the hangar pins (CFG-1 gamma 0.53, CFG-2 empty exclusion) | aerial 94.2 → 99.5 %, gaps 94 → 8 | TEST-1 + no textured-scene ghost-rate increase |
| PERF-1 | `cv2.setNumThreads(4)` after the ultralytics import | −6…−7 ms/frame | bit-identical |
| PERF-3 / PERF-5 / PERF-6 / PERF-8 | Ultralytics overhead bypass · event-driven frame pickup · mono FFV1 recording · GPU preview resize | −5…−10 ms, −2/3 PCIe D2H, −58 % encoder CPU | byte-equal checks |
| ARCH-6 / ARCH-8 / ARCH-9 | Crash hygiene (finally, guarded OSC, per-tick boundary, faulthandler, logging, restart → STANDBY) · atomic config writes · real CI (+ Windows job) | show-loss classes closed | new main-loop tick test |
| REQ-1 | **Load external videos into slots** (TODO-me +++): import button (copy/rename into `recordings/` + `.meta`) | operator test loop | app smoke |
| REQ-5 | Mirror horizontal / rotate 90° (camera + playback) | | app smoke + transforms tests |

## 6. NEXT — Phase 3: the continuity change set (one replay-gated set)

| ID | Item | Notes |
|---|---|---|
| SEAM-1 (CONT-3 seam ≡ MRK interface ≡ ARCH-11) | Typed measurement (`source`, `r_mult`, credits, `resets_skeleton_age`) + per-track evidence ledger; **pure refactor first** (goldens byte-identical) | designed once for continuity *and* markers |
| CONT-3 | YOLO-first tiered assignment; blob suppressed inside YOLO boxes; never drop a YOLO det for a blob-fed track | = REQ-3 "merging tracks (yolo AND motion)" |
| CONT-5 | Bound tentative-track search radius/covariance | stops ghost hijacks |
| CONT-4 | Continuation ≠ birth confirmation; frozen gate on time-since-strong-evidence + displacement; then hold-style coasting | **unsafe without ghost control** (ships with CONT-2/3/5) |
| SEAM-2 (ARCH-12) | Extract `core/output_stage.py` (finalize → identity → smoothing → OSC) | home of CONT-6 |
| CONT-6 | **Identity-slot layer** (known/max N, stable ids 1..N, explicit coasting → lost); optional `/walldance/dancer/state` (opt-in, OSC_CONTRACT §D) | D4, D5; aerial sim 25 → 1 id |
| CONT-11 | Re-measure the track-local bridge after CONT-3/4; retire or narrow | ids −56 % on slot 4 |
| ARCH-10 | Single config applier shared by app and replay + parity test | removes the BUG-3 class |

Gate for the set: TEST-1 improves on full slots 3/4. Guards: tango-H s8 / H2 s9 (textured) locally, and the 12 scenarios on prod. Duplicates must not increase anywhere.

## 7. NEXT — Phase 4: IR markers (ankles + wrists, + harness)

| ID | Item | When |
|---|---|---|
| MRK-1/2 | Eval harness (`marker_eval.py`: floor / assoc / centroid / occlusion / sweep + contact sheets) + synthetic marker injection + offset-vote estimator / R(n,k) | **before footage** |
| MRK-P0a | Run on the ankle/wrist footage → **go/no-go** (M1–M9, [02-ir-markers §6.3](audit-2026-10/02-ir-markers.md)) | on arrival |
| MRK-3/4 | `core/marker_detector.py` (pure, mono8; after PERF-1) + glint map + `T_marker` auto-calibration in Aim + Calib2 validation | skeleton before footage, tune after |
| MRK-5 | Tier A: blob↔keypoint {9,10,15,16}, MARKER keep-reason at crossval, confirm + learn offsets (keypoint refinement behind its own flag) | after go |
| MRK-6 | Tier B: gap relay at moderate R, precedence over the motion bridge, frozen gate uses min(skeleton, marker) age | after go |
| MRK-7 | Tier C: marker-only static acquisition (through the identity slots); optional "markers-required" mode | after go |
| MRK-8/9 | OSC extras (opt-in) · preview overlay + readiness row ("markers seen 4/4") | after go |

## 8. NEXT — Phase 5: operator experience

| ID | Item | Notes |
|---|---|---|
| REQ-2 / DOC-2 | **`docs/MODE_EMPLOI.md`** (French, English UI labels quoted, glossary, printable checklists, PDF on the laptop) | D10; outline in [05-docs §6](audit-2026-10/05-docs.md) |
| REQ-4 / CONT-8 | Motion-first acquisition lane for small far figures, **fixed background only**, inside the N budget | needs IR small-far footage |
| REQ-7 | "cleanup menu" | D15 |
| DOC-* | Consolidation: ER first (DOC-15) → CLAUDE.md (DOC-20) → ROADMAP (this file → DOC-5) + dissolve TODO.md → ARCHITECTURE + TESTING → OSC_CONTRACT fix → IR_MARKERS rename → README rewrite → code-comment path fixes (DOC-17, including the "clean plate" UI strings) | [05-docs §5](audit-2026-10/05-docs.md) |
| — | Small fixes: slot 10 vs 9 slots; `/walldance/clear` on STANDBY / tracker reset + a UI binding; phase-rail chips; `fps_table.json` on Windows | |

## 9. LATER

- **Performance (when headroom matters):**
  - PERF-2: motion-feed signal changes / GPU motion-lite;
  - PERF-4: batched CLAHE;
  - PERF-9: KF/α-β centroid (−50 ms, D6);
  - PERF-10: Python 3.12;
  - PERF-13: INT8/FP8, only once GPU-bound.
- **CAL — unified calibration engine** (ex C-next). Also on-rig validation of known-N / the K3 dark-probe.
- **Research-gated (go/no-go before code):**
  - corpus-trained IR person detector;
  - Track D SNR leads (motion-gated denoise, native bit depth: note that the live path is Mono8 today, so the recorder must follow);
  - optical-flow coherence;
  - clean-plate static path.
- **Output:** X-4 steady-rate OSC resampling · OSC status/heartbeat · P-5 bundling (consumer-gated).
- **Packaging/UI:** check-for-updates is done in the launcher; rotate playback = REQ-5.

## 10. Simplification backlog (opportunistic, gated)

- **ARCH-14:** Track-P leftovers (CPU-mode messages in installers/launcher/GUI), ~45 dead functions, 2 dead constants; decide whether `bg_subtract` and auto-exclusion stay.
- **ARCH-16:** jobs single-flight; tools out of `tests/` into `application/tools/`.
- **Dedupe:** 3 xywh-IoU and 3 weighted-centroid implementations.
- **ARCH-15:** split `tracker.py` along its phases, **only after TEST-1 id goldens + SEAM-1**.
- **Removed from the old backlog:**
  - "Remove `TrackingMode`": the enum is live in 4/12 projects (D14).
  - "`tracker_smoothing` has no config key": it is saved and used.

## 11. Hardware & procurement

| ID | Item | State |
|---|---|---|
| HW-1 (ex P1.3) | More IR illuminators for even coverage → enables ≤ 25 ms exposure (D7) | open |
| HW-2 | IP66 camera housing · mounting/rigging | open (ask: bought?) |
| HW-3 | Retroreflective markers: 4 cuffs + 1 harness marker per dancer; check IR-only ("glint") material if the projector axis makes silver tape visible | after Phase 0a |

## 12. Open decisions

See [AUDIT_2026-10.md §6](AUDIT_2026-10.md) (D1–D17). The blocking ones:
- **D1:** delivery channel.
- **D3:** dependency baseline.
- **D4:** known N.
- **D5:** TouchDesigner behaviour.

## Appendix A — legacy labels → homes

Carried over from [05-docs §4](audit-2026-10/05-docs.md). Summary:

| Label in code comments | Home |
|---|---|
| `ROADMAP bug #N` (fixed) | archives/ENGINEERING_RECORD §8 |
| `ROADMAP §4.2 Phase 2 ①–⑧`, `2b` | ENGINEERING_RECORD §5 |
| `ROADMAP §3a/§3b` | ENGINEERING_RECORD §3 |
| `ROADMAP P0/P1.4/P2/P3/P4`, `§7B` | ENGINEERING_RECORD §4, §7.B |
| `P-1`, `P-2`, `P-6` | ENGINEERING_RECORD §9 (P-1/P-2 to be added, DOC-15) |
| `K1`/`K3`, `ROADMAP §3.2` | ENGINEERING_RECORD §12, to be created (DOC-15) |
| `ROADMAP §6` (launcher safety) | ENGINEERING_RECORD §13, to be created (DOC-15); re-point `launcher/git_manager.py:27` |
| `Track O/X/C/S/G/D/P`, `OPERATOR_V2 §…`, `decision 5` | archives/OPERATOR_V2.md (+ TRACK_X_SMOOTHER for X §) |
| `OSC_CONTRACT §B/§B.1/§B.2` | docs/OSC_CONTRACT.md (numbering kept) |
| `DECOMPOSITION_PLAN …`, `UX_PLAN U2–U5`, `KNOBS E1/E2`, `TUNING Phase A–F`, `CORPUS_ANALYSIS §…` | their files (archives or reference), headings frozen |
