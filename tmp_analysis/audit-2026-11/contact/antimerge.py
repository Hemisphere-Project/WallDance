"""One wide/tall YOLO box over an embracing or lifting pair: the tracker's anti-merge rule
(tracker.py:1364-1392, consumed at 2037-2039).  Logic tier (no images)."""
import io, contextlib, sys
import numpy as np
import contact_repro as R
from contact_repro import Rig, det, union_det, prelude, move, sway, PH, YC, FPS
from sweep import stats, line

def run(scale_w, scale_h, d_h=0.30, contact_s=5.0, images=False):
    rig = Rig(images=images)
    a, b = prelude(rig)
    xc = 640.0
    ac, bc = xc - d_h * PH / 2, xc + d_h * PH / 2
    move(rig, a, b, ac, bc, 20, "approach")
    for i in range(int(contact_s * FPS)):
        s = sway(i)
        ax, bx = ac + s, bc + s
        k, c, bb = union_det(ax, bx, YC, rig.rng)
        cx, cy = bb[0] + bb[2] / 2, bb[1] + bb[3] / 2
        w, h = bb[2] * scale_w, bb[3] * scale_h
        bb = np.array([cx - w / 2, cy - h / 2, w, h])
        rig.step([(k, c, bb)], {"A": (ax, YC), "B": (bx, YC)}, bodies=[("B", bx, YC), ("A", ax, YC)], phase="contact")
    move(rig, ac, bc, 440.0, 840.0, 40, "separate")
    for i in range(60):
        s = sway(i, amp=0.15 * PH)
        rig.step([det("A", 440 + s, YC, rig.rng), det("B", 840 - s, YC, rig.rng)],
                 {"A": (440 + s, YC), "B": (840 - s, YC)}, phase="after")
    return rig, bb[2] * bb[3] / PH / PH

for sw, sh in ((1.0, 1.0), (1.0, 1.25), (1.1, 1.4), (1.3, 1.5)):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rig, area = run(sw, sh)
    n_am = sum(1 for _f, e, _d in rig.events if e == "ANTI_MERGE")
    zero = R.longest([len(r["em"]) == 0 for r in rig.rows if r["phase"] != "prelude"]) / FPS
    one = R.longest([len(r["em"]) <= 1 for r in rig.rows if r["phase"] != "prelude"]) / FPS
    line(f"union box {area:.2f} h2 (single body 0.38 / 0.52 h2), ANTI_MERGE x{n_am}, 0 pts {zero:.2f}s, <=1 pt {one:.2f}s", rig)
