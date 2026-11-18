# Production regime — n-greater-than-2

*Third pass, run after the owner confirmed this regime is part of the production choreography. The real chain (dedupe, cross-validation, tracker, identity slots, OSC) was driven on CPU with synthetic detections at the shipped defaults. This proves the logic path and its time constants. It does not say how often the path is taken on real footage.*

**Severity for production: high** · confidence medium

## What the shipped code does

Detection filters (pipeline.py:2387-2492) work in units of ONE person_height_px: a size gate 0.3-2.5x (only in frames with >= 2 detections), then a "shadow" rule that deletes any weak skeleton (< 8 keypoints or mean conf < 0.5) within 1.5 heights of a strong one. The tracker derives every gate from the same global height (tracker.py:1005-1020): an unmatched detection inside 0.825 h of any track cannot start a track (2572-2636, 2744-2762), and a tentative track moving in step with a better-seen neighbour within 1.43 h is killed as its shadow (3522-3614). The report cap is max_persons = 6, top-K by hits (3415-3436).

Slots: max_dancers = 2 by default (config.py:321; Live slider 1-8, gui_builder.py:1071). Only lost slots accept entries (identity_slots.py:957-990), so a third dancer is tracked and drawn but never sent, and nothing alerts: the only cap alert is on max_persons (ops_monitor.py:462-477). With more bodies than slots, the unslotted dancer sits permanently in the rebind pool: a slot whose track misses one update takes it inside 1 h + 2 h/s (867-870, 924-949), and a slot static for 5 s yields to it and is barred as a "ghost spot" (1174-1205). With cap 4 and three dancers, the spare slot goes to any established track, a YOLO-confirmed wall figure included, until a moving dancer needs it.

Four well-separated dancers with cap 4 behaved like the validated duo in the synthetic runs (coverage 0.94-0.99, no id moves). Cost: dedupe is quadratic in detections, tracker and slots roughly linear per dancer; N = 4 costs about twice N = 2 on the CPU after YOLO. OSC sends 4N+1 datagrams per frame (osc_output.py:173-185). DETECTION_DEFAULTS (config.py:1041-1050) carries no N key; the known-N tools take N from a manifest's expected_count and score the pre-slot stream (tests/tune.py has no max_dancers). None of this has run on footage with more than two IR dancers.

## Rules that decide the outcome

