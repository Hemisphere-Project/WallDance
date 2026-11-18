# Production regime — long-stillness

*Third pass, run after the owner confirmed this regime is part of the production choreography. The real chain (dedupe, cross-validation, tracker, identity slots, OSC) was driven on CPU with synthetic detections at the shipped defaults. This proves the logic path and its time constants. It does not say how often the path is taken on real footage.*

**Severity for production: high** · confidence high

## What the shipped code does

Detection. A still body has no frame-diff motion, so a YOLO box passes cross-validation only via the track bypass (track fed within 5 frames, cell motion-confirmed within 15) or a strong skeleton (8 keypoints above 0.3, mean 0.45). Steady YOLO keeps the bypass open indefinitely. One 6-frame gap closes it for good; a dim skeleton is then rejected every frame until she moves.

Motion. MOG2 absorbs a still body after 110 frames (5.5 s). In low light at the default threshold a +30 DN body never enters the mask.

Tracker. After a YOLO miss the track is hidden within 4 to 7 frames, bridged at most 80 frames and only while MOG2 still shows her, and dies after 135 frames (22 near a side). A returning skeleton must match 15 frames before the id is reported again. The intermittent path requires travel, so it never helps a still dancer.

Slots. The slot follows a hidden bridged track for 3 s; otherwise it coasts at once. A slot still for 5 s counts as static: 1.5 s hold, then the id leaves OSC. With a valid plate and reputation the foreground holds up to 30 s. A foreground-held slot is not protected from the plate update: a faint body is absorbed in about 20 s, and the foreground veto then refuses her re-entry while she stays put. A belt holds 8 s, or without limit when the plate backs it.

Yield. When every slot emits, any unbound moving track takes the still dancer's slot, and her spot is blocked until she moves.

Next to a partner: his motion in adjacent cells keeps her bypass open (read, not run).

Return. The point comes back 0.6 to 0.9 s after she moves, on the same slot id if free.

## Rules that decide the outcome

