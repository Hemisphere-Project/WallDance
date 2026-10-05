# 01 — Tracking continuity audit (WallDance)

**Date:** 2026-10-05 · **Scope:** why the emitted centroid stream (`/walldance/dancer/*`, `/walldance/count`) drops or changes id, measured on local footage, with ranked fixes. Read-only on the repo: nothing in `application/` was edited. All scripts, caches, run JSONs and logs are in `tmp_analysis/audit-2026-10/continuity/` (`drive.py`, `analyze.py`, `contmetrics.py`, `lifecycle.py`, `feeds.py`, `slotsim.py`, `summary.py`, `sheet.py`, `sheets/*.png`). Box: dev i7-3770K + RTX 3090, PyTorch GPU path (no TRT), shared CPU, so no timing conclusions.

Legend: **[M]** measured here · **[C]** read in code · **[H]** hypothesis · `h` = person height (tracker px).

---

## 0. Executive summary

1. **The passing 300-frame scenario windows hide the continuity problem.** **[M]** On the *whole* recordings with the pinned golden configs:
   - **hangar-aerial (slot 4, 4.3 min, N=1):** coverage 94.2 %, 94 drop episodes (21.9/min), longest 1.57 s, and **25 distinct OSC ids for one dancer**. The scenario fails class A (drop 5.8 %, longest 1.57 s), and only 7 of 16 consecutive 300-frame windows pass.
   - **hangar-floor (slot 3, 8.1 min, N=1):** coverage **57.5 %**, 39 gaps ≥ 0.5 s, longest **30 s**. The goldens are short windows chosen in good stretches.
2. **Drops are manufactured after detection, not by missing detections.** **[M]** On slot 4 the dancer is boxed at ≥ 0.25 conf on 94.6 % of frames and an internal track exists on **100 %** of drop frames. The 293 drop frames break down as:

   | Cause | Share of drop frames |
   |---|---|
   | Frozen-ghost report gate | 59 % |
   | Track split (new id still warming up) | 26 % |
   | Warm-up integral decay | 11 % |

   On slot 3, **93 %** of drop frames come from warm-up starvation. During the long holes YOLO sees the dancer at ≥ τ on only ~30 % of frames (60 % overall), the motion path is dead in that config, and the +1/−0.8 integral never reaches 15.
3. **The operator's "+++ merging tracks (yolo AND motion)" is a concrete bug class.** **[M]**
   - **How it happens:** a MOG2 blob becomes a second "synthetic" detection of the *same* dancer whenever its centre is more than 0.3 h from the YOLO box centre (`tracker.py:2662-2684`). Synthetics compete with YOLO in the same Hungarian pass. The blob often wins, and the YOLO skeleton is thrown away.
   - **How often:** 326 frames on slot 4.
   - **What follows:** the track turns "skeleton-stale", and the frozen-ghost gate hides it. At frames 4820-4828 the dancer disappears while YOLO boxes her at **0.61-0.87** on every frame.
   - **Duplicate ids:** 203 frames emit 2 ids, and in 86 % of them *both* ids sit on the dancer.
4. **Fixed wall spots seed tracks that hijack the dancer.** **[M]** 28 of 105 tracks are born on 3 fixed hangar wall spots. Once a tentative track is unmatched, its covariance grows without bound, so it can capture the dancer **520 px away** (frame 329). Painting those 3 exclusion cells has these effects:
   - tracks 105 → 42;
   - OSC ids 25 → 15;
   - 2-id frames 203 → 62;
   - but no coverage gain.

   Both hangar pins ship `exclusion_cells: []`. On the textured tango-H2 wall (April config), it is worse: the emitted id sits on a **wall-stain figure instead of the dancer** in ~half of the sampled frames, and the count-based scorer still calls that 79 % "coverage".
5. **Knob-only quick win on slot 4.** **[M]** Combining τ 0.35, 3 exclusion cells and frozen-gate skeleton age 3 → 15 gives:
   - coverage 94.2 → **99.5 %**;
   - 94 → 8 gap episodes;
   - longest gap 1.57 → 0.51 s;
   - ids 25 → 17;
   - 2-id frames 203 → 213, so duplicates are *not* fixed.

   Relaxing the frozen gate without exclusion doubles the emitted zombies (2-id frames 203 → 482).

   **Decoupling continuation from birth** (keep emitting a once-emitted track while its last skeleton is ≤ 20 f old; a CONT-4 stand-in):
   - **slot 3: 57.5 → 93.8 %**, 0 duplicates;
   - slot 4 with exclusion: 98.2 %, ids 15;
   - textured tango-H2 without exclusion: 2-id frames 259 → 927, because stain "skeletons" keep ghosts alive. Ghost control must come first.
6. **Biggest structural lever: a known-N "identity-slot" output layer.** **[M, simulated]** This is an output-only layer with stable ids 1..N, explicit *coasting*, and rebinding by proximity. Replayed offline on the baseline runs:

   | Slot | Coverage today → with slot layer | Ids | Rows outside 0.75 h of the dancer |
   |---|---|---|---|
   | 4 | 94.2 → 98.7 % (0.5 s hold) / 99.7 % (binding live-but-unconfirmed tracks) | 25 → **1** | 2 % |
   | 3 | 57.5 → 90.8 % (relaxed, 0.5 s hold) | 3 → 1 | — |

   It also makes duplicates harmless: with N known, extra tracks on the same dancer are simply not emitted.
7. **Bugs found.** **[C+M]**
   - (BUG-1) Resurrected tracks are not "immediately confirmed", even though the docstring says so: warm-up resets to 1.0. A naive fix re-emits ghost tracks (ids 25 → 29).
   - (BUG-2) `time_since_update` goes **negative** (-1) when a bridged track is occlusion-aged.
   - (BUG-3) `replay.py` applies `tracker_max_age` *before* `tracking_mode`, so motion_first projects replay with max_age 60 instead of the app's 15.
   - (BUG-4) `tracker_intermittent_confirm` is never enabled by anything (known-N does not search it), yet the docs say it is.
   - Doc drift in OSC_CONTRACT A.3 (it does not say when an id disappears).
