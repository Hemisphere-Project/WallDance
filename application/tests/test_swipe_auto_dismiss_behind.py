"""P6 (Plan Cordee): Dismiss means « not now » for the background suggestion whichever of the two verdicts the swipe
line shows (the blind re-read of 4a30f39, lens B2, finding 4).

With the operator's own verdict on the line (« Try other settings ») and a background suggestion behind it,
Dismiss dropped the background suggestion with no snooze and the policy's streak kept: the next look showed it
again with a new toast.  Now it is put aside for the snooze (15 min) and its streak ends, as when the line shows
it; the faults and the bans are acknowledged as before.  An Apply's own drop of the suggestion puts nothing aside
(regression guard).  CPU, the app's handler on a fake app, the looker on the fakes of ``test_swipe_auto_live``."""
from types import SimpleNamespace

import pytest

from test_seamless_switch import L1280
from test_swipe_auto_rollback import _look, _Tap
from test_swipe_auto_suggestion import _events, _evening_bg, _toasts


def _runs(bg, proc, tap, start, stop):
    return [t for t in range(start, stop) if _look(bg, proc, tap, t) == "run"]


def _app(bg):
    """The app's Dismiss (``WallDanceApp._cb_dismiss_swipe``) on a fake app: the operator's verdict on the line."""
    pytest.importorskip("dearpygui")
    import app as appmod
    published = []
    fake = SimpleNamespace(bus=SimpleNamespace(publish=published.append), swipe_bg=bg, swipe_switch=bg.switch,
                           swipe=SimpleNamespace(dismiss=lambda: None), _swipe_shown="operator")
    return (lambda: appmod.WallDanceApp._cb_dismiss_swipe(fake)), published


def test_dismiss_over_the_operators_verdict_puts_the_suggestion_behind_it_aside():
    """b2r/probe_one_look.py case 2: shown at 300 s, the operator's verdict takes the line, Dismiss at 350 s.  The
    looks after it wait (« ... was dismissed - it looks again at HH:MM »), no new toast until the snooze is over
    (15 min of the loop clock), and then two looks in a row again.  Fails before: a new toast at the next look
    (600 s), the streak kept."""
    from core.config import SWIPE_AUTO_SNOOZE_S
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(4000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300] and len(_toasts(events)) == 1
    dismiss, published = _app(bg)
    proc.clock = 350.0
    dismiss()
    assert bg.suggestion is None and bg.auto.snoozed(350.0) == L1280, "the suggestion behind was not put aside"
    assert bg.auto.policy._streak == 0
    assert [type(e).__name__ for e in published] == ["SettingsSwipeProgress", "SettingsSwipeHold"]
    end = 350.0 + SWIPE_AUTO_SNOOZE_S
    assert _runs(bg, proc, tap, 351, int(end)) == [] and len(_toasts(events)) == 1, "shown again within the snooze"
    assert bg.status()["last_look"]["outcome"].startswith("the automatic swipe waits: l @ 1280 was dismissed")
    first = next(t for t in range(int(end), 4000) if _look(bg, proc, tap, t) == "run")
    assert bg.suggestion is None and bg.last_advice["advice"] == "seen", f"shown at once after the snooze ({first} s)"


def test_regression_guard_dismiss_over_the_operators_verdict_still_acknowledges_the_fault_and_the_bans():
    bg, proc, loader, live = _evening_bg()
    tap = _Tap(range(4000))
    _runs(bg, proc, tap, 1, 301)
    bg._banned["some-key"] = "some.engine"
    bg.switch.fail("the last automatic switch failed")
    dismiss, published = _app(bg)
    dismiss()
    assert not bg.held() and bg.switch.fault is None
    assert [e.held for e in published if type(e).__name__ == "SettingsSwipeHold"] == [False]


def test_regression_guard_an_apply_drops_the_suggestion_without_putting_it_aside():
    """The Apply that reloads (STANDBY) drops the suggestion it applies (``dismiss(not_now=False)``): nothing is put
    aside -- the setting is about to run."""
    bg, proc, loader, live = _evening_bg()
    tap = _Tap(range(4000))
    _runs(bg, proc, tap, 1, 301)
    assert bg.dismiss(not_now=False) is False
    assert bg.suggestion is None and bg.auto.snoozed(proc.clock) is None
