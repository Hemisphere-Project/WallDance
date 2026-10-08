"""Unit tests for the Go-Live scene calibrator (src/calibration.py).

Feeds synthetic samples to SceneCalibrator and locks the measurement maths:
person-height median + percentile-derived ratios, the empirical background
false-positive sweep that picks MOG2 varThreshold, and the exposure / FPS report.
"""

import numpy as np
import pytest

import core.calibration as calibration

from core.calibration import SceneCalibrator, ExclusionMaskBuilder
from core.config import (
    AUTOCAL_MIN_HEIGHT_SAMPLES,
    AUTOCAL_VARTHRESH_CANDIDATES,
    AUTOCAL_FP_TARGET,
)


def _moving_mask(shape=(40, 40), region=(slice(0, 10), slice(0, 10))):
    """A clean MOG2 mask with one fixed foreground region (255), rest background."""
    m = np.zeros(shape, dtype=np.uint8)
    m[region] = 255
    return m


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
    # White-box: inject per-pair FP rates and check the joint decision.
    cal = SceneCalibrator(window_frames=2)
    cal.start()
    cands = sorted(AUTOCAL_VARTHRESH_CANDIDATES)
    # Single-scale injection: first two vars noisy, the third clean → third wins.
    cal._var_pairs = [(v, 0.7) for v in cands]
    fp = [AUTOCAL_FP_TARGET * 5, AUTOCAL_FP_TARGET * 2,
          AUTOCAL_FP_TARGET * 0.5] + [0.0] * (len(cands) - 3)
    cal._var_fp = [[v] for v in fp]
    # satisfy readiness / brightness so compute() runs the rest cleanly
    cal._frames = cal.window_frames
    res = cal.compute()

    assert res.var_ok and not res.var_saturated
    assert res.var_threshold == pytest.approx(cands[2])
    assert res.mog2_scale == pytest.approx(0.7)
    assert res.var_fp_rate == pytest.approx(fp[2])


def test_var_scale_preference_order():
    # At the same (lowest passing) var, the preferred scale (0.7) wins even if
    # other scales also pass.
    cal = SceneCalibrator(window_frames=2)
    cal.start()
    v0 = sorted(AUTOCAL_VARTHRESH_CANDIDATES)[0]
    cal._var_pairs = [(v0, 0.5), (v0, 0.7), (v0, 1.0)]
    cal._var_fp = [[0.0], [0.0], [0.0]]
    cal._frames = cal.window_frames
    res = cal.compute()

    assert res.var_threshold == pytest.approx(v0)
    assert res.mog2_scale == pytest.approx(0.7)


def test_varthreshold_saturates_when_none_clean():
    # White-box: every pair exceeds the FP target (scene too noisy for MOG2)
    # → fall back to the most conservative pair (max var, smallest scale).
    cal = SceneCalibrator(window_frames=2)
    cal.start()
    cal._var_fp = [[AUTOCAL_FP_TARGET * 3] for _ in cal._var_pairs]
    cal._frames = cal.window_frames
    res = cal.compute()

    assert res.var_ok and res.var_saturated
    assert res.var_threshold == pytest.approx(max(AUTOCAL_VARTHRESH_CANDIDATES))
    assert res.mog2_scale == pytest.approx(min(calibration.AUTOCAL_SCALE_CANDIDATES))
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
# Auto exclusion mask (P1.4)
# --------------------------------------------------------------------------
def _excl(**kw):
    return ExclusionMaskBuilder(grid=(4, 4), motion_freq=0.3, skel_freq=0.02,
                                min_frames=5, **kw)


def test_exclusion_masks_persistent_motion_without_skeleton():
    # Top-left cell moves every frame, never holds a skeleton → excluded.
    b = _excl()
    b.start()
    mask = _moving_mask()  # foreground in pixels [0:10, 0:10] → cell (0,0)
    for _ in range(10):
        b.observe(mask, skel_points=[])
    res = b.build()

    assert (0, 0) in res.cells
    assert res.count == 1
    assert b.excluded(0.1, 0.1)        # inside the masked cell
    assert not b.excluded(0.6, 0.6)    # elsewhere


def test_exclusion_spares_cell_with_skeletons():
    # Same moving cell, but a skeleton sits there every frame → NOT a ghost.
    b = _excl()
    b.start()
    mask = _moving_mask()
    for _ in range(10):
        b.observe(mask, skel_points=[(0.1, 0.1)])
    res = b.build()

    assert (0, 0) not in res.cells
    assert not b.excluded(0.1, 0.1)


