#!/usr/bin/env python3
"""Mid-take (re-)entries from a primed_replay timeline with raw dets: every stretch of >= --gap s with no
emitted point, followed by a raw box >= 0.5 (the person back): first raw box >= 0.5 after the gap (t_box),
first with >= 3 keypoints > 0.5 (t_kp), first emitted point (t_emit); latency = t_emit - t_box.
  reentry.py timeline.json [--gap 1.0] [--from 8]"""
import json
import sys
from pathlib import Path

FPS = 20.0
p = sys.argv[1]
gap = float(sys.argv[sys.argv.index("--gap") + 1]) if "--gap" in sys.argv else 1.0
t0 = float(sys.argv[sys.argv.index("--from") + 1]) if "--from" in sys.argv else 8.0
rows = sorted(json.loads(Path(p).read_text()), key=lambda r: r["frame"])
em = [bool((r.get("emitted") or {}).get("tracks")) for r in rows]
i = int(t0 * FPS)
print(f"== {Path(p).stem}")
while i < len(rows):
    if em[i]:
        i += 1
        continue
    j = i
    while j < len(rows) and not em[j]:
        j += 1
    if (j - i) / FPS >= gap:
        lo = i
        tb = next((k for k in range(lo, j) if any(d[0] >= 0.5 for d in rows[k].get("raw") or [])), None)
        tk = next((k for k in range(lo, j) if any(d[0] >= 0.5 and d[4] >= 3 for d in rows[k].get("raw") or [])), None)
        hs = [d[3] for k in range(lo, j) for d in rows[k].get("raw") or [] if d[0] >= 0.5]
        hm = sorted(hs)[len(hs) // 2] if hs else 0
        f = lambda x: f"{x / FPS:7.2f}" if x is not None else "      -"
        lat = (j - tb) / FPS if tb is not None and j < len(rows) else None
        print(f"  no point {i / FPS:7.2f} -> {j / FPS:7.2f} ({(j - i) / FPS:5.2f} s)  box>=.5 {f(tb)}  kp>=3 {f(tk)}  "
              f"h~{hm:4.0f}  emit {f(j if j < len(rows) else None)}  latency from box {lat if lat is not None else -1:5.2f} s")
    i = j
