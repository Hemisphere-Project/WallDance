#!/usr/bin/env python3
"""Dancer size per scene: median height (px) of the slots seen by YOLO (fss 0), the ROI long side, and the
dancer's height in YOLO's input at 800 / 960 / 1280 -- which scenes are 'small' for YOLO.

  python sizes.py scene=timeline.json ...
"""
import json
import statistics as st
import sys

sys.path.insert(0, "/data/WallDance/application/tests")
import replay as R  # noqa: E402


def main():
    for arg in sys.argv[1:]:
        scene, tl = arg.split("=", 1)
        man = json.load(open(f"/data/WallDance/application/tests/scenarios/{scene}.json"))
        cfg = R.scenario_config(man)
        if "profiles" in cfg:
            import core.config_schema as cs
            cfg = cs.flatten(cfg)
        rows = json.load(open(tl))
        hs = [e["bbox"][3] for r in rows for e in (r.get("emitted") or {}).get("tracks") or []
              if e.get("fss") == 0 and e.get("state") == "live"]
        long_side = max(cfg.get("roi_w") or 0, cfg.get("roi_h") or 0) if cfg.get("roi_enabled") else None
        med = st.median(hs) if hs else float("nan")
        nets = {s: round(med * s / long_side) for s in (800, 960, 1280)} if long_side else {}
        print(f"{scene:22s} h med {med:6.0f} (p10 {sorted(hs)[len(hs)//10] if hs else 0:5.0f}, n {len(hs):5d})  ROI long "
              f"{long_side}  net {nets}  gamma {cfg.get('gamma')} clahe {cfg.get('clahe_clip')} conf {cfg.get('confidence')} "
              f"mode {cfg.get('tracking_mode')} imgsz {cfg.get('yolo_imgsz')}")


if __name__ == "__main__":
    main()
