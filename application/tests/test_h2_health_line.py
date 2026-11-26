"""H2 (#t-040 ①): one health line during RUN -- the worst state wins (core/ops_monitor.py).

Clauses of the gate (hub row #t-040 ①):
  * the worst state wins over every source, with its instruction;
  * signal (a) « a foreground group has no point » turns the line non-green after 3 s, and back when the group
    gets a point or disappears; never green while it holds for more than 3 s;
  * the stall rate's thresholds;
  * the foreground off or the plate invalid: (a) says « not checked », the line is never green by default;
plus the plumbing: the main loop feeds and publishes it, /status serves it, the GUI paints it, wdwatch shows it.
The replay clause (mur2 7 at l @ 1920 x 1056) is measured by tmp_analysis/r2-1126/gates/h2/run_gate.sh.
"""
from __future__ import annotations

import importlib.util
import random
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest

from core import ops_monitor as OM
from core.ops_monitor import (BareGroupWatch, CheckResult, HealthLine, HealthMonitor, StallRate,
                              check_enhancement, check_osc, check_plate_exposure, check_roi, foreground_groups,
                              health_alert_rows, health_camera_row, health_engine_row, health_frames_row,
                              health_line, health_output_row, health_plate_row)
from runtime import api

FPS = 20.0
H = 120.0                                    # the dancers' height in these tests (original px)


def _frames(t0, t1):
    """Frame times from t0 to t1 at 20 fps."""
    n = int(round((t1 - t0) * FPS))
    return [round(t0 + i / FPS, 4) for i in range(n + 1)]


def _ok_rows():
    """Every other source, nominal."""
    return [health_camera_row(is_open=True, reconnecting=False, source="ids", fps=19.8),
            health_output_row(enabled=True, target="10.0.0.5:7000"),
            health_plate_row(status="ready"),
            health_frames_row(fail_streak=0),
            health_engine_row(trt_requested=True, trt_active=True)]


def _live_stalls(now, stalls=0):
    s = StallRate()
    s.update(now - 1.0, 0, True)
    s.update(now, stalls, True)
    return s


# ---------------------------------------------------------------------------
# Clause: the worst state wins over every source, with its instruction
# ---------------------------------------------------------------------------

