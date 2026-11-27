"""P5 (Plan Cordee): the settings swipe lets each candidate's engine go right after the END of a live frame --
its OSC out, the loop's tick over (``frame_end``) -- not right after that frame's network pass (``frame_done``):
a TensorRT destructor holds the GIL some 50-70 ms, and there it delayed that frame's tracker and OSC (the P6
re-read, 27/11, measured on the switch's release, which waited for the same live pass).  The inferences keep
their pacing: one right after a live network pass.  On CPU, with a fake model and a fake live loop.
"""
import threading
import time
import weakref
from types import SimpleNamespace

import pytest

from test_swipe_live import X1280, FACADE_ROI, SwipeInput, _BrightBoxModel, _facade_frame


def _runner(freed):
    """A CPU CandidateRunner whose every loaded « engine » records when it is let go (its last reference)."""
    pytest.importorskip("torch")
    pytest.importorskip("kornia")
    from core.swipe_runner import CandidateRunner

    def load(path):
        m = _BrightBoxModel()
        weakref.finalize(m, lambda: freed.append(time.perf_counter()))
        return m
    x, y, w, h = FACADE_ROI
    inp = SwipeInput(roi_enabled=True, roi_x=x, roi_y=y, roi_w=w, roi_h=h, half=False)
    return CandidateRunner(inp, device="cpu", loader=load)


def test_the_release_asks_the_pacer_for_a_frames_end():
    """The pacer's ``release`` (the frame's end) before the engine goes, its plain call before each inference."""
    freed, calls = [], []
    runner = _runner(freed)

    def pace():
        calls.append("inference")
    pace.release = lambda: calls.append("release")
    run = runner.run([_facade_frame()] * 2, X1280, X1280.engine_file, pace=pace)
    assert not run.error and len(run.ms) == 2
    assert calls == ["inference", "inference", "release"] and len(freed) == 1


def test_a_pacer_without_release_is_called_as_before():
    """Regression guard: a plain ``pace`` (a replay driver's, a test's lambda) is called before the release too."""
    freed, calls = [], []
    runner = _runner(freed)
    run = runner.run([_facade_frame()] * 2, X1280, X1280.engine_file, pace=lambda: calls.append("pace"))
    assert not run.error and calls == ["pace"] * 3 and len(freed) == 1


def test_a_candidate_engine_goes_right_after_a_frames_end_not_inside_the_frame():
    """With the app's pacer and a live loop at 20 fps (network pass 15 ms, then 20 ms of tracker and OSC, then the
    frame's end): the engine goes right after a frame's end, never between a network pass and its frame's end."""
    from runtime.swipe_controller import make_pacer
    proc = SimpleNamespace(frame_done=threading.Event(), frame_done_t=0.0, frame_end=threading.Event(),
                           frame_end_t=0.0)
    frames, stop = [], threading.Event()

    def live_loop():
        while not stop.is_set():
            time.sleep(0.015)                            # the network pass
            proc.frame_done_t = t1 = time.perf_counter()
            proc.frame_done.set()
            time.sleep(0.020)                            # the tracker and OSC
            proc.frame_end_t = t2 = time.perf_counter()
            proc.frame_end.set()
            frames.append((t1, t2))
            time.sleep(0.015)
    loop = threading.Thread(target=live_loop, daemon=True)
    loop.start()
    try:
        time.sleep(0.12)
        freed = []
        run = _runner(freed).run([_facade_frame()] * 2, X1280, X1280.engine_file, pace=make_pacer(proc, 0.0))
        time.sleep(0.06)
    finally:
        stop.set()
        loop.join(1.0)
    assert not run.error and len(freed) == 1
    t = freed[0]
    inside = [f for f in frames if f[0] <= t < f[1]]
    assert not inside, f"released {1000 * (t - inside[0][0]):.0f} ms after a network pass, before its frame's end"
    ends = [f[1] for f in frames if f[1] <= t]
    assert ends and t - ends[-1] < 0.01, "not released right after a frame's end"
