"""Sensitivity macro (U5): one dial -> confidence (+var at the loose end).

Span re-fit (Phase 2 ⑦): the dial interpolates seed -> ABSOLUTE corpus
bounds (best-τ 0.15–0.65), so the full measured range is reachable from any
seed — the old fixed deltas covered it only when the seed sat right.
"""
import pytest

from core.sensitivity_macro import (macro_to_settings, bridge_macro_to_settings,
                               CONF_BOUNDS)
from core.config import (SENS_CONF_MAX, SENS_CONF_MIN,
                    SENS_VAR_FLOOR, SENS_VAR_KNEE,
                    SENS_BRIDGE_MIN, SENS_BRIDGE_MAX)


def test_midpoint_returns_seeds():
    m = macro_to_settings(50, conf_seed=0.25, var_anchor=16.0)
    assert m["confidence"] == pytest.approx(0.25)
    assert m["mog2_var_threshold"] == pytest.approx(16.0)


def test_strict_end_reaches_corpus_max():
    m = macro_to_settings(0, conf_seed=0.25, var_anchor=16.0)
    assert m["confidence"] == pytest.approx(SENS_CONF_MAX)
    assert m["mog2_var_threshold"] == pytest.approx(16.0)  # var untouched


def test_loose_end_reaches_corpus_min_and_var_floor():
    m = macro_to_settings(100, conf_seed=0.25, var_anchor=16.0)
    assert m["confidence"] == pytest.approx(SENS_CONF_MIN)
    assert m["mog2_var_threshold"] == pytest.approx(SENS_VAR_FLOOR)


def test_full_span_reachable_from_any_seed():
    # The §6.7 requirement: best-τ spans 0.15–0.65 across scenes; the dial
    # must cover it wherever calib2 seeded the scene.
    for seed in (0.18, 0.30, 0.45, 0.60):
        assert macro_to_settings(0, seed, 16.0)["confidence"] == \
            pytest.approx(SENS_CONF_MAX)
        assert macro_to_settings(100, seed, 16.0)["confidence"] == \
            pytest.approx(SENS_CONF_MIN)
        assert macro_to_settings(50, seed, 16.0)["confidence"] == \
            pytest.approx(seed)


def test_seed_outside_span_holds_instead_of_pushing_further():
    # A manually-set seed above the strict bound: the strict side holds at
    # the seed (never raises it further); the loose side still spans down.
    m = macro_to_settings(0, 0.85, 16.0)
    assert m["confidence"] == pytest.approx(0.85)
    assert macro_to_settings(100, 0.85, 16.0)["confidence"] == \
        pytest.approx(SENS_CONF_MIN)
    # Below the loose bound: loose side holds, strict side spans up.
    m = macro_to_settings(100, 0.12, 16.0)
    assert m["confidence"] == pytest.approx(0.12)
    assert macro_to_settings(0, 0.12, 16.0)["confidence"] == \
        pytest.approx(SENS_CONF_MAX)


def test_var_ramp_starts_at_knee():
    just_below = macro_to_settings(SENS_VAR_KNEE, 0.25, 16.0)
    assert just_below["mog2_var_threshold"] == pytest.approx(16.0)
    mid = macro_to_settings((SENS_VAR_KNEE + 100) / 2, 0.25, 16.0)
    assert SENS_VAR_FLOOR < mid["mog2_var_threshold"] < 16.0


def test_var_never_raised_above_anchor_or_floor():
    # Anchor already below the floor: keep it (never make MOG2 less sensitive).
    m = macro_to_settings(100, 0.25, 6.0)
    assert m["mog2_var_threshold"] == pytest.approx(6.0)


def test_confidence_stays_inside_safety_clamp():
    for s in range(0, 101, 5):
        for seed in (0.05, 0.3, 0.92):
            c = macro_to_settings(s, seed, 16.0)["confidence"]
            assert CONF_BOUNDS[0] <= c <= CONF_BOUNDS[1]


def test_monotonic_in_slider():
    for seed in (0.2, 0.3, 0.6, 0.85):
        confs = [macro_to_settings(s, seed, 16.0)["confidence"]
                 for s in range(0, 101, 5)]
        assert confs == sorted(confs, reverse=True), seed


# --- Dial B: gap bridging -> motion_sensitivity (OPERATOR_V2 §2.2) ----------

def test_bridge_midpoint_returns_seed():
    assert bridge_macro_to_settings(50, 0.55)["motion_sensitivity"] == \
        pytest.approx(0.55)


def test_bridge_ends_reach_corpus_span():
    assert bridge_macro_to_settings(0, 0.55)["motion_sensitivity"] == \
        pytest.approx(SENS_BRIDGE_MIN)
    assert bridge_macro_to_settings(100, 0.55)["motion_sensitivity"] == \
        pytest.approx(SENS_BRIDGE_MAX)


def test_bridge_monotonic_more_is_fewer_drops():
    # Higher slider must monotonically RAISE motion_sensitivity (more bridging).
    for seed in (0.3, 0.55, 0.8):
        ms = [bridge_macro_to_settings(s, seed)["motion_sensitivity"]
              for s in range(0, 101, 5)]
        assert ms == sorted(ms), seed


def test_bridge_seed_outside_span_holds():
    # Seed above the max: the high side holds (never pushes past), low side spans.
    assert bridge_macro_to_settings(100, 0.95)["motion_sensitivity"] == \
        pytest.approx(0.95)
    assert bridge_macro_to_settings(0, 0.95)["motion_sensitivity"] == \
        pytest.approx(SENS_BRIDGE_MIN)


def test_bridge_stays_in_unit_range():
    for s in range(0, 101, 5):
        for seed in (0.0, 0.55, 1.0):
            ms = bridge_macro_to_settings(s, seed)["motion_sensitivity"]
            assert 0.0 <= ms <= 1.0
