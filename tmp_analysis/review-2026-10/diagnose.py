#!/usr/bin/env python3
"""Per-frame attribution: where do emitted points sit vs the pseudo-GT dancers, and why are dancers uncovered?"""
import sys, math
from collections import Counter
sys.path.insert(0, "/data/WallDance/application/tests")
import scoring, continuity
import os; R = os.environ.get("WD_REVIEW_OUT", "/tmp/wd-review") + "/runs"
name, scen = sys.argv[1], sys.argv[2]
rows = sorted(scoring._load_timeline(f"{R}/{name}.timeline.json"), key=lambda r: r["frame"])
m = scoring.load_scenario(f"/data/WallDance/application/tests/scenarios/{scen}.json")
cfg = continuity._ref_cfg(m); tol = float(cfg["tol_h"]); warm = int(m.get("warmup", 0))
def near(x, y, refs): return any(math.hypot(x - r["c"][0], y - r["c"][1]) <= tol * float(r["h"]) for r in refs)
em_state = Counter(); em_off = Counter(); unc = Counter(); n_ref = 0; belt_rows = []
for r in rows:
    if r["frame"] < warm: continue
    n = scoring.expected_at(m, r["frame"])
    refs = continuity.reference_positions(r, n, cfg) if n > 0 else []
    em = (r.get("emitted") or {}).get("tracks") or []
    for t in em:
        st = t.get("state", "?"); c = t["centroid"]; em_state[st] += 1
        if refs and not near(c[0], c[1], refs): em_off[st] += 1
        if st == "belt": belt_rows.append((r["frame"], near(c[0], c[1], refs) if refs else None))
    for ref in refs:
        n_ref += 1
        if any(near(t["centroid"][0], t["centroid"][1], [ref]) for t in em): continue
        # uncovered dancer: why?
        trk = [t for t in (r.get("tracks") or []) if near(t["centroid"][0], t["centroid"][1], [ref])]
        internal = [t for t in (r.get("int") or []) if near(t["sm"][0], t["sm"][1], [ref])]
        if trk:
            unc["reported track on dancer, slot not bound (hits %s)" % ("<8" if min(int(t.get("hits", 0)) for t in trk) < 8 else ">=8")] += 1
        elif internal:
            unc["internal track on dancer, hidden (%s)" % ",".join(sorted({t.get("emit", "?") for t in internal}))] += 1
        else:
            unc["no tracker track on dancer (blind)"] += 1
print(f"== {name} ({scen}): ref dancer-frames {n_ref}")
print("   emitted points by state:", dict(em_state), " off-dancer (>%.2f h from every ref, refs present):" % tol, dict(em_off))
tot_unc = sum(unc.values()); print(f"   uncovered dancer-frames {tot_unc} ({100*tot_unc/max(1,n_ref):.1f} %):")
for k, v in unc.most_common(): print(f"      {v:5d}  {k}")
if belt_rows: print("   belt-state frames:", len(belt_rows), "on-dancer:", sum(1 for _f, ok in belt_rows if ok), "off:", sum(1 for _f, ok in belt_rows if ok is False), "frames", [f for f, _ in belt_rows][:12])
