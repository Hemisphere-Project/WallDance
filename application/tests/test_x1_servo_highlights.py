"""X1 (#t-027 ④, roadmap 2026-11-25 §4): the exposure servo exposes for the highlights of the zone.

Release 1's servo aimed the zone's median at 70 and tolerated 15 % of it clipped: on the façade of 08/10 the IR
flood's oval sat at white while the servo was satisfied.  X1: the zone's 99.5th percentile, belt-sized blobs left
out, stays under 235 and at most 0.5 % of it is at white; the median is a floor (35); when both cannot hold the
servo stops at the floor and says the light is the limit, in the « wall light » row's words.

The closed loops run on a simulated sensor: pixel = radiance x level / reference level, clipped at 255, with
sensor noise; the level is exposure (us) x gain (as a linear factor).
"""
from unittest.mock import MagicMock

import numpy as np
import pytest

import core.calibration as calibration
from core.calibration import (SERVO_CLIP_MAX_PCT, SERVO_HIGHLIGHT_DN, SERVO_MEDIAN_FLOOR, ExposureServo,
                              servo_region_stats, zone_highlights)
from core.light_verdict import LIGHT_CLIP_DN

_H, _W = 240, 320
_REF = 20000.0 * 10.0 ** (12.0 / 20.0)       # the radiance maps below are what the sensor gives at 20 ms / 12 dB
_BAND = (0, 40, 320, 160)                    # an ROI (x, y, w, h)


def _wall(wall=68.0, oval=0.0, belts=0, windows=None, seed=0, sigma=(45.0, 35.0)):
    """A textured wall with a light gradient; ``oval`` = the IR oval's peak in multiples of white at the
    reference level (0: none); ``belts`` small hard spots (24 x 10 px, saturated at any level); ``windows``
    (x, y, w, h) saturated at any level."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:_H, 0:_W]
    rad = wall * rng.uniform(0.85, 1.15, (_H, _W)) * (1.0 - 0.3 * xx / _W)
    if oval:
        g = np.exp(-(((xx - _W / 2) / sigma[0]) ** 2 + ((yy - _H / 2) / sigma[1]) ** 2) / 2)
        rad = rad + g * (oval * 255.0 - wall)
    for i in range(belts):
        x, y = 20 + 45 * i, 200 - 30 * (i % 2)
        rad[y:y + 10, x:x + 24] = 5000.0
    if windows is not None:
        x, y, w, h = windows
        rad[y:y + h, x:x + w] = 5000.0
    return rad


def _expose(rad, level, rng=None):
    img = rad * (level / _REF)
    if rng is not None:
        img = img + rng.normal(0.0, 2.0, img.shape)
    return np.clip(np.rint(img), 0, 255).astype(np.uint8)


def _lv(e, g):
    return e * 10.0 ** (g / 20.0)


def _loop(servo, rad, roi=None, rule="x1", noise=True, frames=1000, cam=None):
    """Close the loop: the camera applies each command at once; the servo sees the next frame.  ``cam`` =
    what the camera runs at first (A6 ①: not the servo's clamp, until the clamp is commanded)."""
    rng = np.random.default_rng(1) if noise else None
    cam = list(cam) if cam is not None else [servo.exposure_us, servo.gain_db]
    cmds, dirs = [], []
    for _ in range(frames):
        frame = _expose(rad, _lv(*cam), rng)
        if rule == "x1":
            z = zone_highlights(frame, roi)
            c = servo.feed(z.median, z.clip_pct, z.region, zone=z)
        else:
            b, cl, reg = servo_region_stats(frame, roi)
            c = servo.feed(b, cl, reg)
        if c is not None:
            before = _lv(*cam)
            cam[0 if c[0] == "exposure" else 1] = float(c[1])
            dirs.append(1 if _lv(*cam) > before else -1)
            cmds.append(c)
        if servo.done:
            break
    final = zone_highlights(_expose(rad, _lv(*cam)), roi)
    return servo.result(), cmds, dirs, final, cam


def _reversals(dirs):
    return sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)


