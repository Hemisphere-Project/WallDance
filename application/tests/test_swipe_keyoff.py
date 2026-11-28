"""P6 (Plan Cordee), the automatic swipe's key off (swipe_auto: off, the technician's choice): the build behaves as it
did before P6 -- P5's Apply (STANDBY only, the reload; in RUN P5's refusal), P5's words on the Apply button, its
tooltip, the swipe button's tooltip and the readiness row, and a remote Apply STANDBY only.  Turning the key off
in RUN leaves nothing of P6 under way: a seamless switch asked before and not yet swapped in does not happen.  A
project that brings the key on says so, once; a full project load with the key off runs off.

The app's own doors on a real ``WallDanceApp`` (no GUI, no main loop, CPU): only what needs a camera or a model is
faked -- the live model (loaded, TensorRT), the operator's verdict (``swipe.applicable`` / ``swipe.outcome``: it
needs 150 kept frames), the engine files on disk, the reload itself (``models.request_setting``) and the switch's
worker thread.  The switch's load, warm-up and swap need a model: those tests stay on the fakes of
``test_swipe_auto_live`` with the app's own hold rule."""
from types import SimpleNamespace

import pytest

from test_seamless_switch import L1280, L1920, X1280, _Loader, _Port, _live  # noqa: F401
from test_swipe_auto_live import _bg, _looking_bg, _look_frames, swipe_auto_unlocked  # noqa: F401
from test_swipe_auto_lock import _toasts, real_app  # noqa: F401 - the fixture

P5_RUN_TOAST = "Apply: stop the show first - Apply reloads the model, a few seconds without tracking"
P5_APPLY_TIP = ("STANDBY only: switches the model / network input to the swipe's\n"
                "pick -- the engine reloads, a few seconds without tracking, as\n"
                "when you change the model by hand.  Not saved: Save the project\n"
                "to keep it.")
P5_SWIPE_TIP_END = "nothing switches\nuntil you Apply, in STANDBY.  Press again to cancel."
P5_ROW = "l @ 1280 covers 91 % of the dancers - in STANDBY press 'Apply l @ 1280 (STANDBY)', under Try other settings"


def _app():
    pytest.importorskip("dearpygui")
    import app as appmod
    return appmod


def _live_model(app):
    """The live model loaded as a model load leaves it: x @ 1280 on TensorRT, every engine built on disk."""
    m = app.models
    m.model, m._model_loaded, m._model_loading, m.current_model_name = object(), True, False, X1280.model
    m.model_manager.is_using_tensorrt = lambda: True
    app.settings.imgsz, app.settings.yolo_shape = 1280, None
    app.swipe_switch.engine_exists = lambda path: True
    live = app._swipe_live_setting()
    assert live is not None and live.candidate == X1280 and live.trt
    return live


def _on(app, mode):
    """The mode through the app's door (the looker thread never started)."""
    app.swipe_bg.start = lambda: None
    app._cb_swipe_auto(mode)
    assert app.swipe_bg.mode == mode


def _apply(app, state, *, verdict=L1280):
    """The operator's Apply of a verdict that names ``verdict``, its engine built, at the live input: P6 would
    switch between two frames.  Every way into a switch is watched; the reload itself is faked."""
    from runtime.api import SystemState
    _live_model(app)
    app.system_state = getattr(SystemState, state)
    app._swipe_shown = "operator"
    app.swipe.applicable = lambda live_now: (None, verdict, None)
    started, asked, reloads = [], [], []
    app.swipe_switch.start = lambda: started.append("worker")
    real_request = app.swipe_bg.request_switch
    app.swipe_bg.request_switch = lambda *a, **k: asked.append(a) or real_request(*a, **k)
    app.models.request_setting = lambda m, i, s, on_done=None: reloads.append((m, i, s)) or None
    app._cb_apply_settings_swipe()
    return SimpleNamespace(asked=asked, started=started, reloads=reloads)


def _no_switch(app, r):
    sw = app.swipe_switch
    assert r.asked == [] and r.started == [] and sw._thread is None and sw._request is None
    assert sw.busy() is None and sw.state == "idle"


