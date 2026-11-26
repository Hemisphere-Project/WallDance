"""H2 follow-up (#t-040): a black picture says « no light » -- one cause, one instruction.

26/11, the show laptop: the room dark, the camera's picture black (« Bright: 0 », the reference watch « the camera's
picture is too dark to compare (0 DN) »), and three things fired at once: the wall-light row « the light is a spot
in the middle of the zone, at least 50x brighter than the left: move the IR flood back from the wall » (it judged
the STORED 14/10 snapshot, not the picture), the Alerts line « The light is <= 0.25x the empty-wall snapshot »
and the health line « enhancement: OFF ».  The cause was that no IR light reached the camera.

Clauses:
  * the floor: the zone's brightest 1 % under 20 DN, the reference watch's « too dark to compare » (one floor);
  * a black frame, a near-black frame with sensor noise and hot pixels, a lens cap half off in that dark room:
    the « no light » verdict with its instruction, never the spot / move-the-flood one -- on the picture itself
    and in the wall-light row over a snapshot that is a spot (the 14/10 plate of the laptop when it is here);
  * the health line names that one cause with that instruction, the Alerts line says the same, and neither the
    « <= 0.25x » nor the spot is said;
  * a dim but usable picture keeps the snapshot's verdict; every 08/10 plate is above the floor (its verdict
    cannot move: tmp_analysis/r2-1126/gates/h2b/run_gate.sh re-runs X2's plate probe and compares).
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from core import ops_monitor as OM
from core import reference_watch as RW
from core.foreground import CleanPlate
from core.light_verdict import (LIGHT_NO_LIGHT_DN, NO_LIGHT_HOW, light_verdict, picture_light_verdict,
                                plate_light_verdict, zone_level)
from core.ops_monitor import (CauseResult, HealthMonitor, check_enhancement, check_wall_light, health_alert_rows,
                              health_camera_row, health_engine_row, health_frames_row, health_line,
                              health_output_row, health_plate_row)

FACADE = (1296, 1776)                 # 08/10's portrait crop (W, H) and its zone
FACADE_ROI = (135, 3, 965, 1771)
MUR2 = (1260, 1824)                   # the laptop's crop on 26/11 (ROI 1260x1824 = the whole frame)
LANDSCAPE = (1920, 1056)
MOVE_WORDS = ("spot", "move the IR flood", "aim the IR flood", "widen its beam", "brighter than")


def _projects() -> Path:
    env = os.environ.get("WD_PROJECTS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parents[2] / "projects"
    return here if (here / "jeudi0809_mur").is_dir() else Path("/data/WallDance/projects")


# ------------------------------------------------------------------ synthetic pictures (raw frames, full size)

def _black(size):
    W, H = size
    return np.zeros((H, W), np.uint8)


def _near_black(size, seed=1):
    """The sensor in the dark: 2 DN of black level with 1.5 DN of noise, and a few hot pixels (255, 128)."""
    W, H = size
    rng = np.random.default_rng(seed)
    img = np.clip(rng.normal(2.0, 1.5, (H, W)), 0, 255)
    ys, xs = rng.integers(0, H, 300), rng.integers(0, W, 300)
    img[ys, xs] = 255.0
    img[ys[:100] // 2, xs[:100] // 2] = 128.0
    return img.round().astype(np.uint8)


def _cap_half_off(size, seed=2, ambient=12.0):
    """A lens cap half off in tonight's dark room: the capped half at the sensor's black, an out-of-focus edge (a
    soft ramp over a quarter of the frame), and through the open half the unlit wall at the room's few DN, with
    the sensor's noise and hot pixels.  The flood off, what comes through is a few grey levels: there is no shape."""
    W, H = size
    u = (np.arange(W) + 0.5) / W
    t = np.clip((u - 0.40) / 0.25, 0.0, 1.0)
    t = t * t * (3 - 2 * t)                                   # the cap's edge, out of focus
    v = (np.arange(H) + 0.5) / H
    wall = ambient * (0.85 + 0.15 * np.cos((v[:, None] - 0.5) * 3.0))   # the wall a little brighter mid-height
    img = wall * t[None, :] + _near_black(size, seed).astype(np.float64) - 2.0
    return np.clip(img, 0, 255).round().astype(np.uint8)


