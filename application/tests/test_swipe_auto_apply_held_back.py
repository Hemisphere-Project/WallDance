"""P6 (Plan Cordee): a background suggestion is advice only until its Apply is released
(``core.config.SWIPE_AUTO_APPLY_RELEASED``, False as the build ships).

The reviewers' condition of use for the first rehearsal with dancers: the technician never presses Apply on a
suggestion -- he notes the time and the line.  It was a rule on paper beside a button on screen, in front of a man
alone; now the build holds it for him:

* the suggestion's line says « ..., seen twice: note the time and this line », no « Apply now? »; no Apply button
  on it, Dismiss stays (« not now », as before); its one toast says « (note the time and the line) »;
* every door that would apply it switches nothing and says why, once: the app's Apply reached with the suggestion
  on the line (a click on a button drawn before, the remote ApplySettingsSwipe), seamless or reload; and
  ``BackgroundSwipe.request_switch`` asked for the suggestion;
* released (the fixture ``swipe_auto_apply_released``): today's line, button and Apply;
* the operator's own swipe and its Apply do not depend on it.

On a real ``WallDanceApp`` (no main loop, CPU; the looker thread never started, the looks through the policy's own
advice as in ``test_swipe_line_not_redrawn_app``) and the swipe panel headless.  Each test failed before for the
reason it names; the regression guards say so."""
from types import SimpleNamespace

import pytest

from test_seamless_switch import L1280
from test_swipe_auto_live import swipe_auto_apply_released  # noqa: F401 - the fixture
from test_swipe_auto_lock import TIP_END, _combo, _toasts, _wall_panel, panel, real_app  # noqa: F401 - fixtures
from test_swipe_keyoff import _live_model
from test_swipe_keyoff import _on as _mode_on
from test_swipe_line_not_redrawn_app import _judged, _on
from test_seamless_switch import _live

# the strings on screen, verbatim (the operator's guide quotes them)
SAID = "l @ 1280 covers 100 % of the dancers, x @ 1280 33 % (6.0 ms a frame against 6.0)"
ADVICE_LINE = f"{SAID}, seen twice: note the time and this line"
INVITING_LINE = f"{SAID}, seen twice: Apply now?"
ADVICE_TOAST = "A better model may be available - see 'Try other settings' (note the time and the line)"
INVITING_TOAST = "A better model may be available - see 'Try other settings' (Apply now)"
REFUSED = "Swipe Apply: nothing switched - a suggestion is advice only in this build: note the time and the line"
HELD_BACK = "a suggestion is advice only in this build: note the time and the line"


def _suggested(app):
    """Auto: suggest; two looks in a row name l @ 1280 through the policy's own advice: the suggestion on the line."""
    live = _live()
    _on(app, live)
    assert _judged(app, live, 100.0) == "run" and app.swipe_bg.suggestion is None      # seen once: nothing shown
    assert _judged(app, live, 200.0) == "run" and app.swipe_bg.suggestion is not None
    assert app._swipe_shown == "background"
    return live


def _results(events):
    return [e.result for e in events if type(e).__name__ == "SettingsSwipeResult"]


def _panel(results):
    """Each verdict published to the swipe panel through ``WallDanceGUI.show_swipe_result`` (headless): (first line,
    Apply shown, Apply's label, Dismiss shown, the line's colour)."""
    dpg = pytest.importorskip("dearpygui.dearpygui")
    import gui_builder
    from gui import WallDanceGUI
    seen = []
    dpg.create_context()
    try:
        noop = lambda *a, **k: None  # noqa: E731
        mock = SimpleNamespace(config={}, _on_check_readiness=noop, _on_calibrate=noop, _on_capture_plate=noop,
                               _on_dryrun=noop, _on_swipe=noop, _on_swipe_apply=noop, _on_swipe_auto=noop,
                               _on_swipe_dismiss=noop, _btn_standby_theme=dpg.add_theme())
        with dpg.window(label="held back"):
            gui_builder.build_phase_wall(mock)
        g = SimpleNamespace()
        for res in results:
            WallDanceGUI.show_swipe_result(g, res, "")
            apply = dpg.get_item_configuration("swipe_apply_btn")
            colour = tuple(round(255 * c) for c in dpg.get_item_configuration("swipe_status_text")["color"][:3])
            seen.append((dpg.get_value("swipe_status_text").splitlines()[0], apply["show"], apply["label"],
                         dpg.get_item_configuration("swipe_dismiss_btn")["show"], colour))
    finally:
        dpg.destroy_context()
    return seen


