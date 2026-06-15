"""Unit tests for the fixed-lag / RTS output smoother (Track X, X-2).

Pure-function tests over synthetic trajectories.  No GPU / model / recordings —
the smoother is output-only and stateless across runs given the same input.

Covers the review-mandated checks (docs/TRACK_X_SMOOTHER.md §10):
  * cascaded-lag guard  — measured latency ≈ L, not > L+5 (catches EMA leak);
  * jitter reduction    — RMS residual vs raw drops;
  * retroactive gap     — a known gap ≤ L reconstructs to < 2 px RMS;
  * release latency      — exactly L, with first-L-frames birth silence;
  * flush-on-death      — a dying track's buffered tail is emitted, not dropped;
  * box-size weighting   — bridged frames down-weighted via the noise ladder.
"""

import numpy as np

from core.output_smoother import OutputSmoother, SmootherInput


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _feed(sm, measurements, *, real_flags=None, fss=None, wh=(40.0, 80.0), tid=1):
    """Feed measurements (list of (x, y)) one per frame.

    Returns ``(released, produced)`` where ``released[frame] -> centroid`` keyed
    by the released frame index, and ``produced[wallstep] -> [centroids]`` keyed
    by the step at which the release was produced.
    """
    T = len(measurements)
    if real_flags is None:
        real_flags = [True] * T
    if fss is None:
        fss = [0 if r else 5 for r in real_flags]
    released, produced = {}, {}
    for s in range(T):
        inp = SmootherInput(
            track_id=tid,
            centroid=np.asarray(measurements[s], dtype=float),
            wh=np.asarray(wh, dtype=float),
            is_real_skeleton=bool(real_flags[s]),
            frames_since_skeleton=int(fss[s]),
        )
        for o in sm.process([inp]):
            released[o.step] = np.asarray(o.centroid, dtype=float)
            produced.setdefault(s, []).append(np.asarray(o.centroid, dtype=float))
    return released, produced


def _best_lag(produced_x, true_x, max_d):
    """Integer delay d ≥ 0 maximizing corr(produced[s], true[s-d]).

    produced[s] ≈ true[s-L] for a phase-accurate fixed-lag smoother, so the best
    d recovers the *total* measured latency (structural L + any phase leak)."""
    steps = sorted(produced_x)
    best_d, best_c = 0, -np.inf
    for d in range(0, max_d + 1):
        a, b = [], []
        for s in steps:
            if s - d < 0:
                continue
            a.append(produced_x[s])
            b.append(true_x[s - d])
        if len(a) < 8:
            continue
        a = np.asarray(a)
        b = np.asarray(b)
        if a.std() < 1e-9 or b.std() < 1e-9:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        if c > best_c:
            best_c, best_d = c, d
    return best_d


# --------------------------------------------------------------------------- #
# cascaded-lag guard                                                          #
# --------------------------------------------------------------------------- #
def test_cascaded_lag_guard_latency_near_L():
    """Cascaded-lag guard (review-mandated): 50-px / 1-Hz sinusoid @ 30 fps, L=30.

    The release is *structurally* exactly L frames late (proven in
    ``test_release_latency_*``).  The cascade regression this guards against is a
    causal EMA leaking into the lagged path, which would add the EMA group delay
    *on top of* L → total latency > L+5.  So we measure the **extra** phase lag
    the smoother adds: the released estimate of frame k should track true[k]
    (extra lag ≈ 0), not true[k - g] for g > 0.

    (The spec's exact 1 Hz @ 30 fps makes the sinusoid period = L, so a raw
    cross-correlation is ambiguous mod L; restricting the search to small d
    isolates the extra-lag signal the guard actually cares about.)"""
    fps, freq, amp, T, L = 30.0, 1.0, 50.0, 240, 30
    true = [(amp * np.sin(2 * np.pi * freq * t / fps), 0.0) for t in range(T)]
    sm = OutputSmoother(lag=L)
    released, _ = _feed(sm, true)  # noise-free → pure phase measurement
    true_x = [p[0] for p in true]

    # released[k] = the smoothed estimate of frame k.  A phase-accurate fixed-lag
    # smoother gives released[k] ≈ true[k] → extra lag 0; an EMA leak of g frames
    # shifts it to true[k-g].  Total measured latency = L + extra; guard: ≤ L+5.
    rel_x = {k: released[k][0] for k in released if L <= k < T - 2}
    extra = _best_lag(rel_x, true_x, max_d=8)
    assert extra <= 5, (
        f"smoother adds {extra} frames of extra lag → total latency > L+5 "
        f"(EMA leaked into the lagged tap)")

    # Direct phase check: released frame k tracks true[k] (no extra lag).
    steady = [k for k in released if k >= L and k < T]
    err = np.array([released[k][0] - true_x[k] for k in steady])
    rms = float(np.sqrt(np.mean(err ** 2)))
    assert rms < 5.0, f"phase RMS {rms:.2f}px too high for a {amp}px sinusoid"


