"""runtime-core#12: which camera object do shutdown and the model-load 'close' touch?"""
import io, contextlib, os, tempfile
from pathlib import Path
from types import SimpleNamespace
import runtime.main_loop as ml
from app import _ModelCameraAdapter

calls = []
rec = lambda name: (lambda *a, **k: calls.append(name))
def fake_app():
    return SimpleNamespace(
        last_fps_time=0.0,
        api=SimpleNamespace(drain=rec("api.drain")),
        tracker=SimpleNamespace(logger=SimpleNamespace(close=rec("tracker.logger.close"))),
        _watchdog=SimpleNamespace(stop=rec("watchdog.stop")),
        _web_monitor=None, _remote=None,
        recorder=SimpleNamespace(close=rec("recorder.close")),
        camera=SimpleNamespace(cap=SimpleNamespace(release=rec("legacy camera.cap.release")),
                               close=rec("legacy CameraManager.close"),
                               state=SimpleNamespace(is_open=True, source="ids")),
        unified_camera=SimpleNamespace(close=rec("UnifiedCamera.close"),
                                       stop_acquisition=rec("UnifiedCamera.stop_acquisition"), is_open=True),
        _use_unified_camera=True,
        ui=SimpleNamespace(destroy=rec("ui.destroy")))

app = fake_app()
loop = ml.MainLoop(app, logs_dir=Path(tempfile.mkdtemp(dir=os.environ["WD_TMP"])))
with contextlib.redirect_stdout(io.StringIO()):
    loop._shutdown()
print("MainLoop._shutdown() calls:", calls)
print("  UnifiedCamera touched:", any(c.startswith("UnifiedCamera") for c in calls))

calls.clear(); app = fake_app()
_ModelCameraAdapter(app).close()        # what ModelController does at model_controller.py:480
print("_ModelCameraAdapter.close() calls:", calls, "| legacy flag is_open =", app.camera.state.is_open,
      "| UnifiedCamera.is_open =", app.unified_camera.is_open)
