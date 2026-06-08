"""Unit tests for the Go-Live scene calibrator (src/calibration.py).

Feeds synthetic samples to SceneCalibrator and locks the measurement maths:
person-height median + percentile-derived ratios, the background-noise sigma →
varThreshold mapping (incl. clamps), and the exposure / FPS report.
"""

import numpy as np
import pytest

import calibration
from calibration import SceneCalibrator
from config import (
    AUTOCAL_MIN_HEIGHT_SAMPLES,
    AUTOCAL_VARTHRESH_BOUNDS,
)


def _run(cal, *, gray, heights_per_frame, frames, fps=30.0):
    """Drive a full window with the same gray + heights every frame."""
    cal.start()
    for i in range(frames):
        cal.feed(gray, list(heights_per_frame), fps, float(i))
    assert cal.ready
    return cal.compute()


def test_height_median_and_ratios():
    # Heights 100..300 inclusive → median 200, p05=110, p95=290.
    heights = list(range(100, 301))
    cal = SceneCalibrator(window_frames=4)
    gray = np.full((48, 64), 100, dtype=np.uint8)  # static → noise sigma 0
    res = _run(cal, gray=gray, heights_per_frame=heights, frames=4)

    assert res.height_ok
    assert res.person_height_px == 200
    assert res.height_samples == 4 * len(heights)
    # 110/200 = 0.55 (within [0.2, 0.8]); 290/200 = 1.45 → clamps up to 1.5 floor.
    assert res.min_ratio == pytest.approx(0.55, abs=1e-3)
    assert res.max_ratio == pytest.approx(1.5, abs=1e-3)


def test_insufficient_height_samples_keeps_height():
    cal = SceneCalibrator(window_frames=3)
    gray = np.full((32, 32), 120, dtype=np.uint8)
    # Far fewer than AUTOCAL_MIN_HEIGHT_SAMPLES total detections.
    few = [200.0]
    res = _run(cal, gray=gray, heights_per_frame=few, frames=3)

    assert res.height_samples < AUTOCAL_MIN_HEIGHT_SAMPLES
    assert not res.height_ok
    assert res.person_height_px is None
    assert res.min_ratio is None and res.max_ratio is None


def test_varthreshold_from_noise(monkeypatch):
    # Measure noise on the raw input (skip the 0.5 downscale, which would
    # average-down independent noise and confound the expected sigma).
    monkeypatch.setattr(calibration, "AUTOCAL_NOISE_SCALE", 1.0)
    rng = np.random.default_rng(0)
    sigma_in = 2.0
    cal = SceneCalibrator(window_frames=80)
    cal.start()
    for i in range(80):
        noisy = np.clip(128 + rng.normal(0, sigma_in, size=(64, 64)), 0, 255).astype(np.uint8)
        cal.feed(noisy, [], 30.0, float(i))
    res = cal.compute()

    assert res.noise_ok
    assert res.noise_sigma == pytest.approx(sigma_in, abs=0.4)
    # varThreshold = (4 * sigma)^2, ~64 here, inside the bounds (not clamped).
    lo, hi = AUTOCAL_VARTHRESH_BOUNDS
    expected = round(min(max((4.0 * res.noise_sigma) ** 2, lo), hi), 1)
    assert res.var_threshold == expected
    assert lo < res.var_threshold < hi


def test_varthreshold_clamps(monkeypatch):
    monkeypatch.setattr(calibration, "AUTOCAL_NOISE_SCALE", 1.0)
    lo, hi = AUTOCAL_VARTHRESH_BOUNDS

    # Static scene → sigma 0 → clamps to the lower bound.
    cal = SceneCalibrator(window_frames=4)
    res = _run(cal, gray=np.full((32, 32), 64, dtype=np.uint8),
               heights_per_frame=[], frames=4)
    assert res.noise_sigma == pytest.approx(0.0, abs=1e-6)
    assert res.var_threshold == pytest.approx(lo)

    # Heavy noise → (4*30)^2 huge → clamps to the upper bound.
    rng = np.random.default_rng(1)
    cal2 = SceneCalibrator(window_frames=40)
    cal2.start()
    for i in range(40):
        g = np.clip(128 + rng.normal(0, 30.0, size=(48, 48)), 0, 255).astype(np.uint8)
        cal2.feed(g, [], 30.0, float(i))
    res2 = cal2.compute()
    assert res2.var_threshold == pytest.approx(hi)


def test_exposure_and_fps_report():
    cal = SceneCalibrator(window_frames=10)
    cal.start()
    for i in range(10):
        gray = np.full((32, 32), 130, dtype=np.uint8)  # steady brightness
        cal.feed(gray, [], 25.0, float(i))             # 25 fps every frame
    res = cal.compute()

    assert res.brightness_mean == pytest.approx(130.0, abs=1.0)
    assert res.brightness_cv == pytest.approx(0.0, abs=1e-6)
    assert res.exposure_stable
    assert res.fps_achieved == pytest.approx(25.0, abs=1e-6)


def test_drifting_exposure_flagged():
    cal = SceneCalibrator(window_frames=20)
    cal.start()
    for i in range(20):
        # Brightness ramps 60→250 → high coefficient of variation.
        val = int(60 + i * 10)
        cal.feed(np.full((16, 16), val, dtype=np.uint8), [], 30.0, float(i))
    res = cal.compute()

    assert not res.exposure_stable
    assert res.brightness_cv > 0.05


def test_state_machine_guards():
    cal = SceneCalibrator(window_frames=3)
    assert not cal.is_collecting
    # feed before start is a no-op (no crash, no frames recorded)
    cal.feed(np.zeros((8, 8), np.uint8), [200.0], 30.0, 0.0)
    assert cal.frames == 0

    cal.start()
    assert cal.is_collecting and not cal.ready
    cal.feed(np.zeros((8, 8), np.uint8), [200.0], 30.0, 0.0)
    assert cal.progress() == pytest.approx(1 / 3, abs=1e-6)
