# Estimator paradigm — two cases and a judge

*Question: is everything between YOLO's output and the OSC point built on the right paradigm? One agent argued to keep and evolve the layered estimator, one argued to re-found it, a third checked both against the code and ruled. The judge did not have the owner's 2026-11-18 answers on the choreography (more than two dancers, contact, vertical entries, long stillness); see [AUDIT_2026-11.md](../AUDIT_2026-11.md) §3 for how they shift the recommendation.*

## Judge

### Verdict

Half of the core is on the right paradigm, and neither case should be followed as written.

The slot layer is the right estimator for the requirement: a fixed number of targets, time in seconds, typed evidence. The tracker under it is not: it is an identity tracker for an unbounded number of people, counted in frames, whose gates are overridden by rescue rules. The measured losses sit at the boundary between the two.

That does not justify a re-foundation now. The only evidence for the alternative is a prototype that never releases a point, on April footage, with a plate cut from the take itself. On the only footage with a real empty-wall plate (the 30 m solo takes), I measured a body-size foreground blob far from the walker on 58-90 % of frames.

It does not justify the planned tracker surgery either (CONT-3 / 4a / 5 plus a time base: 2-3 weeks, rated medium-high risk, sized on single-dancer footage).

The decision depends on two numbers nobody has:
- whether the shipped stream at the D27 recipe with the plate meets the KPI on labelled two-dancer footage;
- whether a slot estimator fed directly by YOLO and foreground, with real entries and exits, beats it.

Both can be measured in about two weeks, mostly on dev37.

### Reasoning

**What case A gets right.** Settings alone moved the white duo from 0.741 to 0.973 on-dancer, so the "0.96 against 0.74" headline is not like for like. The prototype fixes N and coasts forever (nlock.py:4, :113-114), so its 100 % coverage and 0 s hole are by construction and D4 is missing. The first foreground hook produced two field defects in one day, one still open.

**What case A gets wrong.** Its harness argument does not hold. The scorers work on any emitted stream, and stored timelines already carry YOLO detections and foreground blobs. The committed gates do not cover what ships: all 19 manifests pin intermittent confirm off, the six emitted fixtures were recorded before D27, and their replay passes no belt, foreground or ROI bounds. Its stage 3 is the audit's own medium-high-risk item, its pieces measurably do not add up, and it invests in code a better estimator would delete.

**What case B gets right.** Split authority, a Kalman filter that is not doing the estimating, advisory gates and the missing time base are all in the code. My reduced re-run of its probe agrees: at 10 fps the first report takes twice as long, and at 60 % detection duty coverage falls from 99.6 to 95.7 %. The slot layer cannot repair the boundary from above; that was tested and rejected. A shadow estimator is reversible and needs no laptop.

**What case B gets wrong.** Its 89 % "estimator loss" is at the April settings. At D27 with exclusion the white duo loses 45 start-up frames and 19 merge frames of 739. Foreground as a primary measurement is unproven on a real plate. On the 30 m solo takes the plate is valid on 87-92 % of frames, below B's own 95 % gate, and fixed spots (one is the door edge of PLAN §C.15) stay body-size foreground all evening. Those takes are not show-like, but they are the only real-plate footage.

**What both miss.**
- The shipped stack at D27 with the plate hook has never been scored on the duo proxies. The plate hook was scored only at the April settings (0.84 on-dancer, 14.8 % held). That is the fair comparator, and it costs one replay.
- Three real-plate solo projects are already on dev37, so the entry, exit and ghost half of the question does not wait for the laptop.

Both sides agree on the end state: slots as the estimator, the tracker reduced or removed. The open question is order, so measure first, then spend.

### Inventory the judge verified

All counts are mine (AST spans and grep on remote-ops at d2ecfde); the groupings are my own.

**tracker.py**
- 3,798 lines; DancerTracker 75 methods, DancerTrack 29.
- 103 names imported from config; 214 numeric literals other than 0 / 1 / 2, on 162 lines.
- 32 logged decision kinds (28 `logger.log` names plus MATCH, CLOSE_ACCEPT, FORCE_UPDATE, FALLBACK_UPDATE).
- About 48 distinct rules and gates by my enumeration: 21 association, 7 unmatched-detection, 7 motion, 7 lifecycle, 6 emission.
- Default-off: 3 rules, the swap correctors. Their switch is the only False among the 9 booleans the tracker imports.
- Frame-count constants: 26 of the imports. Per-frame rates: about 10. Seconds: none except the log flush interval.
- Absolute-pixel constants: 4 in config (Kalman Q 2.5 and R 2.0, gate noise 700 px², blob area 50 px²). Inline ones in tracker.py were not counted one by one.

**Lines by concern in tracker.py**
- Identity purity and duplicate clean-up: 1,344 (35 %). Swap correctors with their private helpers 552, dormant pool and resurrection 303, identity cost terms 185, shadow / duplicate / takeover merges 304.
- Presence: 840 (22 %). Motion bridge 321, synthetic blobs 78, occlusion ageing 69, emission gate and warm-up 144, unmatched-detection rescue 228.
- The remaining ~1,614 lines are the association core, track and Kalman, lifecycle, logging and setup.

**identity_slots.py**
- 1,494 lines; 83 SlotParams fields (784 lines and 30 fields at its first commit, 49 hours earlier).
- About 28 rules (approximate).
- Default-off as shipped: 8 mechanisms covering 22 fields (weak 5, entry plausibility 6, entry travel 6, min travel, hidden yield, rebind-anywhere, static release, edge touch). The pipeline never passes `weak=`.
- Frame-count fields: 11.

**Harness**
- 19 of 19 manifests pin intermittent confirm off.
- 6 emitted fixtures, none recorded at D27.
- 54 tracker unit tests; the 99 targeted tests pass here (75 s).

**Field footage on dev37 with a real plate**
- 3 projects (12 + 11 + 4.4 GB), all solo.
- Post-fix 30 m timelines (tmp_analysis/stale_plate/tl/V2_*): plate valid on 87-92 % of frames. Of the valid frames that carry an emitted point, 58-90 % also hold a body-size blob (at least 0.02 h² at h = 135 px, among the 6 largest) more than 1.5 h from every emitted point and YOLO detection.

The repository is unchanged: `git status --short` is empty.

### Claims checked

- **holds** (case A) — Against tuned settings plus exclusion the N-lock's on-dancer lead disappears (0.96-0.97 vs 0.973); its real lead is held share, longest hole and jitter.  
  docs/PLAN_25M_2026-10.md §C.10 lines 214-220: shipped 0.741 / 23 % / 5.15 s / 4.6; best settings + exclusion 0.973 / 14 % / 1.51 s / 3.2; foreground only 0.96-0.97 / 6-9 % / 0 s / 1.2. Neither row is the shipped stack at D27 with the plate hook: that combination is not scored anywhere in the docs.
- **weakened** (case A) — The N-lock's other mechanisms were tried in the slot layer and rejected on replay (re-acquire anywhere, binding warm-up tracks, weak measurements).  
  The numbers are there: identity_slots.py:275-276 (0.74 to 0.64), ROADMAP_v2_REVIEW §1.2 (0.74 to 0.58-0.63), PLAN §C.4 (weak: inconsistent). All three were tried before the foreground veto existed, and the stated failure was jumping onto ghost tracks. Foreground re-acquisition itself was never built (CONTEXT §7), so it is untested, not rejected.
- **holds** (case A) — The prototype never releases a slot, so its 100 % two-point coverage and 0 s hole are by construction, and D4 is absent.  
  tmp_analysis/brainstorm-2026-10/nlock.py:4 ('Always emits exactly N points once initialised'), :30 (N and height from the manifest), :113-114 (a slot without a measurement coasts with no expiry). 132 lines, plus fg_lib.py 79.
