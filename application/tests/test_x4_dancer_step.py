"""X4 (hub walldance#t-055): the dancer step -- one dancer on the wall for 10 s -- and the band (#t-050 ②).

The step measures her size from the boxes AND her foreground group with no keypoint gate, chooses the enhancement
on her (never one worse than none, never above X3's clean ceiling) and turns it ON when it chooses one, judges the
belt glare against her body box, says so and sets nothing when nobody or two people are there, ends with one line
and a card whose Cancel puts everything back; readiness waits while it runs; the « dancer size » row is ok only
once it measured her.
The band: her height at the network, orange under ~110 px, a note over ~200 px, on boxes and foreground groups.
Pure logic and stubs (no GPU, no dpg); the GUI parts are in test_gui_x4_dancer_step.py.
"""
import copy
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from core.dancer_step import (BAND_NOTE_PX, BAND_WARN_PX, NONE, DancerBand, DancerStep, Enh, candidates,
                              choose, glare_share, larger_input, observe)
from core.foreground import FgBlob, FgFrame
from runtime.calibration_flows import CalibrationFlows

W, H = 400, 800          # a portrait frame, original px


# ------------------------------------------------------------------------------------------- synthetic frames
def _fg(groups, valid=True):
    """An FgFrame over the whole frame (ds 4) with one foreground group per (x0, y0, x1, y1)."""
    ds = 4
    m = np.zeros((H // ds, W // ds), np.uint8)
    blobs = []
    for x0, y0, x1, y1 in groups:
        m[int(y0) // ds:int(y1) // ds, int(x0) // ds:int(x1) // ds] = 1
        area = float((x1 - x0) * (y1 - y0))
        blobs.append(FgBlob(x=(x0 + x1) / 2, y=(y0 + y1) / 2, area=area, mass=area,
                            bbox=(float(x0), float(y0), float(x1), float(y1))))
    if not valid:
        return FgFrame(None, 0.0, 0.0, ds, valid=False, reason="plate camera")
    return FgFrame(m, 0.0, 0.0, ds, blobs=blobs)


DANCER = (180.0, 300.0, 220.0, 400.0)          # a 100 px dancer (x0, y0, x1, y1)


def _box(r, conf):
    return (r[0], r[1], r[2], r[3], conf)


# ------------------------------------------------------------------------------------------------- candidates
def test_candidates_stay_under_the_ceiling_none_first():
    c = candidates((1.21, 2.5))
    assert c[0] == NONE and Enh(True, 1.21, 2.5) in c and len(c) <= 4
    assert all(e.gamma <= 1.21 + 1e-9 and e.clahe <= 2.5 for e in c if e.enabled)
    assert Enh(True, 1.0, 2.5) in c                      # the ceiling's CLAHE at neutral gamma
    assert candidates((0.8, 1.0)) == [NONE, Enh(True, 0.8, 1.0)]
    assert candidates(None) == []                        # no clean empty-wall check: nothing tried


# --------------------------------------------------------------------------------------- one frame, no keypoints
def test_observe_takes_boxes_and_groups_with_no_keypoint_gate():
    # a box (no keypoints at all: boxes carry none) over her group, a ghost box on no foreground, and a dark
    # dancer no box covers (her group alone)
    dark = (60.0, 500.0, 90.0, 590.0)
    fg = _fg([DANCER, dark])
    obs = observe([_box(DANCER, 0.4), _box((300, 100, 330, 160), 0.5), _box(DANCER, 0.05)], fg, conf=0.15,
                  lb_scale=1.08)
    assert obs.fg_valid and obs.loose == 1               # the box on nobody does not count
    heights = sorted((round(p.height), p.box is not None) for p in obs.persons)
    assert heights == [(90, False), (100, True)]         # the box's height, and the group's for the dark one
    assert obs.apart() == 2


def test_without_a_valid_plate_every_box_counts():
    obs = observe([_box(DANCER, 0.4)], _fg([], valid=False), conf=0.15, lb_scale=1.0)
    assert not obs.fg_valid and len(obs.persons) == 1 and obs.persons[0].height == 100


def test_excluded_cells_drop_boxes_and_groups():
    fg = _fg([DANCER])
    obs = observe([_box(DANCER, 0.4)], fg, conf=0.15, zone_ok=lambda x, y: x > 300)
    assert obs.persons == []


# ------------------------------------------------------------------------------------------ the step's verdicts
def _run_step(frame_of, cands=(), frames=200):
    st = DancerStep(list(cands), frames=frames, ceiling=(1.21, 2.5) if cands else None)
    cur = st.current()
    for i in range(frames):
        st.feed(frame_of(i, cur))
        cur = st.current() or cur
    return st.result()


def test_the_step_measures_a_dancer_too_dark_for_keypoints_from_boxes_and_groups():
    # weak boxes (conf 0.2: no keypoint would pass release 1's 0.5 gate) in half the frames, her group always
    def frame(i, enh):
        boxes = [_box(DANCER, 0.2)] if i % 2 == 0 else []
        return observe(boxes, _fg([DANCER]), conf=0.15, lb_scale=1.0841)
    r = _run_step(frame)
    assert r.people == 1 and r.height_px == pytest.approx(100.0)
    assert r.n_box == 100 and r.n_group == 200
    assert r.net_px == pytest.approx(108.4, abs=0.1)
    assert r.chosen is None and "no clean empty-wall check" in r.why


def test_a_group_alone_counts_in_box_units():
    # a silhouette's group runs smaller than the box around the same body (08/10: ~88 against ~101 px): a frame
    # where the detector misses her must not pull her height down to the group's
    grp = (182.0, 312.0, 218.0, 400.0)                   # 88 px
    r = _run_step(lambda i, e: observe([_box(DANCER, 0.4)] if i % 2 else [], _fg([grp]), conf=0.15, lb_scale=1.0))
    assert r.group_scale == pytest.approx(100 / 88) and r.height_px == pytest.approx(100.0)
    band = DancerBand()
    for i in range(40):
        band.feed(i * 0.05, observe([_box(DANCER, 0.4)] if i % 2 else [], _fg([grp]), conf=0.15, lb_scale=1.0))
    assert band.median("box") == pytest.approx(100.0) and band.median("group") == pytest.approx(88.0)
    assert band.median() == pytest.approx(100.0)
    never = DancerBand()                                  # never detected: the group as it is (reads small)
    for i in range(40):
        never.feed(i * 0.05, observe([], _fg([grp]), conf=0.15, lb_scale=1.0))
    assert never.median() == pytest.approx(88.0)


def test_a_broad_difference_from_the_plate_is_no_body():
    # 08/10 mur 2 with a plate of another exposure: a 500 px region differs from the plate beside her 100 px box;
    # it is neither her height nor a second person
    broad = (20.0, 100.0, 160.0, 600.0)
    r = _run_step(lambda i, e: observe([_box(DANCER, 0.5)], _fg([DANCER, broad]), conf=0.15, lb_scale=1.0))
    assert r.people == 1 and r.height_px == pytest.approx(100.0)
    band = DancerBand()
    for i in range(40):
        band.feed(i * 0.05, observe([_box(DANCER, 0.5)], _fg([DANCER, broad]), conf=0.15, lb_scale=1.0))
    assert band.median() == pytest.approx(100.0)


def test_nobody_in_view_is_said_and_sets_nothing():
    r = _run_step(lambda i, e: observe([], _fg([]), conf=0.15, lb_scale=1.0), cands=candidates((1.21, 2.5)))
    assert r.nobody and r.height_px is None and r.chosen is None


def test_groups_the_detector_never_confirms_set_nothing():
    # 08/10 mur2 4's first 10 s: she hangs at the top edge, a 30 px sliver in the foreground, 4 boxes in 200
    # frames -- no height from groups alone without the boxes' yardstick; the step says the detector misses her
    sliver = (180.0, 0.0, 220.0, 30.0)
    r = _run_step(lambda i, e: observe([_box(DANCER, 0.3)] if i % 50 == 0 else [],
                                       _fg([sliver, DANCER] if i % 50 == 0 else [sliver]), conf=0.15,
                                       lb_scale=1.0), cands=candidates((1.21, 2.5)))
    assert r.nobody and r.unseen and r.height_px is None and r.chosen is None
    assert r.n_box == 4 and "detector saw her in 4 of 200" in r.why
    # with no foreground at all it is plain nobody
    r = _run_step(lambda i, e: observe([], _fg([]), conf=0.15, lb_scale=1.0), cands=candidates((1.21, 2.5)))
    assert r.nobody and not r.unseen


def test_two_people_are_said_and_set_nothing():
    # the step is for ONE dancer alone: with two people her size and the enhancement chosen on her would stand
    # on both (08/10 mur 4, mur2 5-7: two dancers in every frame) -- it says so and sets nothing
    other = (40.0, 600.0, 80.0, 700.0)
    r = _run_step(lambda i, e: observe([_box(DANCER, 0.5), _box(other, 0.5)], _fg([DANCER, other]), conf=0.15,
                                       lb_scale=1.0), cands=candidates((1.21, 2.5)))
    assert r.people == 2 and r.sets_nothing and not r.nobody
    assert r.height_px is None and r.chosen is None and r.net_px is None
    assert r.two == pytest.approx(1.0) and "two people on the wall" in r.why
    # one dancer alone is measured
    r = _run_step(lambda i, e: observe([_box(DANCER, 0.5)], _fg([DANCER]), conf=0.15, lb_scale=1.0))
    assert r.people == 1 and not r.sets_nothing and r.height_px == pytest.approx(100.0)


def _conf_by_enh(table, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)

    def frame(i, enh):
        c = table[enh.key()] + (rng.normal(0, noise) if noise else 0.0)
        boxes = [_box(DANCER, c)] if c >= 0.15 else []
        return observe(boxes, _fg([DANCER]), conf=0.15, lb_scale=1.0)
    return frame


def test_the_enhancement_that_helps_her_most_is_chosen():
    cands = candidates((1.21, 2.5))
    table = {e.key(): v for e, v in zip(cands, (0.30, 0.50, 0.42, 0.36))}
    r = _run_step(_conf_by_enh(table, noise=0.03), cands)
    assert r.chosen == Enh(True, 1.21, 2.5)
    s_none, s_c = r.stat(NONE), r.stat(r.chosen)
    assert s_c.mean_conf > s_none.mean_conf and s_c.det >= s_none.det
    assert all(s.frames >= 30 for s in r.stats)          # every candidate saw her through the 10 s


def test_never_one_worse_than_none():
    cands = candidates((1.21, 2.5))
    worse = {e.key(): (0.45 if not e.enabled else 0.30) for e in cands}
    assert _run_step(_conf_by_enh(worse, noise=0.03), cands).chosen == NONE
    same = {e.key(): 0.40 for e in cands}                # no real gain: noise alone never wins
    assert _run_step(_conf_by_enh(same, noise=0.05, seed=3), cands).chosen == NONE


def test_a_setting_with_more_confidence_but_fewer_detections_loses():
    cands = [NONE, Enh(True, 1.21, 2.5)]

    def frame(i, enh):
        if enh.enabled:                                  # stronger boxes, but she vanishes one frame in three
            boxes = [] if i % 3 == 0 else [_box(DANCER, 0.6)]
        else:
            boxes = [_box(DANCER, 0.35)]
        return observe(boxes, _fg([DANCER]), conf=0.15, lb_scale=1.0)
    r = _run_step(frame, cands)
    assert r.chosen == NONE


def test_choose_needs_enough_frames_of_her():
    st = DancerStep([NONE, Enh(True, 1.2, 2.5)], frames=8)
    for _ in range(8):
        st.feed(observe([_box(DANCER, 0.4)], _fg([DANCER]), conf=0.15))
    assert choose(st.result().stats)[0] is None


def test_the_candidates_interleave_in_blocks_through_the_10_s():
    cands = candidates((1.21, 2.5))
    st = DancerStep(cands, frames=200)
    seen = []
    for _ in range(200):
        seen.append(st.current())
        st.feed(observe([], None, conf=0.15))
    assert seen[:4] == [cands[0]] * 4 and seen[4] == cands[1]
    first_half = set(seen[:100]) & set(seen[100:])
    assert first_half == set(cands)                      # each one early and late: the same poses for all


# ------------------------------------------------------------------------------------------------- belt glare
def test_belt_glare_counts_her_white_band_not_the_clipped_wall():
    g = np.full((H, W), 120, np.uint8)
    g[300:400, 180:220] = 60                             # her body, darker than the wall
    g[340:356, 176:224] = 255                            # the belt at white, a band across her
    share = glare_share(g, DANCER)
    assert 0.1 < share < 0.3
    wall = np.full((H, W), 255, np.uint8)                # the clipped oval behind her
    wall[300:400, 180:220] = 200
    assert glare_share(wall, DANCER) == pytest.approx(0.0)


def test_glare_that_blinds_the_detector_is_said():
    def frame(i, enh):
        g = np.full((H, W), 120, np.uint8)
        g[300:400, 180:220] = 60
        blinded = i % 2 == 0
        if blinded:
            g[330:370, 176:224] = 255                    # a bloom over her middle
        boxes = [] if blinded and i % 4 == 0 or blinded and i % 4 == 2 else [_box(DANCER, 0.4)]
        return observe(boxes, _fg([DANCER]), conf=0.15, lb_scale=1.0, gray=g)
    r = _run_step(frame)
    assert r.glare.blinds and "BLINDS" in r.glare.line()
    ok = _run_step(lambda i, e: observe([_box(DANCER, 0.4)], _fg([DANCER]), conf=0.15,
                                        gray=np.full((H, W), 120, np.uint8)))
    assert not ok.glare.blinds and ok.glare.line() == "belt glare: fine"


# ---------------------------------------------------------------------------------------------------- the band
def _band_of(px, boxes=True, n=40, scale=1.0):
    band = DancerBand()
    r = (200.0, 300.0, 230.0, 300.0 + px)
    for i in range(n):
        obs = observe([_box(r, 0.4)] if boxes else [], _fg([r]), conf=0.15, lb_scale=scale)
        band.feed(i * 0.05, obs)
    return band


def test_the_band_thresholds():
    assert BAND_WARN_PX == 110.0 and BAND_NOTE_PX == 200.0
    small = _band_of(104, scale=0.7228).state(where="1280", larger="yolo_shape 1920x1056 gives ~113 px")
    assert small.state == "warn" and small.net_px == pytest.approx(75.2, abs=0.3)
    assert "too small for this input - use the larger one (yolo_shape 1920x1056" in small.text
    ok = _band_of(104, scale=1.0841).state(where="1920x1056")
    assert ok.state == "ok" and ok.net_px == pytest.approx(112.7, abs=0.3)
    big = _band_of(210).state()
    assert big.state == "note" and "a smaller input would do" in big.text
    assert DancerBand().state().state == "skip"          # nobody seen: not measured, never green


def test_the_band_counts_a_dancer_no_box_covers():
    groups_only = _band_of(104, boxes=False, scale=0.7228)
    assert groups_only.median("box") is None and groups_only.state().state == "warn"


def test_the_band_forgets_after_its_window():
    band = _band_of(104, scale=1.0841)
    assert band.state(now=1.0).state == "ok"
    assert band.state(now=60.0).state == "skip"


def test_larger_input_names_the_rectangle_from_the_square():
    s = larger_input(965, 1771, 1280, None, 75.0)
    assert s.startswith("yolo_shape 1920x1056") and "~112 px" in s
    assert larger_input(965, 1771, 1280, (1920, 1056), 112.0) == ""      # already the largest
    assert larger_input(0, 0, 1280, None, 75.0) == ""


# ---------------------------------------------------------------------------------------------- the app's flow
class _Enhancer:
    def __init__(self, gamma, clahe):
        self.gamma, self.clahe_clip = float(gamma), float(clahe)

    def _update_gamma_lut(self):
        pass

    def _update_clahe(self):
        pass


class _Proc:
    """What the step reads after each processed frame; ``render`` plays the pipeline for the current setting."""

    def __init__(self, scene):
        self.scene = scene                 # (settings, enhancer) -> (boxes, groups)
        self.last_raw_boxes, self.last_fg, self.last_lb_scale = [], None, 1.0841
        self.last_raw_dets = []

    def render(self, settings, enhancer):
        boxes, groups = self.scene(settings, enhancer)
        self.last_raw_boxes = list(boxes)
        self.last_fg = _fg(groups)

    def cancel_exclusion_calibration(self):
        pass


def _flows(scene, ceiling=(1.21, 2.5), enabled=False, playing=True):
    frame = np.full((H, W, 3), 120, np.uint8)
    proc = _Proc(scene)
    settings = SimpleNamespace(confidence=0.15, roi_enabled=True, imgsz=1280, person_height_px=150,
                               enhance_enabled=enabled, yolo_shape=(1920, 1056))
    flows = CalibrationFlows(
        processor=proc, enhancer=_Enhancer(0.8, 1.0), tracker=MagicMock(), settings=settings,
        recorder=SimpleNamespace(is_playing=playing), camera=SimpleNamespace(state=SimpleNamespace(is_open=False)),
        unified_camera=None, use_unified=False, models=SimpleNamespace(_model_loaded=True, model=object()),
        cameras=SimpleNamespace(), configs=MagicMock(), ui=MagicMock(available=True),
        last_raw_frame=lambda: frame, roi_source_size=lambda: (W, H), get_effective_roi=lambda w, h: (0, 0, W, H),
        reset_sensitivity_anchor=lambda **k: None, sync_mask_ui=lambda: None, request_reprocess=lambda: None,
        imgsz_change=lambda v: None)
    flows.calibration_state = {"gamma": {"source": "aim", "ts": "2026-12-12 19:40", "epoch": 1.0}}
    if ceiling is not None:
        flows.calibration_state["enhancement_ceiling"] = {"source": "aim", "ts": "2026-12-12 19:41", "epoch": 2.0,
                                                          "gamma": ceiling[0], "clahe": ceiling[1],
                                                          "kept_current": False}
    return flows, proc, settings


def _main_loop(flows, proc, settings, frames=400):
    """What the main loop does while ``_calibrating``: process a frame (detection forced on), then step."""
    n = 0
    while flows._calibrating and n < frames:
        proc.render(settings, flows.enhancer)
        flows._step_calibration([], 50.0)
        n += 1
    return n


def _helps(settings, enhancer):
    """A dim dancer: the stronger the (allowed) enhancement, the surer the box."""
    c = 0.30 if not settings.enhance_enabled else 0.30 + 0.8 * (enhancer.gamma - 1.0) + 0.02 * enhancer.clahe_clip
    return [_box(DANCER, c)], [DANCER]


def test_the_step_runs_without_run_and_turns_the_enhancement_on():
    flows, proc, settings = _flows(_helps)
    state0 = copy.deepcopy(flows.calibration_state)
    flows._cb_dancer_step()
    assert flows._calibrating and flows.dancer_busy       # detection forced on, as Calibrate does: no RUN needed
    assert _main_loop(flows, proc, settings) == 200
    assert not flows._calibrating and flows.dancer_busy   # the card waits
    assert settings.enhance_enabled is True               # ON: the « run Calibrate » label is no longer a lie
    assert (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.21, 2.5)
    assert settings.person_height_px == 100
    flows.ui.sync_checkbox.assert_any_call("enhance", True)
    flows.ui.sync_slider.assert_any_call("person_height", 100)
    line, details = flows.ui.show_dancer_card.call_args[0]
    assert "person height 100 px (~108 px at the network, 1920x1056)" in line
    assert "enhancement ON, gamma 1.21 / CLAHE 2.5" in line and "by hand" in line.lower()
    assert "Advanced > Detection > Person Height" in line
    st = flows.calibration_state["dancer_step"]
    assert st["height_px"] == 100.0 and st["net_px"] == pytest.approx(108.4, abs=0.1) and st["enh"] == "1.21/2.5"
    assert flows.calibration_state["gamma"]["source"] == "dancers" and state0["gamma"]["source"] == "aim"


def test_cancel_at_the_card_puts_everything_back():
    flows, proc, settings = _flows(_helps)
    state0 = copy.deepcopy(flows.calibration_state)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    flows._cb_dancer_decision(False)
    assert settings.enhance_enabled is False and (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (0.8, 1.0)
    assert settings.person_height_px == 150 and flows.calibration_state == state0
    assert not flows.dancer_busy and flows.dancer_result is None
    flows.configs._cb_save_config.assert_not_called()


def test_a_second_press_cancels_while_it_measures():
    flows, proc, settings = _flows(_helps)
    flows._cb_dancer_step()
    for _ in range(30):
        proc.render(settings, flows.enhancer)
        flows._step_calibration([], 50.0)
    flows._cb_dancer_step()                               # the same button, or CALIBRATE showing CANCEL
    assert not flows._calibrating and not flows.dancer_busy
    assert settings.enhance_enabled is False and (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (0.8, 1.0)
    flows._cb_dancer_step()
    flows._cb_calibrate()                                 # CALIBRATE's CANCEL ends the dancer step too
    assert not flows._calibrating and flows._dancer is None and flows._snapshot is None


def test_keep_saves_and_records_the_result():
    flows, proc, settings = _flows(_helps)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    flows._cb_dancer_decision(True)
    flows.configs._cb_save_config.assert_called_once()
    assert flows.dancer_result is not None and not flows.dancer_busy
    assert settings.enhance_enabled is True and settings.person_height_px == 100


def test_nobody_in_view_sets_nothing_and_says_so():
    flows, proc, settings = _flows(lambda s, e: ([], []))
    state0 = copy.deepcopy(flows.calibration_state)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    assert not flows._calibrating and not flows.dancer_busy
    assert settings.person_height_px == 150 and settings.enhance_enabled is False
    assert flows.calibration_state == state0
    flows.ui.show_dancer_card.assert_not_called()
    toast = flows.ui.show_toast.call_args[0][0]
    assert "nobody seen on the wall - nothing set" in toast


def test_someone_the_detector_misses_sets_nothing_and_says_so():
    flows, proc, settings = _flows(lambda s, e: ([], [(180.0, 0.0, 220.0, 30.0)]))
    state0 = copy.deepcopy(flows.calibration_state)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    assert not flows._calibrating and not flows.dancer_busy
    assert settings.person_height_px == 150 and settings.enhance_enabled is False
    assert flows.calibration_state == state0
    flows.ui.show_dancer_card.assert_not_called()
    toast = flows.ui.show_toast.call_args[0][0]
    assert "the detector saw her in 0 of 200 frames - nothing set" in toast


def test_two_people_set_nothing_and_say_so():
    other = (40.0, 600.0, 80.0, 700.0)
    flows, proc, settings = _flows(lambda s, e: ([_box(DANCER, 0.4), _box(other, 0.4)], [DANCER, other]))
    state0 = copy.deepcopy(flows.calibration_state)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    assert not flows._calibrating and not flows.dancer_busy
    assert settings.person_height_px == 150 and settings.enhance_enabled is False
    assert flows.calibration_state == state0
    flows.ui.show_dancer_card.assert_not_called()
    toast = flows.ui.show_toast.call_args[0][0]
    assert "two people on the wall - nothing set" in toast and "ONE dancer alone" in toast


def test_none_helps_her_the_enhancement_goes_off_by_choice():
    hurts = lambda s, e: ([_box(DANCER, 0.45 if not s.enhance_enabled else 0.25)], [DANCER])
    flows, proc, settings = _flows(hurts, enabled=True)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    assert settings.enhance_enabled is False and (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (1.0, 1.0)
    assert flows.calibration_state["dancer_step"]["enh"] == "none"
    flows.ui.sync_checkbox.assert_any_call("enhance_none_chosen", True)
    assert "enhancement off (none helps her)" in flows.ui.show_dancer_card.call_args[0][0]
    from core.ops_monitor import check_enhancement
    r = check_enhancement(enabled=False, gamma=1.0, clahe=1.0, calibrated=flows.calibration_state["gamma"]["ts"])
    assert r.status == "ok"                               # readiness: off is the step's choice, not a fault


def test_without_a_ceiling_only_the_running_setting_is_judged_never_raised():
    flows, proc, settings = _flows(_helps, ceiling=None, enabled=True)
    flows.enhancer.gamma = 1.3
    flows._cb_dancer_step()
    assert flows._dancer.cands == [NONE, Enh(True, 1.3, 1.0)]
    _main_loop(flows, proc, settings)
    assert settings.enhance_enabled is True and flows.enhancer.gamma == 1.3     # it helps her: kept, not raised
    assert "(the one running: no clean empty-wall check" in flows.ui.show_dancer_card.call_args[0][0]
    assert settings.person_height_px == 100
    hurts = lambda s, e: ([_box(DANCER, 0.45 if not s.enhance_enabled else 0.25)], [DANCER])
    flows, proc, settings = _flows(hurts, ceiling=None, enabled=True)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    assert settings.enhance_enabled is False                                     # it hurts her: switched off


def test_without_a_ceiling_and_the_enhancement_off_it_is_left_as_it_was():
    flows, proc, settings = _flows(_helps, ceiling=None, enabled=False)
    flows._cb_dancer_step()
    assert flows._dancer.cands == []
    _main_loop(flows, proc, settings)
    assert settings.enhance_enabled is False and flows.enhancer.gamma == 0.8
    assert "enhancement unchanged (no clean empty-wall check" in flows.ui.show_dancer_card.call_args[0][0]
    assert settings.person_height_px == 100


def test_the_hook_gets_the_result_when_the_step_finishes():
    flows, proc, settings = _flows(_helps)
    got = []
    flows.on_dancer_step_done = got.append
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    assert len(got) == 1 and got[0].height_px == pytest.approx(100.0)
    flows.on_dancer_step_done = lambda r: 1 / 0          # a failing hook never undoes the step
    flows._cb_dancer_decision(True)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    assert flows.dancer_busy


def test_the_step_never_sets_run_or_standby():
    flows, proc, settings = _flows(_helps)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    flows._cb_dancer_decision(True)
    assert not any("state" in str(c).lower() and "SetState" in str(c) for c in flows.ui.mock_calls)


def test_the_step_refuses_while_calibrate_runs():
    flows, proc, settings = _flows(_helps)
    flows._calibrating = True
    flows._cb_dancer_step()
    assert flows._dancer is None and "Finish or cancel" in flows.ui.show_toast.call_args[0][0]


# ------------------------------------------------------------------------------------------ readiness and app
def _app_mod():
    pytest.importorskip("torch")
    import app
    return app


def test_readiness_waits_while_the_step_runs_and_its_card_is_open():
    from runtime import api
    A = _app_mod().WallDanceApp
    reports = []
    cal = SimpleNamespace(_calibrating=False, _calibrating2=False, dancer_busy=False)
    app = SimpleNamespace(
        bus=api.EventBus(), system_state=api.SystemState.RUN, calibration=cal,
        models=SimpleNamespace(_model_loaded=True, _model_loading=False),
        processor=SimpleNamespace(_plate_capture=None), configs=SimpleNamespace(_pending_project_switch=None),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=True)),
        cameras=SimpleNamespace(_camera_reconnecting=False), recorder=SimpleNamespace(is_playing=False))
    for name in ("_readiness_auto", "_readiness_calibrate_ended", "_readiness_note_toast"):
        setattr(app, name, getattr(A, name).__get__(app))
    app._cb_check_readiness = lambda why="": reports.append(why)
    app.bus.ui_ready = True

    def run(t0, t1):
        t = t0
        while t < t1:
            A._readiness_auto_tick(app, t)
            t += 1 / 30
    run(0, 10)
    assert reports == ["start"]
    cal._calibrating = cal.dancer_busy = True              # the step measures
    run(10, 20)
    cal._calibrating = False                               # measured; its card is open
    run(20, 60)
    assert reports == ["start"]                            # nothing while it runs or waits for Keep / Cancel
    cal.dancer_busy = False
    run(60, 70)
    assert reports == ["start", "after Calibrate"]
    assert app.system_state == api.SystemState.RUN


def test_the_dancer_size_row_is_ok_only_once_the_step_measured_her():
    from core.ops_monitor import check_dancer_size
    A = _app_mod().WallDanceApp
    stub = SimpleNamespace(calibration=SimpleNamespace(calibration_state={}),
                           settings=SimpleNamespace(imgsz=1280, yolo_shape=(1920, 1056)),
                           processor=SimpleNamespace(last_lb_scale=0.0, dancer_height=(120.0, 300, 1e12)))
    measured, lb, age = A._dancer_step_measure(stub, 965, 1771)
    assert measured is None and lb == pytest.approx(1.0841, abs=1e-4)
    row = check_dancer_size(measured=measured, lb_scale=lb, imgsz=1280, shape=(1920, 1056), max_age_s=age,
                            not_measured="not measured - the dancer step")
    assert row.status == "skip" and "dancer step" in row.detail      # not the skeleton measure: never green
    import time
    stub.calibration.calibration_state = {"dancer_step": {"source": "dancers", "ts": "x", "epoch": time.time(),
                                                          "height_px": 104.0, "samples": 380}}
    measured, lb, age = A._dancer_step_measure(stub, 965, 1771)
    row = check_dancer_size(measured=measured, lb_scale=lb, imgsz=1280, shape=(1920, 1056), max_age_s=age)
    assert row.status == "ok" and "~113 px" in row.detail
    stub.settings.yolo_shape = None                       # the 1280 square: the same dancer warns
    measured, lb, age = A._dancer_step_measure(stub, 965, 1771)
    assert check_dancer_size(measured=measured, lb_scale=lb, imgsz=1280, max_age_s=age, roi_w=965,
                             roi_h=1771).status == "warn"


def test_the_band_tick_publishes_its_state_on_the_bus():
    from runtime import api
    A = _app_mod().WallDanceApp
    events = []
    bus = api.EventBus()
    bus.subscribe(events.append)
    r = (200.0, 300.0, 230.0, 404.0)
    proc = SimpleNamespace(last_raw_boxes=None, last_fg=_fg([r]), last_lb_scale=0.7228)
    stub = SimpleNamespace(processor=proc, bus=bus,
                           settings=SimpleNamespace(confidence=0.15, imgsz=1280, yolo_shape=None),
                           roi=SimpleNamespace(state=SimpleNamespace(stored_roi=lambda: (135, 3, 965, 1771, 1296, 1776))))
    for i in range(60):
        proc.last_raw_boxes = [_box(r, 0.4)]               # a new list: a newly processed frame
        A._dancer_band_tick(stub, now=i * 0.05)
    bands = [e for e in events if isinstance(e, api.DancerBand)]
    assert bands and bands[-1].state == "warn"
    assert "too small for this input - use the larger one (yolo_shape 1920x1056" in bands[-1].text


# ------------------------------------------------------------------------------ review 26/11: the four findings
def test_glare_on_her_in_nearly_every_frame_is_judged_against_every_frame():
    """With fewer than 10 frames free of the bloom there was nothing to compare against, and the card said « the
    detector still sees her » whatever it saw: she must be seen in every frame, standing in full view."""
    from core.dancer_step import _glare
    g = _glare([(0.12, i % 5 < 2) for i in range(200)])                 # bloom in all 200, seen in 40 %
    assert g.blinds and not g.compared
    assert g.line().startswith("belt glare BLINDS the detector on her") and "seen in 40 % of those only" in g.line()
    g = _glare([(0.12, i % 10 < 3) for i in range(191)] + [(0.0, True)] * 9)    # 9 frames free of it
    assert g.blinds
    g = _glare([(0.12, True)] * 199)                                    # 08/10 mur 3: bloom always, seen always
    assert not g.blinds
    assert g.line() == "belt glare on her in 100 % of the frames, the detector still sees her in 100 % of those"
    g = _glare([(0.12, i % 10 < 3) for i in range(150)] + [(0.0, i % 10 < 3) for i in range(50)])
    assert g.compared and not g.blinds                                  # as dark without it: not the belt


def _band_held(band, t, px, secs=11.0):
    """Feed ``secs`` of a dancer ``px`` tall at the network; (the state once a second, the time)."""
    r, states = (200.0, 300.0, 230.0, 300.0 + px), []
    for i in range(int(secs * 20)):
        band.feed(t, observe([_box(r, 0.4)], _fg([r]), conf=0.15, lb_scale=1.0))
        t += 0.05
        if i % 20 == 19:
            states.append(band.state(now=t).state)
    return states, t


def test_the_band_holds_its_state_at_the_operating_point():
    """The 10 s median of a dancer at ~105-112 px (1920 x 1056, 08/10) flipped orange / dim every second, and was
    orange where the readiness row (100 px) said ok: orange from under 100 px, back to ok only at 110."""
    band, t, seen = DancerBand(), 0.0, []
    for px in (104, 112, 107, 111, 106, 109) * 10:                      # a minute, the median wandering ~108
        s, t = _band_held(band, t, px, secs=1.0)
        seen += s
    assert set(seen[10:]) == {"ok"}
    s, t = _band_held(band, t, 95)
    assert s[-1] == "warn"                                              # under the readiness row's 100 px
    s, t = _band_held(band, t, 105)
    assert set(s) == {"warn"}                                           # stays orange until 110 ...
    s, t = _band_held(band, t, 112)
    assert s[-1] == "ok"                                                # ... and back
    s, t = _band_held(band, t, 105)
    assert set(s) == {"ok"}


def _calibrate_flows(flows):
    flows.recorder = SimpleNamespace(is_playing=True, playback_camera=None)
    flows.wall_check_enabled = True
    flows.processor.get_motion_var_threshold = lambda: 16.0
    flows.processor.get_motion_scale = lambda: 0.5
    return flows


def _run_wall_check(flows, ghost=lambda g, c: 0.0):
    for _ in range(600):
        if flows._wall_check is None:
            break
        c = ghost(flows.enhancer.gamma, flows.enhancer.clahe_clip)
        flows.processor.last_raw_dets = [(c, 300.0, 400.0, 150.0)] if c > 0 else []
        flows._step_calibration([], 30.0)


def test_a_step_that_chose_none_never_lowers_the_next_calibrate():
    """The step's choice became Calibrate's « keep »: after none (1.0 / 1.0, off) the next empty-wall check -- run
    on a picture the enhancement no longer touched -- kept 1.0 / 1.0 as the ceiling, and the step could never
    offer more than neutral again.  Now the check tries the step's own ceiling first, with the enhancement on."""
    hurts = lambda s, e: ([_box(DANCER, 0.45 if not s.enhance_enabled else 0.25)], [DANCER])
    flows, proc, settings = _flows(hurts, enabled=True)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    flows._cb_dancer_decision(True)
    assert settings.enhance_enabled is False and flows.calibration_state["dancer_step"]["enh"] == "none"
    _calibrate_flows(flows)._cb_calibrate()
    assert flows._pre_enhancement == (1.21, 2.5)                        # not (1.0, 1.0)
    flows._cb_calibrate()                                               # (cancel: only the keep was wanted)
    flows._pre_enhancement = flows._dancer_keep((1.0, 1.0))
    assert flows._start_wall_check() and settings.enhance_enabled is True
    assert flows._wall_check.current() == (1.21, 2.5)
    _run_wall_check(flows)
    assert flows._wall_result.ceiling == (1.21, 2.5)
    assert settings.enhance_enabled is True                             # on at the new ceiling: the step decides
    assert flows.calibration_state["dancer_step"]["enh"] is None
    assert candidates(flows._wall_result.ceiling)[1] == Enh(True, 1.21, 2.5)


def test_a_step_that_chose_a_rung_keeps_the_ceiling_for_the_next_calibrate():
    flows, proc, settings = _flows(_helps)
    flows._dancer_set_enabled(True)
    flows._cb_dancer_step()
    flows._dancer.cands = [NONE, Enh(True, 1.21, 2.5), Enh(True, 1.105, 2.5)]
    _main_loop(flows, proc, settings)
    flows._cb_dancer_decision(True)
    ds = flows.calibration_state["dancer_step"]
    ds["enh"] = "1.10/2.5"                                              # as if the middle rung had won
    assert flows._dancer_keep((1.105, 2.5)) == (1.21, 2.5)
    assert flows._dancer_keep((0.9, 2.0)) == (0.9, 2.0)                 # changed by hand since: as it is
    flows.calibration_state["gamma"]["epoch"] = ds["epoch"] + 1.0       # Calibrate set it since: as it is
    assert flows._dancer_keep((1.105, 2.5)) == (1.105, 2.5)


def test_the_empty_wall_check_runs_with_the_enhancement_on_and_puts_a_hand_off_back():
    flows, proc, settings = _flows(_helps, enabled=False)
    _calibrate_flows(flows)
    flows._pre_enhancement = None
    flows._snapshot = flows._take_snapshot()
    assert flows._start_wall_check() and settings.enhance_enabled is True
    _run_wall_check(flows)
    assert settings.enhance_enabled is False                            # off by hand: off again
    flows._snapshot = flows._take_snapshot()
    flows.calibration_state["dancer_step"] = {"enh": "none", "epoch": 3.0}
    assert flows._start_wall_check() and settings.enhance_enabled is True
    flows._end_wall_check(cancelled=True)
    flows._restore_snapshot()
    assert settings.enhance_enabled is False                            # a cancelled Calibrate puts it back


def test_a_project_switch_drops_the_step_and_its_card_putting_nothing_back():
    flows, proc, settings = _flows(_helps)
    flows._cb_dancer_step()
    for _ in range(80):
        proc.render(settings, flows.enhancer)
        flows._step_calibration([], 50.0)
    other = {"gamma": {"source": "aim", "ts": "2026-12-12 20:00", "epoch": 9.0}}
    flows.calibration_state = other                    # what app._apply_config_without_model does at a switch
    settings.person_height_px, settings.enhance_enabled = 140, True     # the other project's values
    proc.render(settings, flows.enhancer)
    flows._step_calibration([], 50.0)                  # its first frame never reaches the step
    assert not flows._calibrating and not flows.dancer_busy
    assert flows.calibration_state is other and "dancer_step" not in other
    assert settings.person_height_px == 140 and settings.enhance_enabled is True
    # the card open when the project changes: its Cancel puts none of the old project into the new one
    flows, proc, settings = _flows(_helps)
    flows._cb_dancer_step()
    _main_loop(flows, proc, settings)
    assert flows.dancer_busy
    other = {"gamma": {"source": "aim", "ts": "2026-12-12 20:00", "epoch": 9.0}}
    flows.calibration_state = other
    flows._cb_dancer_decision(False)
    assert flows.calibration_state is other and "dancer_step" not in other and not flows.dancer_busy
    flows.configs._cb_save_config.assert_not_called()
    # the same project name changing (Save As, a load) is caught on the main loop's tick
    flows, proc, settings = _flows(_helps)
    flows._cb_dancer_step()
    flows.configs._current_project = "another"
    flows.dancer_watch()
    assert not flows._calibrating and not flows.dancer_busy


def test_a_step_whose_frames_stop_ends():
    import time
    flows, proc, settings = _flows(_helps)
    flows._cb_dancer_step()
    for _ in range(120):
        proc.render(settings, flows.enhancer)
        flows._step_calibration([], 50.0)
    flows.dancer_watch(now=time.monotonic() + 1.0)
    assert flows._dancer is not None                                    # a short gap: it still measures
    flows.dancer_watch(now=time.monotonic() + 6.0)
    assert not flows._calibrating and flows._dancer is None and flows.dancer_busy    # decided on 120: the card
    assert settings.person_height_px == 100
    flows, proc, settings = _flows(_helps)
    flows._cb_dancer_step()                                             # a playback that never starts
    flows.dancer_watch(now=time.monotonic() + 6.0)
    assert not flows._calibrating and not flows.dancer_busy
    assert settings.enhance_enabled is False and (flows.enhancer.gamma, flows.enhancer.clahe_clip) == (0.8, 1.0)
    assert "no frames for 5 s" in flows.ui.show_toast.call_args[0][0]
