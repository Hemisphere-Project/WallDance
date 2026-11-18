"""config-ops#6 (second half): what validation an inactive profile bundle gets, and a bool key
outside _BOOL_KEYS. Real ConfigManager + config_schema; temp dir only."""
import io, contextlib, json, os, tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock
from app import WallDanceApp
from core import config_schema
from core.config_store import ConfigStore
from runtime.config_manager import ConfigManager

tmp = tempfile.mkdtemp(prefix="co6_", dir=os.environ["WD_TMP"])
store = ConfigStore(config_dir=os.path.join(tmp, "projects"),
                    last_project_file=os.path.join(tmp, "projects", "last_project.txt"))
applied = []
models = MagicMock(); models.current_model_name = "yolo11x-pose"
models.model_manager.is_using_tensorrt.return_value = True
models.model_manager.engine_exists.return_value = True
models._load_model_with_progress.return_value = True
camera = SimpleNamespace(state=SimpleNamespace(is_open=False, source="0", unavailable=[]), cap=None, close=lambda: None)
app_mock = MagicMock()
def apply(cfg):
    applied.append(dict(cfg))
    WallDanceApp._apply_config_without_model(app_mock, cfg)       # the real applier
mgr = ConfigManager(models=models, cameras=MagicMock(), recording=MagicMock(), recorder=MagicMock(),
                    camera=camera, unified_camera=None, use_unified=False, settings=SimpleNamespace(imgsz=1280),
                    ui=SimpleNamespace(available=False), watchdog=lambda: MagicMock(),
                    apply_config=apply, saveable_config=lambda: {"gamma": 0.8, "confidence": 0.15},
                    request_reprocess=lambda: None)
mgr.config_store = store
bad = {"gamma": 99, "confidence": "abc", "mog2_var_threshold": "x", "ids_exposure_us": 5e7}
cfg = {"config_version": 2, "active_profile": "show", "camera_source": "0", "model": "yolo11x-pose",
       "use_tensorrt": True, "yolo_imgsz": 1280, "roi_enabled": "false",
       "profiles": {"show": dict(bad), "rehearsal": dict(bad)}}
path = store.save("Show", cfg)
out = io.StringIO()
with contextlib.redirect_stdout(out):
    ok = mgr._execute_project_switch_impl(path)
warn = [l for l in out.getvalue().splitlines() if l.startswith("[Config]") and "code defaults" not in l]
print("load ok:", ok)
print("validation warnings on load (active bundle 'show'):"); [print("  ", w) for w in warn]
a = applied[-1]
print("applied active:", {k: a.get(k, '<dropped>') for k in bad}, "| roi_enabled applied as",
      repr(app_mock.settings.roi_enabled), "from the file's", repr(a["roi_enabled"]))
print("stored inactive bundle 'rehearsal':", mgr._profiles["rehearsal"])
out = io.StringIO()
try:
    with contextlib.redirect_stdout(out):
        mgr._cb_profile_switch("rehearsal")
    err = None
except Exception as e:
    err = f"{type(e).__name__}: {e}"
print("profile switch -> raised:", err, "| _active_profile =", mgr._active_profile,
      "| bundle handed to the applier:", applied[-1])
print("settings.confidence left by the partial apply:", repr(app_mock.settings.confidence))
