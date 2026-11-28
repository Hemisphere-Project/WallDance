"""P6 (Plan Cordee), the automatic swipe released mode by mode (``core.config.SWIPE_AUTO_RELEASED``, the build ships
``("off", "suggest")`` since 27/11): a blind review accepted merging it off, but off was one click away (the combo
under 'Try other settings', the remote SetSwipeAuto in RUN) while two groups of its findings were open.  The owner
opened suggest first (the second group read again, 27/11), auto later (the third).  Only off (the
``swipe_auto_off_only`` fixture, the build until 27/11): the combo is not on screen (the panel is P5's), any other
mode is refused whoever asks, with one toast, and a project whose key is not off loads off, said once, with nothing
of P6 started.  Off and suggest (the build as it ships, and the same list set by the ``swipe_auto_suggest_only``
fixture): the combo shows those two, suggest is set from it, remotely and from a project; auto is refused and the
mode stays what it was; a project that asks for auto loads off.  All three (the ``swipe_auto_unlocked`` fixture):
as before the lock.

Since 28/11 (the owner: « auto: suggest by default ») the default is suggest while the build releases it, else off
(``core.swipe_auto.default_mode``): a project with no key runs it, said once -- "(the default)" -- and its second
model is asked for as a project's key asks for it; Save writes the key whatever the mode, so off stays off.  The app
starts off: nothing of P6 runs before a project applies.

The doors are tested on a real ``WallDanceApp`` (no GUI, no main loop, CPU), the combo on the headless panel."""
from types import SimpleNamespace

import pytest

from test_swipe_auto_live import swipe_auto_off_only, swipe_auto_suggest_only, swipe_auto_unlocked  # noqa: F401
from test_swipe_auto_live import swipe_auto_apply_released  # noqa: F401 - the fixture

REFUSED = "Automatic swipe: {} is not released in this build - it stays {}"
READ_OFF = "Automatic swipe: off - this project asks for {}, which is not released in this build"
FROM_PROJECT = "Automatic swipe: {} (from the project)"
BY_DEFAULT = "Automatic swipe: {} (the default)"
UNREADABLE_OFF = "Automatic swipe: off - this project's setting could not be read"
MODES = [("suggest", "Auto: suggest", "suggest"), ("auto", "Auto: switch", "switch")]
TIP_SUGGEST = ("  suggest  while dancers are on the wall, it tries the other model\n"
               "           at the same network input and says so here when it does\n"
               "           better on two looks in a row and is no more than 15 %\n"
               "           slower (Apply now, or Dismiss: not now, 15 min)\n")
TIP_END = ("An empty wall costs no inference; the other model stays loaded\n"
           "while it is not off.  Saved with the project.")
TIP_ALL = ("The automatic swipe, while the show runs:\n"
           "  off      nothing in the background\n"
           + TIP_SUGGEST
           + "           (the default)\n"
           "  switch   the same, and when the other model is clearly better it\n"
           "           switches between two frames (an alert only if it fails)\n"
           + TIP_END)
TIP_SHIPPED = ("The automatic swipe, while the show runs:\n"
               "  off      nothing in the background\n"
               + TIP_SUGGEST
               + "           (the default)\n"
               + TIP_END)
TIP_OFF_ONLY = ("The automatic swipe, while the show runs:\n"
                "  off      nothing in the background (the project's default)\n"
                + TIP_END)
X_MODEL = "yolo11x-pose"        # a project file names its model: its load is a full one


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


def _load(app, config):
    """A project's load; what it asks of the looker: (the mode, its thread asked to start, a click moment set --
    ``BackgroundSwipe.set_mode``: never by a project's load since 28/11, whose second model waits for a settled
    STANDBY or an empty wall, ``test_swipe_auto_load_settle``).  The thread is not started."""
    starts = []
    app.swipe_bg.start = lambda: starts.append("start")
    app._apply_config_without_model(config)
    return app.swipe_bg.mode, starts, app.swipe_bg._mode_set


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


@pytest.fixture(params=["shipped", "set"])
def suggest_released(request):
    """Off and suggest released: the build as it ships (nothing patched), and the same list set explicitly (the
    ``swipe_auto_suggest_only`` fixture: these tests keep testing it when the build ships auto too)."""
    if request.param == "set":
        request.getfixturevalue("swipe_auto_suggest_only")
    return request.param


