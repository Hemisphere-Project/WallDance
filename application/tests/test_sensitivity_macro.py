"""Sensitivity macro (U5): one dial -> confidence (+var at the loose end)."""
import pytest

from sensitivity_macro import macro_to_settings, CONF_BOUNDS
from config import (SENS_CONF_STRICT_DELTA, SENS_CONF_LOOSE_DELTA,
                    SENS_VAR_FLOOR, SENS_VAR_KNEE)


def test_midpoint_returns_seeds():
    m = macro_to_settings(50, conf_seed=0.25, var_anchor=16.0)
    assert m["confidence"] == pytest.approx(0.25)
    assert m["mog2_var_threshold"] == pytest.approx(16.0)


def test_strict_end_raises_confidence():
    m = macro_to_settings(0, conf_seed=0.25, var_anchor=16.0)
    assert m["confidence"] == pytest.approx(0.25 + SENS_CONF_STRICT_DELTA)
    assert m["mog2_var_threshold"] == pytest.approx(16.0)  # var untouched


def test_loose_end_lowers_confidence_and_var():
    m = macro_to_settings(100, conf_seed=0.25, var_anchor=16.0)
    assert m["confidence"] == pytest.approx(0.25 - SENS_CONF_LOOSE_DELTA)
    assert m["mog2_var_threshold"] == pytest.approx(SENS_VAR_FLOOR)


def test_var_ramp_starts_at_knee():
    just_below = macro_to_settings(SENS_VAR_KNEE, 0.25, 16.0)
    assert just_below["mog2_var_threshold"] == pytest.approx(16.0)
    mid = macro_to_settings((SENS_VAR_KNEE + 100) / 2, 0.25, 16.0)
    assert SENS_VAR_FLOOR < mid["mog2_var_threshold"] < 16.0


def test_var_never_raised_above_anchor_or_floor():
    # Anchor already below the floor: keep it (never make MOG2 less sensitive).
    m = macro_to_settings(100, 0.25, 6.0)
    assert m["mog2_var_threshold"] == pytest.approx(6.0)


def test_confidence_clamped():
    assert macro_to_settings(0, 0.85, 16.0)["confidence"] == CONF_BOUNDS[1]
    assert macro_to_settings(100, 0.12, 16.0)["confidence"] == CONF_BOUNDS[0]


def test_monotonic_in_slider():
    confs = [macro_to_settings(s, 0.3, 16.0)["confidence"] for s in range(0, 101, 10)]
    assert confs == sorted(confs, reverse=True)
