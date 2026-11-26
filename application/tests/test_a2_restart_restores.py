"""A2 (rehearsal build, hub #t-023): a restart restores the show.

① A project load reads the empty-wall plate of the project being loaded, never the previous project's
  (audit runtime-core#4: ``_current_project`` was set only after the apply that reads the plate).
② The live plate -- the snapshot as captured with its slowly updated copy -- is saved every minute beside the
  project's plates/latest.npz (atomically, off the frame loop) and resumed at start, so a relaunch during the
  show comes back with the foreground it had.  The resumed plate is still refused for another exposure, gain
  or crop (X6 ③).
③ (timed app log lines) landed in release 1 with M2 (f6a177a): test_m2_logs.

Headless: the real ConfigManager switch, the real FrameProcessor plate path and the app's real plate methods
on a duck-typed app; synthetic walls, no GPU, no footage.
"""
from __future__ import annotations

import os
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.foreground import (LIVE_PLATE_FILE, LIVE_PLATE_SAVE_S, CleanPlate, ForegroundDetector,
                             live_plate_of, resumable_live_plate, resume_live)

W, H = 480, 264                      # a small full frame (the plate is a quarter of it)
CAMERA = (25000.0, 30.0)             # exposure_us, gain_db the plate is taken at


def _wall(rng, level=70.0, noise=1.5, w=W, h=H, left=None):
    """A textured wall; ``left``: the left half at that level (daylight fading on one side, the IR flood
    constant on the other)."""
    base = level + 6.0 * np.sin(np.arange(w) / 17.0)[None, :] + 4.0 * np.cos(np.arange(h) / 11.0)[:, None]
    if left is not None:
        base = base.copy()
        base[:, : w // 2] *= left / level
    return np.clip(base + rng.normal(0, noise, (h, w)), 0, 255).astype(np.uint8)


def _lamp(frame):
    """A lamp switched on near the wall, for good: one corner 60 DN brighter."""
    frame = frame.copy()
    frame[20:100, 20:120] = np.clip(frame[20:100, 20:120].astype(np.int16) + 60, 0, 255).astype(np.uint8)
    return frame


def _down(frame):
    return CleanPlate.from_frames([frame, frame]).plate


# --------------------------------------------------------------------------- #
# The plate store (core/foreground.py): atomic save, the live copy round trip
# --------------------------------------------------------------------------- #
def test_a_plate_file_is_replaced_atomically(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    first = CleanPlate.from_frames([_wall(rng) for _ in range(10)], source="calibrate:A")
    path = first.save(tmp_path / "plates" / "latest.npz")
    assert path == tmp_path / "plates" / "latest.npz"
    second = CleanPlate.from_frames([_wall(rng, level=120) for _ in range(10)], source="calibrate:A")

    # a power cut in the middle of the write: half a file on disk, the plate untouched
    real = np.savez_compressed

    def cut(f, **arrays):
        f.write(b"PK\x03\x04 half a zip")
        raise OSError("power cut")

    monkeypatch.setattr(np, "savez_compressed", cut)
    with pytest.raises(OSError):
        second.save(path)
    monkeypatch.setattr(np, "savez_compressed", real)
    assert np.array_equal(CleanPlate.load(path).plate, first.plate)

    # a power cut between the write and the rename: the same
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("power cut")))
    with pytest.raises(OSError):
        second.save(path)
    monkeypatch.undo()
    assert np.array_equal(CleanPlate.load(path).plate, first.plate)
    second.save(path)                                       # and a complete write replaces it
    assert np.array_equal(CleanPlate.load(path).plate, second.plate)
    # numpy's rule kept: a name without .npz gets it
    assert first.save(tmp_path / "noext") == tmp_path / "noext.npz"


