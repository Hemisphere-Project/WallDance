#!/usr/bin/env python3
"""Knob sensitivity sweep (TUNING.md Phase E1).

One-at-a-time (OAT): hold every knob at the project baseline, sweep one knob's
candidate values, and measure how much the Phase-A score moves.  Ranks knobs by
*impact* (score range across the sweep) so Phase E2 can decide which knobs a user
must control, which the Go-Live calibration should auto-derive, and which are
inert enough to hide/fix.

Reuses the Phase-C machinery (cache-backed, multi-scenario).  Post-YOLO knobs
reuse one cache per scenario; a YOLO-front-end knob (e.g. ``confidence``)
rebuilds its cache on demand — so the sweep can include both, just slower.

NB OAT is first-order: it measures each knob from the baseline and won't see
interactions (e.g. var only matters at a given scale).  Use tune.py for the
joint search; this is the "which knobs matter at all" map.

Usage:
    python tests/sensitivity.py \
        --scenario tests/scenarios/residence1-solo_slot3.json \
        --scenario tests/scenarios/residence1-solo_slot4.json [--out sens.json]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List

import tune        # ScenarioEnv, Tuner
import scoring

# Candidate values per knob.  Mix of post-YOLO (cache-reusing) and one front-end
# key (confidence, rebuilds) so the map is complete.  Baseline value should be
# in-range so the sweep brackets it.
SWEEP_SPACE: Dict[str, list] = {
    "confidence": [0.25, 0.37, 0.50],            # YOLO front-end (rebuilds cache)
    "mog2_var_threshold": [8, 16, 24, 40],
    "mog2_scale": [0.30, 0.46, 0.70],
    "motion_sensitivity": [0.40, 0.55, 0.70, 0.85],
    "crossval_motion_min_ratio": [0.01, 0.02, 0.04],   # θ_m
    "crossval_skel_min_kpts": [6, 8, 10],              # θ_s
    "crossval_skel_min_conf": [0.35, 0.45, 0.55],      # θ_s
    "person_height_px": [120, 148, 200],
    "tracker_max_age": [10, 15, 25, 40],
    "tracker_smoothing": [1, 2, 3],
}


def sweep(tuner: tune.Tuner, space: Dict[str, list]) -> tuple:
    baseline = tuner.evaluate({})           # all knobs at project default
    base_mean = baseline["mean_score"]
    print(f"baseline mean_score={base_mean:.5f}  {baseline['per_scenario']}")

    rows: List[dict] = []
    for knob, values in space.items():
        points = []
        for v in values:
            agg = tuner.evaluate({knob: v})
            points.append({"value": v, "mean_score": agg["mean_score"],
                           "per_scenario": agg["per_scenario"]})
            print(f"  {knob}={v}: mean={agg['mean_score']:.5f} {agg['per_scenario']}")
        means = [p["mean_score"] for p in points]
        best = min(points, key=lambda p: p["mean_score"])
        worst = max(means)
        rows.append({
            "knob": knob,
            "impact": round(worst - min(means), 5),          # sensitivity
            "best_value": best["value"],
            "best_mean": round(best["mean_score"], 5),
            "improvement_vs_baseline": round(base_mean - best["mean_score"], 5),
            "worst_mean": round(worst, 5),
            "points": points,
        })
    rows.sort(key=lambda r: r["impact"], reverse=True)
    return base_mean, rows


def main():
    ap = argparse.ArgumentParser(description="Knob sensitivity sweep (TUNING Phase E1)")
    ap.add_argument("--scenario", dest="scenarios", action="append", required=True)
    ap.add_argument("--space", default=None, help="JSON {knob:[values]} (default: built-in)")
    ap.add_argument("--weights", default=None)
    ap.add_argument("--out", default=None, help="write the full JSON result here")
    args = ap.parse_args()

    space = json.loads(Path(args.space).read_text()) if args.space else dict(SWEEP_SPACE)
    weights = json.loads(args.weights) if args.weights else None
    scenarios = [tune.ScenarioEnv(s) for s in args.scenarios]
    tuner = tune.Tuner(scenarios, weights)

    t0 = time.time()
    base_mean, rows = sweep(tuner, space)
    dt = time.time() - t0

    print(f"\n=== SENSITIVITY (baseline {base_mean:.5f}, {tuner.n_evals} evals, {dt:.0f}s) ===")
    print(f"{'knob':28s} {'impact':>8} {'best':>8} {'@value':>10} {'gain':>8}")
    for r in rows:
        print(f"{r['knob']:28s} {r['impact']:8.4f} {r['best_mean']:8.4f} "
              f"{str(r['best_value']):>10} {r['improvement_vs_baseline']:8.4f}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"baseline_mean": base_mean, "scenarios": [s.manifest["name"] for s in scenarios],
             "rows": rows}, indent=2))
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
