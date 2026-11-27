"""P6 (Plan Cordee): the autonomous swipe stands down while the empty-wall plate is not usable.

The referee judges every setting against the plate, so a verdict on a plate that does not hold (``plate_ok``
False: no foreground, invalidated by hand -- X5 --, refused for its camera -- X6 --, found stale -- F2's latch, the
rehearsal build's) is worth nothing: no run, no engine, no inference, no suggestion, no switch; a suggestion shown
goes; a run in progress stops and its verdict is dropped; the plate watch is never told « accept » for it.  Any
change of the plate's state starts the pool over.  CPU, with the fakes of ``test_swipe_auto_live``."""
import dataclasses
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest

from test_seamless_switch import L1280, X1280
from test_swipe_auto_live import _bg, _look_frames, _looking_bg

WAIT = "the automatic swipe waits: the empty-wall plate is not valid - Calibrate on the empty wall"
WITHDRAWN = "suggestion withdrawn: the empty-wall plate is not valid - Calibrate on the empty wall"


def _plate(live, ok):
    live["v"] = dataclasses.replace(live["v"], plate_ok=ok)


class _Watch(types.ModuleType):
    def __init__(self):
        super().__init__("core.reference_watch")
        self.calls = []

    def note_referee_refusal(self, reason=""):
        self.calls.append(("refusal", reason))
        return len(self.calls)

    def note_referee_accept(self):
        self.calls.append(("accept", None))


@pytest.fixture
def watch(monkeypatch):
    w = _Watch()
    monkeypatch.setitem(sys.modules, "core.reference_watch", w)
    return w


def test_the_sentence_is_plain_ascii_in_the_family_of_the_operators():
    from runtime.swipe_background import PLATE_NOT_VALID, PLATE_WAIT
    assert PLATE_WAIT == WAIT and PLATE_WAIT.isascii() and PLATE_NOT_VALID.isascii()
    assert WITHDRAWN == f"suggestion withdrawn: {PLATE_NOT_VALID}"


def test_an_unusable_plate_means_no_run_no_engine_no_inference_no_suggestion_no_switch():
    """(was p6g's scratch: the F2 latch, frames still offered -- two runs on disjoint pools and a switch asked.)
    Mode auto, plate_ok False, two whole pools in turn: the looker waits and says why, once."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    _plate(live, False)
    proc.frames = _look_frames(150)
    assert bg.tick() == "waiting"
    proc.frames = _look_frames(150, t0=150.0)
    proc.clock = 400.0
    assert bg.tick() == "waiting"
    assert loader.loads == [] and loader.infers == 0 and bg.inferences == 0 and bg.resident is None
    assert bg.suggestion is None and bg.switch._want is None and bg.last_advice is None
    assert [k for k, _ in events] == []                                  # no toast, no alert, no line
    st = bg.status()
    assert st["state"] == "waiting" and st["last_look"]["outcome"] == WAIT and st["last_look"]["look"] == "none"
    assert [h["outcome"] for h in bg.history if h["kind"] == "look"] == [WAIT]     # said once, not every look


def test_a_suggestion_shown_is_withdrawn_when_the_plate_goes():
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames(150)
    assert bg.tick() == "run" and bg.suggestion is not None
    _plate(live, False)
    assert bg.tick() == "waiting"
    assert bg.suggestion is None and events[-1] == ("cleared", {"text": WITHDRAWN})


def test_the_withdrawn_suggestion_reaches_the_swipe_line_only():
    """Through the app's own handler: the background's « cleared » is the swipe line's text (when the line shows
    the background's suggestion) -- no toast, no alert, no readiness row."""
    pytest.importorskip("dearpygui")
    import app as appmod
    out = []
    fake = SimpleNamespace(bus=SimpleNamespace(publish=out.append), _swipe_shown="background")
    appmod.WallDanceApp._swipe_notify(fake, "cleared", {"text": WITHDRAWN})
    assert [type(e).__name__ for e in out] == ["SettingsSwipeProgress"]
    assert out[0].state == "auto_cleared" and out[0].text == WITHDRAWN


