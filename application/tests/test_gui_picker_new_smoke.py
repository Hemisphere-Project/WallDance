"""Headless DPG smoke of the launch picker's "New" button (2026-10-08): it replaces
"Start blank" (which landed in 'default'); it asks for a name and hands it to the app
(on_project_new), which creates the project from the defaults + the last camera rig."""
import types

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def _picker_gui(calls):
    from gui import WallDanceGUI

    g = types.SimpleNamespace(callbacks={"on_project_new": lambda name: calls.append(name),
                                         "on_project_blank": lambda: calls.append("BLANK")},
                              _center_modal=lambda tag, w, h: (0, 0))
    for name in ("show_project_picker", "_picker_rebuild_list", "_picker_on_select",
                 "_picker_action", "_picker_inline_new", "hide_project_picker"):
        setattr(g, name, types.MethodType(getattr(WallDanceGUI, name), g))
    return g


@pytest.mark.parametrize("projects", [[("mur30m-0710", "10-08 13:00", 3)], []])
def test_new_asks_a_name_and_hands_it_to_the_app(projects):
    calls = []
    dpg.create_context()
    try:
        g = _picker_gui(calls)
        g.show_project_picker(projects)
        assert dpg.does_item_exist("project_picker_modal")
        labels = [dpg.get_item_label(i) for i in dpg.get_all_items()
                  if dpg.get_item_type(i).endswith("mvButton")]
        assert "New" in labels and "Start blank" not in labels
        g._picker_action("new")
        assert dpg.does_item_exist("picker_new_input")
        dpg.set_value("picker_new_input", "")          # an empty name does nothing
        dpg.get_item_callback("picker_new_input")()
        assert calls == [] and dpg.does_item_exist("project_picker_modal")
        dpg.set_value("picker_new_input", "show-0810")
        dpg.get_item_callback("picker_new_input")()
        assert calls == ["show-0810"]
        assert not dpg.does_item_exist("project_picker_modal")
    finally:
        dpg.destroy_context()
