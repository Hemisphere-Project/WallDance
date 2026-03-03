# Tracking Robustness Plan

**Goal**: Eliminate ID steal → ghost creation, and survive complex occluded crossovers.  
**Context**: Similar costumes (ReID low-value), ≤6 dancers, comfortable GPU headroom (>15ms spare).  
**Started**: 2026-03-03  

**Methodology**: For each reported issue, first analyze logs and identify root
cause.  Then assess whether a direct, targeted fix is possible (e.g. Phase 1b/1c/1d
style post-match corrections) or whether the issue requires advancing to the next
plan phase (e.g. Phase 2 temporal signatures, Phase 3 optical flow).  Prefer
direct fixes when the failure mode is clear and self-contained; escalate to a new
phase only when the root cause is structural and can't be patched locally.

---

## Test reference video

**Slot 6** of the current project — 4 people entering and leaving the stage.  
Known issues on this clip (current state):
- Shadows: mostly handled OK by existing shadow suppression
- **ID steal → new ID creation**: primary issue — one dancer takes another's ID, victim gets a fresh ID
- **Partial merge**: when 2 people walk in line (one behind the other), they temporarily merge into one detection; recovery is OK but not instant
- **ID swap**: secondary issue during close crossovers

Use this clip as the benchmark for every phase checkpoint.

---

## Phase 0 — Diagnostics & Logging Infrastructure

> **Before fixing anything, build the tools to see what's happening.**
> We need structured tracking logs tied to frame numbers so that when an
> ID issue is spotted during playback, we can pause, note the frame, and
> trace exactly what the tracker decided and why.

### 0.1 Structured tracking event log
- [x] **Implement** `TrackingLogger` class in `tracker.py` (or new `tracking_logger.py`)
- Replaces ad-hoc `TRACKER_DEBUG` print statements with structured, frame-stamped records
- Each log entry: `{frame: int, event: str, data: dict}`
- Event types to log:
  - `MATCH` — detection d matched to track t (cost, threshold, was_crowded)
  - `MATCH_REJECTED` — cost exceeded threshold (cost, threshold, track_id)
  - `SWAP_DETECTED` — 2-opt swap fired (track_a, track_b, old_cost, new_cost) *(Phase 2+)*
  - `NEW_TRACK` — new ID created (track_id, position, min_dist, gate, reason)
  - `RESURRECT` — dormant ID resurrected (track_id, dormant_age, score)
  - `DORMANT` — track moved to dormant pool (track_id, was_occluded, edge_exit)
  - `KILL` — track removed (track_id, reason: shadow/expired/merged)
  - `ANTI_MERGE` — merged detection rejected (track_id, det_area, avg_area)
  - `OCCLUDED` — track marked occluded (track_id, near_track_id)
  - `MAHALANOBIS_GATE` — entry gated out (track_id, chi2_dist) *(Phase 1+)*
- Log storage: rolling deque (last 3000 entries ~= 200 frames × 15 events/frame)
- **File output**: auto-flush to `tracking_events.jsonl` every 5 seconds (append mode, rotate on playback restart)
- [x] **Config**: Add `TRACKER_EVENT_LOG_ENABLED = True`, `TRACKER_EVENT_LOG_FILE = "tracking_events.jsonl"` to `config.py`

### 0.2 Frame number overlay on preview
- [x] **Implement** in `app.py` preview rendering path (near `_draw_height_ruler` call)
- During playback: overlay `Frame: {playback_frame}/{playback_total}` on the preview image (top-right corner, `cv2.putText`)
- During live: overlay cumulative frame counter (add `self._total_frame_count` to `WallDanceApp`)
- Small, semi-transparent white text on dark background so it's always readable
- **Why**: When the user pauses on a problem frame, they can read the frame number and report it

### 0.3 Per-frame track summary in log
- [x] **Implement** in `DancerTracker.update()`, emitted every frame
- Log entry: `FRAME_SUMMARY` with:
  - `frame`: frame number
  - `n_detections`: how many YOLO detections came in
  - `n_tracks`: how many active tracks
  - `track_states`: list of `{id, centroid, velocity, hits, time_since_update, is_established, is_occluded}`
  - `n_dormant`: dormant pool size
  - `matched_pairs`: list of `(det_idx, track_id, cost)`
- **Why**: Single log entry lets you reconstruct the full tracker state at any frame — essential for post-mortem debugging

### 0.4 Workflow: how to report issues
- [x] **Document** in this section (no code needed)
- Workflow:
  1. Load slot 6, start playback at normal or 0.5× speed
  2. When you see an ID problem, **pause** (Space or pause button)
  3. Note the **frame number** from the overlay (top-right of preview)
  4. Note what happened: "ID 3 stole ID 2's detection" / "ID 5 appeared as new but was ID 1" / "IDs 2 and 4 swapped"
  5. Report: `Frame 1247: ID steal — ID 3 took ID 2, ID 2 got new ID 7`
  6. I examine `tracking_events.jsonl` around frame 1247 to see exactly what the tracker decided
  7. The JSONL log is in `application/` working directory — I can read it directly with tools

### 0.5 Launch verification
- [x] **Verify** the app starts correctly from terminal: `cd application; uv run --no-sync python src/main.py`
- [x] **Verify** slot 6 plays back and frame counter overlay is visible
- [x] **Verify** `tracking_events.jsonl` is being written during playback
- [x] **Verify** I can read the log file and correlate events to frame numbers