| Stage | Rule | Where | Effect | By design |
|---|---|---|---|---|
| detection filter: motion cross-validation | Scored gate: a detection is kept only by the live-track bypass (track fed within 5 frames, warm-up at least 2, its cell or a neighbour motion-confirmed within 15 frames), by frame-diff motion of at least 2 %, or by a strong skeleton (8 keypoints above 0.3, mean at least 0.45). A skeleton pass does not refresh the motion cell. | application/src/core/pipeline.py:2296-2320, 2370-2382; application/src/core/config.py:935-939, 962-971 | Steady YOLO: kept by the bypass on 400 of 400 still frames. After one gap of 6 frames the bypass never reopens for a still body; a dim skeleton is then rejected on every frame (194 of 194 in the run). Real s2c frames 4552-4760: 11 s of detections at confidence about 0.2 with no track at all (rejection reason inferred, the timeline does not record it). | yes |
| motion model: MOG2 | learningRate 0.001 is passed to apply(), so history 500 is unused; in low light (mean under 55) the varThreshold is doubled. | application/src/core/motion_detector.py:5-8, 269-275; application/src/core/config.py:840-842, 950 | A still body leaves the mask after 110 frames (5.5 s), all pixels at once, at every contrast tested. On a 30 DN wall a +30 DN body never enters the mask. The module docstring says dancers 'remain foreground indefinitely'. | no |
| motion model: frame-diff | The raw frame pair advances only when the global peak difference exceeds 8 DN; a pair frozen for more than 30 frames reports zero. | application/src/core/motion_detector.py:251-266, 544-547; application/src/core/config.py:980 | Frame-diff share inside a still body's box measured 0.0000. It gives no support to cross-validation, to cold blobs or to the bridge's third tier. | yes |
| tracker: emission | Frozen-ghost gate: no keypoint above 0.3 for more than 3 frames and speed under 0.03 x person height hides the track. | application/src/core/tracker.py:126-133, 523-524; application/src/core/config.py:1060-1071 | A still track is hidden 4 frames after its last real skeleton, even when YOLO boxes without a keypoint above 0.3 still match it every frame (run: hidden from 0.15 s, point lost at 5.2 s, hole 9.3 s). Real s2c: 'frozen' from frame 330. The stated premise 'a still dancer keeps getting skeletons' fails in the dark. | yes |
| tracker: confirmation | Warm-up integral: +1 per match, -0.8 per miss, cap 20, threshold 15. The intermittent path needs 12 credits in 40 frames and travel of 0.5 x box height. | application/src/core/tracker.py:394-397, 429-436, 447-454, 534-535; application/src/core/config.py:989-1008 | The track is unreported after 7 missed frames. A returning skeleton on the same live track must match 15 frames (0.7 s) before the id is emitted again. Intermittent detections on a still body never confirm (real s2c: tracks 20, 21, 22 stayed in warm-up). | unclear |
| tracker: motion bridge and expiry | Local MOG2 blob bridge, at most 80 frames; presence tier does not reset the miss counter; frame-diff tier. Expiry after 135 missed frames for an established track, 22 near a left or right edge. | application/src/core/tracker.py:2889-2895, 2963-3033, 3063-3077, 3281-3290, 3314 | Bridges only a dancer who stopped less than 110 frames ago, for 80 frames (real s2c: frames 322 to 402). A dancer still for longer gets no bridge. The track dies 6.75 s after the last feed (1.1 s near a side); a later detection is a new tracker id. | yes |
| identity slots: continuation | A bound track that was not updated this frame gives the slot no measurement. A bound track the tracker hides but still updates is followed for at most 3 s. | application/src/core/identity_slots.py:267-268, 867-881; application/src/core/pipeline.py:1513-1520 | The slot reads 'live' for 3 s on a bridged hidden track (real s2c: 16.3 to 19.3 s). Without a bridge it goes to belt, foreground or coast on the first missed frame. | yes |
| identity slots: hold | Smart hold: a slot whose last 5 s of positions stay within 0.06 h (p90 from the median) is static and gets hold_min_s = 1.5 s instead of 2 s. | application/src/core/identity_slots.py:647-664, 1106-1121 | The id leaves OSC 1.5 s after the last measurement. On real s2c every 5 s window of the still person is under the threshold (median 0.0106 h, max 0.0588 h), so a real still dancer is always 'static'. | yes |
| identity slots: foreground hold | A slot that would coast follows the plate's foreground blob. Cap: 30 s since the last live or belt measurement for a slot with reputation (1 s of skeleton-backed tracking and 0.25 h of travel since entry), 1.5 s otherwise. | application/src/core/identity_slots.py:361-362, 1309-1326 | Fills every YOLO gap up to 30 s (run: YOLO stopped at 5 s, held to 34.95 s, lost at 36.45 s; real s2c with the matching plate: no hole). A dancer whose slot was re-entered in place has no travel and gets 1.5 s only. | yes |
| clean plate: selective update and veto | Every 10 frames the plate blends 2 % toward the frame except under protect boxes: live slots, backed-belt slots, reported tracks with a skeleton within 20 frames. A track with under 10 % foreground cannot rebind or enter. | application/src/core/foreground.py:292-318; application/src/core/pipeline.py:1453-1468, 1527-1528; application/src/core/identity_slots.py:543-546 | A slot held only by foreground is not protected. A +5 DN body (2.5 x the threshold) is absorbed after about 20 s. When YOLO then returns on the still body: 770 vetoes, no point for 20 s, until she moves; a ghost blob stays where she was. A +30 DN body is not absorbed within 35 s. | no |
| identity slots: static yield | When every slot emits, a slot static over its last 5 s gives its id to an unbound, established, moving track. Its position becomes a 'ghost' spot that blocks entry within 0.5 h for 30 s, refreshed while a static track stands there. | application/src/core/identity_slots.py:288-294, 681-689, 1069-1084, 1174-1205 | Run with max_dancers 2 and a third moving body: A's id jumps to that body 0.7 s after it appears; A has no point for 17.3 s despite a clear skeleton every frame (400 static blocks). The docstring says 'never moved since its entry'; the code tests only the last 5 s. | no |
| IR belt | A belt-only hold is capped at 8 s unless backed: the slot's own track had a skeleton within 20 frames, or the plate shows at least 10 % foreground at the slot. | application/src/core/identity_slots.py:349-353, 1262-1289, 1379-1382 | Real s2c without a plate: belt from 19.3 to 27.3 s, coast, lost at 29.25 s, hole 7.2 s; later a 12 s hole. With the matching plate the belt held all three YOLO-blind stretches of about 15 s. | yes |

