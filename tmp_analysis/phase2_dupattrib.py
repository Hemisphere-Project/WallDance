"""Attribute drop-rate change: baseline vs after per-frame diff + merge events.

Run: .venv/Scripts/python.exe ../tmp_analysis/phase2_dupattrib.py texture-duo
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "application"
SCEN = APP / "tests" / "scenarios"


def main(name):
    man = json.loads((SCEN / f"{name}.json").read_text())
    n = man["expected_count"]
    warm = man.get("warmup", 15)
    base = json.loads((HERE / "phase2" / "baseline" / f"{name}.timeline.json").read_text())
    after = json.loads((HERE / "phase2" / "dupdiag" / name / "details.json").read_text())
    b = {r["frame"]: r for r in base}
    a = {r["frame"]: r for r in after}

    merges = []
    ev = HERE / "phase2" / "dupdiag" / name / "tracking_events.jsonl"
    for line in ev.read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "TRACK_MERGED":
            d = e.get("data", {})
            merges.append((e.get("frame"), d.get("mode", "colocated"),
                           d.get("victim_id"), d.get("keeper_id")))
    print(f"[{name}] TRACK_MERGED events: {len(merges)}")
    for m in merges:
        print(f"  frame {m[0]}: {m[1]} victim #{m[2]} -> keeper #{m[3]}")

    # transitions
    worse = []   # frames where after under-reports vs baseline
    better = []
    for f in sorted(b):
        if f < warm or f not in a:
            continue
        rb = min(b[f]["reported"], 10)
        ra = min(a[f]["reported"], 10)
        db = max(0, n - rb)
        da = max(0, n - ra)
        if da > db:
            worse.append(f)
        elif da < db:
            better.append(f)

    def runs(fr):
        out = []
        for f in fr:
            if out and f == out[-1][1] + 1:
                out[-1][1] = f
            else:
                out.append([f, f])
        return out

    print(f"\nframes with MORE drop than baseline: {len(worse)}")
    print("  runs:", runs(worse))
    print(f"frames with LESS drop than baseline: {len(better)}")
    print("  runs:", runs(better))

    # what was reporting at those frames in the baseline?
    print("\nbaseline ids at newly-dropped frames (first 15):")
    for f in worse[:15]:
        print(f"  f{f}: base={b[f]['ids']} after={a[f]['ids']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "texture-duo")
