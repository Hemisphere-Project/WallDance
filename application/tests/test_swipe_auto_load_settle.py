"""P6 (Plan Cordee): the second model's load gives way to RUN (28/11).

With suggest the default, every project apply started loading the second model at once -- about 0.3 s warm, up to
about 3 s cold -- and a load cannot be cut short.  A man alone presses RUN when the dancers arrive, often right
after he applied the project: the first RUN frames then ran beside the load (a TensorRT load holds the GIL in slices
of 54-80 ms: 4 to 6 live frames slowed, or one late).  Now, out of RUN, a load starts only once the app has sat
there ``SWIPE_AUTO_LOAD_SETTLE_S`` (10 s) with nothing applied since -- a project, a model, the mode set, RUN or
STANDBY pressed --; RUN pressed before that: nothing was started, and the load waits for the moments the looker
already knew (a wall empty for 2 s, STANDBY again once settled).  His own click on the mode in RUN still loads at
once: his act, as before.

On the looker with the fake clock of ``test_swipe_auto_resident`` (the settle set on the object, as the app's
config gives it), and on a real ``WallDanceApp`` for its doors.  Each test fails on the tree before for the reason
it names (a project's apply goes through ``set_mode(mode)`` there); the regression guards say so."""
import inspect

import pytest

import core.config
from test_swipe_auto_lock import X_MODEL, _pick, real_app  # noqa: F401 - the fixture
from test_swipe_auto_resident import _kinds, _setup

INTO_RUN = "finished loading as RUN began"


def _settled(e):
    """The settle the app builds the looker with (``core.config.SWIPE_AUTO_LOAD_SETTLE_S``)."""
    e.bg.settle_s = float(getattr(core.config, "SWIPE_AUTO_LOAD_SETTLE_S", 10.0))
    return e


def _project_applies(bg, mode="suggest"):
    """A project's apply sets its mode (the app's ``_cb_swipe_auto(..., quiet=True)``): not the operator's click."""
    if "click" in inspect.signature(bg.set_mode).parameters:
        return bg.set_mode(mode, click=False)
    return bg.set_mode(mode)                          # the tree before: a project's apply was a click


def _pressed(e, run):
    """RUN or STANDBY pressed (the app tells the looker; no look runs then)."""
    e.wall.run = run
    getattr(e.bg, "state_changed", lambda: None)()


def test_the_settle_is_ten_seconds():
    assert getattr(core.config, "SWIPE_AUTO_LOAD_SETTLE_S", None) == 10.0


def test_run_three_seconds_after_a_project_apply_finds_no_load_started_and_the_first_empty_wall_loads_it(capsys):
    """STANDBY, a project applied (suggest), RUN pressed 3 s later with the dancers on the wall: no load was started
    -- none runs beside the first RUN frames, nothing to say -- and the second model loads at the first wall empty
    for 2 s.  Fails before: the look the apply woke loaded it at once, 3 s before RUN (a cold load, up to about
    3 s, was still running when RUN began)."""
    e = _settled(_setup(mode="off", run=False, empty=False))
    t0 = e.proc.clock
    _project_applies(e.bg)
    e.bg.tick()                                       # the look the apply wakes, in STANDBY
    assert e.loader.loads == [], "the project's apply started the load at once, 3 s before RUN"
    e.proc.clock = t0 + 3.0
    _pressed(e, run=True)                             # RUN, the dancers on the wall
    e.proc.clock = t0 + 10.0
    e.bg.tick()                                       # the first RUN look
    assert e.loader.loads == [] and e.bg.resident is None and _kinds(e, "load") == []
    assert INTO_RUN not in capsys.readouterr().out
    e.wall.empty = True                               # the dancers leave the wall
    e.proc.clock += 5.0
    e.bg.tick()                                       # seen empty: the count starts
    assert e.loader.loads == []
    e.proc.clock += 2.0
    e.bg.tick()                                       # empty for 2 s: the moment
    assert _kinds(e, "load") == ["load"] and e.bg.resident is not None
    assert INTO_RUN not in capsys.readouterr().out


def test_standby_kept_ten_seconds_after_a_project_apply_loads_it_once():
    """STANDBY kept: nothing for 10 s, the looker comes back when they end, one load, none after.  Fails before:
    loaded at the apply's own look."""
    e = _settled(_setup(mode="off", run=False, empty=True))
    t0 = e.proc.clock
    _project_applies(e.bg)
    e.bg.tick()
    assert e.loader.loads == [], "loaded at the project's apply"
    assert e.bg._next_s == pytest.approx(min(e.bg.look_s, 10.0))    # the next look when the settle ends
    e.proc.clock = t0 + 9.5
    e.bg.tick()
    assert e.loader.loads == []
    e.proc.clock = t0 + 10.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]
    for _ in range(6):
        e.proc.clock += 5.0
        e.bg.tick()
    assert _kinds(e, "load") == ["load"] and len(e.loader.loads) == 1


