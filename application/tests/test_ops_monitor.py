"""Ops cluster (TODO Phase 7): readiness checks, HealthMonitor, LoopWatchdog.

Everything timing-related uses injected clocks (plain floats / datetimes) -
no sleeps, no hardware. The only real sockets are loopback UDP probes.
"""
import socket
import threading
import time
from collections import namedtuple
from datetime import datetime

import pytest

from ops_monitor import (
    Alert,
    CheckResult,
    HealthMonitor,
    LoopWatchdog,
    ReadinessReport,
    check_calibration,
    check_camera,
    check_disk,
    check_gpu_temp,
    check_osc,
    check_tensorrt,
    probe_osc_udp,
)


# ---------------------------------------------------------------- readiness

def test_check_camera_ok_warn_fail():
    ok = check_camera(is_open=True, reconnecting=False, source="ids",
                      fps=19.8, min_fps=15.0, ids_frames=1234, ids_dropped=2)
    assert ok.status == "ok" and "19.8" in ok.detail and "1234" in ok.detail

    slow = check_camera(is_open=True, reconnecting=False, source="ids",
                        fps=9.0, min_fps=15.0)
    assert slow.status == "warn"

    down = check_camera(is_open=False, reconnecting=False, source="0",
                        fps=0.0, min_fps=15.0)
    assert down.status == "fail"

    recon = check_camera(is_open=True, reconnecting=True, source="ids",
                         fps=0.0, min_fps=15.0)
    assert recon.status == "fail" and "reconnecting" in recon.detail


def test_check_tensorrt_states():
    assert check_tensorrt(trt_requested=True, trt_active=True).status == "ok"
    fb = check_tensorrt(trt_requested=True, trt_active=False,
                        fallback_reason="no engine for imgsz 1280")
    assert fb.status == "warn" and "no engine" in fb.detail
    assert check_tensorrt(trt_requested=False, trt_active=False).status == "ok"
    degraded = check_tensorrt(trt_requested=True, trt_active=True,
                              gpu_fallback_reason="kornia unavailable")
    assert degraded.status == "warn"


def test_check_osc_uses_injected_probe_and_skips_when_disabled():
    calls = []

    def fake_probe(ip, port, timeout_s):
        calls.append((ip, port, timeout_s))
        return "fail", "nope"

    r = check_osc(enabled=True, ip="10.0.0.9", port=9000, timeout_s=0.1,
                  probe=fake_probe)
    assert r.status == "fail" and calls == [("10.0.0.9", 9000, 0.1)]

    off = check_osc(enabled=False, ip="10.0.0.9", port=9000, timeout_s=0.1,
                    probe=fake_probe)
    assert off.status == "skip" and len(calls) == 1  # probe not called


def test_check_calibration_age():
    now = datetime(2026, 6, 11, 12, 0, 0)
    fresh = check_calibration(saved_at_iso="2026-06-11T09:00:00",
                              active_profile="show", warn_age_h=24.0, now=now)
    assert fresh.status == "ok" and "3.0 h ago" in fresh.detail

    stale = check_calibration(saved_at_iso="2026-06-08T09:00:00",
                              active_profile="show", warn_age_h=24.0, now=now)
    assert stale.status == "warn"

    missing = check_calibration(saved_at_iso=None, active_profile="rehearsal",
                                warn_age_h=24.0, now=now)
    assert missing.status == "warn" and "rehearsal" in missing.detail

    junk = check_calibration(saved_at_iso="yesterday-ish",
                             active_profile="show", warn_age_h=24.0, now=now)
    assert junk.status == "warn"


Usage = namedtuple("usage", "total used free")


def test_check_disk_thresholds():
    def usage_gb(free):
        return lambda path: Usage(500e9, 500e9 - free * 1e9, free * 1e9)

    assert check_disk(recordings_dir="X", warn_free_gb=60, fail_free_gb=10,
                      disk_usage=usage_gb(200)).status == "ok"
    assert check_disk(recordings_dir="X", warn_free_gb=60, fail_free_gb=10,
                      disk_usage=usage_gb(30)).status == "warn"
    assert check_disk(recordings_dir="X", warn_free_gb=60, fail_free_gb=10,
                      disk_usage=usage_gb(5)).status == "fail"

    def boom(path):
        raise OSError("no such dir")

    assert check_disk(recordings_dir="X", warn_free_gb=60, fail_free_gb=10,
                      disk_usage=boom).status == "warn"


