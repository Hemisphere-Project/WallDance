"""IR-belt detector (core/belt_detector.py): synthetic frames (bands, eyes,
glints, saturated bodies, gain-28-dB noise) plus a few real-frame checks on the
2026-10-05 hangar takes, skipped when the recordings are absent."""
import glob
import json
import math
import os
import time

import cv2
import numpy as np
import pytest

from core.belt_detector import BeltBlob, BeltDetector, BeltParams, StaticMap, static_map_from_frames

# Sensor noise measured on the 2026-10-05 takes (gain 27.8 dB, temporal diff):
# sigma = 1.23 DN at 4.5 DN and 1.73 DN at 12.8 DN -> sigma^2 ~ 0.18 * DN + 0.7.
NOISE_A, NOISE_R2 = 0.18, 0.7
FULL = (1528, 1488)                       # (h, w) of the IDS show frame


def _noisy(img: np.ndarray, rng) -> np.ndarray:
    img = np.asarray(img, np.float64)
    sig = np.sqrt(NOISE_A * np.clip(img, 0, None) + NOISE_R2)
    return np.clip(np.rint(img + rng.normal(0.0, 1.0, img.shape) * sig), 0, 255).astype(np.uint8)


def _scene(shape=(600, 800), wall=12.0) -> np.ndarray:
    return np.full(shape, float(wall))


def _body(img, x, y_waist, H, dn):
    """A crude dancer: torso + hips + legs + head, waist at (x, y_waist)."""
    tw = int(0.26 * H)
    cv2.rectangle(img, (int(x - tw / 2), int(y_waist - 0.30 * H)), (int(x + tw / 2), int(y_waist + 0.12 * H)), dn, -1)
    lw = max(2, int(0.08 * H))
    for dx in (-0.07 * H, 0.07 * H):
        cv2.rectangle(img, (int(x + dx - lw / 2), int(y_waist + 0.1 * H)), (int(x + dx + lw / 2), int(y_waist + 0.5 * H)), dn, -1)
    cv2.circle(img, (int(x), int(y_waist - 0.38 * H)), max(2, int(0.065 * H)), dn, -1)


def _band(img, cx, cy, w, h, dn, angle=0.0, blur=0.7):
    """A soft-edged rotated band (blurred like the lens/motion)."""
    layer = np.zeros(img.shape, np.float64)
    box = cv2.boxPoints(((float(cx), float(cy)), (float(w), float(h)), float(angle)))
    cv2.fillPoly(layer, [np.round(box).astype(np.int32)], 1.0)
    if blur > 0:
        layer = cv2.GaussianBlur(layer, (0, 0), blur)
    img[:] = img * (1 - layer) + dn * layer


def _spot(img, x, y, r, dn):
    cv2.circle(img, (int(x), int(y)), int(r), float(dn), -1)


def _det(**kw) -> BeltDetector:
    return BeltDetector(BeltParams(**kw))


def _near(blobs, x, y, tol=4.0):
    return [b for b in blobs if abs(b.cx - x) <= tol and abs(b.cy - y) <= tol]


# ---------------------------------------------------------------------------
# global mode: bands
# ---------------------------------------------------------------------------

def test_band_on_dark_body_is_found_with_geometry():
    rng = np.random.default_rng(1)
    img = _scene()
    _body(img, 400, 300, 160, 20)
    _band(img, 400, 300, 32, 6, 185)
    blobs = _det().detect(_noisy(img, rng))
    assert len(blobs) == 1
    b = blobs[0]
    assert b.reason is None and b.pieces == 1
    assert abs(b.cx - 400) < 1.0 and abs(b.cy - 300) < 1.0
    assert 26 <= b.w <= 36 and 4 <= b.h <= 9 and abs(b.angle) < 5
    assert b.peak >= 170 and b.bg < 30 and b.contrast > 4
    assert b.score > 0.8 and b.sat_frac == 0


def test_noise_and_hot_pixels_give_nothing():
    rng = np.random.default_rng(2)
    img = _scene(wall=15)
    g = _noisy(img, rng)
    ys, xs = rng.integers(0, g.shape[0], 30), rng.integers(0, g.shape[1], 30)
    g[ys, xs] = 255                                       # isolated hot pixels
    det = _det()
    assert det.detect(g) == []


def test_dark_frame_early_out_is_cheap():
    g = np.full(FULL, 8, np.uint8)
    det = _det()
    det.detect(g)
    t = time.perf_counter()
    for _ in range(20):
        assert det.detect(g) == []
    assert (time.perf_counter() - t) / 20 < 0.01


