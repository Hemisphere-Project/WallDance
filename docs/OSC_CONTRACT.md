# WallDance — OSC output contract

**Date:** 2026-06-15 · **Status:** DRAFT for operator confirmation (OPERATOR_V2 batch-2,
item 1). This is the prerequisite for the Track-X output controls. **Nothing that OSC emits
changes until this contract is confirmed** (OPERATOR_V2 §8 ask-first trigger: "the OSC message
contract").

Documents (A) what `/walldance/*` emits **today, as shipped** (`core/osc_output.py`), and
(B) the **planned batch-2 additions** — box-clamp, the causal output-smoothing model, and the
causal-vs-lagged dual tap. **Locked defaults (operator, 2026-06-15): box-clamp ON, smoothing
L = 1.** The deep fixed-lag / RTS smoother (L > 1) is **deferred** (🔴 joint design with the
engine agent) and only its *namespace* is reserved here so the contract is forward-stable.

Companion: [OPERATOR_V2.md](OPERATOR_V2.md) Track X (§"Track X — Output / OSC layer") and §8.

---

## A. Current contract (as shipped)

**Source:** `application/src/core/osc_output.py` (`OSCSender`). Emitted from the pipeline output
boundary `FrameProcessor` → `OSCSender.send_frame(scaled_tracks, original_w, original_h)`
(`core/pipeline.py:904-905`), gated on `self.osc and self.settings.osc_enabled`.

### A.1 Transport & addressing
| Property | Value | Source |
|----------|-------|--------|
| Protocol | OSC over **UDP** (`pythonosc.udp_client.SimpleUDPClient`) | `osc_output.py:6,24` |
| Target IP | `OSC_IP` = `127.0.0.1` (configurable) | `config.py:378` |
| Target port | `OSC_PORT` = `9000` (configurable) | `config.py:379` |
| Enabled | `OSC_ENABLED` = `True`; live gate `settings.osc_enabled` | `config.py:377`, `pipeline.py:904` |
| Bundling | **None today** — one UDP datagram per `send_message`. (`OscBundleBuilder` is imported but unused.) | `osc_output.py:7,62-116` |
| Cadence | One full message set **per processed frame** (YOLO/tracker cadence, ~15–20 fps). Not resampled. | `pipeline.py:891-905` |

### A.2 Coordinate system & normalization
- All spatial values are **normalized to [0, 1]** against the **original camera frame**
  (`original_w` × `original_h`), via `norm_x(v)=v/frame_width`, `norm_y(v)=v/frame_height`
  (`osc_output.py:46-50`).
- **Origin = top-left**, x → right, y → down (image convention).
- ⚠ **Aspect is not preserved in normalized space.** `x`-like values divide by width, `y`-like
  by height. For a non-square frame, a pixel-square box (`w == h` px) yields `w_norm ≠ h_norm`.
  Consumers that need pixel aspect must multiply back by the frame dimensions (publish them
  out-of-band, or see §B.4).
- OSC types: `id` → **int32** (`track_id`); all coordinates/velocities/confidences → **float32**.

### A.3 Messages

Each per-dancer message **prepends the integer `id`** to its argument list.

#### `/walldance/dancer/centroid` `[id, x, y]`
- **EMA-smoothed centroid**, jitter-free (for generative-video consumers).
  `x,y = norm(track.smoothed_centroid)` when present, else falls back to bbox center
  (`osc_output.py:52-63`).
- `smoothed_centroid` is the keypoint-weighted centroid passed through an EMA with
  `alpha = CENTROID_OUTPUT_SMOOTHING = 0.5` (`config.py:278`). Updated on YOLO match
  (`tracker.py:485-487`), motion-bridge (`tracker.py:2953-2955`), and dormant restore
  (`tracker.py:576-578`). **On a plain miss (predict-only) it is *not* updated → it holds its
  last value** until the track re-acquires or bridges.
- Note: this is the **keypoint centroid**, not the bbox center; the two differ when the pose
  is off-center in the box.

#### `/walldance/dancer/bbox` `[id, x, y, w, h]`
- **Raw** bounding box, normalized: `x = norm_x(bbox[0])` (left), `y = norm_y(bbox[1])` (top),
  `w = norm_x(bbox[2])` (width / frame_width), `h = norm_y(bbox[3])` (height / frame_height)
  (`osc_output.py:65-72`).
- Format is **(x, y, w, h)** top-left + extent, **not** (x1, y1, x2, y2).
- ⚠ **This is today's flicker source (case-1).** During detection gaps the reported box is
  whatever extent currently sits in `DancerTrack.bbox` — which on cold-blob-fed frames can be a
  fat, frame-to-frame-varying motion blob. The EMA-smoothed `centroid` is stable but the **box
  size jumps**. Batch-2's box-clamp (§B.1) fixes this.

#### `/walldance/dancer/velocity` `[id, vx, vy]`
- Per-frame velocity, normalized (`vx = norm_x`, `vy = norm_y`). Clamped to ±1e6 pre-normalize;
  non-finite → `0.0` (`osc_output.py:74-82`). Source: Kalman state velocity (`track.velocity`).

#### `/walldance/dancer/keypoints` `[id, x0, y0, c0, x1, y1, c1, …, x16, y16, c16]`
- All **17 COCO keypoints** as a flat list: normalized `x`, normalized `y`, **raw confidence**
  (0–1, not normalized) per keypoint (`osc_output.py:84-91`). 52 args total (`id` + 17×3).

#### `/walldance/count` `[count, id0, id1, …]`
- Active confirmed-dancer count followed by the active track IDs, in report order
  (`osc_output.py:93-98`, sent first each frame from `send_frame`, `osc_output.py:104-106`).

#### `/walldance/clear` `[1]`
- Reset signal (e.g. engine stop / scene reset). Sent explicitly via `send_clear()`
  (`osc_output.py:111-116`); not part of the per-frame stream.

### A.4 What "a dancer" is
`scaled_tracks` are the tracker's **confirmed** tracks only
(`DancerTracker._collect_confirmed_tracks`, `tracker.py:3126`) — warmup-confirmed, frozen-ghost-
gated, and `MAX_PERSONS`-capped — mapped to original-frame coords as `ScaledTrack`
(`pipeline.py:910` CPU identity / `pipeline.py:1408` GPU letterbox-unscale). `track_id` is a
monotonic counter (`tracker.py:155,167`) — **ids grow unbounded** across a session and are not
reused; consumers must not assume a small/bounded id range.

---

## B. Planned batch-2 additions (gated on this confirmation)

Two **output-domain** controls (OPERATOR_V2 decision 6), strictly separate from the two
*detection* dials. Both live entirely at the output boundary — **they touch neither the detector
nor the tracker internals** (the case-1 lesson).

### B.1 Box-clamp toggle — default **ON**

**What it changes:** only `/walldance/dancer/bbox`. Nothing else.

**Behavior.** When ON and a confirmed track is **not being fed by a fresh YOLO skeleton this
frame** (i.e. it is motion-bridged / cold-blob-sustained / coasting through a miss), the reported
box is the **last-known YOLO size centered on the smoothed centroid**:

```
bbox_out = (cx - W/2,  cy - H/2,  W,  H)
  where (cx, cy) = track.smoothed_centroid   (same point as /centroid)
        (W, H)   = the w/h of the most recent real-YOLO-skeleton detection
```

When OFF → today's raw blob extent (§A.3). On a **fresh-YOLO frame** (real skeleton this frame)
→ the raw YOLO box, **unchanged**, in both modes.

**"Fresh YOLO this frame"** is determined by the tracker's existing
`_frames_since_skeleton` counter (`tracker.py:205-206,362,440-441`): `== 0` ⟺ a real skeleton
(≥1 keypoint over `KEYPOINT_CONFIDENCE`) updated the track this frame. This is broader than the
narrow `is_bridged` flag — it also covers cold-blob and coast frames, which flicker too — so it
fixes the case-1 size flicker **outright**, which is the stated goal. *(Gate choice flagged for
operator confirmation at the batch-2 checkpoint; `is_bridged`-only is the narrower alternative.)*

**Implementation invariant (the case-1 trap).** A new per-track field records the last real-YOLO
`w/h`; the clamp is applied **only at the `ScaledTrack` / OSC / preview boundary** (the
`finalize` functions). **`DancerTrack.bbox` is never mutated** — it sets the bridge gate
(`tracker.py:2891-2899`) and `MAX_VELOCITY` (`tracker.py:392-393`), so shrinking it regressed
drops (case-1). Internal tracking/gating stays **byte-identical** with the toggle on or off →
replay goldens unaffected.

**Effects for consumers:**
- `/bbox` **size stops flickering** through detection gaps (stable dancer-sized rectangle).
- `/bbox` **center aligns with `/centroid`** during gaps (both at `smoothed_centroid`).
- No change to `/centroid`, `/velocity`, `/keypoints`, `/count`.
- Minor, inherent: at a gap→skeleton transition the box may step from the clamped size to the
  fresh YOLO size; the box-size EMA (§B.2) softens this.

### B.2 Output-smoothing depth `L` — default **L = 1** (causal). IMPLEMENTED (batch-2).

A consumer-facing **"smoothness vs latency"** slider (phase ⑥; `output_smoothing_l`, range
1–6, default 1). **Output-only; causal.** Smooths only the reported box **size** around its own
center — position/centroid are already EMA-smoothed via `CENTROID_OUTPUT_SMOOTHING`. No frame
buffering, no look-ahead. Implemented at `FrameProcessor._smooth_output_box_sizes`
(`core/pipeline.py`), state keyed by `track_id`, pruned to the reported set each frame.

**What ships in batch-2 (causal EMA, all `L`):**
`size ← α·size_new + (1−α)·size_prev`, with `α = BOX_SIZE_OUTPUT_SMOOTHING / L`
(`BOX_SIZE_OUTPUT_SMOOTHING = 0.5`, `α` floored at 0.05). So **`L` is a smoothness depth**:
`L = 1 → α = 0.5` (light de-jitter); larger `L → smaller α →` smoother box, more lag.

**Latency model (causal group delay).** A causal EMA adds **no buffering / look-ahead delay**, only
a **group delay ≈ `(1−α)/α` frames** on the box *size*:

| L | α | group delay (frames) | ≈ ms @ 20 fps |
|---|------|----------------------|----------------|
| 1 | 0.50 | ~1.0 | ~50 |
| 2 | 0.25 | ~3.0 | ~150 |
| 3 | 0.167| ~5.0 | ~250 |
| 6 | 0.083| ~11.0 | ~550 |

The dancer **position** stream is unaffected by `L` (only the box size lags). `L = 1` is the
minimal-latency default.

**Acausal fixed-lag / RTS smoother — core shipped (batch-3, Track X X-2).** A genuine look-ahead
buffer of `L` frames feeds an RTS (acausal) smoother on the lagged tap (§B.3). For **`L > 1` the
causal box reverts to raw** (no causal box EMA) and the smoothed box + RTS centroid live on the
lagged tap — **no double-smoothing**; **`L = 1` keeps the causal box-size EMA above unchanged**
(back-compat). The slider's operator-facing meaning ("more L = smoother + more latency") is
forward-stable across the upgrade; only the internal mechanism changes. **Retroactive bridge
correction** falls out of the RTS pass automatically (a bridged gap `≤ L` re-anchored inside the
window is corrected in hindsight). **Still deferred to X-3** (joint w/ the engine agent):
**case-2 flying-ghost *suppression*** (the lagged id set currently equals the causal id set) and
the optional steady-rate resample (X-4).

### B.3 Causal vs lagged — the dual tap

- **Causal tap (zero look-ahead) — the only tap in batch-2.** All `/walldance/dancer/*` messages
  above, plus box-clamp (§B.1) and the causal box-size EMA (§B.2). For latency-sensitive
  consumers.
- **Lagged / smoothed tap (L > 1) — IMPLEMENTED (batch-3, Track X X-2).** A *second*,
  look-ahead-smoothed stream under the **`/walldance/dancer_lagged/*`** namespace (same message
  shapes as §A.3, plus `/walldance/dancer_lagged/count`), released `L` frames late. The centroid
  is an **RTS (acausal) smoothed** estimate over the raw KF centroid (not the causal EMA — no
  cascade); the box is smoothed the same way. The active output latency is published on
  **`/walldance/meta/latency_ms` `[ms]`** (= `L / fps · 1000`, re-emitted when `L` or fps changes;
  `0` when the tap is inactive) so TouchDesigner can time-align the two taps. **Opt-in** via
  `output_lagged_enabled` (default **False** — a second full stream doubles OSC traffic per
  dancer); engages only at `L > 1`. Lagged `track_id`s equal the causal id set (case-2
  flying-ghost *suppression* is X-3, deferred). See `TRACK_X_SMOOTHER.md`.

### B.4 Not changing in batch-2 (explicit)
- Message **shapes, addresses, types, normalization, and cadence** of every §A message are
  **unchanged**. Box-clamp only swaps the *values* inside `/walldance/dancer/bbox` during gaps.
- No bundling change, no new emitted addresses, no frame-rate resampling (output-side
  interpolation to a fixed rate is part of the deferred L > 1 work).
- Frame dimensions are still not published on the wire; aspect caveat (§A.2) stands until the
  lagged tap / `meta` namespace ships.

---

## C. Verification (batch-2 item 2 DoD)
- **Goldens byte-identical** with box-clamp on/off — proves internal gating untouched
  (output-only). Run the golden replay suite.
- **Reported bbox visibly stable** on a bridged clip (`replay.py --trt`, `hangar-aerial`):
  `/bbox` size no longer flickers through the aerial detection gaps.
