"""H3 (#t-026 ④, rehearsal build): readiness at start and after each Calibrate, three new rows, and « skip »
never shown or counted as green.

- The three rows (core/ops_monitor.py): ``check_enhancement`` (on / off / which; red when OFF on a project whose
  Calibrate chose one), ``check_roi`` (the zone and whether it still fits the camera's crop),
  ``check_plate_exposure`` (the plate really loaded -- the project's own or A2's live.npz -- against the camera
  now: X6's exposure / gain rule and the crop, said before RUN).
- « skip » = NOT CHECKED: its own words and colour, above ok in the worst state, and a report with one is never
  READY (``readiness_verdict``: the panel's summary line, the toast, the log block).
- ReadinessAuto + WallDanceApp._readiness_auto_tick: one report at start, one after each Calibrate, none while
  a Calibrate runs, none over a toast on screen (« Previous session crashed »), never a storm; the main loop
  calls it every tick; it never sets RUN/STANDBY (A4: one writer).
- X2's wall light without a plate stays NOT CHECKED (decided here): the empty-wall row already says the one
  thing to do (Calibrate on the empty wall) in orange, so the report is not READY and the technician gets one
  instruction, not three.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.foreground import CleanPlate
from core.ops_monitor import (NOT_CHECKED_COLOR, STATUS_COLORS, STATUS_WORDS, CheckResult, ReadinessAuto,
                              ReadinessReport, check_empty_wall, check_enhancement, check_plate_exposure,
                              check_roi, check_wall_light, readiness_verdict, worst_status)
from runtime.roi_state import RoiState


def _projects_dir() -> Path:
    env = os.environ.get("WD_PROJECTS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parents[2] / "projects"
    return here if (here / "jeudi0809_mur").is_dir() else Path("/data/WallDance/projects")


PROJECTS = _projects_dir()
FACADE = (1296, 1776)                      # 08/10's portrait crop (W, H) and its zone, as drawn
FACADE_ROI = (135, 3, 965, 1771)
LANDSCAPE = (1920, 1056)                   # the rehearsal build's façade choice


def _green(rgb) -> bool:
    """Green to the eye: the green channel well above both others (OK_GREEN is 120, 255, 120)."""
    r, g, b = (float(v) for v in rgb[:3])
    return g > 1.3 * max(r, b)


def _plate(frame=FACADE, **camera):
    W, H = frame
    p = CleanPlate(plate=np.full((H // 4, W // 4), 120.0, np.float32), sigma=0.5, frame_size=(W, H),
                   created="2026-11-26T21:16:00", camera=camera)
    return p


# ------------------------------------------------------------------------------------------------ the enhancement

def test_enhancement_on_as_calibrate_chose_is_ok():
    r = check_enhancement(enabled=True, gamma=0.73, clahe=2.5, calibrated="2026-11-26 21:16")
    assert r.name == "enhancement" and r.status == "ok"
    assert "gamma 0.73 / CLAHE 2.5" in r.detail and "21:16" in r.detail


def test_enhancement_off_on_a_project_that_needs_it_is_red():
    r = check_enhancement(enabled=False, gamma=1.21, clahe=2.5, calibrated="2026-10-08 21:32")
    assert r.status == "fail" and r.detail.startswith("OFF") and "Ctrl+Shift+E" in r.detail
    # lite: gamma only -- a gamma that changes the picture still needs it
    assert check_enhancement(enabled=False, gamma=1.3, clahe=1.0, lite=True, calibrated="x").status == "fail"


def test_enhancement_neutral_or_never_calibrated():
    # Calibrate chose none (neutral: gamma 1, no CLAHE): off changes nothing
    assert check_enhancement(enabled=False, gamma=1.0, clahe=1.0, calibrated="2026-10-07 16:41").status == "ok"
    on_neutral = check_enhancement(enabled=True, gamma=1.0, clahe=0.5, calibrated="")
    assert on_neutral.status == "ok" and "neutral" in on_neutral.detail
    # never chosen by Calibrate on this project: orange, with the instruction
    off = check_enhancement(enabled=False, gamma=1.2, clahe=3.0, calibrated="")
    assert off.status == "warn" and "Calibrate" in off.detail
    on = check_enhancement(enabled=True, gamma=1.2, clahe=3.0, calibrated="")
    assert on.status == "warn" and "Calibrate on the empty wall" in on.detail


def test_enhancement_faded_by_its_brightness_gate_is_orange():
    r = check_enhancement(enabled=True, gamma=2.23, clahe=2.5, force=False, brightness=75.0, threshold=60.0,
                          calibrated="2026-07-01 09:48")
    assert r.status == "warn" and "Force" in r.detail
    assert check_enhancement(enabled=True, gamma=2.23, clahe=2.5, force=False, brightness=40.0,
                             calibrated="2026-07-01 09:48").status == "ok"


@pytest.mark.skipif(not (PROJECTS / "jeudi0809_mur2" / "jeudi0809_mur2_20261008_213208.json").exists(),
                    reason="the 08/10 projects are not on this machine (set WD_PROJECTS_DIR)")
def test_the_0810_project_with_its_enhancement_switched_off_is_red():
    """08/10, mur2 at 21:32: Calibrate chose gamma 1.21 / CLAHE 2.5, the saved project has the enhancement off
    (« one key or one box does it; nothing warned »)."""
    c = json.loads((PROJECTS / "jeudi0809_mur2" / "jeudi0809_mur2_20261008_213208.json").read_text())

    def g(k):
        if k in c:
            return c[k]
        for b in (c.get("profiles") or {}).values():
            if k in b:
                return b[k]
        return (c.get("shared") or {}).get(k)

    st = g("calibration_state")
    when = max((st[k] for k in ("gamma", "clahe") if k in st), key=lambda v: v.get("epoch") or 0)["ts"]
    r = check_enhancement(enabled=bool(g("enhance_enabled")), gamma=float(g("gamma")),
                          clahe=float(g("clahe_clip")), lite=bool(g("enhance_lite")),
                          force=bool(g("enhance_force")), calibrated=when)
    assert r.status == "fail" and "gamma 1.21 / CLAHE 2.5" in r.detail and "2026-10-08 21:32" in r.detail


# ------------------------------------------------------------------------------------------------ the ROI

def test_roi_on_its_own_crop_is_ok():
    r = check_roi(enabled=True, drawn=FACADE_ROI + FACADE, live=FACADE_ROI, frame_size=FACADE)
    assert r.name == "ROI" and r.status == "ok" and "965x1771 at (135, 3)" in r.detail and "1296x1776" in r.detail


def test_roi_off_or_empty():
    off = check_roi(enabled=False, drawn=FACADE_ROI + FACADE, frame_size=FACADE)
    assert off.status == "warn" and "whole 1296x1776 picture" in off.detail
    assert check_roi(enabled=True, drawn=(0, 0, 0, 0, 1296, 1776), frame_size=FACADE).status == "fail"


def test_roi_after_a_crop_change():
    # the 08/10 zone, and the camera switched to the landscape crop: the new crop cuts it -> red
    cut = check_roi(enabled=True, drawn=FACADE_ROI + FACADE, live=RoiState.normalize_rect(*FACADE_ROI, *LANDSCAPE),
                    frame_size=LANDSCAPE)
    assert cut.status == "fail" and "cut to 965x1053" in cut.detail and "1296x1776" in cut.detail
    # a zone the new crop still holds, drawn on another one: it may frame another part of the wall -> orange
    fits = check_roi(enabled=True, drawn=(100, 100, 800, 600) + FACADE, live=(100, 100, 800, 600),
                     frame_size=LANDSCAPE)
    assert fits.status == "warn" and "another part of the wall" in fits.detail


def test_roi_without_a_picture_is_not_checked():
    r = check_roi(enabled=True, drawn=FACADE_ROI + FACADE, live=FACADE_ROI, frame_size=None)
    assert r.status == "skip" and "not checked" in r.detail


# ------------------------------------------------------------------------------------------------ the plate's exposure

def test_plate_of_this_camera_is_ok():
    p = _plate(exposure_us=25000.0, gain_db=30.0, crop=list(FACADE))
    r = check_plate_exposure(plate=p, exposure_us=25000.0, gain_db=30.0, frame_size=FACADE, source="latest.npz")
    assert r.name == "plate exposure" and r.status == "ok"
    assert "25.0 ms / 30.0 dB on 1296x1776" in r.detail and "latest.npz" in r.detail


def test_plate_of_another_exposure_gain_or_crop_is_red_before_run():
    p = _plate(exposure_us=25000.0, gain_db=30.0)
    exp = check_plate_exposure(plate=p, exposure_us=8800.0, gain_db=30.0, frame_size=FACADE)
    assert exp.status == "fail" and "25.0 ms" in exp.detail and "8.8 ms" in exp.detail and "RUN refuses" in exp.detail
    assert check_plate_exposure(plate=p, exposure_us=25000.0, gain_db=24.0, frame_size=FACADE).status == "fail"
    crop = check_plate_exposure(plate=p, exposure_us=25000.0, gain_db=30.0, frame_size=LANDSCAPE)
    assert crop.status == "fail" and "1296x1776" in crop.detail and "1920x1056" in crop.detail
    # X5: changed by hand since: refused at once
    hand = check_plate_exposure(plate=p, exposure_us=25000.0, gain_db=30.0, frame_size=FACADE,
                                invalid="exposure changed by hand - take the empty wall again")
    assert hand.status == "fail" and "by hand" in hand.detail
    # the same rule as RUN's refusal (X6): within 10 % of the exposure and 1 dB is the same camera
    assert check_plate_exposure(plate=p, exposure_us=26500.0, gain_db=30.5, frame_size=FACADE).status == "ok"


def test_plate_from_before_x6_is_orange():
    r = check_plate_exposure(plate=_plate(), exposure_us=25000.0, gain_db=30.0, frame_size=FACADE)
    assert r.status == "warn" and "Calibrate on the empty wall" in r.detail


def test_plate_not_checked_cases():
    p = _plate(exposure_us=25000.0, gain_db=30.0)
    assert check_plate_exposure(plate=None).status == "skip"
    pend = check_plate_exposure(plate=p, pending="live.npz", exposure_us=25000.0, gain_db=30.0, frame_size=FACADE)
    assert pend.status == "skip" and "live.npz" in pend.detail and "not loaded yet" in pend.detail
    unknown = check_plate_exposure(plate=p, exposure_us=None, gain_db=None, frame_size=FACADE)
    assert unknown.status == "skip" and "exposure, gain unknown" in unknown.detail
    assert check_plate_exposure(plate=p, exposure_us=25000.0, gain_db=30.0, frame_size=None).status == "skip"
    assert check_plate_exposure(plate=p, capturing=True).status == "skip"


@pytest.mark.skipif(not (PROJECTS / "jeudi0809_mur" / "plates" / "plate_20261008_200617.npz").exists(),
                    reason="the 08/10 projects are not on this machine")
def test_the_0810_mur_takes_2_to_4_see_their_20_06_plate_red():
    """08/10: the 20:06 plate (Calibrate at 25 ms / 30 dB, stamped as X6's gate does) under takes 2-4 at
    8.8-12 ms -- the row is red before RUN, with RUN's own words."""
    plate = CleanPlate.load(PROJECTS / "jeudi0809_mur" / "plates" / "plate_20261008_200617.npz")
    plate.camera = {"exposure_us": 25000.0, "gain_db": 30.0}
    for take in ("slot_2_20261008_200907", "slot_3_20261008_201808", "slot_4_20261008_205648"):
        cam = json.loads((PROJECTS / "jeudi0809_mur" / "recordings" / f"{take}.avi.meta").read_text())["camera"]
        r = check_plate_exposure(plate=plate, exposure_us=cam["nodes"]["ExposureTime"], gain_db=cam.get("gain_db"),
                                 frame_size=tuple(plate.frame_size))
        assert r.status == "fail" and "25.0 ms" in r.detail, take


# ------------------------------------------------------------------------------------------------ skip is never green

def test_skip_ranks_above_ok_and_is_never_ready():
    assert worst_status(["ok", "skip", "ok"]) == "skip"
    assert worst_status(["skip", "warn"]) == "warn" and worst_status(["warn", "fail", "skip"]) == "fail"
    assert worst_status([]) == "skip"
    state, line = readiness_verdict([("camera", "ok"), ("dancer size", "skip"), ("gpu", "skip")])
    assert state == "skip" and line.startswith("NOT ALL CHECKED") and "2 not checked (dancer size, gpu)" in line
    assert not line.startswith("READY")
    assert readiness_verdict([("camera", "ok"), ("disk", "ok")]) == ("ok", "READY - 2 ok")
    state, line = readiness_verdict([("camera", "fail"), ("ROI", "warn"), ("gpu", "skip"), ("disk", "ok")])
    assert state == "fail" and line == "NOT READY - 1 ok, 1 fail (camera), 1 warn (ROI), 1 not checked (gpu)"
    assert STATUS_WORDS["skip"] == "NOT CHECKED" and not _green(NOT_CHECKED_COLOR) and _green(STATUS_COLORS["ok"])


def test_a_report_of_ok_and_skip_is_not_green_in_the_toast_nor_the_log():
    report = ReadinessReport([CheckResult("camera", "ok", "ids @ 20 FPS"), CheckResult("osc", "skip", "disabled"),
                              CheckResult("gpu", "skip", "no NVIDIA GPU stats available")])
    assert report.worst == "skip"
    msg, color = report.toast_summary()
    assert msg.startswith("Go-Live check: NOT ALL CHECKED") and "2 not checked" in msg
    assert color == NOT_CHECKED_COLOR and not _green(color)
    footer = report.console_block().splitlines()[-1]
    assert "2 not checked" in footer and "NOT ALL CHECKED" in footer
    green = ReadinessReport([CheckResult("camera", "ok", "ids @ 20 FPS")])
    assert green.toast_summary() == ("Go-Live check: READY - 1 ok", STATUS_COLORS["ok"])


def test_no_plate_gives_one_instruction_not_three():
    """X2's wall light (and the plate's exposure) without a plate stay NOT CHECKED: the empty-wall row is the
    orange one, with the instruction; the report is not READY."""
    rows = [check_empty_wall(status="none", detail="empty wall: none - Calibrate on the empty wall"),
            check_wall_light(plate=None, roi=FACADE_ROI, plate_status="none"),
            check_plate_exposure(plate=None)]
    assert [r.status for r in rows] == ["warn", "skip", "skip"]
    assert sum("Calibrate on the empty wall" in r.detail for r in rows if r.status in ("warn", "fail")) == 1
    state, line = readiness_verdict(rows)
    assert state == "warn" and line == ("CHECK - 1 warn (empty wall), 2 not checked (wall light, plate exposure)")


# ------------------------------------------------------------------------------------------------ when it runs

def _ticks(auto, t0, t1, dt=1 / 30, **state):
    """Poll ``auto`` from t0 to t1 every dt with a fixed state -> [(t, why)] of the reports made due."""
    out, t = [], t0
    while t < t1:
        why = auto.poll(t, **state)
        if why:
            out.append((round(t, 2), why))
        t += dt
    return out


READY = dict(ready=True, busy=False, calibrating=False, flowing=True)


def test_start_report_once_the_project_is_loaded_and_the_camera_had_its_chance():
    auto = ReadinessAuto()
    assert _ticks(auto, 0, 30, **dict(READY, ready=False)) == []           # picker / model loading: nothing
    got = _ticks(auto, 30, 40, **dict(READY, flowing=False))               # ready, the camera still opening
    assert got == []
    got = _ticks(auto, 40, 600, **READY)                                    # frames flow from t=40
    assert len(got) == 1 and got[0][1] == "start" and 43.0 <= got[0][0] <= 43.1


def test_start_report_without_a_camera_after_its_grace():
    auto = ReadinessAuto()
    got = _ticks(auto, 0, 120, **dict(READY, flowing=False))
    assert len(got) == 1 and got[0][1] == "start" and 15.0 <= got[0][0] <= 15.1


def test_after_each_calibrate_once_and_never_while_it_runs():
    auto = ReadinessAuto()
    assert len(_ticks(auto, 0, 10, **READY)) == 1                          # the start report
    assert _ticks(auto, 10, 35, **dict(READY, calibrating=True)) == []    # Calibrate: 25 s, no report
    auto.calibrate_ended()                                                  # its card ... and its flag goes down
    assert _ticks(auto, 35, 37, **dict(READY, busy=True)) == []           # the plate capture (2 s)
    got = _ticks(auto, 37, 300, **READY)
    assert len(got) == 1 and got[0][1] == "after Calibrate" and 38.0 <= got[0][0] <= 38.1
    # a cancelled Calibrate (no card, the flag goes down) is a Calibrate's end too
    assert _ticks(auto, 300, 305, **dict(READY, calibrating=True)) == []
    assert [w for _t, w in _ticks(auto, 305, 400, **READY)] == ["after Calibrate"]


def test_a_calibrate_before_the_start_report_makes_one_report():
    auto = ReadinessAuto()
    assert _ticks(auto, 0, 1, **READY) == []
    assert _ticks(auto, 1, 30, **dict(READY, calibrating=True)) == []
    auto.calibrate_ended()
    got = _ticks(auto, 30, 200, **READY)
    assert got == [(got[0][0], "start, after Calibrate")]


def test_the_dancers_pass_and_a_model_reload_hold_it():
    auto = ReadinessAuto()
    _ticks(auto, 0, 10, **READY)
    auto.calibrate_ended()                                                  # the dancers' Apply card ...
    assert _ticks(auto, 10, 20, **dict(READY, ready=False)) == []         # ... reloads the model (imgsz)
    assert [w for _t, w in _ticks(auto, 20, 60, **READY)] == ["after Calibrate"]


def test_a_toast_on_screen_holds_it_at_most_20_s():
    auto = ReadinessAuto()
    got = _ticks(auto, 0, 15, **dict(READY, quiet=False))                  # « Previous session crashed »: 15 s
    got += _ticks(auto, 15, 60, **READY)
    assert len(got) == 1 and 15.0 <= got[0][0] <= 15.1
    auto2 = ReadinessAuto()
    got = _ticks(auto2, 0, 60, **dict(READY, quiet=False))                 # toasts that never end: 3 s + 20 s
    assert len(got) == 1 and 23.0 <= got[0][0] <= 23.1


def test_no_storm_over_an_evening():
    """An hour at 30 ticks a second: start, three Calibrates (one cancelled), a camera drop -> four reports."""
    auto, t, n = ReadinessAuto(), 0.0, []
    plan = [(60, {}), (25, dict(calibrating=True)), (2, dict(busy=True)), (600, {}),
            (40, dict(flowing=False)), (600, {}), (5, dict(calibrating=True)), (300, {}),
            (25, dict(calibrating=True)), (2, dict(busy=True)), (1938, {})]
    for secs, over in plan:
        if over.get("busy"):
            auto.calibrate_ended()
        n += _ticks(auto, t, t + secs, **dict(READY, **over))
        t += secs
    assert [w for _t, w in n] == ["start", "after Calibrate", "after Calibrate", "after Calibrate"]
    assert len(auto.reports) == 4


# ------------------------------------------------------------------------------------------------ the app's wiring

def _app_mod():
    pytest.importorskip("torch")                      # app imports the pipeline
    import app
    return app


def _stub_app(bus=None):
    """The state ``WallDanceApp._readiness_auto_tick`` reads, on a stub; its report is counted, not built."""
    from runtime import api
    A = _app_mod().WallDanceApp
    reports = []
    app = SimpleNamespace(
        bus=bus or api.EventBus(), system_state=api.SystemState.RUN,
        calibration=SimpleNamespace(_calibrating=False, _calibrating2=False),
        models=SimpleNamespace(_model_loaded=False, _model_loading=False),
        processor=SimpleNamespace(_plate_capture=None),
        configs=SimpleNamespace(_pending_project_switch=None),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=False)),
        cameras=SimpleNamespace(_camera_reconnecting=False),
        recorder=SimpleNamespace(is_playing=False),
        reports=reports)
    for name in ("_readiness_auto", "_readiness_calibrate_ended", "_readiness_note_toast"):
        setattr(app, name, getattr(A, name).__get__(app))
    app._cb_check_readiness = lambda why="": reports.append(why)
    app.tick = lambda now: A._readiness_auto_tick(app, now)
    app.bus.ui_ready = True
    app.bus.subscribe(app._readiness_note_toast)
    return app


