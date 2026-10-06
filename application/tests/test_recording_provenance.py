"""MRK-0 — recording provenance: lossless tail drain, rich .meta, camlog,
rig sheet schema, IDS capture snapshot, one-shot record commands."""
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import core.config_schema as cs
import core.video_recorder as vr
from core.version import app_version
from runtime import api
from runtime.recording_controller import RecordingController


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    # MJPG is in every OpenCV build (FFV1 needs the ffmpeg backend).
    monkeypatch.setattr(vr, "RECORDING_CODEC", "MJPG")
    rec = vr.VideoRecorder(projects_dir=str(tmp_path))
    rec.set_project("p")
    return rec


def _frame(i: int) -> np.ndarray:
    f = np.zeros((48, 64, 3), np.uint8)
    f[:, :, :] = i % 255
    return f


def test_stop_drains_every_queued_frame_and_writes_rich_meta(recorder):
    assert recorder.start_recording(3, fps=20.0, size=(64, 48),
                                    meta={"rig": {"f_number": 1.4}, "project": "p"})
    n = 250                                   # queue is 300 deep: all queued
    for i in range(n):
        recorder.write_frame(_frame(i))
    recorder.append_camlog({"nodes": {"ExposureTime": 25000.0}})
    recorder.append_camlog({"nodes": {"ExposureTime": 24000.0}})
    path = recorder.stop_recording(meta={"camera": {"nodes": {"Gain": 12.0}}})
    assert recorder.is_live                   # stop returns at once
    assert recorder.wait_finalized(timeout=60)
    assert not recorder.is_finalizing(path)
    meta = json.loads(open(path + ".meta").read())
    # the old loop discarded the queued tail on stop
    assert meta["frames"] == n and meta["frames_queued"] == n
    assert meta["frames_dropped"] == 0
    assert meta["actual_fps"] > 0 and meta["meta_version"] == 2
    assert meta["slot"] == 3 and meta["codec"] == "MJPG" and meta["size"] == [64, 48]
    assert meta["rig"] == {"f_number": 1.4} and meta["project"] == "p"
    assert meta["at_stop"]["camera"]["nodes"]["Gain"] == 12.0
    assert meta["camlog"].endswith(".camlog.jsonl")
    lines = open(path + ".camlog.jsonl").read().splitlines()
    assert [json.loads(l)["nodes"]["ExposureTime"] for l in lines] == [25000.0, 24000.0]
    # the written file is complete and decodable
    import cv2
    cap = cv2.VideoCapture(path)
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == n
    cap.release()


def test_full_queue_counts_drops(recorder, monkeypatch):
    gate = threading.Event()
    real_run = vr._RecordingJob._run

    def slow_run(self):          # hold the encoder so the queue fills
        gate.wait(10)
        real_run(self)

    monkeypatch.setattr(vr._RecordingJob, "_run", slow_run)
    recorder.start_recording(1, fps=20.0, size=(64, 48))
    for i in range(310):
        recorder.write_frame(_frame(i))
    path = recorder.stop_recording()
    gate.set()
    assert recorder.wait_finalized(timeout=60)
    meta = json.loads(open(path + ".meta").read())
    assert meta["frames_queued"] == 300 and meta["frames_dropped"] == 10
    assert meta["frames"] == 300


def test_rig_schema():
    clean, warnings = cs.sanitize_rig({
        "lens": "  Tamron 8mm ", "f_number": 99, "focus_m": 0, "bogus": 1,
        "illuminator_offset_cm": 0, "notes": "x" * 600, "focal_mm": "abc"})
    assert clean["lens"] == "Tamron 8mm"
    assert clean["f_number"] == 32.0                  # clamped
    assert "focus_m" not in clean                     # 0 = unknown
    assert clean["illuminator_offset_cm"] == 0.0      # on-axis is a real value
    assert len(clean["notes"]) == 500 and "focal_mm" not in clean
    assert any("bogus" in w for w in warnings)
    with pytest.raises(KeyError):
        cs.sanitize_rig_value("nope", 1)
    # rig rides as a shared key through the v2 structure and is validated on load
    flat = {"model": "yolo11x-pose", "confidence": 0.4, "rig": {"f_number": 2.0}}
    structured = cs.structure(flat, {}, "show")
    assert structured["rig"] == {"f_number": 2.0}
    assert "rig" not in structured["profiles"]["show"]
    out, _ = cs.validate_flat(cs.flatten(dict(structured, rig={"f_number": "1.8"})))
    assert out["rig"] == {"f_number": 1.8}


class _FakeNode:
    def __init__(self, value=None, entry=None):
        self._value, self._entry = value, entry

    def Value(self):
        if self._value is None:
            raise RuntimeError("not numeric")
        return self._value

    def CurrentEntry(self):
        if self._entry is None:
            raise AttributeError("not an enumeration")
        return SimpleNamespace(SymbolicValue=lambda: self._entry)


