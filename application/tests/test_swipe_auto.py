"""The autonomous swipe's policy (Plan Cordee P6): when it looks, when it only says, when it switches."""
from types import SimpleNamespace

import pytest

from core.swipe import Candidate, FrameScore, SwipeParams, SwipeResult, SwipeRow
from core.swipe_auto import (MODE_AUTO, MODE_SUGGEST, AutoParams, SwipeAuto, bar_for, headroom, parse_mode,
                             referee_params, seamless)

X = Candidate("yolo11x-pose", 1280, (1920, 1056))
L = Candidate("yolo11l-pose", 1280, (1920, 1056))
X1280 = Candidate("yolo11x-pose", 1280)
L1280 = Candidate("yolo11l-pose", 1280)


def _result(winner, live, gain, noise=0.01, switch=True):
    rows = [SwipeRow(winner, 0.5 + gain, 0.0, 15.0, net=0.5 + gain, gain=gain, noise=noise),
            SwipeRow(live, 0.5, 0.0, 25.0, net=0.5)]
    return SwipeResult(live, winner, switch, rows, "said")


def _frames(*ts):
    return [SimpleNamespace(t=float(t)) for t in ts]


def test_modes_seamless_and_the_referees_confidence():
    assert parse_mode("AUTO") == "auto" and parse_mode(None) == "off" and parse_mode("yes") == "off"
    assert seamless(X, L) and not seamless(X1280, L)
    assert referee_params(0.30).conf == 0.30 and referee_params(0.10).conf == SwipeParams().conf


def test_an_empty_wall_costs_nothing_and_a_run_waits_for_its_frames():
    a = SwipeAuto(MODE_AUTO)
    assert a.due(0.0, lost_fresh=0, covered_fresh=0) == "idle"          # hours of empty wall: nothing kept
    assert a.due(0.0, lost_fresh=60, covered_fresh=89) == "idle"        # 149 frames with dancers: not yet
    assert a.due(0.0, lost_fresh=60, covered_fresh=90) == "run"         # 150
    assert a.due(0.0, lost_fresh=60, covered_fresh=90, busy=True) == "idle"
    assert SwipeAuto("off").due(0.0, 60, 90) == "idle"


def test_two_runs_never_share_a_frame():
    a = SwipeAuto(MODE_AUTO)
    kept = _frames(*range(10))
    assert len(a.fresh(kept)) == 10
    a.advise(20.0, _result(L, X, 0.02), newest_frame_t=6.0)
    assert [f.t for f in a.fresh(kept)] == [7.0, 8.0, 9.0]
    a.switched(30.0, X, L)                                              # a new setting: a new pool
    assert len(a.fresh(kept)) == 10


def test_the_free_prefilter_skips_a_run_that_cannot_clear_the_bar():
    p = SwipeParams()
    # the live setting covers 95 of 100 dancer groups and puts one box on nobody: at most 6 points to gain
    scores = [FrameScore(groups=2, covered=2)] * 45 + [FrameScore(groups=2, covered=1)] * 5 + [FrameScore(0, 0, 1, 1)]
    room = headroom(scores, p)
    assert abs(room - 6 / 100) < 1e-9
    assert abs(bar_for(L, X, p) - (0.03 + 0.05 * 0.6215)) < 1e-3                 # x from l: costlier, 6.1 points
    assert bar_for(X, L, p) == p.min_gain                                       # l from x: cheaper, 3 points
    a = SwipeAuto(MODE_AUTO)
    assert a.due(0.0, 60, 90, room=room - bar_for(L, X, p), newest_frame_t=150.0) == "idle"
    assert a.skipped == 1 and a.fresh(_frames(149, 151)) == _frames(151)        # those frames count as looked at
    assert a.due(0.0, 60, 90, room=room - bar_for(X, L, p)) == "run"
    assert headroom([FrameScore(groups=2, covered=0)] * 10, p) is None          # covers nobody: no bound


def test_a_blind_or_empty_live_setting_gets_a_rare_probe():
    a = SwipeAuto(MODE_SUGGEST)
    assert a.due(0.0, lost_fresh=150, covered_fresh=0) == "probe"
    a.ran(5.0, probe=True)
    assert a.due(200.0, 150, 0) == "idle" and a.due(306.0, 150, 0) == "probe"


def test_a_background_run_tries_only_the_live_input():
    assert SwipeAuto(MODE_AUTO).candidates([X1280, X, L], X) == [X, L]


def test_any_verdict_naming_another_setting_is_a_suggestion():
    a = SwipeAuto(MODE_SUGGEST)
    adv = a.advise(0.0, _result(L, X, 0.04), newest_frame_t=100.0)
    assert (adv.kind, adv.level) == ("suggest", "marginal") and adv.text.endswith("Apply now?") and adv.text.isascii()
    adv = a.advise(160.0, _result(L, X, 0.20), newest_frame_t=260.0)
    assert (adv.kind, adv.level, adv.why_not_auto) == ("suggest", "definite", "Auto: suggest does not switch by itself")
    assert a.advise(320.0, _result(X, X, 0.0, switch=False), 420.0).kind == "none"