def _run(app, t0, t1, dt=1 / 30):
    t = t0
    while t < t1:
        app.tick(t)
        t += dt


def test_the_app_reports_at_start_and_after_calibrate_by_itself():
    app = _stub_app()
    _run(app, 0, 20)
    assert app.reports == []                                                # no model yet (the picker)
    app.models._model_loaded = True
    app.camera.state.is_open = True
    _run(app, 20, 30)
    assert app.reports == ["start"]
    app.calibration._calibrating = True                                     # CALIBRATE pressed
    _run(app, 30, 55)
    assert app.reports == ["start"]                                         # none while it runs
    app.calibration._calibrating = False                                    # its result card, its plate capture
    from runtime import api
    card_events = []
    app.bus.subscribe(card_events.append)
    _app_mod()._CalibrationUiAdapter(app).show_calibration_result_dialog("Brightness: 80", on_save=None)
    app.processor._plate_capture = object()
    _run(app, 55, 57)
    app.processor._plate_capture = None
    _run(app, 57, 600)
    assert app.reports == ["start", "after Calibrate"]                     # once, the card and the flag together
    assert any(isinstance(e, api.CalibReportCard) for e in card_events)
    assert app.system_state == api.SystemState.RUN                         # A4: never set here


def test_the_start_report_waits_for_the_crash_toast(monkeypatch):
    from runtime import api
    app = _stub_app()
    app.models._model_loaded = True
    app.camera.state.is_open = True
    clock = {"t": 0.0}
    monkeypatch.setattr(_app_mod().time, "monotonic", lambda: clock["t"])   # the toast's clock
    app.bus.publish(api.Toast("Previous session crashed (12:03)", 15.0, (255, 80, 80)))   # MainLoop._startup
    seen, t = [], 0.0
    while t < 40:
        clock["t"] = t
        n = len(app.reports)
        app.tick(t)
        if len(app.reports) > n:
            seen.append(t)
        t += 1 / 30
    assert app.reports == ["start"] and 15.0 <= seen[0] <= 15.1           # not at 3 s, over the crash toast


