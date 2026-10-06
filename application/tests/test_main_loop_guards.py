"""Headless tests for the main loop's diagnostics and crash hygiene.

The loop drives DPG, the camera and the GPU, so these tests run single
stages / ``run()`` against minimal fakes (``SimpleNamespace``): only the
attributes a stage touches exist, so an unexpected dependency fails loudly.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("torch")  # runtime.main_loop -> core.pipeline -> torch/ultralytics

from runtime.main_loop import MainLoop, _Tick  # noqa: E402


class _Ui:
    def __init__(self, render_s: float = 0.0):
        self.renders = 0
        self.render_s = render_s

    def render_frame_raw(self):
        self.renders += 1
        if self.render_s:
            time.sleep(self.render_s)

    def render_frame(self):
        self.renders += 1


def _loop(**app_attrs):
    app = SimpleNamespace(last_fps_time=0.0, **app_attrs)
    return MainLoop(app), app


# ---------------------------------------------------------------------------
# PERF-11: the GUI tail reaches the [Budget] line
# ---------------------------------------------------------------------------

def test_budget_line_reports_dpg_render_and_gui_stats(capsys):
    loop, app = _loop(ui=_Ui(render_s=0.003), fps=19.7,
                      timing={"process_wall": 31.6, "yolo": 11.6,
                              "preview_draw": 1.0, "preview_upload": 3.0})
    t = _Tick(gui_stats_ms=0.7, camera_read_ms=0.4)
    t.log_timing = True          # what the preview stage now sets
    loop._tick_render(t)
    out = capsys.readouterr().out
    budget = [ln for ln in out.splitlines() if ln.startswith("[Budget]")]
    assert len(budget) == 1
    assert "dpg_render=" in budget[0]     # never printed before PERF-11
    assert "gui_stats=0.7" in budget[0]
    assert "process_wall=31.6" in budget[0]
    assert app.ui.renders == 1


def test_budget_not_logged_when_preview_stage_did_not_ask(capsys):
    loop, app = _loop(ui=_Ui(), fps=19.7, timing={"process_wall": 31.6})
    loop._tick_render(_Tick(gui_stats_ms=0.7))
    assert "[Budget]" not in capsys.readouterr().out
    assert "dpg_render" in app.timing     # still injected for the GUI stats


# ---------------------------------------------------------------------------
# ARCH-6: per-frame exception boundary in the processing stage
# ---------------------------------------------------------------------------

import json  # noqa: E402

import numpy as np  # noqa: E402

from core.config import (  # noqa: E402
    OPS_TICK_ERROR_ALERT_INTERVAL_S,
    OPS_TICK_ERROR_EXIT_STREAK,
)
from core.osc_output import OSCSender  # noqa: E402
from runtime import api  # noqa: E402
from runtime.api import SystemState  # noqa: E402
from services import crash_marker  # noqa: E402


class _Bus:
    def __init__(self):
        self.events = []
        self.ui_ready = True

    def publish(self, event):
        self.events.append(event)

    def of(self, kind):
        return [e for e in self.events if isinstance(e, kind)]


class _Logger:
    def __init__(self):
        self.events = []
        self.closed = 0

    def log(self, event, data):
        self.events.append((event, data))

    def close(self):
        self.closed += 1


class _Processor:
    """process() raises while ``fail`` is set; returns an empty frame else."""

    def __init__(self, exc=None):
        self.exc = exc
        self.calls = 0
        self.osc = None

    def process(self, frame, need_preview=True, frame_number=None):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return [], None, {"total": 1.0}, 1.0


def _run_app(processor, tmp_path=None):
    """A LoopHost fake with exactly what _tick_process / run() touch."""
    models = SimpleNamespace(
        _model_loaded=True, model=object(), _model_loading=False,
        current_model_name="yolo11x-pose",
        model_manager=SimpleNamespace(is_using_tensorrt=lambda: False))
    app = SimpleNamespace(
        last_fps_time=0.0, ui=_Ui(), bus=_Bus(), models=models,
        system_state=SystemState.RUN,
        calibration=SimpleNamespace(_calibrating=False, _calibrating2=False),
        recorder=SimpleNamespace(is_playing=False, close=lambda: None),
        _total_frame_count=0, preview_enabled=False, frame_count=0,
        preview_stride=1, unified_camera=None, processor=processor,
        tracker=SimpleNamespace(logger=_Logger()), timing={}, latency_ms=0.0,
        last_tracked=[], _last_review_frame=None, running=True,
        configs=SimpleNamespace(_current_project="hangar"),
    )
    return app


def _frame_tick():
    return _Tick(frame=np.zeros((8, 8, 3), np.uint8))


def test_frame_error_skips_the_frame_and_alerts_once(capsys):
    proc = _Processor(RuntimeError("CUDA error: an illegal memory access"))
    app = _run_app(proc)
    loop = MainLoop(app)

    for _ in range(5):
        assert loop._tick_process(_frame_tick()) is False   # tick ends, no raise
    assert proc.calls == 5
    assert app.running is True                               # transient: keep going
    alerts = app.bus.of(api.Alert)
    assert len(alerts) == 1                                  # rate-limited
    assert alerts[0].kind == "frame_error"
    assert "illegal memory access" in alerts[0].message
    out = capsys.readouterr().out
    assert out.count("Traceback (most recent call last)") == 1
    assert ("OPS_ALERT", {"kind": "frame_error", "where": "frame processing",
                          "streak": 1, "count": 1}) in app.tracker.logger.events
    assert app.ui.renders == 5                               # UI kept alive

    # Recovery resets the streak; the next good frame is processed normally.
    proc.exc = None
    assert loop._tick_process(_frame_tick()) is True
    assert loop._proc_fail_streak == 0


def test_alert_repeats_after_the_interval(monkeypatch):
    import runtime.main_loop as ml
    clock = [1000.0]
    monkeypatch.setattr(ml.time, "monotonic", lambda: clock[0])
    proc = _Processor(ValueError("boom"))
    app = _run_app(proc)
    loop = MainLoop(app)
    loop._tick_process(_frame_tick())
    loop._tick_process(_frame_tick())
    clock[0] += OPS_TICK_ERROR_ALERT_INTERVAL_S + 0.1
    loop._tick_process(_frame_tick())
    alerts = app.bus.of(api.Alert)
    assert len(alerts) == 2
    assert alerts[1].data["count"] == 2          # 1 suppressed + this one


def test_persistent_frame_failure_requests_clean_exit():
    proc = _Processor(RuntimeError("CUDA error: device-side assert"))
    app = _run_app(proc)
    loop = MainLoop(app)
    for i in range(OPS_TICK_ERROR_EXIT_STREAK - 1):
        loop._tick_process(_frame_tick())
    assert app.running is True and loop._fatal is None
    loop._tick_process(_frame_tick())
    assert app.running is False
    assert "persistent frame processing failure" in loop._fatal
    assert "device-side assert" in loop._fatal


def test_unrelated_assertion_is_a_frame_error_not_a_crash():
    proc = _Processor(AssertionError("some tracker invariant"))
    app = _run_app(proc)
    app.models._is_trt_input_size_mismatch_error = lambda exc: False
    loop = MainLoop(app)
    assert loop._tick_process(_frame_tick()) is False
    assert loop._proc_fail_streak == 1


# ---------------------------------------------------------------------------
# ARCH-6: run() -- finally/shutdown, tick boundary, crash marker
# ---------------------------------------------------------------------------

def _runnable(tmp_path, app, *, startup=True, tick=None):
    loop = MainLoop(app, logs_dir=tmp_path)
    calls = {"shutdown": 0}

    def fake_startup():
        if isinstance(startup, BaseException):
            raise startup
        return startup

    def fake_shutdown():
        calls["shutdown"] += 1

    loop._startup = fake_startup
    loop._shutdown = fake_shutdown
    if tick is not None:
        loop._tick = tick
    app._watchdog = SimpleNamespace(start=lambda: None, stop=lambda: None)
    app.ui.is_running = lambda: True
    return loop, calls


def test_persistent_tick_failure_exits_cleanly_with_marker(tmp_path):
    app = _run_app(_Processor())

    def bad_tick():
        raise KeyError("preview stage blew up")

    loop, calls = _runnable(tmp_path, app, tick=bad_tick)
    assert loop.run() == 3
    assert calls["shutdown"] == 1
    marker = json.loads((tmp_path / crash_marker.MARKER).read_text())
    assert "persistent tick failure" in marker["reason"]
    assert marker["exception"].startswith("KeyError")
    assert "preview stage blew up" in marker["traceback"]
    assert marker["project"] == "hangar" and marker["state"] == "RUN"
    assert not (tmp_path / crash_marker.SENTINEL).exists()   # controlled exit


def test_transient_tick_failure_keeps_running(tmp_path):
    app = _run_app(_Processor())
    n = {"ticks": 0}

    def flaky_tick():
        n["ticks"] += 1
        if n["ticks"] in (2, 3, 5):
            raise RuntimeError("transient")
        if n["ticks"] >= 10:
            app.running = False          # operator quits

    loop, calls = _runnable(tmp_path, app, tick=flaky_tick)
    assert loop.run() == 0
    assert n["ticks"] == 10 and calls["shutdown"] == 1
    assert not (tmp_path / crash_marker.MARKER).exists()
    assert len(app.bus.of(api.Alert)) == 1


def test_uncaught_exception_runs_shutdown_writes_marker_and_reraises(tmp_path):
    app = _run_app(_Processor())
    loop, calls = _runnable(tmp_path, app,
                            startup=RuntimeError("camera driver exploded"))
    with pytest.raises(RuntimeError, match="camera driver exploded"):
        loop.run()
    assert calls["shutdown"] == 1
    marker = json.loads((tmp_path / crash_marker.MARKER).read_text())
    assert marker["reason"] == "uncaught exception in the main loop"
    assert "camera driver exploded" in marker["traceback"]

    # The next start reports it (console + toast) and archives it.
    app2 = _run_app(_Processor())
    loop2, _ = _runnable(tmp_path, app2, startup=False)
    assert loop2.run() == 2                          # startup failure code
    assert loop2._previous_crash["kind"] == "exception"
    assert not (tmp_path / crash_marker.MARKER).exists()
    assert (tmp_path / crash_marker.REPORTED).exists()


def test_keyboard_interrupt_is_not_a_crash(tmp_path):
    app = _run_app(_Processor())

    def interrupted():
        raise KeyboardInterrupt

    loop, calls = _runnable(tmp_path, app, tick=interrupted)
    with pytest.raises(KeyboardInterrupt):
        loop.run()
    assert calls["shutdown"] == 1
    assert not (tmp_path / crash_marker.MARKER).exists()
    assert not (tmp_path / crash_marker.SENTINEL).exists()


def test_previous_crash_is_toasted_at_startup(tmp_path):
    crash_marker.write_crash(tmp_path, reason="uncaught exception in the main loop",
                             exception="RuntimeError: CUDA error", project="hangar",
                             state="RUN")
    app = _run_app(_Processor())
    loop = MainLoop(app, logs_dir=tmp_path)
    loop._previous_crash = crash_marker.begin_session(tmp_path)
    loop._toast_previous_crash()          # the tail of _startup()
    toasts = app.bus.of(api.Toast)
    assert len(toasts) == 1
    assert "hangar" in toasts[0].message
    assert "RuntimeError: CUDA error" in toasts[0].message
    assert toasts[0].duration >= 10


def test_no_toast_after_a_clean_exit(tmp_path):
    crash_marker.begin_session(tmp_path)
    crash_marker.end_session(tmp_path)
    app = _run_app(_Processor())
    loop = MainLoop(app, logs_dir=tmp_path)
    loop._previous_crash = crash_marker.begin_session(tmp_path)
    loop._toast_previous_crash()
    assert loop._previous_crash is None and not app.bus.of(api.Toast)


def test_shutdown_steps_are_isolated(capsys):
    order = []

    def boom():
        order.append("watchdog")
        raise RuntimeError("watchdog join failed")

    app = SimpleNamespace(
        last_fps_time=0.0,
        api=SimpleNamespace(drain=lambda: order.append("drain")),
        tracker=SimpleNamespace(logger=SimpleNamespace(
            close=lambda: order.append("tracker_log"))),
        _watchdog=SimpleNamespace(stop=boom),
        _web_monitor=None, _remote=None,
        recorder=SimpleNamespace(close=lambda: order.append("recorder")),
        camera=SimpleNamespace(cap=None),
        ui=SimpleNamespace(destroy=lambda: order.append("ui")),
    )
    MainLoop(app)._shutdown()
    assert order == ["drain", "tracker_log", "watchdog", "recorder", "ui"]
    assert "[Shutdown] watchdog stop failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ARCH-6: OSC send failures surface as one operator alert
# ---------------------------------------------------------------------------

class _FailingClient:
    def send_message(self, address, args):
        raise PermissionError(13, "Permission denied")


def test_osc_send_failure_becomes_ops_alert():
    osc = OSCSender("255.255.255.255", 9000)
    osc.enabled = True
    osc.client = _FailingClient()
    proc = _Processor()
    proc.osc = osc
    app = _run_app(proc)
    loop = MainLoop(app)
    track = SimpleNamespace(track_id=1, bbox=np.array([1., 2., 3., 4.]),
                            velocity=np.zeros(2), keypoints=np.zeros((17, 2)),
                            confidence=np.zeros(17), smoothed_centroid=None)
    osc.send_frame([track], 100, 100)              # must not raise
    osc.send_latency_ms(0.0)
    loop._poll_osc_alert()
    loop._poll_osc_alert()                         # alert taken once
    alerts = app.bus.of(api.Alert)
    assert len(alerts) == 1 and alerts[0].kind == "osc_send"
    assert alerts[0].data["target"] == "255.255.255.255:9000"
    assert alerts[0].data["errors"] == osc.send_errors == 6
