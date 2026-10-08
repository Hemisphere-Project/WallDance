"""Track C regression tests for the calibration-flow apply paths.

`calibration_flows.py` is dependency-injected and was previously only exercised
via the GUI smoke test; these lock the Track C correctness fixes:
  - Calib1 (Aim) no longer writes `person_height_px` (Calib2 is the sole writer).
  - Calib1 stamps scene-knob provenance under source "aim".
  - Calib2 reuses a fresh Aim noise σ for the dark net-height target.
"""
import sys, os
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.calibration import CalibrationResult            # noqa: E402
from runtime.calibration_flows import CalibrationFlows     # noqa: E402


def _flows():
    settings = MagicMock()
    settings.person_height_px = 150
    settings.imgsz = 960
    enhancer = MagicMock()
    enhancer.gamma = 1.0
    flows = CalibrationFlows(
        processor=MagicMock(), enhancer=enhancer, tracker=MagicMock(),
        settings=settings, recorder=MagicMock(), camera=MagicMock(),
        unified_camera=MagicMock(), use_unified=False, models=MagicMock(),
        cameras=MagicMock(), configs=MagicMock(),
        ui=MagicMock(available=False),
        last_raw_frame=lambda: None,
        roi_source_size=lambda: (1280, 720),
        get_effective_roi=lambda w, h: (0, 0, 1280, 720),
        reset_sensitivity_anchor=lambda **k: None,
        sync_mask_ui=lambda: None,
        request_reprocess=lambda: None,
        imgsz_change=lambda v: None,
    )
    return flows, settings


def _scene_result():
    res = CalibrationResult()
    res.height_ok = True
    res.person_height_px = 999          # Calib1 measures it...
    res.min_ratio = 0.5
    res.max_ratio = 1.5
    res.var_ok = True
    res.var_threshold = 16.0
    res.mog2_scale = 0.7
    res.clahe_value = None
    res.noise_sigma = 2.0
    return res


def test_calib1_does_not_write_person_height():
    """Aim is height diagnostic-only; person_height_px must be untouched."""
    flows, settings = _flows()
    flows._apply_calibration(_scene_result())
    assert settings.person_height_px == 150          # ...but never writes it


def test_calib1_stamps_aim_provenance():
    flows, _ = _flows()
    flows._apply_calibration(_scene_result())
    assert flows.calibration_state["gamma"]["source"] == "aim"
    assert flows.calibration_state["mog2_var_threshold"]["source"] == "aim"
    # height is Calib2-owned → never stamped by Aim
    assert "person_height_px" not in flows.calibration_state


def test_calib1_retains_noise_sigma_for_calib2():
    flows, _ = _flows()
    flows._apply_calibration(_scene_result())
    assert flows._last_calib1_noise_sigma == 2.0
    assert flows._last_calib1_noise_ts > 0


def _calib2_flows_with(prop):
    """A flows wired for _cb_calib2_apply with a stubbed pool + aggregate."""
    flows, settings = _flows()
    flows.ui = MagicMock(available=True)
    flows.imgsz_change = MagicMock()
    flows._calib2_pool = lambda: MagicMock(load_runs=lambda: [("p1", object())])
    flows._calib2_aggregate = lambda chosen, roi_long, with_probe=False: prop
    return flows, settings


def _ok_proposal(imgsz=1280):
    prop = MagicMock()
    prop.ok = True
    prop.person_height_px = 200
    prop.min_ratio, prop.max_ratio = 0.5, 1.5
    prop.confidence = 0.3
    prop.blur_budget_ms = None
    prop.imgsz = imgsz
    prop.summary.return_value = "height 200px"
    return prop


def test_calib2_quiet_apply_previews_without_modal_or_reload():
    """Checkbox-toggle preview: applies live + refreshes text, but no modal and
    no imgsz/engine reload (settings.imgsz starts 960, proposal 1280)."""
    flows, settings = _calib2_flows_with(_ok_proposal(imgsz=1280))
    flows._cb_calib2_apply(["p1"], quiet=True)
    assert settings.person_height_px == 200            # cheap knobs applied live
    flows.ui.update_calib2_proposal.assert_called_once()
    flows.ui.show_calibration_result_dialog.assert_not_called()
    flows.imgsz_change.assert_not_called()             # heavy reload deferred
    assert "imgsz" not in flows.calibration_state      # imgsz not committed on toggle


