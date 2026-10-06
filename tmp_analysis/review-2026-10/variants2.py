#!/usr/bin/env python3
"""Two output-only variants, offline: (A) rebind to warm-up internal tracks with a fresh skeleton inside the slot gate
(entry into a lost slot stays strict); (B) bind on the smoothed centroid but feed the One-Euro with the raw KF centroid."""
import sys, math
sys.path.insert(0, "/data/WallDance/application/tests"); sys.path.insert(0, "/data/WallDance/application/src")
import scoring, output_quality
from slot_replay import candidates_from_row, hidden_from_row
from core.identity_slots import IdentitySlots, SlotParams, SlotCandidate, OneEuro2D, stability_params
import os; R = os.environ.get("WD_REVIEW_OUT", "/tmp/wd-review") + "/runs"
name, scen = sys.argv[1], sys.argv[2]
rows = sorted(scoring._load_timeline(f"{R}/{name}.timeline.json"), key=lambda r: r["frame"])
m = scoring.load_scenario(f"/data/WallDance/application/tests/scenarios/{scen}.json"); fps = m.get("fps") or 19.8
N = max(1, scoring.max_expected(m))
keys = [("cov","continuity","coverage"),("onD","continuity","on_dancer"),("spat","continuity","spatial_validity"),("eps","continuity","drop_episodes"),
        ("gapmax","continuity","gap_max_s"),("ghostF","continuity","ghost_frames"),("coast","quality","coasting_share"),("jit","quality","jitter_rest_pct"),
        ("lag","quality","lag_ms"),("fastE","quality","fast_err_h"),("sw","quality","id_switches"),("overN","quality","over_n_frames")]
def line(label, b):
    vals = []
    for _n, sec, key in keys:
        v = (b.get(sec) or {}).get(key); vals.append("   -" if v is None else (f"{v:8.3f}" if isinstance(v,float) else f"{v:8}"))
    print(f"{label:<26}" + "".join(f"{v:>8}" for v in vals))
def run(params, warmup_bind=False, split_raw=None):
    slots = IdentitySlots(params); out = []
    filts = {}
    for r in rows:
        t = r.get("abs_frame", r["frame"]) / fps
        cands = candidates_from_row(r); have = {c.key for c in cands}
        if warmup_bind:
            for it in r.get("int") or []:
                k = int(it["id"])
                if k in have or it.get("emit") != "warmup" or int(it.get("fss", 99)) != 0 or int(it.get("tsu", 1)) != 0: continue
                h = float(it["h"]); cands.append(SlotCandidate(key=k, x=float(it["sm"][0]), y=float(it["sm"][1]), w=0.4*h, h=h,
                                                             hits=int(it["hits"]) + 100, fss=0, tsu=0))   # hits+100: passes min_hits for REBIND; entry needs streak too
                slots._streak[k] = max(slots._streak.get(k, 0), 0)
        res = slots.update(cands, t, hidden=hidden_from_row(r, set(slots.bound_keys()) - {c.key for c in cands}))
        em = []
        rawpos = {int(tk["id"]): tk.get("raw") for tk in (r.get("tracks") or [])}
        for o in res:
            x, y = o.x, o.y
            if split_raw is not None:
                f = filts.setdefault(o.slot_id, OneEuro2D(*stability_params(split_raw)))
                if o.state == "live" and o.key in rawpos and rawpos[o.key] is not None:
                    v = f(rawpos[o.key], t, scale=max(1.0, o.h)); x, y = float(v[0]), float(v[1])
                else:
                    f.reset(); x, y = o.x, o.y   # coasting: the slot's own output; filter restarts on the next live frame
            em.append({"id": o.slot_id, "bbox": [x - o.w/2, y - o.h/2, o.w, o.h], "centroid": [x, y], "state": o.state, "key": o.key})
        row = dict(r); row["emitted"] = {"reported": len(res), "ids": sorted(o.slot_id for o in res), "tracks": em}; out.append(row)
    return output_quality.compare_streams(out, m, fps=fps)["emitted"]
print(f"{name} ({scen}) N={N}")
print(f"{'variant':<26}" + "".join(f"{k[0]:>8}" for k in keys))
line("default", run(SlotParams(max_dancers=N)))
# (A) warm-up rebind: entry stays strict (entry_min_hits 12 + streak 3 on REPORTED frames only -> warm-up tracks never open a lost slot
#     because their streak counter counts frames they appear as candidates... so also require entry_min_streak 3: a warm-up track
#     appearing 3 frames in a row could enter -> keep entry_min_hits at 112 to block: hits+100 < 112 until real hits >= 12)
line("A: warmup rebind", run(SlotParams(max_dancers=N, min_hits=100, min_streak=1, entry_min_hits=112, entry_min_streak=3), warmup_bind=True))
line("A: warmup rebind, streak2", run(SlotParams(max_dancers=N, min_hits=100, min_streak=2, entry_min_hits=112, entry_min_streak=3), warmup_bind=True))
line("B: split raw filt stab.5", run(SlotParams(max_dancers=N), split_raw=0.5))
line("B: split raw filt stab.75", run(SlotParams(max_dancers=N), split_raw=0.75))
line("B: split raw filt stab1.0", run(SlotParams(max_dancers=N), split_raw=1.0))
line("A+B(.75)", run(SlotParams(max_dancers=N, min_hits=100, min_streak=1, entry_min_hits=112, entry_min_streak=3), warmup_bind=True, split_raw=0.75))
