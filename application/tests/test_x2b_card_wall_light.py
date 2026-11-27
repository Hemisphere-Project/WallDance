"""X2 follow-up (#t-054, 27/11): the Scene Calibration card always carries its « Wall light » line.

Through the real Calibrate flow (runtime/calibration_flows.py, from CALIBRATE pressed to the card), the real
FrameProcessor's plate capture, the app's own capture callback (WallDanceApp._cb_capture_plate) and the app's
card publication (_CalibrationUiAdapter), on a played take and on the camera path (a fake IDS camera):

- the last card a Calibrate publishes carries exactly one « Wall light: » line, the verdict the readiness row
  (core/ops_monitor.check_wall_light) gives for the same plate;
- a card built while the empty-wall picture is still being taken says « being measured », and a later card
  carries the verdict;
- a Calibrate that ends without a plate says « no verdict » with what to do;
- never a card without the line.
"""
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

H, W = 360, 480                       # a small landscape picture (rows, columns); nothing assumes 08/10's
ROI = (40, 30, 400, 300)              # the zone (x, y, w, h) in its pixels
MEASURING = "Wall light: being measured - it will show here in a few seconds"


def _frame(kind: str) -> np.ndarray:
    """``flat``: an even wall at 70 DN (green); ``hot``: one hot oval on the left of the zone (red)."""
    if kind == "flat":
        return np.full((H, W), 70, np.uint8)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    x0, y0, w, h = ROI
    u, v = (xx - x0) / w, (yy - y0) / h
    img = 22.0 + 200.0 * np.exp(-(((u - 0.15) / 0.12) ** 2 + ((v - 0.5) / 0.25) ** 2))
    return np.clip(img, 0, 255).astype(np.uint8)


class _Enhancer:
    def __init__(self, gamma=1.2, clahe=2.5):
        self.gamma, self.clahe_clip = float(gamma), float(clahe)

    def _update_gamma_lut(self):
        pass

    def _update_clahe(self):
        pass


class _Cameras:
    """The camera controller Calibrate commands (the IDS sliders' callbacks)."""

    def __init__(self, exposure_us=20000.0, gain_db=10.0):
        self.ids_exposure_us, self.ids_gain_db = float(exposure_us), float(gain_db)
        self.ids_exposure_auto = self.ids_gain_auto = False

    def _cb_ids_exposure_change(self, v):
        self.ids_exposure_us = float(v)

    def _cb_ids_gain_change(self, v):
        self.ids_gain_db = float(v)

    def _cb_ids_exposure_auto_toggle(self, on):
        self.ids_exposure_auto = bool(on)

    def _cb_ids_gain_auto_toggle(self, on):
        self.ids_gain_auto = bool(on)

    def exposure_gain_now(self):
        return self.ids_exposure_us, self.ids_gain_db


