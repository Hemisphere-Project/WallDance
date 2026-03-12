# Tracking Robustness Plan

**Goal**: Eliminate ID steal → ghost creation, and survive complex occluded crossovers.  
**Context**: Similar costumes (ReID low-value), ≤6 dancers, comfortable GPU headroom (>15ms spare).  
**Started**: 2026-03-03  
**Last updated**: 2026-03-12  

---

## Status at a Glance

| Area | Status | Notes |
|------|--------|-------|
| Logging & diagnostics (Phase 0) | **DONE** | `TrackingLogger` → JSONL, frame overlay, `FRAME_SUMMARY` |
| Mahalanobis gate (Phase 1.1) | **DONE** | Chi²(df=2, 99%) = 9.21, gate noise R=700 (~80px radius) |
| Cascaded matching (Phase 1.2) | **DONE** | Established-first + tentative-second, deferred updates |
| Gate noise fix (Phase 1b) | **DONE** | Separate R_gate from Kalman R; anti-merge 2.0; ID counter fix |
| Cascade occlusion swap (Phase 1c) | **DONE** | Swap + suppression window (5f) + post-update velocity clamp |
| Merge direction swap (Phase 1d) | **DONE** | Direction-reversed pair swap + velocity clamp on both tracks |
| Temporal pose signature (Phase 2) | **DONE** | Pose history (15f), trajectory cost (30% in crowded), merge-frame guard |
| Tracker structural refactor | **DONE** | `update()` decomposed into named stages; all policies in dedicated helpers |
| Dormant continuity preservation | **DONE** | Full state rehydration on resurrection (pose, velocity, merge metadata) |
| Episode metadata | **DONE** | `last_match_frame`, `last_occluded_frame`, `last_merge_frame`, `merge_episode_id` |
| Review workflow | **DONE** | CLI startup, F8 issue capture, structured issue packets |
| Replay regression report | **DONE** | `replay_report.sh` for session summary / comparison |
| IoU cost signal (Phase 1.3) | Not started | 6th cost term — cheap, high value |
| Optical flow bridge (Phase 3) | Not started | LK flow for occluded-track prediction |
| Occupancy grid (Phase 5) | Not started | Only if residual multi-body crossover issues remain |

### Slot 6 Known Issues

| Issue | Frames | Status |
|-------|--------|--------|
| Edge-entry ID theft | ~237 | **FIXED** — Mahalanobis gate blocks 294px teleport |
| Occlusion zone ghosts | ~430-477 | **FIXED** — Gate noise 700, anti-merge 2.0 |
| Cascade occlusion swap | ~366-393 | **FIXED** — Swap + suppression + velocity clamp |
| Est-est merge swap | ~297-305 | **FIXED** — Merge-frame pose guard + episode metadata |
| Edge occlusion swap | ~377-380 | **FIXED** — Episode metadata replaces live `_occluded` gate |

**Awaiting validation**: Full Slot 6 replay to confirm all fixes hold together after the structural refactor.

---

## Next Steps (priority order)

### 1. Validate refactored tracker on Slot 6

Run the app, replay Slot 6, verify no regressions at the known-issue frames (237, 297, 366, 430).
Then compare sessions:

```bash
./replay_report.sh --compare previous latest
./replay_report.sh --compare previous latest --start-frame 280 --end-frame 400
```

Target: zero new `NEW_TRACK` inflation, zero lost swap corrections.

### 2. Phase 1.3 — IoU cost signal

Add bbox IoU as a 6th cost term in `_compute_cost_matrix`:

- Predicted bbox = last bbox translated by Kalman velocity
- `iou_cost = 1.0 - iou`
- Blend weight: ~10% normal, ~5% crowded
- **Config**: `TRACKER_IOU_WEIGHT = 0.10`, `TRACKER_CLOSE_IOU_WEIGHT = 0.05`
- **Why**: Two dancers at the same centroid but different bbox extents are distinguished. Nearly free to compute.
- Implement as a new helper `_compute_iou_cost()` alongside existing cost helpers, wire into `_combine_assignment_cost()`.

