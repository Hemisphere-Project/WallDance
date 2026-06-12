#!/usr/bin/env python3
"""Phase 2b analysis (ROADMAP 4.2 Phase 2b deliverables a/b/c + 5a seed check).

Reads results/*.jsonl, produces analysis.json + a printed digest:
  (a) score-vs-net-height curves per (scenario, model) column + knee table
  (b) yolo26 vs yolo11 same-tier deltas (same scenario, same imgsz, best tau)
  (c) per-scenario quality/cost frontier (best tau score vs .pt yolo_ms)
  (d) calib2 (5)a seed-tau vs sweep-best-tau validation
  (e) .pt fp32 per-model fps factor table (this rig; relative factors)

Tolerant of partial data (reports missing cells). ASCII-only output.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import p2b_common as C

TIERS = ("n", "s", "m", "l", "x")
KNEE_EPS_ABS = 0.02   # knee = smallest net height within eps of column best
KNEE_EPS_REL = 0.10


def tier_of(model: str) -> str:
    return model.replace("-pose", "")[-1]


def family_of(model: str) -> str:
    return "26" if model.startswith("yolo26") else "11"


def load_records():
    recs = {}        # (scenario, model, imgsz, tau) -> record (last wins)
    sentinels = {}   # (scenario, model, imgsz) -> cell_done record (last wins)
    for f in sorted(C.RESULTS_DIR.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("cell_done"):
                sentinels[(r["scenario"], r["model"], r["imgsz"])] = r
            elif "tau" in r:
                recs[(r["scenario"], r["model"], r["imgsz"], r["tau"])] = r
    return recs, sentinels


def build_cells(recs, sentinels):
    """Per-cell summary: best tau, seed tau performance, floor score."""
    by_cell = defaultdict(dict)   # (scen, model, imgsz) -> tau -> rec
    for (scen, model, imgsz, tau), r in recs.items():
        by_cell[(scen, model, imgsz)][tau] = r
    cells = {}
    for key, taus in by_cell.items():
        scen, model, imgsz = key
        sent = sentinels.get(key, {})
        scored = {t: r for t, r in taus.items() if r.get("score") is not None}
        if not scored:
            continue
        best_tau, best = min(scored.items(), key=lambda kv: (kv[1]["score"], kv[0]))
        # raw fields live on the non-dup record; resolve through dup_of
        braw = best.get("raw")
        if braw is None and best.get("dup_of") is not None:
            src = scored.get(best["dup_of"])
            braw = (src or {}).get("raw")
        seed_tau = sent.get("seed_tau")
        seed_score = None
        if seed_tau is not None:
            sr = scored.get(round(seed_tau, 3))
            seed_score = sr["score"] if sr else None
        cells[key] = {
            "scenario": scen, "model": model, "imgsz": imgsz,
            "family": family_of(model), "tier": tier_of(model),
            "net_height": next(iter(taus.values()))["net_height"],
            "best_tau": best_tau, "best_score": best["score"],
            "best_components": best.get("components", {}),
            "best_raw": braw or {},
            "best_passed": best.get("passed"),
            "floor_score": scored.get(C.CONF_FLOOR, {}).get("score"),
            "seed_tau": seed_tau, "seed_score": seed_score,
            "yolo_ms": sent.get("yolo_ms_median"),
            "n_taus_scored": len(scored),
            "best_at_floor_boundary": bool(best_tau == C.CONF_FLOOR),
        }
    return cells


def knee_table(cells):
    """Per (scenario, model): knee net-height under abs/rel epsilon."""
    cols = defaultdict(list)
    for c in cells.values():
        cols[(c["scenario"], c["model"])].append(c)
    knees = {}
    for (scen, model), pts in cols.items():
        pts = sorted(pts, key=lambda c: c["imgsz"])
        best = min(p["best_score"] for p in pts)
        eps = max(KNEE_EPS_ABS, KNEE_EPS_REL * best)
        knee = next((p for p in pts if p["best_score"] <= best + eps), None)
        top = max(pts, key=lambda p: p["imgsz"])
        knees[(scen, model)] = {
            "scenario": scen, "model": model,
            "col_best_score": best,
            "knee_net_height": knee["net_height"] if knee else None,
            "knee_imgsz": knee["imgsz"] if knee else None,
            "knee_score": knee["best_score"] if knee else None,
            "max_imgsz_score": top["best_score"],
            "above_knee_gain": (knee["best_score"] - top["best_score"])
                               if knee else None,
            "curve": [{"imgsz": p["imgsz"], "net": p["net_height"],
                       "score": p["best_score"], "tau": p["best_tau"],
                       "drop": p["best_components"].get("drop_rate"),
                       "ghost": p["best_components"].get("ghost_rate"),
                       "passed": p["best_passed"]} for p in pts],
        }
    return knees


def family_deltas(cells):
    """yolo26 minus yolo11, same scenario+tier+imgsz, best-tau scores."""
    out = []
    for c in cells.values():
        if c["family"] != "26":
            continue
        twin = cells.get((c["scenario"],
                          f"yolo11{c['tier']}-pose", c["imgsz"]))
        if not twin:
            continue
        out.append({
            "scenario": c["scenario"], "tier": c["tier"],
            "imgsz": c["imgsz"], "net_height": c["net_height"],
            "score_11": twin["best_score"], "score_26": c["best_score"],
            "delta": round(c["best_score"] - twin["best_score"], 5),
            "passed_11": twin["best_passed"], "passed_26": c["best_passed"],
            "tau_11": twin["best_tau"], "tau_26": c["best_tau"],
        })
    return out


def fps_table(cells):
    """Median .pt yolo_ms per (model, imgsz) + factor vs yolo11m @ same imgsz."""
    import statistics
    ms = defaultdict(list)
    for c in cells.values():
        if c["yolo_ms"]:
            ms[(c["model"], c["imgsz"])].append(c["yolo_ms"])
    med = {k: statistics.median(v) for k, v in ms.items()}
    table = {}
    for (model, imgsz), v in sorted(med.items()):
        ref = med.get(("yolo11m-pose", imgsz))
        table[f"{model}@{imgsz}"] = {
            "yolo_ms": round(v, 1),
            "factor_vs_11m": round(v / ref, 3) if ref else None,
        }
    return table


def frontier(cells):
    """Per scenario: cells sorted by cost; mark the efficient frontier."""
    by_scen = defaultdict(list)
    for c in cells.values():
        if c["yolo_ms"]:
            by_scen[c["scenario"]].append(c)
    out = {}
    for scen, lst in by_scen.items():
        lst = sorted(lst, key=lambda c: c["yolo_ms"])
        oracle = min(c["best_score"] for c in lst)
        front, best_so_far = [], None
        for c in lst:
            if best_so_far is None or c["best_score"] < best_so_far - 1e-9:
                best_so_far = c["best_score"]
                front.append({
                    "model": c["model"], "imgsz": c["imgsz"],
                    "net": c["net_height"], "yolo_ms": c["yolo_ms"],
                    "score": c["best_score"], "tau": c["best_tau"],
                    "passed": c["best_passed"],
                })
        cheapest_near = next(
            (c for c in lst if c["best_score"] <= oracle + KNEE_EPS_ABS), None)
        out[scen] = {
            "oracle_score": oracle,
            "cheapest_within_eps": {
                "model": cheapest_near["model"],
                "imgsz": cheapest_near["imgsz"],
                "yolo_ms": cheapest_near["yolo_ms"],
                "score": cheapest_near["best_score"],
            } if cheapest_near else None,
            "frontier": front,
        }
    return out


def seed_validation(cells):
    rows = []
    for c in cells.values():
        if c["seed_score"] is None:
            continue
        rows.append({
            "scenario": c["scenario"], "model": c["model"],
            "imgsz": c["imgsz"], "seed_tau": c["seed_tau"],
            "best_tau": c["best_tau"],
            "regret": round(c["seed_score"] - c["best_score"], 5),
            "seed_at_clamp": c["seed_tau"] in (0.15, 0.65),
        })
    return rows


def expected_cells():
    exp = set()
    for m in C.load_scenarios():
        ls = C.probe_long_side(m["_config"], m["_video"])
        for model in C.MODELS:
            for imgsz in C.cell_imgsz_list(m["_config"], ls):
                exp.add((m["name"], model, imgsz))
    return exp


def main():
    recs, sentinels = load_records()
    cells = build_cells(recs, sentinels)
    exp = expected_cells()
    have = set(cells)
    missing = sorted(exp - have)
    print(f"cells scored: {len(have & exp)}/{len(exp)}  "
          f"(missing {len(missing)})")
    if missing[:10]:
        for k in missing[:10]:
            print(f"  missing: {k}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

    knees = knee_table(cells)
    deltas = family_deltas(cells)
    fps = fps_table(cells)
    front = frontier(cells)
    seeds = seed_validation(cells)

    # ---- digest ----
    import statistics
    print("\n=== (b) yolo26 vs yolo11, same tier+imgsz (delta<0 => 26 wins) ===")
    by_tier = defaultdict(list)
    for d in deltas:
        by_tier[d["tier"]].append(d["delta"])
    for t in TIERS:
        v = by_tier.get(t, [])
        if not v:
            continue
        wins = sum(1 for x in v if x < -1e-9)
        loss = sum(1 for x in v if x > 1e-9)
        print(f"  tier {t}: n={len(v)} mean={statistics.mean(v):+.4f} "
              f"median={statistics.median(v):+.4f} "
              f"26wins={wins} 11wins={loss} ties={len(v)-wins-loss}")

    print("\n=== (a) knee net-height per model tier (median over scenarios) ===")
    by_mt = defaultdict(list)
    for k in knees.values():
        if k["knee_net_height"] is not None:
            by_mt[k["model"]].append(k["knee_net_height"])
    for model in C.MODELS:
        v = by_mt.get(model, [])
        if v:
            print(f"  {model:>14s}: median_knee={statistics.median(v):6.1f}px "
                  f"p75={statistics.quantiles(v, n=4)[2]:6.1f}px n={len(v)}")

    print("\n=== (a) above-knee gain (knee_score - max_imgsz_score, + => gain) ===")
    gains = [k["above_knee_gain"] for k in knees.values()
             if k["above_knee_gain"] is not None]
    if gains:
        print(f"  n={len(gains)} mean={statistics.mean(gains):+.4f} "
              f"median={statistics.median(gains):+.4f} "
              f"p90={sorted(gains)[int(0.9 * (len(gains) - 1))]:+.4f}")

    print("\n=== (d) 5a seed regret (seed_score - best_score) ===")
    regs = [s["regret"] for s in seeds]
    if regs:
        within = sum(1 for r in regs if r <= 0.02)
        print(f"  n={len(regs)} mean={statistics.mean(regs):+.4f} "
              f"median={statistics.median(regs):+.4f} "
              f"<=0.02: {within}/{len(regs)} "
              f"({100 * within / len(regs):.0f}%)")

    print("\n=== (c) cheapest cell within 0.02 of oracle, per scenario ===")
    for scen in sorted(front):
        f = front[scen]
        c = f["cheapest_within_eps"]
        if c:
            print(f"  {scen:>16s}: oracle={f['oracle_score']:.3f}  "
                  f"cheapest~= {c['model']}@{c['imgsz']} "
                  f"({c['yolo_ms']}ms, {c['score']:.3f})")

    out = {
        "cells": {f"{s}|{m}|{i}": v for (s, m, i), v in sorted(cells.items())},
        "knees": {f"{s}|{m}": v for (s, m), v in sorted(knees.items())},
        "family_deltas": deltas,
        "fps_table": fps,
        "frontier": front,
        "seed_validation": seeds,
        "missing_cells": [list(k) for k in missing],
    }
    out_path = C.PHASE_DIR / "analysis.json"
    out_path.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