# ----------------------------------------------------------------------------------------------------------
# The build as it ships: off and suggest (27/11)
# ----------------------------------------------------------------------------------------------------------
def test_the_build_ships_with_off_and_suggest_released():
    import core.config
    from core.swipe_auto import released_modes
    from core.swipe_auto import default_mode
    assert core.config.SWIPE_AUTO_RELEASED == ("off", "suggest")
    assert released_modes() == ("off", "suggest")
    assert core.config.SWIPE_AUTO == "suggest"                   # the wished default (28/11)
    assert default_mode() == "suggest", "a project without the key does not run suggest"


@pytest.mark.parametrize("released,default", [(("off",), "off"), (("off", "suggest"), "suggest"),
                                              (("off", "suggest", "auto"), "suggest"), (("off", "auto"), "off")])
def test_the_default_is_suggest_when_it_is_released_else_off(monkeypatch, released, default):
    """``core.swipe_auto.default_mode``, the one source, read at each call.  Fails before: there was no such
    function, the default was core.config.SWIPE_AUTO, off."""
    import core.config
    from core.swipe_auto import default_mode
    monkeypatch.setattr(core.config, "SWIPE_AUTO_RELEASED", released)
    assert default_mode() == default


def test_regression_guard_the_app_starts_off_until_a_project_applies(real_app):
    """The constructor loads nothing: the looker is built off, no thread, nothing resident, no load moment set --
    the project's load (or a new project) sets the mode."""
    app, events = real_app
    bg = app.swipe_bg
    assert bg.mode == "off" and bg.state == "off" and not _p6_runs(app)
    assert bg.resident is None and bg._mode_set is False
    assert not [t for t in _toasts(events) if t.startswith("Automatic swipe")]


def test_the_shipped_build_end_to_end_suggest_opens_auto_stays_closed_and_nothing_switches(panel, real_app, capsys):
    """Nothing patched, on a real ``WallDanceApp``: the combo shows off and suggest (its tooltip says those two);
    suggest is set from the combo and by the remote SetSwipeAuto; auto is refused by both, one toast each, the mode
    staying where it was (off, then suggest); and with the mode at suggest, a look whose advice is an automatic
    switch asks for no switch -- the last guard in ``_act`` shows it as a suggestion."""
    from core.swipe import SwipeResult, SwipeRow
    from core.swipe_auto import AutoAdvice
    from runtime.swipe_background import AUTO_NOT_RELEASED
    from test_seamless_switch import L1280, X1280, _live
    dpg = panel
    _wall_panel(dpg)
    items, tip = _combo(dpg)
    assert _on_screen(dpg, "swipe_auto_combo"), "the combo is hidden in the shipped build"
    assert items == ["Auto: off", "Auto: suggest"], f"the shipped combo offers {items}"
    assert "  suggest  " in tip and "switch" not in tip, "the tooltip describes a mode the combo does not offer"
    app, events = real_app
    bg = app.swipe_bg
    _pick(app, "Auto: switch")                                      # auto while off: refused by both doors
    _set_remote(app, "auto")
    assert bg.mode == "off" and not _p6_runs(app)
    assert _toasts(events) == [REFUSED.format("switch", "off")] * 2
    events.clear()
    _pick(app, "Auto: suggest")                                     # suggest: from the combo, then remotely
    assert bg.mode == "suggest" and bg._thread.is_alive(), "the combo's suggest was refused"
    _pick(app, "Auto: off")
    assert bg.mode == "off"
    _set_remote(app, "suggest")
    assert bg.mode == "suggest", "the remote suggest was refused"
    assert _toasts(events) == [] and _combo_syncs(events) == ["Auto: suggest", "Auto: off", "Auto: suggest"]
    thread = bg._thread
    _pick(app, "Auto: switch")                                      # auto while suggest: refused, suggest goes on
    _set_remote(app, "auto")
    assert bg.mode == "suggest" and bg._thread is thread and thread.is_alive()
    assert _toasts(events) == [REFUSED.format("switch", "suggest")] * 2
    assert _combo_syncs(events)[3:] == ["Auto: suggest"] * 2
    out = capsys.readouterr().out
    assert out.count("[SwipeAuto] auto refused: not released in this build, it stays off") == 2
    assert out.count("[SwipeAuto] auto refused: not released in this build, it stays suggest") == 2
    # the last guard: an advice to switch by itself (as mode auto would give) reaches _act while the mode is
    # suggest -- the looker stopped, so this look is the only one
    bg.stop(1.0)
    events.clear()
    said = "l @ 1280 covers 100 % of the dancers, x @ 1280 33 % (6.0 ms a frame against 6.0)"
    adv = AutoAdvice(kind="apply", level="definite", winner=L1280, seamless=True, streak=2,
                     text=f"switching to {L1280.label}: {said}", said=said)
    result = SwipeResult(X1280, L1280, True, [SwipeRow(L1280, 1.0, 0.0, 6.0, net=1.0),
                                              SwipeRow(X1280, 0.33, 0.0, 6.0, net=0.33)], "l @ 1280 does better")
    bg._act(adv, result, _live())
    assert app.swipe_switch._want is None and bg.state != "switching", "a switch auto would make was asked"
    assert not app.swipe_switch.fault, "a switch was tried (and failed: nothing loaded beside the live model)"
    assert bg.last_advice["advice"] == "suggest" and bg.last_advice["why_not_auto"] == AUTO_NOT_RELEASED
    assert bg.suggestion is not None and bg.suggestion["winner_c"] == L1280
    assert bg.mode == "suggest"
    assert not [t for t in _toasts(events) if "switching" in t.lower()]


