# Corpus analysis — settings, strategy & robustness report

**Date:** 2026-06-10 · **Status:** AGREED (operator-ratified 2026-06-10) — decisions folded into ROADMAP §4.2; **Phase 0 (corpus re-founding) executed** same day: pinned-config scenario schema + fingerprints + pass lines landed, golden trio (`hangar-floor`, `hangar-aerial`, `texture-aerial`) regenerated and green, 10 manifests + 2 drafts committed, GT sheets in `tmp_analysis/gt_sheets/` awaiting the operator pass.
**Ratified decisions:** golden trio as above (whitebg2 s3/s4 + whitebg s7) · §8 pass lines accepted as-is (refine later) · `0-TEST-phones` stays corpus-only · whitebg slots 8/9 recovered and **moved to texturedbg** · §6 sequence approved.
**Errata vs the reviewed draft:** the slot-3 "footage mapping broken" suspicion (§1.2/§2) was **wrong** — the committed goldens record the exact filenames (`slot_3_20260402_205941.avi` / `slot_4_20260402_210914.avi` = whitebg2 slots 3/4, fps sidecars match), and a closer brightened look at frames 1500–1799 is consistent with the verified floor-dancer GT (the earlier montage was misread). Only the **config** was lost — which the pinned-config schema now prevents structurally.
**Inputs:** [projects/CORPUS_NOTES.md](../projects/CORPUS_NOTES.md) (operator annotations, 38 slots / 10 projects) · the full local recording set (~50 GB) · the existing tooling (replay harness, scoring, SceneCalibrator/calib2, sensitivity/tune)
**Method:** two measurement layers run on the RTX 5080 laptop (this is the first time the whole loop ran off the Linux dev box — it works; full corpus pass ≈ 35 min survey + ≈ 40 min replays):

1. **Survey layer (all 38 annotated slots)** — `tmp_analysis/corpus_survey.py`: per slot, a calib1-style scene pass (`SceneCalibrator`: brightness, clip, uniformity, focus, temporal noise σ, the joint var×scale FP sweep) on a 90-frame block, plus a low-floor YOLO pass (`yolo11x-pose @1280`, conf floor 0.05) over 32 sampled frames — **raw and auto-enhanced** (per-scene auto-gamma + CLAHE 2.5) — recording every candidate box (conf, h, keypoint stats, position). From this: per-scene confidence operating curves vs annotated N, height distributions, calib2 `select_imgsz` suggestions, ghost-vs-duplicate classification of over-counts, and a one-threshold **separability margin**. Brightened 4-frame montages per slot for visual verification.
2. **Replay layer (12 key windows)** — `tmp_analysis/run_replays.py`: full-pipeline replays (real `process()` CPU path) of 300–400-frame windows, **(a)** with each project's *current saved config* (what an operator gets today) and **(b)** with a *calibrated* config derived from the survey (measured height/ratios, auto-gamma, var 8 / scale 0.7, per-scene confidence). Scored with `scoring.py` against the annotated N (provisional ground truth).

All artifacts are under `tmp_analysis/` (survey JSONs + montages, replay summaries + detailed timelines) — kept out of git, regenerable.

---

## 1. Executive summary