### 3. Phase 2.3 — Post-assignment swap detector (2-opt)

For each pair of matched (detection, track) assignments where both tracks are in close proximity:

- Check if swapping the two assignments reduces total cost
- Also check: did the assignment cause velocity reversal AND skeleton jump simultaneously?
- Only runs on nearby matched pairs — O(k²) where k is typically 2–4
- **Config**: `TRACKER_SWAP_DETECT_ENABLED = True`

### 4. Phase 3 — Optical flow bridge

Track pixel motion through detector gaps using sparse LK optical flow:

- `FlowTracker` class in `optical_flow.py` — 5-8 highest-confidence keypoints per track
- LK parameters: window 21×21, 3 pyramid levels
- Flow-assisted Kalman measurement update for missing tracks (inflated noise R×4)
- Flow-predicted position in `_compute_cost_matrix` for `time_since_update > 0` tracks
- Cost: ~1-3ms for 6 dancers × 8 points, CPU only
- **Config**: `TRACKER_OPTICAL_FLOW_ENABLED = True`, `TRACKER_FLOW_MAX_POINTS = 8`

### 5. Phase 4 — Track lifecycle refinements

- **4.1 Aggressive dormant matching**: Before creating any new track, check dormant pool for tracks that died < 10 frames ago using relaxed position gate (2×) + pose trajectory similarity
- **4.2 Anti-steal cooldown**: Freeze victim's aging for 5 frames when the swap detector identifies a steal
- **4.3 Pose trajectory in resurrection**: Score dormant candidates using trajectory similarity in addition to position + height + keypoints

---

## Architecture

### `update()` per-frame flow (tracker.py)

1. `_begin_frame_update()` — tick suppression counters, create `FrameUpdateContext`
2. `_predict_tracks_for_frame()` — Kalman predict, set `merge_frame`
3. `_run_matching_phase()`:
   - **Pass 1**: established (non-suppressed) tracks vs all detections → deferred
   - **Pass 2**: tentative + suppressed tracks vs remaining detections → deferred
   - **Phase 1c**: `_check_occlusion_cascade_swaps()` — swap if est steals from tent
   - **Phase 1d**: `_check_merge_direction_swaps()` — swap if direction-reversed
   - Apply all deferred updates via `FrameUpdateContext.pending_updates`
   - Post-update velocity clamp on swapped tracks
4. `_resolve_unmatched_detections()` — force-update / fallback / resurrect / new track
5. `_apply_occlusion_aging()` — fractional aging for tracks near matched tracks
6. `_finalize_track_lifecycle()` — expire → dormant, age dormant, shadow detection
7. `_log_frame_summary()` — structured `FRAME_SUMMARY` event

### Key data structures

| Structure | Purpose |
|-----------|---------|
| `DancerTrack` | Per-person state: Kalman filter, pose history, episode metadata |
| `DormantSnapshot` | Frozen track for resurrection: full state incl. pose/velocity/merge history |
| `FrameUpdateContext` | Per-frame scope: `merge_frame`, `pending_updates`, `post_update_clamp_indices` |
| `PendingTrackUpdate` | Deferred match awaiting post-swap application |
| `ClosestTrackResult` | Nearest-track lookup result for unmatched-detection resolution |

### Decomposed helpers (where each policy lives)

