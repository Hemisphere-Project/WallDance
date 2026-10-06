# WallDance Roadmap — v2 (field-test edition)

**Date:** 2026-10-06 · **Status:** 🟢 live (replaces the June roadmap, now
[archives/ROADMAP_2026-06.md](archives/ROADMAP_2026-06.md); the v2 draft of 2026-10-05 is in
[archives/ROADMAP_v2_DRAFT_2026-10-05.md](archives/ROADMAP_v2_DRAFT_2026-10-05.md)).
**Evidence:** [AUDIT_2026-10.md](AUDIT_2026-10.md) (+ [audit-2026-10/](audit-2026-10/)) and the measured review
[ROADMAP_v2_REVIEW.md](ROADMAP_v2_REVIEW.md). **This week's detailed plan:** [PLAN_25M_2026-10.md](PLAN_25M_2026-10.md).
**History:** [archives/ENGINEERING_RECORD.md](archives/ENGINEERING_RECORD.md).
**Code-comment anchors** (`ROADMAP §4.2`, `ROADMAP bug #N`, `P3`, `§7B`…) refer to the June file: see Appendix A.

ID prefixes for new work: `OPS` field ops/deployment · `TEST` oracles/gates · `CONT` continuity · `SEAM` tracker
seams · `MRK` IR markers/belt · `REQ` operator requests (TODO-me) · `PERF` · `ARCH` · `DOC` · `CAL` calibration ·
`HW`. Never reuse the legacy letters O/X/C/S/G/D/P or `ROADMAP bug #N`.

---

## 0. North star (restated 2026-10-06)

**One point per dancer on the wall, continuous, stable and smooth, delivered to TouchDesigner.** N points for
N dancers (N capped per show, dancers enter and leave). TouchDesigner drops its video when a point vanishes, so
presence is the product.

Priority order (Thomas, 2026-10-06):
1. **The 25 m hangar setup with the IR we have** (2 × PIR130, 850 nm, 60°): a stable, robust two-point stream the
   client can see this week. Measured by the demo KPI in [PLAN_25M_2026-10.md](PLAN_25M_2026-10.md).
2. **Scaling:** 30–40 m, a bigger scene, smaller dancers, less light, portrait camera.
3. **Later:** id consistency across crossings (slot ids may swap on a crossing today — acceptable), full-skeleton
   quality, extremity markers.

Not a priority now: id swaps, skeleton fidelity, everything that does not change the two points TD sees.

The operator still rigs the camera, aims the IR, presses one calibration button and gets a robust stream for the
whole show, with no per-venue knob tuning.

## 1. Status snapshot (2026-10-06)

