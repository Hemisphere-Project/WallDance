"""P6 (Plan Cordee): the automatic swipe at start-up and after a project load, before the plate is loaded.

The project's empty-wall plate is named at the project load (``settings.fg_plate_path``) and loaded by the next frame
or the ops tick's light sample (``FrameProcessor._ensure_plate``); until then ``plate_usable`` is False with nothing
known against the plate.  The looker's first tick may come first (5 s after the project's ``swipe_auto`` started it,
any time while no frame flows): it waits without a word -- never « the empty-wall plate is not valid », which
would stay its last look until the first run.  A plate that turns out bad, or no plate at all, is the real wait,
with its sentence, as before.  CPU, with the fakes of ``test_swipe_auto_live``, on the real processor's plate.
"""
import dataclasses
import threading
from types import SimpleNamespace

import pytest

from test_seamless_switch import _live, _proc
from test_swipe_auto_live import _bg

WAIT = "the automatic swipe waits: the empty-wall plate is not valid - Calibrate on the empty wall"
PLATE = "projects/jeudi0809_mur2/plates/latest.npz"


@pytest.fixture
def plates(monkeypatch):
    """CleanPlate.load / ForegroundDetector stand-ins: a plate file per path, or a load that raises."""
    import core.foreground as fg
    made = {"fail": set(), "pending_during_load": []}

    def load(path):
        made["pending_during_load"].append(getattr(made["proc"], "plate_pending", None))
        if path in made["fail"]:
            raise ValueError("not a plate file")
        return SimpleNamespace(created="2026-11-26T20:15:00", ds=4, path=path)
    monkeypatch.setattr(fg.CleanPlate, "load", staticmethod(load))
    monkeypatch.setattr(fg, "ForegroundDetector", lambda plate: SimpleNamespace(plate=plate, stale_latched=False))
    return made


def _looker(plates, mode="auto"):
    """The looker on a real FrameProcessor's plate: the live setting reads it as the app does
    (``_swipe_live_setting``: ``plate_identity``, ``plate_usable``)."""
    p = _proc()
    p._fg_detector, p._fg_loaded_path, p._plate_invalid, p.plate_refusal = None, None, None, None
    p.frames, p.clock = [], 100.0
    p.loop_clock = lambda: p.clock
    p.swipe_fresh = lambda after_t=None: ([f for f in p.frames if after_t is None or float(f.t) > after_t], p.clock)
    p._swipe_key, p._swipe_setting_key = "k", lambda: "k"
    p.frame_done, p.frame_done_t = threading.Event(), 0.0
    plates["proc"] = p
    base = _live()
    bg, _, events = _bg({"v": base}, mode=mode)
    live = lambda: dataclasses.replace(base, plate=p.plate_identity, plate_ok=bool(p.plate_usable))  # noqa: E731
    bg.processor = bg.switch.processor = lambda: p
    bg.live = bg.switch.live = live
    return bg, p, events


def _project_load(p, path):
    """``App._load_project_plate``: the project's plate named, loaded on the next frame (or none at all)."""
    p.settings.fg_plate_path = path or ""
    p._fg_loaded_path = None
    if not path:
        p.set_clean_plate(None)


def _looks(bg):
    return [h["outcome"] for h in bg.history if h["kind"] == "look"]


def test_at_start_up_a_plate_not_loaded_yet_is_not_said_to_be_invalid(plates):
    """Start-up: the project names its plate, no frame has loaded it when the looker first ticks -- it waits, says
    nothing; the plate loads (the ops tick's light sample, or the first frame): it watches, nothing was said."""
    bg, p, events = _looker(plates)
    _project_load(p, PLATE)
    assert bg.tick() == "waiting" and bg.state == "waiting"
    assert bg.last_look is None and _looks(bg) == [], "a plate merely not loaded yet was called not valid"
    assert bg.status()["last_look"] is None and events == []
    p._ensure_plate()                                    # the loop loads it
    assert plates["pending_during_load"] == [True], "the plate counted as tried before its load ended"
    assert p.plate_usable and not p.plate_pending
    assert bg.tick() == "idle" and bg.state == "watching" and bg.last_look is None
    _project_load(p, "")                                 # a project with no plate at all: the real wait, at once
    assert bg.tick() == "waiting" and _looks(bg) == [WAIT]


def test_after_a_project_load_the_new_plate_waits_without_a_word(plates):
    """The plate in memory was invalidated by hand (X5) a moment before the project load, the looker had not
    looked since: until the new project's plate loads, the swipe waits without the sentence; then it watches."""
    bg, p, _ = _looker(plates)
    _project_load(p, PLATE)
    p._ensure_plate()
    assert bg.tick() == "idle"
    p.invalidate_plate("exposure changed by hand - take the empty wall again")
    _project_load(p, "projects/vendredi/plates/latest.npz")
    assert not p.plate_usable and bg.tick() == "waiting"
    assert bg.last_look is None and _looks(bg) == [], "the new project's plate was called not valid before it loaded"
    p._ensure_plate()
    assert bg.tick() == "idle" and bg.last_look is None


def test_a_plate_that_does_not_load_is_the_real_wait_once_it_was_tried(plates):
    """Not known before the first try; the file does not load (the foreground stays off): that is the real wait,
    with its sentence, once -- as for a plate that holds no more."""
    bg, p, _ = _looker(plates)
    plates["fail"].add(PLATE)
    _project_load(p, PLATE)
    assert bg.tick() == "waiting" and _looks(bg) == [], "a plate not tried yet was called not valid"
    p._ensure_plate()                                    # tried: it does not load
    assert not p.plate_pending and not p.plate_usable
    assert bg.tick() == "waiting" and _looks(bg) == [WAIT] and bg.status()["last_look"]["outcome"] == WAIT
    assert bg.tick() == "waiting" and _looks(bg) == [WAIT]                 # said once
