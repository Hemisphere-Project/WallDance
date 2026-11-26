"""IDS stall diagnostics: the camera's own frame id / timestamp across a stall.

The laptop's stalls (2026-10-06/07: 1651-1715 ms, every buffer queued) were only
seen on the host clock. Frame id +1 across a stall = the camera sent nothing;
frame id +N = N-1 frames produced but lost in transfer. The GenTL stream counters
that moved during the stall ride along. No SDK or hardware: fakes.
"""
from collections import deque
from types import SimpleNamespace

import pytest

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


# ---------------------------------------------------------------------------------------------------------------
# Stall verdict 26/11 §8.2: a torn picture (its USB transfer broke, the first frame of every stall) is counted,
# never published.  §8.1: the link throughput limit, a project key, written at every open.
# ---------------------------------------------------------------------------------------------------------------

def _loop_camera(monkeypatch, bufs, times, w=8, h=4):
    """The real acquisition loop fed by a fake stream.  ``bufs`` = [(frame id, incomplete, pixel value)],
    ``times`` = the host clock at the loop's start, then at each buffer.  Returns (camera, buffers queued back,
    [(publish time, pixel value)] the recording callback got)."""
    import threading

    import numpy as np

    import camera.ids_camera as ic

    cam = _camera({"StreamLostFrameCount": 0})
    cam.state.fps, cam.state.width, cam.state.height = 20.0, w, h
    cam.state.pixel_format = "Mono8"
    cam._recent_stalls = deque()                 # every stall record, not the last 8
    cam._frame_lock = threading.Lock()
    cam._frame_ready = False
    recorded = []
    cam._frame_callback = lambda bgr: recorded.append((cam._latest_timestamp, int(bgr[0, 0, 0])))
    cam._acquire_running = True
    cam._acquire_error = None
    clock = iter(times)
    monkeypatch.setattr(ic.time, "perf_counter", lambda: next(clock))
    pending, queued = list(bufs), []

    class _Buf:
        def __init__(self, fid, incomplete, value):
            self.fid, self.incomplete, self.value = fid, incomplete, value

        def FrameID(self):
            return self.fid

        def IsIncomplete(self):
            return self.incomplete

    def wait(timeout_ms):
        if not pending:
            cam._acquire_running = False
            return _Buf(-1, False, 0)
        return _Buf(*pending.pop(0))

    cam._datastream.WaitForFinishedBuffer = wait
    cam._datastream.QueueBuffer = queued.append
    monkeypatch.setattr(ic, "ids_peak_ipl_extension", SimpleNamespace(BufferToImage=lambda buf: SimpleNamespace(
        get_numpy_1D=lambda: np.full(w * h, buf.value, np.uint8))))
    cam._acquisition_loop()
    return cam, queued, recorded


def test_a_torn_frame_is_counted_and_never_published(monkeypatch, capsys):
    """Three buffers, the middle one incomplete: the recording gets two, the latest frame is the third, the
    diagnostics count one incomplete, every buffer goes back to the pool."""
    cam, queued, recorded = _loop_camera(monkeypatch, [(10, False, 1), (11, True, 2), (12, False, 3)],
                                         [0.0, 0.05, 0.10, 0.15])
    assert [v for _, v in recorded] == [1, 3]                 # the callback ran twice, never on the torn one
    assert int(cam._latest_frame[0, 0]) == 3 and cam._latest_timestamp == 0.15
    assert cam.state.frame_count == 2
    diag = cam.get_stream_diagnostics()
    assert diag["incomplete"] == 1 and diag["frame_id"] == 12
    assert len(queued) == 4                                   # every buffer queued back, incl. the last
    assert "Incomplete buffer #1 (frame id 11)" in capsys.readouterr().out


def test_a_torn_last_frame_moves_the_watchdog_clock_not_the_picture(monkeypatch):
    """The camera delivered (the disconnect watchdog's clock moves) but nothing new is published."""
    cam, queued, recorded = _loop_camera(monkeypatch, [(10, False, 1), (11, True, 2)], [0.0, 0.05, 0.10])
    assert [v for _, v in recorded] == [1] and int(cam._latest_frame[0, 0]) == 1
    assert cam._latest_timestamp == 0.05 and cam.state.last_frame_time == 0.10
    assert len(queued) == 3


