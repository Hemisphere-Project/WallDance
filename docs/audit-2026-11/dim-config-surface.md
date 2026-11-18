# Dimension — config-surface

*Context-aware auditor's report, followed by an adversarial reviewer's check of its six most consequential claims. Where the two disagree, the reviewer's reading is the safer one.*

## Verdict

The tuning surface is large and has no index. At HEAD: 75 saved project keys, 303 import-time constants, 83 SlotParams fields, 44 ProcessingSettings fields, 94 commands, 17 environment switches. Six hand-written lists (save, live apply, replay apply, schema, DETECTION_DEFAULTS, remote commands) describe the same keys and disagree: load applies five keys that save drops (blind claim confirmed), replay reads seven keys the app never applies, two saved keys are never read back, 32 saved keys are unvalidated. On the dev37 mirror this has not bitten yet (none of 196 project files holds the five keys). What has bitten is recipe drift: all 11 October takes that carry provenance ran imgsz 800 / confidence 0.25 / intermittent off while validation used D27, and no code path compares the two. Units are split: tracker and motion count frames (42 constants, tuned near 20 fps) while slots and ops count seconds, and nine constants are absolute pixels in a space that scales with imgsz. The settled 'no settings framework' decision can stand: one declarative key table that references the constants is enough to derive save, apply, validation, replay, remote setters and a recipe fingerprint.

## Since the October audit

Measured at the audit commit 0a987af against HEAD: constants 242 to 303, ProcessingSettings 27 to 44 fields, saved keys 57 to 75, commands 71 to 94, SlotParams 0 to 83 fields (73 code-only), schema ranges 19 to 25. Better: D33 gives eight detection keys one default dict and a default when absent; slot keys default on a full load; four booleans are validated; BUG-3 and BUG-4 are fixed; each take's `.meta` now carries the saved config and the commit (MRK-0). Not done: ARCH-10 (rated nice-to-have); replay's applier gained six more keys the app lacks; the five-key save drop predates the audit and was missed by it; no tier markers; nothing compares a recipe with the validated one. The new October layers (slots, D34, height-guard follow) are in seconds and sit on a tracker that still counts frames.

## Details

**1. Inventory (AST counts at HEAD; scripts in /tmp/wdaudit)**

| Tier | Count | Split |
|---|---|---|
| Operator value controls | 50 call sites (+11 in dialogs), 40 buttons | Live 13, Rig 3 (rig sheet loops to 11 fields), Advanced 22, expert-gated 8, Expert tools 3, top bar 1 |
| Project keys saved | 75 | 14 per lighting profile, 61 shared |
| config.py constants | 303 | about 50 back a project key |
| SlotParams | 83 | 10 reachable from settings, 73 code-only |
| ProcessingSettings | 44 | 33 saved, 3 applied-unsaved, 2 command-only, 4 replay-only, 2 derived |
| Commands | 94, 78 remote-allowed | 61 of 75 saved keys have a setter |
| Environment switches | 10 app/launcher, 3 harness, 4 wdremote | WD_POSE_RUNNER and WD_ENGINE_DIR change inference |
| Numeric module constants outside config.py | 74 | 21 GUI |

Durations: config.py has 42 frame/sample counts and 15 per-frame rates against 29 wall-time values (19 are ops or log cadence; none in tracker or motion). SlotParams: 12 frame counts, 3 per-frame alphas, 22 seconds. Project keys: 3 in frames (`tracker_max_age`, `tracker_ghost_skeleton_age`, `tracker_smoothing`), 4 in time.
Distances: config.py has 24 height ratios against 9 absolute-pixel values (plus 8 absolute grey-level thresholds); SlotParams 32 in heights, none in pixels.

**2. Key-list diff**

