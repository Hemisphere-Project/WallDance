#!/usr/bin/env python3
"""Per-second overview of a timeline: YOLO dets (count, best conf, median height, x/y), internal tracks,
reported and emitted.  heights.py timeline.json [from_s] [to_s]"""
import json
import sys
from pathlib import Path

FPS = 20.0
rows = sorted(json.loads(Path(sys.argv[1]).read_text()), key=lambda r: r["frame"])
lo = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
hi = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0
for s in range(int(lo), int(hi)):
    w = [r for r in rows if s * FPS <= r["frame"] < (s + 1) * FPS]
    if not w:
        break
    dets = [x for r in w for x in (r.get("ref") or []) if isinstance(x, dict)]
    nd = sum(1 for r in w if r.get("ref"))
    n5 = sum(1 for r in w if any((x.get("conf") or 0) >= 0.5 for x in (r.get("ref") or []) if isinstance(x, dict)))
    hs = sorted(x["h"] for x in dets if x.get("h"))
    cs = [x["c"] for x in dets]
    med = hs[len(hs) // 2] if hs else 0
    xy = f"{cs[len(cs)//2][0]:6.0f},{cs[len(cs)//2][1]:6.0f}" if cs else "      -      "
    ids = sorted({t["id"] for r in w for t in (r.get("int") or [])})
    rep = sum(1 for r in w if r.get("tracks"))
    em = sum(1 for r in w if (r.get("emitted") or {}).get("tracks"))
    print(f"{s:4d}s det {nd:2d}/20 >=.5 {n5:2d}  h~{med:5.0f} at {xy}  int ids {len(ids):2d}  rep {rep:2d}  emit {em:2d}")
