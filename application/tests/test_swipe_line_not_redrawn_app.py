"""P6, the blind re-read of group B2 (finding 8): the swipe line is not redrawn over what the operator must still
read -- on a real ``WallDanceApp`` (no GUI, no main loop, CPU; the looker thread never started, the mode set through
the app's own door with the ``swipe_auto_unlocked`` fixture: the build does not ship auto).

A look that names a standing suggestion again redraws its line silently (« ... seen 3 times: Apply now? »).  It did
so whenever the line had last been the background's: over the red line of a FAILED Apply of that very suggestion --
inviting a second Apply that fails the same way -- and over the progress of the operator's own swipe started after
it.  Now the app keeps what the line shows (``_swipe_shown``) from every state published to it: the operator's
swipe running, an Apply and its outcome take it, Dismiss leaves it free; a silent redraw draws only on the
suggestion's own line or a free one.  Held back, the suggestion is not lost: it shows again once the line is free.

Dismiss, though, is « not now » for the background suggestion behind whatever the line shows (558e2c0): on the red
line of a failed Apply of that suggestion it acknowledges the failure AND puts the suggestion aside (the snooze, its
streak over) -- shown again minutes later, it would invite a second Apply that fails the same way.
"""

from core.swipe_auto import AutoAdvice
from runtime import api
from runtime.seamless_switch import no_room
import pytest

from test_seamless_switch import L1280, X1280, _live
from test_swipe_auto_dismiss import _holds
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: F401 - the fixture
from test_swipe_auto_lock import _toasts, real_app  # noqa: F401 - the fixture
from test_swipe_auto_not_now import _result
from test_swipe_auto_live import swipe_auto_apply_released  # noqa: F401 - the fixture

SAID = "l @ 1280 covers 100 % of the dancers, x @ 1280 33 % (7 ms a frame against 6)"
PROGRESS = "collecting 12 / 150 frames with a dancer on the wall"
MS = {L1280: 6.0, X1280: 6.0}                        # each setting's time a frame, measured alike


def _on(app, live):
    """Auto: suggest through the app's door, no looker thread; the live setting the test's."""
    app.swipe_bg.start = lambda: None
    app._cb_swipe_auto("suggest")
    assert app.swipe_bg.mode == "suggest"
    app._swipe_live_setting = lambda: live
    app.swipe_bg.live = lambda: live
    app.swipe_switch.live = lambda: live


def _look(app, live, streak=2):
    """A look that names l @ 1280 (the policy's « suggest »), through the looker's own advice -> the app."""
    adv = AutoAdvice(kind="suggest", level="definite", winner=L1280, seamless=True, streak=streak, said=SAID,
                     text=f"{SAID}, seen {streak} times: Apply now?",
                     why_not_auto="Auto: suggest does not switch by itself")
    app.swipe_bg._act(adv, _result(), live)


def _judged(app, live, t):
    """A look at ``t`` s of the loop clock, the looker's way: none for a setting it leaves alone (``_stands_down``:
    'snoozed', 'banned' -- what its tick returns), else the policy's own advice on a verdict naming l @ 1280, on
    frames newer than the last look's -> the app; 'run'."""
    app.processor.loop_clock = lambda: float(t)
    bg = app.swipe_bg
    _, why = bg._stands_down(L1280, float(t))
    if why:
        return why
    result = _result()
    bg._act(bg.auto.advise(float(t), result, newest_frame_t=float(t), ms=MS), result, live)
    return "run"


def _line(events):
    """What was published to the swipe line: (event, state or 'verdict')."""
    return [(type(e).__name__, getattr(e, "state", "verdict")) for e in events
            if type(e).__name__ in ("SettingsSwipeResult", "SettingsSwipeProgress")]


def _suggestion_shown(app, events, live):
    _look(app, live)
    assert app._swipe_shown == "background" and app.swipe_bg.suggestion is not None
    shown = [e for e in events if type(e).__name__ == "SettingsSwipeResult"]
    assert shown and shown[-1].result["auto"]["winner"] == L1280.label


