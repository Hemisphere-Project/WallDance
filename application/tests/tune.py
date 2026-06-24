#!/usr/bin/env python3
"""tune.py — detection-setting search harness (TUNING.md Phase C).

Searches a declared param space for the config that minimises the Phase-A
field-priority score, **across all scenarios at once** (C3), using the Phase-B
detect-pass cache so each evaluation skips YOLO.

  C1  arbitrary overrides via replay.apply_overrides / --set key=value.
  C2  grid / random / coordinate-descent (default) search; ranked output; the
      best config written as a saveable project file.
  C3  multi-scenario aggregate (mean across scenarios) so a config can't win by
      overfitting one scene (the slot-7 sin).

Cache handling is automatic and general: each evaluation resolves the cache for
its *merged* config.  Post-YOLO levers (gate θ_s/θ_m, mog2 var/scale, person
height, tracker age/smoothing) reuse one cache per scenario — fast.  A param that
changes the YOLO front-end (e.g. confidence) is a different cache key, so it is
built once on demand and memoised — correct, just slower.

Usage:
    # coordinate descent over the default space, both seed scenarios
    python tests/tune.py --scenario tests/scenarios/residence1-solo_slot3.json \
                         --scenario tests/scenarios/residence1-solo_slot4.json
    # custom space + strategy
    python tests/tune.py --scenario ... --space my_space.json --strategy grid
    # fix some params, search the rest
    python tests/tune.py --scenario ... --set tracking_mode=motion_first
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import detect_cache  # lazy-imports replay (cuda bootstrap) inside its functions
import scoring

# Default space: post-YOLO (cache-tunable) levers, so a search reuses one cache
# per scenario.  Add front-end keys (e.g. "confidence") to search them too — the
# cache rebuilds per value automatically.
DEFAULT_SPACE: Dict[str, list] = {
    "mog2_var_threshold": [8, 12, 16, 24],
    "mog2_scale": [0.3, 0.46, 0.7],
    "crossval_motion_min_ratio": [0.01, 0.02, 0.04],
    "person_height_px": [120, 148, 200],
}


class ScenarioEnv:
    """One scenario + its base config + a per-front-end-config cache registry."""

    def __init__(self, manifest_path: str):
        import replay
        self.manifest = scoring.load_scenario(manifest_path)
        self.base_config = replay.scenario_config(self.manifest)
        video = replay._find_recording(self.manifest["project"], self.manifest["slot"])
        if not video:
            raise SystemExit(
                f"no recording for {self.manifest['project']} slot {self.manifest['slot']}")
        self.video = str(video)
        self._registry: Dict[Path, dict] = {}

    def _cache_for(self, config: dict) -> dict:
        """Resolve (build on demand, memoise) the cache for this merged config."""
        model = config.get("model", self.base_config.get("model", "yolo11x-pose"))
        imgsz = int(config.get("yolo_imgsz", self.base_config.get("yolo_imgsz", 1280)))
        # Track P: the search runs on the GPU+TRT show-path cache, so a
        # post-YOLO search is "test what you ship" (no CPU↔TRT proxy gap — the
        # G1 finding that θ_m/bridge knobs mis-estimate on the CPU cache).
        key = detect_cache.cache_key(
            config, Path(self.video).name,
            self.manifest["start"], self.manifest["frames"], model, imgsz, path="trt")
        cpath = detect_cache.cache_path_for(key)
        if cpath not in self._registry:
            if not cpath.exists():
                print(f"  [build cache] {self.manifest['name']} -> {cpath.name}")
                detect_cache.build_cache_gpu(
                    self.video, config, model_name=model, imgsz=imgsz,
                    start_frame=self.manifest["start"],
                    max_frames=self.manifest["frames"], out_path=cpath, use_trt=True)
            self._registry[cpath] = detect_cache.load_cache(cpath)
        return self._registry[cpath]

    def timeline(self, overrides: dict, frame_skip: int = 1) -> Tuple[dict, List[dict]]:
        config = {**self.base_config, **overrides}
        cache = self._cache_for(config)   # cache built full; stride applied at replay
        summary = detect_cache.replay_from_cache_gpu(
            cache, config, reuse_grays=True, frame_skip=frame_skip)
        return self.manifest, summary["per_frame"]


class Tuner:
    def __init__(self, scenarios: List[ScenarioEnv], weights=None, frame_skip: int = 1):
        self.scenarios = scenarios
        self.weights = weights
        self.frame_skip = frame_skip
        self.n_evals = 0

    def evaluate(self, overrides: dict) -> dict:
        pairs = [s.timeline(overrides, self.frame_skip) for s in self.scenarios]
        self.n_evals += 1
        return scoring.score_multi(pairs, self.weights)


# --------------------------------------------------------------------------- #
# Search strategies
# --------------------------------------------------------------------------- #
_SENTINEL = object()


def _record(history, overrides, agg):
    history.append({
        "overrides": dict(overrides),
        "mean_score": agg["mean_score"],
        "worst_score": agg["worst_score"],
        "per_scenario": agg["per_scenario"],
    })


def coordinate_descent(tuner: Tuner, space: dict, start: dict,
                       max_passes: int = 3, eps: float = 1e-9):
    """Greedy 1-D sweeps until a full pass yields no improvement."""
    history: List[dict] = []
    current = dict(start)
    best = tuner.evaluate(current)
    _record(history, current, best)
    best_score = best["mean_score"]
    print(f"baseline mean_score={best_score:.5f}  {best['per_scenario']}")

    for p in range(max_passes):
        improved = False
        for param, values in space.items():
            best_v, local = current.get(param, _SENTINEL), best_score
            for v in values:
                if current.get(param) == v:
                    continue
                trial = {**current, param: v}
                agg = tuner.evaluate(trial)
                _record(history, trial, agg)
                print(f"  pass{p} {param}={v}: mean={agg['mean_score']:.5f} "
                      f"{agg['per_scenario']}")
                if agg["mean_score"] < local - eps:
                    local, best_v = agg["mean_score"], v
            if best_v is not _SENTINEL and local < best_score - eps:
                current[param] = best_v
                best_score = local
                improved = True
                print(f"  -> adopt {param}={best_v} (mean_score={best_score:.5f})")
        if not improved:
            print(f"converged after pass {p}")
            break
    return current, best_score, history


def grid_search(tuner: Tuner, space: dict, start: dict):
    history: List[dict] = []
    keys = list(space)
    best, best_score = dict(start), float("inf")
    for combo in itertools.product(*(space[k] for k in keys)):
        trial = {**start, **dict(zip(keys, combo))}
        agg = tuner.evaluate(trial)
        _record(history, trial, agg)
        if agg["mean_score"] < best_score:
            best, best_score = trial, agg["mean_score"]
    return best, best_score, history


def random_search(tuner: Tuner, space: dict, start: dict, n: int, seed: int = 0):
    history: List[dict] = []
    rng = random.Random(seed)
    keys = list(space)
    best, best_score = dict(start), float("inf")
    for _ in range(n):
        trial = {**start, **{k: rng.choice(space[k]) for k in keys}}
        agg = tuner.evaluate(trial)
        _record(history, trial, agg)
        if agg["mean_score"] < best_score:
            best, best_score = trial, agg["mean_score"]
    return best, best_score, history


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    import replay  # for apply_overrides + base config write
    ap = argparse.ArgumentParser(description="Detection-setting search (TUNING Phase C)")
    ap.add_argument("--scenario", dest="scenarios", action="append", required=True,
                    help="scenario manifest JSON (repeatable; C3 ranks across all)")
    ap.add_argument("--space", default=None, help="JSON {param: [values...]} (default: built-in)")
    ap.add_argument("--strategy", choices=["coord", "grid", "random"], default="coord")
    ap.add_argument("--passes", type=int, default=3, help="coord: max sweeps")
    ap.add_argument("--samples", type=int, default=30, help="random: number of samples")
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                    help="fix a param (applied to the start config, not searched)")
    ap.add_argument("--weights", default=None, help='JSON score-weight overrides')
    ap.add_argument("--top", type=int, default=8, help="how many ranked configs to print")
    ap.add_argument("--out", default=None,
                    help="write the best merged config here (default: tests/tuned_<ts>.json)")
    ap.add_argument("--frame-skip", type=int, default=1, metavar="N",
                    help="evaluate every Nth frame (stride; default 1 = all). Caches "
                         "are built full and reused; the stride is applied at replay, "
                         "so N>1 speeds a search at some scoring fidelity (Track-G). "
                         "N=1 is byte-identical to a full search.")
    args = ap.parse_args()

    space = json.loads(Path(args.space).read_text()) if args.space else dict(DEFAULT_SPACE)
    weights = json.loads(args.weights) if args.weights else None
    start = replay.apply_overrides({}, args.sets)   # fixed params from --set

    scenarios = [ScenarioEnv(s) for s in args.scenarios]
    tuner = Tuner(scenarios, weights, frame_skip=args.frame_skip)

    t0 = time.time()
    if args.strategy == "coord":
        best, best_score, history = coordinate_descent(tuner, space, start, args.passes)
    elif args.strategy == "grid":
        best, best_score, history = grid_search(tuner, space, start)
    else:
        best, best_score, history = random_search(tuner, space, start, args.samples)
    dt = time.time() - t0

    # Ranked unique configs
    ranked = sorted(history, key=lambda h: h["mean_score"])
    print(f"\n=== TOP {args.top} (of {len(history)} evals, {tuner.n_evals} scored, {dt:.0f}s) ===")
    for h in ranked[:args.top]:
        diff = {k: v for k, v in h["overrides"].items()}
        print(f"  mean={h['mean_score']:.5f} worst={h['worst_score']:.5f} "
              f"{h['per_scenario']}  <- {diff}")

    # Best config = primary scenario's base + best overrides.
    base = scenarios[0].base_config
    tuned = {**base, **best}
    only = {k: v for k, v in best.items() if base.get(k) != v}
    print(f"\n=== BEST (mean_score={best_score:.5f}) ===")
    print(f"  changes vs {scenarios[0].manifest['project']} base: {only or '(none — baseline already optimal in this space)'}")

    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent / f"tuned_{time.strftime('%Y%m%d_%H%M%S')}.json")
    tuned.setdefault("_meta", {})
    tuned["_meta"] = {**tuned.get("_meta", {}), "tuned_by": "tune.py",
                      "tuned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "scenarios": [s.manifest["name"] for s in scenarios],
                      "mean_score": round(best_score, 5),
                      "search_overrides": only}
    Path(out).write_text(json.dumps(tuned, indent=2))
    print(f"\nwrote best config -> {out}")


if __name__ == "__main__":
    main()