def test_two_definite_verdicts_on_disjoint_frames_switch_the_seamless_pair():
    a = SwipeAuto(MODE_AUTO, AutoParams(definite_gain=0.05))
    adv = a.advise(0.0, _result(L, X, 0.04), 100.0)
    assert adv.kind == "suggest" and "under 5 points" in adv.why_not_auto            # too small to switch alone
    assert a.advise(160.0, _result(L, X, 0.20), 260.0).kind == "suggest"           # once is not enough
    assert a.advise(200.0, _result(L, X, 0.20), 260.0).kind == "suggest"           # the same frames again: no
    adv = a.advise(320.0, _result(L, X, 0.20), 420.0)
    assert adv.kind == "apply" and adv.seamless and adv.text.startswith("switching to l @ 1920 x 1056")


def test_another_input_size_is_never_automatic():
    a = SwipeAuto(MODE_AUTO)
    a.advise(0.0, _result(L, X1280, 0.30), 100.0)
    adv = a.advise(160.0, _result(L, X1280, 0.30), 260.0)
    assert adv.kind == "suggest" and not adv.seamless and "another input size" in adv.why_not_auto


def test_what_keeps_a_definite_win_a_suggestion():
    a = SwipeAuto(MODE_AUTO)                                                    # slower than the live setting
    a.advise(0.0, _result(X, L, 0.20), 100.0)
    adv = a.advise(160.0, _result(X, L, 0.20), 260.0, ms={X: 26.0, L: 15.0})
    assert adv.kind == "suggest" and "slower" in adv.why_not_auto
    a = SwipeAuto(MODE_AUTO, AutoParams(dwell_s=300, back_s=900))               # dwell, then no way back
    a.switched(1000.0, X1280, L1280)
    a.advise(1010.0, _result(X1280, L1280, 0.20), 1100.0)
    assert "moment ago" in a.advise(1170.0, _result(X1280, L1280, 0.20), 1260.0).why_not_auto
    a.advise(1400.0, _result(X1280, L1280, 0.20), 1390.0)
    assert "just left" in a.advise(1560.0, _result(X1280, L1280, 0.20), 1550.0).why_not_auto


def test_after_a_run_the_referee_could_not_judge_it_waits():
    a = SwipeAuto(MODE_AUTO, AutoParams(none_wait_s=900))
    a.advise(100.0, SwipeResult(X, None, False, [], "no verdict: every setting puts boxes ..."), 90.0)
    assert a.due(500.0, 60, 90) == "idle" and a.due(1001.0, 60, 90) == "run"
    a.reset()
    assert a.due(0.0, 60, 90) == "run"                                          # a new plate: look again


def _keep(live=X):
    return SwipeResult(live, live, False, [SwipeRow(live, 0.5, 0.0, 15.0, net=0.5)], "keep")


def test_a_settled_show_backs_off_after_each_keep():
    a = SwipeAuto(MODE_AUTO, AutoParams(keep_wait_s=300, keep_wait_max_s=1800))
    assert a.due(0.0, 45, 105) == "run"                                          # lost share 0.30
    a.advise(30.0, _keep(), 30.0)
    assert a.keep_wait() == 300 and a.due(200.0, 45, 105) == "idle" and a.due(331.0, 45, 105) == "run"
    for n, wait in ((2, 600), (3, 1200), (4, 1800), (5, 1800)):                  # doubled, up to the cap
        a.advise(1000.0 * n, _keep(), 1000.0 * n)
        assert a.keep_wait() == wait
    assert a.due(5000.0 + 1799, 45, 105) == "idle" and a.due(5000.0 + 1801, 45, 105) == "run"


def test_a_verdict_naming_another_setting_a_switch_or_a_reset_ends_the_back_off():
    a = SwipeAuto(MODE_SUGGEST)
    a.due(0.0, 45, 105)
    a.advise(30.0, _keep(), 30.0)
    assert a.due(100.0, 45, 105) == "idle"
    a.advise(110.0, _result(L, X, 0.04), 110.0)                                  # named: the next run at once
    assert a.keep_wait() == 0 and a.due(120.0, 45, 105) == "run"
    a.advise(150.0, _keep(), 150.0)
    a.switched(160.0, X, L)
    assert a.due(170.0, 45, 105) == "run"
    a.advise(200.0, _keep(L), 200.0)
    a.reset()
    assert a.due(210.0, 45, 105) == "run"


def test_a_rise_in_the_live_settings_lost_share_wakes_it():
    a = SwipeAuto(MODE_AUTO, AutoParams(wake_lost=0.10))
    a.due(0.0, 45, 105)                                                          # 0.30 on the first keep's frames
    a.advise(30.0, _keep(), 30.0)
    a.due(400.0, 60, 90)                                                         # 0.40 on the second's
    a.advise(430.0, _keep(), 430.0)
    assert a.keep_wait() == 600
    assert a.due(500.0, 74, 76) == "idle"                                        # 0.49: the dancing's own swing
    assert a.due(510.0, 75, 75) == "run" and a.woken == 1                         # 0.50: 10 points over 0.40
    a.advise(540.0, _keep(), 540.0)                                              # judged again: nothing better
    assert a.keep_wait() == 300 and a.due(600.0, 75, 75) == "idle"                # from the start; 0.50 the reference
    assert a.due(841.0, 75, 75) == "run"


@pytest.mark.parametrize("field", ["window_s", "min_lost", "definite_z", "every_s", "probe_every_s", "probe_frames",
                                   "definite_gain", "wins", "dwell_s", "back_s", "slower", "min_covered",
                                   "none_wait_s", "run_frames", "keep_wait_s", "keep_wait_max_s", "wake_lost"])
def test_the_parameters_the_live_code_may_pass_still_exist(field):
    assert hasattr(AutoParams(), field)
