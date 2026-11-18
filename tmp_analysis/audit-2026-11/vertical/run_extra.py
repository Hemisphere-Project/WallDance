#!/usr/bin/env python3
"""G2: a false figure appears near the exit point after a top / side exit (id memory).
H2: the size gate + keypoint splice when a truncated entrant shares a frame with a full dancer.
J : three dancers on the wall with the default cap (max_dancers=2) and with 3, third enters by the top."""
import sys
import io
import glob
import collections
import contextlib
from vsim import *

sc = Scene()
rx, ry, rw, rh = sc.roi
H = sc.H
V = 0.3 * H
cx_mid = rx + rw / 2
SEL = sys.argv[1:] or ["G2", "H2", "J"]


def quiet_run(*a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return run(*a, **k)


def log_events(proc, names, f_min=0):
    out = collections.Counter()
    rows = []
    for fn in glob.glob(proc.tracker.logger._session_dir + "/tracking_events*.jsonl"):
        for line in open(fn):
            e = json.loads(line)
            if e.get("event") in names and e.get("frame", 0) >= f_min:
                out[e["event"]] += 1
                rows.append((e["frame"], e["event"], e.get("data")))
    return out, rows


if "G2" in SEL:
    print("\n===== G2. dancer leaves (1.0 h/s); later a STATIC full-size figure (a false person) shows up 1.5 h from the exit point =====")
    T_EXIT = 4.0
    for kind in ("top", "side"):
        for gap in (3.0, 8.0, 13.0, 18.0):
            if kind == "top":
                y_wall = ry + 1.0 * H
                t_out = T_EXIT + (y_wall + H - ry) / (1.0 * H)
                a = lambda t: ((cx_mid + 0.3 * H * np.sin(2 * np.pi * t / 4.0), y_wall) if t < T_EXIT
                               else ((cx_mid, y_wall - 1.0 * H * (t - T_EXIT)) if t < t_out else None))
                gpos = (cx_mid + 1.5 * H, ry + 0.02 * H)           # fully inside, just under the top border
            else:
                x0 = rx + 1.0 * H
                t_out = T_EXIT + (x0 + BOX_W * H / 2 - rx) / (1.0 * H)
                a = lambda t: ((x0 + 0.3 * H * np.sin(2 * np.pi * t / 4.0), ry + 1.5 * H) if t < T_EXIT
                               else ((x0 - 1.0 * H * (t - T_EXIT), ry + 1.5 * H) if t < t_out else None))
                gpos = (rx + 0.2 * H, ry + 1.5 * H + 1.5 * H)      # fully inside, at the left border, 1.5 h lower
            g = lambda t, gap=gap, t_out=t_out, gpos=gpos: gpos if t >= t_out + gap else None
            rows, proc = quiet_run(sc, [Body(a, name="A"), Body(g, name="ghost")], int((t_out + gap + 4) * FPS),
                                   motion=True, plate=False)
            t_in = t_out + gap
            sub = [r for r in rows if r["t"] >= t_in]
            fe = first(sub, lambda r: len(r["out"]) > 0)
            cnt, ev = log_events(proc, ("RESURRECT", "NEW_TRACK", "DORMANT", "DORMANT_EXPIRED"))
            sev = [(round(r["t"] - t_in, 2), e["ev"], e.get("key")) for r in sub for e in r["ev"]]
            res = [(f, d) for f, n, d in ev if n == "RESURRECT"]
            print(f"[{kind} exit, figure appears {gap:.0f}s after the body is out] OSC point on the figure after "
                  f"+{None if fe is None else round(fe['t'] - t_in, 2)}s | tracker {dict(cnt)} resurrect={res[:1]} | slot events {sev}", flush=True)

if "H2" in SEL:
    print("\n===== H2. B full on the wall, A enters by the top 1.2 h aside; YOLO finds A from 15 % visible, A's keypoints more confident =====")
    bpos = lambda t: (cx_mid, ry + 0.10 * H)
    apos = lambda t: None if t < 5.0 else (cx_mid + 1.2 * H, min(ry - H + V * (t - 5.0), ry + 0.10 * H))
    rows, proc = quiet_run(sc, [Body(bpos, name="B"), Body(apos, name="A", vis_min=0.15, kconf=0.95)], int(12 * FPS),
                           motion=True, plate=False, jitter=0.0)
    sub = [r for r in rows if r["t"] >= 5.0]
    dropped = [r for r in sub if r["n_raw"] == 2 and r["n_det"] == 1]
    by = [next((o["y"] for o in r["out"] if o["id"] == 1), None) for r in rows]
    base = np.median([y for r, y in zip(rows, by) if y is not None and 3.0 < r["t"] < 5.0])
    dev = [(r["t"], y - base) for r, y in zip(rows, by) if y is not None and r["t"] >= 5.0]
    worst = max(dev, key=lambda p: abs(p[1]))
    worst_x = None
    print(f"frames where the size gate removed A's box (2 raw dets -> 1): {len(dropped)} "
          f"(A vis {dropped[0]['vis'][1] if dropped else None}..{dropped[-1]['vis'][1] if dropped else None}) | "
          f"B's emitted point moved by up to {worst[1] / H:+.2f} h (y) at t={worst[0]:.2f}s while B did not move | "
          f"ph gate: person_height {rows[-1]['ph']} px -> min box {0.3 * rows[-1]['ph']:.0f} px", flush=True)
    bx = [next((o["x"] for o in r["out"] if o["id"] == 1), None) for r in rows]
    basex = np.median([x for r, x in zip(rows, bx) if x is not None and 3.0 < r["t"] < 5.0])
    devx = [(r["t"], x - basex) for r, x in zip(rows, bx) if x is not None and r["t"] >= 5.0]
    wx = max(devx, key=lambda p: abs(p[1]))
    print(f"   ... and by up to {wx[1] / H:+.2f} h (x) at t={wx[0]:.2f}s", flush=True)

if "J" in SEL:
    print("\n===== J. two dancers on the wall, a third enters by the top 3 h away (0.3 h/s) =====")
    for nd in (2, 3):
        b1 = lambda t: (cx_mid - 3 * H + 0.3 * H * np.sin(2 * np.pi * t / 5.0), ry + 1.5 * H)
        b2 = lambda t: (cx_mid + 3 * H + 0.3 * H * np.sin(2 * np.pi * t / 4.0), ry + 2.0 * H)
        a = lambda t: None if t < 5.0 else (cx_mid, min(ry - H + V * (t - 5.0), ry + 1.5 * H))
        rows, proc = quiet_run(sc, [Body(b1), Body(b2), Body(a)], int(16 * FPS), motion=True, plate=False, max_dancers=nd)
        sub = [r for r in rows if r["t"] >= 5.0]
        f3 = first(sub, lambda r: len(r["out"]) >= 3)
        n3 = sum(1 for r in sub if len(r["out"]) >= 3)
        nfull = sum(1 for r in sub if (r["vis"][2] or 0) >= 0.999)
        print(f"[max_dancers={nd}] 3 OSC points from +{None if f3 is None else round(f3['t'] - 5.0, 2)}s; frames with 3 points {n3}; "
              f"frames with the third dancer fully in {nfull}; ids {sorted({o['id'] for r in sub for o in r['out']})}", flush=True)