def test_absolute_floor_and_ratio_to_background():
    rng = np.random.default_rng(3)
    # below the floor: invisible; above it with a lower floor: found
    img = _scene()
    _body(img, 400, 300, 160, 12)
    _band(img, 400, 300, 32, 6, 50)
    g = _noisy(img, rng)
    assert _det().detect(g) == []
    assert len(_near(_det(floor_dn=35, min_delta_dn=20).detect(g), 400, 300)) == 1
    # a band only 1.5x a bright (close, lit) body is texture, not a retroreflector
    img = _scene()
    _body(img, 400, 300, 300, 120)
    _band(img, 400, 300, 60, 10, 180)
    assert _near(_det().detect(_noisy(img, rng)), 400, 300, 8) == []
    # 2.5x the body passes
    img = _scene()
    _body(img, 400, 300, 300, 95)
    _band(img, 400, 300, 60, 10, 240)
    assert len(_near(_det().detect(_noisy(img, rng)), 400, 300, 3)) == 1


def test_turned_or_hidden_belt_is_tolerated_but_scored_lower():
    rng = np.random.default_rng(4)
    img = _scene()
    _body(img, 250, 300, 160, 20)
    _band(img, 250, 300, 34, 6, 190)                     # front view
    _body(img, 550, 300, 160, 20)
    _band(img, 550, 300, 9, 6, 190)                      # side view / mostly hidden
    blobs = _det().detect(_noisy(img, rng))
    full, short = _near(blobs, 250, 300), _near(blobs, 550, 300)
    assert len(full) == 1 and len(short) == 1
    assert short[0].score < full[0].score


def test_vertical_band_found_with_lower_score():
    rng = np.random.default_rng(5)
    img = _scene()
    _body(img, 250, 300, 160, 20)
    _band(img, 250, 300, 34, 6, 190, angle=0)
    _body(img, 550, 300, 160, 20)
    _band(img, 550, 300, 34, 6, 190, angle=90)           # dancer sideways on the wall
    blobs = _det().detect(_noisy(img, rng))
    h, v = _near(blobs, 250, 300), _near(blobs, 550, 300)
    assert len(h) == 1 and len(v) == 1
    assert abs(abs(v[0].angle) - 90) < 5
    assert v[0].score < h[0].score


def test_eyes_pair_is_rejected():
    rng = np.random.default_rng(6)
    img = _scene()
    _body(img, 400, 380, 300, 30)
    for dx in (-11, 11):                                 # glasses glowing on axis
        _spot(img, 400 + dx, 266, 3, 255)
    blobs = _det().detect(_noisy(img, rng), return_all=True)
    eyes = [b for b in blobs if b.reason == "eyes"]
    assert len(eyes) == 2
    assert [b for b in blobs if b.reason is None] == []


def test_belt_split_by_an_arm_is_merged():
    rng = np.random.default_rng(7)
    img = _scene()
    _body(img, 400, 300, 200, 20)
    _band(img, 400, 300, 60, 8, 190)
    cv2.rectangle(img, (395, 270), (406, 340), 22, -1)   # forearm across the belt
    blobs = _det().detect(_noisy(img, rng))
    assert len(blobs) == 1
    b = blobs[0]
    assert b.pieces == 2 and abs(b.cx - 400) < 2 and abs(b.cy - 300) < 1.5
    assert b.w > 45


def test_band_width_hint_separates_split_belt_from_eyes():
    """Two compact pieces: with a wide expected belt they are a split belt;
    with a much wider expected belt their span is eyes-sized."""
    rng = np.random.default_rng(8)
    img = _scene()
    _body(img, 400, 300, 200, 20)
    for dx in (-17, 17):
        _band(img, 400 + dx, 300, 12, 8, 200)
    g = _noisy(img, rng)
    det = _det()
    r = det.detect_near(g, [("a", 400, 300, 30, 50)])
    assert r["a"] is not None and r["a"].pieces == 2 and abs(r["a"].cx - 400) < 2
    r = det.detect_near(g, [("a", 400, 300, 30, 160)], return_all=True)
    assert r["a"] is None
    assert any(b.reason == "eyes" for b in r["_all"])


def test_saturated_close_body_is_rejected():
    rng = np.random.default_rng(9)
    img = _scene()
    cv2.rectangle(img, (300, 150), (520, 560), 255, -1)  # blown-out torso, close to the camera
    cv2.rectangle(img, (180, 200), (300, 214), 255, -1)  # saturated arm out of it
    blobs = _det().detect(_noisy(img, rng), return_all=True)
    assert [b for b in blobs if b.reason is None] == []
    assert any(b.reason in ("saturated_body", "too_big") for b in blobs)


