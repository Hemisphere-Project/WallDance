"""W1e (#t-060, 27/11): the Calibrate button after a stop, headless (viewport-less DearPyGui).

Calibrate stops with « Stopped: someone in view, <where> » (X3) and the technician is told to press Calibrate
again.  The line went through ``set_calibrate_status``, which labels the button CANCEL while its text shows: a
button reading CANCEL that in fact starts Calibrate.  The stop is a line Calibrate leaves after it has ended, so it
goes through ``set_calibrate_note`` (X4h): it stays, it wraps, the button reads CALIBRATE.  While Calibrate runs the
button still reads CANCEL (pinned here).  The real flows (X3's rig), the app's real UI adapter on a real event bus,
the GUI's real event routes and its real Calibrate panel.
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")

import test_x3_emptiness_first as x3                 # noqa: E402  X3's rig: the flows, a fake processor

STOP = "Stopped: someone in view, middle left of the wall (x 30, y 95)"


def _wired():
    """X3's flows publishing through the app's real _CalibrationUiAdapter; the events land in a list."""
    pytest.importorskip("torch")                      # app
    import app as app_mod
    from runtime import api
    bus = api.EventBus()
    bus.ui_ready = True
    events = []
    bus.subscribe(events.append)
    flows, proc, plates = x3._rig()
    flows.ui = app_mod._CalibrationUiAdapter(SimpleNamespace(bus=bus))
    return flows, proc, plates, events, api


def _gui_routes(api):
    """The real Calibrate panel (test_gui_x4_dancer_step's build) and the real routes of the GUI adapter."""
    import test_gui_x4_dancer_step as G
    from gui import WallDanceGUI
    from ui.adapter import DpgUiAdapter
    G._build({})
    me = SimpleNamespace()
    me.set_calibrate_status = lambda t: WallDanceGUI.set_calibrate_status(me, t)
    me.set_calibrate_note = lambda t: WallDanceGUI.set_calibrate_note(me, t)
    handlers = DpgUiAdapter._build_event_handlers(SimpleNamespace(
        api=SimpleNamespace(submit=lambda c: None), gui=me, wizard=SimpleNamespace(on_progress=lambda t: None),
        _sync_control=None, _show_state=None, _show_import_progress=None))
    return handlers


def _label():
    return dpg.get_item_configuration("calibrate_btn")["label"]


def test_after_a_stop_the_line_stays_wraps_and_the_button_reads_calibrate():
    flows, proc, plates, events, api = _wired()
    handlers = _gui_routes(api)
    try:
        def route():
            while events:
                e = events.pop(0)
                if isinstance(e, api.CalibProgress):
                    handlers[api.CalibProgress](e)

        flows._cb_calibrate()
        route()
        # Calibrate runs (the emptiness watch first): its line shows, the button reads CANCEL (as before)
        assert dpg.get_value("calibrate_status") == "Checking that the wall is empty..."
        assert dpg.get_item_configuration("calibrate_status")["show"]
        assert _label() == "CANCEL"
        x3._run(flows, proc, lambda i, f: [x3.SHE] if i % 3 else [])           # someone in view: stopped
        assert not flows._calibrating and plates == []
        progress = [e for e in events if isinstance(e, api.CalibProgress)]
        assert progress[-1] == api.CalibProgress(STOP, note=True)               # published as a note
        route()
        assert dpg.get_value("calibrate_status") == STOP                        # the stop's words stay
        assert dpg.get_item_configuration("calibrate_status")["show"]
        assert dpg.get_item_configuration("calibrate_status")["wrap"] > 0      # wrapped in the panel
        assert _label() == "CALIBRATE"                                          # a press starts Calibrate, as it says
        # pressed again: Calibrate runs, the button reads CANCEL again and the stop line is replaced
        flows._cb_calibrate()
        route()
        assert flows._calibrating and _label() == "CANCEL"
        assert dpg.get_value("calibrate_status") == "Checking that the wall is empty..."
    finally:
        dpg.destroy_context()


def test_while_calibrate_runs_its_progress_keeps_cancel():
    """The existing behaviour, pinned: every line shown while a flow runs labels the button CANCEL; idle clears."""
    _flows, _proc, _plates, _events, api = _wired()
    handlers = _gui_routes(api)
    try:
        for text in ("Checking that the wall is empty...", "Calibrating exposure...", "Calibrating 40%",
                     "Checking the empty wall 50%"):
            handlers[api.CalibProgress](api.CalibProgress(text))
            assert _label() == "CANCEL" and dpg.get_value("calibrate_status") == text
        handlers[api.CalibProgress](api.CalibProgress(None))
        assert _label() == "CALIBRATE" and not dpg.get_item_configuration("calibrate_status")["show"]
    finally:
        dpg.destroy_context()
