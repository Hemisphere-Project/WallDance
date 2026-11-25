"""M1 (Plan Cordée rev 2): the presence truth of 08/10 and its scorer.

Fast tests on synthetic timelines, a load test of the 13 stored truths, and -- when the counter-analysis
replays are on this machine -- the gate: the repo's scorer reproduces the counter-analysis summary.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import truth_score as ts  # noqa: E402

COUNTER = Path(os.environ.get("WD_COUNTER_0810", "/data/WallDance/tmp_analysis/counter-0810"))


def _truth(rows, frames, h=100.0, size=(1296, 1776)):
    """rows: (frame, dancer, x, y, state, box)."""
    f = np.array([r[0] for r in rows])
    d = np.array([r[1] for r in rows])
    x = np.array([r[2] for r in rows], float)
    y = np.array([r[3] for r in rows], float)
    st = np.array([r[4] for r in rows])
    box = np.array([r[5] for r in rows], float)
    return ts.Truth("synthetic", frames, size, h, f, d, x, y, st, box)


def _box(x, y, conf, nk=17, w=40, h=100):
    return [x - w / 2, y - h / 2, x + w / 2, y + h / 2, conf, nk]


def test_on_rule_box_grown_and_radius():
    box = [500, 500, 540, 600]
    assert ts.on(520, 550, (520, 550), box, 100)
    assert ts.on(540 + 34, 550, (520, 550), box, 100)          # inside the box grown by 0.35 H
    assert not ts.on(540 + 36, 450, (520, 550), box, 100)      # outside both
    assert ts.on(520, 550 + 74, (520, 900), [0, 0, 1, 1], 100) is False
    assert ts.on(520, 900 - 74, (520, 900), [0, 0, 1, 1], 100)  # within 0.75 H of the centroid


def test_assign_one_point_per_dancer_highest_score():
    dz = [((500, 500), [480, 450, 520, 550])]
    m = ts.assign([(505, 500, 0.2), (500, 505, 0.9)], dz, 100)
    assert m == {0: 1}


def test_score_replay_detector_emit_and_false_boxes():
    rows = [(0, 0, 500, 500, 0, [480, 450, 520, 550]), (0, 1, 800, 900, 0, [780, 850, 820, 950]),
            (1, 0, 500, 505, 0, [480, 455, 520, 555]), (1, 1, 800, 905, 0, [780, 855, 820, 955])]
    T = _truth(rows, 2)
    replay = [
        {"frame": 0, "low": [_box(500, 500, 0.6), _box(800, 900, 0.10), _box(600, 1500, 0.5)],
         "emitted": {"tracks": [{"centroid": [500, 500], "state": "live"}]}},
        {"frame": 1, "low": [_box(500, 505, 0.6), _box(800, 905, 0.5)],
         "emitted": {"tracks": [{"centroid": [500, 505], "state": "live"}, {"centroid": [800, 905], "state": "coasting"},
                                {"centroid": [600, 300], "state": "live"}]}},
    ]
    Z = ts.score_replay(T, replay, conf=0.15)
    assert Z["det"].tolist() == [True, False, True, True]          # the 0.10 box is under the shipped confidence
    assert Z["emit"].tolist() == [True, False, True, True]
    assert Z["emit_state"].tolist() == [1, 0, 1, 4]
    assert Z["f_n_emit_off"].tolist() == [0, 1]                    # the point at (600, 300) is on nobody
    assert len(Z["fp"]) == 1 and Z["fp"][0][0] == 0                # the box at (600, 1500) on the wall, on nobody
    r = ts.take_summary(T, Z)
    assert r["det"]["wall"] == 0.75 and r["replay"]["emit_on"] == 0.75 and r["fp_boxes_per_frame"] == 0.5


def test_contact_point_counts_for_both():
    box = [480, 450, 560, 560]
    rows = [(0, 0, 510, 500, 1, box), (0, 1, 530, 505, 1, box)]
    T = _truth(rows, 1)
    Z = ts.score_replay(T, [{"frame": 0, "low": [_box(520, 505, 0.7)],
                             "emitted": {"tracks": [{"centroid": [520, 503], "state": "live"}]}}], conf=0.15)
    assert Z["emit"].tolist() == [True, True]      # one point on the shared group counts for both
    assert Z["det"].sum() == 1                     # one box credits one dancer
    assert Z["pair_boxes"].tolist() == [1, 1]


def test_truth_roundtrip(tmp_path):
    rows = [(0, 0, 500.3, 500.7, 0, [480, 450, 520, 550]), (1, 0, 501.1, 499.9, 2, [480, 450, 520, 550])]
    T = _truth(rows, 2)
    p = tmp_path / "t.npz"
    T.save(p)
    U = ts.Truth.load(p)
    assert U.frames == 2 and U.size == (1296, 1776)
    assert U.at(0)[0][1] == (500.3, 500.7, 0)     # tenths of a pixel come back exactly
    assert U.at(1)[0][1][2] == 2 and U.at(2) == []


def test_compare_tolerance():
    ref = {"t": {"det": {"wall": 0.50, "n": {"wall": 1000}}, "start": "20:00:00"}}
    assert ts.compare({"t": {"det": {"wall": 0.509, "n": {"wall": 1005}}, "start": "20:00:00"}}, ref) == []
    assert len(ts.compare({"t": {"det": {"wall": 0.52, "n": {"wall": 1000}}, "start": "20:00:00"}}, ref)) == 1


@pytest.mark.parametrize("stem", ts.ORDER)
def test_stored_truths_load(stem):
    T = ts.load_truth(stem)
    assert T.stem == stem and T.frames > 0 and T.size in ts.WALL
    assert T.meta.get("start") and T.h_take > 50
    if len(T.f):
        assert T.f.max() < T.frames and set(np.unique(T.st)) <= {0, 1, 2, 3}


@pytest.mark.skipif(not (COUNTER / "out").is_dir(), reason="the counter-analysis replays are not on this machine")
def test_gate_reproduces_counter_analysis(tmp_path):
    """M1's gate: the counter-analysis tables to within 1 point, from the existing replay outputs."""
    S = ts.summarise(ts.TRUTH_DIR, COUNTER / "out", "ship", COUNTER / "sessions")
    ref = json.loads((COUNTER / "score" / "summary.json").read_text())
    assert ts.compare(S, ref) == []
