#!/usr/bin/env python3
"""PLAN_25M §C.15 gates: before / after the "no snapshot update on stale frames" fix, from run_all.sh's outputs.

  A  mur30m-0710, V1 (D27 + the live snapshot) and V2 (D27 + the Calibrate snapshot): confirm_0710's metrics
     (analyse.take_metrics, against its walker ground truth) on confirm_0710/tl (46a0262) vs stale_plate/tl;
     + the snapshot itself (gain, biggest blob, stale frames, plate updates on stale frames from --probe).
  B  2026-10-06 night, operator flow (tests/flow_check.py s1 / dark): night_rc_46a0262 vs stale_plate/night.
  C  older corpus (flowcheck/older_regression.sh, e1 / v5): stale_plate/older *_pre_* vs *_fix_*.
Per pair: identical rows or the first differing frame, and the first "plate stale" frame (the fix cannot
change anything before it).  Writes stale_plate/results.json and prints the tables.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "application" / "tests"))
sys.path.insert(0, str(WT / "application" / "src"))
C0710 = Path("/data/WallDance/tmp_analysis/confirm_0710")     # its scene/ (walker GT) and tl/ (46a0262)
sys.path.insert(0, str(C0710))
import analyse as A  # noqa: E402
import flow_check as F  # noqa: E402

OUT = Path("/data/WallDance/tmp_analysis/stale_plate")
NIGHT_BEFORE = Path("/data/WallDance/tmp_analysis/flowcheck/night_rc_46a0262")
KEYS = ("emitted", "reported", "tracks", "ids", "fg")


def identity(before, after):
    """(identical, first differing frame, first stale frame in `before`)."""
    first = next((a.get("frame", i) for i, (a, b) in enumerate(zip(before, after))
                  if any(a.get(k) != b.get(k) for k in KEYS)), None)
    if first is None and len(before) != len(after):
        first = min(len(before), len(after))
    stale = next((r.get("frame", i) for i, r in enumerate(before)
                  if (r.get("fg") or {}).get("why") == "plate stale"), None)
    return first is None, first, stale


def fg_summary(rows, fps, t0=8.0, t1=30.0):
    """Snapshot health: stale frames, plate updates on stale frames (--probe), median gain and biggest
    blob (px^2) between t0 and t1 s (after the walk-out right in front of the lens)."""
    fgs = [(r["frame"], r.get("fg")) for r in rows if r.get("fg")]
    if not fgs:
        return None
    win = [g for f, g in fgs if t0 * fps <= f <= t1 * fps and g["v"]]
    upd_stale = sum(1 for r in rows if r.get("pl") and r["pl"][0] and (r.get("fg") or {}).get("why") == "plate stale")
    return {"stale": sum(1 for _f, g in fgs if g["why"] == "plate stale"),
            "upd_on_stale": upd_stale if any(r.get("pl") for r in rows) else None,
            "gain_med": round(float(np.median([g["g"] for g in win])), 3) if win else None,
            "blob_med": round(float(np.median([g["b"][0][2] if g["b"] else 0 for g in win]))) if win else None}


def part_a():
    res = {}
    scenes = {s: json.loads((C0710 / "scene" / f"scene_{s}.json").read_text()) for s in A.TAKES}
    gts = {s: A.gt_of(scenes[s]["rows"], A.walker_heights(F.load_rows(C0710 / "tl" / f"V1_{s}.json"),
                                                         scenes[s]["rows"])) for s in A.TAKES}
    for v in ("V1", "V2"):
        for s, fps in A.TAKES.items():
            pb, pa = C0710 / "tl" / f"{v}_{s}.json", OUT / "tl" / f"{v}_{s}.json"
            if not (pb.exists() and pa.exists()):
                continue
            rb, ra = F.load_rows(pb), F.load_rows(pa)
            same, first, stale = identity(rb, ra)
            m = {}
            for side, rows in (("before", rb), ("after", ra)):
                t = A.take_metrics(s, rows, scenes[s], fps, gts[s])
                t["fg"] = fg_summary(rows, fps)
                m[side] = t
            res[f"{v} {A.SHORT[s]}"] = {"identical": same, "first_diff": first, "first_stale": stale,
                                        "first_diff_s": None if first is None else round(first / fps, 2),
                                        "first_stale_s": None if stale is None else round(stale / fps, 2), **m}
    pre = OUT / "probe" / "V1pre_slot_4_20261007_201949.json"
    if pre.exists():
        res["probe V1 slot 4 before"] = {"fg": fg_summary(F.load_rows(pre), 19.516)}
    return res


def flow_metrics(rows):
    ev, qa = F.analyse(rows), F.quality(rows)
    return {"coverage": ev["coverage"], "holes": len(ev["holes"]), "longest_hole_s": ev["longest_hole_s"],
            "ghost_frames": ev["ghost_frames"], "jumps": len(ev["jumps"]), "id_switches": ev["id_switches"],
            "on_dancer": qa.get("on_dancer"), "coasting": qa.get("coasting_share"), "lag_ms": qa.get("lag_ms")}


def part_b():
    res = {}
    for pa in sorted((OUT / "night").glob("*_slot_*.json")):
        if pa.name.startswith("base_") or not pa.name.split("_")[0] in ("s1", "dark"):
            continue
        pb = NIGHT_BEFORE / pa.name
        if not pb.exists():
            continue
        rb, ra = F.load_rows(pb), F.load_rows(pa)
        same, first, stale = identity(rb, ra)
        res[pa.stem] = {"identical": same, "first_diff": first, "first_stale": stale,
                        "before": flow_metrics(rb), "after": flow_metrics(ra),
                        "fg_before": fg_summary(rb, 20.0), "fg_after": fg_summary(ra, 20.0)}
    for label in ("s1", "dark"):
        for side, d in (("before", NIGHT_BEFORE), ("after", OUT / "night")):
            p = d / f"{label}.json"
            if p.exists():
                cal = json.loads(p.read_text())["calibrate"]
                res.setdefault(f"calibrate {label}", {})[side] = {"after": cal["after"], "plate": cal["plate"]}
    return res


def part_c():
    import output_quality
    import scoring
    res = {}
    for pa in sorted((OUT / "older").glob("*_fix_*.json")):
        s, mode = pa.stem.rsplit("_fix_", 1)
        pb = OUT / "older" / f"{s}_pre_{mode}.json"
        if not pb.exists():
            continue
        man = json.loads((WT / "application" / "tests" / "scenarios" / f"{s}.json").read_text())
        fps = man.get("fps") or 19.8
        out = {}
        for side, p in (("before", pb), ("after", pa)):
            q = output_quality.compare_streams(scoring._load_timeline(str(p)), man, fps=fps).get("emitted", {})
            cont, qual = q.get("continuity", {}), q.get("quality", {})
            out[side] = {"coverage": cont.get("coverage"), "longest_hole_s": cont.get("gap_max_s"),
                         "holes": cont.get("gaps_ge_1s"), "on_dancer": cont.get("on_dancer"),
                         "coasting": qual.get("coasting_share"), "lag_ms": qual.get("lag_ms"),
                         "id_switches": qual.get("id_switches"), "extra_points": qual.get("over_n_frames")}
        rb, ra = F.load_rows(pb), F.load_rows(pa)
        same, first, stale = identity(rb, ra)
        res[f"{s} {mode}"] = {"identical": same, "first_diff": first, "first_stale": stale, **out,
                              "fg_before": fg_summary(rb, fps), "fg_after": fg_summary(ra, fps)}
    return res


def fmt(v):
    if v is None:
        return "-"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def table(title, res, cols):
    print(f"\n## {title}")
    for name, r in res.items():
        if "before" not in r or not isinstance(r["before"], dict) or "after" not in r:
            print(f"{name}: {r}")
            continue
        tag = "IDENTICAL" if r.get("identical") else f"differs from frame {r.get('first_diff')} (first stale {r.get('first_stale')})"
        cells = []
        for c in cols:
            b, a = r["before"].get(c), r["after"].get(c)
            cells.append(f"{c} {fmt(b)}" + ("" if b == a else f" -> {fmt(a)}"))
        print(f"{name}: {tag}\n    " + " | ".join(cells))
        for k in ("fg", ):
            if r["before"].get(k) or r["after"].get(k):
                print(f"    snapshot: {r['before'].get(k)} -> {r['after'].get(k)}")
        if r.get("fg_before") or r.get("fg_after"):
            print(f"    snapshot: {r.get('fg_before')} -> {r.get('fg_after')}")


def main():
    res = {"A_0710": part_a(), "B_night": part_b(), "C_older": part_c()}
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=str))
    table("A mur30m-0710 (V1 = D27, V2 = D27 + Calibrate)", res["A_0710"],
          ["cov_view", "on_view", "cov_wall", "on_wall", "holes_n", "longest_hole_s", "ghost_frames", "wrong_frames",
           "off_frames", "door_frames", "stand_frames", "points_out_of_view", "id_switches", "lag_ms"])
    for name, r in res["A_0710"].items():
        if "before" in r and isinstance(r["before"], dict) and "ghost_eps" in r["before"]:
            print(f"  {name} ghost_eps {r['before']['ghost_eps']} -> {r['after']['ghost_eps']}; door_eps "
                  f"{r['before']['door_eps']} -> {r['after']['door_eps']}; jumps {len(r['before']['jumps'])} -> "
                  f"{len(r['after']['jumps'])}; holes {[(h['t'], h['dur_s']) for h in r['before']['holes']]} -> "
                  f"{[(h['t'], h['dur_s']) for h in r['after']['holes']]}")
    table("B 2026-10-06 night, operator flow", res["B_night"],
          ["coverage", "holes", "longest_hole_s", "ghost_frames", "jumps", "id_switches", "on_dancer", "coasting",
           "lag_ms"])
    table("C older corpus", res["C_older"],
          ["coverage", "holes", "longest_hole_s", "on_dancer", "coasting", "lag_ms", "id_switches", "extra_points"])
    print("\nwrote", OUT / "results.json")


if __name__ == "__main__":
    main()
