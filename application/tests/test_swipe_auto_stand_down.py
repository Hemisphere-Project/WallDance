"""P6 (Plan Cordee): when the automatic swipe must not judge.

* the reference watch (``core.reference_watch``, H4) says the plate in use is wrong -- the camera moved, another
  wall, refused by the referee...: a confirmed alert, ``WATCH.current(plate)`` with the plate the swipe judges
  against (``LiveSetting.plate_ref``, the object ``plate`` identifies).  The plate's own wait: no frame read, no
  engine, no inference, no suggestion, no switch; a suggestion shown goes; a run in progress stops; the status row
  quotes the watch's status and gives no instruction of its own; it leaves at the first look that sees the alert
  gone.  No verdict, a match, a first alert not confirmed, no watch in the build: the swipe goes on;
* STANDBY: no look, no switch asked (the second engine may load there); a run in progress stops at its next frame.

CPU, the fakes of ``test_swipe_auto_live``; the watch is the real ``ReferenceWatch`` with its check scripted."""
import dataclasses
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from test_seamless_switch import L1280
from test_swipe_auto_live import _look_frames, _looking_bg
from test_swipe_auto_live import swipe_auto_unlocked  # noqa: E402,F401 - the fixture

# mode auto set on the BackgroundSwipe itself, past the app's doors: the release list must name it too (the
# last guard before a switch, since 27/11); the build ships locked
pytestmark = pytest.mark.usefixtures("swipe_auto_unlocked")

MOVED_WAIT = "the automatic swipe waits: the references are not right (moved) - see the health line"
OTHER_WAIT = "the automatic swipe waits: the references are not right (other wall) - see the health line"
WITHDRAWN = "suggestion withdrawn: the references are not right (moved) - see the health line"


class _Plate:
    """The plate object the foreground holds (the watch only follows its identity here)."""
    created = "2026-10-08T21:36:18"


@pytest.fixture
def ref(monkeypatch):
    """The real watch as the module's ``WATCH``, its check scripted: ``ref.say(status)`` runs one check of the
    plate (an alert is shown on its second one, a match at once)."""
    import core.reference_watch as RW
    w = RW.ReferenceWatch(run_async=False)
    monkeypatch.setattr(RW, "WATCH", w)
    said = {"status": RW.MATCHES}

    def check(plate, frame, roi=None, exclude=None):
        return RW.ReferenceVerdict(4.0 if said["status"] == RW.MOVED else 0.1, 0.2, said["status"],
                                   f"the camera moved - {RW.FIX_MOVED}")
    monkeypatch.setattr(RW, "check_reference", check)
    t = {"now": 0.0}

    def say(status, plate, times=1):
        said["status"] = status
        for _ in range(times):
            t["now"] += 1.0
            assert w.submit(t["now"], plate, np.zeros((8, 8), np.uint8))
    return SimpleNamespace(RW=RW, watch=w, say=say)


def _with_plate(live, plate):
    if "plate_ref" in {f.name for f in dataclasses.fields(live["v"])}:     # (a build before it: no plate handed)
        live["v"] = dataclasses.replace(live["v"], plate_ref=plate)


def _counting_fresh(proc):
    reads = []
    real = proc.swipe_fresh

    def fresh(after_t=None):
        reads.append(after_t)
        return real(after_t)
    proc.swipe_fresh = fresh
    return reads