def test_a_plate_refused_after_a_look_never_runs_the_old_pool_again():
    """(was p6g's scratch: X6 after a run -- plate_ok flipped, the policy reset, the frames the last run used were
    fresh again: a second run on them, a suggestion, on a refused plate.)  While refused: nothing.  Back: the pool
    starts over -- the frames kept before are never scored, a whole new pool is needed."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    proc.frames = _look_frames(150)
    assert bg.tick() == "run" and loader.infers == 150
    assert bg.auto.policy._streak == 1                                   # one definite verdict so far
    proc.clock = 260.0
    _plate(live, False)                                                  # X6: refused for its camera
    assert bg.tick() == "waiting" and loader.infers == 150
    proc.clock = 300.0
    _plate(live, True)                                                   # the exposure is back
    assert bg.tick() == "idle" and loader.infers == 150                  # the old pool: never again
    assert bg.auto.policy._streak == 0 and bg.last_counts == (0, 0)      # the verdicts in a row start over
    proc.frames = _look_frames(150)[100:] + _look_frames(100, t0=301.0)  # 100 new frames: not a pool yet
    assert bg.tick() == "idle" and loader.infers == 150
    proc.frames = _look_frames(150, t0=301.0)                            # a whole pool kept after it came back
    assert bg.tick() == "run" and loader.infers == 300
    assert bg.last_advice["advice"] == "suggest" and bg.last_advice["streak"] == 1      # counted from one again


def test_a_run_in_progress_stops_when_the_plate_goes_and_its_verdict_is_dropped(watch):
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    from runtime.swipe_controller import RefereeReport
    bg.referee = RefereeReport()
    real = loader.infer

    def infer(images, cand, runner, **kw):                               # the plate goes after 40 frames
        n = [0]
        pace = kw.get("pace")

        def paced():
            n[0] += 1
            if n[0] == 40:
                _plate(live, False)
            if pace is not None:
                pace()
        return real(images, cand, runner, **{**kw, "pace": paced})
    loader.infer = infer
    proc.frames = _look_frames(150)
    assert bg.tick() == "run"
    assert loader.infers == 40 and bg.last_look["outcome"] == "cancelled"        # stopped at its next frame
    assert bg.last_advice is None and bg.suggestion is None and watch.calls == []
    assert bg.tick() == "waiting"


def test_a_plate_lost_after_the_last_frame_drops_the_verdict(watch):
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    from runtime.swipe_controller import RefereeReport
    bg.referee = RefereeReport()
    real = loader.infer

    def infer(images, cand, runner, **kw):
        run = real(images, cand, runner, **kw)
        _plate(live, False)                                              # between the inferences and the verdict
        return run
    loader.infer = infer
    proc.frames = _look_frames(150)
    assert bg.tick() == "run" and loader.infers == 150
    assert bg.last_look["outcome"] == "dropped: the empty-wall plate is not valid - Calibrate on the empty wall"
    assert bg.last_advice is None and bg.suggestion is None and watch.calls == []
    assert [k for k, _ in events] == []


def test_a_keep_on_an_unusable_plate_never_tells_the_watch_accept(watch):
    """(was p6g's scratch: the background's « keep » on an unusable plate told note_referee_accept, which starts
    the watch's refusal count over.)  The background does not run; and RefereeReport itself tells nothing for a
    verdict judged on an unusable plate -- the one place the operator's swipe and the background share."""
    from core.swipe import SwipeParams
    from core.swipe_runner import CandidateRun, live_run_from_boxes, score_runs
    from runtime.swipe_controller import RefereeReport
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    bg.referee = rep = RefereeReport()
    _plate(live, False)
    proc.frames = _look_frames(150)
    loader.infer_boxes = []                                              # the other model sees nobody: « keep »
    assert bg.tick() == "waiting" and watch.calls == []
    fr = _look_frames(150, lost=0)
    keep = score_runs([f.obs for f in fr], [live_run_from_boxes(X1280, [f.boxes for f in fr]),
                                            CandidateRun(L1280, boxes=[[] for _ in fr])], X1280, SwipeParams())
    assert keep.winner == X1280 and not keep.switch
    assert rep.note(keep, fr, plate_ok=False) is None and watch.calls == []
    assert rep.note(keep, fr) == "accept" and watch.calls == [("accept", None)]     # on a usable plate: told


def test_a_refusal_on_an_unusable_plate_is_not_told_either(watch):
    from runtime.swipe_controller import RefereeReport
    refused = SimpleNamespace(winner=None, reason="no verdict: every setting puts boxes where the empty-wall "
                                                  "picture shows nobody (at best 0.40 a frame)")
    rep = RefereeReport()
    assert rep.note(refused, _look_frames(30), plate_ok=False) is None and watch.calls == []
    assert rep.note(refused, _look_frames(30)) == "refusal"              # the same pool, on a usable plate: told


def test_the_operators_swipe_on_an_unusable_plate_tells_no_accept(watch, monkeypatch):
    """The same hole in the operator's swipe: it starts on ``plate_status == "ready"`` -- a different predicate
    from ``plate_usable`` (here they differ) -- and its « keep » told accept.  Closed in RefereeReport."""
    from runtime import swipe_controller as SC
    from runtime.swipe_controller import LiveSetting, SwipeController
    from core.swipe_runner import SwipeOutcome
    from test_swipe_plate_watch import _Proc, _keep, _pool
    from test_swipe_plate_watch import X1280 as PX1280

    def press(plate_ok):
        live = LiveSetting("jeudi0809_mur2", PX1280, 1, True, roi=(True, 135, 3, 965, 1771),
                           plate=(1, "2026-10-08T21:36:18"), plate_ok=plate_ok, frame=(1260, 1824),
                           enhance=(False, False, True, 2.5, 1.2), exclusion=((10, 16), ()))
        proc = _Proc(_pool(0))
        monkeypatch.setattr(SC, "run_swipe", lambda *a, **k: SwipeOutcome(PX1280, _keep(), frames=30))
        c = SwipeController(processor=lambda: proc, live=lambda: live, engine_path=lambda c: c.engine_file,
                            plate_ready=lambda: True, runner_factory=lambda inp, p: None,
                            engine_exists=lambda path: True, poll_s=0.01, pace=False)
        assert c.start() is None
        c.join(5)
        assert c.state == "done"
    press(False)
    assert watch.calls == []
    press(True)
    assert watch.calls == [("accept", None)]


def test_the_real_watch_keeps_its_refusal_count_through_a_keep_on_an_unusable_plate(monkeypatch):
    """With the rehearsal build's ``core.reference_watch``: two refusals, then a « keep » judged on a plate that
    was not usable -- not an accept, the count stays; the third refusal raises the plate alert."""
    left = sys.modules.get("core.reference_watch", False)
    if left is None or isinstance(left, types.ModuleType) and not hasattr(left, "ReferenceWatch"):
        monkeypatch.delitem(sys.modules, "core.reference_watch")
    RW = pytest.importorskip("core.reference_watch",
                             reason="the reference watch is the rehearsal build's (H4): this test runs after "
                                    "the merge")
    from runtime.swipe_controller import RefereeReport
    from test_swipe_plate_watch import _keep, _pool, _refusal
    w = RW.ReferenceWatch(run_async=False)
    monkeypatch.setattr(RW, "WATCH", w)
    rep = RefereeReport()
    assert [rep.note(_refusal(), _pool(t0)) for t0 in (0, 100)] == ["refusal", "refusal"]
    assert rep.note(_keep(), _pool(200), plate_ok=False) is None and w.refusals == 2
    assert rep.note(_refusal(), _pool(300)) == "refusal" and w.refusals == 3 and w.current().status == RW.REFUSED


def test_mode_off_and_an_empty_wall_cost_nothing_on_an_unusable_plate_too():
    from test_seamless_switch import _Loader, _live
    live = {"v": dataclasses.replace(_live(), plate_ok=False)}
    loader = _Loader()
    bg, proc, events = _bg(live, loader=loader, mode="off")
    assert bg.tick() == "off" and len(bg.history) == 0 and bg.last_look is None
    bg.set_mode("suggest")
    for _ in range(3):
        assert bg.tick() == "waiting"
    _plate(live, True)
    for _ in range(3):
        assert bg.tick() == "idle"                                       # an empty wall: nothing kept, nothing run
    assert loader.loads == [] and bg.inferences == 0 and bg.resident is None and events == []


# ------------------------------------------------------------------ the tap: a plate refusal starts the pool over
def test_a_plate_refused_for_its_camera_starts_the_tap_over_both_ways():
    """X6 (``plate_refusal``) is in the tap's key: the frames kept before the refusal go when it comes, and do not
    come back when it goes (they would be scored against a plate the camera left meanwhile)."""
    from core.foreground import CleanPlate
    from test_swipe_live import _fg, _proc, _tap
    proc, clock = _proc()
    proc.set_clean_plate(CleanPlate.from_frames([np.full((64, 64), 10, np.uint8)] * 2))
    frame = np.dstack([np.full((40, 30), 9, np.uint8)] * 3)
    for i in range(3):
        clock["t"] = float(i)
        _tap(proc, frame, _fg((15, 20, 900)))
    assert len(proc.swipe_snapshot()[0]) == 3 and proc.plate_usable
    key = proc._swipe_setting_key()
    proc.plate_refusal = "the plate was taken at 12.0 ms exposure, the camera runs 25.0 ms"
    assert not proc.plate_usable and proc._swipe_setting_key() != key
    assert proc.swipe_snapshot()[0] == []
    clock["t"] = 3.0
    _tap(proc, frame, _fg((15, 20, 900), valid=False))                   # a refused frame: nothing kept
    assert proc.swipe_snapshot()[0] == []
    proc.plate_refusal = None                                            # the camera is back at the plate's
    assert proc.plate_usable and proc._swipe_setting_key() == key
    assert proc.swipe_snapshot()[0] == []                                # the old frames do not come back
    clock["t"] = 4.0
    _tap(proc, frame, _fg((15, 20, 900)))
    assert [f.t for f in proc.swipe_snapshot()[0]] == [4.0]


# ------------------------------------------------------------------ the back-off's wake, through the looker
@pytest.mark.parametrize("startup", ["plate-waits-first", "plate-usable"])
def test_after_a_keep_a_rising_lost_share_wakes_the_looker_before_the_back_off_ends(startup):
    """The lost-share wake through ``BackgroundSwipe.tick`` (not only the policy object), with or without a start-up
    scene change: the looker's first tick sees the plate not usable (none loaded yet), the next one usable --
    ``reset`` and the pool cut at ``_fresh_after``.  A « keep » on a pool whose lost share is 0.20 starts the
    back-off (300 s); within it a fresh pool at 0.293 stays idle, one at 0.30 (0.20 + ``wake_lost``) runs at once:
    the baseline is the keep's, set after the start-up reset, the share read on the fresh frames only."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    loader.infer_boxes = []                                              # the other model sees nobody: « keep »
    proc.clock = 0.0
    early = _look_frames(5, lost=5, t0=0.0) if startup == "plate-waits-first" else []     # kept on a plate
    if startup == "plate-waits-first":                                   # that did not hold: never counted
        _plate(live, False)
        proc.frames = early
        assert bg.tick() == "waiting"
        _plate(live, True)
        proc.clock = 5.0
        assert bg.tick() == "idle" and bg._fresh_after == 5.0           # a scene change: the pool cut there
    else:
        proc.clock = 5.0
        assert bg.tick() == "idle" and bg._fresh_after is None          # the first scene: nothing to start over
    proc.frames = early + _look_frames(150, lost=30, t0=6.0)            # 6 .. 155 s, 30 left bare: 0.20
    proc.clock = 156.0
    assert bg.tick() == "run" and loader.infers == 150
    assert bg.last_advice["advice"] == "none" and bg.last_advice["reason"].startswith("keep ")
    pol = bg.auto.policy
    assert pol._keeps == 1 and pol._keep_at == 156.0 and pol._keep_lost == pytest.approx(0.20)
    assert pol.keep_wait() == 300.0
    # 150 fresh frames inside the back-off (156 + 300 = 456), the dancing's own swing (0.293): settled
    proc.frames = _look_frames(150, lost=44, t0=300.0)                   # 300 .. 449 s
    proc.clock = 450.0
    assert bg.tick() == "idle" and loader.infers == 150 and pol.woken == 0
    assert bg.status()["counts"] == {"lost": 44, "covered": 106}
    # the same moment, one more frame with a dancer bare (0.30): the live model loses more than at the keep
    proc.frames = _look_frames(150, lost=45, t0=300.0)
    assert bg.tick() == "run" and loader.infers == 300 and pol.woken == 1
    assert bg.last_look["look"] == "run" and bg.last_look["t"] == 450.0
    assert bg.last_advice["reason"].startswith("keep ")
    assert pol._keeps == 1 and pol._keep_at == 450.0                     # after a woken run: from the start again
    assert [h["outcome"] for h in bg.history if h["kind"] == "look"].count(WAIT) == (startup == "plate-waits-first")


def test_the_wait_is_no_longer_said_from_the_first_tick_with_a_usable_plate():
    """/status is read remotely during the show: once the operator calibrated on the empty wall, the look no longer
    says the automatic swipe waits for the plate -- from the next tick, not from the next look that runs (minutes
    later: a whole new pool first).  The row goes (no look since); the history keeps it."""
    bg, proc, events, loader, live = _looking_bg(mode="auto")
    _plate(live, False)
    proc.frames = _look_frames(150)
    assert bg.tick() == "waiting" and bg.status()["last_look"]["outcome"] == WAIT
    proc.clock = 300.0
    _plate(live, True)                                                   # Calibrate on the empty wall
    assert bg.tick() == "idle" and loader.infers == 0                    # the old pool: no look yet
    st = bg.status()
    assert st["state"] == "watching" and st["last_look"] is None, "it still says it waits for the plate"
    assert [h["outcome"] for h in bg.history if h["kind"] == "look"] == [WAIT]