| Difference | Keys |
|---|---|
| Applied on load, not saved (5: blind claim confirmed) | `tracker_swap_correctors`, `crossval_skel_min_kpts`, `crossval_skel_min_conf`, `crossval_motion_min_ratio`, `dial_b_relevant` (plus the legacy no-op `tracker_distance`) |
| Saved, never read back (2) | `fp16`, `preview_scale` |
| Replay only (7) | `tracker_own_height_gates`, `height_guard`, `auto_height`, `belt_backing`, `entry_min_travel_h`, `box_clamp_enabled`, `fg_plate` |
| Live detection keys replay ignores | `bg_subtract_enabled`, `bg_subtract_sensitivity`, `roi_source_w/h` |
| Saved, not schema-validated (32 of 75) | 22 of 27 booleans; `tracking_mode`, `slot_filter_input`, `model`, `camera_source`, `osc_ip`, `denoise_strength`, `tracker_smoothing`, `bg_subtract_sensitivity`, `preview_scale`, `calibration_state` |
| DETECTION_DEFAULTS (8) | all saved and applied; replay takes TensorRT from a CLI flag |
| Saved, no setter (14) | `camera_stall_hold`, `max_persons`, `tracker_ghost_skeleton_age`, `tracker_smoothing`, `person_height_min_ratio`, `person_height_max_ratio`, `mog2_var_threshold`, `blur_budget_ms`, `sensitivity_conf_seed`, `sensitivity_var_anchor`, `exclusion_grid`, `exclusion_cells`, `calibration_state`, `fp16` |
| Settable, not saved (2) | `box_clamp_enabled`, `output_smoothing_l` |
| Absent key on a full load | 24 keys get the code default, about 50 keep the previous project's value; replay uses code defaults for all (`enhance_force` False against live True) |

**3. Recipe as run (dev37 mirror; laptop not checked)**

| Source | imgsz / confidence / intermittent |
|---|---|
| Validated (D27, DETECTION_DEFAULTS) | 1280 / 0.15 / on |
| 11 distinct October takes with a `.meta` config | 800 / 0.25 / off, all 11 |
| Project heads `default`, `mur25m-ceinture-0610`, `mur30m-0710` | 800 / 0.25 / off |
| Project head `mur25m-night-common` | 1280 / 0.15 / on |
| 19 scenario manifests | none equals the shipped recipe |

**5. Convert first (field risk when fps or imgsz moves)**

1. Entry counts to seconds: `TRACK_WARMUP_THRESHOLD` 15, slow window 40 / credits 12, `TRACKER_ESTABLISHED_FRAMES` 15, slot `min_hits` 8, `entry_min_hits` 12.
2. Lifetimes to seconds: `TRACKER_MAX_AGE` 45 (x3 established), `MOTION_BRIDGE_MAX_FRAMES` 80, presence 15, `TRACKER_GHOST_SKELETON_AGE` 3, slot `*_fss`.
3. `TRACKER_MAHALANOBIS_GATE_NOISE` 700 px2 and Kalman Q/R to heights.
4. Per-frame motion gates to per-second: frozen speed 0.03 h/frame, displacement gate, velocity clamp, frictions.
5. Blob areas 100 / 50 px2 to height squared.
6. MOG2 history 500 and learning rate, diff-pair age 30.
7. `HEIGHT_GUARD_MIN_SAMPLES` 100 per 10 s, own-height window 200.
8. Calibration windows (plate 40, autocal 90 / 240): last.


## Findings

### 1. Nothing compares the recipe a take or a show runs with against the validated one

severity high · confidence high · status known open · effort S · prior id: CONTEXT §6 / D33

**Evidence.** `take_provenance` writes the saved-config dict and the commit into each `.meta` (app.py:478-502); session.json and SESSION_SETTINGS do the same (recording_controller.py:145-152, 208). No reader compares them: readiness has 8 rows, none on the recipe (app.py:2248-2295); of the recipe, `/status` carries sensitivity and engine only (app.py:1023-1047). D33 fills absent keys only (config_schema.py:279-283). dev37 mirror: all 11 distinct October takes with a config block, and 3 of 4 October project heads, store imgsz 800, confidence 0.25, intermittent false. Laptop state not checked.