def test_a_failing_read_never_reaches_the_loop(capsys):
    app = _stub_app()
    del app.models                                                          # anything missing
    for t in range(5):
        app.tick(float(t))
    assert app.reports == [] and capsys.readouterr().out.count("[Readiness] automatic check off") == 1


def test_the_main_loop_calls_it_every_tick():
    _app_mod()
    from runtime.main_loop import MainLoop
    calls = []
    app = SimpleNamespace(last_fps_time=0.0, api=SimpleNamespace(drain=lambda: calls.append("drain")),
                          _readiness_auto_tick=lambda: calls.append("readiness"))
    loop = MainLoop(app)
    loop._ops_heartbeat = lambda: None
    loop._tick_remote = lambda: None
    loop._live_plate_minute = lambda: None
    loop._tick_pumps = lambda: True                                         # a pump consumed the tick
    loop._tick()
    loop._tick()
    assert calls == ["drain", "readiness", "drain", "readiness"]
    bare = MainLoop(SimpleNamespace(last_fps_time=0.0, api=SimpleNamespace(drain=lambda: None)))
    bare._ops_heartbeat = bare._tick_remote = bare._live_plate_minute = lambda: None
    bare._tick_pumps = lambda: True
    bare._tick()                                                            # an app without it: nothing


def test_the_automatic_report_says_why_and_publishes_rows_and_toast(capsys):
    from runtime import api
    A = _app_mod().WallDanceApp
    events = []
    bus = api.EventBus()
    bus.subscribe(events.append)
    report = ReadinessReport([CheckResult("camera", "ok", "ids @ 20 FPS"),
                              CheckResult("dancer size", "skip", "not measured")])
    app = SimpleNamespace(bus=bus, _build_readiness_report=lambda: report,
                          configs=SimpleNamespace(_current_project="rehearsal", _active_profile="show"))
    A._cb_check_readiness(app, why="start")
    out = capsys.readouterr().out
    assert "Go-Live check (start, project=rehearsal, profile=show)" in out and "NOT ALL CHECKED" in out
    (rows,) = [e for e in events if isinstance(e, api.ReadinessResult)]
    assert [r["status"] for r in rows.rows] == ["ok", "skip"]
    (toast,) = [e for e in events if isinstance(e, api.Toast)]
    assert toast.message.startswith("Go-Live check: NOT ALL CHECKED") and tuple(toast.color) == NOT_CHECKED_COLOR


