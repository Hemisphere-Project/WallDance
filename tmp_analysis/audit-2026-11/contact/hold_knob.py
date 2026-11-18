"""Operator knob check: Hold (coast_s) raised to 5 s / 10 s, one-box contact 5 s (logic tier)."""
import io, contextlib
import contact_repro as R
from contact_repro import Rig, det, union_det, prelude, move, sway, PH, YC, FPS
from sweep import line

def run(coast, d_h, mode):
    rig = Rig(images=False)
    rig.p.settings.coast_s = coast
    a, b = prelude(rig)
    ac, bc = 640 - d_h * PH / 2, 640 + d_h * PH / 2
    move(rig, a, b, ac, bc, 20, "approach")
    for i in range(100):
        s = sway(i); ax, bx = ac + s, bc + s
        dets = [det("A", ax, YC, rig.rng)] if mode == "front" else [union_det(ax, bx, YC, rig.rng)]
        rig.step(dets, {"A": (ax, YC), "B": (bx, YC)}, phase="contact")
    move(rig, ac, bc, 440.0, 840.0, 40, "separate")
    for i in range(60):
        s = sway(i, amp=0.15 * PH)
        rig.step([det("A", 440 + s, YC, rig.rng), det("B", 840 - s, YC, rig.rng)], {"A": (440 + s, YC), "B": (840 - s, YC)}, phase="after")
    assert rig.p._slots.p.coast_s == coast
    return rig

for coast in (2.0, 5.0, 10.0):
    for d, mode in ((0.30, "front"), (0.20, "front"), (0.30, "union")):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rig = run(coast, d, mode)
        line(f"Hold {coast:.0f} s, one box ({mode}), {d:.2f} h, contact 5 s", rig)