- **weakened** (case A) — Each layer has a measured delta (synthetic blobs, frozen gate, slot layer, intermittent confirm, static guard, clean plate, D34).  
  Every number matches its source (01-continuity §2a and §3.4; REVIEW §1.1; PLAN §C.3, §C.4, §C.12, §C.16). But the tracker-side deltas were measured on single-dancer footage at the pre-D27 hangar-aerial pin (01-continuity §3.1: N = 1 throughout), so they do not show what each layer buys at the shipped recipe with two dancers.
- **weakened** (case A) — A rewrite cannot be gated today: the harness is keyed to the tracker's output and replacing the tracker voids it.  
  The scorers take any emitted stream (tests/output_quality.py:334-362). Stored field timelines carry `ref`, `fg` and `int` on every row (e.g. tmp_analysis/confirm_0710/tl/V0_slot_2: 1802 of 1802). `replay_gpu_cached` re-runs the post-YOLO chain from a cache (pipeline.py:825-859). And the committed gates do not cover what ships: 19 of 19 manifests pin intermittent confirm false; the six emitted fixtures were recorded at motion_first / 0.25, yolo_first / 0.5 and similar pre-D27 settings; `simulate` passes no belt, foreground or bounds (tests/emitted_golden.py:110).
- **holds** (case A) — The swap correctors are about 478 lines, off by default, and no config enables them.  
  tracker.py:1499-1972 holds the four methods (465 lines of method bodies). config.py:441 sets TRACKER_SWAP_CORRECTORS = False. Grep finds no `tracker_swap_correctors` key in tests/scenarios or in any JSON under projects. Their helpers have no reader outside that block (grep).
- **holds** (case A) — `_merge_duplicate_tracks` has no co-fed veto while the takeover merge has one.  
  tracker.py:3631-3660: any two established tracks with time_since_update <= 1 within 0.3 x the match gate for 8 frames, lower-hits track removed. tracker.py:3741-3751: the takeover merge requires co-fed < TRACKER_DUP_COFED_VETO. Its effect on a contact take was not replayed by either case or by me.
- **weakened** (case A) — The motion bridge costs 0.2 points of coverage when off, cuts ids from 25 to 11, and takes 45 % of tracker time at two dancers.  
  Quoted correctly (01-continuity §2a `s4_nobridge`; REVIEW §1.7). The coverage and id figures are one single-dancer take at the hangar-aerial pin (confidence 0.5, intermittent off). The bridge has not been measured at D27 or on a duo.
