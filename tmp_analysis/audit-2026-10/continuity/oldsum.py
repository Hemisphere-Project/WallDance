#!/usr/bin/env python3
"""replay.py-style track classes (real/marginal/ghost by hits) + count metrics for runs."""
import json, sys
from pathlib import Path
sys.path.insert(0, "/data/WallDance/application"); sys.path.insert(0, "/data/WallDance/application/tests")
sys.path.insert(0, str(Path(__file__).parent))
from analyze_session import collect_stats, classify_tracks  # noqa
from contmetrics import cont_metrics  # noqa
D = Path(__file__).parent
print(f"{'run':24s} real marg ghost tot | cov  eps maxgap ids 2rep")
for n in sys.argv[1:]:
    p = D / "runs" / f"{n}.json"
    if not p.exists():
        print(n, "missing"); continue
    rows = json.loads(p.read_text())["rows"]
    st = collect_stats(D / "logs" / n / "tracking_events.jsonl")
    tr = classify_tracks(st)
    tl = [{"frame": r["f"], "reported": len(r["rep"]), "ids": [t["id"] for t in r["rep"]]} for r in rows]
    c = cont_metrics(tl, {"expected_count": 1, "fps": 19.7, "warmup": 15})
    two = sum(1 for r in rows[15:] if len(r["rep"]) > 1)
    print(f"{n:24s} {len(tr['real']):4d} {len(tr['marginal']):4d} {len(tr['ghost']):5d} {len(tr['all']):3d} | "
          f"{c['coverage']:.3f} {c['drop_eps']:3d} {c['gap_max_s']:5.2f} {c['distinct_ids']:3d} {two:4d}")
