"""P6 (Plan Cordee): a suggestion the looks no longer confirm goes within about five minutes (the blind re-read of
4a30f39, lens B2, finding 1).

A suggestion is withdrawn after two looks in a row that do not confirm it, but those looks were far apart: after a
shown suggestion the looks back off (300 s doubling), and after a look the referee could not judge the next one
waited 900 s -- the reviewer's probe (the referee refusing every look after a suggestion) left « Apply now » on
screen for 1,050 to 1,800 s, often until its 30-min age withdrew it.  Now, while a suggestion stands:

1. a look that does not confirm it -- the referee's refusal, a « keep », another winner, a refusal of the « slower »
   rule, a look the pre-filter skipped (no room for a gain) -- takes « Apply now? » and the Apply button off the
   line at once (« ... - not confirmed at the last look (HH:MM) »), and the next look comes as soon as 150 fresh
   frames are kept; the second one withdraws it;
2. a look that confirms it in between restores the inviting line, and the count starts over;
3. an Apply that still reaches it (pressed as its line changed, or from afar) switches nothing.

Each test failed before its fix for the reason it names.  CPU, with the fakes of ``test_swipe_auto_live``,
``test_swipe_auto_rollback`` and ``test_swipe_auto_suggestion``."""
import dataclasses
from types import SimpleNamespace

import pytest

from core.swipe import Candidate, SwipeResult, SwipeRow
from test_seamless_switch import L1280, X1280
from test_swipe_auto_live import ON_HER
from test_swipe_auto_rollback import _look, _Tap
from test_swipe_auto_suggestion import LINE, WITHDRAWN, _events, _evening_bg, _toasts

LOOSE = (20.0, 20.0, 80.0, 80.0, 0.9)     # a box where the empty-wall picture shows nobody (the reviewer's)
SAYS = LINE.removesuffix(", seen twice: Apply now?").replace(" covers ", " covered ", 1)
NOT_CONFIRMED = " - not confirmed at the last look ("


class _RefusedTap(_Tap):
    """The tap's frames; from ``refuse_after`` on, the live setting puts a box on nobody too (with the candidate's
    own: the referee refuses the empty-wall picture -- every setting puts boxes where it shows nobody)."""

    def __init__(self, times, refuse_after):
        super().__init__(times)
        self.frames = [dataclasses.replace(f, boxes=tuple(f.boxes) + (LOOSE,)) if f.t > refuse_after else f
                       for f in self.frames]


def _runs(bg, proc, tap, start, stop):
    return [t for t in range(start, stop) if _look(bg, proc, tap, t) == "run"]


def _lines(events):
    """The lines a look sent for the suggestion on screen without a toast: (its text, its winner)."""
    return [(p["text"], p.get("winner")) for k, p in events if k == "suggest" and p.get("again")]


def _withdrawn(events):
    return [p["text"] for k, p in events if k == "cleared"]


# --------------------------------------------------------------------------------------- 1. the reviewer's probe
def test_the_reviewers_probe_a_referee_refusing_every_look_withdraws_it_within_about_five_minutes():
    """b2r/probe_refuse_late.py: shown at 300 s, confirmed at 600 s (the looks back off 600 s), then the referee
    refuses every look.  The first refused look (1200 s) takes « Apply now? » and the Apply button off the line;
    the next look comes 150 fresh frames later, not 900 s later; it withdraws it.  Fails before: the line still
    said « Apply now? » after the first refusal, and the second came 900 s after it (« Apply now » 1,500 s on
    screen through refusals in the reviewer's runs)."""
    from runtime.swipe_controller import REFEREE_WAIT
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _RefusedTap(range(4000), refuse_after=600)
    assert _runs(bg, proc, tap, 1, 601) == [150, 300, 600] and bg.suggestion["seen"] == 3
    loader.infer_boxes = [ON_HER, LOOSE]                  # the candidate puts a box on nobody too
    n = len(events)
    first = _runs(bg, proc, tap, 601, 1201)
    assert first == [1200] and bg.status()["last_look"]["outcome"] == REFEREE_WAIT
    assert bg.suggestion is not None and bg.suggestion["unconfirmed"] == 1
    (text, winner), = _lines(events[n:]) or [(None, "no line sent")]
    assert winner is None, "the refused look left the Apply button on the line (the event still names a winner)"
    assert text.startswith(SAYS) and NOT_CONFIRMED in text and "Apply now" not in text, text
    assert bg.suggestion["text"] == text
    second = next((t for t in range(1201, 3000) if _look(bg, proc, tap, t) == "run"), None)
    assert second is not None and second - 1200 <= 330, f"the second look came {second - 1200} s after the first"
    assert bg.suggestion is None and _withdrawn(events) == [WITHDRAWN]
    assert len(_toasts(events)) == 1