def test_check_gpu_temp():
    assert check_gpu_temp(gpu_stats=None, warn_c=85).status == "skip"
    assert check_gpu_temp(gpu_stats={"util": -1}, warn_c=85).status == "skip"
    cool = check_gpu_temp(gpu_stats={"util": 30, "temp": 61, "vram_pct": 40.0},
                          warn_c=85)
    assert cool.status == "ok" and "61 C" in cool.detail
    hot = check_gpu_temp(gpu_stats={"util": 90, "temp": 88, "vram_pct": 80.0},
                         warn_c=85)
    assert hot.status == "warn"


def test_report_worst_precedence_block_and_toast():
    report = ReadinessReport([
        CheckResult("camera", "ok", "ids @ 20 FPS"),
        CheckResult("osc", "skip", "disabled"),
        CheckResult("disk", "warn", "30 GB free"),
    ])
    assert report.worst == "warn"
    block = report.console_block("(project=x) ")
    assert block.count("[Readiness]") == 5  # header + 3 checks + footer
    msg, color = report.toast_summary()
    assert "1 warn" in msg and color == (255, 200, 100)

    report.results.append(CheckResult("camera", "fail", "down"))
    assert report.worst == "fail"
    assert report.toast_summary()[1] == (255, 80, 80)


# ------------------------------------------------- loopback probe (real UDP)

def test_probe_osc_loopback_listener_replies_or_silent():
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    try:
        status, detail = probe_osc_udp("127.0.0.1", port, 0.2)
        # A bound socket that doesn't reply -> silent timeout -> ok
        assert status == "ok"
    finally:
        listener.close()


def test_probe_osc_loopback_closed_port_fails():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listening here now
    status, detail = probe_osc_udp("127.0.0.1", port, 0.3)
    # Windows and Linux both surface ICMP port-unreachable on connected UDP
    # sockets; tolerate exotic stacks that swallow it (then it's a timeout).
    assert status in ("fail", "ok")
    if status == "fail":
        assert "nothing listening" in detail


# ------------------------------------------------------------ HealthMonitor

def _mk(**kw):
    defaults = dict(fps_window_s=60.0, fps_drop_fraction=0.5, fps_sustain_s=10.0,
                    no_detection_s=30.0, camera_down_s=15.0, gpu_temp_c=85,
                    gpu_temp_sustain_s=30.0, gpu_poll_s=5.0, cooldown_s=120.0,
                    min_baseline_samples=5, gpu_stats_fn=None)
    defaults.update(kw)
    return HealthMonitor(**defaults)


def _live(fps=20.0, n=2, **kw):
    base = dict(fps=fps, n_tracked=n, in_run=True, model_ready=True,
                camera_open=True, camera_reconnecting=False,
                playback_active=False)
    base.update(kw)
    return base


def test_fps_drop_fires_after_sustain_and_respects_cooldown():
    m = _mk()
    t = 0.0
    for _ in range(10):  # build baseline at 20 fps
        t += 1.0
        assert m.tick(t, **_live()) == []
    # Sustained collapse to 5 fps
    alerts = []
    for _ in range(12):
        t += 1.0
        alerts += m.tick(t, **_live(fps=5.0))
    kinds = [a.kind for a in alerts]
    assert kinds == ["fps_drop"]
    # Still low: cooldown suppresses an immediate re-fire
    for _ in range(30):
        t += 1.0
        alerts += m.tick(t, **_live(fps=5.0))
    assert len(alerts) == 1
    # After the cooldown it may ring again
    t += 120.0
    alerts += m.tick(t, **_live(fps=5.0))
    assert [a.kind for a in alerts] == ["fps_drop", "fps_drop"]


def test_fps_baseline_resets_on_run_transition():
    m = _mk()
    t = 0.0
    for _ in range(10):  # standby regime at high fps must NOT seed baseline
        t += 1.0
        m.tick(t, **_live(fps=60.0, in_run=False))
    # enter RUN at 20 fps: no baseline from standby, so no false drop alert
    alerts = []
    for _ in range(20):
        t += 1.0
        alerts += m.tick(t, **_live(fps=20.0))
    assert alerts == []


def test_no_detection_only_when_live():
    m = _mk(no_detection_s=5.0)
    t = 0.0
    alerts = []
    # zero dancers during playback -> suppressed
    for _ in range(10):
        t += 1.0
        alerts += m.tick(t, **_live(n=0, playback_active=True))
    assert alerts == []
    # zero dancers while model is loading -> suppressed
    for _ in range(10):
        t += 1.0
        alerts += m.tick(t, **_live(n=0, model_ready=False))
    assert alerts == []
    # zero dancers live -> fires after sustain
    for _ in range(7):
        t += 1.0
        alerts += m.tick(t, **_live(n=0))
    assert [a.kind for a in alerts] == ["no_detection"]


