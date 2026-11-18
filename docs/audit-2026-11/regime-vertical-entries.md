# Production regime — vertical-entries

*Third pass, run after the owner confirmed this regime is part of the production choreography. The real chain (dedupe, cross-validation, tracker, identity slots, OSC) was driven on CPU with synthetic detections at the shipped defaults. This proves the logic path and its time constants. It does not say how often the path is taken on real footage.*

**Severity for production: medium** · confidence medium

## What the shipped code does

Detection filters. A body cut by the ROI or frame border has few keypoints over 0.3. The scored gate keeps a detection only with 8 or more keypoints at mean confidence 0.45, frame-diff motion, or overlap with a motion-confirmed track (pipeline.py:2352-2382). A slow or paused truncated body is therefore rejected until about half of it is inside. With a full dancer within 1.5 h it is also deleted as a "shadow" (pipeline.py:2434-2465), and below 0.3 x person height its keypoints are spliced into that neighbour (2397-2429). A MOG2 cold blob can start a track earlier, but a blob-born track is hidden by the frozen gate and cannot open a slot.

Tracker. Edge logic is left/right only: `_is_near_edge(x)` (tracker.py:1086-1093), fed with pad_x only (pipeline.py:984-985). The tracker has no vertical bound and does not know the top/bottom letterbox padding. A top or bottom entrant is a centre appearance: the new-track gate is 0.83 h instead of 0.55 h. A top or bottom exit is a centre disappearance: the track lives 135 frames instead of 22 and goes dormant with edge_exit False. Warm-up needs 15 matches. The position is the mean of the visible keypoints.

Slots. Border logic is four-sided, on the ROI (identity_slots.py:1122-1134; pipeline.py:1521-1522). Entry adds 3 frames. On exit, the 0.3 s release needs the centroid within 0.25 x the (truncated) box height of a border and an outward speed over 0.5 h/s; a slow exit keeps the full 2 s hold. `_near_border` only feeds the entry-travel rule, which is off (D31); `entry_plausibility` is off.

Rotate. The transform is applied at the source (input_transform.py:1-12), so edge logic follows the displayed image. With Rotate applied, the world's top and bottom are still not edges; in portrait the side zone is 12 % of the narrower width. Without Rotate the world's top becomes an image side (inferred, not run).

max_dancers defaults to 2 (D4 cap): a third dancer gets no point.

## Rules that decide the outcome