| Policy area | Helpers |
|-------------|---------|
| Matching cost | `_is_detection_in_crowded_zone`, `_mahalanobis_gate_allows`, `_compute_position_cost`, `_compute_keypoint_cost`, `_compute_separation_penalty`, `_compute_direction_penalty`, `_combine_assignment_cost` |
| Assignment acceptance | `_compute_dynamic_match_threshold`, `_is_suspicious_merge_candidate`, `_commit_match`, `_apply_track_update` |
| Unmatched resolution | `_find_closest_track`, `_get_creation_gate`, `_select_force_update_target`, `_try_force_update_unmatched_detection`, `_try_fallback_update_unmatched_detection`, `_create_new_track` |
| Occlusion aging | `_collect_matched_positions`, `_occlusion_distance_for_track`, `_is_track_near_matched_positions`, `_apply_fractional_occlusion_aging`, `_mark_track_occluded` |
| Lifecycle | `_effective_max_age_for_track`, `_retire_track_to_dormant`, `_retire_expired_active_tracks`, `_age_and_prune_dormant_tracks` |
| Swap corrections | `_check_occlusion_cascade_swaps`, `_check_merge_direction_swaps`, `_tracks_share_recent_merge_context` |

### Key files

| File | Purpose |
|------|---------|
| [tracker.py](../application/src/tracker.py) | Core tracker: `DancerTrack`, `DancerTracker`, all matching/swap/lifecycle logic |
| [config.py](../application/src/config.py) | All config constants (gates, thresholds, feature flags) |
| [tracking_logger.py](../application/src/tracking_logger.py) | Structured JSONL event logger |
| [app.py](../application/src/app.py) | Main app, frame overlay, review mode, issue reporting |
| [replay_report.py](../application/replay_report.py) | Offline session summary and comparison tool |
| [replay_report.sh](../replay_report.sh) | Safe launcher (uses `uv run --no-sync` to preserve CUDA) |

### Key config flags

| Flag | Value | Purpose |
|------|-------|---------|
| `TRACKER_MAHALANOBIS_GATE` | 9.21 | Chi² gate threshold |
| `TRACKER_MAHALANOBIS_GATE_NOISE` | 700.0 | Inflated R for gate (~80px radius) |
| `TRACKER_CASCADED_MATCHING` | True | 2-pass established/tentative matching |
| `TRACKER_CASCADE_OCCLUSION_SWAP` | True | Post-cascade swap for est-steals-from-tent |
| `TRACKER_CASCADE_SUPPRESSION_FRAMES` | 5 | Frames to suppress swapped est from Pass 1 |
| `TRACKER_MERGE_DIRECTION_SWAP` | True | Post-merge velocity-direction swap |
| `TRACKER_ESTABLISHED_FRAMES` | 15 | Hits before a track is "established" |
| `TRACKER_CLOSE_PROXIMITY_RATIO` | 0.6 | Proximity ratio for swap detection |
| `TRACKER_MATCH_GATE_RATIO` | 0.90 | Match distance as fraction of person height |
| `TRACKER_POSE_HISTORY_DEPTH` | 15 | Frames of skeleton history for trajectory cost |
| `TRACKER_TRAJECTORY_WEIGHT` | 0.30 | Weight of trajectory cost in crowded zones |

---

## Operations

### Run and test

```bash
# From repo root — normal launch
cd application && uv run --no-sync python src/main.py

# Review mode — jump straight to a slot/frame
cd application && uv run --no-sync python src/main.py \
  --project my_project --slot 6 --speed 0.5 --pause-at-frame 300
```

Frame overlay shows frame number (top-right). Logs go to `application/tracking_events.jsonl`.

### Issue capture during playback

- Press `F8` or click `ISSUE` in the playback toolbar
- Choose issue type: `id_swap`, `abusive_merge`, `ghost_track`, `false_new_id`, `track_loss`, `other`
- Issue packets saved to `projects/<project>/review_issues/`

### Replay regression report

```bash
./replay_report.sh --session latest
./replay_report.sh --compare previous latest
./replay_report.sh --compare previous latest --start-frame 280 --end-frame 400
```

Uses `uv run --no-sync` to preserve CUDA. Reports: event counts, ID inflation, hotspot frames.

### Log analysis (PowerShell)