def test_exclusion_ignores_rare_motion():
    # Cell moves in only 2/10 frames (< motion_freq 0.3) → not excluded.
    b = _excl()
    b.start()
    moving, still = _moving_mask(), np.zeros((40, 40), np.uint8)
    for i in range(10):
        b.observe(moving if i < 2 else still, skel_points=[])
    res = b.build()

    assert (0, 0) not in res.cells
    assert res.count == 0


def test_exclusion_needs_minimum_frames():
    b = _excl()
    b.start()
    for _ in range(3):                 # < min_frames (5)
        b.observe(_moving_mask(), skel_points=[])
    res = b.build()

    assert res.frames == 3
    assert res.count == 0
    assert not b.active


def test_exclusion_persist_roundtrip():
    b = ExclusionMaskBuilder(grid=(16, 10))
    b.set_cells((16, 10), [[2, 3], [7, 1]])
    grid, cells = b.get_cells()

    assert grid == (16, 10)
    assert cells == [(2, 3), (7, 1)]
    assert b.active
    assert b.excluded((2 + 0.5) / 16, (3 + 0.5) / 10)
    assert not b.excluded((5 + 0.5) / 16, (5 + 0.5) / 10)
    b.clear()
    assert not b.active and not b.excluded(0.1, 0.1)


def test_exclusion_out_of_range_safe():
    b = _excl()
    b.set_cells((4, 4), [(0, 0)])
    assert not b.excluded(-0.1, 0.5)
    assert not b.excluded(0.5, 1.5)


# --------------------------------------------------------------------------
# Manual overlays (ROADMAP §4.2 Phase 2 ④)
# --------------------------------------------------------------------------
def test_manual_toggle_masks_and_unmasks():
    b = _excl()
    # Toggle a clean cell ON (bystander zone) → manual-add, excluded.
    assert b.toggle_cell(2, 2) is True
    assert b.excluded((2 + 0.5) / 4, (2 + 0.5) / 4)
    # Toggle it back OFF → override removed, not excluded.
    assert b.toggle_cell(2, 2) is False
    assert not b.excluded((2 + 0.5) / 4, (2 + 0.5) / 4)
    _grid, _auto, add, rem = b.get_state()
    assert add == [] and rem == []


def test_manual_remove_vetoes_auto_cell():
    b = _excl()
    b.set_cells((4, 4), [(0, 0)])
    assert b.excluded(0.1, 0.1)
    assert b.toggle_cell(0, 0) is False     # operator veto of an auto cell
    assert not b.excluded(0.1, 0.1)
    _grid, auto, _add, rem = b.get_state()
    assert auto == [(0, 0)] and rem == [(0, 0)]


def test_manual_overlays_survive_rebuild():
    # The whole point: operator zones must survive a Calib1 re-run.
    b = _excl()
    b.set_cells((4, 4), [(0, 0)])   # prior auto mask
    b.set_cell(3, 3, True)          # bystander bench (auto can never find it)
    b.toggle_cell(0, 0)             # operator veto of the auto cell

    b.start()                       # Calib1 re-run
    mask = _moving_mask()           # auto re-detects (0,0) as a ghost cell
    for _ in range(10):
        b.observe(mask, skel_points=[])
    res = b.build()

    assert (0, 0) in res.cells                       # auto re-found it...
    assert not b.excluded(0.1, 0.1)                  # ...veto still wins
    assert b.excluded((3 + 0.5) / 4, (3 + 0.5) / 4)  # bench still masked
    assert res.manual_add == 1 and res.manual_remove == 1


def test_paint_value_is_idempotent():
    b = _excl()
    b.set_cell(1, 1, True)
    b.set_cell(1, 1, True)      # drag re-enters the cell: no flicker
    assert b.excluded((1 + 0.5) / 4, (1 + 0.5) / 4)
    _grid, _auto, add, _rem = b.get_state()
    assert add == [(1, 1)]


def test_overlay_persist_roundtrip():
    b = ExclusionMaskBuilder(grid=(16, 10))
    b.set_cells((16, 10), [[2, 3], [7, 1]],
                manual_add=[[5, 5]], manual_remove=[[2, 3]])
    grid, cells = b.get_cells()
    assert grid == (16, 10)
    assert cells == [(5, 5), (7, 1)]    # effective = auto ∪ add − remove
    _grid, auto, add, rem = b.get_state()
    assert auto == [(2, 3), (7, 1)] and add == [(5, 5)] and rem == [(2, 3)]
    # Round-trip through persistence keeps the split intact.
    b2 = ExclusionMaskBuilder(grid=(16, 10))
    b2.set_cells(_grid, auto, add, rem)
    assert b2.get_state() == b.get_state()
    assert b2.get_cells() == b.get_cells()


