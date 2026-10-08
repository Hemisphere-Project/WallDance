#!/usr/bin/env python3
"""D33 (2026-10-08): move an EXISTING project to the validated D27 detection settings.

The code defaults changed in D33 (core/config.py DETECTION_DEFAULTS), but a project
file that stores a value keeps it -- so a project saved under the old defaults
(yolo_imgsz 800, confidence 0.25, intermittent confirm OFF, like the laptop's
`default` of 2026-10-07) still runs them.  This writes ONE new timestamped config
from the project's newest one (the app loads the newest), with only the D27 keys
changed:

  shared        model yolo11x-pose, yolo_imgsz 1280, tracking_mode yolo_first,
                tracker_intermittent_confirm true
  profile       confidence 0.15, sensitivity_conf_seed 0.15 (the dial re-anchored
                on it), sensitivity 50          -- the active profile by default

gamma / CLAHE / MOG2 / exposure / gain / ROI / exclusion mask / dancer ids are NOT
touched (per venue: Calibrate sets them).  The diff below is computed on the whole
file, so it shows everything that differs.  The old files stay in the history (the
top bar's config dropdown reloads one).  Generalises project_config_d27_d28.py.

  # dev box (from the repo root or application/):
  python3 tmp_analysis/plan25m/apply_d33.py --project mur30m-0710 --dry-run
  # the laptop (only this script is uploaded; it runs in <root>/application, so the
  # projects are ../projects -- the DEV slot shares LIVE's through a junction):
  python3 extra/wdremote.py py tmp_analysis/plan25m/apply_d33.py -- --project default --dry-run
  python3 extra/wdremote.py py tmp_analysis/plan25m/apply_d33.py -- --project default

If the app has the project open, reload it (top bar: the project's newest config, or
restart) BEFORE any save there: an app save writes its in-memory values -- the old
ones -- as a newer file.  Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

D33_SHARED = {"model": "yolo11x-pose", "yolo_imgsz": 1280, "tracking_mode": "yolo_first",
              "tracker_intermittent_confirm": True}
D33_PROFILE = {"confidence": 0.15, "sensitivity_conf_seed": 0.15, "sensitivity": 50.0}
# calibration provenance of the replaced values (the "Last calibrated" line would
# otherwise credit the dancers pass with a value it no longer set)
STALE_PROVENANCE = ("confidence", "imgsz")
PER_VENUE = ("gamma", "clahe_clip", "mog2_var_threshold", "mog2_scale", "sensitivity_var_anchor",
             "ids_exposure_us", "ids_gain_db", "person_height_px", "roi_enabled")
SENS_VAR_KNEE = 75.0           # core/config.py: past it the dial lowers varThreshold


def find_projects_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg).resolve()
    here = Path(__file__).resolve().parent
    cands = [Path.cwd() / "projects", Path.cwd().parent / "projects"]
    cands += [p / "projects" for p in here.parents]
    for c in cands:
        if c.is_dir():
            return c.resolve()
    raise SystemExit("apply_d33: no projects/ found (cwd, its parent, the script's parents); "
                     "pass --projects DIR")


def config_files(pdir: Path) -> list[Path]:
    """The project's saves, newest first -- as the app lists them
    (config_store.list_config_files: *.json minus `_` specials, by mtime then name)."""
    files = [p for p in pdir.glob("*.json") if not p.name.startswith("_")]
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def newest_valid(pdir: Path) -> tuple[Path, dict, int]:
    files = config_files(pdir)
    for p in files:
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"  (skipping unreadable {p.name}, as the app does)")
            continue
        if isinstance(cfg, dict):
            return p, cfg, len(files)
    raise SystemExit(f"apply_d33: no readable config in {pdir}")


def is_v2(cfg: dict) -> bool:
    return cfg.get("config_version", 1) >= 2 and isinstance(cfg.get("profiles"), dict)


def paths(d, prefix=""):
    """{dotted path: leaf value} of a JSON object (lists are leaves, {} is nothing)."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(paths(v, key + "."))
        else:
            out[key] = v
    return out