### Phase 0 — Validation checkpoint
- [x] Replay slot 6 — confirm frame overlay is visible and log file is populating
- [x] Pause on frame 551 — 4 tracks (IDs 1,2,4,5). Log frame 549 matches: 4 tracks, correct positions, low costs (3.4-12.7). Frame offset due to flush timing.
- [x] **Decision**: Proceed to Phase 1

---

## Known Issue: Edge-Entry ID Theft (no occlusion)

> **Priority**: HIGH — must be root-caused with Phase 0 logs before Phase 1.

### Reproduction
1. Dancer1 (ID1) and Dancer2 (ID2) move around in the center of the image.
2. Dancer3 (ID3) enters from the right edge while Dancer2 (ID2) exits right.
3. Dancer3 (ID3) reaches the center — tracking is stable, no freeze, no loss.
4. Dancer4 enters from the right edge.
5. **Bug**: Dancer4 instantly receives ID3. Dancer3 (still in center, fully visible, never lost) is reassigned a new ID4.

### Key observations
- **No occlusion** — all three dancers (1, 3, 4) are far apart at the moment of the steal.
- **No track freeze** — Dancer3's track does not stutter, go missing, or show `time_since_update > 0` at any point.
- **The steal is instantaneous** — it happens on the exact frame Dancer4's detection first appears at the right edge.

### Hypotheses to investigate with Phase 0 logs
1. **Hungarian global optimum vs local correctness**: The cost matrix may produce a globally cheaper assignment by swapping ID3 to the new edge detection and giving the center detection a new ID. Even if each individual cost is reasonable, the *sum* is lower with the swap. This is the classic Hungarian failure mode — it optimizes total cost, not identity preservation.
   - **Log check**: Look at the MATCH events on the theft frame. Is ID3→Dancer4 cost actually lower than ID3→Dancer3? Or is the *total* assignment cost lower after the swap?
2. **Edge entry + dormant interaction**: Dancer2 recently exited right. Their dormant snapshot may be projected near the right edge area. When Dancer4 enters nearby, the system might resurrect Dancer2's dormant as ID2 for Dancer4 — but this could cascade into the Hungarian reshuffling all other assignments.
   - **Log check**: Any RESURRECT event on the theft frame? Any dormant snapshots near the right edge?
3. **Velocity overshoot**: Dancer3 entered from the right and moved to center. Their Kalman velocity vector still points leftward. The Hungarian may find it "cheaper" to assign a rightward detection (Dancer4) to a track with leftward velocity — nonsensical but mathematically possible if the `dir_penalty` isn't firing because `is_crowded = False` (dancers are far apart).
   - **Log check**: What is `is_crowded` on the theft frame? Is `dir_penalty` being applied?
4. **Creation gate too large**: The `new_track_min_distance` gate (or the center gate multiplier) may be so large that Dancer4's edge detection can't spawn a new ID, forcing the Hungarian to assign it to an existing track.
   - **Log check**: Compare `min_dist` for Dancer4's detection vs `creation_gate`. Is Dancer4 being forced into the Hungarian instead of spawning fresh?

### Root cause (confirmed via Phase 0 logs)

**Hypothesis 1 confirmed: Hungarian global optimum vs local correctness.**

Log analysis of the theft frame (tracker-frame 237, last session):

| Frame | State |
|-------|-------|
| 236 | 2 tracks, 2 dets — ID1 at [351, 486], ID3 at [627, 491]. Stable. |
| 237 | 3 detections appear (Dancer4 enters right edge). Hungarian assigns: |
|     | ID1 → det 1 (cost 150.2) ✓ stays at [361, 482] |
|     | **ID3 → det 2 (cost 142.7)** — teleports from [627] to [921] = **+294 px** |
|     | det 0 at [620, 490] unmatched → **NEW_TRACK ID4** (ghost of Dancer3) |
| 237 | ID3 velocity spikes to **307.8 px/frame** — physically impossible |

- Cost 142.7 is under threshold 163.0, so Hungarian accepted the teleport.
- No resurrection, no occlusion, no crowding — pure global-cost misassignment.
- Hypotheses 2, 3, 4 ruled out (no RESURRECT events, `is_crowded = False`, creation gate not involved).

### Fix applied (Phase 1)
- **Mahalanobis gating**: At frame 237, ID3's Kalman covariance after 72 updates is tight.
  Estimated chi² for the 294px jump: ~28,000 >> gate 9.21 → cost set to 1e6 → Hungarian can't pick it.
- **Cascaded matching**: Established tracks match first, preventing tentative tracks from competing.

---

## Known Issue: Occlusion Zone ID Loss / Ghosts (frames ~430-477)

> **Priority**: HIGH — discovered after Phase 1 fix, during slot 6 replay.

### Reproduction
1. 4 dancers on stage. Two move very close in the center, one exits right.
2. Near tracker-frame ~430: Mahalanobis gate starts blocking nearly all matches.
3. Only 1-2 of 4 tracks match via Hungarian; the other 2-3 rely on FORCE_UPDATE.
4. At F451: ghost track id11 created (a 5th track for 3-4 real dancers).
5. At F458: ANTI_MERGE starts blocking legitimate matches for id6 every frame.
6. At F472: id9 goes dormant after 15 consecutive misses. Dancer gets ghost id11.

### Root cause (confirmed via log analysis)

**The Mahalanobis gate was 13× too tight.**

The Kalman filter's measurement noise `R=2.0` is tuned for **smoothing** — it makes the
filter trust measurements heavily, which is good for output quality.  But after ~30 updates,
the position covariance `P[:2,:2]` converges to ~1.84, making the innovation covariance
`S = P + R ≈ diag(3.84, 3.84)`.  Combined with the chi² gate of 9.21:

