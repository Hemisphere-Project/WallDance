"""P6 (Plan Cordee): when a background suggestion is shown, how it is said, and when it goes (the blind re-read of
5de60e5, lens b2: findings 1, 2, 3, 4, 5 and 9; group C as far as suggest reaches it).

1. a suggestion is earned like a switch: the same setting named on two looks in a row, in suggest and in auto alike
   (was: one verdict, then a new toast at every look);
2. « in a row » is bounded: 15 min of the loop clock at most between the two, no look skipped for lack of room and
   no empty pool (no dancer for the pool's age) between them (was: two verdicts 2 h apart switched);
3. the « slower » rule holds for a suggestion and fails closed: no time, no suggestion; the line gives both times;
4. one wording, whatever the gain: « ..., seen twice: Apply now? » (was: « clearly better » or « it looks
   better » by the 5-point bar);
5. said once: a look that names it again updates the line silently, and the looks back off while it stands;
6. a suggestion the looks no longer name goes after two of them in a row (was: at once on a « keep », never on a
   look without a verdict);
7. the readiness row names what the swipe line shows (was: the operator's verdict, under a background suggestion);
8. the last guard: no automatic switch unless the build releases mode auto.
CPU, with the fakes of ``test_swipe_auto_live`` and ``test_swipe_auto_rollback``."""
from types import SimpleNamespace

import pytest

from core.swipe import Candidate, SwipeResult, SwipeRow
from core.swipe_auto import MODE_AUTO, AutoParams, SwipeAuto
from test_seamless_switch import L1280, _Port, _live
from test_swipe_auto_live import (_bg, _look_frames, _looking_bg, swipe_auto_off_only,  # noqa: F401
                                  swipe_auto_suggest_only, swipe_auto_unlocked)
from test_swipe_auto_rollback import _JudgingRunner, _look, _Tap
from test_swipe_auto_live import swipe_auto_apply_released  # noqa: F401 - the fixture

TOAST = "A better model may be available - see 'Try other settings' (Apply now)"
LINE = "l @ 1280 covers 100 % of the dancers, x @ 1280 33 % (6.0 ms a frame against 6.0), seen twice: Apply now?"
WITHDRAWN = "suggestion withdrawn: the last looks did not confirm it"


def _events(bg):
    out = []
    bg.notify = bg.switch.notify = lambda kind, payload: out.append((kind, payload))
    return out


def _toasts(events):
    """The suggestions the app would toast: a « suggest » event that is not a look naming it again."""
    return [p for k, p in events if k == "suggest" and not p.get("again")]


def _evening_bg(mode="suggest"):
    """A looker whose wall clock follows the loop clock (the suggestion's age), the tap's frames 1 s apart."""
    live = {"v": _live()}
    loader = _JudgingRunner()
    holder = {}
    bg, proc, _ = _bg(live, loader=loader, port=_Port(), mode=mode, wall=lambda: 10_000.0 + holder["p"].clock)
    holder["p"] = proc
    bg.pace_s = 0.0
    proc.swipe_lost_share = lambda: 0.66
    return bg, proc, loader, live


# ----------------------------------------------------------------------------------- 1. earned like a switch
@pytest.mark.parametrize("mode", ["suggest", "auto"])
def test_one_verdict_shows_nothing_the_same_winner_twice_in_a_row_is_shown(mode, swipe_auto_unlocked):  # noqa: F811
    """Fails before: look 1 already put « Apply now » on the line, with a toast."""
    bg, proc, loader, live = _evening_bg(mode)
    if mode == "auto":
        bg.auto.policy._switched_at = 100.0               # a change by hand at 100 s: the dwell holds look 2 back
    events = _events(bg)
    tap = _Tap(range(1500))
    assert _look(bg, proc, tap, 150) == "run"
    assert bg.suggestion is None and _toasts(events) == [], "one verdict put a suggestion on screen"
    assert (bg.last_advice["advice"], bg.last_advice["why_not_auto"]) == ("seen", "seen once, it looks again")
    assert _look(bg, proc, tap, 300) == "run"             # the next look came at once: 150 new frames
    assert bg.suggestion is not None and len(_toasts(events)) == 1 and bg.switch._want is None