def _bad_rows():
    """One failing row per source -> (row, the instruction the line must carry)."""
    mon = HealthMonitor(gpu_stats_fn=None)
    out = [
        (health_camera_row(is_open=False, reconnecting=True, source="ids", down_s=12.0),
         "check the camera's USB3 cable and power"),
        (health_output_row(enabled=True, target="10.0.0.5:7000", errors=40),
         "start TouchDesigner and its patch"),
        (health_plate_row(status="stale", reason="plate stale"),
         "when the wall is empty: CALIBRATE (2 Calibrate), then Save to project"),
        (health_plate_row(status="ready", stale_since="21:43:10"),
         "when the wall is empty: CALIBRATE (2 Calibrate), then Save to project"),
        (health_frames_row(fail_streak=3), "restart the app"),
        (health_engine_row(trt_requested=True, trt_active=False), "restart the app"),
        (check_enhancement(enabled=False, gamma=1.3, clahe=2.5, calibrated="2026-12-12 20:10"),
         "Ctrl+Shift+E, Advanced > Enhancement > Enable"),
        (check_roi(enabled=True, drawn=(100, 100, 900, 1700, 1296, 1776), frame_size=(1296, 1200)),
         "draw it again (1 Rig > Region of Interest), then Calibrate"),
    ]
    plate = SimpleNamespace(camera={"exposure_us": 20000.0, "gain_db": 30.0}, frame_size=(1296, 1776),
                            created="2026-12-12T20:10:00", camera_mismatch=lambda e, g: "")
    out.append((check_plate_exposure(plate=plate, exposure_us=20000.0, gain_db=30.0, frame_size=(1296, 1776),
                                     invalid="the exposure was changed by hand"), None))
    w = BareGroupWatch()
    for t in _frames(0.0, 3.5):
        w.update(t, [(500.0, 900.0, 4000.0)], [], body_h=H)
    out.append((w.state(3.5), "if it is a dancer: check Max dancers (3 Live)"))
    s = StallRate()
    for i in range(61):
        s.update(float(i), i // 8, True)        # 7 stalls in a minute
    out.append((s.state(60.0), "at the next break: reseat the camera's USB cable at both ends, then try the "
                               "other cable"))           # stall verdict 26/11 §8.3: the cable first
    return out


def test_every_source_alone_turns_the_line_its_state_with_its_instruction():
    for bad, instruction in _bad_rows():
        assert bad.status == "fail", bad
        line = health_line(_ok_rows() + [bad])
        assert line.state == "fail" and line.word == "FAIL"
        assert line.source == bad.name
        assert line.text.startswith(f"FAIL - {bad.name}: ")
        if instruction is not None:
            assert line.instruction == instruction, (bad.name, line.instruction)
        assert line.color == OM.STATUS_COLORS["fail"]


def test_the_health_monitors_conditions_are_rows_while_they_hold():
    mon = HealthMonitor(gpu_stats_fn=lambda: {"temp": 90, "util": 50})
    now = 1000.0
    for k in range(0, 200):                 # 200 s of RUN: no dancer tracked, over the cap, the GPU hot
        mon.tick(now + k, fps=20.0, n_tracked=0, in_run=True, model_ready=True, camera_open=True,
                 camera_reconnecting=False, playback_active=False, n_over_cap=1)
    rows = {r.name: r for r in health_alert_rows(mon, now + 199)}
    assert {"no dancer", "over cap", "gpu"} <= set(rows)
    assert all(r.status == "warn" for r in rows.values())
    assert rows["no dancer"].detail.endswith("if dancers are on the wall: check the IR and the picture")
    assert rows["over cap"].detail.endswith("check Max dancers (3 Live)")
    line = health_line(_ok_rows() + list(rows.values()))
    assert line.state == "warn" and line.source == "gpu"       # HEALTH_PRIORITY between the warnings
    mon.tick(now + 200, fps=20.0, n_tracked=2, in_run=True, model_ready=True, camera_open=True,
             camera_reconnecting=False, playback_active=False, n_over_cap=0)
    assert {r.name for r in health_alert_rows(mon, now + 200)} == {"gpu"}   # the conditions that ended go


def test_fail_beats_warn_beats_not_checked_beats_ok_and_priority_breaks_ties():
    ok = CheckResult("camera", "ok", "ids @ 20 FPS")
    skip = CheckResult(OM.POINTS_ROW, "skip", "not checked: the empty wall is none")
    warn = CheckResult("output", "warn", "OSC is off: the visuals get no point - Advanced > OSC > Enable OSC")
    fail = CheckResult("camera stalls", "fail", "7.0 a minute: the picture freezes often - change the port")
    assert health_line([ok]).state == "ok"
    assert health_line([ok, skip]).state == "skip"
    assert health_line([ok, skip, warn]).state == "warn"
    line = health_line([ok, skip, warn, fail])
    assert line.state == "fail" and line.source == "camera stalls" and line.instruction == "change the port"
    assert "(+2: output, points)" in line.text           # the others, worst first
    cam = CheckResult("camera", "fail", "not open - check the camera's USB3 cable and power")
    assert health_line([fail, cam]).source == "camera"   # equal states: the camera names the line
    assert health_line([]).state == "skip"               # nothing checked is not green


def test_the_line_is_green_only_when_every_row_is_ok():
    line = health_line(_ok_rows() + [CheckResult(OM.POINTS_ROW, "ok", "2 bodies"),
                                     CheckResult("camera stalls", "ok", "0.0 a minute")])
    assert line.state == "ok" and line.text.startswith("OK - all 7 checks fine (camera, output, points")
    assert line.to_dict()["rows"] == [] and "camera" in line.to_dict()["ok"]
    for status in ("skip", "warn", "fail", "unknown"):
        assert health_line(_ok_rows() + [CheckResult("x", status, "y")]).state != "ok"


def test_osc_disabled_is_orange_in_readiness_and_in_the_line():
    r = check_osc(enabled=False, ip="10.0.0.5", port=7000, timeout_s=0.1, probe=lambda *a: ("ok", ""))
    assert r.status == "warn" and r.detail.endswith("Advanced > OSC > Enable OSC")
    row = health_output_row(enabled=False, target="10.0.0.5:7000")
    line = health_line([row] + _ok_rows()[2:])
    assert line.state == "warn" and line.source == "output"
    assert line.instruction == "Advanced > OSC > Enable OSC"


def test_a_recording_playing_in_run_is_orange_and_names_live():
    row = health_camera_row(is_open=True, reconnecting=False, playing=True, source="ids", fps=20.0)
    assert row.status == "warn" and row.detail.endswith("LIVE (Recordings bar)")


# ---------------------------------------------------------------------------
# Clause: signal (a) -- after 3 s, and back when the group gets a point or disappears
# ---------------------------------------------------------------------------

GROUP = (500.0, 900.0, 4000.0)


def _line(w, t):
    return HealthLine(w, _live_stalls(t)).compose(t, _ok_rows())


def test_a_turns_the_line_red_after_3_s_not_before():
    w = BareGroupWatch()
    for t in _frames(0.0, 3.0):
        w.update(t, [GROUP], [], body_h=H)
        assert w.state(t).status == "ok" and _line(w, t).state == "ok", t     # within the 3 s: green
    w.update(3.05, [GROUP], [], body_h=H)
    row = w.state(3.05)
    assert row.status == "fail" and row.name == "points"
    assert row.detail == ("a body on the wall has had no point for 3 s - if it is a dancer: check Max dancers "
                          "(3 Live)")
    line = _line(w, 3.05)
    assert line.state == "fail" and line.source == "points"
    assert line.instruction == "if it is a dancer: check Max dancers (3 Live)"
    assert w.snapshot(3.05)["episodes"] == 1


def test_a_back_when_the_group_gets_a_point():
    w = BareGroupWatch()
    for t in _frames(0.0, 5.0):
        w.update(t, [GROUP], [], body_h=H)
    assert w.state(5.0).status == "fail"
    point = (GROUP[0] + 20.0, GROUP[1] - 40.0, H)                # her point, on her
    for t in _frames(5.05, 7.0):
        w.update(t, [GROUP], [point], body_h=H)
        expect = "fail" if t - 5.0 <= OM.BARE_GAP_S else "ok"    # a point for a frame is not enough
        assert w.state(t).status == expect, t
    assert _line(w, 7.0).state == "ok"


def test_a_back_when_the_group_disappears():
    w = BareGroupWatch()
    for t in _frames(0.0, 4.0):
        w.update(t, [GROUP], [], body_h=H)
    for t in _frames(4.05, 6.0):
        w.update(t, [], [], body_h=H)
        assert w.state(t).status == ("fail" if t - 4.0 <= OM.BARE_GAP_S else "ok"), t


def test_a_a_flicker_does_not_restart_the_count():
    """One frame under the group size, one frame a point passing over it: still 3 s from the first frame."""
    w = BareGroupWatch()
    for t in _frames(0.0, 3.2):
        if abs(t - 1.0) < 1e-6:
            w.update(t, [], [], body_h=H)                            # under the size this frame
        elif abs(t - 2.0) < 1e-6:
            w.update(t, [GROUP], [(GROUP[0], GROUP[1], H)], body_h=H)   # a point passes over it
        else:
            w.update(t, [GROUP], [], body_h=H)
    assert w.state(3.2).status == "fail" and w.worst_s(3.2) == pytest.approx(3.2)


def test_a_an_invalid_frame_says_not_checked_and_does_not_restart_the_count():
    w = BareGroupWatch()
    for t in _frames(0.0, 3.2):
        if abs(t - 2.0) < 1e-6:
            w.update(t, None, [], body_h=H, why="the foreground is not valid (plate stale)")
            assert w.state(t).status == "skip"                       # that frame: not checked, not green
        else:
            w.update(t, [GROUP], [], body_h=H)
    assert w.state(3.2).status == "fail" and w.worst_s(3.2) == pytest.approx(3.2)
    for t in _frames(3.25, 5.0):                                     # longer than the gap: it starts again
        w.update(t, None, [], body_h=H, why="the empty wall is none")
    w.update(5.05, [GROUP], [], body_h=H)
    assert w.state(5.05).status == "ok" and w.worst_s(5.05) == 0.0


def test_a_a_moving_dancer_keeps_her_count_and_a_jumping_group_counts_too():
    w = BareGroupWatch()
    for t in _frames(0.0, 3.5):                      # she climbs 60 px a second: followed
        w.update(t, [(500.0, 900.0 - 60.0 * t, 4000.0)], [], body_h=H)
    assert w.state(3.5).status == "fail" and w.snapshot(3.5)["episodes"] == 1
    w2 = BareGroupWatch()
    for k, t in enumerate(_frames(0.0, 3.5)):        # a bare group on every frame, never at the same place
        w2.update(t, [(200.0 + 600.0 * (k % 2), 300.0 + 1000.0 * (k % 2), 900.0)], [], body_h=H)
    assert w2.state(3.5).status == "fail"


def test_a_a_point_sits_on_a_group_within_its_reach():
    w = BareGroupWatch()
    near = (GROUP[0], GROUP[1] - 0.70 * H, H)        # 0.70 x its box height from the group's centre
    far = (GROUP[0], GROUP[1] - 0.80 * H, H)
    for t in _frames(0.0, 4.0):
        w.update(t, [GROUP], [near], body_h=H)
    assert w.state(4.0).status == "ok" and w.snapshot(4.0)["bare"] == 0
    for t in _frames(4.05, 8.0):
        w.update(t, [GROUP], [far], body_h=H)
    assert w.state(8.0).status == "fail"


def test_a_two_bodies_one_point_and_where_they_are():
    w = BareGroupWatch()
    bounds = (0.0, 0.0, 900.0, 1800.0)
    a, b = (150.0, 200.0, 3000.0), (750.0, 1600.0, 3000.0)
    for t in _frames(0.0, 4.0):
        w.update(t, [a, b], [(a[0], a[1], H)], body_h=H, bounds=bounds)
    row = w.state(4.0)
    assert row.status == "fail" and "(bottom right)" in row.detail and "a body on the wall has" in row.detail
    w2 = BareGroupWatch()
    for t in _frames(0.0, 4.0):
        w2.update(t, [a, b], [], body_h=H, bounds=bounds)
    assert "2 bodies on the wall have had no point" in w2.state(4.0).detail
    assert "top left" in w2.state(4.0).detail and "bottom right" in w2.state(4.0).detail
    assert OM.place_text(450.0, 900.0, bounds) == "centre" and OM.place_text(1.0, 1.0, None) == ""


def test_a_groups_are_the_referees_minus_the_excluded_cells():
    from core.foreground import FgBlob, FgFrame
    from core.swipe import SwipeParams
    blobs = [FgBlob(500.0, 900.0, 4000.0, 1.0, (480, 830, 520, 970)),
             FgBlob(100.0, 100.0, 900.0, 1.0, (90, 80, 110, 120)),
             FgBlob(300.0, 300.0, SwipeParams().group_min_area - 4.0, 1.0, (295, 290, 305, 310))]
    fg = FgFrame(np.zeros((10, 10), np.uint8), 0.0, 0.0, 4, blobs=blobs)
    groups, why = foreground_groups(fg)
    assert why == "" and [g[:2] for g in groups] == [(500.0, 900.0), (100.0, 100.0)]   # the referee's size
    groups, _ = foreground_groups(fg, zone_ok=lambda x, y: y > 200.0)                 # a cell at the top
    assert [g[:2] for g in groups] == [(500.0, 900.0)]
    bad = FgFrame(None, 0.0, 0.0, 4, valid=False, reason="plate stale")
    assert foreground_groups(bad) == (None, "the foreground is not valid (plate stale)")
    assert foreground_groups(None)[0] is None


def test_a_never_green_while_a_group_has_no_point_for_more_than_3_s_random():
    """Random scenes (bodies entering, leaving, points coming and going, flicker); wherever some group has had
    no point on every frame for more than 3 s, the line is not green."""
    rng = random.Random(40)
    judged = 0
    for _scene in range(30):
        w = BareGroupWatch()
        stalls = StallRate()
        line = HealthLine(w, stalls)
        bodies = [[rng.uniform(100, 800), rng.uniform(100, 1700)] for _ in range(rng.randint(1, 3))]
        pointed = [True] * len(bodies)                 # each body's point comes and goes for seconds at a time
        until = [rng.expovariate(1 / 3.0) for _ in bodies]
        bare_since = None
        for k, t in enumerate(_frames(0.0, 40.0)):
            groups, points = [], []
            for i, b in enumerate(bodies):
                b[0] += rng.uniform(-6, 6)
                b[1] += rng.uniform(-6, 6)
                if t >= until[i]:
                    pointed[i], until[i] = not pointed[i], t + rng.expovariate(1 / 3.0)
                if rng.random() < 0.97:              # a frame under the size now and then
                    groups.append((b[0], b[1], rng.uniform(300, 5000)))
                if pointed[i] and rng.random() < 0.95:
                    points.append((b[0] + rng.uniform(-30, 30), b[1] + rng.uniform(-60, 60), H))
            if rng.random() < 0.05:                  # a point passing somewhere
                points.append((rng.uniform(100, 800), rng.uniform(100, 1700), H))
            w.update(t, groups, points, body_h=H)
            stalls.update(t, 0, True)
            state = line.compose(t, _ok_rows()).state
            bare = any(all((p[0] - g[0]) ** 2 + (p[1] - g[1]) ** 2 > (0.75 * p[2]) ** 2 for p in points)
                       for g in groups)
            bare_since = (bare_since if bare_since is not None else t) if bare else None
            if bare_since is not None and t - bare_since > 3.0:
                assert state != "ok", (t, bare_since)
                judged += 1
    assert judged > 200                              # the scenes do hold (a) for more than 3 s


# ---------------------------------------------------------------------------
# Clause: the foreground off or the plate invalid -- « not checked », never green by default
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,plate_state", [("off", "warn"), ("none", "warn"), ("size", "fail"),
                                                ("stale", "fail"), ("capturing", "skip")])
