"""P6 (Plan Cordee): a background look tells the reference watch nothing, either way (re-read 5de60e5, lens b2
finding 7; the watch's owner reverses the earlier decision of ad19ebd).

The looker's refusals were not told, but its accepts were: one at every look of mode suggest (every 150 s or so),
each starting the operator's own refusals over, so the watch's « refused » alert hardly ever built.  Now a
background look tells neither; the operator's swipe tells both, as before.

CPU, with the fakes of ``test_swipe_background_refusal_not_counted``.
"""
from types import SimpleNamespace

from runtime.swipe_controller import RefereeReport
from test_swipe_auto_live import _look_frames, _looking_bg
from test_swipe_background_refusal_not_counted import REFUSED, _refusing_pool, calls  # noqa: F401 - fixture


def test_a_background_accept_between_two_refusals_of_the_operator_does_not_start_them_over(calls):
    """The operator is refused twice around a background look that used the plate: the watch hears two refusals
    in a row, no accept between them."""
    bg, proc, events, loader, live = _looking_bg()
    bg.referee = shared = RefereeReport()
    refused = SimpleNamespace(winner=None, reason=REFUSED)
    assert shared.note(refused, _refusing_pool()[0]) == "refusal"
    proc.frames = _look_frames()
    assert bg.tick() == "run" and bg.last_advice["advice"] == "seen"    # the look judged on the plate (named once)
    assert shared.note(refused, _look_frames(30, t0=500.0)) == "refusal"
    assert calls == [("refusal", REFUSED), ("refusal", REFUSED)], "the looker's accept was told to the watch"
