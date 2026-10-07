"""The operator's ROI survives frames of another size (2026-10-07: playing a 1488x1528 portrait take in
the 1776x1300 night project clamped the stored ROI width 1322 -> 1254 for good, and a save persisted it).
The frame-size clamp now only fits the live rect to the frames flowing; the drawn ROI and its source
frame stay, come back exactly on frames of their size, and are what a config save writes."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("dearpygui.dearpygui")

from core.input_transform import IDENTITY, InputTransform  # noqa: E402
from runtime.roi_state import RoiState  # noqa: E402
from ui.roi_mask_editor import RoiMaskEditor  # noqa: E402

LAND, PORT = (1776, 1300), (1488, 1528)
NIGHT_ROI = (234, 3, 1322, 1275)


def _editor():
    settings = SimpleNamespace(roi_x=0, roi_y=0, roi_w=0, roi_h=0, roi_enabled=True)
    state = RoiState(settings, LAND)
    ed = RoiMaskEditor(state=state, settings=settings, processor=None, gui=lambda: None,
                       request_reprocess=lambda: None)
    ed._set_roi_rect(*NIGHT_ROI, frame_w=LAND[0], frame_h=LAND[1],
                     sync_ui=False, request_reprocess=False)          # the project's config load
    return ed, settings, state


def _live(settings):
    return settings.roi_x, settings.roi_y, settings.roi_w, settings.roi_h


def test_a_foreign_size_take_fits_the_live_roi_and_going_back_restores_it():
    ed, settings, state = _editor()
    ed._clamp_roi_to_source(*PORT, sync_ui=False)                     # a portrait take plays
    assert _live(settings) == (234, 3, 1254, 1275)                    # fitted to the 1488 px frame
    assert state.source_size == PORT
    assert state.drawn == NIGHT_ROI + LAND                            # the operator's ROI is kept
    ed._clamp_roi_to_source(*LAND, sync_ui=False)                     # back to LIVE / a landscape take
    assert _live(settings) == NIGHT_ROI and state.source_size == LAND


def test_a_save_during_foreign_playback_keeps_the_drawn_roi():
    ed, settings, state = _editor()
    ed._clamp_roi_to_source(*PORT, sync_ui=False)
    assert state.stored_roi() == NIGHT_ROI + LAND
    from app import WallDanceApp
    app = MagicMock()
    app.roi.state = state
    app.processor.get_exclusion_state.return_value = ((16, 10), [], [], [])
    cfg = WallDanceApp._get_saveable_config(app)
    assert (cfg["roi_x"], cfg["roi_y"], cfg["roi_w"], cfg["roi_h"]) == NIGHT_ROI
    assert (cfg["roi_source_w"], cfg["roi_source_h"]) == LAND


def test_an_operator_edit_on_a_foreign_frame_becomes_the_drawn_roi():
    ed, settings, state = _editor()
    ed._clamp_roi_to_source(*PORT, sync_ui=False)
    ed._cb_roi_reset()                                                # explicit: full portrait frame
    assert state.drawn == (0, 0) + PORT + PORT
    ed._clamp_roi_to_source(*LAND, sync_ui=False)
    assert _live(settings) == (0, 0, 1488, 1300)                      # that drawn ROI, fitted


def test_without_a_drawn_roi_the_live_rect_is_fitted_as_before():
    settings = SimpleNamespace(roi_x=100, roi_y=50, roi_w=2000, roi_h=200)
    state = RoiState(settings, LAND)
    assert state.rect_for_frame(*PORT) == (100, 50, 1388, 200)
    assert state.stored_roi() == (100, 50, 2000, 200) + LAND


def test_the_drawn_roi_follows_an_input_transform():
    ed, settings, state = _editor()
    t = InputTransform(False, 90)
    delta = t.after(IDENTITY.inverse())
    state.apply_transform(delta)
    out = t.output_size(*LAND)
    assert state.drawn[4:] == out
    assert state.drawn[:4] == t.map_rect(*NIGHT_ROI, *LAND)
    assert _live(settings) == state.drawn[:4]
