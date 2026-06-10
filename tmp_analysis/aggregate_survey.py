"""Aggregate corpus_survey.py outputs into per-scene metrics.

For each surveyed slot:
  * confidence operating curve: at each threshold, mean detection count,
    coverage (frames with >= N dets), overcount rate (frames with > N), and
    mean absolute count error vs annotated N -- raw and enhanced passes.
  * suggested per-scene settings: person_height (median of confident det
    heights), min/max ratios (p5/p95), imgsz via calib2.select_imgsz,
    confidence seed (p5 of visible-kp conf - margin, calib2 rule, fixed to
    visible-only per ROADMAP bug #11).
  * scene/lighting passthrough (brightness, noise, var sweep, focus...).

Writes survey_summary.json + a markdown table to stdout.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "application" / "src"
sys.path.insert(0, str(SRC))

OUT = HERE / "survey"
THRESHOLDS = [0.15, 0.25, 0.35, 0.50, 0.65]
CONF_SOLID = 0.25  # height stats from dets at/above this


def curve(rows, n_expected):
    """Operating curve for one pass (list of {frame, dets:[[conf,h,w,kv,kc,cx,cy]..]})."""
    out = {}
    for t in THRESHOLDS:
        counts = [sum(1 for d in r["dets"] if d[0] >= t) for r in rows]
        c = np.array(counts) if counts else np.array([0])
        ent = {"mean_count": round(float(c.mean()), 2)}
        if n_expected is not None:
            ent["coverage"] = round(float((c >= n_expected).mean()), 3)
            ent["overcount_rate"] = round(float((c > n_expected).mean()), 3)
            ent["mean_abs_err"] = round(float(np.abs(c - n_expected).mean()), 3)
        out[str(t)] = ent
    return out


def best_threshold(rows, n_expected):
    if n_expected is None:
        return None
    best, best_err = None, 1e9
    for t in THRESHOLDS:
        counts = np.array([sum(1 for d in r["dets"] if d[0] >= t) for r in rows])
        err = float(np.abs(counts - n_expected).mean())
        if err < best_err - 1e-9:
            best, best_err = t, err
    return {"threshold": best, "mean_abs_err": round(best_err, 3)}


def extra_det_character(rows, n_expected, t=0.25):
    """For frames with count>N at threshold t: are the beyond-N detections
    duplicates of a kept dancer (center within 0.6*h of a top-N det) or
    background ghosts?  Returns fractions + ghost positions (normalized-ish)."""
    if n_expected is None:
        return None
    dups, ghosts, ghost_pos = 0, 0, []
    for r in rows:
        dets = sorted((d for d in r["dets"] if d[0] >= t), key=lambda d: -d[0])
        if len(dets) <= n_expected:
            continue
        kept, extra = dets[:n_expected], dets[n_expected:]
        for e in extra:
            ex, ey, eh = e[5], e[6], e[1]
            is_dup = any(((ex - k[5]) ** 2 + (ey - k[6]) ** 2) ** 0.5
                         < 0.6 * max(eh, k[1]) for k in kept)
            if is_dup:
                dups += 1
            else:
                ghosts += 1
                ghost_pos.append([round(ex, 0), round(ey, 0), round(eh, 0)])
    total = dups + ghosts
    if not total:
        return None
    return {"extra_dets": total, "dup_frac": round(dups / total, 2),
            "bg_ghost_frac": round(ghosts / total, 2),
            "ghost_positions": ghost_pos[:20]}


def separability(rows, n_expected, floor=0.05):
    """Is one confidence threshold enough on this scene? (ROADMAP 3b)
    Proxy: per frame the top-N dets (by conf, dup-merged) are 'real', the rest
    'ghost'.  Margin = p10(real confs) - p90(ghost confs); positive => one
    threshold separates cleanly."""
    if n_expected is None:
        return None
    real, ghost = [], []
    for r in rows:
        dets = sorted((d for d in r["dets"] if d[0] >= floor), key=lambda d: -d[0])
        kept = []
        for d in dets:
            is_dup = any(((d[5] - k[5]) ** 2 + (d[6] - k[6]) ** 2) ** 0.5
                         < 0.6 * max(d[1], k[1]) for k in kept)
            if is_dup:
                continue
            if len(kept) < n_expected:
                kept.append(d)
                real.append(d[0])
            else:
                ghost.append(d[0])
    if len(real) < 5:
        return None
    p10_real = float(np.percentile(np.array(real), 10))
    out = {"real_conf_p10": round(p10_real, 3),
           "real_conf_p50": round(float(np.median(np.array(real))), 3)}
    if ghost:
        p90_ghost = float(np.percentile(np.array(ghost), 90))
        out["ghost_conf_p90"] = round(p90_ghost, 3)
        out["margin"] = round(p10_real - p90_ghost, 3)
        out["ghost_dets_per_frame"] = round(len(ghost) / max(len(rows), 1), 2)
    return out


def height_stats(rows):
    hs = [d[1] for r in rows for d in r["dets"] if d[0] >= CONF_SOLID]
    if len(hs) < 5:
        return None
    hs = np.array(hs)
    return {
        "n": int(len(hs)),
        "median": round(float(np.median(hs)), 1),
        "p5": round(float(np.percentile(hs, 5)), 1),
        "p95": round(float(np.percentile(hs, 95)), 1),
        "min_ratio": round(float(np.percentile(hs, 5) / np.median(hs)), 2),
        "max_ratio": round(float(np.percentile(hs, 95) / np.median(hs)), 2),
    }


def conf_seed(rows):
    """calib2 sensitivity seed, visible-keypoint mean variant (bug #11 fixed)."""
    kcs = [d[4] for r in rows for d in r["dets"] if d[0] >= CONF_SOLID and d[3] > 0]
    if len(kcs) < 5:
        return None
    p5 = float(np.percentile(np.array(kcs), 5))
    return round(min(max(p5 - 0.05, 0.15), 0.50), 3)


def main():
    from calib2 import select_imgsz
    summaries = []
    for f in sorted(OUT.glob("*.json")):
        if f.name == "survey_summary.json":
            continue
        d = json.loads(f.read_text())
        if "error" in d and "yolo_raw" not in d:
            summaries.append({"project": d["project"], "slot": d["slot"],
                              "error": d["error"]})
            continue
        n = d.get("expected_n")
        s = {
            "project": d["project"], "slot": d["slot"], "note": d.get("note"),
            "expected_n": n, "expected_range": d.get("expected_range"),
            "frame_size": d["frame_size"], "fps": d["fps"],
            "window": d["window"], "scene": d.get("scene"),
            "auto_gamma": d.get("auto_gamma"),
            "raw": {
                "curve": curve(d["yolo_raw"], n),
                "best_threshold": best_threshold(d["yolo_raw"], n),
                "heights": height_stats(d["yolo_raw"]),
                "conf_seed": conf_seed(d["yolo_raw"]),
                "extra_dets": extra_det_character(d["yolo_raw"], n),
                "separability": separability(d["yolo_raw"], n),
            },
            "enhanced": {
                "curve": curve(d["yolo_enhanced"], n),
                "best_threshold": best_threshold(d["yolo_enhanced"], n),
                "heights": height_stats(d["yolo_enhanced"]),
                "conf_seed": conf_seed(d["yolo_enhanced"]),
                "extra_dets": extra_det_character(d["yolo_enhanced"], n),
                "separability": separability(d["yolo_enhanced"], n),
            },
        }
        hs = s["raw"]["heights"] or s["enhanced"]["heights"]
        if hs:
            long_side = max(d["frame_size"])
            imgsz, ok, net_h = select_imgsz(hs["median"], long_side)
            s["imgsz_suggested"] = {"imgsz": imgsz, "satisfied": bool(ok),
                                    "net_height_px": round(net_h, 1)}
        summaries.append(s)

    (OUT / "survey_summary.json").write_text(json.dumps(summaries, indent=1))

    # Markdown table
    hdr = ("| project | slot | N | bright | noise | var/scale | gamma | "
           "raw@.25 cov/over | enh@.25 cov/over | best-t raw | best-t enh | "
           "h med (p5-p95) | imgsz |")
    print(hdr)
    print("|" + "---|" * 13)
    for s in summaries:
        if "error" in s:
            print(f"| {s['project']} | {s['slot']} | - | ERROR: {s['error']} |")
            continue
        sc = s["scene"] or {}
        n = s["expected_n"] if s["expected_n"] is not None else (
            f"{s['expected_range'][0]}-{s['expected_range'][1]}" if s["expected_range"] else "?")
        r25 = s["raw"]["curve"]["0.25"]
        e25 = s["enhanced"]["curve"]["0.25"]
        def cov(c):
            return (f"{c.get('coverage','-')}/{c.get('overcount_rate','-')}"
                    if "coverage" in c else f"mean {c['mean_count']}")
        bt_r = s["raw"]["best_threshold"]
        bt_e = s["enhanced"]["best_threshold"]
        hs = s["raw"]["heights"] or s["enhanced"]["heights"] or {}
        im = s.get("imgsz_suggested") or {}
        print(f"| {s['project']} | {s['slot']} | {n} | {sc.get('brightness_mean','-')} "
              f"| {sc.get('noise_sigma','-')} | {sc.get('var_threshold','-')}/{sc.get('mog2_scale','-')}"
              f"{'(sat)' if sc.get('var_saturated') else ''} | {s.get('auto_gamma','-')} "
              f"| {cov(r25)} | {cov(e25)} "
              f"| {bt_r['threshold'] if bt_r else '-'} | {bt_e['threshold'] if bt_e else '-'} "
              f"| {hs.get('median','-')} ({hs.get('p5','-')}-{hs.get('p95','-')}) "
              f"| {im.get('imgsz','-')}{'' if im.get('satisfied', True) else '!'} |")


if __name__ == "__main__":
    main()
