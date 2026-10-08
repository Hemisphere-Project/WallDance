#!/usr/bin/env python3
"""30 m confirmation: metrics per variant x take from the run_variant.py timelines (tl/) and the scene pass
(scene/), against an independent walker ground truth (scene_measure.py "gt": two-background difference).
Writes results.json (everything the report needs).

Definitions (one walker, N = 1):
  in view      first..last frame with a walker blob >= 400 px (GT); the empty start / end are excluded
  far wall     GT box bottom 680-800 px and height <= 260 px (door: centre x 745-1000)
  near lens    GT box height >= 350 px or bottom >= 950 px; else "walking"
  coverage     share of in-view frames with >= 1 point sent; "on the walker": a point within
               max(0.75 GT height, 60 px) of the GT box centre (frames with a GT box)
  hole         >= 1 s (frames / the take's fps) without any point, inside the view window; cause = the GT zone
               most of its frames are in
  ghost frame  > 1 point; wrong frame = exactly one point and it is not on the walker
  static ghost a point on the light stand (+-45 px of its belt, -90/+70 px in y) or on the door while the
               walker is not there (> max(0.75 h, 60 px) away, or out of view)
  jumps / id switches / on_dancer / coasting / lag: tests/flow_check.py analyse() + quality()
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/data/WallDance/application/tests")
import flow_check as F  # noqa: E402

HERE = Path(__file__).resolve().parent
TAKES = {"slot_2_20261007_200742": 18.661, "slot_3_20261007_201246": 19.379,
         "slot_4_20261007_201949": 19.516, "slot_5_20261007_202412": 18.897}
SHORT = {"slot_2_20261007_200742": "slot 2", "slot_3_20261007_201246": "slot 3",
         "slot_4_20261007_201949": "slot 4", "slot_5_20261007_202412": "slot 5"}
VARIANTS = ["V0", "V1", "V1b", "V2", "V1r"]
DOOR_X = (745, 1000)
DOOR_Y = (400, 740)
W_FULL = 1776


def gt_of(scene_rows, heights=None):
    """frame -> (cx, cy, w, h, bottom, top, H): the walker's GT box (the difference blob loses the dark legs, so
    its height under-counts) + H, the walker's apparent height: the V1 YOLO box height (conf >= 0.15, on the
    blob) where there is one, interpolated in time between them (the walker moves slowly), else the blob's."""
    gt = {}
    for r in scene_rows:
        g = r.get("gt")
        if g and g[4] >= 400:
            x, y, w, h, a = g
            gt[r["f"]] = [x + w / 2, y + h / 2, w, h, y + h, y, float(h)]
    if heights:
        fs = sorted(f for f in heights if f in gt)
        if fs:
            hv = np.array([heights[f] for f in fs], float)
            for f in gt:
                gt[f][6] = max(gt[f][3], float(np.interp(f, fs, hv)))
    return {f: tuple(v) for f, v in gt.items()}


def walker_heights(rows, scene_rows):
    """frame -> V1 YOLO height on the walker blob (median of +-3 frames), for gt_of()."""
    raw = {}
    blobs = {r["f"]: r["gt"] for r in scene_rows if r.get("gt") and r["gt"][4] >= 400}
    for r in rows:
        g = blobs.get(r["frame"])
        if not g:
            continue
        x, y, w, h, _a = g
        c = [d for d in r.get("raw") or [] if d[0] >= 0.15 and x - 0.5 * d[3] <= d[1] <= x + w + 0.5 * d[3]
             and y - 0.4 * d[3] <= d[2] - d[3] / 2 <= y + 0.4 * d[3] + 20]
        if c:
            raw[r["frame"]] = max(c, key=lambda d: d[0])[3]
    fs = sorted(raw)
    out = {}
    for i, f in enumerate(fs):
        win = [raw[k] for k in fs[max(0, i - 3):i + 4] if abs(k - f) <= 6]
        out[f] = float(np.median(win))
    return out