# ---------------------------------------------------------------------------------------------------- the zone
def test_the_zone_is_cut_as_release_1_cuts_it_and_white_is_the_light_verdicts():
    frame = np.full((100, 200), 200, np.uint8)
    frame[40:60, :] = 30
    frame[40:42, :100] = 255
    for roi in ((0, 40, 200, 20), None, (0, 0, 200, 100), (500, 500, 10, 10), (-20, 30, 100, 50)):
        z = zone_highlights(frame, roi)
        b, clip, label = servo_region_stats(frame, roi)
        assert (z.median, z.region) == (b, label) and z.clip_pct_all == pytest.approx(clip)
    assert LIGHT_CLIP_DN == 250.0                                    # one « at white » for the servo and X2
    f = np.full((50, 50), 249, np.uint8)
    assert zone_highlights(f).clip_pct == 0.0
    f[:5, :] = 250                                                   # 10 % at 250: at white
    assert zone_highlights(f).clip_pct == pytest.approx(10.0)
    assert zone_highlights(np.dstack([f] * 3)).clip_pct == pytest.approx(10.0)     # BGR
    assert zone_highlights(f.astype(np.float32)).p995 == pytest.approx(250.0)      # float frames


def test_belts_on_the_wall_are_left_out_and_the_oval_is_not():
    frame = _expose(_wall(belts=3), _REF)
    full = servo_region_stats(frame, None)
    assert full[1] > SERVO_CLIP_MAX_PCT                              # 720 px at white = 0.94 % of the zone
    z = zone_highlights(frame)
    assert z.blobs == 3 and z.clip_pct == 0.0 and z.p995 < 120      # the wall's own highlights
    o = zone_highlights(_expose(_wall(oval=1.4), _REF))
    assert o.blobs == 0 and o.p995 == 255 and o.clip_pct > 2.0      # the oval is the light, it counts


def test_the_top_of_a_broad_highlight_is_not_taken_for_a_belt(monkeypatch):
    # an oval whose part above 235 is small (under a quarter of a verdict part) but stands on a broad bright
    # base: the belt detector's contrast rule keeps it -- or the servo would stop with the oval's top at white
    frame = _expose(_wall(oval=0.97), _REF)
    hot = int(np.count_nonzero(frame >= SERVO_HIGHLIGHT_DN))
    assert 0 < hot < 400                                             # a quarter part of this zone = 400 px
    z = zone_highlights(frame)
    assert z.blobs == 0 and z.p995 >= 230
    from core.belt_detector import BeltParams
    monkeypatch.setattr(calibration, "_BELT", BeltParams(k_bg=1.0, min_delta_dn=0))   # size alone
    assert zone_highlights(frame).blobs >= 1


# ---------------------------------------------------------------------------------------------------- the gate
def test_x1_unclips_a_hot_oval_where_release_1_left_it_clipped():
    rad = _wall(oval=1.4)                                            # median ~70, the oval's peak 1.4x white
    r1, cmds1, _d1, fin1, _c1 = _loop(ExposureServo(20000.0, 12.0), rad, rule="release1")
    assert r1.converged and fin1.p995 >= 250 and fin1.clip_pct > SERVO_CLIP_MAX_PCT   # release 1: satisfied
    r, cmds, dirs, fin, _cam = _loop(ExposureServo(20000.0, 12.0), rad)
    assert r.converged and not r.conflict, r.summary_line()
    assert fin.p995 < SERVO_HIGHLIGHT_DN and fin.clip_pct <= SERVO_CLIP_MAX_PCT
    assert r.p995 < SERVO_HIGHLIGHT_DN and r.clip_pct <= SERVO_CLIP_MAX_PCT
    assert fin.median >= SERVO_MEDIAN_FLOOR
    assert [k for k, _ in cmds] == ["gain"] * len(cmds) and cmds[0][1] < 12.0     # down: gain first
    assert len(cmds) <= 4 and _reversals(dirs) == 0
    assert "highlights p99.5" in r.summary_line() and "median" in r.summary_line()


