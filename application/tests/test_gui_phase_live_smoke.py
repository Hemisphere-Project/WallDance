"""Headless DPG build smoke for the phase-6 Live panel (OPERATOR_V2 Track O/X).

Builds the real ``gui_builder.build_phase_live`` in a viewport-less DearPyGui
context with a duck-typed gui, asserting the panel (incl. the batch-2 output
controls: box-clamp checkbox + output-smoothing slider) constructs without a
DPG error.  Pure UI smoke -- no model, camera, or render loop.
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def test_phase_live_builds_with_output_controls():
    import gui_builder

    dpg.create_context()
    try:
        th1 = dpg.add_theme()
        th2 = dpg.add_theme()
        noop = lambda *a, **k: None
        mock = SimpleNamespace(
            config={},
            _on_state_standby=noop,
            _on_state_run=noop,
            _on_sensitivity_change=noop,
            _on_gap_bridging_change=noop,
            _on_box_clamp_toggle=noop,
            _on_output_smoothing_change=noop,
            _btn_standby_theme=th1,
            _btn_run_active_theme=th2,
        )
        # build_visualization_toolbar pulls deeper gui state; stub it -- this
        # smoke targets the phase-6 panel + the new output controls.
        orig_toolbar = gui_builder.build_visualization_toolbar
        gui_builder.build_visualization_toolbar = noop
        try:
            with dpg.window(label="smoke"):
                gui_builder.build_phase_live(mock)
        finally:
            gui_builder.build_visualization_toolbar = orig_toolbar

        assert dpg.does_item_exist("phase_panel_live")
        assert dpg.does_item_exist("sensitivity_slider")        # Dial A
        assert dpg.does_item_exist("gap_bridging_slider")       # Dial B
        assert dpg.does_item_exist("box_clamp_checkbox")
        assert dpg.does_item_exist("output_smoothing_slider")
        assert dpg.does_item_exist("lagged_latency_text")
        # The lagged-tap + case-2 suppression checkboxes were removed (2026-06):
        # the single /walldance/dancer/* stream is selected by L alone.
        assert not dpg.does_item_exist("lagged_tap_checkbox")
        assert not dpg.does_item_exist("lagged_suppress_checkbox")
        # defaults: box-clamp ON, smoothing L=1 (causal/live).
        assert dpg.get_value("box_clamp_checkbox") is True
        assert dpg.get_value("output_smoothing_slider") == 1
    finally:
        dpg.destroy_context()
