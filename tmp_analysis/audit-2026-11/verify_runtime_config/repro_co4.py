"""config-ops#4: a lighting-profile switch, then a restart (a fresh ConfigManager on the same folder)."""
import io, contextlib, os, tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock
from core import config_schema
from core.config_store import ConfigStore
from runtime.config_manager import ConfigManager

tmp = tempfile.mkdtemp(prefix="co4_", dir=os.environ["WD_TMP"])
def manager(applied, live):
    store = ConfigStore(config_dir=os.path.join(tmp, "projects"),
                        last_project_file=os.path.join(tmp, "projects", "last_project.txt"))
    models = MagicMock(); models.current_model_name = "yolo11x-pose"
    models.model_manager.is_using_tensorrt.return_value = True
    models.model_manager.engine_exists.return_value = True
    models._load_model_with_progress.return_value = True
    camera = SimpleNamespace(state=SimpleNamespace(is_open=False, source="0", unavailable=[]), cap=None, close=lambda: None)
    def apply(cfg):
        applied.append(cfg); live.update({k: v for k, v in cfg.items() if k in config_schema.PROFILE_KEYS})
    mgr = ConfigManager(models=models, cameras=MagicMock(), recording=MagicMock(), recorder=MagicMock(),
                        camera=camera, unified_camera=None, use_unified=False, settings=SimpleNamespace(imgsz=1280),
                        ui=SimpleNamespace(available=False), watchdog=lambda: MagicMock(),
                        apply_config=apply, saveable_config=lambda: dict(live), request_reprocess=lambda: None)
    mgr.config_store = store
    return mgr, store

day = {"ids_exposure_us": 8000.0, "ids_gain_db": 0.0, "gamma": 1.0, "confidence": 0.25}
night = {"ids_exposure_us": 50000.0, "ids_gain_db": 24.0, "gamma": 0.6, "confidence": 0.15}
shared = {"camera_source": "0", "model": "yolo11x-pose", "use_tensorrt": True, "yolo_imgsz": 1280}
live = dict(shared, **day)
applied = []
mgr, store = manager(applied, live)
mgr._profiles = {"show": dict(night), "rehearsal": dict(day)}; mgr._active_profile = "rehearsal"
mgr._current_project = "Show"
with contextlib.redirect_stdout(io.StringIO()):
    mgr._cb_do_save_config("Show")                      # afternoon: saved on REHEARSAL
files = lambda: sorted(f for f in os.listdir(os.path.join(store.config_dir, "Show")) if f.endswith(".json"))
n0 = len(files())
with contextlib.redirect_stdout(io.StringIO()):
    mgr._cb_profile_switch("show")                      # dusk: operator switches to SHOW
print(f"after the switch: active={mgr._active_profile!r} live exposure={live['ids_exposure_us']:.0f} us gain={live['ids_gain_db']} dB"
      f" | project files before/after: {n0}/{len(files())}")
# restart: a fresh manager, the picker loads the project's latest file
live2, applied2 = {}, []
mgr2, store2 = manager(applied2, live2)
with contextlib.redirect_stdout(io.StringIO()):
    ok = mgr2._execute_project_switch_impl(store2.latest_for_project("Show"))
print(f"after a restart:  ok={ok} active={mgr2._active_profile!r} applied exposure={applied2[-1]['ids_exposure_us']:.0f} us "
      f"gain={applied2[-1]['ids_gain_db']} dB gamma={applied2[-1]['gamma']}")
