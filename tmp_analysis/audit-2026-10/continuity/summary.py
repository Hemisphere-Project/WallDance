#!/usr/bin/env python3
"""One line per run: continuity + ghost + id metrics (N=1 scenes)."""
import json, sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import analyze, slotsim  # noqa

D = Path(__file__).parent
fps = float(__import__("os").environ.get("FPS", 19.7))
hdr = (f"{'run':22s} {'cov':>6s} {'eps':>4s} {'g/min':>5s} {'p90s':>5s} {'maxs':>5s} {'>=.5s':>5s} "
       f"{'ids':>4s} {'swNG':>4s} {'2rep':>5s} {'mispl':>5s} {'intIDs':>6s} {'slot.5cov':>9s} {'slotErr':>7s} | drop causes")
print(hdr)
for name in sys.argv[1:]:
    p = D / "runs" / f"{name}.json"
    if not p.exists():
        print(f"{name:22s} (missing)"); continue
    meta, rows = analyze.load(p)
    out = subprocess.run([sys.executable, str(D / "analyze.py"), str(p), "--fps", str(fps)],
                         capture_output=True, text=True).stdout
    a = json.loads(out)
    c = a["continuity"]
    s = slotsim.run(rows, fps, 0.5, None, 1.5, 15)
    print(f"{name:22s} {c['coverage']:6.4f} {c['drop_eps']:4d} {c['gaps_per_min']:5.1f} {c['gap_p90_s']:5.2f} "
          f"{c['gap_max_s']:5.2f} {c['gaps_ge_0.5s']:5d} {c['distinct_ids']:4d} {c['id_switch_no_gap']:4d} "
          f"{a['multi_report_frames']:5d} {a['spatial_misplaced']:5d} {a['internal_ids_total']:6d} {s['coverage']:9.4f} {s['err_gt_075h']:7.4f} | "
          + ", ".join(f"{k.replace('split:det_feeds_unconfirmed_track','split').replace('frozen_gate:','fz/').replace('frozen_gate(dancer pos unknown)','fz/unk')}={v}" for k, v in a["drop_cause"].items()))
