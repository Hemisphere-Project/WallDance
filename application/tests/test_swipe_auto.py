"""The autonomous swipe's policy (Plan Cordee P6): when it looks, when it only says, when it switches."""
from core.swipe import Candidate, SwipeResult, SwipeRow
from core.swipe_auto import MODE_AUTO, MODE_SUGGEST, AutoParams, SwipeAuto, parse_mode, seamless

X = Candidate("yolo11x-pose", 1280, (1920, 1056))
L = Candidate("yolo11l-pose", 1280, (1920, 1056))
X1280 = Candidate("yolo11x-pose", 1280)


def _result(winner, live, gain, noise=0.01, switch=True):
    rows = [SwipeRow(winner, 0.5 + gain, 0.0, 15.0, net=0.5 + gain, gain=gain, noise=noise),
            SwipeRow(live, 0.5, 0.0, 25.0, net=0.5)]
    return SwipeResult(live, winner, switch, rows, "said")


def test_modes_and_seamless():
    assert parse_mode("AUTO") == "auto" and parse_mode(None) == "off" and parse_mode("yes") == "off"
    assert seamless(X, L) and not seamless(X1280, L)


def test_it_never_looks_at_an_empty_wall_or_while_busy():
    a = SwipeAuto(MODE_AUTO)
    assert a.due(0.0, lost_fresh=0, covered_fresh=0) == "idle"          # hours of empty wall
    assert a.due(0.0, lost_fresh=30, covered_fresh=10, busy=True) == "idle"
    assert a.due(0.0, lost_fresh=30, covered_fresh=10) == "run"
    a.ran(10.0)
    assert a.due(60.0, 30, 10) == "idle" and a.due(131.0, 30, 10) == "run"
    assert SwipeAuto("off").due(0.0, 30, 10) == "idle"


def test_groups_without_any_live_box_get_a_rare_probe():
    a = SwipeAuto(MODE_SUGGEST)
    assert a.due(0.0, lost_fresh=30, covered_fresh=0) == "probe"
    a.ran(5.0, probe=True)
    assert a.due(200.0, 30, 0) == "idle" and a.due(306.0, 30, 0) == "probe"


def test_a_background_run_tries_only_the_live_input():
    assert SwipeAuto(MODE_AUTO).candidates([X1280, X, L], X) == [X, L]


def test_marginal_is_a_suggestion_definite_twice_is_a_switch():
    a = SwipeAuto(MODE_AUTO)
    adv = a.advise(0.0, _result(L, X, 0.05), newest_frame_t=100.0)
    assert (adv.kind, adv.level) == ("suggest", "marginal") and "Apply now" in adv.text
    adv = a.advise(130.0, _result(L, X, 0.20), newest_frame_t=230.0)
    assert (adv.kind, adv.level, adv.streak) == ("suggest", "definite", 1)      # a marginal run does not count
    a = SwipeAuto(MODE_AUTO)
    assert a.advise(0.0, _result(L, X, 0.20), 100.0).kind == "suggest"          # once is not enough
    assert a.advise(60.0, _result(L, X, 0.20), 100.0).kind == "suggest"         # the same frames again: no
    adv = a.advise(130.0, _result(L, X, 0.20), 230.0)
    assert adv.kind == "apply" and adv.seamless and adv.text.startswith("switching to l @ 1920 x 1056")


def test_what_keeps_a_definite_win_a_suggestion():
    a = SwipeAuto(MODE_SUGGEST)
    a.advise(0.0, _result(L, X, 0.20), 100.0)
    assert a.advise(130.0, _result(L, X, 0.20), 230.0).why_not_auto == "automatic switching is off"
    a = SwipeAuto(MODE_AUTO)                                                    # another input size
    a.advise(0.0, _result(L, X1280, 0.30), 100.0)
    adv = a.advise(130.0, _result(L, X1280, 0.30), 230.0)
    assert adv.kind == "suggest" and not adv.seamless and "input size" in adv.why_not_auto
    a = SwipeAuto(MODE_AUTO)                                                    # slower than the live setting
    a.advise(0.0, _result(X, L, 0.20), 100.0)
    adv = a.advise(130.0, _result(X, L, 0.20), 230.0, ms={X: 26.0, L: 15.0})
    assert adv.kind == "suggest" and "slower" in adv.why_not_auto
    a = SwipeAuto(MODE_AUTO, AutoParams(dwell_s=300, back_s=900))               # dwell, then no way back
    a.switched(1000.0, X, L)
    a.advise(1010.0, _result(X, L, 0.20), 1100.0)
    assert "moment ago" in a.advise(1140.0, _result(X, L, 0.20), 1230.0).why_not_auto
    a.advise(1400.0, _result(X, L, 0.20), 1390.0)
    assert "just left" in a.advise(1530.0, _result(X, L, 0.20), 1520.0).why_not_auto


def test_no_verdict_ends_the_streak():
    a = SwipeAuto(MODE_AUTO)
    a.advise(0.0, _result(L, X, 0.20), 100.0)
    assert a.advise(130.0, _result(X, X, 0.0, switch=False), 230.0).kind == "none"
    assert a.advise(260.0, _result(L, X, 0.20), 360.0).kind == "suggest"
