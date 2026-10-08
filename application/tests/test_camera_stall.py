"""Camera stalls (D34): the hold clocks stop while the live camera is silent, and OSC keeps
sending the last points as coasting.  Fake clocks throughout: the stall clock alone, the
pipeline's output stage (no model / GPU), and the main loop's acquire stage against a scripted
camera."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from core.camera_stall import CameraStallClock, stall_states

FPS = 20.0
P = 1.0 / FPS


# ---------------------------------------------------------------------------
# the clock alone
# ---------------------------------------------------------------------------

def _frames(clock, t0, n, period=P):
    t = t0
    for _ in range(n):
        clock.on_frame(t)
        t += period
    return t - period          # the time of the last frame


def _polls(clock, t_from, t_to, step=0.01):
    """The main loop's camera polls while no frame comes; returns the keep-alive slots."""
    due = []
    k = 1
    while t_from + step * k < t_to - 1e-9:
        t = t_from + step * k
        if clock.keepalive_due(t):
            due.append(t)
            clock.keepalive_sent(t)
        k += 1
    return due


def test_regular_frames_leave_the_clock_alone():
    c = CameraStallClock(period_s=P)
    t = _frames(c, 10.0, 100)
    # slow processing, but under 3 frame periods: not a stall
    for gap in (0.08, 0.12, 0.149):
        _polls(c, t, t + gap)
        t += gap
        assert c.on_frame(t) == 0.0
    assert c.offset == 0.0 and c.stalls == 0


def test_a_stall_removes_its_silence_beyond_three_frame_periods():
    c = CameraStallClock(period_s=P)
    t = _frames(c, 10.0, 20)
    due = _polls(c, t, t + 1.7)
    removed = c.on_frame(t + 1.7)
    assert removed == pytest.approx(1.7 - 3 * P)
    assert c.offset == pytest.approx(removed) and c.stalls == 1
    # the keep-alive grid: from the threshold on, one per frame period, until the frame
    assert due[0] == pytest.approx(t + 3 * P, abs=0.011)
    assert len(due) == pytest.approx((1.7 - 3 * P) / P, abs=1.5)
    gaps = np.diff(due)
    assert gaps.min() >= P - 1e-9 and gaps.max() <= P + 0.011
    assert c.last_stall["resends"] == len(due)
    assert not c.keepalive_due(t + 1.75)          # frames are back


def test_a_silence_the_main_loop_never_polled_is_not_a_stall():
    """A model switch / a blocking dialog: the loop did not look at the camera."""
    c = CameraStallClock(period_s=P)
    t = _frames(c, 10.0, 20)
    assert c.on_frame(t + 1.7) == 0.0 and c.offset == 0.0


def test_beyond_the_cap_the_camera_is_lost_and_the_clock_runs():
    c = CameraStallClock(period_s=P, max_s=5.0)
    t = _frames(c, 10.0, 20)
    due = _polls(c, t, t + 6.0)
    assert due and max(due) <= t + 5.0           # the keep-alive stops at the cap
    assert c.on_frame(t + 6.0) == 0.0 and c.offset == 0.0


def test_camera_lost_disabled_and_playback_do_not_bridge():
    c = CameraStallClock(period_s=P)
    t = _frames(c, 10.0, 20)
    _polls(c, t, t + 0.5)
    c.forget()                                    # closed / reconnecting / a take playing
    assert not c.keepalive_due(t + 0.6)
    assert c.on_frame(t + 1.7) == 0.0
    d = CameraStallClock(period_s=P, enabled=False)
    t = _frames(d, 10.0, 20)
    assert _polls(d, t, t + 1.7) == []
    assert d.on_frame(t + 1.7) == 0.0 and d.offset == 0.0


def test_stall_states_are_coasting_with_the_silence_as_age():
    rows = stall_states([(1, "live", 3.2), (2, "coasting", 0.4), (3, "lost", 7.0), (4, "belt", 1.0)], 0.5)
    assert rows == [(1, "coasting", 0.5), (2, "coasting", 0.9), (3, "lost", 7.0), (4, "coasting", 0.5)]


# ---------------------------------------------------------------------------
# the pipeline output stage (identity slots + OSC), fake clock
# ---------------------------------------------------------------------------