# ----------------------------------------------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------------------------------------------
def test_key_off_apply_in_run_is_p5s_refusal_and_starts_no_switch(real_app):  # noqa: F811
    """The winner's engine built at the live input, the show running, the key off: P5's refusal word for word, and
    no switch at all -- no request, no worker thread, no engine load."""
    app, events = real_app
    r = _apply(app, "RUN")
    assert _toasts(events) == [P5_RUN_TOAST]
    assert r.reloads == []
    _no_switch(app, r)


def test_key_off_apply_in_standby_is_p5s_reload(real_app, capsys):  # noqa: F811
    """STANDBY, the key off, the engine built: P5's reload (the model-switch path), never the seamless switch;
    P5's log line and progress line."""
    app, events = real_app
    r = _apply(app, "STANDBY")
    assert r.reloads == [(L1280.model, L1280.imgsz, L1280.shape)]
    _no_switch(app, r)
    prog = [e for e in events if type(e).__name__ == "SettingsSwipeProgress"]
    assert [(p.state, p.text) for p in prog] == [("applying", "switching to l @ 1280...")]
    assert "[Swipe] applying l @ 1280\n" in capsys.readouterr().out
    assert app._swipe_pending_apply[0] == L1280


@pytest.mark.parametrize("mode", ["suggest", "auto"])
def test_key_on_apply_in_run_is_p6s_seamless_switch(real_app, swipe_auto_unlocked, mode):  # noqa: F811
    """Regression guard: the key on, the same Apply is P6's -- loaded beside the live model, between two frames."""
    app, events = real_app
    _on(app, mode)
    r = _apply(app, "RUN")
    assert r.asked and r.asked[0][0] == L1280 and r.reloads == []
    assert r.started == ["worker"] and app.swipe_switch.busy() == "a switch is already under way"


def test_key_off_apply_button_label_and_tooltip_are_p5s(real_app, swipe_auto_unlocked):  # noqa: F811
    """The Apply button the verdict shows: P5's label and tooltip with the key off, the engine built or not."""
    app, _ = real_app
    _live_model(app)
    app.swipe.outcome = SimpleNamespace(result=SimpleNamespace(winner=L1280, switch=True))
    for winner in (L1920, L1280):
        assert app._swipe_apply_hint(winner) == {"apply_seamless": False,
                                                 "apply_label": f"Apply {winner.label} (STANDBY)",
                                                 "apply_tip": P5_APPLY_TIP}
    assert app._swipe_seamless_winner() is None                   # the readiness row: P5's wording
    _on(app, "suggest")                                           # regression guard: P6's, the key on
    hint = app._swipe_apply_hint(L1280)
    assert hint["apply_seamless"] and hint["apply_label"] == "Apply l @ 1280"
    assert app._swipe_seamless_winner() == L1280


def _readiness(app, mode):
    """The operator's verdict recommends l @ 1280 (its engine built, the live input: seamless with the key on), the
    plate ready, with a ban the automatic swipe holds from earlier."""
    _live_model(app)
    if mode != "off":
        _on(app, mode)
    app._swipe_plate_ready = lambda: True
    app.swipe_bg._banned[L1920.key] = L1920.engine_file
    app.swipe.outcome = SimpleNamespace(result=SimpleNamespace(winner=L1280, switch=True,
                                                               reason="l @ 1280 covers 91 % of the dancers"))
    app.swipe.fault, app.swipe.fault_recovered = None, False
    app.swipe._advice_live = lambda live_now: True


def test_key_off_readiness_row_is_p5s(real_app):  # noqa: F811
    """The readiness row of an operator's verdict that recommends a switch: P5's, word for word, with the key off
    -- the engine built, and a ban left from when the key was on (the automatic swipe tries nothing now)."""
    app, _ = real_app
    _readiness(app, "off")
    row = app._swipe_readiness_row()
    assert row.status == "warn" and row.detail == P5_ROW
    app.swipe_switch.fail("the switch to l @ 1280 failed (RuntimeError: x): x @ 1280 still runs - rebuild it")
    assert app._swipe_readiness_row().detail.startswith("the switch to l @ 1280 failed")


