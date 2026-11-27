"""P6 (Plan Cordee), the automatic swipe locked off in this build (``core.config.SWIPE_AUTO_UNLOCKED`` False): a
blind review accepted merging it off, but off was one click away (the combo under 'Try other settings', the remote
SetSwipeAuto in RUN) while two groups of its findings are open.  Locked, the combo is not on screen (the panel is
P5's), any mode but off is refused whoever asks, with one toast, and a project whose key is not off loads off,
said once, with nothing of P6 started.  Unlocked (the ``swipe_auto_unlocked`` fixture), all three are as they were.

The doors are tested on a real ``WallDanceApp`` (no GUI, no main loop, CPU), the combo on the headless panel."""
from types import SimpleNamespace

import pytest

from test_swipe_auto_live import swipe_auto_unlocked  # noqa: F401 - the fixture

REFUSED = "Automatic swipe: not released in this build - it stays off"
READ_OFF = "Automatic swipe: off - this project asks for {}, which is not released in this build"
MODES = [("suggest", "Auto: suggest", "suggest"), ("auto", "Auto: switch", "switch")]


@pytest.fixture
def real_app():
    """A real WallDanceApp and every event it publishes; its P6 threads stopped after."""
    pytest.importorskip("dearpygui")
    import app as appmod
    app = appmod.WallDanceApp()
    events = []
    app.bus.subscribe(events.append)
    yield app, events
    app.swipe_bg.stop(1.0)
    app.swipe_switch.stop(1.0)


def _toasts(events):
    return [e.message for e in events if type(e).__name__ == "Toast"]


def _combo_syncs(events):
    return [e.value for e in events if type(e).__name__ == "ControlSync" and e.name == "swipe_auto"]


def _remote(app):
    """The remote API with 'Allow remote control' on, in RUN: the review's scenario."""
    from services.remote_api import RemoteApi
    remote = RemoteApi(app.api, app.bus, token="t", roots={}, state_fn=lambda: "run")
    remote.control_enabled = True
    return remote


def _pick(app, label):
    """The operator picks ``label`` in the combo: gui._on_swipe_auto -> the adapter's SetSwipeAuto -> the app."""
    from gui import WallDanceGUI
    gui = SimpleNamespace(callbacks=app.ui._build_callbacks(), _SWIPE_AUTO_MODES=WallDanceGUI._SWIPE_AUTO_MODES)
    WallDanceGUI._on_swipe_auto(gui, "swipe_auto_combo", label)
    app.api.drain()


def _p6_runs(app):
    return app.swipe_bg._thread is not None or app.swipe_switch._thread is not None


def _wall_panel(dpg):
    """The wall phase's panel, headless, opened as the GUI opens it."""
    import gui_builder
    noop = lambda *a, **k: None  # noqa: E731
    mock = SimpleNamespace(config={}, _on_check_readiness=noop, _on_calibrate=noop, _on_capture_plate=noop,
                           _on_dryrun=noop, _on_swipe=noop, _on_swipe_apply=noop, _on_swipe_auto=noop,
                           _on_swipe_dismiss=noop, _btn_standby_theme=dpg.add_theme())
    with dpg.window(label="wall"):
        gui_builder.build_phase_wall(mock)
    dpg.configure_item("phase_panel_wall", show=True)


def _on_screen(dpg, tag):
    item = tag
    while item:
        if not dpg.get_item_configuration(item)["show"]:
            return False
        item = dpg.get_item_parent(item)
    return True


# ----------------------------------------------------------------------------------------------------------
# Locked: the build as it ships
# ----------------------------------------------------------------------------------------------------------
def test_the_build_ships_with_the_automatic_swipe_locked_off():
    import core.config
    assert core.config.SWIPE_AUTO_UNLOCKED is False
    assert core.config.SWIPE_AUTO == "off"


def test_locked_the_combo_is_not_on_screen_and_a_sync_of_it_still_lands():
    dpg = pytest.importorskip("dearpygui.dearpygui")
    from gui import WallDanceGUI
    from gui_builder import SWIPE_BTN_TIP_OFF
    dpg.create_context()
    try:
        _wall_panel(dpg)
        assert _on_screen(dpg, "swipe_btn")                        # P5's button, as before P6
        assert not _on_screen(dpg, "swipe_auto_combo"), "the combo is on screen while the swipe is locked off"
        WallDanceGUI.sync_combo(None, "swipe_auto", "Auto: off")    # what the app syncs at a refusal, a load
        assert dpg.get_value("swipe_auto_combo") == "Auto: off"
        assert dpg.get_value("swipe_btn_tip") == SWIPE_BTN_TIP_OFF
        assert not _on_screen(dpg, "swipe_auto_combo")
    finally:
        dpg.destroy_context()


