#!/usr/bin/env python3
"""Demo KPI (PLAN_25M) for replay timelines: tracker vs emitted (identity-slot) stream.

  kpi.py --manifest M.json T1.timeline.json [T2 ...]       (labels = file names)
Prints one row per timeline and stream: two-point presence (C1 coverage), longest hole, holes >= 1 s,
coasting share, point-on-dancer (all frames), spatial validity, jitter at rest, lag, fast error, id switches,
frames with more points than dancers. Targets from PLAN_25M are shown on the header line.
"""
import argparse, json, os, sys
sys.path.insert(0, "/data/WallDance/application/tests"); sys.path.insert(0, "/data/WallDance/application/src")
import scoring, output_quality

COLS = [("2pts", "continuity", "coverage", "{:.3f}", ">=.95"), ("hole_s", "continuity", "gap_max_s", "{:.2f}", "<=1"),
        ("holes1s", "continuity", "gaps_ge_1s", "{}", "0"), ("coast", "quality", "coasting_share", "{:.3f}", "<=.10"),
        ("onDancer", "continuity", "on_dancer", "{:.3f}", ">=.95"), ("spatial", "continuity", "spatial_validity", "{:.3f}", ""),
        ("jit%h", "quality", "jitter_rest_pct", "{:.2f}", "<=1.5"), ("lag_ms", "quality", "lag_ms", "{:.0f}", "<=80"),
        ("fastE", "quality", "fast_err_h", "{:.3f}", ""), ("sw", "quality", "id_switches", "{}", ""),
        ("overN", "quality", "over_n_frames", "{}", "0"), ("ids", "quality", "distinct_ids", "{}", "")]

def rows_for(path, manifest):
    rows = scoring._load_timeline(path)
    fps = manifest.get("fps") or 19.8
    return output_quality.compare_streams(rows, manifest, fps=fps)

def fmt(v, f):
    if v is None: return "-"
    try: return f.format(v)
    except Exception: return str(v)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--manifest"); ap.add_argument("--json")
    ap.add_argument("--n", type=int, help="no manifest: constant dancer count (field takes)")
    ap.add_argument("--fps", type=float, default=19.8)
    ap.add_argument("--streams", default="tracker,emitted"); ap.add_argument("timelines", nargs="+")
    a = ap.parse_args()
    if a.manifest:
        m = scoring.load_scenario(a.manifest)
    else:
        m = {"name": "field", "start": 0, "frames": 10 ** 9, "warmup": 15, "fps": a.fps, "expected_count": a.n or 2,
             "reference": {"min_conf": 0.5, "tol_h": 0.75, "exclude_spots": []}}
    print(f"{'run':<34}{'stream':<9}" + "".join(f"{c[0]:>9}" for c in COLS))
    print(f"{'(target)':<43}" + "".join(f"{c[4]:>9}" for c in COLS))
    out = {}
    for t in a.timelines:
        rep = rows_for(t, m); lab = os.path.basename(t).replace(".timeline.json", "")
        out[lab] = rep
        for s in a.streams.split(","):
            b = rep.get(s)
            if not b: continue
            print(f"{lab:<34}{s:<9}" + "".join(f"{fmt((b.get(sec) or {}).get(k), f):>9}" for _n, sec, k, f, _t in COLS))
    if a.json: json.dump(out, open(a.json, "w"), indent=1)

main()