# 08/10 mur2 take 7 on the facade: the 52 stalls of its camlog (jeudi0809_mur2/recordings/
# slot_7_20261008_220951.avi.camlog.jsonl, ``recent_stalls``) as (seconds from the take's first row when the frames
# came back, host gap ms).  Every one: one incomplete frame, frame id +2, the driver's 1.65 s repair (verdict §1).
MUR2_7_STALLS = (
    (36.56, 1661), (63.661, 1659), (66.16, 1653), (69.459, 1699), (92.159, 1664), (107.959, 1665),
    (125.958, 1663), (127.857, 1664), (129.858, 1661), (139.807, 1669), (146.357, 1662), (149.756, 1669),
    (153.058, 1663), (156.957, 1661), (186.858, 1672), (194.956, 1669), (210.755, 1668), (215.055, 1664),
    (217.055, 1657), (219.055, 1660), (241.105, 1710), (244.255, 1675), (251.354, 1671), (260.205, 1710),
    (262.855, 1668), (268.253, 1659), (273.954, 1656), (276.203, 1665), (288.953, 1657), (293.353, 1668),
    (313.752, 1673), (321.002, 1653), (329.251, 1664), (330.953, 1666), (333.402, 1708), (335.153, 1665),
    (346.801, 1715), (376.751, 1661), (401.901, 1708), (433.75, 1662), (464.6, 1664), (467.349, 1674),
    (473.998, 1707), (498.999, 1662), (509.098, 1708), (511.397, 1661), (525.497, 1662), (534.446, 1657),
    (536.747, 1712), (545.846, 1665), (557.247, 1674), (561.897, 1671))


def _stall_take(stalls=MUR2_7_STALLS, period=0.05):
    """Buffers and host times of a 20 fps take with these stalls: whole frames on the frame grid, then each
    stall's torn frame (id +1, on time), the silence, the next whole frame (id +2: one lost in transfer)."""
    bufs, times = [], [0.0]
    t, fid = 0.0, 1000
    for t_end, gap_ms in stalls:
        t_torn = round(t_end - gap_ms / 1000.0, 6)
        while t + period < t_torn - 1e-6:
            t = round(t + period, 6)
            fid += 1
            bufs.append((fid, False, fid % 250))
            times.append(t)
        fid += 1
        bufs.append((fid, True, 255))                         # 255: a torn picture's pixels, never a whole one's
        times.append(t_torn)
        fid += 2
        t = t_end
        bufs.append((fid, False, fid % 250))
        times.append(t)
    for _ in range(20):
        t = round(t + period, 6)
        fid += 1
        bufs.append((fid, False, fid % 250))
        times.append(t)
    return bufs, times


def _main_loop(frame_times, poll_s=0.01):
    """The main loop's side of a take (core/camera_stall.py through pipeline.note_camera_frame / osc_keepalive):
    a poll every ``poll_s`` between the published frames.  Returns (clock, [(end, silence, removed)], resends)."""
    from core.camera_stall import CameraStallClock

    clock = CameraStallClock(period_s=0.05, stall_periods=3.0, max_s=5.0)
    stalls, resends, prev = [], 0, None
    for t in frame_times:
        if prev is not None:
            k = 1
            while prev + k * poll_s < t:
                p = prev + k * poll_s
                if clock.keepalive_due(p):
                    clock.keepalive_sent(p)
                    resends += 1
                k += 1
        removed = clock.on_frame(t)
        if removed > 0:
            stalls.append((t, clock.last_stall["silence_s"], removed))
        prev = t
    return clock, stalls, resends


