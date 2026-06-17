#!/usr/bin/env python3
"""sweep_project.py — sweep the user sliders for the best per-project config.

Companion to ``calibrate_project.py``. Given a project's slots (scenarios) and an
optional re-derived base config, grid-sweep the three live user sliders —
``clahe_clip`` (CLAHE), ``confidence`` (Dial A), ``motion_sensitivity`` (Dial B) —
on the **GPU+TRT show path** and pick the combo with the best score **averaged
across the project's slots** (mean + worst, via the per-scenario field-priority
score). This implements the agreed best-effort-baseline procedure: a project's
slots cohere (they model calib-vs-show drift), so one robust config must hold
across them rather than overfit a single slot.

Run on TRT directly — the detect_cache mis-estimates exactly CLAHE (cv2<->kornia)
and the motion/bridge knobs (G1/G2), so the cheap cache is not trustworthy here.

    # sweep on a re-derived base (from calibrate_project.py --out)
    python tests/sweep_project.py tests/scenarios/hangar-floor.json \
        tests/scenarios/hangar-aerial.json \
        --base tmp/whitebg2_rederived.json --out tmp/whitebg2_best.json

Knob grids default to a coarse, center-on-seed set; override with --clahe/--conf/
--motion (comma lists). Each (combo x slot) is one TRT replay (~17 s).
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPLAY = HERE / "replay.py"
PY = sys.executable

# Base-config keys forwarded as --set overrides (structural re-derivation);
# the three swept knobs are applied on top and win.
_BASE_KEYS = ("gamma", "mog2_var_threshold", "mog2_scale", "person_height_px",
              "person_height_min_ratio", "person_height_max_ratio",
              "yolo_imgsz", "blur_budget_ms")


def _run(scenario, base_sets, knobs):
    sets = list(base_sets)
    for k, v in knobs.items():
        sets.append(f"{k}={v}")
    cmd = [PY, str(REPLAY), "--scenario", str(scenario), "--trt", "--score"]
    for s in sets:
        cmd += ["--set", s]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    try:
        post = p.stdout.split("=== SCORE ===", 1)[1]
        score_part, pass_part = post.split("=== PASS LINE ===", 1)
        score = json.loads(score_part.strip())
        passline = json.loads(pass_part.strip())
        return score, passline
    except Exception:
        return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenarios", nargs="+", help="scenario JSON(s) of ONE project")
    ap.add_argument("--base", default=None, help="re-derived merged config JSON")
    ap.add_argument("--clahe", default="1.0,2.5,4.0,6.0")
    ap.add_argument("--conf", default="0.4,0.5,0.6")
    ap.add_argument("--motion", default="0.3,0.55,0.8")
    ap.add_argument("--out", default=None, help="write best merged config here")
    ap.add_argument("--report", default=None, help="write the ranked table (md) here")
    a = ap.parse_args()

    base = json.loads(Path(a.base).read_text()) if a.base else {}
    base_sets = [f"{k}={base[k]}" for k in _BASE_KEYS if k in base]
    grids = {
        "clahe_clip": [float(x) for x in a.clahe.split(",")],
        "confidence": [float(x) for x in a.conf.split(",")],
        "motion_sensitivity": [float(x) for x in a.motion.split(",")],
    }
    combos = [dict(zip(grids, vals)) for vals in itertools.product(*grids.values())]
    print(f"{len(combos)} combos x {len(a.scenarios)} slots = "
          f"{len(combos) * len(a.scenarios)} TRT replays", flush=True)

    results = []
    for i, knobs in enumerate(combos, 1):
        per_slot = []
        for scen in a.scenarios:
            score, passline = _run(scen, base_sets, knobs)
            if score is None:
                per_slot.append(None)
                continue
            per_slot.append({
                "scenario": Path(scen).stem,
                "score": score["score"],
                "drop": passline.get("checks", {}).get("drop_rate", {}).get("value"),
                "ghost": passline.get("checks", {}).get("ghost_rate", {}).get("value"),
                "passed": passline.get("passed"),
            })
        ok = [s for s in per_slot if s]
        mean = round(sum(s["score"] for s in ok) / len(ok), 5) if ok else 9.9
        worst = round(max(s["score"] for s in ok), 5) if ok else 9.9
        n_pass = sum(1 for s in ok if s["passed"])
        results.append({"knobs": knobs, "mean": mean, "worst": worst,
                        "n_pass": n_pass, "n_slots": len(a.scenarios),
                        "per_slot": per_slot})
        kk = " ".join(f"{k.split('_')[0]}={v}" for k, v in knobs.items())
        print(f"[{i}/{len(combos)}] {kk}  mean={mean} worst={worst} "
              f"pass={n_pass}/{len(a.scenarios)}", flush=True)

    results.sort(key=lambda r: (r["mean"], r["worst"]))
    best = results[0]
    out_dir = Path(a.out).parent if a.out else HERE
    (out_dir / "sweep_results.json").write_text(json.dumps(results, indent=2))

    lines = [f"# Slider sweep — {Path(a.scenarios[0]).stem} project "
             f"({len(a.scenarios)} slots), GPU+TRT 2026-06-16", "",
             "Ranked by mean score across slots (lower=better); worst = max slot.",
             "", "| rank | clahe | conf | motion | mean | worst | pass |",
             "|-----:|------:|-----:|-------:|-----:|------:|:----:|"]
    for rank, r in enumerate(results[:12], 1):
        k = r["knobs"]
        lines.append(f"| {rank} | {k['clahe_clip']} | {k['confidence']} | "
                     f"{k['motion_sensitivity']} | {r['mean']} | {r['worst']} | "
                     f"{r['n_pass']}/{r['n_slots']} |")
    lines += ["", f"**Best:** clahe={best['knobs']['clahe_clip']}, "
              f"confidence={best['knobs']['confidence']}, "
              f"motion_sensitivity={best['knobs']['motion_sensitivity']} "
              f"(mean {best['mean']}, worst {best['worst']}, "
              f"pass {best['n_pass']}/{best['n_slots']})"]
    report = "\n".join(lines) + "\n"
    if a.report:
        Path(a.report).write_text(report, encoding="utf-8")
    print("\n" + report)

    if a.out:
        cfg = dict(base)
        cfg.update(best["knobs"])
        Path(a.out).write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"wrote best config -> {a.out}")


if __name__ == "__main__":
    main()