def test_cell_at_maps_points():
    b = _excl()
    assert b.cell_at(0.1, 0.1) == (0, 0)
    assert b.cell_at(0.9, 0.6) == (3, 2)
    assert b.cell_at(1.2, 0.5) is None


# --------------------------------------------------------------------------
# Gamma noise cap (⑤b — verydark regime)
# --------------------------------------------------------------------------
def test_gamma_cap_limits_brightening_on_noisy_scene():
    from core.calibration import cap_gamma_for_noise
    g, capped = cap_gamma_for_noise(2.6, noise_sigma=6.0,
                                    sigma_threshold=4.0, cap=1.8)
    assert capped and g == 1.8


def test_gamma_cap_leaves_quiet_or_mild_scenes_alone():
    from core.calibration import cap_gamma_for_noise
    # Quiet scene: any gamma passes.
    g, capped = cap_gamma_for_noise(2.6, noise_sigma=1.0,
                                    sigma_threshold=4.0, cap=1.8)
    assert not capped and g == 2.6
    # Noisy scene but gamma already mild: untouched.
    g, capped = cap_gamma_for_noise(1.4, noise_sigma=6.0,
                                    sigma_threshold=4.0, cap=1.8)
    assert not capped and g == 1.4


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


# --------------------------------------------------------------------------
# U3 — exposure servo (Calib1 phase A)
# --------------------------------------------------------------------------
from core.calibration import ExposureServo, seed_clahe, seed_gamma, scene_report_stats


def _drive(servo, brightness, clip=0.0, max_frames=400):
    """Feed a constant measurement until the servo is done; collect commands."""
    cmds = []
    for _ in range(max_frames):
        c = servo.feed(brightness, clip)
        if c is not None:
            cmds.append(c)
        if servo.done:
            break
    return cmds


def test_servo_exposure_cap_is_blur_budget():
    # 25 ms blur budget < 66.6 ms (15 FPS floor) → blur is the binding cap.
    s = ExposureServo(exposure_us=10000.0, gain_db=0.0)
    assert s.exposure_cap_us == pytest.approx(25000.0)


def test_servo_dark_scene_exposure_first_then_gain():
    s = ExposureServo(exposure_us=5000.0, gain_db=0.0)
    cmds = _drive(s, brightness=5.0)
    kinds = [k for k, _ in cmds]
    # exposure rises to the cap before any gain command
    first_gain = kinds.index("gain")
    assert all(k == "exposure" for k in kinds[:first_gain])
    assert s.exposure_us == pytest.approx(s.exposure_cap_us)
    # still dark at limits → stopped with the add-IR note
    assert s.done and not s.result().converged
    assert "IR" in s.result().note


def test_servo_clipping_backs_gain_off_first():
    # A bright, clipping region backs gain off before exposure (now via the median-above-band
    # branch: 5 % clipped alone no longer triggers a back-off, see the region tests below).
    s = ExposureServo(exposure_us=20000.0, gain_db=12.0)
    cmds = _drive(s, brightness=200.0, clip=5.0, max_frames=20)
    assert cmds[0][0] == "gain"
    assert cmds[0][1] == pytest.approx(9.0)


def test_servo_never_cuts_while_the_median_is_below_target():
    # 2026-10-08 hangar: windows clipped 6 % of the frame even at 0.2 ms; the old rule
    # (> 0.5 % clipped -> back off first) drove the wall black.  Dark median + any clipping
    # -> raise.
    s = ExposureServo(exposure_us=200.0, gain_db=0.5)
    cmds = _drive(s, brightness=8.0, clip=40.0, max_frames=20)
    assert cmds and cmds[0][0] == "exposure" and cmds[0][1] > 200.0


def test_servo_in_band_heavy_clipping_takes_a_gentle_step():
    # Median in band and at/above target, >= 15 % of the region clipped: a step down sized to
    # keep the median in band (no oscillation with the dark branch), gain before exposure.
    s = ExposureServo(exposure_us=10000.0, gain_db=6.0)
    cmds = _drive(s, brightness=78.0, clip=20.0, max_frames=10)
    assert len(cmds) == 1 and cmds[0][0] == "gain"
    assert 6.0 - 3.0 < cmds[0][1] < 6.0            # a fraction of the 3 dB too-bright step
    # in band, below target: heavy clipping alone does not move it
    s2 = ExposureServo(exposure_us=10000.0, gain_db=6.0)
    assert _drive(s2, brightness=62.0, clip=30.0) == [] and s2.result().converged
    # in band, at target, clipping under the limit: converged untouched
    s3 = ExposureServo(exposure_us=10000.0, gain_db=6.0)
    assert _drive(s3, brightness=75.0, clip=10.0) == [] and s3.result().converged