def test_the_live_plate_round_trips_with_its_snapshot(tmp_path):
    rng = np.random.default_rng(2)
    snap = CleanPlate.from_frames([_wall(rng) for _ in range(10)], source="calibrate:A")
    snap.camera = {"exposure_us": CAMERA[0], "gain_db": CAMERA[1], "crop": [W, H], "roi": None}
    det = ForegroundDetector(snap)
    det._live = _down(_wall(rng, left=30.0))                # the slow update followed a fade
    saved = live_plate_of(det)
    assert saved.live is not det._live                      # a copy: the update writes it in place
    back = CleanPlate.load(saved.save(tmp_path / LIVE_PLATE_FILE))
    assert np.array_equal(back.plate, snap.plate) and np.array_equal(back.live, det._live)
    assert back.created == snap.created and back.camera == snap.camera and back.sigma == snap.sigma
    assert back.live_saved
    assert CleanPlate.load(snap.save(tmp_path / "latest.npz")).live is None    # a capture carries none
    fresh = ForegroundDetector(back)
    assert np.array_equal(fresh._live, back.plate)          # a detector starts from the snapshot...
    assert resume_live(fresh, back) and np.array_equal(fresh._live, det._live)   # ...and resumes the live
    assert np.array_equal(fresh.plate.plate, snap.plate)    # the snapshot stays as captured (light alert)
    assert not resume_live(ForegroundDetector(snap), snap)


def test_a_live_plate_resumes_only_beside_its_own_snapshot(tmp_path):
    rng = np.random.default_rng(3)
    d = tmp_path / "plates"
    one = CleanPlate.from_frames([_wall(rng) for _ in range(10)], source="calibrate:A")
    one.created = "2026-12-12T20:00:00"
    det = ForegroundDetector(one)
    det._live = det._live + 3.0
    live = live_plate_of(det).save(d / LIVE_PLATE_FILE)
    assert resumable_live_plate(live, d / "latest.npz") is None                 # no latest.npz
    one.save(d / "latest.npz")
    got = resumable_live_plate(live, d / "latest.npz")
    assert got is not None and np.array_equal(got.live, det._live)
    two = CleanPlate.from_frames([_wall(rng, level=90) for _ in range(10)], source="calibrate:A")
    two.created = "2026-12-12T20:30:00"
    two.save(d / "latest.npz")                              # Calibrate again since the last minute-save
    assert resumable_live_plate(live, d / "latest.npz") is None
    two.created = one.created                               # same stamp, other pixels: still not its own
    two.save(d / "latest.npz")
    assert resumable_live_plate(live, d / "latest.npz") is None
    one.save(d / "latest.npz")
    (d / LIVE_PLATE_FILE).write_bytes(b"not a plate")      # unreadable: the captured plate loads as before
    assert resumable_live_plate(live, d / "latest.npz") is None


# --------------------------------------------------------------------------- #
# The app around them: real ConfigManager switch, real FrameProcessor, the app's plate methods
# --------------------------------------------------------------------------- #
class _Models:
    def __init__(self):
        self._trt_requested = True
        self.current_model_name = "yolo11x-pose"
        self._model_loading = False
        self.model_manager = SimpleNamespace(
            imgsz=None, using=True, shape=None, rect_fallback_reason=None,
            set_imgsz=lambda sz: None, set_shape=lambda shape: None, effective_shape=lambda: None,
            is_using_tensorrt=lambda: True, engine_exists=lambda name: True)

    def _load_model_with_progress(self, name, force_pt=False):
        return True

    def _update_trt_banner(self):
        pass


class _Bus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