| Stage | Rule | Where | Effect | By design |
|---|---|---|---|---|
| detection filter: motion cross-validation | A detection is kept only with >= 8 keypoints over 0.3 at mean confidence >= 0.45, or frame-diff motion >= 0.02 of its box, or overlap with a motion-confirmed track | application/src/core/pipeline.py:2352-2382 (track bypass 2296-2320); application/src/core/config.py:962-971 | A body cut by the top or bottom border (legs only = 4 keypoints) is rejected while slow or paused. First accepted at about 48 % visible feet first, 37 % head first. 200 of 200 YOLO boxes rejected during a 10 s pause at 40 % visible. On exit, YOLO-only, the detection is dropped at the same point and the slot starts coasting while half the body is still in view. | no |
| detection filter: dedupe, shadow suppression | A detection with < 8 valid keypoints or mean confidence < 0.5 within 1.5 x person height of a strong detection is deleted | application/src/core/pipeline.py:2434-2465; application/src/core/config.py:236-239 | An entrant truncated by the border is removed for as long as a full dancer is within 1.5 h: 22 frames (15 % to 47 % visible) in the run with a neighbour 1.2 h aside. | no |
| detection filter: size gate (frames with >= 2 detections) | A box under 0.3 x person height is removed, and its keypoints with higher confidence overwrite those of the nearest full-size detection within 1.5 h | application/src/core/pipeline.py:2388-2429; application/src/core/config.py:148 | While the entrant was 15-30 % visible, the neighbour's emitted point jumped 0.27 h in x and 0.18 h in y although the neighbour did not move (blind leads detection-pipeline#1 and #3, reproduced for a top entry). | no |
| cold blobs, tracker report gate, slot entry | A MOG2 blob >= 0.3 h tall with frame-diff motion becomes a zero-confidence detection after 3 frames and earns full warm-up; a track with no skeleton for > 3 frames and speed < 0.03 h/frame is hidden; a slot entry needs a skeleton within 60 frames | application/src/core/pipeline.py:969-980, 1790-1818; application/src/core/tracker.py:2820-2871, 107-133; application/src/core/identity_slots.py:254, 539 | In the synthetic motion case the blob pre-warms the track, so the first accepted skeleton is reported on the same frame (entry 1.7 s instead of 2.35 s). A blob alone never gives a point: a 0.3 h/s descent is under the 0.6 h/s frozen-gate speed. | yes |
| tracker edge model | _is_near_edge(x): within 12 % of the content width from the left or right; set_frame_dimensions(width, pad_x) only; no vertical bound | application/src/core/tracker.py:1069-1093, 490-502; application/src/core/pipeline.py:984-985; application/src/core/config.py:282-302 | Top and bottom entries and exits are classed as centre events. The 362 px of top/bottom letterbox padding in the test scene is unknown to the tracker; predict() only clamps at -0.5 h from the tensor origin. No harm from the missing padding was measured. | yes |
| tracker birth | New-track gate 0.55 h, x 1.5 outside the side zones; an unmatched detection inside the gate of an already matched track is ignored | application/src/core/tracker.py:2744-2762, 2576-2636; application/src/core/config.py:199, 295 | A top entrant 0.5 or 0.7 h beside a dancer never gets a track (161 AMBIGUOUS_IGNORED, zero frames with two points) for as long as it stays there. It needs 0.83 h; at a side edge 0.7 h was enough. | yes |
| tracker warm-up | +1 per match, -0.8 per miss, reported at 15; intermittent path: 12 credits in 40 frames and travel >= 0.5 x own box height | application/src/core/tracker.py:380-397, 429-436, 438-454, 534-536; application/src/core/config.py:989-1004 | 0.7 s after the first accepted detection. When the truncated body is found one frame in three, the track is not reported until 0.7 s after the body is fully in (OSC 4.1 s after the first pixel). | yes |
| tracker position | Centroid = confidence-weighted mean of the keypoints over 0.3; the slot uses its EMA | application/src/core/tracker.py:367-377, 547-574; application/src/core/identity_slots.py:1443-1449 | During a vertical entry or exit the point is the centre of the visible part: 0.2-0.29 h from the body centre at first emission, then 18 px/s while the body moves 38 px/s, until the body is whole. | no |
| slot entry | A lost slot takes a reported track after 3 consecutive reports, hits >= 12, a skeleton within 60 frames; anywhere in the ROI; entry_plausibility and entry_min_travel_h are off; at most max_dancers slots | application/src/core/identity_slots.py:532-558, 252-256, 329, 957-990; application/src/core/pipeline.py:156; application/src/core/config.py:321 | 0.1 s after the tracker confirms; no border dependence at the shipped defaults. With max_dancers 2 a third dancer entering by the top never got a point; with 3 it did after 1.75 s. | yes |
| slot hold on loss (smart hold) | Hold 1.5-2.0 s, cut to 0.3 s when the centroid is within 0.25 x box height of any ROI bound and moves outward faster than 0.5 box heights per second; a box touching the bound does not count | application/src/core/identity_slots.py:1106-1135, 313-320; application/src/core/config.py:323, 333 | A 0.3 h/s exit never triggers it at any border: the point is parked on the border 2.05 s after the body is out. At 1-2 h/s it fired at top and bottom with the motion bridge (the truncated box height inflates the speed), not YOLO-only, and not at the side with motion. | unclear |
| tracker exit | An unmatched track expires after max_age x 0.5 (22 frames) in a side zone, x 3 (135 frames) elsewhere once established; the dormant edge flag is set from x only | application/src/core/tracker.py:3281-3296; application/src/core/config.py:175, 271, 292 | After a top or bottom exit the track stays 6.15-7.75 s (side: 0.9-1.45 s) and goes dormant with edge_exit False. A re-entry at the same spot after 2, 5 or 12 s reused the tracker id and the slot took it back by same_track. A false figure 1.5 h from the exit point was not resurrected (normal 0.8 s entry). | yes |
| slot continuation (hidden follow) | A slot keeps following its bound track when the tracker hides it (frozen gate) for up to 3 s within 1.5 h of the last reported position | application/src/core/identity_slots.py:871-886, 267-268; application/src/core/tracker.py:2876-3054 | On an exit with a working MOG2 bridge the slot stays live on the shrinking silhouette down to the border (21 frames at 0.3 h/s), then coasts for the granted hold. | yes |