def _dim_usable(size, seed=3):
    """A dim but usable wall (mur25m 06/10's level: its brightest 1 % near 26 DN): an IR spot at 30 DN over an
    8 DN wall, with noise -- light enough for the foreground, so its own verdict stands."""
    W, H = size
    u, v = np.meshgrid((np.arange(W) + 0.5) / W, (np.arange(H) + 0.5) / H)
    img = 8.0 + 22.0 * np.exp(-(((u - 0.5) / 0.22) ** 2) - (((v - 0.5) / 0.22) ** 2))
    img = img + np.random.default_rng(seed).normal(0.0, 1.5, img.shape)
    return np.clip(img, 0, 255).round().astype(np.uint8)


def _spot_plate(size, roi, ds=4):
    """A snapshot that is a spot in the middle (as 08/10's and the laptop's 14/10 plates): its verdict is red,
    « move the IR flood back from the wall ».  Taken under light, long before the picture went black."""
    W, H = size
    x, y, rw, rh = roi if roi else (0, 0, W, H)
    xs = (np.arange(W // ds) * ds + ds / 2 - x) / rw
    ys = (np.arange(H // ds) * ds + ds / 2 - y) / rh
    u, v = np.meshgrid(xs, ys)
    img = 25.0 + 205.0 * np.exp(-(((u - 0.5) / 0.12) ** 2) - (((v - 0.5) / 0.12) ** 2))
    img = img + np.random.default_rng(0).normal(0.0, 1.5, img.shape)
    return CleanPlate(plate=np.clip(img, 0, 255).astype(np.float32), sigma=0.6, frame_size=(W, H), ds=ds)


DARK = [("black", _black), ("near-black, noise and hot pixels", _near_black), ("lens cap half off", _cap_half_off)]
GEOMETRIES = [(FACADE, FACADE_ROI), (MUR2, None), (LANDSCAPE, None)]


def _as_plate(frame, ds=4) -> CleanPlate:
    """A raw frame as Calibrate would keep it: area-averaged onto the plate's grid."""
    H, W = frame.shape
    g = frame[:H // ds * ds, :W // ds * ds].reshape(H // ds, ds, W // ds, ds).mean(axis=(1, 3))
    return CleanPlate(plate=g.astype(np.float32), sigma=0.6, frame_size=(W, H), ds=ds)


# ------------------------------------------------------------------ the floor

def test_the_floor_is_the_reference_watchs():
    assert LIGHT_NO_LIGHT_DN == RW.DARK_DN == 20.0


@pytest.mark.parametrize("name,make", DARK + [("dim but usable", _dim_usable)])
def test_the_level_is_the_reference_watchs_statistic(name, make):
    """zone_level reads what the reference watch reads (« too dark to compare »): the zone's 99th percentile on
    the plate's grid, every other pixel -- within a grey level (the grid's origin and 8-bit rounding)."""
    frame = make(FACADE)
    ds = 4
    F = RW._down(frame, ds)
    x0, y0, x1, y1 = RW._zone(FACADE_ROI, ds, F.shape)
    assert abs(zone_level(frame, FACADE_ROI, 1, ds) - RW._p99(F, x0, y0, x1, y1)) <= 1.0, name


# ------------------------------------------------------------------ the three dark pictures

@pytest.mark.parametrize("size,roi", GEOMETRIES)
@pytest.mark.parametrize("name,make", DARK)
def test_a_dark_picture_says_no_light_not_move_the_flood(name, make, size, roi):
    frame = make(size)
    level = zone_level(frame, roi, 1, 4)
    assert level < LIGHT_NO_LIGHT_DN, (name, level)
    # the picture itself, as the wall-light row reads it
    v = picture_light_verdict(frame, roi)
    assert v is not None and v.status == "fail" and v.instruction == NO_LIGHT_HOW, (name, v)
    assert "no IR light reaches the camera" in v.detail
    assert not any(w in v.detail for w in MOVE_WORDS), v.detail
    # the same picture kept as a snapshot (a Calibrate in the dark): no light either, then Calibrate again
    p = plate_light_verdict(_as_plate(frame), roi)
    assert p.status == "fail" and p.ratio is None and NO_LIGHT_HOW in p.instruction, (name, p.detail)
    assert not any(w in p.detail for w in MOVE_WORDS), p.detail
    # the wall-light row over a lit snapshot that is a spot: the picture wins, one instruction
    spot = _spot_plate(size, roi)
    assert "spot in the middle" in check_wall_light(plate=spot, roi=roi, plate_status="ready").detail
    row = check_wall_light(plate=spot, roi=roi, plate_status="ready", frame=frame)
    assert row.name == "wall light" and row.status == "fail", row
    assert NO_LIGHT_HOW in row.detail and not any(w in row.detail for w in MOVE_WORDS), row.detail
    assert check_wall_light(plate=None, roi=roi, plate_status="none", frame=frame).status == "fail"
    assert check_wall_light(plate=spot, roi=roi, plate_status="size", frame=frame).detail == row.detail


def test_the_laptops_14_10_snapshot_over_a_black_picture():
    """The exact 26/11 case when the plate is here: jeudi0809_mur2's latest snapshot (14/10 13:30) is « a spot in
    the middle, at least 50x »; over the black picture the row says no light."""
    p = _projects() / "jeudi0809_mur2" / "plates" / "latest.npz"
    if not p.is_file():
        pytest.skip(f"no {p}")
    plate = CleanPlate.load(str(p))
    roi = (0, 0, 1260, 1824)
    before = check_wall_light(plate=plate, roi=roi, plate_status="ready")
    assert before.status == "fail" and "spot in the middle" in before.detail, before.detail
    W, H = plate.frame_size
    for make in (_black, _near_black, _cap_half_off):
        row = check_wall_light(plate=plate, roi=roi, plate_status="ready", frame=make((W, H)))
        assert NO_LIGHT_HOW in row.detail and not any(w in row.detail for w in MOVE_WORDS), row.detail


# ------------------------------------------------------------------ light enough: nothing changes

@pytest.mark.parametrize("size,roi", GEOMETRIES)
def test_a_dim_but_usable_picture_keeps_the_snapshots_verdict(size, roi):
    frame = _dim_usable(size)
    assert zone_level(frame, roi, 1, 4) >= LIGHT_NO_LIGHT_DN
    assert picture_light_verdict(frame, roi) is None
    spot = _spot_plate(size, roi)
    assert check_wall_light(plate=spot, roi=roi, plate_status="ready", frame=frame) == \
        check_wall_light(plate=spot, roi=roi, plate_status="ready")
    v = plate_light_verdict(_as_plate(frame), roi)            # its own light, judged as before: a spot, red
    assert v.ratio is not None and "no IR light" not in v.detail, v.detail
    lit = (np.full((size[1], size[0]), 120, np.uint8))
    assert check_wall_light(plate=spot, roi=roi, plate_status="ready", frame=lit) == \
        check_wall_light(plate=spot, roi=roi, plate_status="ready")


def test_every_0810_plate_is_above_the_floor():
    """The 08/10 plates X2's gate judges: their brightest 1 % is far above 20 DN, so « no light » never takes
    their verdict (the gate runner also re-runs X2's probe and compares the numbers)."""
    from runtime.roi_state import RoiState
    import json
    root = _projects()
    seen = 0
    for proj in ("jeudi0809_mur", "jeudi0809_mur2"):
        d = root / proj
        if not d.is_dir():
            continue
        cfgs = sorted(c for c in d.glob(f"{proj}_20261008_*.json"))
        cfg = json.load(open(cfgs[-1])) if cfgs else {}
        for p in sorted((d / "plates").glob("*.npz")):
            plate = CleanPlate.load(str(p))
            if not str(plate.created).startswith("2026-10-08"):
                continue
            fw, fh = plate.frame_size
            roi = None
            if cfg.get("roi_enabled") and cfg.get("roi_w"):
                r = (cfg["roi_x"], cfg["roi_y"], cfg["roi_w"], cfg["roi_h"])
                roi = r if (cfg.get("roi_source_w"), cfg.get("roi_source_h")) == (fw, fh) else \
                    RoiState.normalize_rect(*r, fw, fh)
            level = zone_level(plate.plate, roi, plate.ds, plate.ds)
            assert level >= 5 * LIGHT_NO_LIGHT_DN, (p.name, level)
            assert "no IR light" not in plate_light_verdict(plate, roi).detail
            seen += 1
    if not seen:
        pytest.skip(f"no 08/10 plates under {root}")
    assert seen >= 11


# ------------------------------------------------------------------ one cause on the health line and the Alerts line

def _ok_rows():
    return [health_camera_row(is_open=True, reconnecting=False, source="ids", fps=19.8),
            health_output_row(enabled=True, target="10.0.0.5:7000"),
            health_plate_row(status="ready"),
            health_frames_row(fail_streak=0),
            health_engine_row(trt_requested=True, trt_active=True)]


def _tick(mon, t, ratio, level, ref="10-14 13:30"):
    return mon.tick(t, fps=19.9, n_tracked=0, in_run=True, model_ready=True, camera_open=True,
                    camera_reconnecting=False, playback_active=False, light_ratio=ratio, light_ref=ref,
                    picture_dn=level)


@pytest.mark.parametrize("name,make", DARK)
def test_the_health_line_and_the_alerts_line_say_one_cause(name, make):
    frame = make(MUR2)
    level = zone_level(frame, None, 1, 4)
    mon = HealthMonitor(gpu_stats_fn=None)
    alerts = _tick(mon, 100.0, 0.0, level)                    # the 26/11 case: <= 0.25x AND black
    light = [a for a in alerts if a.kind == "light_changed"]
    assert len(light) == 1, alerts
    msg = light[0].message
    assert NO_LIGHT_HOW in msg and "0.25x" not in msg and msg.startswith("No IR light reaches the camera")
    assert not any(w in msg for w in MOVE_WORDS)
    assert _tick(mon, 101.0, 0.0, level) == []                # one alert for the episode
    enh = check_enhancement(enabled=False, gamma=1.21, clahe=2.5, calibrated="2026-10-14 10:01")
    assert enh.status == "fail"
    rows = _ok_rows() + [enh] + health_alert_rows(mon, 101.0)
    line = health_line(rows)
    assert line.state == "fail" and line.source == "light", line.text
    assert line.instruction == NO_LIGHT_HOW
    shown = line.text.split("   (+")[0]
    assert shown == "FAIL - light: " + msg[0].lower() + msg[1:], (shown, msg)     # the same words, both lines
    assert "0.25x" not in line.text and not any(w in line.text for w in MOVE_WORDS), line.text
    assert [r.name for r in rows if r.name == "light"] == ["light"]                 # one light row, the cause
    assert isinstance([r for r in rows if r.name == "light"][0], CauseResult)


def test_no_light_ends_or_turns_into_the_ratio_and_survives_a_new_snapshot():
    mon = HealthMonitor(gpu_stats_fn=None)
    assert _tick(mon, 0.0, 0.0, 0.0)[0].message.startswith("No IR light")
    assert mon.pop_cleared() == []
    # a new snapshot while still dark (a Calibrate in the dark): the entry stays, no second alert
    assert _tick(mon, 1.0, None, 0.0, ref="11-26 19:02") == [] and mon.pop_cleared() == []
    # the flood comes back but the light is not the snapshot's: the same entry now says the ratio
    a = _tick(mon, 2.0, 0.3, 60.0, ref="11-26 19:02")
    assert len(a) == 1 and a[0].message.startswith("The light is") and mon.pop_cleared() == []
    rows = health_alert_rows(mon, 2.0)
    assert [(r.name, r.status) for r in rows] == [("light", "warn")]
    # dark again, then light back in the band: the entry goes
    assert _tick(mon, 3.0, 0.3, 0.0, ref="11-26 19:02")[0].message.startswith("No IR light")
    assert _tick(mon, 4.0, 1.0, 80.0, ref="11-26 19:02") == [] and mon.pop_cleared() == ["light_changed"]
    assert health_alert_rows(mon, 4.0) == []
    # no measure (the camera down, a recording): the dark entry goes, the camera row speaks
    assert _tick(mon, 5.0, None, 0.0)[0].message.startswith("No IR light")
    assert _tick(mon, 6.0, None, None) == [] and mon.pop_cleared() == ["light_changed"]


def test_without_a_picture_level_nothing_changes():
    """``picture_dn`` absent (an older caller): the light-ratio alert behaves exactly as before."""
    mon = HealthMonitor(gpu_stats_fn=None)
    a = mon.tick(0.0, fps=19.9, n_tracked=0, in_run=True, model_ready=True, camera_open=True,
                 camera_reconnecting=False, playback_active=False, light_ratio=0.1, light_ref="10-14 13:30")
    assert len(a) == 1 and a[0].message.startswith("The light is <= 0.25x")
    assert [(r.name, r.status) for r in health_alert_rows(mon, 0.0)] == [("light", "warn")]


def test_the_main_loop_samples_the_live_picture_and_feeds_the_line():
    """MainLoop._picture_level: the live camera's zone level at 1 Hz, a 10 s median once 5 samples are in (as the
    light ratio); None with a recording playing, the camera down or Calibrate running.  Then the tick and the
    line: « no light », one cause."""
    from types import SimpleNamespace
    from test_h2_health_line import _fake_app, _import_main_loop
    from runtime import api
    ml = _import_main_loop()
    events = []
    app = _fake_app(events)
    app.processor.settings = SimpleNamespace(roi_enabled=True, roi_x=0, roi_y=0, roi_w=1260, roi_h=1824)
    app.processor._fg_detector.plate.ds = 4
    app.calibration = SimpleNamespace(_calibrating=False)
    app._last_raw_frame = _near_black(MUR2)
    loop = ml.MainLoop(app)
    loop._latest_raw_frame = lambda: app._last_raw_frame
    got = [loop._picture_level(float(t)) for t in range(6)]
    assert got[:4] == [None] * 4 and got[4] is not None and got[5] < LIGHT_NO_LIGHT_DN, got
    app.recorder.is_playing = True                           # a recording: not the camera's light
    assert loop._picture_level(6.0) is None
    app.recorder.is_playing = False
    app.calibration._calibrating = True                      # Calibrate at work
    assert loop._picture_level(7.0) is None
    app.calibration._calibrating = False
    app._last_raw_frame = _dim_usable(MUR2)
    lit = [loop._picture_level(8.0 + t) for t in range(5)]
    assert lit[-1] >= LIGHT_NO_LIGHT_DN, lit
    # the black picture through the tick to the line
    app._last_raw_frame = _black(MUR2)
    for t in range(20, 26):
        level = loop._picture_level(float(t))
    assert level == 0.0
    alerts = app._health.tick(26.0, fps=19.9, n_tracked=0, in_run=True, model_ready=True, camera_open=True,
                              camera_reconnecting=False, playback_active=False, light_ratio=0.0,
                              light_ref="10-14 13:30", picture_dn=level)
    assert [a.kind for a in alerts] == ["light_changed"] and NO_LIGHT_HOW in alerts[0].message
    loop._health_line_tick(True)
    e = [x for x in events if isinstance(x, api.HealthLine)][-1]
    assert e.state == "fail" and e.data["source"] == "light" and e.data["instruction"] == NO_LIGHT_HOW, e.text