# ----------------------------------------------------------------------------------------------------------
# Off only (the swipe_auto_off_only fixture): the build until 27/11
# ----------------------------------------------------------------------------------------------------------
def test_off_only_the_combo_is_not_on_screen_and_holds_off_only(panel, swipe_auto_off_only):  # noqa: F811
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
def test_regression_guard_a_sync_to_a_label_not_in_the_list_breaks_nothing(panel, label,
                                                                          swipe_auto_off_only):  # noqa: F811
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
                                                                                   capsys,
                                                                                   swipe_auto_off_only):  # noqa: F811
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
        real_app, mode, label, word, capsys, swipe_auto_off_only):  # noqa: F811
    app, events = real_app
    app._apply_config_without_model({"swipe_auto": mode})
    assert app.swipe_bg.mode == "off", "the project's key turned the automatic swipe on while not released"
    assert not _p6_runs(app)                                        # no looker thread, no switch worker
    assert _toasts(events) == [READ_OFF.format(word)]
    out = capsys.readouterr().out
    assert out.count(f"[SwipeAuto] mode off - this project asks for {mode}, which is not released in this build") == 1
    assert "(from the project)" not in out


def test_off_only_save_writes_the_key_off(real_app, swipe_auto_off_only):  # noqa: F811
    """A project that asks for suggest loads off; Save writes ``swipe_auto: off`` -- the key is written whatever the
    mode (28/11), the one thing a locked build does differently.  Fails before: Save wrote no key."""
    app, events = real_app
    app._apply_config_without_model({"swipe_auto": "suggest"})
    assert app._get_saveable_config().get("swipe_auto", "no key") == "off"


def test_regression_guard_off_only_a_full_load_without_the_key_runs_off_loads_nothing_says_nothing(
        real_app, capsys, swipe_auto_off_only):  # noqa: F811
    """Off the only mode released: the default is off, so a project file with no key loads as it always did."""
    app, events = real_app
    mode, starts, moment = _load(app, {"model": X_MODEL})
    assert (mode, starts, moment) == ("off", [], False) and not _p6_runs(app)
    assert not [t for t in _toasts(events) if t.startswith("Automatic swipe")]
    assert "SwipeAuto" not in capsys.readouterr().out


# ----------------------------------------------------------------------------------------------------------
# Off and suggest: the build as it ships, and the same list set explicitly
# ----------------------------------------------------------------------------------------------------------
def test_suggest_released_the_combo_is_on_screen_with_off_and_suggest(panel, suggest_released):
    dpg = panel
    _wall_panel(dpg)
    assert _on_screen(dpg, "swipe_auto_combo"), "the combo is hidden while suggest is released"
    items, tip = _combo(dpg)
    assert items == ["Auto: off", "Auto: suggest"], f"the combo offers {items}"
    assert "  suggest  " in tip and "switch" not in tip, "the tooltip describes a mode the combo does not offer"


