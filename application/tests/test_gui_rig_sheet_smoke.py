"""Headless DPG build smoke for the phase-1 Rig sheet (MRK-0).

Builds the section in a viewport-less context with a duck-typed gui, checks
every RIG_FIELDS key has its widget, that edits route to the on_rig_field
callback, and that ControlSync('input', 'rig.<field>') pushes values back in
(text and numeric, None = cleared)."""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def test_rig_sheet_builds_and_syncs():
    import gui_builder
    from core.config_schema import RIG_FIELDS
    from gui import WallDanceGUI

    calls = []
    dpg.create_context()
    try:
        mock = SimpleNamespace(
            config={"rig": {"lens": "Tamron M118FM08 (8 mm)", "focal_mm": 8.0}},
            _on_rig_field=lambda sender, value, user_data=None: calls.append((user_data, value)),
        )
        with dpg.window(label="smoke"):
            gui_builder.build_rig_sheet_section(mock)
        rows = {field for field, _label, _kind in gui_builder.RIG_SHEET_ROWS}
        assert rows == set(RIG_FIELDS)
        for field in RIG_FIELDS:
            assert dpg.does_item_exist(f"rig_{field}_input"), field
        assert dpg.get_value("rig_lens_input") == "Tamron M118FM08 (8 mm)"
        assert dpg.get_value("rig_focal_mm_input") == pytest.approx(8.0)
        assert dpg.get_value("rig_f_number_input") == pytest.approx(0.0)   # unknown

        # edits carry the field name as user_data
        cb = dpg.get_item_callback("rig_f_number_input")
        cb("rig_f_number_input", 1.4, dpg.get_item_user_data("rig_f_number_input"))
        assert calls == [("f_number", 1.4)]

        # runtime -> GUI sync (config load / remote edit)
        WallDanceGUI.sync_input(None, "rig.f_number", 2.8)
        WallDanceGUI.sync_input(None, "rig.lens", None)
        WallDanceGUI.sync_input(None, "rig.notes", "venue B, IR on truss")
        assert dpg.get_value("rig_f_number_input") == pytest.approx(2.8)
        assert dpg.get_value("rig_lens_input") == ""
        assert dpg.get_value("rig_notes_input") == "venue B, IR on truss"
    finally:
        dpg.destroy_context()
