"""P6 (Plan Cordee): the Apply button on screen follows the automatic swipe's mode at once.

With 'Auto: suggest' the operator's swipe names a winner: « Apply l @ 1280 », the seamless tooltip.  The key goes
off: a press now runs P5's Apply (refused in RUN, a reload in STANDBY), so the button says P5's words from that
moment, not at the next verdict.  On a real ``WallDanceApp`` (no GUI, CPU); the operator's verdict is stubbed."""
from types import SimpleNamespace

import pytest

from test_seamless_switch import L1280
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: F401 - the fixture
from test_swipe_auto_lock import real_app  # noqa: F401 - the fixture

P5_LABEL = "Apply l @ 1280 (STANDBY)"


def _verdict(app):
    """The operator's swipe ended on a switch to l @ 1280, its result on screen."""
    result = SimpleNamespace(switch=True, winner=L1280)
    app.swipe.outcome = SimpleNamespace(result=result, to_dict=lambda: {"switch": True, "winner": L1280.label,
                                                                        "reason": "l @ 1280 covers 91 %"})
    app._swipe_shown = "operator"


def _results(events):
    return [e.result for e in events if type(e).__name__ == "SettingsSwipeResult"]


def test_key_on_to_off_refreshes_the_apply_button_to_p5s(real_app, swipe_auto_unlocked):  # noqa: F811
    """Fails before: no new result reaches the panel, the button keeps P6's label and tooltip."""
    import app as appmod
    app, events = real_app
    app._cb_swipe_auto("suggest")
    _verdict(app)
    del events[:]
    app._cb_swipe_auto("off")
    shown = _results(events)
    assert len(shown) == 1 and shown[0]["winner"] == L1280.label and shown[0]["switch"]
    assert shown[0]["apply_label"] == P5_LABEL and shown[0]["apply_tip"] == appmod.SWIPE_APPLY_TIP_P5
    assert shown[0]["apply_seamless"] is False


def test_no_button_on_screen_nothing_is_published(real_app, swipe_auto_unlocked):  # noqa: F811
    """Regression guard: no verdict, the background's line, an applied winner, the same mode: nothing redrawn."""
    app, events = real_app
    app._cb_swipe_auto("suggest")
    del events[:]
    app._cb_swipe_auto("off")                                      # no verdict on screen
    _verdict(app)
    app._swipe_shown = "background"
    app._cb_swipe_auto("suggest")                                  # the background's suggestion: its own line
    app._swipe_shown = "operator"
    app.swipe.applied = L1280
    app._cb_swipe_auto("off")                                      # applied: the button is gone
    app.swipe.applied = None
    app._cb_swipe_auto("off")                                      # the same mode: nothing changed
    assert _results(events) == []
