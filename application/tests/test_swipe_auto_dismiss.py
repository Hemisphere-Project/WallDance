"""P6 (Plan Cordee): Dismiss stays reachable while it has something to lift -- the switch's fault, a setting the
automatic swipe leaves alone (a ban) -- whatever else the swipe panel shows, and the readiness row keeps saying why.

Dismiss (``swipe_dismiss_btn`` -> ``DismissSwipeNotice`` -> ``_cb_dismiss_swipe``) is the one control that lifts a
ban.  It used to show only with the fault's own panel state or with a suggestion: any later state hid it (the
operator's own « Try other settings », an Apply under way, a withdrawn suggestion), and only a later suggestion
brought it back -- which a banned setting no longer makes.  The panel now has one rule (``_show_swipe_dismiss``):
shown when its state asks for it OR while the app says something waits (``SettingsSwipeHold``).  Headless
DearPyGui (no viewport), the app's handlers on fakes, the background swipe on the CPU fakes."""
from types import SimpleNamespace

import pytest

from test_seamless_switch import L1280
from test_swipe_auto_failures import SAID, WAITS, _broken_bg
from test_swipe_auto_rollback import _first_frame_raises, _setup, _two_looks_ask_the_switch

UNBAN = "the automatic swipe will not try yolo11l-pose_1280.engine again until you press Dismiss"


def _hold(gui, held):
    from gui import WallDanceGUI
    getattr(WallDanceGUI, "show_swipe_hold", lambda g, h: None)(gui, held)


def _holds(events):
    return [e.held for e in events if type(e).__name__ == "SettingsSwipeHold"]


def test_dismiss_stays_on_screen_while_something_waits_for_it_whatever_the_panel_shows():
    dpg = pytest.importorskip("dearpygui.dearpygui")
    import gui_builder
    from gui import WallDanceGUI
    dpg.create_context()
    try:
        noop = lambda *a, **k: None  # noqa: E731
        mock = SimpleNamespace(config={}, _on_check_readiness=noop, _on_calibrate=noop, _on_capture_plate=noop,
                               _on_dryrun=noop, _on_swipe=noop, _on_swipe_apply=noop, _on_swipe_auto=noop,
                               _on_swipe_dismiss=noop, _btn_standby_theme=dpg.add_theme())
        with dpg.window(label="dismiss"):
            gui_builder.build_phase_wall(mock)

        def shown():
            return dpg.get_item_configuration("swipe_dismiss_btn")["show"]
        g = SimpleNamespace()
        WallDanceGUI.show_swipe_progress(g, "apply_failed", SAID)       # the fault: its own state shows it
        _hold(g, True)                                                   # ... and the app says it waits
        assert shown()
        later = [("progress", "collecting", "collecting 12 / 150 frames with a dancer on the wall"),
                 ("progress", "running", "trying l @ 1280 (1/1)"),
                 ("result", {"reason": "x @ 1280 covers 90 % of the dancers: keep it", "switch": False}, ""),
                 ("result", {}, "swipe cancelled: cancelled"),
                 ("progress", "applying", "switching to l @ 1280 between two frames..."),
                 ("progress", "auto_switching", "switching to l @ 1280"),
                 ("progress", "auto_switched", "switched to l @ 1280 at 21:04"),
                 ("progress", "auto_cleared", "suggestion withdrawn: the scene changed")]
        for kind, a, b in later:
            if kind == "progress":
                WallDanceGUI.show_swipe_progress(g, a, b, 0.5)
            else:
                WallDanceGUI.show_swipe_result(g, a, b)
            assert shown(), f"Dismiss hidden by {a if kind == 'progress' else (b or 'a keep verdict')} while held"
        _hold(g, False)                                                  # Dismiss (or a project load) lifted it
        assert not shown()
        WallDanceGUI.show_swipe_progress(g, "dismissed", "dismissed")
        assert not shown()
        # a suggestion still shows it, and the next state hides it again -- as before, nothing held
        WallDanceGUI.show_swipe_result(g, {"reason": "r", "switch": True, "winner": "l @ 1280"})
        assert shown()
        WallDanceGUI.show_swipe_progress(g, "collecting", "collecting 1 / 150 frames with a dancer on the wall")
        assert not shown()
        # a hold alone (a ban whose fault a later switch cleared) shows it
        _hold(g, True)
        assert shown()
    finally:
        dpg.destroy_context()


def test_the_app_tells_the_panel_when_something_waits_for_dismiss():
    pytest.importorskip("dearpygui")
    import app as appmod
    out = []
    held = {"ban": False}
    fake = SimpleNamespace(bus=SimpleNamespace(publish=out.append), _swipe_shown="background",
                           swipe=SimpleNamespace(dismiss=lambda: None),
                           swipe_switch=SimpleNamespace(fault=None, dismiss=lambda: None),
                           swipe_bg=SimpleNamespace(held=lambda: held["ban"], dismiss=lambda: None))
    appmod.WallDanceApp._swipe_notify(fake, "fault", {"text": SAID})
    assert _holds(out) == [True], "the fault does not keep Dismiss on screen"
    held["ban"] = True                         # a later switch succeeded: the fault went, the ban holds
    appmod.WallDanceApp._swipe_notify(fake, "switched", {"text": "switched to l @ 1280", "auto": False})
    assert _holds(out) == [True, True]
    held["ban"] = False
    appmod.WallDanceApp._swipe_notify(fake, "switched", {"text": "switched to l @ 1280", "auto": True})
    assert _holds(out) == [True, True, False]
    appmod.WallDanceApp._cb_dismiss_swipe(fake)
    assert _holds(out)[-1] is False and type(out[-1]).__name__ == "SettingsSwipeHold"


def test_a_ban_is_held_until_dismiss_and_said_by_the_readiness_row():
    """The broken resident: held (Dismiss has something to lift) and the row says the fault; Dismiss lifts both."""
    from core.ops_monitor import check_swipe
    bg, proc, events, loader = _broken_bg("auto")
    assert bg.tick() == "run"
    health = getattr(bg, "health", lambda: None)
    assert getattr(bg, "held", lambda: False)() and health() == WAITS
    row = check_swipe(fault=bg.switch.health() or health(), fault_recovered=True)
    assert row.status == "warn" and row.detail == SAID
    bg.switch.dismiss()
    bg.dismiss()
    assert not bg.held() and health() is None
    assert check_swipe(fault=bg.switch.health() or health(), fault_recovered=True) is None


def test_the_row_keeps_saying_the_ban_once_a_later_switch_cleared_the_fault():
    """A failed automatic switch to l @ 1280 (the fault, the ban), then the operator's Apply of it succeeds: the
    fault goes, the ban stays until Dismiss -- the readiness row says so instead of nothing."""
    from core.ops_monitor import check_swipe
    bg, proc, loader, live, port, tap = _setup()
    _two_looks_ask_the_switch(bg, proc, tap)
    _first_frame_raises(port)
    assert bg.switch.fault and bg.status()["auto_banned"] == [L1280.engine_file]
    assert bg.request_switch(L1280, "Apply now") is None
    bg.switch.service()
    assert bg.switch.drain()
    port.hooks[1]()                                      # its first frames ran well
    assert bg.switch.fault is None
    row = check_swipe(fault=bg.switch.health() or getattr(bg, "health", lambda: None)(), fault_recovered=True)
    assert row is not None and row.status == "warn" and row.detail == UNBAN
    assert bg.held()