1. **The corpus is exactly the footage TUNING.md asked for** — it covers multi-dancer, aerial/inverted poses, small-far, ghost-heavy texture, outdoor day/night, blur, and moving-camera stress. With ~10 verified scenario manifests this replaces the single-clip (slot-4) basis of every numeric conclusion in KNOBS.md.
2. **The regression baseline was broken.** The goldens' project (`residence1-solo`) no longer exists: replay silently fell back to a **default config** (metrics far off golden), and the tuned config that produced the goldens was never committed. *(The draft also suspected the slot-3 footage mapping — refuted, see errata above; the files are the same, only the config was lost.)* → **Done 2026-06-10:** corpus linkage re-established with a frozen config snapshot pinned inside each scenario manifest + recording fingerprints + loud-fail lookup (§5).
3. **Today's saved configs fail catastrophically on their own scenes.** Replays with current project configs: drop-rate **1.0** (zero frames reported) on the aerial regression scene and on whitebg3-duo; 0.5–0.66 on four more. Root causes are mundane: a bulk-copied config (`person_height_px=56` on 170–340 px dancers → the height gate rejects everything; 4 projects share one config), `gamma 0.5` (a *darkening* LUT) on 5/255-brightness scenes, `confidence 0.49–0.58` on scenes whose real dancers sit at conf 0.15–0.4. **This is the setup-time pain measured end-to-end** — and all of it is what Calib1/Calib2 are designed to set.
4. **Calibrated settings recover the gross failures but are not sufficient alone.** The same windows with survey-derived settings: aerial scene drop 1.0 → **0.13**; whitebg3-duo drop 1.0 → 0.27; testflou drop 0.59 → **0.0**; the TOGO sitter scene 0.50 → **0.075** (the static sitter acquires a track once enhancement + imgsz give it a skeleton) (§4). But on ghost-heavy scenes a YOLO-level threshold pick backfires at pipeline level (ghost churn: texturedbg 0.39 → 0.79), TOGO-night trades drops for fixed-spot ghosts, and the facade ghost flood is untouched — the **known-N calibration must optimise through the full pipeline** (= `tune.py`), and the **exclusion mask is the missing half**: measured ghosts are overwhelmingly *fixed scene spots* repeated across slots and even across projects (§3.3).
5. **Enhancement is a per-scene decision with big stakes.** Scene-adaptive brightening turns the aerial scene's raw YOLO coverage 0.13 → **0.97**, and is *required* at TOGO-night (0.0 → 0.56) — but it *hurts* on noisy-dark scenes (verydark: 0.47 → 0.25) and inflates fixed-spot ghosts everywhere (2–4×). The calib2 plan (gamma/CLAHE sweep maximising person confidence) is the right rule; a luma-target rule is not. Pair brightening with the exclusion mask and a raised threshold.
6. **Two structural drop amplifiers found in the tracker layer:** the warmup confirmation math (+1/hit, −0.8/miss) can **never confirm** a track when detection rate < ~45% — on hard scenes the dancer is detected a quarter of frames yet *permanently* unreported; and a **static person never acquires a track** (TOGO balcony sitter: one distinct track id over 400 frames — the walking dancer; the sitter never passes θ_s and has no frame-diff motion). Both have concrete fixes (§6).
7. **Identity machinery is a non-issue on this corpus** — swaps were 0–1 in every current-config replay (drops/ghosts dwarf them), supporting ROADMAP §3a: relax the slot-7 swap correctors; spend nothing more there.

---

## 2. Corpus assessment (per project)

Survey table (key columns; full data in `tmp_analysis/survey/survey_summary.json`). *cov/over* = fraction of sampled frames with ≥N / >N detections at conf 0.25; *best-τ* = threshold minimising |count−N|; *h med* = median detected height (px) at conf ≥0.25; *var/scale* = what the calib1 FP sweep picks on the scene (raw-gray feed); *N* = annotated dancer count.

