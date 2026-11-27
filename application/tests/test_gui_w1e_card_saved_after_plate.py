"""W1e (#t-060, 27/11): the Scene Calibration card stays « Saved to project. » when its plate ends after a Save.

X2's card says « Wall light: being measured » while Calibrate's empty-wall picture is still being taken, and comes
again with the verdict when the picture ends (``calibrate_plate_ended``).  A Save made in between was followed by a
fresh card offering « Save to project » / « Keep this session only » again.  Now the card published again carries
``saved`` when the project was saved since that Calibrate ended, and the GUI shows it « Saved to project. » with
Close.  A change after the save -- a new Calibrate -- offers Save again.  The real flows and plate capture (X2b's
rig), the app's real UI adapters, the GUI's real event routes and its real card (headless DearPyGui).
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")

import test_x2b_card_wall_light as x2b              # noqa: E402  X2b's rig: Calibrate, its card, its plate


def _project_saved(r):
    """What a successful SaveConfig does here (app.py): the config manager's « Saved! » through the app's real
    _ConfigUiAdapter, then the pending plate written to the project (A6 ②)."""
    import app as app_mod
    app_mod._ConfigUiAdapter(r.app).show_save_indicator("Saved!")
    r.flows.commit_pending_plate()


def _cards(r):
    return [e for e in r.events if isinstance(e, r.api.CalibReportCard)]


# ------------------------------------------------------------------------------------------- the events
def test_a_save_while_the_wall_light_is_measured_comes_again_saved(tmp_path):
    r = x2b._rig(tmp_path, playing=True, kind="flat", wall_check=False)
    x2b._calibrate(r)
    (first,) = _cards(r)
    assert x2b._light_lines(first.summary) == [x2b.MEASURING] and first.saved is False
    _project_saved(r)                                 # « Save to project » while it says « being measured »
    x2b._frames(r, 60)                                # the picture ends
    cards = _cards(r)
    assert len(cards) == 2 and cards[1].saved is True
    assert x2b._light_lines(cards[1].summary) == [x2b._row_line(r)]         # the verdict still comes
    # a change after the save: a new Calibrate -- its card offers Save, and so does its plate's
    x2b._calibrate(r)
    x2b._frames(r, 60)
    later = _cards(r)[2:]
    assert later and all(c.saved is False for c in later), [(c.saved, c.summary[-40:]) for c in later]


def test_a_save_before_the_calibrate_ended_is_not_its_save(tmp_path):
    r = x2b._rig(tmp_path, playing=True, kind="flat", wall_check=False)
    _project_saved(r)                                 # the project saved, then Calibrate changes it
    x2b._calibrate(r)
    x2b._frames(r, 60)
    cards = _cards(r)
    assert len(cards) == 2 and [c.saved for c in cards] == [False, False]


def test_a_new_calibrate_between_the_save_and_the_plate_forgets_the_save(tmp_path):
    r = x2b._rig(tmp_path, playing=True, kind="hot", wall_check=False)
    x2b._calibrate(r)
    _project_saved(r)
    r.flows._cb_calibrate()                           # pressed again before the picture ended
    x2b._frames(r, 60)
    r.flows._cb_calibrate()                           # ... cancelled
    x2b._frames(r, 10)
    assert [c.saved for c in _cards(r)] == [False]    # the old card is not shown again, nor marked saved


# ------------------------------------------------------------------------------------------- the card on screen
def _buttons():
    out = []
    for c in dpg.get_item_children("calibration_result_buttons", 1):
        kind = dpg.get_item_type(c)
        if kind.endswith("Button"):
            out.append(("button", dpg.get_item_label(c)))
        elif kind.endswith("Text"):
            out.append(("text", dpg.get_value(c)))
    return out


SAVE_OFFERED = [("button", "Save to project"), ("button", "Keep this session only")]
SAVED = [("text", "Saved to project."), ("button", "Close")]


def _press_label(label):
    for c in dpg.get_item_children("calibration_result_buttons", 1):
        if dpg.get_item_type(c).endswith("Button") and dpg.get_item_label(c) == label:
            dpg.get_item_configuration(c)["callback"]()
            return
    raise AssertionError(f"no button {label!r}: {_buttons()}")


@pytest.mark.parametrize("how", ["the card's button", "Ctrl+S"])
def test_on_screen_the_card_stays_saved_when_its_plate_ends(tmp_path, how):
    from gui import WallDanceGUI as G
    from ui.adapter import DpgUiAdapter
    r = x2b._rig(tmp_path, playing=True, kind="flat", wall_check=False)
    me = SimpleNamespace(_center_modal=lambda tag, w, h: [0, 0])
    me.mark_calibration_result_saved = lambda: G.mark_calibration_result_saved(me)
    me.show_calibration_result_dialog = lambda s, on_save: G.show_calibration_result_dialog(me, s, on_save)
    me.show_save_indicator = lambda m: G.show_save_indicator(me, m)
    api = r.api
    submitted = []

    def submit(c):                                    # the app answers SaveConfig (app.py's registration)
        submitted.append(type(c).__name__)
        if isinstance(c, api.SaveConfig):
            _project_saved(r)
    handlers = DpgUiAdapter._build_event_handlers(SimpleNamespace(
        api=SimpleNamespace(submit=submit), gui=me, wizard=SimpleNamespace(on_report_card=lambda s: False),
        _sync_control=None, _show_state=None, _show_import_progress=None))
    seen = [0]

    def route():                                      # the bus's events, in order, through the GUI's routes
        while seen[0] < len(r.events):
            e = r.events[seen[0]]
            seen[0] += 1
            if isinstance(e, (api.CalibReportCard, api.ConfigSaved)):
                handlers[type(e)](e)

    dpg.create_context()
    try:
        x2b._calibrate(r)
        route()
        assert _buttons() == SAVE_OFFERED             # the card says the wall light is being measured
        if how == "Ctrl+S":
            _project_saved(r)                         # the shortcut (or a remote SaveConfig): the card stays open
            route()
            assert _buttons() == SAVED
        else:
            _press_label("Save to project")           # closes the card, then saves
            route()
            assert submitted == ["SaveConfig"] and not dpg.does_item_exist("calibration_result_modal")
        x2b._frames(r, 60)                            # the empty-wall picture ends: the card comes again
        route()
        assert len(_cards(r)) == 2 and _cards(r)[1].saved is True
        assert dpg.does_item_exist("calibration_result_modal")
        assert _buttons() == SAVED                    # « Saved to project. », Close -- no Save offered
        _press_label("Close")
        assert not dpg.does_item_exist("calibration_result_modal")
        # a change after the save: a new Calibrate -- its card offers Save again, and so after its plate
        x2b._calibrate(r)
        route()
        assert _buttons() == SAVE_OFFERED
        x2b._frames(r, 60)
        route()
        assert len(_cards(r)) == 4 and _buttons() == SAVE_OFFERED
        assert submitted == (["SaveConfig"] if how != "Ctrl+S" else [])
    finally:
        dpg.destroy_context()
