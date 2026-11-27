"""A background look's refusal of the empty-wall plate does not count toward the plate watch's alert.

The plate watch (``core.reference_watch``, a fake here) raises its alert « refused » at three refusals in a row,
with STANDBY then Calibrate for instruction: that is for three deliberate presses of the operator's « Try other
settings ».  The automatic swipe's looks go through the same ``RefereeReport.note``; on a wall the referee refuses
for another reason than a wrong plate, mode suggest would raise the alert by itself in the middle of a show.  So a
background refusal tells the watch nothing and is said in the swipe's own row; a background accept is still told;
the operator's swipe tells both, as before.
"""
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest

from core.swipe import Observation, SwipeFrame
from runtime.swipe_controller import RefereeReport
from test_swipe_auto_live import DANCER, ON_HER, _look_frames, _looking_bg

WAITS = "the referee does not trust the empty-wall picture: the automatic swipe waits"
REFUSED = "no verdict: every setting puts boxes where the empty-wall picture shows nobody"


@pytest.fixture
def calls(monkeypatch):
    """The watch's two functions replaced by recorders."""
    got = []
    watch = types.ModuleType("core.reference_watch")
    watch.note_referee_refusal = lambda reason="": got.append(("refusal", reason)) or len(got)
    watch.note_referee_accept = lambda: got.append(("accept", None))
    monkeypatch.setitem(sys.modules, "core.reference_watch", watch)
    return got


def _refusing_pool(n=150):
    """Frames whose live boxes sit where the empty-wall picture shows nobody: the referee refuses the plate."""
    img = np.zeros((64, 48), np.uint8)
    nobody = (50.0, 50.0, 90.0, 120.0, 0.7)
    return [SwipeFrame(float(i), img, Observation(groups=[DANCER]), i < 100, (nobody,)) for i in range(n)], nobody


def test_a_background_refusal_tells_the_watch_nothing_and_is_said_in_the_swipes_row(calls):
    """Three background looks refusing the plate in a row would have been three refusals told, the plate alert
    in the middle of a show: none is told, the swipe's own row says it, the looker waits as after any run without
    a verdict, and the operator's next press on that same pool is told (the looker's was never told)."""
    bg, proc, events, loader, live = _looking_bg()
    bg.referee = shared = RefereeReport()
    proc.frames, nobody = _refusing_pool()
    loader.infer_boxes = [nobody, ON_HER]
    assert bg.tick() == "run"
    assert calls == []                                              # neither function called
    assert bg.status()["last_look"]["outcome"] == WAITS
    assert bg.status()["last_look"]["look"] == "run"                # a look that ran, not a wait
    assert bg.auto.policy._none_at is not None                      # the wait after a run without a verdict
    infers = loader.infers
    proc.frames = _look_frames(150, t0=150.0)                       # fresh frames: still waiting, no run
    assert bg.tick() == "idle" and loader.infers == infers
    assert bg.status()["last_look"]["outcome"] == WAITS             # the row stays
    refused = SimpleNamespace(winner=None, reason=REFUSED)
    assert shared.note(refused, _refusing_pool()[0]) == "refusal"   # the operator's press, same pool: told
    assert [k for k, _r in calls] == ["refusal"]


def test_regression_guard_a_background_accept_is_still_told(calls):
    """Regression guard: a background look that judged on the plate (here it suggests another setting) still
    tells the watch « accept », which can only start its count over."""
    bg, proc, events, loader, live = _looking_bg()
    bg.referee = RefereeReport()
    proc.frames = _look_frames()
    assert bg.tick() == "run"
    assert bg.last_advice["advice"] == "suggest"
    assert calls == [("accept", None)]
    assert bg.status()["last_look"]["outcome"] != WAITS


def test_regression_guard_the_operators_refusal_and_accept_are_told_as_before(calls):
    """Regression guard: the operator's swipe (``note`` without ``background``) tells a refusal with its reason
    and an accept, as before; the same pool pressed twice is one refusal."""
    rep = RefereeReport()
    pool, _nobody = _refusing_pool(30)
    refused = SimpleNamespace(winner=None, reason=REFUSED)
    assert rep.note(refused, pool) == "refusal"
    assert rep.note(refused, pool) is None                          # the same pool pressed again
    assert calls == [("refusal", REFUSED)]
    kept = SimpleNamespace(winner=object(), reason="keep")
    assert rep.note(kept, _look_frames(30, t0=500.0)) == "accept"
    assert calls == [("refusal", REFUSED), ("accept", None)]