def apply_d33(cfg: dict, profiles: list[str] | None, all_profiles: bool) -> tuple[dict, list[str], list[str]]:
    """-> (new config, target profile names, warnings).  Pure: `cfg` is not modified."""
    new = json.loads(json.dumps(cfg))
    warnings: list[str] = []
    new.update(D33_SHARED)
    if is_v2(new):
        bundles = new["profiles"]
        active = new.get("active_profile") if new.get("active_profile") in bundles else "show"
        if all_profiles:
            targets = [n for n, b in bundles.items() if isinstance(b, dict) and b]
        else:
            targets = profiles or [active]
        for name in targets:
            if name not in bundles or not isinstance(bundles[name], dict):
                raise SystemExit(f"apply_d33: no profile {name!r} (have {sorted(bundles)})")
            if not bundles[name]:
                warnings.append(f"profile {name!r} is empty (the app seeds it from the live values "
                                "on first switch); setting the D27 keys in it anyway")
            bundles[name].update(D33_PROFILE)
        for k in D33_PROFILE:          # a flat copy (v1-style reader) follows the profile
            if k in new:
                new[k] = D33_PROFILE[k]
    else:
        targets = ["(flat v1 file)"]
        new.update(D33_PROFILE)
    cs = new.get("calibration_state")
    if isinstance(cs, dict):
        for k in STALE_PROVENANCE:
            cs.pop(k, None)
    # what the dial re-anchoring at 50 means for MOG2 (left alone: per venue)
    for name in (targets if is_v2(cfg) else [None]):
        old = (cfg["profiles"].get(name) or {}) if name else cfg
        sens = old.get("sensitivity", cfg.get("sensitivity"))
        var = old.get("mog2_var_threshold", cfg.get("mog2_var_threshold"))
        anchor = old.get("sensitivity_var_anchor", cfg.get("sensitivity_var_anchor"))
        if (sens is not None and float(sens) > SENS_VAR_KNEE and var is not None
                and anchor is not None and float(var) != float(anchor)):
            warnings.append(f"{name or 'config'}: the dial was at {sens} (> {SENS_VAR_KNEE:.0f}), so the "
                            f"saved mog2_var_threshold {var} is the dial-lowered value, not the anchor "
                            f"{anchor}; left as is (per venue) -- at dial 50 the app would use {anchor}")
    if cfg.get("use_tensorrt") is False:
        warnings.append("use_tensorrt is false: x@1280 on PyTorch is 3-7x slower -- tick TensorRT "
                        "(left as is: not a D27 key)")
    return new, targets, warnings


