"""camera-recording#1 (last part): "never raises an alert when it loops".

A camera that OPENS but then delivers no frame: REAL CameraController watchdog + retry pump via
the REAL MainLoop._tick_pumps/_tick_acquire, REAL HealthMonitor ticked at 1 Hz exactly like
MainLoop._ops_heartbeat feeds it.  Fake clock, fake camera.  OPEN_COST is an assumption.
"""
from __future__ import annotations

import contextlib
import io
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, "tests")
import test_camera_stall as T
import runtime.main_loop as ml
from runtime.camera_controller import CameraController
from camera.ids_camera import CameraSource
from core.ops_monitor import HealthMonitor

now = {"t": 1000.0}
time.perf_counter = lambda: now["t"]
time.sleep = lambda s: now.__setitem__("t", now["t"] + s)


class DeadButOpens:
    def __init__(self, open_cost):
        self.open_cost = open_cost
        self.is_open = True
        self.source_type = CameraSource.IDS_PEAK
        self.width = self.height = 8
        self.crop_ratio = None
        self.opens = self.closes = 0

    def has_error(self): return False
    def get_ids_counters(self): return (0, 0)
    def get_last_acquired_age_s(self): return float("inf")
    def read(self): return (True, None) if self.is_open else (False, None)
    def close(self):
        if self.is_open:
            self.closes += 1
        self.is_open = False
        self.source_type = None
    def open(self, source):
        self.opens += 1
        now["t"] += self.open_cost
        self.is_open = True
        self.source_type = CameraSource.IDS_PEAK
        return True
    def set_exposure_auto(self, e): return True
    def set_gain_auto(self, e): return True


class _Ui:
    available = False


def run(open_cost, minutes=10.0):
    now["t"] = 1000.0
    rig = T._Rig(stall_hold=True)
    cam = DeadButOpens(open_cost)
    camstate = SimpleNamespace(is_open=True, source="ids", available=["ids"], unavailable=[],
                               width=8, height=8)
    legacy = SimpleNamespace(state=camstate, close=lambda: None)
    cameras = CameraController(camera=legacy, unified_camera=cam, use_unified=True, ui=_Ui(),
                               preview_geometry=lambda w, h: None, repush_preview_size=lambda: None,
                               is_running=lambda: True)
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

    health = HealthMonitor()
    last_tick = 0.0
    alerts = []
    samples = {"open": 0, "down": 0}
    t0 = now["t"]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        while now["t"] - t0 < minutes * 60.0:
            # MainLoop._ops_heartbeat: 1 Hz health tick at the top of every tick
            if now["t"] - last_tick >= 1.0:
                last_tick = now["t"]
                down = cameras._camera_reconnecting or not camstate.is_open
                samples["down" if down else "open"] += 1
                for a in health.tick(now["t"], fps=20.0, n_tracked=0, in_run=True,
                                     model_ready=True, camera_open=camstate.is_open,
                                     camera_reconnecting=cameras._camera_reconnecting,
                                     playback_active=False):
                    alerts.append((round(now["t"] - t0, 1), a.kind, a.message))
            if loop._tick_pumps():
                continue
            loop._tick_acquire()
    n_log = sum(1 for l in buf.getvalue().splitlines() if "reconnecting silently" in l)
    print(f"--- camera opens but never delivers, {minutes:.0f} min, open cost {open_cost:.1f} s")
    print(f"    reopen cycles: close {cam.closes} / open {cam.opens}; "
          f"'[Camera] IDS stream stalled, reconnecting silently' printed {n_log}x")
    print(f"    1 Hz health samples: camera 'open' {samples['open']}, 'down/reconnecting' {samples['down']}")
    kinds = {}
    for _t, k, _m in alerts:
        kinds[k] = kinds.get(k, 0) + 1
    print(f"    alerts raised: {kinds if kinds else 'NONE'}")
    for a in alerts[:4]:
        print("      ", a)


if __name__ == "__main__":
    for cost in (0.5, 1.0, 2.0, 3.0):
        run(cost)