# ------------------------------------------------------------------ the reference watch's alert
def test_a_confirmed_alert_means_no_frame_read_no_engine_no_inference_no_suggestion_no_switch(ref):
    """Fails without the stand-down: the look loads the second engine and runs on the pool."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    plate = _Plate()
    _with_plate(live, plate)
    reads = _counting_fresh(proc)
    ref.say(ref.RW.MOVED, plate, times=2)                                 # confirmed: shown
    assert ref.watch.current(plate).alert
    proc.frames = _look_frames(150)
    assert bg.tick() == "waiting"
    proc.frames, proc.clock = _look_frames(150, t0=150.0), 400.0
    assert bg.tick() == "waiting"
    assert reads == [] and loader.loads == [] and loader.infers == 0 and bg.resident is None
    assert bg.suggestion is None and bg.switch._want is None and bg.last_advice is None and events == []
    st = bg.status()
    assert st["state"] == "waiting" and st["last_look"]["outcome"] == MOVED_WAIT
    assert [h["outcome"] for h in bg.history if h["kind"] == "look"] == [MOVED_WAIT]    # said once
    assert ref.RW.FIX_MOVED not in MOVED_WAIT and "CALIBRATE" not in MOVED_WAIT         # the watch's instruction


def test_a_suggestion_shown_is_withdrawn_at_the_alert_and_the_row_leaves_when_it_goes(ref):
    """Fails without the stand-down: the suggestion stays on screen through the alert."""
    bg, proc, events, loader, live = _looking_bg()
    plate = _Plate()
    _with_plate(live, plate)
    proc.frames = _look_frames(150)
    assert bg.tick() == "run"
    proc.frames = _look_frames(150, t0=150.0)            # (27/11) shown at the second look
    assert bg.tick() == "run" and bg.suggestion is not None
    ref.say(ref.RW.MOVED, plate, times=2)
    assert bg.tick() == "waiting"
    assert bg.suggestion is None and events[-1] == ("cleared", {"text": WITHDRAWN})
    ref.say(ref.RW.OTHER_WALL, plate, times=2)                            # the watch says another thing: so do we
    assert bg.tick() == "waiting" and bg.status()["last_look"]["outcome"] == OTHER_WAIT
    ref.say(ref.RW.MATCHES, plate)                                        # a match clears it at once
    proc.clock = 300.0
    assert bg.tick() == "idle" and bg.status()["last_look"] is None      # the row left at the first look
    assert loader.infers == 300 and bg.auto.policy._streak == 0          # the pool and the verdicts start over
    proc.frames = _look_frames(150, t0=301.0)
    assert bg.tick() == "run" and loader.infers == 450


def test_a_run_in_progress_stops_at_its_next_frame_when_the_alert_comes(ref):
    """Fails without the stand-down: the run goes to its end and suggests."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    plate = _Plate()
    _with_plate(live, plate)
    real = loader.infer

    def infer(images, cand, runner, **kw):                               # the alert is shown after 40 frames
        n = [0]
        pace = kw.get("pace")

        def paced():
            n[0] += 1
            if n[0] == 40:
                ref.say(ref.RW.MOVED, plate, times=2)
            if pace is not None:
                pace()
        return real(images, cand, runner, **{**kw, "pace": paced})
    loader.infer = infer
    proc.frames = _look_frames(150)
    assert bg.tick() == "run"
    assert loader.infers == 40 and bg.last_look["outcome"] == "cancelled"
    assert bg.last_advice is None and bg.suggestion is None and bg.switch._want is None


def test_no_verdict_a_match_a_first_alert_or_another_plates_alert_go_on(ref):
    """Regression guard (passes before the fix too): only a confirmed alert on THIS plate stands it down."""
    bg, proc, events, loader, live = _looking_bg()
    plate, other = _Plate(), _Plate()
    _with_plate(live, plate)
    proc.frames = _look_frames(150)
    ref.say(ref.RW.MOVED, plate)                                          # a first alert: not shown yet
    assert ref.watch.current(plate) is None
    ref.say(ref.RW.MOVED, other, times=2)                                 # another plate's alert, confirmed
    assert ref.watch.current(other).alert and ref.watch.current(plate) is None
    assert bg.tick() == "run" and loader.infers == 150 and bg.last_advice["advice"] == "seen"   # (27/11) once


def test_a_watch_never_fed_or_absent_from_the_build_lets_it_look(monkeypatch):
    """Regression guard: the replay drivers run without the watch, or with one never fed."""
    import core.reference_watch as RW
    monkeypatch.setattr(RW, "WATCH", RW.ReferenceWatch(run_async=False))
    bg, proc, events, loader, live = _looking_bg()
    _with_plate(live, _Plate())
    proc.frames = _look_frames(150)
    assert bg.tick() == "run" and loader.infers == 150
    monkeypatch.setitem(sys.modules, "core.reference_watch", None)      # not in this build: the import fails
    bg2, proc2, _, loader2, live2 = _looking_bg()
    _with_plate(live2, _Plate())
    proc2.frames = _look_frames(150)
    assert bg2.tick() == "run" and loader2.infers == 150


