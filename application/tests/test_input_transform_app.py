"""REQ-5 app wiring: WallDanceApp._set_input_transform on a duck-typed app.

A Mirror/Rotate change must reach every frame source (cameras + playback),
re-push the preview geometry, keep the recorder writing RAW frames at the
sensor size, and carry the ROI rect + the exclusion masks (both lighting
profiles) across so they keep covering the same physical region. A config
load applies the transform WITHOUT remapping (its ROI/mask are already in its
space). Plus the Calib1 accumulators after a grid swap.
"""
import types
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch")                      # app imports the pipeline
from app import WallDanceApp, _RecordingCameraAdapter  # noqa: E402
from camera.camera_manager import CameraManager   # noqa: E402
from core.calibration import ExclusionMaskBuilder  # noqa: E402
from core.input_transform import IDENTITY, InputTransform  # noqa: E402
from core.video_recorder import VideoRecorder      # noqa: E402
from runtime import api                            # noqa: E402
from runtime.roi_state import RoiState             # noqa: E402

RAW = (1920, 1080)
RAW_ROI = (100, 50, 400, 300)
RAW_CELLS = [(0, 0), (15, 9), (4, 2)]
RAW_ADD = [(3, 2)]


def _fake_app(tmp_path):
    ids_camera = pytest.importorskip("camera.ids_camera")
    uni = ids_camera.UnifiedCamera()
    uni._cv_camera = CameraManager()
    uni._cv_camera.raw_size = RAW
    uni._source_type = ids_camera.CameraSource.OPENCV
    uni._set_sensor_size(*RAW)
    uni.is_open = True
    camera = CameraManager()
    camera.state.is_open = True
    camera.state.width, camera.state.height = RAW
    excl = ExclusionMaskBuilder()
    excl.set_cells((16, 10), RAW_CELLS, manual_add=RAW_ADD)
    settings = SimpleNamespace(roi_x=RAW_ROI[0], roi_y=RAW_ROI[1], roi_w=RAW_ROI[2],
                               roi_h=RAW_ROI[3], roi_enabled=True)
    calls, events = [], []
    bus = api.EventBus()
    bus.subscribe(events.append)
    recorder = VideoRecorder(projects_dir=str(tmp_path))
    mock = SimpleNamespace(
        input_transform=IDENTITY,
        unified_camera=uni,
        camera=camera,
        recorder=recorder,
        _use_unified_camera=True,
        recording=SimpleNamespace(_apply_playback_dimensions=lambda: calls.append("pbdims")),
        roi=SimpleNamespace(state=RoiState(settings, RAW),
                            _sync_roi_ui=lambda: calls.append("roi_ui"),
                            _sync_mask_ui=lambda: calls.append("mask_ui")),
        processor=SimpleNamespace(get_exclusion_state=excl.get_state,
                                  set_exclusion=excl.set_cells,
                                  bg_subtractor=SimpleNamespace(has_reference=True)),
        configs=SimpleNamespace(
            _active_profile="show",
            _profiles={"show": {"exclusion_grid": [16, 10], "exclusion_cells": [[9, 9]]},
                       "rehearsal": {"exclusion_grid": [16, 10],
                                     "exclusion_cells": [list(c) for c in RAW_CELLS],
                                     "confidence": 0.4}}),
        calibration=SimpleNamespace(_calibrating=False, _calibrating2=False),
        bus=bus,
        _camera_preview_geometry=lambda w, h: calls.append(("geometry", w, h)),
        _cb_bg_clear=lambda: calls.append("bg_clear"),
        _cb_tracker_reset=lambda: calls.append("tracker_reset"),
        _request_reprocess=lambda: calls.append("reprocess"),
    )
    for name in ("_set_input_transform", "_cmd_set_input_transform",
                 "_sync_input_transform_ui", "_sync"):
        setattr(mock, name, types.MethodType(getattr(WallDanceApp, name), mock))
    return mock, excl, settings, calls, events


def _expected_mask(t):
    cols, rows = t.map_grid(16, 10)
    return ((cols, rows), sorted(tuple(t.map_cell(c, r, 16, 10)) for c, r in RAW_CELLS),
            sorted(tuple(t.map_cell(c, r, 16, 10)) for c, r in RAW_ADD))


