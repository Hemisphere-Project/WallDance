# Tracking Robustness Plan

**Goal**: Eliminate ID swaps and ghost creation; survive occluded crossovers.  
**Context**: Similar costumes (ReID low-value), ≤6 dancers, fixed IR camera, comfortable GPU headroom.  
**Started**: 2026-03-03  
**Last updated**: 2026-03-13  

---

## Status at a Glance

| Phase | Status | Summary |
|-------|--------|---------|
| 0 — Diagnostics | **DONE** | `TrackingLogger` → JSONL, frame overlay, `FRAME_SUMMARY`, F8 issue capture |
| 1 — Hardened Association | **DONE** | Mahalanobis gate, cascaded matching, IoU cost, displacement gate |
| 1c/1d — Post-cascade swaps | **DONE** | Occlusion swap + merge-direction swap + 2-opt swap |
| 2 — Temporal Pose Signature | **DONE** | 15-frame pose history, trajectory cost (30% in crowded), merge-frame guard |
| Structural refactor | **DONE** | `update()` decomposed into named stages; all policies in helpers |
| Slot 7 tuning (2026-03-13) | **DONE** | Per-track merge zones, displacement gate, shadow immunity — 0 swaps over 700f |
| 3 — Motion Bridge (MOG2) | **IN PROGRESS** | Blobs keep lost tracks alive up to 80 frames; ~1ms CPU |
| 4 — Lifecycle refinements | Not started | Aggressive dormant matching, anti-steal cooldown |
| 5 — Occupancy grid | Not started | Only if residual crossover issues remain |

### Validated Results

| Slot | Frames | Swaps | Ghost tracks | Notes |
|------|--------|-------|--------------|-------|
| **Slot 7** (tango-phone) | 700 | **0** | Acceptable (trees/balcony/wind) | 4 dancers tracked perfectly |
| Slot 6 | ~477 | 0 (post-fix) | 0 | All 5 known-issue frames fixed |

---

## Recent Work — Slot 7 Tuning (2026-03-13)

Iterative debugging on `tango-phone` slot 7 (4 dancers, ~80px, yolo_imgsz=1920). Started with **861 MERGE_DIRECTION_SWAP events**, ended with **zero swaps** over 700 frames across 4 iterations.

### Changes Applied

| Fix | File | What | Why |
|-----|------|------|-----|
| **A** | config.py | `CLOSE_PROXIMITY_RATIO` 0.6 → 0.35 | `close_dist` was too large (45px) — most tracks falsely "close", inflating merge context |
| **B** | tracker.py | Skip established tracks in `_detect_shadow_tracks` | Shadow detector was killing D4 (an established dancer) at F237 |
| **C** | tracker.py | `merge_frame` counts only established, recently-matched tracks | Ghost tracks (wind/trees) inflated count → merge_frame fired 88% → now 5% |
| **Per-track merge zones** | tracker.py | `merge_zone_trk_indices` in `FrameUpdateContext` | Only tracks whose detection is near another matched detection or occluded track get merge context — prevents far-apart tracks acquiring spurious merge state |
| **Merge context tightened** | tracker.py | `_tracks_share_recent_merge_context` requires recent occlusion | Both tracks having merge frames alone was too permissive; now requires at least one to have recent occlusion |
| **Resurrect merge_frame=False** | tracker.py | `_try_resurrect` no longer inherits frame-level merge_frame | Resurrected tracks were getting spurious merge context |
| **D** | config.py | Mahalanobis gate 9.21 → 16.27 | At F246, correct match was gated (chi²=13.4 > 9.21) due to Kalman velocity amplification during convergence |
| **E** | config.py + tracker.py | Displacement gate: `MAX_DISPLACEMENT_RATIO = 0.5` | Caps per-frame displacement from last measured position for established tracks. Prevents skeleton-weighted cost from masking centroid jumps (p99 of good matches = 18px, cap = 38px) |

### Progression

| Iteration | MERGE_DIRECTION_SWAP | merge_frame rate | D4 alive? | Notes |
|-----------|---------------------|------------------|-----------|-------|
| Baseline | 861 | 42% | Killed at F237 | Shadow detector + merge inflation |
| After A+B | 4 | 88% | Yes | Merge context still too broad |
| After C | 2 | 38% | Yes | Frame-level merge tagging still wrong |
| After per-track zones | 0 | 5% | Yes | All post-hoc swaps at zero |
| After D+E | 0 | 5% | Yes | Core matching errors also eliminated |