def _failed_apply(app, events):
    """The operator presses « Apply now » on the suggestion; its engine finds no room on the GPU: the switch's
    fault, the red line (the switch's own path from a failed load: ``_fail`` -> ``fail`` -> the app)."""
    asked = {}
    app.swipe_switch.can_switch = lambda winner: None             # seamless: loads beside, switches between frames

    def request_switch(winner, text, on_done=None, suggestion=False):
        asked.update(winner=winner, on_done=on_done, suggestion=suggestion)
        return None
    app.swipe_bg.request_switch = request_switch
    app._cb_apply_settings_swipe()
    assert asked["winner"] == L1280 and asked["suggestion"] is True
    req = {"winner": L1280, "text": "Apply", "auto": False, "on_done": asked["on_done"], "ticket": object()}
    app.swipe_switch._fail(req, no_room(312.0), X1280, L1280, before=True)
    failed = [e for e in events if type(e).__name__ == "SettingsSwipeProgress" and e.state == "apply_failed"]
    assert failed and app.swipe_switch.fault == failed[-1].text
    return failed[-1].text


@pytest.mark.usefixtures("swipe_auto_apply_released")
def test_a_confirmation_after_a_failed_apply_does_not_replace_the_failure_line(real_app,  # noqa: F811
                                                                               swipe_auto_unlocked):  # noqa: F811
    app, events = real_app
    live = _live()
    _on(app, live)
    _suggestion_shown(app, events, live)
    _failed_apply(app, events)
    seen = app.swipe_bg.suggestion["seen"]
    del events[:]
    _look(app, live)                                      # the next look names it again: a silent redraw
    assert _line(events) == [], f"the failed Apply's red line was redrawn over: {_line(events)}"
    assert app.swipe_switch.fault, "the fault went with it"
    sug = app.swipe_bg.suggestion
    assert sug is not None and sug["seen"] == seen + 1, "the suggestion held back was lost"


def test_a_confirmation_during_the_operators_own_swipe_does_not_replace_its_progress(real_app,  # noqa: F811
                                                                                     swipe_auto_unlocked):  # noqa: F811
    app, events = real_app
    live = _live()
    _on(app, live)
    _suggestion_shown(app, events, live)
    app.swipe._say("collecting", PROGRESS, 0.08)          # « Try other settings »: the worker's first line
    del events[:]
    _look(app, live)
    assert _line(events) == [], f"the operator's swipe in progress was redrawn over: {_line(events)}"
    app._swipe_notify("cleared", {"text": "suggestion withdrawn: the setting changed since the suggestion"})
    assert _line(events) == [], f"a withdrawal covered the operator's swipe in progress: {_line(events)}"


@pytest.mark.usefixtures("swipe_auto_apply_released")
def test_dismiss_on_a_failed_applys_line_is_not_now_for_the_suggestion_it_came_from(real_app,  # noqa: F811
                                                                                   swipe_auto_unlocked):  # noqa: F811
    """Dismiss on the red line of a failed Apply of the suggestion acknowledges the failure AND says « not now » to
    that suggestion (``BackgroundSwipe.dismiss(behind=True)``, 558e2c0): the fault goes; the suggestion goes, its
    setting put aside for the snooze (15 min of the loop clock), its streak over; nothing waits for Dismiss any
    more.  Meanwhile no look runs for it, and a verdict that names it all the same shows nothing; after the snooze,
    two fresh looks in a row again before it is on the line.  (b033463's version of this test wanted it shown again
    at the next confirming look -- a second Apply invited, failing the same way.)"""
    from core.config import SWIPE_AUTO_SNOOZE_S
    app, events = real_app
    live = _live()
    _on(app, live)
    bg = app.swipe_bg
    assert [_judged(app, live, t) for t in (100, 110)] == ["run", "run"]           # two looks in a row: shown
    assert app._swipe_shown == "background" and bg.suggestion is not None and bg.auto.policy._streak == 2
    _failed_apply(app, events)
    assert _judged(app, live, 120) == "run" and bg.suggestion["seen"] == 3          # held back: the red line stays
    app.processor.loop_clock = lambda: 130.0
    del events[:]
    app._cb_dismiss_swipe()
    assert app.swipe_switch.fault is None, "Dismiss did not acknowledge the failed Apply on the line"
    assert bg.suggestion is None and bg.auto.snoozed(130.0) == L1280, \
        "the suggestion that Apply came from was not put aside: the next look shows it again"
    assert bg.auto.policy._streak == 0, "its streak goes on"
    assert _holds(events) == [False], "Dismiss stays on screen with nothing left to wait for it"
    assert app._swipe_shown == "", "the line Dismiss left free is not free"
    del events[:]
    end = 130.0 + SWIPE_AUTO_SNOOZE_S
    assert _judged(app, live, 140) == "snoozed", "a look for the setting put aside"
    result = _result()                                    # a look that ran across the Dismiss names it all the same
    bg._act(bg.auto.advise(150.0, result, newest_frame_t=150.0, ms=MS), result, live)
    assert _judged(app, live, end - 1) == "snoozed"
    assert _line(events) == [] and _toasts(events) == [] and bg.suggestion is None, \
        f"shown again within the snooze: {_line(events)}"
    assert _judged(app, live, end + 1) == "run" and bg.suggestion is None and _line(events) == [], \
        "shown at the first look after the snooze: its streak was kept"
    assert _judged(app, live, end + 11) == "run"
    shown = [e for e in events if type(e).__name__ == "SettingsSwipeResult"]
    assert shown and shown[-1].result["auto"]["winner"] == L1280.label, f"not shown after two looks: {_line(events)}"
    assert app._swipe_shown == "background" and len(_toasts(events)) == 1          # a new suggestion: one toast