def _watched(app, seamless=True):
    """Every way into a switch watched: the switch's request (seamless), the reload, the switch's worker."""
    asked = []
    app.swipe_switch.can_switch = (lambda w: None) if seamless else (lambda w: "its engine is not built")
    app.swipe_switch.start = lambda: asked.append(("worker",))
    real = app.swipe_bg.request_switch
    app.swipe_bg.request_switch = lambda *a, **k: asked.append(("switch", a[0], k.get("suggestion"))) or real(*a, **k)
    app.models.request_setting = lambda *a, **k: asked.append(("reload", a[0])) or None
    return asked


# -------------------------------------------------------------------------------------------- the flag itself
def test_the_build_ships_the_suggestions_apply_held_back():
    """Fails before: no such flag -- Apply on a suggestion was open in every build."""
    import core.config
    from core import swipe_auto
    assert core.config.SWIPE_AUTO_APPLY_RELEASED is False
    assert swipe_auto.apply_released() is False


def test_regression_guard_releasing_the_apply_releases_no_mode(swipe_auto_apply_released):  # noqa: F811
    """The flag opens the suggestion's Apply, nothing else: mode auto stays out of this build.  (Before: no flag --
    it errors in the fixture.)"""
    from core import swipe_auto
    assert swipe_auto.apply_released() is True
    assert swipe_auto.MODE_AUTO not in swipe_auto.released_modes()


# ----------------------------------------------------------------------- held back (the shipped value): advice
def test_a_suggestion_is_advice_only_its_line_says_note_it_no_apply_button_dismiss_stays(real_app):  # noqa: F811
    """Fails before: the line asked « Apply now? », the Apply button « Apply l @ 1280 » beside it, the toast
    « (Apply now) »."""
    app, events = real_app
    _suggested(app)
    assert app.swipe_bg.suggestion["text"] == ADVICE_LINE
    shown = _results(events)
    assert len(shown) == 1 and shown[0]["auto"]["text"] == ADVICE_LINE and shown[0]["advice_only"] is True
    assert [t for t in _toasts(events) if t.startswith("A better model")] == [ADVICE_TOAST]
    from gui_constants import WARN_AMBER
    ((line, apply_shown, _, dismiss_shown, colour),) = _panel(shown)
    assert line == ADVICE_LINE and "Apply" not in line
    assert apply_shown is False, "an Apply button on a suggestion this build holds back"
    assert dismiss_shown is True, "Dismiss (« not now ») went with the Apply button"
    assert colour == WARN_AMBER                           # still a suggestion: the line he must read and note


def test_a_look_naming_it_again_keeps_the_advice(real_app):  # noqa: F811
    """A third look naming it: the line redrawn silently, still advice (« seen 3 times: note the time and this
    line »), no second toast, no Apply.  Fails before: « seen 3 times: Apply now? » and its button."""
    app, events = real_app
    live = _suggested(app)
    del events[:]
    assert _judged(app, live, 300.0) == "run"
    shown = _results(events)
    assert len(shown) == 1 and shown[0]["auto"]["text"] == f"{SAID}, seen 3 times: note the time and this line"
    assert shown[0]["advice_only"] is True and _toasts(events) == []
    assert [s[1] for s in _panel(shown)] == [False]