@pytest.mark.parametrize("mode", ["suggest", "auto"])
def test_key_on_readiness_row_is_p6s(real_app, swipe_auto_unlocked, mode):  # noqa: F811
    """Regression guard: the key on, the ban says so, and without it the row is the seamless Apply's."""
    app, _ = real_app
    _readiness(app, mode)
    assert app._swipe_readiness_row().detail == (
        f"the automatic swipe will not try {L1920.label} again until you press Dismiss")
    app.swipe_bg._banned.clear()
    assert app._swipe_readiness_row().detail == (
        "l @ 1280 covers 91 % of the dancers - press 'Apply l @ 1280' (under Try other settings): it switches "
        "between two frames, the show runs on")


def test_key_off_gui_shows_p5s_apply_and_swipe_tooltips():
    """The GUI: P5's Apply label on one line (as it was), P5's tooltips while the key is off; the swipe button's
    tooltip follows the mode's combo."""
    dpg = pytest.importorskip("dearpygui.dearpygui")
    import gui_builder
    from gui import WallDanceGUI
    dpg.create_context()
    try:
        noop = lambda *a, **k: None  # noqa: E731
        mock = SimpleNamespace(config={}, _on_check_readiness=noop, _on_calibrate=noop, _on_capture_plate=noop,
                               _on_dryrun=noop, _on_swipe=noop, _on_swipe_apply=noop, _on_swipe_auto=noop,
                               _on_swipe_dismiss=noop, _btn_standby_theme=dpg.add_theme())
        with dpg.window(label="keyoff"):
            gui_builder.build_phase_wall(mock)
        assert dpg.get_value("swipe_apply_tip") == P5_APPLY_TIP
        assert dpg.get_value("swipe_btn_tip").endswith(P5_SWIPE_TIP_END)
        g = SimpleNamespace()
        WallDanceGUI.show_swipe_result(g, {"reason": "r", "switch": True, "winner": "l @ 1920 x 1056",
                                           "apply_seamless": False, "apply_label": "Apply l @ 1920 x 1056 (STANDBY)",
                                           "apply_tip": P5_APPLY_TIP})
        cfg = dpg.get_item_configuration("swipe_apply_btn")
        assert cfg["label"] == "Apply l @ 1920 x 1056 (STANDBY)" and cfg["show"]
        WallDanceGUI.sync_combo(None, "swipe_auto", "Auto: suggest")
        assert dpg.get_value("swipe_btn_tip").endswith("nothing switches\nuntil you Apply.  Press again to cancel.")
        WallDanceGUI.sync_combo(None, "swipe_auto", "Auto: off")
        assert dpg.get_value("swipe_btn_tip").endswith(P5_SWIPE_TIP_END)
    finally:
        dpg.destroy_context()


def test_a_remote_apply_is_standby_only_and_the_key_stays_control():
    """A remote Apply: STANDBY only, whatever the key (P5's class).  The key itself: control -- setting it off
    during a show stays possible (the failure sentences ask for it)."""
    from services.remote_api import POLICY, check_policy
    assert POLICY["ApplySettingsSwipe"] == "heavy"
    assert check_policy("ApplySettingsSwipe", {}, "run", control_enabled=True) is not None
    assert check_policy("ApplySettingsSwipe", {}, "standby", control_enabled=False) is None
    assert POLICY["SetSwipeAuto"] == "control"
    assert check_policy("SetSwipeAuto", {"mode": "off"}, "run", control_enabled=True) is None


# ----------------------------------------------------------------------------------------------------------
# Turning the key off while P6 is under way
# ----------------------------------------------------------------------------------------------------------
def _held_by_the_app(bg):
    """The switch with the hold WallDanceApp.__init__ gives it: the app's own rule on a fake app."""
    appmod = _app()
    rule = getattr(appmod.WallDanceApp, "_swipe_switch_held", None)       # none before the key-off fix
    if rule is not None:
        bg.switch.why_not = lambda req: rule(SimpleNamespace(swipe_bg=bg), req)
    return bg.switch



def test_regression_guard_the_apps_switch_holds_while_the_key_is_off(real_app, swipe_auto_unlocked):  # noqa: F811
    """The real app's switch carries the app's hold (was: a grep of ``__init__``'s source): with the key off a switch
    asked earlier does not go on (checked before its load, its warm-up and the swap), with the key on it does."""
    app, _ = real_app
    req = {"winner": L1280, "text": "switching to l @ 1280 (Apply)", "auto": False}
    assert app.swipe_switch.why_not(req) == "the automatic swipe is off"
    _on(app, "suggest")
    assert app.swipe_switch.why_not(req) is None
    app._cb_swipe_auto("off")
    assert app.swipe_switch.why_not(req) == "the automatic swipe is off"


