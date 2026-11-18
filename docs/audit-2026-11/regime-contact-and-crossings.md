# Production regime — contact-and-crossings

*Third pass, run after the owner confirmed this regime is part of the production choreography. The real chain (dedupe, cross-validation, tracker, identity slots, OSC) was driven on CPU with synthetic detections at the shipped defaults. This proves the logic path and its time constants. It does not say how often the path is taken on real footage.*

**Severity for production: show-stopper** · confidence medium

## What the shipped code does

Detection (pipeline.py:748, 2387-2547): YOLO's NMS (IoU 0.45) and the dedupe pass decide whether a pair is one or two boxes. A tucked, horizontally carried, half-hidden or dim partner near a well-seen dancer is deleted before the tracker.

Tracker: with one box, the hidden dancer's track is marked occluded, aged ten times slower and hidden from the report after 3 frames. It is not held: `_merge_duplicate_tracks` (tracker.py:3616) deletes it once it sits within 0.285 h of the partner for 8 frames, which happens when it coasts or is motion-bridged onto the partner. The same rule deletes one of two fully detected dancers closer than 0.285 h. The victim leaves no dormant snapshot; resurrection, forced updates and the takeover merge never act. The second dancer is then ignored until 0.82 h from the survivor, and needs a new id and 15 matches.

Slots: the hidden dancer's slot coasts on a frozen point for at most 2.0 s, or is merged away after 0.5 s when within 0.25 h of the live slot. The foreground cannot hold it: the pair is one blob, owned by the live slot.

OSC: `/walldance/count` falls to one id 0.7-2.4 s after contact starts and stays there until 0.55-1.4 s after the bodies part. A 5 s one-box embrace costs one dancer's point for 3-5.5 s, a 10 s one 8.8-9.9 s. Two boxes at least 0.29 h apart keep both points live. A 1 s crossing loses the point for 1.4 s without a plate and keeps it with a valid plate (foreground hold, then rebind). Crossings of 2-3 s lose 2.4-4.8 s when the dancers pass through. Slot ids came back swapped in some one-box runs (acceptable per D9), never after a two-box contact or a crossing.

## Rules that decide the outcome