def zone(g):
    if g is None:
        return "out"
    cx, cy, w, h, bot, top, H = g
    if H >= 400 or h >= 350 or bot >= 950:
        return "near lens"
    if H <= 160:
        return "far wall (door)" if DOOR_X[0] <= cx <= DOOR_X[1] else "far wall"
    return "walking"


def near(px, py, g, k=0.6):
    """(px, py) on the walker: within max(k H, half the blob width + 20 px) of the blob centre in x, and between
    0.4 H above the blob top and 1.3 H below it (the blob top is the head; the legs are missing)."""
    if g is None:
        return False
    cx, cy, w, h, bot, top, H = g
    return abs(px - cx) <= max(k * H, 0.5 * w + 20) and top - 0.4 * H <= py <= top + 1.3 * H


def on_stand(px, py, stands):
    return any(abs(px - sx) <= 70 and sy - 110 <= py <= sy + 80 for sx, sy in stands)


def on_door(px, py):
    return DOOR_X[0] <= px <= DOOR_X[1] and DOOR_Y[0] <= py <= DOOR_Y[1]


def pts_of(r):
    return F.emitted(r)


def segments(flags, min_len):
    out, s = [], None
    for i, f in enumerate(list(flags) + [False]):
        if f and s is None:
            s = i
        elif not f and s is not None:
            if i - s >= min_len:
                out.append((s, i))
            s = None
    return out