# --------------------------------------------------------------------------- #
# jitter reduction                                                            #
# --------------------------------------------------------------------------- #
def test_rts_reduces_jitter_vs_raw():
    rng = np.random.default_rng(0)
    T, L = 180, 12
    true = [(0.5 * t, 60.0 + 20.0 * np.sin(2 * np.pi * t / 90.0)) for t in range(T)]
    noise = rng.normal(0.0, 3.0, size=(T, 2))
    meas = [(true[t][0] + noise[t, 0], true[t][1] + noise[t, 1]) for t in range(T)]

    sm = OutputSmoother(lag=L, meas_var=9.0)
    released, _ = _feed(sm, meas)
    steady = [k for k in released if k >= L and k < T]

    out_err = np.array([np.linalg.norm(released[k] - np.array(true[k])) for k in steady])
    raw_err = np.array([np.linalg.norm(np.array(meas[k]) - np.array(true[k])) for k in steady])
    assert out_err.mean() < 0.8 * raw_err.mean(), (
        f"smoothed RMS {out_err.mean():.2f} not < 0.8 * raw {raw_err.mean():.2f}")


# --------------------------------------------------------------------------- #
# retroactive bridge correction (falls out of RTS)                            #
# --------------------------------------------------------------------------- #
def test_retroactive_gap_reconstruction_under_2px():
    rng = np.random.default_rng(1)
    T, L = 130, 20
    g0, g1 = 55, 66  # bridged gap [55..66], length 12 ≤ L
    true = [(1.0 * t, 120.0 - 0.5 * t) for t in range(T)]  # linear (CV-friendly)

    real_flags = [not (g0 <= t <= g1) for t in range(T)]
    fss, c = [], 0
    for t in range(T):
        c = 0 if real_flags[t] else c + 1
        fss.append(c)

    meas = []
    for t in range(T):
        if real_flags[t]:
            meas.append((true[t][0] + rng.normal(0, 0.5),
                         true[t][1] + rng.normal(0, 0.5)))
        else:  # bridged: zero-mean noisy (a coasting bridge — drift, not bias;
               #         a constant bias is unremovable by any smoother)
            meas.append((true[t][0] + rng.normal(0, 4.0),
                         true[t][1] + rng.normal(0, 4.0)))

    sm = OutputSmoother(lag=L)
    released, _ = _feed(sm, meas, real_flags=real_flags, fss=fss)

    gap_ks = [k for k in range(g0, g1 + 1) if k in released]
    assert len(gap_ks) == (g1 - g0 + 1)
    out_err = np.array([np.linalg.norm(released[k] - np.array(true[k])) for k in gap_ks])
    raw_err = np.array([np.linalg.norm(np.array(meas[k]) - np.array(true[k])) for k in gap_ks])
    rms = float(np.sqrt(np.mean(out_err ** 2)))
    assert rms < 2.0, f"gap reconstruction RMS {rms:.2f}px ≥ 2px"
    assert out_err.mean() < raw_err.mean()  # better than the raw bridge


