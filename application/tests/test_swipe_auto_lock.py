"""P6 (Plan Cordee), the automatic swipe released mode by mode (``core.config.SWIPE_AUTO_RELEASED``, the build ships
``("off",)``): a blind review accepted merging it off, but off was one click away (the combo under 'Try other
settings', the remote SetSwipeAuto in RUN) while two groups of its findings are open.  The owner opens suggest
first (once the second group is read again), auto later (the third).  Only off: the combo is not on screen (the
panel is P5's), any other mode is refused whoever asks, with one toast, and a project whose key is not off loads
off, said once, with nothing of P6 started.  Off and suggest (the ``swipe_auto_suggest_only`` fixture): the combo
shows those two, suggest is set from it, remotely and from a project; auto is refused and the mode stays what it
was; a project that asks for auto loads off.  All three (the ``swipe_auto_unlocked`` fixture): as before the lock.

The doors are tested on a real ``WallDanceApp`` (no GUI, no main loop, CPU), the combo on the headless panel."""
from types import SimpleNamespace

import pytest

from test_swipe_auto_live import swipe_auto_suggest_only, swipe_auto_unlocked  # noqa: F401 - the fixtures

REFUSED = "Automatic swipe: {} is not released in this build - it stays {}"
READ_OFF = "Automatic swipe: off - this project asks for {}, which is not released in this build"
FROM_PROJECT = "Automatic swipe: {} (from the project)"
MODES = [("suggest", "Auto: suggest", "suggest"), ("auto", "Auto: switch", "switch")]
TIP_ALL = ("The automatic swipe, while the show runs:\n"
           "  off      nothing in the background (the project's default)\n"
           "  suggest  while dancers are on the wall, it tries the other model\n"
           "           at the same network input and says so here when it does\n"
           "           better on two looks in a row and is no more than 15 %\n"
           "           slower (Apply now, or Dismiss: not now, 15 min)\n"
           "  switch   the same, and when the other model is clearly better it\n"
           "           switches between two frames (an alert only if it fails)\n"
           "An empty wall costs no inference; the other model stays loaded\n"
           "while it is not off.  Saved with the project when not off.")


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


def _set_remote(app, mode):
    assert _remote(app).submit("SetSwipeAuto", {"mode": mode})[0] == 200
    app.api.drain()


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


def _combo(dpg):
    """The combo's items, and the words of its tooltip."""
    tips = [dpg.get_value(i) for i in dpg.get_all_items() if dpg.get_item_type(i) == "mvAppItemType::mvText"
            and str(dpg.get_value(i)).startswith("The automatic swipe, while the show runs:")]
    assert len(tips) == 1
    return dpg.get_item_configuration("swipe_auto_combo")["items"], tips[0]


@pytest.fixture
def panel():
    dpg = pytest.importorskip("dearpygui.dearpygui")
    dpg.create_context()
    try:
        yield dpg
    finally:
        dpg.destroy_context()


# ----------------------------------------------------------------------------------------------------------
# Off only: the build as it ships
# ----------------------------------------------------------------------------------------------------------
def test_the_build_ships_with_off_the_only_mode_released():
    import core.config
    from core.swipe_auto import released_modes
    assert core.config.SWIPE_AUTO_RELEASED == ("off",)
    assert released_modes() == ("off",)
    assert core.config.SWIPE_AUTO == "off"


def test_off_only_the_combo_is_not_on_screen_and_holds_off_only(panel):
    """The row hidden as before (regression guard); new: its one item is off."""
    dpg = panel
    from gui import WallDanceGUI
    from gui_builder import SWIPE_BTN_TIP_OFF
    _wall_panel(dpg)
    assert _on_screen(dpg, "swipe_btn")                        # P5's button, as before P6
    assert not _on_screen(dpg, "swipe_auto_combo"), "the combo is on screen while off is the only mode released"
    assert _combo(dpg)[0] == ["Auto: off"], "the combo offers modes this build does not release"
    WallDanceGUI.sync_combo(None, "swipe_auto", "Auto: off")    # what the app syncs at a refusal, a load
    assert dpg.get_value("swipe_auto_combo") == "Auto: off"
    assert dpg.get_value("swipe_btn_tip") == SWIPE_BTN_TIP_OFF
    assert not _on_screen(dpg, "swipe_auto_combo")