# --------------------------------------------------------------------------- 1. each kind of look that does not
def _refuse_speed(bg, proc, loader, monkeypatch):
    from test_swipe_auto_speed import _timed
    _timed(bg, proc, loader, [30.0], [15.0])              # twice the live model's time: the « slower » rule


def _keep(bg, proc, loader, monkeypatch):
    loader.infer_boxes = []                               # the other model sees nobody: the referee keeps x


def _other_winner(bg, proc, loader, monkeypatch):
    import runtime.swipe_background as sb
    other = Candidate("yolo11m-pose", 1280, None)
    rows = [SwipeRow(other, 0.99, 0.0, None, gain=0.6), SwipeRow(X1280, 0.33, 0.0, None)]
    monkeypatch.setattr(sb, "score_runs", lambda *a, **k: SwipeResult(X1280, other, True, rows,
                                                                       "m @ 1280 covers 99 % of the dancers"))


def _no_room(bg, proc, loader, monkeypatch):
    monkeypatch.setattr(bg.auto, "room", lambda *a, **k: -0.01)     # the pre-filter: no gain could clear the bar


@pytest.mark.parametrize("kind", [_keep, _other_winner, _refuse_speed, _no_room],
                         ids=["keep", "another winner", "slower", "no room"])
def test_each_look_that_does_not_confirm_it_counts_and_brings_the_next_one_soon(kind, monkeypatch):
    """Shown at 300 s (the looks back off 300 s); from then each look does not confirm it.  The first (600 s)
    changes the line, the next comes 150 fresh frames later (750 s) and withdraws it.  Fails before: the line kept
    « Apply now? », and the back-off doubled (the second look at 1200 s) -- a skipped look did not count at all."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300] and bg.suggestion["text"] == LINE
    kind(bg, proc, loader, monkeypatch)
    n = len(events)
    for t in range(301, 601):
        _look(bg, proc, tap, t)
    assert bg.suggestion is not None and bg.suggestion["unconfirmed"] == 1, "the first look that did not confirm it"
    (text, winner), = _lines(events[n:]) or [(None, "no line sent")]
    assert winner is None and NOT_CONFIRMED in text and "Apply now" not in text, (text, winner)
    if kind is _refuse_speed:
        assert text.endswith("): it is more than 15 % slower (30 ms a frame against 15)"), text
    for t in range(601, 751):
        _look(bg, proc, tap, t)
    assert bg.suggestion is None, "the second look that did not confirm it did not come 150 fresh frames later"
    assert _withdrawn(events) == [WITHDRAWN]


# ----------------------------------------------------------------------- 2. a look that confirms it in between
def test_a_confirming_look_restores_the_line_and_the_count_starts_over():
    """Shown at 300 s, a « keep » at 600 s (the line stops inviting), named again at 750 s: the same line as when
    shown, its Apply button back (the event names the winner), the count at 0 -- so the « keep » after it (the
    look at once: a verdict seen once after a keep) does not withdraw it, the one after that does.  Fails before:
    the confirming look came at 1200 s (the back-off doubled after the « keep »)."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    assert _runs(bg, proc, tap, 1, 301) == [150, 300]
    loader.infer_boxes = []
    assert _runs(bg, proc, tap, 301, 601) == [600] and bg.suggestion["unconfirmed"] == 1
    loader.infer_boxes = [ON_HER]
    n = len(events)
    assert _runs(bg, proc, tap, 601, 751) == [750], "the look after the « keep » did not come 150 frames later"
    assert bg.suggestion["unconfirmed"] == 0 and bg.suggestion["text"].endswith("seen 3 times: Apply now?")
    (text, winner), = _lines(events[n:])
    assert winner == "l @ 1280" and text == bg.suggestion["text"]
    loader.infer_boxes = []
    assert _runs(bg, proc, tap, 751, 901) == [900] and bg.suggestion is not None, "the count did not start over"
    assert _runs(bg, proc, tap, 901, 1051) == [1050] and bg.suggestion is None
    assert len(_toasts(events)) == 1