def test_the_stall_detectors_see_the_same_stalls_with_the_torn_frame_dropped(monkeypatch):
    """08/10 mur2 7's 52 stalls fed through the real loop twice: torn frames dropped (now) and torn frames
    published (release 1).  The camera's stall detector: same count, gaps, frame-id steps.  The stall clock of
    core/camera_stall.py on what the main loop gets: same count, each stall ends on the same frame and costs
    the hold clocks the same 3 frame periods; its silence starts at the last whole frame, one frame period
    earlier.  The health line's stall rate: the same."""
    from core.ops_monitor import StallRate

    bufs, times = _stall_take()
    torn = sum(1 for _, inc, _ in bufs if inc)
    now_cam, now_q, now_rec = _loop_camera(monkeypatch, bufs, times)
    old_cam, old_q, old_rec = _loop_camera(monkeypatch, [(f, False, v) for f, _, v in bufs], times)

    # the camera's own stall detector (_track_frame: camlog, .meta) -- unchanged
    def recs(cam):
        return [(r["n"], r["gap_ms"], r["id_delta"], r["kind"]) for r in cam._recent_stalls]
    now_d, old_d = now_cam.get_stream_diagnostics(), old_cam.get_stream_diagnostics()
    assert torn == len(MUR2_7_STALLS) == 52
    assert now_d["stalls"] == old_d["stalls"] == 52
    assert now_d["stall_ms_total"] == old_d["stall_ms_total"]
    assert recs(now_cam) == recs(old_cam)
    assert [g for _, g, _, _ in recs(now_cam)] == [g for _, g in MUR2_7_STALLS]   # the camlog's own gaps
    assert {d for _, _, d, _ in recs(now_cam)} == {2}
    assert now_d["incomplete"] == 52 and now_d["frame_id"] == old_d["frame_id"]

    # what is published: one frame fewer per stall, never a torn one, every buffer back in the pool
    assert len(old_rec) - len(now_rec) == 52
    assert 255 not in {v for _, v in now_rec} and sum(1 for _, v in old_rec if v == 255) == 52
    assert len(now_q) == len(old_q) == len(bufs) + 1
    now_t, old_t = [t for t, _ in now_rec], [t for t, _ in old_rec]
    assert all(b > a for a, b in zip(now_t, now_t[1:]))       # no frame twice, none out of order

    # the stall clock on the main loop's frames
    now_clock, now_st, now_resends = _main_loop(now_t)
    old_clock, old_st, old_resends = _main_loop(old_t)
    assert now_clock.stalls == old_clock.stalls == 52
    assert [e for e, _, _ in now_st] == [e for e, _, _ in old_st]          # each stall ends on the same frame
    thr = now_clock.threshold_s
    for (_, s_now, r_now), (_, s_old, r_old) in zip(now_st, old_st):
        assert s_now - r_now == pytest.approx(thr) and s_old - r_old == pytest.approx(thr)   # 3 periods each
        assert 0.0 < s_now - s_old <= 0.05 + 1e-6                          # the torn frame's slot, no more
    assert now_clock.frames == old_clock.frames - 52

    # the health line's stall rate (StallRate on the clock's running count, once a second): the same
    def rate_rows(stall_ends):
        r, rows = StallRate(), []
        for sec in range(int(times[-1]) + 2):
            r.update(float(sec), sum(1 for e in stall_ends if e <= sec), True)
            rows.append(r.rate())
        return rows
    assert rate_rows([e for e, _, _ in now_st]) == rate_rows([e for e, _, _ in old_st])


class _Node:
    """One GenICam node of the fake camera: value, range, step, entries; every write goes to ``nm.writes``."""

    def __init__(self, nm, name, value=0, lo=0, hi=1 << 40, inc=1, refuse=False):
        self.nm, self.name, self.value, self.lo, self.hi, self.inc, self.refuse = nm, name, value, lo, hi, inc, refuse

    def Value(self):
        return self.value

    def SetValue(self, v):
        if self.refuse:
            raise RuntimeError(f"{self.name}: write refused (fake camera)")
        if not self.lo <= v <= self.Maximum():
            raise ValueError(f"{self.name}: {v} out of range")
        self.nm.writes.append((self.name, v))
        self.value = v

    def Minimum(self):
        return self.lo

    def Maximum(self):
        return self.hi

    def Increment(self):
        return self.inc

    def Entries(self):
        return [SimpleNamespace(SymbolicValue=lambda: "Mono8", AccessStatus=lambda: "ReadWrite")]

    def FindEntry(self, sym):
        return sym

    def SetCurrentEntry(self, entry):
        self.nm.writes.append((self.name, entry))

    def Execute(self):
        self.nm.writes.append((self.name, "Execute"))
        if self.name == "UserSetLoad":            # a UserSet load / reopen restores the camera's own limit
            self.nm.nodes["DeviceLinkThroughputLimit"].value = 162_000_000

    def WaitUntilDone(self):
        pass