def test_a_not_checked_when_the_foreground_is_off_or_the_plate_invalid(status, plate_state):
    w = BareGroupWatch()
    for t in _frames(0.0, 5.0):
        w.update(t, None, [(500.0, 900.0, H)], body_h=H, why=f"the empty wall is {status}")
    row = w.state(5.0)
    assert row.status == "skip" and row.detail == f"not checked: the empty wall is {status}"
    plate = health_plate_row(status=status, reason="plate stale" if status == "stale" else "")
    assert plate.status == plate_state
    rows = [r for r in _ok_rows() if r.name != "empty wall"] + [plate]
    line = HealthLine(w, _live_stalls(5.0)).compose(5.0, rows)
    assert line.state != "ok"
    assert line.state == ("skip" if plate_state == "skip" else plate_state)
    assert any(r.name == "points" and r.status == "skip" for r in line.rows)


def test_a_not_checked_by_default_and_when_the_frames_stop():
    w = BareGroupWatch()
    assert w.state(0.0).status == "skip" and w.state(0.0).detail == "not checked: no frame yet"
    for t in _frames(0.0, 1.0):
        w.update(t, [GROUP], [(GROUP[0], GROUP[1], H)], body_h=H)
    assert w.state(1.0).status == "ok"
    assert w.state(1.0 + OM.BARE_STALE_S + 0.1).status == "skip"     # no new frame: not checked, not green
    assert HealthLine().compose(0.0, _ok_rows()).state == "skip"      # a line before anything: not green


