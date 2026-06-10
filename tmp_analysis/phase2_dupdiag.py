"""Phase 2 (2) diagnostics: characterize duplicate tracks on the duo scenes.

Part A: per-scenario baseline timeline stats (over-count pressure, id churn).
Part B: spatial analysis of after5/texture-duo.details.json -- pairwise
distances between concurrent tracks in person-height units, per-id lifespans,
distance-to-nearest-neighbour histogram for the extra (duplicate) ids.

Run: .venv/Scripts/python.exe ../tmp_analysis/phase2_dupdiag.py  (from application/)
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "application"
SCEN = APP / "tests" / "scenarios"
BASE = HERE / "phase2" / "baseline"

SCENARIOS = ["hangar-floor", "hangar-aerial", "texture-aerial", "texture-duo",
             "texture-wallhang", "white-duo", "white-walkers", "blur-runner",
             "outdoor-night", "outdoor-sitter", "dark-crowd", "facade-ghosts"]


def part_a():
    print("=== A: baseline timelines, over-count pressure ===")
    print(f"{'scenario':18} {'N':>3} {'frames':>6} {'over':>5} {'over%':>6} "
          f"{'maxrep':>6} {'ids':>4} per-id frames (top 8)")
    for name in SCENARIOS:
        man = json.loads((SCEN / f"{name}.json").read_text())
        tl = json.loads((BASE / f"{name}.timeline.json").read_text())
        n = man.get("expected_count")
        warm = man.get("warmup", 15)
        rows = [r for r in tl if r["frame"] >= warm]

        def n_at(frame):
            if not isinstance(n, list):
                return n
            default = None
            for rng in n:
                if "default" in rng:
                    default = rng["default"]
                elif rng["from"] <= frame <= rng["to"]:
                    return rng["n"]
            return default

        id_frames = Counter()
        over = 0
        maxrep = 0
        for r in rows:
            maxrep = max(maxrep, r["reported"])
            nf = n_at(r["frame"])
            if nf is not None and r["reported"] > nf:
                over += 1
            for i in r["ids"]:
                id_frames[i] += 1
        tops = ", ".join(f"#{i}:{c}" for i, c in id_frames.most_common(8))
        n_s = "rng" if isinstance(n, list) else ("-" if n is None else str(n))
        print(f"{name:18} {n_s:>3} {len(rows):>6} {over:>5} "
              f"{100*over/max(1,len(rows)):>5.1f}% {maxrep:>6} "
              f"{len(id_frames):>4} {tops}")


def part_b():
    details_path = HERE / "phase2" / "after5" / "texture-duo.details.json"
    man = json.loads((SCEN / "texture-duo.json").read_text())
    h = man["config"]["person_height_px"]
    tl = json.loads(details_path.read_text())
    print(f"\n=== B: after5/texture-duo spatial details (h={h}px) ===")

    # Per-id lifespan + span
    first, last, frames, xs, ys = {}, {}, Counter(), defaultdict(list), defaultdict(list)
    for r in tl:
        for t in r.get("tracks", []):
            i = t["id"]
            first.setdefault(i, r["frame"])
            last[i] = r["frame"]
            frames[i] += 1
            xs[i].append(t["centroid"][0])
            ys[i].append(t["centroid"][1])
    print(f"{'id':>4} {'first':>6} {'last':>6} {'frames':>6} {'span_x':>7} {'span_y':>7}")
    for i in sorted(frames):
        print(f"{i:>4} {first[i]:>6} {last[i]:>6} {frames[i]:>6} "
              f"{max(xs[i])-min(xs[i]):>7.0f} {max(ys[i])-min(ys[i]):>7.0f}")

    # Pairwise distance (in h units) of concurrent tracks, when reported > 2
    dist_h = []
    nn_by_id = defaultdict(list)  # id -> dist to nearest concurrent track / h
    for r in tl:
        ts = r.get("tracks", [])
        if len(ts) < 2:
            continue
        for a_i, a in enumerate(ts):
            best = None
            for b_i, b in enumerate(ts):
                if a_i == b_i:
                    continue
                d = math.dist(a["centroid"], b["centroid"]) / h
                if best is None or d < best:
                    best = d
                if a_i < b_i:
                    dist_h.append(d)
            nn_by_id[a["id"]].append(best)

    buckets = Counter()
    for d in dist_h:
        buckets[min(20, int(d / 0.1))] += 1
    print("\npairwise concurrent distance histogram (h units, 0.1 bins):")
    for b in sorted(buckets):
        lo = b * 0.1
        label = f"{lo:.1f}-{lo+0.1:.1f}" if b < 20 else ">=2.0"
        print(f"  {label:>9}: {buckets[b]:>5} {'#' * min(60, buckets[b] // 20)}")

    print("\nper-id nearest-neighbour distance (h units): median / p10 / share<0.5h")
    for i in sorted(nn_by_id):
        v = sorted(nn_by_id[i])
        med = v[len(v) // 2]
        p10 = v[len(v) // 10]
        sh = sum(1 for d in v if d < 0.5) / len(v)
        print(f"  #{i:>3}: med={med:5.2f}  p10={p10:5.2f}  <0.5h={100*sh:5.1f}%  (n={len(v)})")


if __name__ == "__main__":
    part_a()
    part_b()