def test_key_off_before_the_load_abandons_the_seamless_apply():
    """The operator's seamless Apply asked (the key on), the key off before the worker loads it: no load, no swap,
    the Apply told it did not happen."""
    live = {"v": _live()}
    loader, port = _Loader(), _Port()
    bg, _, events = _bg(live, loader=loader, port=port, mode="suggest")
    sw = _held_by_the_app(bg)
    done = []
    assert bg.request_switch(L1280, "switching to l @ 1280 (Apply)", on_done=lambda ok, t: done.append((ok, t))) \
        is None
    bg.set_mode("off")
    sw.service()
    assert loader.loads == [] and sw._request is None and sw.busy() is None
    assert not sw.drain() and port.calls == []
    assert done == [(False, "not switched to l @ 1280: the automatic swipe is off")] and sw.fault is None


def test_key_off_during_the_load_frees_the_model_and_does_not_warm_it():
    """The key off while the worker loads the winner: the load ends (it cannot be stopped), then no warm-up, no
    swap; the loaded model goes with the models the switch frees."""
    live = {"v": _live()}
    port = _Port()
    holder = {}

    class _OffDuringLoad(_Loader):
        def load(self, path, cand, model=None):
            out = super().load(path, cand, model)
            holder["bg"].set_mode("off")                  # the operator, while TensorRT deserialises
            holder["runner"] = out[1]
            return out
    bg, _, _ = _bg(live, loader=_OffDuringLoad(), port=port, mode="suggest")
    holder["bg"] = bg
    sw = _held_by_the_app(bg)
    done = []
    bg.request_switch(L1280, "switching to l @ 1280 (Apply)", on_done=lambda ok, t: done.append((ok, t)))
    sw.service()
    assert holder["runner"].calls == []                   # never warmed
    assert sw._request is None and not sw.drain() and port.calls == []
    assert len(sw._drop) == 1 and sw._drop[0][1] is holder["runner"]
    assert done == [(False, "not switched to l @ 1280: the automatic swipe is off")]


def test_key_off_between_the_warm_up_and_the_frame_does_not_swap():
    """Prepared (loaded and warmed), the key off before the loop's next frame: no swap, the model freed."""
    live = {"v": _live()}
    port = _Port()
    bg, _, _ = _bg(live, port=port, mode="suggest")
    sw = _held_by_the_app(bg)
    done = []
    bg.request_switch(L1280, "switching to l @ 1280 (Apply)", on_done=lambda ok, t: done.append((ok, t)))
    sw.service()
    assert sw._request is not None
    bg.set_mode("off")
    assert sw.drain() is True and port.calls == [] and sw._probation is None
    assert len(sw._drop) == 1 and sw.busy() is None
    assert done == [(False, "not switched to l @ 1280: the automatic swipe is off")]


def test_an_automatic_switch_asked_after_the_key_went_off_does_not_happen():
    """The look's advice read the mode before the operator set it off: its request comes after -- no swap; the
    resident is the looker's to release, at its next look."""
    from runtime.seamless_switch import Prepared
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    port = _Port()
    bg, _, _ = _bg(live, port=port, mode="auto")
    sw = _held_by_the_app(bg)
    model, runner, _ = _Loader().load(L1280.engine_file, L1280)           # a resident that warms and runs
    bg.resident = r = Resident(L1280, model, runner, bg._resident_key(live["v"]), 0.4)
    bg.set_mode("off")
    assert sw.request(L1280, "switching to l @ 1280: clearly better", auto=True,
                      prepared=Prepared(r.candidate, r.model, r.runner)) is None
    sw.service()
    assert runner.calls == []                               # never warmed
    assert not sw.drain() and port.calls == [] and sw._drop == [] and sw.fault is None
    bg.tick()
    assert bg.resident is None


def test_with_the_key_on_the_hold_changes_nothing():
    """Regression guard: the key on, the app's hold lets the operator's seamless Apply through."""
    live = {"v": _live()}
    port = _Port()
    bg, _, _ = _bg(live, port=port, mode="suggest")
    sw = _held_by_the_app(bg)
    bg.request_switch(L1280, "switching to l @ 1280 (Apply)")
    sw.service()
    assert sw.drain() is True and port.calls and port.calls[0][0] == "swap"


