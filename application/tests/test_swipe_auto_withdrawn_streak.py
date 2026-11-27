"""P6 (Plan Cordee): whatever withdraws a suggestion because the comparison it was made on no longer holds ends the
policy's streak with it (the blind re-read of 4a30f39, lens B2, finding 3).

A change of the excluded cells changes the live setting's key but not the scene: the suggestion went (« the setting
changed since the suggestion ») while the streak lived on, so the next look naming the same winner made a new
suggestion and a new toast -- judged once on the new setting.  The same held for a verdict seen once before the
change, and for the operator's Apply that found the setting changed.  The paths that ended it already (a new
scene, a switch, the mode off, a ban, Dismiss) are regression guards.  CPU, with the fakes of
``test_swipe_auto_live``, ``test_swipe_auto_rollback`` and ``test_swipe_auto_suggestion``."""
import dataclasses

from test_swipe_auto_rollback import _look, _Tap
from test_swipe_auto_suggestion import _events, _evening_bg, _toasts


def _runs(bg, proc, tap, start, stop):
    return [t for t in range(start, stop) if _look(bg, proc, tap, t) == "run"]


def _cleared(events):
    return [p["text"] for k, p in events if k == "cleared"]


def test_the_excluded_cells_change_under_a_suggestion_two_looks_again_before_the_next():
    """b2r/probe_one_look.py case 1: shown at 300 s, the excluded cells edited at 400 s (withdrawn), the next look
    (600 s) names the same winner: seen once, nothing shown; the look after it shows it.  Fails before: a new
    suggestion and a second toast at 600 s, after one look on the new cells."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300] and len(_toasts(events)) == 1
    live["v"] = dataclasses.replace(live["v"], exclusion=((8, 6), (3, 4)))
    assert _runs(bg, proc, tap, 301, 401) == [] and bg.suggestion is None
    assert _cleared(events) == ["suggestion withdrawn: the setting changed since the suggestion"]
    assert _runs(bg, proc, tap, 401, 601) == [600]
    assert bg.suggestion is None and len(_toasts(events)) == 1, "shown again after one look on the new cells"
    assert bg.last_advice["advice"] == "seen" and bg.last_advice["streak"] == 1
    assert _runs(bg, proc, tap, 601, 751) == [750] and bg.suggestion is not None and len(_toasts(events)) == 2


def test_the_excluded_cells_change_after_a_verdict_seen_once_it_counts_no_more():
    """Seen once at 150 s, the cells edited at 200 s, named again at 300 s: seen once again (one look on the new
    cells), shown at 450 s.  Fails before: shown at 300 s."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 151) == [150] and bg.last_advice["advice"] == "seen"
    live["v"] = dataclasses.replace(live["v"], exclusion=((8, 6), (3, 4)))
    assert _runs(bg, proc, tap, 151, 301) == [300]
    assert bg.suggestion is None and _toasts(events) == [], "shown on one verdict on the new cells"
    assert _runs(bg, proc, tap, 301, 451) == [450] and bg.suggestion is not None


def test_an_apply_that_finds_the_setting_changed_ends_the_streak_too():
    """The operator's Apply asks first whether the suggestion still stands (``withdraw_stale(live, setting=True)``):
    the live model was reloaded since (another ``model_id``, the same setting) -- withdrawn, and the streak with it:
    the next look naming it is seen once.  Fails before: a new suggestion and a toast at that look."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300]
    live["v"] = dataclasses.replace(live["v"], model_id=live["v"].model_id + 1)
    assert bg.withdraw_stale(live["v"], setting=True) is not None and bg.suggestion is None
    assert _runs(bg, proc, tap, 301, 601) == [600]
    assert bg.suggestion is None and len(_toasts(events)) == 1, "shown again after one look"


def test_regression_guard_a_new_scene_ends_the_streak():
    """Shown at 300 s, the ROI moved at 400 s (a new scene: the policy starts over, the pool too): the looks after
    it need two verdicts again before anything is shown."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300]
    live["v"] = dataclasses.replace(live["v"], roi=(True, 0, 0, 10, 10))
    proc.clock = 400.0
    proc.frames = tap.kept(400.0)
    bg.tick()
    assert bg.suggestion is None and bg.auto.policy._streak == 0
    first = next(t for t in range(401, 1200) if _look(bg, proc, tap, t, after=400.0) == "run")
    assert first == 551 and bg.suggestion is None and bg.last_advice["advice"] == "seen"
    assert len(_toasts(events)) == 1
    second = next(t for t in range(first + 1, 1200) if _look(bg, proc, tap, t, after=400.0) == "run")
    assert second == 701 and bg.suggestion is not None and len(_toasts(events)) == 2
