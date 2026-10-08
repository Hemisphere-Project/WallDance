"""D33 (2026-10-08): the validated D27 detection settings are the code defaults.

yolo_first, confidence 0.15 with the sensitivity dial anchored on it, intermittent
confirm ON, yolo11x-pose @ 1280 on TensorRT -- for a new project (the app's startup
state) and for a key a project file lacks.  A key the project file stores keeps its
value (no silent override of saved projects).  A missing engine is loud.

No GPU, no footage (the replay probe builds a model-less processor in a subprocess:
importing ``replay`` re-execs the interpreter for the CUDA libs).
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

import core.config as config  # noqa: E402
from core import config_schema  # noqa: E402
from core.config_store import ConfigStore  # noqa: E402
from core.ops_monitor import check_tensorrt  # noqa: E402
from core.sensitivity_macro import macro_to_settings  # noqa: E402

D27 = {"model": "yolo11x-pose", "yolo_imgsz": 1280, "confidence": 0.15,
       "sensitivity_conf_seed": 0.15, "sensitivity": 50.0,
       "tracking_mode": "yolo_first", "tracker_intermittent_confirm": True}
OLD = {"model": "yolo11x-pose", "yolo_imgsz": 800, "confidence": 0.25,
       "tracking_mode": "yolo_first", "tracker_intermittent_confirm": False}


# --------------------------------------------------------------------------- #
# The constants (the app's startup state = what "Start blank" + a save stores)
# --------------------------------------------------------------------------- #
def test_code_defaults_are_the_d27_settings():
    assert config.YOLO_MODEL == "yolo11x-pose.pt"
    assert config.YOLO_IMGSZ == 1280
    assert config.YOLO_CONFIDENCE == 0.15
    assert config.TRACKING_MODE == config.TrackingMode.YOLO_FIRST
    assert config.TRACK_WARMUP_INTERMITTENT_ENABLED is True
    assert config.USE_TENSORRT is True
    for key, value in D27.items():
        assert config.DETECTION_DEFAULTS[key] == value, key
    assert config.DETECTION_DEFAULTS["use_tensorrt"] is True
    # the defaults are valid values (no clamp / preset snap on load)
    flat, warnings = config_schema.validate_flat(dict(config.DETECTION_DEFAULTS))
    assert warnings == [] and flat == config.DETECTION_DEFAULTS


def test_a_new_tracker_runs_intermittent_confirm():
    from core.tracker import DancerTracker
    tk = DancerTracker()
    assert tk.intermittent_confirm is True
    assert tk.tracking_mode == config.TrackingMode.YOLO_FIRST


def test_the_sensitivity_dial_is_anchored_on_0_15():
    # dial 50 = the anchor (confidence 0.15); strict end 0.65; the loose end
    # holds at the 0.15 floor (SENS_CONF_MIN) and only lowers varThreshold.
    seed = config.DETECTION_DEFAULTS["sensitivity_conf_seed"]
    assert seed == config.DETECTION_DEFAULTS["confidence"] == 0.15
    assert config.DETECTION_DEFAULTS["sensitivity"] == 50.0
    assert macro_to_settings(50, seed, 16.0) == {"confidence": 0.15, "mog2_var_threshold": 16.0}
    assert macro_to_settings(0, seed, 16.0)["confidence"] == config.SENS_CONF_MAX
    assert macro_to_settings(100, seed, 16.0)["confidence"] == 0.15
    assert macro_to_settings(100, seed, 16.0)["mog2_var_threshold"] == config.SENS_VAR_FLOOR


# --------------------------------------------------------------------------- #
# fill_detection_defaults: absent -> default, stored -> kept
# --------------------------------------------------------------------------- #
def test_an_old_file_without_the_keys_gets_the_d27_values():
    out, filled = config_schema.fill_detection_defaults({"gamma": 0.8, "clahe_clip": 1.0})
    for key, value in D27.items():
        assert out[key] == value, key
    assert out["use_tensorrt"] is True
    assert set(filled) == set(config.DETECTION_DEFAULTS)
    assert out["gamma"] == 0.8 and out["clahe_clip"] == 1.0      # per venue: untouched


def test_a_saved_project_keeps_its_stored_values():
    stored = dict(OLD, sensitivity_conf_seed=0.25, sensitivity=70.0, use_tensorrt=False)
    out, filled = config_schema.fill_detection_defaults(stored)
    assert out == stored and filled == []


def test_a_stored_confidence_anchors_the_dial_itself():
    # confidence stored, no seed / dial (a file before U5): the app anchors the
    # dial on the stored confidence -- the default seed must not sneak in.
    out, filled = config_schema.fill_detection_defaults({"confidence": 0.3})
    assert out["confidence"] == 0.3
    assert "sensitivity_conf_seed" not in out and "sensitivity" not in out
    assert "confidence" not in filled


def test_a_stored_seed_without_confidence_is_kept():
    out, filled = config_schema.fill_detection_defaults({"sensitivity_conf_seed": 0.4})
    assert out["confidence"] == 0.15 and out["sensitivity_conf_seed"] == 0.4
    assert out["sensitivity"] == 50.0
    assert "sensitivity_conf_seed" not in filled


def test_a_value_dropped_by_validation_falls_back_to_the_default():
    flat, warnings = config_schema.validate_flat({"confidence": "junk", "yolo_imgsz": "x"})
    assert warnings and "confidence" not in flat and "yolo_imgsz" not in flat
    out, _ = config_schema.fill_detection_defaults(flat)
    assert out["confidence"] == 0.15 and out["yolo_imgsz"] == 1280


# --------------------------------------------------------------------------- #
# The project load (ConfigManager), with a duck-typed app around it
# --------------------------------------------------------------------------- #
class _Models:
    def __init__(self):
        self._trt_requested = True
        self.current_model_name = "yolo11x-pose"
        self._model_loading = False
        self.loads = []
        self.notices = []
        self.model_manager = SimpleNamespace(
            imgsz=None, using=True,
            set_imgsz=lambda sz: setattr(self.model_manager, "imgsz", sz),
            is_using_tensorrt=lambda: self.model_manager.using,
            engine_exists=lambda name: True)

    def _load_model_with_progress(self, name, force_pt=False):
        self.loads.append((name, self.model_manager.imgsz, force_pt))
        return True

    def engine_missing_notice(self, base):
        self.notices.append(base)

    def _update_trt_banner(self):
        pass


def _manager(tmp_path, previous_imgsz=800, previous_conf=0.25):
    from runtime.config_manager import ConfigManager
    applied = []
    store = ConfigStore(config_dir=str(tmp_path / "projects"),
                        last_project_file=str(tmp_path / "projects" / "last_project.txt"))
    camera = SimpleNamespace(state=SimpleNamespace(is_open=False, source="0", unavailable=[]),
                             cap=None, close=lambda: None)
    mgr = ConfigManager(
        models=_Models(),
        cameras=SimpleNamespace(_set_camera_frame_callback=lambda cb: None,
                                _open_camera=lambda s: None,
                                _attempt_camera_connect=lambda s: False),
        recording=SimpleNamespace(_update_recording_ui=lambda: None),
        recorder=SimpleNamespace(stop_recording=lambda: None, stop_playback=lambda: None,
                                 set_project=lambda p: None),
        camera=camera, unified_camera=None, use_unified=False,
        settings=SimpleNamespace(imgsz=previous_imgsz, confidence=previous_conf),
        ui=SimpleNamespace(available=False),
        watchdog=lambda: SimpleNamespace(push_busy=lambda n: None, pop_busy=lambda: None),
        apply_config=applied.append,
        saveable_config=lambda: {},
        request_reprocess=lambda: None)
    mgr.config_store = store
    return mgr, store, applied


def _write(store, project, cfg):
    path = store.save(project, cfg)
    return path


def test_loading_a_file_without_the_keys_runs_the_d27_values(tmp_path, capsys):
    pytest.importorskip("torch")          # runtime.config_manager -> camera stack
    mgr, store, applied = _manager(tmp_path)
    path = _write(store, "old", {"camera_source": "0", "gamma": 0.8, "clahe_clip": 1.0,
                                 "mog2_var_threshold": 8.0})
    assert mgr._execute_project_switch_impl(path)
    cfg = applied[-1]
    for key, value in D27.items():
        assert cfg[key] == value, key
    assert cfg["gamma"] == 0.8 and cfg["mog2_var_threshold"] == 8.0
    # the model loads at 1280 on TensorRT (not the previous project's 800)
    assert mgr.settings.imgsz == 1280
    assert mgr.models.loads == [("yolo11x-pose", 1280, False)]
    assert mgr.models._trt_requested is True
    assert "code defaults (D33)" in capsys.readouterr().out


def test_loading_a_saved_project_keeps_its_values(tmp_path):
    pytest.importorskip("torch")
    mgr, store, applied = _manager(tmp_path, previous_imgsz=1280, previous_conf=0.15)
    stored = dict(OLD, camera_source="0", use_tensorrt=True, sensitivity_conf_seed=0.25,
                  sensitivity=50.0)
    path = _write(store, "default", config_schema.structure(stored, {}, "show"))
    assert mgr._execute_project_switch_impl(path)
    cfg = applied[-1]
    for key, value in OLD.items():
        assert cfg[key] == value, key
    assert cfg["sensitivity_conf_seed"] == 0.25
    assert mgr.settings.imgsz == 800
    assert mgr.models.loads == [("yolo11x-pose", 800, False)]


def test_a_missing_engine_is_loud_and_a_declined_build_keeps_the_intent(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    import core.model_manager as model_manager
    mgr, store, applied = _manager(tmp_path)
    mgr.models.model_manager.engine_exists = lambda name: False
    mgr.models._prompt_trt_build_sync = lambda name: False          # operator: "no"
    declined = []
    mgr.models.keep_trt_intent_after_decline = declined.append
    monkeypatch.setattr(model_manager, "is_tensorrt_available", lambda: True)
    path = _write(store, "old", {"camera_source": "0"})
    assert mgr._execute_project_switch_impl(path)
    assert mgr.models.notices == ["yolo11x-pose"]                    # the loud log
    assert declined == ["yolo11x-pose"]                              # intent kept
    assert mgr.models.loads == [("yolo11x-pose", 1280, True)]        # PyTorch for now


# --------------------------------------------------------------------------- #
# ModelController: startup defaults, the loud notice, the kept intent
# --------------------------------------------------------------------------- #
def _controller(tmp_path):
    pytest.importorskip("ultralytics")
    from runtime.model_controller import ModelController
    return ModelController(models_dir=str(tmp_path), ui=SimpleNamespace(available=False),
                           camera=None, processor=lambda: None, watchdog=lambda: None,
                           restore_playback_dims=lambda: None, update_topbar=lambda: None)


def test_model_controller_starts_on_x_1280_trt(tmp_path):
    mc = _controller(tmp_path)
    assert mc.current_model_name == "yolo11x-pose"
    assert mc.model_manager.imgsz == 1280
    assert mc._trt_requested is True


def test_missing_engine_notice_and_declined_build(tmp_path, capsys):
    mc = _controller(tmp_path)
    path = mc.engine_missing_notice("yolo11x-pose")
    assert path.endswith("yolo11x-pose_1280.engine")
    out = capsys.readouterr().out
    assert "TensorRT engine MISSING" in out and "build_engines" in out
    mc._trt_requested = False
    mc.keep_trt_intent_after_decline("yolo11x-pose")
    assert mc._trt_requested is True
    assert "yolo11x-pose_1280.engine not built" in mc.model_manager.get_tensorrt_fallback_reason()


# --------------------------------------------------------------------------- #
# Readiness: a missing engine is never "ok"
# --------------------------------------------------------------------------- #
def test_readiness_flags_a_missing_default_engine():
    name = "yolo11x-pose_1280.engine"
    req = check_tensorrt(trt_requested=True, trt_active=False, engine_present=False,
                         engine_name=name)
    assert req.status == "fail" and name in req.detail and "MISSING" in req.detail
    unreq = check_tensorrt(trt_requested=False, trt_active=False, engine_present=False,
                           engine_name=name)
    assert unreq.status == "warn" and name in unreq.detail and "MISSING" in unreq.detail
    chosen = check_tensorrt(trt_requested=False, trt_active=False, engine_present=True,
                            engine_name=name)
    assert chosen.status == "warn" and "tick TensorRT" in chosen.detail
    ok = check_tensorrt(trt_requested=True, trt_active=True, engine_present=True,
                        engine_name=name)
    assert ok.status == "ok" and name in ok.detail


# --------------------------------------------------------------------------- #
# Replay: a config without the keys replays with the D27 values
# --------------------------------------------------------------------------- #
_PROBE = """
import json, sys
sys.path.insert(0, sys.argv[1])
import replay
cfg, filled = replay.fill_detection_defaults({"gamma": 0.8})
p = replay._build_processor(cfg, cfg["model"], cfg["yolo_imgsz"], load_model=False)
kept, _ = replay.fill_detection_defaults({"confidence": 0.25, "yolo_imgsz": 800,
                                          "tracker_intermittent_confirm": False})
