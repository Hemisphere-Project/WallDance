"""Camera stalls (D34): the hold clocks stop while the live camera is silent, and OSC keeps
sending the last points as coasting.  Fake clocks throughout: the stall clock alone, the
pipeline's output stage (no model / GPU), and the main loop's acquire stage against a scripted
camera."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from core.camera_stall import CameraStallClock, stall_states

FPS = 20.0
P = 1.0 / FPS


# ---------------------------------------------------------------------------
# the clock alone
# ---------------------------------------------------------------------------

def _frames(clock, t0, n, period=P):
    t = t0
    for _ in range(n):
        clock.on_frame(t)
        t += period
    return t - period          # the time of the last frame


def _polls(clock, t_from, t_to, step=0.01):
    """The main loop's camera polls while no frame comes; returns the keep-alive slots."""
    due = []
    k = 1
    while t_from + step * k < t_to - 1e-9:
        t = t_from + step * k
        if clock.keepalive_due(t):
            due.append(t)
            clock.keepalive_sent(t)
        k += 1
    return due


def test_regular_frames_leave_the_clock_alone():
    c = CameraStallClock(period_s=P)
    t = _frames(c, 10.0, 100)
    # slow processing, but under 3 frame periods: not a stall
    for gap in (0.08, 0.12, 0.149):
        _polls(c, t, t + gap)
        t += gap
        assert c.on_frame(t) == 0.0
    assert c.offset == 0.0 and c.stalls == 0


def test_a_stall_removes_its_silence_beyond_three_frame_periods():
    c = CameraStallClock(period_s=P)
    t = _frames(c, 10.0, 20)
    due = _polls(c, t, t + 1.7)
    removed = c.on_frame(t + 1.7)
    assert removed == pytest.approx(1.7 - 3 * P)
    assert c.offset == pytest.approx(removed) and c.stalls == 1
    # the keep-alive grid: from the threshold on, one per frame period, until the frame
    assert due[0] == pytest.approx(t + 3 * P, abs=0.011)
    assert len(due) == pytest.approx((1.7 - 3 * P) / P, abs=1.5)
    gaps = np.diff(due)
    assert gaps.min() >= P - 1e-9 and gaps.max() <= P + 0.011
    assert c.last_stall["resends"] == len(due)
    assert not c.keepalive_due(t + 1.75)          # frames are back


def test_a_silence_the_main_loop_never_polled_is_not_a_stall():
    """A model switch / a blocking dialog: the loop did not look at the camera."""
    c = CameraStallClock(period_s=P)
    t = _frames(c, 10.0, 20)
    assert c.on_frame(t + 1.7) == 0.0 and c.offset == 0.0


def test_beyond_the_cap_the_camera_is_lost_and_the_clock_runs():
    c = CameraStallClock(period_s=P, max_s=5.0)
    t = _frames(c, 10.0, 20)
    due = _polls(c, t, t + 6.0)
    assert due and max(due) <= t + 5.0           # the keep-alive stops at the cap
    assert c.on_frame(t + 6.0) == 0.0 and c.offset == 0.0


def test_camera_lost_disabled_and_playback_do_not_bridge():
    c = CameraStallClock(period_s=P)
    t = _frames(c, 10.0, 20)
    _polls(c, t, t + 0.5)
    c.forget()                                    # closed / reconnecting / a take playing
    assert not c.keepalive_due(t + 0.6)
    assert c.on_frame(t + 1.7) == 0.0
    d = CameraStallClock(period_s=P, enabled=False)
    t = _frames(d, 10.0, 20)
    assert _polls(d, t, t + 1.7) == []
    assert d.on_frame(t + 1.7) == 0.0 and d.offset == 0.0


def test_stall_states_are_coasting_with_the_silence_as_age():
    rows = stall_states([(1, "live", 3.2), (2, "coasting", 0.4), (3, "lost", 7.0), (4, "belt", 1.0)], 0.5)
    assert rows == [(1, "coasting", 0.5), (2, "coasting", 0.9), (3, "lost", 7.0), (4, "coasting", 0.5)]


# ---------------------------------------------------------------------------
# the pipeline output stage (identity slots + OSC), fake clock
# ---------------------------------------------------------------------------