```powershell
$lines = Get-Content tracking_events.jsonl
$resets = @(); for($i=0; $i -lt $lines.Count; $i++) { if($lines[$i] -match '"RESET"') { $resets += $i } }
$session = $lines[$resets[-1]..($lines.Count-1)]
$session | Select-String 'MERGE_DIRECTION_SWAP|CASCADE_OCCLUSION_SWAP|CASCADE_SUPPRESSED'
```

### Important: never run `uv run python -c "from tracker import ..."` for import tests

This triggers a dependency sync and breaks CUDA packages. Always validate via the full app.

---

## Structural Refactor Summary

Completed 2026-03-11 / 2026-03-12. Steps executed in order:

1. **Unified association paths** — FORCE_UPDATE, FALLBACK_UPDATE, and normal MATCH all route through `_commit_match()` → `_apply_track_update()`, getting consistent merge-frame handling and logging.
2. **Decomposed `update()` into frame stages** — `_begin_frame_update`, `_predict_tracks_for_frame`, `_run_matching_phase`, `_resolve_unmatched_detections`, `_apply_occlusion_aging`, `_finalize_track_lifecycle`, `_log_frame_summary`.
3. **Introduced per-frame context** — `FrameUpdateContext` replaces tracker-wide mutable scratch state (`_is_merge_frame`, `_post_update_clamp_indices`).
4. **Preserved continuity across resurrection** — `DormantSnapshot` now carries pose history, vx history, bbox/confidence history, smoothed centroid, and all episode metadata. `restore_continuity()` rehydrates everything.
5. **Replaced boolean occlusion gate with episode metadata** — `last_match_frame`, `last_occluded_frame`, `last_merge_frame`, `last_reacquired_frame`, `merge_episode_id`. Phase 1d swap check uses `_tracks_share_recent_merge_context()` instead of `_occluded`.
6. **Extracted lifecycle helpers** — `_get_creation_gate`, `_create_new_track`, `_effective_max_age_for_track`, `_retire_track_to_dormant`, `_retire_expired_active_tracks`, `_age_and_prune_dormant_tracks`.
7. **Extracted unmatched-detection helpers** — `_find_closest_track`, `_select_force_update_target`, `_try_force_update_unmatched_detection`, `_try_fallback_update_unmatched_detection`.
8. **Extracted occlusion-aging helpers** — `_collect_matched_positions`, `_occlusion_distance_for_track`, `_is_track_near_matched_positions`, `_apply_fractional_occlusion_aging`, `_mark_track_occluded`.
9. **Extracted matching-policy helpers** — `_is_detection_in_crowded_zone`, `_mahalanobis_gate_allows`, `_compute_position_cost`, `_compute_keypoint_cost`, `_compute_separation_penalty`, `_compute_direction_penalty`, `_combine_assignment_cost`, `_compute_dynamic_match_threshold`, `_is_suspicious_merge_candidate`.

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-03 | Skip ReID embeddings | Similar costumes → appearance not discriminative |
| 2026-03-03 | Skip JPDA/MHT | Overkill for ≤6 dancers; cascaded Hungarian + 2-opt sufficient |
| 2026-03-03 | Prioritize optical flow over ReID | Bridges occlusion gaps directly; more impactful than appearance |
| 2026-03-03 | Prioritize pose trajectory over single-frame skeleton | Temporal signature more discriminative with similar costumes |
| 2026-03-03 | Phase 1.1 Mahalanobis gate | Chi²(df=2, 99%) = 9.21 in `_compute_cost_matrix`; blocks teleport assignments |
| 2026-03-03 | Phase 1.2 Cascaded matching | `_run_assignment_pass()` helper; established-first + tentative-second |
| 2026-03-03 | Separate gate noise from smoothing noise | `R_gate=700` gives ~80px radius; Kalman `R=2` kept for smoothing quality |
| 2026-03-03 | Relaxed MERGE_SIZE_RATIO 1.3→2.0 | 1.3 blocked legitimate detections during normal pose variation |
| 2026-03-03 | Post-cascade occlusion swap (Phase 1c) | Deferred updates + speed-based swap criterion; suppression window prevents reclaim |
| 2026-03-03 | Post-merge direction swap (Phase 1d) | 20-frame `_vx_history`; direction from older half; velocity clamp on both tracks |
| 2026-03-03 | Velocity clamps moved to post-update | Pre-update clamps were no-ops: Kalman gain recomputes velocity from innovation |
| 2026-03-11 | Phase 2 trajectory cost implemented | `_pose_history` buffer (15f), 30% weight in crowded zones |
| 2026-03-11 | Merge-frame pose history guard | Skip recording when `len(detections) < len(tracks)` — prevents merged-skeleton contamination |
| 2026-03-11 | Unified association commits | All paths through `_commit_match` / `_apply_track_update` |
| 2026-03-11 | Per-frame context object | `FrameUpdateContext` replaces tracker-wide mutable scratch state |
| 2026-03-11 | Preserve dormant continuity | Full state rehydration on resurrection |
| 2026-03-11 | Episode metadata replaces boolean gate | Tracks record merge/occlusion/reacquisition timestamps |
| 2026-03-12 | Structural refactor complete | 9 extraction steps + code review — logic verified clean and sound |