def _session(projects_dir, camera=CAMERA):
    """One app session: a ConfigManager whose apply runs the app's plate load (the full-config branch of
    ``_apply_config_without_model``), a model-less FrameProcessor, the app's real plate methods."""
    pytest.importorskip("torch")                            # runtime.config_manager / app -> camera stack
    from app import WallDanceApp
    from core.config_store import ConfigStore
    from core.pipeline import FrameProcessor, ProcessingSettings
    from runtime.config_manager import ConfigManager

    proc = FrameProcessor(model=None, settings=ProcessingSettings(
        confidence=0.3, imgsz=640, use_fp16=False, enhance_enabled=False, enhance_lite=False,
        enhance_force=False, person_height_px=200, use_gpu_path=False))
    proc.settings.fg_enabled = True
    cam = {"v": camera}
    proc.set_camera_state(lambda: cam["v"])
    seen = []                                               # (project during the apply, plate path after)
    app = SimpleNamespace(processor=proc, settings=proc.settings, bus=_Bus(),
                          recorder=SimpleNamespace(is_playing=False),
                          cameras=SimpleNamespace(exposure_gain_now=lambda: cam["v"]))

    def apply(cfg):
        project = mgr._current_project
        app._load_project_plate()
        seen.append((project, proc.settings.fg_plate_path))

    camera_obj = SimpleNamespace(state=SimpleNamespace(is_open=False, source="0", unavailable=[]),
                                 cap=None, close=lambda: None)
    mgr = ConfigManager(
        models=_Models(),
        cameras=SimpleNamespace(_set_camera_frame_callback=lambda cb: None, _open_camera=lambda s: None,
                                _attempt_camera_connect=lambda s: False),
        recording=SimpleNamespace(_update_recording_ui=lambda: None),
        recorder=SimpleNamespace(stop_recording=lambda: None, stop_playback=lambda: None,
                                 set_project=lambda p: None),
        camera=camera_obj, unified_camera=None, use_unified=False,
        settings=SimpleNamespace(imgsz=1280), ui=SimpleNamespace(available=False),
        watchdog=lambda: SimpleNamespace(push_busy=lambda n: None, pop_busy=lambda: None),
        apply_config=apply, saveable_config=lambda: {}, request_reprocess=lambda: None)
    mgr.config_store = ConfigStore(config_dir=str(projects_dir),
                                   last_project_file=str(projects_dir / "last_project.txt"))
    app.configs = mgr
    for name in ("_plate_dir", "_plate_status_text", "_publish_plate_status", "_load_project_plate",
                 "_resume_live_plate", "_save_live_plate", "_cb_capture_plate", "_sync"):
        setattr(app, name, types.MethodType(getattr(WallDanceApp, name), app))
    return SimpleNamespace(app=app, proc=proc, mgr=mgr, cam=cam, seen=seen)


def _start(s, project):
    """A start (or a switch) on ``project``: its latest config, the unified switch path."""
    path = s.mgr.config_store.latest_for_project(project)
    assert path and s.mgr._execute_project_switch_impl(path)
    return path


def _fg(s, frame, w=W, h=H):
    """One frame through the processor's plate path (load, X6 refusals, comparison)."""
    s.proc._belt_gray = frame
    s.proc._belt_gray_offset = (0, 0)
    return s.proc._fg_frame(w, h)


def _capture(s, frames):
    """Calibrate's plate capture, the app's real path (plates/plate_<stamp>.npz + latest.npz)."""
    s.app._cb_capture_plate(len(frames), "calibrate")
    for f in frames:
        s.proc._feed_plate_capture(f)
    assert s.proc._plate_capture is None


def _projects(tmp_path, *names):
    from core.config_store import ConfigStore
    store = ConfigStore(config_dir=str(tmp_path / "projects"),
                        last_project_file=str(tmp_path / "projects" / "last_project.txt"))
    for name in names:
        store.save(name, {"camera_source": "0", "fg_enabled": True})
    return tmp_path / "projects"


def _plate_file(projects, project, level, created, live_offset=None):
    """A project's plates/latest.npz at ``level`` DN (and, with ``live_offset``, its minute-saved live.npz)."""
    rng = np.random.default_rng(int(level))
    plate = CleanPlate.from_frames([_wall(rng, level=level) for _ in range(10)], source=f"calibrate:{project}")
    plate.created = created
    plate.camera = {"exposure_us": CAMERA[0], "gain_db": CAMERA[1], "crop": [W, H], "roi": None}
    d = projects / project / "plates"
    plate.save(d / "latest.npz")
    if live_offset is not None:
        det = ForegroundDetector(plate)
        det._live = det._live + live_offset
        live_plate_of(det).save(d / LIVE_PLATE_FILE)
    return plate