| Subsystem | State |
|---|---|
| Detection P0–P4, corpus, Track P (GPU/TRT-only), known-N, phase rail ①→⑥, two-pass calibration, OSC contract + smoother, 4 h soak | ✅ shipped June (→ ENGINEERING_RECORD) |
| **Identity slots (CONT-6)**: stable ids 1..N, 2 s coasting, One-Euro, belt hook, `/dancer/state` opt-in (OSC_CONTRACT §D) | ✅ on `remote-ops`, measured: aerial take 94.5 % / 15 ids → **99.7 % / 1 id**; floor take 57.7 → 75.6 % with a 24 s hole left; duos 24–31 % coasting; textured wall 52 % coasting |
| CONT-1 tracker bugs (BUG-1..4) + occluded warm-up guard, CONT-10 diagnostic logs (per-show folders, emitted set + hide reason), TEST-1 continuity metrics C1–C10 + long-span manifests, `--quality` for field takes | ✅ `remote-ops` |
| Also shipped 2026-10-06 (`remote-ops`): REQ-1 import external videos into slots (9 slots everywhere) · REQ-5 mirror / rotate (camera, playback, producer threads; offline tools apply it) · ARCH-3 app log + BrokenPipe-safe stdout · ARCH-6 live-path crash hygiene + crash marker (→ STANDBY) · ARCH-8 atomic config history · ARCH-9 CI that can pass + Windows job · ARCH-2/19 launcher release channel, backup refs, labelled buttons · readiness disk estimate by codec · remote `SetRoiRect` / `ExcludeAt` · `WD_ENGINE_DIR` per-box engines · PERF-11 `[Budget]` keys (`mog2_wait`, GUI tail) · laptop `fps_table.json` measured | ✅ `remote-ops` (Windows smoke of each pending on the DEV slot) |
| Perf pass: cv2 threads, PoseRunner (−2.8 ms/frame here), bit-exact batched CLAHE, motion feed first, preview cap | ✅ `remote-ops`; laptop GPU ran 84–87 °C in the balanced plan |
| IR belt detector (gated) + `belt_eval` IR budget; MRK-0 provenance (`.meta` v2, camlog, rig sheet); MRK-1/2 marker harness | ✅ built; **belt unproven at show distance** (below the 60 DN floor on 2026-10-05) |
| Remote ops: `wdremote` over the tailnet, in-app loopback API, DEV slot, release-channel launcher **installed on the laptop** | ✅; `release` not yet on GitHub |
| D3 pin: installers' pinned mode + `requirements-prod.txt` frozen from the laptop | ✅ branch `pinned-install`, unpushed |
| Field test | 🟡 operator sessions at the hangar this week (25 m, 2 dancers, 2 × PIR130) |
| Docs | 🔴 no operator manual; this file + the review are current, README/NEW_SHOW describe June |

## 2. How work flows

- **Inputs:** field takes (slot recordings with `.meta` v2 + rig sheet), session logs (CONT-10 folders), ISSUE/F8
  reports, `TODO-me`, Thomas.
- **Gates, smallest first:** unit suite → golden trio (per engine) → **TEST-1** long-span continuity (C1–C10) →
  **TEST-2** emitted-stream golden + KPI floor (to build, PLAN A7) → 12-scenario sweep on the laptop → **the demo
  KPI** on the latest 25 m take. Behaviour changes are replay-gated; tracker core, calibration engine and OSC
  contract changes need Thomas's go.
