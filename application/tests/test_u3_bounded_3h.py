"""U3 (#t-029 follow-up): what the app keeps while it runs stays flat over a 3-hour show.

The census of 27/11 (tmp_analysis/r2-1126/gates/grow) found no unbounded structure on the frame path or in the
app's periodic work; these tests hold the ones that fill for the first minutes and then must stay flat -- each is
driven for the equivalent of three hours on a fake clock and sized (objects reachable from it, shallow bytes) at
1 h, 2 h and 3 h:

* the tracker's event log (``TrackingLogger``) on an EMPTY wall (a FRAME_SUMMARY a frame, no other event): ``_buffer``
  a ring of ``max_entries``; ``_pending`` flushed on the flush clock (capped at ``max_entries`` while no session
  file exists) -- the one unbounded structure the census found (fixed in ``log_frame_summary``);
* the remote API's event buffer (``RemoteApi._events``, a ring of ``buffer`` events; ``_latest`` one per name);
* the main loop's health line (H2) and the ops monitor (``HealthMonitor``) at 1 Hz, bodies coming and going.
"""
from __future__ import annotations

import gc
import random
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

FPS = 17.0
HOURS = 3
_SKIP = (types.ModuleType, type, types.FunctionType, types.BuiltinFunctionType, types.CodeType, types.MethodType,
         types.FrameType)


def _reach(root, stop=()):
    seen = {id(s) for s in stop}
    stack, n, b = [root], 0, 0
    while stack:
        o = stack.pop()
        if id(o) in seen:
            continue
        seen.add(id(o))
        n += 1
        b += sys.getsizeof(o)
        stack.extend(r for r in gc.get_referents(o) if not isinstance(r, _SKIP) and id(r) not in seen)
    return n, b


def _flat(sizes):
    """sizes at 1 h, 2 h, 3 h: no more than 1 % (and 50 objects) above the 1 h size."""
    (n1, b1) = sizes[0]
    for n, b in sizes[1:]:
        assert n <= n1 * 1.01 + 50, sizes
        assert b <= b1 * 1.01 + 4096, sizes


def _empty_wall_logger(monkeypatch, tmp_path, with_file):
    """The tracker's event log as the app runs it on an EMPTY wall: one FRAME_SUMMARY a frame, no other event."""
    import core.tracking_logger as TL
    clk = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(TL, "time", SimpleNamespace(time=lambda: clk.t, monotonic=lambda: clk.t,
                                                    perf_counter=lambda: clk.t))
    log = TL.TrackingLogger(enabled=True, filepath=None)
    if with_file:
        log.start_session(str(tmp_path / ("s_file" if with_file else "s")))
    return log, clk


def test_tracking_logger_stays_flat_on_an_empty_wall_for_three_hours(monkeypatch, tmp_path):
    """U3: a summary a frame and nothing else (an empty wall) -- with the live session's file open and before
    any session -- keeps the log's memory flat: ``_pending`` at most one flush interval of frames (or the
    ``max_entries`` cap without a file), ``_buffer`` its ring.  Before the fix ``_pending`` held every summary
    of the show (183,600 entries, 5 live objects each, at 17 fps for 3 h)."""
    for with_file in (True, False):
        log, clk = _empty_wall_logger(monkeypatch, tmp_path, with_file)
        per_flush = int(log.flush_interval * FPS) + 2
        cap = per_flush if with_file else log.max_entries + per_flush
        sizes, worst = [], 0
        for f in range(int(HOURS * 3600 * FPS) + 1):     # sized at 1 h, 2 h, 3 h (the same flush phase)
            clk.t = 1000.0 + f / FPS
            log.set_frame(f)
            log.log_frame_summary(0, 0, [], 0, [], emitted=[])
            log.annotate_frame_summary(slots=["lost"] * 4)
            worst = max(worst, len(log._pending))
            if f and f % int(3600 * FPS) == 0:
                assert len(log._buffer) == log.max_entries
                sizes.append(_reach(log))
        assert worst <= cap, (with_file, worst, cap)
        _flat(sizes)
        log.close()


