#!/usr/bin/env python3
"""Smoke-test flow_check's analysis / plot / strip code on an existing timeline (no GPU)."""
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "application" / "tests"))
import flow_check as F  # noqa: E402

tl = Path(sys.argv[1])
video = Path(sys.argv[2])
out = Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
rows = F.load_rows(tl)
ev = F.analyse(rows)
print({k: (v if not isinstance(v, list) else len(v)) for k, v in ev.items()})
print("first jumps:", ev["jumps"][:3])
print("holes:", ev["holes"][:3])
print("quality:", F.quality(rows))
print("plot:", F.plot({"a": rows, "b": rows}, {"a": ev, "b": ev}, out / "xt.png", "selftest"))
if ev["jumps"]:
    print("strip:", F.strip(video, rows, ev["jumps"][0]["frame"], (137, 0, 1299, 1139), out / "strip.jpg", "t"))
print("sheet:", F.sheet(video, rows, (137, 0, 1299, 1139), out / "sheet.jpg"))
for p in sorted(out.iterdir()):
    print(p.name, p.stat().st_size)