pytest.importorskip("torch")  # pipeline pulls in torch/ultralytics
from core.pipeline import FrameProcessor, ProcessingSettings, ScaledTrack  # noqa: E402

H = 200.0


class _Osc:
    enabled = True

    def __init__(self):
        self.frames = []        # [(t, [(id, x, y, vx, vy)])]
        self.states = []
        self.now = None

    def send_frame(self, tracks, w, h):
        self.frames.append((self.now, [(int(t.track_id), float(t.smoothed_centroid[0]),
                                        float(t.smoothed_centroid[1]), float(t.velocity[0]),
                                        float(t.velocity[1])) for t in tracks]))

    def send_states(self, states):
        self.states.append((self.now, list(states)))


def _st(tid, x, y, fss=0, hits=50, tsu=0):
    kp = np.tile([x, y], (17, 1)).astype(np.float64)
    return ScaledTrack(track_id=tid, keypoints=kp, confidence=np.full(17, 0.9),
                       bbox=np.array([x - 40, y - H / 2, 80, H]), history=[],
                       velocity=np.zeros(2), smoothed_centroid=np.array([x, y], float),
                       frames_since_skeleton=fss, centroid_raw=np.array([x, y], float),
                       hits=hits, age=hits, time_since_update=tsu, feed_src="yolo")


class _Rig:
    """A FrameProcessor's output stage driven like the live app: each frame is noted as a live
    camera frame, then emitted; a stall = the main loop polling the silent camera every 10 ms."""

    def __init__(self, stall_hold=True, osc_send_state=True):
        s = ProcessingSettings(confidence=0.3, imgsz=640, use_fp16=False, enhance_enabled=False,
                               enhance_lite=False, enhance_force=False, person_height_px=200,
                               use_gpu_path=False)
        s.camera_stall_hold = stall_hold
        s.osc_send_state = osc_send_state
        s.osc_enabled = True
        self.p = FrameProcessor(model=None, settings=s)
        self.osc = _Osc()
        self.p.osc = self.osc
        self.t = 100.0
        self.p.set_output_clock(lambda: self.t)
        self.out = []           # emitted slot ids per frame

    def frame(self, tracks, dt=P, live=True):
        self.t += dt
        self.osc.now = self.t
        if live:
            self.p.note_camera_frame(self.t, P)
        out = self.p._run_identity_slots(tracks, lambda x: x, 1920, 1080)
        self.p._emit(out, 1920, 1080, 1, bool(self.p.settings.osc_enabled), False, True)
        self.out.append([o.track_id for o in out])
        return out

    def stall(self, seconds):
        t0 = self.t
        k = 1
        while 0.01 * k < seconds - 1e-9:
            self.t = t0 + 0.01 * k
            self.osc.now = self.t
            self.p.osc_keepalive(self.t)
            k += 1
        self.t = t0 + seconds - P      # the next frame() adds one period


def _walk(rig, n, tid=7, x0=500.0, vx=4.0, fss=0):
    for i in range(n):
        rig.frame([_st(tid, x0 + vx * i, 400.0, fss=fss)])
    return x0 + vx * (n - 1)


def test_a_fresh_slot_survives_a_17s_stall_with_d34_and_drops_without():
    """Smart hold gives a fresh slot 1.5 s.  The camera stalls 1.7 s; after it the tracker
    reports nothing for 0.5 s, then the same track again."""
    results = {}
    for hold in (False, True):
        rig = _Rig(stall_hold=hold)
        _walk(rig, 10)                             # 0.5 s live: a fresh slot
        assert rig.out[-1] == [1]
        rig.stall(1.7)
        for _ in range(10):                        # the tracker lost it for 0.5 s
            rig.frame([])
        rig.frame([_st(7, 560.0, 400.0)])          # ... and finds it again
        results[hold] = rig.out[10:]
    assert results[False][0] == []                 # before D34: dropped on the first frame back
    assert all(ids == [1] for ids in results[True])     # D34: id 1 on every frame