def test_regression_guard_his_click_in_run_loads_at_once_a_dancer_on_the_wall():
    """The combo in RUN, a dancer tracked: his own act is the moment, at the next look, as before the settle."""
    e = _settled(_setup(mode="off", run=True, empty=False, frames=150))
    e.bg.set_mode("suggest")
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


def test_his_click_in_standby_waits_for_the_settle_and_lapses_if_run_begins_first():
    """The combo in STANDBY: no load for 10 s; RUN pressed 4 s later with the dancers on: his click is not a moment
    in RUN -- nothing loads while a dancer is tracked.  Kept in STANDBY instead: the load comes 10 s after it.
    Fails before: his click in STANDBY loaded at once."""
    e = _settled(_setup(mode="off", run=False, empty=False))
    t0 = e.proc.clock
    e.bg.set_mode("suggest")
    e.bg.tick()
    assert e.loader.loads == [], "his click in STANDBY loaded at once"
    e.proc.clock = t0 + 4.0
    _pressed(e, run=True)
    e.proc.clock = t0 + 10.0
    e.bg.tick()
    assert e.loader.loads == [], "his click made in STANDBY loaded in RUN with a dancer tracked"
    kept = _settled(_setup(mode="off", run=False, empty=False))
    t1 = kept.proc.clock
    kept.bg.set_mode("suggest")
    kept.bg.tick()
    kept.proc.clock = t1 + 10.0
    kept.bg.tick()
    assert _kinds(kept, "load") == ["load"]


def test_run_and_standby_pressed_between_two_looks_count_the_settle_again():
    """Applied at 0, RUN pressed and STANDBY again at 6 s, no look between: STANDBY has lasted 4 s at the 10 s look,
    no load; 10 s after that look, the load.  Fails before: loaded at the apply's own look."""
    e = _settled(_setup(mode="off", run=False, empty=True))
    t0 = e.proc.clock
    _project_applies(e.bg)
    e.bg.tick()
    e.proc.clock = t0 + 6.0
    _pressed(e, run=True)
    _pressed(e, run=False)
    e.proc.clock = t0 + 10.0
    e.bg.tick()
    assert e.loader.loads == [], "loaded 4 s after STANDBY began again"
    e.proc.clock = t0 + 20.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


def test_a_model_load_counts_the_settle_again():
    """A project switch's model loads for 8 s after its mode was set: the 10 s count from the moment its model is
    live.  Fails before: loaded at the first look with the model live."""
    e = _settled(_setup(mode="off", run=False, empty=True))
    live = e.live["v"]
    e.live["v"] = None
    _project_applies(e.bg)
    assert e.bg.tick() == "loading"
    e.proc.clock += 8.0
    assert e.bg.tick() == "loading"
    e.live["v"] = live                                # the project's model is live
    e.bg.tick()
    assert e.loader.loads == [], "loaded as soon as the project's model was live"
    e.proc.clock += 9.5
    e.bg.tick()
    assert e.loader.loads == []
    e.proc.clock += 0.5
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


def test_the_app_a_projects_mode_is_no_click_his_pick_is_and_run_or_standby_counts_again(real_app):  # noqa: F811
    """The app's doors: a project's load sets the mode without a moment of its own, his pick in the combo sets one,
    and RUN / STANDBY pressed tell the looker.  Fails before: a project's load was a click moment, and the looker
    never heard of RUN or STANDBY."""
    from runtime import api
    app, events = real_app
    app.swipe_bg.start = lambda: None                 # the looker driven by nobody: what it was asked
    app._apply_config_without_model({"model": X_MODEL})         # no key: the default, suggest
    assert app.swipe_bg.mode == "suggest"
    assert not app.swipe_bg._mode_set, "a project's apply is a load moment whatever is on the wall"
    _pick(app, "Auto: off")
    _pick(app, "Auto: suggest")
    assert app.swipe_bg._mode_set                     # his click
    app.swipe_bg._settle_again = False
    app._cmd_set_state(api.SetState("standby"))
    assert getattr(app.swipe_bg, "_settle_again", False), "STANDBY pressed: the looker was not told"