| Stage | Rule | Where | Effect | By design |
|---|---|---|---|---|
| detection (YOLO) | NMS on boxes at IoU 0.45 | core/config.py:59; core/pipeline.py:817-823 | Two bodies whose boxes overlap beyond IoU 0.45 come back as one box before any WallDance rule runs. Computed, not run on images: about 0.13 h apart for 0.38 h-wide boxes. | yes |
| detection dedupe | Second-pass merges: IoU > 0.7, containment (centre inside, area < 0.5), or centroid < 0.3 h AND median keypoint < 0.1 h; the survivor takes the other's higher-confidence keypoints | core/pipeline.py:2476-2492, 2494-2518, 2539-2547 | Run: two standing bodies 0.10 h apart or less become one detection; a tucked 0.4 x 0.4 h body inside the carrier's box is removed. Keypoints of two people can be spliced into one skeleton, and the tracker's position is the keypoint mean. | yes |
| detection dedupe | Size gate and small-detection splice (box height < 0.3 x person height is merged into the nearest body within 1.5 h); runs only with 2 or more detections | core/pipeline.py:2388-2429; config.py:148-149 | Run: a horizontally carried body with a 0.25 h-high box is deleted (kept at 0.35 h). | unclear |
| detection dedupe | Pre-tracker shadow suppression: a detection with fewer than 8 keypoints or mean keypoint confidence under 0.50 is dropped within 1.5 h of a strong one | core/pipeline.py:2434-2471; config.py:236-239 | Run: a half-hidden partner (6 keypoints) is removed at 0.4-1.45 h; a fully visible but dim partner (mean confidence 0.45) is removed at 0.4 and 1.0 h. Two-box phases become one-box phases for the weaker dancer. | unclear |
| tracker association / report | Occlusion ageing (unmatched track within 1.0 x match gate of a matched one ages at 0.1 per frame, time_since_update held at 0-1) plus the frozen-ghost report gate (no skeleton for more than 3 frames and slow = not reported) | core/tracker.py:3182-3255, 126-133, 3368-3383; config.py:222-225, 1067-1071 | The hidden dancer's track can survive (about 67 s by arithmetic, not run) but stops being reported after 3 frames. Run: OCCLUDED x100, GHOST_FROZEN_SUPPRESSED x22-36; DORMANT and RESURRECT never fired. | yes |
| tracker lifecycle | _merge_duplicate_tracks: two established tracks with time_since_update <= 1 within 0.3 x match gate (0.285 h) for 8 frames; the lower-hits one is deleted with no dormant snapshot. No co-fed veto; occluded tracks qualify. | core/tracker.py:3616-3688; config.py:249-250 | Run: two clean boxes 0.28 h apart, track deleted 0.5 s into contact (kept at 0.29 h). One box: the hidden track is deleted after 0.4 s. All 'colocated' merges; the takeover merge beside it (3690-3788) carries the veto whose docstring promises to protect embracing dancers. | no |
| tracker motion bridge | An unmatched track is moved onto the nearest local MOG2 / frame-diff blob and its time_since_update reset | core/tracker.py:2876-3054, 3088-3172 | Images tier: the hidden track is relocated onto the pair's single blob (MOTION_BRIDGE x2-21), inside the tracker merge radius or the slot merge radius. | unclear |
| tracker unmatched detections | Creation gate 0.55 h x 1.5 away from the left/right edges (0.82 h); inside it an unmatched detection is force-matched, or DUPLICATE_IGNORED / AMBIGUOUS_IGNORED; resurrection is tried only beyond the gate | core/tracker.py:2572-2636, 2744-2762, 2193 | Run: after the merge, a fully detected second dancer staying 0.45-0.80 h from the partner for 5 s has no track and no point for 8.95-9.2 s (110-115 detections ignored). A new id is minted at 0.82 h. FORCE_UPDATE and FALLBACK_UPDATE never fired. | yes |
| slot continuation | A bound track that is only predicted (time_since_update > 0) or gone puts the slot on its own decaying prediction; flat hold coast_s 2.0 s (smart hold 1.5-2 s), then lost | core/identity_slots.py:867-870, 1053-1062, 1106-1135 | The hidden dancer's id stays on OSC as a frozen point for at most 2.0 s, even when the tracker still holds the track. Run: 'coast_expired' 2.0 s after the last update. Hold 10 s removes the hole only when the frozen point stays more than 0.25 h from the live slot. | yes |
| slot merge | _merge_converged: a coasting / fg / belt slot within 0.25 h of a live one for 0.5 s is dropped; two non-live slots at once; the dropped slot forgets its track key | core/identity_slots.py:1218-1260, 1213 | Run: at 0.20 h or less, with a union box, or once the bridge moved the hidden track, the id is dropped 0.7-0.85 s into contact whatever the Hold. In a pass-through crossing the front dancer walks over the hidden dancer's frozen point and the slot is dropped as the occlusion ends. | yes |
| slot evidence (foreground, duplicate guard) | The foreground hold ignores a blob within fg_dup_h 0.5 h of another emitting slot; no candidate may bind within dup_bind_h 0.5 h of an emitting slot | core/identity_slots.py:1317-1358 (1343-1349), 899-904; core/foreground.py:254 | Run: two bodies 0.50 h apart or less give one plate blob (two at 0.60 h), 0.13-0.31 h from each body, owned by the live slot: no foreground hold in contact. After a 1 s crossing the hold picks the dancer up once the blobs split (fg 22-24 frames, no count drop). | yes |
| slot re-entry | After a drop, entry needs a reported track (tracker warm-up 15), 3 consecutive reports and 12 hits; a rebind is possible only while the slot still coasts | core/identity_slots.py:249-254, 532-540, 920-990; config.py:989 | Run: the point is back 0.8 s after NEW_TRACK, that is 1.15-1.4 s after the dancers start to part; 0.55-0.65 s when the tracker had kept the track (same_track or entry on the old key). | yes |

## Reproduction