class _FpsNode(_Node):
    """The frame rate's ceiling falls with the link limit (33.8 fps at 162 MB/s on the show crop, verdict §2)."""

    def Maximum(self):
        return 33.8 * self.nm.nodes["DeviceLinkThroughputLimit"].value / 162_000_000


class _NodeMap:
    """The show camera's nodes that matter here; any other node is a plain one (writes recorded)."""

    def __init__(self, refuse_limit=False, no_limit_node=False):
        self.writes = []
        self.nodes = {
            "DeviceLinkThroughputLimit": _Node(self, "DeviceLinkThroughputLimit", 162_000_000, 10_000_000,
                                               400_000_000, 2_000_000, refuse=refuse_limit),
            "AcquisitionFrameRate": _FpsNode(self, "AcquisitionFrameRate", 20.0, 1.0),
            "Width": _Node(self, "Width", 2688, 16, 2688, 8),
            "Height": _Node(self, "Height", 1528, 16, 1528, 2),
            "PayloadSize": _Node(self, "PayloadSize", 2688 * 1528),
        }
        if no_limit_node:
            del self.nodes["DeviceLinkThroughputLimit"]
            self.nodes["AcquisitionFrameRate"] = _Node(self, "AcquisitionFrameRate", 20.0, 1.0, 33.8)

    def FindNode(self, name):
        if name == "DeviceLinkThroughputLimit" and name not in self.nodes:
            raise KeyError(name)
        if name not in self.nodes:
            self.nodes[name] = _Node(self, name)
        return self.nodes[name]

    def limit_writes(self):
        return [v for n, v in self.writes if n == "DeviceLinkThroughputLimit"]


def _limit_camera(mbps, nm=None, target_fps=20.0):
    cam = _camera()
    cam.settings = IDSCameraSettings(target_fps=target_fps, throughput_limit_mbps=mbps)
    cam.state.fps = 20.0                         # the frame-rate step ran before
    cam._node_map = nm or _NodeMap()
    return cam


def test_the_throughput_limit_is_off_by_default_and_0_writes_nothing(capsys):
    assert IDSCameraSettings().throughput_limit_mbps == 0.0
    cam = _limit_camera(0.0)
    assert cam._apply_throughput_limit() is None and cam.get_throughput_limit() is None
    assert cam._node_map.writes == []                         # not the limit, not the frame rate
    assert "DeviceLinkThroughputLimit: 162 MB/s (the camera's own)" in capsys.readouterr().out


def test_124_writes_124_000_000_and_asks_the_frame_rate_again(capsys):
    cam = _limit_camera(124.0)
    info = cam._apply_throughput_limit()
    nm = cam._node_map
    assert nm.writes == [("DeviceLinkThroughputLimit", 124_000_000), ("AcquisitionFrameRate", 20.0)]
    assert info["mbps"] == 124.0 and info["was_mbps"] == 162.0 and info["refused"] is None
    assert info["fps"] == 20.0 and info["fps_kept"] is True and cam.state.fps == 20.0
    assert cam.get_throughput_limit() == info
    assert "DeviceLinkThroughputLimit" in IDSCamera._CAPTURE_NODES_FULL     # each take's .meta says it
    out = capsys.readouterr().out
    assert "DeviceLinkThroughputLimit: 124 MB/s (project 124, was 162); frame rate 20.0 FPS (max 25.9)" in out


def test_the_limit_is_snapped_to_the_cameras_steps_and_range():
    for asked, written in ((125.3, 126_000_000), (123.0, 122_000_000), (11.0, 10_000_000), (3.0, 10_000_000),
                           (900.0, 400_000_000), (162.0, 162_000_000)):
        cam = _limit_camera(asked)
        cam._apply_throughput_limit()
        assert cam._node_map.limit_writes() == [written], asked     # minimum + k x 2 MB/s, within [10, 400]


