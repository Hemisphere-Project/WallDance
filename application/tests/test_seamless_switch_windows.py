"""P6 (Plan Cordee): one seamless switch at a time, and nothing left behind at quit.

* ``busy()`` says « a switch is already under way » from the moment a request is accepted to its outcome (done,
  failed, abandoned, superseded): through the worker's load and warm-up -- a second Apply (or the remote API)
  used to be accepted there and loaded a second copy, and the first Apply never heard back -- and through the
  loop's swap, between ``_request`` and ``_probation`` -- a request landing there was swapped in during the
  first's probation.  A refused request leaves the first untouched.
* the worker thread runs only while it has something to do (it used to wake every second for the session once
  a switch had been asked); a request or a model to free starts it again.
* ``stop()`` at quit waits for a load in progress and frees what the switch holds, before CUDA goes.
On CPU, with fakes.
"""
import threading
import time
import weakref
from types import SimpleNamespace

from test_seamless_switch import L1280, L1920, X1280, _Loader, _Port, _Runner, _live, _sw  # noqa: F401
from test_swipe_auto_live import _bg

UNDER_WAY = "a switch is already under way"


class _Timed:
    """A loaded « engine » whose release (its last reference) is recorded."""

    def __init__(self, freed, name="engine"):
        self.name = name
        self.predictor = SimpleNamespace(model=SimpleNamespace(format="engine"))
        weakref.finalize(self, lambda: freed.append(name))


def test_a_second_apply_during_the_load_and_the_warm_up_is_refused_and_the_first_goes_on():
    """The engines nit 5 (probe_busy_gap): the operator presses Apply again while the first one loads, and once
    more while it warms up: both refused; one load, one swap, and the first Apply hears back."""
    live = {"v": _live()}
    seen = {}

    class Runner(_Runner):
        def __call__(self, tensor, **kw):
            seen.setdefault("warm", (bg.switch.busy(), bg.request_switch(L1920, "Apply again (warm-up)")))
            return super().__call__(tensor, **kw)

    class Loader(_Loader):
        def load(self, path, cand, model=None):
            seen.setdefault("load", (bg.switch.busy(), bg.request_switch(L1920, "Apply again (load)")))
            m, _, s = super().load(path, cand, model)
            return m, Runner(m), s
    loader, port = Loader(), _Port()
    bg, proc, _ = _bg(live, loader=loader, port=port)
    sw = bg.switch
    done = []
    assert bg.request_switch(L1920, "switching to l @ 1920 x 1056 (Apply)",
                             on_done=lambda ok, text: done.append(ok)) is None
    sw.service()                                         # the worker: load, then warm-up
    assert seen["load"] == (UNDER_WAY, UNDER_WAY), "a second Apply was accepted during the load"
    assert seen["warm"] == (UNDER_WAY, UNDER_WAY), "a second Apply was accepted during the warm-up"
    assert sw._want is None and sw._request is not None and sw._request["text"].endswith("(Apply)")
    sw.service()                                         # the worker's next pass: nothing more to load
    assert loader.loads == [L1920.engine_file]
    assert sw.drain() and [c[0] for c in port.calls] == ["swap"]
    port.hooks[1]()                                      # its first frames ran
    assert done == [True] and sw.busy() is None


def test_a_request_landing_inside_the_swap_is_refused():
    """The loop nit 5 (probe_races, probe 1, the window forced through the port): between ``_request = None`` and
    ``_probation = req`` another thread asks (the looker, prepared): refused, no second swap during the first's
    probation, and the first switch's on_done comes."""
    from runtime.seamless_switch import Prepared
    live = {"v": _live()}
    seen = {}

    class Port(_Port):
        def swap_live(self, model, runner, cand, on_fail=None, on_ok=None):
            old = super().swap_live(model, runner, cand, on_fail=on_fail, on_ok=on_ok)
            proc.model = model                       # the processor's live model now
            if "busy" not in seen:          # what another thread sees if it gets the GIL here
                seen["busy"] = sw.busy()
                seen["request"] = sw.request(L1280, "auto, from the looker", auto=True,
                                             prepared=Prepared(L1280, "x-other", _Runner("x-other")))
            return old
    port = Port()
    sw, proc, _ = _sw(live, port=port)
    done = []
    first = SimpleNamespace(name="l-new")
    assert sw.request(L1280, "operator Apply", lambda ok, text: done.append(ok),
                      prepared=Prepared(L1280, first, _Runner(first))) is None
    sw.service()
    assert sw.drain()
    assert seen == {"busy": UNDER_WAY, "request": UNDER_WAY}
    assert sw._probation is not None and sw._probation["text"] == "operator Apply" and sw._want is None
    sw.service()
    assert sw.drain() is False and [c[0] for c in port.calls] == ["swap"]
    port.hooks[1]()
    assert done == [True] and sw.busy() is None