def test_calib2_explicit_apply_commits_imgsz_and_modal():
    flows, settings = _calib2_flows_with(_ok_proposal(imgsz=1280))
    flows._cb_calib2_apply(["p1"], quiet=False)
    flows.imgsz_change.assert_called_once_with(1280)
    flows.ui.show_calibration_result_dialog.assert_called_once()
    flows.ui.update_calib2_proposal.assert_not_called()


# --- Track S: provenance line + config persistence ---------------------------

def test_aim_calib_line_empty():
    flows, _ = _flows()
    assert flows._aim_calib_line() == "Last calibrated: --"


def test_aim_calib_line_groups_by_phase():
    import time as _t
    flows, _ = _flows()
    now = _t.time()
    flows.calibration_state = {
        "gamma": {"source": "aim", "ts": "x", "epoch": now},
        "mog2_var_threshold": {"source": "aim", "ts": "x", "epoch": now},
        "person_height_px": {"source": "dancers", "ts": "x", "epoch": now},
    }
    line = flows._aim_calib_line()
    assert line.startswith("Last calibrated · ")
    assert "Aim:" in line and "Dancers:" in line
    assert "gamma" in line and "var" in line and "height" in line
    assert "just now" in line


def test_calibration_state_survives_config_roundtrip():
    """The provenance dict is a shared key — it must round-trip the schema
    (structure -> flatten -> validate) without being dropped or mangled."""
    from core import config_schema
    state = {"gamma": {"source": "aim", "ts": "t", "epoch": 123.0}}
    flat = {"confidence": 0.3, "gamma": 1.5, "calibration_state": state}
    structured = config_schema.structure(flat, {}, config_schema.DEFAULT_PROFILE)
    back = config_schema.flatten(structured)
    validated, _warnings = config_schema.validate_flat(back)
    assert validated.get("calibration_state") == state


# --- K3: imgsz dark-probe builder guards (headless) ---------------------------
def test_make_imgsz_probe_none_when_trt():
    flows, _ = _flows()
    flows.models.model_manager.is_using_tensorrt.return_value = True
    assert flows._make_imgsz_probe([]) is None       # TRT engine = fixed imgsz


def test_make_imgsz_probe_none_when_no_model():
    flows, _ = _flows()
    flows.models.model_manager.is_using_tensorrt.return_value = False
    flows.processor.model = None
    assert flows._make_imgsz_probe([]) is None


# --- D29: the empty-wall YOLO check + the take's exposure/gain -----------------
def _wall_flows(gamma=1.8, clahe=1.5, conf=0.25):
    flows, settings = _flows()
    flows.enhancer.gamma, flows.enhancer.clahe_clip = gamma, clahe
    settings.confidence = conf
    flows.processor.last_raw_dets = []
    return flows, settings


def _run_check(flows, ghost_conf_at):
    for _ in range(600):
        if flows._wall_check is None:
            break
        c = ghost_conf_at(flows.enhancer.gamma, flows.enhancer.clahe_clip)
        flows.processor.last_raw_dets = [(c, 300.0, 400.0, 150.0)] if c > 0 else []
        flows._step_calibration([], 30.0)


def test_calibrate_ends_with_the_empty_wall_check():
    flows, settings = _wall_flows()
    flows._apply_calibration(_scene_result())
    assert flows._wall_check is not None and flows._calibrating          # YOLO stays forced on
    assert settings.confidence == 0.2                                      # near misses count
    _run_check(flows, lambda g, c: 0.3 if g > 1.3 else 0.0)               # a ghost under strong gamma
    assert flows._wall_check is None and not flows._calibrating
    assert settings.confidence == 0.25                                     # live confidence back
    assert 1.0 < flows.enhancer.gamma < 1.3 and flows._wall_result.clean
    assert flows.calibration_state["clahe"]["source"] == "aim"


def test_a_clean_empty_wall_keeps_the_scene_pick_and_the_check_can_be_disabled():
    flows, settings = _wall_flows()
    flows._apply_calibration(_scene_result())
    _run_check(flows, lambda g, c: 0.0)
    assert flows.enhancer.gamma == 1.8 and flows._wall_result.clean
    flows2, _ = _wall_flows()
    flows2.wall_check_enabled = False
    flows2._apply_calibration(_scene_result())
    assert flows2._wall_check is None and not flows2._calibrating


