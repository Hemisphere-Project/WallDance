"""Unit tests for the Go-Live scene calibrator (src/calibration.py).

Feeds synthetic samples to SceneCalibrator and locks the measurement maths:
person-height median + percentile-derived ratios, the empirical background
false-positive sweep that picks MOG2 varThreshold, and the exposure / FPS report.
"""

import numpy as np
import pytest

import calibration
from calibration import SceneCalibrator
from config import (
    AUTOCAL_MIN_HEIGHT_SAMPLES,
    AUTOCAL_VARTHRESH_CANDIDATES,
    AUTOCAL_FP_TARGET,
)


def _run(cal, *, gray, heights_per_frame, frames, fps=30.0):
    """Drive a full window with the same gray + heights every frame."""
    cal.start()
    for i in range(frames):
        cal.feed(gray, list(heights_per_frame), fps, float(i))
    assert cal.ready
    return cal.compute()


# --------------------------------------------------------------------------
# Person height + ratios
# --------------------------------------------------------------------------
def test_height_median_and_ratios():
    # Heights 100..300 inclusive → median 200, p05=110, p95=290.
    heights = list(range(100, 301))
    cal = SceneCalibrator(window_frames=4)
    gray = np.full((48, 64), 100, dtype=np.uint8)
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
    res = _run(cal, gray=gray, heights_per_frame=[200.0], frames=3)

    assert res.height_samples < AUTOCAL_MIN_HEIGHT_SAMPLES
    assert not res.height_ok
    assert res.person_height_px is None
    assert res.min_ratio is None and res.max_ratio is None


# --------------------------------------------------------------------------
# varThreshold — empirical background false-positive sweep
# --------------------------------------------------------------------------
def test_varthreshold_picks_lowest_on_clean_background():
    # A static background produces (almost) no MOG2 foreground → every candidate
    # is under the FP target → the lowest (most sensitive) candidate wins.
    cal = SceneCalibrator(window_frames=30)
    gray = np.full((54, 96), 128, dtype=np.uint8)
    res = _run(cal, gray=gray, heights_per_frame=[], frames=30)

    assert res.var_ok
    assert not res.var_saturated
    assert res.var_threshold == pytest.approx(min(AUTOCAL_VARTHRESH_CANDIDATES))
    assert res.var_fp_rate <= AUTOCAL_FP_TARGET


def test_varthreshold_selection_logic():
    # White-box: inject per-candidate FP rates and check the decision.
    cal = SceneCalibrator(window_frames=2)
    cal.start()
    cands = list(AUTOCAL_VARTHRESH_CANDIDATES)
    # First two candidates noisy (above target), the third clean → pick the third.
    fp = [AUTOCAL_FP_TARGET * 5, AUTOCAL_FP_TARGET * 2,
          AUTOCAL_FP_TARGET * 0.5] + [0.0] * (len(cands) - 3)
    cal._var_candidates = cands
    cal._var_fp = [[v] for v in fp]
    # satisfy readiness / brightness so compute() runs the rest cleanly
    cal._frames = cal.window_frames
    res = cal.compute()

    assert res.var_ok and not res.var_saturated
    assert res.var_threshold == pytest.approx(cands[2])
    assert res.var_fp_rate == pytest.approx(fp[2])


def test_varthreshold_saturates_when_none_clean():
    # White-box: every candidate exceeds the FP target (scene too noisy for MOG2)
    # → fall back to the highest candidate and flag it saturated.
    cal = SceneCalibrator(window_frames=2)
    cal.start()
    cands = list(AUTOCAL_VARTHRESH_CANDIDATES)
    cal._var_candidates = cands
    cal._var_fp = [[AUTOCAL_FP_TARGET * 3] for _ in cands]
    cal._frames = cal.window_frames
    res = cal.compute()

    assert res.var_ok and res.var_saturated
    assert res.var_threshold == pytest.approx(max(cands))
    assert res.var_fp_rate > AUTOCAL_FP_TARGET


# --------------------------------------------------------------------------
# Noise sigma (diagnostic) + brightness decoupling
# --------------------------------------------------------------------------
def test_noise_sigma_diagnostic_and_brightness_decoupled(monkeypatch):
    # noise_sigma is measured on the (enhanced) noise_gray, while the exposure
    # report uses the explicit raw brightness — they must not be conflated.
    monkeypatch.setattr(calibration, "AUTOCAL_NOISE_SCALE", 1.0)
    rng = np.random.default_rng(7)
    cal = SceneCalibrator(window_frames=60)
    cal.start()
    for i in range(60):
        noisy = np.clip(180 + rng.normal(0, 2.0, size=(48, 48)), 0, 255).astype(np.uint8)
        cal.feed(noisy, [], 30.0, float(i), brightness=5.0)  # raw scene near-black
    res = cal.compute()

    assert res.noise_sigma == pytest.approx(2.0, abs=0.4)       # from the noisy gray
    assert res.brightness_mean == pytest.approx(5.0, abs=1e-6)  # from explicit raw luma


# --------------------------------------------------------------------------
# Exposure / FPS report
# --------------------------------------------------------------------------
def test_exposure_and_fps_report():
    cal = SceneCalibrator(window_frames=10)
    cal.start()
    for i in range(10):
        cal.feed(np.full((32, 32), 130, dtype=np.uint8), [], 25.0, float(i))
    res = cal.compute()

    assert res.brightness_mean == pytest.approx(130.0, abs=1.0)
    assert res.brightness_cv == pytest.approx(0.0, abs=1e-6)
    assert res.exposure_stable
    assert res.fps_achieved == pytest.approx(25.0, abs=1e-6)


def test_drifting_exposure_flagged():
    cal = SceneCalibrator(window_frames=20)
    cal.start()
    for i in range(20):
        cal.feed(np.full((16, 16), int(60 + i * 10), dtype=np.uint8), [], 30.0, float(i))
    res = cal.compute()

    assert not res.exposure_stable
    assert res.brightness_cv > 0.05


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------
def test_state_machine_guards():
    cal = SceneCalibrator(window_frames=3)
    assert not cal.is_collecting
    cal.feed(np.zeros((8, 8), np.uint8), [200.0], 30.0, 0.0)  # before start = no-op
    assert cal.frames == 0

    cal.start()
    assert cal.is_collecting and not cal.ready
    cal.feed(np.zeros((8, 8), np.uint8), [200.0], 30.0, 0.0)
    assert cal.progress() == pytest.approx(1 / 3, abs=1e-6)
