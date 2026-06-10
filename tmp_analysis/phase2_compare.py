"""Compare Phase-2 baseline vs after timelines across all scenarios.

Scores both runs against the GT manifests and prints a markdown table with
pass verdicts; also diffs the golden-comparable summary metrics for the trio.

Run from application/:  .venv/Scripts/python.exe ../tmp_analysis/phase2_compare.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "application"
for p in (str(APP / "tests"), str(APP / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import scoring  # noqa: E402

BASE = HERE / "phase2" / "baseline"
AFTER = HERE / "phase2" / (sys.argv[1] if len(sys.argv) > 1 else "after")
SCEN = APP / "tests" / "scenarios"

SCENARIOS = ["hangar-floor", "hangar-aerial", "texture-aerial", "texture-duo",
             "texture-wallhang", "white-duo", "white-walkers", "blur-runner",
             "outdoor-night", "outdoor-sitter", "dark-crowd", "facade-ghosts"]


def load_tl(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def main():
    print("| scenario | score b->a | drop b->a | ghost b->a | longest b->a | pass b->a |")
    print("|---|---|---|---|---|---|")
    deltas = []
    for name in SCENARIOS:
        m = scoring.load_scenario(SCEN / f"{name}.json")
        tb = load_tl(BASE / f"{name}.timeline.json")
        ta = load_tl(AFTER / f"{name}.timeline.json")
        if tb is None or ta is None:
            print(f"| {name} | MISSING {'baseline' if tb is None else 'after'} | | | | |")
            continue
        rb = scoring.score_timeline(tb, m)
        ra = scoring.score_timeline(ta, m)
        vb = scoring.evaluate_pass(rb, m)["passed"]
        va = scoring.evaluate_pass(ra, m)["passed"]
        cb, ca = rb["components"], ra["components"]
        print(f"| {name} | {rb['score']:.3f} -> **{ra['score']:.3f}** "
              f"| {cb['drop_rate']:.3f} -> {ca['drop_rate']:.3f} "
              f"| {cb['ghost_rate']:.3f} -> {ca['ghost_rate']:.3f} "
              f"| {rb['raw']['longest_drop_seconds']:.2f}s -> {ra['raw']['longest_drop_seconds']:.2f}s "
              f"| {'PASS' if vb else 'fail'} -> {'PASS' if va else 'fail'} |")
        deltas.append(ra["score"] - rb["score"])
    if deltas:
        import statistics
        print(f"\nmean score delta: {statistics.mean(deltas):+.4f} "
              f"(negative = better) over {len(deltas)} scenarios")

    print("\nGolden summary diffs (trio):")
    for name in ("hangar-floor", "hangar-aerial", "texture-aerial"):
        b = json.loads((BASE / f"{name}.summary.json").read_text())
        a = json.loads((AFTER / f"{name}.summary.json").read_text())
        diff = {k: (b[k], a[k]) for k in b
                if k in a and b[k] != a[k] and k not in ("path",)}
        print(f"  {name}: {diff if diff else 'identical'}")


if __name__ == "__main__":
    main()
