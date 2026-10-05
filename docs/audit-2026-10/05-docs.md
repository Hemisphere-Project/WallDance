# 05 — Documentation audit (stream 5/5)

**Baseline:** `origin/main` @ `a49d0f2` (2026-07-14) as checked out on dev37 (`/data/WallDance`, clean tree).
Last code commit `b0b402a`/`d26bd51` 2026-06-25; last doc sync `e8e93f1` 2026-06-25; last consolidation `574eeb0` 2026-06-22.
**Method:** every claim below was checked against code on dev37 (grep/read; unit suite run with
`uv run --no-sync python -m pytest -q -p no:cacheprovider` → **352 passed, 8 skipped, 7.3 s**; 359 collected).
No tracked file was edited. **Caveat:** Thomas says dev has since happened on the prod laptop. If the laptop
holds unpushed commits (after 2026-07-14), some of these fixes may already be done there, or there may be new drift.
Reconcile against the laptop's `git log origin/main..HEAD` before applying anything.

---

## 1. Executive summary

1. **The docs describe the system as of 2026-06-25, and the operator-facing docs are older still.** `README.md` describes the pre-rail UI
   (one "Detection Sensitivity" dial, CALIBRATE/DANCERS in a bottom bar, a "simple control panel", a top-bar profile switch and QR) and
   a flat `src/*.py` file map that has been wrong since the 2026-06-22 shim deletion (`cb96226`). About 25 corrections in README alone.
2. **The CPU path and FP16 advice are stale.** README still tells users to "enable FP16" (no such control: FP16 is hard-on,
   `app.py:482`) and says a CPU fallback runs "slower". In fact the pipeline now refuses to process without CUDA
   (`core/pipeline.py:456-461`).
3. **Internal contradictions remain after the last sync.** ROADMAP §6 (`:214-215`) and ENGINEERING_RECORD §8 (`:233,:242`) still list bugs
   #3/#12a/#12b/#12f as open, but ROADMAP §5 and commit `39acbf2` (2026-06-23) say they are fixed. TODO.md (`Last reviewed 2026-06-10`) lists 4
   "⬜" items that already exist: `extra/oscreplay`, the IDS-ratio ± buttons, the launcher's update check, and the PyInstaller launcher.
4. **Several operator-visible claims don't match the code.** (a) "Aim captures a clean plate": no capture happens. Applying the MOG2 scale only
   resets the adaptive model (`runtime/calibration_flows.py:328-384`), yet the GUI text says otherwise (`gui_builder.py:698,714,722`).
   (b) `/walldance/clear` is only sent on a **playback loop/restart**, never live (`app.py:1531,1553-1556`), and there is no reset key or button.
   (c) The phase-rail status chips are never populated (`gui_builder.py:587-588`, and no `_update_phase_rail` exists). (d) README shortcuts `Q`/`R`/`+/-` don't exist.
5. **OSC_CONTRACT is correct about the wire format but its citations have drifted.** All ~25 `file:line` cites are 3–35 lines off, it cites the deleted
   CPU path (`pipeline.py:910`), §B is still titled "Planned … gated", and `/walldance/meta/latency_ms` is missing from the §A message list. Fix it in place and keep the
   §B/§B.1/§B.2 numbering, because code cites it.
6. **The docs don't reflect the new direction (2026-10-05) at all.** There is no field-test framing. Continuity (centroid drops) is not a track.
   TRACKING_ROBUSTNESS/ROADMAP §3.3 assume **one harness/centre-of-mass marker** (`TRACKING_ROBUSTNESS.md:55-58`), not ankle+wrist markers. The
   "never push from dev boxes" rule is written nowhere, and there is no repo `CLAUDE.md`.
7. **The operator has no manual.** TODO-me's top item is "+++ Mode d'emploi". The material exists, in English, spread across NEW_SHOW, CHECK_TEST Parts 1/3,
   README GUI/troubleshooting, OPTICS venue-fit and launcher behaviour. **Recommendation: one French manual, `docs/MODE_EMPLOI.md`**, with the English UI labels kept
   verbatim plus a glossary (outline in §6).
8. **Consolidation plan:** 12 live/reference docs plus TODO.md become **6 live** (README · MODE_EMPLOI · ARCHITECTURE · ROADMAP · OSC_CONTRACT · IR_MARKERS),
   plus TESTING (dev), 3 reference docs (CORPUS_ANALYSIS · TUNING · OPTICS) and the archives. Dissolve TODO.md. Merge NEW_SHOW and CHECK_TEST and then delete them. Archive AUTOTUNE_DESIGN and GUI_STACK_AUDIT.
   There are 22 actions in total (DOC-1…DOC-22).
9. **Anchors.** About 400 code-comment anchors cite doc labels (ROADMAP ≈49, OPERATOR_V2 ≈36, Track-letters ≈100, DECOMPOSITION_PLAN 25, TUNING 26, UX_PLAN 14…).
   Twelve already don't resolve as written (`ROADMAP §4.2 …` has lived in ENGINEERING_RECORD §5 since 06-22). One is fully orphaned: `ROADMAP §6`
   "Untrack committed junk" in `launcher/git_manager.py:27` and `tests/test_launcher_git_manager.py:1`. 15 comments carry broken doc *paths*. The new ROADMAP needs a
   legacy-anchor appendix, and new tracks must use new prefixes (CONT/MARK/ARCH/PERF…), never re-use O/X/C/S/G/D/P.
10. **Code issues found along the way (passed to the other streams):** slot button 10 is drawn but the recorder has 9 slots. `build_engines.bat` never writes
    `models/fps_table.json`, so the imgsz FPS budget can't use measured costs on the Windows prod laptop. Three production features shell out to scripts in `application/tests/`.
    TODO-me "Motion detect fallback avant Yolo" conflicts with the ROADMAP §4 plan to delete `TrackingMode`.

---

## 2. Inventory

Git = `git log -1 --format=%ad`. Audience: **Op** = operator, **Dev** = developer/Claude, **Hist** = provenance only.