**Recommendation.** Add `recipe_deviations(flat)` against DETECTION_DEFAULTS plus the slot defaults. Print it at project load, show it as a readiness row and a top-bar chip, and write `recipe_id` (hash of the recipe-tier keys), `code_id` (hash of constants and SlotParams defaults) and the deviations into `.meta`, session.json, `/status`, replay output and every golden.

**Risk.** None to the show path: a read-only comparison. The validated envelope must be agreed first, since the sensitivity dial moves confidence by design.

### 2. Six hand-maintained key lists: replay and the live app apply different keys and different absent-key defaults

severity high · confidence high · status known open · effort M · prior id: ARCH-10

**Evidence.** AST diff: save 75 keys (app.py:1222-1313), live apply 79 (app.py:1315-1594), replay 52 (replay.py:196-351). Replay only: tracker_own_height_gates, height_guard, auto_height, belt_backing, entry_min_travel_h, box_clamp_enabled (replay.py:261, 284-287, 311), fg_plate (:837). Absent key: replay takes the code default, with enhance_force False (replay.py:245) against live True (app.py:547); live keeps the previous project's value for about 50 keys. A third mirror exists: emitted_golden.py:90-95 `shipped_params`; slot_replay.py:117 starts from bare `SlotParams()` (static_guard and smart_hold False, shipped True).

**Recommendation.** Do ARCH-10 as a key table, replay first, goldens byte-identical. Land the key-set parity test before any refactor, with today's differences as explicit exceptions. Raise it from nice-to-have: saved keys went from 57 to 75 and replay gained six private keys in four days.

**Risk.** Touches every project load and every replay; needs the dev37 goldens and one laptop smoke. The parity test alone carries no risk.

### 3. Tracker, motion and slot-entry durations are frame counts whose wall-time meaning follows the exposure key

severity medium · confidence medium · status new · effort S

**Evidence.** config.py holds 42 frame or sample counts and 15 per-frame rates, and no tracker or motion duration in seconds: TRACK_WARMUP_THRESHOLD 15 (:989), TRACKER_MAX_AGE 45, commented '3 sec at 15 FPS' (:175), x3 when established (:271), MOTION_BRIDGE_MAX_FRAMES 80 (:838). SlotParams adds 12 (identity_slots.py:249-254, 333). The frame period follows `ids_exposure_us`, a per-profile key the slider lets reach 66.7 ms, 15 fps (ids_camera.py:116-124). First point for an entering dancer: about 17 frames, 0.85 s at 20 fps, 1.1 s at 15. Arithmetic, not replay-verified; corpus takes run 18.7-20.0 fps.

**Recommendation.** Add NOMINAL_FPS = 20 and write the ten riskiest values in seconds, converted at import: unchanged at 20 fps, so goldens stay identical. Add a readiness row when live fps leaves +/-15 % of it. Later convert at tracker reset from the measured rate and gate it with a half-rate decimated replay.

**Risk.** Step one is value-neutral at 20 fps. The runtime conversion changes tracker timing on any take not at 20 fps (two 30 fps corpus scenarios) and needs new goldens.

### 4. Save drops five keys that load applies; two saved keys are never read back

severity medium · confidence high · status new · effort S

**Evidence.** Applied: tracker_swap_correctors, crossval_skel_min_kpts, crossval_skel_min_conf, crossval_motion_min_ratio (app.py:1457-1467), dial_b_relevant (:1503). None is in the saved dict (:1222-1313). Apply writes them to the file (:2542-2546, :2634-2636) and the next Save rebuilds it without them (config_manager.py:346-350). Absent from every app-saved file, they keep whatever an earlier project set in the session. `fp16` (:1230) and `preview_scale` (:1281, :1529-1532) are written and never used. Latent so far: none of the 196 mirrored project files holds any of the five.

