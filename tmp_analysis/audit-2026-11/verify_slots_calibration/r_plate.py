"""identity-output#3: a still dancer nobody protects (fg-held slot, no YOLO skeleton for > 20 frames) is blended
into the live plate.  ForegroundDetector with default FgParams, 20 fps, update every 10 frames, alpha 0.02."""
import sys
sys.dont_write_bytecode = True
import numpy as np
from core.foreground import CleanPlate, ForegroundDetector

H, W = 600, 1400
rng = np.random.default_rng(1)
wall = np.clip(40 + 6 * rng.normal(0, 1, (H // 20, W // 20)).repeat(20, 0).repeat(20, 1), 5, 250)

def frame(contrast, with_dancer=True, sigma=1.0):
    f = wall.copy()
    if with_dancer:
        f[225:375, 670:730] += contrast          # a 60 x 150 px body, brighter than the wall
    return np.clip(f + rng.normal(0, sigma, f.shape), 0, 255).astype(np.uint8)

plate = CleanPlate.from_frames([frame(0, False) for _ in range(40)], source="synthetic")
print(f"plate sigma {plate.sigma:.2f} DN (downscaled) -> threshold max(2.0, 4 x sigma) = {max(2.0, 4 * plate.sigma):.2f} DN")
for contrast in (6, 12, 30):
    for protected in (False, True):
        det = ForegroundDetector(plate)
        veto_at = gone_at = None
        s0 = None
        for i in range(1, 20 * 120 + 1):          # up to 120 s
            fg = det.process(frame(contrast), 0, 0, (W, H))
            sup = fg.support(700.0, 300.0, 150.0)
            if s0 is None: s0 = sup
            det.update_plate([(660, 210, 740, 390)] if protected else [])
            if veto_at is None and sup < 0.10: veto_at = i / 20.0
            big = [b for b in fg.blobs if b.area >= 0.02 * 150 * 150]
            if gone_at is None and not big: gone_at = i / 20.0
            if veto_at is not None and gone_at is not None: break
        print(f"  dancer +{contrast:>2} DN, {'protected box' if protected else 'unprotected  '}: support at start {s0:.2f}; "
              f"no blob >= fg_min_area (hold ends) after {gone_at} s; support < fg_veto 0.10 after {veto_at} s")
