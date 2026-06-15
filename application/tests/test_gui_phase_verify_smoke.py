"""Headless DPG build smoke for the phase-5 Verify panel (OPERATOR_V2 Phase 5).

Builds the real ``gui_builder.build_phase_verify`` in a viewport-less DearPyGui
context and renders readiness rows via the real ``WallDanceGUI.show_readiness_rows``
(called unbound -- it never touches ``self``).  Pure UI smoke; no model/camera.
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def test_phase_verify_builds_and_renders_readiness_rows():
    import gui_builder
    from gui import WallDanceGUI

    dpg.create_context()
    try:
        th = dpg.add_theme()
        noop = lambda *a, **k: None
        mock = SimpleNamespace(
            config={},
            _on_check_readiness=noop,
            _on_dryrun=noop,
            _btn_standby_theme=th,
        )
        with dpg.window(label="smoke"):
            gui_builder.build_phase_verify(mock)

        assert dpg.does_item_exist("phase_panel_verify")
        assert dpg.does_item_exist("check_readiness_btn")
        assert dpg.does_item_exist("readiness_rows_container")
        assert dpg.does_item_exist("dryrun_btn")
        assert dpg.does_item_exist("dryrun_result_text")

        # Render readiness rows (unbound; show_readiness_rows ignores self).
        rows = [
            {"name": "camera", "status": "ok", "detail": "IDS @ 19.0 FPS"},
            {"name": "osc", "status": "warn", "detail": "probe sent (UDP)"},
            {"name": "disk", "status": "fail", "detail": "1.2 GB free"},
            {"name": "gpu", "status": "skip", "detail": "no NVIDIA stats"},
        ]
        WallDanceGUI.show_readiness_rows(None, rows)
        children = dpg.get_item_children("readiness_rows_container", 1)
        assert children is not None and len(children) == 4

        # A second render replaces the rows (no accumulation).
        WallDanceGUI.show_readiness_rows(None, rows[:2])
        children = dpg.get_item_children("readiness_rows_container", 1)
        assert children is not None and len(children) == 2

        # Empty result renders a single placeholder, not a crash.
        WallDanceGUI.show_readiness_rows(None, [])
        children = dpg.get_item_children("readiness_rows_container", 1)
        assert children is not None and len(children) == 1

        # Dry-run result renders into the single text widget (set_value path).
        WallDanceGUI.show_dryrun_result(
            None, {"video": "slot_1.avi", "frames_processed": 300,
                   "real_tracks": 1, "marginal_tracks": 0, "ghost_tracks": 2,
                   "swap_count": 0, "zero_detection_frames": 4,
                   "avg_detections": 1.1})
        assert "300 frames" in dpg.get_value("dryrun_result_text")
        WallDanceGUI.show_dryrun_result(None, {}, error="no recordings found")
        assert "Dry-run failed" in dpg.get_value("dryrun_result_text")
    finally:
        dpg.destroy_context()
