"""A6 (Plan Cordée, #t-027 ① ② ③): Calibrate correctness.

① The exposure servo commands the camera when it clamps: a project saved at 51.6 ms (the hangar corpus) against
  the 25 ms blur budget kept its 51.6 ms while the report said 25.0 ms (audit motion-calibration#7).
② A cancelled or stopped Calibrate puts back what it changed -- the enhancement it seeded, the take's exposure /
  gain, MOG2 var and scale, the provenance -- and the plate it records is written at Save, not before
  (motion-calibration#3: "gamma 0.73 seeded to 3.03 and still 3.03 after cancel, exposure 25000 and gain 36
  written and never undone"; the plate written at apply, before the verdict).
③ The light remedy and the French note stop prescribing a take played in RUN and "paint, then Calibrate again".
"""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from core.calibration import CalibrationResult, ExposureServo
from core.empty_wall import PERSON_SETTLE
from runtime.calibration_flows import CalibrationFlows

REPO = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------------------------------- ①
class _Camera:
    """The IDS camera as the servo's caller drives it: it changes only on a command."""

    def __init__(self, exposure_us, gain_db):
        self.exposure_us, self.gain_db = float(exposure_us), float(gain_db)

    def apply(self, cmd):
        if cmd is not None:
            setattr(self, "exposure_us" if cmd[0] == "exposure" else "gain_db", float(cmd[1]))


def _drive(servo, cam, brightness_of, frames=400):
    cmds = []
    for _ in range(frames):
        cmd = servo.feed(brightness_of(cam), 0.0)
        cam.apply(cmd)
        if cmd is not None:
            cmds.append(cmd)
        if servo.done:
            break
    return cmds


def test_the_servo_commands_an_exposure_above_the_blur_budget_down_to_it():
    # the audit's reproduction: the camera at 51.6 ms / 10 dB, the scene in band -> before: no command at all,
    # the camera still at 51.6 ms, the report "25.0 ms ... converged, 0 steps"
    cam = _Camera(51600.0, 10.0)
    s = ExposureServo(exposure_us=cam.exposure_us, gain_db=cam.gain_db)
    cmds = _drive(s, cam, lambda c: 70.0)
    assert cmds == [("exposure", pytest.approx(25000.0))]
    assert cam.exposure_us == pytest.approx(25000.0) == s.result().exposure_us
    assert s.result().converged and s.result().steps == 1


def test_the_servo_on_a_dark_scene_cuts_the_exposure_to_the_cap_before_raising_gain():
    # before: six gain commands up to 28 dB and the camera still at 51.6 ms (report 25.0 ms)
    cam = _Camera(51600.0, 10.0)
    s = ExposureServo(exposure_us=cam.exposure_us, gain_db=cam.gain_db, gain_max_db=30.0)
    cmds = _drive(s, cam, lambda c: 20.0)
    assert cmds[0] == ("exposure", pytest.approx(25000.0))
    assert all(k == "gain" for k, _ in cmds[1:]) and cam.exposure_us == pytest.approx(25000.0)
    assert cam.gain_db == pytest.approx(30.0) == s.result().gain_db


def test_a_lower_blur_budget_and_a_gain_above_the_ceiling_are_both_commanded():
    # Calib2 lowered the blur budget to 12 ms with the camera at 25 ms; gain 36 dB on a 30 dB camera
    cam = _Camera(25000.0, 36.0)
    s = ExposureServo(exposure_us=cam.exposure_us, gain_db=cam.gain_db, blur_budget_ms=12.0, gain_max_db=30.0)
    cmds = _drive(s, cam, lambda c: 70.0)
    assert cmds == [("exposure", pytest.approx(12000.0)), ("gain", pytest.approx(30.0))]
    assert (cam.exposure_us, cam.gain_db) == (pytest.approx(12000.0), pytest.approx(30.0))


def test_a_start_inside_the_limits_sends_nothing_new():
    s = ExposureServo(exposure_us=20000.0, gain_db=6.0)
    assert _drive(s, _Camera(20000.0, 6.0), lambda c: 70.0) == [] and s.result().converged