def test_suggest_released_it_is_set_from_the_combo_and_remotely(real_app, suggest_released):
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
def test_suggest_released_auto_is_refused_and_the_mode_stays_suggest(real_app, suggest_released, how, capsys):
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


def test_suggest_released_a_project_brings_it_and_one_that_asks_for_auto_loads_off(real_app, suggest_released,
                                                                                   capsys):
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


def test_suggest_released_a_project_without_the_key_runs_the_default_said_once_and_loads_as_a_keyed_one(
        real_app, suggest_released, capsys):
    """A project file with no swipe_auto (a full load): suggest, the default -- one log line, one toast, the combo
    on « Auto: suggest » -- and the second model asked for exactly as a project whose key is suggest asks for it.
    Fails before: such a project ran off, silently."""
    app, events = real_app
    keyless = _load(app, {"model": X_MODEL})
    assert keyless[0] == "suggest", f"a project without the key runs {keyless[0]}"
    assert _toasts(events) == [BY_DEFAULT.format("suggest")] and _combo_syncs(events) == ["Auto: suggest"]
    out = capsys.readouterr().out
    assert out.count("[SwipeAuto] mode suggest (the default)") == 1 and "(from the project)" not in out
    app._cb_swipe_auto("off")
    events.clear()
    keyed = _load(app, {"model": X_MODEL, "swipe_auto": "suggest"})
    assert keyless == keyed == ("suggest", ["start"], False)       # no click moment: the settle, as a keyed one
    assert _toasts(events) == [FROM_PROJECT.format("suggest")]
    assert capsys.readouterr().out.count("[SwipeAuto] mode suggest (from the project)") == 1


@pytest.mark.parametrize("value", ["always", "", None, 1])
def test_suggest_released_a_value_that_names_no_mode_loads_off_said_once_and_save_writes_off(
        real_app, suggest_released, value, capsys):
    """A project file whose swipe_auto was edited by hand into no mode, read as a project switch reads it (the
    schema, then the load), the session running suggest: off -- the combo on « Auto: off », one log line, one toast,
    nothing asked of the looker -- and Save writes off.  Fails before: the schema dropped the value and the load
    took the default, suggest: a setting nobody could read turned a background job on."""
    from core import config_schema
    app, events = real_app
    _load(app, {"model": X_MODEL, "swipe_auto": "suggest"})
    events.clear()
    capsys.readouterr()
    flat, warnings = config_schema.validate_flat(config_schema.flatten(config_schema.migrate(
        {"model": X_MODEL, "swipe_auto": value})))
    mode, starts, moment = _load(app, flat)
    assert mode == "off", f"an unreadable setting loads {mode}"
    assert [w for w in warnings if w.startswith("swipe_auto")] == [f"swipe_auto: invalid value {value!r} read as off"]
    assert starts == [] and not moment and _combo_syncs(events) == ["Auto: off"]
    assert _toasts(events) == [UNREADABLE_OFF]
    out = capsys.readouterr().out
    assert out.count("[SwipeAuto] mode off - this project's setting could not be read") == 1
    assert "(the default)" not in out and "(from the project)" not in out
    assert app._get_saveable_config().get("swipe_auto", "no key") == "off"


def test_regression_guard_suggest_released_an_absent_key_still_runs_the_default_through_the_schema(
        real_app, suggest_released):
    """The same road (the schema, then the load) without the key: the default, suggest, as before."""
    from core import config_schema
    app, events = real_app
    flat, _ = config_schema.validate_flat(config_schema.flatten(config_schema.migrate({"model": X_MODEL})))
    assert "swipe_auto" not in flat
    assert _load(app, flat)[0] == "suggest"
    assert _toasts(events) == [BY_DEFAULT.format("suggest")]


