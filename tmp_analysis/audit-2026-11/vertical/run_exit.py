#!/usr/bin/env python3
"""Exits: bottom / top / side at 0.3 h/s and faster, after 6 s on the wall (the slot earns its reputation)."""
import sys
import io
import contextlib
from vsim import *

sc = Scene()
rx, ry, rw, rh = sc.roi
H = sc.H
cx_mid = rx + rw / 2
T0 = 4.0               # s on the wall before leaving
Y0 = ry + 1.7 * H      # body top while on the wall (mid wall)


def wander(t):         # on the wall: a slow sway, 0.6 h peak to peak (earns travel >= 0.25 h)
    return cx_mid + 0.3 * H * np.sin(2 * np.pi * t / 4.0)


def bottom_exit(t, v):
    if t < T0:
        return (wander(t), Y0)
    return (wander(T0), Y0 + v * (t - T0))


def top_exit(t, v):
    if t < T0:
        return (wander(t), Y0)
    return (wander(T0), Y0 - v * (t - T0))


def side_exit(t, v):
    x0 = rx + 2.0 * H
    if t < T0:
        return (x0 + 0.3 * H * np.sin(2 * np.pi * t / 4.0), Y0)
    return (x0 - v * (t - T0), Y0)


def hold_detail(rows):
    """slot internals on the frame the slot starts coasting"""
    prev = None
    for r in rows:
        if r["slots"] and r["slots"][0]["st"] == "coasting" and (prev is None or prev["slots"][0]["st"] != "coasting"):
            s = r["slots"][0]
            return f"coast starts t={r['t']:.2f}s hold_s={s['hold']} rep={s['rep']} travel={s['travel']}h vel={s['vel']}h/s static={s['static']}"
        prev = r
    return "never coasted"


N = 480
CASES = (
    ("bottom 0.3h/s", lambda t: bottom_exit(t, 0.3 * H), {}),
    ("top    0.3h/s", lambda t: top_exit(t, 0.3 * H), {}),
    ("side   0.3h/s", lambda t: side_exit(t, 0.3 * H), {}),
    ("bottom 1.0h/s", lambda t: bottom_exit(t, 1.0 * H), {}),
    ("top    1.0h/s", lambda t: top_exit(t, 1.0 * H), {}),
    ("top    2.0h/s", lambda t: top_exit(t, 2.0 * H), {}),
    ("side   1.0h/s", lambda t: side_exit(t, 1.0 * H), {}),
    ("top    0.3h/s vis_min .5", lambda t: top_exit(t, 0.3 * H), dict(vis_min=0.5)),
)
SEL = [a for a in sys.argv[1:] if a in ("mp", "m", "y")] or ["mp", "m", "y"]
import time
for key, motion, plate in (("mp", True, True), ("m", True, False), ("y", False, False)):
    if key not in SEL:
        continue
    t_wall = time.time()
    print(f"\n===== EXIT, motion {'ON' if motion else 'OFF'}, empty-wall plate {'ON' if plate else 'OFF'} =====")
    for label, pos, kw in CASES:
        with contextlib.redirect_stdout(io.StringIO()):
            rows, proc = run(sc, [Body(pos, **kw)], N, motion=motion, plate=plate)
        exit_report(sc, rows, label=label)
        print("      ", hold_detail(rows), flush=True)
        if "-v" in sys.argv and label.startswith("top    0.3h/s") and not kw:
            ld = last(rows, lambda r: r["n_raw"] > 0)
            timeline([r for r in rows if r["f"] >= ld["f"] - 30], every=5, upto=ld["t"] + 3.5)
    print(f"  ({time.time() - t_wall:.0f} s wall)", flush=True)