def test_an_earned_hold_keeps_its_full_length_after_a_stall():
    """A slot with its full 2 s hold, lost by the tracker right at the stall's end: before D34
    only ~0.3 s of the hold is left, with D34 the hold runs ~2 s from the stall."""
    lens = {}
    for hold in (False, True):
        rig = _Rig(stall_hold=hold)
        _walk(rig, 40, vx=8.0)                     # 2 s live, moving: full hold earned
        rig.stall(1.7)
        n = 0
        while rig.frame([]) and n < 100:
            n += 1
        lens[hold] = n * P
    assert lens[False] < 0.5
    assert 1.6 <= lens[True] <= 2.1


def test_osc_keeps_sending_the_last_points_as_coasting_during_a_stall():
    rig = _Rig(stall_hold=True)
    _walk(rig, 30)
    last = rig.osc.frames[-1][1]
    n0 = len(rig.osc.frames)
    rig.stall(1.7)
    resent = rig.osc.frames[n0:]
    assert len(resent) == pytest.approx((1.7 - 3 * P) / P, abs=1.5)     # ~20 Hz
    assert resent[0][0] - (rig.t - 1.7 + P) == pytest.approx(3 * P, abs=0.011)
    for _t, pts in resent:                          # same ids, frozen, still
        assert [p[0] for p in pts] == [p[0] for p in last]
        assert [p[1:3] for p in pts] == [p[1:3] for p in last]
        assert all(p[3] == 0.0 and p[4] == 0.0 for p in pts)
    st = rig.osc.states[-len(resent):]
    assert all(rows[0][1] == "coasting" for _t, rows in st)
    assert st[-1][1][0][2] == pytest.approx(1.7 - P, abs=0.011)          # age = the silence
    assert st[-1][1][1][1] == "lost"                # the unused slot 2 stays lost
    # frames back: the real stream resumes, the keep-alive stops
    rig.frame([_st(7, 620.0, 400.0)])
    n1 = len(rig.osc.frames)
    rig.p.osc_keepalive(rig.t + 0.02)
    assert len(rig.osc.frames) == n1


def test_no_keepalive_beyond_the_cap_in_standby_or_after_the_camera_is_lost():
    rig = _Rig(stall_hold=True)
    _walk(rig, 30)
    n0 = len(rig.osc.frames)
    rig.stall(6.0)                                   # a dead camera
    times = [t for t, _ in rig.osc.frames[n0:]]
    assert times and max(times) - (rig.t + P - 6.0) <= 5.0 + 1e-9
    # Standby: the camera delivers, the pipeline emits nothing -> nothing to re-send
    rig2 = _Rig(stall_hold=True)
    _walk(rig2, 30)
    rig2.p.note_camera_frame(rig2.t + P, P)         # a frame acquired, not processed
    rig2.t += P
    n0 = len(rig2.osc.frames)
    rig2.stall(1.0)
    assert len(rig2.osc.frames) == n0
    # the camera lost (reconnecting): the keep-alive stops
    rig3 = _Rig(stall_hold=True)
    _walk(rig3, 30)
    rig3.p.camera_not_live()
    n0 = len(rig3.osc.frames)
    rig3.stall(1.0)
    assert len(rig3.osc.frames) == n0


def test_nothing_changes_without_a_stall():
    """Same frames, D34 on vs off, including slow frames under the threshold and an id loss:
    identical output stream, no offset, no re-send."""
    def run(hold):
        rig = _Rig(stall_hold=hold)
        _walk(rig, 30)
        for i in range(6):
            rig.frame([_st(7, 620.0 + 3 * i, 400.0)], dt=0.12)
            rig.p.osc_keepalive(rig.t + 0.11)
        for _ in range(50):
            rig.frame([])
        _walk(rig, 20, tid=9, x0=900.0)
        return rig
    a, b = run(False), run(True)
    assert b.p.stall.offset == 0.0
    assert a.osc.frames == b.osc.frames
    assert a.osc.states == b.osc.states