---

## Historical Investigation Notes

<details>
<summary>Edge-entry ID theft — root cause analysis (F237)</summary>

**Hypothesis 1 confirmed: Hungarian global optimum vs local correctness.**

| Frame | State |
|-------|-------|
| 236 | 2 tracks, 2 dets — ID1 at [351, 486], ID3 at [627, 491]. Stable. |
| 237 | 3 dets. Hungarian: ID3 → det 2 (cost 142.7) — teleports +294px. det 0 → NEW_TRACK ID4 (ghost). |

Cost 142.7 < threshold 163.0, so the teleport was accepted. Pure global-cost misassignment.
Fixed by Mahalanobis gate (chi² ~28,000 >> 9.21 → cost = 1e6).

</details>

<details>
<summary>Occlusion zone ID loss — root cause analysis (F430-477)</summary>

**Mahalanobis gate was 13× too tight.** Kalman R=2 + 30 updates → P≈1.84 → S≈3.84.
Effective radius: 5.9px. YOLO jitter is 10-50px → 90% of matches blocked.
Cascade: FORCE_UPDATE misassignment → ghost id11 at F451 → id9 dormant at F472.

Fixed by separate gate noise (R_gate=700 → ~80px radius).

</details>

<details>
<summary>Cascade occlusion swap — root cause analysis (F366-393)</summary>

Established-first cascade priority gives merged detection to exiting track. Tentative track starves and dies.

Three iterations to fix:
1. Post-cascade swap: detect and swap when est is exiting, tent is stationary
2. Suppression window: prevent est from reclaiming in Pass 1 for 5 frames
3. Post-update velocity clamp: prevent Kalman velocity spike from drifting tent away

</details>

<details>
<summary>Established-established merge swap — root cause analysis (F297-305)</summary>

Kalman CA model drifts occluded track during merge. On re-emergence, predicted position is closer to wrong body.

| Frame | ID1 | ID2 | Event |
|-------|-----|-----|-------|
| 294 | x=484 v=-7 | x=436 v=+14 | Last 2-det frame |
| 297 | x=485 v=+3 occ | x=458 v=+14 | 1 det, ID1 OCCLUDED |
| 304 | x=539 v=+18 occ | x=482 v=-4 | ID1 drifted 55px RIGHT |
| **305** | x=507 v=+6 | x=466 v=-19 | SWAP |

Fixed by Phase 1d direction swap + merge-frame pose guard (skip `_pose_history` recording during merge).

</details>

<details>
<summary>F380 edge swap — analysis</summary>