| Distance | chi² | Result |
|----------|------|--------|
| 5px | 6.5 | PASS |
| 10px | 26.1 | **GATED** |
| 15px | 58.6 | GATED |
| 50px | 651 | GATED |

**Effective matching radius: only 5.9 pixels.**  YOLO jitter is 10-50px → ~90% of
legitimate matches were blocked, forcing tracks into the fragile FORCE_UPDATE fallback path.

This caused a cascade:
1. FORCE_UPDATE assigns closest unmatched detection → wrong dancer in crowded zone
2. Tracks drift, accumulate `time_since_update` via fractional aging
3. Detections can't match anything → ghost track created (id11 at F451)
4. Original track (id9) starves from misses → goes dormant at F472

**Secondary issue: ANTI_MERGE too aggressive.** `TRACKER_MERGE_SIZE_RATIO=1.3` is very tight;
normal pose variation can push bbox area +30%. Changed to 2.0.

**Minor: ID counter waste.** `_try_resurrect()` created a new `DancerTrack` (incrementing
`_id_counter`) then overwrote `track_id`. Two resurrections consumed IDs 8 and 10, explaining
the gap 7→9→11.

### Fix applied (Phase 1b)
- **Separate gate noise** (`TRACKER_MAHALANOBIS_GATE_NOISE=700`): The Mahalanobis gate now
  uses `R_gate = eye(2) × 700` instead of the Kalman `R` for computing the innovation
  covariance.  This gives an effective radius of ~80px — legitimate matches pass easily,
  teleports (>120px) are still blocked, and the original 294px theft remains firmly gated.
- **Relaxed anti-merge** (`TRACKER_MERGE_SIZE_RATIO=2.0`): From 1.3 to 2.0. Enough headroom
  for normal pose variation while still catching true merges (two people in one bbox).
- **ID counter waste fix**: `_try_resurrect()` now sets the class counter to re-use the
  dormant ID instead of wasting a fresh one.

---

## Known Issue: Cascade Occlusion Swap (frames ~366-393)

> **Priority**: HIGH — discovered during Phase 1 validation, slot 6 replay.

### Reproduction
1. D1 (ID1) on the left, D2 (ID2) moving right toward the edge.
2. D3 enters from the right edge → ID3 created at frame 353 (tentative).
3. D2 (exiting right) passes through D3 (entering).  YOLO merges them into
   a single detection at frame 366 (det count drops from 3 to 2).
4. **Bug**: Cascaded matching gives the merged detection to ID2 (established,
   hits=106) — established-first priority.  ID3 (tentative, hits=8) gets nothing.
5. D2 exits the frame.  ID2 smoothly transitions onto D3's body (velocity
   drops from +18 to ≈0 in one frame).  ID3 drifts off and goes dormant
   at frame 393.
6. Result: D3 is now tracked as ID2.  The real D2 is gone.

### Root cause (confirmed via log analysis)

**Cascaded matching's established-first priority is harmful during occlusion.**

| Frame | Event |
|-------|-------|
| 353 | ID3 created at x=942 (right edge entry), tentative. |
| 358 | ID2 at x=856 (vel +12.5 → right), ID3 at x=934 (vel ≈0). Gap = 78px. |
| 364 | ID2 at x=895, ID3 at x=927. Gap = 32px. Converging. |
| **366** | YOLO drops to 2 dets. Pass 1 matches ID2 (est) to the merged det. ID3 (tent) unmatched → OCCLUDED. |
| **367** | ID2 velocity: **+18 → -0.7** (reversal = identity changed). |
| 370-390 | ID2 at x≈913, nearly stationary = tracking D3's body. ID3 Kalman prediction drifts off-screen. |
| **393** | ID3 goes DORMANT (edge_exit=true). Swap is permanent. |

The mechanism: Pass 1 gives established tracks absolute priority over all
detections.  When an exiting established track (D2, moving fast to the right)
and a tentative track (D3, stationary) overlap in a single merged detection,
the established track always wins.  After the established track exits, it's
seamlessly reassigned to the tentative track's body — a silent ID swap with
no log anomaly except the velocity reversal.

### Fix applied (Phase 1c)
- **Post-cascade occlusion swap** (`TRACKER_CASCADE_OCCLUSION_SWAP=True`):
  After both cascade passes, but **before** Kalman updates are committed,
  check for the pattern:
  1. Detection count < track count (merger/occlusion detected)
  2. An unmatched tentative track T is in close proximity to a matched
     established track E
  3. E is moving significantly faster than T (exiting behavior:
     `speed_E > speed_T × 1.5 + 3`)
  4. T's predicted position is within `close_dist` of the detection E claimed
  When all criteria are met → swap: give the detection to T, un-assign E.
  E will age out via occlusion-aware aging (correctly — it was exiting).
- **Deferred updates**: `_run_assignment_pass` now accepts `pending_updates`
  list. In cascaded mode, `.update()` calls are deferred until after the
  swap check.  Non-cascaded (legacy) mode is unchanged.
- **New log event**: `CASCADE_OCCLUSION_SWAP` with est/tent IDs and speeds.

### Iteration: Cascade suppression window (Phase 1c+)

**Problem**: The single-frame CASCADE_OCCLUSION_SWAP fires correctly (e.g. at
frame 366) but is undone on the very next frame.  On frame 367,
established-first priority in Pass 1 reclaims the detection for the exiting
track.  The tentative track starves with no detections and dies by frame 390.

