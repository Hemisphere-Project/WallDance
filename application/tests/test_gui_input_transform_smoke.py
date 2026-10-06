"""Headless DPG smoke for REQ-5: Mirror / Rotate in phase 1 Rig > Input.

Builds the Input section in a viewport-less context with a duck-typed gui and
checks tags, initial values, callback routing, runtime->GUI sync and the
adapter's callback -> SetInputTransform mapping."""
import types
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def _bind(mock, cls, *names):
    for name in names:
        setattr(mock, name, types.MethodType(getattr(cls, name), mock))


def test_input_section_has_mirror_and_rotate():
    import gui_builder
    from gui import WallDanceGUI

    calls = []
    dpg.create_context()
    try:
        noop = lambda *a, **k: None
        mock = SimpleNamespace(
            config={"input_mirror": True, "input_rotation": 90,
                    "camera_sources": ["0"], "camera_source": "0"},
            callbacks={"on_input_mirror_toggle": lambda v: calls.append(("mirror", v)),
                       "on_input_rotation_change": lambda d: calls.append(("rot", d))},
            _icon_font=None,
            _on_camera_change=noop, _on_camera_refresh=noop,
            _on_ids_settings_toggle=noop, _on_input_fps_cap_toggle=noop,
            _on_ids_ratio_change=noop, _on_ids_gain_change=noop,
            _on_ids_exposure_change=noop,
        )
        _bind(mock, WallDanceGUI, "_on_input_mirror_toggle", "_on_input_rotation_change")
        with dpg.window(label="smoke"):
            gui_builder.build_input_section(mock)
        assert dpg.get_value("input_mirror_checkbox") is True
        assert dpg.get_value("input_rotation_combo") == "90°"
        assert dpg.get_item_configuration("input_rotation_combo")["items"] == \
            ["0°", "90°", "180°", "270°"]

        dpg.get_item_callback("input_mirror_checkbox")("input_mirror_checkbox", False, None)
        dpg.get_item_callback("input_rotation_combo")("input_rotation_combo", "270°", None)
        dpg.get_item_callback("input_rotation_combo")("input_rotation_combo", "junk", None)
        assert calls == [("mirror", False), ("rot", 270)]

        # runtime -> GUI (config load / remote change)
        WallDanceGUI.sync_checkbox(None, "input_mirror", False)
        WallDanceGUI.sync_combo(None, "input_rotation", 180)
        assert dpg.get_value("input_mirror_checkbox") is False
        assert dpg.get_value("input_rotation_combo") == "180°"
    finally:
        dpg.destroy_context()


def test_rotation_labels_round_trip():
    from gui_builder import rotation_from_label, rotation_label
    for deg in (0, 90, 180, 270):
        assert rotation_from_label(rotation_label(deg)) == deg
    assert rotation_label("90") == "90°" and rotation_label(45) == "0°"
    assert rotation_from_label("45°") is None and rotation_from_label(None) is None


def test_adapter_maps_transform_callbacks():
    from runtime import api
    from ui.adapter import DpgUiAdapter

    rt, bus = api.RuntimeAPI(), api.EventBus()
    cbs = DpgUiAdapter(rt, bus)._build_callbacks()
    seen = []
    rt.register(api.SetInputTransform, seen.append)
    cbs["on_input_mirror_toggle"](True)
    cbs["on_input_rotation_change"](90)
    rt.drain()
    assert seen == [api.SetInputTransform(mirror=True), api.SetInputTransform(rotation=90)]