| Stage | Rule | Where | Effect | By design |
|---|---|---|---|---|
| identity slots / cap | N output ids = max_dancers (default 2); only lost slots accept an entry; extra established tracks are not sent | config.py:321; identity_slots.py:238, 481-485, 957-990; gui_builder.py:1071-1079 | Cap 2 with 3 dancers: the third dancer had coverage 0.000 over 15 s, and 19.15 s without a point in the staggered run (until another dancer left and its 2.05 s hold expired) | yes |
| identity slots / first entry | Never-used slots cost 1e3+sid for every track, so slots go to the first tracks in tracker order | identity_slots.py:978-982 | Which dancer is left out = YOLO's output order on the birth frame: order a,b,c kept a,b; reversed order kept c,b | unclear |
| identity slots / rebind | A slot whose bound track is alive but missed this frame's update counts as free and takes any established unbound track within 1 h + 2 h/s (max 3 h) | identity_slots.py:867-870, 924-949 (docstring 34-35 says only a slot 'whose own track is gone') | With a spare dancer always in the pool: the id moved on the 2nd missed YOLO frame (spare dancer 1.2 h away); the first dancer then had no point for 20.95 s (run end) | no |
| identity slots / static yield | All slots emitting + an established moving skeleton-backed track left out: a slot static 5 s (p90 spread < 0.06 h) yields, its place becomes a ghost spot, and a static track cannot enter | identity_slots.py:1174-1205, 640-689; config.py:326 (ON) | A motionless real dancer lost its id to the 3rd body (cap 2) and to a 5th body (cap 4): 34.05 s and 39 s without a point (run end), 424 entries blocked, slot 4 empty for 17.9 s while the dancer was detected every frame | unclear |
| identity slots / spare slot | A lost slot accepts any established track anywhere (3 reported frames, 12 hits, skeleton within 3 s); the static test needs 5 s so it never blocks a first entry; static_release OFF, entry travel OFF (D31); foreground veto only with a valid plate | identity_slots.py:532-558, 681-686, 968-990; config.py:330; docs/ROADMAP.md:229 | Cap 4 with 3 dancers: a static YOLO figure held D4 for 603 frames (588 when detected 12 on / 8 off) until a 4th dancer took it 0.95 s after entering. Cap 2 with 2 dancers and the same figure: 0 ghost frames | yes |
| tracker / report cap | max_persons = 6: top-K by hits, older id wins; over_cap alert after 10 s; project key only, no GUI control | tracker.py:3415-3436; config.py:71, 1108; ops_monitor.py:462-477; app.py:1445-1446 | 3 dancers + 3 static figures: the entering 4th dancer was the 7th confirmed track and was cut: coverage 0.000 for 30 s; one over_cap alert 10 s after | yes |
| detection filter / shadow suppression | A detection with < 8 keypoints over 0.3 or mean conf < 0.5, within 1.5 x the global person height of a strong one, is deleted before the tracker | pipeline.py:2434-2469; config.py:236-239 | Dim dancer 1.2 h beside a bright one: 400 of 800 detections deleted, coverage 0.000 for 20 s; the same dim dancer alone or 2.0 h away: 0.955. Rate on wall footage not checked (0.7 % of skeleton rows are that weak on the Bordeaux floor takes, bodies 180-690 px) | unclear |
| tracker / birth gate on one global height | An unmatched detection within 0.55 x person_height (x1.5 away from the left/right edges) of any track never starts a track; own-height scaling applies only below half the global height | tracker.py:2572-2636, 2744-2762, 1321-1330; config.py:199, 295, 489; pipeline.py:986 | person_height 150 with two 90 px dancers 1.2 own-heights apart: one point for 16.3 s (fine with height 90). Equal heights 0.7 h apart: 5.55 s, ended only by a motion-blob birth 108.7 px away (gate 102) | unclear |
| tracker / shadow tracks | A tentative track (< 15 hits) within 1.43 h of a better-skeleton track moving the same way (cos >= 0.8) is killed; the 12-frame streak is counted twice per frame | tracker.py:3522-3614; config.py:239-242 | Two dancers descending together 1.2 h apart, one dimmer (keypoint conf 0.62 vs 0.80): 15 KILL_SHADOW in 6 s, 6.8 s without a point. Equal brightness or 2.0 h apart: 0.8 s | unclear |
| entry and exit timing | Tracker warm-up of 15 matched frames, then slot entry (3 reported frames, 12 hits); smart hold after a loss | config.py:989; identity_slots.py:252-254, 1106-1135 | Every (re-)entering dancer: 0.8 s without a point at perfect detection, 0.9-1.8 s at 50-75 % YOLO duty. After an exit the id stayed 2.05 s (the 0.3 s edge release did not fire in these synthetic exits; cause not isolated) | yes |
| per-frame cost | Dedupe compares every ordered pair in Python; cost matrix is O(D x T^2) (separation penalty per cell); one belt window per live slot; 4N+1 OSC datagrams | pipeline.py:2473-2518; tracker.py:1394-1494, 1205-1233; identity_slots.py:1360-1413; osc_output.py:173-185 | dev37 thread-CPU ms per frame after YOLO, N = 1/2/4/6: 6.7 / 14.1 / 27.3 / 39.9 on a clean synthetic scene; 04-performance.md:348 puts a real solo scene's tracker at about 6 ms on the same box against 2.0 here | unclear |
| operator feedback | No alert when an established track has no slot; the stats row counts tracker tracks, the ids row lists slots | ops_monitor.py:405-477; main_loop.py:1116, 1169-1184, 1019-1043 | 0 alerts in every run with more bodies than slots; the preview shows a T skeleton without a D ball | no |

## Reproduction