@pytest.mark.parametrize("steps", [
    [InputTransform(False, 90)],
    [InputTransform(True, 0)],
    [InputTransform(False, 90), InputTransform(True, 90), InputTransform(True, 270)],
    [InputTransform(True, 180), IDENTITY],
])
def test_change_moves_roi_and_masks_with_the_image(tmp_path, steps):
    app, excl, settings, calls, events = _fake_app(tmp_path)
    for t in steps:
        app._cmd_set_input_transform(api.SetInputTransform(mirror=t.mirror,
                                                           rotation=t.rotation))
    t = steps[-1]
    assert app.input_transform == t
    out = t.output_size(*RAW)
    # every source + the reported size follow; the recorder still gets RAW
    assert app.unified_camera.input_transform == t
    assert (app.unified_camera.width, app.unified_camera.height) == out
    assert (app.camera.state.width, app.camera.state.height) == out
    assert app.recorder.input_transform == t and app.camera.input_transform == t
    assert _RecordingCameraAdapter(app).record_dimensions() == RAW
    assert ("geometry",) + out in calls
    # ROI keeps covering the same physical region (composition of the steps)
    assert (settings.roi_x, settings.roi_y, settings.roi_w, settings.roi_h) == \
        t.map_rect(*RAW_ROI, *RAW)
    assert app.roi.state.source_size == out
    # live mask (auto + manual) and the OTHER profile's stored mask follow
    grid, auto, add, rem = excl.get_state()
    egrid, eauto, eadd = _expected_mask(t)
    assert (grid, auto, add, rem) == (egrid, eauto, eadd, [])
    reh = app.configs._profiles["rehearsal"]
    assert reh["exclusion_grid"] == list(egrid) and reh["confidence"] == 0.4
    assert [tuple(c) for c in reh["exclusion_cells"]] == eauto
    assert app.configs._profiles["show"]["exclusion_cells"] == [[9, 9]]   # active: live wins
    # models learned in the old orientation are dropped
    assert "tracker_reset" in calls and "bg_clear" in calls
    toast = [e for e in events if isinstance(e, api.Toast)][-1]
    assert "ROI and mask moved" in toast.message
    syncs = {(e.kind, e.name): e.value for e in events if isinstance(e, api.ControlSync)}
    assert syncs[("checkbox", "input_mirror")] == t.mirror
    assert syncs[("combo", "input_rotation")] == t.rotation


def test_rotation_asks_for_recalibration(tmp_path):
    app, *_ , events = _fake_app(tmp_path)
    app._cmd_set_input_transform(api.SetInputTransform(rotation=270))
    assert "Re-run Calibrate" in [e for e in events if isinstance(e, api.Toast)][-1].message
    app, *_, events = _fake_app(tmp_path)
    app._cmd_set_input_transform(api.SetInputTransform(mirror=True))
    assert "Calibrate" not in [e for e in events if isinstance(e, api.Toast)][-1].message


def test_round_trip_restores_roi_and_mask(tmp_path):
    app, excl, settings, calls, events = _fake_app(tmp_path)
    before = excl.get_state()
    app._cmd_set_input_transform(api.SetInputTransform(mirror=True, rotation=90))
    app._cmd_set_input_transform(api.SetInputTransform(mirror=False, rotation=0))
    assert (settings.roi_x, settings.roi_y, settings.roi_w, settings.roi_h) == RAW_ROI
    assert excl.get_state() == before


def test_refused_while_calibrating_and_noop_when_unchanged(tmp_path):
    app, excl, settings, calls, events = _fake_app(tmp_path)
    app.calibration._calibrating = True
    app._cmd_set_input_transform(api.SetInputTransform(rotation=90))
    assert app.input_transform == IDENTITY
    assert "calibration" in [e for e in events if isinstance(e, api.Toast)][-1].message
    app.calibration._calibrating = False
    calls.clear()
    app._cmd_set_input_transform(api.SetInputTransform(mirror=False))   # same value
    assert "tracker_reset" not in calls and not any(c[0] == "geometry" for c in calls
                                                    if isinstance(c, tuple))


def test_config_load_applies_without_remap(tmp_path):
    app, excl, settings, calls, events = _fake_app(tmp_path)
    before = excl.get_state()
    app._set_input_transform(InputTransform(False, 90), remap=False)
    assert app.input_transform == InputTransform(False, 90)
    assert (app.camera.state.width, app.camera.state.height) == (1080, 1920)
    assert (settings.roi_x, settings.roi_y, settings.roi_w, settings.roi_h) == RAW_ROI
    assert excl.get_state() == before and "tracker_reset" not in calls


def test_playback_dims_follow_when_playing(tmp_path, monkeypatch):
    app, *_ , calls, events = _fake_app(tmp_path)
    monkeypatch.setattr(type(app.recorder), "is_playing", property(lambda self: True))
    app._cmd_set_input_transform(api.SetInputTransform(rotation=90))
    assert "pbdims" in calls and not any(isinstance(c, tuple) for c in calls)


def test_calib1_after_a_grid_swap():
    """set_cells with cols/rows swapped (90° rotation) must not break the next
    Calib1 pass (accumulators follow the grid)."""
    b = ExclusionMaskBuilder()
    b.set_cells((10, 16), [(0, 0)], manual_add=[(9, 15)])
    b.start()
    mask = np.zeros((160, 100), np.uint8)
    mask[:16, :10] = 255                              # motion in cell (0, 0)
    for _ in range(max(b.min_frames, 5)):
        b.observe(mask, [(0.95, 0.97)])               # a skeleton in cell (9, 15)
    res = b.build()
    assert res.grid == (10, 16)
    assert (0, 0) in b.effective_cells() and (9, 15) in b.effective_cells()
