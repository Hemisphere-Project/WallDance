#!/usr/bin/env python3
"""Print flow_check's hole list (with the §C.14 start-hole fields) for stored flow-check timelines.
check_holes.py timeline.json [...]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "application" / "tests"))
import flow_check as F  # noqa: E402

for p in sys.argv[1:]:
    ev = F.analyse(F.load_rows(Path(p)))
    print(Path(p).stem, "longest", ev["longest_hole_s"], "holes", ev["holes"][:2])
