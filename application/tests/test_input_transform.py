"""REQ-5 — input transform (mirror horizontal / rotate 90°).

The transform is a D4 element applied where frames enter the app (IDS mono8,
OpenCV camera, slot playback). These tests lock:

* the image ops against a numpy reference (rotate clockwise, THEN mirror in
  the rotated image), for mono and BGR, non-square frames;
* identity = the very same object (the default hot path stays byte-identical);
* the coordinate maps (point / rect / grid cell) against what the image op
  actually does to a marked pixel / region / cell;
* the group algebra (inverse, composition) the ROI/mask carry-over relies on;
* the torch NCHW path (GPU-direct IDS) against the numpy op;
* config schema validation, camera read() paths and playback.
"""
import itertools
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import core.config_schema as cs
from core.input_transform import (IDENTITY, ROTATIONS, InputTransform,
                                  normalize_rotation, remap_exclusion_bundle)
from runtime.roi_state import RoiState

ALL = [InputTransform(m, r) for m in (False, True) for r in ROTATIONS]
IDS = [t.label() for t in ALL]


def _reference(img, t):
    out = np.rot90(img, k=-(t.rotation // 90))        # clockwise
    if t.mirror:
        out = np.fliplr(out)
    return np.ascontiguousarray(out)


def _img(h=6, w=10, channels=None, seed=0):
    rng = np.random.default_rng(seed)
    shape = (h, w) if channels is None else (h, w, channels)
    return rng.integers(0, 256, size=shape, dtype=np.uint8)


# ---------------------------------------------------------------------------
# Image ops
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("t", ALL, ids=IDS)
@pytest.mark.parametrize("channels", [None, 3])
def test_apply_matches_reference(t, channels):
    img = _img(channels=channels)
    out = t.apply(img)
    np.testing.assert_array_equal(out, _reference(img, t))
    assert out.flags["C_CONTIGUOUS"]
    h, w = img.shape[:2]
    assert (out.shape[1], out.shape[0]) == t.output_size(w, h)
    assert t.input_size(*t.output_size(w, h)) == (w, h)


def test_identity_is_free():
    img = _img(channels=3)
    assert IDENTITY.is_identity
    assert IDENTITY.apply(img) is img                  # no copy on the hot path
    copy = IDENTITY.apply_copy(img)
    assert copy is not img and np.array_equal(copy, img)
    assert IDENTITY.apply(None) is None and IDENTITY.apply_copy(None) is None
    assert InputTransform(True, 90).apply_copy(None) is None


@pytest.mark.parametrize("t", ALL, ids=IDS)
def test_apply_copy_owns_its_array(t):
    img = _img(channels=3)
    out = t.apply_copy(img)
    assert not np.shares_memory(out, img)
    np.testing.assert_array_equal(out, _reference(img, t))


# ---------------------------------------------------------------------------
# Coordinates vs what the image op does
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("t", ALL, ids=IDS)
def test_map_point_follows_a_marked_pixel(t):
    h, w = 7, 11
    for (col, row) in [(0, 0), (10, 0), (0, 6), (10, 6), (3, 5), (7, 1)]:
        img = np.zeros((h, w), np.uint8)
        img[row, col] = 255
        out = t.apply(img)
        (orow,), (ocol,) = np.nonzero(out)
        x, y = t.map_point(col + 0.5, row + 0.5, w, h)
        assert (int(x), int(y)) == (ocol, orow)
        assert (x - int(x), y - int(y)) == (0.5, 0.5)    # centers stay centers


@pytest.mark.parametrize("t", ALL, ids=IDS)
def test_map_rect_matches_region_bbox(t):
    h, w = 40, 64
    rect = (5, 9, 20, 13)                              # x, y, w, h
    img = np.zeros((h, w), np.uint8)
    x, y, rw, rh = rect
    img[y:y + rh, x:x + rw] = 255
    out = t.apply(img)
    rows, cols = np.nonzero(out)
    expect = (cols.min(), rows.min(), cols.max() - cols.min() + 1,
              rows.max() - rows.min() + 1)
    assert t.map_rect(*rect, w, h) == expect


@pytest.mark.parametrize("t", ALL, ids=IDS)
def test_map_cells_match_grid_image(t):
    cols, rows = 8, 6                                  # AUTOCAL_EXCL_GRID-like
    cells = [(0, 0), (7, 0), (2, 3), (7, 5), (4, 1)]
    grid = np.zeros((rows, cols), np.uint8)
    for c, r in cells:
        grid[r, c] = 1
    out = t.apply(grid)
    got = sorted([int(cc), int(rr)] for rr, cc in zip(*np.nonzero(out)))
    assert t.map_grid(cols, rows) == (out.shape[1], out.shape[0])
    assert t.map_cells(cells, cols, rows) == got


# ---------------------------------------------------------------------------
# Algebra
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("t", ALL, ids=IDS)
def test_inverse_round_trips(t):
    img = _img(channels=3)
    assert t.then(t.inverse()) == IDENTITY
    assert t.inverse().then(t) == IDENTITY
    np.testing.assert_array_equal(t.inverse().apply(t.apply(img)), img)


@pytest.mark.parametrize("a,b", list(itertools.product(ALL, ALL)),
                         ids=[f"{a.label()}|{b.label()}"
                              for a, b in itertools.product(ALL, ALL)])
def test_composition_matches_sequential_ops(a, b):
    img = _img(h=5, w=9)
    np.testing.assert_array_equal(a.then(b).apply(img), b.apply(a.apply(img)))
    assert b.after(a) == a.then(b)


def test_group_closure_has_eight_elements():
    assert len({a.then(b) for a in ALL for b in ALL}) == 8


# ---------------------------------------------------------------------------
# Torch NCHW (IDS GPU-direct path) == numpy op
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("t", ALL, ids=IDS)
def test_apply_tensor_matches_numpy(t):
    torch = pytest.importorskip("torch")
    img = _img(h=6, w=10, channels=3)
    tensor = torch.from_numpy(img).permute(2, 0, 1).unsqueeze(0).float().contiguous()
    out = t.apply_tensor(tensor)
    if t.is_identity:
        assert out is tensor
    assert out.is_contiguous()
    back = out[0].permute(1, 2, 0).numpy().astype(np.uint8)
    np.testing.assert_array_equal(back, t.apply(img))


# ---------------------------------------------------------------------------
# Config round trip + schema validation (shared keys)
# ---------------------------------------------------------------------------
def test_config_round_trip_and_defaults():
    for t in ALL:
        assert InputTransform.from_config(t.to_config()) == t
    assert InputTransform.from_config({}) == IDENTITY
    assert InputTransform.from_config(None) == IDENTITY
    assert InputTransform.from_config({"input_rotation": 45}) == IDENTITY
    assert InputTransform.from_config({"input_rotation": -90, "input_mirror": "true"}) \
        == InputTransform(True, 270)
    assert normalize_rotation("180") == 180 and normalize_rotation(450) == 90
    assert normalize_rotation(30) is None and normalize_rotation("x") is None
    with pytest.raises(ValueError):
        InputTransform(False, 45)
    assert set(InputTransform().to_config()) == {"input_mirror", "input_rotation"}
    assert InputTransform(True, 90).label() == "rotate 90° + mirror"
    assert IDENTITY.label() == "none"


def test_schema_keys_are_shared_not_per_profile():
    assert "input_mirror" not in cs.PROFILE_KEYS
    assert "input_rotation" not in cs.PROFILE_KEYS
    shared, profile = cs.split_profile({"input_mirror": True, "input_rotation": 90,
                                        "confidence": 0.4})
    assert shared == {"input_mirror": True, "input_rotation": 90}
    flat = cs.flatten(cs.structure({"input_mirror": True, "input_rotation": 90},
                                   {}, "show"))
    assert flat["input_mirror"] is True and flat["input_rotation"] == 90


@pytest.mark.parametrize("raw,expect,warns", [
    ({"input_rotation": 90, "input_mirror": True}, {"input_rotation": 90, "input_mirror": True}, 0),
    ({"input_rotation": 0, "input_mirror": False}, {"input_rotation": 0, "input_mirror": False}, 0),
    ({"input_rotation": -90}, {"input_rotation": 270}, 1),
    ({"input_rotation": "180"}, {"input_rotation": 180}, 1),
    ({"input_rotation": 45}, {}, 1),
    ({"input_rotation": True}, {}, 1),
    ({"input_rotation": "sideways"}, {}, 1),
    ({"input_mirror": 1}, {"input_mirror": True}, 0),
    ({"input_mirror": "off"}, {"input_mirror": False}, 0),
    ({"input_mirror": "maybe"}, {}, 1),
    ({"input_mirror": [1]}, {}, 1),
])
def test_schema_validation(raw, expect, warns):
    out, warnings = cs.validate_flat(raw)
    got = {k: out[k] for k in ("input_rotation", "input_mirror") if k in out}
    assert got == expect
    assert len([w for w in warnings if w.startswith("input_")]) == warns


# ---------------------------------------------------------------------------
# ROI / exclusion carry-over across a change (delta = new.after(old.inverse()))
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("old,new", [(IDENTITY, InputTransform(True, 0)),
                                     (IDENTITY, InputTransform(False, 90)),
                                     (InputTransform(False, 90), InputTransform(True, 270)),
                                     (InputTransform(True, 180), IDENTITY)])
def test_roi_state_follows_the_physical_region(old, new):
    raw_w, raw_h = 64, 40
    raw_rect = (5, 9, 20, 13)
    settings = SimpleNamespace(roi_x=0, roi_y=0, roi_w=0, roi_h=0)
    old_w, old_h = old.output_size(raw_w, raw_h)
    settings.roi_x, settings.roi_y, settings.roi_w, settings.roi_h = \
        old.map_rect(*raw_rect, raw_w, raw_h)
    state = RoiState(settings, (old_w, old_h))
    delta = new.after(old.inverse())
    got = state.apply_transform(delta)
    assert got == new.map_rect(*raw_rect, raw_w, raw_h)
    assert state.source_size == new.output_size(raw_w, raw_h)
    assert (settings.roi_x, settings.roi_y, settings.roi_w, settings.roi_h) == got


def test_remap_exclusion_bundle():
    bundle = {"exclusion_grid": [8, 6], "exclusion_cells": [[0, 0], [7, 5]],
              "exclusion_manual_add": [[2, 3]], "exclusion_manual_remove": [],
              "confidence": 0.4}
    t = InputTransform(False, 90)
    out = remap_exclusion_bundle(bundle, t)
    assert out["exclusion_grid"] == [6, 8]
    assert out["exclusion_cells"] == t.map_cells([[0, 0], [7, 5]], 8, 6)
    assert out["exclusion_manual_add"] == [list(t.map_cell(2, 3, 8, 6))]
    assert out["exclusion_manual_remove"] == [] and out["confidence"] == 0.4
    assert bundle["exclusion_grid"] == [8, 6]           # input untouched
    assert remap_exclusion_bundle(bundle, IDENTITY) == bundle
    assert remap_exclusion_bundle({"confidence": 1}, t) == {"confidence": 1}
    # round trip through the inverse restores the mask
    back = remap_exclusion_bundle(out, t.inverse())
    assert back["exclusion_cells"] == sorted(bundle["exclusion_cells"])


# ---------------------------------------------------------------------------
# Camera read() paths
# ---------------------------------------------------------------------------
def test_camera_manager_read_transforms_but_callback_stays_raw():
    from camera.camera_manager import CameraManager
    cam = CameraManager(threaded=True)
    raw = _img(h=6, w=10, channels=3)
    cam.state.is_open = True
    cam._latest_frame, cam._frame_ready = raw, True
    ok, frame = cam.read()                             # identity: old behaviour
    assert ok and frame is not raw and np.array_equal(frame, raw)

    cam.raw_size = (10, 6)
    t = InputTransform(True, 90)
    cam.set_input_transform(t)
    assert (cam.state.width, cam.state.height) == (6, 10)
    cam._frame_ready = True
    ok, frame = cam.read()
    np.testing.assert_array_equal(frame, _reference(raw, t))


def test_ids_camera_read_transforms_mono_before_bgr():
    ids_camera = pytest.importorskip("camera.ids_camera")

    class _NoSdk(ids_camera.IDSCamera):                # no SDK, no hardware
        def __del__(self):
            pass

    cam = object.__new__(_NoSdk)
    mono = _img(h=6, w=10)
    cam.state = ids_camera.IDSCameraState()
    cam.state.is_open, cam.state.is_acquiring = True, True
    cam.settings = SimpleNamespace(newest_only=True)
    cam._acquire_error, cam._reconfiguring = None, False
    cam._frame_lock = __import__("threading").Lock()
    cam._latest_frame, cam._frame_ready = mono, True
    ok, bgr = cam.read()                               # class default: identity
    assert ok and np.array_equal(bgr[:, :, 0], mono) and bgr.shape == (6, 10, 3)
    t = InputTransform(False, 270)
    cam.input_transform = t
    cam._frame_ready = True
    ok, bgr = cam.read()
    np.testing.assert_array_equal(bgr[:, :, 1], _reference(mono, t))
    assert mono.shape == (6, 10)                       # the raw buffer is untouched


def _bare_ids(ids_camera, mono):
    class _NoSdk(ids_camera.IDSCamera):
        def __del__(self):
            pass

    cam = object.__new__(_NoSdk)
    cam.state = ids_camera.IDSCameraState()
    cam.state.is_open, cam.state.is_acquiring = True, True
    cam.settings = SimpleNamespace(newest_only=True)
    cam._acquire_error, cam._reconfiguring = None, False
    cam._frame_lock = __import__("threading").Lock()
    cam._upload_stream = None
    cam._latest_frame, cam._frame_ready = mono, True
    return cam


@pytest.mark.parametrize("t", [IDENTITY, InputTransform(False, 90),
                               InputTransform(True, 0), InputTransform(True, 270)],
                         ids=lambda t: t.label())
def test_ids_read_gpu_tensor_and_cpu_cache_agree(t):
    ids_camera = pytest.importorskip("camera.ids_camera")
    if not ids_camera.CUDA_AVAILABLE:
        pytest.skip("CUDA not available")
    mono = _img(h=6, w=10)
    cam = _bare_ids(ids_camera, mono)
    cam.input_transform = t
    ok, tensor = cam.read_gpu()
    assert ok and tuple(tensor.shape) == (1, 3) + _reference(mono, t).shape
    gpu = (tensor[0, 0] * 255.0).round().byte().cpu().numpy()
    np.testing.assert_array_equal(gpu, _reference(mono, t))
    cached = cam.get_last_cpu_frame()
    np.testing.assert_array_equal(cached[:, :, 2], _reference(mono, t))


def test_unified_camera_reports_transformed_size():
    ids_camera = pytest.importorskip("camera.ids_camera")
    from camera.camera_manager import CameraManager
    uni = ids_camera.UnifiedCamera()
    uni._cv_camera = CameraManager()
    uni._cv_camera.raw_size = (1920, 1080)
    uni._source_type = ids_camera.CameraSource.OPENCV
    uni._set_sensor_size(1920, 1080)
    assert (uni.width, uni.height) == (1920, 1080)
    uni.set_input_transform(InputTransform(False, 90))
    assert (uni.width, uni.height) == (1080, 1920)
    assert uni.sensor_size == (1920, 1080)
    assert uni._cv_camera.input_transform == InputTransform(False, 90)
    assert (uni._cv_camera.state.width, uni._cv_camera.state.height) == (1080, 1920)
    uni.set_input_transform(IDENTITY)
    assert (uni.width, uni.height) == (1920, 1080)


# ---------------------------------------------------------------------------
# Playback applies the current transform; slot files stay raw
# ---------------------------------------------------------------------------
@pytest.fixture
def recorder_with_take(tmp_path):
    import core.video_recorder as vr
    rec = vr.VideoRecorder(projects_dir=str(tmp_path))
    rec.set_project("p")
    path = tmp_path / "p" / "recordings" / "slot_2_20261006_120000.avi"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20.0, (64, 48))
    frame = np.zeros((48, 64, 3), np.uint8)
    frame[:, :16] = 255                                # bright left band
    for _ in range(10):
        w.write(frame)
    w.release()
    (tmp_path / "p" / "recordings" / "slot_2_20261006_120000.avi.meta").write_text(
        json.dumps({"actual_fps": 20.0, "frames": 10,
                    "input_transform": {"mirror": True, "rotation": 0,
                                        "applied_to_frames": False}}))
    yield rec
    rec.close()


def test_playback_applies_current_transform(recorder_with_take):
    rec = recorder_with_take
    rec.set_input_transform(InputTransform(True, 90))
    assert rec.start_playback(2)
    rec.pause_playback()
    assert (rec.status.playback_width, rec.status.playback_height) == (48, 64)
    frame = rec.read_frame()
    assert frame.shape == (64, 48, 3)
    # left band -> (rot 90 CW) top band -> (mirror) still the top band
    assert frame[:16].mean() > 200 and frame[20:].mean() < 40
    assert rec.playback_recorded_transform == InputTransform(True, 0)
    # switching back mid-playback: dims follow, next frame is raw again
    rec.set_input_transform(IDENTITY)
    assert (rec.status.playback_width, rec.status.playback_height) == (64, 48)
    rec.requeue_frame()
    frame = rec.read_frame()
    assert frame.shape == (48, 64, 3) and frame[:, :16].mean() > 200
    rec.stop_playback()
    assert rec.playback_recorded_transform is None
