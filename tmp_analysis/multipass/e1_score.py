#!/usr/bin/env python3
"""E1 scoring: the same scene replayed at 20 fps (k1) and at 10 fps (``replay.py --frame-skip 2``, k2); KPIs
of the emitted (identity-slot) stream.  For k2 the timeline ``frame`` counts processed frames, so the
manifest fps is halved (constant-N scenes only).  Frame-based tracker parameters (max_age, warm-up windows)
double in time at k2: this is the plain pipeline at 10 fps, not a tuned 10 fps mode.

  python e1_score.py E1_DIR scene1,scene2,...
"""
import json
import os
import sys

sys.path.insert(0, "/data/WallDance/application/tests")
sys.path.insert(0, "/data/WallDance/application/src")
import output_quality  # noqa: E402
import scoring  # noqa: E402

KEYS = [("cov", "continuity", "coverage"), ("hole_s", "continuity", "gap_max_s"),
        ("holes1s", "continuity", "gaps_ge_1s"), ("coast", "quality", "coasting_share"),
        ("onD", "continuity", "on_dancer"), ("jit%h", "quality", "jitter_rest_pct"),
        ("lag_ms", "quality", "lag_ms"), ("sw", "quality", "id_switches"), ("overN", "quality", "over_n_frames")]


def main():
    e1, scenes = sys.argv[1], sys.argv[2].split(",")
    print("scene                 k  " + "  ".join(f"{k[0]:>7}" for k in KEYS))
    out = {}
    for s in scenes:
        man = json.load(open(f"/data/WallDance/application/tests/scenarios/{s}.json"))
        for k in (1, 2):
            p = f"{e1}/{s}_k{k}.json"
            if not os.path.exists(p):
                continue
            m = dict(man)
            m["fps"] = (man.get("fps") or 19.8) / k
            rows = scoring._load_timeline(p)
            q = output_quality.compare_streams(rows, m, fps=m["fps"]).get("emitted", {})
            vals = [q.get(sec, {}).get(key) for _n, sec, key in KEYS]
            out[f"{s}_k{k}"] = dict(zip([kk[0] for kk in KEYS], vals))
            cells = []
            for v in vals:
                cells.append(f"{v:7.3f}" if isinstance(v, float) else f"{str(v):>7}")
            print(f"{s:21s} {k}  " + "  ".join(cells))
    with open(f"{e1}/e1_scores.json", "w") as f:
        json.dump(out, f, indent=1)


if __name__ == "__main__":
    main()
