# WallDance — OSC output contract

**Date:** 2026-06-22 · **Status:** **CONFIRMED / live** — the canonical wire-level contract for
WallDance's OSC output (box-clamp + L-driven output smoother shipped on `main`). Any change to
what `/walldance/*` emits is an explicit, operator-confirmed change to this document.

Documents (A) what `/walldance/*` emits **today, as shipped** (`core/osc_output.py`), and
(B) the **output-domain controls** — box-clamp and the L-driven output smoother, and (D) the
**identity-slot layer** (stable ids 1..N, coasting, `/walldance/dancer/state`; 2026-10). **Locked
defaults: box-clamp ON, smoothing L = 1.** The output is a **single stream** selected by `L`:
`L = 1` is causal/live; `L > 1` is the fixed-lag RTS-smoothed stream, released `L` frames late, on
the **same** `/walldance/dancer/*` namespace. (The earlier "dual tap" `/walldance/dancer_lagged/*`
and the opt-in case-2 flying-ghost suppression were **removed, 2026-06** — one stream, no
suppression.)

Companion: [ROADMAP.md](ROADMAP.md) §3 (Track X) · design history:
[archives/TRACK_X_SMOOTHER.md](archives/TRACK_X_SMOOTHER.md).

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
  last value** for as long as the id is still emitted — which on a plain miss is short: the id
  drops out of the whole stream after ≤ 8 un-bridged misses, or after 4 skeleton-less frames
  if the track is also slow (see §A.4 "When an id disappears").
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

**When an id disappears** (audit 2026-10 `01-continuity.md` §1.3, DOC-1). A track is emitted on
a frame only while it passes *all* of the report gates of `_collect_confirmed_tracks`:
- **warm-up:** its integral is ≥ 15 (+1 per match, +0.4 per motion-bridge frame, −0.8 per missed
  frame from the 2nd miss, capped at 20) — or the intermittent path when the per-scene
  `tracker_intermittent_confirm` is on. A confirmed track at the cap therefore drops after
  **8 un-bridged misses**; a new id needs ~14 consecutive feeds (~0.75 s);
- **frozen-ghost gate:** not (no real skeleton for more than `tracker_ghost_skeleton_age`
  frames — default 3 — **and** KF speed < 0.03 × person height per frame);
- **slow-path separation:** an intermittent-only track within 0.7 h of a kept track is hidden;
- **cap:** at most `max_persons`, most-hit first.

On the **first frame** an id fails a gate it is simply **absent** from `/walldance/count` and from
every `/walldance/dancer/*` message. There is no coasting state and no "lost" message, so a
consumer cannot tell a 1-frame flicker from an exit. The **same** id comes back if its internal
track re-passes the gates, or is resurrected from the dormant pool (an id that had already been
emitted is confirmed again on the resurrect frame — CONT-1, 2026-10). Otherwise the dancer
reappears under a **new, higher** id after its warm-up. With `L > 1` the RTS smoother restarts on
any reporting gap (`output_smoother.py`), so it does not bridge these gaps either.
The FRAME_SUMMARY log records, per frame, the emitted id set and each track's hide reason
(`warmup` / `frozen` / `slow_dup` / `cap`) — CONT-10.

> **Since 2026-10 (CONT-6) this section describes the tracker's *internal* ids.** With the
> identity-slot layer ON (the default), the ids on the wire are **slot ids `1..max_dancers`**,
> stable for the whole show, and a lost dancer **coasts** instead of vanishing — see **§D**.
> Turning "Stable IDs" off restores the behaviour above.

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

**Acausal fixed-lag / RTS smoother — shipped.** A genuine look-ahead buffer of `L` frames feeds
an RTS (acausal) smoother that **becomes** the `/walldance/dancer/*` stream when `L > 1` (§B.3).
At **`L > 1`** the centroid + box + keypoints + velocity are all RTS-smoothed and the stream is
released `L` frames late; at **`L = 1`** the causal box-size EMA above is unchanged (back-compat).
The slider's operator-facing meaning ("more L = smoother + more latency") is stable.
**Retroactive bridge correction** falls out of the RTS pass automatically (a bridged gap `≤ L`
re-anchored inside the window is corrected in hindsight). The released id set **equals the
reported id set** — there is no flying-ghost suppression (that opt-in feature was removed,
2026-06; the optional steady-rate resample X-4 remains unbuilt).

### B.3 One stream, selected by `L` (causal at L=1, lagged at L>1)

