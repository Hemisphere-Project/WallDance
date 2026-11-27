"""A project switch carries nothing of the old project (A2 follow-up, #t-023, blind review 27/11).

The REAL switch path, headless.  MainLoop's pump stage runs the deferred switch
(``ConfigManager._execute_project_switch``, as live): it closes the camera, loads the project
file, applies it and reopens the camera through the real ``CameraController``.  MainLoop's
acquire stage then polls a fake IDS camera (20 fps) whose reopen takes 1 s, 3 s or 6 s to deliver
a frame -- either warming up after a quick open (the main loop polls it meanwhile) or blocking
inside the open.  Every frame goes through the FrameProcessor's real post-YOLO chain
(``replay_gpu_cached``: tracker -> identity slots -> OSC emit), a scripted detection standing in
for YOLO.  A virtual clock throughout (time.perf_counter / time.sleep), no camera, no GPU.

Stubbed: the model load (0.5 s of blocking: a TRT project always reloads at a switch), the app's
``apply_config`` (it calls no reset: app.py ``_apply_config_without_model``), the UI.

The drive: a dancer earns a slot (3 s), the tracker loses her for 0.3 s (the slot coasts), the
switch to another project starts, then an empty wall (4 s), then a first dancer elsewhere.

On release 1 (b949fdd) this drive carried the old project in every case: the tracker's track and
the coasting slot crossed the switch; a 1 s warm-up was bridged as a camera stall (stall count +1,
« [CameraStall] 1.54 s ... OSC re-sent 15x », the old slot sent on 45 frames after the switch);
3 s and 6 s warm-ups re-sent the old slot 45 times as keep-alives before the IDS timeout; after
6 s the new dancer's first frame went out at the old slot's smoothed position (980, 578)."""
from __future__ import annotations

import inspect
import json
import os
import time
import types
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("torch")                              # runtime.* -> camera stack
import runtime.main_loop as ml                             # noqa: E402
from camera.ids_camera import CameraSource                 # noqa: E402
from core import config_schema                             # noqa: E402
from core.config_store import ConfigStore                  # noqa: E402
from core.pipeline import FrameProcessor, ProcessingSettings  # noqa: E402
from runtime.camera_controller import CameraController     # noqa: E402
from runtime.config_manager import ConfigManager           # noqa: E402

W, H = 1776, 1304
P = 1.0 / 20.0              # the IDS cadence (IDS_MAX_FPS)
PROC_S = 0.02               # one frame through the pipeline, on the loop
SWITCH_BLOCK_S = 0.5        # the switch's own blocking work (the model reload stub)
SPACE = {"person_height": 200, "scale": 1.0, "pad_x": 0.0, "pad_y": 0.0, "roi_x": 0, "roi_y": 0,
         "frame_width": W}
OLD_XY, NEW_XY = (600.0, 700.0), (1300.0, 500.0)
_KP = np.array([(0, -90), (-5, -93), (5, -93), (-10, -90), (10, -90), (-25, -60), (25, -60),
                (-35, -30), (35, -30), (-38, 0), (38, 0), (-15, 0), (15, 0), (-15, 45), (15, 45),
                (-15, 90), (15, 90)], dtype=np.float64)


def _det(x, y):
    return (_KP + (x, y), np.full(17, 0.9), np.array([x - 40.0, y - 100.0, 80.0, 200.0]))


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def perf_counter(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, float(s))


class _Osc:
    enabled = True

    def __init__(self, clock):
        self.clock = clock
        self.frames = []        # [(t, [(id, x, y)])]
        self.clears = []

    def send_frame(self, tracks, w, h):
        self.frames.append((self.clock.t, [(int(t.track_id), round(float(t.smoothed_centroid[0]), 1),
                                            round(float(t.smoothed_centroid[1]), 1)) for t in tracks]))

    def send_states(self, states):
        pass

    def send_clear(self):
        self.clears.append(self.clock.t)


