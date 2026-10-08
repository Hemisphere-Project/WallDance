#!/usr/bin/env python3
"""Light numbers (8-bit DN as recorded) from scene/*.json -> light.json, + each take's camera nodes (.meta).

30 m (mur30m-0710): door / plain wall / floor boxes (every 5th frame, median); the walker's torso at the far wall
(V1 YOLO box conf >= 0.5, bottom >= 680 px, h <= 240 px: centre 24 % x 25 % of the box, median DN; and the median
of its foreground pixels); the belt on the stand (global BeltDetector blobs within 30 px of its position: peak,
blob mean, bbox-window mean) in the empty end segment, and its detection rate over the take while the walker is
not within 150 px of it; the worn belt (blobs inside the walker's GT box at the far wall).
25 m (mur25m-night-common): the same boxes moved 100 px left (same wall scale), the dancer's torso and worn belt
from the night flow-check baseline YOLO boxes (h <= 260 px)."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
T30 = {"slot_2_20261007_200742": 1700, "slot_3_20261007_201246": 1072, "slot_4_20261007_201949": 1242,
       "slot_5_20261007_202412": 1058}
T25 = ["slot_2_20261006_211831", "slot_3_20261006_212855", "slot_4_20261006_213310", "slot_6_20261006_214306",
       "slot_2_20261006_214531", "slot_7_20261006_220640"]
N25 = {"slot_2_20261006_211831": "s2a bright", "slot_3_20261006_212855": "s3 bright",
       "slot_4_20261006_213310": "s4 dark", "slot_6_20261006_214306": "s6 dark",
       "slot_2_20261006_214531": "s2c dark", "slot_7_20261006_220640": "s7 dark"}


def q(vals, qs=(10, 50, 90), nd=1):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return [round(float(np.percentile(vals, x)), nd) for x in qs]


def boxes_dn(rows):
    out = {}
    for k in ("door", "wall", "floor"):
        v = [r["dn"][k]["med"] for r in rows if r.get("dn")]
        out[k] = q(v)
    return out


def nodes(meta_path: Path):
    m = json.loads(meta_path.read_text())
    n = (m.get("camera") or {}).get("nodes") or {}
    c = m.get("config") or {}
    return {"ExposureTime_us": n.get("ExposureTime"), "Gain_node": n.get("Gain"), "PixelFormat": n.get("PixelFormat"),
            "BlackLevel": n.get("BlackLevel"), "cfg_exposure_us": c.get("ids_exposure_us"), "cfg_gain_db": c.get("ids_gain_db"),
            "sensor": n.get("SensorName")}


def main():
    res = {"30": {}, "25": {}}
    for stem, end0 in T30.items():
        sc = json.loads((HERE / "scene" / f"scene_{stem}.json").read_text())
        rows = sc["rows"]
        stands = [p for p in sc["stands"] if p]
        d = {"boxes": boxes_dn(rows), "nodes": nodes(Path(f"/data/WallDance/projects/mur30m-0710/recordings/{stem}.avi.meta"))}
        tor = [r["torso"]["med"] for r in rows if r.get("torso")]
        tor_fg = [r["torso"].get("fg_med") for r in rows if r.get("torso")]
        d["torso_far"] = {"n": len(tor), "med": q(tor), "fg_med": q(tor_fg)}

        def is_stand(b):
            return any(math.hypot(b["cx"] - sx, b["cy"] - sy) <= 30 for sx, sy, _n in stands)
        end = [b for r in rows if r["f"] >= end0 for b in r.get("belt") or [] if is_stand(b)]
        d["stand_belt_end"] = {"n": len(end), "frames": sum(1 for r in rows if r["f"] >= end0),
                               "peak": q([b["peak"] for b in end]), "mean": q([b["mean"] for b in end]),
                               "win_mean": q([b["win_mean"] for b in end]), "w": q([b["w"] for b in end]),
                               "h": q([b["h"] for b in end]), "sat": q([b["sat"] for b in end], nd=3),
                               "bg": q([b["bg"] for b in end])}
        # detection rate while the walker is away (GT centre > 150 px from the stand)
        n_ok = n_det = 0
        first = next((r["f"] for r in rows if any(is_stand(b) for b in r.get("belt") or [])), 0)
        d["stand_belt_first_f"] = first
        for r in rows:
            if r["f"] < first:
                continue
            g = r.get("gt")
            if g and g[4] >= 400:
                cx, cy = g[0] + g[2] / 2, g[1] + g[3] / 2
                if any(math.hypot(cx - sx, cy - sy) <= 150 for sx, sy, _n in stands):
                    continue
                if g[3] >= 350:          # walker close to the lens: skip (glare)
                    continue
            n_ok += 1
            n_det += any(is_stand(b) for b in r.get("belt") or [])
        d["stand_belt_rate"] = {"frames": n_ok, "detected": n_det, "rate": round(n_det / max(1, n_ok), 3)}
        worn = []
        for r in rows:
            g = r.get("gt")
            if not (g and g[4] >= 400 and g[3] <= 260 and 680 <= g[1] + g[3] <= 800):
                continue
            x, y, w, h = g[:4]
            for b in r.get("belt") or []:
                if is_stand(b):
                    continue
                if x - 0.3 * w <= b["cx"] <= x + 1.3 * w and y - 0.2 * h <= b["cy"] <= y + 1.2 * h:
                    worn.append(b)
        d["worn_belt_far"] = {"n": len(worn), "peak": q([b["peak"] for b in worn]), "mean": q([b["mean"] for b in worn]),
                              "win_mean": q([b["win_mean"] for b in worn]), "w": q([b["w"] for b in worn]),
                              "h": q([b["h"] for b in worn]), "sat": q([b["sat"] for b in worn], nd=3)}
        d["stands"] = stands
        res["30"][stem] = d
        print("30m", stem, json.dumps({k: d[k] for k in ("boxes", "torso_far", "stand_belt_end", "stand_belt_rate", "worn_belt_far")}))
    for stem in T25:
        p = HERE / "scene" / f"scene25_{stem}.json"
        if not p.exists():
            continue
        rows = json.loads(p.read_text())["rows"]
        d = {"name": N25[stem], "boxes": boxes_dn(rows),
             "nodes": nodes(Path(f"/data/WallDance/projects/mur25m-night-common/recordings/{stem}.avi.meta"))}
        tor = [r["torso"]["med"] for r in rows if r.get("torso")]
        hs = [r["yolo"][2] for r in rows if r.get("torso")]
        d["torso_far"] = {"n": len(tor), "med": q(tor), "h": q(hs)}
        worn = [b for r in rows for b in r.get("belt") or []]
        d["worn_belt"] = {"n": len(worn), "frames": sum(1 for r in rows if r.get("torso")),
                          "peak": q([b["peak"] for b in worn]), "mean": q([b["mean"] for b in worn]),
                          "win_mean": q([b["win_mean"] for b in worn]), "w": q([b["w"] for b in worn]),
                          "h": q([b["h"] for b in worn]), "sat": q([b["sat"] for b in worn], nd=3)}
        res["25"][stem] = d
        print("25m", stem, json.dumps({k: d[k] for k in ("boxes", "torso_far", "worn_belt")}))
    (HERE / "light.json").write_text(json.dumps(res, indent=1))
    print("wrote light.json")


if __name__ == "__main__":
    main()
