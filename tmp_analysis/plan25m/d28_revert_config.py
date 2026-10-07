#!/usr/bin/env python3
"""D28 (2026-10-07): give mur25m-ceinture-0610 back last night's scene calibration.

The 10:33 Calibrate on the empty-wall playback (gamma 1.8, CLAHE 1.5, MOG2 16 @ 0.5) makes YOLO confirm
false persons on the night takes; the 2026-10-06 21:03 Aim values (gamma 0.73, CLAHE 2.5, MOG2 8 @ 0.7)
have none.  Writes a NEW timestamped config (the app loads the newest) from the project's latest one:
everything else (ROI, dancer ids, rig) is kept.  Run on the laptop from application/:

  python extra/wdremote.py --slot dev py tmp_analysis/plan25m/d28_revert_config.py -- [--projects DIR] [--dry-run]
"""
import argparse, glob, json, os, time
from pathlib import Path

P = "mur25m-ceinture-0610"
SCENE = {"gamma": 0.7300000190734863, "clahe_clip": 2.5, "mog2_var_threshold": 8.0, "mog2_scale": 0.7,
         "sensitivity_var_anchor": 8.0, "sensitivity": 50.0}
STAMP = {"ts": "2026-10-06 21:03", "epoch": 1791313385.9302988, "source": "aim"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--projects", default=str(Path.cwd().parent / "projects"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    pdir = Path(a.projects) / P
    files = sorted(glob.glob(str(pdir / f"{P}_*.json")), key=os.path.getmtime)
    if not files:
        raise SystemExit(f"no config in {pdir}")
    src = files[-1]
    cfg = json.load(open(src))
    prof = cfg.get("active_profile") or "show"
    target = cfg.setdefault("profiles", {}).setdefault(prof, {})
    before = {k: target.get(k, cfg.get(k)) for k in SCENE}
    target.update(SCENE)
    for k in SCENE:                       # flat copies (a v1-style reader) follow the profile
        if k in cfg:
            cfg[k] = SCENE[k]
    cs = cfg.setdefault("calibration_state", {})
    for k in ("gamma", "mog2_var_threshold", "mog2_scale", "clahe"):
        cs[k] = dict(cs.get(k) or {}, **STAMP)
    name = f"{P}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    cfg["_meta"] = dict(cfg.get("_meta") or {}, project=P, filename=name,
                        saved_at=time.strftime("%Y-%m-%dT%H:%M:%S"), note="D28: 2026-10-06 21:03 scene calibration restored")
    print(json.dumps({"from": os.path.basename(src), "to": name, "profile": prof, "before": before, "after": SCENE}))
    if not a.dry_run:
        with open(pdir / name, "w") as f:
            json.dump(cfg, f, indent=4)
        out = Path(os.environ.get("WD_REMOTE_OUT", "."))
        (out / "d28.json").write_text(json.dumps({"written": str(pdir / name)}))


if __name__ == "__main__":
    main()
