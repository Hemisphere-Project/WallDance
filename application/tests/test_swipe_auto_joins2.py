"""P6 (Plan Cordee): where « a suggestion is shown once, on two verdicts in a row » meets the rest of the panel.

The combo's tooltip still told the old flow: « each time two and a half minutes of dancing are kept, it tries the
other model ... and says so here when it does better (Apply now, or Dismiss) ».  Since 27/11 one look shows nothing,
the same winner on two looks in a row is shown, never a slower one, and Dismiss on it means not now (for
``SWIPE_AUTO_SNOOZE_S``).  The tooltip says that, still only for the modes the build releases.  Headless DearPyGui,
the lock's own panel."""
import pytest

from test_swipe_auto_live import swipe_auto_suggest_only, swipe_auto_unlocked  # noqa: F401 - the fixtures
from test_swipe_auto_lock import _combo, _wall_panel, panel  # noqa: F401 - the fixture

SUGGEST_TIP = ("  suggest  while dancers are on the wall, it tries the other model\n"
               "           at the same network input and says so here when it does\n"
               "           better on two looks in a row and is no more than 15 %\n"
               "           slower (Apply now, or Dismiss: not now, 15 min)\n")


@pytest.mark.parametrize("released", ["shipped", "suggest only", "all"])
def test_the_combos_tooltip_says_when_a_suggestion_shows_and_what_dismiss_does(panel, released, request):  # noqa: F811
    """Fails before: « says so here when it does better (Apply now, or Dismiss) », every two and a half minutes."""
    from core.config import SWIPE_AUTO_SNOOZE_S
    if released != "shipped":                    # shipped: off and suggest, nothing patched
        request.getfixturevalue("swipe_auto_suggest_only" if released == "suggest only" else "swipe_auto_unlocked")
    dpg = panel
    _wall_panel(dpg)
    _, tip = _combo(dpg)
    assert SUGGEST_TIP in tip, f"the tooltip tells the old flow: {tip!r}"
    assert SWIPE_AUTO_SNOOZE_S == 15 * 60.0, "the tooltip's « not now, 15 min » is no longer the snooze"
    assert ("  switch   " in tip) == (released == "all"), "the tooltip describes a mode the combo does not offer"
    assert max(len(line) for line in tip.splitlines()) <= 68, "a tooltip line wider than the others"