# --------------------------------------------------------------------------- #
# release mechanics                                                           #
# --------------------------------------------------------------------------- #
def test_release_latency_exactly_L_with_birth_silence():
    T, L = 40, 7
    sm = OutputSmoother(lag=L)
    seen = []
    for s in range(T):
        inp = SmootherInput(1, np.array([2.0 * s, 5.0]), np.array([10.0, 20.0]),
                            True, 0)
        for o in sm.process([inp]):
            seen.append((s, o.step))

    assert all(s >= L for s, _ in seen), "released before the window filled (birth silence)"
    assert seen[0] == (L, 0), "first release should be frame 0 at wallstep L"
    for s, frame in seen:
        assert frame == s - L, f"latency != L at wallstep {s}: released frame {frame}"


def test_flush_on_death_emits_tail():
    L = 5
    sm = OutputSmoother(lag=L)
    for s in range(10):  # track present frames 0..9
        sm.process([SmootherInput(1, np.array([1.0 * s, 0.0]),
                                   np.array([10.0, 20.0]), True, 0)])
    flushed = []
    for _ in range(10):  # track gone → flush the buffered tail
        outs = sm.process([])
        flushed += [o.step for o in outs]
        if not sm._tracks:
            break
    assert flushed == [5, 6, 7, 8, 9], f"flushed tail {flushed} != [5..9]"
    assert not sm._tracks, "dead track not pruned after flush"


def test_box_size_weighted_mean_downweights_bridged():
    L = 3
    sm = OutputSmoother(lag=L)
    frames = [(True, (100.0, 100.0)), (True, (100.0, 100.0)),
              (True, (100.0, 100.0)), (False, (200.0, 200.0)),
              (True, (100.0, 100.0))]
    rel = {}
    for s, (real, wh) in enumerate(frames):
        fss = 0 if real else 1  # noise_mult 1.5 → weight 1/1.5
        for o in sm.process([SmootherInput(1, np.array([0.0, 0.0]),
                                            np.array(wh), real, fss)]):
            rel[o.step] = o.wh
    # frame 0 released at wallstep L=3 over window [0..3]: three real (w=1, size 100)
    # + one bridged (w=1/1.5, size 200).
    w_b = 1.0 / 1.5
    expect = (3 * 100.0 + w_b * 200.0) / (3 + w_b)
    np.testing.assert_allclose(rel[0], [expect, expect], rtol=1e-9)


def test_two_tracks_independent_and_idset_preserved():
    """X-2: lagged id set == causal id set (no case-2 suppression yet)."""
    L = 4
    sm = OutputSmoother(lag=L)
    released_ids = set()
    for s in range(20):
        inps = [
            SmootherInput(1, np.array([1.0 * s, 0.0]), np.array([10.0, 20.0]), True, 0),
            SmootherInput(2, np.array([0.0, 2.0 * s]), np.array([12.0, 24.0]), True, 0),
        ]
        for o in sm.process(inps):
            released_ids.add(o.track_id)
    assert released_ids == {1, 2}


def test_smoothed_velocity_tracks_true_velocity():
    """The released velocity comes from the RTS state (de-jittered), not raw."""
    rng = np.random.default_rng(3)
    T, L, vx = 120, 8, 2.0
    vels = []
    sm = OutputSmoother(lag=L, meas_var=9.0)
    for s in range(T):
        meas = np.array([vx * s + rng.normal(0, 3.0), 5.0 + rng.normal(0, 3.0)])
        for o in sm.process([SmootherInput(1, meas, np.array([10.0, 20.0]), True, 0)]):
            if o.step >= L:  # steady
                vels.append(o.velocity)
    vels = np.array(vels)
    # smoothed vx ≈ true 2.0, vy ≈ 0; well inside a loose band despite 3px noise
    assert abs(vels[:, 0].mean() - vx) < 0.4
    assert abs(vels[:, 1].mean()) < 0.4
    assert np.all(np.isfinite(vels))