@pytest.mark.parametrize("wall,oval", [(80.0, 0.0), (68.0, 1.4), (68.0, 2.0)])
def test_a_belt_sized_blob_at_white_does_not_move_the_command(wall, oval):
    _r0, cmds0, _d0, _f0, cam0 = _loop(ExposureServo(20000.0, 12.0), _wall(wall=wall, oval=oval))
    r, cmds, _d, fin, cam = _loop(ExposureServo(20000.0, 12.0), _wall(wall=wall, oval=oval, belts=3))
    assert len(cmds) == len(cmds0)
    assert all(k == k0 and v == pytest.approx(v0, rel=0.02) for (k, v), (k0, v0) in zip(cmds, cmds0))
    assert cam == pytest.approx(cam0, rel=0.02)
    if oval == 0.0:                                                  # a wall in band: no command at all
        assert cmds == [] and r.converged and r.blobs == 3


def test_without_leaving_them_out_the_belts_would_have_driven_it_down(monkeypatch):
    monkeypatch.setattr(calibration, "SERVO_BLOB_PART_SHARE", 0.0)
    _r, cmds, _d, _f, _c = _loop(ExposureServo(20000.0, 12.0), _wall(wall=80.0, belts=3))
    assert cmds and cmds[0][0] == "gain" and cmds[0][1] < 12.0


def test_the_median_floor_holds_and_the_conflict_is_reported():
    rad = _wall(oval=3.0)                                            # an oval far above white: no level fits
    r, cmds, dirs, fin, _cam = _loop(ExposureServo(20000.0, 12.0), rad)
    assert r.conflict and not r.converged and "floor" in r.note
    assert SERVO_MEDIAN_FLOOR * 0.95 <= fin.median <= SERVO_MEDIAN_FLOOR * 1.25     # at the floor, not under
    assert fin.p995 >= SERVO_HIGHLIGHT_DN                            # ... with the oval still at white
    assert len(cmds) <= 5 and _reversals(dirs) == 0
    assert "IR flood" in r.instruction and "exposure" not in r.instruction.lower()  # the light, not a setting
    line = r.summary_line()
    assert "the light is the limit" in line and r.instruction in line and "(35)" in line


def test_the_conflict_words_are_the_wall_light_rows():
    from core.light_verdict import light_verdict
    rad = _wall(oval=3.0)
    r, _cmds, _dirs, _fin, cam = _loop(ExposureServo(20000.0, 12.0), rad)
    v = light_verdict(_expose(rad, _lv(*cam)))
    assert v.red and r.instruction == v.instruction


def test_the_servo_and_the_wall_light_row_agree_on_white():
    # one « at white » (LIGHT_CLIP_DN): where the servo holds the highlights the row sees no part at white;
    # where the servo reports the conflict the row sees the bright part at white
    from core.light_verdict import light_verdict
    for oval in (1.2, 1.4, 1.6):
        r, _c, _d, _f, cam = _loop(ExposureServo(20000.0, 12.0), _wall(oval=oval))
        assert r.converged and not light_verdict(_expose(_wall(oval=oval), _lv(*cam))).clipped
    for oval in (3.0, 5.0):
        r, _c, _d, _f, cam = _loop(ExposureServo(20000.0, 12.0), _wall(oval=oval))
        assert r.conflict and light_verdict(_expose(_wall(oval=oval), _lv(*cam))).clipped


def test_a_static_measurement_still_ends(monkeypatch):
    # a camera that does not answer (the frame never changes): the brackets and the step limit end it
    s = ExposureServo(20000.0, 12.0)
    z = zone_highlights(_expose(_wall(oval=3.0), _REF))
    for _ in range(2000):
        s.feed(z.median, z.clip_pct, z.region, zone=z)
        if s.done:
            break
    assert s.done and s.result().steps <= s.max_steps


# ---------------------------------------------------------------------------------------------------- bounded
_STARTS = [(20000.0, 12.0, 36.0), (51600.0, 10.0, 36.0), (25000.0, 36.0, 30.0), (200.0, 0.5, 30.0),
           (5000.0, 0.0, 30.0)]


