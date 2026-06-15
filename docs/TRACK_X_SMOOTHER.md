# Track X — fixed-lag / RTS output smoother (joint design spec)

**Date:** 2026-06-15 · **Status:** 🔴 DESIGN, not built. Joint design between the
**operator-surface lane** (owns the controls + OSC integration + this spec) and the
**engine agent** (kinematics/identity semantics). Code only after both sign off
(OPERATOR_V2 §8 ask-first: the fixed-lag/RTS smoother core is "design before code").

**Builds on what shipped (batch-2):** box-clamp (`OSC_CONTRACT.md` §B.1) +
causal box-size EMA (`OSC_CONTRACT.md` §B.2). This spec is the **L > 1 / acausal**
upgrade those two deferred. Read `OSC_CONTRACT.md` §B (esp. B.2/B.3) and
OPERATOR_V2 "Track X" first.

---

## 0. One-paragraph summary
Add an **output-boundary** stage that buffers the last `L` reported frames per track
and **releases each frame `L` frames late**, using those `L` "future" frames to (1)
**de-jitter** the trajectory with a fixed-interval (RTS) smoother, (2) **retroactively
correct** a motion-bridged segment once YOLO re-acquires, and (3) **suppress case-2
flying ghosts** — bridged segments that never re-acquire a real skeleton within the
window. It publishes a second **lagged tap** (`/walldance/dancer_lagged/*`) alongside
the zero-lag causal tap, plus the active latency on `/walldance/meta/latency_ms`.
Like box-clamp it is **output-only** — it never touches the tracker, the detector, or
`DancerTrack` state, so replay goldens stay byte-identical (the case-1 lesson).

---

## 1. The baseline it extends (shipped code)
The post-YOLO output path today (`core/pipeline.py:_post_yolo_chain`):

```
tracked (DancerTrack)  → finalize(t) → ScaledTrack            # box-clamp here (§B.1)
                       → _smooth_output_box_sizes(scaled)     # causal box-size EMA (§B.2)
                       → osc.send_frame(scaled) / preview      # causal tap, zero-lag
```

Relevant per-track signals already available on `ScaledTrack` / `DancerTrack`:
- `smoothed_centroid` (EMA, `CENTROID_OUTPUT_SMOOTHING`), `bbox`, `velocity`, `keypoints`,
  `box_conf` (YOLO box conf of the feeding detection, else `None`), `is_bridged`.
- ⚠ **`_frames_since_skeleton` is NOT on `ScaledTrack`** — it lives only on `DancerTrack`
  (the box-clamp gate reads it *inside* `reported_bbox`, before finalize). The smoother runs
  on `ScaledTrack` (post-finalize), so it cannot see it today. **This is a prerequisite, not
  a given — see §2.** (Adversarial review 2026-06-15 flagged this as fatal-if-missed: without
  it, Features 2 + 3 — which test "real skeleton in the window?" — cannot be implemented.)

The smoother inserts **after** the causal EMA but does **not stack on it** — it consumes the
**raw** centroid, not the EMA-smoothed one (§2/§3 — cascaded filtering would double-lag the
lagged tap). The causal tap is unchanged; the lagged tap is new.

---

## 2. Architecture — a per-track ring buffer at the output boundary
New module **`core/output_smoother.py`** (output-only, mirrors where
`_smooth_output_box_sizes` lives). Per track id, a ring buffer of the last `L+1`
*output snapshots*:

```
Snapshot = { frame, centroid_raw(x,y), wh(w,h), is_real_skeleton, velocity, keypoints, conf }
            # centroid_raw = the tracker KF estimate (kf.x[:2]), NOT the EMA smoothed_centroid
            # (avoids cascaded filtering — review 2026-06-15). All in ORIGINAL space.
```

At frame `N` the smoother:
1. appends the new snapshot for each reported track,
2. **releases frame `N-L`** for the lagged tap — having now seen `N-L … N`, i.e. `L`
   frames of look-ahead relative to the released frame,
