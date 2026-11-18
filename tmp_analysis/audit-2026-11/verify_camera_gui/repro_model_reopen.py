"""camera-recording#5: "closing the camera" for a model load acts on the legacy CameraManager;
the reopen afterwards has no retry.

REAL: app._ModelCameraAdapter, CameraController, UnifiedCamera, CameraManager (never opened),
IDSCamera.__init__, MainLoop._tick_pumps/_tick_acquire.  FAKE: the IDS SDK.  No GPU, no hardware.
"""
from __future__ import annotations

import contextlib
import io
import time
from types import SimpleNamespace

import camera.ids_camera as ic
ic.CUDA_AVAILABLE = False
from camera.ids_camera import IDSCamera, UnifiedCamera
from camera.camera_manager import CameraManager
from runtime.camera_controller import CameraController
import runtime.main_loop as ml
import app as app_module


class SdkLessIDS(IDSCamera):
    fail_next_opens = 0
    opens = 0
    closes = 0

    @classmethod
    def _acquire_library(cls):
        pass

    def open(self, serial=None):
        SdkLessIDS.opens += 1
        if SdkLessIDS.fail_next_opens > 0:
            SdkLessIDS.fail_next_opens -= 1
            return False
        self.state.is_open = True
        self.state.width, self.state.height, self.state.fps = 64, 48, 20.0
        return True

    def start_acquisition(self):
        self.state.is_acquiring = True
        return True

    def stop_acquisition(self):
        self.state.is_acquiring = False

    def close(self):
        if self.state.is_open:
            SdkLessIDS.closes += 1
        self.state.is_open = False
        self.state.is_acquiring = False

    def has_error(self): return False
    def read(self): return True, None
    def set_exposure_auto(self, e): return True
    def set_gain_auto(self, e): return True


ic.IDSCamera = SdkLessIDS


class _Ui:
    available = False


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


if __name__ == "__main__":
    uc = UnifiedCamera()
    legacy = CameraManager()                         # app.camera: "kept for compatibility"
    cameras = CameraController(camera=legacy, unified_camera=uc, use_unified=True, ui=_Ui(),
                               preview_geometry=lambda w, h: None, repush_preview_size=lambda: None,
                               is_running=lambda: True)
    app = SimpleNamespace(camera=legacy, cameras=cameras, unified_camera=uc, _use_unified_camera=True)
    port = app_module._ModelCameraAdapter(app)

    quiet(cameras._attempt_camera_connect, "ids")
    ids = uc._ids_camera
    print(f"start            : unified.is_open={uc.is_open} acquiring={ids.state.is_acquiring} "
          f"legacy.state.is_open={legacy.state.is_open} legacy.cap={legacy.cap}")

    was_open, source = port.snapshot()               # model_controller.py:477
    quiet(port.close)                                # model_controller.py:480 "Pausing camera..."
    print(f"'camera paused'  : unified.is_open={uc.is_open} acquiring={ids.state.is_acquiring} "
          f"(IDS closes so far: {SdkLessIDS.closes})  legacy.state.is_open={legacy.state.is_open}")

    # ... the model loads / the TRT engine builds for minutes ...

    SdkLessIDS.fail_next_opens = 1                   # the immediate close-then-open fails ONCE
    real_sleep = time.sleep
    time.sleep = lambda s: None
    _, out = quiet(port.reopen_and_flush, source)    # model_controller.py:612/623/687
    time.sleep = real_sleep
    print(f"reopen (1 failure): unified.is_open={uc.is_open} legacy.state.is_open={legacy.state.is_open} "
          f"_next_camera_retry_time={cameras._next_camera_retry_time} "
          f"_camera_reconnecting={cameras._camera_reconnecting}")

    # the main loop afterwards, 60 s of fake time
    now = {"t": 5000.0}
    time.perf_counter = lambda: now["t"]
    time.sleep = lambda s: now.__setitem__("t", now["t"] + s)
    loop_app = SimpleNamespace(
        last_fps_time=0.0, recorder=SimpleNamespace(is_playing=False),
        _use_unified_camera=True, unified_camera=uc, camera=legacy, cameras=cameras,
        processor=SimpleNamespace(gpu_path_active=False),
        models=SimpleNamespace(_model_loaded=True, _model_loading=False,
                               _drain_pending_trt_build=lambda: False,
                               _drain_pending_model_switch=lambda: False),
        configs=SimpleNamespace(_pending_project_switch=None),
        recording=SimpleNamespace(_drain_pending_playback_event=lambda: None,
                                  source_transitioning=False),
        ui=SimpleNamespace(render_frame=lambda: None, render_frame_raw=lambda: None),
        bus=SimpleNamespace(ui_ready=False),
        input_fps_cap=False, roi=SimpleNamespace(state=SimpleNamespace(source_size=(8, 8))),
        _last_raw_frame=None)
    loop = ml.MainLoop(loop_app, logs_dir="/nonexistent-not-used")
    opens0 = SdkLessIDS.opens
    ticks = 0
    t0 = now["t"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        while now["t"] - t0 < 60.0:
            if not loop._tick_pumps():
                loop._tick_acquire()
            ticks += 1
    print(f"60 s of main loop : {ticks} ticks, open attempts made = {SdkLessIDS.opens - opens0}, "
          f"unified.is_open={uc.is_open}  -> the camera stays down until a manual refresh")

    # same failure through the path that DOES retry (what a watchdog trip uses)
    SdkLessIDS.fail_next_opens = 1
    quiet(cameras._attempt_camera_connect, "ids")
    print(f"for comparison, _attempt_camera_connect after 1 failure: _next_camera_retry_time>0 = "
          f"{cameras._next_camera_retry_time > 0}, _camera_reconnecting={cameras._camera_reconnecting}")
    opens0 = SdkLessIDS.opens
    t0 = now["t"]
    with contextlib.redirect_stdout(buf):
        while now["t"] - t0 < 2.0:
            if not loop._tick_pumps():
                loop._tick_acquire()
    print(f"2 s of main loop  : open attempts made = {SdkLessIDS.opens - opens0}, unified.is_open={uc.is_open}")