def test_the_project_being_loaded_is_current_while_its_config_applies(tmp_path):
    projects = _projects(tmp_path, "showA", "showB")
    s = _session(projects)
    _start(s, "showA")
    _start(s, "showB")                                      # a switch A -> B
    assert [p for p, _ in s.seen] == ["showA", "showB"]     # never 'default', never the previous one
    s2 = _session(projects)                                 # a restart on B, last project A
    s2.mgr.config_store.remember_last_project("showA")
    _start(s2, "showB")
    assert s2.seen[0][0] == "showB" and s2.mgr._current_project == "showB"
    assert s2.mgr.config_store.read_last_project() == "showB"


def test_a_restart_on_project_b_never_opens_a_plate(tmp_path):
    projects = _projects(tmp_path, "showA", "showB", "showC")
    a = _plate_file(projects, "showA", 100.0, "2026-12-12T19:00:00", live_offset=4.0)
    b = _plate_file(projects, "showB", 160.0, "2026-12-12T19:10:00")
    a_live = (projects / "showA" / "plates" / LIVE_PLATE_FILE).read_bytes()
    rng = np.random.default_rng(9)

    # a session that ran A, then switches to B
    s = _session(projects)
    _start(s, "showA")
    assert s.proc._fg_detector is not None and np.array_equal(s.proc._fg_detector.plate.plate, a.plate)
    _start(s, "showB")
    assert s.seen[-1][1] == str(projects / "showB" / "plates" / "latest.npz")
    # until B's plate is in, A's detector is still in memory: the minute-save writes nothing anywhere
    assert s.app._save_live_plate(wait=True) is None
    assert _fg(s, _wall(rng, level=160.0)) is not None      # B's plate on B's wall
    assert np.array_equal(s.proc._fg_detector.plate.plate, b.plate)
    assert s.app._save_live_plate(wait=True) == str(projects / "showB" / "plates" / LIVE_PLATE_FILE)
    assert (projects / "showA" / "plates" / LIVE_PLATE_FILE).read_bytes() == a_live   # A's folder untouched

    # B -> C, which has no plate: none, not the one in memory
    _start(s, "showC")
    assert s.seen[-1][1] == "" and s.proc._fg_detector is None and s.proc.plate_status == "none"
    assert s.app._save_live_plate(wait=True) is None
    assert not (projects / "showC" / "plates").exists()

    # a cold restart on B after a session on A (the field case: last project A, start on B)
    s2 = _session(projects)
    s2.mgr.config_store.remember_last_project("showA")
    _start(s2, "showB")
    assert s2.seen == [("showB", str(projects / "showB" / "plates" / LIVE_PLATE_FILE))]   # B's own minute-save
    assert _fg(s2, _wall(rng, level=160.0)) is not None
    assert np.array_equal(s2.proc._fg_detector.plate.plate, b.plate)
    # every plate a load opened lies in the folder of the project being loaded
    for project, path in s.seen + s2.seen:
        assert path == "" or Path(path).parent == projects / project / "plates", (project, path)


