#!/usr/bin/env python3
"""Offline gate for slot-layer changes (PLAN_25M): re-run the identity-slot layer over recorded replay timelines
with parameter variants, score the emitted stream against each take's manifest (demo KPI).

  slot_eval.py --set-file variants.txt  [--only white-duo-full,...]
Datasets are the DATASETS list below (timeline + manifest + slots, optional injected static ghost).
Variant file: one per line "label | key=value key=value ..." (SlotParams fields); '#' comments.
"""
import argparse, copy, json, math, os, sys
sys.path.insert(0, "/data/WallDance/application/tests"); sys.path.insert(0, "/data/WallDance/application/src")
import scoring, output_quality
from slot_replay import candidates_from_row, hidden_from_row
from core.identity_slots import SlotParams, IdentitySlots, WeakMeasure

S = os.environ.get("WD_REVIEW_OUT", "/tmp/wd-review")
R = f"{S}/runs"; T = "/data/WallDance/application/tests/scenarios"
# YOLO detections at a 0.10 floor (the tau=0.10 sweep runs record them as `ref`), for sub-tau weak measurements
SUBTAU = {"white-duo-full": f"{S}/sweep/wd_tau010.timeline.json", "white-duo+ghost": f"{S}/sweep/wd_tau010.timeline.json",
          "texture-duo-full": f"{S}/sweep/td_tau010.timeline.json"}
DATASETS = [
    # name, timeline, manifest, max_dancers, injected ghost (x, y, h) or None
    ("white-duo-full", f"{R}/white-duo-full.timeline.json", f"{T}/white-duo-full.json", 2, None),
    ("white-duo+ghost", f"{R}/white-duo-full.timeline.json", f"{T}/white-duo-full.json", 2, (450, 550, 190)),
    ("texture-duo-full", f"{R}/texture-duo-full.timeline.json", f"{T}/texture-duo-full.json", 2, None),
    ("white-duo(win)", f"{R}/white_duo.timeline.json", f"{T}/white-duo.json", 2, None),
    ("texture-duo(win)", f"{R}/texture_duo.timeline.json", f"{T}/texture-duo.json", 2, None),
    ("aerial-full", f"{R}/aerial_full.timeline.json", f"{T}/hangar-aerial-full.json", 1, None),
    ("aerial-full,max2", f"{R}/aerial_full.timeline.json", f"{T}/hangar-aerial-full.json", 2, None),
    ("floor-full", f"{R}/floor_full.timeline.json", f"{T}/hangar-floor-full.json", 1, None),
    ("wallhang-clip(still)", f"{R}/wallhang_clip.timeline.json", f"{T}/texture-wallhang-clip.json", 1, None),
    ("texture-aerial", f"{R}/texture_aerial.timeline.json", f"{T}/texture-aerial.json", 1, None),
    ("blur-runner", f"{R}/blur_runner.timeline.json", f"{T}/blur-runner.json", 2, None),
    ("white-duo,cand", f"{S}/sweep/wd5_yf15_interm_excltool.timeline.json", f"{T}/white-duo-full.json", 2, None),
    ("texture-duo,cand", f"{S}/sweep/td2_interm_excl.timeline.json", f"{T}/texture-duo-full.json", 2, None),
    ("aerial,cand,max2(ghost)", f"{S}/sweep/ae3_yf15_interm.timeline.json", f"{T}/hangar-aerial-full.json", 2, None),
    ("bdx-s5(static fig),max1", f"{R}/bdx_s5_belt.timeline.json", f"{T}/bdx1005-s5-ghost.json", 1, None),
    ("bdx-s5(static fig),max2", f"{R}/bdx_s5_belt.timeline.json", f"{T}/bdx1005-s5-ghost.json", 2, None),
    ("bdx-s8(shadows),max2", f"{R}/bdx_s8_belt.timeline.json", f"{T}/bdx1005-s8-shadows.json", 2, None),
]
COLS = [("2pts", "continuity", "coverage", "{:.3f}"), ("onD", "continuity", "on_dancer", "{:.3f}"),
        ("spat", "continuity", "spatial_validity", "{:.3f}"), ("hole_s", "continuity", "gap_max_s", "{:.2f}"),
        ("coast", "quality", "coasting_share", "{:.3f}"), ("overN", "quality", "over_n_frames", "{}"),
        ("sw", "quality", "id_switches", "{}"), ("jit", "quality", "jitter_rest_pct", "{:.2f}"),
        ("lag", "quality", "lag_ms", "{:.0f}")]

def inject(rows, ghost):
    x, y, h = ghost
    out = []
    for r in rows:
        r = dict(r); f = r.get("abs_frame", r["frame"])
        r["tracks"] = list(r.get("tracks") or []) + [{"id": 900000, "bbox": [x - 0.2 * h, y - 0.5 * h, 0.4 * h, h],
                       "centroid": [x, y], "raw": [x, y], "hits": f + 1, "age": f + 1, "fss": 0, "tsu": 0, "src": "yolo"}]
        out.append(r)
    return out