class _Ids:
    """UnifiedCamera stand-in on the virtual clock: frames on a 20 fps grid.  ``arm(block, warm)``:
    the next open blocks ``block`` s, then the first frame comes ``warm`` s after it (the sensor
    warms up once: a reconnect open does not restart it)."""

    def __init__(self, clock):
        self.clock = clock
        self.is_open = True
        self.width, self.height = W, H
        self.source_type = CameraSource.IDS_PEAK
        self.crop_ratio = None
        self.frame = np.zeros((8, 8, 3), np.uint8)
        self.next_t = clock.t
        self.count = 1
        self.last_acq = clock.t
        self.opens = []
        self.delivered = []
        self._arm = None

    def arm(self, block, warm):
        self._arm = (float(block), float(warm))

    def open(self, source):
        if self._arm is not None:
            block, warm = self._arm
            self._arm = None
            self.clock.t += block
            self.next_t = self.clock.t + warm
        self.opens.append(round(self.clock.t, 3))
        self.count = 0
        self.is_open = True
        self.source_type = CameraSource.IDS_PEAK
        return True

    def close(self):
        self.is_open = False

    def has_error(self):
        return False

    def read(self):
        if not self.is_open:
            return False, None
        if self.clock.t < self.next_t:
            return True, None
        while self.next_t <= self.clock.t:
            self.next_t += P
        self.count += 1
        self.last_acq = self.clock.t
        self.delivered.append(self.clock.t)
        return True, self.frame

    read_gpu = read

    def get_ids_counters(self):
        return self.count, 0

    def get_last_acquired_age_s(self):
        return self.clock.t - self.last_acq

    def get_last_cpu_frame(self):
        return self.frame

    def __getattr__(self, name):             # set_frame_callback / set_gain / start_acquisition ...
        if name.startswith(("set_", "start_", "stop_", "update_")):
            return lambda *a, **k: None
        raise AttributeError(name)


class _Models:
    def __init__(self, clock):
        self.clock = clock
        self._model_loading = False
        self._model_loaded = True
        self._trt_requested = True
        self.current_model_name = "yolo11x-pose"
        self._pending_model_switch = self._pending_trt_switch = None
        self._pending_trt_build = self._pending_model_for_trt_build = None
        self.model_manager = SimpleNamespace(
            shape=None, rect_fallback_reason=None, set_imgsz=lambda sz: None,
            set_shape=lambda shape: None, effective_shape=lambda: None,
            is_using_tensorrt=lambda: True, engine_exists=lambda name: True)

    def _load_model_with_progress(self, name, force_pt=False):
        self.clock.sleep(SWITCH_BLOCK_S)
        return True

    def _drain_pending_trt_build(self):
        return False

    def _drain_pending_model_switch(self):
        return False

    def _update_trt_banner(self):
        pass


def _project(store, name):
    flat = {"model": "yolo11x-pose", "yolo_imgsz": 1280, "use_tensorrt": True, "camera_source": "ids"}
    store.save(name, config_schema.structure(flat, {}, "show"))
    return store.latest_for_project(name)


