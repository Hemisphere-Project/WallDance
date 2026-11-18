"""runtime-core#5 / config-ops#6: an exception inside a project switch.
Real MainLoop._guarded_tick + real ConfigManager + real HealthMonitor; fakes around them.
Simulated clock; writes only under a tempdir."""
import io, contextlib, os, sys, json, tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock
from pathlib import Path

import runtime.main_loop as ml
from app import WallDanceApp
from core.config_store import ConfigStore
from core import config_schema
from core.ops_monitor import HealthMonitor
from runtime.config_manager import ConfigManager
from runtime import api
from runtime.api import SystemState

tmp = tempfile.mkdtemp(prefix="rc5_", dir=os.environ["WD_TMP"])

class Clock:
    def __init__(self): self.t = 1_000_000.0
    def time(self): return self.t
    def monotonic(self): return self.t
    def perf_counter(self): return self.t
    def sleep(self, s): self.t += s
clock = Clock()
ml.time = clock            # the module-level `time` the loop uses

class _Models:
    def __init__(self):
        self._trt_requested = True; self.current_model_name = "yolo11x-pose"
        self._model_loading = False; self._model_loaded = True
        self._pending_model_switch = None
        self.model_manager = SimpleNamespace(set_imgsz=lambda sz: None,
            is_using_tensorrt=lambda: True, engine_exists=lambda n: True)
    def _load_model_with_progress(self, name, force_pt=False): return True
    def _update_trt_banner(self): pass
    def _drain_pending_trt_build(self): return False
    def _drain_pending_model_switch(self): return False

def build(apply_config):
    store = ConfigStore(config_dir=os.path.join(tmp, "projects"),
                        last_project_file=os.path.join(tmp, "projects", "last_project.txt"))
    settings = SimpleNamespace(imgsz=1280, confidence=0.15, person_height_px=150,
                               person_height_min_ratio=0.5, person_height_max_ratio=2.0)
    camera = SimpleNamespace(state=SimpleNamespace(is_open=True, source="ids", unavailable=[]),
                             cap=None, close=lambda: None)
    cameras = SimpleNamespace(_set_camera_frame_callback=lambda cb: None, _open_camera=lambda s: True,
                              _attempt_camera_connect=lambda s: True, _pending_camera_refresh=False,
                              _next_camera_retry_time=0.0, _camera_reconnecting=False)
    models = _Models()
    wd = SimpleNamespace(beat=lambda: None, push_busy=lambda n: None, pop_busy=lambda: None)
    events = []
    bus = SimpleNamespace(ui_ready=True, publish=events.append)
    renders = [0]
    ui = SimpleNamespace(render_frame_raw=lambda: renders.__setitem__(0, renders[0] + 1),
                         render_frame=lambda: None)
    mgr = ConfigManager(models=models, cameras=cameras,
        recording=SimpleNamespace(_update_recording_ui=lambda: None),
        recorder=SimpleNamespace(stop_recording=lambda: None, stop_playback=lambda: None,
                                 set_project=lambda p: None, is_playing=False),
        camera=camera, unified_camera=None, use_unified=False, settings=settings,
        ui=SimpleNamespace(available=False), watchdog=lambda: wd,
        apply_config=apply_config, saveable_config=lambda: {}, request_reprocess=lambda: None)
    mgr.config_store = store
    app = SimpleNamespace(last_fps_time=0.0, _watchdog=wd, bus=bus, system_state=SystemState.RUN,
        processor=SimpleNamespace(light_ratio=lambda f: None), settings=settings,
        _health=HealthMonitor(gpu_stats_fn=None), fps=20.0, last_tracked=[], models=models,
        camera=camera, cameras=cameras, recorder=mgr.recorder,
        tracker=SimpleNamespace(last_over_cap=0, logger=SimpleNamespace(log=lambda *a: None)),
        calibration=SimpleNamespace(_calibrating=False), configs=mgr,
        api=SimpleNamespace(drain=lambda: None),
        recording=SimpleNamespace(_drain_pending_playback_event=lambda: None, source_transitioning=False),
        ui=ui, running=True)
    loop = ml.MainLoop(app, logs_dir=Path(tmp) / "logs")
    return loop, app, mgr, store, events, renders

def run_case(title, cfg, apply_config, max_ticks=None):
    loop, app, mgr, store, events, renders = build(apply_config)
    path = store.save("ShowB", cfg)
    # what the picker / top bar does (ConfigManager._cb_do_load_config)
    with contextlib.redirect_stdout(io.StringIO()):
        mgr._cb_do_load_config(path)
    t0 = clock.t
    out = io.StringIO()
    n_ticks = 0
    with contextlib.redirect_stdout(out):
        while clock.t - t0 < 40.0 and app.running and n_ticks < (max_ticks or 100000):
            loop._guarded_tick()
            n_ticks += 1
    alerts = [(e.kind, e.message[:70]) for e in events if isinstance(e, api.Alert)]
    err = [ln for ln in out.getvalue().splitlines() if ln.startswith(("[Error]", "[Fatal]"))]
    tb_last = [ln for ln in out.getvalue().splitlines() if "Error:" in ln and not ln.startswith("[")][:1]
    print(f"--- {title}")
    print(f"  simulated {clock.t - t0:.1f} s = {n_ticks} ticks, idle renders = {renders[0]}")
    print(f"  models._model_loading = {app.models._model_loading}   camera.state.is_open = {app.camera.state.is_open}"
          f"   app.running = {app.running}   loop._fatal = {loop._fatal}")
    print(f"  tick_fail_streak now = {loop._tick_fail_streak}   system_state = {app.system_state.name}")
    print(f"  _current_project = {mgr._current_project!r}  _active_profile = {mgr._active_profile!r}"
          f"  current_config_path = {os.path.basename(mgr.current_config_path)}")
    print(f"  console: {err}  {tb_last}")
    print(f"  alerts published: {alerts}")

base = {"camera_source": "ids", "model": "yolo11x-pose", "use_tensorrt": True, "yolo_imgsz": 1280}

# (a) control: a good file
run_case("control: valid file (1 tick = the switch itself)", config_schema.structure(base, {}, "show"), lambda cfg: None, max_ticks=1)

# (b) file-triggered, before the model load: "model": null (config_manager.py:206-210)
run_case('file has "model": null', config_schema.structure(dict(base, model=None), {}, "rehearsal"),
         lambda cfg: None)

# (c) the REAL app._apply_config_without_model on a mock app, with a key validate_flat does not cover
real_apply = lambda cfg: WallDanceApp._apply_config_without_model(MagicMock(), cfg)
run_case('real apply, good values (control)', config_schema.structure(dict(base, crossval_skel_min_kpts=4), {}, "show"),
         real_apply, max_ticks=1)
run_case('real apply, "crossval_skel_min_kpts": "four"',
         config_schema.structure(dict(base, crossval_skel_min_kpts="four"), {}, "show"), real_apply)