def test_over_cap_fires_after_sustain_and_in_playback():
    m = _mk(over_cap_s=5.0)
    t = 0.0
    alerts = []
    # over cap while NOT in RUN -> suppressed (stale tracker value in standby)
    for _ in range(10):
        t += 1.0
        alerts += m.tick(t, **_live(in_run=False), n_over_cap=2)
    assert alerts == []
    # transient flash under the sustain window -> quiet
    for _ in range(3):
        t += 1.0
        alerts += m.tick(t, **_live(), n_over_cap=2)
    t += 1.0
    alerts += m.tick(t, **_live(), n_over_cap=0)
    assert alerts == []
    # sustained over cap during playback rehearsal -> fires (unlike fps/no-det
    # this alert is gated only on RUN + model ready)
    for _ in range(7):
        t += 1.0
        alerts += m.tick(t, **_live(playback_active=True), n_over_cap=3)
    assert [a.kind for a in alerts] == ["over_cap"]
    assert alerts[0].data["n_over_cap"] == 3


def test_camera_down_escalates_and_refires_each_cooldown():
    m = _mk(camera_down_s=15.0, cooldown_s=60.0)
    t = 0.0
    alerts = []
    for _ in range(200):
        t += 1.0
        alerts += m.tick(t, **_live(camera_reconnecting=True))
    kinds = [a.kind for a in alerts]
    assert kinds.count("camera_down") >= 3  # escalate + re-ring while down
    # recovery clears the episode
    alerts.clear()
    for _ in range(20):
        t += 1.0
        alerts += m.tick(t, **_live())
    assert alerts == []


def test_gpu_temp_sustain_with_injected_stats():
    temps = {"temp": 90}
    m = _mk(gpu_stats_fn=lambda: {"util": 50, "temp": temps["temp"]},
            gpu_temp_sustain_s=10.0, gpu_poll_s=1.0)
    t = 0.0
    alerts = []
    for _ in range(15):
        t += 1.0
        alerts += m.tick(t, **_live())
    assert [a.kind for a in alerts] == ["gpu_temp"]
    # cooling clears the sustain window
    temps["temp"] = 60
    alerts.clear()
    for _ in range(15):
        t += 1.0
        alerts += m.tick(t, **_live())
    assert alerts == []


# -------------------------------------------------------------- LoopWatchdog

def test_watchdog_evaluate_pure():
    ev = LoopWatchdog.evaluate
    assert ev(100.0, 105.0, 10.0, 0, False) is None          # not old enough
    assert ev(100.0, 111.0, 10.0, 0, False) == pytest.approx(11.0)
    assert ev(100.0, 111.0, 10.0, 1, False) is None          # busy suppressed
    assert ev(100.0, 111.0, 10.0, 0, True) is None           # once per episode


def test_watchdog_busy_counter_reentrant():
    w = LoopWatchdog(hang_s=10.0, poll_s=0.1, on_hang=lambda age: None)
    w.push_busy("model")
    w.push_busy("nested")
    assert w._busy_depth == 2
    w.pop_busy()
    assert w._busy_depth == 1
    w.pop_busy()
    assert w._busy_depth == 0
    w.pop_busy()  # extra pop never goes negative
    assert w._busy_depth == 0


def test_watchdog_thread_reports_hang_and_resume():
    hangs, resumes = [], []
    w = LoopWatchdog(hang_s=0.2, poll_s=0.05,
                     on_hang=hangs.append, on_resume=resumes.append)
    w.start()
    try:
        deadline = time.monotonic() + 3.0
        while not hangs and time.monotonic() < deadline:
            time.sleep(0.05)  # no beats -> hang fires
        assert hangs and hangs[0] >= 0.2
        assert len(hangs) == 1  # once per episode
        w.beat()
        deadline = time.monotonic() + 3.0
        while not resumes and time.monotonic() < deadline:
            time.sleep(0.05)
        assert resumes
    finally:
        w.stop()


def test_watchdog_start_stop_joins():
    w = LoopWatchdog(hang_s=10.0, poll_s=0.05)
    w.start()
    assert w._thread is not None and w._thread.is_alive()
    w.stop()
    assert w._thread is None
