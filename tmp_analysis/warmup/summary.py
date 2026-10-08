#!/usr/bin/env python3
"""Start-of-take table over variants: summary.py out_dir variant[,variant...]

Per take x variant: first YOLO det >= 0.5 (s), first emitted point (s), the entry latency between them,
the tracks born before the first report (churn), and the person height at the first emitted frame."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decompose import analyse  # noqa: E402

FPS = 20.0
out = Path(sys.argv[1])
variants = sys.argv[2].split(",")
takes = ["s2a", "s2c", "s3", "s4", "s6", "s7"]
print(f"{'take':5s} " + " ".join(f"{v:>34s}" for v in variants))
print(f"{'':5s} " + " ".join(f"{'det.5 -> emit (lat) born ph':>34s}" for _ in variants))
lat = {v: [] for v in variants}
for t in takes:
    cells = []
    for v in variants:
        p = out / f"{t}_{v}.json"
        if not p.exists():
            cells.append(f"{'-':>34s}")
            continue
        a = analyse(p)
        rows = a["rows"]
        c50, em, rep = a["c50"], a["emit"], a["rep"]
        born = len({tr["id"] for r in rows if c50 is not None and r["frame"] <= (rep or 10 ** 9)
                    for tr in (r.get("int") or [])})
        ph = next((r.get("ph") for r in rows if r["frame"] == em), None) if em is not None else None
        l = (em - c50) / FPS if (em is not None and c50 is not None) else None
        if l is not None:
            lat[v].append(l)
        cells.append(f"{c50 / FPS if c50 is not None else -1:6.2f} -> {em / FPS if em is not None else -1:6.2f} "
                     f"({l if l is not None else -1:5.2f}) {born:3d} {ph if ph else '-':>5}")
    print(f"{t:5s} " + " ".join(f"{c:>34s}" for c in cells))
print(f"{'mean':5s} " + " ".join(f"{(sum(lat[v]) / len(lat[v]) if lat[v] else -1):>34.2f}" for v in variants))
