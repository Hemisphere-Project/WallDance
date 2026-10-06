#!/usr/bin/env python3
"""Offline slot-layer sweeps over a replay timeline (no YOLO): stability, raw vs smoothed input, coast_s, hidden-follow, velocity decay."""
import sys, json, os
sys.path.insert(0, "/data/WallDance/application/tests"); sys.path.insert(0, "/data/WallDance/application/src")
import scoring, output_quality
from slot_replay import simulate, params_with
from core.identity_slots import SlotParams
import os; R = os.environ.get("WD_REVIEW_OUT", "/tmp/wd-review") + "/runs"
name = sys.argv[1]; scen = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] != "-" else None
max_d = int(sys.argv[3]) if len(sys.argv) > 3 else 2
rows = scoring._load_timeline(f"{R}/{name}.timeline.json")
manifest = scoring.load_scenario(f"/data/WallDance/application/tests/scenarios/{scen}.json") if scen else None
fps = (manifest or {}).get("fps") or 19.8
base = SlotParams()
if manifest is not None: base.max_dancers = max(1, scoring.max_expected(manifest))
else: base.max_dancers = max_d
variants = [("default", [], "smoothed")]
variants += [(f"stab={s}", [f"stability={s}"], "smoothed") for s in (0.0, 0.25, 0.75, 1.0)]
variants += [("raw stab=0.5", [], "raw"), ("raw stab=0.75", ["stability=0.75"], "raw")]
variants += [(f"coast={c}", [f"coast_s={c}"], "smoothed") for c in (0.5, 1.0, 3.0, 5.0)]
variants += [("hidden_max=0", ["hidden_max_s=0.0"], "smoothed"), ("hidden_max=10", ["hidden_max_s=10.0"], "smoothed")]
variants += [("tau=0(hold)", ["vel_decay_tau_s=0.0"], "smoothed"), ("tau=1e9(constV)", ["vel_decay_tau_s=1e9"], "smoothed")]
variants += [("entry_fss=20", ["entry_max_fss=20"], "smoothed"), ("minhits=4,streak=2", ["min_hits=4", "min_streak=2", "entry_min_hits=6", "entry_min_streak=2"], "smoothed")]
keys = [("cov","continuity","coverage"),("onD","continuity","on_dancer"),("spat","continuity","spatial_validity"),("eps","continuity","drop_episodes"),
        ("gapmax","continuity","gap_max_s"),("ghostF","continuity","ghost_frames"),("coast","quality","coasting_share"),("jit","quality","jitter_rest_pct"),
        ("lag","quality","lag_ms"),("fastE","quality","fast_err_h"),("sw","quality","id_switches"),("overN","quality","over_n_frames"),("ids","quality","distinct_ids")]
print(f"{name} ({scen or 'no manifest, N='+str(base.max_dancers)}), fps {fps}")
print(f"{'variant':<20}" + "".join(f"{k[0]:>8}" for k in keys))
tr = output_quality.compare_streams(rows, manifest, fps=fps, max_dancers=None if manifest else base.max_dancers)["tracker"]
def line(label, b):
    vals = []
    for _n, sec, key in keys:
        v = (b.get(sec) or {}).get(key)
        vals.append("   -" if v is None else (f"{v:8.3f}" if isinstance(v,float) else f"{v:8}"))
    print(f"{label:<20}" + "".join(f"{v:>8}" for v in vals))
line("TRACKER", tr)
for label, sets, pos in variants:
    p = params_with(sets, SlotParams(**base.__dict__))
    sim = simulate(rows, p, fps, pos)
    rep = output_quality.compare_streams(sim, manifest, fps=fps, max_dancers=None if manifest else base.max_dancers)
    line(label, rep["emitted"])
