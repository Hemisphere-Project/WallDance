"""Output ball (the emitted OSC centroid in the preview): size follows the dancer's
certain body length, hide/show toggle (2026-10-07)."""
from types import SimpleNamespace

import numpy as np
import pytest

from core.visualization import (BALL_SCALE_MAX, BALL_SCALE_MIN, BallSizer,
                                certain_body_length, draw_slot)


def _track(length=300.0, conf=0.9, state="live", fss=0, sid=1, cx=500.0, cy=400.0):
    kp = np.tile([cx, cy], (17, 1)).astype(float)
    kp[0:5] = [cx, cy - length / 2]          # head
    kp[15:17] = [cx, cy + length / 2]        # ankles
    kc = np.full(17, conf)
    return SimpleNamespace(track_id=sid, keypoints=kp, confidence=kc, slot_state=state,
                           frames_since_skeleton=fss, smoothed_centroid=np.array([cx, cy]),
                           bbox=np.array([cx - 50, cy - length / 2, 100, length]))


def test_certain_length_needs_a_fresh_confident_full_skeleton():
    assert certain_body_length(_track(300)) == pytest.approx(300)
    assert certain_body_length(_track(300, conf=0.3)) is None        # unsure keypoints
    assert certain_body_length(_track(300, fss=2)) is None           # no fresh skeleton
    assert certain_body_length(_track(300, state="coasting")) is None
    t = _track(300)
    t.confidence[15:17] = 0.1                                        # ankles hidden
    assert certain_body_length(t) is None


def test_scale_tracks_distance_within_the_range():
    fh = 1500.0
    far, mid, near = BallSizer(), BallSizer(), BallSizer()
    for i in range(60):
        t = i * 0.05
        s_far = far.scale(1, _track(0.10 * fh), fh, t)      # 25 m wall body
        s_mid = mid.scale(1, _track(0.20 * fh), fh, t)      # half distance
        s_near = near.scale(1, _track(0.60 * fh), fh, t)    # by the lens
    assert s_far == pytest.approx(0.5, abs=0.02)
    assert s_mid == pytest.approx(1.0, abs=0.02)
    assert s_near == BALL_SCALE_MAX


def test_scale_is_smoothed_and_held_without_certain_skeletons():
    fh, sz = 1500.0, BallSizer()
    assert sz.scale(1, _track(0.2 * fh, fss=5), fh, 0.0) == 1.0     # nothing certain yet: base size
    sz.scale(1, _track(0.2 * fh), fh, 0.05)
    s = sz.scale(1, _track(0.1 * fh), fh, 0.10)                       # one frame of a smaller body
    assert 0.9 < s < 1.0                                              # smoothed, not a jump
    held = sz.scale(1, _track(0.6 * fh, fss=4), fh, 0.15)             # blob-fed frame: ignored
    assert held == pytest.approx(s, abs=1e-6)
    assert BALL_SCALE_MIN <= held <= BALL_SCALE_MAX
    assert sz.scale(1, _track(0.2 * fh, fss=4), fh, 10.0) == 1.0     # forgotten after a long absence


def test_draw_slot_hides_the_ball_and_scales_it():
    def ball_pixels(**kw):
        img = np.zeros((750, 1000, 3), np.uint8)
        draw_slot(img, _track(300, cx=500, cy=375), **kw)
        fill = np.array((0, 255, 0), np.uint8)                        # D1 dancer colour (centre)
        return int((img == fill).all(axis=2).sum())
    assert ball_pixels(show_ball=False) == 0
    small, big = ball_pixels(ball_scale=0.5), ball_pixels(ball_scale=2.0)
    assert 0 < small < ball_pixels() < big