def test_a_limit_the_frame_rate_cannot_keep_is_said(capsys):
    cam = _limit_camera(60.0)                     # ceiling 33.8 x 60 / 162 = 12.5 fps
    info = cam._apply_throughput_limit()
    assert info["mbps"] == 60.0 and info["fps"] == pytest.approx(12.52, abs=0.01)
    assert info["fps_kept"] is False and info["fps_before"] == 20.0
    assert "The frame rate fell from 20.0 to 12.5 FPS at 60 MB/s" in capsys.readouterr().out


def test_a_camera_that_refuses_the_limit_keeps_running_with_a_logged_line(capsys):
    cam = _limit_camera(124.0, _NodeMap(refuse_limit=True))
    info = cam._apply_throughput_limit()
    assert info["refused"] and info["mbps"] == 162.0 and cam._node_map.writes == []
    assert cam.state.fps == 20.0
    assert "the camera refused 124 MB/s" in capsys.readouterr().out
    cam = _limit_camera(124.0, _NodeMap(no_limit_node=True))       # a camera without the node
    info = cam._apply_throughput_limit()
    assert info["refused"] and info["mbps"] is None and cam._node_map.writes == []


def _fake_sdk(monkeypatch, nm):
    """ids_peak / ids_peak_ipl stand-ins for IDSCamera.open(): one openable camera whose node map is ``nm``."""
    from unittest.mock import MagicMock

    import camera.ids_camera as ic

    sdk = MagicMock()
    device = MagicMock()
    device.RemoteDevice.return_value.NodeMaps.return_value = [nm]
    device.DataStreams.return_value.empty.return_value = False
    device.DataStreams.return_value.__getitem__.return_value.OpenDataStream.return_value.AnnouncedBuffers \
        .return_value = []
    descriptor = MagicMock()
    descriptor.IsOpenable.return_value = True
    descriptor.OpenDevice.return_value = device
    sdk.DeviceManager.Instance.return_value.Devices.return_value = [descriptor]
    monkeypatch.setattr(ic, "ids_peak", sdk)
    monkeypatch.setattr(ic, "ids_peak_ipl", MagicMock())
    monkeypatch.setattr(ic, "IDS_PEAK_AVAILABLE", True)
    monkeypatch.setattr(ic.IDSCamera, "_library_initialized", True)


def test_every_open_writes_the_limit_after_the_user_set_and_a_refusal_never_fails_the_open(monkeypatch, capsys):
    """IDSCamera.open() on a fake SDK: the UserSet load restores the camera's own limit, the open writes the
    project's again after it and after the frame rate -- at the first open and at a reopen.  A camera that
    refuses the write still opens."""
    from camera.ids_camera import IDSCamera

    nm = _NodeMap()
    _fake_sdk(monkeypatch, nm)
    cam = IDSCamera(IDSCameraSettings(target_fps=20.0, user_set="UserSet1", throughput_limit_mbps=124.0))
    for _ in range(2):                                       # the first open, then a reconnect's reopen
        nm.writes.clear()
        assert cam.open() is True
        names = [n for n, _ in nm.writes]
        assert nm.limit_writes() == [124_000_000]
        load, limit = names.index("UserSetLoad"), names.index("DeviceLinkThroughputLimit")
        fps_writes = [i for i, n in enumerate(names) if n == "AcquisitionFrameRate"]
        assert load < fps_writes[0] < limit < fps_writes[-1]   # UserSet, frame rate, limit, frame rate again
        assert "UserSetSave" not in names                      # nothing is ever saved into the camera
        assert nm.nodes["DeviceLinkThroughputLimit"].value == 124_000_000
        cam.close()

    refusing = _NodeMap(refuse_limit=True)
    _fake_sdk(monkeypatch, refusing)
    cam = IDSCamera(IDSCameraSettings(target_fps=20.0, user_set="UserSet1", throughput_limit_mbps=124.0))
    assert cam.open() is True and cam.state.is_open
    assert cam.get_throughput_limit()["refused"]
    assert "the camera refused 124 MB/s" in capsys.readouterr().out
    cam.close()