def test_roi_and_bgr_input():
    rng = np.random.default_rng(10)
    img = _scene()
    _body(img, 200, 300, 160, 20)
    _band(img, 200, 300, 32, 6, 190)
    _body(img, 600, 300, 160, 20)
    _band(img, 600, 300, 32, 6, 190)
    g = _noisy(img, rng)
    det = _det()
    only_right = det.detect(g, roi=(400, 0, 400, 600))
    assert len(only_right) == 1 and abs(only_right[0].cx - 600) < 1
    bgr = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    assert len(det.detect(bgr)) == 2


def test_blob_to_dict_is_json_serialisable():
    rng = np.random.default_rng(11)
    img = _scene()
    _body(img, 400, 300, 160, 20)
    _band(img, 400, 300, 32, 6, 190)
    d = _det().detect(_noisy(img, rng))[0].to_dict()
    s = json.dumps(d)
    assert "_m" not in d and d["reason"] is None and len(d["bbox"]) == 4 and s


# ---------------------------------------------------------------------------
# static map
# ---------------------------------------------------------------------------

def _glint_scene(rng, belt=True):
    img = _scene()
    _spot(img, 120, 520, 4, 255)                      # floor-lamp flare / fixed reflection
    _band(img, 700, 100, 30, 5, 200)                  # a bright horizontal fixture (static)
    if belt:
        _body(img, 400, 300, 160, 20)
        _band(img, 400, 300, 32, 6, 190)
    return _noisy(img, rng)


def test_static_map_from_empty_stage_rejects_fixed_reflections():
    rng = np.random.default_rng(12)
    empty = [_glint_scene(rng, belt=False) for _ in range(6)]
    sm = static_map_from_frames(empty, min_frac=0.5)
    assert sm.contains(700, 100) and sm.contains(120, 520) and not sm.contains(400, 300)
    det = BeltDetector(static=sm)
    g = _glint_scene(rng)
    acc = det.detect(g)
    assert len(acc) == 1 and abs(acc[0].cx - 400) < 1
    assert BeltDetector().detect(g) and len(BeltDetector().detect(g)) >= 2   # without the map: FPs
    allb = det.detect(g, return_all=True)
    assert any(b.reason == "static" for b in allb) or det.last["rejected"].get("static", 0) > 0


def test_static_map_persistence_protects_tracked_dancers(tmp_path):
    rng = np.random.default_rng(13)
    det = BeltDetector()
    sm = StaticMap(_scene().shape)
    for _ in range(150):
        g = _glint_scene(rng)            # dancer holds still on the wall for 150 frames
        sm.update(det.detect(g, return_all=True), protect=[(400, 300, 60)], alpha=0.05, on=0.6)
    assert sm.contains(700, 100)         # the fixture became static
    assert not sm.contains(400, 300)     # the protected dancer did not
    p = tmp_path / "static.npz"
    sm.save(p)
    sm2 = StaticMap.load(p)
    assert np.array_equal(sm2.cells, sm.cells) and sm2.frames == sm.frames


# ---------------------------------------------------------------------------
# gated mode
# ---------------------------------------------------------------------------

def _duo(rng, x1=500, x2=560, wall_shape=FULL):
    img = _scene(wall_shape, wall=14)
    for x in (x1, x2):
        _body(img, x, 800, 150, 20)
        _band(img, x, 800, 26, 5, 180)
    return _noisy(img, rng)


def test_gated_finds_each_belt_and_none_when_dark():
    rng = np.random.default_rng(14)
    g = _duo(rng, 400, 900)
    det = _det()
    r = det.detect_near(g, [("A", 403, 806, 25, 26), ("B", 895, 797, 25, 26), ("C", 1300, 400, 25, 26)])
    assert r["A"] is not None and abs(r["A"].cx - 400) < 1 and r["A"].key == "A"
    assert r["B"] is not None and abs(r["B"].cx - 900) < 1
    assert r["C"] is None
    assert r["A"].dist is not None and r["A"].dist < 8


def test_gated_one_belt_answers_one_prediction_only():
    rng = np.random.default_rng(15)
    img = _scene(FULL, wall=14)
    _body(img, 700, 800, 150, 20)
    _band(img, 700, 800, 26, 5, 180)                 # one belt visible (the other dancer hidden)
    g = _noisy(img, rng)
    r = _det().detect_near(g, [("near", 702, 801, 30, 26), ("far", 715, 805, 30, 26)])
    got = [k for k, b in r.items() if b is not None]
    assert got == ["near"]