def _rig(tmp_path, *, playing: bool, kind: str, wall_check: bool = True, capture_fails_to_start=False):
    """The app's Calibrate as it runs: a real FrameProcessor, the real CalibrationFlows wired as app.py wires
    it, the app's own plate capture and card adapter on a stub app (its project folder under ``tmp_path``)."""
    pytest.importorskip("torch")                      # app and the pipeline
    import app as app_mod
    from core.config import PLATE_CAPTURE_FRAMES
    from core.pipeline import FrameProcessor, ProcessingSettings
    from runtime import api
    from runtime.calibration_flows import CalibrationFlows, CameraSource

    proc = FrameProcessor(model=None, settings=ProcessingSettings(
        confidence=0.15, imgsz=640, use_fp16=False, enhance_enabled=False, enhance_lite=False,
        enhance_force=False, person_height_px=110, use_gpu_path=False))
    proc.settings.fg_enabled = True
    settings = SimpleNamespace(confidence=0.15, imgsz=1280, person_height_px=110, yolo_shape=None,
                               roi_enabled=True, roi_x=ROI[0], roi_y=ROI[1], roi_w=ROI[2], roi_h=ROI[3])
    bus = api.EventBus()
    bus.ui_ready = True
    events = []
    bus.subscribe(events.append)
    frame = _frame(kind)
    app = SimpleNamespace(processor=proc, settings=settings, bus=bus, cameras=_Cameras(),
                          configs=SimpleNamespace(_current_project="proj"))
    app._plate_dir = lambda: str(tmp_path / "proj" / "plates")
    app._publish_plate_status = lambda: None
    app._cb_capture_plate = types.MethodType(app_mod.WallDanceApp._cb_capture_plate, app)

    def capture_plate(source):                        # app.py: lambda source: self._cb_capture_plate(N, source)
        if capture_fails_to_start:
            raise RuntimeError("no frames")
        app._cb_capture_plate(PLATE_CAPTURE_FRAMES, source)

    camera_path = not playing
    flows = CalibrationFlows(
        processor=proc, enhancer=_Enhancer(), tracker=MagicMock(), settings=settings,
        recorder=SimpleNamespace(is_playing=playing,
                                 playback_camera={"exposure_us": 25000.0, "gain_db": 30.0} if playing else None),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=camera_path)),
        unified_camera=(SimpleNamespace(is_open=True, source_type=CameraSource.IDS_PEAK,
                                        get_gain_range_db=lambda: (0.0, 30.0)) if camera_path else None),
        use_unified=camera_path, models=SimpleNamespace(_model_loaded=True, model=object(), current_model_name="m"),
        cameras=app.cameras, configs=MagicMock(), ui=app_mod._CalibrationUiAdapter(app),
        last_raw_frame=lambda: frame, roi_source_size=lambda: (W, H),
        get_effective_roi=lambda w, h: ROI, reset_sensitivity_anchor=lambda **k: None,
        sync_mask_ui=lambda: None, request_reprocess=lambda: None, imgsz_change=lambda v: None,
        capture_plate=capture_plate)
    flows.wall_check_enabled = wall_check
    app.calibration = flows
    return SimpleNamespace(app=app, flows=flows, proc=proc, frame=frame, events=events, api=api,
                           camera_path=camera_path)


def _frames(r, n=1):
    """``n`` frames of the main loop: the processor feeds its plate capture, then Calibrate steps."""
    for _ in range(n):
        r.proc._feed_plate_capture(r.frame)
        r.flows._step_calibration([], 30.0)


def _calibrate(r, frames=800):
    """CALIBRATE pressed, then frames until Calibrate has ended (its card, or a stop)."""
    r.flows._cb_calibrate()
    for _ in range(frames):
        if not r.flows._calibrating and r.flows._scene_pending is None and r.flows._emptiness is None:
            break
        _frames(r)
    assert not r.flows._calibrating


def _cards(r):
    return [e.summary for e in r.events if isinstance(e, r.api.CalibReportCard)]


def _light_lines(summary):
    return [ln for ln in summary.split("\n") if ln.startswith("Wall light:")]


def _row_line(r):
    """The readiness row's verdict for the plate in use (app._readiness_report's call), as the card writes it."""
    from core.light_verdict import LightVerdict
    from core.ops_monitor import check_wall_light
    s = r.app.settings
    row = check_wall_light(plate=r.proc._fg_detector.plate, roi=(s.roi_x, s.roi_y, s.roi_w, s.roi_h),
                           plate_status=str(r.proc.plate_status))
    return LightVerdict(row.status, row.detail).line()


def _every_card_has_one_line(r):
    cards = _cards(r)
    assert cards, "Calibrate published no card"
    for c in cards:
        assert len(_light_lines(c)) == 1, f"a card without exactly one Wall light line:\n{c}"
        assert c.split("\n")[-1].startswith("Wall light:"), "the Wall light line is the card's last line"
    return cards


# ------------------------------------------------------------------ the card ends with the verdict

@pytest.mark.parametrize("playing,kind,word", [(True, "hot", "RED"), (True, "flat", "ok"),
                                                (False, "flat", "ok"), (False, "hot", "RED")],
                         ids=["take-hot", "take-flat", "camera-flat", "camera-hot"])
def test_the_last_card_of_a_calibrate_carries_the_rows_verdict_for_its_plate(tmp_path, playing, kind, word):
    r = _rig(tmp_path, playing=playing, kind=kind)
    _calibrate(r)
    assert r.flows._servo_result is not None if r.camera_path else r.flows._take_camera_line
    assert r.proc.plate_status == "ready" and r.flows._pending_plate is not None   # this Calibrate's plate
    cards = _every_card_has_one_line(r)
    (line,) = _light_lines(cards[-1])
    assert line == _row_line(r), (line, _row_line(r))
    assert line.startswith(f"Wall light: {word} - ")
    assert not (tmp_path / "proj").exists()          # A6 ②: nothing written before Save


