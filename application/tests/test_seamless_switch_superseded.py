"""P6 (Plan Cordee): a seamless switch overtaken by a reload during its probation, on CPU with fakes.

The switched-in model runs its first frames on probation (``FrameProcessor.adopt_model``).  A reload in those
frames (the operator changes the model or the input, a STANDBY Apply, a project load) puts another model live
before they ran.  The probation then ends as superseded: not a fault, no ban, no rollback (the reload's model is
the live one), nothing of the switch held, the switch free for the next one -- also in STANDBY, where no frame
runs -- and the automatic swipe takes the reload's setting for a change by hand.
"""
import gc
import threading
import weakref
from types import SimpleNamespace

import pytest

from core.swipe import Candidate
from test_seamless_switch import L1280, X1280, _Loader, _Port, _Runner, _controller, _live, _proc

M1280 = Candidate("yolo11m-pose", 1280)          # a model outside the swipe's pair, picked by hand
SUPERSEDED = "the switch to l @ 1280 was overtaken by a model load during its first frames: m @ 1280 runs"


class _Model:
    """An engine stand-in a weak reference can watch: it must go by reference counting alone."""

    def __init__(self, name):
        self.name = name
        self.predictor = SimpleNamespace(model=SimpleNamespace(format="engine"))


class _WatchedLoader(_Loader):
    def __init__(self):
        super().__init__()
        self.made = []

    def load(self, path, cand, model=None):
        self.loads.append(path)
        m = _Model(cand.key)
        self.made.append(weakref.ref(m))
        return m, _Runner(m), 0.4


def _switch(p, mc, live, loader):
    """The switch on the real processor and model controller (the app's port)."""
    pytest.importorskip("torch")
    from runtime.seamless_switch import SeamlessSwitch
    events = []
    p.loop_clock = lambda: 100.0
    p._swipe_key = None
    p.frame_done_t = 0.0
    sw = SeamlessSwitch(processor=lambda: p, live=lambda: live["v"], engine_path=lambda c: c.engine_file,
                        port=mc, notify=lambda k, pl: events.append((k, pl)), runner_factory=loader,
                        engine_exists=lambda path: True, engine_lock=threading.RLock(), size_change=False)
    sw.start = lambda: None                              # the test drives the worker by hand (``service``)
    return sw, events


def _reload(mc, p, cand, model):
    """What a reload does (``ModelController._drain_pending_model_switch`` -> ``_attach_model``): another model
    object live; the processor's runner follows it on its next frame (``_run_yolo``), here at once."""
    mc.model, mc.current_model_name = model, cand.model
    mc.model_manager.effective_shape = lambda: None
    mc._attach_model()
    p._yolo_runner = _Runner(model)


def test_a_reload_during_the_probation_ends_it_and_frees_the_switch():
    """The operator's Apply swaps l @ 1280 in; after its first frame the operator picks m @ 1280 (a reload): the
    probation ends there -- no fault, no rollback, the operator's Apply told, the switch free for the next one,
    and the switched-in engine freed after a live frame of the reload's model."""
    p = _proc(runner=_Runner("x1280"))
    mc = _controller(p)
    live = {"v": _live()}
    loader = _WatchedLoader()
    sw, events = _switch(p, mc, live, loader)
    done = []
    gc.collect()
    gc.disable()
    try:
        assert sw.request(L1280, "switching to l @ 1280 (Apply)",
                          on_done=lambda ok, text: done.append((ok, text))) is None
        sw.service()                                     # loaded beside, warmed
        assert sw.drain() and p.model is loader.made[0]() and sw._probation is not None
        live["v"] = _live(cand=L1280, model_id=2)
        assert p._run_yolo(None)                         # its first frame: two left
        assert not sw.drain() and sw._probation is not None, "a probation still live was ended"
        _reload(mc, p, M1280, "m1280")                   # the operator picks another model meanwhile
        live["v"] = _live(cand=M1280, model_id=3)
        for _ in range(3):                               # the loop: the switch between two frames, then a frame
            sw.drain()
            assert p._run_yolo(None) == ["results of m1280"]
        assert sw.busy() is None, "the probation never ended: no seamless Apply, no automatic switch again"
        assert sw._probation is None and sw.state == "idle" and p._swap_probation is None
        assert sw.fault is None and [k for k, _ in events] == ["cleared"] and events[0][1]["text"] == SUPERSEDED
        assert done == [(False, SUPERSEDED)]
        assert (p.model, mc.model, mc.current_model_name) == ("m1280", "m1280", "yolo11m-pose")   # no rollback
        assert [h["kind"] for h in sw.history][-1] == "superseded"
        sw.service()                                     # the worker sees the reload's model first ...
        p.frame_done_t = 1.0                             # ... then a live frame of it: the switch's models go
        sw.service()
        assert sw._drop == [] and loader.made[0]() is None, "the switched-in engine stays held"
        assert sw.request(L1280, "switching to l @ 1280 (Apply)") is None     # the next seamless Apply goes
    finally:
        gc.enable()