def test_the_plate_the_swipe_judges_against_is_the_one_the_watch_keys_on():
    """``_swipe_live_setting`` hands the plate object the foreground holds (``plate_ref``) with its identity
    (``plate``): the watch's ``current`` keys on that object.  Fails before: no ``plate_ref``."""
    pytest.importorskip("dearpygui")
    import app as appmod
    from core.foreground import CleanPlate
    from test_swipe_live import _proc
    proc, _ = _proc()
    proc.set_clean_plate(CleanPlate.from_frames([np.full((64, 64), 10, np.uint8)] * 2))
    s = proc.settings
    s.roi_enabled, s.roi_x, s.roi_y, s.roi_w, s.roi_h = True, 0, 0, 64, 64
    fake = SimpleNamespace(
        models=SimpleNamespace(model=object(), _model_loaded=True, _model_loading=False,
                               current_model_name="yolo11x-pose",
                               model_manager=SimpleNamespace(is_using_tensorrt=lambda: True)),
        settings=s, configs=SimpleNamespace(_current_project="jeudi0809_mur2"),
        roi=SimpleNamespace(state=SimpleNamespace(source_size=(64, 64))), enhancer=proc.enhancer, processor=proc)
    live = appmod.WallDanceApp._swipe_live_setting(fake)
    assert live.plate_ref is proc._fg_detector.plate and live.plate == proc.plate_identity
    assert live.plate[0] == id(live.plate_ref)
    assert dataclasses.replace(live, plate_ref=None) == live and dataclasses.replace(live, plate_ref=None).key == live.key


# ------------------------------------------------------------------ STANDBY
def test_in_standby_no_look_and_no_switch_the_second_engine_still_loads():
    """Fails without the STANDBY check: in STANDBY a due look runs on the frames kept before and, in mode auto,
    counts toward the switch."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    st = {"standby": True}
    bg.standby = lambda: st["standby"]
    proc.frames = _look_frames(150)
    assert bg.tick() == "idle"
    proc.frames, proc.clock = _look_frames(150, t0=150.0), 400.0
    assert bg.tick() == "idle"
    assert loader.infers == 0 and bg.last_advice is None and bg.switch._want is None and bg.suggestion is None
    assert loader.loads == [L1280.engine_file] and bg.resident.candidate == L1280     # deliberate: it loads there
    st["standby"] = False                                                # RUN again: the look runs
    assert bg.tick() == "run" and loader.infers == 150


def test_a_run_in_progress_stops_at_its_next_frame_when_standby_begins():
    """Fails without the STANDBY check: the run goes on to its end."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    st = {"standby": False}
    bg.standby = lambda: st["standby"]
    real = loader.infer

    def infer(images, cand, runner, **kw):
        n = [0]
        pace = kw.get("pace")

        def paced():
            n[0] += 1
            if n[0] == 40:
                st["standby"] = True
            if pace is not None:
                pace()
        return real(images, cand, runner, **{**kw, "pace": paced})
    loader.infer = infer
    proc.frames = _look_frames(150)
    assert bg.tick() == "run"
    assert loader.infers == 40 and bg.last_look["outcome"] == "cancelled"
    assert bg.last_advice is None and bg.switch._want is None


def test_standby_after_the_last_frame_drops_the_verdict():
    """Fails without the STANDBY check: the verdict counts and the policy advises."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    st = {"standby": False}
    bg.standby = lambda: st["standby"]
    real = loader.infer

    def infer(images, cand, runner, **kw):
        run = real(images, cand, runner, **kw)
        st["standby"] = True                                             # after the last frame
        return run
    loader.infer = infer
    proc.frames = _look_frames(150)
    assert bg.tick() == "run" and loader.infers == 150
    assert bg.last_look["outcome"] == "dropped: STANDBY began during the run"
    assert bg.last_advice is None and bg.switch._want is None


def test_the_app_tells_the_looker_when_it_is_in_standby():
    """The real app wires ``standby`` to its state.  Fails before: no such argument."""
    pytest.importorskip("dearpygui")
    import app as appmod
    from runtime.api import SystemState
    app = appmod.WallDanceApp()
    try:
        app.system_state = SystemState.STANDBY
        assert app.swipe_bg.standby() is True and app.swipe_bg.in_run() is False
        app.system_state = SystemState.RUN
        assert app.swipe_bg.standby() is False and app.swipe_bg.in_run() is True
    finally:
        app.swipe_bg.stop(1.0)
        app.swipe_switch.stop(1.0)