# ------------------------------------------------------------------ the card built before the plate

@pytest.mark.parametrize("playing", [True, False], ids=["take", "camera"])
def test_a_card_built_while_the_plate_is_taken_says_so_then_carries_the_verdict(tmp_path, playing):
    r = _rig(tmp_path, playing=playing, kind="hot", wall_check=False)
    _calibrate(r)
    assert r.proc.plate_status == "capturing"         # the card came first: the race
    first = _every_card_has_one_line(r)
    assert len(first) == 1 and _light_lines(first[0]) == [MEASURING]
    _frames(r, 60)                                    # the picture ends while the card is on screen
    assert r.proc.plate_status == "ready"
    cards = _every_card_has_one_line(r)
    assert len(cards) == 2
    assert cards[1].split("\n")[:-1] == first[0].split("\n")[:-1]   # the same card, its last line updated
    (line,) = _light_lines(cards[1])
    assert line == _row_line(r) and line.startswith("Wall light: RED - ")
    _frames(r, 60)
    assert len(_cards(r)) == 2                        # once


def test_a_save_while_the_plate_is_taken_still_updates_the_card(tmp_path):
    r = _rig(tmp_path, playing=True, kind="flat", wall_check=False)
    _calibrate(r)
    assert r.flows.commit_pending_plate() is True     # « Save to project » before the picture ended
    _frames(r, 60)
    cards = _every_card_has_one_line(r)
    assert len(cards) == 2 and _light_lines(cards[1]) == [_row_line(r)]
    assert (tmp_path / "proj" / "plates" / "latest.npz").is_file()


# ------------------------------------------------------------------ no plate, no verdict: says so

def test_a_calibrate_whose_plate_capture_does_not_start_says_no_verdict(tmp_path):
    r = _rig(tmp_path, playing=True, kind="hot", capture_fails_to_start=True)
    _calibrate(r)
    cards = _every_card_has_one_line(r)
    (line,) = _light_lines(cards[-1])
    assert line.startswith("Wall light: no verdict - ") and line.endswith(" - Calibrate on the empty wall")


def test_a_plate_capture_that_fails_after_the_card_says_no_verdict(tmp_path):
    r = _rig(tmp_path, playing=False, kind="flat", wall_check=False)
    _calibrate(r)
    assert _light_lines(_cards(r)[0]) == [MEASURING]

    def boom(_frame):
        raise RuntimeError("camera gone")
    r.proc._plate_capture.add = boom
    _frames(r, 2)
    cards = _every_card_has_one_line(r)
    assert len(cards) == 2
    (line,) = _light_lines(cards[1])
    assert line.startswith("Wall light: no verdict - ") and line.endswith(" - Calibrate on the empty wall")
    assert any(isinstance(e, r.api.Toast) and "failed" in e.message for e in r.events)


def test_a_new_calibrate_forgets_the_card_that_waited(tmp_path):
    """The card waiting for its plate is gone once another Calibrate runs: it is not shown again."""
    r = _rig(tmp_path, playing=True, kind="hot", wall_check=False)
    _calibrate(r)
    assert len(_cards(r)) == 1 and r.proc.plate_status == "capturing"
    r.flows._cb_calibrate()                           # pressed again before the picture ended
    _frames(r, 60)                                    # the old picture ends during the new Calibrate
    assert r.proc.plate_status != "capturing" and r.flows._calibrating
    assert len(_cards(r)) == 1                        # the old card is not shown again over the new run
    r.flows._cb_calibrate()                           # ... cancelled
    _frames(r, 10)
    assert len(_cards(r)) == 1


def test_a_plate_from_another_camera_crop_says_what_the_row_says(tmp_path):
    r = _rig(tmp_path, playing=True, kind="hot")
    _calibrate(r)
    r.proc.last_fg = SimpleNamespace(valid=False, reason="plate size")   # the referee refuses the plate
    assert r.proc.plate_status == "size"
    r.flows.ui.show_calibration_result_dialog("Brightness: 80", on_save=None)
    (line,) = _light_lines(_cards(r)[-1])
    assert line == _row_line(r) and "another camera crop" in line and line.endswith("Calibrate on the empty wall")
