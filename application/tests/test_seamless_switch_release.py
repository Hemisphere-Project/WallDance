"""P6 (Plan Cordee): every engine the seamless switch lets go goes right after the loop's NEXT live pass -- a
TensorRT destructor holds the GIL some 50 ms, and in the loop's slack it delays no frame mid-way.  The old live
model after a confirmed switch, the automatic swipe's resident when it no longer fits (mode off here), the
resident the operator's Apply lets go first: the release waits for a live pass that comes after it was due, goes
right after it, and never waits for ever (at most 0.2 s with the loop running, none without a recent live pass).
On CPU, with a fake processor whose live passes the test makes.
"""
import threading
import time
import weakref

import pytest

from test_seamless_switch import L1280, L1920, X1280, _Loader, _Port, _live  # noqa: F401
from test_swipe_auto_live import _bg


class _Engine:
    """Stands for an engine: its release (the last reference goes) is timed."""

    def __init__(self, freed):
        weakref.finalize(self, lambda: freed.append(time.perf_counter()))


def _live_pass(proc):
    """What the pipeline does once a frame's network pass is done (``FrameProcessor``, P5 pacing)."""
    proc.frame_done_t = time.perf_counter()
    proc.frame_done.set()


def _in_a_thread(fn):
    done = threading.Event()
    t = threading.Thread(target=lambda: (fn(), done.set()), daemon=True)
    t.start()
    return t, done


def _running(proc):
    """The loop runs YOLO: its last live pass was a frame ago."""
    proc.frame_done_t = time.perf_counter() - 0.01
    proc.frame_done.set()


def _released_right_after_the_next_live_pass(proc, freed, fn):
    _running(proc)
    t, done = _in_a_thread(fn)
    time.sleep(0.08)
    assert freed == [], "released before the next live pass"
    _live_pass(proc)
    t_pass = proc.frame_done_t
    assert done.wait(1.0)
    assert len(freed) == 1 and freed[0] >= t_pass, "released before the live pass"
    assert freed[0] - t_pass < 0.05, f"released {1000 * (freed[0] - t_pass):.0f} ms after the live pass"


def _switched(live, port):
    """A confirmed operator's seamless Apply x @ 1280 -> l @ 1920 x 1056 (another input: the old pair goes)."""
    bg, proc, _ = _bg(live, port=port, mode="suggest")
    sw = bg.switch
    freed = []
    port.live_pair = (_Engine(freed), "x1280-runner")
    sw.request(L1920, "switching to l @ 1920 x 1056 (Apply)")
    sw.service()
    assert sw.drain() and len(sw._drop) == 1
    port.hooks[1]()                                      # its first frames ran: confirmed
    live["v"] = _live(cand=L1920, model_id=2)
    port.live_pair = port.hooks = None                   # the processor holds the new model only, its probation over
    return sw, proc, freed


def test_the_old_live_model_goes_right_after_the_next_live_pass():
    live, port = {"v": _live()}, _Port()
    sw, proc, freed = _switched(live, port)
    _released_right_after_the_next_live_pass(proc, freed, sw.service)
    assert sw._drop == []


def test_the_resident_goes_right_after_the_next_live_pass_when_the_mode_goes_off():
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    bg, proc, _ = _bg(live, mode="suggest")
    freed = []
    bg.resident = Resident(L1280, _Engine(freed), "r-l1280", bg._resident_key(live["v"]), 0.4)
    bg.set_mode("off")
    _released_right_after_the_next_live_pass(proc, freed, bg.tick)
    assert bg.resident is None


def test_the_resident_an_apply_lets_go_goes_right_after_the_next_live_pass():
    """The operator's Apply of another setting than the resident: the resident goes first (two engines at most),
    on the switch's worker, right after a live pass -- then the winner loads."""
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    loader = _Loader()
    bg, proc, _ = _bg(live, loader=loader, mode="suggest")
    freed = []
    bg.resident = Resident(L1280, _Engine(freed), "r-l1280", bg._resident_key(live["v"]), 0.4)
    assert bg.request_switch(L1920, "switching to l @ 1920 x 1056 (Apply)") is None and bg.resident is None
    _released_right_after_the_next_live_pass(proc, freed, bg.switch.service)
    assert loader.loads == [L1920.engine_file]           # then the winner loaded, as before


def test_without_a_live_pass_the_release_waits_a_bounded_time():
    """The loop was running and stops (a stall, the app quitting): the release does not wait for ever -- it goes
    after the pacer's 0.2 s."""
    live, port = {"v": _live()}, _Port()
    sw, proc, freed = _switched(live, port)
    _running(proc)
    t0 = time.perf_counter()
    t, done = _in_a_thread(sw.service)
    time.sleep(0.08)
    assert freed == [], "released at once, without waiting for a live pass"
    assert done.wait(2.0), "the release waited for ever"
    assert len(freed) == 1 and 0.15 <= freed[0] - t0 < 1.0


def test_in_standby_the_release_does_not_wait():
    """Regression guard: no live pass in the last 0.25 s (STANDBY, the loop stopped): released at once."""
    live, port = {"v": _live()}, _Port()
    sw, proc, freed = _switched(live, port)
    proc.frame_done_t = time.perf_counter() - 5.0
    t0 = time.perf_counter()
    sw.service()
    assert len(freed) == 1 and freed[0] - t0 < 0.05


def test_a_fresh_pacer_from_now_waits_for_the_next_live_pass():
    """``make_pacer(..., start=now)``: its first call waits for a live pass after ``start``; without ``start`` (P5's
    pacer before its first inference) any earlier one counts.  (``start`` is new with this fix.)"""
    from runtime.swipe_controller import make_pacer
    live = {"v": _live()}
    _, proc, _ = _bg(live, mode="off")
    _running(proc)
    t0 = time.perf_counter()
    make_pacer(proc, 0.0)()
    assert time.perf_counter() - t0 < 0.05                # P5's: at once
    _running(proc)
    t, done = _in_a_thread(make_pacer(proc, 0.0, start=time.perf_counter()))
    time.sleep(0.05)
    assert not done.is_set()
    _live_pass(proc)
    assert done.wait(1.0)
