#!/usr/bin/env python3
"""Variant C, offline: a reported track with no skeleton for > F frames that has moved > D x h from its own last
fresh-skeleton position is not followed (the slot coasts / re-binds instead). Hidden-follow drift bound set to D too."""
import sys, math
sys.path.insert(0, "/data/WallDance/application/tests"); sys.path.insert(0, "/data/WallDance/application/src")
import scoring, output_quality
from slot_replay import candidates_from_row, hidden_from_row
from core.identity_slots import IdentitySlots, SlotParams
import os; R = os.environ.get("WD_REVIEW_OUT", "/tmp/wd-review") + "/runs"
name, scen = sys.argv[1], sys.argv[2]
rows = sorted(scoring._load_timeline(f"{R}/{name}.timeline.json"), key=lambda r: r["frame"])
m = scoring.load_scenario(f"/data/WallDance/application/tests/scenarios/{scen}.json"); fps = m.get("fps") or 19.8
N = max(1, scoring.max_expected(m))
keys = [("cov","continuity","coverage"),("onD","continuity","on_dancer"),("spat","continuity","spatial_validity"),("eps","continuity","drop_episodes"),
        ("gapmax","continuity","gap_max_s"),("coast","quality","coasting_share"),("fastE","quality","fast_err_h"),("sw","quality","id_switches")]
def line(label, b):
    vals = []
    for _n, sec, key in keys:
        v = (b.get(sec) or {}).get(key); vals.append("   -" if v is None else (f"{v:8.3f}" if isinstance(v,float) else f"{v:8}"))
    print(f"{label:<26}" + "".join(f"{v:>8}" for v in vals))
def run(F=None, D=0.5, hidden_drift=None, mark_weak=False):
    p = SlotParams(max_dancers=N)
    if hidden_drift is not None: p.hidden_drift_h = hidden_drift
    slots = IdentitySlots(p); out = []; anchor = {}
    for r in rows:
        t = r.get("abs_frame", r["frame"]) / fps
        cands = candidates_from_row(r)
        for c in cands:
            if c.fss == 0: anchor[c.key] = (c.x, c.y)
        if F is not None:
            kept = []
            for c in cands:
                a = anchor.get(c.key)
                if c.fss is not None and c.fss > F and a is not None and math.hypot(c.x - a[0], c.y - a[1]) > D * max(1.0, c.h):
                    continue   # drifting blob-fed track: do not follow it
                kept.append(c)
            cands = kept
        res = slots.update(cands, t, hidden=hidden_from_row(r, set(slots.bound_keys()) - {c.key for c in cands}))
        em = [{"id": o.slot_id, "bbox": [o.x - o.w/2, o.y - o.h/2, o.w, o.h], "centroid": [o.x, o.y],
               "state": ("weak" if (mark_weak and o.state == "live" and o.fss is not None and o.fss > (F or 10)) else o.state), "key": o.key} for o in res]
        row = dict(r); row["emitted"] = {"reported": len(res), "ids": sorted(o.slot_id for o in res), "tracks": em}; out.append(row)
    rep = output_quality.compare_streams(out, m, fps=fps)["emitted"]
    weak = sum(1 for r in out for t in r["emitted"]["tracks"] if t["state"] == "weak")
    return rep, weak
print(f"{name} ({scen}) N={N}")
print(f"{'variant':<26}" + "".join(f"{k[0]:>8}" for k in keys) + "   weak-live frames")
rep, w = run(mark_weak=True); line("default (weak=fss>10)", rep); print(f"{'':<26}{'':>64}   {w}")
for F, D, hd in ((10, 0.5, 0.5), (20, 0.5, 0.5), (10, 0.75, 0.75), (20, 0.75, 0.75), (40, 0.75, 0.75)):
    rep, _ = run(F, D, hd); line(f"C: F={F} D={D}h", rep)
