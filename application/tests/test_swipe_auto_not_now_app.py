"""P6 (Plan Cordee): the technician's Dismiss and Apply on the background swipe's line, and what the app shows of an
automatic switch -- on a real ``WallDanceApp`` (no GUI, no main loop, CPU; the looker thread never started, the
mode set through the app's own door with the ``swipe_auto_unlocked`` fixture: the build does not ship auto).

* one press, one meaning: with the background suggestion on the line, Dismiss is « not now » -- it lifts no ban,
  acknowledges no fault, and Dismiss stays on screen for them; with none on the line it acknowledges the fault and
  lifts the bans (as before);
* Apply on a suggestion judged on another project (or too old) switches nothing: it is withdrawn, one toast;
* a successful automatic switch is a toast in the green of the operator's own Apply, a failure an alert (red)."""
import pytest

from test_seamless_switch import L1280, L1920, _live
from test_swipe_auto_dismiss import _holds
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: F401 - the fixture
from test_swipe_auto_lock import _toasts, real_app  # noqa: F401 - the fixture

FAULT = ("the automatic switch to l @ 1920 x 1056 failed (RuntimeError: x): x @ 1280 still runs - the automatic "
         "swipe will not try yolo11l-pose_1920x1056.engine again until you press Dismiss")
TEXT = "l @ 1280 covers 100 % of the dancers, x @ 1280 33 %: clearly better - Apply now (seen 1 time(s), 2 needed)"


def _on(app, mode):
    """The mode on through the app's door, no looker thread (the test sets what a look would)."""
    app.swipe_bg.start = lambda: None
    app._cb_swipe_auto(mode)
    assert app.swipe_bg.mode == mode


def _suggest(app, live, age_s=10.0):
    """The background suggestion of l @ 1280, judged on ``live`` ``age_s`` ago, on the swipe line."""
    bg = app.swipe_bg
    bg.suggestion = {"kind": "suggest", "winner": L1280.label, "text": TEXT, "winner_c": L1280,
                     "live_key": live.key, "live": live, "wall": bg.wall() - age_s}
    app._swipe_shown = "background"


# ---------------------------------------------------------------------------------------- 3. one meaning
def test_dismiss_on_the_suggestion_is_not_now_and_the_next_press_acknowledges(real_app, swipe_auto_unlocked):  # noqa: F811
    app, events = real_app
    _on(app, "auto")
    bg = app.swipe_bg
    bg._banned[L1920.key] = L1920.engine_file             # held from earlier: a failed automatic switch to it
    app.swipe_switch.fail(FAULT)
    _suggest(app, _live())                                # then the suggestion took the line
    del events[:]
    app._cb_dismiss_swipe()                               # « not now »
    assert app.swipe_switch.fault == FAULT and bg.held(), \
        "the press that said « not now » to the suggestion also lifted the fault and the ban"
    assert bg.suggestion is None and bg.auto.snoozed(app.processor.loop_clock()) == L1280
    assert _holds(events) == [True], "Dismiss left the screen while the fault and the ban still wait for it"
    assert app.swipe.dismissed                            # the operator's own verdict: as before
    app._cb_dismiss_swipe()                               # no suggestion on the line: the acknowledgement
    assert app.swipe_switch.fault is None and not bg.held() and _holds(events) == [True, False]
    assert bg.auto.snoozed(app.processor.loop_clock()) == L1280     # the « not now » holds on


def test_regression_guard_dismiss_over_the_operators_own_verdict_acknowledges_as_before(real_app,  # noqa: F811
                                                                                         swipe_auto_unlocked):  # noqa: F811
    """The operator's swipe's verdict on the line (a background suggestion behind it): its Dismiss is P5's, and the
    fault and the bans are acknowledged as before; the hidden suggestion goes -- since 27/11 « not now » too, its
    setting put aside (``test_swipe_auto_dismiss_behind``)."""
    app, events = real_app
    _on(app, "suggest")
    bg = app.swipe_bg
    _suggest(app, _live())
    app._swipe_shown = "operator"
    bg._banned[L1920.key] = L1920.engine_file
    app.swipe_switch.fail(FAULT)
    del events[:]
    app._cb_dismiss_swipe()
    assert app.swipe.dismissed and app.swipe_switch.fault is None and not bg.held() and bg.suggestion is None
    assert _holds(events) == [False]
    assert bg.auto.snoozed(app.processor.loop_clock()) == L1280


# ------------------------------------------------------------------------ 5. Apply on a stale suggestion
@pytest.mark.parametrize("stale", ["another project", "too old"])
def test_apply_on_a_stale_suggestion_switches_nothing(real_app, swipe_auto_unlocked, stale):  # noqa: F811
    """Loop nit 8: within the 5 s before the looker withdraws it, the previous project's verdict was applied to the
    new one (with the previous project's resident); and a rehearsal's suggestion was still applied at night."""
    app, events = real_app
    _on(app, "auto")
    then = _live()
    _suggest(app, then, age_s=1860.0 if stale == "too old" else 10.0)
    now = _live(project="vendredi1009_mur3", model_id=2) if stale == "another project" else then
    app._swipe_live_setting = lambda: now
    asked = []
    app.swipe_switch.can_switch = lambda winner: None     # seamless: the Apply would switch between two frames
    app.swipe_bg.request_switch = lambda *a, **k: asked.append(("switch", a)) or None
    app.models.request_setting = lambda *a, **k: asked.append(("reload", a)) or None
    del events[:]
    app._cb_apply_settings_swipe()
    assert asked == [], f"a stale suggestion was applied ({stale})"
    why = ("project switched to vendredi1009_mur3 since the suggestion" if stale == "another project" else
           "it is 31 min old")
    assert app.swipe_bg.suggestion is None
    assert _toasts(events) == [f"Swipe Apply: nothing switched - suggestion withdrawn: {why}"]


# ------------------------------------------------------------ 7c. a success is a toast, a failure an alert
def test_a_successful_automatic_switch_is_a_toast_and_a_failure_an_alert(real_app):  # noqa: F811
    app, events = real_app
    text = "switching to l @ 1280: l @ 1280 covers 100 % of the dancers, x @ 1280 33 %"
    app._swipe_notify("switching", {"text": text, "to": "l @ 1280", "from": "x @ 1280"})
    shown = [e for e in events if type(e).__name__ in ("Alert", "Toast")]
    assert [type(e).__name__ for e in shown] == ["Toast"], "a successful automatic switch is shown as an alert"
    assert shown[0].message == text
    assert tuple(shown[0].color) == (120, 220, 140), "not the green of the operator's own Apply success toast"
    del events[:]
    app._swipe_notify("fault", {"text": FAULT})            # regression guard: a failure stays an alert
    assert [type(e).__name__ for e in events if type(e).__name__ in ("Alert", "Toast")] == ["Alert"]
