#!/usr/bin/env python3
"""Hold vs drift: inject synthetic tracker losses of k frames into the slot layer and measure the coasted position error.

At anchors (frames where the tracker reports exactly one fresh-skeleton track and the slot is live), snapshot the
slot layer, feed k empty frames (no candidates, no hidden tracks) and compare the slot output at t0+k with the
tracker's fresh-skeleton centroid at t0+k (what the slot would have received). Variants = velocity decay tau.
"""
import sys, json, copy, math, statistics as st
sys.path.insert(0, "/data/WallDance/application/tests"); sys.path.insert(0, "/data/WallDance/application/src")
import scoring
from slot_replay import candidates_from_row, hidden_from_row
from core.identity_slots import IdentitySlots, SlotParams
import os; R = os.environ.get("WD_REVIEW_OUT", "/tmp/wd-review") + "/runs"
name = sys.argv[1]; fps = float(sys.argv[2]) if len(sys.argv) > 2 else 19.7
rows = sorted(scoring._load_timeline(f"{R}/{name}.timeline.json"), key=lambda r: r["frame"])
KS = [1, 2, 5, 10, 20, 40]
TAUS = [0.0, 0.1, 0.25, 0.5, 1.0, 1e9]
def fresh_single(r):
    tr = r.get("tracks") or []
    if len(tr) != 1: return None
    t = tr[0]
    if int(t.get("fss", 99)) != 0 or int(t.get("tsu", 1)) != 0: return None
    return t
res = {tau: {k: {"fast": [], "slow": [], "fast_raw": [], "slow_raw": []} for k in KS} for tau in TAUS}
for tau in TAUS:
    p = SlotParams(max_dancers=1, vel_decay_tau_s=tau)
    slots = IdentitySlots(p)
    n_anchor = 0
    for i, r in enumerate(rows):
        t = r.get("abs_frame", r["frame"]) / fps
        cands = candidates_from_row(r)
        out = slots.update(cands, t, hidden=hidden_from_row(r, set(slots.bound_keys()) - {c.key for c in cands}))
        if i % 5 or i + 41 >= len(rows): continue
        tk = fresh_single(r)
        if tk is None or not out or out[0].state != "live" or out[0].key != int(tk["id"]): continue
        # speed class from the tracker centroid over the previous 5 frames
        prev = fresh_single(rows[i - 5]) if i >= 5 else None
        if prev is None or int(prev["id"]) != int(tk["id"]): continue
        h = float(tk["bbox"][3]); c0 = tk["centroid"]; cp = prev["centroid"]
        speed = math.hypot(c0[0] - cp[0], c0[1] - cp[1]) / h * fps / 5.0   # heights per second
        cls = "fast" if speed > 0.5 else "slow"
        n_anchor += 1
        snap = copy.deepcopy(slots)
        for k in range(1, 41):
            tk2 = snap.update([], (r.get("abs_frame", r["frame"]) + k) / fps, hidden={})
            if k in KS:
                truth_row = rows[i + k]; tt = fresh_single(truth_row)
                if tt is None or int(tt["id"]) != int(tk["id"]) or not tk2: continue
                o = tk2[0]
                e = math.hypot(o.x - tt["centroid"][0], o.y - tt["centroid"][1]) / h
                er = math.hypot(o.raw_x - tt["centroid"][0], o.raw_y - tt["centroid"][1]) / h
                res[tau][k][cls].append(e); res[tau][k][cls + "_raw"].append(er)
    print(f"tau={tau}: anchors {n_anchor}", file=sys.stderr)
def q(v, p): 
    if not v: return float('nan')
    v = sorted(v); return v[min(len(v)-1, int(p * len(v)))]
print(f"{name}: coasted-position error vs the fresh-skeleton centroid, median/p90 in dancer heights (smoothed output)")
for cls in ("fast", "slow"):
    print(f"--- {cls} anchors (n per cell ~ {len(res[0.25][5][cls])})")
    print(f"{'tau (s)':<12}" + "".join(f"{'k='+str(k):>14}" for k in KS))
    for tau in TAUS:
        lab = "hold" if tau == 0 else ("const-vel" if tau > 100 else f"{tau}")
        print(f"{lab:<12}" + "".join(f"{q(res[tau][k][cls],0.5):6.3f}/{q(res[tau][k][cls],0.9):5.2f}  " for k in KS))
    print(f"  (raw slot position, no One-Euro) k=5/10/20: " + ", ".join(f"tau {tau}: {q(res[tau][5][cls+'_raw'],0.5):.3f}/{q(res[tau][10][cls+'_raw'],0.5):.3f}/{q(res[tau][20][cls+'_raw'],0.5):.3f}" for tau in (0.0, 0.25, 1e9)))
