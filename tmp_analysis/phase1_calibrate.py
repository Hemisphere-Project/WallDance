"""Phase 1 — agent-run calibration per project (ROADMAP §4.2).

Per IDS-rig project:
  1. Pool the corpus-survey data -> calib1/calib2 derivations (var/scale from
     the FP sweep, CLAHE seed by noise, enhance+gamma by measured effect,
     person height/ratios from pooled detections, imgsz suggestion).
  2. Build the auto exclusion mask headless over the project's scenario
     window(s) (MOG2 warmup, then start/finish_exclusion_calibration).
  3. known-N joint search (tune.py coordinate descent on the detect cache)
     over confidence x var x theta_s, multi-scenario per project.
  4. Before/after full replays on each scenario (current latest config vs the
     calibrated result) + pass-line verdicts.
  5. Write a normal timestamped project save (config_schema v2-aware) + a
     per-project markdown report under tmp_analysis/phase1/.

Run from application/:
    .venv/Scripts/python.exe ../tmp_analysis/phase1_calibrate.py [--only PROJECT]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APP = REPO / "application"
for p in (str(APP / "tests"), str(APP / "src"), str(APP)):
    if p not in sys.path:
        sys.path.insert(0, p)

import replay  # noqa: E402
import scoring  # noqa: E402
import tune  # noqa: E402
import config_schema  # noqa: E402
from calib2 import select_imgsz  # noqa: E402

SCEN_DIR = APP / "tests" / "scenarios"
SURVEY = HERE / "survey"
OUT = HERE / "phase1"
OUT.mkdir(exist_ok=True)

# project -> scenarios (Phase 1 set; phones = corpus-only, cantine = one-off)
PROJECTS = {
    "0-TEST-verydark": ["dark-crowd"],
    "1_TANGO_HANGAR-texturedbg": ["texture-duo", "texture-wallhang"],
    "2_TANGO_HANGAR-whitebg": ["texture-aerial"],
    "3_TANGO_HANGAR-whitebg2": ["hangar-floor", "hangar-aerial"],
    "4_TANGO_HANGAR-whitebg3": ["white-duo", "white-walkers"],
    "5_TANGO_HANGAR-testflou": ["blur-runner"],
    "6_TANGO_TOGO-night": ["outdoor-night"],
    "7_TANGO_TOGO-day": ["outdoor-sitter"],
}
# slots 8/9 moved whitebg -> texturedbg after the survey ran; remap their pools
SURVEY_REMAP = {("2_TANGO_HANGAR-whitebg", 8): "1_TANGO_HANGAR-texturedbg",
                ("2_TANGO_HANGAR-whitebg", 9): "1_TANGO_HANGAR-texturedbg"}
# bulk-copied wrong-scene ROI -> disable (operator re-sets in-app if wanted)
DROP_ROI = {"5_TANGO_HANGAR-testflou", "6_TANGO_TOGO-night", "7_TANGO_TOGO-day"}

SPACE = {
    "confidence": [0.25, 0.4, 0.55],
    "mog2_var_threshold": [8, 16],
    "crossval_skel_min_kpts": [8, 10],
}


def load_survey(project: str):
    rows = []
    for f in SURVEY.glob("*.json"):
        if f.name == "survey_summary.json":
            continue
        d = json.loads(f.read_text())
        if "yolo_raw" not in d:
            continue
        proj = SURVEY_REMAP.get((d["project"], d["slot"]), d["project"])
        if proj == project:
            rows.append(d)
    return rows


def auto_gamma(median_luma: float, target: float = 110.0) -> float:
    med = max(float(median_luma), 1.0)
    if med >= target:
        return 1.0
    g = math.log(med / 255.0) / math.log(target / 255.0)
    return float(np.clip(g, 0.8, 2.2))


def derive(project: str, slots: list, base: dict) -> dict:
    """Calib1/2 derivations from the pooled survey data."""
    heights, raw_cov, enh_cov = [], [], []
    bright, noise = [], []
    long_side = 1488
    for d in slots:
        long_side = max(d["frame_size"])
        for r in d["yolo_enhanced"]:
            heights += [det[1] for det in r["dets"] if det[0] >= 0.25]
        sc = d.get("scene") or {}
        if sc:
            bright.append(sc["brightness_mean"])
            noise.append(sc["noise_sigma"])
        n = d.get("expected_n")
        if n:
            rc = d["yolo_raw"]
            ec = d["yolo_enhanced"]
            raw_cov.append(np.mean([sum(1 for x in r["dets"] if x[0] >= .25) >= n for r in rc]))
            enh_cov.append(np.mean([sum(1 for x in r["dets"] if x[0] >= .25) >= n for r in ec]))

    med_b = float(np.median(bright)) if bright else 60.0
    med_n = float(np.median(noise)) if noise else 2.0
    enhance_wins = (np.mean(enh_cov) >= np.mean(raw_cov) - 0.02) if raw_cov else True

    h = np.array(heights)
    med_h = float(np.median(h))
    min_r = float(np.clip(np.percentile(h, 5) / med_h, 0.2, 0.8))
    max_r = float(np.clip(np.percentile(h, 95) / med_h, 1.5, 4.0))

    sugg, _ok, _net = select_imgsz(med_h, long_side)
    cur = int(base.get("yolo_imgsz", 960))
    imgsz = min(max(cur, sugg), 1280)

    d = {
        "person_height_px": int(round(med_h)),
        "person_height_min_ratio": round(min_r, 2),
        "person_height_max_ratio": round(max_r, 2),
        "yolo_imgsz": imgsz,
        "mog2_var_threshold": 8.0,   # FP sweep picked 8/0.7 on every slot
        "mog2_scale": 0.7,
        "clahe_clip": 1.5 if med_n > 4.0 else 2.5,
        "enhance_enabled": bool(enhance_wins),
        "brightness_threshold": 131,
        "gamma": round(auto_gamma(med_b), 2) if enhance_wins else 1.0,
        "greyscale": True,
    }
    if project in DROP_ROI:
        d["roi_enabled"] = False
    diag = {"median_brightness": round(med_b, 1), "median_noise": round(med_n, 2),
            "raw_cov": round(float(np.mean(raw_cov)), 3) if raw_cov else None,
            "enh_cov": round(float(np.mean(enh_cov)), 3) if enh_cov else None,
            "pooled_heights": len(heights), "imgsz_suggested": sugg}
    return d, diag


def build_exclusion(config: dict, manifests: list) -> tuple:
    """Headless exclusion-mask build over the scenario windows."""
    model_name = config.get("model", "yolo11x-pose")
    imgsz = int(config.get("yolo_imgsz", 1280))
    proc = replay._build_processor(config, model_name, imgsz)
    proc.tracker.reset()
    import tempfile
    proc.tracker.logger.start_session(tempfile.mkdtemp(prefix="wd_excl_"))
    started = False
    fed = 0
    for m in manifests:
        video = replay._find_recording(m["project"], m["slot"])
        cap = cv2.VideoCapture(str(video))
        warm = max(0, m["start"] - 50)
        cap.set(cv2.CAP_PROP_POS_FRAMES, warm)
        for i in range(m["start"] + m["frames"] - warm):
            ok, frame = cap.read()
            if not ok:
                break
            if not started and i >= 50:
                proc.start_exclusion_calibration()
                started = True
            proc.process(frame, need_preview=False, frame_number=i)
            fed += 1
        cap.release()
    res = proc.finish_exclusion_calibration()
    grid, cells = proc.get_exclusion()
    proc.tracker.logger.close()
    return list(grid), [list(c) for c in cells], fed, res


def run_search(project: str, manifests: list, base_config: dict):
    envs = []
    for m in manifests:
        env = tune.ScenarioEnv(str(SCEN_DIR / f"{m['name']}.json"))
        env.base_config = json.loads(json.dumps(base_config))
        envs.append(env)
    tuner = tune.Tuner(envs)
    start = {"confidence": base_config.get("confidence", 0.4)}
    best, score, history = tune.coordinate_descent(tuner, SPACE, start, max_passes=2)
    return best, score, tuner.n_evals


def full_replay_score(config: dict, manifest: dict) -> dict:
    video = replay._find_recording(manifest["project"], manifest["slot"])
    summary = replay.replay_recording(
        str(video), json.loads(json.dumps(config)),
        model_name=config.get("model", "yolo11x-pose"),
        imgsz=int(config.get("yolo_imgsz", 1280)),
        start_frame=manifest["start"], max_frames=manifest["frames"])
    result = scoring.score_timeline(summary["per_frame"], manifest)
    verdict = scoring.evaluate_pass(result, manifest)
    return {"score": result["score"],
            "drop": result["components"]["drop_rate"],
            "ghost": result["components"]["ghost_rate"],
            "longest_drop_s": result["raw"]["longest_drop_seconds"],
            "passed": verdict["passed"]}


def write_config(project: str, updates: dict) -> Path:
    pdir = REPO / "projects" / project
    latest = sorted((f for f in pdir.glob("*.json") if not f.name.startswith("_")),
                    key=lambda f: f.stat().st_mtime, reverse=True)[0]
    raw = json.loads(latest.read_text())
    raw.pop("_meta", None)
    cfg = config_schema.migrate(raw)
    active = cfg.get("active_profile", "show")
    for k, v in updates.items():
        if k in config_schema.PROFILE_KEYS:
            cfg["profiles"][active][k] = v
            cfg.pop(k, None)  # avoid stale shadowed top-level copies
        else:
            cfg[k] = v
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{project}_{ts}.json"
    cfg["_meta"] = {"project": project, "saved_at": datetime.now().isoformat(),
                    "filename": fname,
                    "note": "Phase 1 agent calibration (docs/CORPUS_ANALYSIS.md; "
                            "tmp_analysis/phase1_calibrate.py)"}
    out = pdir / fname
    out.write_text(json.dumps(cfg, indent=2))
    return out


def process_project(project: str, scen_names: list, write: bool = True):
    t0 = time.time()
    print(f"\n##### {project} #####", flush=True)
    manifests = [scoring.load_scenario(SCEN_DIR / f"{n}.json") for n in scen_names]
    base = replay._latest_config(project)
    if base is None:
        raise SystemExit(f"{project}: no saved config")

    slots = load_survey(project)
    derived, diag = derive(project, slots, base)
    print(f"derived: {derived}\n  diag: {diag}", flush=True)

    candidate = {**base, **derived}

    print("building exclusion mask ...", flush=True)
    grid, cells, fed, _res = build_exclusion(candidate, manifests)
    candidate["exclusion_grid"] = grid
    candidate["exclusion_cells"] = cells
    print(f"  exclusion: {len(cells)} cells over {fed} frames: {cells}", flush=True)

    print("known-N search ...", flush=True)
    best, search_score, n_evals = run_search(project, manifests, candidate)
    candidate.update(best)
    # sensitivity macro anchors follow the calibrated operating point (bug #8)
    candidate["sensitivity"] = 50.0
    candidate["sensitivity_conf_seed"] = candidate.get("confidence", 0.4)
    candidate["sensitivity_var_anchor"] = float(candidate.get("mog2_var_threshold", 8.0))
    print(f"  best={best} mean_score={search_score:.4f} ({n_evals} evals)", flush=True)

    print("before/after replays ...", flush=True)
    rows = []
    for m in manifests:
        before = full_replay_score(base, m)
        after = full_replay_score(candidate, m)
        rows.append((m["name"], before, after))
        print(f"  {m['name']}: before {before} -> after {after}", flush=True)

    # Keep the better config (full-pipeline mean score decides) -- a
    # calibration pass must never regress a scene the operator already had
    # working (TOGO-night smoke test: enhancement trades drops for static
    # facade ghosts the exclusion mask cannot catch, and loses on net).
    mean_before = float(np.mean([b["score"] for _, b, _ in rows]))
    mean_after = float(np.mean([a["score"] for _, _, a in rows]))
    adopt = mean_after <= mean_before
    print(f"decision: mean before={mean_before:.4f} after={mean_after:.4f} -> "
          f"{'ADOPT calibrated' if adopt else 'RETAIN current config'}", flush=True)

    saved = None
    if write and not adopt:
        write = False
    if write:
        # strip non-config keys the app never persists
        for k in ("_meta",):
            candidate.pop(k, None)
        saved = write_config(project, {k: v for k, v in candidate.items()
                                       if k not in base or base.get(k) != v})
        print(f"saved: {saved}", flush=True)

    # report
    lines = [f"# Phase 1 calibration — {project}", "",
             f"*{datetime.now().isoformat(timespec='seconds')} — "
             f"{time.time()-t0:.0f}s; scenarios: {', '.join(scen_names)}*", "",
             "## Derived settings", "```json",
             json.dumps({**derived, **best,
                         "exclusion_cells": cells}, indent=1), "```", "",
             f"Survey diagnostics: {json.dumps(diag)}", "",
             "## Before / after (full pipeline, scored vs verified GT)", "",
             "| scenario | score | drop | ghost | longest drop | pass |",
             "|---|---|---|---|---|---|"]
    for name, b, a in rows:
        lines.append(f"| {name} (before) | {b['score']:.3f} | {b['drop']:.3f} | "
                     f"{b['ghost']:.3f} | {b['longest_drop_s']:.2f}s | "
                     f"{'PASS' if b['passed'] else 'fail'} |")
        lines.append(f"| {name} (**after**) | **{a['score']:.3f}** | {a['drop']:.3f} | "
                     f"{a['ghost']:.3f} | {a['longest_drop_s']:.2f}s | "
                     f"{'**PASS**' if a['passed'] else 'fail'} |")
    lines += ["", f"**Decision:** mean score before {mean_before:.4f} vs calibrated "
              f"{mean_after:.4f} → {'**calibrated config adopted**' if adopt else '**current config retained** (calibration candidate regressed on net — see Phase 2 notes)'}",
              "", f"Config saved: `{saved}`" if saved else "Config not written.",
              "", "Operator pass: load the project, play the scenario slot, check "
              "boxes/IDs in preview, nudge sensitivity to taste, save."]
    (OUT / f"{project}.md").write_text("\n".join(lines), encoding="utf-8")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--dry", action="store_true", help="don't write project configs")
    args = ap.parse_args()
    for project, scens in PROJECTS.items():
        if args.only and args.only != project:
            continue
        if (OUT / f"{project}.md").exists():
            print(f"skip (report exists): {project}")
            continue
        try:
            process_project(project, scens, write=not args.dry)
        except Exception as e:
            print(f"ERROR {project}: {e!r}", flush=True)
            (OUT / f"{project}.ERROR.txt").write_text(repr(e), encoding="utf-8")


if __name__ == "__main__":
    main()