There is a **single** output stream — the `/walldance/dancer/*` messages of §A.3, plus
box-clamp (§B.1). Its source is selected by `L` alone (no second namespace, no opt-in toggle):

- **`L = 1` — causal / live (default).** Zero look-ahead. Plus the causal box-size EMA (§B.2).
  For latency-sensitive consumers.
- **`L > 1` — fixed-lag / RTS-smoothed.** The *same* `/walldance/dancer/*` messages now carry
  the look-ahead-smoothed report, released `L` frames late. The centroid is an **RTS (acausal)
  smoothed** estimate over the raw KF centroid (not the causal EMA — no cascade); the box,
  keypoints, and velocity are smoothed/derived the same way. **Retroactive bridge correction**
  falls out of the RTS pass automatically (a bridged gap `≤ L` re-anchored inside the window is
  corrected in hindsight). The released `track_id` set **equals the reported (confirmed) id
  set** — every confirmed track is released `L` frames late (no flying-ghost suppression). The
  smoother is **output-only**: it never touches the detector, the tracker, or `DancerTrack`
  state, so replay goldens stay byte-identical at any `L`.

- **Latency.** The active output latency is published on **`/walldance/meta/latency_ms` `[ms]`**
  (= `L / fps · 1000`, re-emitted when `L` or fps changes; `0` at `L = 1`) so consumers know how
  far behind real time the stream runs.
- **Per-message coherence (L > 1).** Within one output frame the keypoints are rigidly translated
  by the same centroid correction (so the skeleton stays aligned with the corrected centroid/box,
  incl. through corrected gaps) and `velocity` is the RTS-smoothed velocity — `centroid`, `bbox`,
  `keypoints`, `velocity` are mutually consistent. See `TRACK_X_SMOOTHER.md`.
- **Preview.** The operator preview always shows the causal report (live), regardless of `L`, so
  the on-screen view never lags even when the OSC stream is fixed-lag.

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

---

## D. Identity slots + `/walldance/dancer/state` (2026-10, CONT-6) — additive

**Source:** `application/src/core/identity_slots.py` (pure, unit-tested), wired in
`FrameProcessor._post_yolo_chain` after `finalize`, before `OSCSender.send_frame`. Output-only:
the tracker and the replay summaries are unchanged (the returned / preview tracks are
byte-identical with slots on or off).

### D.1 What changes on the wire (slots ON, the default)
- **Ids are slot ids `1..max_dancers`**, stable for the whole show. Every `/walldance/dancer/*`
  message and `/walldance/count` carries them; message **shapes, addresses, types and cadence are
  unchanged** (§A.3). `/count` lists the emitted slot ids (never more than `max_dancers`).
- **A lost dancer coasts**: its id keeps being sent, held at its last position (constant
  velocity decaying in ~0.25 s), for up to `coast_s` seconds (default **2.0 s**), then it is
  absent (state `lost`). A dancer the tracker re-finds under a new internal id gets the **same
  slot id back** (gated around the held position; the gate grows with the coast time).
- **Smart hold** (2026-10-06, `smart_hold`, default ON): the full `coast_s` is *earned*. A slot gets it
  only after >= 1 s of live tracking backed by a fresh YOLO skeleton **and** >= 0.25 h of travel since it
  took its id; before that (and for a static slot) the hold is 1.5 s, ramping linearly to `coast_s`.
  A dancer lost within 0.25 h of the ROI border (the frame when the ROI is off) while moving outward
  faster than 0.5 h/s is released after 0.3 s. Bit-identical to plain hold at `coast_s` = 2 s
  (6 goldens); at 5 s it cuts the frames with a held point beyond the dancers by 24 % on the ghost-pressure
  take. The hold length is decided once, when the slot starts coasting.
- **A new dancer** (an established tracker track: confirmed, a few frames reported, a real
  skeleton within 3 s, outside the exclusion mask) takes a free slot, lowest / nearest first.
  Extra tracks beyond `max_dancers` are not sent. A track sitting on a body another slot already
  sends never takes a slot (no two ids on one dancer).
- **Static figures** (2026-10-06, `static_ghost_guard`, default ON): a person-like shape YOLO keeps
  confirming but that never moves (p90 spread < 0.06 h over 5 s: a coat by a door, a poster, a stain)
  cannot take back an id it gave up and cannot re-acquire one; when every slot emits and a moving,
  skeleton-backed dancer is left out, a slot that has not moved for 5 s gives its id to that dancer
  (event `yield`).  Replay-measured: neutral on 12 takes, a ghost-starved dancer 0.004 -> 0.80 of the
  frames with a point on it.  `static_release_s` (default 0 = off) would also drop a static slot with no
  newcomer -- off because at 8 s it dropped a still floor dancer.