@pytest.mark.parametrize("label", ["Auto: suggest", "Auto: switch"])
def test_regression_guard_a_sync_to_a_label_not_in_the_list_breaks_nothing(panel, label):
    """The app never syncs a mode it did not accept, but a sync of one lands without an error: the combo says it,
    its items stay the released ones, the row stays hidden, the swipe button's tooltip follows (P6's words)."""
    dpg = panel
    from gui import WallDanceGUI
    from gui_builder import SWIPE_BTN_TIP_ON
    _wall_panel(dpg)
    items = _combo(dpg)[0]
    WallDanceGUI.sync_combo(None, "swipe_auto", label)
    assert dpg.get_value("swipe_auto_combo") == label
    assert _combo(dpg)[0] == items and not _on_screen(dpg, "swipe_auto_combo")
    assert dpg.get_value("swipe_btn_tip") == SWIPE_BTN_TIP_ON


@pytest.mark.parametrize("mode,label,word", MODES)
def test_off_only_the_combo_and_the_remote_command_are_refused_and_off_is_accepted(real_app, mode, label, word,
                                                                                   capsys):
    """Refused as before (regression guard); new: the toast names the mode refused and the one it stays."""
    app, events = real_app
    _set_remote(app, mode)
    assert app.swipe_bg.mode == "off", "the remote SetSwipeAuto turned the automatic swipe on while not released"
    _pick(app, label)
    assert app.swipe_bg.mode == "off", "the combo turned the automatic swipe on while not released"
    assert app.swipe_bg.state == "off" and not _p6_runs(app)
    assert _toasts(events) == [REFUSED.format(word, "off")] * 2
    assert _combo_syncs(events) == ["Auto: off", "Auto: off"]
    assert capsys.readouterr().out.count(f"[SwipeAuto] {mode} refused: not released in this build, it stays off") == 2
    events.clear()
    _pick(app, "Auto: off")                                         # off: always, silently as before
    _set_remote(app, "off")
    assert app.swipe_bg.mode == "off" and not _p6_runs(app)
    assert _toasts(events) == [] and _combo_syncs(events) == ["Auto: off", "Auto: off"]


@pytest.mark.parametrize("mode,label,word", MODES)
def test_regression_guard_off_only_a_project_that_asks_for_it_loads_off_says_so_once_and_starts_nothing(
        real_app, mode, label, word, capsys):
    app, events = real_app
    app._apply_config_without_model({"swipe_auto": mode})
    assert app.swipe_bg.mode == "off", "the project's key turned the automatic swipe on while not released"
    assert not _p6_runs(app)                                        # no looker thread, no switch worker
    assert _toasts(events) == [READ_OFF.format(word)]
    out = capsys.readouterr().out
    assert out.count(f"[SwipeAuto] mode off - this project asks for {mode}, which is not released in this build") == 1
    assert "(from the project)" not in out
    assert "swipe_auto" not in app._get_saveable_config()           # Save: the session's mode is off, no key


# ----------------------------------------------------------------------------------------------------------
# Off and suggest: the build once the review's second group is closed
# ----------------------------------------------------------------------------------------------------------
def test_suggest_released_the_combo_is_on_screen_with_off_and_suggest(panel, swipe_auto_suggest_only):  # noqa: F811
    dpg = panel
    _wall_panel(dpg)
    assert _on_screen(dpg, "swipe_auto_combo"), "the combo is hidden while suggest is released"
    items, tip = _combo(dpg)
    assert items == ["Auto: off", "Auto: suggest"], f"the combo offers {items}"
    assert "  suggest  " in tip and "switch" not in tip, "the tooltip describes a mode the combo does not offer"


def test_suggest_released_it_is_set_from_the_combo_and_remotely(real_app, swipe_auto_suggest_only):  # noqa: F811
    app, events = real_app
    _pick(app, "Auto: suggest")
    assert app.swipe_bg.mode == "suggest" and app.swipe_bg._thread.is_alive(), "the combo's suggest was refused"
    _pick(app, "Auto: off")
    assert app.swipe_bg.mode == "off"
    _set_remote(app, "suggest")
    assert app.swipe_bg.mode == "suggest", "the remote suggest was refused"
    assert not [t for t in _toasts(events) if "not released" in t]
    assert _combo_syncs(events) == ["Auto: suggest", "Auto: off", "Auto: suggest"]


