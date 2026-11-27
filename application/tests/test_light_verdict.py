"""X2 (#t-054): the light verdict of the empty wall, with one instruction (core/light_verdict.py).

Gate: the 08/10 plates (``jeudi0809_mur``, ``jeudi0809_mur2``) give red; a flat synthetic wall gives green;
a synthetic wall with one hot oval gives red and the instruction points at the oval's side.
"""
import glob
import json
import os
from pathlib import Path

import numpy as np
import pytest

from core.foreground import CleanPlate
from core.light_verdict import (LIGHT_RED_RATIO, light_verdict, opposite_words, plate_light_verdict,
                                where_words)
from core.ops_monitor import check_wall_light
from runtime.roi_state import RoiState


def _projects_dir() -> Path:
    env = os.environ.get("WD_PROJECTS_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parents[2] / "projects"
    return here if (here / "jeudi0809_mur").is_dir() else Path("/data/WallDance/projects")


PROJECTS = _projects_dir()

FACADE = (1296, 1776)                 # 08/10's portrait crop (W, H) ...
FACADE_ROI = (135, 3, 965, 1771)      # ... and its zone
LANDSCAPE = (1920, 1056)              # the façade choice of the rehearsal build (Thomas, 26/11), no ROI


def _wall(frame, roi, fn, ds=4, seed=0):
    """A synthetic plate of ``frame`` (W, H): ``fn(u, v)`` gives the level, u / v from 0 to 1 across the
    zone ``roi`` (None: the frame), plus 1.5 DN of noise."""
    W, H = frame
    w, h = W // ds, H // ds
    x, y, rw, rh = roi if roi else (0, 0, W, H)
    xs = (np.arange(w) * ds + ds / 2 - x) / rw
    ys = (np.arange(h) * ds + ds / 2 - y) / rh
    u, v = np.meshgrid(xs, ys)
    img = fn(u, v) + np.random.default_rng(seed).normal(0.0, 1.5, (h, w))
    return CleanPlate(plate=np.clip(img, 0, 255).astype(np.float32), sigma=0.6, frame_size=(W, H), ds=ds)


def _oval(cu, cv, base=25.0, peak=230.0, su=0.12, sv=0.12):
    return lambda u, v: base + (peak - base) * np.exp(-(((u - cu) / su) ** 2) - (((v - cv) / sv) ** 2))


# ------------------------------------------------------------------ the gate's synthetic clauses

@pytest.mark.parametrize("frame,roi", [(FACADE, FACADE_ROI), (LANDSCAPE, None)])
def test_a_flat_wall_is_green(frame, roi):
    plate = _wall(frame, roi, lambda u, v: 120.0 * (1.0 + 0.1 * (u - 0.5)) + 0 * v)
    v = plate_light_verdict(plate, roi)
    assert v.status == "ok", v.detail
    assert v.ratio is not None and v.ratio < 1.5
    assert not v.instruction and "red above 4x" in v.detail


@pytest.mark.parametrize("frame,roi", [(FACADE, FACADE_ROI), (LANDSCAPE, None)])
@pytest.mark.parametrize("side,cu,cv", [("left", 0.15, 0.5), ("right", 0.85, 0.5),
                                        ("top", 0.5, 0.12), ("bottom", 0.5, 0.88),
                                        ("top left", 0.15, 0.12), ("bottom right", 0.85, 0.88)])
def test_one_hot_oval_is_red_and_the_instruction_points_at_its_side(frame, roi, side, cu, cv):
    plate = _wall(frame, roi, _oval(cu, cv))
    v = plate_light_verdict(plate, roi)
    assert v.status == "fail" and v.red, v.detail
    assert v.ratio > LIGHT_RED_RATIO
    assert v.bright_at == side
    # the instruction names the oval's side and moves the flood off it, toward the opposite side
    assert f"away from the {side}" in v.instruction
    assert f"toward the {opposite_words(side)}" in v.instruction
    assert "IR flood" in v.instruction and "Calibrate" in v.instruction
    assert v.detail.endswith(v.instruction) and v.line().startswith("Wall light: RED - ")


def test_a_spot_in_the_middle_asks_for_a_wider_beam():
    plate = _wall(FACADE, FACADE_ROI, _oval(0.5, 0.45, su=0.2, sv=0.12))
    v = plate_light_verdict(plate, FACADE_ROI)
    assert v.status == "fail" and v.bright_at == "middle"
    assert "spot in the middle" in v.detail
    assert "move the IR flood back from the wall" in v.instruction


def test_a_clipped_oval_gives_a_lower_bound():
    plate = _wall(FACADE, FACADE_ROI, _oval(0.5, 0.45, peak=400.0, su=0.25, sv=0.15))   # clipped at 255
    v = plate_light_verdict(plate, FACADE_ROI)
    assert v.status == "fail" and v.clipped and v.bounded
    assert "at least" in v.detail


# ------------------------------------------------------------------ the other states

def test_a_gentle_gradient_under_4x_is_green():
    plate = _wall(FACADE, FACADE_ROI, lambda u, v: 40.0 + 100.0 * np.clip(v, 0, 1) + 0 * u)
    v = plate_light_verdict(plate, FACADE_ROI)
    assert v.status == "ok" and 2.0 < v.ratio < LIGHT_RED_RATIO, v.detail


def test_a_corner_of_sky_in_the_zone_does_not_make_a_lit_wall_red():
    """08/10's façade ROI holds a corner of night sky (top left): a well-lit wall must not stay red for it."""
    sky = lambda u, v: np.where((u < 0.25) & (v < 0.2), 8.0, 150.0)       # 5 % of the zone at 8 DN
    v = plate_light_verdict(_wall(FACADE, FACADE_ROI, sky), FACADE_ROI)
    assert v.status == "ok", v.detail          # (its darkest single part alone would say ~19x: red)


def test_a_dark_band_is_red_and_named():
    band = lambda u, v: np.where(v > 0.75, 12.0, 150.0) + 0 * u           # the light misses the bottom quarter
    v = plate_light_verdict(_wall(FACADE, FACADE_ROI, band), FACADE_ROI)
    assert v.status == "fail" and v.dark_at.startswith("bottom"), v.detail
    assert "does not reach the bottom" in v.detail and "toward the bottom" in v.instruction


def test_a_dark_zone_says_light_it():
    plate = _wall(FACADE, FACADE_ROI, lambda u, v: 6.0 + 0 * u * v)
    v = plate_light_verdict(plate, FACADE_ROI)
    assert v.status == "fail" and v.ratio is None
    assert "the whole zone is dark" in v.detail and "light the zone with the IR flood" in v.instruction


def test_a_white_even_zone_is_not_green():
    plate = _wall(FACADE, FACADE_ROI, lambda u, v: 300.0 + 0 * u * v)        # all at white
    v = plate_light_verdict(plate, FACADE_ROI)
    assert v.status == "warn" and v.clipped and "at white" in v.detail


def test_no_plate_or_no_zone_is_skipped():
    assert plate_light_verdict(None).status == "skip"
    assert light_verdict(np.full((10, 10), 100.0, np.float32), (0, 0, 4, 4), ds=4).status == "skip"


def test_the_zone_is_clamped_onto_the_plate():
    plate = _wall(FACADE, None, lambda u, v: 100.0 + 0 * u * v)
    assert plate_light_verdict(plate, (1000, 1500, 5000, 5000)).status == "ok"


def test_words():
    assert where_words(0.1, 0.1) == "top left" and where_words(0.5, 0.5) == "middle"
    assert where_words(0.9, 0.5) == "right" and where_words(0.5, 0.9) == "bottom"
    assert opposite_words("top left") == "bottom right" and opposite_words("middle") == "middle"


# ------------------------------------------------------------------ the readiness row

def test_readiness_row():
    flat = _wall(FACADE, FACADE_ROI, lambda u, v: 120.0 + 0 * u * v)
    ok = check_wall_light(plate=flat, roi=FACADE_ROI, plate_status="ready")
    assert ok.name == "wall light" and ok.status == "ok"
    hot = _wall(FACADE, FACADE_ROI, _oval(0.15, 0.5))
    red = check_wall_light(plate=hot, roi=FACADE_ROI, plate_status="off")
    assert red.status == "fail" and "away from the left" in red.detail
    assert check_wall_light(plate=None, roi=FACADE_ROI, plate_status="none").status == "skip"
    other = check_wall_light(plate=hot, roi=FACADE_ROI, plate_status="size")
    assert other.status == "skip" and "another camera crop" in other.detail


# ------------------------------------------------------------------ the end of Calibrate's result card

def test_calibrates_card_ends_with_the_light_line_of_its_own_plate():
    pytest.importorskip("torch")                      # app imports the pipeline
    import time
    from types import SimpleNamespace
    import app as app_mod
    from runtime import api

    hot = _wall(FACADE, FACADE_ROI, _oval(0.15, 0.5))
    hot.created, hot.source = time.strftime("%Y-%m-%dT%H:%M:%S"), "calibrate:proj"
    settings = SimpleNamespace(roi_x=FACADE_ROI[0], roi_y=FACADE_ROI[1], roi_w=FACADE_ROI[2],
                               roi_h=FACADE_ROI[3], roi_enabled=True)
    proc = SimpleNamespace(_fg_detector=SimpleNamespace(plate=hot), plate_status="ready")
    events = []
    bus = api.EventBus()
    bus.subscribe(events.append)
    ui = app_mod._CalibrationUiAdapter(SimpleNamespace(processor=proc, settings=settings, bus=bus))

    def card():
        events.clear()
        ui.show_calibration_result_dialog("Brightness: 80", on_save=None)
        (ev,) = [e for e in events if isinstance(e, api.CalibReportCard)]
        return ev.summary

    s = card()
    assert s.startswith("Brightness: 80\nWall light: RED - ") and "away from the left" in s
    # X2 follow-up (27/11): never a card without its line -- a capture that is not this Calibrate's (no
    # Calibrate holds it here), an old plate, another source, no plate: « no verdict » and what to do
    # (the « being measured » card and its update: tests/test_x2b_card_wall_light.py)
    none = "Brightness: 80\n" + app_mod._CalibrationUiAdapter.WALL_LIGHT_NO_PLATE
    assert none.endswith(" - Calibrate on the empty wall")
    proc.plate_status = "capturing"
    assert card() == none
    proc.plate_status = "ready"
    hot.created = "2026-10-08T21:36:18"               # an old plate: not this Calibrate's
    assert card() == none
    hot.created, hot.source = time.strftime("%Y-%m-%dT%H:%M:%S"), "button:proj"
    assert card() == none
    proc._fg_detector = None
    assert card() == none


# ------------------------------------------------------------------ the 08/10 plates

_CONFIGS_0810 = {"jeudi0809_mur": "jeudi0809_mur_20261008_211350.json",
                 "jeudi0809_mur2": "jeudi0809_mur2_20261008_213208.json"}


def _plates_0810():
    out = []
    for proj, cfg in _CONFIGS_0810.items():
        d = json.loads((PROJECTS / proj / cfg).read_text())
        for p in sorted(glob.glob(str(PROJECTS / proj / "plates" / "*.npz"))):
            plate = CleanPlate.load(p)
            if plate.created.startswith("2026-10-08"):
                out.append((proj, Path(p).name, plate, d))
    return out


@pytest.mark.skipif(not all((PROJECTS / p / c).exists() for p, c in _CONFIGS_0810.items()),
                    reason="the 08/10 projects are not on this machine (set WD_PROJECTS_DIR)")
def test_the_0810_plates_are_red():
    """Each 08/10 plate under its project's 08/10 zone (the 965x1771 façade ROI), mapped onto the plate's
    frame the way the app maps a drawn ROI (RoiState.rect_for_frame)."""
    plates = _plates_0810()
    assert {proj for proj, *_ in plates} == set(_CONFIGS_0810), "a project without an 08/10 plate"
    for proj, name, plate, d in plates:
        rs = RoiState(None, plate.frame_size)
        rs.set_drawn(d["roi_x"], d["roi_y"], d["roi_w"], d["roi_h"], d["roi_source_w"], d["roi_source_h"])
        roi = rs.rect_for_frame(*plate.frame_size)
        v = plate_light_verdict(plate, roi)
        assert v.status == "fail", f"{proj}/{name}: {v.detail}"
        assert v.ratio is not None and v.ratio > LIGHT_RED_RATIO, f"{proj}/{name}: {v.ratio}"
        assert v.instruction and "IR flood" in v.instruction
