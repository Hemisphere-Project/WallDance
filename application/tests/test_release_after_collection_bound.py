"""An engine's release does not land right after a full collection when its bounded wait runs out inside it.

A release waits for the END of a live frame (``after_frame_end``), 0.2 s at most.  A tick in which the collector
policy (``core.gc_policy``) ran a full collection does not end its frame (``MainLoop._guarded_tick``), and the
waiting thread cannot run while that collection holds the GIL (120-175 ms on the show laptop).  When the wait
began early in the frame, its 0.2 s run out about when the collection ends: without a second look, the release
went straight after the collection, in the same gap between frames.  Now, when the bound runs out and the policy
collected since the wait began (``collected_n`` moved) or is collecting at that moment, the release waits once
more for a frame's end, bounded again.  Without the policy, or without a collection, nothing changes.
"""
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from core import gc_policy
from runtime.swipe_controller import after_frame_end


def _proc():
    """A processor while the loop runs YOLO: its last network pass a frame ago, no frame end since."""
    return SimpleNamespace(frame_done=threading.Event(), frame_done_t=time.perf_counter() - 0.01,
                           frame_end=threading.Event(), frame_end_t=0.0)


def _frame_end(proc):
    """What the loop does once a frame is over, its OSC out (``MainLoop._guarded_tick`` -> ``frame_ended``)."""
    proc.frame_end_t = time.perf_counter()
    proc.frame_end.set()


def _release_in_a_thread(proc, stop=None):
    """``after_frame_end`` on a worker thread, as the switch's release calls it; ``out["t"]``: when it returned."""
    out = {}
    done = threading.Event()

    def run():
        after_frame_end(proc, stop)
        out["t"] = time.perf_counter()
        done.set()
    threading.Thread(target=run, daemon=True).start()
    return done, out


def _policy(monkeypatch, **fields):
    policy = SimpleNamespace(installed=True, collected_n=7, _ours=False, **fields)
    monkeypatch.setattr(gc_policy, "POLICY", policy)
    return policy


def test_the_bound_running_out_after_a_collection_waits_for_the_next_frames_end(monkeypatch):
    """The policy collected while the release waited, and that tick's end was not signalled: when the 0.2 s run
    out the release does not go -- it goes right after the next frame's end."""
    policy = _policy(monkeypatch)
    proc = _proc()
    t0 = time.perf_counter()
    done, out = _release_in_a_thread(proc)
    time.sleep(0.05)
    policy.collected_n += 1                       # the tick's tail: a full collection, its frame end held back
    time.sleep(0.27 - (time.perf_counter() - t0))
    assert not done.is_set(), "released when the bound ran out, straight after the collection"
    _frame_end(proc)                              # the next tick, without a collection, ends its frame
    t_end = proc.frame_end_t
    assert done.wait(1.0), "the release waited for ever"
    assert t_end <= out["t"] < t_end + 0.1


class _GilHoldingGc:
    """A ``gc`` module whose full collection holds the GIL in one C call for about ``hold_s``, as a real one does:
    no other thread runs until it is over."""

    def __init__(self, hold_s):
        t = time.perf_counter()
        sum(range(200_000))
        per_item = max(time.perf_counter() - t, 1e-6) / 200_000
        self.n = int(hold_s / per_item)
        self.held = None

    def collect(self):
        t = time.perf_counter()
        sum(range(self.n))
        self.held = (t, time.perf_counter())
        return 0

    def get_objects(self, generation=None):
        return []


