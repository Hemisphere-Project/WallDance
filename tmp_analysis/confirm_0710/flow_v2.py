#!/usr/bin/env python3
"""V2 "operator flow" on mur30m-0710 with the D27 settings, through application/tests/flow_check.py unchanged:
the project config (newest = as shot) gets the D27 keys in memory only (yolo_first, confidence 0.15,
intermittent confirm ON, x@1280); the replays get the same keys plus each take's recorded person height and
the live snapshot (plates/latest.npz) for the baseline (= V1); Calibrate's own snapshot replaces it after.

  flow_v2.py --empty slot_2_20261007_200742.avi:1700 --out DIR --label v2 [--calibrate-only]

--calibrate-only writes <out>/<label>_calibrate.json (the run_variant.py sets for V2 are derived from it).
Timelines already in --out (base_<stem>.json, <label>_<stem>.json) are reused by flow_check (its cache).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/data/WallDance/application/tests")
import flow_check as F  # noqa: E402  (imports replay -> CUDA bootstrap)

R = F.R
PROJECT = "mur30m-0710"
D27 = {"tracking_mode": "yolo_first", "confidence": 0.15, "sensitivity_conf_seed": 0.15,
       "tracker_intermittent_confirm": True, "yolo_imgsz": 1280}
PH = {"slot_2_20261007_200742.avi": 150, "slot_3_20261007_201246.avi": 253,
      "slot_4_20261007_201949.avi": 253, "slot_5_20261007_202412.avi": 253}
TAKES = list(PH)

_orig_latest = R._latest_config


def _latest_d27(project):
    cfg = _orig_latest(project)
    if cfg is not None and project == PROJECT:
        cfg.update(D27)
    return cfg


R._latest_config = _latest_d27
_orig_replay = F.replay


def _replay_d27(project, take, sets, timeline, engine_dir, imgsz):
    pre = [f"{k}={json.dumps(v)}" for k, v in D27.items()] + [f"person_height_px={PH[take]}",
                                                               "fg_plate=plates/latest.npz"]
    return _orig_replay(project, take, pre + list(sets), timeline, engine_dir, imgsz)


F.replay = _replay_d27


def main():
    args = sys.argv[1:]
    if "--calibrate-only" in args:
        out = Path(args[args.index("--out") + 1])
        label = args[args.index("--label") + 1]
        empty = args[args.index("--empty") + 1]
        eng = args[args.index("--engine-dir") + 1] if "--engine-dir" in args else \
            "/data/WallDance/tmp_analysis/confirm_0710/engines"
        os.environ["WD_ENGINE_DIR"] = eng
        out.mkdir(parents=True, exist_ok=True)
        cfg = R._latest_config(PROJECT)
        take, _, st = empty.partition(":")
        cal = F.calibrate(PROJECT, take, int(st or 0), cfg, cfg.get("model", "yolo11x-pose"),
                          int(cfg["yolo_imgsz"]), out)
        (out / f"{label}_calibrate.json").write_text(json.dumps(cal, indent=1, default=str))
        print(f"[flow_v2] Calibrate -> {cal['after']}  wall: {cal['wall_check']['log']}  plate: {cal['plate']}")
        return
    if "--takes" not in args:
        args += ["--takes", ",".join(TAKES)]
    sys.argv = [sys.argv[0], "--project", PROJECT] + args
    F.main()


if __name__ == "__main__":
    main()
