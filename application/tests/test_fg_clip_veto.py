"""F1 (Plan Cordée rev 2): the clip-aware foreground."""
import numpy as np
from core.foreground import CleanPlate, FgParams, ForegroundDetector

W, H = 400, 800          # full frame (portrait), downscaled 4x -> 100 x 200


def _plate(oval=True, level=120.0, sigma=0.5):
    p = np.full((H // 4, W // 4), level, np.float32)
    if oval:
        yy, xx = np.mgrid[0:H // 4, 0:W // 4]
        p[((xx - 50) / 30.0) ** 2 + ((yy - 100) / 50.0) ** 2 <= 1.0] = 255.0
    return CleanPlate(plate=p, sigma=sigma, frame_size=(W, H))


def _frame(plate, gain=1.0, noise=0.0, seed=0, patch=None):
    """A full-size frame: the plate x gain (clipped at 255), noise per full pixel, an optional dark patch."""
    rng = np.random.default_rng(seed)
    radiance = np.where(plate.plate >= 250, 400.0, plate.plate)     # the lit oval is brighter than white
    full = np.kron(radiance, np.ones((4, 4), np.float32)) * gain
    full = np.minimum(full, 255.0)
    if noise:
        full = full + rng.normal(0, noise, full.shape)
    if patch is not None:
        x0, y0, x1, y1, val = patch
        full[y0:y1, x0:x1] = val
    return np.clip(full, 0, 255).astype(np.uint8)


def test_darker_exposure_leaves_the_clipped_oval_alone():
    pl = _plate()
    fr = _frame(pl, gain=0.9, noise=6.0)          # 10 % less light: the oval stays clipped
    new = ForegroundDetector(pl, FgParams()).process(fr, 0, 0, (W, H))
    old = ForegroundDetector(pl, FgParams(clip_aware=False)).process(fr, 0, 0, (W, H))
    assert new.valid and new.fg_ratio < 0.002
    assert not old.valid or old.fg_ratio > 0.05     # the shipped rule flags the oval (or calls the plate stale)


def test_threshold_follows_the_frame_noise():
    pl = _plate(oval=False)
    det = ForegroundDetector(pl, FgParams())
    det.process(_frame(pl, noise=8.0), 0, 0, (W, H))       # 8 DN per full pixel ~ 2 DN downscaled
    noisy = det.last_thr
    det.process(_frame(pl, noise=2.0), 0, 0, (W, H))
    quiet = det.last_thr
    assert noisy > quiet >= FgParams().thr_frame_floor
    assert 14 <= noisy <= 30


def test_a_dancer_is_seen_on_the_wall_and_in_the_oval():
    pl = _plate()
    det = ForegroundDetector(pl, FgParams())
    wall = det.process(_frame(pl, noise=6.0, patch=(40, 600, 80, 720, 40)), 0, 0, (W, H))
    assert wall.valid and wall.mask[150:180, 10:20].mean() > 0.9
    oval = det.process(_frame(pl, noise=6.0, patch=(180, 380, 220, 500, 200)), 0, 0, (W, H))
    assert oval.valid and oval.mask[95:125, 45:55].mean() > 0.9          # 55 under white in the oval
    assert oval.mask[150:180, 10:20].sum() == 0