# --------------------------------------------------------------------------- 1. the app's line, its Apply button
def test_the_apps_line_drops_the_apply_button_and_a_confirming_look_brings_it_back():
    """The events of the test above through the app's own handler (``WallDanceApp._swipe_notify``) to the panel
    (``WallDanceGUI.show_swipe_result``, headless): the line that does not invite shows no Apply button; the
    confirming look's line shows it again.  Fails before: no such line was sent, « Apply now » stayed."""
    dpg = pytest.importorskip("dearpygui.dearpygui")
    import app as appmod
    import gui_builder
    from gui import WallDanceGUI
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    _runs(bg, proc, tap, 1, 301)
    loader.infer_boxes = []
    _runs(bg, proc, tap, 301, 601)
    loader.infer_boxes = [ON_HER]
    _runs(bg, proc, tap, 601, 751)
    published = []
    fake = SimpleNamespace(bus=SimpleNamespace(publish=published.append), swipe_bg=bg, _swipe_shown="",
                           swipe_switch=bg.switch)
    fake._swipe_apply_hint = lambda w: appmod.WallDanceApp._swipe_apply_hint(fake, w)
    dpg.create_context()
    try:
        noop = lambda *a, **k: None  # noqa: E731
        mock = SimpleNamespace(config={}, _on_check_readiness=noop, _on_calibrate=noop, _on_capture_plate=noop,
                               _on_dryrun=noop, _on_swipe=noop, _on_swipe_apply=noop, _on_swipe_auto=noop,
                               _on_swipe_dismiss=noop, _btn_standby_theme=dpg.add_theme())
        with dpg.window(label="unconfirmed"):
            gui_builder.build_phase_wall(mock)
        g = SimpleNamespace()
        seen = []
        for kind, payload in [e for e in events if e[0] == "suggest"]:
            n = len(published)
            appmod.WallDanceApp._swipe_notify(fake, kind, payload)
            res = next(e.result for e in published[n:] if type(e).__name__ == "SettingsSwipeResult")
            WallDanceGUI.show_swipe_result(g, res, "")
            seen.append((dpg.get_value("swipe_status_text").splitlines()[0],
                         dpg.get_item_configuration("swipe_apply_btn")["show"]))
    finally:
        dpg.destroy_context()
    assert [s for _, s in seen] == [True, False, True], seen            # shown, not confirmed, confirmed again
    assert NOT_CONFIRMED in seen[1][0] and "Apply now" not in seen[1][0] and seen[2][0].endswith("Apply now?")


# ------------------------------------------------------------------- 3. an Apply that still reaches it
def test_an_apply_of_a_suggestion_the_last_look_did_not_confirm_switches_nothing():
    """The Apply button is off the line; a press already in flight (or a remote Apply) asks ``withdraw_stale``:
    it goes, nothing switches.  Fails before: it was applied."""
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    _runs(bg, proc, tap, 1, 301)
    assert bg.withdraw_stale(live["v"], setting=True) is None            # (regression guard) confirmed: it stands
    loader.infer_boxes = []
    _runs(bg, proc, tap, 301, 601)
    assert bg.withdraw_stale(live["v"], setting=True) == "the last look did not confirm it"
    assert bg.suggestion is None and _withdrawn(events) == ["suggestion withdrawn: the last look did not confirm it"]


