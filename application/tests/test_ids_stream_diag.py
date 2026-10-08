"""IDS stall diagnostics: the camera's own frame id / timestamp across a stall.

The laptop's stalls (2026-10-06/07: 1651-1715 ms, every buffer queued) were only
seen on the host clock. Frame id +1 across a stall = the camera sent nothing;
frame id +N = N-1 frames produced but lost in transfer. The GenTL stream counters
that moved during the stall ride along. No SDK or hardware: fakes.
"""
from collections import deque
from types import SimpleNamespace

from camera.ids_camera import (STALL_CAMERA_SILENT, STALL_LOST_IN_TRANSFER,
                               STALL_UNKNOWN, IDSCamera, IDSCameraSettings,
                               IDSCameraState, buffer_meta, classify_stall)


class _Counter:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def Value(self):
        return self.store[self.name]


class _StreamNodeMap:
    def __init__(self, store):
        self.store = store

    def FindNode(self, name):
        if name not in self.store:
            raise KeyError(name)
        return _Counter(self.store, name)


class _DataStream:
    def __init__(self, store, queued=16, announced=16, underruns=0):
        self.store, self.queued, self.announced, self.underruns = store, queued, announced, underruns

    def NodeMaps(self):
        return [_StreamNodeMap(self.store)]

    def NumBuffersQueued(self):
        return self.queued

    def AnnouncedBuffers(self):
        return [object()] * self.announced

    def NumUnderruns(self):
        return self.underruns


def _camera(store=None):
    cam = object.__new__(IDSCamera)               # no SDK / hardware needed
    cam.close = lambda: None                      # __del__ on a bare object
    cam.settings = IDSCameraSettings()
    cam.state = IDSCameraState()
    cam._stall_threshold_s = 0.4
    cam._stall_count = 0
    cam._last_acq_frame_time = 0.0
    cam._stall_ms_total = 0.0
    cam._last_frame_id = None
    cam._last_dev_ts_ns = None
    cam._frame_id_gaps = 0
    cam._frame_ids_skipped = 0
    cam._incomplete_frames = 0
    cam._recent_stalls = deque(maxlen=8)
    cam._stall_counters_ref = {}
    cam._datastream = _DataStream(store) if store is not None else None
    return cam


def test_classify_stall():
    assert classify_stall(1) == STALL_CAMERA_SILENT
    assert classify_stall(34) == STALL_LOST_IN_TRANSFER
    assert classify_stall(None) == STALL_UNKNOWN
    assert classify_stall(0) == STALL_UNKNOWN


def test_buffer_meta_reads_what_the_buffer_has():
    buf = SimpleNamespace(FrameID=lambda: 42, Timestamp_ns=lambda: 1_000_000,
                          IsIncomplete=lambda: True)
    assert buffer_meta(buf) == (42, 1_000_000, True)

    def boom():
        raise RuntimeError("not available")
    assert buffer_meta(SimpleNamespace(FrameID=boom)) == (None, None, False)
    assert buffer_meta(SimpleNamespace(FrameID=lambda: 7, Timestamp_ns=lambda: 0)) == (7, None, False)


def test_camera_silent_stall(capsys):
    store = {"StreamDeliveredFrameCount": 100, "StreamLostFrameCount": 0,
             "StreamPipeTotalErrorCount": 0}
    cam = _camera(store)
    cam._reset_frame_tracking()
    cam._last_acq_frame_time = 10.0
    assert cam._track_frame(10.05, 100, 1_000_000_000) is None
    assert cam._track_frame(10.10, 101, 1_050_000_000) is None
    store["StreamDeliveredFrameCount"] = 102           # moves every frame: never reported
    store["StreamPipeTotalErrorCount"] = 1
    rec = cam._track_frame(11.77, 102, 2_717_000_000)  # 1.67 s host gap, id +1
    assert rec["kind"] == STALL_CAMERA_SILENT and rec["id_delta"] == 1
    assert rec["gap_ms"] == 1670 and rec["cam_dt_ms"] == 1667
    assert rec["pool"] == "queued=16/16"
    assert rec["stream_delta"] == {"PipeTotalErrorCount": 1}
    out = capsys.readouterr().out
    assert "USB3 SEVERE: 1670ms gap (#1, queued=16/16)" in out      # the old prefix, kept
    assert "frame id +1: the camera sent nothing" in out
    assert "camera clock +1667ms" in out and "stream PipeTotalErrorCount+1" in out
    assert cam._frame_id_gaps == 0 and cam._frame_ids_skipped == 0


