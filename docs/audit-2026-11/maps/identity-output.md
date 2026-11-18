# Subsystem map — identity-output

*Code-only reading of 2026-11-18 (branch `remote-ops` @ d2ecfde) by an independent reader with no docs or hub context. A description of what the code does, not a design intent. Findings for this subsystem: [findings-blind.md](../findings-blind.md).*

## Purpose

Turns the tracker's churning track ids into N stable dancer ids (1..max_dancers), holds them through detection loss and camera stalls, smooths them, and sends them as OSC to the media server. It also holds the auxiliary evidence used to keep a dancer YOLO lost (IR waist belt, empty-wall foreground plate), the camera-stall clock and keep-alive, a fixed-lag RTS smoother, and the Calibrate-time empty-wall YOLO check.

What is live in a show by default: identity slots, smart hold, static-ghost guard and yield, the belt hook with its online glint map (on even if nobody wears a belt), the foreground rules (only when the project has a plate and the frame is not judged stale), the camera-stall hold. Opt-in: the /walldance/dancer/state message. Dormant but still wired: the RTS smoother (L>1, its GUI slider was removed), weak measurements, entry plausibility, entry travel / person evidence, rebind-anywhere, hidden yield, static release, raw filter inputs. marker_model.py has no caller under application/src.

## Architecture

IdentitySlots (application/src/core/identity_slots.py) is a self-contained second tracker. It owns N _Slot objects (state live/belt/fg/weak/coasting/lost, bound tracker key, position and velocity, a OneEuro2D filter each for centroid and box size, learned belt and foreground offsets, reputation, granted hold), per-tracker-key bookkeeping (_streak, _first_pos, _max_travel, _hist, _confs) and static-ghost spots. One 330-line update() runs: predict, continuation on the bound key, same-track re-entry, gated Hungarian rebind, entry, static yield, belt / weak / foreground rounds, measure or coast or drop, merge, One-Euro, SlotOutput.

FrameProcessor (application/src/core/pipeline.py) owns all the state around it, built lazily: _slots, _output_smoother (OutputSmoother, L>1 only), stall (CameraStallClock), _osc_last (the frame the keep-alive re-sends), _belt_detector plus an online StaticMap, _fg_detector (ForegroundDetector with a live copy of the CleanPlate), _plate_capture. _BeltHook adapts slot queries to BeltDetector.detect_near on the raw ROI gray. OSCSender owns the UDP client and the send-error counters.

CameraStallClock, BeltDetector, ForegroundDetector and EmptyWallCheck are pure, with time or frames passed in. EmptyWallCheck is driven by runtime/calibration_flows.py, not by the show path.

## Data flow