## Reproduction

Scripts in tmp_analysis/audit-2026-11/stillness ($TMPDIR is /tmp here): still_chain.py, still_gap.py, still_extra.py. They drive the real FrameProcessor.replay_gpu_cached (real MOG2 and frame-diff, cross-validation, DancerTracker, IdentitySlots, clean plate) with model=None, CUDA hidden, a 20 fps frame clock and shipped defaults (confidence 0.15, intermittent on, max_dancers 2, coast 2 s, smart hold, static guard, MOG2 40 at 0.5). Not default: person height 127 px, belt off. Synthetic 1280x720 wall at 30 DN, noise sigma 2, body +30 DN; a 3 s walk-in, then 20 s motionless.

(a) Steady YOLO, clear or dim skeleton: live 400/400 frames. Box with no keypoint above 0.3: lost at 5.2 s, hole 9.3 s; with plate, no hole.
(b) YOLO off 5-8, 11-14, 17-20 s, no plate. Clear skeleton: lost 1.5 s into each gap, back 0.7 s after YOLO returns; holes 2.2, 2.2, 2.3 s. Dim skeleton (10 keypoints at 0.35): 120 returning detections rejected; one hole from 6.5 s to the end (13.5 s). With plate: no hole.
(c) No YOLO after 5 s. No plate: lost at 6.5 s. Plate, +30 DN: foreground until 34.95 s, lost at 36.45 s. Plate, +5 DN: blob gone from 25 s, lost at 30.2 s; YOLO returning at 30 s on the still body: 770 foreground vetoes, no point for 20 s.
In every case the point returns 0.6-0.9 s after she moves, slot id 1.

One 6-frame gap on the dim still dancer: 194 detections rejected, no point for the rest. MOG2: body gone after 110 frames at every contrast.
Duo plus a third moving body, max_dancers 2: yield at still+10.7 s; A, with a clear skeleton every frame, has no point for 17.3 s. With max_dancers 3: no loss. A yield triggered by the partner's id churn was not reproduced.

Real footage, read only (existing s2c timelines under tmp_analysis, s2c_*.py): without plate, belt 8 s then holes of 7.2 and 12 s; 288 frames with a YOLO detection and no point; with the plate, 44. The still person's 5 s spread is at most 0.0588 h, always under the 0.06 static threshold.

Synthetic runs prove the logic and the time constants, not how often they occur. Footage would add the real rate of YOLO gaps and sub-strong skeletons on a hanging body, the real contrast against the plate, and real jitter. Repo untouched (git status clean).

## Existing coverage

Stillness was partly exercised, contrary to the brief: solo only, standing on the floor.
- Real takes, measured outside the suite: s2c (dark, 2 min still) and s2a (bright, still), docs/PLAN_25M_2026-10.md §C.12 and docs/MULTIPASS_REVIEW_2026-10.md §3.7; timelines under tmp_analysis/flowcheck and tmp_analysis/stale_plate. No scenario manifest for them in application/tests/scenarios.
- Emitted golden wallhang-still (solo, coverage floor 0.83): application/tests/test_emitted_golden.py:48.
- Unit tests, one rule at a time: test_tracker_ghost_gate.py (frozen gate), test_tracker_warmup.py (a static 1-in-3 track never confirms), test_identity_slots.py (hidden follow bounded; a static ghost yields; 'a swaying still dancer never yields' uses a 0.12 h sway, twice the threshold; smart hold; faint still entry), test_foreground.py (foreground hold, cap, reputation, veto, belt backing, protected plate update), test_motion_model.py (stale frame-diff pair).
- None: the cross-validation decision (only its coordinate transform, test_transforms.py:115), a motionless dancer with an extra mover, plate absorption of a foreground-held slot, MOG2 absorption time, any end-to-end stillness test, stillness next to a partner, a hanging or inverted pose.

