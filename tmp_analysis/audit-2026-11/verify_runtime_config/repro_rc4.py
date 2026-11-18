"""runtime-core#4: which plate folder does a project switch read?
Real ConfigManager._execute_project_switch_impl + real WallDanceApp._plate_dir /
_load_project_plate, duck-typed app around them. Writes only under a tempdir."""
import os, sys, tempfile, types
from types import SimpleNamespace
import numpy as np

from app import WallDanceApp
from core.config_store import ConfigStore
from core import config_schema
from runtime.config_manager import ConfigManager

tmp = tempfile.mkdtemp(prefix="rc4_", dir=os.environ["WD_TMP"])
store = ConfigStore(config_dir=os.path.join(tmp, "projects"),
                    last_project_file=os.path.join(tmp, "projects", "last_project.txt"))

class _Models:
    def __init__(self):
        self._trt_requested = True; self.current_model_name = "yolo11x-pose"
        self._model_loading = False
        self.model_manager = SimpleNamespace(set_imgsz=lambda sz: None,
            is_using_tensorrt=lambda: True, engine_exists=lambda n: True)
    def _load_model_with_progress(self, name, force_pt=False): return True
    def _update_trt_banner(self): pass

settings = SimpleNamespace(imgsz=1280, confidence=0.15, fg_plate_path="<unset>")
camera = SimpleNamespace(state=SimpleNamespace(is_open=False, source="0", unavailable=[]),
                         cap=None, close=lambda: None)
fake_app = SimpleNamespace(settings=settings,
    processor=SimpleNamespace(_fg_loaded_path="x", set_clean_plate=lambda p: None),
    _publish_plate_status=lambda: None)
fake_app._plate_dir = types.MethodType(WallDanceApp._plate_dir, fake_app)
seen = []
def apply_config(cfg):
    # exactly what app.py:1497-1498 does on a full config
    if "model" in cfg or "camera_source" in cfg:
        seen.append(fake_app.configs._current_project)
        WallDanceApp._load_project_plate(fake_app)

mgr = ConfigManager(models=_Models(),
    cameras=SimpleNamespace(_set_camera_frame_callback=lambda cb: None, _open_camera=lambda s: None,
                            _attempt_camera_connect=lambda s: False),
    recording=SimpleNamespace(_update_recording_ui=lambda: None),
    recorder=SimpleNamespace(stop_recording=lambda: None, stop_playback=lambda: None, set_project=lambda p: None),
    camera=camera, unified_camera=None, use_unified=False, settings=settings,
    ui=SimpleNamespace(available=False),
    watchdog=lambda: SimpleNamespace(push_busy=lambda n: None, pop_busy=lambda: None),
    apply_config=apply_config, saveable_config=lambda: {}, request_reprocess=lambda: None)
mgr.config_store = store
fake_app.configs = mgr

paths = {}
for name in ("ShowA", "ShowB"):
    paths[name] = store.save(name, config_schema.structure(
        {"camera_source": "0", "model": "yolo11x-pose", "use_tensorrt": True, "yolo_imgsz": 1280}, {}, "show"))
    os.makedirs(os.path.join(store.config_dir, name, "plates"))
    np.savez(os.path.join(store.config_dir, name, "plates", "latest.npz"), a=np.zeros(1))

def rel(p): return os.path.relpath(p, store.config_dir) if p else repr(p)

print("initial _current_project:", mgr._current_project)
import io, contextlib
for step, name in (("cold start -> ShowA", "ShowA"), ("switch -> ShowB", "ShowB"), ("reload ShowB", "ShowB")):
    with contextlib.redirect_stdout(io.StringIO()):
        ok = mgr._execute_project_switch_impl(paths[name])
    print(f"{step:22s} ok={ok} project_during_apply={seen[-1]!r:10s} "
          f"project_after={mgr._current_project!r:8s} fg_plate_path={rel(settings.fg_plate_path)}")