- **Centroid**: One-Euro filter per slot (adaptive: calm at rest, quick on fast moves), operator
  knob **Stability** 0..3 (0..1 unchanged; above 1 both filter parameters shrink x0.25 per unit:
  heavier smoothing, more lag; replay-measured lag on fast moves ~70 ms at 1, ~105-170 ms at 2,
  ~160-400 ms at 3; jitter at rest 2-5x lower at 2 than at 0.5).  Its input (`slot_filter_input`) is the tracker's EMA centroid
  (`smoothed`, default); `raw_skeleton` feeds the bound track's raw Kalman centroid on frames with a
  fresh skeleton (lag on fast moves -30..-60 ms, jitter at rest x1.3-1.5; binding unchanged), `raw` on
  every frame (fragile on textured walls). `/bbox` is the slot's smoothed box centred on the centroid; `/keypoints`
  are the bound track's skeleton translated onto the centroid (last skeleton while coasting);
  `/velocity` is the filtered slot velocity (px/frame, normalized as §A.3).
- **`L > 1`**: the RTS fixed-lag smoother now runs on the slot ids (no restart on tracker id
  churn).

### D.2 `/walldance/dancer/state` `[id, state, age_s]` — opt-in (`osc_send_state`, default OFF)
- One message per slot **every frame, lost slots included**: `id` int32, `state` string
  `live` (bound track updated this frame) / `belt` (held by the IR waist belt) / `weak` (a coasting
  slot re-found on weak evidence near its prediction; only with the `weak_enabled` slot option, off by
  default) / `coasting` (holding, no measurement) / `lost` (absent from `/count`), `age_s` float32 =
  seconds in that state. Causal: not delayed at `L > 1`.
- Additive: consumers that ignore it are unaffected. Shared with the marker stream's proposed
  `/dancer/source` (audit 02 §3) — one message, extended later if needed.

### D.3 Defaults and how they were set (replays, dev37 PyTorch path)
| knob (config key) | default | why |
|---|---|---|
| Stable IDs (`identity_slots_enabled`) | ON | TD drops its video when an id vanishes |
| Max dancers (`max_dancers`) | 2 | this week's show: 2 dancers on the wall (a cap, not a constant) |
| Stability (`stability`) | 0.5 | jitter at rest about halved vs the legacy EMA centroid for +15..25 ms lag on fast moves; 0 ~ legacy lag, 1 = calm (+60..80 ms), up to 3 for very heavy smoothing (PLAN_25M §C.11) |
| Hold (`coast_s`) | 2.0 s | 355 of the 356 tracker losses observed over all replays + the 2026-10-05 field takes re-acquire within 2.0 s (99 % within 1.5 s, longest 2.37 s); settable to 10 s |
| Smart hold (`smart_hold`) | ON | earned hold, fast release at border exits (above); neutral at 2 s |
| IR belt (`use_ir_belt`) | ON | no-op unless `core/belt_detector.py` is importable; plain coasting stays the base |
| Send state (`osc_send_state`) | OFF | opt-in |
| Ignore static figures (`static_ghost_guard`) | ON | PLAN_25M 2026-10-06 offline gate (`tmp_analysis/plan25m/slot_eval.py`) |
| Static release (`static_release_s`) | 0 (off) | dropped a still floor dancer at 8 s |
| Filter input (`slot_filter_input`) | `smoothed` | `raw_skeleton` to be judged by eye on TD (D18) |

Operator knobs live in phase **6 Live → Dancer IDs** and are remote-settable
(`SetIdentitySlots`, `SetMaxDancers`, `SetStability`, `SetCoastSeconds`, `ToggleIrBelt`,
`ToggleOscState`, `SetStaticGhostGuard`, `SetStaticRelease`, `SetSlotFilterInput`, `SetSmartHold`; policy `control`).
The preview draws each emitted slot as a **ball** at exactly the position `/centroid` sends
(white fill in every state; only the border takes the state colour). The FRAME_SUMMARY log carries `slots` (per slot: id,
state, bound tracker id, age) and `emitted_slots`; `SLOT_EVENT` lines record binds and losses.