Scripts (CPU, CUDA hidden) in tmp_analysis/audit-2026-11/contact/: contact_repro.py, sweep.py, fg_blob.py, antimerge.py, hold_knob.py.

Each 20 fps frame: synthetic 17-keypoint detections (128 px bodies, 1280x720) go through the real `_guard_person_height`, `_filter_duplicate_detections` and `replay_gpu_cached` (shipped crossval, `DancerTracker.update`, `IdentitySlots.update`, `_emit`); output is read from `last_emitted`. Shipped defaults (confidence 0.15, max_dancers 2, coast 2 s, smart hold, static guard). Two tiers: "logic" (no image) and "images" (rendered wall and bodies, empty-wall plate, MOG2 fed). Both dancers are tracked apart for 4 s first.

Dedupe alone: bodies 0.10 h apart or less give 1 detection; a carried body (box 0.25 h high) or a tucked body is removed; a half-hidden or dim partner is removed up to 1.45 h away.

(a) One box (front dancer), 0.30 h apart:
- Second id absent 1.8 / 3.8 / 8.8 s for a 3 / 5 / 10 s contact (logic); 2.9 / 4.9 / 9.9 s (images).
- TRACK_MERGED 0.4 s in (logic), or slot "merged" 0.8 s in (images).
- Staying 0.45-0.80 h apart afterwards with two clean boxes: 8.95-9.2 s without the point (logic).
- Hold 10 s: 0.4 s hole at 0.30 h; unchanged 5.45 s at 0.20 h and 4.85 s with a union box.

(b) Two boxes, 5 s: 0.29 h or more apart, two live points, no swap; 0.28 h: 3.65 s hole; 0.25 h: 3.85 s; 0.15 h: 5.45 s (96-103 detections of the second dancer ignored). Rear box half-hidden at 0.40 h: 3.05 s (logic), 3.7 s (images).

(c) Crossing, single detection for 0.5 / 1 / 1.5 / 2 / 3 s:
- Logic, pass through: 0 / 1.40 / 2.05 / 2.65 / 3.90 s hole; turn back: 0.35 / 0 / 0.75 / 1.50 / 3.95 s.
- Images: 1 s: 0-0.25 s; 2 s: 2.40 / 0 s; 3 s: 4.80 s.
- A third tracker id is minted from 1 s up; no slot swap; 5 seeds identical.

This proves the logic path only. Which boxes YOLO returns in an embrace, their keypoint confidences and real blob shapes need footage. `git status` stayed clean.

## Existing coverage

Unit tests cover the slot layer alone, with both tracker tracks reported live throughout: tests/test_identity_slots.py:166 (contact with skeletons not merged), :177 (crossing not merged), :145, :152. tests/test_tracker_takeover_merge.py:94 covers the co-fed veto of the takeover merge only; tests/test_tracker_cont1.py:110-130 covers the occlusion-ageing arithmetic. Grep finds no test that references `_filter_duplicate_detections`, `_merge_duplicate_tracks`, `_resolve_unmatched_detections`, `_get_creation_gate` or the shadow suppression, and none drives detections through tracker and slots with two bodies.

Footage: emitted goldens `white-duo-full` (floor: coverage >= 0.85, on-dancer >= 0.74) and `texture-duo-win` (>= 0.89) in tests/test_emitted_golden.py:41-49, from the April duos ("long contact phases look like one body", tests/scenarios/README.md:99-101). They have count-only ground truth and floors that record current behaviour. PLAN_25M §C reports 90.9-91.6 % two-point presence on the white duo and "contact swaps". No take has timed embraces, lifts or crossings, and none has id-level ground truth; the rehearsal takes (t-020) are unprocessed.

## What the technician and TouchDesigner see

TouchDesigner: `/walldance/count` goes from two ids to one. Before that, the hidden dancer's id is a frozen point for up to 2 s, indistinguishable from live because `/dancer/state` is off by default (config.py:398). The id returns about 1 s after the dancers part, sometimes on the other dancer. With D5, one dancer's video drops for most of every embrace.

