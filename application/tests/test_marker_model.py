"""MRK-2: the IR-marker model library (core/marker_model.py) -- offset-vote
estimator, unlabelled slot association, R(n, k) table, appearance physics and
the synthetic-marker renderer. Pure numpy, no GPU."""
import itertools
import math

import numpy as np
import pytest

from core import marker_model as mm


def _skeleton(rng, center=(400.0, 300.0), H=200.0):
    """A plausible 17-keypoint skeleton (all conf 0.9) around ``center``."""
    k = np.array(center) + rng.normal(0, H / 4, size=(17, 2))
    return k, np.full(17, 0.9)


def test_kpt_conf_is_the_tracker_definition():
    from core.config import KEYPOINT_CONFIDENCE
    assert mm.KPT_CONF == KEYPOINT_CONFIDENCE


def test_track_centroid_is_conf_weighted_mean_above_threshold():
    k = np.array([[0, 0], [10, 0], [0, 10], [100, 100]], float)
    c = np.array([0.9, 0.5, 0.3, 0.1])            # 0.3 is NOT > 0.3
    C = mm.track_centroid(k, c)
    assert np.allclose(C, np.average(k[:2], axis=0, weights=c[:2]))
    assert mm.track_centroid(k, np.zeros(4)) is None


def test_extremity_points_layout_and_harness():
    rng = np.random.default_rng(0)
    k, c = _skeleton(rng)
    c[10] = 0.2                                   # right wrist occluded
    pts, vis = mm.extremity_points(k, c)
    assert np.allclose(pts, k[[9, 10, 15, 16]])
    assert vis.tolist() == [True, False, True, True]
    pts5, vis5 = mm.extremity_points(k, c, harness=True)
    assert pts5.shape == (5, 2) and np.allclose(pts5[4], k[[11, 12]].mean(0)) and vis5[4]


def test_offset_vote_exact_under_rigid_translation():
    rng = np.random.default_rng(1)
    k, c = _skeleton(rng)
    C = mm.track_centroid(k, c)
    M = k[list(mm.EXT_KPTS)]
    offs = mm.learn_offsets(C, M)
    shift = np.array([37.5, -12.25])
    for n in range(1, 5):
        for S in itertools.combinations(range(4), n):
            S = list(S)
            est = mm.offset_vote(M[S] + shift, offs[S])
            assert np.allclose(est, C + shift)
    assert mm.offset_vote(np.full((4, 2), np.nan), offs) is None


def test_offset_vote_robust_median_rejects_one_bad_slot():
    offs = np.zeros((4, 2))
    pts = np.array([[10, 10], [10.5, 9.5], [9.5, 10.2], [80, -40]], float)   # slot 3 is a glint
    mean = mm.offset_vote(pts, offs)
    med = mm.offset_vote(pts, offs, robust=True)
    assert np.linalg.norm(med - [10, 10]) < 1.0 < np.linalg.norm(mean - [10, 10])
    # NaN rows (slot not seen) are ignored
    pts[1] = np.nan
    assert np.allclose(mm.offset_vote(pts[:3], offs[:3]), [9.75, 10.1])


def test_offset_model_learn_update_and_single_blend():
    C, M = np.array([100.0, 100.0]), np.array([[80, 60], [120, 60], [90, 160], [110, 160]], float)
    om = mm.OffsetModel.learn(C, M, H=200)
    assert np.allclose(om.estimate(M + 5), C + 5)
    only_one = np.full((4, 2), np.nan)
    only_one[0] = M[0] + 10
    hold = C.copy()
    # n = 1 -> 50/50 with the hold position (§3.2)
    assert np.allclose(om.estimate(only_one, hold=hold), 0.5 * (C + 10) + 0.5 * hold)
    om.update(C + 1, M, alpha=0.5)                 # EMA of the offsets
    assert np.allclose(om.offsets, mm.learn_offsets(C, M) + 0.5)
    e = mm.ema_offsets(np.array([[np.nan, np.nan], [1, 1]]), np.array([[2, 2], [np.nan, np.nan]]), 0.5)
    assert np.allclose(e, [[2, 2], [1, 1]])


def test_assign_bruteforce_matches_scipy():
    rng = np.random.default_rng(2)
    for shape in [(3, 3), (4, 2), (2, 5), (5, 5)]:
        c = rng.random(shape)
        r1, k1 = mm.assign(c)
        r2, k2 = mm._assign_bruteforce(c)
        assert math.isclose(c[r1, k1].sum(), c[r2, k2].sum())


def test_associate_points_respects_gate_and_nan():
    slots = np.array([[0, 0], [10, 0], [np.nan, np.nan]], float)
    obs = np.array([[10.5, 0.2], [0.3, -0.1], [50, 50]], float)
    idx = mm.associate_points(slots, obs, gate=2.0)
    assert idx.tolist() == [1, 0, -1]
    assert mm.associate_points(slots, np.zeros((0, 2))).tolist() == [-1, -1, -1]


def test_slot_tracker_follows_shuffled_identical_dots():
    rng = np.random.default_rng(3)
    start = np.array([[0, 0], [100, 0], [0, 200], [100, 200]], float)
    trk = mm.SlotTracker(start)
    pos = start.copy()
    for _ in range(10):
        pos = pos + rng.normal(0, 2, pos.shape) + [3, 1]
        perm = rng.permutation(4)
        vis = trk.step(pos[perm])
        assert vis.all()
    assert np.allclose(trk.pos, pos)              # identities kept despite the shuffles
    # one dot hidden: its slot keeps the last position and is flagged invisible
    last = trk.pos.copy()
    vis = trk.step(pos[[0, 1, 2]])
    assert vis.tolist() == [True, True, True, False] and np.allclose(trk.pos[3], last[3])