## What the technician and TouchDesigner see

TouchDesigner: the still dancer's id leaves /walldance/count 1.5 s after YOLO loses her (about 5 s if she had just stopped; up to 30 s with a valid plate) and returns under the same id 0.6-0.9 s after she moves. With an extra mover, her id jumps to that body. Each loss drops the video (D5).

Technician: a body plainly on screen with no box and no D-ball. Cross-validation rejections are not in the event log. The ids line reads 'live' while following a hidden track, then 'fg' or 'belt', then nothing. 'No dancers tracked 30 s' counts tracker tracks (main_loop.py:1318), so it can fire while a held point is still sent and stays silent after a yield. No remedy mid-show.

## Smallest fix inside the current design

1. Cross-validation: also keep a detection that overlaps an emitting slot, or a slot lost inside the wall within entry_reappear_s. The pipeline already holds the slot table.
2. Tracker: restore the warm-up integral when a previously reported live track is re-matched by a real skeleton, as resurrection already does.
3. Static yield: yield only a slot that never travelled since its entry (what the docstring says), never one with reputation; mark no ghost spot for a reputed slot.
4. Plate: protect every emitting slot with reputation, not only live and backed-belt ones; waive the foreground veto where a slot was just lost.
5. Hold: for a reputed slot away from the borders, make the foreground cap a per-show 'longest stillness' value, and do not cut a static reputed slot to 1.5 s.
6. Log cross-validation rejections near a slot in FRAME_SUMMARY.
Each needs the duo replays before release.

## What a re-founded estimator would do

N persistent targets, each present until an exit is observed: a border crossing, or the foreground at its position reading empty for a set time. Losing measurements in place is evidence of stillness, not absence, so a stationary target keeps its position with no growing uncertainty and no timer. Motion evidence is required only to create a target, never to keep one: a detection at a held target's position is accepted whatever its confidence. Measurements are typed: a skeleton gives position and identity, the frozen clean plate gives presence and position regardless of motion, the belt gives position. The plate never updates under a held target. A target born from a moving, skeleton-backed body can never be reclassified as a static ghost; ghosts are refused at birth. Horizons are in seconds. The state (seen, held by foreground, unseen) goes to TD and the operator, with the reason.

## Footage that would confirm or refute it

1. Two dancers, show lighting, an empty-wall take first: A hangs motionless 30 s (upright, inverted, curled) at the darkest position, then at the centre, while B moves far from her, then within one body height, then passes in front. Once with belts, once without.
2. The same with a third person crossing the wall area, and with three dancers at max_dancers 3.
3. One 15-minute take across dusk with a 20 s hang every two minutes, to see whether the plate stays valid as daylight fades.
Measure per frame: YOLO box and keypoint confidence on the still body, cross-validation rejections, slot states, holes, yield events, 5 s position spread.
The two-dancer rehearsal takes still on the laptop (t-020) may already contain item 1; s2c and s2a on dev37 cover only a solo person standing on the floor.

## Questions for the owner

- Will the dancers wear the IR belt in production (D19)? On the s2c night take the three YOLO-blind still stretches of about 15 s were held only by belt plus plate; without a plate the belt gave up after 8 s.
- How long is the longest planned stillness, and where on the wall (lit centre, dark edges, top)? The foreground hold stops 30 s after the last YOLO or belt measurement.
- How many dancers are on the wall at once, will max_dancers be set to exactly that number, and can anyone else (rigger, belayer) move inside the ROI during the show? With every slot busy, any extra moving track takes a still dancer's id.
- Can an empty-wall take be captured for each lighting state before the audience arrives (dusk, night)? The plate cannot be re-captured once dancers are on the wall.
- When a motionless dancer is not seen for N seconds, which is worse for TouchDesigner: a point held on the last position (possibly a ghost if she left unseen) or a dropped id? This sets the hold policy.
- Do the still poses include inverted or curled bodies? YOLO's skeleton confidence there decides whether the dancer passes the 'strong skeleton' rule after any gap.
