"""P6 (Plan Cordee): every engine the seamless switch lets go goes right after the END of the loop's NEXT live
frame -- its OSC out, its tick over (``frame_end``) -- not right after its network pass (``frame_done``): a
TensorRT destructor holds the GIL some 50-70 ms, and after the network pass alone it delayed that frame's
tracker and OSC (the P6 re-read, 27/11: 5 releases of 5).  The old live model after a confirmed switch, the
automatic swipe's resident when it no longer fits (mode off here), the resident the operator's Apply lets go
first: the release waits for a frame that ends after it was due, goes right after its end, and never waits for
ever (at most 0.2 s with the loop running, none without a recent live pass).
On CPU, with a fake processor whose network passes and frame ends the test makes.
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


def _frame_end(proc):
    """What the loop does once that frame is over, its OSC out (``MainLoop._guarded_tick`` -> ``frame_ended``)."""
    proc.frame_end_t = time.perf_counter()
    proc.frame_end.set()


def _with_frame_end(proc):
    """The processor carries the frame-end signal, as ``FrameProcessor`` does."""
    proc.frame_end, proc.frame_end_t = threading.Event(), 0.0
    return proc


def _in_a_thread(fn):
    done = threading.Event()
    t = threading.Thread(target=lambda: (fn(), done.set()), daemon=True)
    t.start()
    return t, done


def _running(proc):
    """The loop runs YOLO: its last live pass was a frame ago."""
    proc.frame_done_t = time.perf_counter() - 0.01
    proc.frame_done.set()


def _released_right_after_the_next_frame_end(proc, freed, fn):
    _with_frame_end(proc)
    _running(proc)
    t, done = _in_a_thread(fn)
    time.sleep(0.08)
    assert freed == [], "released before the next live frame"
    _live_pass(proc)                                     # its network pass: tracker and OSC still to come
    time.sleep(0.06)
    assert freed == [], "released after the network pass, before the frame's OSC"
    _frame_end(proc)
    t_end = proc.frame_end_t
    assert done.wait(1.0)
    assert len(freed) == 1 and freed[0] >= t_end, "released before the frame's end"
    assert freed[0] - t_end < 0.05, f"released {1000 * (freed[0] - t_end):.0f} ms after the frame's end"


def _switched(live, port):
    """A confirmed operator's seamless Apply x @ 1280 -> l @ 1920 x 1056 (another input: the old pair goes)."""
    bg, proc, _ = _bg(live, port=port, mode="suggest")
    _with_frame_end(proc)
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


def test_the_old_live_model_goes_right_after_the_next_frames_end():
    live, port = {"v": _live()}, _Port()
    sw, proc, freed = _switched(live, port)
    _released_right_after_the_next_frame_end(proc, freed, sw.service)
    assert sw._drop == []


def test_the_resident_goes_right_after_the_next_frames_end_when_the_mode_goes_off():
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    bg, proc, _ = _bg(live, mode="suggest", in_run=lambda: True)      # in RUN (out of RUN it goes at once)
    freed = []
    bg.resident = Resident(L1280, _Engine(freed), "r-l1280", bg._resident_key(live["v"]), 0.4)
    bg.set_mode("off")
    _released_right_after_the_next_frame_end(proc, freed, bg.tick)
    assert bg.resident is None


def test_the_resident_an_apply_lets_go_goes_right_after_the_next_frames_end():
    """The operator's Apply of another setting than the resident: the resident goes first (two engines at most),
    on the switch's worker, right after a live frame's end -- then the winner loads."""
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    loader = _Loader()
    bg, proc, _ = _bg(live, loader=loader, mode="suggest")
    freed = []
    bg.resident = Resident(L1280, _Engine(freed), "r-l1280", bg._resident_key(live["v"]), 0.4)
    assert bg.request_switch(L1920, "switching to l @ 1920 x 1056 (Apply)") is None and bg.resident is None
    _released_right_after_the_next_frame_end(proc, freed, bg.switch.service)
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


def test_a_network_pass_without_its_frames_end_holds_the_release_a_bounded_time():
    """The loop's tick ran the network and never got to its end (a long tick, the loop stopping mid-frame): the
    release waits for the end, not for ever -- it goes after the 0.2 s, and not at the network pass."""
    live, port = {"v": _live()}, _Port()
    sw, proc, freed = _switched(live, port)
    _running(proc)
    t0 = time.perf_counter()
    t, done = _in_a_thread(sw.service)
    time.sleep(0.03)
    _live_pass(proc)
    time.sleep(0.05)
    assert freed == [], "released at the network pass"
    assert done.wait(2.0), "the release waited for ever"
    assert len(freed) == 1 and 0.15 <= freed[0] - t0 < 1.0


def test_a_processor_without_the_frame_end_keeps_the_live_pass():
    """Regression guard: a processor that has no ``frame_end`` (a replay driver's, an older fake): the release
    goes right after the next live pass, as before."""
    from runtime.swipe_controller import after_frame_end
    live = {"v": _live()}
    _, proc, _ = _bg(live, mode="off")
    assert not hasattr(proc, "frame_end")
    _running(proc)
    t, done = _in_a_thread(lambda: after_frame_end(proc))
    time.sleep(0.05)
    assert not done.is_set()
    _live_pass(proc)
    assert done.wait(1.0)


def test_the_frame_ends_only_after_a_tick_that_ran_the_network():
    """``FrameProcessor.frame_ended`` (the loop calls it at the end of every tick): a tick in STANDBY, a tick a
    pump took, a tick that waited for the camera ended no live frame; one that ran the network did."""
    from core.pipeline import FrameProcessor
    p = FrameProcessor.__new__(FrameProcessor)
    p.frame_done, p.frame_done_t = threading.Event(), 0.0
    _with_frame_end(p)
    p.frame_ended()
    assert p.frame_end_t == 0.0 and not p.frame_end.is_set()
    _live_pass(p)
    p.frame_ended()
    t_end = p.frame_end_t
    assert t_end >= p.frame_done_t and p.frame_end.is_set()
    p.frame_end.clear()
    p.frame_ended()                                      # the next tick ran no network
    assert p.frame_end_t == t_end and not p.frame_end.is_set()


def test_the_loop_ends_the_frame_after_its_tick_even_a_failed_one():
    """``MainLoop._guarded_tick``: the frame's end comes after the whole tick (OSC, preview, render) -- also when a
    stage raised (the boundary reports it), so a release never waits for a tick that already ended."""
    from types import SimpleNamespace
    from runtime.main_loop import MainLoop
    order = []
    proc = SimpleNamespace(frame_ended=lambda: order.append("frame end"))
    loop = MainLoop.__new__(MainLoop)
    loop.app = SimpleNamespace(processor=proc)
    loop._tick_fail_streak = 0
    loop._on_failure = lambda where, exc, streak: order.append("reported")
    loop._tick = lambda: order.append("tick")
    loop._guarded_tick()
    assert order == ["tick", "frame end"]

    def failing():
        order.append("tick")
        raise RuntimeError("the preview stage raised")
    loop._tick = failing
    order.clear()
    loop._guarded_tick()
    assert order == ["tick", "reported", "frame end"]


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