### Key Observations from Slot 7 Debugging

1. **Merge-frame inflation is the #1 cause of false swaps.** When ghost tracks (wind, trees, balcony) are counted as "active", `n_detections < n_tracks` fires almost every frame → every track gets merge context → swap detectors fire incorrectly.

2. **Frame-level merge context is too coarse.** Even with correct `merge_frame` detection, stamping ALL matched tracks as "in merge" causes far-apart tracks to share merge context and swap. Per-track zones (`merge_zone_trk_indices`) solved this.

3. **Mahalanobis gate at 99% (9.21) is too tight when Kalman velocity amplifies during track convergence.** Two tracks approaching each other get amplified velocities; the gate rejects the correct match, forcing the Hungarian solver into a swap. Relaxing to 99.97% (16.27) fixes this without losing teleport protection (the displacement gate handles that).

4. **Skeleton matching can mask centroid jumps.** At F664, track #1 matched a detection 75px away (vx was ~2) because the skeleton shape matched. The displacement gate (38px cap) prevents this class of error. Distribution analysis: p99=18px, all 4 matches >40px were swap errors.

5. **Post-hoc swap correction is inherently fragile** (confirmed again). Timing-dependent on `_occluded` flags, `_vx_history`. One fix triggers false positives elsewhere. Pre-assignment gates (Mahalanobis, displacement) are more robust than post-assignment swaps.

---

## Next Steps (priority order)

### 1. Continue testing on longer sequences

Run slot 7 beyond frame 700. Run other tango-phone slots and other projects. Verify the displacement gate (38px) doesn't reject legitimate fast-moving matches in different choreographies.

### 2. Phase 3 — Motion Bridge (MOG2)

Bridge YOLO detection gaps using MOG2 foreground blobs — keeps existing tracks alive up to 80 frames (~2.7s at 30fps). Already partially implemented in `motion_detector.py`.

| Tier | Condition | Update source | Keypoints |
|------|-----------|---------------|-----------|
| 1 | YOLO matched | Full detection | Live |
| 2 | No YOLO, blob available | MOG2 blob centroid | Frozen from last YOLO |
| 3 | No YOLO, no blob | Kalman-only prediction | Frozen |

Key design: blobs never create new tracks or resurrect dormant ones. Progressive Kalman noise: R×2 (1-10f), R×4 (11-30f), R×8 (31-80f). ~1ms CPU, zero GPU impact.

### 3. Phase 4 — Track Lifecycle Refinements

- **4.1 Aggressive dormant matching**: Before creating a new track, check dormant pool for tracks that died < 10 frames ago (relaxed gate + pose trajectory similarity)
- **4.2 Anti-steal cooldown**: Freeze victim's aging for 5 frames when swap detector fires
- **4.3 Pose trajectory in resurrection**: Score dormant candidates using trajectory similarity

### 4. Ghost track suppression

Current state: ghost tracks from trees, wind, balcony rails are harmless but noisy (47 NEW_TRACK in 717 frames). Could add:
- Tighter `NEW_TRACK_GATE_RATIO` for non-edge detections
- Kill tracks with persistently low YOLO confidence (<0.3 over 10 frames)
- Scene-specific exclusion zones (configurable per project)

### 5. Phase 5 — Occupancy-Aware Occlusion

Only if residual multi-body crossover issues remain after Phase 3-4. Lightweight 2D grid (one cell per ~50px), disables new-track creation in occupied cells.

---

## Architecture

### `update()` per-frame flow (tracker.py)

1. `_begin_frame_update()` — tick suppression/cooldown counters, create `FrameUpdateContext`
2. `_predict_tracks_for_frame()` — Kalman predict, compute `merge_frame` (established + recently-matched only)
3. `_run_matching_phase()`:
   - **Pass 1**: established (non-suppressed) tracks vs all detections → deferred
   - **Pass 2**: tentative + suppressed tracks vs remaining detections → deferred
   - Post-cascade swap checks: occlusion (1c), merge-direction (1d), 2-opt (2.3)
   - Compute `merge_zone_trk_indices` (per-track merge proximity)
   - Apply all deferred updates; velocity clamp on swapped tracks
4. `_resolve_unmatched_detections()` — force-update / fallback / resurrect / new track
5. `_lazy_bridge_with_motion()` — MOG2 blob bridge for lost tracks (Phase 3)
6. `_apply_occlusion_aging()` — fractional aging for tracks near matched tracks
7. `_finalize_track_lifecycle()` — expire → dormant, age dormant, shadow detection
8. `_log_frame_summary()` — structured `FRAME_SUMMARY` event