Ran CPU only, no model, no footage; repo untouched (git status clean). Scripts: tmp_analysis/audit-2026-11/n_dancers/harness.py, tmp_analysis/audit-2026-11/n_dancers/run_scenarios.py, tmp_analysis/audit-2026-11/n_dancers/run_extra.py, tmp_analysis/audit-2026-11/n_dancers/run_timing.py; outputs in the same folder (scenarios_A.out, scen_B/C/D/E/S.out, timing.out, profile.out).

They drive the real FrameProcessor(model=None): _guard_person_height -> _filter_duplicate_detections -> replay_gpu_cached (MOG2 feed + _post_yolo_chain: crossval, cold blobs, DancerTracker.update, IdentitySlots.update, OSC to loopback) and the real HealthMonitor at 1 Hz, with synthetic 17-keypoint detections (125 px bodies) and a synthetic gray at 20 fps. Shipped defaults: max_dancers 2 unless stated, max_persons 6, static guard / smart hold / belt ON, no plate.

Results:
- Cap 2, 3 dancers: third dancer coverage 0.000 (15 s); reversed detection order leaves a different dancer out; staggered entry: 19.15 s without a point; alerts none.
- Cap 2, slotted dancer missed 0.5 s, spare one 1.2 h away: id moved on the 2nd missed frame; 20.95 s hole.
- Cap 2, one dancer motionless, a third walks in: yield 0.95 s later; 34.05 s hole (run end).
- Cap 4, 3 dancers + static YOLO figure: 603 ghost-point frames (588 at 12 on / 8 off) until a 4th dancer took D4. Cap 2, 2 dancers, same figure: 0.
- Cap 4, 4 separated dancers, YOLO duty 1.0 / 0.75 / 0.5: coverage 0.944-0.987, longest hole 0.8-1.8 s, 0 id moves.
- Cap 4, 4 dancers (one motionless) + a 5th moving body: 39 s hole, 424 blocked entries.
- person_height 150, two 90 px dancers 1.2 own-heights apart: 16.3 s with one point.
- Dim dancer 1.2 h beside a bright one: 400/800 detections deleted, coverage 0.000; alone or at 2.0 h: 0.955.
- Two dancers descending together 1.2 h apart, one dimmer: 15 KILL_SHADOW, 6.8 s without a point.
- 3 dancers + 3 figures + a 4th dancer: coverage 0.000 for 30 s; over_cap alert 10 s later.
- Thread-CPU ms/frame (dedupe + chain), dev37 at load ~6, N = 1/2/4/6: 6.7 / 14.1 / 27.3 / 39.9 (tracker.update 2.0/4.4/9.2/13.0; slots 2.2/4.3/7.0/9.0; dedupe 0/1.1/4.6/9.5). The first full pass, at machine load ~40, read 20-30 % higher.

Synthetic inputs prove the logic path only. Real footage would give the rates (YOLO misses, dim skeletons, false figures) and the laptop cost.

## Existing coverage