def take_metrics(stem, rows, scene, fps, gt):
    n = len(rows)
    stands = [(p[0], p[1]) for p in scene["stands"] if p]
    vis = sorted(gt)
    a, b = vis[0], vis[-1]
    far = [f for f in vis if zone(gt[f]).startswith("far")]
    wa, wb = (far[0], far[-1]) if far else (a, b)
    pts = [pts_of(r) for r in rows]
    inv = range(a, b + 1)
    cov = sum(1 for f in inv if pts[f]) / len(inv)
    with_gt = [f for f in inv if f in gt]
    on = sum(1 for f in with_gt if any(near(p["x"], p["y"], gt[f]) for p in pts[f])) / max(1, len(with_gt))
    wall = range(wa, wb + 1)
    cov_wall = sum(1 for f in wall if pts[f]) / max(1, len(wall))
    wall_gt = [f for f in wall if f in gt]
    on_wall = sum(1 for f in wall_gt if any(near(p["x"], p["y"], gt[f]) for p in pts[f])) / max(1, len(wall_gt))
    # holes inside the view window
    holes = []
    for s, e in segments([a <= f <= b and not pts[f] for f in range(n)], int(round(fps))):
        zs = {}
        for f in range(s, e):
            z = zone(gt.get(f))
            zs[z] = zs.get(z, 0) + 1
        cause = max(zs, key=zs.get)
        seen = sum(1 for f in range(s, e) if any(d[0] >= 0.5 and near(d[1], d[2], gt.get(f)) for d in rows[f].get("raw") or []))
        hs = [gt[f][3] for f in range(s, e) if f in gt]
        holes.append({"frame": s, "t": round(s / fps, 2), "dur_s": round((e - s) / fps, 2), "cause": cause,
                      "zones": zs, "yolo_saw": round(seen / (e - s), 2),
                      "gt_h_med": round(float(np.median(hs))) if hs else None,
                      "x_med": round(float(np.median([gt[f][0] for f in range(s, e) if f in gt]))) if hs else None})
    # ghosts / wrong / static ghosts
    ghost_frames = [f for f in range(n) if len(pts[f]) > 1]
    ghost_view = [f for f in ghost_frames if a <= f <= b]
    wrong = [f for f in with_gt if len(pts[f]) == 1 and not near(pts[f][0]["x"], pts[f][0]["y"], gt[f])]
    outside = [f for f in range(n) if (f < a or f > b) and pts[f]]
    st_frames, door_frames, other_frames, st_states, door_states, off_frames = [], [], [], {}, {}, []
    st_pos = []
    for f in range(n):
        g = gt.get(f) if a <= f <= b else None
        for p in pts[f]:
            if near(p["x"], p["y"], g):
                continue
            off_frames.append(f)
            if on_stand(p["x"], p["y"], stands):
                st_frames.append(f)
                st_states[p["state"]] = st_states.get(p["state"], 0) + 1
                st_pos.append((p["x"], p["y"]))
            elif on_door(p["x"], p["y"]):
                door_frames.append(f)
                door_states[p["state"]] = door_states.get(p["state"], 0) + 1
            else:
                other_frames.append(f)
    st_eps = [(round(s / fps, 2), round((e - s) / fps, 2)) for s, e in
              segments([f in set(st_frames) for f in range(n)], 1)]
    door_eps = [(round(s / fps, 2), round((e - s) / fps, 2)) for s, e in
                segments([f in set(door_frames) for f in range(n)], 1)]
    ev = F.analyse(rows)
    qa = F.quality(rows)
    # YOLO false persons on the stand / door (raw dets, any conf the run kept)
    yfp = {"stand": [], "door": []}
    for f in range(n):
        g = gt.get(f) if a <= f <= b else None
        for d in rows[f].get("raw") or []:
            if near(d[1], d[2], g):
                continue
            if on_stand(d[1], d[2], stands):
                yfp["stand"].append(d[0])
            elif on_door(d[1], d[2]):
                yfp["door"].append(d[0])
    # height guard / own size
    ph = [r.get("ph") for r in rows]
    ph_changes = [(round(f / fps, 2), ph[f - 1], ph[f]) for f in range(1, n) if ph[f] != ph[f - 1]]
    ph_wall = [ph[f] for f in wall]
    own_wall, own_ratio, own_last = [], [], []
    for f in range(wa + 2 * (wb - wa) // 3, wb + 1):
        for t in rows[f].get("int") or []:
            if t.get("own") and t.get("est") and near(t["p"][0], t["p"][1], gt.get(f)):
                own_last.append(t["own"])
    for f in wall:
        em_ids = {p["id"] for p in pts[f]}
        for t in rows[f].get("int") or []:
            if t.get("own") and t.get("est"):
                own_wall.append(t["own"])
                own_ratio.append(ph[f] / t["own"])
    dh = [r.get("dh") for r in rows if r.get("dh")]
    seen_wall = sum(1 for f in wall if any(d[0] >= 0.5 for d in rows[f].get("raw") or []))
    return {"yolo_seen_wall": round(seen_wall / max(1, len(wall)), 4), "take": stem, "fps": fps, "frames": n, "view": [a, b], "view_s": [round(a / fps, 2), round(b / fps, 2)],
            "wall_win": [wa, wb], "wall_s": [round(wa / fps, 2), round(wb / fps, 2)],
            "cov_view": round(cov, 4), "on_view": round(on, 4), "cov_wall": round(cov_wall, 4),
            "on_wall": round(on_wall, 4), "cov_all": ev["coverage"],
            "holes": holes, "holes_n": len(holes), "longest_hole_s": max([h["dur_s"] for h in holes], default=0.0),
            "ghost_frames": len(ghost_frames), "ghost_view": len(ghost_view),
            "ghost_wall": sum(1 for f in ghost_frames if wa <= f <= wb),
            "off_wall": len({f for f in off_frames if wa <= f <= wb}),
            "ghost_eps": [(round(g["frame"] / fps, 2), g["n"]) for g in ev["ghost_episodes"]],
            "wrong_frames": len(wrong), "wrong_eps": [(round(s / fps, 2), round((e - s) / fps, 2)) for s, e in
                                                    segments([f in set(wrong) for f in range(n)], 3)],
            "points_out_of_view": len(outside),
            "stand_frames": len(set(st_frames)), "stand_eps": st_eps, "stand_states": st_states,
            "door_frames": len(set(door_frames)), "door_eps": door_eps, "door_states": door_states,
            "other_frames": len(set(other_frames)), "off_frames": len(set(off_frames)),
            "off_eps": [(round(s / fps, 2), round((e - s) / fps, 2)) for s, e in
                        segments([f in set(off_frames) for f in range(n)], 3)],
            "own_last_med": round(float(np.median(own_last)), 1) if own_last else None,
            "yolo_fp_stand": {"n": len(yfp["stand"]), "max_conf": max(yfp["stand"], default=None),
                              "n_ge_05": sum(1 for c in yfp["stand"] if c >= 0.5)},
            "yolo_fp_door": {"n": len(yfp["door"]), "max_conf": max(yfp["door"], default=None),
                             "n_ge_05": sum(1 for c in yfp["door"] if c >= 0.5)},
            "jumps": [(j["t"] * 20.0 / fps, j["dist_h"], j["state"]) for j in ev["jumps"]],
            "id_switches": ev["id_switches"], "ids": ev["ids"], "states": ev["states"],
            "on_dancer": qa.get("on_dancer"), "coasting": qa.get("coasting_share"), "lag_ms": qa.get("lag_ms"),
            "ph_start": ph[0], "ph_end": ph[-1], "ph_changes": ph_changes,
            "ph_wall_med": float(np.median(ph_wall)) if ph_wall else None,
            "own_wall_med": round(float(np.median(own_wall)), 1) if own_wall else None,
            "own_gates_share": round(sum(1 for r in own_ratio if r > 2.0) / len(own_ratio), 3) if own_ratio else None,
            "dh_end": dh[-1] if dh else None}


def recall(rows, gt, frames, thr):
    hit = 0
    for f in frames:
        g = gt[f]
        if any(d[0] >= thr and near(d[1], d[2], g, 0.6) for d in rows[f].get("raw") or []):
            hit += 1
    return round(hit / max(1, len(frames)), 4)


NIGHT = Path("/data/WallDance/tmp_analysis/flowcheck/night_rc_46a0262")
# 2026-10-06 night takes: the "operator at the wall" windows (abs frames) of tmp_analysis/plan25m/validate_night.py
WIN25 = {"slot_2_20261006_211831": ("s2a bright, still", 259, 5617), "slot_3_20261006_212855": ("s3 bright, moving", 227, 1833),
         "slot_4_20261006_213310": ("s4 dark, moving", 181, 2135), "slot_6_20261006_214306": ("s6 dark, entry/exit", 286, 1128),
         "slot_2_20261006_214531": ("s2c dark, still at the back", 174, 4995), "slot_7_20261006_220640": ("s7 dark, moving", 244, 813)}


def night_metrics():
    """25 m reference: the 46a0262 flow check (D27 + Calibrate on slot 1 = 'after s1'; ROI on, x@1280), the
    wall window only (no walker GT on those takes: 'YOLO sees' = a YOLO box >= 0.5 in the frame)."""
    out = {}
    for stem, (name, wa, wb) in WIN25.items():
        p = NIGHT / f"s1_{stem}.json"
        if not p.exists():
            continue
        rows = F.load_rows(p)
        fps = 20.0
        wb = min(wb, len(rows) - 1)
        pts = [F.emitted(r) for r in rows]
        win = range(wa, wb + 1)
        holes = [(round(s / fps, 1), round((e - s) / fps, 2)) for s, e in
                 segments([wa <= f <= wb and not pts[f] for f in range(len(rows))], 20)]
        hs = [x["h"] for f in win for x in (rows[f].get("ref") or []) if isinstance(x, dict) and (x.get("conf") or 0) >= 0.5]
        seen = sum(1 for f in win if any(isinstance(x, dict) and (x.get("conf") or 0) >= 0.5 for x in rows[f].get("ref") or []))
        ev = F.analyse(rows[wa:wb + 1])
        qa = F.quality(rows[wa:wb + 1])
        out[stem] = {"name": name, "win_s": [round(wa / fps, 1), round(wb / fps, 1)],
                     "cov_wall": round(sum(1 for f in win if pts[f]) / len(win), 4),
                     "holes": holes, "ghost_frames": sum(1 for f in win if len(pts[f]) > 1),
                     "jumps": len(ev["jumps"]), "id_switches": ev["id_switches"], "lag_ms": qa.get("lag_ms"),
                     "coasting": qa.get("coasting_share"), "on_dancer": qa.get("on_dancer"),
                     "yolo_seen": round(seen / len(win), 4),
                     "h_med": round(float(np.median(hs)), 1) if hs else None,
                     "h_p10": round(float(np.percentile(hs, 10)), 1) if hs else None}
        print(f"25m {name}: {out[stem]}")
    return out


def main():
    res = {"takes": {}, "variants": {}, "size": {}, "recall": {}}
    res["night25"] = night_metrics()
    scenes = {s: json.loads((HERE / "scene" / f"scene_{s}.json").read_text()) for s in TAKES}
    gts = {}
    for s in TAKES:
        v1 = F.load_rows(HERE / "tl" / f"V1_{s}.json")
        gts[s] = gt_of(scenes[s]["rows"], walker_heights(v1, scenes[s]["rows"]))
    for v in VARIANTS:
        res["variants"][v] = {}
        for s, fps in TAKES.items():
            p = HERE / "tl" / f"{v}_{s}.json"
            if not p.exists():
                continue
            rows = F.load_rows(p)
            m = take_metrics(s, rows, scenes[s], fps, gts[s])
            info = json.loads(Path(str(p) + ".info.json").read_text())
            m["guard_events"] = info.get("guard_events")
            m["config"] = info.get("config")
            res["variants"][v][s] = m
            print(f"{v} {SHORT[s]}: cov_view {m['cov_view']:.3f} on {m['on_view']:.3f} wall {m['cov_wall']:.3f}/"
                  f"{m['on_wall']:.3f} holes {[(h['t'], h['dur_s'], h['cause']) for h in m['holes']]} ghostF "
                  f"{m['ghost_frames']} wrong {m['wrong_frames']} out {m['points_out_of_view']} stand "
                  f"{m['stand_frames']} {m['stand_eps'][:4]} door {m['door_frames']} jumps {m['jumps']} sw "
                  f"{m['id_switches']} ph {m['ph_start']}->{m['ph_end']} {m['ph_changes']} own {m['own_wall_med']} "
                  f"gates {m['own_gates_share']} yfp {m['yolo_fp_stand']} lag {m['lag_ms']}", flush=True)
    # dancer size at the far wall (GT box + YOLO box from V1, conf >= 0.5, matched to the GT)
    for s, fps in TAKES.items():
        gt = gts[s]
        farz = [f for f in sorted(gt) if zone(gt[f]).startswith("far")]
        far = [f for f in sorted(gt) if farz and farz[0] <= f <= farz[-1]]     # the wall window, walker in view
        zc = {}
        for f in range(sorted(gt)[0], sorted(gt)[-1] + 1):
            z = zone(gt.get(f))
            zc[z] = zc.get(z, 0) + 1
        res["takes"][s] = {"far_frames": len(far), "zones": zc, "wall_s": [round(far[0] / fps, 2), round(far[-1] / fps, 2)] if far else None}
        gth = [gt[f][3] for f in far]
        yh = []
        p = HERE / "tl" / f"V1_{s}.json"
        if p.exists():
            rows = F.load_rows(p)
            for f in far:
                c = [d for d in rows[f].get("raw") or [] if d[0] >= 0.5 and near(d[1], d[2], gt[f], 0.6)]
                if c:
                    yh.append(max(c, key=lambda d: d[0])[3])
        res["size"][s] = {"gt_h": [round(float(np.percentile(gth, q)), 1) for q in (0, 10, 50, 90)] if gth else None,
                          "yolo_h": [round(float(np.percentile(yh, q)), 1) for q in (0, 10, 50, 90)] if yh else None,
                          "n_gt": len(gth), "n_yolo": len(yh)}
        rec = {}
        for v in VARIANTS:
            p = HERE / "tl" / f"{v}_{s}.json"
            if not p.exists():
                continue
            rows = F.load_rows(p)
            rec[v] = {str(t): recall(rows, gt, far, t) for t in (0.15, 0.25, 0.5)}
        res["recall"][s] = rec
        print(f"size {SHORT[s]}: far {len(far)} gt_h {res['size'][s]['gt_h']} yolo_h {res['size'][s]['yolo_h']} recall {rec}")
    (HERE / "results.json").write_text(json.dumps(res, indent=1, default=str))
    print("wrote", HERE / "results.json")


if __name__ == "__main__":
    main()
