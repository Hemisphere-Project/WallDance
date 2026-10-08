#!/usr/bin/env python3
"""Entry episodes in a timeline (replay --quality --internal rows): stretches where YOLO sees a person
confidently (det conf >= --conf) that no emitted point covers (no emitted point within --tol x h).
An episode = a run of such frames, gaps <= --gap frames bridged, >= --min frames long.  Each episode
ends 'emitted' (a point appears on that person) or 'gone' (the uncovered dets stop).  The latency of an
entry = first uncovered confident det -> first emitted point on it.

  entries.py [--conf 0.5] [--tol 0.75] [--min 10] [--gap 10] timeline.json [...]
"""
import argparse
import json
import math
from pathlib import Path

FPS = 20.0


def uncovered(r, conf, tol):
    em = [(e.get("centroid") or [e["bbox"][0] + e["bbox"][2] / 2, e["bbox"][1] + e["bbox"][3] / 2])
          for e in ((r.get("emitted") or {}).get("tracks") or [])]
    out = []
    for x in r.get("ref") or []:
        if not isinstance(x, dict) or (x.get("conf") or 0) < conf:
            continue
        h = max(20.0, float(x.get("h") or 100))
        if all(math.hypot(x["c"][0] - c[0], x["c"][1] - c[1]) > tol * h for c in em):
            out.append(x)
    return out


def episodes(rows, conf=0.5, tol=0.75, min_len=10, gap=10):
    u = [bool(uncovered(r, conf, tol)) for r in rows]
    eps, start, last = [], None, None
    for i, v in enumerate(u + [False] * (gap + 1)):
        if v:
            if start is None:
                start = i
            last = i
        elif start is not None and i - last > gap:
            if last - start + 1 >= min_len:
                eps.append((start, last))
            start = None
    out = []
    for s, e in eps:
        # how did it end: is the frame after the episode emitting on the person?
        nxt = e + 1
        em_after = nxt < len(rows) and bool((rows[nxt].get("emitted") or {}).get("tracks"))
        n_em_before = len(((rows[s - 1].get("emitted") or {}).get("tracks") or [])) if s > 0 else 0
        dets = sum(1 for i in range(s, e + 1) if u[i])
        hs = [x["h"] for i in range(s, min(e + 1, s + 20)) for x in uncovered(rows[i], conf, tol)]
        out.append({"start": rows[s]["frame"], "end": rows[e]["frame"], "dur_s": round((e - s + 1) / FPS, 2),
                    "duty": round(dets / (e - s + 1), 2), "ended": "emitted" if em_after else "gone",
                    "n_emitted_before": n_em_before, "h": round(sorted(hs)[len(hs) // 2]) if hs else 0})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("timelines", nargs="+")
    ap.add_argument("--conf", type=float, default=0.5)
    ap.add_argument("--tol", type=float, default=0.75)
    ap.add_argument("--min", type=int, default=10)
    ap.add_argument("--gap", type=int, default=10)
    a = ap.parse_args()
    for p in a.timelines:
        rows = sorted(json.loads(Path(p).read_text()), key=lambda r: r["frame"])
        eps = episodes(rows, a.conf, a.tol, a.min, a.gap)
        print(f"== {Path(p).stem}  ({len(rows)} frames, {len(eps)} episodes)")
        for e in eps:
            print(f"   {e['start'] / FPS:7.2f}s -> {e['end'] / FPS:7.2f}s  {e['dur_s']:5.2f}s  duty {e['duty']:.2f}  "
                  f"h~{e['h']:4d}  emitted before {e['n_emitted_before']}  ended {e['ended']}")