@pytest.mark.parametrize("seamless", [True, False], ids=["seamless", "reload"])
@pytest.mark.parametrize("door", ["click", "remote"])
def test_an_apply_that_still_reaches_the_suggestion_switches_nothing_and_says_why(real_app, door,  # noqa: F811
                                                                                  seamless):
    """No Apply button on the line, yet a press that still reaches the app's Apply with the suggestion on it -- a
    click on a button drawn before (the operator's verdict's, as the suggestion took the line), the remote
    ApplySettingsSwipe (STANDBY: heavy) -- switches nothing, seamless or reload: one toast says why, and the
    suggestion stays on the line as advice.  Fails before: it was applied (the switch asked, or the reload)."""
    from runtime.api import SystemState
    app, events = real_app
    _suggested(app)
    app.system_state = SystemState.STANDBY
    asked = _watched(app, seamless)
    del events[:]
    if door == "click":
        app._cb_apply_settings_swipe()
    else:
        from services.remote_api import RemoteApi
        remote = RemoteApi(app.api, app.bus, token="t", roots={}, state_fn=lambda: "standby")
        remote.control_enabled = True
        assert remote.submit("ApplySettingsSwipe", {})[0] == 200
        app.api.drain()
    assert asked == [], f"the suggestion was applied ({door}, {'seamless' if seamless else 'reload'}): {asked}"
    assert _toasts(events) == [REFUSED]
    assert app.swipe_switch.busy() is None and app._swipe_pending_apply is None
    assert app.swipe_bg.suggestion is not None and app._swipe_shown == "background"     # still there: advice


def test_request_switch_refuses_the_suggestions_apply(real_app):  # noqa: F811
    """``BackgroundSwipe.request_switch`` asked for the suggestion: refused while held back, nothing asked of the
    switch, nothing released.  Fails before: no such door guard (the keyword was unknown: TypeError)."""
    app, events = real_app
    _suggested(app)
    asked = []
    app.swipe_switch.can_switch = lambda w: None
    app.swipe_switch.request = lambda *a, **k: asked.append(a) or None
    app.swipe_switch.release = lambda *a, **k: asked.append(("release", a)) or None
    why = app.swipe_bg.request_switch(L1280, "switching to l @ 1280 (Apply)", suggestion=True)
    assert why == HELD_BACK and asked == []


def test_regression_guard_dismiss_on_the_advice_is_not_now_as_before(real_app):  # noqa: F811
    """Dismiss beside the advice: « not now » -- the suggestion goes, that setting put aside (the snooze).  (Passes
    before as well: Dismiss was there beside the Apply button.)"""
    app, events = real_app
    _suggested(app)
    app._cb_dismiss_swipe()
    assert app.swipe_bg.suggestion is None
    assert app.swipe_bg.auto.snoozed(app.processor.loop_clock()) == L1280


# ------------------------------------------------------------------------------- released: today's behaviour
def test_regression_guard_released_the_suggestion_invites_and_its_apply_switches(real_app,  # noqa: F811
                                                                                 swipe_auto_apply_released):  # noqa: F811
    """With its Apply released: « Apply now? », the button « Apply l @ 1280 » and Dismiss, the toast « (Apply now) »;
    Apply switches between two frames, ``request_switch`` told it is the suggestion's.  (Before: no flag -- it errors
    in the fixture.)"""
    from runtime.api import SystemState
    app, events = real_app
    asked = _watched(app)                                 # the winner's engine built: Apply switches between frames
    _suggested(app)
    assert app.swipe_bg.suggestion["text"] == INVITING_LINE
    assert [t for t in _toasts(events) if t.startswith("A better model")] == [INVITING_TOAST]
    app.system_state = SystemState.RUN
    shown = _results(events)
    assert len(shown) == 1 and "advice_only" not in shown[0]
    ((line, apply_shown, label, dismiss_shown, _),) = _panel(shown)
    assert (line, apply_shown, label, dismiss_shown) == (INVITING_LINE, True, "Apply l @ 1280", True)
    del events[:]
    app._cb_apply_settings_swipe()
    assert asked == [("switch", L1280, True), ("worker",)]
    assert app.swipe_switch.busy() == "a switch is already under way"
    assert [(e.state, e.text) for e in events if type(e).__name__ == "SettingsSwipeProgress"] == [
        ("applying", "switching to l @ 1280 between two frames...")]