### Cost matrix gates (applied in order)

| Gate | Condition | Effect |
|------|-----------|--------|
| **Mahalanobis** | chi² > 16.27 (df=2, 99.97%) | cost = 1e6 (blocks pair) |
| **Displacement** | raw_dist > 0.5 × dist_threshold, established, recently-matched | cost = 1e6 (blocks pair) |
| Cost function | position + skeleton + size + IoU + trajectory + separation + direction | Weighted sum |

### Key data structures

| Structure | Purpose |
|-----------|---------|
| `DancerTrack` | Per-person state: Kalman, pose history, episode metadata |
| `DormantSnapshot` | Frozen track for resurrection: full state |
| `FrameUpdateContext` | Per-frame scope: `merge_frame`, `merge_zone_trk_indices`, `pending_updates` |
| `PendingTrackUpdate` | Deferred match awaiting post-swap application |

### Key files

| File | Purpose |
|------|---------|
| [tracker.py](../application/src/tracker.py) | Core tracker: `DancerTrack`, `DancerTracker` |
| [config.py](../application/src/config.py) | All config constants (gates, thresholds, feature flags) |
| [tracking_logger.py](../application/src/tracking_logger.py) | Structured JSONL event logger |
| [app.py](../application/src/app.py) | Main app, frame overlay, review mode |
| [motion_detector.py](../application/src/motion_detector.py) | MOG2 foreground blob detector (Phase 3) |

### Key config values (current)

| Flag | Value | Purpose |
|------|-------|---------|
| `TRACKER_MAHALANOBIS_GATE` | 16.27 | Chi² gate (df=2, 99.97%) |
| `TRACKER_MAHALANOBIS_GATE_NOISE` | 700.0 | Inflated R for gate (~80px radius) |
| `TRACKER_MAX_DISPLACEMENT_RATIO` | 0.5 | Max displacement from last position (× dist_threshold) |
| `TRACKER_CLOSE_PROXIMITY_RATIO` | 0.35 | Proximity ratio for merge/swap zones |
| `TRACKER_MATCH_GATE_RATIO` | 0.95 | Match distance as fraction of person height |
| `TRACKER_CASCADED_MATCHING` | True | 2-pass established/tentative matching |
| `TRACKER_MERGE_DIRECTION_SWAP` | True | Post-merge velocity-direction swap |
| `TRACKER_TWO_OPT_SWAP` | True | Post-assignment 2-opt cost-based swap |
| `TRACKER_ESTABLISHED_FRAMES` | 15 | Hits before a track is "established" |
| `TRACKER_POSE_HISTORY_DEPTH` | 15 | Frames of pose history for trajectory cost |
| `TRACKER_TRAJECTORY_WEIGHT` | 0.30 | Trajectory cost weight in crowded zones |

---

## Operations

### Run and test

```bash
# Normal launch
./run.sh --project tango-phone

# Review mode — jump to a specific frame
cd application && uv run --no-sync python src/main.py \
  --project tango-phone --slot 7 --speed 0.5 --pause-at-frame 300
```

Logs go to `application/tracking_events.jsonl` (legacy fallback) or, when playback is active, to per-run session directories under `projects/<project>/sessions/`.

### Session directories

Each playback start creates an isolated session directory:

```
projects/tango-phone/sessions/
  20260313_143022_slot7/
    tracking_events.jsonl   ← clean log for this run only (write mode)
    session.json            ← metadata (slot, model, imgsz, path)
    issues/                 ← F8 issue reports (JSON + PNG + issues.jsonl)
  latest -> 20260313_143022_slot7/   ← symlink to most recent session
```

Benefits:
- No append-forever files — each run gets a clean log
- `latest` symlink for quick access to the most recent session
- `camera_id` field on every event (multi-camera ready)
- Issue reports (F8) land inside the session, not a shared folder

### Issue capture

Press `F8` during playback → choose issue type → saved to the active session's `issues/` subfolder (or legacy `projects/<project>/review_issues/` if no session is active).

### Log analysis