@pytest.mark.parametrize("oval", [0.0, 0.8, 1.2, 1.6, 2.5, 5.0])
@pytest.mark.parametrize("wall", [8.0, 40.0, 68.0, 160.0])
@pytest.mark.parametrize("start", _STARTS)
def test_it_converges_in_a_bounded_number_of_steps_without_hunting(oval, wall, start):
    e0, g0, gmax = start
    s = ExposureServo(e0, g0, gain_max_db=gmax)
    clamps = len(s._clamp_cmds)
    r, cmds, dirs, fin, _cam = _loop(s, _wall(wall=wall, oval=oval), cam=(e0, g0))
    assert s.done and r.steps - clamps <= 12, (r.steps, r.summary_line())
    assert _reversals(dirs[clamps:]) <= 1, dirs
    if r.converged:                                                  # the highlights hold, the floor holds
        assert fin.p995 < SERVO_HIGHLIGHT_DN and fin.clip_pct <= SERVO_CLIP_MAX_PCT, r.summary_line()
        assert fin.median >= SERVO_MEDIAN_FLOOR * 0.95, r.summary_line()
    elif r.conflict:                                                 # stopped at the floor, said so
        assert SERVO_MEDIAN_FLOOR * 0.95 <= fin.median <= SERVO_MEDIAN_FLOOR * 1.25 and r.instruction
    else:                                                            # at a limit of the camera
        assert ("add IR" in r.note and fin.median < 70 - 12) or "minimum" in r.note, r.summary_line()


def test_a_clamped_start_is_commanded_first_then_the_highlights_rule():
    # A6 ①: the hangar project saved at 51.6 ms against the 25 ms blur budget -- the clamp goes out first
    rad = _wall(oval=1.4) * (_REF / _lv(25000.0, 10.0))               # the scene as the camera sees it at 25 ms
    s = ExposureServo(51600.0, 10.0)
    r, cmds, dirs, fin, cam = _loop(s, rad, cam=(51600.0, 10.0))
    assert cmds[0] == ("exposure", pytest.approx(25000.0))
    assert all(k == "gain" for k, _ in cmds[1:]) and cmds[1][1] < 10.0   # then down, gain first
    assert r.converged and fin.p995 < SERVO_HIGHLIGHT_DN and _reversals(dirs[1:]) == 0
    assert cam[0] == pytest.approx(25000.0) == r.exposure_us
    s2 = ExposureServo(25000.0, 36.0, gain_max_db=30.0)              # a gain above the camera's ceiling
    r2, cmds2, _d2, fin2, _cam2 = _loop(s2, _wall(oval=1.4) * (_REF / _lv(25000.0, 30.0)), cam=(25000.0, 36.0))
    assert cmds2[0] == ("gain", pytest.approx(30.0)) and r2.converged and fin2.p995 < SERVO_HIGHLIGHT_DN


# ---------------------------------------------------------------------------------------------------- the hangar
def test_where_nothing_is_hot_it_drives_the_median_as_release_1_did():
    # windows above the ROI (outside the zone), a dark wall: raised until the median is on target
    rad = _wall(wall=20.0, windows=(0, 0, 320, 30))
    r, cmds, _d, fin, _cam = _loop(ExposureServo(200.0, 0.5), rad, roi=_BAND)
    assert r.converged and abs(fin.median - 70.0) <= 12.0 and cmds[0][0] == "exposure"
    # a uniformly too bright wall backs off into the band, gain first
    r, cmds, _d, fin, cam = _loop(ExposureServo(20000.0, 12.0), _wall(wall=900.0), roi=_BAND)
    assert r.converged and abs(fin.median - 70.0) <= 12.0 and cmds[0][0] == "gain"
    assert cam[1] == 0.0 and cam[0] < 20000.0
    # a dark night: exposure to the blur cap, then the camera's gain ceiling, then « add IR »
    s = ExposureServo(5000.0, 0.0, gain_max_db=30.0)
    r, cmds, _d, fin, _cam = _loop(s, _wall(wall=0.05, belts=2), roi=_BAND)
    assert s.exposure_us == pytest.approx(s.exposure_cap_us) and s.gain_db == pytest.approx(30.0)
    kinds = [k for k, _ in cmds]
    assert kinds.index("gain") > 0 and "exposure" not in kinds[kinds.index("gain"):]
    assert not r.converged and "IR" in r.note