**Root cause**: The swap is a one-shot per-frame correction.  Established-first
cascade priority is persistent — it overrides the swap on every subsequent frame.

**Fix applied**:
- **Cascade suppression window** (`TRACKER_CASCADE_SUPPRESSION_FRAMES=5`):
  When CASCADE_OCCLUSION_SWAP fires for established track E, E is excluded
  from Pass 1 for N frames (default 5).  During suppression, E participates
  in Pass 2 alongside tentative tracks — it can still match if spare
  detections remain, but it cannot steal priority from the tentative track.
- Implementation: `_cascade_suppressed: dict[int, int]` on DancerTracker
  maps track_id → frames remaining.  Counters decrement each frame; stale
  entries (track deleted) are auto-pruned.
- Pass 1 `est_indices` now filters out suppressed track IDs.
- Pass 2 `tent_indices` now includes suppressed established tracks.
- **New log event**: `CASCADE_SUPPRESSED` with active suppression map.
- **Cleanup**: Removed `[MERGE_DBG]` debug prints from
  `_check_merge_direction_swaps` (Phase 1d).

### Iteration 2: Velocity clamp on swap beneficiary

**Problem**: Pass 1 suppression alone is insufficient. The suppressed
established track T2 competes in Pass 2 and still wins, because the tentative
track T3's Kalman prediction drifts away from the detection.

