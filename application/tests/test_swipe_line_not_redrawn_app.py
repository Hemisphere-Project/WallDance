"""P6, the blind re-read of group B2 (finding 8): the swipe line is not redrawn over what the operator must still
read -- on a real ``WallDanceApp`` (no GUI, no main loop, CPU; the looker thread never started, the mode set through
the app's own door with the ``swipe_auto_unlocked`` fixture: the build ships locked).

A look that names a standing suggestion again redraws its line silently (« ... seen 3 times: Apply now? »).  It did
so whenever the line had last been the background's: over the red line of a FAILED Apply of that very suggestion --
inviting a second Apply that fails the same way -- and over the progress of the operator's own swipe started after
it.  Now the app keeps what the line shows (``_swipe_shown``) from every state published to it: the operator's
swipe running, an Apply and its outcome take it, Dismiss leaves it free; a silent redraw draws only on the
suggestion's own line or a free one.  Held back, the suggestion is not lost: it shows again once the line is free.
"""

from core.swipe_auto import AutoAdvice
from runtime import api
from runtime.seamless_switch import no_room
from test_seamless_switch import L1280, X1280, _live
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: F401 - the fixture
from test_swipe_auto_lock import real_app  # noqa: F401 - the fixture
from test_swipe_auto_not_now import _result

SAID = "l @ 1280 covers 100 % of the dancers, x @ 1280 33 % (7 ms a frame against 6)"
PROGRESS = "collecting 12 / 150 frames with a dancer on the wall"


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

    def request_switch(winner, text, on_done=None):
        asked.update(winner=winner, on_done=on_done)
        return None
    app.swipe_bg.request_switch = request_switch
    app._cb_apply_settings_swipe()
    assert asked["winner"] == L1280
    req = {"winner": L1280, "text": "Apply", "auto": False, "on_done": asked["on_done"], "ticket": object()}
    app.swipe_switch._fail(req, no_room(312.0), X1280, L1280, before=True)
    failed = [e for e in events if type(e).__name__ == "SettingsSwipeProgress" and e.state == "apply_failed"]
    assert failed and app.swipe_switch.fault == failed[-1].text
    return failed[-1].text


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


def test_after_dismiss_the_standing_suggestion_is_shown_again_at_the_next_confirmation(real_app,  # noqa: F811
                                                                                     swipe_auto_unlocked):  # noqa: F811
    """Dismiss on the failure line acknowledges the failure -- it is not « not now » for a suggestion the line no
    longer shows: nothing is put aside, and the next look that names it puts it back on the free line."""
    app, events = real_app
    live = _live()
    _on(app, live)
    _suggestion_shown(app, events, live)
    _failed_apply(app, events)
    _look(app, live)                                      # held back: the failure line stays
    app._cb_dismiss_swipe()
    now = app.processor.loop_clock()
    assert app.swipe_switch.fault is None, "Dismiss did not acknowledge the failed Apply on the line"
    assert app.swipe_bg.auto.snoozed(now) is None, "Dismiss put the suggestion aside: the next look will not name it"
    assert app._swipe_shown == "", "the line Dismiss left free is not free"
    del events[:]
    _look(app, live, streak=3)
    shown = [e for e in events if type(e).__name__ == "SettingsSwipeResult"]
    assert shown and shown[-1].result["auto"]["winner"] == L1280.label, f"not shown again: {_line(events)}"
    assert app._swipe_shown == "background" and app.swipe_bg.suggestion is not None


def test_a_line_left_free_by_dismiss_takes_the_silent_redraw(real_app, swipe_auto_unlocked):  # noqa: F811
    """The rule's other half: a suggestion still standing once the line is free (the operator's own verdict on the
    line, then Dismiss -- here its state alone, the suggestion kept as a Dismiss that only acknowledged his verdict
    would keep it) is drawn at its next confirmation, silently (no second toast)."""
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