@pytest.mark.parametrize("how", ["combo", "remote"])
def test_suggest_released_auto_is_refused_and_the_mode_stays_suggest(real_app, swipe_auto_suggest_only,  # noqa: F811
                                                                     how, capsys):
    """Asked for auto while suggest runs (the remote command, or a combo item it no longer shows): refused, one toast
    and one log line, and suggest goes on -- not forced off."""
    app, events = real_app
    _set_remote(app, "suggest")
    thread = app.swipe_bg._thread
    capsys.readouterr()
    events.clear()
    if how == "combo":
        _pick(app, "Auto: switch")
    else:
        _set_remote(app, "auto")
    assert app.swipe_bg.mode == "suggest", f"asking for auto left the mode {app.swipe_bg.mode}"
    assert app.swipe_bg._thread is thread and thread.is_alive()
    assert _toasts(events) == [REFUSED.format("switch", "suggest")]
    assert _combo_syncs(events) == ["Auto: suggest"]
    assert capsys.readouterr().out.count("[SwipeAuto] auto refused: not released in this build, it stays suggest") == 1


def test_suggest_released_a_project_brings_it_and_one_that_asks_for_auto_loads_off(real_app,  # noqa: F811
                                                                                   swipe_auto_suggest_only, capsys):
    app, events = real_app
    app._apply_config_without_model({"swipe_auto": "suggest"})
    assert app.swipe_bg.mode == "suggest", "a project's suggest was read as off while suggest is released"
    assert _toasts(events) == [FROM_PROJECT.format("suggest")]
    assert app._get_saveable_config()["swipe_auto"] == "suggest"
    capsys.readouterr()
    events.clear()
    app._apply_config_without_model({"swipe_auto": "auto"})          # suggest live: the project's auto loads off
    assert app.swipe_bg.mode == "off"
    assert _toasts(events) == [READ_OFF.format("switch")]
    out = capsys.readouterr().out
    assert out.count("[SwipeAuto] mode off - this project asks for auto, which is not released in this build") == 1
    assert "(from the project)" not in out


# ----------------------------------------------------------------------------------------------------------
# All three: as before the lock (regression guards)
# ----------------------------------------------------------------------------------------------------------
def test_regression_guard_all_released_the_combo_is_on_screen_as_before(panel, swipe_auto_unlocked):  # noqa: F811
    dpg = panel
    _wall_panel(dpg)
    assert _on_screen(dpg, "swipe_auto_combo")
    assert _combo(dpg) == (["Auto: off", "Auto: suggest", "Auto: switch"], TIP_ALL)


@pytest.mark.parametrize("mode,label,word", MODES)
def test_regression_guard_all_released_the_combo_and_the_remote_command_set_the_mode(
        real_app, swipe_auto_unlocked, mode, label, word):  # noqa: F811
    app, events = real_app
    _set_remote(app, mode)
    assert app.swipe_bg.mode == mode and app.swipe_bg._thread.is_alive()
    _pick(app, "Auto: off")
    assert app.swipe_bg.mode == "off"
    _pick(app, label)
    assert app.swipe_bg.mode == mode
    assert not [t for t in _toasts(events) if "not released" in t]
    assert _combo_syncs(events) == [label, "Auto: off", label]


@pytest.mark.parametrize("mode,label,word", MODES)
def test_regression_guard_all_released_a_project_that_asks_for_it_turns_it_on(real_app, swipe_auto_unlocked,  # noqa: F811
                                                                              mode, label, word, capsys):
    app, events = real_app
    app._apply_config_without_model({"swipe_auto": mode})
    assert app.swipe_bg.mode == mode and app.swipe_bg._thread.is_alive()
    assert _toasts(events) == [FROM_PROJECT.format(word)]
    assert capsys.readouterr().out.count(f"[SwipeAuto] mode {mode} (from the project)") == 1
    assert app._get_saveable_config()["swipe_auto"] == mode
