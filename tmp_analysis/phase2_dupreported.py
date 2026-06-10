"""Phase 2 (2): analyze REPORTED-pair structure from saved internal.json.

For frames where reported count exceeds expected N: which tracks co-report,
how far apart, and what state is the extra track in (bridged / skeleton-stale
/ freshly fed)?  Identifies the mechanism that lets zombies through the
report gate.

Run: .venv/Scripts/python.exe ../tmp_analysis/phase2_dupreported.py texture-duo white-duo texture-aerial
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "application"
SCEN = APP / "tests" / "scenarios"
OUTROOT = HERE / "phase2" / "dupdiag"


def analyze(name):
    man = json.loads((SCEN / f"{name}.json").read_text())
    n_exp = man["expected_count"]
    h = float(man["config"]["person_height_px"])
    frames = json.loads((OUTROOT / name / "internal.json").read_text())
    warm = man.get("warmup", 15)

    over_frames = 0
    state = Counter()       # state of the extra (lowest-priority) reported tracks
    extra_ids = Counter()
    fss_hist = Counter()
    dmin_hist = Counter()

    for fr in frames:
        if fr["frame"] < warm:
            continue
        rep = [t for t in fr["tracks"] if t["rep"]]
        if not isinstance(n_exp, int) or len(rep) <= n_exp:
            continue
        over_frames += 1
        # Heuristic: the N tracks most recently skeleton-fed are "real";
        # the rest are the extras under scrutiny.
        rep_sorted = sorted(rep, key=lambda t: (t["fss"], -t["hits"]))
        extras = rep_sorted[n_exp:]
        for t in extras:
            extra_ids[t["id"]] += 1
            fss_hist[min(t["fss"], 10)] += 1
            key = ("bridged" if t["bridged"]
                   else "fresh-skel" if t["fss"] == 0
                   else f"stale-skel")
            state[key] += 1
            dmin = min((float(np.linalg.norm(
                np.array(t["c"]) - np.array(u["c"]))) / h
                for u in rep if u["id"] != t["id"]), default=None)
            if dmin is not None:
                dmin_hist[min(20, int(dmin / 0.25))] += 1

    print(f"\n[{name}] over-count frames: {over_frames}")
    print(f"  extra-track state: {dict(state)}")
    print(f"  extra-track fss (frames since skeleton, cap 10): "
          f"{dict(sorted(fss_hist.items()))}")
    print(f"  extra ids: {dict(extra_ids.most_common(10))}")
    print("  extra->nearest reported dist (0.25h bins): "
          f"{ {f'{k*0.25:.2f}+': v for k, v in sorted(dmin_hist.items())} }")


if __name__ == "__main__":
    for nm in (sys.argv[1:] or ["texture-duo"]):
        analyze(nm)
