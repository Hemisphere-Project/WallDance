"""runtime-core#5 (second half): a model load that fails inside a project switch."""
import io, contextlib, os, tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock
from core import config_schema
from core.config_store import ConfigStore
from runtime.config_manager import ConfigManager

tmp = tempfile.mkdtemp(prefix="rc5b_", dir=os.environ["WD_TMP"])
store = ConfigStore(config_dir=os.path.join(tmp, "projects"),
                    last_project_file=os.path.join(tmp, "projects", "last_project.txt"))
toasts, applied = [], []
models = SimpleNamespace(_trt_requested=False, current_model_name="yolo11x-pose", _model_loading=False,
    model_manager=SimpleNamespace(imgsz=1280, set_imgsz=lambda sz: setattr(models.model_manager, "imgsz", sz),
                                  is_using_tensorrt=lambda: False, engine_exists=lambda n: True),
    _load_model_with_progress=lambda name, force_pt=False: False)        # both TRT and the .pt fallback failed
camera = SimpleNamespace(state=SimpleNamespace(is_open=True, source="ids", unavailable=[]), cap=None, close=lambda: None)
settings = SimpleNamespace(imgsz=1280)
live_A = {"camera_source": "ids", "model": "yolo11x-pose", "use_tensorrt": False, "yolo_imgsz": 1280,
          "gamma": 0.8, "confidence": 0.15}
mgr = ConfigManager(models=models, cameras=SimpleNamespace(_set_camera_frame_callback=lambda cb: None,
                        _open_camera=lambda s: setattr(camera.state, "is_open", True)),
                    recording=MagicMock(), recorder=MagicMock(), camera=camera, unified_camera=None,
                    use_unified=False, settings=settings,
                    ui=SimpleNamespace(available=True, show_toast=lambda *a, **k: toasts.append(a),
                                       update_project_list=lambda *a: None, update_config_list=lambda *a: None,
                                       set_current_config=lambda *a: None, show_save_indicator=lambda *a: None),
                    watchdog=lambda: MagicMock(), apply_config=applied.append,
                    saveable_config=lambda: dict(live_A), request_reprocess=lambda: None)
mgr.config_store = store
mgr._current_project = "ShowA"; mgr._profiles = {"show": {"gamma": 0.8, "confidence": 0.15},
                                                 "rehearsal": {"gamma": 1.0, "confidence": 0.25}}
pathB = store.save("ShowB", {"config_version": 2, "active_profile": "rehearsal", "camera_source": "ids",
                             "model": "yolo11l-pose", "use_tensorrt": True, "yolo_imgsz": 960,
                             "profiles": {"show": {"gamma": 0.5, "confidence": 0.35},
                                          "rehearsal": {"gamma": 2.0, "confidence": 0.45}}})
with contextlib.redirect_stdout(io.StringIO()):
    ok = mgr._execute_project_switch_impl(pathB)
print("switch ok:", ok, "| toasts:", toasts, "| apply_config calls:", len(applied))
print("  _current_project      =", mgr._current_project, "(old)")
print("  current_config_path   =", os.path.basename(mgr.current_config_path), "(new)")
print("  _active_profile       =", mgr._active_profile, "(new)   _profiles =", mgr._profiles, "(new)")
print("  settings.imgsz        =", settings.imgsz, "(new)   models._trt_requested =", models._trt_requested, "(new)")
print("  _model_loading        =", models._model_loading, "  camera.state.is_open =", camera.state.is_open)
import time; time.sleep(1.1)
with contextlib.redirect_stdout(io.StringIO()):
    mgr._cb_save_config()                                       # the operator presses Save
saved = store.load(store.latest_for_project("ShowA"))
print("Save ->", os.path.basename(store.latest_for_project("ShowA")), "active_profile:", saved["active_profile"],
      "profiles:", saved["profiles"])