3. prunes track ids not seen for `> L + tracker_max_age` frames (bounded memory; ids
   are unbounded over a show — same discipline as `_box_size_ema`).

The causal tap emits frame `N` immediately (today). The lagged tap emits the
**smoothed, corrected** frame `N-L`. Two streams, same `track_id`s.

**Design rule (the case-1 lesson):** the smoother reads only the reported (original-space)
trajectory. It runs its **own** small Kalman over the buffered centroids — it does **not**
read or mutate `DancerTrack.kf`. Output-only by construction → goldens unaffected.

**Prerequisite — two output-only `ScaledTrack` fields (added at `finalize`).** The smoother
needs, per reported track per frame: (a) **`frames_since_skeleton`** (→ `is_real_skeleton =
(frames_since_skeleton == 0)`) and (b) a **raw centroid** (`DancerTrack.get_centroid()` =
`kf.x[:2]`, distinct from the EMA `smoothed_centroid`). Neither is on `ScaledTrack` today;
both must be copied through `_identity_scaled_track` (CPU) and `_unscale_letterbox` (GPU — the
raw centroid gets the same unscale transform as `smoothed_centroid`). **Output-only — copying
read-only tracker scalars never touches tracker state, so goldens stay byte-identical** (verify
with the box-clamp A/B method: `--out` summaries identical with the fields present/absent).

---

## 3. Feature 1 — trajectory de-jitter (fixed-interval / RTS)
Per track, over the `L+1`-frame window, on the **raw** centroids (`centroid_raw`, not the
EMA `smoothed_centroid` — no cascaded filtering):
- **Forward pass:** a constant-velocity (CV) Kalman over the buffered raw centroids. Every
  frame is a **measurement** (none are dropped — preserves continuity); measurement noise `R`
  is **inflated on non-real-skeleton frames** (bridged/cold/miss localize worse). **Decision
  (was open Q#4):** mirror the tracker's `MOTION_BRIDGE_NOISE_STAGES` ratios — one source of
  truth, and a `R` that's *not too high* keeps the retroactive anchor (§4) strong (review:
  over-inflated `R` makes the backward anchor weak/drifty).
- **Backward pass:** the **RTS (Rauch–Tung–Striebel) fixed-interval smoother** over the
  window → the smoothed estimate for the released frame `N-L`, informed by the `L` future
  measurements. The acausal win the causal EMA can't get: smoothing **without** the causal
  filter's lag-vs-noise tradeoff.
- **Box size:** smooth `wh` the same way (RTS / centered window mean).

`L` is the **smoothness depth = look-ahead frames**; latency = `L / fps`. The CV process
noise is the secondary "how smooth" knob (fixed default; not operator-exposed).

**Layering (was design-flaw #5 — no triple smoothing).** The lagged tap is RTS over the *raw*
centroid; it does **not** also carry the centroid EMA or the causal box EMA. For **`L > 1` the
causal tap's box reverts to raw** (the RTS lagged tap is the smoothed one — avoids two box
strategies). **`L = 1`** keeps today's behavior exactly (causal box-size EMA, no lagged tap) —
back-compat. The internal centroid EMA (`CENTROID_OUTPUT_SMOOTHING`) stays on the *causal*
centroid for back-compat; the lagged centroid bypasses it.

## 4. Feature 2 — retroactive bridge correction
When a bridged segment `[a..b]` is followed by a real-skeleton re-acquisition at frame `c`
**within the window** (`c ≤ N`), the RTS backward pass **automatically** anchors the
bridged centroids in `[a..b]` between the last real skeleton before `a` and the skeleton at
`c` → the lagged-tap path through the gap is clean *in hindsight*. No special case — it
falls out of the smoother as long as `c` is in the `L`-window.

**Coverage caveat (document for the operator):** gaps **longer than `L`** are only
partially corrected (the future anchor is outside the window — the tail is Kalman prediction,
covariance growing). **Multiple disjoint bridged segments in one window** are not guaranteed
to be coherently corrected (the RTS white-noise assumption is violated across a gap + real +
gap pattern) — document, don't fight it.

