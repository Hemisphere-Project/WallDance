"""P6 (Plan Cordee): the edges of the second engine's load (re-read 5de60e5, lens b1 findings 3, 4 and 6).

* The operator's click (the mode set) is the one load moment whatever is on the wall.  The look took it at the
  start of every tick, before the early returns (a model loading, busy, the plate pending): a click on such a tick
  lost its moment, and with dancers on the wall all night the second engine never loaded.  Now the click is kept
  until a look tries the load.
* A look's moment out of RUN was read once at the tick's start: a load started there could run on into the first
  RUN frames.  Now RUN is checked again right before the load (no load, the next moment); a load that RUN overtakes
  (it cannot be cut short) is said once, in the log and in the look's row.
* At quit the looker's join gave up after 3 s while a cold load ran on, and what it loaded was freed after the
  app began tearing CUDA down.  The app's join is now bounded as the switch's own (8 s).

CPU, with the fakes of ``test_swipe_auto_resident``; each test fails on 5de60e5 for the reason it names.
"""
import ast
import threading
import time
from pathlib import Path

import pytest

from test_swipe_auto_resident import _Engine, _kinds, _setup, _until

INTO_RUN = "the second model finished loading as RUN began"


@pytest.mark.parametrize("cannot", ["busy", "a model loading"])
def test_a_click_on_a_tick_that_cannot_load_keeps_its_moment(cannot):
    """In RUN, a dancer tracked all along: the operator sets the mode on a tick that cannot load (a calibration
    runs / a model loads).  The next tick that can loads the second engine, dancer or not -- it was the click's."""
    e = _setup(mode="off", run=True, empty=False, frames=150)
    live = e.live["v"]
    e.bg.set_mode("suggest")
    if cannot == "busy":
        e.bg.busy = lambda: "a calibration runs"
        assert e.bg.tick() == "busy"
        e.bg.busy = lambda: None
    else:
        e.live["v"] = None
        assert e.bg.tick() == "loading"
        e.live["v"] = live
    assert _kinds(e, "load") == []
    e.bg.tick()
    assert _kinds(e, "load") == ["load"], "the click's moment was lost on a tick that could not load"
    assert e.bg.resident is not None


def test_regression_guard_the_click_is_used_once():
    """Regression guard: the look that tries the load uses the click up; a resident made stale later, with a
    dancer tracked, waits for an empty wall."""
    e = _setup(mode="off", run=True, empty=False, frames=0)
    e.bg.set_mode("suggest")
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


def test_run_beginning_between_the_looks_moment_and_the_load_loads_nothing():
    """Out of RUN, a look's moment; RUN begins (a dancer on) before the load starts: no load now -- never on
    entering RUN -- and the next empty wall loads it."""
    e = _setup(mode="suggest", run=False, empty=True, frames=0)
    real = e.bg.engine_exists

    def run_begins(path):                           # read right before the load: RUN is pressed meanwhile
        e.wall.run, e.wall.empty = True, False
        return real(path)
    e.bg.engine_exists = run_begins
    e.bg.tick()
    assert _kinds(e, "load") == [], "a load started out of RUN went on into RUN"
    assert e.bg.resident is None
    e.bg.engine_exists = real
    e.wall.empty = True
    e.bg.tick()
    e.proc.clock += 3.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


def test_a_load_that_run_overtakes_is_said_once(capsys):
    """Out of RUN the load starts; RUN begins while it runs (it cannot be cut short): the log and the look's row
    say it, once."""
    e = _setup(mode="suggest", run=False, empty=True, frames=0)
    real = e.loader.load

    def slow_load(path, cand, model=None):
        got = real(path, cand, model)
        e.wall.run, e.wall.empty = True, False      # RUN pressed while it loaded
        return got
    e.loader.load = slow_load
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]
    row = e.bg.status()["last_look"] or {}
    assert row.get("outcome") == INTO_RUN, "a load RUN overtook was not said in the look's row"
    out = capsys.readouterr().out
    assert out.count("finished loading as RUN began") == 1
    e.bg.tick()
    assert capsys.readouterr().out.count("finished loading as RUN began") == 0