# ------------------------------------------------------------------------------------- 2. « in a row »
X = Candidate("yolo11x-pose", 1920, (1056, 1920))
L = Candidate("yolo11l-pose", 1920, (1056, 1920))
MS = {X: 15.0, L: 15.0}


def _verdict(gain, live=X, other=L):
    rows = [SwipeRow(other, 0.92 + gain, 0.0, None, gain=gain), SwipeRow(live, 0.92, 0.0, None)]
    return SwipeResult(current=live, winner=other, switch=True, rows=rows, reason="switch")


def test_the_reviewers_probe_a_skipped_look_between_two_verdicts_breaks_the_streak():
    """decision/policy_probe.py scenario 3 (definite / no-room skip / definite).  Fails before: the third switched."""
    a = SwipeAuto(MODE_AUTO)
    assert a.advise(155.0, _verdict(0.06), newest_frame_t=155.0, ms=MS).kind == "seen"
    assert a.due(305.0, lost_fresh=0, covered_fresh=150, room=-0.01, newest_frame_t=305.0) == "idle"
    adv = a.advise(455.0, _verdict(0.06), newest_frame_t=455.0, ms=MS)
    assert adv.kind == "seen" and adv.streak == 1, f"switched across a skipped look: {adv.kind}"


def test_the_reviewers_probe_two_verdicts_two_hours_apart_are_not_in_a_row():
    """decision/policy_probe.py scenario 4 (definite / 2 h gap / definite).  Fails before: the second switched."""
    a = SwipeAuto(MODE_AUTO)
    a.advise(155.0, _verdict(0.06), newest_frame_t=155.0, ms=MS)
    adv = a.advise(7355.0, _verdict(0.06), newest_frame_t=7355.0, ms=MS)
    assert adv.kind == "seen" and adv.streak == 1, f"two verdicts 2 h apart counted in a row: {adv.kind}"
    from core.config import SWIPE_AUTO_STREAK_GAP_S
    assert SWIPE_AUTO_STREAK_GAP_S == 900.0 and AutoParams().streak_gap_s == 900.0
    # regression guard: within the bound they are in a row
    a = SwipeAuto(MODE_AUTO)
    a.advise(155.0, _verdict(0.06), newest_frame_t=155.0, ms=MS)
    assert a.advise(155.0 + 899.0, _verdict(0.06), newest_frame_t=1054.0, ms=MS).kind == "apply"


def test_an_empty_pool_between_two_looks_breaks_the_streak():
    """No dancer for the pool's age (the tap kept nothing for 300 s: the pool is empty at a look) between look 1
    and look 2: look 2 starts a new streak.  Fails before: look 1 was already shown."""
    bg, proc, loader, live = _evening_bg()
    tap = _Tap(list(range(150)) + list(range(600, 900)))
    assert _look(bg, proc, tap, 150) == "run" and bg.suggestion is None
    proc.clock, proc.frames = 500.0, []                   # the pool empty: nobody on the wall since 150 s
    assert bg.tick() == "idle"
    assert _look(bg, proc, tap, 750) == "run"
    assert bg.suggestion is None and bg.last_advice["streak"] == 1, "a verdict across an empty wall counted"
    assert _look(bg, proc, tap, 900) == "run" and bg.suggestion is not None     # (regression guard)


