"""camera-recording#11: incomplete buffers are delivered as frames; an unpack failure is a silent
no-frame loop that only the 2.5 s reopen watchdog sees.

REAL: IDSCamera.__init__, start_acquisition, _acquisition_loop, _track_frame, _unpack_raw, read,
has_error; UnifiedCamera; CameraController._ids_stream_timed_out.
FAKE: the SDK (datastream, node map, BufferToImage).  No GPU, no hardware.
"""
from __future__ import annotations

import contextlib
import io
import time
from types import SimpleNamespace

import numpy as np

import camera.ids_camera as ic
ic.CUDA_AVAILABLE = False
from camera.ids_camera import IDSCamera, UnifiedCamera, CameraSource
from runtime.camera_controller import CameraController

W, H = 64, 48


class _Buf:
    def __init__(self, n, incomplete):
        self.n, self.incomplete = n, incomplete
    def FrameID(self): return self.n
    def Timestamp_ns(self): return self.n * 50_000_000
    def IsIncomplete(self): return self.incomplete


class _Stream:
    def __init__(self, incomplete_every=0):
        self.n = 0
        self.incomplete_every = incomplete_every
        self.queued = 0
    def StartAcquisition(self): pass
    def StopAcquisition(self, *a): pass
    def KillWait(self): pass
    def Flush(self, *a): pass
    def AnnouncedBuffers(self): return []
    def RevokeBuffer(self, b): pass
    def QueueBuffer(self, b): self.queued += 1
    def WaitForFinishedBuffer(self, timeout_ms):
        time.sleep(0.02)
        self.n += 1
        return _Buf(self.n, bool(self.incomplete_every) and self.n % self.incomplete_every == 0)


class _Node:
    def Execute(self): pass
    def WaitUntilDone(self): pass


# BufferToImage: a full-size payload (an incomplete buffer still has the full payload size)
ic.ids_peak_ipl_extension = SimpleNamespace(
    BufferToImage=lambda buf: SimpleNamespace(
        get_numpy_1D=lambda: np.full(W * H, buf.n % 256, np.uint8)))


class SdkLess(IDSCamera):
    @classmethod
    def _acquire_library(cls):
        pass


def make(pixel_format, incomplete_every=0):
    cam = SdkLess()
    cam._datastream = _Stream(incomplete_every)
    cam._node_map = SimpleNamespace(FindNode=lambda name: _Node())
    cam.state.is_open = True
    cam.state.width, cam.state.height, cam.state.fps = W, H, 20.0
    cam.state.pixel_format = pixel_format
    return cam


def run(pixel_format, incomplete_every, seconds, label):
    cam = make(pixel_format, incomplete_every)
    uc = UnifiedCamera()
    uc._ids_camera, uc._source_type, uc.is_open = cam, CameraSource.IDS_PEAK, True
    legacy = SimpleNamespace(state=SimpleNamespace(is_open=True, source="ids", available=["ids"],
                                                   unavailable=[], width=W, height=H))
    cameras = CameraController(camera=legacy, unified_camera=uc, use_unified=True,
                               ui=SimpleNamespace(available=False),
                               preview_geometry=lambda w, h: None,
                               repush_preview_size=lambda: None, is_running=lambda: True)
    cameras._last_camera_open_time = time.perf_counter()
    buf = io.StringIO()
    got = 0
    with contextlib.redirect_stdout(buf):
        cam.start_acquisition()
        t_end = time.time() + seconds
        while time.time() < t_end:
            ok, frame = cam.read()
            if frame is not None:
                got += 1
            time.sleep(0.005)
        tripped = cameras._ids_stream_timed_out()
        err = cam.has_error()
        cam._acquire_running = False
        time.sleep(0.1)
    lines = buf.getvalue().splitlines()
    spam = sum(1 for l in lines if "Unknown pixel format" in l or "Unpack error" in l)
    print(f"--- {label}")
    print(f"    buffers from the stream: {cam._datastream.n}, flagged incomplete: {cam._incomplete_frames}, "
          f"frames stored (state.frame_count): {cam.state.frame_count}, frames read by the main loop: {got}")
    print(f"    has_error() = {err}, _acquire_error = {cam._acquire_error!r}, "
          f"'unknown format / unpack error' lines printed: {spam}")
    print(f"    reopen watchdog _ids_stream_timed_out() after {seconds:.1f} s = {tripped}")
    cam.state.is_acquiring = False
    cam.close = lambda: None


if __name__ == "__main__":
    run("Mono8", 5, 1.5, "Mono8, every 5th buffer flagged IsIncomplete")
    run("Mono12", 0, 3.0, "pixel format the unpacker does not know (Mono12)")