**Root cause**: The merged detection centroid is offset from T3's true position
(it's a blend of two bodies). When T3 receives this offset detection via the
swap, Kalman interprets the 14px position jump as velocity (v=-14.2). On
subsequent frames T3's prediction drifts far left (895→872→841→805...) and
by F371 it is Mahalanobis-gated (chi²=10.8 > gate 9.21). T2, even in Pass 2,
is always closer and wins every frame. T3 starves and dies.

**Fix applied**:
- **Velocity clamp**: When CASCADE_OCCLUSION_SWAP fires and gives the merged
  detection to tentative track T, clamp T's Kalman velocity and acceleration
  to zero (`kf.x[2:] = 0.0`).
- ~~Initially placed before the deferred `.update()` call — but this was
  ineffective because the Kalman gain recomputes velocity from the
  innovation (predicted - measured), undoing the clamp.~~
- **Moved to post-update** (Phase 1c+++): clamp fires **after** all deferred
  `track.update()` calls via `_post_update_clamp_indices` set.  Swap methods
  register track indices; the `update()` loop applies clamps after Kalman.
- Combined with Pass 1 suppression, this ensures the tentative track keeps
  matching until it accumulates enough hits to become established.

---

## Known Issue: Established-Established Merge Swap (frames ~297-305)

> **Priority**: HIGH — discovered during Phase 1 validation, slot 6 replay.

### Reproduction
1. D1 (ID1, established, 200 hits) on the right, moving LEFT (vel ≈ -7).
2. D2 (ID2, established, 64 hits) entered from left, moving RIGHT (vel ≈ +14).
3. D2 passes in front of D1.  They converge (48px gap at frame 294).
4. Frame 291+: Mahalanobis gate blocks correct matches at 89px (chi²=11 >
   gate 9.21, effective radius ~80px).  Both tracks fall to FORCE_UPDATE
   (correct assignment, but bypasses cost matrix).
5. Frame 297: YOLO merges to 1 detection (D2 in front).  Both established →
   both in Pass 1.  ID2 (closer to merged det) gets it.  ID1 → OCCLUDED.
6. Frames 297-304: ID1's Kalman (constant-acceleration model) extrapolates
   rightward: vel 0→+3→+6→+9→+12→+15→+18.  ID2 tracks merged centroid.
7. **Frame 305**: 2 detections return.  Hungarian matches:
   - ID1 (predicted at x=557) → det1 at x=507 (D2's body) — cost 156
   - ID2 (at x=482) → det0 at x=466 (D1's body) — cost 25
8. **Result**: ID1 now tracks D2 (going right), ID2 tracks D1 (going left).

### Root cause

**Kalman drift during occlusion + spatial re-match on wrong side.**

| Frame | ID1 (D1) | ID2 (D2) | Event |
|-------|----------|----------|-------|
| 294 | x=484 v=-7 | x=436 v=+14 | Last 2-det frame, 48px gap |
| 295 | x=484 v=0 | x=443 v=+7 | FORCE_UPDATE (gate too tight), vel corrupted |
| 297 | x=485 v=+3 occ | x=458 v=+14 | 1 det. ID1 OCCLUDED. |
| 304 | x=539 v=+18 occ | x=482 v=-4 | ID1 drifted 55px RIGHT of entry |
| **305** | x=507 v=+6 | x=466 v=-19 | **SWAP**: ID1→right det, ID2→left det |

Two reinforcing mechanisms:
1. Constant-acceleration Kalman extrapolation pushes ID1 far from its true
   position during occlusion (from x=484 to x=539).
2. After separation, ID1's predicted position (x=557) is closer to D2's
   detection (x=507) than to D1's detection (x=466).  ID2 meanwhile sits
   near the merged centroid (x=482) and matches D1's detection (x=466).

### Fix applied (Phase 1d)
- **Post-merge direction swap** (`TRACKER_MERGE_DIRECTION_SWAP=True`):
  After cascade passes (and Phase 1c swap), but **before** Kalman updates,
  check pairs of matched tracks for velocity-direction reversal:
  1. At least one track was occluded (merge exit)
  2. Dominant velocity directions are opposite (+1 vs -1) — computed from the
     older half of a 20-frame `_vx_history` buffer (resistant to merge
     corruption)
  3. Matched detections are close (just separated from merge zone)
  4. Assignment is direction-reversed: LEFT-going track matched RIGHT
     detection and vice versa
  When all criteria are met → swap assignments.
- **Velocity direction buffer**: `DancerTrack._vx_history` (deque, maxlen=20)
  records x-velocity on each matched frame.  `get_dominant_vx_direction()`
  returns the dominant sign from the older half of the buffer.
- **New log event**: `MERGE_DIRECTION_SWAP` with track IDs and directions.

### Iteration: Velocity clamp on merge direction swap (Phase 1d+)

**Problem**: The F306 swap fires correctly, but the detection-jump creates a
Kalman velocity spike (v=33→60).  This spike causes Hungarian to cross-match
on the next frame (F310), triggering an oscillation cycle:
swap → velocity shock → cross-match → merge → swap → shock, until
`_vx_history` gets corrupted and the swap detector fails permanently.

**Root cause**: When we swap detections between two tracks, each track receives
a detection far from its Kalman prediction.  The Kalman filter interprets this
position jump as real velocity, creating a spike that feeds back into the cost
matrix and causes cross-matching.

**Fix applied**:
- **Velocity clamp on both tracks**: After MERGE_DIRECTION_SWAP fires, zero
  out Kalman velocity and acceleration on both swapped tracks
  (`kf.x[2:] = 0.0`).  Same technique as CASCADE_OCCLUSION_SWAP clamp.
- ~~Initially placed before the deferred `.update()` call — ineffective
  because the Kalman gain recomputes velocity from the innovation.~~
- **Moved to post-update** (Phase 1d++): clamp fires **after** all deferred
  `track.update()` calls via `_post_update_clamp_indices` set.
- Both tracks then predict from their updated position with zero velocity,
  giving Hungarian a clean cost matrix on the next frame.

---

## Phase 1 — Hardened Association (prevent steals at source)

> **Gate bad matches before they happen.** The root cause of most ID steals
> is Hungarian picking a globally-cheap but locally-implausible assignment.
> Fix by filtering impossible matches and giving established tracks priority.

### 1.1 Mahalanobis gating pre-filter
- [x] **Implement** in `_compute_cost_matrix` (`tracker.py`)
- Use Kalman covariance to compute chi-squared distance: $d = (z - Hx)^T S^{-1} (z - Hx)$
- Gate at χ²(0.99, df=2) ≈ 9.21 → set cost to `INF` for entries exceeding the gate
- Innovation covariance uses inflated noise: `S = P[:2,:2] + eye(2) × GATE_NOISE` (not Kalman `R`)
- **Why**: Eliminates "teleport" assignments where a detection is picked up by a far-away track whose Kalman state says it can't possibly be there
- Logs `MAHALANOBIS_GATE` event with chi², gate value, and pixel distance
- [x] **Config**: `TRACKER_MAHALANOBIS_GATE = 9.21`, `TRACKER_MAHALANOBIS_GATE_NOISE = 700.0`

### 1.2 Cascaded matching (established-first)
- [x] **Implement** in `update()` (`tracker.py`)
- Split the single Hungarian stage into two passes:
  - **Pass 1**: Established tracks only (`hits >= TRACKER_ESTABLISHED_FRAMES`) — full cost matrix + Mahalanobis gate. These are real dancers; they get first pick.
  - **Pass 2**: Remaining tentative tracks vs leftover detections — looser gate.
- Matching logic extracted into `_run_assignment_pass()` helper (threshold check, anti-merge, logging)
- Controlled by `TRACKER_CASCADED_MATCHING = True` in config (can disable for A/B testing)
- **Why**: Prevents a freshly-spawned tentative track from stealing a detection that belongs to an established dancer
- [ ] **Test**: Scenario with established dancer + nearby tentative track. Established must win.
- [ ] **Regression**: Run on recorded crossover clips — count ID changes before/after

### 1.3 IoU cost signal
- [ ] **Implement** as 6th cost term in `_compute_cost_matrix`
- Predicted bbox = last bbox translated by Kalman velocity
- IoU between predicted bbox and detection bbox → `iou_cost = 1.0 - iou`
- Blend weight: ~10% in normal mode, ~5% in crowded mode
- **Why**: Two dancers at the same centroid but different bbox extents are distinguished. Nearly free to compute.
- [ ] **Test**: Side-by-side dancers with different heights — IoU should disambiguate
- [ ] **Config**: Add `TRACKER_IOU_WEIGHT = 0.10`, `TRACKER_CLOSE_IOU_WEIGHT = 0.05`

### Phase 1 — Validation checkpoint
- [ ] Run on recorded video with known crossover moments
- [ ] Count ID swaps/steals before vs after Phase 1
- [ ] Check for regressions: false track kills, missed detections, latency impact
- [ ] **Decision**: If steal rate dropped enough, skip to Phase 3 (optical flow). Otherwise continue to Phase 2.

---

## Phase 2 — Temporal Pose Signature (detect and correct steals)

> **Use motion history to catch swaps that slipped past the gate.**
> A true match shows smooth pose evolution; a steal shows an abrupt jump.

### 2.1 Pose trajectory buffer
- [ ] **Implement** in `DancerTrack` (`tracker.py`)
- Add rolling buffer: last 8 normalized skeleton snapshots (`deque(maxlen=8)`)
- On each `update()`, append centroid-normalized keypoints (already computed via `get_normalized_skeleton()`)
- Compute "pose trajectory descriptor" = flattened recent history
- [ ] **Config**: Add `TRACKER_POSE_HISTORY_DEPTH = 8`

### 2.1b Frame history buffer
- [ ] **Implement** in `DancerTrack` (`tracker.py`)
- Add per-track rolling buffer of last N frames of tracking data (`deque(maxlen=N)`):
  - **Tracking data per entry**: bbox `[x,y,w,h]`, centroid, keypoints (raw + normalized), confidence scores, Kalman state snapshot `[x, y, vx, vy]`
  - **Crop thumbnail** (optional, for image-level checks): 64×128 grayscale crop of the track's bbox region, resized to fixed size for cheap comparison. Start WITHOUT crops; add only if skeleton-only signals aren't enough for the swap detector.
- Stored per-frame, updated on every `update()` call (matched frames only — don't store predicted-only frames)
- Memory: ~1 KB/frame tracking data + ~8 KB/frame optional crop = ~60–540 KB total for 6 tracks × 10 frames — negligible
- [ ] **Config**: Add `TRACKER_FRAME_HISTORY_DEPTH = 10` to `config.py`

### 2.1c Derived temporal features (computed from history buffer)
- [ ] **Implement** helper methods on `DancerTrack`:
  - `get_velocity_profile()` → array of per-frame velocity magnitudes over last N frames
  - `get_aspect_ratio_profile()` → array of bbox width/height ratios over last N frames
  - `get_pose_change_rate()` → mean per-joint displacement between consecutive frames (how fast the pose is evolving)
  - `get_appearance_stability()` → if crops stored, mean absolute difference between consecutive crops (low = stable identity, spike = likely steal or occlusion). Skip if crops not enabled.
- Compute cost: sub-0.1ms per track (simple numpy over small arrays)
- **Why**: These derived signals feed into cost matrix (2.2) and swap detector (2.3) for richer matching. Two dancers at the same position with the same instantaneous pose can still have different velocity profiles (one decelerating, one accelerating) and different bbox aspect ratio trajectories.
- [ ] **Test**: Log profiles for known-good tracks — verify they are smooth. Inject a simulated steal — verify spike in `pose_change_rate` and (if crops enabled) `appearance_stability`.

### 2.2 Trajectory similarity in cost matrix
- [ ] **Implement** in `_compute_cost_matrix` for crowded zones
- When `is_crowded` and track has ≥4 frames of history:
  - Compare detection skeleton vs track's last N skeletons
  - Score = weighted mean distance with exponential recency bias
  - Compare detection's implied velocity (from detection position vs track's last known position) against the track's `get_velocity_profile()` — penalize assignments that would require physically implausible acceleration
  - Compare detection bbox aspect ratio against track's `get_aspect_ratio_profile()` — penalize sudden shape changes
  - Replaces or supplements the single-frame `kpt_cost`
- **Why**: Single-frame skeleton can be ambiguous (both dancers in similar pose at one instant). Trajectory over 5–8 frames is much more discriminative. Velocity/acceleration profile adds physics-based plausibility checking.
- [ ] **Test**: Two dancers with similar pose at crossing point — trajectory should still disambiguate

### 2.3 Post-assignment swap detector (2-opt)
- [ ] **Implement** in `update()`, after Hungarian matching
- For each pair of matched (detection, track) assignments where both tracks were in close proximity:
  - Check if swapping the two assignments reduces total cost
  - Also check: did the assignment cause velocity reversal AND skeleton jump simultaneously?
  - Check `get_pose_change_rate()` — if the assigned detection causes a pose change rate 3× above the track's running average, flag as likely steal
  - Check `get_appearance_stability()` (if crops enabled) — if the matched crop diverges from the track's recent crop history, flag as suspicious
  - If swap improves cost → apply swap
- Only runs on pairs within `close_dist` — O(k²) where k = nearby matched pairs (typically 2–4)
- **Why**: Catches the most common swap pattern — two crossing dancers whose IDs flip in one frame. Frame history provides multiple independent confirmation signals beyond just cost.
- [ ] **Test**: Synthetic 2-track crossing scenario — verify swap is detected and corrected
- [ ] **Config**: Add `TRACKER_SWAP_DETECT_ENABLED = True`

### Phase 2 — Validation checkpoint
- [ ] Run on crossover clips — verify swap detector fires correctly
- [ ] Count false positives (unnecessary swaps) — should be zero or near-zero
- [ ] Measure per-frame latency impact (should be <1ms)
- [ ] **Decision**: Proceed to Phase 3

---

## Phase 3 — Optical Flow Bridge (maintain tracks through occlusion)

> **Track pixel motion through detector gaps.** Kalman dead-reckoning decays
> to zero velocity; optical flow follows actual image movement for 3–15 frames.

### 3.1 Sparse optical flow module
- [ ] **Create** `application/src/optical_flow.py`
- Class `FlowTracker`:
  - Stores previous frame (grayscale)
  - Per-track: sparse point set = 5–8 highest-confidence keypoints
  - On each frame: run `cv2.calcOpticalFlowPyrLK` for all active point sets
  - Returns per-track displacement vector (median of valid flow vectors)
  - Error handling: discard points with high flow error or out-of-bounds
  - **Multi-frame flow validation**: keep flow vectors for last 3 frames. If a flow vector at frame T disagrees with the trend from T-1, T-2 by >2× median deviation, discard it as noise. This hardens flow through brief lighting changes and motion blur.
- Lucas-Kanade parameters tuned for dance motion (window 21×21, 3 pyramid levels)
- **Why**: LK is fast (~1–3ms for 6 dancers × 8 points), runs on CPU, doesn't need GPU
- [ ] **Test**: Verify flow tracks keypoints accurately on recorded video
- [ ] **Config**: Add `TRACKER_OPTICAL_FLOW_ENABLED = True`, `TRACKER_FLOW_MAX_POINTS = 8`

### 3.2 Flow-assisted prediction for missing tracks
- [ ] **Integrate** into `DancerTrack.predict()` and `DancerTracker.update()`
- When a track is unmatched for 1+ frames AND flow displacement is available:
  - Feed flow displacement as a Kalman measurement update (with inflated noise `R *= 4.0`)
  - This keeps the Kalman state anchored to real image evidence
- When flow is NOT available: fall back to current Kalman-only prediction (no regression)
- [ ] **Test**: Occlude a dancer for 5–10 frames — verify predicted position follows actual motion via flow

### 3.3 Flow-enhanced cost matrix
- [ ] **Integrate** flow-predicted position into `_compute_cost_matrix`
- For tracks with `time_since_update > 0` and valid flow, use flow-adjusted position instead of (or blended with) Kalman prediction for `dist_pred`
- **Why**: Flow position is much more accurate than Kalman extrapolation after 3+ frames of occlusion
- [ ] **Test**: Dancer emerges from behind another — verify re-association uses flow position

### Phase 3 — Validation checkpoint
- [ ] Run on occlusion-heavy clips
- [ ] Measure track survival rate through 5-frame, 10-frame, 15-frame occlusions
- [ ] Count new IDs created during occlusion events — should decrease significantly
- [ ] Verify no latency regression (flow should add <3ms total)
- [ ] **Decision**: If occlusion survival is good, Phase 4 refinements. If merge is still an issue, consider Phase 5.

---

## Phase 4 — Track Lifecycle Refinements (reduce ghost creation)

> **Tighten the rules around birth, death, and resurrection
> to minimize spurious new IDs.**

### 4.1 Aggressive dormant matching for recent deaths
- [ ] **Implement** in `update()` new-track creation path (`tracker.py`)
- Before creating ANY new track (even when `min_dist > creation_gate`):
  - Check dormant pool for tracks that died < 10 frames ago
  - Use relaxed position gate (2× normal) + pose trajectory similarity
  - If match found → resurrect instead of creating new ID
- **Why**: Directly addresses "stolen dancer gets a new ID" — the old ID is still in dormant pool
- [ ] **Test**: Steal scenario → verify victim ID is resurrected, not replaced

### 4.2 Anti-steal cooldown
- [ ] **Implement** "contested" track state in `DancerTrack`
- When the swap detector (2.3) identifies a steal victim:
  - Freeze victim's aging for 5 frames
  - Give victim priority in next frame's cascaded matching (Pass 0.5)
  - Penalize the usurper's assignment cost by 1.5× for those frames
- **Why**: Gives the stolen track a chance to reclaim its correct detection before dying
- [ ] **Config**: Add `TRACKER_STEAL_COOLDOWN_FRAMES = 5`

### 4.3 Pose trajectory in dormant resurrection
- [ ] **Enhance** `_try_resurrect()` (`tracker.py`)
- Store pose trajectory buffer (from 2.1) in `DormantSnapshot`
- Score resurrection candidates using trajectory similarity in addition to current position + height + keypoints
- **Why**: Improves re-ID accuracy when position is uncertain (long dormancy, complex motion)
- [ ] **Test**: Dancer hidden for 3 seconds, re-emerges with similar pose style → correct ID restored

### Phase 4 — Validation checkpoint
- [ ] Run full show recording end-to-end
- [ ] Metric: `total_ids_created / actual_persons` — target ≤ 1.2
- [ ] Count ghost IDs (IDs that appear for <2 seconds then vanish) — should be near zero
- [ ] **Decision**: If metrics are satisfactory, ship. If crossover merge is still an issue, proceed to Phase 5.

---

## Phase 5 — Occupancy-Aware Occlusion (advanced, if needed)

> **Explicit spatial reasoning about who is where.**
> Only pursue if Phases 1–4 leave residual issues in complex multi-body crossovers.

### 5.1 Occupancy grid
- [ ] **Implement** lightweight 2D grid (one cell per ~50px) in `DancerTracker`
- Mark cells occupied by each track's predicted bbox
- When two tracks' cells overlap → declare "occlusion event"
- [ ] **Config**: Add `TRACKER_OCCUPANCY_GRID_SIZE = 50`

### 5.2 Occlusion event handling
- [ ] On occlusion event:
  - Tag both tracks as participants
  - Disable new-track creation in overlapping cells
  - Boost optical flow tracking priority for involved tracks
  - After separation: use pose trajectory + bbox size + flow direction to re-assign IDs
- [ ] **Test**: 3-dancer pileup → verify no ghost IDs created, all emerge with correct IDs

### Phase 5 — Validation checkpoint
- [ ] Stress test with worst-case choreography (all dancers crossing simultaneously)
- [ ] Final metric pass: ID stability, ghost rate, latency

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-03 | Skip ReID embeddings | Similar costumes → appearance not discriminative |
| 2026-03-03 | Skip JPDA/MHT | Overkill for ≤6 dancers; cascaded Hungarian + 2-opt sufficient |
| 2026-03-03 | Skip IMM | Current CA model + velocity clamping handles dance motion well |
| 2026-03-03 | Skip segmentation | Anti-merge bbox check + skeleton matching adequate; revisit if needed |
| 2026-03-03 | Prioritize optical flow over ReID | Bridges occlusion gaps directly; more impactful than appearance |
| 2026-03-03 | Prioritize pose trajectory over single-frame skeleton | Temporal signature more discriminative with similar costumes |
| 2026-03-03 | Add per-track frame history buffer (tracking data + optional crops) | Low cost (~540 KB), enables velocity/aspect-ratio/pose-change-rate temporal signals for richer matching and steal detection |
| 2026-03-03 | Start without crop thumbnails, add if needed | Skeleton + tracking data signals likely sufficient; crops add memory and complexity |
| 2026-03-03 | Phase 1.1 Mahalanobis gate implemented | Chi²(df=2, 99%) = 9.21 gate in `_compute_cost_matrix`; blocks teleport assignments |
| 2026-03-03 | Phase 1.2 Cascaded matching implemented | `_run_assignment_pass()` helper; established-first + tentative-second; `TRACKER_CASCADED_MATCHING` toggle |
| 2026-03-03 | Root-caused edge-entry ID theft | Hypothesis 1 confirmed: Hungarian global cost minimization caused 294px teleport at frame 237 |
| 2026-03-03 | Root-caused occlusion zone ID loss | Mahalanobis gate effective radius was 5.9px (13× too tight); cascaded into FORCE_UPDATE misassignment → ghost creation → track death |
| 2026-03-03 | Separate gate noise from smoothing noise | `TRACKER_MAHALANOBIS_GATE_NOISE=700` gives ~80px effective radius; Kalman `R=2` kept for smoothing quality |
| 2026-03-03 | Relaxed MERGE_SIZE_RATIO 1.3 → 2.0 | 1.3 was blocking legitimate detections during normal pose variation near occlusions |
| 2026-03-03 | Fixed ID counter waste in resurrection | `_try_resurrect()` now reuses dormant ID in constructor instead of wasting a fresh one |
| 2026-03-03 | Root-caused cascade occlusion swap | Established-first priority gives merged detection to exiting track; tentative track (the one staying) starves and dies → silent ID swap |
| 2026-03-03 | Post-cascade occlusion swap (Phase 1c) | `TRACKER_CASCADE_OCCLUSION_SWAP=True`; deferred updates + speed-based swap criterion; `_check_occlusion_cascade_swaps()` |
| 2026-03-03 | Root-caused established-established merge swap | Kalman CA model drifts occluded track rightward during merge; re-emergence matches wrong body. Both established → no cascade priority to exploit |
| 2026-03-03 | Post-merge direction swap (Phase 1d) | `TRACKER_MERGE_DIRECTION_SWAP=True`; 20-frame `_vx_history` buffer; dominant direction from older half; direction-reversed pairs swapped before Kalman update |
| 2026-03-03 | Cascade suppression window (Phase 1c+) | CASCADE_OCCLUSION_SWAP was single-frame, undone by Pass 1 next frame. Added `TRACKER_CASCADE_SUPPRESSION_FRAMES=5` — suppressed track excluded from Pass 1 for N frames; participates in Pass 2 instead. Cleaned up `[MERGE_DBG]` prints |
| 2026-03-03 | Velocity clamp on swap beneficiary (Phase 1c++) | Suppression alone insufficient — merged detection offset interpreted as velocity → Kalman drift → tentative track Mahalanobis-gated. Clamp `kf.x[2:]=0` on swap beneficiary prevents runaway drift |
| 2026-03-03 | Velocity clamp on merge direction swap (Phase 1d+) | MERGE_DIRECTION_SWAP detection jump → velocity spike → cross-match oscillation (swap↔cross-match until `_vx_history` corrupted). Clamp `kf.x[2:]=0` on both swapped tracks eliminates feedback loop |
| 2026-03-03 | Move velocity clamps to post-update (Phase 1c+++/1d++) | Pre-update clamps were no-ops: Kalman gain recomputes velocity from innovation, undoing the zeroing. Moved to `_post_update_clamp_indices` set, applied after all deferred `.update()` calls. Fixes both F300 and F370 swap regressions |

---

## Files to modify

| File | Changes |
|------|---------|
| [tracker.py](../application/src/tracker.py) | Phase 0: TrackingLogger + structured events. Phases 1–4: gating, cascaded matching, cost signals, swap detector, lifecycle |
| [config.py](../application/src/config.py) | New constants for all phases (including logging flags) |
| [app.py](../application/src/app.py) | Phase 0: frame number overlay on preview, cumulative frame counter |
| [optical_flow.py](../application/src/optical_flow.py) | Phase 3: new module |
| [pipeline.py](../application/src/pipeline.py) | Phase 3: wire optical flow into frame processing |
| [tracking_logger.py](../application/src/tracking_logger.py) | Phase 0: new module (optional — can be inlined in tracker.py) |

## Verification tools

- `tracking_events.jsonl` — structured frame-stamped event log (Phase 0), auto-written during playback
- Frame number overlay — visible on preview, readable when paused (Phase 0)
- `TRACKER_DEBUG = True` — verbose console log (legacy, kept as fallback)
- Recorded video slots — replay known-bad scenarios (slot 6 = primary test clip)
- Metric: `total_ids_created / actual_persons` ratio per clip
- Metric: ghost ID count (IDs alive < 2 seconds)
- Metric: ID swap count at known crossover frames
- **Workflow**: Pause on bad frame → read frame number → report → examine JSONL log around that frame