def test_the_live_calibrate_sends_the_clamp_to_the_camera_and_reports_what_it_runs_at():
    flows, _parts = _rig(playing=False)
    cams = flows.cameras
    cams.ids_exposure_us, cams.ids_gain_db = 51600.0, 10.0
    flows._servo = ExposureServo(cams.ids_exposure_us, cams.ids_gain_db)
    flows._calibrating = True
    flows._step_calibration([], 30.0)                     # the first frame: the clamp goes out at once
    assert cams.exposure_calls == [pytest.approx(25000.0)]
    flows.last_raw_frame = lambda: np.full((200, 120), 70, np.uint8)
    for _ in range(20):
        if flows._servo is None:
            break
        flows._step_calibration([], 30.0)
    assert flows._servo_result.exposure_us == pytest.approx(25000.0)
    assert "25.0 ms" in flows._servo_result.summary_line()


# ---------------------------------------------------------------------------------------------------- ②
class _Enhancer:
    def __init__(self, gamma, clahe):
        self.gamma, self.clahe_clip = float(gamma), float(clahe)

    def _update_gamma_lut(self):
        pass

    def _update_clahe(self):
        pass


class _Cameras:
    def __init__(self, exposure_us=8800.0, gain_db=30.0):
        self.ids_exposure_us, self.ids_gain_db = float(exposure_us), float(gain_db)
        self.ids_exposure_auto = self.ids_gain_auto = False
        self.exposure_calls, self.gain_calls = [], []

    def _cb_ids_exposure_change(self, v):
        self.ids_exposure_us = float(v)
        self.exposure_calls.append(float(v))

    def _cb_ids_gain_change(self, v):
        self.ids_gain_db = float(v)
        self.gain_calls.append(float(v))

    def _cb_ids_exposure_auto_toggle(self, on):
        self.ids_exposure_auto = bool(on)

    def _cb_ids_gain_auto_toggle(self, on):
        self.ids_gain_auto = bool(on)


class _Proc:
    """The processor surface Calibrate uses; raw detections are set by the test."""

    def __init__(self):
        self.last_raw_dets = []
        self.var, self.scale = 8.0, 0.7
        self.gray = np.random.default_rng(1).integers(30, 50, (60, 40)).astype(np.uint8)

    def get_last_motion_gray(self):
        return self.gray

    def get_motion_var_threshold(self):
        return self.var

    def get_motion_scale(self):
        return self.scale

    def set_motion_var_threshold(self, v):
        self.var = float(v)

    def set_motion_scale(self, s):
        self.scale = float(s)

    def cancel_exclusion_calibration(self):
        pass


def _rig(playing=True, gamma=0.73, clahe=2.5, calibrated=True, proc=None, capture_plate=None):
    frame = np.full((200, 120), 40, np.uint8)
    proc = proc if proc is not None else _Proc()
    settings = SimpleNamespace(confidence=0.15, roi_enabled=True, imgsz=1280, person_height_px=110)
    recorder = SimpleNamespace(is_playing=playing,
                               playback_camera={"exposure_us": 25000.0, "gain_db": 36.0} if playing else None)
    anchors = []
    flows = CalibrationFlows(
        processor=proc, enhancer=_Enhancer(gamma, clahe), tracker=MagicMock(), settings=settings,
        recorder=recorder, camera=SimpleNamespace(state=SimpleNamespace(is_open=not playing)),
        unified_camera=None, use_unified=False, models=SimpleNamespace(_model_loaded=True, model=object()),
        cameras=_Cameras(), configs=MagicMock(), ui=MagicMock(available=True),
        last_raw_frame=lambda: frame, roi_source_size=lambda: (120, 200),
        get_effective_roi=lambda w, h: (10, 0, 90, 180),
        reset_sensitivity_anchor=lambda **k: anchors.append(k), sync_mask_ui=lambda: None,
        request_reprocess=lambda: None, imgsz_change=lambda v: None, capture_plate=capture_plate)
    if calibrated:
        flows.calibration_state = {k: {"source": "aim", "ts": "2026-10-08 20:06", "epoch": 1.0}
                                   for k in ("gamma", "clahe", "mog2_var_threshold", "mog2_scale")}
    return flows, SimpleNamespace(proc=proc, settings=settings, anchors=anchors, frame=frame)


def _step_until(flows, cond, frames=600):
    for _ in range(frames):
        if cond():
            return True
        flows._step_calibration([], 50.0)
    return cond()