def test_a_confirmation_during_the_operators_own_swipe_is_held_back_not_put_aside(real_app,  # noqa: F811
                                                                                  swipe_auto_unlocked):  # noqa: F811
    """The complement, where nothing was dismissed: a look that confirms the suggestion while the operator's own
    swipe runs, and again once it has ended on his verdict, draws nothing over them (b033463) -- and the
    suggestion is not lost: it stands, counted, nothing put aside, its streak on; the line free, the next look
    puts it back on the line, silently.  (In the app only Dismiss frees the line, and a Dismiss is « not now »:
    here the line's free state alone, as ``test_a_line_left_free_by_dismiss_takes_the_silent_redraw`` does.)"""
    app, events = real_app
    live = _live()
    _on(app, live)
    bg = app.swipe_bg
    assert [_judged(app, live, t) for t in (100, 110)] == ["run", "run"]           # shown
    app.swipe._say("collecting", PROGRESS, 0.08)          # « Try other settings »: the worker's first line
    assert app._swipe_shown == "operator"
    del events[:]
    assert _judged(app, live, 120) == "run"               # a look already under way as he pressed it
    assert _line(events) == [], f"the operator's swipe in progress was redrawn over: {_line(events)}"
    app._swipe_on_result({"reason": "keep x @ 1280"}, "")                  # his swipe ends: his verdict
    del events[:]
    assert _judged(app, live, 130) == "run"
    assert _line(events) == [], f"the operator's verdict was redrawn over: {_line(events)}"
    sug = bg.suggestion
    assert sug is not None and sug["seen"] == 4, "the suggestion held back was lost"
    assert bg.auto.snoozed(130.0) is None and bg.auto.policy._streak == 4, "held back was taken for « not now »"
    app.bus.publish(api.SettingsSwipeProgress("dismissed", "dismissed", 0.0))     # the line free
    assert _judged(app, live, 140) == "run"
    shown = [e for e in events if type(e).__name__ == "SettingsSwipeResult"]
    assert shown and shown[-1].result["auto"]["winner"] == L1280.label, \
        f"the suggestion held back did not come back on the free line: {_line(events)}"
    assert app._swipe_shown == "background" and _toasts(events) == [], "not silent: a second toast"


def test_a_line_left_free_by_dismiss_takes_the_silent_redraw(real_app, swipe_auto_unlocked):  # noqa: F811
    """The rule's other half: a suggestion still standing once the line is free (the operator's own verdict on the
    line, then the line free -- here Dismiss's state alone: a real Dismiss says « not now » to the suggestion behind
    his verdict, 558e2c0) is drawn at its next confirmation, silently (no second toast)."""
    app, events = real_app
    live = _live()
    _on(app, live)
    _suggestion_shown(app, events, live)
    app._swipe_on_result({"reason": "keep x @ 1280"}, "")               # his own verdict takes the line
    app.bus.publish(api.SettingsSwipeProgress("dismissed", "dismissed", 0.0))     # Dismiss's state: the line free
    del events[:]
    _look(app, live, streak=3)
    shown = [e for e in events if type(e).__name__ == "SettingsSwipeResult"]
    assert shown and shown[-1].result["auto"]["winner"] == L1280.label, \
        f"the standing suggestion did not come back on the free line: {_line(events)}"
    assert not [e for e in events if type(e).__name__ == "Toast"], "a silent redraw made a toast"
