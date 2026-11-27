"""P6 (Plan Cordee): what the technician's Dismiss means, and what the background swipe tells him (the blind review
of 26/11: decision finding 1, nits 9 and 11; loop nits 4 and 8).

* Dismiss on a suggestion is « not now »: the streak ends, and that setting is neither suggested nor switched to
  by itself -- no look runs for it at all -- for ``SWIPE_AUTO_SNOOZE_S`` (15 min) of the loop clock, or until the
  scene changes or a project loads; the look's row says why while it waits; after it, a new streak (was: the next
  look switched by itself, a red alert after « no »);
* a setting an automatic switch failed to is left alone as one whose engine could not load: no look, no inference,
  never suggested (was: « clearly better - Apply now (the automatic switch to it failed: ...) » at every look);
* a suggestion ages out at ``SWIPE_VERDICT_MAX_AGE_S`` (P5's bound on the operator's own verdict), on an empty wall
  too; the operator's Apply never hands over a resident loaded for another project;
* an automatic verdict that meets the operator's Apply under way is not a failure; a switch that fails before it
  begins says one thing to do; in mode suggest the line says what the mode does.
CPU, with the fakes of ``test_swipe_auto_live`` and ``test_swipe_auto_rollback``."""
import dataclasses
import pytest

from test_seamless_switch import L1280, X1280, _Loader, _Port, _Runner, _live
from test_swipe_auto_live import ON_HER, _apply_adv, _bg, _look_frames, _looking_bg
from test_swipe_auto_rollback import _first_frame_raises, _look, _next_runs, _setup, _Tap

SNOOZE = 900                     # core.config.SWIPE_AUTO_SNOOZE_S: 15 min of the loop clock
SNOOZE_SAYS = "the automatic swipe waits: l @ 1280 was dismissed - it looks again at "
BAN = "the automatic swipe will not try l @ 1280 again until you press Dismiss"


def _events(bg):
    """What the background swipe and its switch tell the app from now on."""
    out = []
    bg.notify = bg.switch.notify = lambda kind, payload: out.append((kind, payload))
    return out


def _result():
    """A real verdict: l @ 1280 sees the dancer on the 100 frames x @ 1280 left her bare on (definite)."""
    from core.swipe import SwipeParams
    from core.swipe_runner import CandidateRun, live_run_from_boxes, score_runs
    fr = _look_frames()
    return score_runs([f.obs for f in fr], [live_run_from_boxes(X1280, [f.boxes for f in fr]),
                                           CandidateRun(L1280, boxes=[[ON_HER] for _ in fr])], X1280, SwipeParams())


# ------------------------------------------------------------------------------------------------ 1. not now
def test_dismiss_on_a_suggestion_is_not_now_then_a_new_streak():
    """The review's probe (decision/dismiss_probe.py): mode auto, look 1 « seen 1 time(s), 2 needed », Dismiss.  It
    read: look 2 switched by itself.  Now: no look for l @ 1280 until the snooze ends -- no inference, no
    suggestion, no switch --, the look's row says why and goes with it; then two fresh definite verdicts."""
    bg, proc, loader, live, port, _ = _setup(mode="auto")
    tap = _Tap(range(1500))
    assert _look(bg, proc, tap, 150) == "run" and bg.last_advice["why_not_auto"] == "seen 1 time(s), 2 needed"
    assert bg.suggestion is not None
    infers = loader.infers
    bg.dismiss()                                         # the app's Dismiss, the suggestion on the swipe line
    assert bg.suggestion is None
    events = _events(bg)
    did = {_look(bg, proc, tap, t) for t in range(151, 150 + SNOOZE)}
    assert did == {"snoozed"}, f"Dismiss did not put l @ 1280 aside: the looks did {sorted(did)}"
    assert loader.infers == infers and bg.switch._want is None and bg.suggestion is None
    assert [k for k, _ in events if k in ("suggest", "switching", "fault")] == []
    assert bg.status()["last_look"]["outcome"].startswith(SNOOZE_SAYS)
    proc.clock, proc.frames = 150.0 + SNOOZE, []         # the snooze is over (an empty wall): its row goes
    assert bg.tick() == "idle" and bg.status()["last_look"] is None
    runs = _next_runs(bg, proc, tap, 150 + SNOOZE, n=2, stop=1500)
    assert [a["advice"] for _, a in runs] == ["suggest", "apply"], "not a new streak after the snooze"
    assert runs[0][1]["why_not_auto"] == "seen 1 time(s), 2 needed" and bg.switch._want is not None
    from core.config import SWIPE_AUTO_SNOOZE_S
    assert SWIPE_AUTO_SNOOZE_S == SNOOZE and bg.snooze_s == SNOOZE


