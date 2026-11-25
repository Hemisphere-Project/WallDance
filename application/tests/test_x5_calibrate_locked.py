"""X5 (Plan Cordée rev 2): what Calibrate set is locked.

08/10: exposure was changed by hand several times, the sensor crop twice, and the enhancement switched off (one
key or one box did it); nothing warned, and the plate was then from another exposure or crop.  Now: no single key
toggles the enhancement; its box and the camera's crop / gain / exposure are expert-only; enhancement off is red
in the Advanced drawer and on the Live panel; a change of exposure, gain or crop by hand invalidates the plate at
once.  Headless (viewport-less DearPyGui) and stub tests.
"""
from types import SimpleNamespace

import numpy as np
import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


class _Gui:
    def __init__(self, theme, texture, config, expert=False):
        self.config = config
        self.callbacks = {}
        self.expert_mode = expert
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


def _build(config, expert=False):
    import gui_builder
    dpg.create_context()
    theme = dpg.add_theme()
    with dpg.texture_registry():
        tex = dpg.add_dynamic_texture(4, 4, [0.0] * 64)
    gui_builder.build_ui(_Gui(theme, tex, config, expert))


def _shown(tag):
    return bool(dpg.get_item_configuration(tag)["show"])


def test_operator_surface_hides_what_calibrate_sets_and_shows_enhancement_off_in_red():
    _build({"camera_type": "IDS_PEAK", "enhance_enabled": False})
    try:
        for tag in ("enhance_enable_group", "ids_expert_group"):
            assert dpg.does_item_exist(tag) and not _shown(tag), tag
        assert not _shown("ids_settings_toggle_btn")              # the gear that opened gain / exposure
        for tag in ("adv_enhance_checkbox", "adv_ids_gain_slider", "adv_ids_exposure_slider", "adv_ids_ratio_slider"):
            assert dpg.does_item_exist(tag), tag                    # moved behind expert, not lost
        assert "OFF" in dpg.get_value("enhance_state_text")
        assert _shown("live_enhance_state") and "OFF" in dpg.get_value("live_enhance_state")
    finally:
        dpg.destroy_context()


def test_expert_mode_shows_them_and_enhancement_on_is_quiet():
    _build({"camera_type": "IDS_PEAK", "enhance_enabled": True}, expert=True)
    try:
        for tag in ("enhance_enable_group", "ids_expert_group", "ids_settings_toggle_btn"):
            assert _shown(tag), tag
        assert "ON" in dpg.get_value("enhance_state_text")
        assert not _shown("live_enhance_state")
    finally:
        dpg.destroy_context()


def test_the_e_key_no_longer_toggles_the_enhancement():
    from ui.adapter import DpgUiAdapter as UiAdapter
    sent = []
    gui = SimpleNamespace(project_picker_visible=lambda: False, expert_mode=False)
    stub = SimpleNamespace(gui=gui, api=SimpleNamespace(submit=sent.append), wizard=None)
    dpg.create_context()
    try:
        UiAdapter._handle_key(stub, None, dpg.mvKey_E)
        assert sent == []
        UiAdapter._handle_key(stub, None, dpg.mvKey_T)                 # the other shortcuts stay
        assert len(sent) == 1 and type(sent[0]).__name__ == "ToggleOverlay"
    finally:
        dpg.destroy_context()


def test_a_change_by_hand_invalidates_the_plate_until_a_new_one():
    pytest.importorskip("torch")
    from core.foreground import CleanPlate
    from core.pipeline import FrameProcessor, ProcessingSettings
    proc = FrameProcessor(model=None, settings=ProcessingSettings(
        confidence=0.3, imgsz=640, use_fp16=False, enhance_enabled=False, enhance_lite=False,
        enhance_force=False, person_height_px=200, use_gpu_path=False))
    plate = CleanPlate(plate=np.full((100, 50), 120.0, np.float32), sigma=0.5, frame_size=(200, 400))
    proc.invalidate_plate("nothing loaded")                     # no plate: nothing to invalidate
    assert proc._plate_invalid is None
    proc.set_clean_plate(plate)
    proc._belt_gray = np.full((400, 200), 120, np.uint8)
    proc._belt_gray_offset = (0, 0)
    assert proc._fg_frame(200, 400) is not None and proc.plate_status == "ready"
    proc.invalidate_plate("exposure changed by hand - take the empty wall again")
    proc._fg_cached = False
    assert proc._fg_frame(200, 400) is None and proc.last_fg.reason == "plate invalid"
    assert proc.plate_status == "stale"
    proc.set_clean_plate(plate)                                  # Calibrate / "capture empty wall"
    proc._fg_cached = False
    assert proc._fg_frame(200, 400) is not None


def test_the_ids_command_invalidates_the_plate_the_project_load_does_not():
    import app as app_mod
    calls, inval = [], []
    cams = SimpleNamespace(_cb_ids_ratio_change=lambda v: calls.append(("ratio", v)),
                           _cb_ids_gain_change=lambda v: calls.append(("gain", v)),
                           _cb_ids_exposure_change=lambda v: calls.append(("exposure", v)))
    stub = SimpleNamespace(cameras=cams, processor=SimpleNamespace(invalidate_plate=inval.append))
    from runtime import api
    app_mod.WallDanceApp._cmd_set_ids_param(stub, api.SetIdsParam("exposure_us", 12000.0))
    app_mod.WallDanceApp._cmd_set_ids_param(stub, api.SetIdsParam("ratio", 1.38))
    assert calls == [("exposure", 12000.0), ("ratio", 1.38)]
    assert len(inval) == 2 and "exposure" in inval[0] and "sensor crop" in inval[1]