def test_hard_windows_inside_the_zone_stop_the_wall_at_the_floor_and_say_so():
    # Release 1 raised the wall to 70 under windows clipping inside the ROI; X1 raises it to the floor -- the
    # median beats the highlights -- and no further, and says the light is the limit.  A window larger than a
    # belt is the light as far as the servo can tell.
    rad = _wall(wall=20.0, windows=(0, 40, 320, 10))                 # 6.25 % of the ROI at white at any level
    r, cmds, dirs, fin, _cam = _loop(ExposureServo(200.0, 0.5), rad, roi=_BAND)
    assert cmds[0][0] == "exposure" and cmds[0][1] > 200.0           # raised, never driven black
    assert SERVO_MEDIAN_FLOOR * 0.95 <= fin.median <= SERVO_MEDIAN_FLOOR * 1.25
    assert r.conflict and r.instruction and _reversals(dirs) <= 1


def test_the_release_1_rule_still_answers_a_caller_without_a_zone():
    s = ExposureServo(10000.0, 6.0)
    for _ in range(40):
        s.feed(78.0, 20.0)
    assert s.result().p995 is None and "highlights" not in s.result().summary_line()


# ---------------------------------------------------------------------------------------------------- the flows
class _Cams:
    """The camera controller as the flows drive it: the callbacks set what the camera runs at."""

    def __init__(self, e, g):
        self.ids_exposure_us, self.ids_gain_db = float(e), float(g)
        self.calls = []

    def _cb_ids_exposure_change(self, v):
        self.ids_exposure_us = float(v)
        self.calls.append(("exposure", float(v)))

    def _cb_ids_gain_change(self, v):
        self.ids_gain_db = float(v)
        self.calls.append(("gain", float(v)))


def _live_flows(rad, e=20000.0, g=12.0):
    from runtime.calibration_flows import CalibrationFlows
    settings = MagicMock()
    settings.roi_enabled = False
    cams = _Cams(e, g)
    rng = np.random.default_rng(2)
    flows = CalibrationFlows(
        processor=MagicMock(), enhancer=MagicMock(gamma=1.0, clahe_clip=2.5), tracker=MagicMock(),
        settings=settings, recorder=MagicMock(), camera=MagicMock(), unified_camera=MagicMock(),
        use_unified=False, models=MagicMock(), cameras=cams, configs=MagicMock(), ui=MagicMock(available=True),
        last_raw_frame=lambda: _expose(rad, _lv(cams.ids_exposure_us, cams.ids_gain_db), rng),
        roi_source_size=lambda: (_W, _H), get_effective_roi=lambda w, h: (0, 0, w, h),
        reset_sensitivity_anchor=lambda **k: None, sync_mask_ui=lambda: None, request_reprocess=lambda: None,
        imgsz_change=lambda v: None)
    flows._servo = ExposureServo(e, g)
    flows._calibrating = True
    return flows, cams


def _run_servo(flows, frames=400):
    for _ in range(frames):
        if flows._servo is None:
            break
        flows._step_calibration([], 30.0)


def test_the_live_calibrate_runs_the_highlights_rule():
    flows, cams = _live_flows(_wall(oval=1.4))
    _run_servo(flows)
    assert cams.calls and cams.calls[0][0] == "gain" and cams.calls[0][1] < 12.0   # release 1 sent nothing
    res = flows._servo_result
    assert res.converged and res.p995 < SERVO_HIGHLIGHT_DN and not res.conflict
    assert res.gain_db == pytest.approx(cams.ids_gain_db)
    assert not any("light is the limit" in str(c) for c in flows.ui.show_toast.call_args_list)


def test_a_conflict_reaches_the_technician_as_one_instruction_about_the_light():
    flows, _cams = _live_flows(_wall(oval=3.0))
    _run_servo(flows)
    res = flows._servo_result
    assert res.conflict and "IR flood" in res.instruction
    toasts = [c.args[0] for c in flows.ui.show_toast.call_args_list]
    assert any("the light is the limit" in t and res.instruction in t for t in toasts)
    assert res.instruction in res.summary_line()                     # and on the result card