| Doc | Purpose (as written) | Aud. | Header date | Git | Size | Declared status | Actually current? | Action |
|---|---|---|---|---|---|---|---|---|
| `README.md` | Overview, install, workflow, GUI, file map, perf, TRT, OSC, troubleshooting | Op+Dev | — | 06-24 | 218 l / 15.5 KB | (implicit live) | ❌ Badly stale: pre-rail UI, flat file map, CPU/FP16 | DOC-1 |
| `TODO-me` | Thomas/operator raw wishlist (FR) | Op/Thomas | — | 07-14 | 19 l | live | ✅ The only doc that reflects the field (07-14) | DOC-21 |
| `docs/README.md` | Docs index | Dev | — | 06-25 | 66 l | live | 🟡 Index OK but lists TODO.md as live; no manual or architecture doc | DOC-14 |
| `docs/ROADMAP.md` | "Single source of truth for forward work" | Dev | 06-22 | 06-25 | 247 l / 23 KB | 🟢 live | 🟡 Mostly a done-log (struck rows); contradictions; no field-test/continuity/ankle-wrist; no §4.2 for code anchors | DOC-5 |
| `docs/TODO.md` | Hardware/procurement + phase inventory | Dev | "reviewed 06-10" | 06-25 | 87 l / 8 KB | 🟢 live | ❌ Duplicates ROADMAP; 4 "⬜" items already exist; broken link | DOC-9 |
| `docs/TRACKING_ROBUSTNESS.md` | IR-marker direction + Phase 0a/0b/1 | Dev | 06-16 | 06-16 | 155 l / 11 KB | 🟡 agreed, not built | ❌ Placement and fusion design superseded (ankles+wrists) | DOC-10 |
| `docs/OSC_CONTRACT.md` | Wire contract `/walldance/*` | Dev + video team | 06-22 | 06-22 | 220 l / 13 KB | confirmed/live | 🟡 Wire format right; citations drifted; §B mislabelled; `/clear` semantics wrong; meta missing from §A | DOC-8 |
| `docs/NEW_SHOW.md` | Operator field playbook ①→⑥ | Op | 06-22 | 06-22 | 110 l / 6 KB | 🟢 live | 🟡 Mostly right; wrong button names, clean-plate claim, missing auto-tune/known-N/ISSUE | DOC-2/3 |
| `docs/CHECK_TEST.md` | Desk / joint-tuning / on-rig tests | Op+Dev mixed | 06-16 | 06-25 | 159 l / 8 KB | 🟢 live | 🟡 Wrong test count, Windows-only paths, sweep script not in repo | DOC-2/4/7 |
| `docs/CORPUS_ANALYSIS.md` | Scene-physics measurements + corpus re-founding | Dev | 06-10 (note 06-22) | 06-25 | 247 l / 35 KB | 📘 reference | ✅ As a dated record (header warns); 1 prod-only link | DOC-13 |
| `docs/TUNING.md` | Replay/tune toolchain plan (A–F) + inventory | Dev | 06-09 | 06-25 | 295 l / 19 KB | 📘 reference | 🟡 Tool table current (Track-P edits); §2 "carry-over facts" + 4 refs stale | DOC-12 |
| `docs/OPTICS.md` | Lens/camera envelopes, venue fit | Op+Dev | 06-12 | 06-12 | 88 l / 7 KB | 📘 reference | ✅ (1 stale anchor link) | DOC-13 |
| `docs/AUTOTUNE_DESIGN.md` | Knob determinability + calibrate workflow design | Dev | 06-16 (upd. 06-25) | 06-25 | 358 l / 23 KB | 📘 "largely historical" | 🟡 Historical (self-declared) | DOC-11 |
| `docs/GUI_STACK_AUDIT.md` | Stay-Python/DPG decision | Dev | 06-11 (note 06-22) | 06-22 | 135 l / 15 KB | 📘 decision record | 🟡 Decision holds; metrics drifted (self-declared) | DOC-11 |
| `docs/archives/ENGINEERING_RECORD.md` | Shipped history P0–P4, bugs, lessons | Hist+Dev | — (06-22) | 06-22 | 283 l / 22 KB | historical | 🟡 Bug statuses wrong; nothing after 06-22 | DOC-15 |
| `docs/archives/OPERATOR_V2.md` | Tracks O/X/C/S/G/D/P plan | Hist | archived 06-22 | 06-22 | 806 l / 65 KB | archived | ✅ Frozen; heavily anchored (≈36 code cites) | keep |
| `docs/archives/UX_PLAN.md` | U0–U5 UX | Hist | archived 06-22 | 06-22 | 193 l | archived | ✅ Frozen; anchored (U2–U5, §6) | keep |
| `docs/archives/DECOMPOSITION_PLAN.md` | app.py → packages | Hist | archived 06-22 | 06-22 | 170 l / 22 KB | archived | ✅ Frozen; anchored (25 cites) | keep (+ARCHITECTURE) |
| `docs/archives/TRACK_X_SMOOTHER.md` | RTS smoother design | Hist | archived 06-22 | 06-22 | 534 l / 42 KB | archived | ✅ Anchored (§2/§7/§10) | keep |
| `docs/archives/KNOBS.md` | Knob sensitivity E1/E2 | Hist | archived 06-22 | 06-22 | 140 l | archived | ✅ Anchored (E1/E2, finding #2) | keep |
| `docs/archives/CALIB_DETECTION_FIX_PLAN.md` | Detection cases 1–4 | Hist | archived 06-22 | 06-22 | 81 l | archived | ✅ | keep |
| `docs/archives/AUDIT.md` | Maintainability audit | Hist | 03-26 | 06-08 | 410 l | archived | ✅ | keep |
| `docs/archives/audit_report.md` | Older script-parity audit | Hist | 03-26 | 04-01 | 47 l | archived | ❌ Obsolete (`--cpu` flag etc.) | DOC-16 (optional delete) |
| `docs/archives/ROBUSTNESS_PLAN.md` | Original detection north star | Hist | 06-08 | 06-08 | 138 l | archived | ✅ (12 broken links) | keep |
| `docs/archives/TRACKING_PLAN.md` | Tracker decision log | Hist | 03-13 | 06-08 | 275 l | archived | ✅ (5 broken links; cited by session-analyst agent) | keep |
| `docs/archives/P3_FUSION_SIMPLIFICATION.md` | P3 motion-fusion design | Hist | 06-08 | 06-08 | 134 l | archived | ✅ (5 broken links; "P3 §5" anchor) | keep |
| `docs/archives/HARDWARE_GUIDE.md` | Hardware/procurement guide | Hist(+Op) | "March 2026" | 04-01 | 160 l | archived | 🟡 README still links it as *the* hardware ref | keep; relink README→OPTICS |
| `docs/archives/SPECIFICATIONS.md` | Old tech specs | Hist | 03-26 | 04-01 | 787 l / 36 KB | archived | ✅ (cited `config.py:75`) | keep |
| `docs/archives/IDS_CAMERA_STALL_INVESTIGATION.md` · `IDS_STALL_CONCLUSIONS.md` | USB3 stall study | Hist | 02-2026 | 04-01 | 757 + 190 l | archived | ✅ (cited `config.py:84`, `ids_camera.py:261`, README troubleshooting) | keep |
| `docs/archives/tiling_plan.md` | Rejected tiling | Hist | — | 04-01 | 102 l | archived | ❌ Rejected (TRACKING_ROBUSTNESS "4×640 tiling OUT") | DOC-16 (optional delete) |
| `docs/archives/LEGACY_pre-project-proposal_fr.md` | Original FR proposal | Hist | — | 04-01 | 618 l / 27 KB | archived | ✅ Useful FR vocabulary for the manual | keep |
| `application/tests/scenarios/README.md` | Scenario manifest schema + GT protocol | Dev | (06-10) | 06-10 | 73 l | live | 🟡 `drafts/` and "drafts pending" stale | DOC-18 |
| `.github/copilot-instructions.md` | Agent shell etiquette | Dev | — | 04-03 | 16 l | live | ✅ generic | — |
| `.github/agents/session-analyst.agent.md` | Session-log analyst agent | Dev | — | 04-03 | 105 l | live | 🟡 2 dead paths | DOC-19 |
| `prototypes/0{1..4}-*/README.md` | Dec-2025 model prototypes | Hist | — | 2025-12-08 | 0/34/52/62 l | — | Historical; 01-MoveNet README empty | DOC-16 (optional) |
| `launcher/`, `extra/`, `tmp_analysis/` | — | — | — | — | — | **no docs** | Launcher update policy and `extra/` tools are undocumented | DOC-6 (ARCH §2/§14) |
| *(missing)* repo `CLAUDE.md` | dev-box rules | Dev | — | — | — | absent | — | DOC-20 |

Totals: `docs/` = 12 files / 173 KB, `docs/archives/` = 18 files / 362 KB, about 8,000 lines in all.

---

## 3. Correction list

Format: **ID · file:line**: wrong → right · *evidence*. Line numbers refer to `a49d0f2`. Where an item is merged into a new doc (MODE_EMPLOI, ARCHITECTURE),
the correction applies to the merged text.

### 3.1 `README.md`

- **R1 · :24-30, :218**: "WallDance will show **CPU fallback** … FPS will be much lower" / "CPU fallback: … re-run install.bat" → since Track P there is no CPU processing.
  Without CUDA the `[CPU FALLBACK]` badge shows and the pipeline refuses to process, so there is no detection and no OSC. Keep the install.bat auto-fix steps and reword the
  consequence. *`core/pipeline.py:456-461` raises "GPU pipeline required (Track P removed the CPU fallback path)"; `:510`; `runtime/main_loop.py:255-260`.*
- **R2 · :63**: "Python 3.10+" → "Python **3.10–3.12**". Also state that the `gpu` extra (kornia, TensorRT, onnx) is mandatory now; the installers add it when a GPU is detected.
  *`application/pyproject.toml:6,23-30`; `install.bat:74`, `install.sh:80`.*
- **R3 · :5-20**: the field install path is missing. The Windows laptop is installed and updated by `launcher/release/WallDanceLauncher.exe`, which clones
  `Hemisphere-Project/WallDance`, creates a desktop shortcut, fetches origin on every start, offers "Update" (behind) or "Discard and Update" (diverged), re-runs
  install.bat when deps change, then runs run.bat. *`launcher/main.py:114`, `launcher/gui.py:126-200`, `launcher/git_manager.py:10-16`.*
- **R4 · :67-72**: "Builds TensorRT engines for **all** `.pt` models" → builds FP16 engines for **yolo11m/l/x** at 640–1920; n/s are built on demand. The Linux script also writes
  `models/fps_table.json` (`measure_engine_fps.py`); **the Windows .bat does not**. *`extra/build_engines.sh:40-46` + tail; `extra/build_engines.bat:82-87`.*
- **R5 · :65-79**: "Extra Scripts" omits `extra/oscreplay/` (OSC record/replay, since 2026-02-26 `5a8b746`), `extra/venue_fit.py` (OPTICS venue fit) and
  `extra/measure_engine_fps.py`.
- **R6 · :100**: hardware details → link `docs/OPTICS.md` (current; it also lists the 6 mm Lens B), not the archived `HARDWARE_GUIDE.md` (March 2026).
- **R7 · :104**: "See `docs/archives/UX_PLAN.md` for the shipped design rationale" → UX_PLAN is superseded by the phase rail (OPERATOR_V2 Track O). Point to the manual and ARCHITECTURE.
- **R8 · :106**: "tap the QR button **in the top bar**" → the QR button is in **phase ② Profile** (`gui_builder.py:680-690`). The web monitor is on port 8080 (`core/config.py:420`).
- **R9 · :107**: "press **CALIBRATE** … seeds gamma/CLAHE, sweeps MOG2 … and **captures a clean plate**" → the CALIBRATE button sits in phase ③ (label "CALIBRATE",
  `gui_builder.py:702-703`). It applies the exposure/gain servo (IDS), gamma (noise-capped), a CLAHE seed and MOG2 var×scale. **No static clean plate is captured.** Applying the scale only
  resets the adaptive MOG2 so it re-learns from the empty stage. The real plate capture is the expert-only Background section button. *`runtime/calibration_flows.py:328-384`;
  `core/motion_detector.py:89-96`; `gui_builder.py:1390-1397`; ROADMAP:97 itself says "clean-plate pixel recovery … is the unbuilt piece".*
- **R10 · :108**: "press **DANCERS** … seeds the sensitivity dial" → phase ④ button **"Calibrate with Dancers"** (`gui_builder.py:736-737`). Apply writes person height,
  confidence, imgsz and blur budget (`calibration_flows.py:644-651`) and re-centres Dial A at 50. Also missing: ④ **"Calculate (auto-tune)"** and **"Tune (known-N)"**.
- **R11 · :109**: "The only live knob you need is **Detection Sensitivity**" → phase ⑥ has **Dial A "Drops <-> Ghosts"** and **Dial B "Gap bridging"** (hidden when
  calibration finds it inert), plus output **Box-clamp** and **smooth L**. *`gui_builder.py:917-984`.*
- **R12 · :111**: profiles "switched from the top bar" → phase ② radio button. *`gui_builder.py:648-655`; top-bar comment `:370` "moved to the phase 2 Profile panel".*
- **R13 · :115**: top bar = "project + saved-version pickers, Show/Rehearsal, save, QR, badges" → the top bar holds only the **Project combo, Save, and status chips** (state, CAM, [IDS]/[CV], OSC,
  model, [TRT]/[PT], [CPU FALLBACK], FPS, GPU, VRAM). Config-version, safe-defaults (click = load, Ctrl+click = save) and QR are in ②. *`gui_builder.py:333-425, 659-690`.*
- **R14 · :117**: "Control panel (simple): Input, ROI, Enhancement, Model, Detection (Person Height + Sensitivity), Preview, OSC, S/K/B/T/I toolbar" → no simple panel exists.
  The right column shows the **selected phase** only. The numeric sections live in the floating **"Advanced"** drawer (button at the right end of the phase rail): Background (expert),
  Enhancement, Model, Detection (Person Height + expert raw knobs), Preview, OSC. Input, ROI and Exclusion moved to ①; the view toolbar moved to ⑥. *`gui_builder.py:514-541, 593-604, 1100-1125`.*
- **R15 · :118**: bottom bar = "source/playback, CALIBRATE + DANCERS, STANDBY/RUN, stats" → the bottom bar shows **stats only** (`gui_builder.py:464-470`). Above it sits the
  always-visible **Recordings** bar: LIVE, REC, slot buttons, status, speed/pause/step, **ISSUE** (`gui_builder.py:1003-1098`). STANDBY/RUN are in ⑥ (`:893-913`).
- **R16 · :121**: shortcuts "`Q` quit, `R` reset tracker, `+/-` preview scale" **do not exist**. The real set is `E S K B T I P`, **Ctrl+S** save, **Ctrl+Shift+E** expert, **F8** issue report;
  Ctrl+click on a slot opens its history. Preview scale is auto-fit. *`ui/adapter.py:220-266`; `gui.py:890-894`; `gui_builder.py:1541-1543`.*
- **R17 · :130-138**: the runtime flow lacks the runtime/ui seam, output smoother/box-clamp, motion worker and package paths → replace with a link to ARCHITECTURE §5.
- **R18 · :140-162**: the "File Map (src/)" lists 21 flat modules (`pipeline.py`, `tracker.py`, `web_monitor.py`, `background.py`, …). These live in `core/` (18), `camera/`
  (2) and `services/` (1), and the import shims were deleted on 2026-06-22 (`cb96226`). It misses `runtime/*` (8 modules), `ui/*` (4), `core/output_smoother.py`, `core/ops_monitor.py`
  and `gui_constants.py`. Replace with a link to ARCHITECTURE §3.
- **R19 · :154**: `enhancer.py` "low-light enhancement (CPU fallback)" → `core/enhancer.py` owns the CLAHE/gamma parameters and provides the CPU enhance used for the **STANDBY preview**
  (`runtime/main_loop.py:673`). Detection-path enhancement is GPU, in `core/gpu_pipeline.py`.
- **R20 · :168, :179**: "TRT checkbox" / "In the **MODEL** section" → Advanced drawer → Model → **"TensorRT:"** checkbox. *`gui_builder.py:1311-1317`.*
- **R21 · :170, :213**: "Enable FP16 … (applies to PyTorch mode)" → **no FP16 control exists.** FP16 is always on: `app.py:482 use_fp16=True`, `core/pipeline.py:587`,
  engines exported `half=True` (`core/model_manager.py:189`). Delete the tip.
- **R22 · :215**: "install with `pip install tensorrt`" → TensorRT comes with the `gpu` extra (`pyproject.toml:25`). Re-run `install.bat`/`install.sh`; never use plain pip or `uv sync` (see §9).
- **R23 · :195-206**: the OSC table is missing **`/walldance/meta/latency_ms [ms]`** (`core/osc_output.py:103-113`, `runtime/main_loop.py:880-895`). `/walldance/clear` "Tracker reset
  event" → it is only emitted when **playback loops or restarts** (`app.py:1531`, `:1553-1556`, `runtime/recording_controller.py:142`). There is no GUI or keyboard reset
  (`gui.py:818` `_on_tracker_reset` is not bound to any widget), so a live show never sends it. The table also doesn't mention L (L>1 = RTS-smoothed, L frames late). Replace with a 3-line summary and a link to OSC_CONTRACT.
- **R24 · :209-213**: troubleshooting "raise/lower **Detection Sensitivity**" → Dial A (Drops <-> Ghosts) / Dial B (Gap bridging). "enable FP16" → delete (R21). Move the section to the manual.
- **R25 · :1-3**: no mention of OSC consumers (TouchDesigner), the phone monitor, the field-test status or the manual. README should be the front door → DOC-1.

### 3.2 `docs/ROADMAP.md`

- **M1 · :3**: header date 2026-06-22, but content was edited through 06-25. The whole doc predates the field test (2026-10) and the 2026-10-05 priorities → rewrite (DOC-5).
- **M2 · :14-16**: "Track labels … preserved in §3 — that is now their canonical home" → §3 only uses the letters in table cells and never defines them. Move a label glossary to Appendix A.
- **M3 · :47, :98, :168**: "CI (352 tests)" / "unit 354" / "351 tests" → three different counts. On dev37 @a49d0f2: **352 passed + 8 skipped** (359 collected). CI runs with minimal
  deps (`ci.yml`: numpy, opencv-headless, pytest, dulwich; no torch). Stop quoting counts.
- **M4 · :82**: `app.py:1125` (intermittent-confirm wiring) → `app.py:1141-1142`, inside `_apply_config_without_model`. Cite the symbol, not the line. Same at `AUTOTUNE_DESIGN.md:5`.
- **M5 · :84**: `tune.py:201` → `tune.py:204`.
- **M6 · :85, :108-115**: Phase 0a "Retroreflective tape/thread on a **test harness**" and Phase 1 "marker centroid → high-confidence fusion" are **superseded** by the 2026-10-05 direction: markers
  on **ankles + wrists**, i.e. 4 per dancer with keypoint-level fusion (COCO 9/10/15/16) and per-dancer grouping. The rewrite belongs to the MARK track (IR-markers stream).
- **M7 · :96**: "the live probe needs on-rig validation" / "live click→search→Apply flow wants one on-rig pass" → field status unknown since the field test began. Ask Thomas (Q5).
- **M8 · :146-148**: the "TODO Phase 9" list includes four items that exist: **standalone OSC record/playback tool** = `extra/oscreplay/` (`5a8b746`, 2026-02-26); **IDS crop ratio buttons** =
  ± row on the ratio slider (`gui_builder.py:1471-1482`); **check-for-updates on startup** = the launcher (`launcher/gui.py:144-200`); **Windows launcher build** = PyInstaller
  (`launcher/WallDanceLauncher.spec`, `launcher/release/WallDanceLauncher.exe`). Only packaging the *app* (Nuitka) remains.
- **M9 · :156**: "see [TODO.md] for purchases" → fold hardware into the ROADMAP Hardware section (DOC-9).
- **M10 · :169**: `TrackingMode` "**71 refs** across config/tracker/pipeline/**motion_detector**/app" → **33 refs in 6 files**: src `app.py`, `core/config.py`, `core/pipeline.py`,
  `core/tracker.py`; tests `replay.py`, `tune.py`; none in motion_detector. ⚠ This conflicts with TODO-me "++ Motion detect fallback avant Yolo" (MOTION_FIRST was exactly that), so decide before deleting.
- **M11 · :171**: `tracker_smoothing` "(G4: truly Fixed, **no config key**)" → it *is* persisted and loaded. *`app.py:722, 987, 1130-1131`.*
- **M12 · :214-215 vs :178-193**: §6 "*(Still open: #3, #12a, #12b, #12f — see §5.)*" contradicts §5 "Fixed 2026-06-23" → **fixed** in `39acbf2`. *`core/pipeline.py:1425`
  (timing reset); `runtime/calibration_flows.py:255` ("keep the stage clear"); `app.py:1023` (ROI guard).*
- **M13 · :10-12, :197-208**: "§6 keeps a condensed index so code comments that cite `ROADMAP bug #N` / `P3` / `§3a` / `Track O` still resolve here". But 15 code sites cite
  **`ROADMAP §4.2 Phase 2 ①–⑧`** / `§4.2` / `4.2 2b`, and ROADMAP has no §4.2. Map them in Appendix A → ENGINEERING_RECORD §5.
- **M14 · :46, :82, :96**: `tmp_analysis/g1…g6/`, `analysis.json`, `phase2/SUMMARY.md`: tmp_analysis outputs are **gitignored** (`tmp_analysis/.gitignore`: `*`, `!*.py`), so they exist only
  on the prod laptop. Say so.
- **M15 · :89-98, :160-174**: most rows are struck-through or "✅ COMPLETE" (Track P, shims, known-N, P-1/P-2, calib fixes) → move them to ENGINEERING_RECORD (DOC-15). ROADMAP should be forward-only.
- **M16 · :22-24 (§0)**: "press **one calibration button**" → today there are two (③ CALIBRATE + ④ Calibrate with Dancers) plus the optional auto-tune and known-N. Mark this as the C-next aspiration.
- **M17 · :41**: phase rail "status strip" → the per-phase chips (done/pending/count) are **never populated**. `gui_builder.py:587-588` creates them empty and no `gui._update_phase_rail`
  exists (only the smoke test checks they exist). Don't claim per-phase status.

### 3.3 `docs/TODO.md` (dissolve; corrections matter only for what moves)

- **T1 · :5**: "Last reviewed 2026-06-10" → superseded. Dissolve (DOC-9).
- **T2 · :18**: "YOLO **8/11/26** pose" → **yolo11 n/s/m/l/x only** (`gui_builder.py:1291-1294`; yolo26 removed in Phase 2b).
- **T3 · :19**: "9 slots/project" → the recorder has 9 (`core/video_recorder.py:63`) but the GUI draws **10** slot buttons (`gui_builder.py:1043`). Slot 10 is dead (§3.12-X1).
- **T4 · :26**: "elevated to step ⑤ of the ROADMAP §4.1 sequence" → ROADMAP has had no §4.1 since 06-22.
- **T5 · :28**: link `../application/src/ops_monitor.py` → `../application/src/core/ops_monitor.py` (broken).
- **T6 · :39, :44**: "feeds ROADMAP P4" / "pairs with the P2 Go-Live calibration log (**ROADMAP §5**)" → ROADMAP §5 is now "Open bugs". P2/P4 are in ENGINEERING_RECORD §4.
- **T7 · :52, :55, :66, :70**: "⬜ IDS crop ratio buttons", "⬜ Check for updates on startup", "⬜ Standalone OSC record/playback tool", "⬜ Windows launcher" → all exist (see M8).
- **T8 · :79**: "the *scene-mask* half is now **automatic** via P1.4" → reversed: exclusion is **manual-only** (OPERATOR_V2 decision 5; `runtime/calibration_flows.py:375-378`).
- **T9 · :85**: hardware list is missing Lens B (Tamron M118FM06 6 mm) from `OPTICS.md:11`. It also doesn't say whether the IP66 housing and mounting were bought. Ask (Q9).

### 3.4 `docs/OSC_CONTRACT.md` (fix in place; keep §A/§B.1–§B.4 numbering, which code cites)

- **O1 · :22-24, :34**: emission at `core/pipeline.py:904-905` / cadence `:891-905` → now `:848-876`, the tail of `FrameProcessor._post_yolo_chain`. Switch all cites to **symbols**.
- **O2 · :30-32**: `config.py:377/378/379` → `:382/:383/:384` (`OSC_ENABLED/IP/PORT`). `osc_output.py:7` (bundle import unused): still true.
- **O3 · :52-98**: `osc_output.py` line cites are off by 3–18 (centroid `55-66`, bbox `68-75`, velocity `77-85`, keypoints `87-94`, count `96-101`, send_frame `115-127`, clear
  `129-134`). Tracker cites are off by 10–35 (`485-487`→~`496`, `2953-2955`→~`2989`, `576-578`→~`585-587`, `3126`→`3161`, `205-206/362/440-441`→`342/448`). → Use symbols
  (`DancerTrack.update`, `_collect_confirmed_tracks`, `_frames_since_skeleton`).
- **O4 · §A.3 (:47-87)**: `/walldance/meta/latency_ms [float ms]` is emitted (`osc_output.py:103-113`) but is listed only in prose under §B.3 → add it to the §A message list
  (re-emitted when L, fps or the OSC on/off state changes; suppressed while fps is unknown: `runtime/main_loop.py:880-895`).
- **O5 · :85-87**: `/walldance/clear [1]` "Reset signal (e.g. **engine stop / scene reset**)" → sent **only** by `_cb_tracker_reset` (`app.py:1524-1532`), whose only trigger is
  a **playback loop/restart** (`app.py:1553-1556`). It is never sent on STANDBY or stop, nor in live-camera mode. Either document that or make it a real signal (ask the video team).
- **O6 · :68-71**: bbox "⚠ This is today's flicker source … Batch-2's box-clamp fixes this" → box-clamp shipped, **default ON** (`core/pipeline.py:107, 638-643`). Reword as "with Box-clamp OFF".
- **O7 · :93**: "`pipeline.py:910` CPU identity / `pipeline.py:1408` GPU letterbox-unscale" → the CPU path was deleted (Track P 2B, `25d1b68`). Only `FrameProcessor._unscale_letterbox`
  remains (`core/pipeline.py:1325`, `clamp_to_yolo_size=`).
- **O8 · :99**: "## B. **Planned** batch-2 additions (**gated on this confirmation**)" → retitle "B. Output controls (shipped)". Keep the §B.1/§B.2 numbers: `osc_output.py:34,104,117`,
  `core/pipeline.py:848,932` and `gui_builder.py:954` cite them.
- **O9 · :146-149 vs :169-177**: B.2 "No frame buffering, no look-ahead" is true only at L=1; at L>1 the RTS window *is* an L-frame look-ahead buffer. State it per-L.
- **O10 · :206-212**: B.4 "No … new emitted addresses … aspect caveat (§A.2) stands until the lagged tap / `meta` namespace ships" → `/walldance/meta/latency_ms` shipped and the
  lagged tap was removed. Frame dimensions are still not on the wire.
- **O11 · :216-220**: §C "Verification (batch-2 item 2 DoD)" is historical → move to TESTING.md.
- **O12 · (gap)**: no mention of `extra/oscreplay/` (record/replay of the stream for the video team) or of `MAX_PERSONS=6` (`core/config.py:62`) as the report cap → add.

### 3.5 `docs/NEW_SHOW.md` (merged into MODE_EMPLOI; fix during the merge)

- **N1 · :36, :119 (CHECK_TEST)**: "press **Aim**" → the button is **"CALIBRATE"**, in the panel "3 - Aim & Empty Scene" (`gui_builder.py:696-703`).
- **N2 · :38-39, :92**: "→ captures a **clean plate**" / "The clean plate is grabbed from the opening dancer-free frames" → not implemented (see R9). Add the **CLAHE seed** to the list.
- **N3 · :48-49**: Calib2 "derives detection enhancement (**CLAHE**), person-height + ratios, image size, a confidence seed, and the blur budget" → Apply writes height (+ratios), confidence,
  imgsz and blur budget (`runtime/calibration_flows.py:644-651`). CLAHE comes from the ③ seed or the ④ **"Calculate (auto-tune)"** sweep. The blur budget caps the *next* ③ servo.
- **N4 · :50**: "④ warns if ③ never ran in this profile" → no such warning found in code (no `_update_phase_rail`; chips empty; no toast in `calibration_flows.py`). Verify on the laptop or drop it.
- **N5 · :43-56**: missing ④ **"Calculate (auto-tune)" → "Apply seed"** and **"Tune (known-N)" → "Apply tune"** (`gui_builder.py:760-830`). These are STANDBY subprocesses over the newest recording.
- **N6 · :71**: "press **Go Live** (RUN)" → the button is **"RUN"** (beside "STANDBY") in "6 - Go Live" (`gui_builder.py:893-913`).
- **N7 · :108-110**: "🎞 **Recordings drawer (off the live surface)**" → an always-visible one-line **Recordings** bar (`gui_builder.py:1003-1010`). 9 usable slots (10 drawn), Ctrl+click for slot history, **ISSUE** button.
- **N8 · :6-7**: "each phase has … a plain-language status line" → each panel has a static description; dynamic status chips are not implemented (M17).

### 3.6 `docs/CHECK_TEST.md` (split into MODE_EMPLOI + TESTING)

- **C1 · :17-18, :59, :63, :155-158**: `./.venv/Scripts/python.exe` is Windows-only, and `WD_RUN_REPLAY=1 ./.venv/...` mixes bash syntax with a Windows path → use `uv run --no-sync python …`
  (cross-platform, and avoids the CPU-torch re-sync, §9). Give cmd/PowerShell env-var variants.
- **C2 · :59-60**: "expect **365 passed, 7 skipped**" → **352 passed, 8 skipped** on dev37 @a49d0f2. Don't hardcode.
- **C3 · :78-81, :157**: `../tmp_analysis/baseline_20260616/sweep.py` and `table.md` → **not in the repo**: the gitignored dir exists only on the prod laptop. Commit it (e.g. `application/tests/baseline_sweep.py`)
  or label it prod-only. Note that `tests/replay_sweep.py` is a *byte-compare* gate, not a pass-line scorer.
- **C4 · :28**: "Each phase shows a one-line plain status" → see M17/N8.
- **C5 · Part 2 (:69-108)**: written as a Claude↔Thomas protocol ("I report the numbers / You watch") → developer content, moves to TESTING.md. Parts 1.1–1.4 and 3 are operator content and move to the manual.

### 3.7 `docs/TRACKING_ROBUSTNESS.md` (direction change; content owned by the IR-markers stream)

- **TR1 · :3**: status "gated on a physical spike (Phase 0a)" → re-baseline. The 2026-10-05 direction is **ankle + wrist** markers. Was Phase 0a run? (Q5)
- **TR2 · :55-59**: "*Placement:* **one marker at harness / centre-of-mass** … add a second (front+back)" → superseded: 4 limb markers per dancer.
- **TR3 · :61-71**: fusion "marker centroid = high-confidence measurement into `tracker.update`; marker→track by proximity" → with limb markers the measurement is **keypoint-level**
  (L/R wrist = COCO 9/10, L/R ankle = 15/16). This needs marker→limb association, per-dancer grouping and a centroid model that isn't biased by limb swing. Extremities move fastest, so the
  blur budget (`AUTOCAL2_BLUR_*`) becomes load-bearing.
- **TR4 · :52-54**: size estimates (3–5 cm, 5–9 px) assume a torso patch → redo for wrist/ankle bands (cylindrical, frequently self-occluded, under rope/aerial inversion).
- **TR5 · :26-27, :151**: "Load-bearing OSC = position + identity; skeleton nice-to-have; `osc_output.py` **unchanged**" → may no longer hold if marker-anchored wrists/ankles become load-bearing.
  Any change is an OSC_CONTRACT edit (Q7).
- **TR6 · (file)**: no code anchors cite TRACKING_ROBUSTNESS (0 hits), so a rename to `docs/IR_MARKERS.md` is safe. Inbound doc links: `docs/README.md:21,31`; `ROADMAP.md:8,65,109,139,232`;
  `archives/ENGINEERING_RECORD.md:54`.

### 3.8 Reference docs + index

- **U1 · TUNING.md:18**: "top priority in **ROADMAP §4.1**" → gone. **:125, :155, :198** `docs/KNOBS.md` → `docs/archives/KNOBS.md`. **:166** "Opt-in golden regression (**slots 3 & 4**)" → the trio
  `hangar-floor`/`hangar-aerial`/`texture-aerial` on **TRT**, engine/driver-locked (`tests/test_regression_replay.py:1-27`). **:201** link `../application/src/motion_model.py` →
  `…/src/core/motion_model.py` (broken). **§2 (:179-226)** "carry-over facts the fresh session MUST know" → mark as a 2026-06-09 snapshot (e.g. the "Deferred" list is partly done).
- **U2 · AUTOTUNE_DESIGN.md:5**: `app.py:1125` → `app.py:1141`. The doc self-declares as historical → archive (DOC-11). Its anchors (`AUTOTUNE_DESIGN §7`, `AUTOTUNE gap #2`) are name-based, so they survive the move.
- **U3 · CORPUS_ANALYSIS.md:11** (and `archives/OPERATOR_V2.md:53`): link `../projects/CORPUS_NOTES.md` → `projects/` is gitignored and the file is **not on dev37**. Mark it "(prod laptop only)". Keep the §5/§6.5/§6.7/§8 numbering (anchored).
- **U4 · OPTICS.md:3**: "[ROADMAP §4.2 2b](ROADMAP.md)" → `archives/ENGINEERING_RECORD.md` §5 (Phase 2b).
- **U5 · GUI_STACK_AUDIT.md**: the header already says the metrics drifted → archive and keep a 5-line ADR in ARCHITECTURE §15.
- **U6 · docs/README.md:19, :21, :30-34, :43-44, :55**: index rows for NEW_SHOW, TODO ("🟢 Live"), CHECK_TEST, AUTOTUNE and GUI_STACK_AUDIT; UX_PLAN "superseded by ROADMAP / OPERATOR_V2" → regenerate (DOC-14).

### 3.9 `docs/archives/ENGINEERING_RECORD.md` + archives

- **E1 · :233**: bug #3 "🟡 Low (**open**)" → ✅ fixed 2026-06-23 (`39acbf2`; `core/pipeline.py:1425`).
- **E2 · :242**: #12 (a) "**open**", (b) "**open**", (f) "**open**" → ✅ fixed 2026-06-23 (`39acbf2`).
- **E3 · :240**: bug #10 "one `_post_yolo_chain` … serves both [CPU/GPU]" plus a link to `test_gpu_cpu_parity.py` → the CPU path and the parity test were deleted in Track P 2B (`25d1b68`). Dead link.
- **E4 · :254-256**: "still-open performance items — **mono-aware gray path, persistent motion worker**, …" → P-1/P-2 done 2026-06-23 (`bed262c`).
- **E5 · (gaps)**: nothing after 06-22. Missing: Track C fixes + pool quiet-apply (06-22), shim deletion (06-22), Track S provenance, P-1/P-2, the open-bug cluster (06-23), Track P
  Stages 1/1b/2A/2B/3 (06-24), known-N K1/K3/GUI (06-24/25). The **launcher update-safety** work is also absent, which leaves the orphaned "ROADMAP §6" anchor (§4).
- **E6 · archives (all)**: 27 broken relative links. Moving files one level deeper broke `../application` → `../../application`, plus flat `src/*.py` paths: ROBUSTNESS_PLAN ×12,
  P3_FUSION ×5, TRACKING_PLAN ×5, DECOMPOSITION_PLAN ×1 (`../application/tests/replay_sweep.py`), OPERATOR_V2 ×1, ENGINEERING_RECORD ×1. Archives stay frozen; a mechanical fix is optional (DOC-16).

### 3.10 Other repo docs

- **S1 · application/tests/scenarios/README.md:9-11, :69-70**: "`drafts/` holds manifests …" / "Drafts pending per-range labels: dark-crowd, white-walkers" → there is **no `drafts/`**.
  Both are first-class manifests with per-range `expected_count` lists and pass lines (class B / class A). *ER:173.*
- **S2 · same :65**: golden trio "regression-tested in `test_regression_replay.py`" → add "on the GPU+TRT path; goldens are engine/driver-locked".
- **G1 · .github/agents/session-analyst.agent.md:93-94**: `docs/TRACKING_PLAN.md` → `docs/archives/TRACKING_PLAN.md`; `application/src/config.py` → `application/src/core/config.py`.
- **G2 · .gitignore:53**: "numbers live in `docs/KNOBS.md`" → `docs/archives/KNOBS.md`.

### 3.11 Code comments and UI strings pointing at docs (behaviour-free edits, but they are code: app-smoke gate only)

Broken doc **paths** in comments (15): `camera/ids_camera.py:261` `docs/IDS_STALL_CONCLUSIONS.md`; `core/config.py:84` `docs/IDS_CAMERA_STALL_INVESTIGATION.md`;
`core/config.py:75` `SPECIFICATIONS.md`; `core/config.py:455,496,533`, `core/calib2.py:1` and `core/sensitivity_macro.py:1` `UX_PLAN.md`; `core/config.py:456,533` and
`core/calibration.py:315,639` `KNOBS.md`; `tests/test_output_smoother.py:6` `docs/TRACK_X_SMOOTHER.md`; `tmp_analysis/corpus_survey.py:3` `CORPUS_NOTES.md`
(prod-only). The bare-name ones (`UX_PLAN.md`, `KNOBS.md`) resolve by name to `docs/archives/` and can stay. The `docs/X.md` ones should become `docs/archives/X.md`. Also
`core/config.py:416` "src/web_monitor.py" and `:432` "src/calibration.py" use flat paths.

**Operator-visible UI text that is wrong** (fix alongside the manual): "captures the clean plate" at `gui_builder.py:698`, `:714`, `:722` and `ui/calibrate_all_wizard.py:148`. Docstrings
`runtime/calibration_flows.py:331,376` "derives … clean-plate". *Evidence as R9.*

### 3.12 Code findings from this audit (for the continuity, architecture and performance streams; not doc fixes)

- **X1**: slot button **10** is drawn (`gui_builder.py:1043` `range(1, 11)`), but `VideoRecorder.NUM_SLOTS = 9` (`core/video_recorder.py:63`) and the UI refresh covers only 1–9
  (`runtime/recording_controller.py:432`). Slot 10 rejects record and play. *(Likely; confirm with a click on the laptop.)*
- **X2**: `extra/build_engines.bat` never runs `measure_engine_fps.py`, so on the **Windows prod laptop** `models/fps_table.json` is only produced by a manual run, and calib2's measured
  imgsz FPS budget (P-6) falls back to estimates. *(Not on dev37 either.)*
- **X3**: three production features shell out to scripts in **`application/tests/`**: ⑤ dry-run → `tests/replay.py` (`app.py:1916`), ④ auto-tune → `tests/calibrate_segment.py` (`app.py:1997`),
  ④ known-N → `tests/known_n.py` (`app.py:2096`).
- **X4**: `/walldance/clear` is never sent live (O5), and the `ResetTracker` command has no UI binding (`gui.py:818` is unused).
- **X5**: phase-rail chips are never updated (M17).
- **X6**: TODO-me "++ Motion detect fallback avant Yolo (pour petits guguss sur fond fixe)" versus ROADMAP §4 "Remove the `TrackingMode` enum" (MOTION_FIRST) → the continuity stream should arbitrate.
- **X7**: `pyproject.toml:37-44` pins torch/torchvision to the **CPU** index (explicit), and `uv.lock` is gitignored, so any `uv sync`/`uv run` without `--no-sync` replaces the installers' CUDA
  wheels. This is the root of the `--no-sync` rule (§9) and a footgun worth fixing in the installer/pyproject.

---

## 4. Anchor map (labels that code comments rely on)

Counts are from `grep` over `application/src` (src) and `application/tests`, `application/*.py`, `launcher`, `extra`, `tmp_analysis` (other). Rule for all rows: **never renumber or
rename these labels. New work gets new prefixes.** The new ROADMAP Appendix A carries this table as "legacy label → home".

| Label family (as cited) | Example sites | ≈# | Must resolve in | Resolves today? | Keep-alive requirement |
|---|---|---|---|---|---|
| `ROADMAP bug #N` / `Bug #N` (#1,2,5,6,8,10,11,12c,14) | `core/motion_model.py:5,27`; `app.py:1015,1384`; `ui/wizard_state.py:143`; `runtime/calibration_flows.py:397`; `core/pipeline.py:342,1418`; `tests/replay.py:168`; `tests/test_transforms.py:1`; `tests/test_tracker_warmup.py:1` | 16 | ER §8 (+ ROADMAP App. A) | ✅ via ROADMAP §6 index | ER keeps the bug table; no renumbering |
| `ROADMAP §4.2 Phase 2 ①–⑧` / `§4.2` / `4.2 2b` | `core/config.py:223,324`; `core/calibration.py:448`; `core/tracker.py:858,3472`; `ui/roi_mask_editor.py:51,396`; `tests/test_tracker_{takeover_merge,max_persons,swap_correctors}.py:1`; `tests/test_calibration.py:261`; `extra/venue_fit.py:12`; `extra/build_engines.bat:29`; `tmp_analysis/phase1_calibrate.py:1` | 15 | ER §5 (Phase 2 ①–⑧, 2b) | ⚠ only via ER header note; ROADMAP has no §4.2 | App. A row "old ROADMAP §4.2 = ER §5"; ER §5 should have ①–⑧ sub-anchors |
| `ROADMAP §3a` / `3b` | `core/config.py:324,800`; `tmp_analysis/aggregate_survey.py:90`; `tests/test_tracker_swap_correctors.py:1` | 4 | ER §3 | ✅ (ROADMAP §2 too) | keep "§3a/§3b" wording in ER §3 |
| `ROADMAP §7B` | `runtime/config_manager.py:403`; `runtime/main_loop.py:217`; `gui.py:1978`; `core/config.py:573`; `tests/test_config_store.py:3` | 5 | ER §7.B | ✅ | — |
| `ROADMAP P0/P1.4/P2/P3/P4`, `P3 Stage 0/1`, `P3 §5` | `core/config.py:416,432,561`; `runtime/main_loop.py:264`; `services/web_monitor.py:4`; `core/motion_model.py:1`; `tests/replay.py:1`; `tests/test_regression_replay.py:1`; `tests/test_motion_model.py:1`; +18 bare "P3", 7 "P1.4" | ~35 | ER §4 (+ `archives/P3_FUSION_SIMPLIFICATION.md` §5 for "P3 §5") | ✅ | — |
| `P-1`, `P-2`, `P-6` (perf items) | `extra/measure_engine_fps.py:4`; `extra/build_engines.sh` tail; 9× "P-6", 3× "P-2", 2× "P-1" in src | 14 | ER §9 | ⚠ P-6 yes; P-1/P-2 only in ROADMAP §4 (struck) | add P-1/P-2 to ER §9 before slimming ROADMAP |
| `ROADMAP §3.2 K1`, `K1`, `K3` | `tests/known_n.py:2`; 6× K3, 3× K1 in src | 10 | new ER §12 (known-N) | ✅ today (ROADMAP §3.2) → ❌ after a slim rewrite unless moved | move the known-N record into ER first |
| `ROADMAP §6` "Untrack committed junk" / launcher safety | `launcher/git_manager.py:27`; `tests/test_launcher_git_manager.py:1` | 2 | **nowhere** | ❌ **orphaned** | add ER §13 "Launcher update safety"; repoint both comments |
| `ROADMAP.md (env / install findings)` | `core/gpu_pipeline.py:76` | 1 | ER §10 | ⚠ | App. A row |
| `OPERATOR_V2 §0.1/§2.1/§2.2/§2.3a/§2.3c/§2.5/§6`, `decision 5`, `Track O/X`, `OPERATOR_V2 P3` | `gui_builder.py:312,589,1546`; `core/sensitivity_macro.py`; `core/config.py`; `runtime/calibration_flows.py:375`; 29 src + 7 other | 36 | `archives/OPERATOR_V2.md` | ✅ | frozen; never edit headings |
| Bare `Track O/X/C/S/P/G` (+ `Track X §2/§7/§B.x`, `Track O §2.1`) | `core/pipeline.py:456,510` (Track P); `runtime/calibration_flows.py:124,337` (Track C); `core/output_smoother.py` (Track X §2); 66 src + 26 other | ~92 | glossary in ROADMAP App. A → OPERATOR_V2 §2–§5 / TRACK_X_SMOOTHER | ⚠ partially (ROADMAP §3 tables) | **don't reuse O/X/C/S/G/D/P for new tracks** |
| `TRACK_X_SMOOTHER.md §N` | `core/output_smoother.py:9`; `tests/test_output_smoother.py:6` (dead `docs/` path) | 2 | `archives/TRACK_X_SMOOTHER.md` | ⚠ path | fix path (DOC-17) |
| `OSC_CONTRACT §B / §B.1 / §B.2` | `core/osc_output.py:34,104,117`; `core/pipeline.py:848,932`; `gui_builder.py:954`; `core/config.py:386` | 12 | `docs/OSC_CONTRACT.md` | ✅ | keep the §B.1/§B.2 numbering in the DOC-8 rewrite |
| `DECOMPOSITION_PLAN §1.2/§4/§5 Phase 2 (1)–(6)/Phase 3/Phase 4/§6` | `runtime/api.py:3`; `ui/adapter.py`; `app.py`; `tests/replay_sweep.py:4` | 25 | `archives/DECOMPOSITION_PLAN.md` | ✅ | ARCHITECTURE.md restates but **does not replace** the file |
| `UX_PLAN U2–U5`, `UX_PLAN §6` | `core/calib2.py:1`; `core/config.py:455,496,533`; `core/sensitivity_macro.py:1` | 14 | `archives/UX_PLAN.md` | ✅ by name | — |
| `KNOBS E1/E2`, `KNOBS.md finding #2` | `core/calibration.py:315,639`; `core/config.py:456,533` | 6 | `archives/KNOBS.md` | ✅ by name | — |
| `TUNING Phase A–F` (A1, A2, B, C, D, E1, F) | `tests/{detect_cache,overlay,replay,scoring,sensitivity,tune}.py` headers; 6 in src | 26 | `docs/TUNING.md` | ✅ | slim TUNING but **keep the Phase A–F headings** |
| `CORPUS_ANALYSIS §5/§6.5/§6.5a/§6.7/§8` | `tests/test_regression_replay.py:23`; `core/config.py`; `core/tracker.py` | 9 | `docs/CORPUS_ANALYSIS.md` | ✅ | don't renumber |
| `AUTOTUNE_DESIGN §7`, `AUTOTUNE gap #2` | 2 sites | 2 | `AUTOTUNE_DESIGN.md` (archives OK) | ✅ by name | keep the filename when moving |
| `G1/G4`, `U2–U5`, `X-2` short labels | 4× G1, 1× G4, U-labels above, 1× X-2 | ~15 | OPERATOR_V2 §5 / UX_PLAN / TRACK_X_SMOOTHER | ✅ | glossary row |
| Bare `Phase 0/1/1c/2/2b/3/4/5/7` | 21× "Phase 2", 16× "Phase 3", 10× "Phase 0" | ~70 | ambiguous: corpus phases (ER §5), decomposition phases, TODO phases | ⚠ | glossary: "Phase N" in core/ = corpus; in runtime/ui/app = decomposition |

---

## 5. Target doc set and actions

### 5.1 Target set (one owner-purpose each)

| Doc | Audience | Owns | Size target |
|---|---|---|---|
| `README.md` | anyone | What it is, hardware, install (launcher + dev), run, where to read next, license/Ultralytics | ≤ 100 lines |
| `docs/MODE_EMPLOI.md` *(new, French)* | **operator** | Everything needed to rig, calibrate, run, record, report and troubleshoot a show | ~400–600 lines + screenshots; PDF export on the laptop desktop |
| `docs/ARCHITECTURE.md` *(new)* | developer / Claude | Structure, seams, frame lifecycle, threads, config, deployment, decisions, debts | ~300 lines |
| `docs/TESTING.md` *(new)* | developer / Claude | Gates (unit / golden / sweep tiers), corpus and scenarios, the recorded-case tuning loop, tool inventory | ~150 lines |
| `docs/ROADMAP.md` | Thomas + dev | **Forward only**: field priorities, tracks, backlog, hardware, decisions; Appendix A legacy anchors | ≤ 200 lines |
| `docs/OSC_CONTRACT.md` | dev + video team | Wire contract | as today, fixed |
| `docs/IR_MARKERS.md` *(renamed from TRACKING_ROBUSTNESS)* | dev + operator (costume) | Ankle/wrist marker design, spike protocol, fusion plan | rewritten by the markers stream |
| `docs/CORPUS_ANALYSIS.md`, `docs/TUNING.md`, `docs/OPTICS.md` | dev | Reference (dated) | unchanged except fixes |
| `docs/archives/ENGINEERING_RECORD.md` | dev (history) | Everything shipped, incl. 06-22→25 and the TODO.md inventory | grows |
| `docs/archives/*` | provenance | frozen | — |
| `CLAUDE.md` *(new, repo root)* | Claude sessions | dev-box rules (§9) | ~60 lines |
| `TODO-me` | Thomas/operator | Raw French wishlist (left as-is; mirrored in ROADMAP §3.3) | — |

### 5.2 Actions

| ID | Action | Inputs / corrections applied | Deletes/moves | Order |
|---|---|---|---|---|
| **DOC-1** | Rewrite `README.md` short: what/why, hardware (link OPTICS), **field install via WallDanceLauncher.exe**, dev install (`install.sh` + `uv run --no-sync`), run, extra tools (incl. oscreplay, venue_fit), docs map, license | R1–R7, R22, R25; drops GUI/file-map/runtime/TRT/OSC/troubleshooting sections (moved to DOC-2/6/8) | — | 7 |
| **DOC-2** | Create `docs/MODE_EMPLOI.md` (French; outline §6) + PDF export | NEW_SHOW (N1–N8), CHECK_TEST 1.1–1.4 + Part 3 (C4), README GUI/projects/troubleshooting (R8–R16, R21, R24), OPTICS venue-fit, launcher prompts, recordings/ISSUE, TODO-me "Desactiver mise à jour auto Windows", slot-loading workaround | — | 4 |
| **DOC-3** | Delete `docs/NEW_SHOW.md` once DOC-2 lands; fix inbound links (`docs/README.md:19,33`; `ROADMAP.md:44,234`; `CHECK_TEST.md:13`) | — | NEW_SHOW.md (no code anchors) | 5 |
| **DOC-4** | Split `docs/CHECK_TEST.md`: operator parts → DOC-2; 1.5 + Part 2 + Reference + cheat-sheet → DOC-7. Commit the baseline sweep script into `application/tests/` (C3). Then delete | C1–C5 | CHECK_TEST.md (no code anchors) | 5 |
| **DOC-5** | Rewrite `docs/ROADMAP.md` forward-only (skeleton §8) with **Appendix A = §4 anchor table + Track-letter glossary**. Move done rows (§3.1 struck, §3.2 Track P/known-N, §4 struck, §5 fixed) to ER (DOC-15). Absorb the TODO.md open items + hardware (DOC-9). Mirror TODO-me as REQ-n (DOC-21) | M1–M17, synthesis content from streams 1–4 | — | 2 |
| **DOC-6** | Create `docs/ARCHITECTURE.md` (outline §7) | README R17–R19; DECOMPOSITION_PLAN outcome; GUI_STACK_AUDIT ADR; `runtime/api.py` docstring rules; X3, X7; launcher policy | — | 3 |
| **DOC-7** | Create `docs/TESTING.md`: unit/CI, golden trio (TRT, engine-locked), `replay_sweep.py` tiers (its docstring), 12 scenarios + pass lines, joint tuning loop (CHECK_TEST Part 2), tool inventory (TUNING §1), soak, OSC verification (O11) | C1–C3, C5, U1, S2 | — | 3 |
| **DOC-8** | Fix `docs/OSC_CONTRACT.md` in place: symbol cites, meta in §A, `/clear` truth, §B retitle (numbers kept), per-L buffering, oscreplay, MAX_PERSONS | O1–O12 | — | 6 |
| **DOC-9** | Dissolve `docs/TODO.md`: open 🟡/⬜ items → ROADMAP backlog; hardware → ROADMAP Hardware; Phases 1–7 shipped inventory → ER §12; then `git mv` to `docs/archives/TODO_2026-06.md` (safer than delete, since archived docs link it) | T1–T9 | TODO.md → archives | 2 |
| **DOC-10** | `git mv docs/TRACKING_ROBUSTNESS.md docs/IR_MARKERS.md`; keep Context/Verdict/Rejected; rewrite placement/fusion/spike/verification for **ankles + wrists** (markers stream); fix inbound links | TR1–TR6 | rename | 6 (after markers stream) |
| **DOC-11** | `git mv` `AUTOTUNE_DESIGN.md` and `GUI_STACK_AUDIT.md` → `docs/archives/` (filenames unchanged, so name anchors survive); fix inbound links; ADR summary into ARCHITECTURE §15 | U2, U5 | 2 moves | 7 |
| **DOC-12** | `docs/TUNING.md`: keep as reference with **Phase A–F headings intact**; fix U1; banner "2026-06-09 plan snapshot"; replace §1 tool table with a pointer to TESTING.md | U1 | — | 8 |
| **DOC-13** | `CORPUS_ANALYSIS.md` / `OPTICS.md`: U3/U4 only | U3, U4 | — | 8 |
| **DOC-14** | Regenerate `docs/README.md` index: Live (MODE_EMPLOI, ROADMAP, ARCHITECTURE, OSC_CONTRACT, IR_MARKERS, TESTING) · Reference (CORPUS_ANALYSIS, TUNING, OPTICS) · Archives (with the "anchored, don't plan from" note) | U6 | — | 7 |
| **DOC-15** | Update `archives/ENGINEERING_RECORD.md`: E1–E4; **add §12 "2026-06-22→25 completions"** (Track C/S fixes, quiet-apply, shims, P-1/P-2, bug cluster, Track P stages, known-N K1/K3/GUI) and **§13 "Launcher update safety"** (re-home the `ROADMAP §6` anchor); add TODO.md Phases 1–7 inventory; give §5 explicit Phase 2 ①–⑧ sub-anchors | E1–E5 | — | **1 (first: gives done content a home)** |
| **DOC-16** | Archives hygiene (optional, low): mechanical link fix (E6); optionally delete `audit_report.md` and `tiling_plan.md` (no anchors; obsolete or rejected) and the empty `prototypes/01-MoveNet/README.md` | E6 | ≤3 deletions | 9 |
| **DOC-17** | Code-side: fix the 15 broken doc paths in comments; repoint `launcher/git_manager.py:27` and `tests/test_launcher_git_manager.py:1` to ER §13; fix the "clean plate" UI strings and docstrings | §3.11 | — | 8 (app-smoke gate) |
| **DOC-18** | `application/tests/scenarios/README.md` | S1–S2 | — | 8 |
| **DOC-19** | `.github/agents/session-analyst.agent.md`, `.gitignore:53` | G1–G2 | — | 8 |
| **DOC-20** | Add repo **`CLAUDE.md`** (draft §9) | X7, launcher policy, gates | — | **1 (now)** |
| **DOC-21** | Keep `TODO-me` in place (Thomas's working file, French). ROADMAP §3.3 mirrors each line as `REQ-n` with status and owner, re-triaged at every roadmap revision. Don't edit TODO-me from Claude sessions | — | — | 2 |
| **DOC-22** | Launcher docs: no separate README. ARCHITECTURE §2 covers build (`launcher/build.bat` + `.spec`) and update policy (UP_TO_DATE/BEHIND/AHEAD/DIVERGED/dirty refusal); MODE_EMPLOI §4 covers the operator prompts | — | — | with DOC-6 |

**Where to apply:** on the prod laptop (where dev now happens) or as a `git format-patch` series for Thomas to apply there. **Do not push from dev37** (§9).
**Gates:** docs-only for everything except DOC-17 (app smoke + unit suite) and DOC-4's committed sweep script (unit suite).

---

## 6. Operator manual outline: `docs/MODE_EMPLOI.md`

**Language recommendation:** **French**, one document, not bilingual (bilingual docs drift and double the upkeep). The GUI is **English**, so every button, label and toast
is quoted **verbatim in bold English** (e.g. **CALIBRATE**, **Calibrate with Dancers**, **Drops <-> Ghosts**), with an FR↔UI glossary in Annex A.
Optionally add a one-page English quick card for visiting technicians. Screenshots should be taken on the prod laptop (Windows DPI). Export the PDF to the laptop desktop.
`docs/archives/LEGACY_pre-project-proposal_fr.md` gives established French vocabulary.

0. **À propos de ce guide**: who it's for, conventions (English UI labels in bold), version/date, who to contact.
1. **Le système en une page**: IR camera → laptop (WallDance) → OSC → TouchDesigner/video; phone monitor; what WallDance sends (position + identity, skeleton) and what it doesn't do.
2. **Matériel et implantation**: IDS camera, 8 mm/6 mm lenses, 850 nm filter, IR projectors, active USB3 cable, laptop. Choosing the camera position (OPTICS venue-fit table +
   `extra/venue_fit.py` by request). Focus. **Even IR** (the #1 lever: raw IR ≈5/255). Placeholder: **IR markers on ankles + wrists** (fitting, care) → chap. 13.
3. **Préparer l'ordinateur (avant la tournée / avant chaque date)**: **pause or disable Windows Update** (TODO-me), mains power + performance mode, sleep off, NVIDIA driver
   frozen (goldens/engines depend on it), firewall UDP 9000, free disk space (FFV1 recordings are huge; readiness warns < 60 GB), engines built.
4. **Démarrer WallDance**: desktop icon (launcher). Update screens: **"Update Available"** → yes/no rule; **"Local Version Differs" → always "Keep Local Version"** unless Thomas says otherwise;
   "update skipped: local changes". Automatic install. **Project picker** (Enter, New, Rename, Delete).
5. **L'écran principal**: top bar (Project, Save/Ctrl+S, chips CAM [IDS]/[CV], OSC, model, **[TRT]/[PT]**, **[CPU FALLBACK]** = stop and call, FPS, GPU, VRAM); **phase rail 1→6** and
   **Advanced**; preview + overlays (S K B T I); alerts strip; **Recordings** bar; stats bar. The red TRT banner + **Rebuild TRT**.
6. **Nouveau spectacle : le rail ①→⑥** (per phase: *vous faites / le système fait / c'est bon quand / pièges*):
   - 6.1 **1 Rig**: Camera combo + refresh, IDS ⚙ (gain/exposure), **IDS Crop Ratio** ±, **Cap Input 20 FPS**; ROI (double-click preview, drag); exclusion mask paint (stays dimmed).
   - 6.2 **2 Profile**: **Show / Rehearsal**; **Config version**; safe defaults (click = load, Ctrl+click = save); **QR** → phone monitor (focus score, lighting, darkest tile).
   - 6.3 **3 Aim**: clear stage → **CALIBRATE** → read the report (brightness/blur/noise, "varThreshold saturated" warning) → fix IR/focus → repeat; "Last calibrated" line.
   - 6.4 **4 Calib**: **Calibrate with Dancers** (1–4 dancers, live or playback), pool review (checkboxes = live preview), **Apply selected** + save; optional **Calculate (auto-tune)** → **Apply seed**;
     **Tune (known-N)** → **Apply tune** (STANDBY, newest recording).
   - 6.5 **5 Verify**: **Check readiness** (ok/warn/fail rows, never blocks); **Dry-run last recording**.
   - 6.6 **6 Live**: **STANDBY/RUN**; **Drops <-> Ghosts** (Dial A), **Gap bridging** (Dial B); **Box-clamp**, **smooth L** + latency readout.
7. **Installation courte (sans répétition)**: Rig → Profile → Aim → RUN, with the dancer pass live as the dancers enter.
8. **Pendant le spectacle**: what to watch (FPS, chips, alerts: FPS drop, no detection, camera down + auto-reconnect, GPU temp, over-cap, height-stale); when to touch which dial; what never to touch live.
9. **Enregistrer et rejouer**: LIVE/REC, slots **1–9**, Ctrl+click history, speed/pause/step; files under `projects/<projet>/recordings/slot_N_AAAAMMJJ_HHMMSS.avi`.
   **Loading an external video into a slot** (TODO-me +++): until the feature ships, copy/rename the file as `slot_N_YYYYMMDD_HHMMSS.mp4|.avi` into that folder. Disk-space rules.
10. **Signaler un problème** (key for the field test): during **playback only**, **ISSUE** or **F8** → click IDs to classify (dancer / swapped / ghost / comment) + note. Where it is saved
    (`sessions/…/issues/*.json`) and how to send a session folder to Thomas.
11. **Projets, profils, sauvegardes**: timestamped versions, safe defaults, last project, what a profile contains, when to recalibrate.
12. **Pour l'équipe vidéo (OSC)**: target IP/port (127.0.0.1:9000 default; Advanced → OSC), the addresses in brief, `meta/latency_ms`, ids grow unbounded; `extra/oscreplay` to record or replay a stream → link OSC_CONTRACT.
13. **Marqueurs IR (chevilles + poignets)**: placement, attachment, checks under IR, what the operator sees. *(Placeholder, filled after the markers stream and the spike.)*
14. **Dépannage**: symptom → likely cause → action (from README troubleshooting + CHECK_TEST on-rig + launcher): no image / IDS stall / CPU FALLBACK / TRT banner / slow FPS /
    losing dancers / ghosts / OSC not received / the update prompt / disk full / GPU hot.
15. **Check-lists imprimables**: installation (J-1), before doors, during the show, end of night (save, copy sessions + recordings, shut down).
- **Annexe A**: FR ↔ UI glossary (Rig, Aim, Calib, Verify, Live, STANDBY, RUN, Drops <-> Ghosts, Gap bridging, Box-clamp, smooth L, Advanced, ISSUE…).
- **Annexe B**: keyboard shortcuts (E S K B T I P, Ctrl+S, Ctrl+Shift+E, F8; *no* Q/R).
- **Annexe C**: folder map on the laptop (projects/, recordings/, sessions/, calib2/, models/) and what may be deleted.

---

## 7. `docs/ARCHITECTURE.md` outline

1. **Purpose and context**: one diagram: IDS/OpenCV camera → WallDance (Win laptop) → OSC/UDP → TouchDesigner; phone MJPEG monitor; launcher/updater; offline tools.
2. **Deployment and environments**: prod (Win 11, RTX 5080, full corpus, show + since 2026-07 a dev host) versus dev37 (Linux, i7-3770K no-AVX2, RTX 3090, partial corpus). Launcher clone/update
   policy and dirty-tree refusal (`launcher/git_manager.py`). `install.{bat,sh}` (gpu/ids extras, CUDA torch ladder cu130→cu124). **`uv run --no-sync`** and why (X7). TRT engines are GPU-specific;
   `fps_table.json` (X2). GPU-only since Track P.
3. **Repository layout**: top-level dirs (application, docs, extra, launcher, models, projects [gitignored], prototypes, tmp_analysis [outputs gitignored]). `application/src` package map, one line
   per module (core 20, runtime 8, ui 4, camera 2, services 1, top-level gui*/app/main). `application/tests` = unit tests **plus tools** (replay, tune, known_n, soak…).
4. **Layering and seams**: `app.py` = composition root; `core/` imports neither runtime nor ui; `runtime/api.py` commands (queued, drained once per tick) and events (EventBus); `ui/adapter.py`
   is the only DPG↔API translator (rules verified by grep 2026-10-05); the three documented off-bus exceptions (TRT prompt, render_frame pump, layout query).
5. **Frame lifecycle (one tick)**: camera read (IDS GPU-direct `read_gpu` / OpenCV) → `GpuPipeline` (enhance, letterbox) → YOLO (TRT/PT, FP16) ∥ motion-feed worker (gamma-only
   gray → MOG2 + frame-diff) → `_post_yolo_chain` (crossval gate θ_s/θ_m, exclusion, bridge, `tracker.update`, MAX_PERSONS) → `_unscale_letterbox` (+box-clamp) → L=1 EMA / L>1
   `OutputSmoother` → `OSCSender`; preview path; STANDBY path (CPU enhancer, no YOLO/OSC).
6. **Tracking model**: `DancerTrack` Kalman, cascaded Hungarian, gates (Mahalanobis 16.27, displacement 0.5, close 0.35), warmup confirm + intermittent switch, dormant pool, takeover merge,
   swap correctors off by default, `_frames_since_skeleton`; **where continuity is decided** (hook for CONT).
7. **Motion subsystem**: `MotionModel`, frame-diff as ghost killer, bridge tiers, `TrackingMode` vestige, `MotionModel.detector` shim.
8. **Calibration**: Aim (servo, gamma cap, CLAHE seed, var×scale sweep), Calib2 pool (height, imgsz + K3 probe, confidence, blur budget), sensitivity macro (Dial A/B), auto-tune and known-N
   subprocesses, `calibration_state` provenance; what is *not* built (clean plate, C-next).
9. **Configuration**: `core/config.py` constants; schema v2 (shared keys + show/rehearsal profiles, migration); `config_store` versions, safe defaults, last project; `projects/<p>/` layout
   (configs, recordings + `.meta`, sessions, calib2, issues).
10. **Output boundary**: OSC (link OSC_CONTRACT); web monitor.
11. **Ops and observability**: readiness checks, HealthMonitor, LoopWatchdog, camera recovery, `tracking_logger` JSONL sessions, issue reports, `analyze_session.py`/`analyze_log.py`.
12. **Threads and processes**: table (main loop, motion worker, IDS acquisition, recorder encoder/decoder, watchdog, web server, readiness threads, model-load threads, subprocess tools).
13. **Testing and gates**: summary + link to TESTING.md.
14. **Tools**: `extra/*` (build_engines, measure_engine_fps, gpu_limiter, venue_fit, oscreplay), `application/*.py` analyzers, `tmp_analysis/*`.
15. **Decisions (ADR-lite, dated)**: stay Python/DPG (GUI_STACK_AUDIT 06-11); GPU-only (Track P 06-24); single OSC stream selected by L (06-15); manual exclusion (decision 5); swap correctors off
    (Phase 2 ⑧); no on-site training; known-N as offline subprocess.
16. **Hotspots and known debts**: file sizes (tracker 3.6k, gui 2.9k, app 2.2k, ids_camera 2.0k, pipeline 1.8k, gui_builder 1.6k lines); production features in `tests/` (X3); enhancer dual role; TrackingMode;
    slot 9/10 (X1); unbound reset (X4) → owned by ROADMAP ARCH/PERF items.
17. **Legacy label glossary**: pointer to ROADMAP Appendix A.

---

## 8. New ROADMAP skeleton (sections only; the synthesis fills them)

```
# WallDance Roadmap
Date · Status: forward work only. History → archives/ENGINEERING_RECORD.md · Index → README.md
ID prefixes: CONT (continuity) · MARK (IR markers) · ARCH · PERF · OPS (field operations) · REQ (operator requests, from TODO-me)
             · CAL (calibration engine) · DOC · HW.  Never reuse the legacy letters O/X/C/S/G/D/P (Appendix A).

## 0. North star + field-test context (2026-10)
## 1. Status snapshot (≤10 rows: shipped subsystems → ENGINEERING_RECORD anchors)
## 2. How work flows (field feedback → triage → gate)
   2.1 Inputs: ISSUE/F8 reports + session logs from the laptop · TODO-me · Thomas
   2.2 Gates: replay-gated vs app-smoke vs cross-lane go (→ TESTING.md)
   2.3 Where code lands: prod laptop; dev boxes never push (→ CLAUDE.md)
## 3. NOW (field-test critical)
   3.1 CONT — continuity / centroid drops            (stream 1)
   3.2 MARK — IR markers on ankles + wrists          (stream 2)
   3.3 REQ  — operator requests (TODO-me mirror: video slots, mode d'emploi, track-merge issue, motion fallback, mirror/rotate, Windows Update, menu cleanup)
   3.4 OPS  — field operations hygiene (laptop git state, Windows Update, disk, engines/fps_table)
   3.5 DOC  — this consolidation (DOC-1…DOC-22)
## 4. NEXT
   4.1 ARCH — architecture review outcomes        (stream 3)
   4.2 PERF — performance review outcomes         (stream 4)
   4.3 CAL  — unified calibration engine (C-next), known-N/K3 on-rig validation
   4.4 OPS  — logging & diagnostics (per-show folder, CSV metrics, Go-Live snapshot, end-of-show summary)
## 5. LATER (research-gated, go/no-go before code)
   5.1 Corpus-trained IR detector · 5.2 Detection-quality leads (ex Track D list) · 5.3 Output (X-4 resample, OSC status/heartbeat, bundling P-5) · 5.4 UI/packaging
## 6. Simplification backlog (TrackingMode — see REQ motion-fallback conflict · detector shim · dead knobs · tools out of tests/)
## 7. Hardware & procurement (HW-n; from TODO.md)
## 8. Open decisions (dated; question → owner → due)
## Appendix A. Legacy labels → homes (the §4 anchor table + Track-letter glossary)
## Appendix B. Doc map
```

---

## 9. Draft repo `CLAUDE.md` (root)

```
# WallDance — rules for Claude sessions

## What this is
Live multi-dancer IR pose tracking (YOLO-pose + Kalman/Hungarian + motion) → OSC → TouchDesigner, for Tango Nomade.
In FIELD TEST since 2026-10 with a French-speaking, non-developer operator. Read docs/ARCHITECTURE.md first;
forward work = docs/ROADMAP.md; history = docs/archives/ENGINEERING_RECORD.md; operator = docs/MODE_EMPLOI.md (FR).

## Machines
- prod: Windows 11, ASUS ROG Strix SCAR 16, RTX 5080 — the show laptop AND a dev host (since 2026-07).
  Holds the full corpus (projects/, gitignored), tmp_analysis outputs, CORPUS_NOTES.md, TRT engines.
- dev37: Linux, i7-3770K (NO AVX2) + RTX 3090 — partial corpus only. kornia_rs is stubbed in core/gpu_pipeline.py
  (AVX2 wheel → SIGILL); never remove the stub. Native crash = check SIGILL first.

## Git — NEVER push from a dev box (Thomas, 2026-10-05)
origin = github.com/Hemisphere-Project/WallDance (shared upstream). The laptop's launcher fetches origin/main at every
start and offers "Update" (behind) or "Discard and Update" (diverged → destroys the laptop's local commits).
So from dev37: no push, no tags, no branches on origin. Commit locally; hand Thomas `git format-patch`/`git bundle`.
(This overrides the global "pushing the current branch is normal" rule for this repo.)

## Python env
Always `uv run --no-sync …` from application/. pyproject pins torch/torchvision to the CPU index and uv.lock is
gitignored: `uv sync`, `uv add` or a plain `uv run` replaces the CUDA wheels the installers put in → the app cannot
run (GPU-only since Track P). To change deps: edit pyproject, then re-run install.sh / install.bat.

## Gates (smallest the diff can fail — tests/replay_sweep.py docstring)
- docs / ui / gui*: unit suite only — `uv run --no-sync python -m pytest -q` (~7 s).
- runtime/ or app.py: + golden trio — `WD_RUN_REPLAY=1 uv run --no-sync python -m pytest tests/test_regression_replay.py -v`
  (GPU+TRT; goldens are engine/driver-locked: byte-identical only on the baselining machine, tolerance elsewhere).
- core/ or config defaults: full 12-scenario sweep (needs the corpus → prod laptop).
- Behaviour changes are replay-gated; cross-lane (calibration engine, tracker core, OSC contract) need Thomas's go.
- Any change to /walldance/* = explicit, operator-confirmed edit of docs/OSC_CONTRACT.md.

## Data
projects/ is user data (gitignored). Scenario manifests pin config + recording fingerprint (hard-fail on mismatch).
Quick test recs: residence1-solo slots 3 & 4 (= hangar-floor / hangar-aerial).

## Docs & anchors
Code comments cite doc labels (ROADMAP bug #N, P3, §4.2 Phase 2 ②, Track X, OPERATOR_V2 §2.2,
DECOMPOSITION_PLAN Phase 3, TUNING Phase B, CORPUS_ANALYSIS §5, OSC_CONTRACT §B.1 …). Never renumber/rename
them; new work uses new prefixes (ROADMAP Appendix A). In docs, cite symbols, not line numbers or test counts.

## 37brain
Commit trailer: `Refs-37: <slug>#t-NNN [done]` (slug: ask Thomas). Never write the hub from here.
```

---

## 10. Questions for Thomas

1. **Manual language:** French-only with English UI labels quoted plus a glossary (recommended)? Or bilingual? Or should the operator-facing GUI labels themselves be translated to French (a code change; Track-O panels only)?
2. **Manual format and delivery:** markdown in the repo + PDF on the laptop desktop (recommended)? Printed copy? Who is the operator and what do they do: rigging and calibration, or only run and monitor?
3. **Prod laptop git state:** does it hold commits after `a49d0f2`? Should the doc consolidation be applied there, or delivered as a patch series? (Nothing gets pushed from dev37.)
4. **Repo visibility:** is `Hemisphere-Project/WallDance` public? Is a committed `CLAUDE.md` with these rules OK there, or should it be local-only (`CLAUDE.local.md`, gitignored)? What is the 37brain slug?
5. **IR markers:** was Phase 0a (the physical spike) run? What drove ankles+wrists over the harness? Are there recordings with markers? (Needed to rewrite IR_MARKERS.)
6. **Clean plate:** the GUI and docs say Aim "captures the clean plate", but the code only resets MOG2. Fix the text (recommended) or build the plate (C-next)?
7. **OSC with markers:** if wrists/ankles become marker-anchored, do they become load-bearing for the video team, i.e. an OSC_CONTRACT change? Should `/walldance/clear` be sent on STANDBY/stop, given it is never sent live today?
8. **TODO-me:** keep it as your free-form file at the root (mirrored in ROADMAP as REQ-n), or move it into `docs/`?
9. **Hardware:** were the IP66 housing and the mounting bought? Which lens is in use in the field (8 mm or 6 mm)? Are new IR projectors planned (P1.3)?
10. **Deletions:** OK to delete NEW_SHOW.md and CHECK_TEST.md after the merge, and optionally `archives/audit_report.md`, `archives/tiling_plan.md` and the empty `prototypes/01-MoveNet/README.md`?
11. **"Motion detect fallback avant Yolo"** versus the plan to delete `TrackingMode`: should the motion-first fallback come back as a feature (which makes the enum deletion moot)?
