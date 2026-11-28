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
import dataclasses
import inspect
from types import SimpleNamespace

import pytest

import core.config
from core.swipe import Candidate
from test_seamless_switch import L1280, _live
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


# ------------------------------------------------------------------ P7 (blind review p7review, F2 and F5, 28/11)
# F2: the settle sat on (project, input, shape, TensorRT) -- no model --, and a look that found the app busy counted
# towards it.  So a model changed by hand at the same input, its reload between two looks (up to 5 s apart), and a
# calibration in STANDBY both ended in a load at the very next look: RUN pressed right after ran its first frames
# beside it -- the case the settle was made for.  The review's probes (gates/p7review/probes/test_probe_settle.py),
# as tests: each fails on the tree before for the reason it names; the imgsz change is the control (it passed).
_LOADS = ("load", "release")


def _applied_and_loaded(e):
    """STANDBY kept 10 s after a project's apply: l @ 1280 loaded beside x @ 1280 (the start of each case)."""
    t0 = e.proc.clock
    _project_applies(e.bg)
    e.bg.tick()
    e.proc.clock = t0 + 10.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"] and e.bg.resident.candidate == L1280
    return t0


def _said(e):
    return [(h["kind"], h.get("resident")) for h in e.bg.history if h["kind"] in _LOADS]


def test_a_model_change_by_hand_at_the_same_input_counts_the_settle_again():
    """STANDBY, settled, l @ 1280 resident beside x @ 1280.  He changes the model by hand to l (same input); its
    reload falls between two looks.  The look 1 s after it frees the old resident (releases are not held back) and
    loads nothing; the other model, x @ 1280, loads 10 s after that look.  Fails before: loaded at that very look,
    one second after his change (the key had no model in it)."""
    e = _settled(_setup(mode="off", run=False, empty=True))
    t0 = _applied_and_loaded(e)
    e.proc.clock = t0 + 60.0
    e.live["v"] = _live(cand=L1280, model_id=2)        # his change x -> l, same input: a reload no look saw
    e.proc.clock += 1.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"], f"the look 1 s after a model change loaded the other one: {_said(e)}"
    assert _said(e) == [("load", "l @ 1280"), ("release", "l @ 1280")] and e.bg.resident is None
    assert e.bg._next_s == pytest.approx(10.0)          # the looker comes back when the settle ends
    e.proc.clock += 9.5
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]
    e.proc.clock += 0.5
    e.bg.tick()
    assert _said(e)[-1] == ("load", "x @ 1280") and e.bg.resident.candidate.label == "x @ 1280"


def test_control_an_imgsz_change_counts_the_settle_again():
    """The same with another input (x @ 1536): the key changed before too -- the settle counts again, and the other
    model at that input, l @ 1536, loads 10 s later (the control: passes on the tree before)."""
    e = _settled(_setup(mode="off", run=False, empty=True))
    t0 = _applied_and_loaded(e)
    e.proc.clock = t0 + 60.0
    e.live["v"] = _live(cand=Candidate("yolo11x-pose", 1536), model_id=2)
    e.proc.clock += 1.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]
    e.proc.clock += 10.0
    e.bg.tick()
    assert _said(e)[-1] == ("load", "l @ 1536")


def test_a_calibration_in_standby_counts_nothing_the_settle_starts_after_it():
    """STANDBY, a new project's mode set (no plate yet: the looker waits), then CALIBRATE on the empty wall for
    40 s (busy), the plate good after it: no load at the first look after the calibration; 10 s after that look,
    the load.  Fails before: loaded at that first look -- the 10 s were counted through the calibration, and the
    guide's order is CALIBRATE, then RUN."""
    e = _settled(_setup(mode="off", run=False, empty=True))
    good = e.live["v"]
    e.live["v"] = dataclasses.replace(good, plate=None, plate_ok=False)
    t0 = e.proc.clock
    _project_applies(e.bg)                             # New project: the default
    e.bg.tick()
    e.proc.clock = t0 + 5.0
    e.bg.busy = lambda: "a calibration"
    assert e.bg.tick() == "waiting"                    # (the plate is not usable yet: the look stops there)
    e.proc.clock = t0 + 45.0                           # the calibration ends, the plate is good
    e.bg.busy = lambda: None
    e.live["v"] = good
    e.bg.tick()
    assert _kinds(e, "load") == [], "loaded at the first look after the calibration (the settle ran through it)"
    e.proc.clock = t0 + 54.5
    e.bg.tick()
    assert _kinds(e, "load") == []
    e.proc.clock = t0 + 55.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


def test_a_look_that_finds_the_app_busy_counts_nothing_the_same_plate_kept():
    """The busy look alone (the plate the same before and after: a calibration that keeps it, a model load, an
    Apply under way): applied at 0, busy seen at 5 s, free again at 45 s -- no load then, the load 10 s later.
    Fails before: loaded at 45 s."""
    e = _settled(_setup(mode="off", run=False, empty=True))
    t0 = e.proc.clock
    _project_applies(e.bg)
    e.bg.tick()
    e.proc.clock = t0 + 5.0
    e.bg.busy = lambda: "a calibration"
    assert e.bg.tick() == "busy"
    e.proc.clock = t0 + 45.0
    e.bg.busy = lambda: None
    e.bg.tick()
    assert _kinds(e, "load") == [], "loaded at the first look after a busy one: the settle counted through it"
    e.proc.clock = t0 + 55.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