def test_regression_guard_suggest_released_a_project_whose_key_is_off_runs_off_and_says_nothing(
        real_app, suggest_released, capsys):
    """Its key off (a full load, the session in suggest): off, nothing said, nothing of a default."""
    app, events = real_app
    _load(app, {"model": X_MODEL, "swipe_auto": "suggest"})
    events.clear()
    capsys.readouterr()
    assert _load(app, {"model": X_MODEL, "swipe_auto": "off"})[0] == "off"
    assert not [t for t in _toasts(events) if t.startswith("Automatic swipe")]
    assert "(the default)" not in capsys.readouterr().out


def _saved_and_read_back(app, tmp_path):
    """Save (the app's saveable config, as the file holds it) and read back as a project switch reads it."""
    from core import config_schema
    from core.config_store import ConfigStore
    store = ConfigStore(config_dir=str(tmp_path / "projects"),
                        last_project_file=str(tmp_path / "projects" / "last_project.txt"))
    path = store.save("c2_swipe_saved", config_schema.structure(app._get_saveable_config(), {}, "show"))
    flat, warnings = config_schema.validate_flat(config_schema.flatten(config_schema.migrate(store.load(path))))
    assert not [w for w in warnings if w.startswith("swipe_auto")]
    return flat


def test_suggest_released_off_chosen_is_saved_and_reloads_off(real_app, suggest_released, tmp_path, capsys):
    """The technician sets off (one click) on a project that ran the default: Save writes ``swipe_auto: off``, and
    the project reloads off, silently, though the default is suggest.  Fails before: Save wrote no key."""
    app, events = real_app
    _load(app, {"model": X_MODEL})                                  # no key: the default, suggest
    _pick(app, "Auto: off")
    assert app.swipe_bg.mode == "off"
    flat = _saved_and_read_back(app, tmp_path)
    assert flat.get("swipe_auto") == "off", f"Save wrote {flat.get('swipe_auto', 'no key')} for off"
    _set_remote(app, "suggest")                                     # the session moves on
    events.clear()
    capsys.readouterr()
    assert _load(app, {"model": flat["model"], "swipe_auto": flat["swipe_auto"]})[0] == "off"
    assert not [t for t in _toasts(events) if t.startswith("Automatic swipe")]
    assert "(the default)" not in capsys.readouterr().out


def test_regression_guard_suggest_released_suggest_chosen_is_saved_and_reloads_suggest(real_app, suggest_released,
                                                                                         tmp_path):
    app, events = real_app
    _pick(app, "Auto: suggest")
    flat = _saved_and_read_back(app, tmp_path)
    assert flat.get("swipe_auto") == "suggest"
    _pick(app, "Auto: off")
    events.clear()
    assert _load(app, {"model": flat["model"], "swipe_auto": flat["swipe_auto"]})[0] == "suggest"
    assert _toasts(events) == [FROM_PROJECT.format("suggest")]


@pytest.mark.parametrize("released,tip", [(None, TIP_SHIPPED), (("off",), TIP_OFF_ONLY),
                                          (("off", "suggest", "auto"), TIP_ALL)],
                         ids=["shipped", "off-only", "all"])
def test_the_tooltip_marks_the_default_only_and_says_saved_with_the_project(panel, monkeypatch, released, tip,
                                                                             swipe_auto_apply_released):  # noqa: F811
    """The default in the tooltip: « (the default) » under suggest's paragraph while suggest is the default, off's
    own words of before only while off is.  Fails before: off was marked the default and Save « when not off ».
    (A suggestion's Apply released: its tooltip while held back, ``test_swipe_auto_apply_held_back``.)"""
    import core.config
    if released is not None:
        monkeypatch.setattr(core.config, "SWIPE_AUTO_RELEASED", released)
    _wall_panel(panel)
    assert _combo(panel)[1] == tip


# ----------------------------------------------------------------------------------------------------------
# All three: as before the lock (regression guards)
# ----------------------------------------------------------------------------------------------------------
def test_regression_guard_all_released_the_combo_is_on_screen_as_before(panel, swipe_auto_unlocked):  # noqa: F811
    """Its tooltip: test_the_tooltip_marks_the_default_only_and_says_saved_with_the_project."""
    dpg = panel
    _wall_panel(dpg)
    assert _on_screen(dpg, "swipe_auto_combo")
    assert _combo(dpg)[0] == ["Auto: off", "Auto: suggest", "Auto: switch"]


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
