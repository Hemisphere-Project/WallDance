#!/usr/bin/env python3
"""Config for the common-frame night project (recut_common.py): the night project's D27+D28 config with the
project ROI moved from the 1776x1300 landscape frame into the common 1488x1300 frame (landscape crop at frame
x 144, y 0: sensor area 600,116).

  python make_common_config.py --src <night config .json> --dst-project <dir> [--dx 144 --dy 0]
"""
import argparse
import json
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst-project", required=True)
    ap.add_argument("--dx", type=int, default=144)
    ap.add_argument("--dy", type=int, default=0)
    ap.add_argument("--w", type=int, default=1488)
    ap.add_argument("--h", type=int, default=1300)
    a = ap.parse_args()
    cfg = json.loads(Path(a.src).read_text())
    prof = cfg["profiles"][cfg.get("active_profile", "show")]
    print("source:", {k: cfg.get(k) for k in ("roi_x", "roi_y", "roi_w", "roi_h", "roi_source_w", "roi_source_h",
                                             "yolo_imgsz", "tracking_mode", "tracker_intermittent_confirm",
                                             "person_height_px")})
    print("profile:", {k: prof.get(k) for k in ("gamma", "clahe_clip", "mog2_var_threshold", "mog2_scale",
                                               "confidence", "sensitivity_conf_seed")})
    cfg["roi_x"] = int(cfg["roi_x"]) - a.dx
    cfg["roi_y"] = int(cfg["roi_y"]) - a.dy
    cfg["roi_source_w"], cfg["roi_source_h"] = a.w, a.h
    assert cfg["roi_x"] >= 0 and cfg["roi_y"] >= 0
    assert cfg["roi_x"] + cfg["roi_w"] <= a.w and cfg["roi_y"] + cfg["roi_h"] <= a.h
    proj = Path(a.dst_project)
    proj.mkdir(parents=True, exist_ok=True)
    name = f"{proj.name}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    cfg["_meta"] = dict(cfg.get("_meta") or {}, project=proj.name, filename=name,
                        note=f"{Path(a.src).name} with the ROI mapped into the common {a.w}x{a.h} frame "
                             f"(landscape crop at {a.dx},{a.dy})")
    (proj / name).write_text(json.dumps(cfg, indent=4))
    print("written", proj / name, "ROI", cfg["roi_x"], cfg["roi_y"], cfg["roi_w"], cfg["roi_h"])


if __name__ == "__main__":
    main()