- **holds** (case A) — Foreground alone never creates a point; only an established tracker track opens a lost slot.  
  identity_slots.py:957-990 (entry takes candidates from the tracker's reported tracks only) and :1323-1326 (the foreground round serves only non-lost slots that have no other measurement).
- **holds** (case B) — Two estimators with split authority; the slot layer follows, for up to 3 s and as `live`, tracks the tracker hid on purpose.  
  pipeline.py:1513-1520 reads `self.tracker.tracks` for bound tracks that were not reported. identity_slots.py:871-886 follows them for `hidden_max_s` = 3.0 (:267), and :1016 emits them as STATE_LIVE.
- **weakened** (case B) — About 36 % of tracker.py serves identity purity or duplicate clean-up that a known-N estimator does not need.  
  The arithmetic reproduces from my AST spans: 552 + 303 + 185 + 304 = 1,344 lines (35 %). But 304 of those are the shadow / duplicate / takeover merges, which are ghost and duplicate control, a current requirement (B concedes this). Strictly identity is about 1,040 lines (27 %), and only the 552 swap-corrector lines are dead by default.
- **holds** (case B) — The tracker has no time base, and halving the frame rate doubles entry latency and costs coverage when detections are missed.  
  tracker.py:330 (`dt = 1.0`), :2340-2341 (no time argument), pipeline.py:989-992; grep finds no clock, fps or stall in tracker.py. My reduced re-run of the probe (3 seeds, 60 s, tracker alone): first report 0.55 s at 20 fps and 1.10 s at 10 fps; at 60 % duty 0.95 s and 1.90 s, coverage 99.6 % and 95.7 %. The full pipeline with its motion paths was not measured at half rate by anyone.
- **weakened** (case B) — On the duo proxy 89 % of uncovered dancer-frames are attributed to the estimator, not to YOLO blindness.  
  PLAN §C.2 gives 32 + 30 + 27 %, but at the April pin (motion_first, confidence 0.25). At D27 with exclusion, PLAN §C.11 counts 45 start-up frames, 0 after a hold expired and 19 after a merge, of 739.
- **weakened** (case B) — A 211-line known-N estimator fed only by clean-plate foreground beat the shipped stream (0.96-0.97 / 6-9 % / 0 s against 0.74 / 23 % / 5.15 s).  
  Numbers as in BRAINSTORM §4.2 and §4.4. Not at equal settings, plate from the take, N fixed, slots never die. On the only real-plate footage (tmp_analysis/stale_plate/tl/V2_*, post-fix) I find the plate valid on 87-92 % of frames, and a body-size blob more than 1.5 h from every emitted point and YOLO detection on 58-90 % of the frames that carry a point, recurring at a few fixed cells.
- **holds** (case B) — Association gates are advisory.  
  tracker.py:2036 accepts a pair when `raw_dist < close_accept_dist` whatever the cost. :2592-2606 forces an unmatched detection inside the creation gate onto the nearest unmatched track; the docstring at :2748-2752 says so. The only veto (:1342-1352) applies under own-height gates to a track fed last frame.
- **holds** (case B) — The Kalman filter is not used as an estimator.  
  config.py:189 R = 2.0 while gating uses a separate 700 px² term (tracker.py:1146-1148). The exported position is a separate EMA (tracker.py:572-574), then a One-Euro per slot (identity_slots.py:1092).
- **holds** (case B) — A synthetic blob detection earns full skeleton credit.  
  tracker.py:2859-2862 builds the synthetic detection with zero keypoint confidence. tracker.py:531-536 adds a hit, +1.0 warm-up and 1.0 window credit on every match with no branch on the feed source.
- **holds** (case B) — The harness already supports a shadow stream.  
  With limits. `compare_streams` scores two fixed names, so a third stream needs a small edit. A timeline's `ref` is post-size-gate and post-dedupe, centre + height + confidence only (tests/continuity.py:113-135). Its `fg` is the six largest blob centres and areas with no mask (tests/replay.py:630-635), so the support test is not available from a timeline. The detect cache has full detections and the raw gray (pipeline.py:793-795).
- **holds** (case B) — `_fg_protect` does not protect slots held by foreground alone, so the plate update can absorb a still dancer.  
  pipeline.py:1453-1468 protects STATE_LIVE slots, backed belts and tracks with a skeleton within 20 frames. foreground.py:292-318 blends everything else every 10 frames. Read in code; not measured on footage.
- **weakened** (case both) — Stage 0 is blocked on the technician and the laptop.  
  True for the two-dancer takes (ROADMAP N-10, laptop offline since 2026-10-07). But projects holds mur25m-ceinture-0610, mur25m-night-common and mur30m-0710 with their plates and takes, and tmp_analysis holds their timelines. The real-plate entry, exit and ghost guards can run on dev37 now.

### Recommended path

**Stage 0: fair baseline and oracle** (2-3 days, dev37; needs your go for about an hour of GPU replays)
- Score the shipped stack at D27 + exclusion + plate hook on both April duos and on the real-plate solo takes. Add a half-rate replay (frame skip 2) and injected stalls.
- Hand-label centroids on about 300 frames per duo take, contact phases included, and score against the labels.
- Re-record the six emitted fixtures at D27, and pass belt, foreground and bounds in their replay.
- Gate: one labelled KPI table for the shipped stream, with each loss attributed to a cause.

**Stage 1: deletions** (1-1.5 days)
- Remove the swap correctors and their plumbing (about 550 lines), the L>1 live branch, and the 22 slot parameters of the eight switched-off mechanisms. Gate: all goldens byte-identical.
- Separately, behind `camera_stall_hold`: reset track velocity after a credited stall. Gate: no runaway track in `replay.py --stall`. I read this mechanism in the code and PLAN §C.15; I did not replay it.

**Stage 2: offline shadow estimator** (5-7 days, dev37, no laptop)
- Build it only if Stage 0 shows a KPI miss attributed to the tracker boundary: warm-up, frozen gate, blob-fed drift or the rebind gate.
- N slots in seconds and heights, fed from the detect cache by YOLO detections, foreground blobs and belt. A slot opens only on a skeleton with foreground support. Exits are real.
- Gate, all of:
  - on the labelled duos, on-body +3 points or held share halved, with two-point coverage not lower;
  - on the real-plate solos with two slots, no more ghost frames than shipped and exits released as fast;
  - no guard take worse;
  - half-rate and stall replays within 1 point.

**Stage 3: the t-020 duo takes** (1-2 days once the laptop is reachable)
- The same table for shipped and shadow, with the real plate.

**Decision**
- **Shipped meets the KPI:** stop. Keep the tracker as it is; CONT-3, 4a and 11 leave the roadmap.
- **Shipped misses and the shadow passes** (2-3 weeks elapsed): run it live behind a key (logged and drawn, never sent, 2 ms p95). Then switch authority, with the old path as fallback. After one field period, delete the dormant pool, merges, motion bridge, synthetic blobs, cascade, identity cost terms and slot binding steps 2-4.
- **Shipped misses and the shadow fails** (2.5-3.5 weeks): do the tracker change set, now gated by the labelled table. Time base first, then CONT-3 + 4a + 5 behind one key, then CONT-11.

**Kept in every branch:** YOLO front end, size gate, dedupe, exclusion, height guard, foreground and belt detectors, stall clock, One-Euro, slot states and OSC contract, logger, detect cache, scorers. MOG2 stays until plate validity is proven on show takes.

Effort figures are my estimates.

### Risks

- No show-like two-dancer footage exists before t-020. Until then every duo gate rests on April footage lit off-axis with a plate cut from the take; hand labels remove the YOLO-as-reference bias but do not make that footage representative.
- Foreground reliability on a real plate. On the 30 m solo takes the plate is valid on 87-92 % of frames, and fixed spots stay body-size foreground all evening. Anything rigged or moved after the capture becomes permanent foreground, and the plate cannot be re-captured with dancers on the wall.
- If the shipped stream meets the KPI and work stops, the frame-count tracker stays. At priority 2 (less light, lower frame rate or detection duty) entry latency doubles at 10 fps and coverage drops about 4 points at 60 % duty in the tracker-alone probe; the full pipeline was not measured at half rate.
- Duo contact is governed by rules never run on show footage: the co-located merge with no co-fed veto, the 0.5 h duplicate rule and the slot merge. A failure there would appear first in the field.
- The shadow branch runs three estimators on the laptop for at least one release, on a machine with 6-10 ms of slack per frame and a GPU at 84-87 °C.
- The tracker-surgery branch is the audit's own medium-high-risk change. Its pieces measurably do not add up (YOLO-first plus blob suppression 0.947, below either alone), and it was sized on single-dancer footage.
- The simplification is smaller than the headline. With entries, exits and ghost control, a direct-fed estimator is on the order of 800-1,000 lines and 25-30 parameters (case B's own estimate), and the static-blob finding suggests its ghost rules will resemble today's.
- Until the emitted fixtures are re-recorded at D27, no committed gate covers the shipped recipe, so any stage, including the deletions, can regress the show configuration without a test noticing.
- Labelling about 1,500 frames by hand is slow and its quality decides every later gate; labels produced by an agent need your spot check.
- Opportunity cost: the operator manual, the USB stall root cause and the unreleased D34 all wait while this runs.

### Questions

- May the dev37 GPU be used for about an hour of replays (the D27 + plate baseline on the two April duos, the real-plate solos, and their detect caches)?
- When can the technician bring the laptop online for the t-020 duo takes, and does each lighting in those takes have its own empty-wall take to serve as the plate?
- Does the show contain sustained contact between the two dancers (centroids within half a body height for more than 2 s)? How long and how often?
- In the 25 m show, do dancers enter and leave during the performance, or are both on the wall from start to end? If N is constant on the wall, an 'always N' scene mode is legitimate and the bar for entries and exits is lower.
- How does the TouchDesigner patch decide a point is lost: the id missing from `/walldance/count`, a timeout, or `/dancer/state`? Can it fade on a held point? This decides whether held share or holes matter more.
- Will anything be rigged or moved on the wall after the empty-wall capture (ropes, stands, props), and does the show lighting include sources that emit at 850 nm (halogen, tungsten)?
- At 30-40 m with less light, does the camera stay at 20 fps with 50 ms exposure, or will exposure lengthen and the frame rate drop?
- Who labels the roughly 300 frames per duo take, and is your by-eye spot check acceptable as the oracle?
- Is a shift of about 0.1 body height (foreground mass centre against keypoint mean) visible or acceptable in TouchDesigner?

## Case A — keep and evolve

Keep the layered estimator and evolve it in gated steps; do not re-found it now. Four reasons.

1. The alternative's measured lead comes from the foreground signal, which the kept design already uses. The N-lock prototype scored on-dancer 0.96-0.97 against 0.74 for the shipped stream on the white duo. Against the layered stream at tuned settings plus exclusion it is 0.96-0.97 against 0.973. Its real lead is held share (6-9 % vs 14 %), longest hole and jitter. Its other mechanisms were tried in the slot layer without foreground and rejected on replay: re-acquire anywhere (0.74 to 0.64), binding warm-up tracks (0.74 to 0.58-0.63), weak measurements (inconsistent). The slot layer is already an N-target estimator taking track, belt and foreground measurements. The unbuilt part, foreground re-acquisition, is one more round behind a key.

2. The prototype does not meet the requirement. It never releases a slot, so its 100 % two-point coverage and 0 s hole are by construction, and D4 (N is a cap, dancers enter and leave) is absent. Entry, exit and ghost control are what the layers pay for, and each has a measured delta:
- synthetic blobs off: longest gap 1.57 to 4.16 s;
- frozen gate off: duplicate frames 203 to 482;
- slot layer: 15 ids and 98 holes to 1 id and 1 hole;
- intermittent confirm: white-duo two points 0.911 to 0.973;
- static guard: on-dancer 0.004 to 0.803;
- clean plate: a 13.5 s hole to none;
- D34: 28 to 2 stalls losing an id.

3. A rewrite cannot be gated today. There is no two-dancer baseline on show-like footage and no spatial ground truth. The only duo proxy is April footage scored against YOLO itself, with a plate taken from the take. The harness that decides small changes in seconds is keyed to the tracker's output: a deterministic tracker, one chain shared by live and replay, six emitted goldens with KPI floors. Replacing the tracker voids it at the moment of greatest change. The foreground hook, estimated at 1.5-2.5 days, showed two defects on the next day's takes: the plate blended on stale frames (464 phantom frames), and a hold on a blob that predated the dancer, still open.

4. The evolutionary path deletes the worst code. The swap correctors are 478 lines, off by default, and no config enables them. The motion bridge costs 0.2 points of coverage when off, cuts ids from 25 to 11, and takes 45 % of tracker time at two dancers. CONT-3 subordinates the blob-as-detection path, which produces every off-body "live" point.

The design is already moving toward slots as the N-target estimator with the tracker as a tracklet source. Finish that move in gated steps, and decide on a re-foundation only once the duo takes exist.

### Evidence

- application/src/core/tracker.py:328-348: 6-state constant-acceleration Kalman built with `dt = 1.0`; R and Q come from config.py:188-189 (2.5 and 2.0). `update()` at tracker.py:2340-2341 takes no timestamp.
- application/src/core/tracker.py:2360-2399: `update()` runs blob fusion, predict, cascaded matching, unmatched-detection rescue, motion bridge, occlusion ageing, lifecycle, emission and log, in that order.
- application/src/core/tracker.py:531-536: every matched detection adds a hit and +1.0 warm-up with no branch on feed source, so a synthetic blob earns the same credit as a YOLO skeleton.
- application/src/core/tracker.py:1499-1976 with config.py:441: the three swap correctors plus their pair-cost helper are 478 lines (my script count) and `TRACKER_SWAP_CORRECTORS = False`. A grep of every JSON under application/tests/scenarios and projects finds no `tracker_swap_correctors` key.
- application/src/core/tracker.py:13-78: 103 config names are imported, 25 of them frame-count constants by my name-pattern count (max age 45, dormant 150, warm-up 15, bridge 80, ghost skeleton age 3). The only False boolean among them is the swap-corrector switch.
- application/src/core/tracker.py:3631-3660 against 3741-3751: `_merge_duplicate_tracks` removes one of two established tracks that stay close for 8 frames and has no co-fed veto; `_merge_takeover_duplicates` has one. Read in code, not replayed on a contact take.
- Lines per concern in tracker.py (script over method spans): cost matrix and assignment 491, swap correctors 478, merges 310, motion bridge 306, dormant and resurrection 235, unmatched-detection rescue 227. The file has 3798 lines and DancerTracker has 75 methods.
- application/src/core/identity_slots.py:237-367: 83 SlotParams fields. 22 belong to features that are off as shipped: weak measurements 5, entry plausibility 6, entry travel 7, rebind-anywhere and hidden yield 2, static release 1, edge touch 1.
- application/src/core/identity_slots.py:957-990 and 1323-1326: only an established tracker track can open a lost slot, and the foreground round serves only non-lost slots that would otherwise coast. Foreground alone never creates a point.
- application/src/core/identity_slots.py:275-276: `rebind_any_after_s = 99.0` (off), with the comment that it let a coasting slot jump onto ghost tracks, white-duo on-dancer 0.74 to 0.64.
- application/src/core/pipeline.py:1044-1055 and 1513-1520: the slot layer is output-only. It reads the reported tracks and `self.tracker.tracks` for hidden candidates, and `_post_yolo_chain` returns the tracker's `scaled_tracks` untouched.
- application/tests/emitted_golden.py:98-123 and tests/test_emitted_golden.py:41-49: six emitted-stream fixtures with KPI floors. `simulate` calls `slots.update(cands, t, hidden=...)` with no belt and no foreground, so the goldens do not exercise the foreground or belt rules.
- application/tests/scenarios/*.json: all 19 manifests pin `"tracker_intermittent_confirm": false` (grep: 19 false, 0 true). The shipped default at config.py:1017 is True.
- My run on dev37, no GPU: test_emitted_golden, test_identity_slots, test_tracker_takeover_merge, test_tracker_warmup, test_tracker_cont1, test_tracker_ghost_gate and test_camera_stall give 99 passed in 17.5 s. `git status --short` was empty afterwards.
- tmp_analysis/brainstorm-2026-10/nlock.py (132 lines): line 4 says it always emits exactly N points once initialised; lines 113-114 coast a slot with no expiry; line 30 fixes N and the person height from the manifest. It has no exit path.
- git log: identity_slots.py was created on 2026-10-06 and grew by 1592 added lines in 11 commits by 2026-10-08. tracker.py changed +272/-51 in 5 commits in October. A grep of application/src finds no CONT-3, CONT-4, CONT-5 or CONT-11 marker.
- docs/PLAN_25M_2026-10.md §C.10: white duo on-dancer, held share, longest hole and jitter are 0.741 / 23 % / 5.15 s / 4.6 for the shipped slots, 0.973 / 14 % / 1.51 s / 3.2 for the best tracker settings plus exclusion, and 0.96-0.97 / 6-9 % / 0 s / 1.2 for foreground only.
- docs/BRAINSTORM_APPROACHES_2026-10.md §1 and §4.5: the report's own limits are April footage lit off-axis, YOLO as the positional reference, and a plate from the take's median or a causal running median. In the by-eye audit 9 of 64 N-lock points sat on neither dancer, all while the pair formed one blob.
- docs/audit-2026-10/01-continuity.md §3.4: `s4_nosynth` coverage 0.942 to 0.921 and longest gap 1.57 to 4.16 s; `s4_nobridge` coverage 0.940 with ids 25 to 11; `s4_nofrozen` 2-id frames 203 to 482; `s4_yfm_fuse` 0.947, below fusebox alone (0.952) and YOLO-first alone (0.957); adding YOLO-first to `excl_struct` moves ids 10 to 16.
- docs/audit-2026-10/01-continuity.md §3.1: N = 1 throughout every local recording the continuity audit used, so the CONT-3/4/5/11 evidence is single-dancer.
- docs/ROADMAP_v2_REVIEW.md §1.1: hangar-aerial-full goes from coverage 0.945, 15 ids and 98 holes on the tracker stream to 0.997, 1 id and 1 hole on the emitted stream. hangar-floor-full keeps a 24.5 s hole with slots on.
- docs/ROADMAP_v2_REVIEW.md §1.2-1.3: binding warm-up tracks in the slot gate was rejected (white-duo on-dancer 0.74 to 0.58-0.63, wall-hang 0.95 to 0.68). Every off-body live point was on a track with no fresh skeleton.
- docs/PLAN_25M_2026-10.md §C.3 and §C.8: white duo with `yolo_first`, tau 0.15 and intermittent confirm reaches two points 0.973 and hole 1.26 s. With the `ghost_spots.py` exclusion it is 0.960 / 1.51 s / coasting 0.135 / on-dancer 0.973, against KPI targets of 0.95 / 1 s / 0.10 / 0.95.
- docs/PLAN_25M_2026-10.md §C.11: with the candidate settings the white duo's frames with fewer than two points are 45 at start-up, 0 after a hold expired and 19 after a merge.
- docs/PLAN_25M_2026-10.md §C.12: the clean plate takes the dark still take s2c from 88.6 % with a 13.5 s hole to 100 % with none, but with 132 ghost frames when the plate came from the other lighting. Every night take is solo.
- docs/PLAN_25M_2026-10.md §C.15: the plate was updated on stale frames (V1 slot 4: 464 two-point frames, 0 after the fix). The V2 slot 4 second point for 6.8 s after a stall is recorded as not fixed.
- docs/PLAN_25M_2026-10.md §C.16: in the offline sweep of 229 injected stalls, stalls after which an id went missing fall from 28 to 2 and frames with an id missing from 333 to 7. The tracker still counts frames, and its Kalman sees a 1.7 s jump as one frame.
- docs/ROADMAP_v2_REVIEW.md §1.7: `tracker_update` is 15.0 / 26.3 ms p50 / p95 at two dancers on a textured wall on dev37, with 45 % in the bridge's per-track `frame_diff_blob_in_bbox` and 26 % in the Python cost matrix.
- docs/audit-2026-10/03-architecture.md §5 (line 465): the October keep-as-is list says no tracker split before ID-level goldens exist.

### Weakest point of this side

The tracker's rules do not compose, and the measured evidence says so. This is the strongest argument against me.

- **The planned fixes interact.** In the audit's own experiments YOLO-first plus blob suppression scored 0.947, below either piece alone, and adding YOLO-first to the structural set took ids from 10 to 16. Continuation-not-birth without ghost control took 2-id frames from 259 to 927 on the textured wall. So CONT-3 + CONT-4a + CONT-5 is one coupled rewrite of the association core, not three small steps, and it was sized on single-dancer footage.
- **No time base.** The Kalman runs at dt = 1 frame and 25 imported constants are frame counts. D34 had to be built around the tracker, not in it. Priority 2 (less light) threatens the frame rate those constants assume.
- **Split authority.** Two estimators are stacked, and the slot layer reads tracker internals and cannot correct the layer below. It reached 1494 lines and 83 parameters in three days and was validated on solo takes only.
- **Duo contact is unmeasured.** It is governed by at least four rules never run on show footage: the co-located merge with no co-fed veto, the 0.5 h duplicate rule, the slot merge and the creation-gate lockout. At tuned settings the white duo's only remaining loss outside start-up is "after a merge".
- **The Kalman is not doing the estimating.** R is 2 px² with a separate 700 px² gate noise, and the exported position is an EMA that freezes during a coast.
- **The harness I rely on has holes.** All 19 manifests pin intermittent confirm off while the show default is on, and the emitted goldens run without belt or foreground. I did not check which tracker settings the emitted-golden fixtures were recorded with.

A 132-line estimator matching 5,300 lines on position quality on the only duo proxy is a real signal that much of the rule base buys ghost control and entry/exit, not estimation.

### What would change this side's mind

Evidence 1 and 2 together, or 3 alone, would flip me. Evidence 4 would make me re-cost the choice.

1. **The shipped stream fails on real duo takes for tracker-internal reasons.** On the t-020 two-dancer takes, with a true empty-wall plate, per-range N and a sparse spatial ground truth, the stream at D27 plus plate misses the KPI widely (two points under 0.90, coasting over 20 %, or on-body under 0.90). Attribution must also put more than half the loss on tracker-internal causes: warm-up after a split, a forced update, a merge of two co-fed tracks, blob-fed drift. Blindness, light or setup would not count.
2. **A shadow N-target estimator wins without the fixed-N crutch.** It would take foreground and YOLO measurements straight to slots, with real entry and exit per D4 and a causal true plate. It must beat the shipped stream on those same takes and hold every guard: the static-figure and shadow takes, the textured single-dancer take, the still wall-hang, and zero ghost frames on the empty-wall night takes.
3. **The CONT-3 + CONT-4a change set fails its gates after two honest attempts.** If it cannot improve the white duo and the floor take without regressing the textured guards, the rule base has stopped composing.
4. **Production frame rate at 30-40 m measures at or below 12-15 fps.** The frame-count constants and the per-frame Kalman would then need re-deriving in seconds anyway, at a cost near that of a smaller estimator.

### Plan

**Stage 0: measure, no behaviour change.**
- Pull the t-020 duo takes; label per-range N and a sparse spatial ground truth.
- Add emitted goldens at the D27 recipe with plate and belt.
- Run `nlock.py` as a shadow scorer on the true-plate takes.
- Gate: a two-dancer KPI table for shipped and shadow.

**Stage 1: delete dead weight.**
- Remove the three swap correctors and their deferred-update plumbing, the L>1 branch in live emit, and the slot experiments replay rejected (weak measurements, entry plausibility, rebind-anywhere, hidden yield).
- Gate: golden trio, 12-scenario summaries and emitted goldens byte-identical.

**Stage 2: time base.**
- Pass elapsed time into `tracker.update`, step predict and ageing for long gaps, convert lifetimes to seconds.
- Gate: byte-identical at 20 fps; a half-rate replay keeps the same ids; an injected stall gives no runaway velocity.

**Stage 3: CONT-3 + CONT-4a + CONT-5, one change set behind a key.**
- Typed measurement source, YOLO-first tiers, blobs suppressed inside YOLO boxes, synthetic matches credited like bridge frames, continuation separated from birth, bounded tentative gates, a co-fed veto in the co-located merge.
- Gate: zero YOLO detections ignored next to a blob-fed track; duplicates no higher on any scene; floor-take hole under 10 s; textured guards no worse; Stage 0 duo KPI not worse.

**Stage 4: CONT-11 plus foreground re-acquisition.**
- Retire the bridge tiers where the plate is valid; add a foreground re-acquire round for held slots. Foreground still never opens a slot.
- Gate: coverage loss at most 0.2 points; tracker p95 at most 10 ms at two dancers; coasting at most 10 % on duo takes; zero ghost frames on empty-wall takes.

**Stage 5: decision.**
- If the duo KPI is still missed and the shadow estimator wins with all guards, make slots the sole estimator and reduce the tracker to per-frame association.

**Kept:** Hungarian cascade, warm-up integral and intermittent path, frozen gate, takeover merge, dormant pool, slot layer, One-Euro, D34 clocks, replay harness.

**Deleted:** swap correctors, bridge tiers, L>1 live path, rejected slot experiments, blobs as detections inside YOLO boxes.

### Cost and risk

All effort figures are my estimates, not measurements, except the audit's own S/M ratings.

**Cost of keep-and-evolve**
- Stage 0: about 2 days plus laptop access. The laptop has been offline since 2026-10-07 and t-020 waits on the technician.
- Stage 1: about 1 day.
- Stage 2: 2-3 days.
- Stage 3: the audit rates CONT-3 and CONT-4 at M each (2-5 days) and CONT-5 at S. With gating, about 1.5-2 weeks.
- Stage 4: about 3-4 days.
- The engine-locked goldens and the 12-scenario sweep run on the rig only, so every tracker stage needs a laptop window.

**Risks of keep-and-evolve**
- CONT-4a is rated medium-high by the audit: it touches the gate that hides zombies.
- The pieces are measurably non-additive, so Stage 3 can slide into whack-a-mole.
- Its evidence base is single-dancer footage.
- After the deletions the tracker is still about 3,000 lines with about 100 constants.
- The time-base change touches every lifetime.
- Duo-contact behaviour stays unverified until Stage 0 delivers.
- If no duo footage arrives, the plan stalls at Stage 1-2, which are safe but do not move the KPI.

**Cost of a re-foundation, for comparison**
- The prototype is 132 lines. It lacks entry and exit (D4), a causal true plate with light-change handling, contact splitting, the static and shadow ghost guards, the stall clocks, the belt, the 32 logged event kinds, and tests.
- It would re-base 54 tracker unit tests, 46 slot tests, 6 emitted goldens and 19 manifests.
- The foreground hook is the calibration point: estimated at 1.5-2.5 days, then three follow-up fixes in two days and one defect still open.
- My estimate is 3-5 weeks to parity on the guards, with no way to verify duo behaviour before t-020.
- The main risk is trading known, logged failure modes for unknown ones on a system in field test with a remote developer and a non-developer operator.

**Risk of keeping that I accept**
- A failure on duo contact in the field would come from rule interactions that nobody can predict from the code. Stage 0 exists to find that before a show does.

**Not checked**
- I did not use the GPU or run footage replays.
- I did not re-run the full unit suite; the 1028-pass figure is from the context brief.
- I did not check which tracker settings the emitted-golden fixtures were recorded with.
- The repository is unchanged: `git status --short` is empty on `remote-ops` at d2ecfde.

## Case B — re-found

The requirement (D25, ROADMAP §0) is N presence points: centroid only, swaps acceptable, fixed camera and scene, N capped. The core was built for another problem: unbounded-N identity tracking, indexed by frames, with YOLO as nearly the only sensor. When the requirement changed, every fix landed in a second estimator stacked on the first.

Verified in code:

1. Two estimators with split authority. The tracker owns association and what is "reported". The slot layer owns lifetime, re-identification and what OSC carries, and it overrides the tracker: it follows, for up to 3 s and as `live`, tracks the frozen-ghost gate hid on purpose. It grew from 30 parameters to 83 in 49 hours; 16 only interpret tracker internals.

2. About 36 % of tracker.py (1,357 of 3,798 lines) serves identity purity or duplicate clean-up that a known-N estimator does not need: swap correctors (562 lines, off by default, documented as net-harmful), dormant pool and resurrection (303), identity cost terms (185), shadow / duplicate / takeover merges (307). Another 399 lines are the motion bridge and blob-as-detection path, behind every off-body "live" point (REVIEW §1.3).

3. No time base. `update()` takes no timestamp, dt is one frame, 26 constants are frame counts, comments assume 15, 20 and 25 fps. The layers around it run on stall-compensated seconds (D34). In a synthetic probe of the tracker alone at 60 % detection duty, halving the frame rate doubled entry latency (1.0 to 2.0 s) and cut coverage from 99.4 to 96.7 %.

On the duo proxy at the April settings, 89 % of uncovered dancer-frames are attributed to the estimator, not to YOLO blindness: re-born tracks in warm-up 32 %, slots on ghosts or blob drift 30 %, a stale gate that never rebinds 27 % (PLAN §C.2). A 211-line known-N estimator fed only by clean-plate foreground scored on-dancer 0.96-0.97, held 6-9 %, longest hole 0 s, against 0.74, 23 % and 5.15 s shipped.

The re-foundation is smaller than it sounds. The slot layer is already a known-N, seconds-based estimator with typed evidence (live, belt, fg, weak) and the same gated assignment written three times. The change: feed it YOLO detections and foreground blobs directly, weight them by measurement noise instead of a priority ladder, and stop routing through tracker identity. The planned alternative (CONT-3, 4a, 5, 11) is weeks of medium-to-high-risk edits to code this would delete.

No settled decision is reopened: D24, D26 and D27 stand and nothing is split or edited; the candidate runs in shadow until its gates pass. New since the October audit: the brainstorm measurement, the slot layer's growth, D34's second clock, the end of demo pressure.

### Evidence

- TIME BASE (known_open in CONTEXT §7): application/src/core/tracker.py:330 sets `dt = 1.0` in the Kalman transition; :2340-2341 `update(detections, frame_number, motion_detector, motion_blobs)` has no time argument; application/src/core/pipeline.py:989-992 calls it without one. A grep of tracker.py for time / clock / stall / fps / seconds returns only lines 330-335.
- FRAME-COUNT CONSTANTS: tracker.py:13-78 imports 103 names from application/src/core/config.py. Of the 102 I resolved, 26 are frame counts by my reading of names and comments (e.g. TRACKER_MAX_AGE 45 at :175, TRACKER_DORMANT_MAX_AGE 150 at :181, TRACK_WARMUP_THRESHOLD 15 at :989, MOTION_BRIDGE_MAX_FRAMES 80 at :838), about 10 are per-frame rates, 4 are absolute pixels (R 2.0 at :189, Q 2.5 at :188, Mahalanobis noise 700 at :426, blob area 50 at :860). The only seconds value is the log flush interval (:409). Comments assume 15 fps (:175, :182), 20 fps (:882, :1000) and 25 fps (:980).
- LINES PER CONCERN in tracker.py (3,798 lines; AST inventory, script at tmp_analysis/audit-2026-11/estimator_refound/inv.py; the grouping is mine): swap correctors plus helpers only they call 562 (14.8 %); dormant pool and resurrection 303 (8.0 %); identity-discrimination cost terms 185 (4.9 %); shadow / duplicate / takeover merges 307 (8.1 %); motion bridge 321 (8.5 %); cold blobs as synthetic detections 78; occlusion ageing 82; unmatched-detection rules 228; emission gate and warm-up 148; association core 647; DancerTrack and Kalman 399; lifecycle, log and setup 277.
- SWAP CORRECTORS OFF AND NET-HARMFUL: config.py:441-454 sets TRACKER_SWAP_CORRECTORS = False and records that they 'false-fire on aerial/erratic motion, suppressing the REAL track -- corpus-measured off = mean -0.027'. They run only under `if self.swap_correctors:` at tracker.py:2496. `_tracks_share_recent_merge_context`, `has_recent_merge_context`, `get_dominant_vx_direction`, `_compute_single_pair_cost`, `_cascade_suppressed` and `_merge_swap_cooldown` have no reader outside tracker.py:1499-1972 and their own bookkeeping (grep).
- DESIGN CENTRE IS IDENTITY: the docstring of `_compute_cost_matrix` (tracker.py:1397-1398) says the cost blends four signals 'to disambiguate when centroids alone are ambiguous (= ID-swap scenario)'. docs/ROADMAP.md §0 line 33: 'Not a priority now: id swaps, skeleton fidelity, everything that does not change the two points TD sees.'
- GATES ARE ADVISORY: tracker.py:2748-2752 (docstring of `_get_creation_gate`): inside the creation gate an unmatched detection 'is FORCED onto the nearest unmatched track (FORCE_UPDATE) whatever the cost / Mahalanobis gates said'. tracker.py:2036 also accepts a pair when `raw_dist < close_accept_dist` even if its cost is the gated 1e6.
- KALMAN NOT USED AS AN ESTIMATOR: config.py:426-435 says the Kalman R (2.0) 'collapses the innovation cov to ~4px2' while YOLO jitter is 10-50 px, so gating uses a separate 700 px2 term (tracker.py:1146-1148). The output centroid is a separate EMA (tracker.py:572-574, alpha 0.5 at config.py:307), followed by a One-Euro per slot (application/src/core/identity_slots.py:1092). ROADMAP_v2_REVIEW §1.5 measures 55-85 ms of output-smoothing lag on fast moves and names the EMA as the lever; the KPI is lag <= 80 ms.
- LAYERS CONTRADICT, case A: `emission_gate` (tracker.py:130-132) hides a track that is skeleton-stale and slow; its stated premise is 'a still dancer keeps getting skeletons' (config.py:1062-1065). The slot layer exists partly to undo that: identity_slots.py:23-28 and :871-886 keep following such a hidden track for `hidden_max_s` = 3 s (:267), pipeline.py:1513-1520 reads `self.tracker.tracks` to fetch it, and identity_slots.py:1016 emits it as STATE_LIVE. docs/audit-2026-10/01-continuity.md §2: the frozen gate fired with the track ON the dancer in 38 % of slot-4 drop frames.
- LAYERS CONTRADICT, case B: the motion bridge is deliberately credited +0.4 with no window credit because 'bridge credit here incubates ghosts' (config.py:879-882, :1000-1002). A cold blob promoted to a synthetic detection gets a full hit, +1.0 warm-up and 1.0 window credit (tracker.py:531-536), its type being inferred from all-zero confidence (tracker.py:2135). The slot layer then refuses blob-born tracks at entry (identity_slots.py:254, 'never blob-born') while using `hits`, which those matches inflate, as its maturity test (:537).
- LAYERS CONTRADICT, case C (known_open): docs/PLAN_25M_2026-10.md §C.16: 'Unchanged: the tracker counts frames, so a stall never aged it' and 'its Kalman sees the 1.7 s jump as one frame'; §C.15: the stall 'gives the walker's lost track a runaway velocity (~200 px a frame)' and 'the orphaned slot follows that prediction'. The slot layer has two clocks and stall-widened gates (identity_slots.py:759-762, :859-861, :931-934).
- ONE QUESTION ASKED IN NINE PLACES ('is this the same body?'): pipeline.py:2387 (dedupe); tracker.py:2608 (DUPLICATE_IGNORED), :3397-3413 (slow-path separation), :3522 (shadow), :3616 (co-located merge), :3690 (takeover merge); identity_slots.py:899-904 (`is_duplicate`), :1218-1260 (`_merge_converged`), :1347 (`fg_dup_h`).
- UNBOUNDED-N SIDE EFFECT, in the code's own words: tracker.py:3695-3697 (takeover-merge docstring): 'duo/textured scenes carry 3-6 long-lived established tracks per 2 dancers, scattered 1.3-5xh apart'. The tracker's report cap is 6 by hits (config.py:71, tracker.py:3421-3426); the slot cap is 2 (config.py:321).
- SLOT LAYER GROWTH (git log on application/src/core/identity_slots.py): 45b35e4, 2026-10-06 12:46, 784 lines and 30 SlotParams fields; e3ad7de, 2026-10-08 13:56, 1,494 lines and 83 fields; 11 commits. tracker.py went 2,427 (June audit) to 3,577 (docs/audit-2026-10/03-architecture.md §2.1) to 3,798 today.
- SLOTPARAMS (identity_slots.py:236-367, 83 fields, counted by AST): 22 belong to mechanisms off as shipped (weak_* 5; entry_plausibility and its 5; entry_min_travel_h = 0 and the 5 only `_entry_travel` reads, :565-566; min_travel_h; hidden_yield_s 99; rebind_any_after_s 99; static_release_s 0; edge_touch). 11 are frame counts (:249-254, :285, :304, :333-334, :351). 16 are defined on tracker internals (hits, frames since skeleton, reported streak, hidden track, tracker centroid choice): my classification.
- THE SLOT LAYER ALREADY ASSOCIATES TYPED EVIDENCE: three gated slot-by-measurement Hungarian rounds, each with its own gate triple: rebind on tracker tracks (identity_slots.py:924-949), weak (:1137-1172), foreground blobs (:1317-1358); plus a gated belt query (:1360-1413). Evidence is applied as a strict ladder live > belt > fg > weak > coast (:1005-1062), a switch rather than a blend.
- TRACKER PRIVATES HAVE THREE OUTSIDE READERS: the slot layer (pipeline.py:1516), the exclusion step (pipeline.py:1960-1961, `time_since_update`) and the cross-validation bypass (pipeline.py:2303-2306, `time_since_update` and `_warmup_score`).
- BRAINSTORM MEASUREMENT (docs/BRAINSTORM_APPROACHES_2026-10.md §4.2 and §4.4, not re-run by me): white duo, shipped slots on-dancer 0.741, held 23.2 %, longest hole 5.15 s, jitter 4.57 % h, lag 57 ms; foreground-only N-lock with a causal plate 0.961, 9.1 %, 0 s, 1.17 %, 0 ms. Textured duo: 0.892 / 25.8 % / 7.37 s against 0.931-0.942 / 7.4-10.5 % / 1.5-1.9 s. The N-lock is git-tracked at tmp_analysis/brainstorm-2026-10/nlock.py (132 lines) plus fg_lib.py (79 lines) and is scored by the repo's own `output_quality.compare_streams` (nlock.py:123).
- LOSS ATTRIBUTION (PLAN_25M §C.2, white duo, slots on): re-born tracker track still in warm-up 32 %; both slots elsewhere (fixed ghost spot, blob-fed drift) 30 %; coasting slot never rebinds 27 %; other including tracker blind 11 %. Same section: 'The slot layer covers each dancer less often than the raw tracker stream on both duos (white 0.741 vs 0.899, textured 0.892 vs 0.924)'.
- THE TWO-LAYER SEAM CANNOT BE PATCHED FROM ABOVE: ROADMAP_v2_REVIEW §1.2: 'Tracker warm-up is now the largest cause of uncovered frames (the slot layer can only bind reported tracks)'; binding warm-up tracks in the slot layer was tested and rejected (white duo on-dancer 0.74 to 0.58-0.63). §1.3: 'Every off-body live point is a blob-fed track'.
- TRACKER-SIDE PATCHES HAVE A MEASURED CEILING: 01-continuity §0.2: an internal track existed on 100 % of slot-4 drop frames. §3.4: 'No tracker-side patch gets ids near 1 alone. The best is 10.' §2(a): with the bridge off, coverage -0.2 pt but ids 25 to 11 and 2-id frames 203 to 57.
- COUNTER-EVIDENCE 1: PLAN_25M §C.8: with the D27 settings plus the 4 exclusion spots from `ghost_spots.py`, the shipped stack reaches two points 0.960, on-dancer 0.973, coasting 0.135, longest hole 1.51 s on the white duo. ROADMAP.md N-14: on the 30 m solo takes the release keeps a point on the walker on 96-100 % of the wall frames.
- COUNTER-EVIDENCE 2: the blob path carries weight where no plate exists. 01-continuity §2(a): without synthetic detections slot-4 coverage 94.2 to 92.1 % and longest gap 1.57 to 4.16 s. BRAINSTORM §4.4: the N-lock on YOLO alone holds 33.7 % (white) and 40.5 % (textured) of its points.
- SYNTHETIC PROBE RUN IN THIS PASS (tracker alone, no motion model so no bridge and no blobs, which is pessimistic for the shipped pipeline; one dancer, 190 px, swing peaking at 2.1 h/s, 120 s, 8 seeds, intermittent confirm on; script tmp_analysis/audit-2026-11/estimator_refound/timebase2.py): detection duty 1.0, first report 0.55 s at 20 fps and 1.10 s at 10 fps; duty 0.6, 1.00 s and 2.00 s, coverage 99.4 % and 96.7 %, median longest hole 0.13 s and 0.50 s; duty 0.45, coverage 95.7 % and 88.7 %.
- SAME PROBE, AGAINST MY SIDE (timebase.py in the same folder): with a detection on every frame the tracker kept one reported id at 10 fps on swings up to 4.7 h/s, and after each injected 1.7 s stall it lost only 0.25-0.40 s (2 to 3 extra internal tracks minted, 1 to 2 reported ids). The frame base alone does not break it; it breaks in combination with missed detections.
- HARNESS ALREADY SUPPORTS A SHADOW STREAM: application/tests/slot_replay.py:70-91 re-runs an output layer from a timeline in seconds, no GPU. Timeline rows carry `ref` (YOLO detections in original px with box confidence, application/tests/continuity.py:113-135), `fg` (plate validity, gain, up to 6 blobs, application/tests/replay.py:630-635) and `int` (replay.py:418). `compare_streams` scores each named stream with the same code (application/tests/output_quality.py:334-362). The detect cache stores detections, tracker space and the raw ROI gray (pipeline.py:793-795, :826-853). `replay.py --stall` injects stalls (replay.py:794). Six emitted goldens exist under application/tests/golden/emitted.
- COST AND FRAGILITY OF THE FOREGROUND: application/src/core/foreground.py:22 states about 1-2 ms per frame on the laptop CPU; ROADMAP_v2_REVIEW §1.7 puts the tracker at about 6 / 10 ms p50 / p95 on the laptop with two dancers, 45 % of it in motion-bridge connected-component queries. A frame where more than 25 % of the ROI differs gives no foreground at all (foreground.py:257-261). `_fg_protect` (pipeline.py:1453-1468) protects STATE_LIVE slots, backed belts and tracks with a skeleton within 20 frames, not slots held by foreground alone, so the plate update (foreground.py:292-318) can absorb a still dancer YOLO does not see.
- SETTLED CONTEXT: 03-architecture.md §5 line 465: 'The tracker monolith: no split before ID-level goldens exist (ARCH-13).' ROADMAP.md §5 lines 179-182 lists 'replacing the tracker's blob-as-detection path by the foreground' under LATER; CONT-3, CONT-4a, CONT-5 and CONT-11 are planned and not done (ROADMAP.md §4 P-3, P-4; §5).

### Weakest point of this side

The measured advantage does not survive a fair comparison yet.

- The 0.96-0.97 against 0.74 figure compares a crude N-lock with the shipped stream at the April pinned settings. With the settings that are now the code default (D27/D33) plus four excluded ghost spots, the shipped stack scores on-dancer 0.973 on the same take (PLAN §C.8). What remains of the gap is held share (6-9 % against 13.5 %), longest hole (0 s against 1.51 s), jitter, and not needing exclusion painting.
- The reference in every one of those numbers is YOLO itself, the footage is April footage lit off-axis, and the plate was the take's own median or a causal running median, never a real empty wall. No two-dancer show-like footage has been scored by anyone (CONTEXT §6).
- The N-lock gets 100 % two-point coverage by construction because it never lets a slot die. D4 forbids that. Once birth and death are added, the entry, hold, edge-release and static-ghost rules come back, and those are where most of the slot layer's parameters already live.
- Foreground as a primary measurement inherits real defects: it goes invalid as a whole above 25 % ROI difference, it cannot be re-captured once dancers are on the wall, it can absorb a still dancer, it is biased about 0.1 h by off-axis shadows, and two dancers in contact are one blob (9 of 64 audit points were forced off-body in that case). Without a valid plate the estimator is roughly the YOLO-only N-lock (34-40 % held), which is worse than the shipped tracker with its motion paths.
- My own probe shows the frame base alone is not fatal: with a detection on every frame the tracker held its id at 10 fps and lost under half a second after a 1.7 s stall.
- Part of what I count as "identity" code (the merges, the emission gate) is ghost and duplicate control, which is a current requirement. A known-N estimator removes the need for those particular mechanisms but still needs its own ghost control.

### What would change this side's mind

Any one of these, in order of weight:

1. The shipped stream, at the D27 settings with the real empty-wall plate, meets the demo KPI on the two-dancer rehearsal takes (two points >= 95 %, longest hole <= 1 s, coasting <= 10 %, on the body >= 95 %, jitter <= 1.5 % h, lag <= 80 ms), scored against labelled centroids. Then there is nothing to re-found for priority 1, and CONT-3 / CONT-4a become optional.
2. The Stage 1 shadow fails its gate: it does not beat the shipped stream by at least 3 points on-dancer or half the held share on at least three duo takes with a real plate, or it is worse on any guard take (static figure, shadows, still wall-hang, exits, empty wall).
3. The plate is invalid on a material share of real show time (stale frames, light drift at sunset, show lighting in the 850 nm band). Foreground then cannot be a primary measurement, and the MOG2 paths have to stay.
4. A half-rate and stall-injected replay of the full shipped pipeline (motion bridge included) stays within 1-2 points of its full-rate score on real takes. The time-base argument would then be cosmetic. I did not run this: footage replays were outside this brief.
5. Identity across crossings moves up from "later" to a show requirement. The tracker's shape and trajectory costs would then have a job again, though the swap correctors' measured net harm argues that even then they are the wrong tool.
6. Evidence that CONT-3 plus CONT-4a, done as planned, removes the warm-up and blob-drift losses (62 % of the duo attribution) for less work than Stage 1. The audit's own stand-ins did not: the best tracker-side combination left 10 ids and needed the slot layer to reach 1.

### Plan

Stage 0, oracle (precondition). Process the two-dancer takes (t-020) and hand-label centroids on about 300 frames per duo take, April duos included. Gate: a two-dancer KPI table for the SHIPPED stream scored against labels, not against YOLO. If the shipped stream meets the demo KPI, stop.

Stage 1, offline shadow (dev37, no GPU). New `core/estimator.py`: N slots, constant-velocity filter in seconds and heights, typed measurements (skeleton, foreground, belt) with per-type noise, one gate growing per second, birth on skeleton plus foreground support, death on absence of evidence. New `tests/shadow_replay.py`, a sibling of `slot_replay.py`, reads `ref` and `fg` from timelines and writes a `shadow` block that `compare_streams` scores as a third stream. Gate: on each duo take, on-dancer +3 points or held share halved against shipped; no guard take worse (static figure, shadows, wall-hang, aerial, floor, night solos); no extra ghost frames on empty-wall takes; half-rate decimation and `--stall` injection within 1 point. Deleted whatever the outcome: the swap correctors and the 22 slot parameters replays already rejected.

Stage 2, live shadow (key `shadow_estimator`). Runs after the slots on the same detections, foreground frame and belt hook; logged in FRAME_SUMMARY, drawn as a third ball, never sent. Gate: 2 ms p95 on the laptop; disagreement report and a 32-hard-frame audit per take over two rehearsals; plate valid on 95 % of frames.

Stage 3, swap authority (key `output_source`, remote-settable; the old path keeps running as the shadow). Gate: demo KPI on the laptop with TouchDesigner, and Thomas's eye. Deleted: the RTS path and the box EMA.

Stage 4, delete, after one field period without fallback: dormant pool, merges, motion bridge, synthetic blobs and cascade in tracker.py, then the rest; slot binding steps 2-4 and the 16 tracker-interpreting parameters; the cold-blob gate and the crossval track bypass.

Kept: YOLO front end, size gate, dedupe, exclusion, height guard, ForegroundDetector, BeltDetector, CameraStallClock, OneEuro2D, state vocabulary and OSC contract, TrackingLogger, detect cache, goldens, output_quality. MOG2 stays as a typed weak measurement for frames without a valid plate.

### Cost and risk

EFFORT (my estimates, not measured). Stage 0: 2-3 days, and it is blocked on the technician and the laptop (t-020). Stage 1: 4-6 days. Stage 2: 2-3 days plus a release. Stages 3-4: 1-2 weeks elapsed, most of it field time. For comparison the planned tracker work is CONT-3 (M), CONT-4 (M, risk rated medium-high), CONT-5 (S), CONT-11 (S-M) in 01-continuity §4, where M is 2-5 days. Stages 0 and 1 are worth doing even if the answer is "keep": Stage 0 produces the first two-dancer baseline, and Stage 1 turns the brainstorm's one-off into a repeatable gate.

WHAT IT COSTS WHILE IT RUNS. During Stages 2-3 the laptop carries three estimators instead of two, so there is more code, not less, for at least one release, on a machine with 6-10 ms of slack per frame and a GPU at 84-87 C. The 2 ms p95 gate is there for that reason.

RISKS
1. No oracle. If Stage 0 does not happen, every later gate rests on YOLO as its own reference, which favours YOLO-fed streams on fast-move error and penalises the foreground's shadow bias.
2. Plate dependence. Whole-frame invalidation at 25 % ROI difference, no re-capture mid-show, absorption of a still dancer into the plate, off-axis shadow bias, contact giving one blob, ropes and light sweeps as foreground. The fallback without a plate must be at least as good as today's motion paths, which is why MOG2 stays and why the 16-23 ms motion worker may not be deletable.
3. The simplification is smaller than the headline. With entries and exits (D4) and ghost control, a realistic end state is on the order of 800-1,000 lines and 25-30 parameters, not the 211-line N-lock. That is an inference from which slot rules have no known-N substitute.
4. Regression of validated behaviour. The shipped stack holds 96-100 % on the 30 m solo takes and sits under 1,028 unit tests and replay-fitted constants. A new estimator starts with no field hours. Shadow running and a one-key fallback limit this; they do not remove it.
5. A different point on the wire. OSC sends the confidence-weighted keypoint mean today; a foreground mass centre sat a median 0.10-0.13 h lower on the April footage. TouchDesigner would see that unless the learned offset works.
6. Stage 4 removes `TrackingMode`, which the October keep-list holds as live configuration in 4 projects. That needs an explicit decision, not a side effect.
7. Opportunity cost. The project is idle with no show date; the operator manual, the USB stall root cause and the unreleased D34 are also waiting, and none of them is helped by this.

NOT CHECKED. No GPU run and no footage or cache replay (outside this brief), so the half-rate behaviour of the full pipeline is unmeasured. The brainstorm and PLAN numbers are quoted, not re-run. The two-dancer rehearsal takes were not seen. Counts by category (the 36 %, the 26 frame counts, the 16 and 22 parameters) rest on my own grouping of methods and fields; the scripts are in tmp_analysis/audit-2026-11/estimator_refound/. The repo was not modified: `git status --short` is empty after the last command.