# ---------------------------------------------------------------------------
# Clause: the stall rate's thresholds
# ---------------------------------------------------------------------------

def _rate(stall_times, until, live=lambda t: True):
    s = StallRate()
    for i in range(int(until) + 1):
        s.update(float(i), sum(1 for x in stall_times if x <= i), live(float(i)))
    return s


def test_stall_rate_thresholds():
    assert OM.STALL_WARN_PER_MIN == 2.0 and OM.STALL_FAIL_PER_MIN == 6.0
    assert _rate([], 120).state(120.0).status == "ok"
    one = _rate([10.0], 30).state(30.0)
    assert one.status == "ok" and one.detail.startswith("1.0 a minute")       # never over less than a minute
    two = _rate([10.0, 20.0], 30)
    assert two.state(30.0).status == "warn" and two.rate()[0] == pytest.approx(2.0)
    assert two.state(30.0).detail.endswith(" - at the next break: reseat the camera's USB cable at both ends, "
                                           "then try the other cable")
    under = _rate([20.0, 70.0, 130.0, 190.0], 240)                  # 4 in 4 minutes: 1 a minute
    assert under.state(240.0).status == "ok"
    five = _rate([10.0 * k for k in range(1, 6)], 60)
    assert five.state(60.0).status == "warn"
    six = _rate([10.0 * k for k in range(1, 7)], 60)
    assert six.state(60.0).status == "fail" and six.rate()[0] == pytest.approx(6.0)
    # the one instruction, orange and red alike: physical, whole on the line, ASCII (stall verdict 26/11 §8.3)
    how = "at the next break: reseat the camera's USB cable at both ends, then try the other cable"
    assert OM.STALL_HOW == how and " - " not in how and how.isascii()
    assert six.state(60.0).detail.endswith(": the picture freezes often - " + how)
    for row in (two.state(30.0), six.state(60.0)):
        line = health_line([row])
        assert line.instruction == how and line.text.endswith(how) and line.text.isascii()
    # the window: 5 minutes later, without a new stall, it is green again
    later = _rate([10.0 * k for k in range(1, 7)], 60 + OM.STALL_WINDOW_S + 2)
    assert later.state(60 + OM.STALL_WINDOW_S + 2).status == "ok"