def _rows_app(**over):
    """A stub with what ``WallDanceApp._readiness_setup_rows`` reads."""
    settings = SimpleNamespace(enhance_enabled=True, enhance_force=True, enhance_lite=False, brightness_threshold=60,
                               roi_enabled=True, roi_x=FACADE_ROI[0], roi_y=FACADE_ROI[1], roi_w=FACADE_ROI[2],
                               roi_h=FACADE_ROI[3], fg_plate_path="")
    roi_state = RoiState(settings, FACADE)
    roi_state.set_drawn(*FACADE_ROI, *FACADE)
    app = SimpleNamespace(
        settings=settings, timing={},
        processor=SimpleNamespace(_fg_detector=None, _fg_loaded_path=None, _plate_invalid=None, _plate_capture=None),
        camera=SimpleNamespace(state=SimpleNamespace(is_open=True)), recorder=SimpleNamespace(is_playing=False),
        cameras=SimpleNamespace(_camera_reconnecting=False, exposure_gain_now=lambda: (25000.0, 30.0)),
        roi=SimpleNamespace(state=roi_state),
        calibration=SimpleNamespace(calibration_state={
            "gamma": {"source": "aim", "ts": "2026-11-26 21:16", "epoch": 2.0},
            "clahe": {"source": "aim", "ts": "2026-11-26 21:15", "epoch": 1.0}}),
        enhancer=SimpleNamespace(gamma=0.73, clahe_clip=2.5))
    for k, v in over.items():
        setattr(app, k, v)
    return app


