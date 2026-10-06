"""REQ-5 follow-ups: offline tools apply Mirror/Rotate; producers pre-transform.

* ``tests/replay.py`` / ``detect_cache.py`` decode slot files themselves: they
  must hand the processor the same transformed frames the app's playback does
  (identity: the very same decoded array -> default-off replays unchanged), and
  a non-identity transform must enter the detect-cache key (identity keys --
  and so existing caches -- stay as they were).
* The IDS acquisition thread and the playback decoder thread now make the
  transformed frame; ``read()`` / ``read_gpu()`` / ``read_frame()`` reuse it
  while the transform matches and fall back to transforming the raw frame.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from core.input_transform import IDENTITY, InputTransform

HERE = Path(__file__).resolve().parent
ROT_MIRROR = InputTransform(True, 90)


def _load_replay(monkeypatch):
    """Import tests/replay.py without its CUDA re-exec, as ``replay`` for the
    duration of the test only (detect_cache imports it lazily by that name)."""
    monkeypatch.setenv("_WD_LD_BOOTSTRAPPED", "1")
    spec = importlib.util.spec_from_file_location("_replay_under_test", HERE / "replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setitem(sys.modules, "replay", mod)
    return mod


class _FakeProc:
    """Records the frames process() receives (no model, no GPU)."""
    gpu_path_active = True

    def __init__(self):
        self.frames = []
        self._cache_capture_gpu = None
        self.tracker = SimpleNamespace(
            reset=lambda: None,
            logger=SimpleNamespace(start_session=lambda d: None, close=lambda: None))

    def process(self, frame, need_preview=False, frame_number=0):
        self.frames.append(frame.copy())
        if self._cache_capture_gpu is not None:
            space = SimpleNamespace(person_height=1, scale=1.0, pad_x=0.0, pad_y=0.0,
                                    roi_x=0, roi_y=0, frame_width=frame.shape[1])
            self._cache_capture_gpu([], space, None, frame.shape[1], frame.shape[0])
        return [], None, {}, 0.0


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "slot_1_20261006_120000.avi"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20.0, (40, 24))
    rng = np.random.default_rng(1)
    for i in range(6):
        f = rng.integers(0, 255, (24, 40, 3), dtype=np.uint8)
        f[:, : 8 + i] = 255                      # asymmetric content
        w.write(f)
    w.release()
    cap = cv2.VideoCapture(str(path))
    raw = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        raw.append(f)
    cap.release()
    assert len(raw) == 6
    return path, raw


@pytest.mark.parametrize("config,xf", [({}, IDENTITY),
                                       ({"input_rotation": 90, "input_mirror": True}, ROT_MIRROR),
                                       ({"input_rotation": 270}, InputTransform(False, 270))])
def test_replay_feeds_transformed_frames(monkeypatch, video, config, xf):
    replay = _load_replay(monkeypatch)
    path, raw = video
    fake = _FakeProc()
    monkeypatch.setattr(replay, "_build_processor", lambda *a, **k: fake)
    monkeypatch.setattr(replay, "_summary_from_log", lambda *a, **k: {"per_frame": a[-1]})
    replay.replay_recording(str(path), dict(config), max_frames=4)
    assert len(fake.frames) == 4
    for got, r in zip(fake.frames, raw):
        np.testing.assert_array_equal(got, xf.apply(r))
        assert got.shape == ((40, 24, 3) if xf.swaps_axes else (24, 40, 3))


def test_identity_transform_is_the_same_array(monkeypatch):
    replay = _load_replay(monkeypatch)
    f = np.zeros((4, 5, 3), np.uint8)
    for cfg in ({}, {"input_rotation": 0, "input_mirror": False}, {"input_rotation": "junk"}):
        assert replay.input_transform_for(cfg).apply(f) is f


def test_detect_cache_build_feeds_transformed_frames(monkeypatch, video, tmp_path):
    replay = _load_replay(monkeypatch)
    import detect_cache
    path, raw = video
    fake = _FakeProc()
    monkeypatch.setattr(replay, "_build_processor", lambda *a, **k: fake)
    cfg = {"input_rotation": 90, "input_mirror": True}
    out = detect_cache.build_cache_gpu(str(path), cfg, max_frames=3,
                                       out_path=tmp_path / "c.pkl", use_trt=False)
    assert out.exists() and len(fake.frames) == 3
    for got, r in zip(fake.frames, raw):
        np.testing.assert_array_equal(got, ROT_MIRROR.apply(r))


def test_detect_cache_key_only_changes_for_a_real_transform():
    import detect_cache
    base = {"confidence": 0.4, "roi_enabled": True}
    k0 = detect_cache.cache_key(base, "v.avi", 0, 100, "yolo11x-pose", 1280, path="trt")
    k_id = detect_cache.cache_key({**base, "input_rotation": 0, "input_mirror": False},
                                  "v.avi", 0, 100, "yolo11x-pose", 1280, path="trt")
    k_rot = detect_cache.cache_key({**base, "input_rotation": 90},
                                   "v.avi", 0, 100, "yolo11x-pose", 1280, path="trt")
    assert k0 == k_id and "_input_transform" not in k0
    assert k_rot["_input_transform"] == [False, 90]
    assert detect_cache.cache_path_for(k_rot) != detect_cache.cache_path_for(k0)


# ---------------------------------------------------------------------------
# Producers pre-transform; the readers reuse it only while it matches
# ---------------------------------------------------------------------------
def _bare_ids(ids_camera, mono):
    class _NoSdk(ids_camera.IDSCamera):
        def __del__(self):
            pass

    cam = object.__new__(_NoSdk)
    cam.state = ids_camera.IDSCameraState()
    cam.state.is_open, cam.state.is_acquiring = True, True
    cam.settings = SimpleNamespace(newest_only=False)
    cam._acquire_error, cam._reconfiguring = None, False
    cam._frame_lock = threading.Lock()
    cam._upload_stream = None
    cam._latest_frame, cam._frame_ready = mono, True
    return cam


def test_ids_read_reuses_the_acquisition_threads_transform():
    ids_camera = pytest.importorskip("camera.ids_camera")
    mono = np.arange(6 * 10, dtype=np.uint8).reshape(6, 10)
    cam = _bare_ids(ids_camera, mono)
    t = InputTransform(False, 90)
    cam.input_transform = t
    marker = np.full(t.output_size(10, 6)[::-1], 7, np.uint8)   # (h, w) = (10, 6)
    cam._latest_frame_xf = (t, marker)
    ok, bgr = cam.read()
    assert ok and np.all(bgr == 7)                      # the pre-made one was used
    cam._latest_frame_xf = (InputTransform(True, 90), marker)   # stale: other transform
    ok, bgr = cam.read()
    np.testing.assert_array_equal(bgr[:, :, 0], t.apply(mono))
    cam.input_transform = IDENTITY                      # identity ignores it entirely
    ok, bgr = cam.read()
    np.testing.assert_array_equal(bgr[:, :, 0], mono)


def test_ids_read_gpu_reuses_it_for_tensor_and_cpu_cache():
    ids_camera = pytest.importorskip("camera.ids_camera")
    if not ids_camera.CUDA_AVAILABLE:
        pytest.skip("CUDA not available")
    mono = np.arange(6 * 10, dtype=np.uint8).reshape(6, 10)
    cam = _bare_ids(ids_camera, mono)
    t = InputTransform(True, 270)
    cam.input_transform = t
    cam._latest_frame_xf = (t, t.apply(mono))
    ok, tensor = cam.read_gpu()
    gpu = (tensor[0, 0] * 255.0).round().byte().cpu().numpy()
    np.testing.assert_array_equal(gpu, t.apply(mono))
    np.testing.assert_array_equal(cam.get_last_cpu_frame()[:, :, 1], t.apply(mono))


@pytest.fixture
def recorder(tmp_path):
    import core.video_recorder as vr
    rec = vr.VideoRecorder(projects_dir=str(tmp_path))
    rec.set_project("p")
    path = tmp_path / "p" / "recordings" / "slot_2_20261006_120000.avi"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20.0, (64, 48))
    frame = np.zeros((48, 64, 3), np.uint8)
    frame[:, :16] = 255
    for _ in range(40):
        w.write(frame)
    w.release()
    (tmp_path / "p" / "recordings" / "slot_2_20261006_120000.avi.meta").write_text(
        json.dumps({"actual_fps": 200.0, "frames": 40}))
    yield rec
    rec.close()


def test_playback_decoder_thread_pretransforms(recorder):
    rec = recorder
    t = InputTransform(False, 90)
    rec.set_input_transform(t)
    assert rec.start_playback(2)
    rec.read_frame()                                  # the pre-decoded first frame
    deadline = time.time() + 5.0
    while time.time() < deadline:                     # a decoder-thread frame
        with rec._frame_lock:
            pre, new = rec._frame_buffer_xf, rec._frame_new
        if pre is not None and new:
            break
        time.sleep(0.005)
    assert pre is not None and pre[0] == t
    raw = rec._frame_buffer
    frame = rec.read_frame()
    assert frame is not pre[1]                        # caller owns a fresh array
    np.testing.assert_array_equal(frame, t.apply(raw))
    assert frame.shape == (64, 48, 3)
    # transform changed after decoding: the stale pre-made frame is not used
    rec.pause_playback()
    rec.set_input_transform(IDENTITY)
    rec.requeue_frame()
    frame = rec.read_frame()
    assert frame.shape == (48, 64, 3)
    rec.stop_playback()
    assert rec._frame_buffer_xf is None
