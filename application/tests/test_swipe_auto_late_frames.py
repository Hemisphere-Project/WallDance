"""P6 (Plan Cordee): a look stands down when the live frames run late (re-read 5de60e5, lens b1 finding 2).

A look's cost on the live loop was bounded only in how often and how long it runs (one inference every 0.2 s for
about 30 s), whatever the live frames did: from x @ 1920x1056 on the show laptop they went over the 55 ms budget
during the looks.  Now the looker counts the live frames' own time (``FrameProcessor.timing["total"]``: the frame
in hand to its OSC out, never the looker's inference time) once per live network pass: no look starts while one in
twenty of the last 3 s of them takes more than ``SWIPE_AUTO_FRAME_FLOOR_MS`` (the owner's 15 fps floor since 28/11,
66.7 ms; 55 ms before); a look in flight stops at its next inference once the frames beside it do (after its first
seconds), its frames kept fresh; then no look for the wait after a « keep » (doubled at each stand-down in a row),
said in the look's row until it is over.

CPU, with the fakes of ``test_swipe_auto_live`` and a fake loop thread; the windows are shortened here.
"""
import threading
import time

import pytest

from test_swipe_auto_live import _look_frames, _looking_bg

LATE = ("the automatic swipe waits: the live frames run late (one in twenty takes {} ms or more; the floor is 15 fps "
        "(67 ms))")


@pytest.fixture
def short(monkeypatch):
    """The 3 s window and the warm-up shortened (the loop below runs a frame every 4 ms)."""
    import runtime.swipe_background as SB
    monkeypatch.setattr(SB, "LATE_WINDOW_S", 0.3, raising=False)
    monkeypatch.setattr(SB, "LATE_WARMUP_S", 0.1, raising=False)


class _Loop:
    """The live loop on its own thread: a frame every ``period`` s, its own time (``timing["total"]``) from
    ``ms()``, its network pass signalled (``frame_done``) as the processor does."""

    def __init__(self, proc, ms, period=0.004):
        self.proc, self.ms, self.period = proc, ms, period
        self.stop = threading.Event()
        self.n = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            self.proc.timing = {"total": float(self.ms()), "yolo": 12.0}   # a new dict per frame, as the processor's
            self.proc.frame_done_t = time.perf_counter()
            self.proc.frame_done.set()
            self.n += 1
            time.sleep(self.period)

    def end(self):
        self.stop.set()
        self.thread.join(1.0)


def test_no_look_starts_while_the_live_frames_run_late_and_the_row_goes_with_the_wait(short):
    """A look falls due while the live frames take 70 ms (over the floor's 66.7): no inference, the row says why;
    still none inside the wait after a « keep » (300 s of the loop clock); after it, with the frames on time, the
    look runs and the row is gone."""
    bg, proc, events, loader, live = _looking_bg()
    proc.frames = _look_frames()
    frame_ms = [70.0]
    loop = _Loop(proc, lambda: frame_ms[0])
    try:
        assert bg.tick() == "waiting", "a look started while the live frames ran late"
        assert loader.infers == 0
        assert bg.status()["last_look"]["outcome"] == LATE.format(70)
        frame_ms[0] = 30.0
        proc.clock += 299.0
        assert bg.tick() == "waiting" and loader.infers == 0            # inside the wait: no look, the row stays
        assert bg.status()["last_look"]["outcome"] == LATE.format(70)
        proc.clock += 2.0
        assert bg.tick() == "run" and loader.infers == 150
        assert bg.status()["last_look"]["outcome"] != LATE.format(70)
    finally:
        loop.end()


def test_a_look_stops_when_the_live_frames_run_late_beside_it_and_keeps_its_frames(short):
    """The live frames are on time until the look infers, then take 80 ms: the look stops at its next inference
    after its warm-up (not 150 of them), no verdict, its 150 frames still fresh; no look inside the wait; after
    it, the same again -- and the next wait is twice as long (it cannot start and stop over and over)."""
    bg, proc, events, loader, live = _looking_bg()
    bg.pace_s = 0.01
    proc.frames = _look_frames()
    loop = _Loop(proc, lambda: 80.0 if bg.running and loader.infers else 30.0)
    try:
        assert bg.tick() == "run"
        n = loader.infers
        assert 0 < n < 150, f"the look ran {n} inferences beside late frames"
        row = bg.status()["last_look"]
        assert row["look"] == "run" and row["outcome"] == LATE.format(80)
        assert bg.last_advice is None and bg.suggestion is None
        assert len(bg.auto.fresh(proc.frames)) == 150, "the frames of a look stood down were lost"
        proc.clock += 299.0
        assert bg.tick() == "waiting" and loader.infers == n
        proc.clock += 2.0
        assert bg.tick() == "run" and n < loader.infers < n + 150
        assert bg._late_until == pytest.approx(proc.loop_clock() + 600.0, abs=1.0)
    finally:
        loop.end()


def test_regression_guard_the_lookers_own_inference_time_is_not_counted(short):
    """Regression guard: the frame time counted is the live frame's own.  The looker's inferences take 90 ms each
    (its own time), the live frames 30 ms: the look runs all its inferences and judges."""
    bg, proc, events, loader, live = _looking_bg()
    bg.pace_s = 0.002
    real = loader.infer

    def slow_infer(*a, **k):
        run = real(*a, **k)
        run.ms[:] = [90.0] * len(run.ms)
        return run
    loader.infer = slow_infer
    proc.frames = _look_frames()
    loop = _Loop(proc, lambda: 30.0)
    try:
        assert bg.tick() == "run" and loader.infers == 150
        assert bg.last_advice is not None
    finally:
        loop.end()


def test_late_is_one_in_twenty_of_the_recent_frames_over_the_budget():
    """The measure: the 95th percentile, nearest rank, of the frames seen in the window -- five slow frames in a
    hundred is not late, six is; fewer than twenty frames says nothing."""
    from runtime.swipe_background import LiveFrames
    lf = LiveFrames()
    now = time.perf_counter()
    lf.rows.extend((now, 30.0) for _ in range(95))
    lf.rows.extend((now, 80.0) for _ in range(5))
    assert lf.late(now - 1.0, 55.0) is None
    lf.rows.append((now, 80.0))
    assert lf.late(now - 1.0, 55.0) == 80.0
    few = LiveFrames()
    few.rows.extend((now, 90.0) for _ in range(19))
    assert few.late(now - 1.0, 55.0) is None