- **Where code lands:** dev boxes never push (the laptop's launcher tracks `release`); delivery = Thomas pushes
  `release` in two steps (code first, the D3 pin second, supervised) after the gates in REVIEW §5.
- **Where knowledge lands:** the 37brain inbox via `/brain-out`, never direct.

---

## 3. NOW — the 25 m demo (this week)

| ID | Item | Owner | Gate / output |
|---|---|---|---|
| **N-1** | Tonight's takes (`mur25m-ceinture-0610`, protocol in TOURNAGE_2026-10-06_FR) → tomorrow's laptop window (PLAN §B): provenance, belt + IR budget on real belts (= the marker **Phase 0a go/no-go**, M1–M9 via `marker_eval` / `belt_eval`), the KPI on slots 9/4/5/6, perf with TD | operator → Claude + Thomas | belt ON/OFF (D19), IR plan, KPI baseline |
| **N-2** | Release gates on the DEV slot (pytest, TRT replays, PoseRunner A/B byte-equal, GUI smoke, `release-check`) → **push 1** (`remote-ops` → `release`, no reinstall) | Claude → Thomas | the operator runs LIVE with stable ids |
| **N-3** | Settings for the day from measurements, not hands: classify the Bordeaux takes and ground-truth the usable ones (PLAN A1 — they are mixed tests: off-axis light, blur, operator near the lens, unknown N), second-point loss attribution (A2), knob-level deltas with the detect cache (A3), known-N dry run (A4) → applied remotely in STANDBY | Claude | two-point presence ≥ 95 %, coasting ≤ 10 % on the show-like take |
| **N-4** | Output quick wins, output-only, replay-gated: raw-centroid input to the One-Euro (D18, PLAN A5), belt static glint map (A6) or belt OFF, skeleton age in `/dancer/state` | Claude, on Thomas's go | `slot_sweep.py` tables: binding unchanged, lag −30…−60 ms; no false belt holds |
| **N-5** | On-site settings and hygiene (TERRAIN_2026-10-07_FR): tight ROI, exclusion painting, Performance mode + raised chassis, Max dancers 2, Hold 2 s, Stability 0.5; remote `SetRoiRect` / `ExcludeAt` | operator + Claude | GPU < 83 °C, no ghost slots |
| **N-6** | **TEST-2** emitted-stream golden + KPI floor from committed timelines (PLAN A7) | Claude | the slot layer gated in seconds, no GPU |
| **N-7** | **Push 2** (`pinned-install`, D3) in a supervised window | Thomas | engines load, suite green on the laptop |
| **N-8** | Operator note v2 (FR) with the day's settings and the preview colours as the operator's KPI | Claude | PDF on the laptop desktop |
| **N-9** | **Clean-plate foreground evidence** (BRAINSTORM §3.2, PLAN §C.10): run the foreground N-lock on tonight's takes with the empty wall as the plate; if it beats the slot stream there, build the output-side hook (ghost veto, held-slot measurement, known-N re-acquisition) + plate capture at Calibrate, behind a key (D26) | Claude, on Thomas's go | on-dancer / held share / longest hole vs the shipped stream on slots 4/5/6/9 + a by-eye audit |

Done when the client has seen the two-point stream hold through losses on the wall, and the KPI table from the
latest take is in this file.

**Progress 2026-10-06 evening (offline, PLAN §C):** N-3 analysis done (the Bordeaux takes are single-walker tests,
no KPI from them; the demo proxy is the April white-wall duo: two points 0.909, longest hole 5.2 s, on-dancer 0.74
with the shipped layer); N-4 shipped behind keys (static-ghost guard + yield ON, `raw_skeleton` filter input OFF
for D18, belt static map ON); N-6 TEST-2 in the suite; candidate settings `yolo_first` + tau 0.15 + intermittent
confirm (white duo: two points 0.973, hole 1.26 s, on-dancer 0.93) **only with the venue's ghost spots excluded**
(they otherwise fill the spare slot when a dancer is off the wall): decided tomorrow on tonight's takes. N-8 done
(TERRAIN_2026-10-07_FR with the good-take conditions).

## 4. NEXT — scaling (30–40 m, bigger scene, less light, portrait)

Only after §3 is demonstrated. Order and evidence in REVIEW §3–§4.

| ID | Item | Evidence / gate |
|---|---|---|
| **P-1** | Venue numbers → optics + IR: lens (8 mm = 117 px at 40 m; 12 mm quote if the wall is ≤ 21 m wide, D20), tight ROI or imgsz 1536, projector count × beam (30° before more units) × offset ≤ 10 cm from the lens, exposure ≤ 25 ms only with the IR multiplied (D7) | `venue_fit.py`; the IR budget from N-1 |
| **P-2** | **CONT-2 + CONT-9**: τ down to 0.30–0.35 with exclusion, calibration proposes exclusion cells, known-N searches `tracker_intermittent_confirm` + `tracker_ghost_skeleton_age`, re-pin the hangar manifests (CFG-1/2); textured-wall guard (tango-H2 s9) | audit s4_combo 94.2 → 99.5 %; tango-H2 52 % coasting with slots |
| **P-3** | **CONT-3**: YOLO-first tiered assignment, blob suppression inside YOLO boxes, never discard a YOLO det for a blob-fed track — one replay-gated tracker change set | every off-body "live" point is a blob-fed drift (REVIEW §1.3) |
| **P-4** | **CONT-4a**: continuation ≠ birth (an emitted track stays emitted while its last skeleton is ≤ ~1 s old) + `intermittent_confirm` per scene | the floor take still holes 24 s with slots; warm-up is the top uncovered cause (REVIEW §1.2) |
| **P-5** | **MRK-7-lite**: belt-only hold/acquisition of a still dancer through the slot layer; MRK-9 "belt seen" readiness row | gated on the belt being visible at show distance (N-1) |
| **P-6** | **ARCH-5** engine stamps + loud fallback; emitted-stream pass lines in the long-span manifests | TRT goldens are per-engine; silent PyTorch fallback is a 3–7× regression |
| **P-7** | **ARCH-7** readiness rows (power plan, AC, GPU temperature, pending reboot) · **DOC-2 MODE_EMPLOI** (FR, seeded from the two session notes) · DOC-14 README/index refresh | 87 °C observed; "easy on-site settings" is a goal |
| **P-8** | Perf with TD: model/imgsz by accuracy at the production distance (x@960 / l@1280 return ~9 ms of GPU per frame); tracker bridge CC queries + cost matrix only if `track` p95 > 10 ms at 2 dancers; **PERF-6** mono FFV1 recording; **PERF-2** motion feed (blur after downscale, integer-ratio resize, gate the Welford σ; GPU motion-lite later — re-baseline) and **PERF-7** IDS mono path (no GRAY2BGR round trips) if the motion worker is still the critical path on the laptop | laptop `[Budget]` with TD (N-1 B5); dev37: motion worker 25 ms vs YOLO 14 ms at 1 dancer, tracker 15/26 ms at 2 dancers |
| **P-9** | Rectangular TRT engine for a tall, narrow portrait ROI | only if the venue ROI exceeds imgsz on one side |

## 5. LATER — id consistency, skeleton, markers, structure

- **Id consistency / swaps:** CONT-5 (bound tentative tracks), CONT-11 (retire the bridge), swap gates per D9,
  MRK-10 coded harness pattern (only if duo takes show marker-attributable swaps) — swaps are acceptable per the
  field constraints; measured by `id_switches`.
- **Calibration engine (CAL, ex C-next):** one calibration engine behind Aim / Calib / known-N; on-rig validation
  of known-N and the K3 dark probe; exclusion proposals (CONT-9) and the belt static map (MRK-4) plug into it.
- **Operator requests still open:** REQ-7 "cleanup menu" (D15: which menu?), REQ-4 small far figures (= CONT-8).
- **Small fixes:** `/walldance/clear` on STANDBY / tracker reset + a `ResetTracker` UI binding · phase-rail status
  chips never filled · "Aim captures the clean plate" UI text (D16: fix the text) · `fps_table.json` written by
  `build_engines.bat` (the laptop table exists, 2026-10-06) · ARCH-17 single-key shortcuts while an input has focus.
- **Docs consolidation** (05-docs §5.2, after MODE_EMPLOI): DOC-15 give finished work a home in ENGINEERING_RECORD
  (§12 June 22→25, §13 launcher safety, K1/K3) → DOC-20 repo `CLAUDE.md` → DOC-9 dissolve TODO.md → DOC-6/7
  ARCHITECTURE + TESTING → DOC-8 OSC_CONTRACT fixes → DOC-10 TRACKING_ROBUSTNESS → IR_MARKERS (belt) → DOC-17 code
  comment paths → DOC-1 README rewrite.
- **Full skeleton:** keypoint refinement, extremity markers and their Kalman fusion (MRK-5/6), SEAM-1 typed
  measurement (only if CONT-3 or marker fusion proceed).
- **Structure:** SEAM-2 `core/output_stage.py`, ARCH-10 one config applier, ARCH-14 Track-P leftovers + dead code,
  ARCH-15 tracker split (after TEST-2), ARCH-16 jobs single-flight / tools out of `tests/`.
- **Perf:** PERF-5 event-driven pickup, PERF-8 GPU preview resize, PERF-10 Python 3.12, PERF-12 pipelining,
  PERF-13 INT8/FP8 (only once GPU-bound).
- **Output:** X-4 steady-rate OSC resampling, OSC status/heartbeat, `/dancer/state` extensions (skeleton age /
  `weak`), P-5 bundling (consumer-gated).
- **Acquisition:** CONT-8 motion-first lane for small far figures (only beyond ~45 m with the 8 mm).
- **Ops:** REM-5 in-app job runner for 4G without SSH; remote low-bitrate preview stream.
- **Research-gated:** corpus-trained IR person detector, Track D SNR leads, optical-flow coherence.
- **From the brainstorm (BRAINSTORM_APPROACHES_2026-10):** rope-pendulum prediction for held points (needs the
  anchors per show); lighter YOLO once the foreground carries presence (l@1280 / x@960, -35 to -65 % GPU); the
  belt as a primary sensor if the on-axis belt saturates; replacing the tracker's blob-as-detection path by the
  foreground (a CONT-3 / CONT-11 simplification).

## 6. Simplification backlog (opportunistic, gated)

ARCH-14 Track-P leftovers (CPU-mode messages), ~45 dead functions, 2 dead constants; decide `bg_subtract` and
auto-exclusion · ARCH-16 jobs single-flight, tools out of `tests/` · dedupe the 3 xywh-IoU and 3 weighted-centroid
copies · ARCH-15 split `tracker.py` only after TEST-2 + SEAM-1 · keep `TrackingMode` (live in 4/12 projects, D14) ·
`tracker_smoothing` is a real key.

## 7. Hardware & procurement

| ID | Item | State |
|---|---|---|
| HW-1 | **IR:** projector ≤ 10 cm from the lens axis (retro return falls ~30× between 0.3° and 1.5°); narrower beams (30° ≈ 3.9× on-axis vs 60°) before more units; count from the IR budget (belt ×3.6 at 25 m / ×9 at 40 m, body ×2–6 at 40 m) | decide after N-1 (D19) |
| HW-2 | IP66 housing · rigging | open (bought?) |
| HW-3 | **Belts:** retroreflective, front + back, material grade (glass-bead vs prismatic) — ankle/wrist cuffs dropped from the plan | measure tonight |
| HW-4 | 12 mm lens quote (production only, D20) | conditional |

## 8. Decisions

**Answered:** D1 release branch · D3 pin prod's stack · D4 N is a cap, dancers enter/leave · D5 TD drops its video
when an id vanishes → stable ids + coasting · **D25 (2026-10-06 evening): continuous N-point presence at the
current 25 m setup is the priority; id consistency/swaps and the full skeleton come after scaling.**

**Open (recommendations in REVIEW §7):**

| # | Decision | Recommendation |
|---|---|---|
| D18 | Raw Kalman centroid as the One-Euro input (lag −30…−60 ms, jitter ×1.6)? | build behind a key, judge by eye on TD |
| D19 | Belt-grade IR or opportunistic belt? | after tonight's offset and ladder takes |
| D20 | Production lens 8 mm vs 12 mm | needs the venue's wall width and camera spot |
| D21 | Two-step release | yes, the second supervised |
| D22 | `/dancer/state` ON for the TD patch | yes if the patch uses it (fade while coasting) |
| D23 | Textured venues: operator paints exclusion now; calibration proposes before production | both |
| D24 | `Stable IDs` ON on LIVE at push 1 | yes, checkbox as the fallback, TD patch checked |
| D26 | Build the clean-plate foreground hook before the demo (1.5-2.5 d) or right after? | before, only if tonight's takes confirm the April numbers; it also fixes the spare-slot ghost |
| D27 | Demo settings: `yolo_first` + tau 0.15 + intermittent confirm + exclusion from the empty wall, vs the project defaults | decide on tonight's takes (PLAN B3/B6) |
| D6 | Latency budget | measured ~140–170 ms effective centroid on fast moves today; D18 is the lever |
| D7 | Exposure ≤ 25 ms | only with the IR multiplied (P-1) |
| D8 | Costume | belt front + back; cuffs dropped |
| D9 | Duos / crossings | yes; swaps acceptable, measured |
| D10 | Manual | French, English UI labels quoted (P-7) |
| D13 | Crash mid-show | restart into STANDBY (crash marker shipped) |
| D17 | TD on the same laptop | yes — the perf constraint |
| D2, D11, D12, D14, D15 | laptop facts (done via `inventory`), repo public?, `numpy<2`, `motion_first` (keep), cleanup menu | unchanged from the audit |
| D16 | Clean plate: fix the UI text or build it? | **build it** if D26 goes ahead (it is the foreground's input); else fix the text |

---

## Appendix A — legacy labels → homes

Code comments cite the **June roadmap**, kept verbatim at [archives/ROADMAP_2026-06.md](archives/ROADMAP_2026-06.md);
its section numbers are the ones below. Never renumber them; new work uses the prefixes above.

| Label in code comments | Home |
|---|---|
| `ROADMAP bug #N` (fixed) | archives/ROADMAP_2026-06.md §5 · ENGINEERING_RECORD §8 |
| `ROADMAP §4.2 Phase 2 ①–⑧`, `2b` | archives/ROADMAP_2026-06.md §6 index · ENGINEERING_RECORD §5 |
| `ROADMAP §3a/§3b`, `§3.2` | archives/ROADMAP_2026-06.md §3 · ENGINEERING_RECORD §3 |
| `ROADMAP P0/P1.4/P2/P3/P4`, `§7B` | archives/ROADMAP_2026-06.md §6 · ENGINEERING_RECORD §4, §7.B |
| `P-1`, `P-2`, `P-6` | ENGINEERING_RECORD §9 (P-1/P-2 to be added, DOC-15) |
| `K1`/`K3` | ENGINEERING_RECORD §12, to be created (DOC-15) |
| `ROADMAP §6` (launcher safety, `launcher/git_manager.py:27`) | archives/ROADMAP_2026-06.md · ENGINEERING_RECORD §13, to be created (DOC-15) |
| `Track O/X/C/S/G/D/P`, `OPERATOR_V2 §…`, `decision 5` | archives/OPERATOR_V2.md (+ TRACK_X_SMOOTHER for X §) |
| `OSC_CONTRACT §A/§B/§D` | docs/OSC_CONTRACT.md (numbering kept) |
| `DECOMPOSITION_PLAN …`, `UX_PLAN U2–U5`, `KNOBS E1/E2`, `TUNING Phase A–F`, `CORPUS_ANALYSIS §…` | their files (archives or reference), headings frozen |

## Appendix B — doc map

**Live:** this file · [PLAN_25M_2026-10.md](PLAN_25M_2026-10.md) · [ROADMAP_v2_REVIEW.md](ROADMAP_v2_REVIEW.md) ·
[OSC_CONTRACT.md](OSC_CONTRACT.md) · [REMOTE_OPS.md](REMOTE_OPS.md) · [MARKERS_PHASE0A.md](MARKERS_PHASE0A.md) ·
operator notes [TOURNAGE_2026-10-06_FR.md](TOURNAGE_2026-10-06_FR.md), [TERRAIN_2026-10-07_FR.md](TERRAIN_2026-10-07_FR.md)
· [NEW_SHOW.md](NEW_SHOW.md) / [CHECK_TEST.md](CHECK_TEST.md) (June, to merge into MODE_EMPLOI) · [TODO.md](TODO.md).
**Reference:** [AUDIT_2026-10.md](AUDIT_2026-10.md) + [audit-2026-10/](audit-2026-10/) · [CORPUS_ANALYSIS.md](CORPUS_ANALYSIS.md)
· [TUNING.md](TUNING.md) · [OPTICS.md](OPTICS.md) · [TRACKING_ROBUSTNESS.md](TRACKING_ROBUSTNESS.md) (direction changed: belt, not cuffs)
· [AUTOTUNE_DESIGN.md](AUTOTUNE_DESIGN.md) · [GUI_STACK_AUDIT.md](GUI_STACK_AUDIT.md).
**Archives:** [archives/](archives/) — anchored, do not plan from them.