def test_cancel_during_the_check_puts_back_the_enhancement_the_take_exposure_and_mog2():
    written = []
    proc = _Proc()

    def capture_plate(source):                            # the app's capture: its done-callback writes the file
        proc._plate_capture = object()
        proc._plate_capture_done = lambda plate, err: written.append(plate)
    proc.var, proc.scale = 20.0, 0.5
    flows, p = _rig(proc=proc, capture_plate=capture_plate)
    state0 = copy.deepcopy(flows.calibration_state)
    flows._cb_calibrate()
    assert flows.cameras.exposure_calls == [25000.0] and flows.cameras.gain_calls == [36.0]   # the take's
    assert _step_until(flows, lambda: flows._wall_check is not None)                          # an empty wall
    assert (p.proc.var, p.proc.scale) == (8.0, 0.7) and p.settings.confidence == pytest.approx(0.12)
    assert flows.calibration_state["mog2_var_threshold"]["ts"] != "2026-10-08 20:06"
    flows._cb_calibrate()                                 # second press = cancel
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (0.73, 2.5)
    assert (flows.cameras.ids_exposure_us, flows.cameras.ids_gain_db) == (8800.0, 30.0)
    assert (p.proc.var, p.proc.scale) == (20.0, 0.5) and p.anchors[-1] == {"var_anchor": 20.0}
    assert flows.calibration_state == state0 and p.settings.confidence == 0.15
    assert not flows._calibrating and flows._snapshot is None
    assert written == [] and proc._plate_capture is None  # the capture stopped: nothing written


def test_cancel_after_the_gamma_seed_puts_the_projects_gamma_back():
    # the audit's reproduction, cancelled before the window filled: gamma 0.73 seeded to ~3, then cancel
    flows, p = _rig()
    flows._cb_calibrate()
    _step_until(flows, lambda: flows._emptiness is None)
    assert flows.enhancer.gamma > 2.0                      # the seed the scene window runs at
    flows._cb_calibrate()
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (0.73, 2.5)
    assert (flows.cameras.ids_exposure_us, flows.cameras.ids_gain_db) == (8800.0, 30.0)


def test_a_cancelled_calibrate_from_auto_exposure_goes_back_to_auto():
    flows, _p = _rig()
    flows.cameras.ids_exposure_auto = flows.cameras.ids_gain_auto = True
    flows._cb_calibrate()
    flows._cb_calibrate()
    assert flows.cameras.ids_exposure_auto and flows.cameras.ids_gain_auto


def test_a_finished_calibrate_is_kept_and_records_its_ceiling():
    flows, p = _rig()
    flows._cb_calibrate()
    assert _step_until(flows, lambda: not flows._calibrating, frames=900)
    assert flows._snapshot is None and flows._wall_result.clean
    assert flows.enhancement_ceiling == (0.73, 2.5)       # the project's enhancement passed first
    ceil = flows.calibration_state["enhancement_ceiling"]
    assert (ceil["gamma"], ceil["clahe"], ceil["kept_current"], ceil["source"]) == (0.73, 2.5, True, "aim")
    flows._cb_calibrate()                                 # a new run, cancelled: back to what the last one left
    flows._cb_calibrate()
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (0.73, 2.5)
    assert (flows.cameras.ids_exposure_us, flows.cameras.ids_gain_db) == (25000.0, 36.0)


def _real_processor():
    pytest.importorskip("torch")
    from core.pipeline import FrameProcessor, ProcessingSettings
    return FrameProcessor(model=None, settings=ProcessingSettings(
        confidence=0.15, imgsz=640, use_fp16=False, enhance_enabled=False, enhance_lite=False,
        enhance_force=False, person_height_px=110, use_gpu_path=False))


def _real_rig(written, wall_check):
    from core.foreground import PLATE_MIN_FRAMES
    proc = _real_processor()

    def capture_plate(source):                            # app._cb_capture_plate: the done-callback writes
        proc.start_plate_capture(PLATE_MIN_FRAMES, lambda plate, err: written.append((plate, err, source)))
    flows, p = _rig(proc=proc, capture_plate=capture_plate)
    flows.wall_check_enabled = wall_check
    return flows, proc


def _scene():
    res = CalibrationResult()
    res.var_ok, res.var_threshold, res.mog2_scale, res.clahe_value, res.noise_sigma = True, 16.0, 0.7, None, 2.0
    return res


def _feed_plate(proc, n=80):
    frame = np.full((200, 120), 90, np.uint8)
    for _ in range(n):
        if proc._plate_capture is None:
            break
        proc._feed_plate_capture(frame)


