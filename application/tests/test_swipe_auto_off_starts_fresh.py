"""P6 (Plan Cordee): two leftovers of « Dismiss means not now » (b688fe0).

* the mode set to off ends what the operator said to that mode and what it saw: a Dismiss's snooze (it survived
  off and on: the setting dismissed stayed aside for the rest of its 15 min) and the streak (a verdict seen before
  off counted towards a switch after it); the dwell and the back-offs stay;
* a mode click is the second engine's load moment whatever else is true, a snooze too (the look stood down before
  the load, and the click's moment was used up: the load waited for an empty wall).
CPU, with the fakes of ``test_swipe_auto_live`` and ``test_swipe_auto_rollback``."""
from test_seamless_switch import _Port, _live
from test_swipe_auto_live import _bg
from test_swipe_auto_rollback import _JudgingRunner, _look, _setup, _Tap

SNOOZE_SAYS = "the automatic swipe waits: l @ 1280 was dismissed - it looks again at "


def test_off_ends_the_snooze_a_mode_set_again_looks_at_once():
    bg, proc, loader, live, port, _ = _setup(mode="suggest")
    tap = _Tap(range(1500))
    assert _look(bg, proc, tap, 150) == "run" and bg.suggestion is not None
    bg.dismiss()                                          # not now: l @ 1280 aside for 15 min
    assert _look(bg, proc, tap, 300) == "snoozed"
    bg.set_mode("off")
    assert bg.auto.snoozed(300.0) is None, "the snooze survived the mode going off"
    assert not str((bg.status()["last_look"] or {}).get("outcome", "")).startswith(SNOOZE_SAYS)
    assert _look(bg, proc, tap, 301) == "off"
    bg.set_mode("suggest")                                # a mode set again: fresh, l @ 1280 is looked at
    assert _look(bg, proc, tap, 302) == "run", "a mode set again after off still waits for the old snooze"
    assert bg.suggestion is not None


def test_off_ends_the_streak_a_verdict_before_it_never_counts_after():
    bg, proc, loader, live, port, _ = _setup(mode="auto")
    tap = _Tap(range(1500))
    assert _look(bg, proc, tap, 150) == "run" and bg.last_advice["why_not_auto"] == "seen 1 time(s), 2 needed"
    bg.set_mode("off")
    assert _look(bg, proc, tap, 151) == "off"
    bg.set_mode("auto")
    assert _look(bg, proc, tap, 300) == "run"
    assert bg.last_advice["advice"] == "suggest", "a verdict seen before off counted towards a switch after it"
    assert bg.last_advice["why_not_auto"] == "seen 1 time(s), 2 needed" and bg.switch._want is None


def test_a_mode_click_during_a_snooze_loads_the_second_engine_with_a_dancer_on_the_wall():
    live = {"v": _live()}
    loader = _JudgingRunner()
    state = {"run": True}
    bg, proc, _ = _bg(live, loader=loader, port=_Port(), mode="off", in_run=lambda: state["run"],
                      wall_empty=lambda: False)           # RUN, a dancer tracked all along
    bg.pace_s = 0.0
    proc.swipe_lost_share = lambda: 0.66
    tap = _Tap(range(1500))
    bg.set_mode("suggest")                                # the click: its look loads, whatever is on the wall
    assert _look(bg, proc, tap, 150) == "run" and bg.suggestion is not None and len(loader.loads) == 1
    bg.dismiss()                                          # not now: l @ 1280 aside for 15 min
    shown, live["v"], state["run"] = live["v"], None, False
    assert _look(bg, proc, tap, 160) == "loading" and bg.resident is None   # a model load in STANDBY freed it
    live["v"], state["run"] = shown, True
    assert _look(bg, proc, tap, 170) == "snoozed" and bg.resident is None   # RUN, a dancer: no moment to load
    infers = loader.infers
    bg.set_mode("auto")                                   # the operator's click, the snooze still on
    assert _look(bg, proc, tap, 171) == "snoozed"
    assert len(loader.loads) == 2 and bg.resident is not None, "the mode click during a snooze loaded nothing"
    assert loader.infers == infers and bg.suggestion is None and bg.switch._want is None   # still not looked at
    assert bg.status()["last_look"]["outcome"].startswith(SNOOZE_SAYS)
    assert _look(bg, proc, tap, 172) == "snoozed" and len(loader.loads) == 2               # once