def test_cancel_during_the_check_restores_the_confidence():
    flows, settings = _wall_flows()
    flows.models._model_loaded = True
    flows._apply_calibration(_scene_result())
    flows.processor.last_raw_dets = [(0.3, 1.0, 1.0, 100.0)] * 1
    for _ in range(10):
        flows._step_calibration([], 30.0)
    flows._cb_calibrate()                                                  # second press = cancel
    assert flows._wall_check is None and not flows._calibrating
    assert settings.confidence == 0.25 and flows.enhancer.gamma == 1.8


def test_playback_calibration_takes_the_exposure_and_gain_of_the_take():
    flows, _ = _wall_flows()
    flows.models._model_loaded = True
    flows.recorder.is_playing = True
    flows.recorder.playback_camera = {"exposure_us": 25000.0, "gain_db": 36.0}
    flows.unified_camera = None
    flows._cb_calibrate()
    flows.cameras._cb_ids_exposure_change.assert_called_once_with(25000.0)
    flows.cameras._cb_ids_gain_change.assert_called_once_with(36.0)
    assert flows.calibration_state["ids_exposure_us"]["source"] == "aim"
    assert "from the take" in flows._take_camera_line


def test_calibrate_keeps_the_projects_calibrated_enhancement_when_it_passes():
    flows, settings = _wall_flows(gamma=1.8, clahe=1.5)             # the scene step's pick
    flows.calibration_state = {"gamma": {"source": "aim"}}
    flows._pre_enhancement = (0.73, 2.5)                            # recorded by _cb_calibrate
    flows._apply_calibration(_scene_result())
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (0.73, 2.5)   # tried first
    _run_check(flows, lambda g, c: 0.3 if g > 1.3 else 0.0)
    assert flows._wall_result.kept_current and flows.enhancer.gamma == 0.73
    assert flows.enhancer.clahe_clip == 2.5 and settings.confidence == 0.25


def _servo_flows(roi_enabled):
    """A flows with the live exposure servo running on a hangar-like frame: a dark wall band
    (ROI rows 80-160, luma 10) under daylight windows (rows 0-60 clipped = 30 % of the frame)."""
    import numpy as np
    from core.calibration import ExposureServo
    flows, settings = _flows()
    settings.roi_enabled = roi_enabled
    frame = np.full((200, 320), 10, np.uint8)
    frame[:60, :] = 255
    flows.last_raw_frame = lambda: frame
    flows.get_effective_roi = lambda w, h: (0, 80, 320, 80)
    flows._servo = ExposureServo(exposure_us=200.0, gain_db=0.5)
    flows._calibrating = True
    for _ in range(7):                                  # settle, then one command
        flows._step_calibration([], 30.0)
    return flows


def test_live_servo_exposes_for_the_roi_not_the_clipped_windows():
    flows = _servo_flows(roi_enabled=True)
    flows.cameras._cb_ids_exposure_change.assert_called_once()
    assert flows.cameras._cb_ids_exposure_change.call_args[0][0] > 200.0   # raised, wall is dark
    flows.cameras._cb_ids_gain_change.assert_not_called()
    assert flows._servo._region == "ROI 320x80" and flows._servo._brightness == 10.0


def test_live_servo_without_roi_drives_the_frame_median():
    flows = _servo_flows(roi_enabled=False)
    assert flows._servo._region == "frame 320x200" and flows._servo._brightness == 10.0
    assert flows._servo._clip_pct == 30.0
    assert flows.cameras._cb_ids_exposure_change.call_args[0][0] > 200.0   # 30 % clipped, still up


def test_calibrate_records_the_enhancement_to_keep_only_for_a_calibrated_project():
    flows, _ = _wall_flows(gamma=0.73, clahe=2.5)
    flows.models._model_loaded = True
    flows.recorder.is_playing = True
    flows.recorder.playback_camera = None
    flows.unified_camera = None
    flows._cb_calibrate()                                           # no provenance: nothing to keep
    assert flows._pre_enhancement is None
    flows2, _ = _wall_flows(gamma=0.73, clahe=2.5)
    flows2.models._model_loaded = True
    flows2.recorder.is_playing = True
    flows2.recorder.playback_camera = None
    flows2.unified_camera = None
    flows2.calibration_state = {"gamma": {"source": "aim"}}
    flows2._cb_calibrate()
    assert flows2._pre_enhancement == (0.73, 2.5)