def test_lost_in_transfer_stall_and_cumulative_state(capsys):
    cam = _camera({"StreamLostFrameCount": 0})
    cam._reset_frame_tracking()
    cam._last_acq_frame_time = 0.0
    cam._track_frame(0.05, 500)
    cam._datastream.store["StreamLostFrameCount"] = 33
    rec = cam._track_frame(1.75, 534)                   # 33 frames numbered, never delivered
    assert rec["kind"] == STALL_LOST_IN_TRANSFER and rec["id_delta"] == 34
    assert rec["cam_dt_ms"] is None                      # no camera timestamp here
    assert rec["stream_delta"] == {"LostFrameCount": 33}
    assert "34: 33 frame(s) lost in transfer" in capsys.readouterr().out
    diag = cam.get_stream_diagnostics()
    assert diag["stalls"] == 1 and diag["stall_ms_total"] == 1700
    assert diag["frame_id"] == 534 and diag["frame_id_gaps"] == 1
    assert diag["frame_ids_skipped"] == 33
    assert diag["counters"] == {"LostFrameCount": 33, "Underruns": 0}
    assert diag["recent_stalls"][-1]["n"] == 1


def test_frame_id_restart_is_not_a_gap():
    cam = _camera()
    cam._last_acq_frame_time = 0.0
    cam._track_frame(0.05, 9000)
    cam._track_frame(0.10, 3)                            # a new acquisition restarts the ids
    cam._track_frame(0.15, 4)
    assert cam._frame_id_gaps == 0 and cam._last_frame_id == 4


def test_incomplete_buffers_are_counted(capsys):
    cam = _camera()
    cam._last_acq_frame_time = 0.0
    cam._track_frame(0.05, 1, incomplete=True)
    assert cam.get_stream_diagnostics()["incomplete"] == 1
    assert "Incomplete buffer #1 (frame id 1)" in capsys.readouterr().out


def test_capture_settings_carry_the_stream_diagnostics():
    cam = _camera({"StreamLostFrameCount": 2, "StreamPipeErrorRecoveryCount": 1})
    cam.state.is_open = True
    cam._node_map = SimpleNamespace(FindNode=lambda name: (_ for _ in ()).throw(KeyError(name)))
    snap = cam.get_capture_settings(fast=True)
    assert snap["stream"]["counters"] == {"LostFrameCount": 2, "PipeErrorRecoveryCount": 1,
                                          "Underruns": 0}
    assert snap["stream"]["stalls"] == 0 and snap["stream"]["recent_stalls"] == []


def test_acquisition_loop_tracks_frame_ids(monkeypatch, capsys):
    """The real loop, fed by a fake stream: frame ids reach the tracker, a stall
    with skipped ids is reported, buffers always go back to the pool."""
    import threading

    import numpy as np

    import camera.ids_camera as ic

    w, h = 8, 4
    cam = _camera({"StreamLostFrameCount": 0})
    cam.state.fps, cam.state.width, cam.state.height = 20.0, w, h
    cam.state.pixel_format = "Mono8"
    cam._frame_lock = threading.Lock()
    cam._frame_ready = False
    cam._frame_callback = None
    cam._acquire_running = True
    cam._acquire_error = None

    ids = [10, 11, 12, 46, 47]                  # 33 ids skipped between 12 and 46
    clock = iter([0.0, 0.05, 0.10, 0.15, 1.85, 1.90])
    monkeypatch.setattr(ic.time, "perf_counter", lambda: next(clock))
    queued = []

    class _Buf:
        def __init__(self, fid):
            self.fid = fid

        def FrameID(self):
            return self.fid

    def wait(timeout_ms):
        if not ids:
            cam._acquire_running = False
            return _Buf(-1)
        return _Buf(ids.pop(0))

    cam._datastream.WaitForFinishedBuffer = wait
    cam._datastream.QueueBuffer = queued.append
    frame = SimpleNamespace(get_numpy_1D=lambda: np.zeros(w * h, np.uint8))
    monkeypatch.setattr(ic, "ids_peak_ipl_extension",
                        SimpleNamespace(BufferToImage=lambda buf: frame))
    cam._acquisition_loop()

    assert len(queued) == 6                      # every buffer returned, incl. the last
    assert cam.state.frame_count == 5
    diag = cam.get_stream_diagnostics()
    assert diag["frame_id"] == 47 and diag["frame_ids_skipped"] == 33
    assert diag["stalls"] == 1 and diag["recent_stalls"][0]["kind"] == STALL_LOST_IN_TRANSFER
    assert "1700ms gap (#1, queued=16/16) frame id +34" in capsys.readouterr().out