**Recommendation.** Add the five keys to the saved dict and give the four tracker and crossval ones a code default on a full load. Remove `fp16` and `preview_scale` from the save. Add the round-trip test: a config holding every applied key survives load then save.

**Risk.** Low. Saving `dial_b_relevant` makes a hidden Dial B stay hidden across saves, which is the intended behaviour.

### 5. Validated behaviours have no live switch, and the code-only tier is invisible to provenance

severity medium · confidence high · status new · effort S

**Evidence.** `height_guard`, `auto_height`, `belt_backing` and `entry_min_travel_h` are ProcessingSettings fields (pipeline.py:155-158) and `own_height_gates` a tracker attribute (tracker.py:947) with no project key, control or command in the app; only replay sets them. 73 of 83 SlotParams fields and about 250 of 303 constants are code-only. Changing one on site means editing a tracked file: the launcher then skips updates (launcher/gui.py:180-189) and `app_version` reports the commit with no dirty flag (version.py:32-66). WD_POSE_RUNNER and WD_ENGINE_DIR are not recorded either.

**Recommendation.** Promote the switches that guard known-open behaviours (`height_guard`, `tracker_own_height_gates`) to project keys. Hash the config constants and SlotParams defaults at import into a `code_id` shown in `app_version`, `.meta` and the log header, so an edited constant is visible without a settings framework.

**Risk.** Low. A new project key is one more thing a file can set wrongly; the recipe row would show it.

### 6. Nine absolute-pixel constants do not follow the person height; the Mahalanobis gate radius changes with imgsz and ROI

severity medium · confidence medium · status new · effort M

**Evidence.** Tracker space is the letterboxed YOLO input on the GPU path (pipeline.py:328-345). TRACKER_MAHALANOBIS_GATE_NOISE 700 (tracker.py:1147) fixes the gate radius near 107 px: 0.87 heights for the 123 px dancer at 1280, 1.4 for 77 px at 800 (sizes from config.py:62-64), about 0.7 at 1536. Kalman R and Q are absolute too (tracker.py:345-348), as are MOTION_BRIDGE_MIN_AREA 100 px2 (motion_detector.py:354) and LOCAL_MIN_BLOB_AREA 50 px2 (tracker.py:2972). By contrast 24 config ratios and all 32 SlotParams distances are in heights.

**Recommendation.** Derive them in `set_person_height`: gate noise = (0.87 x height) squared, areas = k x height squared, with k reproducing today's values at the validated 123 px so goldens stay identical there. Add a scale-invariance test: coordinates and height doubled must give the same ids.

**Risk.** Changes association away from the 123 px operating point: needs replay goldens and the two-dancer takes (t-020) before release.

### 7. Load validation covers 43 of 75 saved keys and 5 of 27 booleans; ranges are written in up to three places

severity low · confidence high · status known open · effort S · prior id: 03-architecture §3.6

**Evidence.** `validate_flat` checks 25 ranges, 4 bools, imgsz, rig, transform, ROI and mask (config_schema.py:52-83, 292-341). Unchecked: 22 booleans, several applied as `bool(config[k])`, so the string 'false' is True (app.py:1424, 1454, 1535), plus tracking_mode, slot_filter_input, model, osc_ip. Ranges for max_dancers, stability, coast_s and static_release_s are repeated in api.py:143-165, 223, config_schema.py:75-78 and pipeline.py:1148-1155. Eleven numeric setters have no range check (api.py:97-121, 293-310, 370-372, 406-408). Inactive profile bundles are never validated (config_manager.py:197, 362-365).

**Recommendation.** Generate `_RANGES`, the bool and enum checks and the command `__post_init__` checks from the key table, and validate every profile bundle at load, not only the active one.

**Risk.** Stricter validation can drop a value a hand-edited file relied on; warnings already reach the operator.