## 5. Feature 3 — case-2 flying-ghost suppression (made safe)
At release time for frame `N-L`, suppress the track from the **LAGGED tap** iff:

```
(1) is_real_skeleton(N-L) == False                       # the released frame is sustained
(2) count of CONFIDENT real skeletons in (N-L … N] < K   # K = 2 (not just ">=1 exists")
        confident := frames_since_skeleton==0 AND keypoint/box conf over a floor
(3) (optional) the last confident skeleton is within the last ~L/3 frames  # recency
```

The single-existence predicate the first draft used was **flawed** (review #3): one spurious
1-frame YOLO flicker would rescue a real ghost (false-keep), and a real dancer occluded for
`> L` frames would be dropped at a hard cliff (false-drop). Requiring **K≥2 confident**
re-acquisitions + recency makes it graceful:
- Real aerials re-acquire ~1-in-3 frames → ≥2 confident hits in a reasonable window → **kept**.
- Ambient-motion flyers never re-acquire (or only flicker once, low-conf) → **dropped**.
- A real dancer in a genuine long occlusion (`> L`) is still dropped on the lagged tap — that
  is the latency/coverage tradeoff; raise `L` to cover longer occlusions. Optionally add a
  **post-hoc un-suppression**: if a suppressed track later re-acquires within ~`L` frames,
  re-emit its (now-correctable) segment — at extra latency.

- **Causal tap is unaffected** (it can't see the future — emits live as today).
- **Output suppression only** — never deletes the tracker's track (case-1/internal lesson).
  Complements the *internal* frozen-ghost gate (`TRACKER_GHOST_SKELETON_AGE = 3` +
  `TRACKER_GHOST_FROZEN_SPEED_RATIO = 0.03`, verified in `tracker._collect_confirmed_tracks`),
  which already drops **stationary** stale tracks; case-2 catches the **moving** ghosts that
  gate spares. **Tap-id consistency:** lagged id set = causal id set **minus** case-2
  suppressions (a track the causal tap dropped via the internal gate is already absent). →
  remaining **open question #3** (the exact `K`, conf floor, recency window — joint w/ engine
  agent; tune on a labeled clip).

## 6. Feature 4 — steady high-rate OSC (optional, phase X-4)
Resample the smoothed trajectory to a fixed output rate (e.g. 60 Hz) independent of YOLO
cadence by interpolating the smoothed centroid/box between buffered frames (output-side
interpolation — the useful kind; input-frame interpolation does **not** help detection).
Adds an output timer/thread; decoupled from `L`. Ship last, only if a consumer needs it.

## 7. Dual tap + latency contract (reserved in OSC_CONTRACT §B.3)
- **Causal tap** — `/walldance/dancer/*` (today + box-clamp + causal EMA). Zero look-ahead.
- **Lagged tap** — `/walldance/dancer_lagged/*` (identical message shapes to §A.3):
  RTS-smoothed + retroactively-corrected + case-2-suppressed, `L` frames late. Same
  `track_id`s as the causal tap.
- **`/walldance/meta/latency_ms` `[ms]`** — current lagged latency (`L / fps * 1000`),
  re-emitted whenever `L` or fps changes, so TouchDesigner time-aligns the two taps.
- `L = 1` → causal tap only (lagged latency 0, no `dancer_lagged`); `L > 1` → lagged tap
  active (opt-in via `output_lagged_enabled`, default False — see §12). For `L > 1` the
  causal tap's box reverts to raw (the smoothed box lives on the lagged tap); `L = 1` keeps
  the causal box-size EMA (OSC_CONTRACT §B.2, back-compat).

## 8. Code integration points
- **`core/output_smoother.py`** (new): `OutputSmoother` with per-track ring buffers + the
  CV-forward / RTS-backward passes + the case-2 predicate. Pure, unit-testable (feed
  synthetic trajectories, assert smoothness/latency/suppression).
- **`core/pipeline.py`**: after the causal EMA, feed `scaled_tracks` to the smoother;
  it returns `lagged_tracks` (possibly fewer, due to suppression). Emit causal on
  `/dancer/*`, lagged on `/dancer_lagged/*`.
- **`core/osc_output.py`**: today `send_dancer`/`send_frame` use **hard-coded** addresses
  (`/walldance/dancer/centroid` …) — they are **not** prefix-parameterized, and there is
  **no** `meta/latency_ms` message yet (review-confirmed). Small refactor: add a `prefix`
  arg to `send_dancer`/`send_frame` (default `/walldance/dancer`) + a `send_latency_ms`.
- **`ProcessingSettings`**: `output_smoothing_l` (exists) — re-purpose `L>1` from "causal EMA
  depth" to "look-ahead buffer depth + smoothness"; `L=1` unchanged. Add
  `output_lagged_enabled: bool = False` (the lagged tap is opt-in; OSC doubles per dancer).
- **`finalize`** (`_identity_scaled_track` / `_unscale_letterbox`): add the two output-only
  `ScaledTrack` fields from §2 (`frames_since_skeleton`, `centroid_raw`).
- **GUI**: the `output_smoothing_slider` (phase ⑥) already exists; its `L>1` meaning
  upgrades. Add a "lagged tap" enable checkbox + show the published latency.

## 9. What needs the engine agent (open questions — joint design)
*Resolved by the 2026-06-15 review (now spec decisions):* **(R1) independent output CV Kalman
on the raw centroid** — not the tracker's KF, not the EMA'd centroid (output-only, no cascade);
**(R4) mirror `MOTION_BRIDGE_NOISE_STAGES`** for bridged-frame `R`. Remaining:
1. **Identity across the lag.** A track born at `N-L` but only warmup-confirmed at `N`: the
   lagged tap sees the confirmation in its window — emit retroactively from birth (cleaner
   entrances, **proposed default**) or only from confirmation? Interaction with
   `warmup_confirmed` and the slow intermittent path (bug #14). *(Proposal: lagged emits from
   the first buffered frame regardless of which path confirmed it — it has hindsight.)*
2. **Case-2 tuning** — the `K` (≥2), the confidence floor, and the recency window in §5, plus
   whether to add post-hoc un-suppression. Tune on a labeled clip; reconcile with the internal
   frozen-ghost gate so the two never fight.
3. **Read-only kinematic snapshot?** R1 uses an independent Kalman. *Optional upgrade:* the
   engine agent exposes a read-only per-frame `(x, P)` from `DancerTrack.kf` for higher
   fidelity. Only if R1's independent filter proves insufficient — keep output-only by default.
4. **Replay-gating the lagged tap** — jointly design the `replay.py` extension + metrics (§10).

## 10. Verification plan
- **Output-only** → replay goldens **byte-identical** (same A/B method as box-clamp:
  `--out` summaries identical with the smoother on/off, since it never touches the event log).
- **Unit:** `OutputSmoother` over synthetic trajectories:
  - **Cascaded-lag guard (review-mandated):** feed a 50-px / 1 Hz sinusoid at 30 fps, `L=30`;
    assert the lagged tap's measured latency ≈ `L` frames (**not** `> ~L+5` — a larger value
    means the EMA leaked into the lagged path; this catches the §3 cascade regression).
  - RTS reduces jitter vs raw (RMS residual ↓); retroactive correction reconstructs a known
    `gap ≤ L` to < ~2 px RMS; case-2 drops a never-re-acquiring blob, keeps a 1-in-3 aerial.