```bash
# Latest session events (via 'latest' symlink)
python3 -c "
import json, pathlib
log = pathlib.Path('projects/tango-phone/sessions/latest/tracking_events.jsonl')
for line in log.open():
    e = json.loads(line)
    ev = e.get('event','')
    if ev in ('MERGE_DIRECTION_SWAP','CASCADE_OCCLUSION_SWAP','TWO_OPT_SWAP','DISPLACEMENT_GATE'):
        print(f\"F{e['frame']} {ev}: {e.get('data',{})}\")
"
```

### Important

Never run `uv run python -c "from tracker import ..."` — this triggers a dependency sync that breaks CUDA packages. Always validate via the full app.

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 03-03 | Skip ReID embeddings | Similar costumes → appearance not discriminative |
| 03-03 | Skip JPDA/MHT | Overkill for ≤6 dancers; cascaded Hungarian + 2-opt sufficient |
| 03-03 | Mahalanobis gate + separate gate noise | Gate R=700 (~80px radius) vs Kalman R=2 for smoothing; prevents teleport assignments |
| 03-03 | Cascaded matching (est-first) | Prevents tentative tracks from stealing detections |
| 03-03 | Post-cascade swap checks (1c, 1d) | Fix systematic misassignment patterns after deferred assignment |
| 03-03 | Velocity clamps post-update | Pre-update clamps are no-ops (Kalman gain recomputes velocity from innovation) |
| 03-11 | Temporal pose signature (Phase 2) | 15-frame pose history + trajectory cost (30% crowded); more discriminative than single-frame skeleton with similar costumes |
| 03-11 | Episode metadata over booleans | `_occluded` flag was timing-dependent → episode timestamps are robust |
| 03-12 | Structural refactor | `update()` → 8 named stages; all policies in dedicated helpers; `FrameUpdateContext` |
| 03-12 | MOG2 over LK optical flow | Small dancers (~50px) lack features for sparse LK; MOG2 gives full silhouette, survives pauses |
| 03-12 | IoU cost + 2-opt swap | 6th cost term (10%/5% weight); 2-opt catches cost-reducing swaps that heuristic checks miss |
| 03-13 | `CLOSE_PROXIMITY_RATIO` 0.6 → 0.35 | 0.6 made `close_dist=45px` — most tracks falsely "close" |
| 03-13 | Per-track merge zones | Frame-level merge context caused far-apart tracks to swap; per-track checks proximity to other matched detections or occluded tracks |
| 03-13 | Shadow immunity for established tracks | `_detect_shadow_tracks` was killing established dancers |
| 03-13 | Mahalanobis gate 9.21 → 16.27 | Kalman velocity amplification during convergence caused chi²=13.4 on correct match → gated × forced wrong assignment |
| 03-13 | Displacement gate (0.5 × dist_threshold) | Caps centroid jump for recently-matched est. tracks. p99 of good matches = 18px; all matches >40px were swap errors. Prevents skeleton similarity from masking bad centroid assignments |
| 03-13 | `merge_frame` counts only est+recent tracks | Ghost tracks inflated count → merge_frame fired 88% of frames |

---

## Lessons Learned

These observations are important for anyone continuing this work:

1. **Post-hoc swap correction is inherently fragile.** Timing-dependent on flag states; one fix can trigger false positives elsewhere. Pre-assignment gates (Mahalanobis, displacement) are more robust. Use post-hoc swaps only as a last resort.

2. **Merge-frame inflation is the #1 silent killer.** Ghost tracks from scenery (wind, trees) count as "active" → `n_det < n_tracks` fires almost every frame → all tracks get merge context → swap detectors misfire. Always count only established, recently-matched tracks.

3. **Frame-level context is too coarse for merge handling.** Even correctly-detected merge frames need per-track proximity checks. Two tracks 250px apart should not share merge context just because the frame has a merge.

4. **Kalman velocity amplifies during track convergence.** When two tracks approach, their velocity estimates can spike (vx=+32). This makes the Mahalanobis gate reject correct matches. The gate needs to be generous; use the displacement gate for teleport protection instead.

5. **Skeleton matching can mask centroid jumps.** A track can match a detection 75px away if the body shape is similar enough. The displacement gate catches this by enforcing a hard centroid distance cap independent of pose similarity.

6. **The event log is essential.** Every diagnostic insight came from `tracking_events.jsonl`. The `FRAME_SUMMARY` with full track state (centroid, velocity, episode metadata) was used in every debugging iteration.

7. **Multiple cameras/trackers share the same log file.** When analyzing events, filter by segment (between consecutive `FRAME_SUMMARY` entries at the same frame number) to isolate one camera's tracker.
