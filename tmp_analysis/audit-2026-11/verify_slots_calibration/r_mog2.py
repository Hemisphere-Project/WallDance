"""motion-calibration#5: does the Calibrate sweep's false-positive rate predict production's?
Synthetic empty wall: fixed texture + iid temporal Gaussian noise. CPU only."""
import sys, time
sys.dont_write_bytecode = True
import numpy as np, cv2
from core.calibration import SceneCalibrator
from core.motion_detector import MotionDetector
from core.config import AUTOCAL_FP_GRID, MOTION_LOWLIGHT_LUMA_THRESHOLD

H, W = 560, 1320
rng = np.random.default_rng(3)
tex = cv2.GaussianBlur(rng.normal(0, 1, (H, W)).astype(np.float32), (0, 0), 6)
tex = tex / tex.std()

def frame(mean, sigma):
    f = mean + 0.15 * mean * tex + rng.normal(0, sigma, (H, W)).astype(np.float32)
    return np.clip(f, 0, 255).astype(np.uint8)

def tile_stats(mask, level):
    fg = (mask == 255) if level == 255 else (mask >= 127)
    fg = fg.astype(np.float32)
    tiles = cv2.resize(fg, AUTOCAL_FP_GRID, interpolation=cv2.INTER_AREA)
    return float(np.median(tiles)), float(fg.mean())

print(f"low-light switch: motion-gray mean < {MOTION_LOWLIGHT_LUMA_THRESHOLD}")
print("scene (mean, sigma) | sweep pick var@scale, sweep FP(median tile, ==255) | production with that pick: "
      "frames-> FP median-tile ==255 / mean ==255 / mean >=127 | var in force")
for mean, sigma in ((90, 2.0), (90, 6.0), (90, 10.0), (40, 2.0), (40, 6.0), (40, 10.0)):
    cal = SceneCalibrator()
    cal.start()
    while not cal.ready:
        cal.feed(frame(mean, sigma), [], 20.0, time.time())
    res = cal.compute()
    md = MotionDetector()
    md.set_scale(res.mog2_scale)
    md.set_var_threshold(res.var_threshold)
    rows = []
    acc = {}
    for i in range(1, 1201):
        md.feed(frame(mean, sigma))
        if i in (45, 90, 300, 1200) or 1150 < i <= 1200:
            acc[i] = (tile_stats(md._fg_mask, 255), tile_stats(md._fg_mask, 127))
    def fmt(i):
        (mt, m255), (_mt2, m127) = acc[i]
        return f"{i}f: {100*mt:.2f}% / {100*m255:.2f}% / {100*m127:.2f}%"
    low = md.last_brightness < MOTION_LOWLIGHT_LUMA_THRESHOLD
    print(f"mean {mean} sigma {sigma:>4} | var {res.var_threshold:g}@{res.mog2_scale:g} sat={res.var_saturated} "
          f"FP {100*res.var_fp_rate:.2f}% noise_sigma={res.noise_sigma:.2f} | "
          + "  ".join(fmt(i) for i in (45, 90, 300, 1200))
          + f" | {'x2 low-light: ' + str(res.var_threshold * 2) if low else 'as calibrated'}")
