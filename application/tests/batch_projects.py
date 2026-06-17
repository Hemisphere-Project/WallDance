#!/usr/bin/env python3
"""batch_projects.py — best-effort calibration for every HANGAR/TOGO project.

For each project: (0) re-derive the structural config from its footage
(``calibrate_project``), (1) sweep the user sliders CLAHE x confidence x gamma on
the GPU+TRT path across the project's slots and pick the best mean+worst config,
(2) save the winner straight into the project as a timestamped save (profile-aware
overlay — gamma/clahe/conf/var/scale go into the active profile, height/imgsz/blur
into the shared keys). Records a real best-effort baseline table.

Gamma is swept (2.2->4.0) to test whether relaxing seed_gamma's 2.2 clamp helps
the IR-under-lit projects, or whether they are genuinely hardware-limited.
Gap-bridging (motion_sensitivity) is fixed here and swept in a SECOND pass once
these three axes are set (operator's sequencing 2026-06-16).

    python tests/batch_projects.py                 # all projects in PROJECTS below
    python tests/batch_projects.py --only texturedbg   # one project (validation)
    python tests/batch_projects.py --dry-run       # re-derive + sweep, do NOT save

Long job (~36 combos x slots x ~17 s/replay/project). Writes incremental results
to tmp_analysis/calib_project_20260616/batch/ and prints per-project progress.
"""
from __future__ import annotations

import argparse
import json
import time
from glob import glob
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
OUT = REPO / "tmp_analysis" / "calib_project_20260616" / "batch"

import calibrate_project as cp
import sweep_project as sp
from core import config_schema
from core import config_store as cs

# Project -> its scenario slots (HANGAR/TOGO targets; 0-TEST-* excluded).
PROJECTS = {
    "3_TANGO_HANGAR-whitebg2": ["hangar-floor", "hangar-aerial"],
    "2_TANGO_HANGAR-whitebg": ["texture-aerial"],
    "1_TANGO_HANGAR-texturedbg": ["texture-duo", "texture-wallhang"],
    "4_TANGO_HANGAR-whitebg3": ["white-duo", "white-walkers"],
    "5_TANGO_HANGAR-testflou": ["blur-runner"],
    "6_TANGO_TOGO-night": ["outdoor-night"],
    "7_TANGO_TOGO-day": ["outdoor-sitter"],
}

CLAHE_GRID = [1.0, 2.5, 4.0, 6.0]
FIXED_MOTION = 0.55      # gap-bridging fixed this pass (swept in pass 2)
MOTION_GRID = [0.0, 0.3, 0.55, 0.8, 1.0]   # pass-2 gap-bridging (Dial B) sweep

# Structural keys forwarded to the sweep as fixed --set (gamma/clahe/conf are swept).
_SWEEP_BASE_KEYS = ("mog2_var_threshold", "mog2_scale", "person_height_px",
                    "person_height_min_ratio", "person_height_max_ratio",
                    "yolo_imgsz", "blur_budget_ms")


def _latest_project_config(project: str):
    files = [f for f in glob(str(REPO / "projects" / project / f"{project}_*.json"))
             if "_safe_defaults" not in f]
    if not files:
        return None
    latest = max(files, key=lambda f: Path(f).stat().st_mtime)
    return json.loads(Path(latest).read_text())


def save_into_project(project: str, tuned: dict, store: cs.ConfigStore) -> str:
    """Overlay tuned keys into the project's latest config (profile-aware) + save."""
    base = _latest_project_config(project)
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


def _score_slots(scen_paths, base_sets, knobs):
    """Run `knobs` across a project's slots; return (mean, worst, n_pass, per_slot)."""
    per_slot = []
    for scen in scen_paths:
        score, passline = sp._run(scen, base_sets, knobs)
        per_slot.append(None if score is None else {
            "scenario": Path(scen).stem, "score": score["score"],
            "drop": passline.get("checks", {}).get("drop_rate", {}).get("value"),
            "passed": passline.get("passed")})
    ok = [s for s in per_slot if s]
    mean = round(sum(s["score"] for s in ok) / len(ok), 5) if ok else 9.9
    worst = round(max(s["score"] for s in ok), 5) if ok else 9.9
    n_pass = sum(1 for s in ok if s["passed"])
    return mean, worst, n_pass, per_slot