| project / slot | N | bright /255 | noise σ | var/scale picked | raw@.25 cov/over | enh@.25 cov/over | best-τ raw→enh | h med (p5–p95) | imgsz sugg. |
|---|---|---|---|---|---|---|---|---|---|
| cantine s1 | 0–2 | 22 | 4.3 | 8/0.7 | mean 1.3 | mean 1.8 | – | 996 (587–1140) | 640 |
| phones s1 | 4 | 131 | 9.9 | 56/0.7 | **0.06**/0 | 0.19/0 | 0.15→0.15 | 102 (71–126) | **1920, unmet** |
| phones s2 | 3 | 132 | 0.3 | 8/0.7 | **0.00**/0 | 0.00/0 | – | – | – |
| phones s5/s6 (moving cam) | 6 | 115–130 | 3–26 | **saturated 120** | **0.00**/0 | 0.00/0 | – | – | – |
| verydark s1–s3 | 1–2 | **1.8–13** | 0.4–13.6 | 8/0.7 | 0.47–0.78/≤.09 | **worse** (0.19–0.75) | 0.15–0.35 | 552–794 | 640 |
| verydark s4–s6 | 1–4 | 2–5 | 0.3–0.7 | 8/0.7 | mean 0.4–1.3 | mean 0.1–1.2 | – | 315–631 | 640–800 |
| texturedbg s1 | 1 | 36 | 3.3 | 8/0.7 | 0.97/**0.34** | 1.0/**0.56** | 0.50→0.65 | 281 (183–435) | 800 |
| texturedbg s3 | 1 | 86 | 2.0 | 8/0.7 | 1.0/0.13 | 0.94/0.22 | 0.35→0.25 | 257 | 800 |
| texturedbg s4 (wall-hang) | 1 | 85 | 2.1 | 8/0.7 | 0.56/0.13 | 0.59/0.22 | 0.15→0.15 | 394 (156–522) | 640 |
| texturedbg s5 (duo) | 2 | 82 | 2.1 | 8/0.7 | 0.34/0.06 | 0.41/0.19 | 0.15→0.15 | 330 | 640 |
| whitebg s1/s4 (walkers) | 1 | 39–53 | 1.5–1.8 | 8/0.7 | 1.0/0 | 1.0/≤.16 | 0.25 | 342–348 | 640 |
| whitebg s6–s9 (evening) | 1 | **14–48** | 1.0–2.0 | 8/0.7 | 0.47–1.0/≤.59 | ↑cov, ↑over | 0.15–0.5 | 273–522 | 640 |
| whitebg2 s1–s3, s5 | 1 | **5–12** | 0.7–0.9 | 8/0.7 | 0.78–0.97/0 | 1.0/0.19–0.66 | 0.15→0.5–0.65 | 241–342 | 640–800 |
| whitebg2 s4 (aerial swing) | 1 | **5** | 0.7 | 8/0.7 | **0.13**/0 | **0.97**/0.44 | 0.15→0.5 | 190 (99–309) | 960 |
| whitebg3 s1 | 1 | 26 | 1.2 | 8/0.7 | 0.84/0 | 1.0/0.22 | 0.15→0.35 | 251 | 800 |
| whitebg3 s2 (duo) | 2 | 26 | 1.2 | 8/0.7 | 0.41/0 | 0.75/0.16 | 0.15→0.25 | 206 | 960 |
| whitebg3 s3 (4–5 walkers) | 4–5 | 28 | 1.3 | 8/0.7 | mean 4.2 | mean 4.2 | – | 341 | 640 |
| testflou s4 (2 static, blur) | 2 | 40 | 1.2 | 8/0.7 | **0.00**/0 | 0.06/0 | – | 320 | 640 |
| testflou s5 (walker, 7.4 fps) | 1 | 38 | 1.2 | 8/0.7 | 1.0/0.03 | 1.0/0.56 | 0.35→0.65 | 198 | 960 |
| testflou s6 (running, blur) | 1(2!) | 21 | 0.9 | 8/0.7 | 1.0/**0.72** | 1.0/0.78 | 0.65→0.5 | 170 | 1280 |
| TOGO-night s1 | 1 | **1.4** | 0.4 | 8/0.7 | 0.81/0 | 1.0/0.22 | 0.15→0.5 | 139 | 1280 |
| TOGO-night s2 (wall-hang) | 1 | **1.1** | 0.4 | 8/0.7 | **0.00**/0 | **0.56**/0.09 | 0.15→0.25 | 509 | 640 |
| TOGO-day s5/s6/s9 | 2 | 26–98 | 1.2–2.1 | 8/0.7 | 0.84–1.0/0.03–0.19 | 1.0/0.59–0.66 | 0.25–0.5→0.65 | 122–131 | **1536** |
| TOGO-day s8 (bystanders) | 2* | 90 | 2.0 | 8/0.7 | 0.94/**0.59** | 0.94/0.69 | 0.5→0.5 | 121 (34–177) | 1536 |

### Annotation corrections needed (visually verified on montages)

| Where | Finding | Action |
|---|---|---|
| `2_TANGO_HANGAR-whitebg` slots 6–9 | The background in these slots is **still the textured/stained wall** (same wall as project 1's montages) — the white paper isn't there yet. Explains their high ghost pressure (s8: a fixed conf-0.5 ghost at (1278,1008) h≈520 in 19/20 over-count frames). | ✅ Resolved 2026-06-10: slots 8/9 moved to `texturedbg`; 6/7 stay in whitebg with a "still textured" annotation in CORPUS_NOTES (slot 7 = the `texture-aerial` golden) |
| `3_TANGO_HANGAR-whitebg2` slot 3 | ~~Footage doesn't match the committed slot-3 GT~~ — **RETRACTED** (see errata in the header): the goldens record the exact filenames, fps sidecars match, and the closer brightened look is consistent with the verified floor-dancer GT. | None — provenance confirmed; GT carried over into `hangar-floor` |
| `5_TANGO_HANGAR-testflou` slot 6 | A **second real person** (white-clad assistant standing by the left equipment) is visible — the "fixed-spot detections" there are them, not ghosts. | N=2 at YOLO level (or define a stage ROI that excludes them); same check needed on s4/s5 |
| `7_TANGO_TOGO-day` slot 8 (likely s5–s9) | **Bystanders/spectators** at the bottom edge (heads at y≈1500, h 30–70 px) and people in the archway are real detections beyond the annotated N=2. | Either count all visible people in N, or fix a stage ROI per scene and count inside it. ROI discipline is part of ground truth |
| `0-TEST-verydark` s4–s6, `whitebg3` s3 | "up to N" annotations can't be scored — drops and ghosts are indistinguishable without per-range counts. | Add per-frame-range `expected_count` labels (the scenario schema already supports it) for one window each |

### Coverage gaps the corpus still has

- **No IDS+Starvis2+even-IR session** — the rig the product is converging to (ROADMAP P1.3). Everything here is the old 65 W single-spot regime (brightness 1–35/255 indoors). The quiet-scene frame-diff regime (bug #4's cap) stays unexercised.
- **No close-interaction multi-dancer tango** (the swap stress case). texturedbg s5 has a duo *moving together* — good seed, but a dedicated contact-improv / tango-embrace recording would close it.
- **No 4 h soak footage** (ops cluster) — different tool, noted for completeness.

---

## 3. Scene physics — what the calibration tools measured

### 3.1 The var×scale sweep result is universal (and surprising)
On **every fixed-camera scene** (35/38 slots), the empirical FP sweep picks **var 8 @ scale 0.7** — the most sensitive candidate pair — with background FP well under target (typ. 0.005–0.07 %). The shipped default (40) and the bulk configs (40 @ 0.99/0.75) are far off the evidence everywhere. The two exceptions are exactly right: moving-camera phone clips saturate to 120 (everything is foreground — correctly flagged `saturated`), and phones-s1's tree foliage pushes to 56. **Conclusions:** (a) the P2/Calib1 sweep generalises corpus-wide, exactly as designed; (b) `var=8, scale=0.7` is the right *seed/floor* for the sensitivity macro's loose end (KNOBS already proposed this from one clip — now corpus-confirmed); (c) a `saturated` sweep is a reliable "scene unusable for motion" detector (moving rig, wind-dominated) → surface it as a calibration warning.

### 3.2 Enhancement: per-scene, evidence-driven, paired with exclusion
Measured effect of scene-adaptive brightening (auto-gamma to median 110 + CLAHE 2.5) on YOLO:

| Regime | Example | Raw → enhanced coverage@.25 | Verdict |
|---|---|---|---|
| Quiet near-black (noise σ < ~1) | whitebg2 s4 aerial | **0.13 → 0.97** | Transformative — this *is* the recorded slot-4 drop pain |
| | TOGO-night s2 | **0.00 → 0.56** | Required to see the dancer at all |
| Noisy near-black (σ ≥ ~4 or extreme dark) | verydark s1/s3/s4 | 0.47 → 0.25 / 0.53 → 0.19 / 0.38 → 0.12 | **Harmful** — amplified noise confuses YOLO |
| Already-lit | TOGO-day, phones | cov already ~1.0; over-count ×2–4 | No coverage gain, pure ghost inflation |

Enhanced passes also lift real-dancer confidence dramatically (whitebg2 s5 real-conf p10 0.16 → 0.64; TOGO-day s9 0.27 → 0.80), which is what lets the threshold rise. **The calib2 design (sweep gamma/CLAHE to maximise person confidence on pooled frames) is validated — a luma-target rule is refuted** (it would brighten verydark into failure). Two amendments: add a noise-σ guard/penalty to the sweep, and treat the resulting fixed-spot ghost inflation as expected — the exclusion mask (§3.3) is the designed counterpart. (Reminder: this is the *YOLO input* path only; the motion feed stays gamma-only per bug #1.)

### 3.3 Ghosts are fixed scene spots → the exclusion mask is the right weapon
Classifying every over-count detection (duplicate-on-dancer vs background ghost) and logging positions: **background ghosts dominate (60–95 % of over-counts on ghost-prone scenes) and they sit at a handful of fixed positions, stable across slots and even across projects** sharing a venue:

- whitebg2 *and* whitebg3 (same hangar): three recurring spots — (1225,519) h≈85, (873,487) h≈152, (833,145) h≈185 — residual wall stains / rig features;
- texturedbg: (413,1024) h≈183 and (1124,856) h≈430 — the wall-stain figures the operator remembers fighting;
- whitebg s8: (1278,1008) h≈520 in 19/20 ghost frames;
- TOGO-day: (1308,1110) h≈186 + the bystander strip at the bottom edge.

A 16×10 exclusion grid trivially covers these (spot footprints 80–520 px). **This is the ghost-heavy validation P1.4 was waiting for.** Recommendations: build the mask during Calib1 on every setup; add the manual mask editor (TODO Phase 9) since some "ghosts" are *people who shouldn't count* (bystander zones); persist per lighting profile (shadow spots move with light — already the U2 design).

### 3.4 One confidence threshold per scene — sometimes not even that
Separability margin (p10 of real-det conf − p90 of ghost-det conf, raw pass): comfortably positive on clean scenes (whitebg walkers +0.6…+0.75, TOGO-day s5/s6 +0.5, verydark s2 +0.7), **negative or ~0 on the hard third** (texturedbg s5 −0.13, whitebg s8/s9 −0.06/−0.08, whitebg3 s2 −0.10, testflou s4/s6, phones s1 ≈0). Per-scene best-τ spans **0.15–0.65** across the corpus. So: (a) a global confidence default is impossible — *measured*, closing ROADMAP §12 Q1; (b) the sensitivity macro must span the full 0.15–0.65 range around a per-scene seed; (c) on negative-margin scenes no threshold works alone — those need the spatial gate (exclusion + frame-diff + tracker context), confirming ROADMAP §3b's decomposition.

### 3.5 Small-far and pose regimes are YOLO's hard floor
- phones s1 (102 px people @1920-wide frame): coverage 0.06 raw — `select_imgsz` correctly answers 1920 *and flags it insufficient*. phones s2/s5/s6: ~0 coverage (20–40 px people; aerial crumpled poses). The 110 px net-height target is corpus-confirmed as roughly the cliff edge: TOGO-day at 960 ⇒ ~78 px net → drops; suggested 1536 fixes the math.
- Aerial inverted/spread poses depress confidence ~2–4× vs standing even when large (whitebg2 s4 raw vs s3 raw) — pose, not size: best handled by enhancement + per-scene threshold, not by imgsz.
- Heavy defocus (testflou s4): two *static* blurred people = 0 coverage; the same blur with *motion* (s5/s6) detects fine. Focus discipline (P0 monitor) matters most for static subjects.

---

## 4. Replay layer — current configs vs calibrated configs (full pipeline)

Field-priority score (lower better; drop+ghost dominated), 300–400-frame windows, provisional N from CORPUS_NOTES:

| Scene (window) | N | Current project config | Calibrated (survey-derived) | Reading |
|---|---|---|---|---|
| whitebg2 s3 @1500 | 1 | **0.072** (drop .07, ghost 0) | 0.252 (drop .14, ghost .02, ids 5) | Current conf 0.49 already fits this scene; naive brightening+conf 0.5 caused track churn — calibration must not over-fire on already-good scenes |
| whitebg2 s4 aerial @1500 | 1 | **1.001 — zero frames reported** (drop 1.0) | **0.222** (drop .13) | The headline recovery: gamma 2.2 + measured height + τ 0.5 |
| texturedbg s4 wall-hang | 1 | 0.388 (drop .36) | 0.794 (drop .32, **ghost .34**) | τ 0.15 from YOLO-level pick floods texture ghosts through — known-N must optimise *through the pipeline* (tune.py), plus exclusion |
| texturedbg s5 duo | 2 | 0.747 (drop .66, 6 ids) | 0.616 (drop .39, ghost .11) | Net better; still needs exclusion + joint search |
| whitebg3 s2 duo | 2 | **1.001 — zero reported** (h=56 gate kills all) | 0.809 (drop .27, ghost .43) | Gross failure fixed; ghost half needs exclusion/tune |
| whitebg3 s3 4–5 walkers | 4–5 | avg det **0.09** (h-gate) | avg det **3.95**, 0 zero-det frames | Massive recovery (no score — needs per-range N) |
| testflou s6 runner | 1(2) | 0.594 (drop .59) | 0.585 (drop **0.0**, "ghost" .53) | The "ghost" is the second real person — with corrected N=2 this scores ≈ 0.03 |
| TOGO-night s1 | 1 | 0.291 (drop .29) | 0.439 (drop .13, **ghost .21**) | Drops halved, fixed-spot ghosts admitted — the exclusion-mask case (§6.4) |
| TOGO-day s9 walker+sitter | 2 | 0.501 (drop .50 — **sitter never tracked**, 1 id total) | **0.075** (drop .07, ghost 0, 2 ids) | Enhancement + imgsz 1280 gave the sitter a real skeleton → tracked. Calibration alone fixed this instance; §6.2 remains the robustness layer |
| TOGO-day s8 (+bystanders) | 2* | 0.350 (ghost .27 = the bystanders; GT unfair) | – | Needs ROI-based GT |
| verydark s4 (N varies) | 1–4 | avg det 0.24, 306/400 zero-det (h=155 gate vs 480–920 px people) | – | Needs per-range labels |
| phones s1 facade | 4 | 1.892 (**ghost 1.73**, 33 ids, 34k gate rejections) | 1.895 (unchanged, 48 ids) | Threshold/height moves don't touch a facade ghost flood — only exclusion + MAX_PERSONS (§6.8) do |

**Failure mechanics identified (all reproducible in the timelines):**
- **Stale `person_height_px` is lethal** — it gates detections (min/max ratio) *and* scales every tracker distance. One bulk-copied config (h=56, conf 0.14, `motion_first`, one ROI) is live on 4 projects; on their own scenes it reports ~nothing.
- **Saved gamma 0.5/0.53 on 5–35/255 scenes is a darkening LUT** at the YOLO input (enhancer semantics: >1 brightens). Whether operator error or a UI semantics trap, a calibration-time sanity ("scene is near-black and gamma <1 — really?") would catch it.
- **Warmup confirmation math** (+1 hit / −0.8 miss, threshold 15, bridge hits +0.4): detection rate <~45 % ⇒ score can never reach 15 ⇒ **detected-but-never-reported forever** (wb2_s4: avg 0.25 detections/frame, 0 reported frames). See §6.1.
- **Static people never acquire a track** (TOGO sitter): θ_s needs 8 kpts @0.45 (seated/occluded fails), frame-diff shows nothing, live-track bypass only helps *existing* tracks. See §6.2.
- Track churn under multi-detection (calibrated runs: 4–10 ids for 1–2 dancers) — duplicate/new-track + warmup interaction; swaps stay irrelevant (0–1 everywhere with current configs).

---

## 5. The regression corpus — re-founding it (✅ executed 2026-06-10, GT pass pending)

**Why from scratch:** the old `residence1-solo` goldens were orphaned (config never committed; project deleted in the reorganisation). The survey + montages here already provide the visual-verification raw material.

**As built:** schema + loud-fail + fingerprints landed (`replay.scenario_config`, `check_fingerprint`, `scoring.evaluate_pass`; consumed by replay/tune/overlay/detect_cache); goldens regenerated for the trio and the regression test reads scenarios directly (`test_regression_replay.py`, 3/3 green, run-to-run reproducible); 12 manifests committed; unit suite green. **Operator GT pass completed 2026-06-10** — all 12 manifests verified (constant-N confirmed; per-range labels landed for `blur-runner` 1→2 @rel 180, `dark-crowd` 1/2/1, `white-walkers` 5/4 alternating; both ex-drafts promoted into `scenarios/`). **Phase 0 closed.**

1. **Pin config in the manifest.** Extend the scenario schema with a frozen `"config": {...}` snapshot (the *exact* dict replay applies). `replay.py`: prefer the pinned config; **error loudly** (don't silently default) when a `--project` lookup fails. Goldens regenerate from pinned configs only.
2. **Proposed scenario set** (windows already cut and measured in this analysis; each needs the montage GT pass before committing — `ground_truth.verified` stays false until then):

| manifest | project/slot | window | N | covers (tags) |
|---|---|---|---|---|
| `hangar-floor` | whitebg2 s3 | 1500+300 | 1 | single, near-black, clean-ish bg (GT carried over) |
| `hangar-aerial` | whitebg2 s4 | 1500+300 | 1 | aerial fast, small, drops (the hard seed, kept) |
| `texture-aerial` | whitebg s7 | 200+600 | 1 | aerial on the heavily textured wall (operator-picked third golden) |
| `texture-duo` | texturedbg s5 | 1000+400 | 2 | multi-dancer, occlusion, **textured ghosts** |
| `texture-wallhang` | texturedbg s4 | 2500+400 | 1 | aerial static, texture ghosts |
| `white-duo` | whitebg3 s2 | 100+400 | 2 | duo split/merge |
| `white-walkers` | whitebg3 s3 | 0+400 | 4–5/range | **count stress**, enter/leave |
| `blur-runner` | testflou s6 | 900+400 | 2 | fast motion, defocus, bystander discipline |
| `outdoor-night` | TOGO-night s1 | 0+330 | 1 | outdoor, 1.4/255 brightness |
| `outdoor-sitter` | TOGO-day s9 | 2500+400 | 2 | **static person**, daylight+IR, stage-ROI GT |
| `dark-crowd` | verydark s5 | 0+400 | per-range | multi enter/leave, near-black |
| `facade-ghosts` | phones s1 | 200+400 | 4 | small-far, **ghost flood**, YOLO stress (non-rig source) |

3. **Ground-truth protocol** stays the scenarios/README montage discipline, plus two rules learned here: *count every visible person or fix a stage ROI into the manifest* (TOGO bystanders, testflou assistant), and *re-verify N whenever footage is re-organised* (file mtime/sha in the manifest would make drift detectable — cheap: store the recording's byte size + frame count).
4. **Keep moving-camera clips out of tracking goldens** (motion model is meaningless there); they remain YOLO-only stress assets.

---

## 6. Algorithm & strategy recommendations

Ordered by measured impact:

### 6.1 Fix the warmup confirmation (drop amplifier #1)
Replace the consecutive-ish score with a **windowed hit-ratio** (e.g. confirm when ≥8 hits in the last 15 frames — same anti-flicker strength, but a 50 %-detection regime confirms in ~16 frames instead of never). Keep the existing decay for *de*-confirmation. This single change converts "detected 25–45 % of frames" scenes from *permanent silence* to *bridged tracking* — it directly attacks the #1 field pain on the hardest footage. Replay-gate it on `hangar-aerial` + `texture-duo`.

### 6.2 Add a static-person acquisition path (drop amplifier #2)
A high-box-confidence detection that **persists at the same location** (e.g. conf ≥ 0.5 within 0.5×h radius for ≥ N frames) should be admitted by the gate even with a weak skeleton and no frame-diff motion — it cannot be background (background at that conf would have been there during calibration → exclusion mask / clean plate covers it). This is the TOGO-sitter / seated-audience-facing-stage class. Natural home: a fourth OR-term in the scored gate, fed by the same spatial-memory cells the gate already keeps.
*Calibration nuance:* the calibrated replay of the sitter scene shows good signal (enhancement + adequate net height) lets the sitter pass θ_s outright (0.50 → 0.075 with no gate change) — so this path is the **robustness layer** for when signal can't be fixed (occlusion behind railings, marginal IR), not the primary fix.

### 6.3 Make the known-N calibration pipeline-level (it exists: tune.py)
The texturedbg calibrated run proves a YOLO-level threshold pick can *worsen* the full-pipeline score. The known-N product feature (UX: "put K dancers on stage / play a slot, press calibrate") should run the **tune.py joint search** (confidence × var × scale × θ_s, cached) against the live scenario — not a one-shot percentile rule. The pieces (cache, search, scoring) are all built; this is integration work, and this corpus is its test bed.

### 6.4 Exclusion mask: from validated to default-on
Corpus evidence (§3.3) makes it the single highest-leverage ghost weapon. (a) Build during every Calib1; (b) add the **manual mask editor** (bystander zones are human knowledge); (c) report masked-cell count in the calibration report card; (d) re-run `facade-ghosts` + `texture-duo` replays with masks to quantify (expected: most of the 0.1–0.43 calibrated ghost rates collapse).

### 6.5 Calib2 amendments (all small)
- **Confidence seed from box-conf, not keypoint-conf** — measured: visible-only kp-conf p5 pins the seed at the 0.50 clamp on nearly every scene while actual best-τ spans 0.15–0.65. Collect per-track *box* confidences in the pool and seed from their p5 − margin. (Supersedes the bug #11 fix — visible-only is still right, just not sufficient.)
- **Gamma/CLAHE sweep**: keep conf-maximising objective; add the noise-σ guard (skip/limit brightening when σ high — verydark regime); on already-bright scenes the sweep should choose ≈identity (it will, by objective).
- **`select_imgsz`**: confirmed correct on heights; the **FPS-budget cap (bug 12e / P-6) is mandatory** — suggestions hit 1536 (TOGO) and 1920 (phones). Add the "target unmet even at max imgsz" outcome as an explicit *rig advisory* ("move camera closer / longer lens"), not a silent fallback.
- **Height ratios**: measured in-scene spreads (p5–p95 = 0.4–1.8×median, aerial scenes widest) fit the AUTOCAL clamps; no change — but add a **runtime staleness alarm**: if the live median detection height sits outside the configured min/max gate for ~minutes, toast "person height calibration looks stale" (this single alarm would have caught the bulk-copied h=56 config on four projects).

### 6.6 Tracker: confirm the §3a relaxation, add duplicate merge
Swaps: 0–1 per 400-frame window in every current-config replay; identity is simply not where the pain is. Proceed with relaxing the slot-7 correctors (gate constants stay), guarded by the new `texture-duo`/`white-duo` scenarios. The calibrated runs' id-churn (4–10 ids) instead motivates the **duplicate-track merge** TUNING Phase F already named (count+speed gate spares moving duplicates) — schedule it with the warmup fix since they interact.

### 6.7 Sensitivity macro: corpus-calibrated range
The macro design survives contact with the corpus; set its numbers from measurement: confidence span must reach 0.15–0.65 (today's ±0.25/−0.15 deltas around the seed cover this only if the seed is right — another reason for 6.5a); the loose-end var floor of 8 is corpus-confirmed (§3.1).

### 6.8 MAX_PERSONS: enforce it (bug 12c)
phones-s1 emitted 8+ tracks for 4 dancers; a show consumer would receive unbounded dancer ids. Cap reported tracks (top-K by confidence/age) and surface the "more people than MAX_PERSONS visible" condition as a status warning.

---

## 7. Usability & robustness findings

1. **Silent config fallbacks are operator hazards.** Replay defaults when a project is missing (broke the goldens); four projects share one bulk-copied config (would silently no-op on stage); saved gamma can be a darkening LUT on a near-black scene. Mitigations: replay errors loudly (§5.1); the height-staleness alarm (§6.5); a calibration-age + "config matches scene?" line in the **show-readiness check** (TODO Phase 7) — the corpus shows config-vs-scene mismatch is *the* dominant real-world failure, ahead of any algorithmic weakness.
2. **Project reorganisation needs tooling.** The rename/move broke scenario→project linkage, config lineage, and (apparently) slot numbering ↔ content mapping. Cheap fixes: recording sha/size+frames in manifests; `config_store.rename_project` keeping a `renamed_from` breadcrumb; CORPUS_NOTES cross-reference table regenerated by a script rather than by hand.
3. **The Windows laptop is now a first-class harness host** (verified: venv py3.12 + torch 2.12 cu130 + RTX 5080 run survey, replay, scoring, calib1 sweep). Update TUNING.md's environment section (currently Linux-only paths) — the show machine can self-validate before a show.
4. **Cheap corpus passes are field-practical**: full 38-slot survey ≈ 35 min, a 400-frame full-pipeline replay ≈ 1–3 min. A "pre-show dry-run on last show's recording" is a realistic operator ritual.
5. **Annotation effort placement**: per-range N labels for 3 windows + montage verification of ~10 manifests is days, not weeks — and it unblocks every numeric re-fit queued in UX_PLAN (U4/U5 provisional constants), KNOBS (FIXED verdicts), and ROADMAP §4.1 step 1.

---

## 8. Answers to ROADMAP §12 open questions

| Question | Answer (measured) |
|---|---|
| Dancer size range / does one config generalise? | Median heights 100–1000 px across venues; in-scene p5–p95 spread 0.4–1.8×. **One config cannot generalise — per-scene Calib2 is structurally required.** imgsz needs 640→1536+ per scene. |
| Ghost flood magnitude on a bad scene | Raw @0.25: 0.7–3 ghost-dets/frame on textured/outdoor scenes; 8+/frame on the facade stress case; **fixed-spot dominated** (60–95 %), i.e. maskable. |
| Setup ritual / remaining manual steps | The corpus replays *are* the measurement: un-calibrated (stale) configs ⇒ drop 0.36–1.0 on 6/7 hard scenes. Calib1+Calib2+exclusion+known-N closes most of it; residual manual steps = ROI/stage definition + sensitivity nudge. |
| "Good enough for a show" numeric bar | Proposal for scenario pass lines: indoor rigged scenes — drop_rate ≤ 0.05, longest drop ≤ 1 s, ghost_rate ≤ 0.05; outdoor/textured — drop ≤ 0.10, ghost ≤ 0.15 pre-exclusion. Set per-manifest in `ground_truth.pass` so CI can speak. |

---

## 9. Proposed next steps (for ROADMAP §4.1 integration, in order)

1. **Re-found the regression corpus** (§5): pin-config schema + loud-fail replay lookup; montage-verify the 11 manifests; per-range labels for the 3 varying-N windows; regenerate goldens. *(This subsumes the old "rename residence1-solo" action item.)*
2. **Warmup ratio fix + duplicate-track merge** (§6.1/§6.6), replay-gated on the new corpus.
3. **Exclusion-mask default-on + manual editor** (§6.4); re-measure `texture-*`/`facade-ghosts`.
4. **Static-person acquisition path** (§6.2), gated on `outdoor-sitter`.
5. **Calib2 amendments** (§6.5: box-conf seed, noise guard, FPS cap, staleness alarm) — small diffs, then re-fit U4/U5 provisional constants on the verified corpus.
6. **Known-N pipeline-level calibration** (§6.3) — productise tune.py behind the Calib2 UI.
7. Then the already-sequenced ops cluster (readiness check gains the config-vs-scene line).

Items 2–5 are independent small tracks once 1 lands; nothing here displaces the P1.3 hardware step — better IR still lifts every curve in §3.

---

*Analysis scripts and raw artifacts: `tmp_analysis/corpus_survey.py`, `tmp_analysis/run_replays.py`, `tmp_analysis/aggregate_survey.py`; outputs in `tmp_analysis/survey/` (38 slot JSONs + montages, `survey_summary.json`) and `tmp_analysis/replays/` (22 replay summaries with per-frame timelines). All regenerable; nothing committed.*