class _FakeNodeMap:
    def __init__(self, nodes):
        self.nodes = nodes

    def FindNode(self, name):
        if name not in self.nodes:
            raise KeyError(name)
        return self.nodes[name]


def test_ids_capture_settings_reads_node_map():
    from camera.ids_camera import IDSCamera, IDSCameraSettings, IDSCameraState
    cam = object.__new__(IDSCamera)               # no SDK / hardware needed
    cam.close = lambda: None                      # __del__ on a bare object
    cam.settings = IDSCameraSettings(user_set="UserSet1", crop_ratio=1.0)
    cam.state = IDSCameraState()
    cam.state.is_open = True
    cam.state.fps, cam.state.width, cam.state.height = 19.8, 1488, 1528
    cam._node_map = _FakeNodeMap({
        "ExposureTime": _FakeNode(24800.0), "ExposureAuto": _FakeNode(entry="Off"),
        "Gain": _FakeNode(12.5), "GainAuto": _FakeNode(entry="Off"),
        "DeviceTemperature": _FakeNode(41.5), "PixelFormat": _FakeNode(entry="Mono8"),
        "DeviceSerialNumber": _FakeNode("4108xxxx"),
    })
    fast = cam.get_capture_settings(fast=True)
    assert fast["nodes"] == {"ExposureTime": 24800.0, "ExposureAuto": "Off", "Gain": 12.5,
                             "GainAuto": "Off", "DeviceTemperature": 41.5}
    assert "app_settings" not in fast
    full = cam.get_capture_settings()
    assert full["nodes"]["PixelFormat"] == "Mono8"
    assert full["nodes"]["DeviceSerialNumber"] == "4108xxxx"
    assert full["app_settings"]["user_set"] == "UserSet1"
    assert full["frame_size"] == [1488, 1528]
    cam.state.is_open = False
    assert cam.get_capture_settings() == {"source": "ids", "open": False}


class _Cam:
    def __init__(self):
        self.callback = None

    def set_frame_callback(self, cb):
        self.callback = cb

    def start_acquisition(self):
        pass

    def stop_acquisition(self):
        pass

    def live_dimensions(self):
        return (64, 48)

    def record_dimensions(self):
        return (64, 48)

    def capture_settings(self, fast=False):
        return {"nodes": {"ExposureTime": 25000.0}, "fast": fast}


class _Ui:
    available = False


class _Session:
    config_dir = "."
    current_project = "p"
    model_name = "yolo11x-pose"
    imgsz = 1280

    def saveable_config(self):
        return {}

    def take_provenance(self):
        return {"app": {"commit": "abc"}, "rig": {"f_number": 1.4}}


def test_controller_one_shot_record_and_camlog(recorder, monkeypatch):
    rc = RecordingController(recorder, tracker_logger=None, camera=_Cam(), ui=_Ui(),
                             session=_Session(), on_playback_restart=lambda: None,
                             startup_review=SimpleNamespace(pause_at_frame=None))
    assert rc.start_recording_slot(4) is True
    assert recorder.is_recording and recorder.status.current_slot == 4
    assert rc.start_recording_slot(5) is False          # already recording
    for i in range(5):
        rc._camera_frame_callback(_frame(i))
    monkeypatch.setattr("runtime.recording_controller.RECORDING_CAMLOG_INTERVAL_S", 0.0)
    rc._tick_camlog()
    rc._tick_camlog()
    assert rc.stop_recording_now() is True
    assert rc.stop_recording_now() is False
    assert recorder.wait_finalized(timeout=60)
    path = recorder.get_slot_info(4).recordings[0][1]
    meta = json.loads(open(path + ".meta").read())
    assert meta["camera"]["fast"] is False and meta["app"] == {"commit": "abc"}
    assert meta["rig"] == {"f_number": 1.4}
    assert meta["at_stop"]["camera"]["nodes"]["ExposureTime"] == 25000.0
    camlog = [json.loads(l) for l in open(path + ".camlog.jsonl")]
    assert len(camlog) == 2 and camlog[0]["fast"] is True and "t" in camlog[0]


def test_commands_validate():
    with pytest.raises(ValueError):
        api.StartRecordingSlot(0)
    assert api.StartRecordingSlot(9).slot == 9
    assert api.SetRigSheet("f_number", 2.8).echo is False


def test_app_version_without_git_cli(tmp_path):
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/release\n")
    (tmp_path / ".git" / "packed-refs").write_text(
        "# pack-refs\n0123456789abcdef0123456789abcdef01234567 refs/heads/release\n")
    v = app_version.__wrapped__(str(tmp_path))
    assert v["branch"] == "release" and v["commit"] == "0123456789ab"
