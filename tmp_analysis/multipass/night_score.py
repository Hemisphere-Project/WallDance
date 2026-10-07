#!/usr/bin/env python3
"""Score night_runs.sh timelines like validate_night.py: rows trimmed to the 'operator at the wall' window
(abs frames), N = 1, emitted stream.

  python night_score.py DIR take:lo:hi:tag1,tag2 ...
"""
import json
import os
import sys

sys.path.insert(0, "/data/WallDance/application/tests")
sys.path.insert(0, "/data/WallDance/application/src")
import output_quality  # noqa: E402

KEYS = [("cov", "continuity", "coverage"), ("hole", "continuity", "gap_max_s"), ("holes1s", "continuity", "gaps_ge_1s"),
        ("coast", "quality", "coasting_share"), ("onD", "continuity", "on_dancer"), ("jit", "quality", "jitter_rest_pct"),
        ("lag", "quality", "lag_ms"), ("overN", "quality", "over_n_frames")]


def main():
    d = sys.argv[1]
    print(f"{'run':30s} " + " ".join(f"{k[0]:>7s}" for k in KEYS))
    for spec in sys.argv[2:]:
        take, lo, hi, tags = spec.split(":", 3)
        lo, hi = int(lo), int(hi)
        for tag in tags.split(","):
            p = os.path.join(d, f"{take}_{tag}.json")
            if not os.path.exists(p):
                print(f"{take}_{tag:24s} missing")
                continue
            rows = sorted(json.load(open(p)), key=lambda r: r["frame"])
            win = [dict(r, frame=r["frame"] - lo) for r in rows if lo <= r["frame"] <= hi]
            man = {"name": take, "start": 0, "frames": 10 ** 9, "warmup": 15, "fps": 20.0, "expected_count": 1,
                   "reference": {"min_conf": 0.5, "tol_h": 0.75, "exclude_spots": []}}
            q = output_quality.compare_streams(win, man, fps=20.0).get("emitted", {})
            vals = [q.get(s, {}).get(k) for _n, s, k in KEYS]
            print(f"{(take + '_' + tag)[:30]:30s} " + " ".join(
                f"{v:7.3f}" if isinstance(v, float) else f"{str(v):>7s}" for v in vals))


if __name__ == "__main__":
    main()
