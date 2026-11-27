"""An engine's release never lands in the tick where the collector policy collected.

A release waits for the END of a live frame (``MainLoop._guarded_tick`` -> ``FrameProcessor.frame_ended`` sets
``frame_end``), then holds the GIL some 50 ms while the next frame starts.  The rehearsal build's collector policy
(``core.gc_policy``) runs its full collections at that same tail, 120-175 ms on the show laptop.  The two must not
stack in one gap between frames: after a tick in which the policy collected, the frame's end is not signalled; it
is at the end of the next tick without a collection.
"""
import threading
import time
from types import SimpleNamespace

import pytest

from core import gc_policy


def _proc():
    """A ``FrameProcessor`` with only the frame-end signal (its own ``frame_ended``)."""
    from core.pipeline import FrameProcessor
    p = FrameProcessor.__new__(FrameProcessor)
    p.frame_done, p.frame_done_t = threading.Event(), 0.0
    p.frame_end, p.frame_end_t = threading.Event(), 0.0
    return p


def _live_pass(p):
    """What the pipeline does once a frame's network pass is done."""
    p.frame_done_t = time.perf_counter()
    p.frame_done.set()


def _loop(p, tick):
    from runtime.main_loop import MainLoop
    loop = MainLoop.__new__(MainLoop)
    loop.app = SimpleNamespace(processor=p)
    loop._tick_fail_streak = 0
    loop._on_failure = lambda where, exc, streak: None
    loop._tick = tick
    return loop


def test_a_tick_that_collected_does_not_end_the_frame_for_a_release_the_next_one_does(monkeypatch):
    """A fake policy record: the tick in which a full collection was counted leaves ``frame_end`` unset; the next
    tick without one sets it -- also when that tick ran no network (the frame that ended is the earlier one)."""
    policy = SimpleNamespace(installed=True, collected_n=7)
    monkeypatch.setattr(gc_policy, "POLICY", policy)
    p = _proc()
    plan = {"collect": True, "network": True}

    def tick():
        if plan["network"]:
            _live_pass(p)
        if plan["collect"]:
            policy.collected_n += 1                 # the render stage's end: the policy collected
    loop = _loop(p, tick)
    loop._guarded_tick()
    assert not p.frame_end.is_set() and p.frame_end_t == 0.0     # no release in the collection's gap
    loop._guarded_tick()                                         # a second tick that collected too
    assert not p.frame_end.is_set()
    plan.update(collect=False, network=False)
    loop._guarded_tick()                                         # the next tick without one: the release goes
    assert p.frame_end.is_set() and p.frame_end_t >= p.frame_done_t
    p.frame_end.clear()
    plan.update(network=True)
    loop._guarded_tick()
    assert p.frame_end.is_set()


def test_the_real_policy_collecting_in_the_tick_holds_the_frame_end_back(monkeypatch):
    """With ``GcPolicy`` on a fake gc, served by ``gc_policy.app_tick`` inside the tick (as the render stage does):
    a request served in this tick holds the frame's end back; the next tick, nothing due, signals it."""
    from test_gc_policy import _policy
    policy, fake, _lines = _policy()
    monkeypatch.setattr(gc_policy, "POLICY", policy)
    policy.start()
    p = _proc()
    app = SimpleNamespace(system_state=SimpleNamespace(name="STANDBY"), calibration=None, processor=p)

    def tick():
        _live_pass(p)
        gc_policy.app_tick(app, [])
    loop = _loop(p, tick)
    policy.request("model load")
    loop._guarded_tick()
    assert fake.collects == 2 and not p.frame_end.is_set()
    loop._guarded_tick()
    assert fake.collects == 2 and p.frame_end.is_set()


@pytest.mark.parametrize("how", ["absent", "no counter", "not installed"])
def test_regression_guard_without_a_working_policy_every_tick_ends_the_frame(monkeypatch, how):
    """Regression guard: no policy (a replay, another build), a policy without the counter, or the policy not
    installed (its tick does nothing) -- every tick that ran the network signals the frame's end, as before."""
    if how == "absent":
        monkeypatch.delattr(gc_policy, "POLICY")
    elif how == "no counter":
        monkeypatch.setattr(gc_policy, "POLICY", SimpleNamespace(installed=True))
    else:
        from test_gc_policy import _policy
        monkeypatch.setattr(gc_policy, "POLICY", _policy()[0])
    p = _proc()
    app = SimpleNamespace(system_state=SimpleNamespace(name="STANDBY"), calibration=None, processor=p)

    def tick():
        _live_pass(p)
        if how != "absent":
            gc_policy.request("model load")         # ignored before install
        if how == "not installed":
            gc_policy.app_tick(app, [])
    loop = _loop(p, tick)
    for _ in range(3):
        p.frame_end.clear()
        loop._guarded_tick()
        assert p.frame_end.is_set()