def test_a_reload_in_standby_ends_the_probation_without_a_frame():
    """A switch made in STANDBY waits for its first frames (none run there); a reload meanwhile ends it at the
    next pass of the loop -- the processor forgets the probation too, so it holds neither model nor handler."""
    p = _proc(runner=_Runner("x1280"))
    mc = _controller(p)
    live = {"v": _live()}
    loader = _WatchedLoader()
    sw, events = _switch(p, mc, live, loader)
    assert sw.request(L1280, "switching to l @ 1280 (Apply)") is None
    sw.service()
    assert sw.drain() and p._swap_probation is not None
    _reload(mc, p, X1280, "x1280-again")                 # back to x by hand, no frame in between
    live["v"] = _live(cand=X1280, model_id=4)
    sw.drain()                                           # the next pass of the loop, still STANDBY
    assert sw.busy() is None, "the probation waits for a frame that a reload made pointless"
    assert p._swap_probation is None, "the processor still holds the switched-in model and its handlers"
    assert sw.fault is None and [k for k, _ in events] == ["cleared"]
    assert [m for m, _ in sw._drop] == ["x1280", loader.made[0]()]     # freed after a live frame, by the worker


class _LivePort(_Port):
    """The fake port, with the processor running the switched-in model (``FrameProcessor.adopt_model``)."""

    proc = None

    def swap_live(self, model, runner, cand, on_fail=None, on_ok=None):
        old = super().swap_live(model, runner, cand, on_fail=on_fail, on_ok=on_ok)
        self.proc.model = model
        return old


def test_after_a_superseded_automatic_switch_the_looker_sees_a_change_by_hand():
    """Mode auto switches to its resident l @ 1280; during the probation the operator loads m @ 1280.  The
    policy never heard of the switch (no confirmation); the looker's next tick sees a live setting that is
    neither the old one nor the one it expected: a change by hand (the dwell starts), no ban, the switch not
    busy, and the old live model it kept as resident goes (it no longer fits the live setting)."""
    from test_swipe_auto_live import _bg
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    port = _LivePort()
    bg, proc, events = _bg(live, port=port, mode="auto")
    port.proc = proc
    assert bg.tick() == "idle" and bg._last_live == X1280
    m, r = SimpleNamespace(name="l1280"), _Runner("l1280")
    bg.resident = Resident(L1280, m, r, bg._resident_key(live["v"]), 0.4)
    adv = SimpleNamespace(kind="apply", level="definite", winner=L1280, seamless=True, streak=2,
                          text="switching to l @ 1280 (it covers 20 % more of the dancers)", why_not_auto=None)
    bg._act(adv, SimpleNamespace(reason="l @ 1280 covers 90 %", winner=L1280, current=X1280), live["v"])
    bg.switch.service()
    assert bg.switch.drain() and proc.model is m and bg.resident.candidate == X1280
    proc.model = SimpleNamespace(name="m1280")           # the operator's reload, before the first frames ran
    live["v"] = _live(cand=M1280, model_id=3)
    bg.switch.drain()                                    # the loop's next pass
    pol = bg.auto.policy
    assert bg.tick() == "idle", "the switch stays busy: no look, no automatic switch again"
    kinds = [h["kind"] for h in bg.history]
    assert "manual-change" in kinds and [h for h in bg.history if h["kind"] == "manual-change"][-1]["new"] == \
        M1280.label
    assert (pol._switched_at, pol._left) == (proc.clock, X1280)          # the dwell starts at the change by hand
    assert bg.switch.fault is None and bg.status()["auto_banned"] == []
    assert bg.resident is None and "release" in kinds and bg.switch._drop == []
    assert "switched" not in [k for k, _ in events]
