"""Unit tests for the takeover duplicate merge (ROADMAP §4.2 Phase 2 ②).

Exercises DancerTracker._merge_takeover_duplicates with real DancerTrack
objects driven frame-by-frame — no GPU, no frames.  Locks down:

* a never-co-fed close pair, fed one-sided, merges after the windowed
  qualifying count (zombie absorbed, keeper inherits the live state when the
  absorbed side held the detection stream);
* the co-fed veto protects real pairs (embracing/crossing dancers);
* distance gate, window requirement, and the established-only restriction.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import core.config as config  # noqa: E402
from core.tracker import DancerTracker, DancerTrack  # noqa: E402


def mk_track(x, y, hits):
    kpts = np.tile([float(x), float(y)], (17, 1))
    conf = np.full(17, 0.9)
    bbox = (x - 30.0, y - 90.0, 60.0, 180.0)
    tr = DancerTrack(kpts, conf, bbox)
    tr.hits = hits
    return tr


def mk_tracker(*tracks):
    t = DancerTracker()
    t.logger.start_session(tempfile.mkdtemp())
    t.set_person_height(150)  # prox gate = 0.6 * 150 = 90 px
    t.tracks = list(tracks)
    return t


def step(t, fed_map):
    """One merge evaluation with per-track fed/stale state.

    fed_map: track -> True (skeleton-fed this frame) / False (stale).
    """
    for tr in t.tracks:
        tr._frames_since_skeleton = 0 if fed_map.get(tr, False) else 5
    t._merge_takeover_duplicates()


def ids(t):
    return {tr.track_id for tr in t.tracks}


def test_zombie_pair_merges_after_windowed_hits():
    zombie = mk_track(100, 100, hits=80)   # stale, more hits (older id)
    fresh = mk_track(150, 100, hits=20)    # fed, riding the dancer
    t = mk_tracker(zombie, fresh)
    for _ in range(config.TRACKER_DUP_TAKEOVER_HITS - 1):
        step(t, {fresh: True})
        assert ids(t) == {zombie.track_id, fresh.track_id}
    step(t, {fresh: True})  # 4th qualifying frame fires
    assert ids(t) == {zombie.track_id}


def test_keeper_inherits_live_state_from_fed_victim():
    zombie = mk_track(100, 100, hits=80)
    fresh = mk_track(150, 100, hits=20)
    fresh._warmup_score = config.TRACK_WARMUP_THRESHOLD + 3
    zombie._warmup_score = 2.0  # decayed during the stale stretch
    t = mk_tracker(zombie, fresh)
    for _ in range(config.TRACKER_DUP_TAKEOVER_HITS):
        step(t, {fresh: True})
    assert ids(t) == {zombie.track_id}
    # keeper jumped onto the subject with the victim's warmth
    assert np.allclose(zombie.get_centroid(), [150, 100])
    assert zombie._warmup_score >= config.TRACK_WARMUP_THRESHOLD
    assert zombie._frames_since_skeleton == 0


def test_stale_victim_is_not_inherited():
    real = mk_track(100, 100, hits=80)     # fed (the dancer's track)
    zombie = mk_track(150, 100, hits=20)   # stale young duplicate
    t = mk_tracker(real, zombie)
    for _ in range(config.TRACKER_DUP_TAKEOVER_HITS):
        step(t, {real: True})
    assert ids(t) == {real.track_id}
    # keeper kept its own state (victim was not holding the stream)
    assert np.allclose(real.get_centroid(), [100, 100])


def test_cofed_veto_protects_real_pairs():
    a = mk_track(100, 100, hits=80)
    b = mk_track(150, 100, hits=20)
    t = mk_tracker(a, b)
    # The pair co-feeds first (two real dancers seen simultaneously) …
    for _ in range(config.TRACKER_DUP_COFED_VETO):
        step(t, {a: True, b: True})
    # … so later one-sided stretches (occlusion/embrace) never merge them.
    for _ in range(20):
        step(t, {a: True})
    assert ids(t) == {a.track_id, b.track_id}


def test_distance_gate():
    a = mk_track(100, 100, hits=80)
    b = mk_track(300, 100, hits=20)  # 200 px > 90 px gate
    t = mk_tracker(a, b)
    for _ in range(20):
        step(t, {b: True})
    assert ids(t) == {a.track_id, b.track_id}


def test_window_requires_enough_qualifying_frames():
    a = mk_track(100, 100, hits=80)
    b = mk_track(150, 100, hits=20)
    t = mk_tracker(a, b)
    # 3 qualifying frames, then the pair separates: window drains, no merge.
    for _ in range(config.TRACKER_DUP_TAKEOVER_HITS - 1):
        step(t, {b: True})
    b.kf.x[0] = 400.0
    for _ in range(config.TRACKER_DUP_TAKEOVER_WINDOW):
        step(t, {b: True})
    assert ids(t) == {a.track_id, b.track_id}
    # And resuming proximity must re-earn the full count.
    b.kf.x[0] = 150.0
    for _ in range(config.TRACKER_DUP_TAKEOVER_HITS - 1):
        step(t, {b: True})
    assert ids(t) == {a.track_id, b.track_id}


def test_tentative_tracks_are_left_alone():
    a = mk_track(100, 100, hits=config.TRACKER_ESTABLISHED_FRAMES - 1)
    b = mk_track(150, 100, hits=config.TRACKER_ESTABLISHED_FRAMES - 1)
    t = mk_tracker(a, b)
    for _ in range(20):
        step(t, {b: True})
    assert ids(t) == {a.track_id, b.track_id}


def test_both_fed_frames_do_not_qualify():
    a = mk_track(100, 100, hits=80)
    b = mk_track(150, 100, hits=20)
    t = mk_tracker(a, b)
    # both fed = co-fed accrual, not takeover evidence; veto then locks in
    for _ in range(20):
        step(t, {a: True, b: True})
    assert ids(t) == {a.track_id, b.track_id}
