"""camera-recording#3 (recording callback lost at a reconnect) and camera-recording#4
(playback -> LIVE trips the 2.5 s watchdog at once).

REAL: IDSCamera.__init__ / set_frame_callback / stop_acquisition / start_acquisition /
get_last_acquired_age_s, UnifiedCamera, CameraController, RecordingController,
app._RecordingCameraAdapter, VideoRecorder (FFV1 into a temp dir).
FAKE: the IDS SDK objects (device, datastream, node map) and the frames.  No GPU, no hardware.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import numpy as np

import camera.ids_camera as ic
ic.CUDA_AVAILABLE = False                           # no torch.cuda.Stream() in IDSCamera.__init__
from camera.ids_camera import IDSCamera, UnifiedCamera, CameraSource
from runtime.camera_controller import CameraController
from runtime.recording_controller import RecordingController
from core.video_recorder import VideoRecorder
from core.input_transform import IDENTITY
import app as app_module                            # _RecordingCameraAdapter (the real port)

W, H = 64, 48
BGR = np.zeros((H, W, 3), np.uint8)


class _Node:
    def Execute(self): pass
    def WaitUntilDone(self): pass


class _NodeMap:
    def FindNode(self, name): return _Node()


class _DataStream:
    """A stream that never delivers: WaitForFinishedBuffer blocks, then 'times out'."""
    def __init__(self): self._kill = threading.Event()
    def StartAcquisition(self): self._kill.clear()
    def StopAcquisition(self, *a): pass
    def KillWait(self): self._kill.set()
    def Flush(self, *a): pass
    def AnnouncedBuffers(self): return []
    def RevokeBuffer(self, b): pass
    def WaitForFinishedBuffer(self, timeout_ms):
        self._kill.wait(timeout_ms / 1000.0)
        raise RuntimeError("timeout (fake stream)")


class SdkLessIDS(IDSCamera):
    """The real IDSCamera with the hardware calls stubbed (constructor, callback slot, start/stop
    and the watchdog timestamp are the shipped code)."""
    built = 0

    @classmethod
    def _acquire_library(cls):                      # no ids_peak.Library.Initialize()
        pass

    def __init__(self, settings=None):
        super().__init__(settings)
        SdkLessIDS.built += 1
        self.serial_no = SdkLessIDS.built

    def open(self, serial=None):
        self._datastream = _DataStream()
        self._node_map = _NodeMap()
        self.state.is_open = True
        self.state.width, self.state.height, self.state.fps = W, H, 20.0
        self.state.pixel_format = "Mono8"
        return True

    def set_exposure_auto(self, e): return True
    def set_gain_auto(self, e): return True
    def get_capture_settings(self, fast=False): return {"source": "ids", "fake": True}

    def deliver(self, n):
        """What _acquisition_loop does per frame (ids_camera.py:1205-1217), minus the SDK."""
        for _ in range(n):
            with self._frame_lock:
                self.state.frame_count += 1
                self.state.last_frame_time = time.perf_counter()
            if self._frame_callback is not None:
                self._frame_callback(BGR)


ic.IDSCamera = SdkLessIDS                            # UnifiedCamera._open_ids builds this class


class _Ui:
    available = False


def build(tmp):
    uc = UnifiedCamera()
    legacy = SimpleNamespace(state=SimpleNamespace(is_open=False, source="ids", available=["ids"],
                                                   unavailable=[], width=0, height=0),
                             close=lambda: None, set_frame_callback=lambda cb: None)
    cameras = CameraController(camera=legacy, unified_camera=uc, use_unified=True, ui=_Ui(),
                               preview_geometry=lambda w, h: None, repush_preview_size=lambda: None,
                               is_running=lambda: True)
    recorder = VideoRecorder(projects_dir=tmp)
    recorder.set_project("p")
    fake_app = SimpleNamespace(cameras=cameras, _use_unified_camera=True, unified_camera=uc,
                               camera=legacy, input_transform=IDENTITY)
    port = app_module._RecordingCameraAdapter(fake_app)
    session = SimpleNamespace(config_dir=tmp, current_project="p", model_name="m", imgsz=1280,
                              saveable_config=lambda: {}, take_provenance=lambda: {})
    rc = RecordingController(recorder=recorder, tracker_logger=SimpleNamespace(flush=lambda: None),
                             camera=port, ui=_Ui(), session=session,
                             on_playback_restart=lambda: None,
                             startup_review=SimpleNamespace(pause_at_frame=None, slot=None))
    return uc, cameras, recorder, rc


def quiet(fn, *a, **k):
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


def meta_of(recorder, path):
    recorder.wait_finalized(20.0)
    return json.load(open(path + ".meta"))


def case_3a(tmp):
    print("=== #3a  a reconnect during a take ===")
    uc, cameras, recorder, rc = build(tmp)
    quiet(cameras._attempt_camera_connect, "ids")
    cam1 = uc._ids_camera
    rc._rec_armed = True
    quiet(rc._cb_rec_slot_click, 1, False)           # REC armed + slot 1
    path = recorder._recording_path
    cam1.deliver(20)
    print(f"  before reconnect : camera #{cam1.serial_no}, callback set = {cam1._frame_callback is not None}, "
          f"recorder.is_recording = {recorder.is_recording}, recording_frames = {recorder.status.recording_frames}")
    # what main_loop.py:691-696 + the retry pump (main_loop.py:576-583) do on a watchdog trip
    quiet(cameras._mark_camera_unavailable, "ids", close_active=True)
    cameras._schedule_camera_retry(delay=0.5)
    quiet(cameras._attempt_camera_connect, "ids")
    cam2 = uc._ids_camera
    cam2.deliver(60)                                 # 3 s of frames after the reopen
    print(f"  after reconnect  : camera #{cam2.serial_no} (new object: {cam2 is not cam1}), "
          f"callback set = {cam2._frame_callback is not None}, "
          f"recorder.is_recording = {recorder.is_recording}, recording_frames = {recorder.status.recording_frames}")
    quiet(rc._cb_rec_toggle)                         # operator presses REC to stop
    m = meta_of(recorder, path)
    print(f"  .meta            : frames = {m['frames']} (camera delivered 80), frames_queued = {m['frames_queued']}, "
          f"frames_dropped = {m['frames_dropped']}, error = {m['error']}")


def case_3b(tmp):
    print("=== #3b  REC pressed while the camera is closed / reconnecting ===")
    uc, cameras, recorder, rc = build(tmp)
    quiet(cameras._attempt_camera_connect, "ids")
    quiet(cameras._mark_camera_unavailable, "ids", close_active=True)
    rc._rec_armed = True
    quiet(rc._cb_rec_slot_click, 2, False)
    path = recorder._recording_path
    quiet(cameras._attempt_camera_connect, "ids")
    uc._ids_camera.deliver(60)
    print(f"  recorder.is_recording = {recorder.is_recording}, recording_frames = {recorder.status.recording_frames}, "
          f"callback set on the reopened camera = {uc._ids_camera._frame_callback is not None}")
    quiet(rc._cb_rec_toggle)
    m = meta_of(recorder, path)
    print(f"  .meta            : frames = {m['frames']} (camera delivered 60), error = {m['error']}")


def case_4(tmp, playback_s):
    print(f"=== #4   playback {playback_s:.1f} s, then LIVE ===")
    uc, cameras, recorder, rc = build(tmp)
    quiet(cameras._attempt_camera_connect, "ids")
    cam = uc._ids_camera
    quiet(cam.start_acquisition)                     # (UnifiedCamera._open_ids already did; idempotent)
    cam.deliver(100)                                 # live for a while
    print(f"  live             : frame_count = {cam.state.frame_count}, "
          f"watchdog _ids_stream_timed_out() = {cameras._ids_stream_timed_out()}")
    quiet(uc.stop_acquisition)                       # RecordingController._start_playback_safe:509
    time.sleep(playback_s)                           # the take plays
    ok, _ = quiet(uc.start_acquisition)              # RecordingController._cb_rec_live:344
    age = uc.get_last_acquired_age_s()
    tripped = cameras._ids_stream_timed_out()        # main_loop.py:691, the very next tick
    print(f"  back to LIVE     : start_acquisition() -> {ok}, is_acquiring = {cam.state.is_acquiring}, "
          f"frame_count = {cam.state.frame_count}, last-acquired age = {age:.2f} s")
    print(f"  next tick        : _ids_stream_timed_out() = {tripped}  "
          f"(threshold {cameras._ids_disconnect_timeout_s} s) -> main_loop closes and reopens the camera")
    cam.deliver(1)                                   # had ONE frame arrived first, no trip:
    print(f"  after 1 new frame: _ids_stream_timed_out() = {cameras._ids_stream_timed_out()}")
    quiet(uc.close)


if __name__ == "__main__":
    tmp = tempfile.mkdtemp(prefix="wd_cb_", dir=os.path.dirname(os.path.abspath(__file__)))
    try:
        case_3a(tmp)
        case_3b(tmp)
        case_4(tmp, 3.0)
        case_4(tmp, 1.0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
