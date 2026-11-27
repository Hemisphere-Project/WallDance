"""P6 (Plan Cordee): setting Auto to off acknowledges the automatic swipe's own fault (the check of c983cfa, B3,
finding 3) -- on a real ``WallDanceApp`` (no GUI, no main loop, CPU; the mode set through the app's own door with
the ``swipe_auto_unlocked`` fixture: the build ships locked).

A failure of the background's own load or run tells the technician one thing, « set Auto to off ».  After he did,
the red line and the readiness row still said « ... - set Auto to off » until he pressed Dismiss.  Now Auto to off
is that acknowledgement, for that fault only: it goes, its red line, its readiness row and the Dismiss it held go
with it, and its text and detail stay in the log and in /status's ``swipe.switch.last_fault``.  The bans stay, as
before (``set_mode`` keeps them): said nothing while off, said again with Dismiss once a mode is set again.  A
failed Apply or automatic switch is not acknowledged by Auto to off: it still waits for Dismiss."""
import pytest

from test_seamless_switch import L1280, L1920
from test_swipe_auto_dismiss import _holds
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: F401 - the fixture
from test_swipe_auto_lock import real_app  # noqa: F401 - the fixture
from test_swipe_auto_not_now_app import FAULT, _on
from test_swipe_failure_one_instruction import BACKGROUND, BAN, ONE, _apply_load, _background
from test_swipe_failure_one_instruction import _here  # noqa: F401 - the fixture (TensorRT here, the free MB)


def _lines(events):
    return [(e.state, e.text) for e in events if type(e).__name__ == "SettingsSwipeProgress"]


@pytest.mark.parametrize("how", ["load, no room (a sound file, 312 MB free)", "run, an error"])
def test_auto_to_off_acknowledges_the_backgrounds_own_failure(how, real_app, swipe_auto_unlocked,  # noqa: F811
                                                             tmp_path, _here):  # noqa: F811
    """Fails before: after Auto to off the switch still held the fault, the red line stayed (no « dismissed »),
    the readiness row still said « ... - set Auto to off » and Dismiss stayed on screen for it."""
    app, out = real_app
    _on(app, "suggest")
    bg, events, verb, cause, detail = _background(how, "suggest", tmp_path, _here)
    fault = bg.switch.fault
    app.swipe_bg._banned.update(bg._banned)               # the app's own looker, as after that failure
    app.swipe_bg._unloadable.update(bg._unloadable)
    app.swipe_switch.fail(fault)
    assert app._swipe_shown == "apply" and _holds(out)[-1] is True
    assert ONE in app._swipe_readiness_row().detail
    del out[:]
    app._cb_swipe_auto("off")
    assert app.swipe_switch.fault is None, "Auto to off left the background's own fault waiting for Dismiss"
    assert _lines(out) == [("dismissed", "dismissed")] and app._swipe_shown == "", "the red line stayed"
    row = app._swipe_readiness_row()
    assert row is None or (ONE not in row.detail and "Dismiss" not in row.detail), row.detail
    assert _holds(out) == [False], "Dismiss stayed on screen for an acknowledged fault"
    st = app._remote_status()["swipe"]["switch"]
    assert st["fault"] is None and st["fault_detail"] is None
    assert st["last_fault"]["text"] == str(fault)
    for words in BACKGROUND[how][3]:
        assert words in st["last_fault"]["detail"], f"the detail is gone: {st['last_fault']}"
    assert app.swipe_bg.status()["auto_banned"] == [L1280.engine_file]     # the ban kept through off, as before
    del out[:]
    _on(app, "suggest")                                   # a mode again: the ban said again, Dismiss with it
    assert _holds(out) == [True] and BAN in app._swipe_readiness_row().detail


@pytest.mark.parametrize("whose", ["the operator's Apply", "an automatic switch"])
def test_regression_guard_a_failed_switch_still_waits_for_dismiss_after_auto_off(whose, real_app,  # noqa: F811
                                                                               swipe_auto_unlocked,  # noqa: F811
                                                                               tmp_path, _here):  # noqa: F811
    """Auto to off is not what a failed switch asks: its fault, its red line, its readiness row and Dismiss stay
    until Dismiss.  (A guard: it passed before -- nothing was acknowledged by Auto to off then.)"""
    app, out = real_app
    _on(app, "auto")
    if whose == "the operator's Apply":
        fault = _apply_load(tmp_path, _here, "no room").fault
        assert fault.startswith("the switch to l @ 1920 x 1056 failed") and fault.detail
    else:
        fault = FAULT
        app.swipe_bg._banned[L1920.key] = L1920.engine_file
    app.swipe_switch.fail(fault)
    del out[:]
    app._cb_swipe_auto("off")
    assert app.swipe_switch.fault == fault, f"Auto to off acknowledged {whose}'s failure"
    assert _lines(out) == [] and app._swipe_shown == "apply" and _holds(out) == []
    assert "still runs" in app._swipe_readiness_row().detail
    assert app._remote_status()["swipe"]["switch"]["fault"] == str(fault)
    app._cb_dismiss_swipe()
    assert app.swipe_switch.fault is None and _holds(out) == [False] and app._swipe_shown == ""
