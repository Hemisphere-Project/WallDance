"""X3 (roadmap 2026-11-25, #t-027 ⑤): emptiness first.

08/10, 20:06: Calibrate ran with a dancer on the wall; every rung of the empty-wall check "failed" on her and the
ladder walked the `mur` project down to gamma 0.8 / CLAHE 1.0 for the whole evening (about 18 points of recall).
Now Calibrate first watches for someone in view and stops, saying where, changing nothing; and the ladder never
goes under neutral because of a person.  Flow-level tests on CalibrationFlows with a fake processor whose raw
detections the test sets; the replay on `mur` 3 is the gate (gates/x3a6/run_gate.sh).

Gate replay, 26/11 on mgr-ran (l @ 1920 x 1056, the real pipeline and CalibrationFlows, the take looping): on
`mur` 3 from f1300 (two dancers) and f16200 (one), from gamma 1.0 / CLAHE 2.0 (the project at 20:06) and from
mur2's 1.2147 / 2.5, Calibrate stopped after 11 frames each time, named a place 22-26 px from a dancer of the
truth, and left enhancement and camera exactly as they were; release 1 on the same four ran to the end at
gamma 0.8 / CLAHE 1.0 with the plate written.  The empty take `mur2` 2 still calibrates (clean, 1.28 / 2.5).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from core.empty_wall import NEUTRAL_GAMMA, PERSON_FRAMES, PERSON_SETTLE
from runtime.calibration_flows import CalibrationFlows

ROI = (10, 0, 90, 180)                     # x, y, w, h on a 120 x 200 portrait frame
SHE = (0.62, 30.0, 95.0, 50.0)             # conf, x, y, h: middle left of the wall


class _Enhancer:
    def __init__(self, gamma, clahe):
        self.gamma, self.clahe_clip = float(gamma), float(clahe)
        self.seen = []                     # every gamma YOLO was given

    def _update_gamma_lut(self):
        self.seen.append(self.gamma)

    def _update_clahe(self):
        pass


class _Proc:
    def __init__(self):
        self.last_raw_dets = []
        self.var, self.scale = 20.0, 0.5
        self.var_calls = []
        self.gray = np.random.default_rng(2).integers(30, 50, (60, 40)).astype(np.uint8)

    def get_last_motion_gray(self):
        return self.gray

    def get_motion_var_threshold(self):
        return self.var

    def get_motion_scale(self):
        return self.scale

    def set_motion_var_threshold(self, v):
        self.var = float(v)
        self.var_calls.append(float(v))

    def set_motion_scale(self, s):
        self.scale = float(s)

    def cancel_exclusion_calibration(self):
        pass


class _Cameras:
    def __init__(self):
        self.ids_exposure_us, self.ids_gain_db = 8800.0, 30.0
        self.ids_exposure_auto = self.ids_gain_auto = False
        self.calls = []

    def _cb_ids_exposure_change(self, v):
        self.ids_exposure_us = float(v)
        self.calls.append(("exposure", float(v)))

    def _cb_ids_gain_change(self, v):
        self.ids_gain_db = float(v)
        self.calls.append(("gain", float(v)))


def _rig(gamma=1.2147, clahe=2.5, calibrated=True, playing=True):
    """The 08/10 façade evening in small: the mur2 project's calibrated enhancement, a take played at 25 ms /
    36 dB, a dark wall (the scene formula seeds a strong gamma)."""
    proc = _Proc()
    plates = []
    settings = SimpleNamespace(confidence=0.15, roi_enabled=True, imgsz=1280, person_height_px=110)
    frame = np.full((200, 120), 40, np.uint8)
    flows = CalibrationFlows(
        processor=proc, enhancer=_Enhancer(gamma, clahe), tracker=MagicMock(), settings=settings,
        recorder=SimpleNamespace(is_playing=playing,
                                 playback_camera={"exposure_us": 25000.0, "gain_db": 36.0} if playing else None),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=True)), unified_camera=None, use_unified=False,
        models=SimpleNamespace(_model_loaded=True, model=object()), cameras=_Cameras(), configs=MagicMock(),
        ui=MagicMock(available=True), last_raw_frame=lambda: frame, roi_source_size=lambda: (120, 200),
        get_effective_roi=lambda w, h: ROI, reset_sensitivity_anchor=lambda **k: None, sync_mask_ui=lambda: None,
        request_reprocess=lambda: None, imgsz_change=lambda v: None, capture_plate=plates.append)
    if calibrated:
        flows.calibration_state = {k: {"source": "aim", "ts": "2026-10-08 21:36", "epoch": 1.0}
                                   for k in ("gamma", "clahe", "mog2_var_threshold", "mog2_scale")}
    return flows, proc, plates


def _run(flows, proc, dets_at, frames=900):
    """Step Calibrate; ``dets_at(i, flows)`` -> the raw detections of frame i.  Returns the frames used."""
    for i in range(frames):
        if not flows._calibrating:
            return i
        proc.last_raw_dets = list(dets_at(i, flows))
        flows._step_calibration([], 50.0)
    return frames


def test_someone_in_view_stops_calibrate_before_anything_is_measured_and_says_where():
    flows, proc, plates = _rig()
    state0 = {k: dict(v) for k, v in flows.calibration_state.items()}
    flows._cb_calibrate()
    used = _run(flows, proc, lambda i, f: [SHE] if i % 3 else [])          # seen in 2 frames of 3
    assert used <= PERSON_SETTLE + 10 and not flows._calibrating
    # changed nothing: the enhancement, the take's exposure / gain put back, nothing measured, no plate
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.2147, 2.5)
    assert (flows.cameras.ids_exposure_us, flows.cameras.ids_gain_db) == (8800.0, 30.0)
    assert proc.var_calls == [] and plates == [] and flows.calibration_state == state0
    assert flows.settings.confidence == 0.15 and flows._snapshot is None
    # says where: the status line stays, a toast, the log
    assert "middle left of the wall (x 30, y 95)" in flows.last_stop
    flows.ui.set_calibrate_status.assert_called_with("Stopped: someone in view, middle left of the wall (x 30, y 95)")
    toast = flows.ui.show_toast.call_args_list[-1][0][0]
    assert toast == flows.last_stop and "Clear the wall, then press Calibrate again" in toast
    flows.ui.show_calibration_result_dialog.assert_not_called()


def test_a_dim_dancer_seen_one_frame_in_three_is_still_someone():
    flows, proc, _plates = _rig()
    flows._cb_calibrate()
    _run(flows, proc, lambda i, f: [SHE] if i % 3 == 0 else [(0.09, 30.0, 95.0, 50.0)])
    assert flows.last_stop is not None and (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.2147, 2.5)


def test_the_watch_runs_at_neutral_so_a_strong_enhancement_ghost_is_no_person():
    # a project at gamma 1.8: the night project's ghost by the door equipment (17/30 frames at 1.8, none at 1.0)
    flows, proc, _plates = _rig(gamma=1.8, clahe=1.5)
    flows._cb_calibrate()
    seeded = flows._emptiness_resume[0]
    _run(flows, proc, lambda i, f: [(0.5, 60.0, 120.0, 90.0)] if f.enhancer.gamma > 1.3 else [], frames=40)
    assert flows.last_stop is None and flows._emptiness is None
    assert NEUTRAL_GAMMA in flows.enhancer.seen                              # the watch ran at gamma 1.0
    assert flows.enhancer.gamma == pytest.approx(seeded)                     # then the scene window's seed


def test_a_stray_false_person_does_not_stop_calibrate():
    flows, proc, _plates = _rig()
    flows._cb_calibrate()
    used = _run(flows, proc, lambda i, f: [(0.3, 60.0, 120.0, 90.0)] if i == 10 else [])
    assert flows.last_stop is None and flows._wall_result is not None and flows._wall_result.clean
    assert used > PERSON_SETTLE + PERSON_FRAMES                              # the whole Calibrate ran


def test_the_ladder_never_goes_under_neutral_because_of_someone_who_walks_in():
    # a new project (no enhancement to keep): the scene seed is strong, the ladder starts above neutral
    flows, proc, plates = _rig(gamma=1.0, clahe=2.0, calibrated=False)
    flows._cb_calibrate()
    _run(flows, proc, lambda i, f: [SHE] if f._wall_check is not None else [])
    r = flows._wall_result
    assert flows.last_stop is not None and "middle left of the wall" in flows.last_stop
    assert min(flows.enhancer.seen) >= NEUTRAL_GAMMA - 1e-9                  # 0.8 never tried
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.0, 2.0)    # what the project had
    assert proc.var == 20.0 and plates == ["calibrate"] and flows._pending_plate is None
    assert r is None and flows.enhancement_ceiling is None


def test_a_live_calibrate_watches_before_the_servo_moves_the_camera():
    flows, proc, _plates = _rig(playing=False)
    from core.calibration import ExposureServo
    flows._cb_calibrate()
    flows._servo = ExposureServo(8800.0, 30.0)                               # as a live IDS camera gets it
    for _ in range(PERSON_SETTLE + 5):
        proc.last_raw_dets = []
        flows._step_calibration([], 50.0)
    assert flows._emptiness is not None and flows.cameras.calls == []        # the servo waits
    _run(flows, proc, lambda i, f: [SHE])
    assert flows.last_stop is not None and flows.cameras.calls == [] and flows._servo is None


def test_emptiness_first_can_be_turned_off():
    flows, proc, _plates = _rig()
    flows.emptiness_first = False
    flows._cb_calibrate()
    assert flows._emptiness is None and flows._calibrator.is_collecting