@pytest.mark.parametrize("ends", ["a new scene", "a project load"])
def test_not_now_ends_with_a_new_scene_or_a_project_load(ends):
    """The policy's ``reset`` (a new scene: here a new empty-wall plate) and ``clear_bans`` (a project load) end
    the snooze: the look no longer waits for it."""
    bg, proc, loader, live, port, _ = _setup(mode="suggest")
    tap = _Tap(range(1500))
    assert _look(bg, proc, tap, 150) == "run" and bg.suggestion is not None
    bg.dismiss()
    assert _look(bg, proc, tap, 300) == "snoozed", "Dismiss put nothing aside"
    if ends == "a new scene":
        live["v"] = dataclasses.replace(live["v"], plate=(2, "t"))
    else:
        bg.clear_bans()
    assert _look(bg, proc, tap, 301) in ("idle", "run")
    assert bg.auto.snoozed(301.0) is None
    assert not str((bg.status()["last_look"] or {}).get("outcome", "")).startswith(SNOOZE_SAYS)


# ------------------------------------------------------------------------- 2. a banned setting is left alone
@pytest.mark.parametrize("how", ["rolled back", "failed before it began"])
def test_a_setting_an_automatic_switch_failed_to_is_never_suggested_nor_looked_at(how):
    bg, proc, loader, live, port, _ = _setup()
    tap = _Tap(range(1500))
    assert _look(bg, proc, tap, 150) == "run" and bg.suggestion is not None          # « seen 1 time(s) »
    if how == "failed before it began":
        bg.switch.warm = lambda prep, winner: "RuntimeError: its warm-up raised"
    assert _look(bg, proc, tap, 300) == "run" and bg.last_advice["advice"] == "apply"
    bg.switch.service()
    if how == "rolled back":
        assert bg.switch.drain()
        _first_frame_raises(port)
    assert bg.switch.fault and bg.held() and bg.status()["auto_banned"] == [L1280.engine_file]
    assert bg.suggestion is None, "the suggestion of the setting just banned is still on screen"
    events = _events(bg)
    loads, infers = len(loader.loads), loader.infers
    did = {_look(bg, proc, tap, t) for t in range(301, 800)}
    assert did == {"banned"}, f"the looker kept looking at the banned setting: {sorted(did)}"
    assert len(loader.loads) == loads and loader.infers == infers
    assert bg.suggestion is None and events == []
    assert bg.status()["last_look"]["outcome"] == BAN and bg.health() == BAN
    # no suggestion on screen: Dismiss acknowledges -- the ban goes, its row with it, the looks come back
    bg.switch.dismiss()
    bg.dismiss()
    assert not bg.held() and bg.status()["last_look"] is None
    (first, _), = _next_runs(bg, proc, tap, 800)
    assert first == 801 and loader.infers == infers + 150


# ------------------------------------------------------------------------------ 4. a suggestion ages out
def test_a_suggestion_ages_out_even_on_an_empty_wall():
    clock = {"wall": 10_000.0}
    bg, proc, events, loader, live = _looking_bg(wall=lambda: clock["wall"])
    proc.frames = _look_frames()
    assert bg.tick() == "run" and bg.suggestion is not None
    proc.frames = []                                     # the wall empties: no look runs from now on
    clock["wall"] += 1799.0
    proc.clock += 1799.0
    assert bg.tick() == "idle" and bg.suggestion is not None        # (regression guard: the bound is 30 min)
    clock["wall"] += 2.0
    proc.clock += 2.0
    assert bg.tick() == "idle" and bg.suggestion is None, "a suggestion 30 min old is still offered"
    assert [p["text"] for k, p in events if k == "cleared"] == ["suggestion withdrawn: it is 30 min old"]