q = replay._build_processor(kept, "yolo11x-pose", kept["yolo_imgsz"], load_model=False)
bare = replay._build_processor({}, "yolo11x-pose", 1280, load_model=False)
print("PROBE" + json.dumps({
    "filled": sorted(filled), "model": cfg["model"], "imgsz": p.settings.imgsz,
    "conf": p.settings.confidence, "mode": p.tracker.tracking_mode.value,
    "intermittent": p.tracker.intermittent_confirm,
    "kept": [q.settings.confidence, q.settings.imgsz, q.tracker.intermittent_confirm],
    "bare": [bare.settings.confidence, bare.tracker.intermittent_confirm,
             bare.tracker.tracking_mode.value]}))
"""


@pytest.mark.skipif(importlib.util.find_spec("torch") is None,
                    reason="replay._build_processor needs torch")
def test_replay_of_a_config_without_the_keys_runs_d27(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(_PROBE)
    proc = subprocess.run([sys.executable, str(script), str(HERE)],
                          capture_output=True, text=True, timeout=300,
                          env=dict(os.environ))
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE")]
    assert line, proc.stdout[-2000:] + proc.stderr[-2000:]
    out = json.loads(line[0][5:])
    assert out["model"] == "yolo11x-pose" and out["imgsz"] == 1280
    assert out["conf"] == 0.15 and out["mode"] == "yolo_first"
    assert out["intermittent"] is True
    assert set(out["filled"]) == set(config.DETECTION_DEFAULTS)
    assert out["kept"] == [0.25, 800, False]                 # stored values win
    assert out["bare"] == [0.15, True, "yolo_first"]          # _build_processor alone too


def test_the_corpus_manifests_pin_intermittent_off():
    # Their snapshots predate the key (absent meant OFF): pinned so the corpus
    # and its goldens do not move with the D33 default.
    for path in sorted((HERE / "scenarios").glob("*.json")):
        cfg = json.loads(path.read_text())["config"]
        assert cfg.get("tracker_intermittent_confirm") is False, path.name