def test_stall_rate_counts_live_camera_time_only_and_not_checked_otherwise():
    off = _rate([], 30, live=lambda t: False)
    assert off.state(30.0).status == "skip" and off.state(30.0).detail.endswith("the camera is not live")
    # 2 stalls over 2 minutes of live camera spread over 4 minutes: 1 a minute, not 0.5
    s = _rate([30.0, 200.0], 240, live=lambda t: t < 60 or t >= 180)
    assert s.rate()[1] == 2 and s.rate()[2] == pytest.approx(119.0)
    assert s.rate()[0] == pytest.approx(60.0 * 2 / 119.0) and s.state(240.0).status == "ok"
    s2 = _rate([10.0, 20.0, 25.0], 30)
    s2.update(31.0, 0, True)                                       # a new stall clock (a new processor)
    assert s2.rate()[1] == 0
    assert StallRate().state(0.0).status == "skip"


# ---------------------------------------------------------------------------
# Plumbing: the main loop, /status, the GUI band, the watch tool
# ---------------------------------------------------------------------------

def _import_main_loop():
    if importlib.util.find_spec("torch") and importlib.util.find_spec("ultralytics"):
        import runtime.main_loop as ml
        return ml
    import core
    stub = types.ModuleType("core.pipeline")
    stub.ScaledTrack = object
    sys.modules["core.pipeline"] = stub
    try:
        import runtime.main_loop as ml
    finally:
        sys.modules.pop("core.pipeline", None)
        if getattr(core, "pipeline", None) is stub:
            delattr(core, "pipeline")
    return ml


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _fake_app(events, plate_status="ready"):
    from core.foreground import FgBlob, FgFrame

    class _Proc:
        def __init__(self):
            self.last_fg = None
            self.last_emitted = []
            self.plate_status = plate_status
            self.stall = SimpleNamespace(stalls=0)
            self.osc = SimpleNamespace(send_errors=0)
            self.dancer_height = (H, 40, 0.0)
            self._plate_invalid = None
            self.plate_refusal = None
            self._fg_detector = SimpleNamespace(plate=SimpleNamespace(frame_size=(1260, 1824)), stale_since="")

        @staticmethod
        def sent_xy(tr):
            return float(tr.x), float(tr.y)

        def _zone_ok_fn(self, w, h):
            return lambda x, y: True

        def new_frame(self, groups, points):
            self.last_fg = FgFrame(np.zeros((4, 4), np.uint8), 0.0, 0.0, 4,
                                   blobs=[FgBlob(x, y, a, 1.0, (x - 20, y - 60, x + 20, y + 60))
                                          for x, y, a in groups])
            self.last_emitted = [SimpleNamespace(x=x, y=y, bbox=(x - 20, y - 60, 40, h)) for x, y, h in points]

    app = SimpleNamespace(
        last_fps_time=0.0, processor=_Proc(), fps=19.8,
        camera=SimpleNamespace(state=SimpleNamespace(is_open=True, source="ids")),
        recorder=SimpleNamespace(is_playing=False),
        cameras=SimpleNamespace(_camera_reconnecting=False),
        models=SimpleNamespace(_model_loaded=True, _trt_requested=True,
                               model_manager=SimpleNamespace(is_using_tensorrt=lambda: True)),
        settings=SimpleNamespace(person_height_px=150),
        osc_enabled=True, osc_ip="127.0.0.1", osc_port=9000,
        _health=HealthMonitor(gpu_stats_fn=None),
        _readiness_setup_rows=lambda: [CheckResult("ROI", "warn", "drawn on another crop")],
        bus=SimpleNamespace(publish=lambda e: events.append(e)))
    return app


