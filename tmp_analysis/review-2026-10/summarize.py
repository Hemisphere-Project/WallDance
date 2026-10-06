#!/usr/bin/env python3
"""Compact table of tracker vs emitted streams from replay logs (JSON report at the end of each log)."""
import json, sys, glob, os
import os; R = os.environ.get("WD_REVIEW_OUT", "/tmp/wd-review") + "/runs"
def report(path):
    txt = open(path).read()
    i = txt.rfind('\n{\n'); j = txt.rfind('\n}')
    if i < 0 or j < 0: return None
    try: return json.loads(txt[i+1:j+2])
    except Exception as e: return None
cols = [("cov","continuity","coverage"),("onD","continuity","on_dancer"),("spat","continuity","spatial_validity"),
        ("ids","continuity","distinct_ids"),("eps","continuity","drop_episodes"),("gapmax","continuity","gap_max_s"),
        ("ghostF","continuity","ghost_frames"),("dupF","continuity","dup_frames"),
        ("coast","quality","coasting_share"),("belt","quality","belt_share"),("jit","quality","jitter_rest_pct"),
        ("refjit","quality","ref_jitter_rest_pct"),("lag","quality","lag_ms"),("fastE","quality","fast_err_h"),
        ("sw","quality","id_switches"),("overN","quality","over_n_frames"),("close","quality","close_pair_frames"),("qids","quality","distinct_ids")]
hdr = f"{'run':<16}{'stream':<9}" + "".join(f"{c[0]:>8}" for c in cols)
print(hdr)
for log in sorted(glob.glob(f"{R}/*.log")):
    name = os.path.basename(log)[:-4]
    if name == "run_all": continue
    rep = report(log)
    if not rep: print(f"{name:<16} (no report yet)"); continue
    for stream in ("tracker","emitted"):
        b = rep.get(stream)
        if not b: continue
        vals = []
        for _n, sec, key in cols:
            v = (b.get(sec) or {}).get(key)
            vals.append("   -" if v is None else (f"{v:8.3f}" if isinstance(v,float) else f"{v:8}"))
        print(f"{name:<16}{stream:<9}" + "".join(f"{v:>8}" for v in vals))
