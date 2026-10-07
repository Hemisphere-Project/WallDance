"""Headless DPG build smoke of the WHOLE window (gui_builder.build_ui): every phase panel, the
Advanced drawer with its Expert tools, the toolbar and the recordings bar in one context, so a
duplicate tag or a builder that lost a widget fails here instead of at app start (the phase
tests build one panel each).  The gui is duck-typed: themes resolve to one real theme, every
``_on_*`` / ``_toggle*`` callback to a no-op.  Pure UI smoke; no model/camera.
"""
import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


class _Gui:
    def __init__(self, theme, texture):
        self.config = {}
        self.callbacks = {}
        self.expert_mode = False
        self.video_width, self.video_height = 640, 480
        self.frame_texture_tag = texture
        self._middle_height = 600
        self._icon_font = None
        self._theme = theme

    def __getattr__(self, name):
        if name.endswith("_theme"):
            return self._theme
        if name.startswith(("_on_", "_toggle", "on_")):
            return lambda *a, **k: None
        raise AttributeError(name)


def test_the_whole_window_builds_with_the_d29_rail():
    import gui_builder

    dpg.create_context()
    try:
        theme = dpg.add_theme()
        with dpg.texture_registry():
            tex = dpg.add_dynamic_texture(4, 4, [0.0] * 64)
        gui_builder.build_ui(_Gui(theme, tex))
        for pid, _label in gui_builder.PHASES:
            assert dpg.does_item_exist(f"phase_panel_{pid}") and dpg.does_item_exist(f"phase_btn_{pid}")
        for gone in ("phase_panel_profile", "phase_panel_aim", "phase_panel_calibrate",
                     "phase_panel_verify", "output_smoothing_slider"):
            assert not dpg.does_item_exist(gone), gone
        # the operator widgets moved, not lost
        for tag in ("calibrate_btn", "calibrate_status", "aim_last_calib_text", "fg_status_wall_text",
                    "check_readiness_btn", "topbar_config_combo", "foreground_checkbox", "fg_status_text",
                    "box_clamp_checkbox", "section_expert", "profile_switch_radio", "calib2_btn",
                    "calib2_pool_inline", "calib_sweep_btn", "known_n_btn", "dryrun_btn",
                    "person_height_slider"):
            assert dpg.does_item_exist(tag), tag
    finally:
        dpg.destroy_context()
