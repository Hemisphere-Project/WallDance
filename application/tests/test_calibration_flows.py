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
