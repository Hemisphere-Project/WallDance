#!/usr/bin/env python3
"""Emitted-stream KPIs (as kpi.py) for prototype timelines named <scene>_<variant>.json in a directory.

  python score.py DIR scene:variant1,variant2 [scene:...]
"""
import json
import os
import sys

sys.path.insert(0, "/data/WallDance/application/tests")
sys.path.insert(0, "/data/WallDance/application/src")
import output_quality  # noqa: E402
import scoring  # noqa: E402

KEYS = [("cov", "continuity", "coverage"), ("hole", "continuity", "gap_max_s"), ("coast", "quality", "coasting_share"),
        ("onD", "continuity", "on_dancer"), ("jit", "quality", "jitter_rest_pct"), ("lag", "quality", "lag_ms"),
        ("sw", "quality", "id_switches"), ("overN", "quality", "over_n_frames")]


def fmt(v):
    if v is None:
        return "-"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def main():
    d = sys.argv[1]
    print(f"{'run':44s} " + " ".join(f"{k[0]:>6s}" for k in KEYS))
    for spec in sys.argv[2:]:
        scene, variants = spec.split(":", 1)
        man = json.load(open(f"/data/WallDance/application/tests/scenarios/{scene}.json"))
        for v in variants.split(","):
            p = os.path.join(d, f"{scene}_{v}.json")
            if not os.path.exists(p):
                print(f"{scene}_{v:30s} missing")
                continue
            q = output_quality.compare_streams(scoring._load_timeline(p), man, fps=man.get("fps") or 19.8).get("emitted", {})
            print(f"{(scene + '_' + v)[:44]:44s} " + " ".join(f"{fmt(q.get(s, {}).get(k)):>6s}" for _n, s, k in KEYS))


if __name__ == "__main__":
    main()