def test_the_tooltip_says_the_rules_15_percent():
    """The combo's tooltip says « no more than 15 % slower », the rule's own figure (finding 5).  Fails before:
    « is no slower », while a model up to 15 % slower is suggested."""
    from core.swipe_auto import AutoParams
    from gui_builder import SWIPE_AUTO_TIP_LINES
    assert AutoParams().slower == pytest.approx(1.15)
    assert "no more than 15 %\n           slower" in SWIPE_AUTO_TIP_LINES["suggest"]


# ------------------------------------------------------------- 4. the line that no longer invites: information
def test_the_line_that_no_longer_invites_is_in_the_normal_colour_without_apply_and_dismiss_says_not_now():
    """The line of a look that did not confirm the suggestion (« ... - not confirmed at the last look (HH:MM) »,
    the event's ``winner`` None) through the app's handler (``WallDanceApp._swipe_notify``) to the panel
    (``WallDanceGUI.show_swipe_result``, headless): the verdict it publishes says ``switch`` only with a winner, so
    the line is in the normal colour, not the suggestion's amber; no Apply; Dismiss on screen, and its press is
    « not now » for that suggestion (``_cb_dismiss_swipe``: put aside, its streak over).  Fails before: ``switch``
    True with no winner -- the line in amber -- and Dismiss went off the screen with the Apply button."""
    dpg = pytest.importorskip("dearpygui.dearpygui")
    import app as appmod
    import gui_builder
    from gui import WallDanceGUI
    from gui_constants import TEXT_NORMAL, WARN_AMBER
    bg, proc, loader, live = _evening_bg()
    events = _events(bg)
    tap = _Tap(range(3000))
    _runs(bg, proc, tap, 1, 301)                         # shown
    loader.infer_boxes = []
    _runs(bg, proc, tap, 301, 601)                       # a look that does not confirm it: the line stops inviting
    suggests = [e for e in events if e[0] == "suggest"]
    assert len(suggests) == 2 and suggests[1][1]["winner"] is None and NOT_CONFIRMED in suggests[1][1]["text"]
    published = []
    fake = SimpleNamespace(bus=SimpleNamespace(publish=published.append), swipe_bg=bg, _swipe_shown="",
                           swipe_switch=bg.switch, swipe=SimpleNamespace(dismiss=lambda: None))
    fake._swipe_apply_hint = lambda w: appmod.WallDanceApp._swipe_apply_hint(fake, w)
    dpg.create_context()
    try:
        noop = lambda *a, **k: None  # noqa: E731
        mock = SimpleNamespace(config={}, _on_check_readiness=noop, _on_calibrate=noop, _on_capture_plate=noop,
                               _on_dryrun=noop, _on_swipe=noop, _on_swipe_apply=noop, _on_swipe_auto=noop,
                               _on_swipe_dismiss=noop, _btn_standby_theme=dpg.add_theme())
        with dpg.window(label="no longer invites"):
            gui_builder.build_phase_wall(mock)
        g = SimpleNamespace()
        seen = []
        for kind, payload in suggests:
            n = len(published)
            appmod.WallDanceApp._swipe_notify(fake, kind, payload)
            res = next(e.result for e in published[n:] if type(e).__name__ == "SettingsSwipeResult")
            WallDanceGUI.show_swipe_result(g, res, "")
            colour = tuple(round(255 * c) for c in dpg.get_item_configuration("swipe_status_text")["color"][:3])
            seen.append((res["switch"], colour, dpg.get_item_configuration("swipe_apply_btn")["show"],
                         dpg.get_item_configuration("swipe_dismiss_btn")["show"]))
    finally:
        dpg.destroy_context()
    assert seen[0] == (True, WARN_AMBER, True, True), seen[0]                   # (regression guard) it invites
    assert seen[1] == (False, TEXT_NORMAL, False, True), \
        f"the line that no longer invites: (switch, colour, Apply, Dismiss) = {seen[1]}"
    assert fake._swipe_shown == "background"
    appmod.WallDanceApp._cb_dismiss_swipe(fake)          # the operator's Dismiss on that line
    assert bg.suggestion is None and bg.auto.snoozed(proc.clock) == L1280 and bg.auto.policy._streak == 0
