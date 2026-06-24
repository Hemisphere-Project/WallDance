#!/usr/bin/env python3
"""known_n.py — known-N calibration ritual (ROADMAP §3.2 K1).

Given a project's labelled scenarios (per-range ``expected_count`` ground truth),
joint-search the per-scene known-N knobs through the **GPU+TRT show-path cache**
(Track P) and write the winner into the project as a timestamped save.

Knobs (G4 + AUTOTUNE gap #2), scene-dependent and never a user dial:
  - ``confidence`` (τ, Dial A)            -> active lighting profile
  - ``crossval_skel_min_kpts`` (θ_s)      -> shared (per-project)
  - ``crossval_motion_min_ratio`` (θ_m)   -> shared
  - ``tracker_max_age``                   -> shared
τ is oracle-seeded from the Phase-2b analysis when available (it is a front-end
key, so each value rebuilds the cache — the seed keeps the sweep short).

    python tests/known_n.py --project 3_TANGO_HANGAR-whitebg2            # search + save
    python tests/known_n.py --project 3_TANGO_HANGAR-whitebg2 --dry-run  # search only
    python tests/known_n.py --scenario tests/scenarios/hangar-floor.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from glob import glob
from pathlib import Path

# Allow ``from core import ...`` when run as a standalone CLI (src not on path).
# (We can't import ``replay`` here to get its path setup — that re-execs for the
# CUDA bootstrap, which would wreck an in-process pytest import.)
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import tune        # ScenarioEnv / Tuner / coordinate_descent (GPU+TRT cache via Track P)
from core import config_schema
from core import config_store as cs

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
SCEN_DIR = HERE / "scenarios"
ORACLE = REPO / "tmp_analysis" / "phase2b" / "analysis.json"

# The known-N (per-scene) search space.  θ_s/θ_m/max_age are post-YOLO (one cache
# per scenario, fast); confidence/τ is a front-end key (cache rebuilds per value,
# so it is oracle-seeded to keep the sweep short).
KNOWN_N_SPACE = {
    "confidence": [0.15, 0.25, 0.35, 0.45, 0.55, 0.65],
    "crossval_skel_min_kpts": [6, 8, 10, 12],
    "crossval_motion_min_ratio": [0.01, 0.02, 0.04, 0.07],
    "tracker_max_age": [30, 45, 60, 90],
}


def scenarios_for_project(project: str) -> list:
    """Verified scenario manifests whose ``project`` matches."""
    out = []
    for f in sorted(glob(str(SCEN_DIR / "*.json"))):
        try:
            m = json.loads(Path(f).read_text())
        except Exception:
            continue
        if m.get("project") == project and m.get("ground_truth", {}).get("verified"):
            out.append(f)
    return out


def latest_project_config(project: str):
    files = [f for f in glob(str(REPO / "projects" / project / f"{project}_*.json"))
             if "_safe_defaults" not in f]
    if not files:
        return None
    return json.loads(Path(max(files, key=lambda f: Path(f).stat().st_mtime)).read_text())


def save_into_project(project: str, tuned: dict, store: cs.ConfigStore) -> str:
    """Overlay tuned knobs into the project's latest config (profile-aware) and
    save a timestamped version.  ``confidence`` lands in the active lighting
    profile; the gate/tracker knobs are shared (G4: per-scene, internal)."""
    base = latest_project_config(project)
    if base is None:
        raise SystemExit(f"no existing config for {project} to overlay onto")
    cfg = config_schema.migrate(base)
    active = cfg.get("active_profile", config_schema.DEFAULT_PROFILE)
    prof = cfg.setdefault("profiles", {}).setdefault(active, {})
    for k, v in tuned.items():
        if k in config_schema.PROFILE_KEYS:
            prof[k] = v
        else:
            cfg[k] = v
    prev_last = store.read_last_project()
    path = store.save(project, cfg)
    if prev_last and prev_last != project:   # don't hijack the active project
        store.remember_last_project(prev_last)
    return path


def oracle_tau(scenario_name: str, model: str, imgsz: int):
    """Best-effort Phase-2b oracle best-τ for (scenario, model, imgsz)."""
    if not ORACLE.exists():
        return None
    try:
        cells = json.loads(ORACLE.read_text()).get("cells", {})
    except Exception:
        return None
    cell = cells.get(f"{scenario_name}|{model}|{imgsz}")
    return cell.get("best_tau") if cell else None


def main():
    import replay  # noqa: F401 — trigger the CUDA bootstrap re-exec early

    ap = argparse.ArgumentParser(description="Known-N calibration ritual (K1)")
    ap.add_argument("--project", default=None,
                    help="project name (its verified scenarios are searched)")
    ap.add_argument("--scenario", dest="scenarios", action="append", default=[],
                    help="scenario manifest (repeatable; overrides --project discovery)")
    ap.add_argument("--passes", type=int, default=3, help="coord-descent max sweeps")
    ap.add_argument("--frame-skip", type=int, default=1, metavar="N",
                    help="evaluate every Nth frame (Track-G stride; N=1 = all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="search + report, do NOT write the project save")
    args = ap.parse_args()

    scen_paths = list(args.scenarios)
    if args.project and not scen_paths:
        scen_paths = scenarios_for_project(args.project)
    if not scen_paths:
        raise SystemExit("no scenarios — give --scenario or a --project with verified scenarios")

    print(f"known-N search over {len(scen_paths)} scenario(s):")
    for s in scen_paths:
        print(f"  {Path(s).stem}")

    envs = [tune.ScenarioEnv(s) for s in scen_paths]
    project = args.project or envs[0].manifest["project"]
    base = envs[0].base_config
    model = base.get("model", "yolo11x-pose")
    imgsz = int(base.get("yolo_imgsz", 1280))

    # Warm-start τ from the Phase-2b oracle (best-effort) for the primary scene.
    space = {k: list(v) for k, v in KNOWN_N_SPACE.items()}
    start: dict = {}
    otau = oracle_tau(envs[0].manifest["name"], model, imgsz)
    if otau is not None:
        start["confidence"] = float(otau)
        if float(otau) not in space["confidence"]:
            space["confidence"] = sorted(set(space["confidence"]) | {float(otau)})
        print(f"oracle warm-start: confidence(tau)={otau} "
              f"({envs[0].manifest['name']}|{model}|{imgsz})")

    tuner = tune.Tuner(envs, None, frame_skip=args.frame_skip)
    t0 = time.time()
    best, best_score, history = tune.coordinate_descent(tuner, space, start, args.passes)
    dt = time.time() - t0

    base_score = history[0]["mean_score"]
    # Final value of every known-N knob (winning override, else the project base).
    final = {k: best.get(k, base.get(k)) for k in KNOWN_N_SPACE
             if best.get(k, base.get(k)) is not None}
    changed = {k: v for k, v in final.items() if base.get(k) != v}
    print(f"\n=== known-N result ({project}; {tuner.n_evals} evals, {dt:.0f}s) ===")
    print(f"  baseline mean_score = {base_score:.5f}")
    print(f"  tuned    mean_score = {best_score:.5f}  ({best_score - base_score:+.5f})")
    print(f"  final knobs : {final}")
    print(f"  changed     : {changed or '(none — base already optimal in this space)'}")

    if args.dry_run:
        print("\n--dry-run: not saving.")
        return
    store = cs.ConfigStore()
    path = save_into_project(project, final, store)
    print(f"\nwrote known-N config -> {path}")


if __name__ == "__main__":
    main()
