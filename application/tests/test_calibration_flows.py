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
    flows._calib2_aggregate = lambda chosen, roi_long: prop
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