def test_the_main_loop_feeds_a_per_frame_and_publishes_the_line(monkeypatch):
    ml = _import_main_loop()
    clk = _Clock()
    monkeypatch.setattr(ml, "time", SimpleNamespace(monotonic=clk, time=clk, perf_counter=clk))
    events = []
    app = _fake_app(events)
    loop = ml.MainLoop(app)
    proc = app.processor
    for _ in range(int(3.5 * FPS)):                 # 3.5 s: one body, no point
        proc.new_frame([(500.0, 900.0, 4000.0)], [])
        loop._health_line_frame()
        loop._health_line_frame()                   # the same frame twice counts once
        clk.t += 1 / FPS
    loop._health_line_tick(True)
    e = events[-1]
    assert isinstance(e, api.HealthLine) and e.state == "fail"
    assert e.text.startswith("FAIL - points: a body on the wall has had no point for")
    assert e.data["points"]["groups"] == 1 and e.data["source"] == "points" and "t" in e.data
    assert [r["name"] for r in e.data["rows"]] == ["points"]       # a setup row that only warns stays out
    for _ in range(int(1.5 * FPS)):                 # her point comes and stays
        proc.new_frame([(500.0, 900.0, 4000.0)], [(510.0, 880.0, H)])
        loop._health_line_frame()
        clk.t += 1 / FPS
    loop._health_line_tick(True)
    assert events[-1].state == "ok" and events[-1].text.startswith("OK - all ")
    loop._health_line_tick(False)                   # STANDBY: nothing processed, not checked
    assert events[-1].state == "skip" and "STANDBY" in events[-1].text
    proc.plate_status = "none"                      # no plate: not checked, and the empty wall row warns
    loop._health_line_tick(True)
    assert events[-1].state == "warn" and events[-1].data["source"] == "empty wall"
    assert any(r["name"] == "points" and r["status"] == "skip" for r in events[-1].data["rows"])