def test_the_rows_read_the_plate_really_loaded(tmp_path):
    """A2: at start the project's plate, or plates/live.npz resumed with it, is what the row compares -- and
    a plate file named but not loaded yet is not checked, even while the previous project's plate is still
    installed."""
    A = _app_mod().WallDanceApp
    live = _plate(exposure_us=25000.0, gain_db=30.0, crop=list(FACADE))
    live.live = live.plate.copy()
    path = str(tmp_path / "plates" / "live.npz")
    app = _rows_app()
    app.settings.fg_plate_path = path
    app.processor._fg_detector = SimpleNamespace(plate=live)
    app.processor._fg_loaded_path = path                                     # _resume_live_plate installed it
    rows = {r.name: r for r in A._readiness_setup_rows(app)}
    assert list(rows) == ["enhancement", "ROI", "plate exposure"]
    assert rows["enhancement"].status == "ok" and "21:16" in rows["enhancement"].detail
    assert rows["ROI"].status == "ok"
    assert rows["plate exposure"].status == "ok" and "live.npz" in rows["plate exposure"].detail
    app.cameras.exposure_gain_now = lambda: (8800.0, 30.0)                  # the camera moved since
    assert {r.name: r for r in A._readiness_setup_rows(app)}["plate exposure"].status == "fail"
    app.settings.fg_plate_path = str(tmp_path / "other" / "latest.npz")      # a project switch, not loaded yet
    row = {r.name: r for r in A._readiness_setup_rows(app)}["plate exposure"]
    assert row.status == "skip" and "latest.npz" in row.detail
    app.camera.state.is_open = False                                         # no picture: crop and ROI unknown
    rows = {r.name: r for r in A._readiness_setup_rows(app)}
    assert rows["ROI"].status == "skip"