### 8. Controls, persistence and setters do not line up, and the surface has no tier marker

severity low · confidence high · status known open · effort S · prior id: 03-architecture §2.3

**Evidence.** Box clamp has a Live-panel checkbox and a command, output smoothing a command (app.py:871-873, 1779-1790); neither is saved or applied, so both reset at restart and are missing from take provenance. Fourteen saved keys have no setter, among them `camera_stall_hold`, the unreleased D34 switch ('no GUI: a project key', app.py:1493), `max_persons` and `tracker_ghost_skeleton_age`. config.py still carries no operator / calibrated / expert / fixed marker (03-architecture.md §2.3), while the constant count grew by a quarter in October.

**Recommendation.** Give every key a tier and a unit in the key table. Generate `GET /api/v1/keys` and one generic `SetKey` command for tiers up to expert, so a switch such as `camera_stall_hold` can be A/B-tested remotely without hand-editing project JSON.

**Risk.** A generic remote setter widens what a remote session can change in RUN; keep it under the existing control policy.

## Paradigm-level change proposed

One declarative key table, `core/project_keys.py`: per key the name, type, range or choices, default (the existing config.py constant, so constants stay import-time values), unit, scope (shared or profile), tier, a recipe flag, a headless flag and a getter/setter on (settings, tracker, enhancer, processor). Save, apply, schema validation, replay's processor build, a generic remote `SetKey`, the GUI sync names and the recipe fingerprint become loops over it. This is ARCH-10 widened; raise it from nice-to-have, because the lists drifted further in four October days and the D33 episode showed the cost.

Migration, goldens byte-identical on dev37 at each step: (1) land the tests; (2) table for the 52 headless keys, replay switches to it; (3) the app's apply and save go through it, the five dropped keys are saved, app-only keys keep custom setters; (4) schema and command checks are generated, and a full project load becomes defaults overlaid by the file; (5) fingerprint and readiness row; (6) seconds and heights for the ranked constants.

Parity test: for a config holding every key at a legal non-default value, `saveable(apply(cfg)) == cfg`; a replay-built and a live-applied processor give the same headless snapshot; table defaults equal the ProcessingSettings and SlotParams defaults; every scenario config is a load-save fixpoint.

Cost: 3-4 days plus one laptop smoke, since it touches every project load. Cheapest intermediate step, about a day, no behaviour change: the AST key-set test with today's differences as a shrinking exception list, plus `recipe_deviations()` at load and as a readiness row.

## Questions for the owner

- On the laptop, which project will the next show run, and does its newest save hold the D27 values (1280 / 0.15 / intermittent on)? The dev37 mirror shows `default` and `mur30m-0710` still at 800 / 0.25 / off; was apply_d33.py run there, and was the project reloaded before any Save?
- Which deviations from D27 are acceptable without a warning: exact values, or an envelope (for example confidence up to 0.25 through the sensitivity dial, imgsz 1280 or more)?
- For the 30-40 m step, will exposure stay at or under 50 ms (20 fps), or are longer exposures (the slider allows 66.7 ms, 15 fps) or imgsz 1536 on the table? That decides whether the frame counts must be converted before that step.
- Should `height_guard`, `tracker_own_height_gates` and `box_clamp_enabled` become project keys the field can switch, or stay replay-only A/B flags?
- Has known-N Apply or Auto-tune Apply ever been used on a laptop project (the mirrored project files hold none of the five keys an Apply would write), and are constants in config.py ever edited on the laptop?

## Adversarial review

The report's inventory and list diffs are accurate: I re-derived the key sets by AST and the counts, the five-key save drop, the seven replay-only keys and the 57-to-75 growth all check out. Two of the six claims hold as written (findings 2 and 4); four are weakened on wording or proportion, none is refuted.