def _session(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(time, "perf_counter", clock.perf_counter)
    monkeypatch.setattr(time, "sleep", clock.sleep)
    from app import WallDanceApp

    s = ProcessingSettings(confidence=0.3, imgsz=640, use_fp16=False, enhance_enabled=False,
                           enhance_lite=False, enhance_force=False, person_height_px=200,
                           use_gpu_path=False)
    s.osc_enabled = True
    proc = FrameProcessor(model=None, settings=s)
    osc = _Osc(clock)
    proc.osc = osc
    proc.set_output_clock(clock.perf_counter)
    cam = _Ids(clock)
    legacy = SimpleNamespace(
        state=SimpleNamespace(is_open=True, source="ids", available=["ids"], unavailable=[],
                              width=W, height=H),
        cap=None, close=lambda: None, set_input_transform=lambda t: None,
        has_capture_error=lambda: False)
    cams = CameraController(camera=legacy, unified_camera=cam, use_unified=True,
                            ui=SimpleNamespace(available=False),
                            preview_geometry=lambda w, h: None, repush_preview_size=lambda: None,
                            is_running=lambda: True)
    models = _Models(clock)
    recording = SimpleNamespace(_drain_pending_playback_event=lambda: None,
                                source_transitioning=False, _update_recording_ui=lambda: None)
    recorder = SimpleNamespace(is_playing=False, stop_recording=lambda: None,
                               stop_playback=lambda: None, set_project=lambda p: None)
    applied = []
    app = SimpleNamespace(
        processor=proc, tracker=proc.tracker, osc=osc, cameras=cams, camera=legacy,
        unified_camera=cam, _use_unified_camera=True, models=models, recording=recording,
        recorder=recorder, input_fps_cap=False, _last_raw_frame=None, last_fps_time=0.0,
        ui=SimpleNamespace(available=False, render_frame_raw=lambda: None, render_frame=lambda: None),
        roi=SimpleNamespace(state=SimpleNamespace(source_size=(8, 8)),
                            _clamp_roi_to_source=lambda *a, **k: None),
        _get_saveable_config=lambda: {})
    # The app's own reset methods, bound to this duck app (those that exist on the code under test)
    for name in ("_cb_tracker_reset", "_cb_project_start_clean"):
        if hasattr(WallDanceApp, name):
            setattr(app, name, types.MethodType(getattr(WallDanceApp, name), app))
    kw = dict(models=models, cameras=cams, recording=recording, recorder=recorder, camera=legacy,
              unified_camera=cam, use_unified=True, settings=SimpleNamespace(imgsz=1280, yolo_shape=None),
              ui=SimpleNamespace(available=False),
              watchdog=lambda: SimpleNamespace(push_busy=lambda n: None, pop_busy=lambda: None),
              apply_config=applied.append, saveable_config=lambda: {}, request_reprocess=lambda: None)
    if "start_clean" in inspect.signature(ConfigManager.__init__).parameters:
        kw["start_clean"] = app._cb_project_start_clean      # as app.py wires it
    mgr = ConfigManager(**kw)
    mgr.config_store = ConfigStore(config_dir=str(tmp_path / "projects"),
                                   last_project_file=str(tmp_path / "projects" / "last_project.txt"))
    app.configs = mgr
    paths = {n: _project(mgr.config_store, n) for n in ("showA", "showB")}
    mgr._current_project = "showA"
    loop = ml.MainLoop(app)
    monkeypatch.setattr(loop, "_log_runtime_diag_if_stalled", lambda **k: None)
    monkeypatch.setattr(loop, "_render_while_waiting", lambda: None)
    monkeypatch.setattr(loop, "_update_gpu_stats_if_due", lambda *a, **k: None)
    return SimpleNamespace(clock=clock, proc=proc, osc=osc, cam=cam, cams=cams, mgr=mgr, app=app,
                           loop=loop, paths=paths, applied=applied, n=0, scene=lambda t: [])


def _tick(s):
    """MainLoop._tick's stages that matter here, in its order: pumps, acquire, then the
    pipeline's post-YOLO chain on the frame (the scene's detections in place of YOLO)."""
    if s.loop._tick_pumps():
        return "pump"
    if s.loop._tick_acquire() is None:
        return None
    s.proc.replay_gpu_cached(s.scene(s.clock.t), SPACE, None, W, H, s.n, {})
    s.n += 1
    s.clock.sleep(PROC_S)
    return "frame"


def _run(s, seconds):
    end = s.clock.t + seconds
    while s.clock.t < end:
        _tick(s)


def _bound(s):
    slots = s.proc._slots.slots if s.proc._slots is not None else []
    return [(sl.sid, sl.state, round(sl.bound_since, 3)) for sl in slots if sl.state != "lost"]


def _drive(tmp_path, monkeypatch, capsys, *, block, warm):
    """One switch; returns the per-case record (what the switch carried)."""
    s = _session(tmp_path, monkeypatch)
    x = {"v": OLD_XY[0]}

    def walking(t):
        x["v"] += 1.0
        return [_det(x["v"], OLD_XY[1])]

    s.scene = walking
    _run(s, 3.0)                                        # a dancer earns her slot
    held = [o.track_id for o in s.proc.last_emitted]
    assert held, "the drive needs a held slot at the switch"
    s.scene = lambda t: []
    _run(s, 0.3)                                        # the tracker lost her: the slot coasts
    assert [o.track_id for o in s.proc.last_emitted] == held
    stalls0, frames0 = s.proc.stall.stalls, len(s.osc.frames)
    capsys.readouterr()
    s.cam.arm(block, warm)
    s.mgr._pending_project_switch = s.paths["showB"]
    t_req = s.clock.t
    assert _tick(s) == "pump"                           # the switch, one blocking pump
    t_end = s.clock.t
    after = {"tracks": len(s.proc.tracker.tracks) + len(s.proc.tracker._dormant),
             "slots_bound": _bound(s), "osc_clears": len(s.osc.clears)}
    _run(s, 4.0)                                        # the new project: an empty wall
    empty_frames = s.osc.frames[frames0:]
    t_new = s.clock.t
    s.scene = lambda t: [_det(*NEW_XY)]
    n_new = s.n                                         # the first dancer of the new project:
    while s.n - n_new < 40 and s.clock.t < t_new + 15.0:     # 2 s of camera frames of her
        _tick(s)
    out = capsys.readouterr().out
    old_frames = [f for f in empty_frames if f[1]]
    new_first = next((f for f in s.osc.frames if f[0] >= t_new and f[1]), None)
    new_slot = None
    if new_first is not None and s.proc._slots is not None:
        sid = new_first[1][0][0]
        sl = next(sl for sl in s.proc._slots.slots if sl.sid == sid)
        xy = np.array(new_first[1][0][1:])
        seen_at = _KP.mean(axis=0) + NEW_XY            # where the dancer is (her centroid)
        # fresh = bound after the switch AND first sent where she is (no filter state carried)
        new_slot = {"sid": sid, "bound_since": round(sl.bound_since, 3), "xy": xy.tolist(),
                    "fresh": bool(sl.bound_since >= t_end
                                  and float(np.abs(xy - seen_at).max()) <= 5.0)}
    rec = {"block_s": block, "warm_s": warm, "switch_s": round(t_end - t_req, 3),
           "silence_s": round(min(d for d in s.cam.delivered if d > t_req)
                              - max(d for d in s.cam.delivered if d <= t_req), 3),
           "held_ids": held, "stall_count_moved": s.proc.stall.stalls - stalls0,
           "camerastall_lines": [ln for ln in out.splitlines() if "[CameraStall]" in ln],
           "old_slot_frames_after_switch": len(old_frames),
           "old_slot_last_xy": list(old_frames[-1][1][0][1:]) if old_frames else None,
           "tracker_after_switch": after["tracks"], "slots_bound_after_switch": after["slots_bound"],
           "osc_clear_at_switch": after["osc_clears"], "new_dancer_slot": new_slot,
           "ids_reconnects": len(s.cam.opens) - 1}
    path = os.environ.get("WD_SWITCH_REPORT")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


CASES = [(0.0, w) for w in (1.0, 3.0, 6.0)] + [(b, 0.0) for b in (1.0, 3.0, 6.0)]


@pytest.mark.parametrize("block,warm", CASES,
                         ids=[f"warm{w:g}s" if w else f"block{b:g}s" for b, w in CASES])
def test_a_project_switch_carries_nothing_of_the_old_project(tmp_path, monkeypatch, capsys, block, warm):
    """warmN: the reopen is quick, the camera delivers its first frame N s later (the main loop
    polls it meanwhile).  blockN: the open itself blocks N s inside the switch."""
    rec = _drive(tmp_path, monkeypatch, capsys, block=block, warm=warm)
    assert rec["stall_count_moved"] == 0 and not rec["camerastall_lines"], rec
    assert rec["old_slot_frames_after_switch"] == 0, rec
    assert rec["tracker_after_switch"] == 0 and rec["slots_bound_after_switch"] == [], rec
    assert rec["new_dancer_slot"] is not None and rec["new_dancer_slot"]["fresh"], rec


def test_a_lighting_profile_switch_keeps_the_slots(tmp_path, monkeypatch):
    """Same wall, same dancers: the Show/Rehearsal switch resets nothing."""
    s = _session(tmp_path, monkeypatch)
    x = {"v": OLD_XY[0]}

    def walking(t):
        x["v"] += 1.0
        return [_det(x["v"], OLD_XY[1])]

    s.scene = walking
    _run(s, 3.0)
    held = [o.track_id for o in s.proc.last_emitted]
    tracks = [t.track_id for t in s.proc.tracker.tracks]
    assert held and tracks
    n0 = len(s.osc.frames)
    other = next(n for n in config_schema.PROFILE_NAMES if n != s.mgr._active_profile)
    s.mgr._cb_profile_switch(other)
    assert s.mgr._active_profile == other and s.applied
    assert [t.track_id for t in s.proc.tracker.tracks] == tracks
    _run(s, 1.0)
    assert all([tr[0] for tr in f[1]] == held for f in s.osc.frames[n0:]), s.osc.frames[n0:n0 + 5]
    assert not s.osc.clears and s.proc.stall.stalls == 0


def test_the_app_wires_the_clean_start_into_its_switch():
    """app.py hands ConfigManager the app's own clean start (the tests above bind it to a duck
    app; this keeps the wiring itself from being lost in a merge)."""
    from app import WallDanceApp
    src = inspect.getsource(WallDanceApp.__init__)
    assert "start_clean=self._cb_project_start_clean" in src