def simulate(rows, p, fps, weak_src=None, subtau=None, tau=None, bounds=None):
    """slot_replay.simulate + weak measurements + bounds."""
    slots = IdentitySlots(p)
    out = []
    for r in sorted(rows, key=lambda r: r["frame"]):
        f = r.get("abs_frame", r["frame"]); t = f / fps
        cands = candidates_from_row(r)
        weak = []
        if weak_src in ("warmup", "both"):
            rep_ids = {c.key for c in cands}
            for it in r.get("int") or []:
                if int(it["id"]) in rep_ids or it.get("emit") == "ok": continue
                if int(it.get("fss", 99)) == 0 and int(it.get("tsu", 1)) == 0:
                    weak.append(WeakMeasure(float(it["sm"][0]), float(it["sm"][1]), float(it["h"]), "warmup"))
        if weak_src in ("subtau", "both") and subtau is not None:
            for d in subtau.get(f, []):
                if d.get("conf") is not None and d["conf"] < tau:
                    weak.append(WeakMeasure(float(d["c"][0]), float(d["c"][1]), float(d["h"]), "subtau"))
        res = slots.update(cands, t, hidden=hidden_from_row(r, set(slots.bound_keys()) - {c.key for c in cands}),
                           weak=weak, bounds=bounds)
        em = [{"id": o.slot_id, "bbox": [o.x - o.w / 2.0, o.y - o.h / 2.0, o.w, o.h], "centroid": [o.x, o.y],
               "state": o.state, "key": o.key} for o in res]
        row = dict(r); row["emitted"] = {"reported": len(res), "ids": sorted(o.slot_id for o in res), "tracks": em}
        if slots.events: row["slot_events"] = list(slots.events)
        out.append(row)
    return out


def parse_variants(path):
    out = []
    for line in open(path):
        line = line.split("#", 1)[0].strip()
        if not line: continue
        lab, _, kv = line.partition("|")
        sets = {}
        for tok in kv.split():
            k, _, v = tok.partition("="); sets[k.strip()] = json.loads(v)
        out.append((lab.strip(), sets))
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--set-file", required=True); ap.add_argument("--only", default="", help="dataset names separated by ;")
    ap.add_argument("--json")
    a = ap.parse_args()
    variants = parse_variants(a.set_file); only = set(x for x in a.only.split(";") if x)
    allres = {}
    for name, tl, man, nmax, ghost in DATASETS:
        if only and name not in only: continue
        if not os.path.exists(tl): print(f"-- {name}: missing {tl}"); continue
        m = scoring.load_scenario(man); fps = m.get("fps") or 19.8
        if ghost:
            m = copy.deepcopy(m); m.setdefault("reference", {}).setdefault("exclude_spots", [])
            m["reference"]["exclude_spots"] = list(m["reference"]["exclude_spots"]) + [[ghost[0], ghost[1], 0.5 * ghost[2]]]
        rows = scoring._load_timeline(tl)
        if ghost: rows = inject(rows, ghost)
        print(f"== {name} (max_dancers {nmax})")
        print(f"   {'variant':<30}" + "".join(f"{c[0]:>8}" for c in COLS) + "  yields")
        cfg = m.get("config") or {}
        tau = float(cfg.get("confidence", 0.25))
        bounds = None
        if cfg.get("roi_enabled") and cfg.get("roi_w"):
            bounds = (cfg["roi_x"], cfg["roi_y"], cfg["roi_x"] + cfg["roi_w"], cfg["roi_y"] + cfg["roi_h"])
        subtau = None
        if name in SUBTAU and os.path.exists(SUBTAU[name]):
            subtau = {r.get("abs_frame", r["frame"]): r.get("ref") or [] for r in scoring._load_timeline(SUBTAU[name])}
        for lab, sets in variants:
            p = SlotParams(max_dancers=nmax)
            sets = dict(sets); weak_src = sets.pop("weak_src", None)
            for k, v in sets.items():
                if not hasattr(p, k): raise SystemExit(f"unknown SlotParams field {k}")
                setattr(p, k, type(getattr(p, k))(v))
            sim = simulate(rows, p, fps, weak_src=weak_src, subtau=subtau, tau=tau, bounds=bounds)
            rep = output_quality.compare_streams(sim, m, fps=fps)["emitted"]
            ny = sum(1 for r in sim for ev in r.get("slot_events", []) if ev.get("ev") == "yield")
            vals = [(rep.get(sec) or {}).get(k) for _n, sec, k, f in COLS]
            print(f"   {lab:<30}" + "".join(f"{('-' if v is None else f.format(v)):>8}" for v, (_n, _s, _k, f) in zip(vals, COLS)) + f"  {ny:>6}")
            allres.setdefault(name, {})[lab] = {c[0]: v for c, v in zip(COLS, vals)} | {"yields": ny}
    if a.json: json.dump(allres, open(a.json, "w"), indent=1)

main()