def run_project(project: str, scenarios: list, save: bool, store) -> dict:
    """Coordinate-descent re-tune across a project's slots (improved 2026-06-16):
    SEED-CENTERED confidence grid (the old fixed 0.4-0.6 grid missed better low-conf
    values — white-duo 0.598->0.337 at conf 0.34) + intermittent-confirm on/off;
    GAMMA is now deterministic (calibrate_window caps the window gamma by noise),
    not swept. Scores mean+worst across slots so no single slot overfits."""
    scen_paths = [str(HERE / "scenarios" / f"{s}.json") for s in scenarios]
    print(f"\n=== {project} ({len(scenarios)} slots) — re-deriving ===", flush=True)
    derived, _base = cp.calibrate_project(scen_paths)
    print(f"  re-derived: gamma {derived['gamma']}, imgsz {derived['yolo_imgsz']}, "
          f"height {derived['person_height_px']}, conf_seed {derived['confidence_seed']}, "
          f"ir_limited={derived['ir_limited']}", flush=True)
    for f in (derived.get("saturation_flags") or []):
        print(f"  ! {f}", flush=True)

    base_sets = [f"{k}={derived[_map(k)]}" for k in _SWEEP_BASE_KEYS]
    base_sets.append(f"gamma={derived['gamma']}")
    base_sets.append(f"motion_sensitivity={FIXED_MOTION}")
    seed = derived.get("confidence_seed") or 0.4

    # 1. CLAHE sweep (confidence at the seed).
    clahe_scores = {c: _score_slots(scen_paths, base_sets,
                    {"clahe_clip": c, "confidence": round(seed, 2)}) for c in CLAHE_GRID}
    best_clahe = min(CLAHE_GRID, key=lambda c: clahe_scores[c][:2])
    print("  clahe: " + " ".join(f"{c}:{clahe_scores[c][0]}" for c in CLAHE_GRID)
          + f"  -> {best_clahe}", flush=True)

    # 2. confidence sweep (SEED-CENTERED; CLAHE fixed at best).
    conf_grid = sorted({round(max(0.15, min(0.65, seed + d)), 2) for d in (-0.1, 0.0, 0.1)})
    conf_scores = {cf: _score_slots(scen_paths, base_sets,
                   {"clahe_clip": best_clahe, "confidence": cf}) for cf in conf_grid}
    best_conf = min(conf_grid, key=lambda c: conf_scores[c][:2])
    print("  conf: " + " ".join(f"{cf}:{conf_scores[cf][0]}" for cf in conf_grid)
          + f"  -> {best_conf}", flush=True)

    # 3. intermittent-confirm on/off (scene-dependent; CLAHE+conf fixed).
    int_scores = {ic: _score_slots(scen_paths, base_sets,
                  {"clahe_clip": best_clahe, "confidence": best_conf,
                   "tracker_intermittent_confirm": str(ic).lower()}) for ic in (False, True)}
    best_int = min((False, True), key=lambda ic: int_scores[ic][:2])
    print(f"  intermittent: off:{int_scores[False][0]} on:{int_scores[True][0]} "
          f"-> {best_int}", flush=True)

    mean, worst, n_pass, per_slot = int_scores[best_int]
    # Dial-B relevance (build #3): worst per-slot drop at the tuned config still
    # over the class-A line -> gap-bridging worth showing; else hidden (Advanced).
    drops = [s["drop"] for s in per_slot if s and s.get("drop") is not None]
    dial_b_relevant = bool(drops and max(drops) > 0.05)
    print(f"  BEST: clahe={best_clahe} conf={best_conf} intermittent={best_int} "
          f"gamma={derived['gamma']}  mean={mean} worst={worst} "
          f"pass={n_pass}/{len(scenarios)}  dial_b_relevant={dial_b_relevant}", flush=True)

    tuned = {
        "gamma": derived["gamma"], "clahe_clip": best_clahe, "confidence": best_conf,
        "tracker_intermittent_confirm": bool(best_int),
        "dial_b_relevant": dial_b_relevant,
        "mog2_var_threshold": derived["var_threshold"], "mog2_scale": derived["mog2_scale"],
        "person_height_px": derived["person_height_px"],
        "person_height_min_ratio": derived["person_height_min_ratio"],
        "person_height_max_ratio": derived["person_height_max_ratio"],
        "yolo_imgsz": derived["yolo_imgsz"], "blur_budget_ms": derived["blur_budget_ms"],
        "motion_sensitivity": FIXED_MOTION,
    }
    tuned = {k: v for k, v in tuned.items() if v is not None}
    saved_path = save_into_project(project, tuned, store) if save else None
    if saved_path:
        print(f"  saved -> {saved_path}", flush=True)

    best = {"clahe": best_clahe, "conf": best_conf, "intermittent": bool(best_int),
            "mean": mean, "worst": worst, "n_pass": n_pass, "per_slot": per_slot}
    summary = {"project": project, "scenarios": scenarios, "derived": derived,
               "best": best, "tuned": tuned, "saved_path": saved_path,
               "clahe_scores": {str(c): clahe_scores[c][0] for c in CLAHE_GRID},
               "conf_scores": {str(cf): conf_scores[cf][0] for cf in conf_grid}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{project}.json").write_text(json.dumps(summary, indent=2))
    return summary


_PASS2_BASE_KEYS = ("gamma", "clahe_clip", "confidence", "mog2_var_threshold",
                    "mog2_scale", "person_height_px", "person_height_min_ratio",
                    "person_height_max_ratio", "yolo_imgsz", "blur_budget_ms")


def run_project_pass2(project: str, scenarios: list, save: bool, store) -> dict:
    """Pass 2: sweep gap-bridging (motion_sensitivity) on the project's already-set
    best config (CLAHE/conf/gamma from pass 1 held fixed), re-save if it beats 0.55."""
    scen_paths = [str(HERE / "scenarios" / f"{s}.json") for s in scenarios]
    cfg = config_schema.migrate(_latest_project_config(project))
    active = cfg.get("active_profile", config_schema.DEFAULT_PROFILE)
    prof = cfg.get("profiles", {}).get(active, {})

    def getk(k):
        return prof.get(k) if k in config_schema.PROFILE_KEYS else cfg.get(k)

    base_sets = [f"{k}={getk(k)}" for k in _PASS2_BASE_KEYS if getk(k) is not None]
    print(f"\n=== {project} PASS-2 gap-bridging ({len(scenarios)} slots) ===", flush=True)
    print(f"  fixed base: {' '.join(base_sets)}", flush=True)

    results = []
    for m in MOTION_GRID:
        per_slot = []
        for scen in scen_paths:
            score, passline = sp._run(scen, base_sets, {"motion_sensitivity": m})
            per_slot.append(None if score is None else {
                "scenario": Path(scen).stem, "score": score["score"],
                "passed": passline.get("passed")})
        ok = [s for s in per_slot if s]
        mean = round(sum(s["score"] for s in ok) / len(ok), 5) if ok else 9.9
        worst = round(max(s["score"] for s in ok), 5) if ok else 9.9
        n_pass = sum(1 for s in ok if s["passed"])
        results.append({"motion": m, "mean": mean, "worst": worst, "n_pass": n_pass,
                        "per_slot": per_slot})
        print(f"  motion={m}: mean={mean} worst={worst} pass={n_pass}/{len(scenarios)}",
              flush=True)

    results.sort(key=lambda r: (r["mean"], r["worst"]))
    best = results[0]
    at_default = next(r for r in results if r["motion"] == FIXED_MOTION)
    helped = best["motion"] != FIXED_MOTION and best["mean"] < at_default["mean"] - 1e-6
    print(f"  BEST motion={best['motion']} mean={best['mean']} "
          f"(default 0.55 mean={at_default['mean']}) -> "
          f"{'IMPROVED' if helped else 'inert/no gain'}", flush=True)

    saved = None
    if save and helped:
        saved = save_into_project(project, {"motion_sensitivity": best["motion"]}, store)
        print(f"  saved -> {saved}", flush=True)
    elif save:
        print("  (gap-bridging inert — keeping 0.55, no re-save)", flush=True)

    summary = {"project": project, "pass": "gap-bridging", "best_motion": best["motion"],
               "helped": helped, "default_mean": at_default["mean"],
               "results": results, "saved_path": saved}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{project}_pass2.json").write_text(json.dumps(summary, indent=2))
    return summary


# derived dict key for each sweep-base config key
def _map(k):
    return {"mog2_var_threshold": "var_threshold", "mog2_scale": "mog2_scale",
            "person_height_px": "person_height_px",
            "person_height_min_ratio": "person_height_min_ratio",
            "person_height_max_ratio": "person_height_max_ratio",
            "yolo_imgsz": "yolo_imgsz", "blur_budget_ms": "blur_budget_ms"}[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="run a single project (substring match)")
    ap.add_argument("--dry-run", action="store_true", help="do not save into projects/")
    ap.add_argument("--pass2", action="store_true",
                    help="pass 2: sweep gap-bridging on the already-set best configs")
    a = ap.parse_args()
    store = cs.ConfigStore()
    items = [(p, s) for p, s in PROJECTS.items()
             if not a.only or a.only in p]
    print(f"batch{' PASS-2' if a.pass2 else ''}: {len(items)} project(s), "
          f"save={not a.dry_run}", flush=True)

    if a.pass2:
        rows = ["# Pass-2 gap-bridging — 2026-06-16", "",
                "| project | best motion | default(0.55) mean | best mean | helped |",
                "|---------|:-----------:|-------------------:|----------:|:------:|"]
        for project, scenarios in items:
            try:
                s = run_project_pass2(project, scenarios, save=not a.dry_run, store=store)
            except Exception as e:  # noqa: BLE001
                print(f"  !! {project} FAILED: {e}", flush=True)
                rows.append(f"| {project} | ERROR | - | - | - |")
                continue
            rows.append(f"| {project} | {s['best_motion']} | {s['default_mean']} | "
                        f"{s['results'][0]['mean']} | "
                        f"{'YES' if s['helped'] else '-'} |")
            (OUT / "pass2_table.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
        print("\n" + "\n".join(rows))
        return

    table = ["# Best-effort baseline (re-derived + CLAHE/conf/intermittent, seed-centered) — 2026-06-16", "",
             "| project | clahe/conf/interm | gamma | mean | worst | pass | IR-limited |",
             "|---------|-------------------|------:|-----:|------:|:----:|:----------:|"]
    for project, scenarios in items:
        t0 = time.time()
        try:
            s = run_project(project, scenarios, save=not a.dry_run, store=store)
        except Exception as e:  # noqa: BLE001
            print(f"  !! {project} FAILED: {e}", flush=True)
            table.append(f"| {project} | ERROR | - | - | - | - | - |")
            continue
        b = s["best"]
        table.append(
            f"| {project} | {b['clahe']}/{b['conf']}/{('on' if b['intermittent'] else 'off')} | "
            f"{s['derived']['gamma']} | {b['mean']} | {b['worst']} | "
            f"{b['n_pass']}/{len(scenarios)} | "
            f"{'YES' if s['derived']['ir_limited'] else '-'} |")
        (OUT / "baseline_table.md").write_text("\n".join(table) + "\n", encoding="utf-8")
        print(f"  ({project} done in {(time.time()-t0)/60:.1f} min)", flush=True)
    print("\n" + "\n".join(table))


if __name__ == "__main__":
    main()