# --- closed loop on a simulated sensor: pixel = radiance x exposure(ms) x gain, clipped --------
from core.calibration import servo_region_stats                    # noqa: E402

_H, _W = 200, 320
_BAND = (0, 80, 320, 80)                 # the operator's wall band ROI (x, y, w, h)


def _scene(wall_rad, windows=None, rad_bright=5000.0):
    """Per-pixel radiance (luma per ms at 0 dB): a textured wall + saturating windows."""
    rng = np.random.default_rng(0)
    rad = wall_rad * rng.uniform(0.8, 1.2, (_H, _W))
    if windows is not None:
        x, y, w, h = windows
        rad[y:y + h, x:x + w] = rad_bright
    return rad


def _expose(rad, exposure_us, gain_db):
    img = rad * (exposure_us / 1000.0) * 10.0 ** (gain_db / 20.0)
    return np.clip(img, 0, 255).astype(np.uint8)


def _close_loop(servo, rad, roi, frames=600):
    for _ in range(frames):
        frame = _expose(rad, servo.exposure_us, servo.gain_db)
        b, clip, region = servo_region_stats(frame, roi)
        servo.feed(b, clip, region)
        if servo.done:
            break
    return servo.result()


@pytest.mark.parametrize("windows,roi,clip_min", [
    ((0, 0, 320, 12), _BAND, 0.0),       # 6 % of the frame clipped, above the band
    ((0, 80, 320, 5), _BAND, 6.0),       # 6.25 % of the ROI clipped, inside the band
    ((0, 0, 320, 12), None, 5.9),        # no ROI: 6 % of the frame clipped
])
def test_servo_dark_wall_with_clipped_windows_raises_until_the_wall_median_is_on_target(
        windows, roi, clip_min):
    # The field end state: 0.2 ms / 0.5 dB, windows saturating even there, the wall black.
    rad = _scene(wall_rad=20.0, windows=windows)
    s = ExposureServo(exposure_us=200.0, gain_db=0.5)
    res = _close_loop(s, rad, roi)
    assert res.converged, res.summary_line()
    assert s.exposure_us > 2000.0                       # raised, not driven to the 0.2 ms floor
    assert abs(res.brightness - 70.0) <= 12.0           # the wall median is on target
    assert res.clip_pct >= clip_min                     # ...with the windows still clipping
    assert ("ROI 320x80" if roi else "frame 320x200") in res.summary_line()


def test_servo_uniformly_too_bright_backs_off_into_the_band():
    rad = _scene(wall_rad=300.0)                         # all clipped at 20 ms / 12 dB
    s = ExposureServo(exposure_us=20000.0, gain_db=12.0)
    res = _close_loop(s, rad, _BAND)
    assert res.converged, res.summary_line()
    assert s.gain_db == 0.0 and s.exposure_us < 20000.0  # gain off first, then exposure
    assert abs(res.brightness - 70.0) <= 12.0


def test_servo_bright_lamp_over_a_lit_wall_steps_down_once_and_stays_in_band():
    # Wall median just above target, 20 % of the band a saturated lamp: one gentle step,
    # converged in band -- not the old ratchet down to the minimum.
    rad = _scene(wall_rad=7.4, windows=(0, 80, 320, 16))  # band median ~77, 20 % of it a lamp
    s = ExposureServo(exposure_us=10000.0, gain_db=0.0)
    res = _close_loop(s, rad, _BAND)
    assert res.converged and res.steps == 1, res.summary_line()
    assert 70.0 - 12.0 <= res.brightness < 70.0
    assert s.exposure_us > 8000.0


def test_servo_night_dark_scene_goes_to_the_exposure_cap_then_the_camera_gain_max():
    rad = _scene(wall_rad=0.05, windows=(0, 80, 20, 4))  # a few belts / lamps saturate
    s = ExposureServo(exposure_us=5000.0, gain_db=0.0, gain_max_db=30.0)
    res = _close_loop(s, rad, _BAND)
    assert s.exposure_us == pytest.approx(s.exposure_cap_us)      # 25 ms blur budget
    assert s.gain_db == pytest.approx(30.0)                       # the camera's real max
    assert not res.converged and "IR" in res.note


