"""camera-recording#1 / runtime-core#3 / identity-output#4.

Drives the REAL MainLoop._tick_pumps + _tick_acquire, the REAL CameraController (2.5 s watchdog,
retry pump) and the REAL FrameProcessor output stage (identity slots + D34 stall clock) against a
scripted IDS-like camera and a fake clock.  No GPU, no camera, no GUI, nothing written.

Assumptions of the fake (NOT measured): a reopen costs OPEN_COST seconds of blocked main thread,
and the camera stays silent until the scripted end of the stall whether it is reopened or not.
"""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, "tests")
import test_camera_stall as T                      # _Rig, _st, P (read-only reuse)
import runtime.main_loop as ml
from runtime.camera_controller import CameraController
from camera.ids_camera import CameraSource

P = T.P
now = {"t": 50.0}
time.perf_counter = lambda: now["t"]               # one clock for main loop + controller
time.sleep = lambda s: now.__setitem__("t", now["t"] + s)


class FakeUnified:
    """Newest-only IDS-like source: a frame every P unless silent; open() blocks OPEN_COST."""

    def __init__(self, open_cost):
        self.open_cost = open_cost
        self.is_open = True
        self.source_type = CameraSource.IDS_PEAK
        self.width = self.height = 8
        self.crop_ratio = None
        self.silent_until = None
        self.frame_count = 0
        self.last_acq = now["t"]
        self._next = now["t"]
        self.closes = self.opens = 0
        self.frame = np.zeros((8, 8, 3), np.uint8)

    def _pump(self):                                # the acquisition thread, lazily
        t = now["t"]
        got = False
        while self._next <= t:
            if self.silent_until is None or self._next >= self.silent_until:
                self.frame_count += 1
                self.last_acq = self._next
                got = True
                self._unread = True                  # newest-only slot holds an unread frame
            self._next += P
        return got

    def has_error(self):
        return False

    def get_ids_counters(self):
        self._pump()
        return (self.frame_count, 0)

    def get_last_acquired_age_s(self):
        self._pump()
        return now["t"] - self.last_acq

    def read(self):
        if not self.is_open:
            return False, None
        self._pump()
        if getattr(self, "_unread", False):
            self._unread = False
            return True, self.frame
        return True, None

    def close(self):
        if self.is_open:
            self.closes += 1
        self.is_open = False
        self.source_type = None

    def open(self, source):
        self.opens += 1
        now["t"] += self.open_cost                   # blocking, on the main thread
        self.is_open = True
        self.source_type = CameraSource.IDS_PEAK
        self.frame_count = 0
        self._next = now["t"]
        self._unread = False
        return True

    def set_exposure_auto(self, e):
        return True

    def set_gain_auto(self, e):
        return True


class _Ui:
    available = False


