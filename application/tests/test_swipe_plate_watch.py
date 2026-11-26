"""P5: the swipe tells the plate watch (``core.reference_watch``, the rehearsal build's) what the referee made of
the empty-wall plate -- refused it, or used it -- through one place, ``runtime.swipe_controller.RefereeReport``.
The watch is a fake injected in ``sys.modules``: this build has no such module, and the swipe must run alone."""
import sys
import threading
import types
from types import SimpleNamespace

import numpy as np
import pytest

from core.swipe import Candidate, FrameScore, Observation, SwipeFrame, SwipeParams, Tally, decide
from core.swipe_runner import SwipeOutcome
from runtime import swipe_controller as SC
from runtime.swipe_controller import RefereeReport, referee_refused, referee_used

X1280 = Candidate("yolo11x-pose", 1280)
L1920 = Candidate("yolo11l-pose", 1280, (1920, 1056))


def _tallies(spec):
    """{candidate: [(groups, covered, loose, lost), ...]} -> tallies over the same frames."""
    out = {}
    for c, rows in spec.items():
        t = Tally()
        for g, cov, lo, lost in rows:
            t.add(FrameScore(groups=g, covered=cov, boxes=cov + lo, loose=lo), lost=lost)
        out[c] = t
    return out


LIVE = [(2, 1, 1, True)] * 20 + [(2, 1, 0, True)] * 20


def _refusal():
    """decide's own refusal: every setting puts boxes where the empty-wall picture shows nobody."""
    return decide(_tallies({X1280: LIVE, L1920: [(2, 2, 1, True)] * 40}), X1280)


def _switch():
    return decide(_tallies({X1280: LIVE, L1920: [(2, 2, 0, True)] * 40}), X1280)


def _keep():
    return decide(_tallies({X1280: [(2, 2, 0, False)] * 40, L1920: [(2, 2, 0, False)] * 40}), X1280)


def _too_few_dancers():
    return decide(_tallies({X1280: [(1, 1, 0, False)] * 5, L1920: [(1, 1, 0, False)] * 5}), X1280)


class _Watch(types.ModuleType):
    def __init__(self):
        super().__init__("core.reference_watch")
        self.calls = []

    def note_referee_refusal(self, reason: str = "") -> int:
        self.calls.append(("refusal", reason))
        return sum(1 for k, _r in self.calls if k == "refusal")

    def note_referee_accept(self) -> None:
        self.calls.append(("accept", None))


@pytest.fixture
def watch(monkeypatch):
    w = _Watch()
    monkeypatch.setitem(sys.modules, "core.reference_watch", w)
    return w


def _pool(t0, n=30):
    return [SwipeFrame(float(t0 + i), np.zeros((1824, 1260), np.uint8), Observation(groups=[(500.0, 500.0, 1000.0)]),
                       True) for i in range(n)]


def test_the_refusal_is_recognised_on_decides_own_sentence():
    r = _refusal()
    assert r.winner is None and r.reason.startswith(SC.REFEREE_REFUSAL)       # the sentence, as decide says it
    assert referee_refused(r) and not referee_used(r)
    for other in (_switch(), _keep()):
        assert referee_used(other) and not referee_refused(other)
    assert _keep().winner == X1280 and not _keep().switch
    for none in (_too_few_dancers(), decide({X1280: Tally(), L1920: Tally()}, X1280), None):
        assert not referee_refused(none) and not referee_used(none)
    # the referee at a looser cut uses the same plate: a verdict, no refusal
    loose = decide(_tallies({X1280: LIVE, L1920: [(2, 2, 1, True)] * 40}), X1280, SwipeParams(referee_max_loose=1.0))
    assert referee_used(loose) and not referee_refused(loose)


def test_a_refusal_is_told_once_with_its_reason_and_a_verdict_is_an_accept(watch):
    rep = RefereeReport()
    r = _refusal()
    assert rep.note(r, _pool(0)) == "refusal"
    assert watch.calls == [("refusal", r.reason)]
    assert rep.note(_keep(), _pool(100)) == "accept"
    assert rep.note(_switch(), _pool(200)) == "accept"
    assert watch.calls[1:] == [("accept", None), ("accept", None)]


def test_other_ends_tell_nothing(watch):
    rep = RefereeReport()
    assert rep.note(_too_few_dancers(), _pool(0)) is None
    assert rep.note(None, _pool(0)) is None
    assert watch.calls == []


def test_the_same_pool_refused_three_times_is_one_refusal(watch):
    rep = RefereeReport()
    pool = _pool(0)
    told = [rep.note(_refusal(), pool) for _ in range(3)]
    assert told == ["refusal", None, None] and len(watch.calls) == 1
    # a pool that shares one frame with the told one: still not a new refusal
    assert rep.note(_refusal(), _pool(29)) is None
    # frames the told refusal did not use: a new one
    assert rep.note(_refusal(), _pool(30)) == "refusal" and len(watch.calls) == 2


def test_without_the_module_nothing_is_told_and_nothing_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.reference_watch", None)        # import -> ModuleNotFoundError
    rep = RefereeReport()
    assert rep.note(_refusal(), _pool(0)) is None
    assert rep.note(_keep(), _pool(100)) is None


def test_a_failing_watch_never_sinks_the_swipe(monkeypatch, capsys):
    w = _Watch()

    def boom(reason=""):
        raise RuntimeError("watch down")
    w.note_referee_refusal = boom
    monkeypatch.setitem(sys.modules, "core.reference_watch", w)
    assert RefereeReport().note(_refusal(), _pool(0)) is None
    assert "watch down" in capsys.readouterr().out