# -------------------------------------------------------------------- 5. Apply never takes a stale resident
def test_the_operators_apply_never_hands_over_a_resident_loaded_for_another_project():
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    loader, port = _Loader(), _Port()
    bg, _, _ = _bg(live, loader=loader, port=port, mode="suggest")
    bg.resident = Resident(L1280, "l1280-old", _Runner("l1280-old"), bg._resident_key(live["v"]), 0.4)
    live["v"] = _live(project="vendredi1009_mur3", model_id=2)    # another project, the looker not past it yet
    assert bg.request_switch(L1280, "Apply") is None
    assert bg.resident is None, "the previous project's resident was handed to the switch"
    bg.switch.service()
    assert loader.loads == [L1280.engine_file]           # the winner loaded for this project


# --------------------------------------------------------- 6. an automatic verdict meeting an Apply under way
@pytest.mark.parametrize("under_way", ["the seamless Apply", "the reload Apply"])
def test_an_automatic_verdict_meeting_the_operators_apply_is_no_fault(under_way):
    """The review's probe 2 (loop/probe_races.py): the operator pressed Apply on the suggestion a moment ago, the
    looker's definite verdict for the same winner comes a few ms later.  It read: THE fault, « failed before it
    began (a switch is already under way) ... - set the automatic swipe to off », until the Apply's probation
    ended.  Now nothing failed: no fault, no ban, nothing said; the Apply goes through."""
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    port = _Port()
    busy = (lambda: "an Apply is under way") if under_way == "the reload Apply" else (lambda: None)
    bg, proc, events = _bg(live, port=port, mode="auto", busy=busy)
    bg.resident = Resident(L1280, "l1280", _Runner("l1280"), bg._resident_key(live["v"]), 0.4)
    if under_way == "the seamless Apply":
        assert bg.request_switch(L1280, "switching to l @ 1280 (Apply)") is None
    want = bg.switch._want
    bg._act(_apply_adv(), _result(), live["v"])
    assert bg.switch.fault is None and [k for k, _ in events if k in ("fault", "switching")] == [], \
        f"a switch already under way was taken for a failed automatic switch: {bg.switch.fault!r}"
    assert bg.switch._want is want and not bg.held() and bg._fault_why is None
    if under_way == "the seamless Apply":
        bg.switch.service()
        assert bg.switch.drain()
        port.hooks[1]()                                  # the Apply's first frames ran well
        assert bg.switch.fault is None and bg._fault_why is None


# ------------------------------------------------------------------------- 7. one instruction per situation
def test_an_automatic_switch_that_fails_before_it_begins_says_one_thing_to_do():
    """(a) The fault (the alert, the swipe line, the readiness row: this one text) says Dismiss lets it try again;
    the next definite verdict, held back by it, says whose switch failed and gives no other instruction.  Other
    refusals than an Apply under way stay a fault (regression guard of 6)."""
    from runtime.swipe_background import Resident
    live = {"v": _live()}
    bg, proc, events = _bg(live, mode="auto")            # no resident: the switch cannot begin
    bg._act(_apply_adv(), _result(), live["v"])
    text = ("the automatic switch to l @ 1280 failed before it began (l @ 1280 is not loaded beside the live "
            "model): x @ 1280 still runs - Dismiss lets it try again")
    assert bg.switch.health() == text and [p["text"] for k, p in events if k == "fault"] == [text]
    bg.resident = Resident(L1280, "l1280", _Runner("l1280"), bg._resident_key(live["v"]), 0.4)
    bg._act(_apply_adv(), _result(), live["v"])
    assert bg.last_advice["advice"] == "suggest" and bg.switch._want is None
    assert bg.suggestion["why_not_auto"] == "the last automatic switch failed"
    assert bg.suggestion["text"].endswith("clearly better - Apply now (the last automatic switch failed)")
    assert "automatic swipe to off" not in text + bg.suggestion["text"]


def test_in_mode_suggest_the_line_says_what_the_mode_does():
    """(b) The combo reads « Auto: suggest »: the line says that mode does not switch by itself (it read
    « automatic switching is off »)."""
    bg, proc, events, loader, live = _looking_bg("suggest")
    proc.frames = _look_frames()
    assert bg.tick() == "run"
    assert bg.suggestion["why_not_auto"] == "Auto: suggest does not switch by itself"
    assert bg.suggestion["text"] == ("l @ 1280 covers 100 % of the dancers, x @ 1280 33 %: clearly better - Apply now "
                                     "(Auto: suggest does not switch by itself)")
