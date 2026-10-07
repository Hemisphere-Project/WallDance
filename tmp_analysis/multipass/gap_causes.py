#!/usr/bin/env python3
"""What the 'lost' frames are (a slot emitted while its track got no fresh skeleton).

For each lost frame, at the slot's interpolated position:
  ref>=.5   YOLO's raw output (after the size gate, before the motion cross-check) held a detection >= 0.5;
  ref<.5    it held one under 0.5 (above the scene's model threshold);
  upd/noskel  no detection there, but an internal track was UPDATED this frame (tsu 0) without a skeleton
            (a motion bridge / a box without keypoints);
  none      nothing at all near the dancer: a detector miss.
Only 'none' (and part of ref<.5) is for a second YOLO pass; ref>=.5 frames are lost after YOLO.

  python gap_causes.py scene=timeline.json ...
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mp_study import truth_from_timeline  # noqa: E402


def main():
    print(f"{'scene':22s} {'lost':>6s} {'ref>=.5':>8s} {'ref<.5':>7s} {'upd/noskel':>10s} {'none':>6s}")
    for arg in sys.argv[1:]:
        scene, tl = arg.split("=", 1)
        rows = {r["abs_frame"]: r for r in json.load(open(tl))}
        truth = truth_from_timeline(list(rows.values()))
        c = {"hi": 0, "lo": 0, "upd": 0, "none": 0}
        n = 0
        for f, people in truth.items():
            r = rows.get(f, {})
            for _sid, kind, cx, cy, h in people:
                if kind != "gap":
                    continue
                n += 1
                near = lambda x, y: abs(x - cx) <= 0.5 * h and abs(y - cy) <= 0.5 * h
                refs = [e for e in r.get("ref") or [] if isinstance(e, dict) and e.get("c") and near(*e["c"])]
                if any((e.get("conf") or 0) >= 0.5 for e in refs):
                    c["hi"] += 1
                elif refs:
                    c["lo"] += 1
                elif any(t.get("tsu") == 0 and t.get("p") and near(*t["p"]) for t in r.get("int") or []):
                    c["upd"] += 1
                else:
                    c["none"] += 1
        pct = lambda v: f"{100 * v / max(n, 1):.0f}%"
        print(f"{scene:22s} {n:6d} {pct(c['hi']):>8s} {pct(c['lo']):>7s} {pct(c['upd']):>10s} {pct(c['none']):>6s}")


if __name__ == "__main__":
    main()