def test_a_fast_dancer_keeps_its_id_across_a_stall():
    """2.4 h/s for 1.7 s (a run): the tracker's track is ~3.6 h past the slot's prediction when
    frames resume, beyond the 2 h jump check and the 3 h rebind gate.  The dancer moved while
    the camera was silent, so the slot's distances widen by 2 h/s x the silence: the slot keeps
    following its track.  Before D34 the slot let go of it, and the track took a second id."""
    runs = {}
    for hold in (False, True):
        rig = _Rig(stall_hold=hold)
        x = _walk(rig, 40, vx=24.0)               # 2 s at 480 px/s (h = 200 px)
        rig.stall(1.7)
        x += 24.0 * 35
        for i in range(20):
            rig.frame([_st(7, x + 24.0 * i, 400.0)])
        runs[hold] = rig.out[40:]
    assert all(ids == [1] for ids in runs[True])
    assert any(ids != [1] for ids in runs[False])


def test_the_motion_model_keeps_the_wall_clock():
    """The dancer kept moving during the stall: the slot velocity and One-Euro run on the output
    clock, so the first frame back sees the same motion step with or without D34."""
    outs = {}
    for hold in (False, True):
        rig = _Rig(stall_hold=hold)
        _walk(rig, 40, vx=8.0)
        rig.stall(1.7)
        o = rig.frame([_st(7, 500 + 8 * 39 + 8 * 35, 400.0)])
        outs[hold] = (o[0].smoothed_centroid.copy(), o[0].slot_state)
    np.testing.assert_allclose(outs[True][0], outs[False][0])
    assert outs[True][1] == outs[False][1] == "live"


# ---------------------------------------------------------------------------
# the main loop's acquire stage against a scripted camera
# ---------------------------------------------------------------------------

def test_main_loop_acquire_bridges_a_camera_stall(monkeypatch):
    import runtime.main_loop as ml

    now = {"t": 50.0}
    monkeypatch.setattr(ml.time, "perf_counter", lambda: now["t"])
    monkeypatch.setattr(ml.time, "sleep", lambda s: now.__setitem__("t", now["t"] + s))
    rig = _Rig(stall_hold=True)
    rig.p.set_output_clock(lambda: now["t"])
    frame = np.zeros((8, 8, 3), np.uint8)
    script = {"silent_until": None}

    class _Cam:
        is_open = True

        def has_error(self):
            return False

        def read(self):
            if script["silent_until"] is not None and now["t"] < script["silent_until"]:
                return True, None
            return True, frame

    app = SimpleNamespace(
        last_fps_time=0.0, recorder=SimpleNamespace(is_playing=False),
        _use_unified_camera=True, unified_camera=_Cam(),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=True)),
        cameras=SimpleNamespace(_ids_stream_timed_out=lambda: False,
                                _is_ids_camera_active=lambda: False),
        processor=rig.p, models=SimpleNamespace(_model_loaded=True),
        input_fps_cap=False, roi=SimpleNamespace(state=SimpleNamespace(source_size=(8, 8))),
        _last_raw_frame=None)
    loop = ml.MainLoop(app)
    monkeypatch.setattr(loop, "_log_runtime_diag_if_stalled", lambda **k: None)
    monkeypatch.setattr(loop, "_render_while_waiting", lambda: None)
    monkeypatch.setattr(loop, "_update_gpu_stats_if_due", lambda *a, **k: None)
    monkeypatch.setattr(loop, "_camera_period_s", lambda: P)

    def tick():
        tk = loop._tick_acquire()
        if tk is not None:                     # a frame: the pipeline's output stage
            rig.osc.now = now["t"]
            out = rig.p._run_identity_slots([_st(7, 500.0, 400.0)], lambda x: x, 1920, 1080)
            rig.p._emit(out, 1920, 1080, 1, True, False, True)
            now["t"] += P
        return tk

    for _ in range(20):
        assert tick() is not None
    n0 = len(rig.osc.frames)
    script["silent_until"] = now["t"] + 1.7 - P      # 1.7 s since the last frame
    while tick() is None:
        rig.osc.now = now["t"]
    assert rig.p.stall.offset == pytest.approx(1.7 - 3 * P, abs=0.02)
    assert len(rig.osc.frames) - n0 - 1 == pytest.approx(31, abs=2)       # re-sends + the new frame
    # a take playing: the stall clock forgets the camera, nothing is bridged
    app.recorder = SimpleNamespace(is_playing=True, read_frame=lambda: frame)
    loop._tick_acquire()
    assert rig.p.stall.last_frame is None
