"""Headless DPG build smoke for Advanced > Expert tools (D29, 2026-10-07: the lighting
profiles, the dancers pass, the auto-tune and the known-N tune left the operator rail) + the
auto-tune (CLAHE x confidence sweep) result renderer (OPERATOR_V2 / AUTOTUNE_DESIGN §7).

Builds the real ``gui_builder.build_expert_tools`` viewport-less and renders a
sweep result via the real ``WallDanceGUI.show_calib_sweep_result`` (unbound).
Pure UI smoke; no model/camera/subprocess.
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def test_expert_tools_build_and_render_sweep_result():
    import gui_builder
    from gui import WallDanceGUI

    dpg.create_context()
    try:
        th = dpg.add_theme()
        noop = lambda *a, **k: None
        mock = SimpleNamespace(
            config={},
            _icon_font=None,
            _on_profile_switch=noop,
            _on_dryrun=noop,
            _on_calib2=noop,
            _on_calib_sweep=noop,
            _on_calib_sweep_apply=noop,
            _on_known_n=noop,
            _on_known_n_apply=noop,
            _btn_standby_theme=th,
        )
        with dpg.window(label="smoke"):
            gui_builder.build_expert_tools(mock)

        assert not dpg.does_item_exist("phase_panel_calibrate")   # off the rail (D29)
        assert dpg.does_item_exist("section_expert")
        assert dpg.does_item_exist("profile_switch_radio")
        assert dpg.does_item_exist("dryrun_btn")
        assert dpg.does_item_exist("calib2_btn")
        assert dpg.does_item_exist("calib2_pool_inline")
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

        # Known-N tune (K1) widgets + result rendering.
        assert dpg.does_item_exist("known_n_btn")
        assert dpg.does_item_exist("known_n_result_text")
        assert dpg.does_item_exist("known_n_apply_btn")
        assert dpg.get_item_configuration("known_n_apply_btn")["show"] is False
        WallDanceGUI.show_known_n_result(None, {
            "baseline_score": 0.61, "tuned_score": 0.51, "delta": -0.10, "evals": 34,
            "final": {"confidence": 0.15, "tracker_max_age": 60},
            "changed": {"tracker_max_age": 60}})
        kn_txt = dpg.get_value("known_n_result_text")
        assert "0.61" in kn_txt and "tracker_max_age=60" in kn_txt
        assert dpg.get_item_configuration("known_n_apply_btn")["show"] is True
        WallDanceGUI.show_known_n_result(None, {}, error="no scenarios")
        assert "Known-N tune failed" in dpg.get_value("known_n_result_text")
        assert dpg.get_item_configuration("known_n_apply_btn")["show"] is False
    finally:
        dpg.destroy_context()