# ---------------------------------------------------------------------------- the operator's own swipe: as before
def _his_verdict(app):
    """The operator pressed « Try other settings »; his swipe ended on a switch to l @ 1280, its result on the line
    -- the background suggestion standing behind it."""
    result = SimpleNamespace(switch=True, winner=L1280)
    app.swipe.outcome = SimpleNamespace(result=result, to_dict=lambda: {"switch": True, "winner": L1280.label,
                                                                        "reason": "l @ 1280 covers 91 %"})
    app.swipe.applicable = lambda live_now: (None, L1280, None)
    app._swipe_on_result(app.swipe.outcome.to_dict(), "")
    assert app._swipe_shown == "operator"


@pytest.mark.parametrize("seamless", [True, False], ids=["seamless", "reload"])
def test_regression_guard_the_operators_own_verdict_keeps_its_apply_while_suggestions_are_advice(real_app,  # noqa: F811
                                                                                                 seamless):
    """Held back (the shipped value), Auto: suggest, a background suggestion behind his verdict: his line keeps its
    Apply button and Dismiss; his Apply in RUN switches between two frames (``request_switch`` as before, not told
    it is a suggestion), or, the winner's engine not built, the STANDBY rule; in STANDBY the reload.  (Passes
    before as well.)"""
    from runtime.api import SystemState
    app, events = real_app
    _mode_on(app, "suggest")
    _live_model(app)
    app.swipe_bg.suggestion = {"kind": "suggest", "winner": L1280.label, "text": ADVICE_LINE, "winner_c": L1280,
                               "live_key": app._swipe_live_setting().key, "live": app._swipe_live_setting(),
                               "wall": app.swipe_bg.wall()}
    if not seamless:
        app.swipe_switch.engine_exists = lambda path: False
    del events[:]
    _his_verdict(app)
    shown = _results(events)
    assert len(shown) == 1 and "advice_only" not in shown[0]
    ((_, apply_shown, label, dismiss_shown, _),) = _panel(shown)
    assert apply_shown is True and dismiss_shown is True
    assert label == ("Apply l @ 1280" if seamless else
                     "Apply l @ 1280\n(STANDBY: a few seconds\nwithout tracking)")
    asked = _watched(app, seamless)
    app.system_state = SystemState.RUN
    del events[:]
    app._cb_apply_settings_swipe()
    if seamless:
        assert asked == [("switch", L1280, None), ("worker",)]
        assert _toasts(events) == []
    else:
        assert asked == []
        assert _toasts(events) == ["Apply: stop the show first - this Apply reloads the model (its engine is not "
                                   "built), a few seconds without tracking"]
        app.system_state = SystemState.STANDBY
        app._cb_apply_settings_swipe()
        assert asked == [("reload", L1280.model)]


TIP_ADVICE = ("  suggest  while dancers are on the wall, it tries the other model\n"
              "           at the same network input and says so here when it does\n"
              "           better on two looks in a row and is no more than 15 %\n"
              "           slower, or is clearly better (5 points) on both looks\n"
              "           and a frame with it would still fit the 15 fps\n"
              "           floor: advice only - note the time and the line\n"
              "           (Dismiss: not now, 15 min)\n"
              "           (the default)\n")


TIP_SWITCH = ("  switch   the same, and when the other model is clearly better it\n"
              "           switches between two frames (an alert only if it fails)\n")


@pytest.mark.parametrize("released,more", [(None, ""), (("off", "suggest", "auto"), TIP_SWITCH)],
                         ids=["shipped", "all"])
def test_the_combos_tooltip_promises_no_apply_while_it_is_held_back(panel, monkeypatch, released, more):  # noqa: F811
    """The combo's tooltip, the build as it ships: suggest's paragraph says what a suggestion is now -- advice: the
    time and the line to note, Dismiss for « not now », 15 min -- and offers no Apply.  Fails before: « (Apply now, or
    Dismiss: not now, 15 min) », a button the line no longer has."""
    import core.config
    if released is not None:
        monkeypatch.setattr(core.config, "SWIPE_AUTO_RELEASED", released)
    _wall_panel(panel)
    tip = _combo(panel)[1]
    assert tip == ("The automatic swipe, while the show runs:\n  off      nothing in the background\n"
                   + TIP_ADVICE + more + TIP_END)
    assert "Apply" not in tip