def test_noncontiguous_reappearance_resets_filter():
    """A non-contiguous reporting gap (same id) starts a FRESH window — never
    stitch a post-gap measurement onto stale nodes as if dt=1 (review finding)."""
    L = 4
    sm = OutputSmoother(lag=L)
    for s in range(10):  # continuous frames 0..9
        sm.process([SmootherInput(1, np.array([1.0 * s, 0.0]),
                                  np.array([10.0, 20.0]), True, 0)])
    old_filter = sm._tracks[1]
    sm.process([])           # absent (step 10) → flush tail
    sm.process([])           # absent (step 11)
    # reappear at a distant position (step 12, last_step was 9 → gap of 3)
    sm.process([SmootherInput(1, np.array([100.0, 0.0]),
                              np.array([10.0, 20.0]), True, 0)])
    new_filter = sm._tracks.get(1)
    assert new_filter is not None and new_filter is not old_filter, "filter not reset"
    assert len(new_filter.nodes) == 1, "fresh window should hold only the new frame"


def test_lagged_keypoints_stay_coherent_with_centroid():
    """Pipeline coherence (review finding): the released skeleton is rigidly
    translated by the centroid correction, so keypoints keep their offset from
    the smoothed centroid (no centroid/keypoint drift on the lagged tap)."""
    from types import SimpleNamespace
    from pipeline import FrameProcessor, ScaledTrack

    L = 3
    fake = SimpleNamespace(
        _output_smoother=None,
        tracker=SimpleNamespace(max_age=45),
        settings=SimpleNamespace(output_lagged_suppress=True,
                                 output_lagged_case2_min_bridge=8))
    offset = np.array([5.0, -3.0])
    releases = []
    for s in range(14):
        c = np.array([2.0 * s, 0.0])                 # moving centroid
        kp = np.tile(c + offset, (17, 1))            # every keypoint at c+offset
        st = ScaledTrack(
            track_id=1, keypoints=kp, confidence=np.ones(17),
            bbox=np.array([c[0] - 5, c[1] - 10, 10.0, 20.0]),
            history=[], velocity=np.zeros(2), smoothed_centroid=c.copy(),
            centroid_raw=c.copy(), frames_since_skeleton=0)
        releases += FrameProcessor._run_output_smoother(fake, [st], L)
    assert releases
    for r in releases:
        rel_off = np.asarray(r.keypoints) - np.asarray(r.smoothed_centroid)
        np.testing.assert_allclose(rel_off, np.tile(offset, (17, 1)), atol=1e-6)


# --------------------------------------------------------------------------- #
# case-2 flying-ghost suppression (X-3, §5.1: K=2, conf floor 0.6, recency L/3)  #
# --------------------------------------------------------------------------- #
def _feed_case2(frames, L, suppress=True, min_bridge=1):
    """frames = list of (is_real, max_conf) per frame for one track id; returns
    the set of released frame indices that were EMITTED (not suppressed).

    ``fss`` is a realistic running bridge counter (reset on a real frame, +1 on a
    bridged one).  ``min_bridge=1`` isolates the K/recency/floor logic (any bridge
    is a candidate); the production default (8) gates on a sustained bridge."""
    sm = OutputSmoother(lag=L, case2_min_bridge=min_bridge)
    emitted = set()
    fss = 0
    for s, (is_real, conf) in enumerate(frames):
        fss = 0 if is_real else fss + 1
        inp = SmootherInput(1, np.array([float(s), 0.0]), np.array([10.0, 20.0]),
                            bool(is_real), fss, float(conf))
        for o in sm.process([inp], lag=L, suppress=suppress):
            emitted.add(o.step)
    return emitted, len(frames)


def test_case2_suppresses_never_reacquiring_ghost():
    """A bridged track that never re-acquires a solid skeleton is dropped from
    the lagged tap (only the genuine real frames survive)."""
    L = 6
    frames = [(True, 0.9)] * 3 + [(False, 0.0)] * 20
    emitted, _ = _feed_case2(frames, L, suppress=True)
    assert sorted(emitted) == [0, 1, 2], f"ghost not suppressed: kept {sorted(emitted)}"


