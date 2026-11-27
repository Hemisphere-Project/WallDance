"""X4 (hub walldance#t-055) and the band (#t-050 ②), headless (viewport-less DearPyGui): the « A DANCER, 10 s »
button under CALIBRATE, its card (Keep and save / Cancel - put back answering through the adapter's commands), the
band on the Live panel (orange when the dancer is too small for the input), and the enhancement label: after the
step it is true -- OFF names the step that turns it on, and off by the step's choice is no longer a red warning.
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


class _Gui:
    def __init__(self, theme, texture, config):
        self.config = config
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


def _build(config):
    import gui_builder
    dpg.create_context()
    theme = dpg.add_theme()
    with dpg.texture_registry():
        tex = dpg.add_dynamic_texture(4, 4, [0.0] * 64)
    gui_builder.build_ui(_Gui(theme, tex, config))


def _press(tag):
    cb = dpg.get_item_configuration(tag)["callback"]
    cb()


def test_the_calibrate_panel_has_the_dancer_step_and_the_live_panel_the_band():
    _build({"enhance_enabled": False})
    try:
        assert dpg.does_item_exist("dancer_step_btn")
        assert dpg.get_item_configuration("dancer_step_btn")["label"] == "A DANCER, 10 s"
        panel = dpg.get_item_parent("dancer_step_btn")
        while panel and dpg.get_item_alias(panel) != "phase_panel_wall":
            panel = dpg.get_item_parent(panel)
        assert panel, "the button sits in the 2 Calibrate panel"
        assert dpg.does_item_exist("live_dancer_band")
        assert "not measured" in dpg.get_value("live_dancer_band")
        assert "A DANCER, 10 s" in dpg.get_value("live_enhance_state")      # OFF names the step that fixes it
    finally:
        dpg.destroy_context()


def test_the_card_keeps_or_cancels_through_its_buttons():
    from gui import WallDanceGUI
    decisions = []
    _build({})
    try:
        me = SimpleNamespace(callbacks={"on_dancer_step_decision": decisions.append},
                             _center_modal=lambda tag, w, h: [0, 0])
        WallDanceGUI.show_dancer_step_card(me, "Set: person height 104 px; enhancement ON", "Her height: 104 px")
        assert dpg.does_item_exist("dancer_step_modal")
        texts = [dpg.get_value(c) for c in dpg.get_item_children("dancer_step_modal", 1)
                 if dpg.get_item_type(c).endswith("Text")]
        assert "Set: person height 104 px; enhancement ON" in texts
        _press("dancer_step_keep_btn")
        assert decisions == [True] and not dpg.does_item_exist("dancer_step_modal")
        WallDanceGUI.show_dancer_step_card(me, "Set: ...", "")
        _press("dancer_step_cancel_btn")
        assert decisions == [True, False] and not dpg.does_item_exist("dancer_step_modal")
    finally:
        dpg.destroy_context()


def test_the_band_shows_orange_when_too_small():
    from gui import WallDanceGUI
    from gui_constants import WARN_ORANGE, TEXT_MUTED
    _build({})
    try:
        WallDanceGUI.show_dancer_band(None, "warn", "dancer ~75 px at 1280: too small for this input - use the "
                                                    "larger one (yolo_shape 1920x1056 gives ~112 px)")
        assert "too small for this input" in dpg.get_value("live_dancer_band")
        col = dpg.get_item_configuration("live_dancer_band")["color"]
        assert [round(c * 255) for c in col[:3]] == list(WARN_ORANGE[:3])
        WallDanceGUI.show_dancer_band(None, "ok", "dancer ~112 px at 1920x1056")
        col = dpg.get_item_configuration("live_dancer_band")["color"]
        assert [round(c * 255) for c in col[:3]] == list(TEXT_MUTED[:3])
    finally:
        dpg.destroy_context()


def test_off_by_the_steps_choice_is_not_a_red_warning():
    from gui import WallDanceGUI
    _build({"enhance_enabled": False})
    try:
        me = SimpleNamespace(config={"enhance_enabled": False}, _update_enhance_row_state=lambda *a, **k: None)
        me.update_enhance_state = lambda en: WallDanceGUI.update_enhance_state(me, en)
        WallDanceGUI.sync_checkbox(me, "enhance", False)
        assert dpg.get_item_configuration("live_enhance_state")["show"]
        assert dpg.get_value("enhance_state_text").startswith("ENHANCEMENT OFF - run A DANCER, 10 s")
        WallDanceGUI.sync_checkbox(me, "enhance_none_chosen", True)          # the step chose none
        assert not dpg.get_item_configuration("live_enhance_state")["show"]
        assert "the dancer step found none that helps her" in dpg.get_value("enhance_state_text")
        WallDanceGUI.sync_checkbox(me, "enhance", True)                       # the step (or expert) turned it on
        assert "ON" in dpg.get_value("enhance_state_text")
        assert not dpg.get_item_configuration("live_enhance_state")["show"]
    finally:
        dpg.destroy_context()


def test_the_adapter_sends_the_step_and_the_decision_and_paints_its_events():
    from runtime import api
    from ui.adapter import DpgUiAdapter
    sent, painted = [], []
    stub_ = SimpleNamespace(api=SimpleNamespace(submit=sent.append), _on_report_issue_request=None, wizard=None)
    cbs = DpgUiAdapter._build_callbacks(stub_)
    cbs["on_dancer_step"]()
    cbs["on_dancer_step_decision"](False)
    assert sent == [api.StartDancerStep(), api.DancerStepDecision(False)]
    gui = SimpleNamespace(show_dancer_step_card=lambda line, details: painted.append(("card", line)),
                          show_dancer_band=lambda state, text: painted.append(("band", state)))
    ad = SimpleNamespace(api=stub_.api, gui=gui, wizard=None, _sync_control=None, _show_state=None,
                         _show_import_progress=None)
    handlers = DpgUiAdapter._build_event_handlers(ad)
    handlers[api.DancerStepCard](api.DancerStepCard("Set: ...", ""))
    handlers[api.DancerBand](api.DancerBand("warn", "dancer ~75 px"))
    assert painted == [("card", "Set: ..."), ("band", "warn")]
