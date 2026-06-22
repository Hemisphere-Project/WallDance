"""Unit tests for warmup confirmation (ROADMAP bug #14, v5 synthesis).

The shipped +1.0/match −0.8/miss integral stays the primary mechanism — four
replay-measured variants (2026-06-10) proved its hysteresis is load-bearing:
it rides through short detection dips AND self-revokes after false-positive
bursts.  Its one structural gap: below ~45% sustained detection rate it can
never confirm (corpus: an aerial dancer detected 1 frame in 3, permanently
unreported).  A second, LIVE path closes exactly that gap: >=12 YOLO credits
over the last 40 frames AND real travel (history span >= 0.5x own height) —
bridge matches feed the integral but earn no windowed credit, and fixed
flicker spots fail the travel test.

Drives a real ``DancerTrack`` through predict()/update() cycles — no GPU.
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import core.config as config  # noqa: E402
from core.tracker import DancerTrack  # noqa: E402


def _mk_track(x: float = 100.0):
    # birth at the same spot the first update will measure (as in production:
    # a track is constructed from its first real detection)
    kpts = np.tile([x + 30.0, 190.0], (17, 1)).astype(float)
    conf = np.full(17, 0.8)
    return DancerTrack(kpts, conf, bbox=np.array([x, 100.0, 60.0, 180.0]))


def _step(track, hit: bool, x: float):
    track.predict()
    if hit:
        kpts = np.tile([x + 30.0, 190.0], (17, 1)).astype(float)
        conf = np.full(17, 0.8)
        track.update(kpts, conf, np.array([x, 100.0, 60.0, 180.0]))


def _bridge_step(track):
    """Emulate the tracker's bridge-match site: integral credit, no window
    credit, time_since_update kept at 0."""
    track.predict()
    track.time_since_update = 0
    track._warmup_score = min(
        track._warmup_score + config.MOTION_BRIDGE_WARMUP_INCREMENT,
        config.TRACK_WARMUP_THRESHOLD + 5.0)
    track._warmup_history.append(0.0)


def _frames_to_confirm(pattern, *, speed=0.0, max_frames=200):
    """pattern(i) -> bool hit; speed = px/frame x-drift of the subject."""
    t = _mk_track()
    for i in range(max_frames):
        if t.warmup_confirmed:
            return i
        _step(t, pattern(i), x=100.0 + speed * i)
    return None


def test_solid_track_confirms_via_integral_even_static():
    # The static-but-solid case (balcony sitter): integral path, no travel
    # needed; latency = old behavior (~15 consecutive matches).
    n = _frames_to_confirm(lambda i: True, speed=0.0)
    assert n is not None and n <= config.TRACK_WARMUP_THRESHOLD + 1


def test_one_in_three_moving_confirms_within_slow_window():
    # The corpus regime the integral can NEVER confirm (~33% duty, aerial):
    # the live intermittent path with travel closes it.
    n = _frames_to_confirm(lambda i: i % 3 == 0, speed=15.0)
    assert n is not None and n <= config.TRACK_WARMUP_SLOW_WINDOW + 5


def test_one_in_three_static_never_confirms():
    # Same duty at a FIXED spot = flickering texture ghost -> never confirmed
    # (integral can't reach threshold; travel test fails).
    n = _frames_to_confirm(lambda i: i % 3 == 0, speed=0.0)
    assert n is None


def test_sparse_flicker_never_confirms_even_moving():
    # 20% duty stays below the windowed floor (12/40 = 30%) and the integral.
    n = _frames_to_confirm(lambda i: i % 5 == 0, speed=15.0)
    assert n is None


def test_fp_burst_self_revokes():
    # A solid false-positive burst at a fixed spot confirms via the integral
    # (old behavior) but the score decay un-confirms it shortly after the
    # burst ends — the hysteresis that keeps burst ghosts from being
    # permanently reported (replay-measured: latching here exploded ghosts).
    t = _mk_track()
    for i in range(16):
        _step(t, True, x=100.0)
    assert t.warmup_confirmed
    for i in range(10):
        _step(t, False, x=100.0)
    assert not t.warmup_confirmed


def test_bridge_keeps_confirmed_track_alive():
    # A confirmed dancer carried by the motion bridge through a YOLO gap
    # stays confirmed (score replenished by bridge increments).
    t = _mk_track()
    for i in range(20):
        _step(t, True, x=100.0)
    assert t.warmup_confirmed
    for i in range(60):
        _bridge_step(t)
    assert t.warmup_confirmed


def test_bridge_only_track_confirms_slowly_via_integral():
    # Old (shipped) behavior preserved: a never-YOLO track sustained purely
    # by bridge blobs reaches the threshold in ~(15-1)/0.4 = 35 frames.
    t = _mk_track()
    frames = 0
    while not t.warmup_confirmed and frames < 100:
        _bridge_step(t)
        frames += 1
    assert t.warmup_confirmed
    assert frames > 25  # much slower than YOLO matches


def test_slow_path_duplicate_suppressed_at_report():
    # A slow-path-only track riding an integral-confirmed track is not
    # reported; the same track far away is (real second dancer).
    import tempfile
    from core.tracker import DancerTracker

    def _confirmed_ids(tracks):
        tk = DancerTracker()
        tk.logger.start_session(tempfile.mkdtemp())
        tk._person_height_px = 180
        tk.frame_count = 10_000
        tk.intermittent_confirm = True   # the per-scene switch (default off)
        tk.tracks = tracks
        return {t.track_id for t in tk._collect_confirmed_tracks()}

    def _real_track(x, *, solid):
        t = _mk_track(x)
        for i in range(40):
            # solid -> integral path; else 1-in-3 moving -> slow path only
            _step(t, True if solid else i % 3 == 0, x=x + 4.0 * i)
        assert t.warmup_confirmed
        if solid:
            assert t._warmup_score >= config.TRACK_WARMUP_THRESHOLD
        else:
            assert t._warmup_score < config.TRACK_WARMUP_THRESHOLD
        return t

    solid = _real_track(100.0, solid=True)
    dup = _real_track(140.0, solid=False)      # ends ~40px from solid (<0.7h)
    far = _real_track(900.0, solid=False)      # well beyond separation
    ids = _confirmed_ids([solid, dup, far])
    assert solid.track_id in ids
    assert far.track_id in ids
    assert dup.track_id not in ids


def test_slow_path_default_off_at_report():
    # With the per-scene switch at its default (off), a slow-path-only track
    # is NOT reported — the report boundary behaves exactly like the shipped
    # integral-only warmup.
    import tempfile
    from core.tracker import DancerTracker

    t = _mk_track()
    for i in range(60):
        _step(t, i % 3 == 0, x=100.0 + 15.0 * i)
    assert t.warmup_confirmed                       # slow path evaluates true
    assert t._warmup_score < config.TRACK_WARMUP_THRESHOLD

    tk = DancerTracker()
    tk.logger.start_session(tempfile.mkdtemp())
    tk._person_height_px = 180
    tk.frame_count = 10_000
    tk.tracks = [t]
    assert tk.intermittent_confirm is False          # default off
    assert tk._collect_confirmed_tracks() == []      # ...so not reported
    tk.intermittent_confirm = True
    assert [x.track_id for x in tk._collect_confirmed_tracks()] == [t.track_id]


def test_intermittent_path_is_live_not_latched():
    # A moving intermittent dancer confirms via the windowed path; if the
    # evidence then stops entirely (and no bridge), confirmation drains away
    # with the window.
    t = _mk_track()
    i = 0
    while not t.warmup_confirmed and i < 100:
        _step(t, i % 3 == 0, x=100.0 + 15.0 * i)
        i += 1
    assert t.warmup_confirmed
    for j in range(config.TRACK_WARMUP_SLOW_WINDOW + 1):
        _step(t, False, x=100.0 + 15.0 * (i + j))
    assert not t.warmup_confirmed