# ------------------------------------------------------------------ through the operator's swipe
class _Proc:
    def __init__(self, frames):
        from core.pipeline import ProcessingSettings
        self.frames = frames
        self.settings = ProcessingSettings(confidence=0.15, imgsz=1280, use_fp16=True, enhance_enabled=False,
                                           enhance_lite=False, enhance_force=False, person_height_px=150,
                                           roi_enabled=True, roi_x=135, roi_y=3, roi_w=965, roi_h=1771)
        self.enhancer = SimpleNamespace(clahe_clip=2.5, gamma=1.2)
        self.bg_subtractor = None
        self.frame_done = threading.Event()
        self.frame_done_t = 0.0
        self.dancer_height = None
        self.last_lb_scale = 0.0

    def swipe_snapshot(self):
        return list(self.frames), len(self.frames), 1.0, True


def _controller(proc, monkeypatch, result):
    from runtime.swipe_controller import LiveSetting, SwipeController
    live = LiveSetting("jeudi0809_mur2", X1280, 1, True, roi=(True, 135, 3, 965, 1771),
                       plate=(1, "2026-10-08T21:36:18"), plate_ok=True, frame=(1260, 1824),
                       enhance=(False, False, True, 2.5, 1.2), exclusion=((10, 16), ()))
    monkeypatch.setattr(SC, "run_swipe", lambda *a, **k: SwipeOutcome(X1280, result(), frames=len(proc.frames)))
    c = SwipeController(processor=lambda: proc, live=lambda: live, engine_path=lambda c: c.engine_file,
                        plate_ready=lambda: True, runner_factory=lambda inp, p: None,
                        engine_exists=lambda path: True, poll_s=0.01, pace=False)
    c.rerun_guard_s = 0.0
    return c


def _press(c):
    assert c.start() is None
    c.join(5)


def test_the_operators_swipe_tells_the_watch_once_per_pool(watch, monkeypatch):
    proc = _Proc(_pool(0))
    c = _controller(proc, monkeypatch, _refusal)
    for _ in range(3):                              # three presses on the same pool
        _press(c)
        assert c.state == "done"
    assert [k for k, _r in watch.calls] == ["refusal"]
    proc.frames = _pool(40)                         # new frames: a new refusal
    _press(c)
    assert [k for k, _r in watch.calls] == ["refusal", "refusal"]


def test_the_operators_keep_is_an_accept_and_an_error_end_tells_nothing(watch, monkeypatch):
    proc = _Proc(_pool(0))
    c = _controller(proc, monkeypatch, _keep)
    _press(c)
    assert c.state == "done" and watch.calls == [("accept", None)]
    c2 = _controller(proc, monkeypatch, lambda: None)       # no setting could be scored: an error end
    _press(c2)
    assert c2.state == "error" and watch.calls == [("accept", None)]


def test_the_operators_swipe_runs_without_the_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.reference_watch", None)
    c = _controller(_Proc(_pool(0)), monkeypatch, _refusal)
    _press(c)
    assert c.state == "done" and c.outcome.result.winner is None


# ------------------------------------------------------------------ the real watch, where the build has it
def test_the_real_watch_alerts_at_three_refusals_in_a_row_once_per_pool(monkeypatch):
    """With the rehearsal build's ``core.reference_watch`` (H4) and its own module-level hooks, through
    ``RefereeReport.note``: three refusals on three disjoint pools raise the watch's plate alert (its verdict,
    ``current``: status ``refused``); an accept in between starts the count over; the same pool pressed three
    times counts once.  A fresh watch stands in for the module's ``WATCH`` each time, as the watch's own tests
    do (monkeypatch puts the module's own back after the test)."""
    left = sys.modules.get("core.reference_watch", False)
    if left is None or isinstance(left, _Watch):       # another test's stand-in (their monkeypatch takes it back)
        monkeypatch.delitem(sys.modules, "core.reference_watch")
    RW = pytest.importorskip("core.reference_watch",
                             reason="the reference watch is the rehearsal build's (H4): this test runs after "
                                    "the merge")
    assert not isinstance(RW, _Watch) and RW.REFUSALS_ALERT == 3

    def fresh():
        w = RW.ReferenceWatch(run_async=False)
        monkeypatch.setattr(RW, "WATCH", w)
        return w, RefereeReport()

    # 1. three refusals on three disjoint pools: the plate alert
    w, rep = fresh()
    assert [rep.note(_refusal(), _pool(t0)) for t0 in (0, 100)] == ["refusal", "refusal"]
    assert w.current() is None                          # two: counted, no alert (nothing checked: no verdict)
    assert rep.note(_refusal(), _pool(200)) == "refusal"
    v = w.current()
    assert v is not None and v.status == RW.REFUSED and v.alert and w.refusals == 3
    assert _refusal().reason in v.instruction           # the referee's own sentence, as the alert shows it

    # 2. two refusals, an accept, two refusals: no alert (and a third in a row raises it)
    w, rep = fresh()
    told = [rep.note(_refusal(), _pool(0)), rep.note(_refusal(), _pool(100)), rep.note(_keep(), _pool(200)),
            rep.note(_refusal(), _pool(300)), rep.note(_refusal(), _pool(400))]
    assert told == ["refusal", "refusal", "accept", "refusal", "refusal"]
    assert w.current() is None and w.refusals == 2
    assert rep.note(_refusal(), _pool(500)) == "refusal" and w.current().status == RW.REFUSED

    # 3. the same pool pressed three times: one refusal, no alert
    w, rep = fresh()
    pool = _pool(0)
    assert [rep.note(_refusal(), pool) for _ in range(3)] == ["refusal", None, None]
    assert w.current() is None and w.refusals == 1
