"""Unit tests for the MAX_PERSONS report cap (ROADMAP §4.2 Phase 2 ③, bug 12c).

The cap lives at the report boundary (`_collect_confirmed_tracks`), the same
place as the frozen-ghost and slow-path duplicate gates: internal tracks are
never touched, only what is exposed to OSC/overlay.  Ranking is top-K by
``hits`` (cumulative real matches — stable frame-to-frame), older track id on
ties, original list order preserved.

Drives real ``DancerTrack`` objects through predict()/update() — no GPU.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

from tracker import DancerTrack, DancerTracker  # noqa: E402


def _mk_track(x: float, n_matches: int):
    """A confirmed track at x with `hits == n_matches` (all solid matches)."""
    kpts = np.tile([x + 30.0, 190.0], (17, 1)).astype(float)
    conf = np.full(17, 0.8)
    t = DancerTrack(kpts, conf, bbox=np.array([x, 100.0, 60.0, 180.0]))
    for _ in range(n_matches - 1):  # construction counts as the first hit
        t.predict()
        t.update(kpts, conf, np.array([x, 100.0, 60.0, 180.0]))
    assert t.hits == n_matches
    assert t.warmup_confirmed
    return t


def _mk_tracker(tracks, max_persons=None):
    tk = DancerTracker()
    tk.logger.start_session(tempfile.mkdtemp())
    tk._person_height_px = 180
    tk.frame_count = 10_000
    if max_persons is not None:
        tk.max_persons = max_persons
    tk.tracks = tracks
    return tk


def test_under_cap_reports_all():
    tracks = [_mk_track(100.0 * (i + 1), 30) for i in range(3)]
    tk = _mk_tracker(tracks, max_persons=6)
    out = tk._collect_confirmed_tracks()
    assert [t.track_id for t in out] == [t.track_id for t in tracks]
    assert tk.last_over_cap == 0


def test_over_cap_keeps_top_k_by_hits():
    # 5 tracks, cap 3: the two with the fewest hits are suppressed.
    hits = [50, 20, 40, 18, 30]
    tracks = [_mk_track(100.0 * (i + 1), h) for i, h in enumerate(hits)]
    tk = _mk_tracker(tracks, max_persons=3)
    out = tk._collect_confirmed_tracks()
    kept = [t.track_id for t in out]
    expect = [tracks[0].track_id, tracks[2].track_id, tracks[4].track_id]
    assert kept == expect          # top-3 by hits, original order preserved
    assert tk.last_over_cap == 2


def test_tie_break_prefers_older_track():
    # Equal hits: the older id (created earlier) wins the last slot.
    tracks = [_mk_track(100.0 * (i + 1), 30) for i in range(4)]
    tk = _mk_tracker(tracks, max_persons=3)
    out = tk._collect_confirmed_tracks()
    assert [t.track_id for t in out] == [t.track_id for t in tracks[:3]]
    assert tk.last_over_cap == 1


def test_cap_disabled_with_zero():
    tracks = [_mk_track(100.0 * (i + 1), 30) for i in range(8)]
    tk = _mk_tracker(tracks, max_persons=0)
    out = tk._collect_confirmed_tracks()
    assert len(out) == 8
    assert tk.last_over_cap == 0


def test_over_cap_resets_when_flood_clears():
    tracks = [_mk_track(100.0 * (i + 1), 30) for i in range(4)]
    tk = _mk_tracker(tracks, max_persons=3)
    tk._collect_confirmed_tracks()
    assert tk.last_over_cap == 1
    tk.tracks = tracks[:2]
    tk._collect_confirmed_tracks()
    assert tk.last_over_cap == 0


def test_capped_tracks_stay_alive_internally():
    # The cap suppresses reporting only — internal track state is untouched.
    tracks = [_mk_track(100.0 * (i + 1), 30 + i) for i in range(5)]
    tk = _mk_tracker(tracks, max_persons=2)
    out = tk._collect_confirmed_tracks()
    assert len(out) == 2
    assert len(tk.tracks) == 5
