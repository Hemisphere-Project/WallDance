"""The project key ``ids_throughput_limit_mbps`` (stall verdict 26/11 §8.1): the IDS camera's USB link throughput
limit, OFF by default (0 = the camera's own, release 1's behaviour), saved and loaded with a project, handed to
the camera at EVERY open (first open, reconnect, source switch: a reopen restores the camera's own value).  The
readiness « camera » row says the limit in force.  The camera side (the write, the snap, the frame rate asked
again, a refusing camera) is in test_ids_stream_diag.py.  No SDK, no camera: fakes."""
import json
import pathlib
from types import SimpleNamespace
from unittest import mock

import pytest

from camera.ids_camera import CameraSource, IDSCameraSettings, UnifiedCamera
from core import config, config_schema
from core.config_store import ConfigStore
from core.ops_monitor import LINK_LIMIT_HOW, camera_link_text, check_camera
from runtime.camera_controller import CameraController

KEY = "ids_throughput_limit_mbps"


class _FakeUnified:
    """UnifiedCamera stand-in: records the limit it is asked to open with, at each open."""

    def __init__(self):
        self.crop_ratio = None
        self.throughput_limit_mbps = 0.0
        self.opened = []
        self.width, self.height = 1488, 1528
        self.source_type = None
        self.is_open = False

    def close(self):
        self.is_open = False

    def open(self, source):
        self.opened.append((source, self.throughput_limit_mbps))
        self.source_type = CameraSource.IDS_PEAK if source.startswith("ids") else CameraSource.OPENCV
        self.is_open = True
        return True

    def update_crop_ratio(self, ratio):
        return False

    def __getattr__(self, name):                 # set_gain / set_exposure / *_auto
        if name.startswith("set_"):
            return lambda *a: None
        raise AttributeError(name)


def _controller():
    uni = _FakeUnified()
    camera = SimpleNamespace(
        state=SimpleNamespace(is_open=False, source="ids", available=[], unavailable=[], width=0, height=0),
        cap=None, set_input_transform=lambda t: None, close=lambda: None)
    cams = CameraController(camera=camera, unified_camera=uni, use_unified=True,
                            ui=SimpleNamespace(available=False), preview_geometry=lambda w, h: None,
                            repush_preview_size=lambda: None, is_running=lambda: True)
    return cams, uni


def test_the_key_is_off_by_default():
    assert config.IDS_THROUGHPUT_LIMIT_MBPS == 0.0
    assert IDSCameraSettings().throughput_limit_mbps == 0.0
    assert UnifiedCamera().throughput_limit_mbps == 0.0
    cams, uni = _controller()
    assert cams.ids_throughput_limit_mbps == 0.0
    assert cams._attempt_camera_connect("ids") is True
    assert uni.opened == [("ids", 0.0)]                      # the camera's own: nothing will be written


def test_the_key_reaches_every_open_first_reconnect_and_source_switch():
    cams, uni = _controller()
    assert cams.set_ids_throughput_limit(124) == 124.0
    cams._attempt_camera_connect("ids")                      # first open
    cams._mark_camera_unavailable("ids")                     # the camera lost ...
    cams._schedule_camera_retry()
    cams._attempt_camera_connect(cams.camera.state.source)   # ... the main loop's reconnect
    cams._cb_camera_change("0")                              # a source switch to a webcam and back
    cams._cb_camera_change("ids")
    ids_opens = [mbps for src, mbps in uni.opened if src.startswith("ids")]
    assert ids_opens == [124.0, 124.0, 124.0]
    cams.set_ids_throughput_limit(0)                         # back to the camera's own at the next open
    cams._cb_camera_change("0")
    cams._cb_camera_change("ids")
    assert uni.opened[-1] == ("ids", 0.0)


def test_junk_or_a_negative_value_is_the_cameras_own():
    cams, _ = _controller()
    for value in (None, -5, "fast", float("nan"), float("inf"), True, 0):
        cams.set_ids_throughput_limit(124)
        assert cams.set_ids_throughput_limit(value) == 0.0, value
    assert cams.set_ids_throughput_limit("124") == 124.0


def test_the_unified_camera_opens_the_ids_with_the_key(monkeypatch):
    import camera.ids_camera as ic

    built = []

    class _Recorder:
        def __init__(self, settings):
            built.append(settings)

        def open(self, serial=None):
            return False                         # stop there: the settings are what this test reads

    monkeypatch.setattr(ic, "IDS_PEAK_AVAILABLE", True)
    monkeypatch.setattr(ic, "IDSCamera", _Recorder)
    cam = UnifiedCamera()
    cam.throughput_limit_mbps = 124.0
    assert cam._open_ids() is False
    cam.throughput_limit_mbps = -3.0
    cam._open_ids()
    cam.throughput_limit_mbps = None
    cam._open_ids()
    assert [s.throughput_limit_mbps for s in built] == [124.0, 0.0, 0.0]
    assert UnifiedCamera().get_throughput_limit() is None    # not open: nothing in force