def test_servo_region_stats_roi_vs_frame():
    frame = np.full((100, 200), 200, np.uint8)           # bright surround
    frame[40:60, :] = 30                                 # the dark wall band
    frame[40:42, :100] = 255                             # 5 % of the band clipped
    b, clip, label = servo_region_stats(frame, (0, 40, 200, 20))
    assert b == pytest.approx(30.0) and clip == pytest.approx(5.0) and label == "ROI 200x20"
    b, clip, label = servo_region_stats(frame, None)     # no ROI -> the frame's median
    assert b == pytest.approx(200.0) and clip == pytest.approx(1.0) and label == "frame 200x100"
    # an ROI covering the whole frame / off the frame is the frame
    assert servo_region_stats(frame, (0, 0, 200, 100))[2] == "frame 200x100"
    assert servo_region_stats(frame, (500, 500, 10, 10))[2] == "frame 200x100"
    # BGR and non-uint8 input
    bgr = np.dstack([frame] * 3)
    assert servo_region_stats(bgr, (0, 40, 200, 20))[0] == pytest.approx(30.0)
    assert servo_region_stats(frame.astype(np.float32), (0, 40, 200, 20))[:2] == \
        pytest.approx((30.0, 5.0))


def test_servo_summary_says_what_it_measured():
    s = ExposureServo(exposure_us=10000.0, gain_db=6.0)
    for _ in range(40):
        s.feed(70.0, 3.2, "ROI 1322x412")
    line = s.result().summary_line()
    assert "ROI 1322x412 median 70" in line and "clip 3.2%" in line and "converged" in line


def test_servo_converges_in_band():
    s = ExposureServo(exposure_us=10000.0, gain_db=6.0)
    cmds = _drive(s, brightness=70.0)
    assert cmds == []
    assert s.done and s.result().converged


def test_servo_too_bright_drops_gain_then_exposure():
    s = ExposureServo(exposure_us=10000.0, gain_db=3.0)
    cmds = _drive(s, brightness=150.0, max_frames=200)
    kinds = [k for k, _ in cmds]
    assert kinds[0] == "gain"
    assert "exposure" in kinds[1:]
    assert s.exposure_us < 10000.0


# --------------------------------------------------------------------------
# U3 — gamma / CLAHE seeds
# --------------------------------------------------------------------------
def test_seed_gamma_mapping():
    # Upper clamp relaxed 2.2 -> 4.0 for near-black IR (2026-06-16, corpus sweep).
    assert seed_gamma(5.0) == pytest.approx(4.0)      # near-black → clamp high (relaxed)
    assert seed_gamma(110.0) == pytest.approx(1.0, abs=0.05)
    assert seed_gamma(200.0) == pytest.approx(0.8)    # bright → clamp low
    assert seed_gamma(30.0) > seed_gamma(60.0)        # darker → stronger gamma


def test_seed_clahe_noise_aware():
    assert seed_clahe(1.0) == pytest.approx(2.5)
    assert seed_clahe(6.0) == pytest.approx(1.5)


# --------------------------------------------------------------------------
# U3 — scene report card
# --------------------------------------------------------------------------
def test_scene_report_stats_uniform_frame():
    gray = np.full((100, 160), 128, dtype=np.uint8)
    s = scene_report_stats(gray)
    assert s["uniformity"] == pytest.approx(1.0, abs=0.01)
    assert s["clip_high"] == 0.0 and s["clip_low"] == 0.0
    assert s["focus"] == pytest.approx(0.0, abs=1e-6)  # flat frame = no edges


def test_scene_report_stats_dark_corner_and_clip():
    gray = np.full((100, 160), 128, dtype=np.uint8)
    gray[80:, :40] = 2          # dark bottom-left corner
    gray[:10, 120:] = 255       # clipped top-right strip
    s = scene_report_stats(gray, grid=(4, 4))
    assert s["clip_high"] > 0.0
    assert s["uniformity"] < 0.5
    col, row = s["dark_tile"]
    assert col == 0 and row == 3  # bottom-left tile of the 4x4 grid


def test_compute_fills_report_card():
    cal = SceneCalibrator(window_frames=30)
    cal.start()
    gray = np.full((54, 96), 128, dtype=np.uint8)
    for i in range(30):
        cal.feed(gray, [], 30.0, float(i), report_frame=gray)
    res = cal.compute()
    assert res.report_ok
    assert res.uniformity == pytest.approx(1.0, abs=0.01)
    assert res.clahe_value is not None