def test_the_bound_running_out_inside_the_policys_collection_waits_too(monkeypatch):
    """The real policy's ``collect`` (``core.gc_policy.GcPolicy``), its collection holding the GIL across the end
    of the release's 0.2 s: the waiting thread wakes in the collection's tail, before the policy's counter moves
    (it moves once ``gc.collect`` has returned) -- a collection under way counts as one: the release still waits
    for the next frame's end."""
    gc = _GilHoldingGc(0.25)
    policy = gc_policy.GcPolicy(gc_module=gc, log=lambda line: None, trace=False)
    monkeypatch.setattr(gc_policy, "POLICY", policy)
    proc = _proc()
    t0 = time.perf_counter()
    done, out = _release_in_a_thread(proc)
    time.sleep(0.05)
    policy.collect("test")                        # this thread, the loop's: holds the GIL ~0.25 s
    assert gc.held[0] < t0 + 0.2 < gc.held[1], "the collection did not cover the bound's end"
    time.sleep(0.05)
    assert not done.is_set(), "released straight after the collection"
    _frame_end(proc)
    t_end = proc.frame_end_t
    assert done.wait(1.0), "the release waited for ever"
    assert t_end <= out["t"] < t_end + 0.1


def test_the_second_wait_is_bounded_too(monkeypatch):
    """After a collection and no frame's end at all (the loop stopped): the release goes about 0.2 s after the first
    bound ran out, not later."""
    policy = _policy(monkeypatch)
    proc = _proc()
    t0 = time.perf_counter()
    done, out = _release_in_a_thread(proc)
    time.sleep(0.05)
    policy.collected_n += 1
    assert done.wait(2.0), "the release waited for ever"
    assert 0.35 <= out["t"] - t0 < 0.7


def test_a_frames_end_inside_the_bound_releases_at_once_even_after_a_collection(monkeypatch):
    """Regression guard: a collection in one tick, then the next tick ends its frame before the 0.2 s run out --
    the release goes right after that frame's end, as before."""
    policy = _policy(monkeypatch)
    proc = _proc()
    done, out = _release_in_a_thread(proc)
    time.sleep(0.03)
    policy.collected_n += 1
    time.sleep(0.05)
    _frame_end(proc)
    t_end = proc.frame_end_t
    assert done.wait(1.0)
    assert t_end <= out["t"] < t_end + 0.1


def test_regression_guard_the_bound_running_out_without_a_collection_releases_as_today(monkeypatch):
    """No collection while the release waited (the counter unchanged, none under way): it goes when the 0.2 s run
    out, as before."""
    _policy(monkeypatch)
    proc = _proc()
    t0 = time.perf_counter()
    done, out = _release_in_a_thread(proc)
    assert done.wait(2.0)
    assert 0.15 <= out["t"] - t0 < 0.33


def test_regression_guard_a_stop_set_during_the_wait_skips_the_second_wait(monkeypatch):
    """The swipe is told to stop while the release waits: after a collection the release does not wait again."""
    policy = _policy(monkeypatch)
    proc = _proc()
    stop = threading.Event()
    t0 = time.perf_counter()
    done, out = _release_in_a_thread(proc, stop)
    time.sleep(0.05)
    policy.collected_n += 1
    stop.set()
    assert done.wait(2.0)
    assert 0.15 <= out["t"] - t0 < 0.33


class _Raises:
    @property
    def collected_n(self):
        raise RuntimeError("broken policy")


@pytest.mark.parametrize("how", ["no policy module loaded", "no POLICY", "a policy without the counter",
                                 "a counter that raises"])
def test_regression_guard_without_a_working_policy_the_release_waits_as_today(monkeypatch, how):
    """The replay drivers never load ``core.gc_policy``; an older or broken policy: the release goes when the 0.2 s
    run out, as before, and ``after_frame_end`` does not load the policy itself."""
    if how == "no policy module loaded":
        monkeypatch.delitem(sys.modules, "core.gc_policy")
    elif how == "no POLICY":
        monkeypatch.delattr(gc_policy, "POLICY")
    elif how == "a policy without the counter":
        monkeypatch.setattr(gc_policy, "POLICY", SimpleNamespace(installed=True))
    else:
        monkeypatch.setattr(gc_policy, "POLICY", _Raises())
    proc = _proc()
    t0 = time.perf_counter()
    done, out = _release_in_a_thread(proc)
    assert done.wait(2.0)
    assert 0.15 <= out["t"] - t0 < 0.33
    if how == "no policy module loaded":
        assert "core.gc_policy" not in sys.modules