In _post_yolo_chain (pipeline.py:927) the tracker returns its reported DancerTracks; finalize turns them into ScaledTracks in original pixels; at L=1 a box-size EMA is applied. _run_identity_slots (pipeline.py:1482) builds one SlotCandidate per reported track (the tracker's EMA centroid, clamped box, hits, frames since skeleton, frames since update, zone_ok from the exclusion mask) and adds 'hidden' candidates read from tracker.tracks for slot-bound tracks the tracker did not report. _belt_hook() and _fg_frame() both read the raw, un-enhanced ROI gray. IdentitySlots.update(cands, t = camera-live time, t_motion = wall time, bounds = ROI) returns SlotOutputs. _slot_track rebuilds ScaledTracks with track_id = slot id, the One-Euro centroid, a box centred on that centroid, and the last skeleton translated rigidly.

_emit (pipeline.py:1057) calls OSCSender.send_frame: /walldance/count [n, ids...], then per dancer /dancer/centroid, /bbox, /velocity, /keypoints as separate datagrams normalised by the full frame size; optionally one /dancer/state row per slot. _osc_last keeps the frame for the stall keep-alive; last_emitted feeds the preview's D-balls. The plate is then updated outside protected boxes. process() returns the tracker's tracks, not the slots.

Main loop (application/src/runtime/main_loop.py): _stall_frame on each live frame, _stall_keepalive on empty polls, _stall_not_live on playback or camera loss.

## Threading and state

Single-threaded. The slot layer, belt, foreground, OSC sends and the stall keep-alive all run inline on the main loop thread. OSC is one non-blocking UDP sendto per message, no queue. The only worker is the 'mog2-feed' executor, awaited before the tracker runs, so the raw gray is not used concurrently. The OpsReadiness thread reads plate_status / last_fg without a lock; last_fg is set to None at the start of every frame, so it can read 'ready' mid-frame (benign).

Clocks: the output clock is time.perf_counter live (only tests/replay.py install a frame clock; in-app playback uses wall time). Hold clocks read output clock minus stall.offset. The tracker counts frames.

State lifetime: slots, static spots, the belt glint map and _osc_last are cleared by reset_output(), called on the tracker-reset button, playback restart, mirror/rotate, and when slots are toggled off. The RTS smoother buffers and the box-size EMA are not cleared there. Slots are not reset on STANDBY/RUN changes; they stop being updated and expire on the first frame back unless their track is matched at once. The live plate (ForegroundDetector._live) exists in memory only; the .npz stays as captured.

## Failure handling

OSC: _send drops a datagram on OSError, counts it and queues an alert at most every 10 s, which the ops tick polls at 1 Hz. Any other exception reaches the per-frame boundary (frame skipped; a long streak ends the app). A missing receiver is not detectable at runtime because the socket is unconnected; only the Go-Live readiness probe uses a connected socket. A failed OSCSender init is a console print.

Belt: an import, construction or detection failure marks it 'unavailable' for the session; a glint-map failure stops the learning only. Foreground: a plate load failure leaves it off; a process exception drops the detector; a wrong frame size, or more than 25 % of the ROI differing, makes that frame's foreground invalid and every foreground rule is silently off. Slots: belt-hook exceptions are swallowed, NaN candidates skipped, greedy assignment without scipy. _run_identity_slots itself is not guarded, so a bug in this output-only layer skips whole frames.

Camera stall: a silence of 150 ms to 5 s (2.5 s in practice on IDS, see findings) pauses the hold clocks and re-sends the last frame at frame rate as coasting. Beyond that, and on reconnect, STANDBY or exit, the stream simply stops; there is no terminal OSC frame.

Detection loss ladder: tracker motion bridge, hidden follow 3 s, belt 8 s (renewable), foreground 30 s, coast 1.5-2 s (0.3 s at ROI borders), then the id leaves /walldance/count.

## Tunables

About 160 constants.

SlotParams: 83 fields (identity_slots.py:236-367) in seconds, dancer heights (= bounding-box height), frame counts and ratios. Nine are wired from ProcessingSettings (pipeline.py:1488-1497); about 30 belong to features that are off by default. Per-project keys: max_dancers, stability (0-3), coast_s (0-10 s), static_ghost_guard, static_release_s, slot_filter_input, smart_hold, use_ir_belt, fg_enabled, osc_send_state, camera_stall_hold. Eight module constants map the Stability knob to One-Euro parameters (identity_slots.py:112-123).

BeltParams: 27 fields (belt_detector.py:70-104) in absolute grey levels and full-resolution pixels; BELT_STATIC_* x3 (config.py:341-343).

FgParams: 12 fields (foreground.py:116-134), grey levels on the 4x downscaled raw image and frame counts; PLATE_CAPTURE_FRAMES 40; LIGHT_CHANGE_* x3.

CameraStallClock: 3 frame periods, 5 s cap (config.py:134-136), against a 2.5 s IDS reconnect timeout in runtime/camera_controller.py:77.

OutputSmoother: process_var 1, meas_var 4 px^2, lag L in frames. EmptyWallCheck: 120 frames, settle 4, margin 0.8, zero tolerated ghost frames, gamma floor 0.8. OSC: ip, port, enabled, alert interval 10 s.

Inline constants in pipeline.py: plate protect box 0.6 h x 1.2 h and frames-since-skeleton <= 20 (:1460-1467), belt protect radius 0.6 h and 8 px cells (:1237-1251), velocity dt cap 0.25 s (:1504).

Operator panel: 10 controls, of which four carry 'Leave it ON'.

## Interfaces with neighbours

Tracker: the slot layer depends on tracker internals rather than an interface (tracker.tracks, hits, time_since_update, _frames_since_skeleton, the frozen-ghost hide) and on the tracker reporting tracks it is only predicting. A tracker reset must be paired with reset_output(); only app._cb_tracker_reset does both.

Camera: the stall cap (5 s) and the IDS reconnect timeout (2.5 s, runtime/camera_controller.py:77) disagree. The stall threshold is 3 periods of the target fps, so a camera actually delivering under about 7 fps would have every frame gap counted as a stall.

Calibration: Calibrate and plate capture force the pipeline on in STANDBY, so tracker, slots and OSC all run there, at the wall check's lowered confidence. The empty-wall check's keep-first rule relies on calibration_state, which is persisted with the project.

Enhancement: belt and foreground work on the raw un-enhanced ROI gray in absolute grey levels, while YOLO works on the enhanced image; their thresholds therefore depend on the exposure and gain the calibration servo or auto-exposure chose.

ROI and coordinates: slot edge logic uses the ROI as bounds; OSC normalises by the full camera frame, so an IDS crop or mirror/rotate change moves the coordinate mapping while an ROI change does not.

GUI and ops: the preview draws tracker ids (T) and slot ids (D) together; the plate status text is event-driven, not live. The health tick's n_tracked counts the tracker's reported tracks, not the slots OSC emits (main_loop.py:1318).

Remote API: SetOutputSmoothing is still reachable in RUN although its GUI control was removed.

## Strengths

- Output-only by construction: the slot layer never mutates tracker state and process() still returns the tracker's tracks, so replay goldens are unaffected and the layer can be toggled live (pipeline.py:1044-1055, :1167-1168).
- Time is injected everywhere it matters: IdentitySlots.update(t, t_motion) and CameraStallClock take caller-supplied times, replays install a frame clock, and the stall logic has a 377-line test file including a main-loop acquire test.
- The camera-stall design is coherent: hold clocks on camera-live time, the motion model on wall time, distance gates widened by the silent time (identity_slots.py:759-762, :859-861, :931-934), a stall only counts if the main loop actually polled through it (camera_stall.py:92-98), and the keep-alive is stamped with the camera frame number so it never re-sends a frame from STANDBY or a take (pipeline.py:1126).
- Optional evidence fails soft: belt hook, glint map, foreground and plate capture are each wrapped so a failure disables that evidence and not the show (pipeline.py:1207-1218, :1254-1256, :1304-1308, :1366-1369); OSC send errors are dropped, counted and surfaced as a rate-limited alert (osc_output.py:50-77).
- Anti-ghost asymmetry is deliberate and documented with replay numbers: entry is strict, continuation is never blocked, merges are conservative, and features that hurt on replays are left off with the measured reason in the comment (identity_slots.py:269-276, :280-282, :335-337).
- The foreground plate cannot drift in brightness: the update blends the gain-normalised frame, frames judged stale are never blended, confirmed dancers are protected, and the light check compares against the snapshot as captured (foreground.py:215-230, :263-265, :292-318).
- The belt detector's live mode is well bounded: gated windows only, an adaptive local threshold, one belt answers at most one slot, eyes versus split-belt handling, and a cost cap for large windows (belt_detector.py:358-406, :440-463).
- Slot binds, losses and yields are logged as SLOT_EVENT lines and slot rows are added to each FRAME_SUMMARY (pipeline.py:1563-1580), and every module in this subsystem has its own test file.

## Questions the reader could not answer from code

- How does the TouchDesigner patch decide an id is lost: from the id list in /walldance/count, from a per-id timeout, or from /dancer/state? Does it read bbox, keypoints or velocity, or only the centroid? This sets the severity of every OSC contract item.
- Do dancers wear the IR belt in current shows, and is the on-axis IR projector part of the rig? If not, the belt path is default-on surface with no benefit.
- Is L>1 (the RTS stream) used by anything other than the replay tools? If not, can it leave the live emit path?
- How much sustained contact does the choreography have (centroids within half a body height for more than 2 s), and how often are bodies horizontal or foreshortened on the wall?
- Was the foreground veto checked on the darkest takes? What is the lowest foreground share a real, bound dancer ever shows?
- Do IDS stalls longer than 2.5 s recover unaided, or is the reconnect at 2.5 s needed? Which should win, the 5 s bridge or the reconnect?
- Is re-capturing the empty wall ever possible once a show has started? If not, what should happen to the foreground evidence when the light changes?
- Can anyone other than the N dancers be inside the ROI during a show (riggers, audience)? With static yield on, a passer-by can take the slot of a dancer who has been motionless for 5 s, and that spot is then blocked for re-entry.
- Are the top and bottom ROI borders real exits? The smart hold releases in 0.3 s at any of the four borders (identity_slots.py:1130-1134).
- What is the lowest frame rate the show laptop sustains in a dark scene? The frame-count thresholds and the 3-period stall threshold assume about 20 fps.