def test_run_pressed_while_the_second_model_loads_never_waits_for_the_load(capsys):
    """28/11: out of RUN the looker's own thread loads the second model and holds the engine lock (a load cannot be
    cut short); RUN is pressed then, the dancers on.  What the loop thread does at that moment -- the looker told
    (``state_changed``, from ``_cb_system_state_change``), the switch's drain between two frames -- returns at once
    while the load still runs: no lock the load holds is on the live loop's path (its frames take none; the drain
    takes the switch's guard without waiting, and never the engine lock).  The load then ends and is said once, and
    the live loop goes on.  What a real TensorRT load does to the GIL (slices of 54-80 ms: frames slowed, not
    stopped) is not seen on the CPU; the 10 s settle keeps a load away from the first RUN frames
    (``test_swipe_auto_load_settle``)."""
    from runtime.swipe_background import BackgroundSwipe
    e = _setup(mode="suggest", run=False, empty=True)   # STANDBY (the helper's settle 0): the first look loads
    began, go = threading.Event(), threading.Event()
    real = e.loader.load

    def slow_load(path, cand, model=None):
        got = real(path, cand, model)
        began.set()
        go.wait(5.0)                                    # the load runs on until the test lets it end
        return got
    e.loader.load = slow_load
    BackgroundSwipe.start(e.bg)
    try:
        assert began.wait(3.0), "the first look did not load"
        assert not e.bg.engine_lock.acquire(blocking=False), "the load does not hold the engine lock"
        t0 = time.perf_counter()
        e.wall.run, e.wall.empty = True, False          # RUN pressed, the dancers on the wall
        e.bg.state_changed()
        drained = [e.bg.switch.drain() for _ in range(5)]   # five frames of the loop thread
        waited = time.perf_counter() - t0
        assert waited < 0.05, f"the loop thread waited {waited * 1000:.0f} ms for the load"
        assert drained == [False] * 5 and e.bg._thread.is_alive() and _kinds(e, "load") == []   # still loading
        go.set()
        assert _until(lambda: _kinds(e, "load") == ["load"]), "the load did not end"
        assert _until(lambda: (e.bg.status()["last_look"] or {}).get("outcome") == INTO_RUN)
    finally:
        go.set()
        e.bg.stop(2.0)
    assert capsys.readouterr().out.count("finished loading as RUN began") == 1


def _quit_timeout() -> float:
    """The looker's join at quit, as ``App._cb_quit`` calls it (``self.swipe_bg.stop(<seconds>)``)."""
    src = Path(__file__).resolve().parent.parent / "src" / "app.py"
    for node in ast.walk(ast.parse(src.read_text(encoding="utf-8"))):
        if isinstance(node, ast.FunctionDef) and node.name == "_cb_quit":
            for call in ast.walk(node):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "stop" \
                        and isinstance(call.func.value, ast.Attribute) and call.func.value.attr == "swipe_bg":
                    return float(call.args[0].value)
    raise AssertionError("no swipe_bg.stop in App._cb_quit")


def test_quit_waits_for_a_cold_load_to_end_and_frees_what_it_loaded():
    """The app quits 0.1 s into a cold load of 3.5 s: with the app's own join, the looker ends and what it
    loaded is freed before ``stop`` returns (before the switch's stop and CUDA's teardown)."""
    from runtime.swipe_background import BackgroundSwipe
    e = _setup(mode="suggest")
    began, freed = threading.Event(), []
    real = e.loader.load

    def cold_load(path, cand, model=None):
        _, runner, s = real(path, cand, model)
        began.set()
        time.sleep(3.5)
        return _Engine(freed), runner, s
    e.loader.load = cold_load
    BackgroundSwipe.start(e.bg)
    t = e.bg._thread
    assert began.wait(3.0)
    time.sleep(0.1)
    t0 = time.perf_counter()
    e.bg.stop(_quit_timeout())
    assert not t.is_alive(), f"stop gave up after {time.perf_counter() - t0:.1f} s with the load still running"
    assert freed and e.bg.resident is None
    assert _until(lambda: True)