id2 was occluded F365-F373, returned at F374, `_occluded` cleared. By F377, id2 and id3 are 25px apart.
Old swap detector didn't fire because neither was `_occluded` at that moment.
Attempted fix (`_frames_since_occluded` counter) reverted — caused false swaps at F363/F374.
Resolved by episode metadata: `_tracks_share_recent_merge_context()` replaces the live-boolean gate.

</details>

<details>
<summary>Lesson: whack-a-mole pattern in post-hoc swap correction</summary>

Post-hoc swap correction (Phase 1c/1d style) is inherently fragile:
- Timing-dependent: `_occluded` flag state, `_vx_history` corruption
- One fix can trigger false positives in other scenarios
- Root cause: cost matrix can't distinguish tracks during/after merge → need better pre-assignment signals (Phase 2/3)

</details>

---

## Phase Specifications (reference)

<details>
<summary>Phase 0 — Diagnostics & Logging</summary>

All items complete. See `TrackingLogger` in `tracking_logger.py`.

- `TrackingLogger` class: structured, frame-stamped records
- Event types: MATCH, MATCH_REJECTED, NEW_TRACK, RESURRECT, DORMANT, KILL, ANTI_MERGE, OCCLUDED, MAHALANOBIS_GATE, CASCADE_OCCLUSION_SWAP, MERGE_DIRECTION_SWAP, FRAME_SUMMARY, etc.
- Rolling deque (3000 entries), auto-flush to `tracking_events.jsonl` every 5s
- Frame overlay on preview (top-right)
- Per-frame `FRAME_SUMMARY` with full track states

</details>

<details>
<summary>Phase 1 — Hardened Association</summary>

**1.1 Mahalanobis gate** [DONE]: Chi² distance in cost matrix, gate at 9.21, inflated noise R_gate=700.

**1.2 Cascaded matching** [DONE]: Two-pass established-first; `_run_assignment_pass()` helper.

**1b Gate noise fix** [DONE]: Separate R_gate from Kalman R; anti-merge 2.0; ID counter fix.

**1c Cascade occlusion swap** [DONE]: `_check_occlusion_cascade_swaps()` + suppression + velocity clamp.

**1d Merge direction swap** [DONE]: `_check_merge_direction_swaps()` + `_vx_history` + velocity clamp.

**1.3 IoU cost signal** [NOT STARTED]: See Next Steps §2.

</details>

<details>
<summary>Phase 2 — Temporal Pose Signature</summary>

**2.1 Pose trajectory buffer** [DONE]: `_pose_history` deque (15 frames), centroid-normalized keypoints + mask + aspect ratio. Merge-frame guard skips recording during body mergers.

**2.2 Trajectory cost in cost matrix** [DONE]: `trajectory_cost()` method with exponential decay weighting (0.7^age). 30% weight in crowded zones via `_combine_assignment_cost()`.

**2.3 Post-assignment swap detector** [NOT STARTED]: See Next Steps §3.

</details>

<details>
<summary>Phase 3 — Optical Flow Bridge</summary>

Not started. See Next Steps §4 for specification.

**3.1** Sparse LK optical flow module (`optical_flow.py`)
**3.2** Flow-assisted prediction for missing tracks
**3.3** Flow-enhanced cost matrix

</details>

<details>
<summary>Phase 4 — Track Lifecycle Refinements</summary>

Structural cleanup (creation gate, expiry, dormant aging helpers) is done.
Behavioral enhancements not started. See Next Steps §5.

**4.1** Aggressive dormant matching for recent deaths
**4.2** Anti-steal cooldown
**4.3** Pose trajectory in dormant resurrection

</details>

<details>
<summary>Phase 5 — Occupancy-Aware Occlusion</summary>

Not started. Only pursue if Phases 1–4 leave residual multi-body crossover issues.

**5.1** Lightweight 2D occupancy grid (one cell per ~50px)
**5.2** Occlusion event handling: disable new-track creation in overlapping cells, boost flow tracking

</details>