# ----------------------------------------------------------------------------------- 3. the « slower » rule
def test_a_slower_model_is_not_suggested_and_an_unknown_time_refuses():
    """Fails before: mode suggest put the slower model on screen at once (no rule), and mode auto switched with
    the live model's time unknown."""
    from test_swipe_auto_speed import _timed
    bg, proc, events, loader, live = _looking_bg("suggest")
    _timed(bg, proc, loader, [30.0], [15.0])              # twice the live model's time
    for clock in (200.0, 800.0):
        proc.clock, proc.frames = clock, _look_frames(150, t0=clock - 200.0)
        assert bg.tick() == "run"
        assert bg.suggestion is None
        assert bg.last_advice["why_not_auto"] == "it is more than 15 % slower (30.0 ms a frame against 15.0)"
    bg, proc, events, loader, live = _looking_bg("auto")
    proc.timing = {}                                      # no live time sampled during the runs
    for clock in (200.0, 800.0):
        proc.clock, proc.frames = clock, _look_frames(150, t0=clock - 200.0)
        assert bg.tick() == "run"
    assert bg.switch._want is None and bg.suggestion is None, "switched with no time to compare"
    assert bg.last_advice["why_not_auto"] == "its time or the live model's is not known"


# ------------------------------------------------------------------------------------------ 4. one wording
@pytest.mark.usefixtures("swipe_auto_apply_released")
def test_one_wording_with_both_times_and_the_reason_that_holds_a_switch_back(swipe_auto_unlocked):  # noqa: F811
    """Fails before: « ...: clearly better - Apply now (Auto: suggest does not switch by itself) », no live time."""
    bg, proc, loader, live = _evening_bg("suggest")
    tap = _Tap(range(1500))
    _look(bg, proc, tap, 150)
    _look(bg, proc, tap, 300)
    assert bg.suggestion["text"] == LINE and bg.suggestion["text"].isascii()
    bg, proc, loader, live = _evening_bg("auto")
    bg.auto.policy._switched_at = 100.0
    _look(bg, proc, tap, 150)
    _look(bg, proc, tap, 300)
    assert bg.suggestion["text"] == LINE + " (the setting changed a moment ago)"
    assert "clearly" not in bg.suggestion["text"]


# --------------------------------------------------------------------------------------------- 5. said once
@pytest.mark.usefixtures("swipe_auto_apply_released")
def test_a_look_naming_it_again_updates_the_line_silently_and_the_looks_back_off():
    """Fails before: every look re-announced it (a toast each), and the next look came 150 s later."""
    pytest.importorskip("dearpygui")
    import app as appmod
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    runs = [t for t in range(1, 2000) if _look(bg, proc, tap, t) == "run"]
    assert runs == [150, 300, 600, 1200], runs            # shown at 300 s: then 300 s, 600 s of back-off
    assert len(_toasts(events)) == 1
    again = [p for k, p in events if k == "suggest" and p.get("again")]
    assert len(again) == 2 and bg.suggestion["seen"] == 4 and bg.suggestion["text"].endswith("seen 4 times: Apply now?")
    # the app: the first one toasts, a look naming it again redraws the line only
    published = []
    fake = SimpleNamespace(bus=SimpleNamespace(publish=published.append), swipe_bg=bg, _swipe_shown="",
                           swipe_switch=bg.switch)
    fake._swipe_apply_hint = lambda w: appmod.WallDanceApp._swipe_apply_hint(fake, w)
    for kind, payload in [e for e in events if e[0] == "suggest"]:
        appmod.WallDanceApp._swipe_notify(fake, kind, payload)
    assert [type(e).__name__ for e in published] == ["SettingsSwipeResult", "Toast", "SettingsSwipeResult",
                                                     "SettingsSwipeResult"]
    assert published[1].message == TOAST