def test_the_minute_save_is_the_plate_reloaded_at_start_and_x6_still_refuses(tmp_path, capsys):
    projects = _projects(tmp_path, "showA")
    rng = np.random.default_rng(11)

    # the show: Calibrate takes the empty wall; later a lamp lights a corner of it for good and the
    # slow update absorbs it (its result is set here: A2 persists the live plate, whatever moved it)
    s = _session(projects)
    _start(s, "showA")
    _capture(s, [_wall(rng) for _ in range(12)])
    latest = projects / "showA" / "plates" / "latest.npz"
    assert latest.is_file() and s.proc.settings.fg_plate_path == str(latest)
    snap = CleanPlate.load(latest)
    assert snap.camera["exposure_us"] == CAMERA[0] and snap.camera["gain_db"] == CAMERA[1]
    lit = _lamp(_wall(rng))
    det = s.proc._fg_detector
    det._live = _down(_lamp(_wall(np.random.default_rng(12))))      # what the slow update reached
    fg = _fg(s, lit)
    assert fg is not None and fg.blobs == []                # the live plate follows the light
    path = s.app._save_live_plate()                         # the minute-save (a thread)
    s.app._live_plate_writer.result(5.0)
    assert path == str(projects / "showA" / "plates" / LIVE_PLATE_FILE)
    saved_live = det._live.copy()

    # the app is relaunched during the show: the live plate is the one loaded, not the stale snapshot
    s2 = _session(projects)
    _start(s2, "showA")
    assert s2.proc.settings.fg_plate_path == path and s2.proc._fg_loaded_path == path
    det2 = s2.proc._fg_detector
    assert det2 is not None and np.array_equal(det2._live, saved_live)
    assert np.array_equal(det2.plate.plate, snap.plate) and det2.plate.camera == snap.camera
    assert "live plate resumed (showA)" in capsys.readouterr().out
    fg = _fg(s2, lit)
    assert fg is not None and fg.blobs == [] and s2.proc.plate_status == "ready"
    body = lit.copy()
    body[60:200, 300:340] = 200                             # a dancer on the resumed plate is found
    fg = _fg(s2, body)
    assert fg is not None and len(fg.blobs) == 1 and abs(fg.blobs[0].x - 320) < 8

    # before A2 the relaunch reloaded the snapshot as captured: a false group where the light changed
    old = ForegroundDetector(CleanPlate.load(latest)).process(lit, 0, 0, (W, H))
    assert old.valid and len(old.blobs) == 1 and abs(old.blobs[0].x - 70) < 8 and abs(old.blobs[0].y - 60) < 8

    # X6 ③ still refuses the resumed plate for another exposure or gain, and the crop check holds
    s2.cam["v"] = (8800.0, CAMERA[1])
    assert _fg(s2, lit) is None and s2.proc.last_fg.reason == "plate camera"
    assert "25.0 ms" in s2.proc.plate_refusal and s2.proc.plate_status == "stale"
    s2.cam["v"] = (CAMERA[0], 27.5)
    assert _fg(s2, lit) is None and "27.5 dB" in s2.proc.plate_refusal
    s2.cam["v"] = CAMERA
    assert _fg(s2, _wall(rng, w=W + 64), w=W + 64) is None
    assert s2.proc.last_fg.reason == "plate size" and s2.proc.plate_status == "size"
    assert _fg(s2, lit) is not None                        # the camera back: the plate back

    # a minute-save never writes a refused plate
    s2.cam["v"] = (8800.0, CAMERA[1])
    _fg(s2, lit)
    assert s2.app._save_live_plate(wait=True) is None


def test_a_new_capture_wins_over_an_older_minute_save(tmp_path):
    projects = _projects(tmp_path, "showA")
    rng = np.random.default_rng(13)
    s = _session(projects)
    _start(s, "showA")
    _capture(s, [_wall(rng) for _ in range(12)])
    s.proc._fg_detector._live += 2.0
    assert s.app._save_live_plate(wait=True)
    _capture(s, [_wall(rng, level=90) for _ in range(12)])  # Calibrate again, then a relaunch within the minute
    s2 = _session(projects)
    _start(s2, "showA")
    assert s2.proc.settings.fg_plate_path.endswith("latest.npz")
    _fg(s2, _wall(rng, level=90))
    assert np.array_equal(s2.proc._fg_detector.plate.plate, CleanPlate.load(
        projects / "showA" / "plates" / "latest.npz").plate)