def test_the_schema_keeps_a_shared_bounded_key():
    assert KEY not in config_schema.PROFILE_KEYS             # the cable is the same for both lighting profiles
    flat, warnings = config_schema.validate_flat({KEY: 124})
    assert flat[KEY] == 124 and not warnings
    flat, warnings = config_schema.validate_flat({KEY: "fast"})
    assert KEY not in flat and warnings
    flat, warnings = config_schema.validate_flat({KEY: -10})
    assert flat[KEY] == 0 and warnings


def test_the_key_is_saved_and_loaded_with_a_project(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("dearpygui.dearpygui")
    import app as app_mod

    def stub(mbps):
        s = mock.MagicMock()
        s.cameras, _ = _controller()
        s.cameras.set_ids_throughput_limit(mbps)
        s.processor.get_exclusion_state.return_value = ([], [], [], [])
        s.roi.state.stored_roi.return_value = (0, 0, 10, 10, 10, 10)
        s.models.model_manager.shape = None
        return s
    # save: the app's own saveable config carries the key, off by default
    assert app_mod.WallDanceApp._get_saveable_config(stub(None))[KEY] == 0.0
    saved = app_mod.WallDanceApp._get_saveable_config(stub(124))[KEY]
    assert saved == 124.0
    # ... through the schema and the store, as a project save does
    store = ConfigStore(config_dir=str(tmp_path))
    flat = {"model": "yolo11l-pose", "camera_source": "ids", "confidence": 0.3, KEY: saved}
    path = store.save("rehearsal-cordee", config_schema.structure(flat, {}, "show"))
    assert json.loads(pathlib.Path(path).read_text())[KEY] == 124.0
    loaded, warnings = config_schema.validate_flat(config_schema.flatten(config_schema.migrate(store.load(path))))
    assert loaded[KEY] == 124.0
    # load: the app's own apply path sets the key; a project without it is back to the camera's own
    app = stub(None)
    app_mod.WallDanceApp._apply_config_without_model(app, loaded)
    assert app.cameras.ids_throughput_limit_mbps == 124.0
    app.cameras._attempt_camera_connect("ids")               # the project switch reopens the camera
    assert app.cameras.unified_camera.opened[-1] == ("ids", 124.0)
    other = dict(loaded)
    other.pop(KEY)
    app_mod.WallDanceApp._apply_config_without_model(app, other)
    assert app.cameras.ids_throughput_limit_mbps == 0.0
    # a lighting-profile switch (a profile bundle) leaves it alone
    app.cameras.set_ids_throughput_limit(124)
    app_mod.WallDanceApp._apply_config_without_model(app, {"gamma": 1.2, "confidence": 0.3})
    assert app.cameras.ids_throughput_limit_mbps == 124.0


def _link(**kw):
    info = {"asked_mbps": 124.0, "mbps": 124.0, "was_mbps": 162.0, "refused": None,
            "fps_before": 20.0, "fps": 20.0, "fps_kept": True}
    info.update(kw)
    return info


def test_the_readiness_camera_row_says_the_limit_in_force():
    base = dict(is_open=True, reconnecting=False, source="ids", fps=19.8, min_fps=15.0, ids_frames=1234,
                ids_dropped=2)
    off = check_camera(**base)
    assert off == check_camera(**base, link=None) and "link" not in off.detail      # key 0: as before
    ok = check_camera(**base, link=_link())
    assert ok.status == "ok" and ok.detail.endswith(", link limit 124 MB/s")
    refused = check_camera(**base, link=_link(mbps=162.0, refused="write refused"))
    assert refused.status == "ok"
    assert refused.detail.endswith(", link limit 162 MB/s (the camera's own): the camera refused 124 MB/s")
    slow = check_camera(**base, link=_link(mbps=60.0, asked_mbps=60.0, fps=12.5, fps_kept=False))
    assert slow.status == "warn"
    assert slow.detail.endswith(", link limit 60 MB/s: the camera runs 12.5 FPS, not 20.0 - " + LINK_LIMIT_HOW)
    assert LINK_LIMIT_HOW == "set ids_throughput_limit_mbps to 0 in the project (the camera's own)"
    for row in (ok, refused, slow):
        assert row.detail.isascii()
    floor = check_camera(**dict(base, fps=9.0), link=_link())
    assert floor.status == "warn" and floor.detail.endswith("- below the 15 FPS show floor")
    assert camera_link_text(mock.MagicMock()) == ("", "")    # never a mock's words on the row
