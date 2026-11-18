# Subsystem map — tracker

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

Turns per-frame pose detections (YOLO skeletons plus synthetic "cold" motion blobs) into persistent dancer tracks and decides which track ids are handed to the output stage (identity slots, then OSC). It also holds tracks through YOLO gaps with a track-local motion bridge, re-identifies returning dancers from a dormant pool, removes ghosts and duplicates, and writes the JSONL event log used for post-mortems. `auto_height.py` is a small seconds-based estimator of person height used by the pipeline (continuous auto-height and the height guard), not by the tracker itself.

Answer to the framing question: it is a SORT skeleton (Kalman filter, Hungarian assignment, age-based lifecycle) whose estimator is not load-bearing. Behaviour is set by roughly 45 replay-fitted rules layered on top. The rules are individually evidence-backed (comments cite scenarios and metric deltas), but they do not compose: I found seven pairs where one rule cancels another (gates vs force-update; occlusion ageing vs co-located merge; cascade vs 2-opt; resurrection wideners vs creation gate and keypoint gate; bridge crediting vs synthetic-detection crediting; frozen-ghost premise vs intermittent-confirm premise; resurrect warm-up restore vs live re-warm).

## Architecture

`DancerTrack` (application/src/core/tracker.py:210-871) owns one track: a filterpy 6-state constant-acceleration Kalman filter on the centroid, the last skeleton and box, a warm-up integral plus a 40-frame credit window, bridge and occlusion flags, pose/velocity/area histories, and a torso-based own height. `DormantSnapshot` (136-207) is a frozen copy for re-identification. `DancerTracker` (907-3798) owns `tracks`, the dormant pool, three per-pair memories (distance, co-fed count, takeover window), swap-corrector timers, blob-persistence cells and the `TrackingLogger`; thresholds derive from `set_person_height()` or module constants. `emission_gate()` (107-133) is the only pure function. `FrameUpdateContext` and `PendingTrackUpdate` (874-895) defer Kalman updates within a frame.

The pipeline calls `set_frame_dimensions`, `set_person_height`, then `update()` once per processed frame (application/src/core/pipeline.py:984-992) and reads back `tracks` and per-track fields. `TrackingLogger` (application/src/core/tracking_logger.py) buffers events and flushes JSONL every 2 s, with per-show folders, rotation and pruning. `AutoHeight` (application/src/core/auto_height.py) is a time-windowed sample store instantiated three times by the pipeline.

Rule count, by my reading: about 19 in association, 6 for unmatched detections, 6 in the motion paths, 8 in lifecycle, 6 at emission.

## Data flow

`update()` (tracker.py:2340-2399):
1. Append synthetic detections for persistent motion blobs that do not overlap a YOLO box.
2. `predict()` every track; compute the merge-frame flag.
3. Cascaded Hungarian: established tracks against all detections, then tentative tracks against the rest. Each cell passes a Mahalanobis gate and a displacement gate, then gets a blended cost (position, keypoint shape, size, IoU, pose history in crowded zones, plus separation and direction penalties). A pair is accepted below a dynamic threshold or within the close-accept distance.
4. Optional swap correctors; deferred Kalman updates applied.
5. Each unmatched detection: new track or resurrection if beyond the creation gate; otherwise force-update, duplicate-drop, fallback-update or ignore.
6. Unmatched tracks try the local motion bridge (MOG2 blob, presence, frame difference).
7. Occlusion ageing.
8. Lifecycle: expiry to dormant, ghost expiry, dormant pruning, pair distances, shadow kill, co-located merge, takeover merge.
9. Emission: warm-up or intermittent path, frozen-ghost gate, slow-path separation, report cap.
10. FRAME_SUMMARY log entry.

Complexity: the cost matrix is O(D x T squared) Python-level work because the separation penalty loops over tracks per cell (1205-1233); every gated cell also logs an event (1164-1170, 1455-1460). Lifecycle passes are O(T squared). The bridge makes up to three OpenCV region queries per unmatched track. Internal track count is uncapped; only the report is capped.

## Threading and state

