"""P6 (Plan Cordee): where « the looker stands down when the live frames run late » meets « a suggestion is shown
once, on two verdicts in a row », and four leftovers of that round.

Joins (1 and 2 held already: regression guards; 3 failed before):
1. a look stood down for late frames is not a verdict: it is not one of the two looks that withdraw a suggestion
   they do not confirm, the streak neither ends nor grows, and its wait and the back-off after a suggestion are two
   deadlines -- the next look comes at the later of the two, neither one lengthened nor doubled by the other;
2. a setting seen once, then a stand-down or a wait for an empty wall: the second verdict comes when a look can run
   again, and two verdicts more than 15 min apart are still not in a row;
3. the replay driver (``tests/switch_replay.py``) gives the looker a frame budget (``--frame-budget-ms``, 0 = none:
   bench gate runs on a shared GPU) and says it in its header line.

Leftovers (each failed before its fix):
a. a setting put aside (a failed load, run or automatic switch) ends the streak: once Dismiss lifts the ban, two
   looks in a row again (was: the very next verdict showed it again with a toast, « seen 3 times »);
b. a look that names a suggestion on screen once more says the reason that holds NOW in its parentheses (was: the
   dwell's « the setting changed a moment ago » kept after the dwell ended);
c. the combo's tooltip: an automatic switch is said in green, only a failure is an alert (was: « with an alert »);
d. ``tests/swipe_auto_gate.py --no-ms`` gives every setting the same time, so the « slower » rule passes (was: no
   time at all, which the rule now refuses: every verdict refused, the gate RED with no suggestion).

CPU, with the fakes of ``test_swipe_auto_live``, ``test_swipe_auto_rollback`` and ``test_swipe_auto_suggestion``.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.swipe_auto import DWELL, MODE_AUTO, SwipeAuto
from test_swipe_auto_late_frames import LATE, _Loop, short  # noqa: F401 - the fixture
from test_swipe_auto_live import ON_HER, _look_frames, _looking_bg, swipe_auto_unlocked  # noqa: F401 - the fixture
from test_swipe_auto_lock import _combo, _wall_panel, panel  # noqa: F401 - the fixture
from test_swipe_auto_rollback import _look, _Tap
from test_swipe_auto_suggestion import LINE, _events, _evening_bg, _toasts, _verdict

TESTS = Path(__file__).resolve().parent
SEEN_3 = LINE.replace("seen twice", "seen 3 times")


def _late_from(bg, at):
    """The live frames run late (one in twenty takes 72 ms) whenever a look falls due at ``at`` (a set of loop
    clock seconds), on time otherwise -- what ``_late_before`` measures over the 3 s before a look."""
    bg._late_before = lambda proc: 72.0 if float(proc.loop_clock()) in at else None


def _runs(bg, proc, tap, start, stop):
    return [t for t in range(start, stop) if _look(bg, proc, tap, t) == "run"]


def _next_run(bg, proc, tap, start, stop):
    """The loop clock of the next look that runs, a look a second from ``start`` (None: none before ``stop``)."""
    return next((t for t in range(start, stop) if _look(bg, proc, tap, t) == "run"), None)


# ------------------------------------------------------------------- 1. a stand-down is not a verdict
def test_regression_guard_a_stand_down_neither_withdraws_nor_counts_in_the_streak():
    """Shown at 300 s (the looks back off 300 s), a « keep » at 600 s (one look that does not confirm it; the
    back-off doubles: 600 s), the look due at 1200 s stands down for late frames: the suggestion stays, the streak
    and the back-off are as they were.  The next look comes when the stand-down's wait ends and names it: the
    same line, seen 3 times, no second toast."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300] and bg.suggestion["text"] == LINE
    loader.infer_boxes = []                               # the other model sees nobody: the referee keeps x
    assert _runs(bg, proc, tap, 301, 601) == [600] and bg.suggestion["unconfirmed"] == 1
    pol = bg.auto.policy
    streak, backoff = (pol._streak, pol._streak_winner, pol._streak_at), (pol._keeps, pol._keep_at, pol.keep_wait())
    _late_from(bg, {1200.0})
    loader.infer_boxes = [ON_HER]                         # it would name it again
    assert _runs(bg, proc, tap, 601, 1201) == [] and bg.status()["last_look"]["outcome"] == LATE.format(72)
    assert bg.suggestion is not None and bg.suggestion["unconfirmed"] == 1, "a stand-down counted as unconfirmed"
    assert (pol._streak, pol._streak_winner, pol._streak_at) == streak, "a stand-down moved the streak"
    assert (pol._keeps, pol._keep_at, pol.keep_wait()) == backoff, "a stand-down moved the back-off"
    assert _next_run(bg, proc, tap, 1201, 1600) == 1500   # the stand-down's own 300 s, from 1200 s
    assert bg.suggestion["text"] == SEEN_3 and bg.suggestion["unconfirmed"] == 0
    assert len(_toasts(events)) == 1