def test_the_plate_calibrate_records_is_written_at_save_not_before():
    written = []
    flows, proc = _real_rig(written, wall_check=False)
    flows._apply_calibration(_scene())
    _feed_plate(proc)
    assert proc._plate_capture is None and proc._fg_detector is not None     # in use for this session
    assert written == [] and flows._pending_plate is not None                # ...not on disk
    flows.ui.show_calibration_result_dialog.assert_called_once()
    flows.ui.show_calibration_result_dialog.call_args.kwargs["on_save"]()    # « Save to project »
    assert len(written) == 1 and written[0][1:] == ("", "calibrate")
    flows.configs._cb_save_config.assert_called_once()
    assert flows.commit_pending_plate() is False and len(written) == 1      # once


def test_a_save_while_the_plate_is_still_recording_writes_it_when_it_is_done():
    written = []
    flows, proc = _real_rig(written, wall_check=False)
    flows._apply_calibration(_scene())
    assert proc._plate_capture is not None and flows.commit_pending_plate() is True
    assert written == []
    _feed_plate(proc)
    assert len(written) == 1 and written[0][0] is not None


def test_no_save_during_calibrate_and_a_stopped_calibrate_keeps_the_projects_plate(tmp_path):
    from core.foreground import CleanPlate
    old = CleanPlate(plate=np.full((50, 30), 60.0, np.float32), sigma=0.5, frame_size=(120, 200),
                     created="2026-10-08T21:36:18")
    path = str(old.save(tmp_path / "latest.npz"))
    written = []
    flows, proc = _real_rig(written, wall_check=True)
    proc.settings.fg_plate_path = path
    assert proc._ensure_plate() is not None and proc._fg_detector.plate.created == "2026-10-08T21:36:18"
    flows._calibrating = True
    flows._apply_calibration(_scene())
    _feed_plate(proc)
    assert proc._fg_detector.plate.created != "2026-10-08T21:36:18"           # Calibrate's, this session only
    assert flows.commit_pending_plate() is False and written == []           # Ctrl+S mid-Calibrate: not yet
    proc.last_raw_dets = [(0.7, 40.0, 90.0, 60.0)]                           # someone walks in and stays
    assert _step_until(flows, lambda: not flows._calibrating, frames=300)
    assert flows.last_stop and "Someone is in view" in flows.last_stop
    assert written == [] and flows._pending_plate is None
    assert proc._ensure_plate() is not None and proc._fg_detector.plate.created == "2026-10-08T21:36:18"


def test_every_project_save_commits_the_plate():
    pytest.importorskip("dearpygui")
    import app as app_mod
    from runtime import api
    handlers = {}
    stub = MagicMock()
    stub.api.register = lambda kind, fn: handlers.__setitem__(kind, fn)
    app_mod.WallDanceApp._register_command_handlers(stub)
    handlers[api.SaveConfig](api.SaveConfig())
    stub.configs._cb_save_config.assert_called_once()
    stub.calibration.commit_pending_plate.assert_called_once()
    handlers[api.SaveConfig](api.SaveConfig("other"))
    stub.configs._cb_do_save_config.assert_called_once_with("other")
    assert stub.calibration.commit_pending_plate.call_count == 2


# ---------------------------------------------------------------------------------------------------- ③
def test_the_light_remedy_never_sends_a_take_to_the_show():
    from core.ops_monitor import LIGHT_REMEDY
    assert "STANDBY" in LIGHT_REMEDY and "never play a take in RUN" in LIGHT_REMEDY
    assert "back to LIVE" in LIGHT_REMEDY and "Save" in LIGHT_REMEDY


def test_the_french_note_no_longer_prescribes_a_take_in_run_or_painting():
    note = (REPO / "docs" / "TERRAIN_DEMO_FR.md").read_text(encoding="utf-8")
    assert "le spectacle continue" not in note
    assert "peindre une case d'exclusion dessus" not in note
    assert "Jamais de prise lue en RUN" in note and "en STANDBY" in note
    assert "Someone is in view" in note


def test_the_empty_wall_messages_no_longer_say_paint():
    import inspect
    import core.empty_wall as ew
    import runtime.calibration_flows as cf
    for mod in (ew, cf):
        src = inspect.getsource(mod)
        assert "paint an exclusion there, then calibrate again" not in src
        assert "or paint an exclusion" not in src