def test_a_row_that_cannot_be_read_is_not_checked_not_a_crash():
    A = _app_mod().WallDanceApp
    app = _rows_app(enhancer=None)                                           # enhancement unreadable
    rows = {r.name: r for r in A._readiness_setup_rows(app)}
    assert rows["enhancement"].status == "skip" and rows["enhancement"].detail.startswith("not checked")
    assert rows["ROI"].status == "ok"
    app = _rows_app()
    app.settings.enhance_enabled = False                                     # 08/10: switched off by a key
    assert {r.name: r for r in A._readiness_setup_rows(app)}["enhancement"].status == "fail"


# ------------------------------------------------------------------------------------------------ headless GUI

def test_the_panel_draws_skip_neutral_and_never_calls_it_ready():
    dpg = pytest.importorskip("dearpygui.dearpygui")
    from runtime import api
    from ui.adapter import DpgUiAdapter
    rt, bus = api.RuntimeAPI(), api.EventBus()
    DpgUiAdapter(rt, bus).create_gui({"camera_type": "IDS_PEAK"})
    try:
        def shown():
            items = dpg.get_item_children("readiness_rows_container", 1)
            rows = [(dpg.get_value(i), [255 * c for c in dpg.get_item_configuration(i)["color"]]) for i in items]
            conf = dpg.get_item_configuration("readiness_summary_text")
            return rows, dpg.get_value("readiness_summary_text"), [255 * c for c in conf["color"]], conf["show"]

        bus.publish(api.ReadinessResult([
            {"name": "camera", "status": "ok", "detail": "ids @ 20.0 FPS"},
            {"name": "dancer size", "status": "skip", "detail": "not measured"},
            {"name": "wall light", "status": "skip", "detail": "no empty-wall snapshot"},
            {"name": "gpu", "status": "ok", "detail": "61 C"}]))
        rows, summary, color, show = shown()
        assert len(rows) == 4                                                # one line per row, as before
        skip_rows = [(t, c) for t, c in rows if t.startswith("[NOT CHECKED]")]
        assert len(skip_rows) == 2 and all(not _green(c) for _t, c in skip_rows)
        assert all(_green(c) for t, c in rows if t.startswith("[OK]"))
        assert show and summary.startswith("NOT ALL CHECKED") and not summary.startswith("READY")
        assert "2 not checked (dancer size, wall light)" in summary and not _green(color)

        bus.publish(api.ReadinessResult([{"name": "camera", "status": "ok", "detail": "ids @ 20.0 FPS"}]))
        rows, summary, color, show = shown()
        assert summary == "READY - 1 ok" and _green(color)

        # the toast of a report with skips is not green either
        report = ReadinessReport([CheckResult("camera", "ok", "x"), CheckResult("gpu", "skip", "no stats")])
        msg, col = report.toast_summary()
        bus.publish(api.Toast(msg, 5.0, col))
        assert dpg.get_value("toast_text").startswith("Go-Live check: NOT ALL CHECKED")
        assert not _green([255 * c for c in dpg.get_item_configuration("toast_text")["color"]])
    finally:
        dpg.destroy_context()