- **Recipe drift is overstated as evidence.** All 11 takes predate D33 (10-08 13:16), and the "3 of 4 project heads" are dev37 artefacts, one a deliberate reconstruction of take conditions. `apply_d33.py --dry-run` already compares a project with D27. The in-app gap (no load-time or readiness comparison) is real and worth the proposed one-day step.
- **The frames-versus-seconds mechanism is misattributed.** The 20 fps comes from the `IDS_MAX_FPS` cap; the October projects ran 25 ms exposure. Processing load and stalls are what change the rate, and an fps floor and drop alert already exist.
- **The rethink is a challenge to "no settings framework", not compatible with it.** It has a legitimate post-October reason, but it understates the ordering and group couplings in the applier and the saved keys the app rewrites at runtime, so 3-4 days and a strict round-trip equality are optimistic.
- **Two things the report missed matter more than the five dropped keys.** The height guard rewrites `person_height_px` live (visible in the 10-07 take metas: 253 against 150 in the named file), and the empty-wall plate is live state that neither replay nor take provenance captures.

Not checked: laptop state, the report's GUI control counts, the 42/15/29 unit tallies beyond spot checks, and the setter range claims in `api.py`. Read-only throughout; `git status` is clean.

### Claims checked

- **weakened** — VERDICT: the surface is large and unindexed (75 saved keys, 303 constants, 83 SlotParams, 44 ProcessingSettings, 94 commands); six hand-written lists disagree; the save drop is latent (0 of 196 files); 'what has bitten is recipe drift: all 11 October takes ran 800 / 0.25 / off ... no code path compares the two'; the 'no settings framework' decision can stand.  
  Inventory and list diffs hold. The 'bitten' evidence is the single pre-D33 episode CONTEXT §6 already records, not a recurrence; and 'no code path compares' is wrong as worded: a tracked dev tool does, on demand. Nothing in the app does.  
  Evidence: Counts re-derived by AST: save 75 (app.py:1222-1313), apply 79 (app.py:1315-1594), SlotParams 83 (identity_slots.py:237), ProcessingSettings 44, 94 Command subclasses in runtime/api.py; config.py top-level constants 305 by my count against 303. Apply-not-saved = the five keys plus tracker_distance. 196 project files, none holds the five. Take metas: 18 files with a config block = 11 distinct takes, all dated 2026-10-06 or 10-07, all 800 / 0.25 / False. D33 landed after them (12ce95f, 2026-10-08 13:16). tmp_analysis/plan25m/apply_d33.py:205-225 prints a project-vs-D27 diff (--dry-run), and ROADMAP.md:215-218 names it as the way to move a project.
- **weakened** — FINDING 1 (high): nothing compares the recipe a take or show runs with against the validated one; all 11 October takes and 3 of 4 October project heads on the mirror store 800 / 0.25 / off.  
  The structural gap is real and cheap to close. The mirror evidence says nothing about the laptop today, and D33 deliberately keeps stored values with apply_d33.py as the mover; the open question is whether that was run on the laptop.  
  Evidence: In-app gap confirmed: take_provenance writes live values and the commit (app.py:478-502); readiness has 8 rows, none on confidence or intermittent (app.py:2248-2296); /status carries imgsz and sensitivity only (app.py:1033-1044); fill_detection_defaults fills absent keys only (config_schema.py:279-283). But the '3 of 4 heads' are dev37 artefacts: the mur30m-0710 head's _meta says 'reconstructed ... the config takes slot_3/4/5 of 2026-10-07 ran with ... Kept newest on purpose'; the default head on dev37 is 10-06 21:08 while the 10-07 takes name default_20261007_200620.json; the mur25m-ceinture head is the 10:33 calibration D28 reverted. The 'dancer size' row already recommends a larger Image Size when the dancer is under 100 px in YOLO's input (ops_monitor.py:252-280).
