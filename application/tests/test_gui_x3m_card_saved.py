"""X3m (27/11, the show laptop): the Scene Calibration card after a Save, headless (viewport-less DearPyGui).

A remote `cmd SaveConfig` saved the project while the card was open, and the card stayed with « Save to project »
on it (gates/w1c/runs/m1/window_after_save.jpg).  Ctrl+S does the same: the modal does not block the shortcut.
The card's own button closes the card before it saves.  Now a project save (the « Saved! » indicator, which only a
successful project save sends) turns the card's buttons into « Saved to project. » and Close; the reading stays.
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def _gui():
    from gui import WallDanceGUI
    me = SimpleNamespace(_center_modal=lambda tag, w, h: [0, 0])
    me.mark_calibration_result_saved = lambda: WallDanceGUI.mark_calibration_result_saved(me)
    return WallDanceGUI, me


def _buttons():
    out = []
    for c in dpg.get_item_children("calibration_result_buttons", 1):
        kind = dpg.get_item_type(c)
        if kind.endswith("Button"):
            out.append(("button", dpg.get_item_label(c)))
        elif kind.endswith("Text"):
            out.append(("text", dpg.get_value(c)))
    return out


def _press(item):
    dpg.get_item_configuration(item)["callback"]()


def test_a_save_from_elsewhere_marks_the_open_card_saved_and_offers_close():
    G, me = _gui()
    saves = []
    dpg.create_context()
    try:
        G.show_calibration_result_dialog(me, "Kept gamma 1.82 / CLAHE 2.5", on_save=lambda: saves.append(1))
        assert ("button", "Save to project") in _buttons()
        G.show_save_indicator(me, "Safe defaults loaded!")              # not a project save: unchanged
        assert ("button", "Save to project") in _buttons()
        G.show_save_indicator(me, "Saved!")                             # Ctrl+S / a remote SaveConfig
        assert _buttons() == [("text", "Saved to project."), ("button", "Close")]
        assert dpg.does_item_exist("calibration_result_modal") and saves == []   # the reading stays
        G.show_save_indicator(me, "Saved!")                             # a second save: the same card
        assert _buttons() == [("text", "Saved to project."), ("button", "Close")]
        _press("calibration_result_close_btn")
        assert not dpg.does_item_exist("calibration_result_modal") and saves == []
    finally:
        dpg.destroy_context()


def test_the_cards_own_save_button_closes_it_then_saves():
    G, me = _gui()
    saves = []
    dpg.create_context()
    try:
        G.show_calibration_result_dialog(me, "Kept gamma 1.82 / CLAHE 2.5", on_save=lambda: saves.append(1))
        btn = [c for c in dpg.get_item_children("calibration_result_buttons", 1)
               if dpg.get_item_label(c) == "Save to project"][0]
        _press(btn)
        assert saves == [1] and not dpg.does_item_exist("calibration_result_modal")
        G.show_save_indicator(me, "Saved!")                             # its own save's indicator: nothing to mark
        assert not dpg.does_item_exist("calibration_result_modal")
    finally:
        dpg.destroy_context()