Single-threaded by design: `update()` runs on the main-loop thread inside the per-frame chain, and there are no locks in the three files. All state is mutated in place and indexed by frame. Decisions use no randomness and no wall clock, so a replay with the same inputs is deterministic (the logger's flush timer uses wall time but does not feed back).

Order dependence exists: unmatched detections are resolved greedily in detection order while `self.tracks` grows (2572-2636); the fallback update takes the first eligible track in list order (2717-2741); shadow and merge passes iterate in list order.

Track ids come from the class attribute `DancerTrack._id_counter` (213), shared by every tracker in the process; `reset()` zeroes it (1118) and `_try_resurrect` temporarily rewinds it (2310-2314). Replays run in separate processes, so this is a test-isolation hazard rather than a field one.

Tunable attributes (`max_age`, `max_persons`, `intermittent_confirm`, `ghost_skeleton_age`, `swap_correctors`) and `reset()` are written by app callbacks (application/src/app.py:1440-1458, 1975-1984). I could not confirm from these files that those callbacks run on the main-loop thread.

`TrackingLogger._pending` is appended by the tracker, the output stage (`annotate_frame_summary`) and the app (`log_settings`) without a lock; JSON serialisation and the file write happen on the frame thread every 2 s.

## Failure handling

Numerical guards: the cost matrix is sanitised before Hungarian (tracker.py:2013-2017); Kalman state NaN/inf is zeroed (478-488), which would move a track to the image origin rather than retire it; a singular innovation covariance makes the Mahalanobis gate pass (1149-1153).

There is no try/except inside `update()`. An exception surfaces at the main loop's per-frame boundary (application/src/runtime/main_loop.py:868-869) with the tracker possibly left mid-frame (predicted, not matched); the next frame continues from that state.

Latching cases:
- The anti-merge check consumes a detection without updating the track (2037-2039); its reference area then cannot adapt while the track stays unfed (1367-1371), so the rejection persists until the pose shrinks or the neighbouring track goes away.
- A track held by presence bridging blocks its own frame-difference tier (finding 12).

Logger: I/O errors are swallowed and pending events dropped (tracking_logger.py:369-376). If creating the live folder fails once, `_live_root` is already set (202), so it is never retried (248-250) and the show runs without an event log, announced only by a console print (211-216). A rotation failure on Windows falls back to append (401-411).

The only tracker-internal anomaly surfaced to the operator is `last_over_cap`.

## Tunables

About 100 names are imported from application/src/core/config.py at module load (tracker.py:13-78). On top of that, about 160 lines in tracker.py carry a bare float literal (a rough grep that includes some docstring lines): cost weights 0.40/0.45/0.15, crowded multipliers 1.3/1.5, coast friction 0.9/0.5, velocity clamp 0.67 heights, resurrection wideners, the sensitivity mappings at 1047-1067.

Units, by my classification:
- Roughly a third are ratios of person height (gates, proximities).
- About 25 are frame counts (max age 45, dormant 150, established 15 hits, warm-up 15, slow window 40, bridge caps 80/15, shadow 12, merge 8, takeover 4-in-8, ghost age 100, skeleton age 3).
- About 14 are per-frame rates or decays (friction, covariance inflation 1.5, dormant velocity decay 0.95, occlusion age factor 0.1, gate growth 0.18 per miss, frozen speed 0.03 heights/frame, displacement 0.475 heights/frame).
- About 8 are absolute pixels in tracker space (Kalman Q/R 2.5/2.0, Mahalanobis noise 700 px squared, 3.0 and 1.0 px/frame, aspect x 15, blob area 50 px squared, 16 px cell floor).
- None are in seconds. Comments assume 15, 20 and 25 fps in different places.

Runtime surface: per project only `tracker_max_age`, `max_persons`, `tracker_intermittent_confirm`, `tracker_ghost_skeleton_age`, `tracker_swap_correctors`, `tracking_mode`, `person_height_px` and the bridge sensitivity (app.py:1436-1458). `tracker_own_height_gates` is read only by application/tests/replay.py:311.

## Interfaces with neighbours

1. Time units. The tracker counts frames; the identity slots, the stall clock and `AutoHeight` count seconds with stall compensation (pipeline.py:1086-1099, 1400). The tracker's re-warm latency (frames) is added to the slot coast (seconds), and at low frame rates the sum can exceed the coast.

2. Coordinate space. Tracker space is the letterboxed YOLO input on the GPU path and original pixels on the CPU path (pipeline.py:333-337). The tracker's absolute-pixel constants therefore differ between paths and with imgsz. `blob.area` is not rescaled by the letterbox proxy (pipeline.py:247-269) while the tracker compares it with a fixed area (tracker.py:2972), so that threshold is in the motion detector's units.

3. Feedback loop with the detection gate. The pipeline reads `tracker.tracks`, `time_since_update` and `_warmup_score` to let detections near a live track bypass motion cross-validation (pipeline.py:1960-1961, 2303-2306). Occlusion ageing and bridge resets keep `time_since_update` low for tens of frames, so the bypass window of '5 miss frames' is much longer than intended, and a ghost track can shield the detections that sustain it.

4. Person height. `set_person_height` is called every frame from `space.person_height`; the value has several writers (config, slider, calibration, height guard, auto-height). The tracker's own-height uses median torso x 3.4, while `AutoHeight` and the height guard use box height with a head and an ankle visible: two definitions of body size. `AutoHeight.update` ignores its `current` argument after the first estimate (auto_height.py:83-87), so with auto-height on, a slider change is overwritten on the next frame.

5. `hits` is consumed by the identity slots (identity_slots.py:537, 1191) as a maturity signal, but it includes synthetic-blob matches and is inflated by `inherit_subject_from` and by resurrection.

6. Emitted coasting tracks export the EMA centroid, which is frozen during a coast (pipeline.py:2114), not the Kalman prediction. That is safer, but it means the tracker's coasting adds no positional information downstream.

7. The replay harness and the app apply tracker config keys through separate lists (tests/replay.py:305-316 vs app.py:1436-1458); `tracker_own_height_gates` exists only in the former.

8. The logger is owned by the tracker but written by the output stage and the app; it flushes synchronously on the frame thread.

## Strengths

- Diagnosability is designed in: `emission_gate()` is a pure function returning a reason code, and FRAME_SUMMARY records per track what fed it (`src`), the warm-up integral, frames since a real skeleton and the emit/hide reason (tracker.py:107-133, 3442-3480), so a field log can answer why an id vanished without a recording.
- Decisions are evidence-driven and recorded next to the code: the takeover-merge docstring scores the alternatives that were tried (3703-3725), and the swap correctors were switched off when the corpus showed net harm (config.py:441-454) rather than tuned further.
- The takeover-merge discriminator (two real dancers are seen simultaneously early and often; a zombie and its dancer never are) is sound, cheap and unit-tested with eight cases (tracker.py:3690-3788; tests/test_tracker_takeover_merge.py).
- Output-only state is kept apart from tracking state: `reported_bbox` never mutates `bbox` (709-732), the EMA centroid is not fed back (572-574), and the report cap leaves internal tracks untouched while surfacing `last_over_cap` (3415-3436).
- The warm-up restore on resurrection applies only to ids the consumer has already seen (`was_reported`, 2318-2322), which avoids re-emitting scenery ghosts; own height comes from the median torso length, which is pose-invariant, and only ever tightens gates (734-748, 1321-1340).
- The presence bridge deliberately injects no fake measurement into the Kalman filter (3056-3077), and the occluded warm-up guard stops a bridged duplicate from earning confirmation on its neighbour's motion (3152-3163).
- The tracker is deterministic given its inputs (no randomness, no wall clock in decisions), which is what makes the golden replays and the documented metric deltas possible.
- The logger never stops a show: errors are swallowed, sessions are isolated per show with rotation and pruning, events logged before the first live frame are carried over, and a failed rename on Windows degrades to append (tracking_logger.py:195-236, 380-420).
- `AutoHeight` works in seconds and its `per_second` weighting stops a dense close-up from outvoting a sparse far dancer (auto_height.py:43-52).

## Questions the reader could not answer from code

- Is sustained contact between two dancers (embrace, lift, one passing in front of the other) part of the show? It decides the severity of the co-located merge (finding 3) and of the creation-gate lockout, and whether anything like the swap correctors is needed at all.
- Which layer owns identity: tracker ids or the identity slots? If the slots are authoritative and the dancer count is capped, the dormant pool, resurrection, takeover inheritance and swap correctors could shrink to 'positions plus rough identity', and the tracker could be reduced to smoothing and ghost rejection.
- What is the real processed frame rate range on the show laptop, and what does the tracker receive after a USB stall: nothing and then one fresh frame, or a burst of buffered frames? What is the longest stall seen with a moving dancer?
- Do dancers enter and leave from the top or bottom of the wall? The edge logic is horizontal only (`_is_near_edge`, tracker.py:1086-1093; merge zones compare x only at 2525 and 2538), so a top entry is treated as a centre appearance.
- Does any real project config set `tracker_swap_correctors` to true, or use MOTION_FIRST mode? If not, both can be removed.
- Is the YOLO input size fixed for the show (1280)? Several tracker constants are absolute pixels in that space and change meaning with imgsz and ROI.
- Does the corpus behind the documented metric deltas include dark-wall duo contact at 25 m, or is that regime only covered by field takes without ground truth?
- Do GUI callbacks that write tracker attributes or call `reset()` run on the main-loop thread? The tracker has no locking.