- **holds** — FINDING 2 (high): replay and the live app apply different keys and different absent-key defaults; ARCH-10 should be raised from nice-to-have because saved keys went 57 to 75 and replay gained six private keys in four days.  
  Holds, with proportion: six of the seven replay-only keys default to the same value the live code uses, and all 19 manifests pin enhance_force, so nothing diverges today unless a key is set. The one live/replay difference that changes output is the plate (see missed).  
  Evidence: Replay-only keys at tests/replay.py:261, 284-287, 311-312 and fg_plate at :837-840; none is in app.py's apply. enhance_force defaults False in replay (replay.py:245) and True live (app.py:547). emitted_golden.py:88-93 and slot_replay.py:117 (bare SlotParams(); static_guard and smart_hold default False at identity_slots.py:295, 313, shipped True at config.py:326, 333) are further mirrors. History by AST: save 57 at 0a987af, 68 at 4fbace3, 75 at HEAD; replay-only keys 1 then 7. ARCH-10 is 'nice-to-have' in ROADMAP_v2_REVIEW.md:371 and :490 (dated 2026-10-06); the six replay-only keys arrived 10-07, after that rating.
- **holds** — FINDING 4 (medium, new): Save drops five keys that load applies (tracker_swap_correctors, three crossval gates, dial_b_relevant); fp16 and preview_scale are saved and never read back; absent keys keep whatever an earlier project set.  
  One correction: dial_b_relevant does not 'keep an earlier value'. Line 1503 is unconditional, so any apply lacking the key, including a lighting-profile bundle, makes Dial B visible again; and the app holds no attribute to save it from. Both writers sit in Expert tools since D29, which fits the medium rating.  
  Evidence: Applied at app.py:1457-1467 and :1503; absent from the saved dict :1222-1313. Writers: auto-tune Apply (TUNED at app.py:2539-2546) and known-N Apply (:2634-2636); the next Save rebuilds from saveable_config (config_manager.py:346-350). The tracker is built once (app.py:586), so the four tracker and crossval values persist across project switches. fp16 is written at app.py:1230 and read nowhere; preview_scale is a 'pass' at :1529-1532. Not mentioned in docs/ (grep).
- **weakened** — FINDING 3 (medium, new): tracker, motion and slot-entry durations are frame counts whose wall-time meaning 'follows the exposure key' (ids_exposure_us, slider to 66.7 ms = 15 fps); fix with NOMINAL_FPS = 20 and a readiness row.  
  Exposure moves the rate only if someone drags the slider past 50 ms. What actually moves frames-per-second is processing load (that take: actual_fps 18.66) and stalls. A 15 fps readiness floor and a sustained fps-drop alert already exist (config.py:1100-1106; ops_monitor.py:110, 428), and IDS_MAX_FPS is already the nominal rate constant. Blind config-ops#10 raised the same point, so 'new' is generous.  
  Evidence: The frame-count facts hold: TRACKER_MAX_AGE 45 '~3 sec at 15 FPS' (config.py:175), TRACK_WARMUP_THRESHOLD 15 (:989), slow window '~2 s @ 20 fps' (:1000), MOTION_BRIDGE_MAX_FRAMES 80 (:838), Kalman dt = 1.0 per frame (tracker.py:330), slot min_hits and *_fss in frames (identity_slots.py:249-254). The mechanism does not: the camera rate is min(target_fps, hardware max) (ids_camera.py:759, 1608) with target_fps from IDS_MAX_FPS = 20 (config.py:107; ids_camera.py:1971). The October takes ran ExposureTime 25 ms with AcquisitionFrameRate 20.0 (slot_2_20261007_200742.avi.meta), and every October project stores ids_exposure_us 25000. Calibrate's blur budget is 25 ms, bounds 5-30 (config.py:633, 705).