def run(stall_s, watchdog_s, open_cost, tracker_gap_frames=10, label=""):
    now["t"] = 50.0
    rig = T._Rig(stall_hold=True)
    rig.p.set_output_clock(lambda: now["t"])
    cam = FakeUnified(open_cost)
    camstate = SimpleNamespace(is_open=True, source="ids", available=["ids"], unavailable=[],
                               width=8, height=8)
    legacy = SimpleNamespace(state=camstate, close=lambda: None)
    cameras = CameraController(camera=legacy, unified_camera=cam, use_unified=True, ui=_Ui(),
                               preview_geometry=lambda w, h: None, repush_preview_size=lambda: None,
                               is_running=lambda: True)
    cameras._ids_disconnect_timeout_s = watchdog_s
    cameras._last_camera_open_time = now["t"]
    app = SimpleNamespace(
        last_fps_time=0.0, recorder=SimpleNamespace(is_playing=False),
        _use_unified_camera=True, unified_camera=cam, camera=legacy, cameras=cameras,
        processor=rig.p,
        models=SimpleNamespace(_model_loaded=True, _model_loading=False,
                               _drain_pending_trt_build=lambda: False,
                               _drain_pending_model_switch=lambda: False),
        configs=SimpleNamespace(_pending_project_switch=None),
        recording=SimpleNamespace(_drain_pending_playback_event=lambda: None,
                                  source_transitioning=False),
        ui=SimpleNamespace(render_frame=lambda: None, render_frame_raw=lambda: None),
        input_fps_cap=False, roi=SimpleNamespace(state=SimpleNamespace(source_size=(8, 8))),
        _last_raw_frame=None)
    loop = ml.MainLoop(app, logs_dir="/nonexistent-not-used")
    loop._log_runtime_diag_if_stalled = lambda **k: None
    loop._render_while_waiting = lambda: None
    loop._update_gpu_stats_if_due = lambda *a, **k: None
    loop._camera_period_s = lambda: P

    emitted = []                                     # (t, ids) of real (non keep-alive) frames
    x = {"v": 500.0}
    state = {"miss": 0}

    def tick():
        if loop._tick_pumps():
            return None
        tk = loop._tick_acquire()
        if tk is not None:
            rig.osc.now = now["t"]
            if state["miss"] > 0:                    # the tracker has lost the dancer for a while
                state["miss"] -= 1
                tracks = []
            else:
                x["v"] += 8.0
                tracks = [T._st(7, x["v"], 400.0)]
            out = rig.p._run_identity_slots(tracks, lambda v: v, 1920, 1080)
            rig.p._emit(out, 1920, 1080, 1, True, False, True)
            emitted.append((now["t"], [o.track_id for o in out]))
            now["t"] += 0.045                        # processing time of the frame
        else:
            rig.osc.now = now["t"]
        return tk

    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        guard = 0
        while len(emitted) < 40 and guard < 200000:  # 2 s live: a full (earned) hold
            tick()
            guard += 1
        assert len(emitted) == 40, "fake camera never delivered"
        t_last = cam.last_acq
        n_osc0 = len(rig.osc.frames)
        cam.silent_until = t_last + stall_s          # the camera sends nothing for stall_s
        state["miss"] = tracker_gap_frames           # ... and the tracker needs 0.5 s to re-find
        n_em0 = len(emitted)
        guard = 0
        while len(emitted) < n_em0 + 30 and now["t"] < t_last + 60 and guard < 200000:
            tick()
            guard += 1
    log = [l for l in buf.getvalue().splitlines() if "Camera" in l or "IDS" in l]
    osc_during = [t for t, _ in rig.osc.frames[n_osc0:] if t < emitted[n_em0][0] - 1e-9]
    first_back_t, first_back_ids = emitted[n_em0]
    ids_after = [ids for _, ids in emitted[n_em0:n_em0 + 30]]
    lost = sum(1 for ids in ids_after if ids != [1])
    print(f"--- {label}: stall {stall_s:.1f} s, reopen watchdog {watchdog_s:.1f} s, open cost {open_cost:.1f} s")
    print(f"    camera close()/open() calls during the stall : {cam.closes}/{cam.opens}")
    print(f"    keep-alive OSC frames sent                   : {len(osc_during)}"
          f"  (last one {max(osc_during) - t_last:.2f} s after the last frame)" if osc_during
          else "    keep-alive OSC frames sent                   : 0")
    print(f"    first frame processed after                  : {first_back_t - t_last:.2f} s")
    print(f"    OSC silent before that frame for             : "
          f"{first_back_t - (max(osc_during) if osc_during else t_last):.2f} s")
    print(f"    hold-clock time removed (stall.offset)       : {rig.p.stall.offset:.2f} s"
          f"  (stalls bridged: {rig.p.stall.stalls})")
    print(f"    slot ids on the first frame back             : {first_back_ids}")
    print(f"    frames (of the next 30) without id 1         : {lost}")
    for l in log[:6]:
        print("      log:", l)


if __name__ == "__main__":
    from core.config import CAMERA_STALL_MAX_S
    print("CAMERA_STALL_MAX_S =", CAMERA_STALL_MAX_S)
    run(1.7, 2.5, 1.0, label="A routine stall, shipped watchdog")
    run(3.5, 2.5, 1.0, label="B stall inside the 5 s cap, shipped watchdog")
    run(3.5, 6.0, 1.0, label="C same stall, watchdog raised above the cap")
    run(4.5, 2.5, 1.0, label="D stall inside the 5 s cap, shipped watchdog")
    run(4.5, 6.0, 1.0, label="E same stall, watchdog raised above the cap")