def test_key_off_stops_a_look_under_way():
    """Regression guard: the key off during a look -- it stops at its next frame, says nothing, and the resident it
    loaded goes at the next look."""
    bg, proc, events, loader, live = _looking_bg(mode="suggest")
    proc.frames = _look_frames()
    infer = loader.infer

    def off_at_the_third(images, cand, runner, **kw):
        pace = kw.get("pace")

        def paced():
            if loader.infers == 2:
                bg.set_mode("off")
            if pace is not None:
                pace()
        return infer(images, cand, runner, **{**kw, "pace": paced})
    loader.infer = off_at_the_third
    assert bg.tick() == "run"
    assert loader.infers == 3 and bg.suggestion is None and bg.last_look["outcome"] == "cancelled"
    assert not [k for k, _ in events if k in ("suggest", "switching")]
    assert bg.resident is not None
    bg.tick()
    assert bg.resident is None


# ----------------------------------------------------------------------------------------------------------
# A project load
# ----------------------------------------------------------------------------------------------------------
def _swipe_toasts(events):
    return [t for t in _toasts(events) if t.startswith("Automatic swipe")]


@pytest.mark.parametrize("mode,word", [("suggest", "suggest"), ("auto", "switch")])
@pytest.mark.parametrize("full", [False, True], ids=["partial", "full"])
def test_a_project_load_that_turns_the_key_on_says_so_once(real_app, swipe_auto_unlocked, mode, word,  # noqa: F811
                                                           full, capsys):
    app, events = real_app
    app.swipe_bg.start = lambda: None
    app._apply_config_without_model({"model": X1280.model, "swipe_auto": mode} if full else {"swipe_auto": mode})
    assert app.swipe_bg.mode == mode
    assert _swipe_toasts(events) == [f"Automatic swipe: {word} (from the project)"]
    assert capsys.readouterr().out.count(f"[SwipeAuto] mode {mode} (from the project)") == 1


def test_a_project_load_with_the_key_off_says_nothing(real_app, swipe_auto_unlocked, capsys):  # noqa: F811
    """Regression guard: off -- silent, as before."""
    app, events = real_app
    _on(app, "suggest")
    capsys.readouterr()
    events.clear()
    app._apply_config_without_model({"swipe_auto": "off"})
    assert app.swipe_bg.mode == "off"
    assert _swipe_toasts(events) == []
    assert "SwipeAuto" not in capsys.readouterr().out


def test_regression_guard_a_full_project_load_with_the_key_off_runs_off_and_lifts_the_bans(  # noqa: F811
        real_app, swipe_auto_unlocked, capsys):
    """A project file has a model (a full load) and swipe_auto off: off -- silently (since 28/11 a project with no
    key runs the default: test_swipe_auto_lock).  The load lifts the bans (``clear_bans``: failed automatic
    switches may retry) and tells the panel whether anything still waits for Dismiss (``SettingsSwipeHold``).
    None of the three was reached by the fakes (a partial config)."""
    app, events = real_app
    _on(app, "suggest")
    app.swipe_bg._banned[L1920.key] = L1920.engine_file
    cleared = []
    clear = app.swipe_bg.clear_bans
    app.swipe_bg.clear_bans = lambda: cleared.append("clear_bans") or clear()
    capsys.readouterr()
    events.clear()
    app._apply_config_without_model({"model": X1280.model, "swipe_auto": "off"})
    assert app.swipe_bg.mode == "off", "the key off on a full project load left the automatic swipe on"
    assert cleared == ["clear_bans"] and app.swipe_bg._banned == {}
    assert [e.held for e in events if type(e).__name__ == "SettingsSwipeHold"] == [False]
    assert _swipe_toasts(events) == [] and "SwipeAuto" not in capsys.readouterr().out
    app.swipe_switch.fail("the switch to l @ 1280 failed (RuntimeError: x): x @ 1280 still runs - rebuild it")
    events.clear()
    app._apply_config_without_model({"model": X1280.model, "swipe_auto": "off"})
    assert [e.held for e in events if type(e).__name__ == "SettingsSwipeHold"] == [True]   # a fault still waits