def test_two_hours_of_a_better_model_never_pressed_is_one_toast_and_few_looks():
    """The reviewer's evening (b2/probe_dismiss.py scenario 2): dancers on the wall for two hours of loop clock,
    the other model better at every look, nobody presses.  It read: 47 toasts, 47 runs, 7,050 inferences.
    Now: one toast, 14 runs (the back-off after the suggestion: 300, 600, 1200 s, then a verdict more than 15 min
    after the last one starts a streak again and the next look comes at once), the suggestion still standing."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(7200))
    runs = [t for t in range(0, 7200, 5) if _look(bg, proc, tap, t) == "run"]
    assert len(_toasts(events)) == 1, f"{len(_toasts(events))} toasts in two hours"
    assert len(runs) == 14 and loader.infers == 14 * 150, runs
    assert bg.suggestion is not None and [k for k, _ in events if k == "cleared"] == []


# --------------------------------------------------------------------------------- 6. withdrawn, not confirmed
@pytest.mark.parametrize("second", ["keep", "no verdict"])
def test_a_suggestion_goes_after_two_looks_in_a_row_that_do_not_name_it(second, monkeypatch):
    """Fails before: one « keep » withdrew it at once (« the last look keeps x @ 1280 »), and looks without a
    verdict (a referee's refusal of the plate) left it on screen up to its age."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    _look(bg, proc, tap, 150)
    assert _look(bg, proc, tap, 300) == "run" and bg.suggestion is not None
    loader.infer_boxes = []                               # the other model now sees nobody: the referee keeps x
    assert _look(bg, proc, tap, 600) == "run" and bg.last_advice["advice"] == "none"
    assert bg.suggestion is not None, "one look that did not name it withdrew it"
    if second == "no verdict":
        import runtime.swipe_background as sb
        monkeypatch.setattr(sb, "score_runs", lambda *a, **k: SwipeResult(L1280, None, False, [], "no verdict"))
    runs = [t for t in range(601, 3000) if _look(bg, proc, tap, t) == "run"]
    assert runs and bg.suggestion is None
    assert [p["text"] for k, p in events if k == "cleared"] == [WITHDRAWN]


# ------------------------------------------------------------------------------- 7. the line and the row agree
def test_the_readiness_row_names_what_the_swipe_line_shows():
    """The operator's own verdict on the line: the row quotes it.  A background suggestion took the line: the
    row no longer names an Apply button that is not on screen.  Fails before: the row still quoted it."""
    pytest.importorskip("dearpygui")
    import app as appmod
    from test_swipe_live import _FakeProc, _FakeRunner, _controller, _frames
    from test_swipe_live import _live as _op_live
    proc = _FakeProc(_frames(), 30)
    live = {"v": _op_live()}
    c, _ = _controller(proc, live, _FakeRunner(), verdict_max_age_s=1800.0)
    c.start()
    c.join(5)
    fake = SimpleNamespace(swipe=c, swipe_bg=SimpleNamespace(mode="suggest", health=lambda: None),
                           swipe_switch=SimpleNamespace(health=lambda: None), _swipe_shown="operator",
                           _swipe_plate_ready=lambda: True, _swipe_live_setting=lambda: live["v"],
                           _swipe_seamless_winner=lambda: None)
    row = appmod.WallDanceApp._swipe_readiness_row(fake)
    assert row is not None and row.status == "warn"       # (regression guard) the operator's verdict on the line
    fake._swipe_shown = "background"
    assert appmod.WallDanceApp._swipe_readiness_row(fake) is None, "the row quotes a verdict no longer on screen"


# ------------------------------------------------------------------------------------------ 8. the last guard
@pytest.mark.parametrize("released", ["off only", "suggest only", "shipped"])
@pytest.mark.usefixtures("swipe_auto_apply_released")
def test_no_automatic_switch_unless_the_build_releases_auto(released, request):
    """Mode auto set past the app's doors, the build not releasing it: shown as a suggestion, nothing switches.
    Fails before: the switch was asked.  « shipped »: the list as the build ships it, nothing patched."""
    if released != "shipped":
        request.getfixturevalue("swipe_auto_off_only" if released == "off only" else "swipe_auto_suggest_only")
    bg, proc, loader, live = _evening_bg("auto")
    tap = _Tap(range(1500))
    _look(bg, proc, tap, 150)
    assert _look(bg, proc, tap, 300) == "run"
    assert bg.switch._want is None and bg.state != "switching", "a switch the build does not release was asked"
    assert bg.suggestion is not None and bg.last_advice["why_not_auto"] == "Auto: switch is not released in this build"
    assert bg.suggestion["text"] == LINE