def test_the_minute_save_runs_off_the_loop_and_skips_what_it_must(tmp_path, monkeypatch):
    projects = _projects(tmp_path, "showA")
    rng = np.random.default_rng(14)
    s = _session(projects)
    _start(s, "showA")
    assert s.app._save_live_plate() is None                 # no plate yet
    _capture(s, [_wall(rng) for _ in range(12)])
    det = s.proc._fg_detector

    writers, gate = [], threading.Event()
    real_save = CleanPlate.save

    def slow_save(self, path, **kw):
        writers.append(threading.current_thread().name)
        gate.wait(5.0)
        return real_save(self, path, **kw)

    monkeypatch.setattr(CleanPlate, "save", slow_save)
    t0 = time.perf_counter()
    path = s.app._save_live_plate()
    loop_ms = (time.perf_counter() - t0) * 1000
    assert path and loop_ms < 250                           # the loop does not wait for the write
    assert s.app._save_live_plate() is None                 # unchanged (and the write still runs)
    det._live += 1.0
    assert s.app._save_live_plate() is None                 # changed, but the last write still runs
    gate.set()
    s.app._live_plate_writer.result(5.0)
    assert writers == ["LivePlateSave_0"]     # the session's one writer thread
    assert np.allclose(CleanPlate.load(path).live, det._live - 1.0)
    assert s.app._save_live_plate(wait=True) == path        # the next minute takes the change
    assert np.array_equal(CleanPlate.load(path).live, det._live)

    det._live += 1.0
    s.app.recorder.is_playing = True                        # a recording plays: the camera's plate is not it
    assert s.app._save_live_plate(wait=True) is None
    s.app.recorder.is_playing = False
    s.proc.invalidate_plate("exposure changed by hand - take the empty wall again")
    assert s.app._save_live_plate(wait=True) is None        # an invalid plate
    s.proc._plate_invalid = None
    s.proc.start_plate_capture(12, lambda plate, err: None)
    assert s.app._save_live_plate(wait=True) is None        # a capture runs
    s.proc._plate_capture = None
    s.proc._fg_loaded_path = None                           # a switch queued the next project's plate
    assert s.app._save_live_plate(wait=True) is None
    s.proc._fg_loaded_path = s.proc.settings.fg_plate_path
    assert s.app._save_live_plate(wait=True) == path


# --------------------------------------------------------------------------- #
# The main loop: once a minute, the first a minute after start; at quit, waited for
# --------------------------------------------------------------------------- #
def _main_loop():
    pytest.importorskip("torch")
    from runtime.main_loop import MainLoop
    return MainLoop


def test_the_main_loop_saves_once_a_minute(monkeypatch, capsys):
    MainLoop = _main_loop()
    import runtime.main_loop as ml
    calls = []
    app = SimpleNamespace(last_fps_time=0.0, _save_live_plate=lambda: calls.append(clock["t"]))
    loop = MainLoop(app)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ml.time, "monotonic", lambda: clock["t"])
    for t in (1000.0, 1030.0, 1059.9, 1060.0, 1061.0, 1119.0, 1120.5, 1200.0):
        clock["t"] = t
        loop._live_plate_minute()
    assert calls == [1060.0, 1120.5, 1200.0] and LIVE_PLATE_SAVE_S == 60.0

    def boom():
        raise OSError("disk full")

    app._save_live_plate = boom
    clock["t"] = 1300.0
    loop._live_plate_minute()                               # logged, never raised into the tick
    assert "live plate save failed (OSError: disk full)" in capsys.readouterr().out
    loop2 = MainLoop(SimpleNamespace(last_fps_time=0.0))    # an app without the saver: nothing
    loop2._live_plate_minute()
    clock["t"] = 2000.0
    loop2._live_plate_minute()


def test_a_quit_saves_the_live_plate_and_waits_for_it():
    MainLoop = _main_loop()
    order = []
    app = SimpleNamespace(
        last_fps_time=0.0,
        api=SimpleNamespace(drain=lambda: order.append("drain")),
        tracker=SimpleNamespace(logger=SimpleNamespace(close=lambda: order.append("tracker_log"))),
        _save_live_plate=lambda wait=False: order.append(("live_plate", wait)),
        _watchdog=SimpleNamespace(stop=lambda: order.append("watchdog")),
        _web_monitor=None, _remote=None,
        recorder=SimpleNamespace(close=lambda: order.append("recorder")),
        camera=SimpleNamespace(cap=None),
        ui=SimpleNamespace(destroy=lambda: order.append("ui")))
    MainLoop(app)._shutdown()
    assert order == ["drain", "tracker_log", ("live_plate", True), "watchdog", "recorder", "ui"]
