#!/usr/bin/env python3
"""Start-of-take decomposition from the RAW YOLO detections (before the size gate; the `raw` rows of a
primed_replay run, taken from <take>_<rawvariant>.json) and the first emitted point of each variant.

  summary2.py out_dir rawvariant variant[,variant...]

t_box  = first raw box conf >= 0.5 (any keypoints: the edge slivers / saturated halves count)
t_skel = first raw box conf >= 0.5 with a confident torso (shoulder + hip >= 0.5): a confident skeleton
t_dense= first frame from which >= 10 of the next 20 frames hold a confident skeleton
lat    = t_emit - t_skel (the entry latency the task asks for)"""
import json
import sys
from pathlib import Path

FPS = 20.0
out = Path(sys.argv[1])
rawv = sys.argv[2]
variants = sys.argv[3].split(",")
takes = ["s2a", "s2c", "s3", "s4", "s6", "s7"]


def skel(r):
    return any(d[0] >= 0.5 and d[5] for d in (r.get("raw") or []))


def first_emit(p):
    rows = sorted(json.loads(p.read_text()), key=lambda r: r["frame"])
    return next((r["frame"] for r in rows if (r.get("emitted") or {}).get("tracks")), None)


hdr = f"{'take':5s} {'box.5':>6s} {'skel':>6s} {'dense':>6s} | " + " | ".join(f"{v:>14s}" for v in variants)
print(hdr)
print(f"{'':5s} {'':>6s} {'':>6s} {'':>6s} | " + " | ".join(f"{'emit  (lat)':>14s}" for _ in variants))
lat = {v: [] for v in variants}
for t in takes:
    p = out / f"{t}_{rawv}.json"
    if not p.exists():
        continue
    rows = sorted(json.loads(p.read_text()), key=lambda r: r["frame"])
    tb = next((r["frame"] for r in rows if any(d[0] >= 0.5 for d in (r.get("raw") or []))), None)
    ts = next((r["frame"] for r in rows if skel(r)), None)
    td = None
    for i in range(len(rows) - 20):
        if sum(1 for r in rows[i:i + 20] if skel(r)) >= 10:
            td = next(r["frame"] for r in rows[i:i + 20] if skel(r))
            break
    cells = []
    for v in variants:
        q = out / f"{t}_{v}.json"
        if not q.exists():
            cells.append(f"{'-':>14s}")
            continue
        fe = first_emit(q)
        l = (fe - ts) / FPS if fe is not None and ts is not None else None
        if l is not None:
            lat[v].append(l)
        cells.append(f"{fe / FPS if fe is not None else -1:6.2f} ({l if l is not None else -9:+5.2f})")
    f = lambda x: f"{x / FPS:6.2f}" if x is not None else "     -"
    print(f"{t:5s} {f(tb)} {f(ts)} {f(td)} | " + " | ".join(cells))
print(f"{'mean lat':26s} | " + " | ".join(f"{(sum(lat[v]) / len(lat[v]) if lat[v] else -9):>14.2f}" for v in variants))
