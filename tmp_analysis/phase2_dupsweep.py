"""Phase 2 (2): parameter sweep of the takeover-merge rule on recorded states.

Sweeps PROX / STREAK / STALE / windowed-vs-consecutive and prints merges +
over-count reduction per scene, to pick the implementation constants.

Run: .venv/Scripts/python.exe ../tmp_analysis/phase2_dupsweep.py
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from itertools import product
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "application"
SCEN = APP / "tests" / "scenarios"
OUTROOT = HERE / "phase2" / "dupdiag"

SCENES = ["texture-duo", "white-duo", "texture-aerial", "outdoor-sitter"]
COFED_VETO = 3


def load(name):
    man = json.loads((SCEN / f"{name}.json").read_text())
    frames = json.loads((OUTROOT / name / "internal.json").read_text())
    return man, frames


def simulate(man, frames, prox, stale, streak_n, window):
    """window=None -> consecutive streak; else 'streak_n hits in last window'."""
    h = float(man["config"]["person_height_px"])
    n_exp = man["expected_count"]
    warm = man.get("warmup", 15)
    cofed = defaultdict(int)
    hist = defaultdict(lambda: deque(maxlen=window or 1))
    streak = defaultdict(int)
    dead = set()
    merges = []

    for fr in frames:
        est = [t for t in fr["tracks"] if t["est"] and t["id"] not in dead]
        for i in range(len(est)):
            for j in range(i + 1, len(est)):
                a, b = est[i], est[j]
                k = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                if a["fss"] == 0 and b["fss"] == 0:
                    cofed[k] += 1
        hits_now = set()
        for i in range(len(est)):
            for j in range(i + 1, len(est)):
                a, b = est[i], est[j]
                k = (min(a["id"], b["id"]), max(a["id"], b["id"]))
                d = float(np.linalg.norm(np.array(a["c"]) - np.array(b["c"]))) / h
                ok = (d < prox
                      and ((a["fss"] == 0 and b["fss"] >= stale)
                           or (b["fss"] == 0 and a["fss"] >= stale))
                      and cofed[k] < COFED_VETO)
                fire = False
                if window:
                    hist[k].append(1 if ok else 0)
                    fire = sum(hist[k]) >= streak_n
                else:
                    streak[k] = streak[k] + 1 if ok else 0
                    fire = streak[k] >= streak_n
                if ok:
                    hits_now.add(k)
                if fire:
                    victim, keeper = (a, b) if a["hits"] < b["hits"] else (b, a)
                    dead.add(victim["id"])
                    merges.append((fr["frame"], victim["id"], keeper["id"]))
                    streak[k] = 0
                    hist[k].clear()
        if not window:
            for k in list(streak):
                if k not in hits_now:
                    streak[k] = 0

    over_b = over_a = 0
    for fr in frames:
        if fr["frame"] < warm or not isinstance(n_exp, int):
            continue
        rep = [t for t in fr["tracks"] if t["rep"]]
        if len(rep) > n_exp:
            over_b += 1
        rep_a = [t for t in rep if not any(
            t["id"] == v and fr["frame"] >= f for (f, v, _k) in merges)]
        if len(rep_a) > n_exp:
            over_a += 1
    return merges, over_b, over_a


def main():
    data = {nm: load(nm) for nm in SCENES}
    print(f"{'prox':>5} {'stale':>5} {'k/win':>7} | per-scene merges, over b->a")
    for prox, stale, (k, win) in product(
            (0.5, 0.6, 0.7), (1, 2), ((4, None), (3, None), (4, 8), (5, 10))):
        cells = []
        for nm in SCENES:
            man, frames = data[nm]
            merges, ob, oa = simulate(man, frames, prox, stale, k, win)
            cells.append(f"{nm.split('-')[0][:4]}:{len(merges)}m {ob}->{oa}")
        print(f"{prox:>5} {stale:>5} {f'{k}/{win}':>7} | " + "  ".join(cells))


if __name__ == "__main__":
    main()