@pytest.mark.parametrize("mode,label,word", MODES)
def test_locked_the_combo_and_the_remote_command_are_refused_and_off_is_accepted(real_app, mode, label, word, capsys):
    app, events = real_app
    assert _remote(app).submit("SetSwipeAuto", {"mode": mode})[0] == 200
    app.api.drain()
    assert app.swipe_bg.mode == "off", "the remote SetSwipeAuto turned the automatic swipe on while locked"
    _pick(app, label)
    assert app.swipe_bg.mode == "off", "the combo turned the automatic swipe on while locked"
    assert app.swipe_bg.state == "off" and not _p6_runs(app)
    assert _toasts(events) == [REFUSED, REFUSED]
    assert _combo_syncs(events) == ["Auto: off", "Auto: off"]
    assert capsys.readouterr().out.count(f"[SwipeAuto] {mode} refused") == 2
    events.clear()
    _pick(app, "Auto: off")                                         # off: always, silently as before
    assert _remote(app).submit("SetSwipeAuto", {"mode": "off"})[0] == 200
    app.api.drain()
    assert app.swipe_bg.mode == "off" and not _p6_runs(app)
    assert _toasts(events) == [] and _combo_syncs(events) == ["Auto: off", "Auto: off"]


@pytest.mark.parametrize("mode,label,word", MODES)
def test_locked_a_project_that_asks_for_it_loads_off_says_so_once_and_starts_nothing(real_app, mode, label, word,
                                                                                      capsys):
    app, events = real_app
    app._apply_config_without_model({"swipe_auto": mode})
    assert app.swipe_bg.mode == "off", "the project's key turned the automatic swipe on while locked"
    assert not _p6_runs(app)                                        # no looker thread, no switch worker
    assert _toasts(events) == [READ_OFF.format(word)]
    out = capsys.readouterr().out
    assert out.count(f"[SwipeAuto] mode off - this project asks for {mode}, which is not released in this build") == 1
    assert "(from the project)" not in out
    assert "swipe_auto" not in app._get_saveable_config()           # Save: the session's mode is off, no key


# ----------------------------------------------------------------------------------------------------------
# Unlocked: as before the lock
# ----------------------------------------------------------------------------------------------------------
def test_unlocked_the_combo_is_on_screen(swipe_auto_unlocked):  # noqa: F811
    dpg = pytest.importorskip("dearpygui.dearpygui")
    dpg.create_context()
    try:
        _wall_panel(dpg)
        assert _on_screen(dpg, "swipe_auto_combo")
    finally:
        dpg.destroy_context()


@pytest.mark.parametrize("mode,label,word", MODES)
def test_unlocked_the_combo_and_the_remote_command_set_the_mode(real_app, swipe_auto_unlocked,  # noqa: F811
                                                                mode, label, word):
    app, events = real_app
    assert _remote(app).submit("SetSwipeAuto", {"mode": mode})[0] == 200
    app.api.drain()
    assert app.swipe_bg.mode == mode and app.swipe_bg._thread.is_alive()
    _pick(app, "Auto: off")
    assert app.swipe_bg.mode == "off"
    _pick(app, label)
    assert app.swipe_bg.mode == mode
    assert REFUSED not in _toasts(events)
    assert _combo_syncs(events) == [label, "Auto: off", label]


@pytest.mark.parametrize("mode,label,word", MODES)
def test_unlocked_a_project_that_asks_for_it_turns_it_on(real_app, swipe_auto_unlocked, mode, label,  # noqa: F811
                                                         word, capsys):
    app, events = real_app
    app._apply_config_without_model({"swipe_auto": mode})
    assert app.swipe_bg.mode == mode and app.swipe_bg._thread.is_alive()
    assert _toasts(events) == [f"Automatic swipe: {word} (from the project)"]
    assert capsys.readouterr().out.count(f"[SwipeAuto] mode {mode} (from the project)") == 1
    assert app._get_saveable_config()["swipe_auto"] == mode