def test_similarity_fit_recovers_known_transform():
    rng = np.random.default_rng(4)
    src = rng.random((5, 2)) * 100
    th, s, t = 0.3, 1.2, np.array([5.0, -7.0])
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    dst = (s * (R @ src.T)).T + t
    s2, R2, t2 = mm.similarity_fit(src, dst)
    assert math.isclose(s2, s, rel_tol=1e-9) and np.allclose(R2, R) and np.allclose(t2, t)
    assert np.allclose(mm.apply_similarity(s2, R2, t2, src[0]), dst[0])


@pytest.mark.parametrize("scene", sorted(mm.R_TABLE))
def test_r_table_shape_and_order(scene):
    tab = mm.R_TABLE[scene]
    for key in ("hold", "cvdecay", 1, 2, 3, 4, "hip", "4+hip"):
        assert sorted(tab[key]) == list(mm.R_TABLE_KS)
        for p50, p90 in tab[key].values():
            assert 0 < p50 <= p90
    for k in mm.R_TABLE_KS:                       # more markers -> smaller error
        assert tab[4][k][0] <= tab[2][k][0] <= tab[1][k][0]
    for n in (1, 2, 3, 4):                        # lookups are monotone in k
        vals = [mm.marker_error(n, k, scene=scene) for k in range(1, 41)]
        assert all(b >= a - 1e-12 for a, b in zip(vals, vals[1:]))


def test_r_lookup_values_and_kalman_conversion():
    # table values (aerial, 02 §3.0)
    assert mm.marker_error(4, 5) == 0.063 and mm.marker_error(4, 5, q="p90") == 0.134
    assert mm.marker_error(2, 10, scene="climb") == 0.047
    # interpolation and k < 1
    assert math.isclose(mm.marker_error(4, 3.5), (0.048 + 0.063) / 2)
    assert mm.marker_error(4, 0) == mm.marker_error(4, 1)
    # beyond the table: grows with the 10->20 slope, never below k = 20
    assert mm.marker_error(4, 40) > mm.marker_error(4, 20)
    # small-N dip (aerial hip at k=20 < k=10) is flattened
    assert mm.marker_error(0, 20, harness=True) == mm.marker_error(0, 10, harness=True) == 0.070
    assert mm.marker_error(0, 5) is None and mm.marker_R(0, 5, 175) is None
    # §3.2 worked example: H = 175 px, n = 4, k <= 5 -> sigma ~ 6-9 px
    sig = [mm.marker_sigma(4, k, 175) for k in (1, 5)]
    assert 6.0 <= sig[0] <= sig[1] <= 9.5
    assert math.isclose(mm.marker_R(4, 5, 175), sig[1] ** 2)
    assert math.isclose(mm.marker_sigma(4, 1, 1.0), 0.043 / mm.RAYLEIGH_MEDIAN)
    # harness layouts
    assert mm.layout_key(4, harness=True) == "4+hip" and mm.layout_key(2, harness=True) == "hip"
    assert mm.hold_error(10) == 0.283


def test_physics_helpers():
    assert math.isclose(mm.mm_per_px(20.0), 7.25)
    assert math.isclose(mm.marker_diameter_px(5.0, 204.0), 6.0)
    assert math.isclose(mm.streak_length_px(10.0, 25000.0, 20.0), 5.0)
    assert math.isclose(mm.dwell_fraction(4, 12), 0.25)
    assert math.isclose(mm.streak_level(1000, 4, 12), 250.0)


def _weighted_centroid(img, T):
    ys, xs = np.nonzero(img >= T)
    w = img[ys, xs].astype(float)
    return np.array([(xs * w).sum() / w.sum(), (ys * w).sum() / w.sum()])


def test_render_disc_is_saturated_and_subpixel_centred():
    img = np.full((80, 100), 6, np.uint8)
    bb = mm.render_marker(img, (50.3, 40.7), diameter=6.0, level=2000.0)
    assert bb is not None and img.max() == 255
    assert np.linalg.norm(_weighted_centroid(img, 160) - [50.3, 40.7]) < 0.15
    area = int((img >= 160).sum())
    # pi r^2 .. pi (r+1)^2: at 2000 DN a pixel only 8 % covered still reads 160
    assert math.pi * 9 <= area <= math.pi * 16
    assert img[0, 0] == 6                                   # background untouched


def test_render_streak_follows_dwell_model():
    img = np.zeros((60, 120), np.uint8)
    # d = 4 px, moving 30 px during the exposure, static level 1000 DN
    mm.render_marker(img, (60, 30), diameter=4.0, level=1000.0, velocity=(30, 0))
    centre = img[30, 60]
    assert abs(centre - 1000 * 4 / 30) <= 3                 # centre line ~ d / L of the exposure
    assert centre < 160                                      # an off-axis streak drops below T
    ys, xs = np.nonzero(img > 0)
    assert xs.max() - xs.min() >= 30 and ys.max() - ys.min() <= 6
    bright = np.zeros_like(img)
    mm.render_marker(bright, (60, 30), diameter=4.0, level=8000.0, velocity=(30, 0))
    assert bright[30, 60] == 255                             # on-axis: saturated with margin


def test_render_offset_psf_and_out_of_frame():
    roi = np.zeros((50, 50), np.uint8)
    mm.render_marker(roi, (120.0, 230.0), diameter=5.0, level=3000.0, offset=(100, 200), psf_sigma=0.7)
    assert np.linalg.norm(_weighted_centroid(roi, 100) - [20, 30]) < 0.2
    assert mm.render_marker(roi, (-50, -50), diameter=4, level=500) is None
    boxes = mm.render_markers(roi, [mm.SyntheticMarker(5, 5, 3, 900), mm.SyntheticMarker(500, 5, 3, 900)])
    assert len(boxes) == 1