def test_a_new_empty_wall_plate_between_two_looks_counts_the_settle_again():
    """An empty-wall capture short enough that no look saw it (busy) between two looks: its new plate counts the
    settle again -- applied at 0, captured at 6 s, no load at the 10 s look, the load 10 s after it.  Fails before:
    loaded at 10 s (the plate was not in the key)."""
    e = _settled(_setup(mode="off", run=False, empty=True))
    t0 = e.proc.clock
    _project_applies(e.bg)
    e.bg.tick()
    e.proc.clock = t0 + 6.0
    e.live["v"] = dataclasses.replace(e.live["v"], plate=(2, "captured at 6 s"))
    e.proc.clock = t0 + 10.0
    e.bg.tick()
    assert _kinds(e, "load") == [], "loaded 4 s after a new plate"
    e.proc.clock = t0 + 20.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


def test_regression_guard_in_run_an_empty_wall_still_loads_it_whatever_the_settle():
    """RUN, the wall empty for 2 s after a project's apply: the settle is out of RUN only -- the load comes, as
    before (the busy look and the model in the key change nothing in RUN)."""
    e = _settled(_setup(mode="off", run=True, empty=True))
    _project_applies(e.bg)
    e.bg.tick()
    e.proc.clock += 2.0
    e.bg.tick()
    assert _kinds(e, "load") == ["load"]


# F5: while the second model is still to load (the mode on, nothing loaded), Check readiness said nothing -- so the
# guide's step (load in STANDBY, the row shows what it takes, then look at TouchDesigner) could not be done once RUN
# was pressed inside the settle, and 0.7-1.6 GB were taken beside TouchDesigner at the first empty wall of the show.
STILL_TO_LOAD = ("the automatic swipe's second model ({}) is still to load (after 10 s in STANDBY, or in RUN at "
                 "the first wall empty for 2 s) - to have it before the show, stay 10 s in STANDBY, then press "
                 "'Check readiness' again")


def _row(e):
    import app as appmod
    return appmod.WallDanceApp._swipe_resident_row(SimpleNamespace(swipe_bg=e.bg))


def test_readiness_says_the_second_model_is_still_to_load_then_what_it_holds():
    """Applied, nothing loaded yet: one information row (``ok``: advice, READY stays reachable) saying when it loads
    and what to do; RUN pressed inside the settle with a dancer on the wall: the same row; loaded: the row of
    before, what it holds; another project at the same model and input: still to load, with what it took at its
    last load.  Fails before: no row until it was loaded."""
    pytest.importorskip("dearpygui")
    e = _settled(_setup(mode="off", run=False, empty=False))
    assert _row(e) is None                             # the mode off: no row
    _project_applies(e.bg)
    e.bg.tick()
    row = _row(e)
    assert row is not None, "nothing said while the second model is still to load"
    assert (row.name, row.status, row.detail) == ("auto swipe", "ok", STILL_TO_LOAD.format("l @ 1280"))
    _pressed(e, run=True)                              # RUN inside the settle, a dancer on the wall: no load
    e.proc.clock += 5.0
    e.bg.tick()
    assert e.bg.resident is None and _row(e).detail == STILL_TO_LOAD.format("l @ 1280")
    used = iter([1000 * 2 ** 20, 1234 * 2 ** 20])
    e.bg.gpu_used = lambda: next(used)
    _pressed(e, run=False)                             # STANDBY kept 10 s: loaded
    e.bg.tick()
    e.proc.clock += 10.0
    e.bg.tick()
    assert e.bg.resident is not None
    assert _row(e).detail == "the automatic swipe keeps a second model loaded: about 230 MB"
    e.live["v"] = _live(project="another_project", model_id=3)      # another project, same model and input
    e.proc.clock += 1.0
    e.bg.tick()                                        # out of RUN: the old resident goes at once
    assert e.bg.resident is None
    assert _row(e).detail == STILL_TO_LOAD.format("l @ 1280, about 230 MB")


def test_readiness_says_nothing_of_a_load_that_will_not_come():
    """No row where nothing will load: the mode off, a model loading, the live model on PyTorch, the other model's
    engine not built here, or a setting banned (the fault row says why).  (Fails before at its first step: no row
    at all while it was to load.)"""
    pytest.importorskip("dearpygui")
    e = _settled(_setup(mode="off", run=False, empty=True))
    _project_applies(e.bg)
    e.bg.tick()
    assert _row(e) is not None
    live = e.live["v"]
    e.live["v"] = None
    assert _row(e) is None, "a model loads"
    e.live["v"] = dataclasses.replace(live, trt=False)
    assert _row(e) is None, "PyTorch: no second engine"
    e.live["v"] = live
    e.bg.engine_exists = lambda p: False
    assert _row(e) is None, "its engine is not built here"
    e.bg.engine_exists = lambda p: True
    e.bg._banned[L1280.key] = L1280.engine_file
    assert _row(e) is None, "banned: the fault row says it"
    e.bg._banned.clear()
    e.bg.set_mode("off")
    assert _row(e) is None
    from core.ops_monitor import check_swipe_resident
    assert check_swipe_resident(swipe_auto="suggest", to_load="l @ 1280").detail == STILL_TO_LOAD.format("l @ 1280")
    assert check_swipe_resident(swipe_auto="off", to_load="l @ 1280") is None