Operator: one T box and one D ball on the pair; the readout shows "D1 live | D2 coasting 1.9s", then "D2 lost" (main_loop.py:1169-1184). No alert fires: the ops monitor only alerts on zero tracks for 30 s (ops_monitor.py:450). The log has TRACK_MERGED and SLOT_EVENT lost (merged or coast_expired). Raising Hold helps only when the frozen point stays more than 0.25 h from the live slot.

## Smallest fix inside the current design

1. tracker.py:3631-3645: in `_merge_duplicate_tracks`, skip pairs whose `_pair_cofed` count reached `TRACKER_DUP_COFED_VETO` and tracks flagged `_occluded` (the veto the takeover merge already has).

2. identity_slots.py: a contact hold. A reputed slot that loses its measurement within about 0.6 h of a live slot gets its own state: no coast expiry, exempt from `_merge_converged`, emitted at the live slot's position plus its last offset, capped like `fg_max_s` (30 s). It ends when its track is updated again or an established, skeleton-backed candidate appears in its gate; allow that bind inside `dup_bind_h` when both tracks have fresh skeletons.

3. pipeline.py:2434: no shadow suppression of a detection that sits on another established track.

4. Commit the three synthetic scenarios as full-chain tests (count never below 2 during contact) and add unit tests for `_merge_duplicate_tracks`.

Then measure on the duo takes: seconds with an id missing while both dancers are on the wall.

## What a re-founded estimator would do

N targets are declared for the scene, each persistent from its entry to a declared exit; proximity rules neither delete nor create a target. Measurements are typed and may explain several targets: one skeleton box, or one plate blob whose area is about two bodies, is a group measurement. A target with no measurement of its own inside a group stays "merged": it is emitted at the group position plus its remembered offset, with that state, for as long as the group evidence lasts, counted in seconds. At the split, the second skeleton or blob goes at once to the merged target, with no warm-up and no creation gate. Identity uses motion continuity, body scale and the belt when worn; a swap is an accepted, logged outcome. A known-N blob split (2-means, BRAINSTORM §3.2) gives two points when YOLO gives one. The contact hole is then zero by construction and the re-acquire delay one frame.

## Footage that would confirm or refute it

One 3-4 minute take on the 25 m wall under the show IR, N = 2 throughout, preceded by an empty-wall plate:
- 3 embraces (3, 5 and 10 s);
- 2 lifts (one dancer carried horizontally, one tucked against the partner);
- 6 crossings (about 1 s and 2-3 s of occlusion; pass through and turn back);
- 1 release where they stay about 0.5 h apart for 5 s.

Record with the raw-detection cache on. Label each episode with its start and end frame and who is left or right afterwards.

Read from it: the share of contact frames with 0, 1 or 2 YOLO boxes; the distance between the two centroids (share under 0.285 h); keypoint count and mean confidence of the rear dancer (shadow-suppression rate); plate blob count; seconds with fewer than 2 ids per episode; re-acquire delay; swaps.

First check whether the unprocessed rehearsal takes (t-020) already contain such phases. The April texture-duo take has long contact phases, but off-axis light and count-only ground truth.

## Questions for the owner

- During an embrace or a lift, what should TouchDesigner receive: two ids (both points on the pair), or one id plus a 'merged' state? Does the video drop on id loss (D5) apply there, or is one visual for the pair acceptable?
- Is N fixed for a scene (for example three dancers who never leave during it), so a dancer may be held as long as the pair is visible, or must an id still expire on a timer during contact?
- With more than two dancers, is an id swap after a crossing or an embrace still acceptable (D9), or does each id carry its own content?
- How long are the longest full occlusions and lifts in the piece, and is a carried dancer horizontal or tucked (a box under 0.3 body heights is deleted today)?
- Will the dancers wear the IR belt front and back (D8/D19)? It is the only measurement in the system that can tell two bodies in contact apart.
- Do the rehearsal takes on the laptop (t-020) contain contact and crossings, and can they be pulled for a replay?
- Can `/walldance/dancer/state` be turned on in the TouchDesigner patch (D22), so a held point can be faded instead of dropped?
