"""Unit test for the Phase-F frozen-ghost report gate (TUNING.md Phase F).

Exercises DancerTracker._collect_confirmed_tracks directly with controlled
fake tracks — no GPU, no frames — so the gate's logic (skeleton-stale AND frozen
=> suppress; moving OR skeleton-fresh => report) is locked down.
"""
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

import core.config as config  # noqa: E402
from core.tracker import DancerTracker  # noqa: E402


class FakeTrack:
    def __init__(self, tid, since_skel, vel, *, hits=50, warmup=None):
        self.track_id = tid
        self.hits = hits
        # warmup=None -> confirmed; a low number -> not yet confirmed
        # (mirrors DancerTrack.warmup_confirmed / the integral path)
        self._warmup_score = (config.TRACK_WARMUP_THRESHOLD if warmup is None
                              else warmup)
        self.warmup_confirmed = (self._warmup_score
                                 >= config.TRACK_WARMUP_THRESHOLD)
        self.bbox = np.array([0.0, 0.0, 60.0, 180.0])
        self._frames_since_skeleton = since_skel
        self._vel = np.asarray(vel, dtype=float)

    def get_velocity(self):
        return self._vel


def _tracker():
    t = DancerTracker()
    t.logger.start_session(tempfile.mkdtemp())
    t._person_height_px = 150          # frozen speed cutoff = 0.03*150 = 4.5 px/f
    t.frame_count = 10_000             # well past warmup/min_hits
    return t


def _confirmed_ids(t, tracks):
    t.tracks = tracks
    return {tk.track_id for tk in t._collect_confirmed_tracks()}


def test_fresh_skeleton_stationary_is_reported():
    t = _tracker()
    # a still dancer keeps getting skeletons -> not stale -> reported
    ids = _confirmed_ids(t, [FakeTrack(1, since_skel=0, vel=[0.0, 0.0])])
    assert ids == {1}


def test_frozen_skeleton_stale_is_suppressed():
    t = _tracker()
    # abandoned ghost: no skeleton for a while AND not moving -> suppressed
    ids = _confirmed_ids(t, [FakeTrack(2, since_skel=10, vel=[0.5, 0.5])])
    assert ids == set()


def test_skeleton_stale_but_moving_is_reported():
    t = _tracker()
    # a gap-bridged dancer is skeleton-stale but MOVING -> spared
    ids = _confirmed_ids(t, [FakeTrack(3, since_skel=10, vel=[20.0, 0.0])])
    assert ids == {3}


def test_recently_skeletoned_frozen_is_reported():
    t = _tracker()
    # within the skeleton-age grace even if momentarily still -> reported
    ids = _confirmed_ids(t, [FakeTrack(4, since_skel=config.TRACKER_GHOST_SKELETON_AGE,
                                       vel=[0.0, 0.0])])
    assert ids == {4}


def test_low_warmup_still_not_reported():
    t = _tracker()
    # the existing warmup gate still applies regardless of the ghost gate
    ids = _confirmed_ids(t, [FakeTrack(5, since_skel=0, vel=[0.0, 0.0], warmup=1.0)])
    assert ids == set()


def test_gate_can_be_disabled(monkeypatch):
    import core.tracker as tracker

    monkeypatch.setattr(tracker, "TRACKER_REPORT_REQUIRES_SKELETON", False)
    t = _tracker()
    ids = _confirmed_ids(t, [FakeTrack(6, since_skel=10, vel=[0.0, 0.0])])
    assert ids == {6}  # frozen ghost reported when the gate is off