def test_busy_ends_with_each_outcome():
    """Regression guard: done, failed before it began, failed on its first frame, abandoned, superseded -- each
    one lets the next switch be asked, from its own handler too (``on_done`` asks the next: accepted)."""
    outcomes = {}

    def run(name, *, loader=None, act):
        live = {"v": _live()}
        port = _Port()
        sw, proc, _ = _sw(live, loader=loader or _Loader(), port=port)
        nxt = []
        assert sw.request(L1280, name, on_done=lambda ok, text: nxt.append(sw.request(L1280, "next"))) is None
        act(sw, live, port)
        outcomes[name] = (nxt, sw.busy() is None or sw._want is not None)

    run("done", act=lambda sw, live, port: (sw.service(), sw.drain(), port.hooks[1]()))
    run("failed before", loader=_Loader(fail="cudaErrorMemoryAllocation"), act=lambda sw, live, port: sw.service())
    run("first frame raised",
        act=lambda sw, live, port: (sw.service(), sw.drain(), port.hooks[0](RuntimeError("raised"))))
    run("abandoned", act=lambda sw, live, port: (live.update(v=_live(model_id=2)), sw.service()))
    run("superseded", act=lambda sw, live, port: (sw.service(), sw.drain(), sw.drain()))
    assert outcomes == {k: ([None], True) for k in outcomes}, outcomes


def test_the_worker_ends_when_it_has_nothing_to_do_and_starts_again():
    """Re-read nit 3: once a switch was asked (the key on, then off), the worker woke every second for the
    session.  Now it ends when nothing is asked, prepared, on probation or waiting to be freed; a request, or a
    model to free, starts it again -- and it stays while a model waits (STANDBY: not freed yet)."""
    live = {"v": _live()}
    sw, proc, _ = _sw(live)
    del sw.start                                         # the real worker thread
    sw.period_s = 0.02
    sw.why_not = lambda req: "the automatic swipe was turned off"
    try:
        assert sw.request(L1920, "Apply") is None
        t1 = sw._thread
        t1.join(2.0)
        assert not t1.is_alive() and sw._thread is None, "the worker goes on waking with nothing to do"
        assert sw.request(L1920, "Apply") is None and sw._thread is not None   # started again
        sw._thread.join(2.0)
        live["v"] = None                                 # a model load: nothing can be freed yet
        sw.release(("old", "old-runner"))
        t2 = sw._thread
        time.sleep(0.2)
        assert t2 is not None and t2.is_alive(), "the worker ended with a model waiting to be freed"
        live["v"] = _live()
        t2.join(2.0)
        assert not t2.is_alive() and sw._drop == []
    finally:
        sw.stop(1.0)


def test_quit_waits_for_a_load_in_progress_and_frees_it():
    """The engines nit 7: the operator quits while the switch loads an engine (it cannot be cut short).  ``stop``
    waits for the load, no warm-up follows, and the engine goes before ``stop`` returns -- not under a daemon
    thread while the process tears CUDA down."""
    live = {"v": _live()}
    loading, gate, freed = threading.Event(), threading.Event(), []

    class Slow(_Loader):
        def load(self, path, cand, model=None):
            self.loads.append(path)
            loading.set()
            gate.wait(5.0)
            m = _Timed(freed)
            return m, _Runner(m), 0.4
    sw, proc, _ = _sw(live, loader=Slow())
    del sw.start
    sw.period_s = 0.02
    assert sw.request(L1920, "switching to l @ 1920 x 1056 (Apply)") is None
    assert loading.wait(2.0)
    quit_ = threading.Thread(target=lambda: sw.stop(5.0), daemon=True)
    quit_.start()
    time.sleep(0.15)
    assert quit_.is_alive(), "stop returned while an engine was loading"
    gate.set()
    quit_.join(5.0)
    assert not quit_.is_alive()
    assert freed == ["engine"], "the engine loaded at quit outlived stop()"
    assert sw._request is None and sw._drop == [] and not sw._thread.is_alive()


def test_quit_frees_the_models_waiting_and_a_prepared_one():
    """The engines nit 7: what the switch holds goes at ``stop`` -- a model waiting to be freed (STANDBY: no live
    frame came) and a prepared model not swapped in yet; a prepared resident is the automatic swipe's, it stays."""
    from runtime.seamless_switch import Prepared
    live = {"v": _live()}
    freed = []

    class Loader(_Loader):
        def load(self, path, cand, model=None):
            self.loads.append(path)
            m = _Timed(freed, "prepared")
            return m, _Runner(m), 0.4
    sw, proc, _ = _sw(live, loader=Loader())
    assert sw.request(L1920, "Apply") is None
    sw.service()                                         # loaded and warmed, not swapped in yet ...
    sw.release((_Timed(freed, "old live"), "runner"))   # ... and a model waiting to be freed
    assert sw._request is not None and len(sw._drop) == 1 and freed == []
    sw.stop(1.0)
    assert sorted(freed) == ["old live", "prepared"] and sw._request is None and sw._drop == []

    resident = _Timed(freed, "resident")
    sw2, _, _ = _sw({"v": _live()})
    assert sw2.request(L1280, "auto", prepared=Prepared(L1280, resident, _Runner(resident)), auto=True) is None
    sw2.service()
    assert sw2._request is not None
    sw2.stop(1.0)
    assert "resident" not in freed