def free_name(pdir: Path, project: str) -> tuple[str, float]:
    now = time.time()
    t = now
    while True:
        name = f"{project}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(t))}.json"
        if not (pdir / name).exists():
            return name, now
        t += 1.0


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def fmt(v) -> str:
    return json.dumps(v) if not isinstance(v, str) else v


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True, help="project folder name (e.g. default)")
    ap.add_argument("--projects", default=None,
                    help="projects dir (default: ./projects, ../projects, or above the script)")
    ap.add_argument("--from", dest="src", default=None,
                    help="source config file name in the project (default: the newest, as the app loads)")
    ap.add_argument("--profile", action="append", default=None,
                    help="lighting profile to change (repeatable; default: the active one)")
    ap.add_argument("--all-profiles", action="store_true",
                    help="change every non-empty lighting profile")
    ap.add_argument("--dry-run", action="store_true", help="print the diff, write nothing")
    a = ap.parse_args(argv)

    projects = find_projects_dir(a.projects)
    pdir = projects / a.project
    if not pdir.is_dir():
        raise SystemExit(f"apply_d33: no project {a.project!r} in {projects}")
    if a.src:
        src = pdir / a.src
        cfg = json.loads(src.read_text(encoding="utf-8"))
        n_files = len(config_files(pdir))
    else:
        src, cfg, n_files = newest_valid(pdir)
    new, targets, warnings = apply_d33(cfg, a.profile, a.all_profiles)

    before = paths({k: v for k, v in cfg.items() if k != "_meta"})
    after = paths({k: v for k, v in new.items() if k != "_meta"})
    changed = sorted(k for k in set(before) | set(after) if before.get(k, "<absent>") != after.get(k, "<absent>"))
    allowed = set(D33_SHARED) | set(D33_PROFILE) | {f"calibration_state.{k}" for k in STALE_PROVENANCE}
    stray = [k for k in changed if k.split(".")[-1] not in allowed
             and not (k.startswith("calibration_state.") and k.split(".")[1] in STALE_PROVENANCE)]
    assert not stray, f"apply_d33 would change more than the D27 keys: {stray}"

    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(src.stat().st_mtime))
    name, now = free_name(pdir, a.project)
    print(f"apply_d33: project {a.project}   (projects dir {projects})")
    print(f"  from     {src.name}   ({'--from' if a.src else f'newest of {n_files}'}, mtime {stamp})")
    print(f"  to       {name}   ({'DRY RUN, not written' if a.dry_run else 'NEW file; the old ones stay in the history'})")
    print(f"  profile  {', '.join(targets)}")
    if changed:
        w = max(len(k) for k in changed)
        print(f"  {'key'.ljust(w)}   before -> after")
        for k in changed:
            print(f"  {k.ljust(w)}   {fmt(before.get(k, '<absent>'))} -> {fmt(after.get(k, '<absent>'))}")
    same = sorted(k for k in after if k.split(".")[-1] in allowed and k not in changed
                  and not k.startswith("calibration_state."))
    if same:
        print("  already D27: " + ", ".join(f"{k}={fmt(after[k])}" for k in same))
    flat_after = dict(new)
    if is_v2(new):
        act = new.get("active_profile") or "show"
        flat_after = {k: v for k, v in new.items() if k not in ("profiles", "config_version", "active_profile")}
        flat_after.update(new["profiles"].get(act) or {})
    print("  untouched (per venue): " + ", ".join(f"{k}={fmt(flat_after[k])}" for k in PER_VENUE
                                                   if k in flat_after))
    models = projects.parent / "models"
    engine = models / f"{D33_SHARED['model']}_{D33_SHARED['yolo_imgsz']}.engine"
    if models.is_dir() and not engine.exists():
        warnings.append(f"NO ENGINE {engine}: the app will prompt to build it (a few minutes) or run "
                        "PyTorch (3-7x slower) -- run extra/build_engines --default-only first")
    for msg in warnings:
        print(f"  WARNING: {msg}")

    result = {"project": a.project, "from": src.name, "to": name, "dry_run": a.dry_run,
              "profiles": targets, "changes": {k: [before.get(k), after.get(k)] for k in changed},
              "warnings": warnings, "written": None}
    if not changed:
        print("  nothing to change: the project already runs the D27 detection settings")
    elif not a.dry_run:
        meta = cfg.get("_meta") if isinstance(cfg.get("_meta"), dict) else {}
        new["_meta"] = {"project": meta.get("project") or a.project,
                        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                        "filename": name, "derived_from": src.name,
                        "note": "D33 (apply_d33.py): the D27 detection settings -- yolo_first, "
                                "confidence 0.15 (dial anchored on it), intermittent confirm ON, "
                                "x@1280; nothing else changed"}
        target = pdir / name
        atomic_write(target, json.dumps(new, indent=2))
        newest = max(p.stat().st_mtime for p in config_files(pdir))
        if target.stat().st_mtime < newest:          # an older save dated in the future
            os.utime(target, (newest + 1.0, newest + 1.0))
        assert config_files(pdir)[0] == target, "the new file is not the newest save"
        result["written"] = str(target)
        print(f"  written  {target}")
        print("  If the app has this project open: reload it (top bar) or restart BEFORE any save there.")
    out = os.environ.get("WD_REMOTE_OUT")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "apply_d33.json").write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
