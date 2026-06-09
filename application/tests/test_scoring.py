"""Unit tests for the field-priority scoring objective (TUNING.md Phase A2).

Pure stdlib -- no torch/cv2, no GPU, no recordings -- so this runs in the normal
`pytest` suite (unlike the opt-in GPU replay regression).  These lock down the
*objective itself*: a search is only as trustworthy as the function it optimises.
"""
import json
from pathlib import Path

import pytest

import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scoring  # noqa: E402


def _const(n=1, frames=20, warmup=5, start=1000, fps=20.0):
    return {
        "name": "t", "project": "p", "slot": 0,
        "start": start, "frames": frames, "warmup": warmup, "fps": fps,
        "expected_count": n,
    }


def _tl(reported, ids=None, start_frame=0):
    """Build a timeline from a list of per-frame reported counts."""
    rows = []
    for i, rep in enumerate(reported):
        row = {"frame": start_frame + i, "reported": rep}
        if ids is not None:
            row["ids"] = ids[i]
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# expected_at / max_expected
# --------------------------------------------------------------------------- #
def test_expected_at_constant():
    m = _const(n=2)
    assert scoring.expected_at(m, 0) == 2
    assert scoring.expected_at(m, 999) == 2
    assert scoring.max_expected(m) == 2


def test_expected_at_ranges():
    m = _const()
    m["expected_count"] = [
        {"from": 0, "to": 9, "n": 1},
        {"from": 10, "to": 19, "n": 2},
        {"default": 0},
    ]
    assert scoring.expected_at(m, 5) == 1
    assert scoring.expected_at(m, 10) == 2
    assert scoring.expected_at(m, 19) == 2
    assert scoring.expected_at(m, 25) == 0   # falls through to default
    assert scoring.max_expected(m) == 2


# --------------------------------------------------------------------------- #
# perfect / drop / ghost
# --------------------------------------------------------------------------- #
def test_perfect_is_zero():
    m = _const(n=1, frames=20, warmup=5)
    r = scoring.score_timeline(_tl([1] * 20, ids=[[7]] * 20), m)
    assert r["score"] == 0.0
    assert r["raw"]["scored_frames"] == 15
    assert r["raw"]["missed_dancer_frames"] == 0
    assert r["raw"]["distinct_ids"] == 1


def test_drops_only_in_scored_region():
    # reported=0 for frames 0..9 ; warmup=5 excludes 0..4, so only 5..9 score.
    m = _const(n=1, frames=20, warmup=5)
    rep = [0] * 10 + [1] * 10
    r = scoring.score_timeline(_tl(rep), m)
    # 5 missed dancer-frames over 15 scored, 15 expected.
    assert r["raw"]["missed_dancer_frames"] == 5
    assert r["components"]["drop_rate"] == pytest.approx(5 / 15, abs=1e-4)
    assert r["raw"]["ghost_dancer_frames"] == 0


def test_ghost_rate():
    m = _const(n=1, frames=10, warmup=0)
    rep = [1, 2, 2, 1, 1, 1, 1, 1, 1, 1]  # 2 excess dancer-frames
    r = scoring.score_timeline(_tl(rep), m)
    assert r["raw"]["ghost_dancer_frames"] == 2
    assert r["components"]["ghost_rate"] == pytest.approx(2 / 10, abs=1e-4)
    assert r["components"]["drop_rate"] == 0.0


def test_seconds_use_fps():
    m = _const(n=1, frames=10, warmup=0, fps=20.0)
    rep = [0, 0, 0, 0, 1, 1, 1, 1, 1, 1]  # 4 missed frames
    r = scoring.score_timeline(_tl(rep), m)
    assert r["raw"]["missed_dancer_seconds"] == pytest.approx(4 / 20.0, abs=1e-3)


# --------------------------------------------------------------------------- #
# episode span mapping (the warmup-offset bug fixed in A2)
# --------------------------------------------------------------------------- #
def test_episode_spans_are_window_relative_not_array_index():
    # warmup=5, a drop at window-relative frames 8..10 must report as 8..10
    # (and abs start+8..start+10), NOT as scored-array indices 3..5.
    m = _const(n=1, frames=20, warmup=5, start=1000)
    rep = [1] * 20
    rep[8] = rep[9] = rep[10] = 0
    r = scoring.score_timeline(_tl(rep), m)
    assert r["raw"]["drop_episode_spans_rel"] == [[8, 10]]
    assert r["raw"]["drop_episode_spans_abs"] == [[1008, 1010]]
    assert r["raw"]["longest_drop_frames"] == 3
    assert r["raw"]["drop_episodes"] == 1


def test_two_separate_drop_episodes():
    m = _const(n=1, frames=20, warmup=0)
    rep = [1] * 20
    rep[3] = 0
    rep[10] = rep[11] = 0
    r = scoring.score_timeline(_tl(rep), m)
    assert r["raw"]["drop_episodes"] == 2
    assert r["components"]["frag_rate"] == pytest.approx(2 / 20, abs=1e-4)


# --------------------------------------------------------------------------- #
# ID instability is bounded (swaps are acceptable -> can't dominate)
# --------------------------------------------------------------------------- #
def test_id_switch_counted_only_between_consecutive_reported():
    m = _const(n=1, frames=10, warmup=0)
    # continuous presence, id flips 7->8 once mid-stream = one switch
    ids = [[7], [7], [7], [8], [8], [8], [8], [8], [8], [8]]
    r = scoring.score_timeline(_tl([1] * 10, ids=ids), m)
    assert r["raw"]["id_switches"] == 1


def test_reacquire_after_drop_is_not_a_switch():
    m = _const(n=1, frames=10, warmup=0)
    # id 7 drops out, dancer re-acquired as id 9 across the gap -> NOT a switch
    rep = [1, 1, 1, 0, 0, 1, 1, 1, 1, 1]
    ids = [[7], [7], [7], [], [], [9], [9], [9], [9], [9]]
    r = scoring.score_timeline(_tl(rep, ids=ids), m)
    assert r["raw"]["id_switches"] == 0
    assert r["raw"]["distinct_ids"] == 2
    assert r["raw"]["excess_ids"] == 1


def test_id_penalty_cannot_dominate_drop():
    # Heavy fragmentation (many ids) but no drops: id_pen is bounded < a real
    # drop_rate would be, after weighting.
    m = _const(n=1, frames=10, warmup=0)
    ids = [[i] for i in range(10)]  # a new id every frame
    r = scoring.score_timeline(_tl([1] * 10, ids=ids), m)
    assert r["components"]["id_pen"] <= 2.0
    assert r["components"]["weighted"]["id"] <= 0.2


# --------------------------------------------------------------------------- #
# multi-scenario aggregation (Phase C3 prep)
# --------------------------------------------------------------------------- #
def test_score_multi_mean_and_worst():
    m = _const(n=1, frames=10, warmup=0)
    good = _tl([1] * 10)
    bad = _tl([0] * 10)
    agg = scoring.score_multi([(m, good), (m, bad)])
    assert agg["worst_score"] >= agg["mean_score"]
    assert agg["per_scenario"]  # populated
    assert len(agg["details"]) == 2


# --------------------------------------------------------------------------- #
# the committed seed manifests load & validate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["residence1-solo_slot3", "residence1-solo_slot4"])
def test_seed_manifests_valid(name):
    m = scoring.load_scenario(HERE / "scenarios" / f"{name}.json")
    assert m["expected_count"] == 1
    assert m["warmup"] == 15
    assert m["frames"] == 300
