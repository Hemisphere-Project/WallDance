"""Phase 2 (2): offline simulation of the takeover-merge rule.

Replays the recorded internal states and applies the candidate rule:

  pair of established tracks, centroid distance < PROX x h,
  exactly one skeleton-fed this frame (fss==0), the other stale (fss >= STALE),
  pair lifetime co-fed count < COFED_VETO
  -> streak++ ; streak >= STREAK -> merge (victim = fewer hits)

Reports every simulated merge (frame, victim, keeper, hits) so false
positives on real pairs are visible, plus how many over-count frames the
removed victims were responsible for afterwards (upper-bound benefit, since
post-merge dynamics change).

Run: .venv/Scripts/python.exe ../tmp_analysis/phase2_dupsim.py texture-duo white-duo texture-aerial outdoor-sitter
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "application"
SCEN = APP / "tests" / "scenarios"
OUTROOT = HERE / "phase2" / "dupdiag"

PROX = 0.5          # x person height
STALE = 2           # victim fss >= this
COFED_VETO = 3      # pair co-fed frames >= this -> two real people, never merge
STREAK = 4          # consecutive qualifying frames to fire


def simulate(name):
    man = json.loads((SCEN / f"{name}.json").read_text())
    n_exp = man["expected_count"]
    h = float(man["config"]["person_height_px"])
    frames = json.loads((OUTROOT / name / "internal.json").read_text())
    warm = man.get("warmup", 15)

    cofed = defaultdict(int)
    streak = defaultdict(int)
    dead = set()
    merges = []

    for fr in frames:
        est = [t for t in fr["tracks"] if t["est"] and t["id"] not in dead]
        # lifetime co-fed accumulation (all established pairs)
        for i in range(len(est)):
            for j in range(i + 1, len(est)):
                a, b = est[i], est[j]
                k = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                if a["fss"] == 0 and b["fss"] == 0:
                    cofed[k] += 1
        # takeover rule
        hit_this_frame = set()
        for i in range(len(est)):
            for j in range(i + 1, len(est)):
                a, b = est[i], est[j]
                k = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                d = float(np.linalg.norm(np.array(a["c"]) - np.array(b["c"]))) / h
                one_fed_one_stale = (
                    (a["fss"] == 0 and b["fss"] >= STALE)
                    or (b["fss"] == 0 and a["fss"] >= STALE))
                if d < PROX and one_fed_one_stale and cofed[k] < COFED_VETO:
                    streak[k] += 1
                    hit_this_frame.add(k)
                    if streak[k] >= STREAK:
                        victim, keeper = (a, b) if a["hits"] < b["hits"] else (b, a)
                        dead.add(victim["id"])
                        merges.append((fr["frame"], victim["id"], keeper["id"],
                                       victim["hits"], keeper["hits"],
                                       round(d, 2), cofed[k]))
        for k in list(streak):
            if k not in hit_this_frame:
                streak[k] = 0

    # benefit estimate: over-count frames before vs after removing dead ids
    over_before = over_after = 0
    for fr in frames:
        if fr["frame"] < warm or not isinstance(n_exp, int):
            continue
        rep = [t for t in fr["tracks"] if t["rep"]]
        if len(rep) > n_exp:
            over_before += 1
        # remove victims from the frame where they were merged onward
        rep_after = [t for t in rep if not any(
            t["id"] == v and fr["frame"] >= f for (f, v, *_rest) in merges)]
        if len(rep_after) > n_exp:
            over_after += 1

    print(f"\n[{name}] simulated merges (PROX={PROX} STALE={STALE} "
          f"COFED_VETO={COFED_VETO} STREAK={STREAK}):")
    for (f, v, kp, vh, kh, d, cf) in merges:
        print(f"  frame {f:>4}: victim #{v} (hits {vh}) -> keeper #{kp} "
              f"(hits {kh}), d={d}h, pair_cofed={cf}")
    if not merges:
        print("  (none)")
    if isinstance(n_exp, int):
        print(f"  over-count frames: {over_before} -> {over_after} (upper bound)")


if __name__ == "__main__":
    for nm in (sys.argv[1:] or ["texture-duo"]):
        simulate(nm)