def test_regression_guard_a_stand_down_inside_a_back_off_ends_at_the_later_of_the_two():
    """Shown at 300 s: the looks back off until 600 s.  At 450 s the lost share wakes a look (it loses clearly more
    dancers than when shown), which stands down for late frames: no look until 750 s (its own 300 s) -- the
    back-off is not restarted, not doubled, and does not add to it (not 1050 s); then it names it again."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300]
    pol = bg.auto.policy
    pol._keep_lost = 0.5                                  # the share when shown: today's 0.67 wakes a look
    _late_from(bg, {450.0})
    assert _runs(bg, proc, tap, 301, 451) == [] and bg._late_until == pytest.approx(750.0)
    assert (pol._keeps, pol._keep_at, pol.keep_wait()) == (1, 300.0, 300.0), "a stand-down moved the back-off"
    assert _next_run(bg, proc, tap, 451, 1100) == 750, "the two waits added up"
    assert bg.suggestion["text"] == SEEN_3 and len(_toasts(events)) == 1


# --------------------------------------------- 2. seen once, then a stand-down or a wait for an empty wall
@pytest.mark.parametrize("late_at, then", [({300.0}, "shown"), ({300.0, 600.0}, "seen again")])
def test_regression_guard_seen_once_then_a_stand_down_the_second_verdict_comes_after_it(late_at, then):
    """Seen at 150 s (nothing shown, the next look at once); the look due at 300 s stands down (300 s), and in the
    second case the one at 600 s too (600 s more).  The look after the wait gives the second verdict: at 600 s,
    450 s after the first, it is shown; at 1200 s, 1050 s after it, not in a row (15 min): seen once again."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 151) == [150] and bg.last_advice["advice"] == "seen"
    _late_from(bg, late_at)
    first = _next_run(bg, proc, tap, 151, 1300)
    if then == "shown":
        assert first == 600 and bg.suggestion is not None and bg.suggestion["text"] == LINE
        assert len(_toasts(events)) == 1
    else:
        assert first == 1200 and bg.last_advice["advice"] == "seen" and bg.suggestion is None
        assert _toasts(events) == []


@pytest.mark.parametrize("empty_at, then", [(400, "shown"), (1100, "seen again")])
def test_regression_guard_seen_once_then_a_wait_for_an_empty_wall_the_second_verdict_comes_after_it(empty_at,
                                                                                                    then):
    """Seen at 150 s out of RUN; then in RUN with a dancer tracked, its second engine gone: the look due waits
    for an empty wall to load it.  The wall empties at ``empty_at``: after its 2 s the engine loads and the look
    runs -- the second verdict, shown when it is less than 15 min after the first, seen once again after."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 151) == [150] and bg.last_advice["advice"] == "seen"
    wall = {"empty": False}
    bg.in_run, bg.wall_empty = (lambda: True), (lambda: wall["empty"])
    assert bg._release_now("test")                        # the second engine gone (freed out of the look)
    assert _runs(bg, proc, tap, 151, empty_at) == []
    assert bg.status()["last_look"]["outcome"].startswith("the automatic swipe waits for an empty wall")
    wall["empty"] = True
    assert _next_run(bg, proc, tap, empty_at, empty_at + 10) == empty_at + 2
    if then == "shown":
        assert bg.suggestion is not None and bg.suggestion["text"] == LINE and len(_toasts(events)) == 1
    else:
        assert bg.last_advice["advice"] == "seen" and bg.suggestion is None and _toasts(events) == []


# --------------------------------------------------------------- 3. the replay driver's frame budget
def test_a_frame_budget_of_0_is_none_and_the_default_is_the_apps(short):
    """``frame_budget_ms`` is the looker's: None is the app's constant, 0 is no budget (the live frames at 70 ms
    and the look runs).  Fails before: no such argument."""
    from core.config import SWIPE_AUTO_FRAME_BUDGET_MS
    bg, proc, events, loader, live = _looking_bg()
    assert bg.frame_budget_ms == SWIPE_AUTO_FRAME_BUDGET_MS
    bg, proc, events, loader, live = _looking_bg(frame_budget_ms=0)
    proc.frames = _look_frames()
    loop = _Loop(proc, lambda: 70.0)
    try:
        assert bg.tick() == "run" and loader.infers == 150, "no budget, yet the look stood down"
    finally:
        loop.end()


def test_the_replay_driver_passes_its_frame_budget_and_says_it():
    """Fails before: the looker of a bench run always ran under the app's 55 ms (in the author's GPU run the look
    due at 152 s stood down at its start), and the log did not say it."""
    src = (TESTS / "switch_replay.py").read_text()
    tree = ast.parse(src)
    opts = [c.args[0].value for c in ast.walk(tree) if isinstance(c, ast.Call)
            and getattr(c.func, "attr", "") == "add_argument" and c.args and isinstance(c.args[0], ast.Constant)]
    assert "--frame-budget-ms" in opts
    bg = next(c for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "BackgroundSwipe")
    assert "frame_budget_ms" in [k.arg for k in bg.keywords], "the looker runs under the app's budget whatever asked"
    head = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "say"
                and "take {video.name}" in ast.unparse(n))
    assert "frame budget" in ast.unparse(head), "the header line does not say the budget"


# ---------------------------------------------------------------------------------------- the leftovers
def test_a_setting_put_aside_then_dismissed_needs_two_looks_in_a_row_again():
    """(a) Shown at 300 s; the look at 600 s fails to run the other model: the setting is banned, the suggestion
    goes without a word; Dismiss lifts the ban.  Fails before: the very next verdict showed it again, with a toast,
    « seen 3 times »."""
    from core.swipe_runner import CandidateRun
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300] and len(_toasts(events)) == 1
    real = loader.infer

    def fails_once(images, cand, runner, **kw):
        loader.infer = real
        run = CandidateRun(cand)
        run.error = "CUDA error: an illegal memory access was encountered"
        return run
    loader.infer = fails_once
    assert _look(bg, proc, tap, 600) == "run" and bg.held() and bg.suggestion is None
    assert bg.dismiss() is False and not bg.held()        # no suggestion on screen: Dismiss lifts the ban
    first = _next_run(bg, proc, tap, 601, 1200)
    assert bg.last_advice["advice"] == "seen" and bg.suggestion is None, \
        f"one look after the ban showed it again: {bg.suggestion and bg.suggestion['text']!r}"
    assert _next_run(bg, proc, tap, first + 1, 1200) and bg.suggestion["text"] == LINE
    assert len(_toasts(events)) == 2


@pytest.mark.parametrize("dwell_s, held", [(300.0, ""), (2000.0, DWELL)])
def test_a_look_naming_it_again_says_the_reason_that_holds_now(dwell_s, held, swipe_auto_unlocked):  # noqa: F811
    """(b) Mode auto, the setting changed at 100 s: shown at 300 s inside the dwell, « (the setting changed a
    moment ago) ».  Nobody on the wall for the pool's age (the streak ends), then a look at 651 s names it once:
    the line, seen 3 times, says the reason that holds then -- none once the dwell (300 s) is over, the dwell's
    while it lasts.  Fails before: the dwell's reason stayed after it ended."""
    bg, proc, loader, live = _evening_bg("auto")
    pol = bg.auto.policy
    pol._switched_at, pol.params.dwell_s = 100.0, dwell_s
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300]
    assert bg.suggestion["text"] == LINE + f" ({DWELL})"
    _look(bg, proc, tap, 650, after=650)                  # an empty pool: the streak ends
    assert _look(bg, proc, tap, 651) == "run" and bg.last_advice["advice"] == "seen"
    assert bg.suggestion["text"] == SEEN_3 + (f" ({held})" if held else ""), bg.suggestion["text"]
    assert bg.suggestion["held"] == held and (bg.suggestion["why_not_auto"] == DWELL) == bool(held)


