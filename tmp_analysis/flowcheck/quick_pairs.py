#!/usr/bin/env python3
"""Early numbers: analyse every finished base_/<label>_ timeline pair in a flow_check run folder."""
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "application" / "tests"))
import flow_check as F  # noqa: E402

run = Path(sys.argv[1])
label = sys.argv[2] if len(sys.argv) > 2 else "s1"
for b in sorted(run.glob("base_*.json")):
    stem = b.name[len("base_"):-len(".json")]
    c = run / f"{label}_{stem}.json"
    if not c.exists():
        continue
    out = []
    for tag, p in (("base", b), (label, c)):
        rows = F.load_rows(p)
        ev, q = F.analyse(rows), F.quality(rows)
        holes = [(h["t"], h["dur_s"]) for h in ev["holes"] if not h.get("startup")]
        out.append(f"{tag}: cov {ev['coverage']:.3f} on {q.get('on_dancer')} holes {holes} "
                   f"jumps {[(j['t'], j['dist_h']) for j in ev['jumps']]} ghost {ev['ghost_frames']} "
                   f"sw {ev['id_switches']} lag {q.get('lag_ms')}")
    print(stem)
    for o in out:
        print("   ", o)
