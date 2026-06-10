"""Unit tests for the Phase C search strategies (TUNING.md Phase C).

Pure logic: a FakeTuner scores overrides from a known synthetic function, so the
search algorithms are tested without GPU / caches / recordings.  Importing
``tune`` is safe in-process (it imports detect_cache, which lazy-imports replay,
so no CUDA-bootstrap re-exec fires).
"""
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tune  # noqa: E402


class FakeTuner:
    """Scores overrides by a separable function with a known optimum.

    score = sum over params of (value - optimum[param])**2 ; min at the optimum.
    Mimics Tuner.evaluate's return shape.
    """

    def __init__(self, optimum, base=None):
        self.optimum = optimum
        self.base = base or {}
        self.n_evals = 0
        self.seen = []

    def evaluate(self, overrides):
        self.n_evals += 1
        self.seen.append(dict(overrides))
        cfg = {**self.base, **overrides}
        s = sum((cfg.get(k, self.optimum[k]) - opt) ** 2
                for k, opt in self.optimum.items())
        return {"mean_score": float(s), "worst_score": float(s),
                "per_scenario": {"fake": float(s)}}


def test_coordinate_descent_finds_separable_optimum():
    space = {"a": [0, 1, 2, 3], "b": [0, 1, 2, 3]}
    opt = {"a": 2, "b": 3}
    tuner = FakeTuner(opt, base={"a": 0, "b": 0})
    best, best_score, history = tune.coordinate_descent(tuner, space, {"a": 0, "b": 0})
    assert best["a"] == 2 and best["b"] == 3
    assert best_score == pytest.approx(0.0)


def test_coordinate_descent_converges_without_full_grid():
    # 4 params x 5 values: grid would be 625; coord must be far fewer.
    space = {p: list(range(5)) for p in "abcd"}
    opt = {p: 4 for p in "abcd"}
    tuner = FakeTuner(opt, base={p: 0 for p in "abcd"})
    best, score, _ = tune.coordinate_descent(tuner, space, {p: 0 for p in "abcd"},
                                             max_passes=3)
    assert best == opt
    assert tuner.n_evals < 625


def test_grid_search_is_exhaustive_and_optimal():
    space = {"a": [0, 1, 2], "b": [0, 1]}
    opt = {"a": 1, "b": 1}
    tuner = FakeTuner(opt, base={"a": 0, "b": 0})
    best, score, history = tune.grid_search(tuner, space, {})
    assert tuner.n_evals == 3 * 2          # full product
    assert best["a"] == 1 and best["b"] == 1
    assert score == pytest.approx(0.0)


def test_random_search_respects_sample_count_and_is_deterministic():
    space = {"a": list(range(10)), "b": list(range(10))}
    opt = {"a": 5, "b": 5}
    t1 = FakeTuner(opt)
    b1, s1, _ = tune.random_search(t1, space, {}, n=20, seed=42)
    assert t1.n_evals == 20
    # same seed -> same trajectory
    t2 = FakeTuner(opt)
    b2, s2, _ = tune.random_search(t2, space, {}, n=20, seed=42)
    assert t1.seen == t2.seen and s1 == s2


def test_default_space_is_post_yolo_only():
    # The default space must be cache-tunable (no YOLO-front-end / rebuild keys),
    # so a default search reuses one cache per scenario.
    import detect_cache
    assert set(tune.DEFAULT_SPACE).isdisjoint(detect_cache.REBUILD_KEYS)
