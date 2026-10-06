"""Headless DPG smoke for REQ-1: IMPORT a video into a slot.

The slot picker -> file dialog -> command flow (native dialog monkeypatched;
DearPyGui fallback driven directly), the STANDBY gate on the button, and the
adapter's callback -> ImportVideoToSlot / ImportProgress -> toast mapping."""
import time
import types
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def _bind(mock, cls, *names):
    for name in names:
        setattr(mock, name, types.MethodType(getattr(cls, name), mock))


def _import_mock(submitted, slots_info=()):
    from gui import WallDanceGUI
    th = dpg.add_theme()
    mock = SimpleNamespace(
        callbacks={"on_import_video": lambda slot, path: submitted.append((slot, path))},
        _last_slots_info=list(slots_info),
        _import_last_dir="",
        _import_dialog_open=False,
        _slot_has_recording_theme=th,
        _slot_empty_theme=th,
    )
    _bind(mock, WallDanceGUI, "show_import_slot_picker", "_pick_import_file",
          "_show_import_file_dialog", "_submit_import")
    return mock


def _wait_for(pred, timeout=5.0):
    t0 = time.monotonic()
    while not pred() and time.monotonic() - t0 < timeout:
        time.sleep(0.01)
    return pred()


def test_slot_picker_then_native_dialog_submits(monkeypatch, tmp_path):
    import gui
    from core.config import RECORDING_SLOTS

    picked = str(tmp_path / "videos" / "take.mov")
    seen_dirs = []
    monkeypatch.setattr(gui, "ask_video_file_native",
                        lambda start: (seen_dirs.append(start), picked)[1])
    submitted = []
    dpg.create_context()
    try:
        mock = _import_mock(submitted, [(1, True), (2, False)])
        mock.show_import_slot_picker()
        assert dpg.does_item_exist("import_slot_picker")
        for slot in range(1, RECORDING_SLOTS + 1):
            assert dpg.does_item_exist(f"import_slot_{slot}_btn"), slot
        assert not dpg.does_item_exist(f"import_slot_{RECORDING_SLOTS + 1}_btn")
        cb = dpg.get_item_callback("import_slot_4_btn")
        cb("import_slot_4_btn", None, dpg.get_item_user_data("import_slot_4_btn"))
        assert not dpg.does_item_exist("import_slot_picker")      # closed on pick
        assert _wait_for(lambda: submitted)
        assert submitted == [(4, picked)]
        assert mock._import_last_dir == str(tmp_path / "videos")
        assert _wait_for(lambda: not mock._import_dialog_open)
        # cancel in the native dialog -> nothing submitted
        monkeypatch.setattr(gui, "ask_video_file_native", lambda start: None)
        mock._pick_import_file(2)
        assert _wait_for(lambda: not mock._import_dialog_open)
        assert submitted == [(4, picked)]
    finally:
        dpg.destroy_context()


def test_dpg_file_dialog_fallback(tmp_path):
    submitted = []
    dpg.create_context()
    try:
        mock = _import_mock(submitted)
        mock._show_import_file_dialog(6, str(tmp_path))
        assert dpg.does_item_exist("import_file_dialog")
        cb = dpg.get_item_callback("import_file_dialog")
        path = str(tmp_path / "clip.mkv")
        cb("import_file_dialog", {"file_path_name": path,
                                  "selections": {"clip.mkv": path}})
        assert submitted == [(6, path)]
        assert not dpg.does_item_exist("import_file_dialog")
    finally:
        dpg.destroy_context()


def test_native_dialog_failure_falls_back(monkeypatch, tmp_path):
    import gui

    def boom(start):
        raise RuntimeError("no display")

    monkeypatch.setattr(gui, "ask_video_file_native", boom)
    dpg.create_context()
    try:
        mock = _import_mock([])
        mock._pick_import_file(3)
        assert _wait_for(lambda: dpg.does_item_exist("import_file_dialog"))
        assert mock._import_dialog_open is False
    finally:
        dpg.destroy_context()


def test_import_button_needs_standby():
    from gui import WallDanceGUI
    from gui_builder import SystemState

    toasts, pickers = [], []
    mock = SimpleNamespace(_system_state=SystemState.RUN,
                           show_toast=lambda m, duration, color: toasts.append(m),
                           show_import_slot_picker=lambda: pickers.append(1))
    WallDanceGUI._on_import_video(mock)
    assert toasts == ["Switch to STANDBY to import a video"] and pickers == []
    mock._system_state = SystemState.STANDBY
    WallDanceGUI._on_import_video(mock)
    assert pickers == [1]


def test_adapter_maps_import_callback_and_progress_toasts():
    from runtime import api
    from ui.adapter import DpgUiAdapter

    rt, bus = api.RuntimeAPI(), api.EventBus()
    adapter = DpgUiAdapter(rt, bus)
    cbs = adapter._build_callbacks()
    seen = []
    rt.register(api.ImportVideoToSlot, seen.append)
    cbs["on_import_video"](5, "C:/Videos/take.mov")
    rt.drain()
    assert seen == [api.ImportVideoToSlot(5, "C:/Videos/take.mov")]
    toasts = []
    adapter.gui = SimpleNamespace(show_toast=lambda m, duration, color: toasts.append(
        (m, duration, color)))
    bus.publish(api.ImportProgress("error", 5, "/v/x.mov", "Import failed: nope"))
    bus.publish(api.ImportProgress("done", 5, "/v/x.mov", "Slot 5: done", 1.0, "/r/s.avi"))
    assert [t[0] for t in toasts] == ["Import failed: nope", "Slot 5: done"]
    assert toasts[0][2] != toasts[1][2]                 # error vs done colors
