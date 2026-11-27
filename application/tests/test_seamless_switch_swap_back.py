"""P6 (Plan Cordee): the model controller's way back after a seamless switch, on CPU with fakes.

``ModelController.swap_live`` keeps the pre-switch live model (and its bookkeeping) so that ``restore_live`` can
put it back if the switched-in model fails its first frames.  That way back is needed only during those frames:
once they ran well, once a model load overtook the switch, the controller lets it go -- or the old live engine
(0.7 to 1.3 GB on the GPU) stays held after the switch freed it, until the next switch.  The rehearsal build holds
the full collections during a show: the old model must go by reference counting alone, so ``gc`` is off here and
weak references watch the models.  A rollback during the probation still brings the very same model back.
"""
import gc
import weakref

import pytest

from test_seamless_switch import L1280, X1280, _controller, _live, _proc, _Runner
from test_seamless_switch_superseded import M1280, _Model, _reload, _switch, _WatchedLoader

pytest.importorskip("torch")
# imported here, not first inside a helper: torch's first import leaves the frames that called it alive until the
# cyclic collector runs, and with it the helper's locals -- the very models these tests watch
import core.pipeline  # noqa: E402,F401
import runtime.model_controller  # noqa: E402,F401

APPLY = "switching to l @ 1280 (Apply)"


@pytest.fixture
def no_gc():
    gc.collect()
    gc.disable()
    try:
        yield
    finally:
        gc.enable()


def _app():
    """The real processor and model controller (the app's port), around an old live model only the app holds."""
    old = _Model("x1280")
    p = _proc(model=old, runner=_Runner(old))
    mc = _controller(p)
    mc.model = old
    live = {"v": _live()}
    loader = _WatchedLoader()
    sw, events = _switch(p, mc, live, loader)
    return p, mc, live, loader, sw, events, weakref.ref(old)


def _frames(p, n=3):
    for _ in range(n):
        p._run_yolo(None)


def _free(sw, p):
    """The worker's passes: it sees the live model first, then a live frame of it -- the switch's models go."""
    sw.service()
    p.frame_done_t += 1.0
    sw.service()
    assert sw._drop == [], "the switch still holds models to free"


def test_after_a_confirmed_switch_the_old_live_model_goes_as_soon_as_the_switch_frees_it(no_gc):
    """x @ 1280 -> l @ 1280, its first frames run well: once the worker frees the old pair, nothing holds the old
    live model any more -- not the model controller's way back."""
    p, mc, live, loader, sw, events, old = _app()
    assert sw.request(L1280, APPLY) is None
    sw.service()                                         # loaded beside, warmed
    assert sw.drain() and p.model is loader.made[0]() and mc.model is p.model
    live["v"] = _live(cand=L1280, model_id=2)
    _frames(p)                                           # its probation: three frames
    assert sw.last_switch["confirmed"] and sw.busy() is None and [k for k, _ in events] == ["switched"]
    _free(sw, p)
    assert old() is None, "the model controller still holds the old live model after a confirmed switch"
    assert mc.current_model_name == "yolo11l-pose" and mc.model is loader.made[0]()


def test_after_a_superseded_switch_the_old_live_model_goes_too(no_gc):
    """The operator loads m @ 1280 during the switch's first frames: the switch ends as superseded; once the worker
    frees what it holds, neither the old live model nor the switched-in one is held any more."""
    p, mc, live, loader, sw, events, old = _app()
    assert sw.request(L1280, APPLY) is None
    sw.service()
    assert sw.drain()
    live["v"] = _live(cand=L1280, model_id=2)
    _frames(p, 1)                                        # its first frame: two left
    m = _Model("m1280")
    _reload(mc, p, M1280, m)                             # the operator's load meanwhile
    live["v"] = _live(cand=M1280, model_id=3)
    sw.drain()                                           # the loop's next pass: superseded
    _frames(p)
    assert sw.last_switch.get("superseded") and sw.busy() is None and sw.fault is None
    _free(sw, p)
    assert loader.made[0]() is None, "the switched-in engine stays held"
    assert old() is None, "the model controller still holds the old live model after a superseded switch"
    assert (p.model, mc.model, mc.current_model_name) == (m, m, "yolo11m-pose")     # no rollback


def test_a_rollback_still_brings_the_same_old_model_back_and_the_switched_in_one_goes(no_gc):
    """Regression guard: the switched-in model raises on its first frame -- the very same old model object is live
    again, in the processor and in the controller's bookkeeping, and the failed engine goes once freed."""
    p, mc, live, loader, sw, events, old = _app()
    assert sw.request(L1280, APPLY) is None
    sw.service()
    sw._request["prepared"].runner.fail = True          # warmed fine, its first live frame raises
    assert sw.drain()
    live["v"] = _live(cand=L1280, model_id=2)
    assert p._run_yolo(None) == [f"results of {old()}"]  # the old model took that very frame
    assert old() is not None and p.model is old() and mc.model is old()
    assert (mc.current_model_name, mc.current_model) == ("yolo11x-pose", "yolo11x-pose.engine")
    assert sw.fault and sw.busy() is None
    live["v"] = _live(model_id=1)
    _frames(p)
    _free(sw, p)
    assert loader.made[0]() is None, "the failed engine stays held after the rollback"
    assert p.model is old() and mc.model is old()


def test_a_second_switch_right_after_a_confirmed_one_rolls_back_to_the_first_ones_model(no_gc):
    """Regression guard: x -> l confirmed and freed, then l -> x (another engine) whose first frame raises: l --
    the same object -- is live again with its bookkeeping, the second engine goes, and a third switch goes."""
    p, mc, live, loader, sw, events, old = _app()
    assert sw.request(L1280, APPLY) is None
    sw.service()
    assert sw.drain()
    live["v"] = _live(cand=L1280, model_id=2)
    _frames(p)
    _free(sw, p)
    first = loader.made[0]
    assert sw.request(X1280, "switching to x @ 1280 (Apply)") is None
    sw.service()
    sw._request["prepared"].runner.fail = True
    assert sw.drain() and p.model is loader.made[1]()
    live["v"] = _live(cand=X1280, model_id=4)
    assert p._run_yolo(None) == [f"results of {first()}"]
    assert p.model is first() and mc.model is first() and mc.current_model_name == "yolo11l-pose"
    live["v"] = _live(cand=L1280, model_id=2)
    _frames(p)
    _free(sw, p)
    assert loader.made[1]() is None, "the second, failed engine stays held"
    assert sw.request(X1280, "switching to x @ 1280 (Apply)") is None       # the next switch goes


def test_a_switch_whose_bookkeeping_failed_keeps_no_way_back(no_gc):
    """The controller's bookkeeping raises during the swap: everything is put back and the error raised -- and no
    way back stays held, so the old model goes once a load replaces it."""
    from types import SimpleNamespace

    def broken(name):
        raise RuntimeError("the combo is gone")
    p, mc, live, loader, sw, events, old = _app()
    mc.ui = SimpleNamespace(available=True, sync_model_combo=broken)
    new = _Model("l1280")
    with pytest.raises(RuntimeError):
        mc.swap_live(new, _Runner(new), L1280)
    assert p.model is old() and mc.model is old() and mc.current_model_name == "yolo11x-pose"
    assert p._swap_probation is None
    m = _Model("m1280")
    _reload(mc, p, M1280, m)                             # a load later
    assert old() is None, "the failed swap's way back still holds the old model"