- **New replay metric** (open Q#4) on `hangar-aerial` (re-acquiring aerials → kept, smoothed)
  and a texture-ghost clip (flyers → suppressed). **Metric defs:** *smoothness* = RMS residual
  of the lagged centroid vs its RTS fit (lower better); *latency* = frame delay causal→lagged
  (must equal `L`); *case-2* = precision/recall (F1) of suppression vs labeled ground truth.
- `L = 1` default unchanged; lagged tap strictly opt-in (`output_lagged_enabled=False`).

## 11. Phasing
- **X-1 (this doc):** design + namespace reserved (done in OSC_CONTRACT §B.3). ✅
- **X-2 — SHIPPED (branch operator-v2-batch3):** the prerequisite finalize fields (§2),
  `core/output_smoother.py` (CV-forward Kalman + RTS-backward over `centroid_raw`, R-inflation
  mirroring `MOTION_BRIDGE_NOISE_STAGES`), the dual tap (`/walldance/dancer_lagged/*`) +
  `meta/latency_ms`, and the phase-⑥ lagged-enable toggle + latency readout. Retroactive bridge
  correction already falls out of the RTS pass. **Verified:** goldens 3/3 byte-identical; the
  output-only A/B (lagged tap ON → identical internal summary vs golden); the cascaded-lag guard
  (measured extra lag = 0 → total latency = `L`, no EMA leak); and on `hangar-aerial` (CPU+TRT)
  the lagged centroid is ~5× smoother than causal with frame delay = `L` vs the raw centroid
  (`tests/verify_lagged_tap.py`). `output_lagged_enabled` default **False** (opt-in).
- **X-3 (GATED — needs the §9 engine-agent answers):** case-2 flying-ghost *suppression* (the
  hardened ≥2-confident-reacquisitions + recency predicate, §5) + tap-id consistency (lagged ids =
  causal minus case-2). The RTS retroactive correction is already in X-2.
- **X-4:** steady-rate resample (optional).

Owner split: operator-surface lane = `output_smoother.py` scaffold + controls + OSC
integration + replay metric; engine agent = the kinematics/identity calls in §9.

---

## 12. Edge cases & lifecycle (from the 2026-06-15 completeness review)
Lock these before the joint build (a ~30-min sync); they're decisions, not open research.

- **Startup / track birth:** for the first `L` frames after startup *or* a track's birth, the
  lagged tap is **silent** for that track (no full window yet). First lagged emission =
  `birth + L`.
- **Track death mid-window:** when a track ages out/goes dormant, **flush** its buffered tail
  (emit the remaining frames `L`-late), don't drop them. Then prune.
- **Track-id reuse:** ids are monotonic (OSC_CONTRACT §A.4) → a "reborn" id can't collide;
  a reborn track is **new** (no state carry-over). Prune buffers `> L + tracker_max_age` old.
- **`L` changed live (operator slider):** truncate each track's buffer to the new size and
  **release any now-overdue frames immediately** (avoid stale releases); the next
  `meta/latency_ms` reflects the new `L`. Accept a one-time small discontinuity on change.
- **Lagged tap opt-in + volume:** `output_lagged_enabled` (default **False**). A second full
  `/dancer_lagged/*` stream **doubles** OSC traffic per dancer (centroid+bbox+velocity+17
  kpts). Batch-2 ships the full message set when enabled; per-message-type filtering deferred.
- **Warmup interaction:** lagged emits a track from its first buffered frame regardless of
  whether the integral or the slow intermittent path (bug #14) confirmed it (hindsight). The
  *causal* tap still respects warmup as today.
- **Feature 4 (resample) threading:** a fixed-rate **timer thread** reads the last two smoothed
  snapshots and interpolates **centroid, bbox, and all 17 keypoints** element-wise, keyed on
  **elapsed wall-time** (pass timestamps in — `time` is fine off the render loop). OSC send is
  thread-safe (UDP). Fail-safe: if the newest snapshot is `> 2` output intervals stale, emit it
  without interpolation. Disabled by default; phase X-4.
- **DPG/threading note:** the smoother posts to OSC (off-thread safe); it does **not** touch
  DPG. Any phase-⑥ status it surfaces (e.g. published latency) follows the batch-2 rule —
  `dpg.set_value` only from a background thread (see `gui.show_dryrun_result`).
