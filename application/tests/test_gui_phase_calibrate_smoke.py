"""Headless DPG build smoke for the phase-4 Calibrate panel + the auto-tune
(CLAHE x confidence sweep) result renderer (OPERATOR_V2 / AUTOTUNE_DESIGN §7).

Builds the real ``gui_builder.build_phase_calibrate`` viewport-less and renders a
sweep result via the real ``WallDanceGUI.show_calib_sweep_result`` (unbound).
Pure UI smoke; no model/camera/subprocess.
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def test_phase_calibrate_builds_and_renders_sweep_result():
    import gui_builder
    from gui import WallDanceGUI

    dpg.create_context()
    try:
        th = dpg.add_theme()
        noop = lambda *a, **k: None
        mock = SimpleNamespace(
            config={},
            _on_calib2=noop,
            _on_calib_sweep=noop,
            _on_calib_sweep_apply=noop,
            _btn_standby_theme=th,
        )
        with dpg.window(label="smoke"):
            gui_builder.build_phase_calibrate(mock)

        assert dpg.does_item_exist("phase_panel_calibrate")
        assert dpg.does_item_exist("calib2_btn")
        # Auto-tune (segment/slot CLAHE x confidence sweep) widgets.
        assert dpg.does_item_exist("calib_sweep_n")
        assert dpg.does_item_exist("calib_sweep_slot")
        assert dpg.does_item_exist("calib_sweep_btn")
        assert dpg.does_item_exist("calib_sweep_result_text")
        assert dpg.does_item_exist("calib_sweep_apply_btn")
        # Apply button starts hidden until a result lands.
        assert dpg.get_item_configuration("calib_sweep_apply_btn")["show"] is False

        # A successful sweep result renders the curve + best values and reveals Apply.
        WallDanceGUI.show_calib_sweep_result(None, {
            "best_clahe": 6.0, "best_conf": 0.55,
            "clahe_curve": {"1.0": 0.22, "2.5": 0.26, "6.0": 0.17},
            "derived": {"gamma": 4.0, "person_height_px": 284, "yolo_imgsz": 640,
                        "var_threshold": 8.0, "saturation_flags": ["gamma clamped at 4.0"]},
        })
        txt = dpg.get_value("calib_sweep_result_text")
        assert "CLAHE 6.0" in txt and "gamma clamped" in txt
        assert dpg.get_item_configuration("calib_sweep_apply_btn")["show"] is True

        # An error renders the failure and re-hides Apply.
        WallDanceGUI.show_calib_sweep_result(None, {}, error="no recordings found")
        assert "Auto-tune failed" in dpg.get_value("calib_sweep_result_text")
        assert dpg.get_item_configuration("calib_sweep_apply_btn")["show"] is False
    finally:
        dpg.destroy_context()
