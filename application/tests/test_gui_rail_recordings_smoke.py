"""Headless DPG build smoke for the phase rail + inline Recordings panel.

Covers two UI moves (2026-06):
  * the Advanced drawer toggle is now right-aligned ON the phase rail
    (build_phase_rail → a 2-column stretch table), and
  * the Recordings controls are inline on ONE line of the recordings bar,
    always visible — no toggle button, no floating window
    (build_drawer_bar → build_recordings_content).

Builds both in a viewport-less DearPyGui context with a duck-typed gui and
asserts they construct without a DPG error and keep their widget tags (so the
existing callbacks / update_recording_ui keep working).  Pure UI smoke.
"""
from types import SimpleNamespace

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")


def test_phase_rail_has_advanced_button():
    import gui_builder
    from gui_builder import PHASES

    dpg.create_context()
    try:
        th = dpg.add_theme()
        noop = lambda *a, **k: None
        mock = SimpleNamespace(
            _on_phase_select=noop,
            _toggle_advanced_drawer=noop,
            _btn_standby_theme=th,
        )
        with dpg.window(label="smoke"):
            gui_builder.build_phase_rail(mock)

        assert dpg.does_item_exist("phase_rail_table")
        # Advanced toggle promoted onto the rail (right column).
        assert dpg.does_item_exist("advanced_drawer_btn")
        # Every phase still has its button + status chip (tag-addressed by
        # gui._on_phase_select / _update_phase_rail).
        for pid, _label in PHASES:
            assert dpg.does_item_exist(f"phase_btn_{pid}"), pid
            assert dpg.does_item_exist(f"phase_chip_{pid}"), pid
    finally:
        dpg.destroy_context()


def test_recordings_bar_is_inline_one_line_no_toggle():
    import gui_builder

    dpg.create_context()
    try:
        th = dpg.add_theme()
        noop = lambda *a, **k: None
        mock = SimpleNamespace(
            _rec_live_active_theme=th,
            _rec_btn_theme=th,
            _slot_empty_theme=th,
            _icon_font=None,
            _on_rec_live=noop,
            _on_rec_toggle=noop,
            _on_rec_slot_click=noop,
            _on_playback_speed_change=noop,
            _on_playback_pause=noop,
            _on_playback_prev_frame=noop,
            _on_playback_next_frame=noop,
            _on_report_issue=noop,
        )
        # build_drawer_bar IS the recordings bar now (controls inline on one line).
        with dpg.window(label="smoke"):
            gui_builder.build_drawer_bar(mock)

        # No toggle button, no hidden panel, no old floating window — the controls
        # are shown directly on the bar.
        assert not dpg.does_item_exist("recordings_drawer_btn")
        assert not dpg.does_item_exist("recordings_inline_panel")
        assert not dpg.does_item_exist("recordings_drawer_window")
        # All transport / slot tags preserved so update_recording_ui still works.
        for tag in ("rec_live_btn", "rec_rec_btn", "rec_status_text",
                    "rec_frame_counter", "rec_playback_progress", "rec_speed_combo",
                    "rec_pause_btn", "rec_prev_frame_btn", "rec_next_frame_btn",
                    "rec_report_issue_btn", "source_status_group",
                    "source_playback_group"):
            assert dpg.does_item_exist(tag), tag
        for slot in range(1, 11):
            assert dpg.does_item_exist(f"rec_slot_{slot}_btn"), slot
        # The transport sub-group stays hidden until playback (update_recording_ui).
        assert dpg.get_item_configuration("source_playback_group")["show"] is False
    finally:
        dpg.destroy_context()