- **weakened** — RETHINK: one declarative key table (core/project_keys.py) so that save, apply, validation, replay's build, a generic remote SetKey, GUI sync and a recipe fingerprint 'become loops over it'; compatible with 'no settings framework'; 3-4 days; parity test saveable(apply(cfg)) == cfg.  
  The direction has a post-October reason (finding 2), and the one-day step (key-set test plus recipe_deviations) is sound. But a typed, ranged, tiered registry with a generic setter and generated validation is a settings framework for the 75 project keys: say it is a challenge to the keep-as-is, not compatible with it. The strict equality test cannot hold for dial-coupled, hardware-clamped or guard-written keys, and 3-4 days is optimistic with at least nine ordering or group couplings and no headless app fixture.  
  Evidence: Apply is not a per-key loop today: tracking_mode must precede tracker_max_age (app.py:1430-1441); mog2_var_threshold sets the anchor that sensitivity_var_anchor then overrides (:1350-1356); confidence seeds the dial when seed or dial are absent (:1359-1367); exclusion is a four-key bundle (:1368-1374); ten slot keys go through one batched callback (:1478-1492); motion_sensitivity re-centres Dial B (:1468-1474); ROI is a six-key group normalised against stored source dims (:1543-1559); IDS keys call hardware (:1564-1577); full-config and profile-bundle applies differ (:1322). Saved values are also rewritten at runtime: the height guard sets settings.person_height_px (pipeline.py:1445-1451) and the dial moves confidence. The only apply test runs on a duck-typed app (tests/test_new_project_rig.py:86-132). 03-architecture.md §5 reads 'Don't introduce a settings framework; document the tiers instead' and 'Only extract the config applier (ARCH-10)'.

### Missed or out of proportion

- Saved keys that the running app rewrites are absent from the inventory. The height guard (ON per D29, no live switch) sets settings.person_height_px (pipeline.py:1445-1451), the key every tracker gate scales from (tracker.py:1005-1020). On the mirror, takes slot_3/4/5 of 2026-10-07 carry person_height_px 253 in their .meta while the file they name (default_20261007_200620.json) holds 150, and the 20:30 save then persisted 253. A recipe fingerprint or load-save fixpoint must model these writers; this is the config-surface side of the known 'walk-out height' issue.
- The empty-wall plate is configuration that sits outside every key list. Live loads plates/latest.npz on each full project load (app.py:1497-1498, 1860-1870); replay uses a plate only if fg_plate is passed (replay.py:281-290, 837-840); take_provenance records no plate identity (app.py:478-502) and latest.npz is overwritten at each capture. A take's .meta therefore cannot reproduce the live foreground veto and hold, which is a larger live/replay gap than the six replay-only switches.
- The Mahalanobis item (finding 6, 'convert first' rank 3) is out of proportion. Height-relative gates sit beside it: the 0.95 h match gate (config.py:198; tracker.py:1018, 1356, 2721) and, for established tracks matched last frame, a 0.475 h displacement gate (config.py:464; tracker.py:1446-1452). At 800 or at 30 m (about 105 px) the absolute floor of about 107 px is looser than those and stops binding; it only tightens for dancers above about 113 px (imgsz 1536 or a closer camera). The arithmetic is right; the risk for the 30-40 m step is small.
- The recipe finding does not connect to lighting profiles. confidence, sensitivity and the seed are per-profile keys (config_schema.py:32-47); apply_d33.py changes only the active profile by default (apply_d33.py:108-113); the inactive bundle is applied raw, without validation or the D33 fill (config_manager.py:362-365). A recipe_deviations(flat) check sees the active profile only. Not observed on the mirror (the October rehearsal bundles are empty and get seeded from the current values), and the switch is Expert plus remote since D29.
- CONTEXT's 'exposure ~50 ms so ~20 fps' does not match what the October projects stored: 25 ms exposure, with 20 fps coming from the IDS_MAX_FPS cap (config.py:107). The report's units ranking and its owner question on exposure are built on the 50 ms premise; the question that decides the frames-to-seconds work is whether processing holds 20 fps with TouchDesigner on the same GPU, not the exposure setting.
