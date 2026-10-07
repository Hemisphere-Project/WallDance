#!/usr/bin/env python3
"""D28 + D27 (2026-10-07) for mur25m-ceinture-0610, one NEW timestamped config (the app loads the newest)
written from the project's latest one; everything else (ROI, dancer ids, rig) is kept.

D28: last night's scene calibration back.  The 10:33 Calibrate on the empty-wall playback (gamma 1.8, CLAHE
1.5, MOG2 16 @ 0.5) makes YOLO confirm false persons on the night takes; the 2026-10-06 21:03 Aim values
(gamma 0.73, CLAHE 2.5, MOG2 8 @ 0.7) have none.
D27 (Thomas: "switch to validated settings"): the detection settings every 2026-10-07 validation ran with --
yolo_first, confidence 0.15 (the sensitivity dial re-anchored on it), intermittent confirm ON, x@1280 (the
night dancers are ~127 px in a ~1320 px ROI: ~123 px in YOLO's input at 1280, ~77 px at the 800 the field ran).
Run on the laptop from application/:

  python extra/wdremote.py --slot dev py tmp_analysis/plan25m/project_config_d27_d28.py -- [--projects DIR] [--dry-run]
"""
import argparse, glob, json, os, time
from pathlib import Path

P = "mur25m-ceinture-0610"
SCENE = {"gamma": 0.7300000190734863, "clahe_clip": 2.5, "mog2_var_threshold": 8.0, "mog2_scale": 0.7,
         "sensitivity_var_anchor": 8.0, "sensitivity": 50.0,
         "confidence": 0.15, "sensitivity_conf_seed": 0.15}               # per-profile keys (D28 + D27)
SHARED = {"tracking_mode": "yolo_first", "tracker_intermittent_confirm": True, "yolo_imgsz": 1280}  # D27
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
    before.update({k: cfg.get(k) for k in SHARED})
    target.update(SCENE)
    cfg.update(SHARED)
    for k in SCENE:                       # flat copies (a v1-style reader) follow the profile
        if k in cfg:
            cfg[k] = SCENE[k]
    cs = cfg.setdefault("calibration_state", {})
    for k in ("gamma", "mog2_var_threshold", "mog2_scale", "clahe"):
        cs[k] = dict(cs.get(k) or {}, **STAMP)
    name = f"{P}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    cfg["_meta"] = dict(cfg.get("_meta") or {}, project=P, filename=name,
                        saved_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                        note="D28: 2026-10-06 21:03 scene calibration; D27: yolo_first, conf 0.15, intermittent, x@1280")
    print(json.dumps({"from": os.path.basename(src), "to": name, "profile": prof, "before": before,
                      "after": dict(SCENE, **SHARED)}))
    if not a.dry_run:
        with open(pdir / name, "w") as f:
            json.dump(cfg, f, indent=4)
        out = Path(os.environ.get("WD_REMOTE_OUT", "."))
        (out / "project_config.json").write_text(json.dumps({"written": str(pdir / name)}))


if __name__ == "__main__":
    main()
