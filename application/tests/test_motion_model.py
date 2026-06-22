"""Unit tests for the unified MotionModel (ROADMAP P3 Stage 1).

Synthetic frames only -- no model, GPU, or recordings.  Validates the frozen
surface so Stage 2 can reroute the live pipeline through it with confidence.
"""
import numpy as np
import pytest

pytest.importorskip("cv2")
import cv2  # noqa: E402

from core.config import MOTION_DIFF_PAIR_MAX_AGE_FRAMES  # noqa: E402
from core.motion_model import MotionModel  # noqa: E402

W, H = 640, 480
PERSON_H = 200
MID = 100  # mid-brightness base, above the low-light path threshold (55)


def _frame(value=MID):
    return np.full((H, W), value, dtype=np.uint8)


def _frame_with_square(cx, cy, w=70, h=160, base=MID, fill=240):
    f = _frame(base)
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    f[y0:y0 + h, x0:x0 + w] = fill
    return f


def _settle(model, n=70, base=MID):
    """Feed static frames so MOG2 learns the background and clears warmup."""
    for _ in range(n):
        model.feed(_frame(base))


# ---------------------------------------------------------------------------
# noise_sigma
# ---------------------------------------------------------------------------
def test_noise_sigma_zero_before_two_frames():
    m = MotionModel()
    assert m.noise_sigma() == 0.0
    m.feed(_frame())
    assert m.noise_sigma() == 0.0  # still <2 frames


def test_noise_sigma_near_zero_on_constant_input():
    m = MotionModel()
    for _ in range(30):
        m.feed(_frame(MID))
    assert m.noise_sigma() < 0.5


def test_noise_sigma_rises_with_injected_noise():
    rng = np.random.default_rng(0)
    quiet, noisy = MotionModel(), MotionModel()
    for _ in range(40):
        base = _frame(MID).astype(np.int16)
        q = np.clip(base + rng.normal(0, 1, base.shape), 0, 255).astype(np.uint8)
        n = np.clip(base + rng.normal(0, 12, base.shape), 0, 255).astype(np.uint8)
        quiet.feed(q)
        noisy.feed(n)
    assert noisy.noise_sigma() > quiet.noise_sigma()
    assert noisy.noise_sigma() > 1.0


# ---------------------------------------------------------------------------
# recent_motion (frame-diff "moving now?")
# ---------------------------------------------------------------------------
def test_recent_motion_zero_on_static_scene():
    m = MotionModel()
    _settle(m)
    roi = (W / 2 - 40, H / 2 - 80, 80, 160)
    assert m.recent_motion(roi) < 0.01


def test_recent_motion_fires_on_moving_square():
    m = MotionModel()
    _settle(m)
    # Move the square between consecutive frames so frame-diff sees change.
    m.feed(_frame_with_square(280, H / 2))
    m.feed(_frame_with_square(360, H / 2))
    roi_moving = (300, H / 2 - 90, 140, 180)  # spans both positions
    roi_quiet = (40, 40, 80, 160)
    assert m.recent_motion(roi_moving) > 0.05
    assert m.recent_motion(roi_quiet) < 0.01


def test_recent_motion_stale_pair_caps_out():
    """Bug #4: the raw pair only advances on global change, so a clean static
    stretch freezes it and the diff kept reporting the LAST motion event.
    Under the cap the stale report stays (it bridges slow movers); past the
    cap it must read zero."""
    m = MotionModel()
    _settle(m)
    m.feed(_frame_with_square(280, H / 2))
    m.feed(_frame_with_square(360, H / 2))
    roi = (300, H / 2 - 90, 140, 180)
    assert m.recent_motion(roi) > 0.05          # fresh motion reports

    static = _frame_with_square(360, H / 2)     # bit-identical frames
    for _ in range(MOTION_DIFF_PAIR_MAX_AGE_FRAMES):
        m.feed(static)
    assert m.recent_motion(roi) > 0.05          # age == cap: still bridging
    m.feed(static)                              # one past the cap
    assert m.recent_motion(roi) == 0.0
    blob, ratio = m.recent_motion_blob(roi)
    assert blob is None and ratio == 0.0


def test_recent_motion_revives_after_stale_cap():
    m = MotionModel()
    _settle(m)
    m.feed(_frame_with_square(280, H / 2))
    m.feed(_frame_with_square(360, H / 2))
    static = _frame_with_square(360, H / 2)
    for _ in range(MOTION_DIFF_PAIR_MAX_AGE_FRAMES + 5):
        m.feed(static)
    assert m.recent_motion((300, H / 2 - 90, 140, 180)) == 0.0
    # New movement advances the pair -> reporting resumes immediately.
    m.feed(_frame_with_square(440, H / 2))
    assert m.recent_motion((360, H / 2 - 90, 160, 180)) > 0.0


# ---------------------------------------------------------------------------
# foreground silhouette (MOG2)
# ---------------------------------------------------------------------------
def test_foreground_blob_finds_silhouette():
    m = MotionModel()
    _settle(m)
    # Introduce a stationary bright figure; slow MOG2 keeps it foreground.
    for _ in range(3):
        m.feed(_frame_with_square(W / 2, H / 2))
    roi = (W / 2 - 80, H / 2 - 110, 160, 220)
    blob, ratio = m.foreground_blob(roi, include_shadows=True)
    assert ratio > 0.05
    assert blob is not None
    # Blob centroid lands near the square center.
    assert abs(blob.centroid[0] - W / 2) < 60
    assert abs(blob.centroid[1] - H / 2) < 60


def test_foreground_ratio_low_on_clean_background():
    m = MotionModel()
    _settle(m)
    roi = (W / 2 - 40, H / 2 - 80, 80, 160)
    assert m.foreground_ratio(roi) < 0.02


def test_foreground_blobs_global_detects_figure():
    m = MotionModel()
    _settle(m)
    for _ in range(3):
        m.feed(_frame_with_square(W / 2, H / 2))
    blobs = m.foreground_blobs(
        PERSON_H, allow_during_warmup=True, suppress_static=False,
        include_shadows=True)
    assert len(blobs) >= 1


# ---------------------------------------------------------------------------
# lifecycle / state
# ---------------------------------------------------------------------------
def test_reset_clears_state():
    m = MotionModel()
    _settle(m, n=10)
    assert m.frame_count > 0
    assert m.has_model
    m.reset()
    assert m.frame_count == 0
    assert not m.has_model
    assert m.noise_sigma() == 0.0


def test_frame_count_and_brightness_track_feed():
    m = MotionModel()
    m.feed(_frame(120))
    assert m.frame_count == 1
    assert 100 < m.brightness < 140


def test_var_threshold_passthrough():
    m = MotionModel(var_threshold=40)
    m.set_var_threshold(16)
    assert m.get_var_threshold() == 16


# ---------------------------------------------------------------------------
# fixed-gray contract (Bug #1)
# ---------------------------------------------------------------------------
def test_fixed_gamma_is_applied_and_frame_independent():
    m = MotionModel(fixed_gamma=2.0)  # >1 brightens midtones (inv=1/gamma, as display path)
    gray = _frame(100)
    out1 = m._apply_fixed_gray(gray)
    out2 = m._apply_fixed_gray(gray)
    assert int(out1[0, 0]) > 100            # midtone brightened
    np.testing.assert_array_equal(out1, out2)  # cached LUT -> no per-frame jitter


def test_default_is_identity_gray():
    m = MotionModel()  # fixed_gamma=1.0
    gray = _frame(137)
    np.testing.assert_array_equal(m._apply_fixed_gray(gray), gray)