## Reproduction

Scripts, CPU only, 20 fps: tmp_analysis/audit-2026-11/vertical/vsim.py plus run_entry.py, run_exit.py, run_more.py, run_extra.py; outputs out_*.txt in the same folder. They drive the real FrameProcessor.replay_gpu_cached (crossval, cold blobs, DancerTracker.update, finalize, IdentitySlots.update) after _guard_person_height and _filter_duplicate_detections, with model=None. Shipped defaults, except person_height_px = the simulated 127 px and the IR belt off. Scene: 1776x1304 frame, ROI 1304x566, pad_y 362. Synthetic upright body: keypoints outside the ROI get confidence 0.05, the box is clipped, YOLO is assumed to find it from 30 % visible. "Motion" feeds the real MOG2 and frame-diff with a bright rectangle on a noisy dark wall; "YOLO-only" passes no gray.

Top entry at 0.3 h/s, feet first: OSC starts 1.7 s after the first pixel with motion (52 % of the body in), 2.35 s YOLO-only (72 % in), 4.1 s when the truncated body is found one frame in three. Error at first emission 0.2-0.29 h. Side entry at the same speed: 1.1-1.25 s, error 0.01 h.

Pause of 10 s with 40 % visible: 0 of 200 frames emitted. With 60 %: 192-200 of 200.

Exit at 0.3 h/s by the top or bottom: last point 2.05 s after the body is fully out, parked 2-3 px beyond the border (motion); 0.45-0.8 s YOLO-only. Tracker track alive 6.15-7.75 s after the last detection, edge_exit False (side: 0.9-1.45 s, True).

Exits at 1-2 h/s: the 0.3 s release fired at top and bottom with motion, not YOLO-only (1.55-1.8 s after out), not at the side with motion (2.05 s).

Top re-entry at the same x after 2, 5, 12 s: same tracker id, slot event same_track, OSC after 1.6 s.

Entrant 0.5 or 0.7 h beside a dancer under the top border: never two points; 0.9 h: two points after 1.75 s.

Entrant 1.2 h aside, found from 15 % visible: removed for 22 frames; the neighbour's point jumped 0.27 h.

Portrait 1304x1776: same timings.

No swap or extra slot id in any single-dancer run. This proves logic paths only; how YOLO scores truncated bodies under IR is an assumption. The repo was not modified (git status empty).

## Existing coverage

Unit tests: application/tests/test_identity_slots.py:455-465 covers the 0.3 s border release at the right bound only (a 1.5 h/s walk-out); :541-565 uses the left border for the entry-travel prior. No test uses a top or bottom bound. No test exercises _is_near_edge or set_frame_dimensions (only set in tests/test_cont10_logging.py:134), the exited_from_edge behaviour (only a constructor argument in tests/test_tracker_cont1.py:55), or _crossval_motion_filter and _filter_duplicate_detections on truncated boxes. The input-transform tests (test_input_transform*.py, test_offline_input_transform.py) cover the image, ROI and mask remap, not tracking. Footage: the scenario corpus has no entry or exit through the top or bottom; the rope take hangar-aerial is N=1 "present throughout" (tests/scenarios/hangar-aerial.json:33). Docs: no mention of vertical entries found in ROADMAP, PLAN_25M, AUDIT_2026-10 or OSC_CONTRACT (grep).

## What the technician and TouchDesigner see

