#!/usr/bin/env python3
"""Per internal tracker track: lifetime, max hits, frames reported ('ok'), first-ok delay, travel (x own h),
and the YOLO confidence of the dets it sat on (nearest det within 0.3 h on frames it was fed).
track_profile.py timeline.json [--min-hits 3]"""
import json
import math
import sys
from pathlib import Path

FPS = 20.0
rows = sorted(json.loads(Path(sys.argv[1]).read_text()), key=lambda r: r["frame"])
min_hits = int(sys.argv[sys.argv.index("--min-hits") + 1]) if "--min-hits" in sys.argv else 3
tr = {}
for r in rows:
    refs = [x for x in (r.get("ref") or []) if isinstance(x, dict)]
    for t in r.get("int") or []:
        d = tr.setdefault(t["id"], {"first": r["frame"], "last": r["frame"], "hits": 0, "ok": 0, "first_ok": None,
                                    "pts": [], "h": [], "conf": [], "fss0": 0})
        d["last"] = r["frame"]
        d["hits"] = max(d["hits"], t["hits"])
        if t.get("emit") == "ok":
            d["ok"] += 1
            if d["first_ok"] is None:
                d["first_ok"] = r["frame"]
        d["pts"].append(t["p"])
        d["h"].append(t["h"])
        if t["tsu"] == 0 and t["fss"] == 0:
            d["fss0"] += 1
            best = None
            for x in refs:
                dist = math.hypot(x["c"][0] - t["p"][0], x["c"][1] - t["p"][1])
                if dist < 0.3 * max(20, t["h"]) and (best is None or dist < best[0]):
                    best = (dist, x.get("conf") or 0)
            if best:
                d["conf"].append(best[1])
print(f"{'id':>5s} {'from':>7s} {'to':>7s} {'hits':>5s} {'skel':>5s} {'ok':>5s} {'1st ok':>7s} {'h':>5s} "
      f"{'travel/h':>8s} {'conf med':>8s} {'conf>=.5':>8s}")
for i, d in sorted(tr.items()):
    if d["hits"] < min_hits:
        continue
    xs = [p[0] for p in d["pts"]]
    ys = [p[1] for p in d["pts"]]
    h = sorted(d["h"])[len(d["h"]) // 2]
    trav = math.hypot(max(xs) - min(xs), max(ys) - min(ys)) / max(20, h)
    cs = sorted(d["conf"])
    cm = cs[len(cs) // 2] if cs else 0
    c5 = sum(1 for c in cs if c >= 0.5) / len(cs) if cs else 0
    fo = f"{(d['first_ok'] - d['first']) / FPS:7.2f}" if d["first_ok"] is not None else "      -"
    print(f"{i:5d} {d['first'] / FPS:7.2f} {d['last'] / FPS:7.2f} {d['hits']:5d} {d['fss0']:5d} {d['ok']:5d} {fo} "
          f"{h:5.0f} {trav:8.2f} {cm:8.2f} {c5:8.2f}")