def test_the_main_loop_line_never_breaks_the_loop(capsys):
    ml = _import_main_loop()
    loop = ml.MainLoop(SimpleNamespace(last_fps_time=0.0, processor=SimpleNamespace(last_fg=object())))
    loop._health_line_frame()
    loop._health_line_tick(True)
    loop._health_line_tick(True)
    out = capsys.readouterr().out
    assert out.count("[Health] signal (a) failed") == 1 and out.count("[Health] the health line failed") == 1


def test_status_serves_the_health_line(tmp_path):
    from services.remote_api import RemoteApi
    runtime, bus = api.RuntimeAPI(), api.EventBus()
    remote = RemoteApi(runtime, bus, token="t" * 32, roots={"projects": tmp_path}, state_fn=lambda: "run",
                       host="127.0.0.1", port=0)
    assert "health" not in remote.status()
    bus.publish(api.HealthLine("fail", "FAIL - points: a body ...", {"state": "fail", "source": "points",
                                                                     "for_s": 4.0}))
    st = remote.status()
    assert st["health"]["state"] == "fail" and st["health"]["line"] == "FAIL - points: a body ..."
    assert st["health"]["for_s"] == 4.0 and "HealthLine" not in st["latest"]
    seq = st["event_seq"]
    for _ in range(5):
        bus.publish(api.HealthLine("ok", "OK - all", {}))
    assert remote.status()["event_seq"] == seq                       # once a second: never in the event ring