def test_gated_crossing_duo_keeps_both():
    rng = np.random.default_rng(16)
    g = _duo(rng, 680, 724)                          # belts 44 px apart (crossing)
    r = _det().detect_near(g, [("L", 684, 800, 20, 26), ("R", 720, 800, 20, 26)])
    assert r["L"] is not None and r["R"] is not None
    assert abs(r["L"].cx - 680) < 2 and abs(r["R"].cx - 724) < 2


def test_gated_big_window_is_downscaled_but_consistent():
    rng = np.random.default_rng(17)
    img = _scene(FULL, wall=14)
    _body(img, 700, 800, 700, 25)
    _band(img, 700, 800, 150, 24, 200)
    g = _noisy(img, rng)
    det = _det()
    r = det.detect_near(g, [("A", 705, 790, 110, 150)])
    assert det.last.get("downscaled", 0) >= 1
    assert r["A"] is not None and abs(r["A"].cx - 700) < 4 and abs(r["A"].cy - 800) < 4
    assert 120 < r["A"].w < 180


def test_cost_budget_synthetic_show_frame():
    """Production-like frame (25-m wall, two dancers): gated < 0.5 ms for 2
    predictions and global <= 3 ms are the targets; the asserts are looser
    (shared CI boxes), the real numbers are printed and reported."""
    rng = np.random.default_rng(18)
    img = _scene(FULL, wall=14)
    for x in (500, 1000):
        _body(img, x, 800, 150, 20)
        _band(img, x, 800, 26, 5, 180)
    for (x, y) in ((200, 1450), (1300, 1460)):
        _spot(img, x, y, 6, 255)                      # floor-lamp flares
    g = _noisy(img, rng)
    old = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        det = _det()
        preds = [("A", 503, 803, 25, 26), ("B", 996, 797, 25, 26)]
        det.detect_near(g, preds)
        det.detect(g)
        n = 30
        t = time.perf_counter()
        for _ in range(n):
            det.detect_near(g, preds)
        gated = (time.perf_counter() - t) / n * 1000
        t = time.perf_counter()
        for _ in range(n):
            det.detect(g)
        glob_ms = (time.perf_counter() - t) / n * 1000
    finally:
        cv2.setNumThreads(old)
    print(f"\n[belt cost] gated 2 preds {gated:.3f} ms, global {glob_ms:.3f} ms (1 cv2 thread)")
    assert gated < 2.0
    assert glob_ms < 12.0


# ---------------------------------------------------------------------------
# real frames (2026-10-05 hangar, on-axis light): skipped without the takes
# ---------------------------------------------------------------------------

_REC = os.path.join(os.path.dirname(__file__), "..", "..", "projects", "4_TANGO_HANGAR-whitebg3", "recordings")


def _frame(slot: int, f: int) -> np.ndarray:
    paths = sorted(glob.glob(os.path.join(_REC, f"slot_{slot}_20261005_*.avi")))
    if not paths:
        pytest.skip("2026-10-05 hangar takes not present")
    cap = cv2.VideoCapture(paths[0])
    cap.set(cv2.CAP_PROP_POS_FRAMES, f)
    ok, fr = cap.read()
    cap.release()
    if not ok:
        pytest.skip(f"cannot decode slot {slot} frame {f}")
    return fr[:, :, 0].copy()


def test_real_slot5_mid_distance_belt_found():
    g = _frame(5, 405)                    # belt ~64x24 px bbox on a lit shirt (~100 DN)
    blobs = _det().detect(g)
    hit = _near(blobs, 452, 869, 8)
    assert hit, [b.to_dict() for b in blobs]
    assert 25 <= hit[0].w <= 70 and abs(hit[0].angle) < 20 and hit[0].peak >= 240


def test_real_slot5_far_belt_found_gated():
    g = _frame(5, 534)                    # far belt ~34x12 px, peak ~144 on a ~30 DN body
    r = _det().detect_near(g, [("d", 865, 845, 30, 40)])
    assert r["d"] is not None and abs(r["d"].cx - 869) < 6 and abs(r["d"].cy - 840) < 6


def test_real_slot6_glasses_are_rejected():
    g = _frame(6, 198)                    # glasses glowing as two saturated spots, no belt visible
    det = _det()
    blobs = det.detect(g, return_all=True)
    assert not [b for b in blobs if b.reason is None and math.hypot(b.cx - 1098, b.cy - 667) < 60]
    assert det.last["rejected"].get("eyes", 0) >= 2


def test_real_slot5_blown_out_torso_gives_no_belt():
    g = _frame(5, 300)                    # dancer close to the camera, white shirt saturated
    blobs = _det().detect(g)
    assert not [b for b in blobs if 825 <= b.cx <= 1100 and 700 <= b.cy <= 1025]
