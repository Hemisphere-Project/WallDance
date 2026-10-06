#!/usr/bin/env python3
"""PLAN_25M A2: where does the emitted (slot) stream lose the dancers?

For every reference dancer position (manifest reference: YOLO conf floor, fixed ghost spots excluded, top-N)
not covered by an emitted point within tol_h x h, classify why, from the replay row:
  bound_lag        the slot bound to the tracker track on the dancer is > tol away (filter lag / snap)
  dup_blocked      an unbound reported track is on the dancer but within dup_bind_h of another emitting slot
  slots_full       an unbound reported track is on the dancer, all slots emit elsewhere (ghost / other dancer twice)
  not_established  an unbound reported track is on the dancer, a slot is free/coasting but the track is too young / out of gate
  tracker_warmup   no reported track, an internal one is on the dancer (warm-up / hidden)
  tracker_blind    no tracker track at all on the dancer (a YOLO det exists: it was gated / filtered)
and for emitted points that sit on no reference dancer: coasting vs live (and the live ones' bound-track skeleton age).
  attribute.py --manifest M.json T.timeline.json [--dup-h 0.5]
"""
import argparse, math, sys
from collections import Counter
sys.path.insert(0, "/data/WallDance/application/tests")
import scoring, continuity

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--manifest", required=True); ap.add_argument("timeline")
    ap.add_argument("--dup-h", type=float, default=0.5); ap.add_argument("--max-dancers", type=int, default=None)
    ap.add_argument("--ref-conf", type=float, default=None, help="override the manifest reference conf floor")
    a = ap.parse_args()
    m = scoring.load_scenario(a.manifest)
    if a.ref_conf is not None:
        m = dict(m); m["reference"] = dict(m.get("reference") or {}, min_conf=a.ref_conf)
    cfg = continuity._ref_cfg(m); tol = float(cfg["tol_h"])
    rows = sorted(scoring._load_timeline(a.timeline), key=lambda r: r["frame"]); warm = int(m.get("warmup", 0))
    nmax = a.max_dancers or max(1, scoring.max_expected(m))
    cause = Counter(); off = Counter(); nref = 0; covered = 0
    near = lambda x, y, rx, ry, h, k=tol: math.hypot(x - rx, y - ry) <= k * h
    for r in rows:
        if r["frame"] < warm: continue
        n = scoring.expected_at(m, r["frame"])
        refs = continuity.reference_positions(r, n, cfg) if n > 0 else []
        em = (r.get("emitted") or {}).get("tracks") or []
        trk = r.get("tracks") or []
        # the replay rows do not carry the slot's bound tracker id: a live slot's raw position IS its bound
        # track's (smoothed) centroid, so recover the binding from it
        for e in em:
            e["key"] = None
            if e.get("state") == "live" and e.get("raw") is not None:
                for t in trk:
                    if math.hypot(t["centroid"][0] - e["raw"][0], t["centroid"][1] - e["raw"][1]) < 1.0:
                        e["key"] = t["id"]; break
        for e in em:
            if refs and not any(near(e["centroid"][0], e["centroid"][1], q["c"][0], q["c"][1], q["h"]) for q in refs):
                st = e.get("state", "?")
                if st == "live":
                    fss = int(e.get("fss") if e.get("fss") is not None else 999)
                    st = "live, skeleton this frame" if fss == 0 else ("live, skeleton <= 10 f old" if fss <= 10 else "live, no skeleton > 10 f (blob-fed)")
                off[st] += 1
        for q in refs:
            nref += 1
            rx, ry, h = q["c"][0], q["c"][1], q["h"]
            if any(near(e["centroid"][0], e["centroid"][1], rx, ry, h) for e in em):
                covered += 1; continue
            on = [t for t in trk if near(t["centroid"][0], t["centroid"][1], rx, ry, h)]
            if on:
                keys = {e.get("key") for e in em}
                if any(t["id"] in keys for t in on):
                    cause["bound_lag"] += 1
                elif any(any(near(t["centroid"][0], t["centroid"][1], e["centroid"][0], e["centroid"][1], max(h, e["bbox"][3]), a.dup_h) for e in em) for t in on):
                    cause["dup_blocked"] += 1
                elif len(em) >= nmax and all(e.get("state") == "live" for e in em):
                    cause["slots_full_live"] += 1
                else:
                    # a slot is free or coasting, yet the reported track on the dancer is not bound:
                    # out of the coasting slot's gate, or not established yet (streak / hits / skeleton age)
                    t = on[0]
                    young = int(t.get("hits", 99)) < 8
                    co = [e for e in em if e.get("state") != "live"]
                    if co:
                        d = min(math.hypot(e["centroid"][0] - t["centroid"][0], e["centroid"][1] - t["centroid"][1]) / max(1.0, e["bbox"][3]) for e in co)
                        cause["coasting_slot_no_rebind (%s, %s)" % ("young track" if young else "established", "d<=1.5h" if d <= 1.5 else "d>1.5h")] += 1
                    else:
                        cause["free_slot_no_entry (%s)" % ("young track" if young else "established")] += 1
            elif any(near(t["sm"][0], t["sm"][1], rx, ry, h) for t in (r.get("int") or [])):
                cause["tracker_warmup"] += 1
            else:
                cause["tracker_blind"] += 1
    unc = nref - covered
    print(f"[ref conf >= {cfg.get('min_conf')}] {a.timeline.split('/')[-1]}: reference dancer-frames {nref}, covered {covered} ({covered/max(1,nref):.1%}), uncovered {unc}")
    for k, v in cause.most_common(): print(f"   {v:6d} {v/max(1,unc):6.1%}  {k}")
    print(f"   emitted points off every reference dancer: {dict(off)}")

main()
