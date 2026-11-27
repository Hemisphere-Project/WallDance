"""P6 (Plan Cordee): where three works on the automatic swipe meet.

* a suggestion keeps the setting it was judged on (Dismiss « not now », what_changed's words): not the plate
  object the reference watch is asked with (``LiveSetting.plate_ref``) -- a suggestion that outlives its plate
  must not keep it in memory up to its max age; the words still say what changed;
* ``tests/switch_replay.py`` builds its looker with the arguments the app gives (STANDBY among them: no look
  before the take plays), and its ``--dismiss-at-s`` is the app's Dismiss: « not now » while a suggestion is on
  screen (the switch's fault stays), else the switch's fault and the bans go.

CPU, the fakes of ``test_swipe_auto_live``."""
import ast
import dataclasses
import gc
import inspect
import weakref
from pathlib import Path

from test_seamless_switch import L1280
from test_swipe_auto_live import _look_frames, _looking_bg


class _Plate:
    """The plate object the foreground holds."""


def test_a_suggestion_on_screen_does_not_keep_the_plate_object_it_was_judged_on():
    """Fails before: the suggestion's stored setting holds the plate until the suggestion goes."""
    bg, proc, events, loader, live = _looking_bg()
    plate = _Plate()
    live["v"] = dataclasses.replace(live["v"], plate_ref=plate)
    proc.frames = _look_frames(150)
    assert bg.tick() == "run"
    proc.frames = _look_frames(150, t0=150.0)            # (27/11) shown at the second look
    assert bg.tick() == "run" and bg.suggestion is not None
    gone = weakref.ref(plate)
    live["v"] = dataclasses.replace(live["v"], plate_ref=None)     # the foreground let it go (no live model...)
    del plate
    gc.collect()
    assert gone() is None and bg.suggestion is not None
    # what_changed's words, from the stored setting, as before
    assert bg.suggestion["live"].key == live["v"].key
    assert bg.withdraw_stale(dataclasses.replace(live["v"], enhance=(True,)), setting=True) \
        == "the enhancement changed since the suggestion"


def _replay_looker_keywords():
    tree = ast.parse(Path(__file__).with_name("switch_replay.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "BackgroundSwipe"]
    assert len(calls) == 1
    return {k.arg for k in calls[0].keywords}


def test_the_replay_builds_its_looker_with_standby_as_the_app_does():
    """Fails before: the replay gives no ``standby`` -- a look could run before the take plays."""
    from runtime.swipe_background import BackgroundSwipe
    said = _replay_looker_keywords()
    assert {"in_run", "wall_empty", "standby"} <= said
    assert said <= set(inspect.signature(BackgroundSwipe).parameters)     # every one the looker takes


class _Switch:
    def __init__(self):
        self.dismissed = 0

    def dismiss(self):
        self.dismissed += 1


def test_the_replay_dismiss_is_the_apps_not_now_then_the_fault():
    """Fails before: the replay drops the switch's fault at a Dismiss that means « not now »."""
    import switch_replay as SR
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames(150)
    assert bg.tick() == "run"
    proc.frames = _look_frames(150, t0=150.0)            # (27/11) shown at the second look
    assert bg.tick() == "run" and bg.suggestion is not None
    sw = _Switch()
    assert SR.press_dismiss(sw, bg) is True                       # « not now »: the fault stays
    assert bg.suggestion is None and sw.dismissed == 0 and bg.auto.snoozed(proc.loop_clock()) == L1280
    assert events[-1] == ("cleared", {"text": "dismissed"})
    assert SR.press_dismiss(sw, bg) is False and sw.dismissed == 1    # nothing on screen: the fault acknowledged
    assert SR.press_dismiss(sw, None) is False and sw.dismissed == 2  # no automatic swipe: the switch's Dismiss