def test_case2_keeps_reacquiring_aerial():
    """A 1-in-3 re-acquiring aerial (≥2 confident hits per window) is KEPT."""
    L = 9
    frames = [((s % 3 == 0), 0.9 if s % 3 == 0 else 0.0) for s in range(40)]
    emitted, T = _feed_case2(frames, L, suppress=True)
    assert emitted == set(range(0, T - L)), \
        f"real aerial frames dropped: {sorted(set(range(0, T - L)) - emitted)}"


def test_case2_disabled_keeps_id_for_id():
    """suppress=False → lagged id set == causal id set (no drops)."""
    L = 6
    frames = [(True, 0.9)] * 3 + [(False, 0.0)] * 20
    emitted, T = _feed_case2(frames, L, suppress=False)
    assert emitted == set(range(0, T - L))


def test_case2_only_solid_reacquisitions_count():
    """A marginal flicker (conf below the 0.6 floor) is NOT 'confident' — a track
    sustained only by sub-floor flickers is still suppressed."""
    L = 6
    # real warmup, then bridged with weak flickers (conf 0.4 < 0.6 floor)
    frames = [(True, 0.9)] * 2 + [(False, 0.0), (True, 0.4)] * 12
    emitted, _ = _feed_case2(frames, L, suppress=True)
    # the genuine real warmup (0,1) survive; the weak-flicker bridged region is
    # suppressed where the look-ahead lacks ≥2 SOLID (>0.6) hits.
    assert 0 in emitted and 1 in emitted
    assert len(emitted) < 2 + (len(frames) - L), "weak flickers wrongly rescued the ghost"


def test_case2_recency_keeps_a_recent_solid_hit():
    """Recency clause: a single SOLID re-acquisition in the last ⌈L/3⌉ steps
    keeps the released frame even below K; a stale lone hit does not."""
    L = 9  # recency window = max(1, 9//3) = 3

    def r0_emitted(hit_step):
        sm = OutputSmoother(lag=L, case2_min_bridge=1)  # isolate recency logic
        out = set()
        fss = 0
        for s in range(10):  # R=0 releases at step 9 (N=9), look-ahead steps 1..9
            is_real = (s == hit_step)
            fss = 0 if is_real else fss + 1
            conf = 0.9 if is_real else 0.0
            for o in sm.process([SmootherInput(1, np.array([float(s), 0.0]),
                                 np.array([10.0, 20.0]), is_real,
                                 fss, conf)], lag=L, suppress=True):
                out.add(o.step)
        return out

    assert 0 in r0_emitted(9), "recent solid hit (step 9) should keep R=0"
    assert 0 in r0_emitted(7), "recent solid hit (step 7 > N-3) should keep R=0"
    assert 0 not in r0_emitted(2), "stale lone hit (step 2) should NOT keep R=0"


def test_case2_sustained_gate_exempts_brief_low_conf_drops():
    """The hangar-aerial fix: a real aerial with BRIEF drops that re-acquire at
    LOW conf (the IR regime, below the 0.6 'confident' floor) is fully KEPT — its
    drops never reach the sustained-bridge depth, so suppression never fires."""
    L = 6
    # 3 real / 3 bridged repeating: drops are ≤3 frames (fss ≤ 3 < min_bridge=8),
    # re-acquisitions are conf 0.4 (< 0.6 floor → NOT 'confident').
    frames = [((s % 6) < 3, 0.4 if (s % 6) < 3 else 0.0) for s in range(48)]
    emitted, T = _feed_case2(frames, L, suppress=True, min_bridge=8)
    assert emitted == set(range(0, T - L)), \
        f"brief real-dancer drops wrongly suppressed: {sorted(set(range(0, T-L)) - emitted)}"


def test_case2_sustained_gate_still_suppresses_long_ghost():
    """A never-re-acquiring ghost bridges for a long unbroken run (fss grows past
    the gate) → still suppressed under the sustained-bridge gate."""
    L = 6
    frames = [(True, 0.9)] * 2 + [(False, 0.0)] * 30
    emitted, T = _feed_case2(frames, L, suppress=True, min_bridge=8)
    assert 0 in emitted and 1 in emitted          # genuine real frames kept
    assert len(emitted) < (T - L), "sustained ghost not suppressed"