8. **Coast policy (IR-marker stream's question).** **[M]** Coasting KF velocity at 0 per miss ("hold"):
   - **Identity improves:** ids 25 → 18, tracks 105 → 67, 2-id frames 203 → 131. Drift is what creates zombies and splits.
   - **Coverage gets worse:** 94.2 → 93.1 %, because the frozen-ghost gate keys on *instantaneous KF speed*, so a held track looks "frozen".
   - **Conclusion:** switch association to hold-style coasting, but only after decoupling the frozen gate from KF speed (CONT-4). Output-side hold is what L=1 already emits (the EMA centroid is held on a miss).
9. **`scoring.py` is count-based and too short to see any of this.** **[C]** It has no spatial check, no "duplicate on the dancer" vs "ghost in the scenery" split, no re-association or acquisition latency, and its warm-up exclusion hides acquisition. CONT-7 adds a continuity suite and long-span scenarios.
10. **Today's logs cannot tell why an id vanished.** **[C]** `FRAME_SUMMARY` does not record the emitted id set or gate reasons. Live runs append forever to one `tracking_events.jsonl`. CONT-10 is a prerequisite for using the operator's field logs.

---

## 1. Lifecycle map (code-verified)

All refs `application/src/core/…` unless noted. Tracker space = ROI-local letterbox (imgsz) px; `h` = `person_height_px × lb_scale` (187 px for hangar-aerial).

### 1.1 Detection intake (pipeline, per frame)
| Step | What happens | Where | Continuity relevance |
|---|---|---|---|
| YOLO | `model(..., conf=settings.confidence)`: τ is applied *inside* YOLO, so sub-τ boxes never reach Python | `pipeline.py:582-589` | With τ = 0.5 (hangar-aerial pin), the dancer is sub-τ (0.25–0.5) on **28 %** of slot-4 frames |
| Extract | box conf kept only in a side map `_last_box_confs` keyed by bbox value | `pipeline.py:1412-1466` | conf is not carried on the detection tuple |
| Size gate + dedupe | `_filter_duplicate_detections`: `h < h_px·min_ratio` → merged into a nearby big det **or silently dropped**; `h > max_ratio` dropped; shadow suppression; pairwise merge | `pipeline.py:1644-1749` (gate 1654-1666) | small/far figures die here (class g) |
| Scored gate (crossval) | keep if TRACK-overlap ∨ frame-diff ≥ θ_m (×1.2 in low light) ∨ strong skeleton (≥ 8 kpts, mean ≥ 0.45) | `pipeline.py:1498-1642` | static + weak-skeleton person rejected (class f) |
| Exclusion | manual cells; protected within 0.6 h of a live track | `pipeline.py:1214-1249` | the only ghost-spot weapon; empty in both hangar pins |
| Cold blobs | MOG2 blobs filtered: h ≥ 0.3 h, aspect 0.3–2.0, 60-frame MOG2 warm-up, static-cell kill after 90 f. Then gated by frame-diff ≥ θ_m + exclusion | `pipeline.py:799-810, 1051-1079`; `motion_detector.py:284-397` | the "motion fallback" already exists |
| Fusion | blob → synthetic det (17 kpts at the blob centre, **conf = 0**) unless its centroid is < **0.3 h** from a YOLO *box centre*; needs ≥ 3 frames of persistence in a 0.35 h cell | `tracker.py:2652-2703` | **root of the YOLO-vs-motion split** (§2 d) |

### 1.2 Association (`DancerTracker.update`, `tracker.py:2187-2246`)
1. **Predict** every track (`tracker.py:364-429`).
   - On a miss: velocity ×0.9, warm-up −0.8 (from the 2nd miss), `_frames_since_skeleton += 1`.
   - On a bridge frame: velocity ×0.5 and P ×1.5.
2. **Cascaded Hungarian** (`tracker.py:2302-2411`).
   - Pass 1: established tracks (≥ 15 hits) × **all** dets, YOLO and synthetic mixed. Pass 2: tentative tracks × leftovers.
   - Cost (`1252-1352`) is applied in this order:
     1. Mahalanobis gate χ² ≤ 16.27 with R_gate = 700 (×3 when bridged; `1031-1063`).
     2. Displacement gate for established tracks with tsu ≤ 1: a centroid jump > 0.5 · 0.95 h ⇒ cost 1e6 (`1304-1319`).
     3. Blended pos/kpt/size/IoU cost.
   - Accept if cost < `dt + speed + min(tsu·0.04dt, 0.3dt)`, **or if raw distance < 0.2 h even when the cost is 1e6** ("CLOSE_ACCEPT", `1879-1919`).
3. **Unmatched dets** (`2413-2483`).
   - Farther than the creation gate from every track's last *measured* position (0.55 h, ×1.5 in the centre zone ⇒ 0.825 h): resurrect from dormant, else **new id**.
   - Otherwise: force-update the closest *unmatched* track.
   - Otherwise: drop as DUPLICATE/AMBIGUOUS_IGNORED.
4. **Motion bridge** for still-unmatched tracks (`2708-2998`). Tiers: track-local MOG2 blob → frame-diff blob → "presence" hold (≤ 15 f, tsu keeps growing).
   - A blob bridge **resets tsu to 0**, adds +0.4 warm-up, moves the bbox and overrides the KF velocity with the blob displacement.
   - It never resets `_frames_since_skeleton`. Cap: 80 consecutive bridge frames.
5. **Occlusion aging** (`3000-3066`): an unmatched track near a matched one ages at 0.1/frame, implemented as `tsu -= 1` (`3045`; BUG-2).
6. **Lifecycle** (`3068-3159`).
   - Expiry at `tsu ≥ max_age`: 45 by default, 135 f for established tracks (×3), ×0.5 at the edges. Expired tracks go dormant for 150 f.
   - Ghost expiry: `age ≥ 100 ∧ hits < 5 % of age`.
   - Shadow kill.
   - Duplicate merge: established pair, < 0.3·dt, for 8 f.
   - **Takeover merge** (`3469-3577`): established pair < 0.6 h, fed one-sided, co-fed < 3 frames, on 4 of 8 frames ⇒ the lower-hits id is absorbed and the keeper inherits its state.
7. **Resurrect** (`2052-2185`): only reached when a det is > the creation gate from *all* active tracks. Applies position/size/shape gates, then sets `hits`, **not** the warm-up score.

### 1.3 Report boundary — what OSC sees (`_collect_confirmed_tracks`, `tracker.py:3161-3233`)
A live track is emitted only if **all** of these hold:
- **Warm-up:** `_warmup_score ≥ 15`, or the intermittent path when `tracker_intermittent_confirm` is on (default **off**, `config.py:789`).
  - The integral gets +1 per match (synthetic matches included), +0.4 per blob bridge, −0.8 per missed frame from the 2nd miss, capped at 20.
  - ⇒ A confirmed track at the cap is hidden after **8** un-bridged misses. A new or resurrected track needs about 14 consecutive feeds.
- **Frozen-ghost gate:** not (`_frames_since_skeleton > 3` ∧ KF speed < 0.03 h px/frame) (`3175-3184`). Synthetic (conf 0) and bridge feeds do not count as a skeleton.
- **Slow-path separation:** slow-path tracks need 0.7 h separation from integral-confirmed ones.
- **Cap:** top `MAX_PERSONS` = 6 by hits.

**Output path:** `pipeline.py:836-876` → `osc_output.py:118-128`.
- **Count:** every frame, `/walldance/count [n, ids…]` lists exactly the emitted set. There is **no per-id state** (live / coasting / lost).
- **Vanish:** an id **vanishes** from `/count` and from all `/dancer/*` on the first frame it fails the gate. There is no coasting and no "lost" message.
- **New id:** one appears whenever a new track first passes the gate. Ids are monotonic and never reused (`tracker.py:155-168`).
- **Held centroid:** while an id is emitted on a plain miss, the EMA centroid is held (it is updated only on a feed, `tracker.py:494-497`), so the consumer sees a frozen point.
- **L > 1:** the RTS smoother restarts on any reporting gap (`output_smoother.py:296-305`), so it does not bridge gaps either.

---

## 2. Root-cause taxonomy

Per-frame attribution from `analyze.py`. For every drop frame (dancer present, nothing emitted) it records:
- the state of every internal track;
- what YOLO saw, including sub-τ boxes taken from a 0.10-floor cache;
- a pseudo-GT dancer position: the best box ≥ 0.25 that is not on a recurring fixed spot.

The fixed-spot exclusion makes the pseudo-GT reliable on the white-paper hangar. It is **not** reliable on the textured tango-H wall (§3.2).

**Slot 4 (aerial) — 293 drop frames, 94 episodes:**

| Drop-frame cause | frames | share |
|---|---|---|
| frozen-ghost gate, track **on the dancer** | 112 | 38 % |
| split: the dancer's detection feeds a new/unconfirmed track while the established one goes stale | 77 | 26 % |
| frozen-ghost gate, track **drifted off** the dancer (zombie) | 40 | 14 % |
| warm-up integral decayed below 15 | 31 | 11 % |
| frozen gate, dancer position unknown (YOLO < 0.25) | 21 | 7 % |
| cold-start acquisition (right after the 15-frame scoring warm-up) | 12 | 4 % |
| **no internal track at all** | **0** | 0 % |

- **What YOLO saw on those frames:**
  - a dancer box ≥ τ that passed every gate on 112 (38 %);
  - a sub-τ box on 154;
  - < 0.25 on 23;
  - nothing on 2.
- **Episode lengths:** 67 × 1–2 f, 17 × 3–7 f, 8 × 8–19 f, 2 × 20–59 f. The consumer mostly sees **flicker** (21.9 gaps/min) plus a few 0.5–1.6 s holes.

**Slot 3 (floor/rope, 9 674 f) — 4 105 drop frames, 70 episodes:**
- Causes: 3 838 (93 %) warm-up starved, 169 cold start, 37 split, 37 frozen-gate, 24 other.
- Episodes ≥ 60 f hold 3 330 drop frames; the longest are **30.0 s, 23.5 s and 18.6 s**. An internal track exists on the dancer throughout every one of them (same id before and after in 2 of the 3), and YOLO boxes the dancer at up to 0.84–0.87.

### (a) YOLO miss with no bridge
- **Mechanism.** **[C]**
  - τ is enforced inside YOLO, so a sub-τ dancer produces nothing.
  - Two fallbacks exist: cold-blob synthetic dets and the track-local bridge.
- **Frequency on slot 4.** **[M]**
  - The dancer is sub-τ / weak / blind on 1 709 of 5 073 frames, yet only 179 drop frames have YOLO < τ. The motion path covers ~90 % of the YOLO gaps there.
  - Without synthetic dets: coverage 94.2 → 92.1 %, longest gap 1.57 → **4.16 s** (`s4_nosynth`).
  - Without the bridge: coverage −0.2 pt, but ids 25 → 11 and 2-id frames 203 → 57 (`s4_nobridge`). On this footage **the bridge creates more duplicates than continuity**.
- **Frequency on slot 3.** **[M]**
  - With the pinned hangar-floor config, **0** bridged frames and **0** synthetic matches in 9 674 frames: the motion path is completely inert.
  - The pinned config has gamma 0.53 (a darkening LUT, also applied to the motion gray at `pipeline.py:1081-1098`) and var 40 @ scale 0.99.
  - Motion-only fix experiment (`s3_motion`): see §3.4.
- **Fixes.** Keep the motion feed but make it subordinate to YOLO (CONT-3). Lower τ with exclusion paint (CONT-2).

### (b) Track killed too early / not resurrected
- **Mechanism.** **[C]**
  - Internal life is long (135 f for an established track), so the track itself is rarely killed. What kills the **output** is the integral.
  - Resurrection (`2052-2185`) is reachable only when the det is far from every active track, so zombies block it.
  - It **does not restore the warm-up**: `wu = 1.0` was measured on all 7 slot-4 resurrects that survived the frame, with 7–25 hits (BUG-1).
- **Frequency.** **[M]**
  - Slot 4: 31 drop frames.
  - Slot 3: 93 % of all drop frames. This is the bug-#14 regime: YOLO duty is about 30–45 % and the +1/−0.8 integral cannot hold 15.
  - Of 14 slot-4 resurrects, 12 revived scenery ghost tracks that had never been emitted. The one real track (29) was never re-emitted.
- **Fixes.**
  - Decouple *continuation* from *birth confirmation* (CONT-4).
  - Restore warm-up on resurrect, **only for previously emitted tracks** (CONT-1). Measured blind, it re-emits ghosts: ids 25 → 29.
  - Enable the intermittent path per scene (CONT-2).

### (c) New ID minted instead of re-association
- **Mechanism.** **[C]**
  - During a miss streak the KF velocity drifts the prediction, while the creation gate compares against the stale *last measured* position.
  - A fast swing puts the re-detection farther than 0.825 h (creation gate) or farther than 0.475 h (displacement gate on a tsu ≤ 1 established track) ⇒ a new id with a 15-frame warm-up.
  - The old id lingers as a zombie. A takeover merge later absorbs one of the two.
- **Frequency on slot 4.** **[M]**
  - 100 YOLO-born + 5 motion-born tracks for one dancer, and **25 emitted ids in 4.3 min**.
  - 7 of 92 gaps end on a different id. 38 emitted-id changes happen *without* a gap (handovers). 31 takeover merges.
  - Longest hole, episode 4274 (1.57 s): during a sub-τ stretch, track 28 slid onto the dark floor band, where floor-noise motion kept it alive. The dancer reappeared 260 px away, and the new id 83 needed 27 frames of warm-up (`sheets/s4_ep4274.png`).
- **Fixes.**
  - Identity-slot layer (CONT-6).
  - Hold-style coasting for association (CONT-4b).
  - Stale-established re-acquire gate (`--reacq 2.5`): weak alone, ids 25 → 23, +0.65 pt.

### (d) YOLO-vs-motion merge/split — the operator's "+++"
- **Mechanism.** **[C+M]**
  1. A MOG2 blob of a swinging aerial dancer (body + rope + limbs) sits 0.3–0.6 h from the YOLO box centre, so it survives the 0.3 h overlap test and **the same dancer arrives twice**.
  2. Pass 1 mixes YOLO and synthetic dets. The blob is often closer to the KF prediction, or the YOLO centroid trips the displacement gate.
  3. The established track eats the blob, and the YOLO skeleton is dropped as DUPLICATE/AMBIGUOUS_IGNORED.
  4. Blob feeds never reset `_frames_since_skeleton`, so after 4 such frames a slow phase trips the frozen-ghost gate.
- **Frequency on slot 4.** **[M]**
  - Synthetic dets are 41 % of all committed matches (2 331/5 648), and 44 % of the dancer track's feeds. They are load-bearing.
  - **326 frames**: an unmatched YOLO det ignored next to a track that had just been fed a synthetic det.
  - 534 matches fed a *second* track within 1.5 h of the dancer.
  - 203 frames emit 2 ids, and in 86 % of them both ids are on the dancer.
  - Frames 4820-4828: YOLO 0.61–0.87 on every frame; track 86 is blob-fed instead (fss 15→23, speed 1.3–5.4 < 5.6 px/f); the dancer is hidden for 9 frames.
- **Fixes.**
  - CONT-3: two-tier assignment (YOLO first, synthetic only for tracks YOLO left unmatched) + blob suppression by box containment.
  - The pieces alone are partial **[M]**:

    | Patch | Coverage | Ids | 2-id frames |
    |---|---|---|---|
    | `--fuse-box 0.1` | 95.2 % | 25 → 18 | 203 → 85 |
    | `--yolo-first-match` | 95.7 % | 25 | 203 → 99 |

    The remaining drops then come from the frozen gate and splits, so CONT-3 must ship together with CONT-4.

### (e) Ghost tracks competing
- **Mechanism.** **[C+M]**
  - Fixed wall features occasionally score ≥ τ: (870,490) h≈155, (1230,515) h≈90, (1345,513) h≈100 — the same hangar spots as CORPUS_ANALYSIS §3.3.
  - The scored gate admits them.
  - An unmatched tentative track's covariance grows without bound, so pass 2 can hand it the dancer's det **520 px away** (frame 329: cost 218 < threshold 230.6; `logs/s4_base`).
  - The ghost then migrates onto the dancer, becomes established, is emitted as a 2nd id, and is eventually takeover-merged.
- **Frequency.** **[M]**
  - Slot 4: 28 of 105 tracks born on the three spots; 369 YOLO feeds to far tracks.
  - `s4_excl` (3 cells painted): tracks 105 → 42, ids 25 → 15, 2-id frames 203 → 62, coverage 94.6 %.
  - tango-H slot 8 (textured wall): **92 internal tracks** for one dancer in 75 s, but only 2 emitted ids, spatially correct.
  - tango-H2 slot 9 (textured wall): 136 internal tracks, 15 emitted ids, 259 frames with 2 emitted ids. On a sampled sheet, ~half of the emitted frames sit on wall stains while the dancer is missed (§3.2).
- **Fixes.**
  - Paint exclusion (exists). Propose cells at calibration time (CONT-9).
  - Bound the tentative-track search radius and covariance (CONT-5).

### (f) Static dancer never acquired / dropped when still
- **Mechanism.** **[C]**
  - Acquisition needs a strong skeleton or frame-diff ≥ θ_m.
  - Cold blobs also need frame-diff, and are static-suppressed after 90 f.
  - *Any* track that is skeleton-stale for > 3 frames **and** slower than 0.03 h/frame is hidden, even when it is sitting on the dancer.
- **Frequency.** **[M]**
  - Slot 4: 112 + 21 frozen-gate drop frames. **[H]** These are mostly a *slow* aerial dancer at the end of a swing, fed by blobs instead of YOLO (§2 d).
  - Slot 3: 37.
  - tango-H slot 8 opens with the dancer **crouched and static on the floor for ~250 frames**. It *was* acquired at frame 16, via the strong-skeleton path in good light. No never-acquired case is reproducible on local footage. The laptop goldens show the flicker signature instead: `texture-wallhang` has 35 gaps of median 0.1 s, with the same id returning 34/34 times.
- **Fixes.**
  - Base the frozen gate on time since strong evidence + displacement, not instantaneous speed (CONT-4).
  - Marker "confirmed-real" evidence (§5).

### (g) Small / far figures YOLO can't see (operator's "Motion detect fallback avant Yolo")
- **Mechanism (structural).** **[C]**
  1. `person_height_px` is calibrated on the median *detected* height. Both the YOLO size gate (`h < 0.52·h_px` is dropped in the aerial pin, `pipeline.py:1654-1662`) and the blob gate (`h < 0.3·h_px`, `motion_detector.py:351`) scale with it. A far figure in a scene calibrated on near dancers is therefore filtered twice.
  2. The minimum blob area is 100 px²·scale², ×1.8 in low light.
  3. A motion-only track starts with `_frames_since_skeleton = 999` (`tracker.py:212-213`). It is emitted **only while moving > 0.03 h/frame** and must still reach warm-up 15.
- **Evidence.** **[M]**
  - Slot 4: 5 of 105 births were motion-born and 3 were emitted. One of them (id 28) carried the dancer for 2 763 frames. The fallback *can* acquire.
  - No IR small-far footage exists locally.
  - tango-phone slot 7 (4 dancers, boxes ≈ 80–120 px, on a sunny facade) is daylight phone footage; its probe is in §3.2.
- **Fixes.** A dedicated motion-first acquisition lane for small figures, with its own size prior and emission through the slot layer (CONT-8). Not a looser global gate.

### (h) Acquisition latency
Minimum 15 frames (integral 1 → 15). Measured 27 frames at the slot-4 start and **185 frames (9.3 s)** at the slot-3 start. Every split in (c) pays it again.

### Corpus-wide shape (laptop goldens, 12 scenarios, CPU path, 300–400-frame windows) **[M]**
Computed with `contmetrics.py` on the committed `tests/golden/decomp-phase0/*.timeline.json`:

| scenario | N | coverage | drop eps | gaps/min | longest s | ids | same-id / new-id after a gap |
|---|---|---|---|---|---|---|---|
| hangar-floor | 1 | 0.930 | 2 | 8.3 | 0.71 | 1 | 1/0 |
| hangar-aerial | 1 | 0.958 | 1 | 4.2 | 0.61 | 1 | 0/0 |
| texture-aerial | 1 | 0.949 | 6 | 12.2 | 0.56 | 2 | 3/2 |
| texture-duo | 2 | 0.523 | 21 | 64.8 | 3.58 | 8 | 19/0 |
| texture-wallhang | 1 | 0.639 | **35** | **108** | 2.47 | 1 | 34/0 |
| white-duo | 2 | 0.780 | 15 | 46.3 | 3.33 | 9 | 14/0 |
| outdoor-night | 1 | 0.711 | 2 | 7.5 | 4.29 | 1 | 0/0 |
| outdoor-sitter | 2 | 0.934 | 7 | 21.4 | 1.74 | 2 | 6/0 |
| blur-runner | 1→2 | 0.965 | 9 | 27.8 | 0.20 | 2 | 9/0 |
| facade-ghosts | 4 | 0.967 | 2 | 9.3 | 0.70 | 26 | 1/0 (ghost 1.14) |
| dark-crowd / white-walkers | var | 0.11 / 0.00 | – | – | 9.6 / 19.5 | – | detection failure, not continuity |

Flicker with the same id returning dominates the short windows. Id churn only shows up on long spans.

---

## 3. Measurements

### 3.1 Method and fidelity
- **Cache.** `drive.py build` runs the real GPU path once per recording at a **0.10 YOLO floor** and stores raw pre-dedupe dets + box conf + tracker space + an md5 of the motion gray.
- **Replay.** `drive.py replay` re-decodes the lossless FFV1 frames to rebuild the motion gray (md5 verified, 0 mismatches), filters dets by τ, re-runs `_filter_duplicate_detections`, then calls `FrameProcessor.replay_gpu_cached` — the same `_post_yolo_chain` as live. It records per frame: emitted tracks, every internal track (tsu, hits, warm-up, fss, speed, bridged), dormant ids, gate counters, cold blobs, and the top-3 raw dets including sub-τ ones.
- **Fidelity.** **[M]** On hangar-aerial frames 1500–1799, the cache replay at τ = 0.5 matches `tests/replay.py --scenario hangar-aerial --score` (PyTorch) **frame for frame on the emitted id sets: 0 mismatches over 300 frames**. The direct run passes: drop 3.86 %, one 11-frame cold-start drop at 1515–1525, 1 id, 0 ghost.
- **GT.** N = 1 throughout every local recording used. Checked on brightened contact sheets: `sheets/s4_overview.png` (every 100 f), `s3_overview.png` (every 200 f), `tH8.png`, `tH2_9.png`.
- **Behaviour experiments** are monkeypatches inside `drive.py` only (`--const`, `--fuse-box`, `--yolo-first-match`, `--reacq`, `--resurrect-warm`, `--sticky`, `--keep-confirmed`, `--coast-vel`). The repo is untouched.

### 3.2 Long-span baselines (N = 1; pinned scenario configs; tango-* use their own April project config with app-faithful max_age)
| recording | frames | coverage | gaps (/min) | longest | gaps ≥ 0.5 s | emitted ids | 2-id frames | internal tracks |
|---|---|---|---|---|---|---|---|---|
| whitebg2 s4 aerial (`hangar-aerial` pin) | 5 088 | **0.942** | 94 (21.9) | 1.57 s | 6 | **25** | 203 | 105 |
| whitebg2 s3 floor/rope (`hangar-floor` pin) | 9 674 | **0.575** | 70 (8.6) | **30.0 s** | 39 | 3 | 0 | 3 |
| tango-H s8 textured climb, static start | 1 504 | 0.991 | 4 (3.2) | 0.40 s | 0 | 2 | 15 | **92** |
| tango-H2 s9 textured wall dance | 1 766 | 0.790 † | 22 (14.9) | 7.63 s | 5 | 15 | **259** | 136 |
| s4 frames 1000-2499, **April config** | 1 500 | 0.882 | 9 | 2.79 s | – | 5 | 0 | 54 |

- **Slot 4, 300-frame windows** with the official scorer on a warm tracker: **7 of 16 pass** class A. Worst window: 4200–4499 (drop 28 %, longest 1.57 s, 4 ids).
- **Skeleton feed:** 53 % of emitted slot-4 dancer-frames have a fresh skeleton.
  - Feed mix: 39 % synthetic-blob-fed, 7 % bridged, 0.5 % plain coast.
  - Skeleton gaps on long tracks: p50 2 / p90 9 / p99 26 frames. This agrees with the IR-marker stream's 53 % and p50 2 / p90 6.
- **Position quality vs the pseudo-GT, slot 4** (median / p90, in h):

  | Feed | Error |
  |---|---|
  | skeleton | 0.10 / 0.21 |
  | synthetic | 0.23 / 0.49 |
  | bridged | 0.31 / 0.78 |
  | plain coast | 0.37 / 1.56 |

  Only 4.3 % of emitted frames repeat the previous centroid exactly (held point).
- **† Count coverage ≠ spatial coverage on textured walls.** On a 16-frame sampled contact sheet of tango-H2 (`sheets/tH2_marks.png`), the emitted id sits on the dancer in only ~4 frames and on a **wall-stain "figure" in ~8**: the output reports a ghost while the dancer is missed, and the count scorer calls that "covered". tango-H s8 is spatially fine: 15/16 sampled frames on the dancer, including the crouched static start (`sheets/tH8_marks.png`). The automatic pseudo-GT is unusable on these walls, which is one more reason for C8 with real spatial GT (markers or hand labels).
- **tango-H2 failure shape.**
  - Drop cause: 270 "split" frames + 68 warm-up frames.
  - **76 resurrects** in 89 s.
  - The April config's `tracker_max_age` 12, honoured by the app (BUG-3), kills tracks quickly and re-mints them.
- **tango-phone s7** (4 dancers ≈ 100 px, daylight phone, facade + foliage, project config τ 0.2 / h 90 / imgsz 1536, frames 600–1199):
  - **YOLO is not blind at ~100 px:** 3.3 boxes/frame ≥ 0.2, and ≥ 4 boxes on 53 % of frames.
  - The output still churns: count coverage 97.7 %, but **ghost rate 1.21** and **42 ids for 4 dancers in 20 s**.
  - The cold-blob path produces **11 blobs/frame** on this non-static background.
  - **Conclusion:** a looser motion-first acquisition would flood such a scene. The operator's own qualifier "**sur fond fixe**" is the right precondition (CONT-8). No IR footage with truly tiny (< 50 px) figures exists locally.

### 3.3 Continuity metric — definition, and what `scoring.py` covers
For "position + rough identity", per scene:

| # | metric | definition | in `scoring.py`? |
|---|---|---|---|
| C1 | coverage | Σ min(emitted, N) / Σ N | yes (`1 − drop_rate`) |
| C2 | gap rate | drop episodes per minute | count only (`drop_episodes`) |
| C3 | gap distribution | p50/p90/max gap, #gaps ≥ 0.5 s and ≥ 1 s | max only |
| C4 | ids per dancer | distinct emitted ids / N over the span | `distinct_ids`, weight 0.1, bounded |
| C5 | re-association | share of gaps after which the same id returns (N = 1; spatial match for N > 1) | **no** |
| C6 | handovers | emitted-id change on consecutive covered frames | `id_switches` |
| C7 | duplicates vs ghosts | extra ids within 0.75 h of a dancer vs elsewhere | **no** (both counted as "ghost") |
| C8 | spatial validity | share of emitted dancer-frames within 0.75 h of a reference (YOLO ≥ 0.5 pseudo-GT now, markers later) | **no** |
| C9 | acquisition latency | frames from first dancer evidence to first emission | **no** (the warm-up exclusion hides it) |
| C10 | freeze share | emitted frames whose centroid equals the previous one | **no** |

Proposed pass line for class A on **long spans (≥ 4 min)**:
- C1 ≥ 0.98;
- 0 gaps ≥ 1 s;
- C2 ≤ 5/min;
- C4 ≤ 1.5;
- C8 ≥ 0.97.

`contmetrics.py` implements C1–C6 on any replay timeline (incl. the existing goldens). `analyze.py` + `slotsim.py` add C7, C8 and C10 using drive rows.

### 3.4 Experiments (full slot 4, pinned hangar-aerial, cached; one change per row unless noted)
Columns: coverage / gap episodes / longest / emitted ids / frames with 2 ids / internal tracks / slot-layer coverage (0.5 s hold) and share of slot rows > 0.75 h off.

| run | change | cov | eps | longest | ids | 2-id | int. | slot cov / err |
|---|---|---|---|---|---|---|---|---|
| s4_base | — | 0.942 | 94 | 1.57 | 25 | 203 | 105 | 0.987 / 2.0 % |
| s4_nofrozen | frozen gate off | 0.995 | 2 | 0.76 | 26 | **482** | 105 | 0.997 / 5.3 % |
| s4_skelage15 | frozen gate after 15 f (not 3) | 0.977 | 18 | 1.57 | 25 | 289 | 105 | 0.990 |
| s4_excl | 3 exclusion cells | 0.946 | 97 | 1.83 | 15 | 62 | 42 | 0.987 / 1.8 % |
| s4_excl_skel15 | excl + skel age 15 | 0.976 | 17 | 1.57 | 15 | 99 | 42 | 0.990 |
| s4_excl_nofrozen | excl + frozen gate off | 0.995 | 2 | 0.76 | 15 | 249 | 42 | 0.997 / 4.7 % |
| s4_tau035 | τ 0.35 | 0.949 | 71 | 1.07 | 31 | 227 | 125 | 0.992 |
| s4_tau035_excl | τ 0.35 + excl | 0.973 | 61 | 0.61 | 17 | 145 | 86 | 0.999 |
| **s4_combo** | **τ 0.35 + excl + skel 15** | **0.995** | **8** | **0.51** | 17 | 213 | 86 | **1.000 / 2.2 %** |
| s4_intermittent | `tracker_intermittent_confirm` | 0.951 | 88 | 1.32 | 27 | 235 | 105 | 0.991 |
| s4_resurrectwarm | resurrect restores warm-up (blind) | 0.942 | 94 | 1.57 | **29** | 216 | 105 | – |
| s4_sticky10 | keep emitting once emitted while tsu ≤ 10 | 0.997 | 1 | 0.76 | 25 | **952** | 105 | – |
| s4_nodisp | displacement gate off | 0.951 | 83 | 1.57 | 22 | 154 | 103 | 0.989 |
| s4_reacq | stale-established re-acquire 2.5 h | 0.949 | 92 | 1.42 | 23 | 193 | 98 | 0.992 |
| s4_fusebox | blob suppressed inside YOLO box (+10 %) | 0.952 | 71 | 1.02 | 18 | 85 | 95 | 0.993 |
| s4_yfm | YOLO-first two-tier assignment | 0.957 | 69 | 1.83 | 25 | 99 | 115 | 0.990 |
| s4_yfm_fuse | yfm + fusebox | 0.947 | 73 | 1.27 | 22 | 85 | 107 | 0.990 |
| s4_excl_struct | excl + fusebox + reacq + resurrect-warm + skel 15 | 0.987 | 9 | 1.07 | **10** | 100 | 44 | 0.995 |
| s4_struct2 | excl_struct + yfm | 0.983 | 12 | 1.07 | 16 | 135 | 43 | 0.995 |
| s4_nosynth | no synthetic (cold-blob) dets | 0.921 | 77 | **4.16** | 8 | 36 | 97 | 0.966 |
| s4_nobridge | no track-local bridge | 0.940 | 95 | 1.57 | 11 | 57 | 92 | 0.986 |
| s4_coastvel0 | KF velocity → 0 on a miss (hold) | 0.931 | 122 | 1.37 | 18 | 131 | 67 | 0.989 |
| s4_keep20 | emit while last skeleton ≤ 20 f (once emitted) | 0.984 | 9 | 1.57 | 25 | **751** | 105 | 0.991 |
| s4_keep20_excl | keep20 + excl | 0.982 | 10 | 1.83 | 15 | 119 | 42 | 0.990 / 1.9 % |

Readings:
- **The frozen-ghost gate is the coverage switch.** It is also the only thing hiding zombies: turning it off without exclusion more than doubles the 2-id frames.
- **Exclusion fixes identity, not coverage.** τ 0.35 + exclusion is the best single knob pair.
- **No tracker-side patch gets ids near 1 alone.** The best is 10 (`s4_excl_struct`).
- **Only the output-side slot layer reaches 1 id at ≥ 99 % coverage** on top of any of these runs, with the slot position > 0.75 h off on 2–5 % of rows.

**Slot 3 experiments** (`hangar-floor` pin; base coverage 0.575):

| run | change | cov | gap eps | longest |
|---|---|---|---|---|
| s3_base | — | 0.575 | 70 | 30.0 s |
| s3_struct | fusebox + reacq + resurrect-warm + skel 15 | 0.579 | 60 | 30.0 s |
| s3_intermittent | intermittent confirm on | 0.739 | 89 | 15.4 s |
| **s3_keep20** | **continuation ≠ birth: stay emitted while the last skeleton is ≤ 20 f old** | **0.938** | 32 | 9.3 s (= cold start) |
| s3_motion | motion feed alive: gamma 2.2 on the motion gray only + var 8 / scale 0.7 | 0.777 | **301** | 11.1 s |
| slot layer on s3_base (offline, relaxed 0.5 s) | — | 0.908 | – | – |
| slot layer on s3_keep20 / s3_motion (relaxed 0.5 s) | — | 0.957 / 0.975 | – | – |

Readings:
- **Slot 3 is a warm-up-starvation scene.** Decoupling continuation from the birth integral (`--keep-confirmed 20`, a stand-in for CONT-4a) recovers **+36 pt** with 0 duplicates and still 3 ids. The residual is mostly the 9.3 s cold start, which the intermittent path addresses.
- **Waking the motion path** (`s3_motion`) recovers +20 pt but creates 301 short gaps. Blob-fed tracks go skeleton-stale and the frozen gate fires (1 245 on-dancer frozen frames): the §2 d mechanism again. This is why CONT-3/4 must ship together.

### 3.5 Config sensitivity — the April project config (`tango-H3_20260403_083419.json`)
- **Reproduced:** on s4 1500–1799, the April config gives **3 real + 1 marginal + 4 ghost** internal tracks. This matches the reported 3+4.
- **What the consumer actually saw:** coverage 88.8 %, 3 gaps (max 0.81 s), **2 emitted ids**, 0 frames with 2 ids. The pinned config on the same window gives 96.1 %, 1 id.
- **Internal ghost counts overstate the output harm.** Most "ghosts" are short-lived tentative tracks that are never emitted.

One-at-a-time swaps toward the pinned config, all on the April YOLO front-end:

| change on the window | real/marg/ghost | coverage | longest | ids |
|---|---|---|---|---|
| April as-is (replay.py ordering: motion_first resets max_age to **60**) | 3/1/4 | 0.888 | 0.81 | 2 |
| app ordering (max_age **15**, BUG-3) | 2/2/9 | 0.919 | 0.81 | 2 |
| τ 0.37 → 0.5 | 1/1/3 | 0.926 | 0.76 | 1 |
| tracking_mode → yolo_first | 2/2/9 | 0.919 | 0.81 | 2 |
| height 148 (0.3–2.5) → 190 (0.52–1.63) | 2/1/1 | 0.958 | 0.41 | 1 |
| MOG2 var default 40 @ 0.46 → var 8 @ 0.7 | 2/1/5 | **0.986** | 0.15 | 2 |
| all of the above | 1/1/4 | 0.940 | 0.61 | 1 |

Explanation:
- **The size gate does most of the ghost-side work.** At 148 px with 0.3–2.5 ratios, the gate admits 44–370 px boxes, i.e. every fixed wall spot (h 85–155). The 190/0.52 pin rejects most of them.
- **The MOG2 operating point does most of the drop-side work.** A sensitive MOG2 at var 8 / scale 0.7 is what lets synthetic dets fill the YOLO gaps.
- **The effects are not additive**: τ 0.5 removes some detections that the motion path would have relayed.
- **Lesson.** Continuity is very sensitive to `person_height_px`/ratios and the MOG2 operating point, and both are calibration outputs. A stale or bulk-copied config moves coverage by ~10 pt on the same footage, which repeats the CORPUS_ANALYSIS §4 finding.
- **Harness caveat.** Replays of motion_first projects silently ran with max_age 60 (BUG-3).

### 3.6 Coast policy (answer to the IR-marker stream)
- **What is emitted today.**
  - The *emitted* centroid already holds on a plain miss at L = 1. What drifts is the **internal** KF prediction, which is used for gating, cost and the bridge query box.
  - Plain-coast frames are rare in the output (25 of 4 983 emitted rows) because a track either gets a blob feed or is hidden within 4–8 frames.
- **`s4_coastvel0` ("hold").** **[M]**
  - Identity improves: tracks 105 → 67, ids 25 → 18, 2-id frames 203 → 131. Track 28's slide onto the floor band is exactly this drift.
  - Coverage drops 1.1 pt, because the frozen-ghost gate reads "KF speed < 0.03 h" and a held track is, by construction, slow.
- **Verdict.**
  - Move to hold-like coasting in association: velocity → 0 within a few misses, and gate around the last measured position with a radius that grows with tsu.
  - **But first** replace the frozen gate's speed test with "no strong evidence for T frames ∧ displacement since the last skeleton < x·h" (CONT-4).
  - Keep the output hold, with an explicit *coasting* state and a time limit (CONT-6). Slot-sim hold error is ~0.4 h median at 0.5 s, and grows past 1 h beyond ~1 s on slot 3.
  - This agrees with the marker stream's synthetic study: hold ≤ velocity decay beyond ~5 frames.

### 3.7 Reproduce
```bash
cd /data/WallDance/application; S=tmp_analysis/audit-2026-10/continuity
.venv/bin/python $S/drive.py build  --scenario tests/scenarios/hangar-aerial.json --start 0 --floor 0.1 --out $S/cache/s4_aerialcfg.pkl
.venv/bin/python $S/drive.py replay --scenario tests/scenarios/hangar-aerial.json --cache $S/cache/s4_aerialcfg.pkl \
    --out $S/runs/s4_base.json --log-dir $S/logs/s4_base [--set exclusion_cells=[[9,2],[13,2],[15,2]] --set confidence=0.35 --const tracker.TRACKER_GHOST_SKELETON_AGE=15]
python3 $S/summary.py s4_base s4_combo        # continuity table;  analyze.py / lifecycle.py / feeds.py / slotsim.py for detail
.venv/bin/python tests/replay.py --scenario tests/scenarios/hangar-aerial.json --score --log-dir $S/logs/direct_aerial   # fidelity anchor
```

---

## 4. Ranked recommendations

Effort: S ≤ 1 day, M ≈ 2–5 days, L > 1 week. "Gate" = the replay evidence that must pass before merge.

**Standing gate for every item: G0.**
- Golden trio unchanged where claimed byte-identical.
- 12-scenario scores on the laptop do not regress (drop/ghost).
- **The new long-span continuity suite (CONT-7)** improves on full whitebg2 s3 + s4, with tango-H s8/H2 s9 as textured guards.

| ID | What | Effort | Risk | Measured / expected gain | Gate |
|---|---|---|---|---|---|
| **CONT-7** | **Measure continuity first.** Add the C1–C10 metrics (port `contmetrics.py` + spatial pseudo-GT into `scoring.py`). Add long-span manifests (`hangar-aerial-full` 0–5088, `hangar-floor-full` 0–9674; laptop: whole slots for texture-duo/wallhang/outdoor-sitter). Add a per-300-frame window pass-rate. Keep the 0.10-floor cache as the standard harness (τ becomes cache-tunable). | S–M | none (tooling) | Makes every item below decidable. Today's PASS hides 5.8 % drops and 25 ids. | n/a |
| **CONT-1** | **Bug fixes.** (i) BUG-2: clamp tsu ≥ 0 in `_apply_fractional_occlusion_aging` (or skip tracks bridged this frame). (ii) BUG-1: resurrect restores warm-up **only if the snapshot had been emitted** (add `was_reported` to `DormantSnapshot`). (iii) BUG-3: `replay._build_processor` applies `tracker_max_age` after `set_tracking_mode` (app order). (iv) Doc fixes: OSC_CONTRACT A.3/A.4 say when an id disappears; fix the `_try_resurrect` docstring and ENGINEERING_RECORD #14 "known-N enables it". | S | low. (ii) done blind re-emits ghosts (+4 ids, measured). (iii) changes replay numbers for motion_first projects only. | Correctness and harness fidelity; small direct continuity gain. | G0; goldens byte-identical for (i)/(iii) on yolo_first pins |
| **CONT-2** | **Ship the knob win, per scene, via calibration.** (a) Exclusion paint for recurring fixed dets (the operator does it; CONT-9 proposes the cells). (b) τ from known-N must be allowed down to 0.30–0.35 when exclusion is set. (c) Add `tracker_intermittent_confirm` and `tracker_ghost_skeleton_age` (new key) to the known-N search space. Re-pin the hangar scenarios with exclusion cells, and fix the hangar-floor pin's gamma 0.53 / var 40. | S | medium: per-scene; skel-age 15 raises 2-id frames (203 → 213 in the combo) | slot 4: **94.2 → 99.5 %**, gaps 94 → 8, longest 0.51 s, ids 25 → 17. slot 3: intermittent switch alone 57.5 → 73.9 % | G0 + no texture scene's ghost rate up > 0.02 |
| **CONT-3** | **Fix the YOLO-vs-motion merge.** (a) A blob is suppressed when its centroid lies inside a YOLO box (+10 %), not when it is < 0.3 h from the box centre. (b) Tiered assignment: YOLO dets first (est → tentative); synthetic dets only for tracks YOLO left unmatched, through the deferred `PendingTrackUpdate` path so a later marker tier slots in. (c) A YOLO det inside an established track's gate is never discarded as DUPLICATE/AMBIGUOUS when that track was only blob-fed this frame: re-feed it instead. | M | medium: changes association everywhere; can expose more frozen-gate drops until CONT-4 lands (measured: fusebox+yfm alone 94.7 %) | ids 25 → 18, 2-id frames 203 → 85 (fusebox). 326 discarded-skeleton frames → expected ~0. Full value only with CONT-4. | G0; `feeds.py`: 0 "YOLO ignored next to a blob-fed track" |
| **CONT-4** | **Decouple continuation from confirmation; fix the frozen gate.** (a) Birth: keep the integral (or the windowed path) for *new* tracks. Continuation: an emitted track stays emitted while it had strong evidence (skeleton; later marker) within `T_cont` ≈ 0.75–1 s *and* has not drifted > x·h from its last strong position. (b) Frozen gate: replace "KF speed < 0.03 h" with "displacement since last strong evidence < 0.3 h ∧ no strong evidence for > T_frozen" and make it apply only to tracks never emitted or drifted off. (c) Then hold-like association coasting (velocity → 0, gate around the last measured position growing with tsu). | M | medium-high: this is exactly where zombies were hidden (frozen off ⇒ 2-id frames ×2.4). Must ship with CONT-3 + exclusion and be evaluated on textured scenes. | Upper bound (frozen off + excl): 99.5 %, 2 gaps. **slot 3 keep-confirmed 20: 57.5 → 93.8 %**, 0 duplicates. slot 4 keep20 + excl: 98.2 %. Hold coast: tracks 105 → 67. **Unsafe without ghost control:** tango-H2 keep20 2-id frames 259 → 927. | G0 + C7 duplicates ≤ today on all scenes |
| **CONT-5** | **Bound tentative tracks.** Cap the position covariance / search radius of unmatched tentative tracks (e.g. raw distance ≤ 1.5 h + growth capped at ~1 h/s). Never let pass-2 give a tentative track a det that a stale *established* track could take within the creation gate. | S | low-medium | Stops the frame-329-type 520 px hijack; directly reduces ghost-born duplicates (28 of 105 births). Expected ids/2-id frames between `s4_excl` and base on unpainted scenes. | G0 |
| **CONT-6** | **Identity-slot output layer (known-N / max-N), OUTPUT-ONLY.** Between `finalize` and OSC (`pipeline.py:836-876`): N stable slots (N from calibration / `max_persons`). A slot binds to a track, follows it, rebinds by proximity to any new or unconfirmed live track (internal `tsu ≤ 3`, hits ≥ 5), coasts (hold) up to `T_coast` ≈ 0.5–1 s with state *coasting*, then *lost*, and keeps its id for the show. Emitted ids = slot ids. Add `/walldance/dancer/state [id, state, age_s]` and share it with the marker stream's proposed `/dancer/source` — one additive, opt-in message, OSC_CONTRACT §D, operator-confirmed. Without N: max-N = calibrated count + 1, and the extra slot is only bindable by an established track. | M–L | medium: N > 1 crossings swap slots (acceptable per field constraints, but must be measured on texture-duo/white-duo on the laptop); contract change | **slot 4: 25 → 1 id, 94.2 → 98.7 % (hold) / 99.7 % (relaxed); slot 3: 57.5 → 90.8 %**; duplicates become invisible by construction. Slot spatial error > 0.75 h on 2–5 % of rows. | G0 + C8 ≥ 0.97 + duo scenarios: slot swaps ≤ today's id switches. **Caveat:** the layer holds whatever the tracker emits. On tango-H2 it would hold wall-stain ghosts, so it must ship with CONT-2/3/5 ghost control and a spatial gate. |
| **CONT-8** | **Motion-first acquisition for small/far figures** (operator's "++"). A separate lane, not a looser global gate: blobs with their own size prior (expected-size range per zone, or "any h ≥ k px" within the stage ROI), persistence ≥ 1 s, frame-diff coherence, outside exclusion. They create *motion-only* tracks that are emitted through the slot layer with state "motion" (exempt from the frozen gate while inside a known-N budget). Needs IR small-far footage to tune; the marker stream's Tier C (constellations) is the same lane with stronger evidence. | M | medium (ghost risk on foliage/texture; the static-cell + exclusion + N-budget bound it) | Unmeasured locally (no IR small-far footage). Today's lane already acquired the slot-4 dancer once (id 28, 2 763 frames). | G0 + a recorded "petits guguss" take with N labels |
| **CONT-9** | **Calibration proposes exclusion cells**: a "recurring fixed detection" pass over the Calib window (boxes at the same spot in ≥ 10 % of frames, low displacement; the `ghost_spots()` logic) is shown as *suggested* cells. The operator accepts; manual-only policy kept. | S | low | Brings `s4_excl`-type gains (internal tracks ÷2.5, ids −40 %) to every venue without a hand search. | G0 |
| **CONT-10** | **Make field logs diagnostic.** In `FRAME_SUMMARY`: the emitted id set, and per track `warmup`, `fss`, `src` (yolo/synthetic/bridge/coast) and an emit/hide reason (`warmup`, `frozen`, `cap`, `slow_dup`). Per-show timestamped folders and rotation for live runs (today they append forever to `./tracking_events.jsonl`; ≈ 1.9 KB/frame ⇒ ~200 MB per 2 h show). | S | none | Makes tomorrow-style operator logs answer "why did id X vanish" without a recording. | n/a |
| **CONT-11** | **Retire or rethink the track-local bridge** once CONT-3/4 land: on slot 4 it adds no coverage (−0.2 pt when off) and creates most zombies (ids 25 → 11 when off). Keep presence/frame-diff tiers only where the slot layer needs a position. | S–M | medium (ENGINEERING_RECORD §4 P3 says the bridge prevents drops; re-measure on the laptop corpus) | ids −56 % on slot 4 | G0 on the 12 scenarios |

**Suggested order:**
1. CONT-7 → CONT-1 → CONT-10. These are cheap and make the rest measurable.
2. CONT-2: an immediate field win via calibration and paint.
3. CONT-3 + CONT-5 + CONT-4, as one replay-gated tracker change set.
4. CONT-6: the operator-facing continuity guarantee. It can be built in parallel because it is output-only.
5. CONT-8 / CONT-11.

---

## 5. Marker-fusion hooks the tracker should expose

**Coordination with the IR-marker stream (`02-ir-markers.md` §3–§4).** I **agree** with its interface:

| Proposal | Status / amendment |
|---|---|
| `update(..., markers=None)`, byte-identical when None/[] | **agree** |
| one `apply_measurement(source, r_mult, integral_credit, window_credit, resets_skeleton_age)` replacing "all-zero conf ⇒ motion" | **agree, and CONT-3 should introduce it first**. The YOLO/synthetic tiering needs the same source tag. |
| hook order: YOLO primacy → marker assoc → unmatched → marker relay → motion bridge → constellation acquisition | **agree**. It is CONT-3's tiering with a marker tier inserted. Synthetic updates must go through the deferred path, as they note. |
| `_frames_since_marker`; the frozen gate uses min(fss, fsm) | **agree, generalised**. CONT-4 re-bases the gate on "frames since *strong* evidence" (skeleton ∨ marker) + displacement, not KF speed. |
| MARKER keep-reason at the crossval gate | **agree** |
| markers = existence / keep-alive at **moderate R (~20–110× YOLO)**, no velocity override | **agree**. This corrects TRACKING_ROBUSTNESS's "tiny R". Extremity markers need the per-slot offset model they describe. |
| `ScaledTrack.source`, `n_markers`, `frames_since_marker` output fields | **agree**. These are exactly the inputs the CONT-6 slot layer needs to choose live/coasting and to prefer marker-fed candidates when rebinding. |

**Flagged interactions:**
- **BUG-2 first.** Their relays set tsu = 0, and occlusion aging then drives it to −1.
- **Warm-up credit for markers must be replay-gated.** Bridge window-credit once incubated ghosts (`tracker.py:2970-2981`).
- **Their optional `/dancer/source` and my `/dancer/state` should be one message**: `[id, src∈{skeleton, marker, motion, coast}, state∈{live, coasting, lost}, age_s]`.
- **Their Tier C (marker-only acquisition) must emit through the slot layer.** That way a marker-only ghost is bounded by N.

**Additional seams from the continuity side:**
1. **Per-track evidence ledger** in place of the single `_frames_since_skeleton`: `frames_since[src]`, `hits[src]`, `last_strong_pos`, `ever_emitted`, `ever_strong`. It is read by the report gate, the frozen gate, resurrect (CONT-1 ii) and the slot layer.
2. **Emission decision as a function with a reason** (`emit_reason(track) → {ok, warmup, frozen, cap, dup}`). It is logged per frame (CONT-10) and is testable in isolation.
3. **The slot layer consumes evidence, not just tracks.** Rebinding prefers marker-fed candidates. Coasting ends early when markers say "gone", and lasts longer when markers keep a track alive without YOLO.
4. **Detect-cache payload gets a `markers` field** next to the YOLO dets (capture hook `pipeline.py:649-651`), so continuity experiments replay markers deterministically (same 0.10-floor approach).
5. **Spatial scoring from markers.** Marker-equipped recordings give C8 ground truth without hand labels. Wire it into CONT-7's spatial metric.

---

## 6. Bugs and doc drift found (details)

| ID | Where | What | Evidence |
|---|---|---|---|
| BUG-1 | `tracker.py:2064-2066, 2170` vs `198`, `548-603`, `3166` | "Resurrected tracks are immediately confirmed": only `hits` is set; `_warmup_score` stays 1.0, so the id re-warms for ~14 frames before it is emitted again | slot 4: all 7 surviving resurrects had wu = 1.0 with 7–25 hits; the real track 29 was never re-emitted |
| BUG-2 | `tracker.py:3039-3049` (with `2967`) | `_apply_fractional_occlusion_aging` does `tsu -= 1` on tracks that a motion bridge reset to 0 in the same frame ⇒ **tsu = −1** | observed in rows (track 8 at f340/344/368, track 1 at f356/360). Effects: negative `time_bonus` in `_compute_dynamic_match_threshold` (`1216-1219`), query box < 1× (`2783-2786`) |
| BUG-3 | `tests/replay.py:240-260` vs `app.py:1118-1128` | the harness applies `tracker_max_age` before `set_tracking_mode`; MOTION_FIRST then resets it to 60 (`tracker.py:924-926`). The app applies it after. | April config window: 4 → 9 ghost tracks, coverage 0.888 → 0.919 when app order is used |
| BUG-4 | `tests/known_n.py:48-53` vs `ENGINEERING_RECORD.md` #14 | `tracker_intermittent_confirm` is "enabled per scene via the known-N search", but the search space does not contain it and nothing else sets it | grep |
| DOC-1 | `docs/OSC_CONTRACT.md` A.3 | "On a plain miss … holds its last value until the track re-acquires or bridges". In reality the id is dropped from `/count` after 8 un-bridged misses (integral) or after 4 skeleton-less slow frames (frozen gate); nothing tells the consumer | §1.3 |
| DOC-2 | `docs/TRACKING_ROBUSTNESS.md` "cascade near its ceiling" | true for *detection* on dark/textured scenes (laptop corpus), but on the hangar footage the tracker/report layer, not detection, creates most drops: an internal track existed on 100 % of slot-4 drop frames | §2 |
| CFG-1 | `tests/scenarios/hangar-floor.json` pin | gamma 0.53 (darkening LUT on a 5/255 scene, flagged in CORPUS_ANALYSIS §4) + var 40 @ 0.99 ⇒ the motion path is inert (0 bridges, 0 synthetic matches in 9 674 frames) | `s3_base` |
| CFG-2 | both hangar pins | `exclusion_cells: []` despite 3 known recurring hangar spots | `s4_excl` |

---

## 7. Open questions for Thomas / the operator

1. **Is N known per show (or per scene)?** Even "max N on stage" would do. That single input unlocks CONT-6: one stable id per dancer, coasting instead of vanishing.
2. **What does TouchDesigner do today when an id disappears from `/walldance/count`?** Does it fade, hold, or jump to the new id? Can the patch accept an additive `/walldance/dancer/state` message (shared with the marker stream's `/source`)? And is a ~0.5–1 s "coasting" hold acceptable visually?
3. **How long is the acceptable output latency at a re-acquisition?** It is 0.75 s minimum today, plus every split re-pays it.
4. **Which ids does the operator see change in the field?** Is "+++ merging tracks" (a) two boxes on one dancer, (b) the id jumping to a new number, or (c) the dancer disappearing while visible? Slot 4 has all three; they have different fixes (CONT-3/6/4).
5. **Do shows paint exclusion cells?** Both golden pins have none, and the hangar has 3 recurring spots.
6. **For the "petits guguss":** typical figure height in px at the far end, background (fixed wall? foliage? projection?), and whether they are dancers to *count* (part of N) or to ignore.

**What to look for in tomorrow's session logs / recordings:**
- **Collect** the **recordings** of the problem moments and the **exact project config JSON** used. The JSONL alone cannot show the emitted set (CONT-10). Live runs append to `application/tracking_events.jsonl`; playback runs write `projects/<p>/sessions/<stamp>_slot<n>/`.
- **Replay** each recording with `drive.py build --floor 0.1` + `replay`, then run `summary.py` / `analyze.py`, with N eyeballed via `sheet.py` contact sheets. That yields the §3.2 row for the field footage within an hour.
- **JSONL signatures, per minute:**
  - `NEW_TRACK` (id churn; slot 4 = 24/min);
  - `TRACK_MERGED mode=takeover` (duplicate pairs; slot 4 = 7/min);
  - `GHOST_FROZEN_SUPPRESSED` (frozen gate firing; slot 4 = 130/min — 42 % on the real dancer, 50 % on zombies);
  - `DUPLICATE_IGNORED`/`AMBIGUOUS_IGNORED` in the same frame as `MOTION_SYNTHETIC_DET` (YOLO-vs-motion, §2 d);
  - `RESURRECT` followed by no emission;
  - `MAX_PERSONS_CAPPED`;
  - negative `t_miss` in `FRAME_SUMMARY.track_states` (BUG-2);
  - long runs of `n_detections > 0` while `track_states` holds an established track with growing `t_miss` (warm-up starvation, slot-3 type).
- **Config sanity:**
  - `person_height_px` and ratios vs the real dancer height;
  - `confidence`;
  - `gamma < 1` on a dark IR scene (it kills the motion path);
  - `mog2_var_threshold` / `mog2_scale`;
  - `tracking_mode` / `tracker_max_age`;
  - `exclusion_cells`;
  - `tracker_intermittent_confirm`.