Unit tests: test_identity_slots.py:118 (3 tracks with N = 2, asserts only that two ids are sent), :308 and test_identity_slots_pipeline.py:93 (resize to 4 / 3 only), test_tracker_max_persons.py (top-K cap through the private _collect_confirmed_tracks). No test drives DancerTracker.update or the slot layer with three or more dancers. Goldens: all six emitted goldens use max_dancers 1 or 2 (tests/golden/emitted/*.golden.json); the three tracker goldens are solo. Scenarios: one N = 4 manifest, tests/scenarios/facade-ghosts.json (daylight phone footage, class S, no pass line, no max_dancers); 5 manifests with N = 2, 10 with N = 1. Docs: audit-2026-10/01-continuity.md:328-330 (4 dancers on phone footage, pre-slots: 42 ids in 20 s, ghost rate 1.21); PLAN_25M_2026-10.md:562, 569 (solo walker with Max dancers 2 keeps a second point for 6.8 s: the spare-slot case on real footage). No IR take with more than two dancers exists.

## What the technician and TouchDesigner see

Shipped cap 2 with three dancers: TD gets ids 1-2; one dancer has no point for as long as the other two stay (15-19 s in the runs). Which one is decided by YOLO's output order, then changes: a slotted dancer missed for 0.1 s near the spare one, or hanging still 5 s, hands its id over and the point jumps to another body. The technician sees three T skeletons, two D balls, "Dancers 3", "ids: D1 live | D2 live"; no alert. With Max dancers 4 and three dancers, a YOLO-confirmed wall figure gets D4 within 0.8-1.6 s: TD renders a fourth dancer until a real one claims the id. Beyond six confirmed tracks the newest dancer is cut; one alert 10 s later.

## Smallest fix inside the current design

1. Treat N as a show setting: Max dancers set at Rig and saved; max_persons = max(6, 2 x max_dancers); a persistent alert when an established skeleton-backed track stays unslotted for more than 3 s (counter in IdentitySlots.update, rule in HealthMonitor).
2. identity_slots.py:924: a slot whose bound track is still alive is not free for rebind.
3. Static yield: never evict a slot with reputation (_reputed) and never mark its place a ghost spot.
4. Per-body scale: lower TRACKER_OWN_HEIGHT_TRIGGER toward 1.2; use max(h_i, h_j) instead of the global height for the dedupe shadow radius; exempt detections overlapping a confirmed track from shadow suppression and from tentative-track shadow kills.
5. Spare slots: require belt or foreground evidence, or travel (D31), for an entry while fewer dancers than the cap are on the wall.
6. Vectorise _should_merge and cache the separation means.
Each needs a 3-4 dancer take before release.

## What a re-founded estimator would do

State = N targets, with N given per scene by the technician or a cue from TD (count up / down), not discovered. Each target carries its own position, velocity (in seconds), body scale and presence (on the wall, or off at a declared edge, roof and floor included). Each frame, one global assignment of typed measurements to targets: skeletons (noise from keypoint confidence), clean-plate blobs (split by the known count when one blob covers k targets), belt points; gates in the target's own height and elapsed time. No birth from an unassigned detection: a persistent strong one raises "N+1?" to the technician instead of a point. A target without a measurement keeps its id and a predicted point with growing uncertainty; stillness is a zero-velocity prior, never a "static ghost". So there is no spare slot for a ghost to take, no id hand-over, no report cap, no second identity layer; cost O(N x M), vectorised.

## Footage that would confirm or refute it

1. One 3-4 dancer take on the IR wall at show light, Max dancers 4, tracker JSONL and [Budget] timing recorded with TD running. About 6 min: all on the wall more than 2 h apart; a group within 1 h; one hanging still 15 s or more while the others move; two leaving and re-entering (one by the roof, one by the floor); two descending side by side. Ground truth: a spoken count per range plus hand-marked positions about once a second (or belts).
2. The same take replayed with Max dancers 2 and 6 (no new shooting): the over-subscribed and spare-slot cases.
3. Two dancers on the wall with Max dancers 4 for 3 min, starting from the empty wall: ghost points per minute on spare slots.
4. One dancer at the top and one at the bottom of the tall wall at once, then a pair side by side near the top: the real apparent-height ratio.
5. The unprocessed two-dancer rehearsal takes (hub task t-020) replayed with Max dancers 4.

## Questions for the owner

- What is the largest number of dancers on the wall at once, and is the count per scene known in advance (a cue list)? Could the technician or TouchDesigner send the current count?
- Is the TouchDesigner patch built for ids above 2 (how many generators), and what should it do when an id jumps from one dancer to another?
- When there are more bodies than ids, who should go without a point: the newest arrival, the stillest, or never a dancer who already has one?
- Is a wrong extra point (a ghost on a spare id) worse for the piece than a missing point for one or two seconds? This decides the spare-slot entry rule (D31).
- Are non-dancers ever in the camera's view during the piece (riggers at the roof edge, belayers at the wall base, audience), or is the ROI the wall only?
- How different are apparent sizes on the production wall (top against bottom), and do dancers stay within about one body height of each other for long, such as side-by-side descents or stacked in one column?
- Will every dancer wear the IR belt (D19)? With N known it could be the per-dancer presence sensor.
- If four dancers cost more CPU than the 6-10 ms of slack measured at two, what frame-rate floor is acceptable, and can a rig measurement with TD running be scheduled?