Technician: the preview shows the dancer's legs coming over the top border with no box and no id for 1.7-2.4 s (up to 4 s if YOLO flickers), or for as long as the dancer waits with less than half the body in the ROI. Nothing on screen or in the log says why. After 30 s on an otherwise empty wall the 8 s toast "No dancers tracked" fires (ops_monitor.py:450-456). TouchDesigner: the id joins /walldance/count late, about 0.3 h inside the border, then moves at half the body's speed until the body is whole. At a slow exit the point parks on the border for 2 s after the dancer has gone, in state coasting, which TD only sees with the opt-in /dancer/state.

## Smallest fix inside the current design

1. No code: if the frame allows, extend the ROI one body height above the wall top and below the floor line, so bodies are whole when they cross; check who else is visible there.
2. Pipeline: exempt a box touching an ROI border from the 8-keypoint rule, the shadow suppression and the small-box splice (pipeline.py:2352-2355, 2397-2465); accept it on box confidence.
3. Tracker: pass pad_y and the content height to set_frame_dimensions and make _is_near_edge two-dimensional (tracker.py:1069-1093; pipeline.py:984-985), so top and bottom get the 0.55 h birth gate and the short exit age.
4. Slots: in _granted_hold use the configured person height, not the truncated box, and treat a shrinking box that touches the bound as leaving (identity_slots.py:1122-1134).
5. Output: for a border-truncated box, report the box's inner edge minus half the known height instead of the visible-keypoint mean.
Add unit tests for the top and bottom bounds.

## What a re-founded estimator would do

State per dancer: body centre (allowed outside the ROI), velocity in h/s on real time, and a known height. The ROI is a known window, so a truncated skeleton or foreground blob is a partial measurement: its inner edge plus the known height places the centre, and the point crosses the border at body speed with no bias. Birth: the technician declares the entry lines for the show (roof, floor, sides); clean-plate foreground crossing a declared line, with fewer than N dancers on the wall, opens a tentative track at once, with no 8-keypoint rule, and confirms in about 0.3 s. A paused, half-visible dancer is held by foreground alone. Death: a track whose predicted body has fully crossed an exit line and whose foreground is gone is released immediately, without the 2 s hold; one that stops at the line with foreground present is kept. One typed rule set serves all four borders.

## Footage that would confirm or refute it

1. One dancer, show ROI: a rope descent from the roof at about 0.3 h/s, a 10 s pause with only the legs in the ROI, descent to mid-wall, then exit by the top; once feet first, once inverted or horizontal.
2. The same dancer lowered through the bottom border and brought back.
3. Two dancers: B hanging under the top border, A descending 0.5, 1 and 1.5 h beside B.
4. Take 1 again with the ROI extended above the wall, if the frame allows.
5. Take 1 in the portrait rig, if it is planned.
About 1-2 minutes each, with the tracker event log on and hand-marked times for first limb in, body fully in and body fully out. This gives what the synthetic run cannot: YOLO11x box and keypoint confidence against visible fraction under 850 nm, real MOG2 and foreground behaviour at the border, and rope or rigging artefacts.

## Questions for the owner

- Is the top of the wall inside the camera frame with margin, or is the wall framed edge to edge (the mur25m-night-common ROI starts 3 px from the frame top)? Can the ROI extend one body height above the wall and below the floor line?
- Is anyone else visible above the wall top or at its base during the show (riggers, belayers, dancers waiting)?
- How do dancers come in from the roof: feet first, inverted or horizontal, and at what speed? Do they wait half-visible at the edge before descending, and for how long?
- What should TouchDesigner get during an entry: a point from the first visible limb, or only once the dancer is whole on the wall?
- At an exit, is a point lingering 2 s on the border acceptable, or should it vanish as the dancer leaves? Does the TD patch read /walldance/dancer/state (D22), so it could hide a coasting point itself?
- What is the largest number of dancers on the wall at once? max_dancers ships at 2 and a third dancer gets no point until the technician raises it.
- In the portrait rig, will Rotate always be applied so the image is upright? Is the floor inside the ROI, or is the ROI cut at the wall base?
