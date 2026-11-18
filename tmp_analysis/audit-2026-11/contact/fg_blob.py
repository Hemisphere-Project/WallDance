"""Clean-plate foreground with two bodies at contact distance: how many blobs, where is the blob point."""
import os, sys
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
import numpy as np
sys.path.insert(0, "/data/WallDance/application/src")
import contact_repro as R
from core.foreground import CleanPlate, ForegroundDetector
rig = R.Rig(images=False)
empties = [rig._render([]) for _ in range(40)]
det = ForegroundDetector(CleanPlate.from_frames(empties, source="synthetic"))
PH, YC = R.PH, R.YC
for d in (0.0, 0.2, 0.3, 0.4, 0.45, 0.5, 0.6, 0.8):
    ax, bx = 640 - d * PH / 2, 640 + d * PH / 2
    g = rig._render([("B", bx, YC), ("A", ax, YC)])
    fg = det.process(g, 0, 0, (R.W, R.HF))
    big = [b for b in fg.blobs if b.area >= 0.02 * PH * PH]
    ta, tb = R.true_centroid("A", ax, YC), R.true_centroid("B", bx, YC)
    desc = [f"({b.x:.0f},{b.y:.0f}) area {b.area/PH/PH:.2f} h2, {np.hypot(b.x-ta[0], b.y-ta[1])/PH:.2f} h from A, {np.hypot(b.x-tb[0], b.y-tb[1])/PH:.2f} h from B" for b in big]
    print(f"sep {d:.2f} h: valid={fg.valid} fg_ratio={fg.fg_ratio:.4f} body blobs={len(big)} :: " + " | ".join(desc)
          + f" :: support at A {fg.support(ta[0], ta[1], PH):.2f}, at B {fg.support(tb[0], tb[1], PH):.2f}")