def test_the_combos_tooltip_says_only_a_failed_switch_is_an_alert(panel, swipe_auto_unlocked):  # noqa: F811
    """(c) Fails before: « switches between two frames, with an alert » -- a switch that works is a green toast."""
    dpg = panel
    _wall_panel(dpg)
    _, tip = _combo(dpg)
    assert "with an alert" not in tip
    assert "           switches between two frames (an alert only if it fails)\n" in tip


def test_the_gates_no_ms_gives_every_setting_the_same_time_and_the_speed_rule_passes(monkeypatch):
    """(d) ``--no-ms``: the policy is given the same time for every setting of the take, so a better setting is
    named (seen, then suggested) on the referee's verdicts alone.  Fails before: no time at all -- refused."""
    import swipe_auto_gate as G
    from test_swipe_auto_suggestion import L, X
    given = []

    class _Stream:
        def __init__(self, label, paths, truth_dir=None, conf=0.15):
            self.label, self.order = label, [0]
            self.take = SimpleNamespace(cands=[X, L])

        def by_label(self, label):
            return next(c for c in self.take.cands if c.label == label)

    monkeypatch.setattr(G, "Stream", _Stream)
    monkeypatch.setattr(G, "simulate", lambda st, live, *a, **kw: given.append((st, kw["ms"])))
    monkeypatch.setattr(G, "_row", lambda st, live, runs, expect="": {
        k: 0 for k in ("runs", "runs_per_h", "keeps", "wakes", "skipped", "probes", "gpu_s_per_h", "suggestions",
                       "suggest_worse", "suggest_named", "switches", "worse_switches", "flip_flops",
                       "first_switch_s", "to", "recall", "after_splice_s", "hours")} | {"take": "t", "live": "x"})
    assert G.main(["--take", "t", "rec", "--live", X.label, "--starts", "1", "--no-ms"]) == 0
    st, ms = given[0]
    ms_c = {c: ms[c.label] for c in st.take.cands if ms and c.label in ms} if ms else None   # as ``simulate``
    a = SwipeAuto(MODE_AUTO)
    assert a.advise(155.0, _verdict(0.06), newest_frame_t=155.0, ms=ms_c).kind == "seen"
    assert a.advise(305.0, _verdict(0.06), newest_frame_t=305.0, ms=ms_c).kind in ("suggest", "apply")