def test_tracking_logger_annotation_still_lands_on_its_summary_line(monkeypatch, tmp_path):
    """The flush clock in log_frame_summary never writes the summary the output stage is about to annotate."""
    import json
    log, clk = _empty_wall_logger(monkeypatch, tmp_path, True)
    for f in range(200):
        clk.t = 1000.0 + f / FPS
        log.set_frame(f)
        log.log_frame_summary(0, 0, [], 0, [], emitted=[])
        log.annotate_frame_summary(slots=[f])
    log.close()
    lines = [json.loads(x) for p in sorted((tmp_path / "s_file").rglob("*.jsonl")) for x in p.read_text().splitlines()]
    summaries = [e for e in lines if e["event"] == "FRAME_SUMMARY"]
    assert len(summaries) == 200 and not [e for e in lines if e["event"] == "FRAME_OUTPUT"]
    assert all(e["data"]["slots"] == [e["frame"]] for e in summaries)


def test_remote_api_event_buffer_stays_flat_for_three_hours():
    from runtime import api
    from services.remote_api import RemoteApi
    bus = api.EventBus()
    remote = RemoteApi(api.RuntimeAPI(), bus, token="t", roots={}, state_fn=lambda: "RUN", buffer=2000)
    sizes = []
    for f in range(int(HOURS * 3600 * FPS)):
        if f % 4 == 0:
            bus.publish(api.BgStatus(True, True, 0.01 * (f % 7), False))
        if f % int(FPS) == 0:
            bus.publish(api.Toast(f"status {f}", 1.0, (255, 255, 255)))
            bus.publish(api.StatsTick({"fps": 17.0, "frame": f}))
        if f and f % int(3600 * FPS) == 0:
            assert len(remote._events) == 2000
            sizes.append(_reach(remote))
    sizes.append(_reach(remote))
    _flat(sizes)
    assert len(remote._latest) <= 5


def test_health_line_and_ops_monitor_stay_flat_for_three_hours(monkeypatch):
    import test_h2_health_line as H2
    ml = H2._import_main_loop()
    clk = H2._Clock()
    monkeypatch.setattr(ml, "time", SimpleNamespace(monotonic=clk, time=clk, perf_counter=clk))
    app = H2._fake_app([])
    app.bus = SimpleNamespace(publish=lambda e: None, ui_ready=True)
    loop = ml.MainLoop(app)
    proc, health = app.processor, app._health
    rng = random.Random(7)
    fps = 4.0                                   # the structures are windowed in time: the rate scales their content
    bodies, sizes = [], []
    for f in range(int(HOURS * 3600 * fps) + 1):
        now = f / fps
        clk.t = now
        if f % 28 == 0:
            bodies = [(rng.uniform(100, 1100), rng.uniform(200, 1700), 4000.0) for _ in range(rng.choice((0, 1, 3)))]
        proc.new_frame(bodies, [(x + 5, y - 20, 150.0) for x, y, _a in bodies if rng.random() < 0.7])
        loop._health_line_frame()
        if f % int(fps) == 0:
            sec = int(now)
            for a in health.tick(now, fps=17.0 - (6.0 if sec % 600 < 20 else 0.0), n_tracked=len(bodies),
                                 in_run=True, model_ready=True, camera_open=True,
                                 camera_reconnecting=(sec % 1800 < 5), playback_active=False, n_over_cap=0,
                                 height_median=150.0, height_gate=(100.0, 220.0),
                                 light_ratio=1.0 + (0.5 if sec % 1200 < 60 else 0.0), light_ref=1.0,
                                 picture_dn=120.0):
                loop._emit_ops_alert(a)
            health.pop_cleared()
            loop._health_line_tick(True)
        if f and f % int(3600 * fps) == 0:
            sizes.append(_reach(loop))
    _flat(sizes)
    assert np.isfinite(sizes[-1][1])