def test_the_gui_band_paints_the_lines_state_and_text():
    dpg = pytest.importorskip("dearpygui.dearpygui")
    import gui as gui_mod
    import gui_builder

    class _Gui:
        def __init__(self):
            self.config, self.callbacks, self.expert_mode = {}, {}, False
            self.video_width, self.video_height, self._middle_height, self._icon_font = 640, 480, 600, None

        def __getattr__(self, name):
            if name.startswith("_health"):
                raise AttributeError(name)
            if name.endswith("_theme"):
                return self.__dict__.setdefault("_any_theme", dpg.add_theme())
            if name.startswith(("_on_", "_toggle", "on_")):
                return lambda *a, **k: None
            raise AttributeError(name)

    dpg.create_context()
    try:
        g = _Gui()
        with dpg.texture_registry():
            g.frame_texture_tag = dpg.add_dynamic_texture(4, 4, [0.0] * 64)
        gui_builder.build_ui(g)
        assert dpg.get_value("health_line_text") == gui_builder.HEALTH_LINE_WAITING
        assert dpg.get_item_theme("health_line_window") == g._health_themes["skip"]
        # the band sits in the alerts strip's wrapper (its height is in the layout's measure)
        assert dpg.get_item_parent("health_line_window") in ("alerts_strip_wrapper",
                                                              dpg.get_alias_id("alerts_strip_wrapper"))
        show = gui_mod.WallDanceGUI.show_health_line
        show(g, "fail", "FAIL - points: a body on the wall has had no point for 4 s")
        assert dpg.get_value("health_line_text").startswith("FAIL - points")
        assert dpg.get_item_theme("health_line_window") == g._health_themes["fail"]
        show(g, "ok", "OK - all 7 checks fine")
        assert dpg.get_item_theme("health_line_window") == g._health_themes["ok"]
        show(g, "weird", "?")
        assert dpg.get_item_theme("health_line_window") == g._health_themes["fail"]
        assert set(g._health_themes) == {"ok", "skip", "warn", "fail"}
    finally:
        dpg.destroy_context()


def test_the_adapter_routes_the_event_to_the_band():
    pytest.importorskip("dearpygui.dearpygui")
    from ui.adapter import DpgUiAdapter
    seen = []
    adapter = DpgUiAdapter(api.RuntimeAPI(), api.EventBus())
    adapter.gui = SimpleNamespace(show_health_line=lambda s, t: seen.append((s, t)))
    adapter._on_event(api.HealthLine("warn", "WARN - output: OSC is off", {}))
    assert seen == [("warn", "WARN - output: OSC is off")]
