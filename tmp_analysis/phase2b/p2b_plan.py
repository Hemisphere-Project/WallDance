#!/usr/bin/env python3
"""Print the pruned grid plan: per scenario, long side, net heights, cells."""
import json

import p2b_common as C


def main():
    manifests = C.load_scenarios()
    total = 0
    rows = []
    for m in manifests:
        cfg = m["_config"]
        ls = C.probe_long_side(cfg, m["_video"])
        kept = C.cell_imgsz_list(cfg, ls)
        pruned = [s for s in C.IMGSZ_PRESETS if s not in kept]
        nets = {s: round(C.net_height(cfg, s, ls), 1) for s in C.IMGSZ_PRESETS}
        n_cells = len(kept) * len(C.MODELS)
        total += n_cells
        rows.append({
            "scenario": m["name"], "frames": m["frames"],
            "pinned": f"{cfg.get('model')}@{cfg.get('yolo_imgsz')}",
            "pinned_conf": round(float(cfg.get("confidence", 0.25)), 3),
            "person_h": cfg.get("person_height_px"),
            "long_side": ls, "net_heights": nets,
            "kept_imgsz": kept, "pruned_imgsz": pruned, "cells": n_cells,
        })
    print(json.dumps(rows, indent=1))
    print(f"TOTAL CELLS: {total} (of {len(manifests) * len(C.MODELS) * len(C.IMGSZ_PRESETS)} unpruned)")


if __name__ == "__main__":
    main()
