#!/usr/bin/env python3
"""processor.dancer_height (the readiness "dancer size" input) inside each take's wall window -> extra.json."""
import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
r = json.loads((HERE / "results.json").read_text())
extra = {}
for v in ("V0", "V1", "V1r"):
    for s in r["variants"][v]:
        tl = json.loads((HERE / "tl" / f"{v}_{s}.json").read_text())
        wa, wb = r["variants"][v][s]["wall_win"]
        dh = [row.get("dh") for row in tl[wa:wb + 1] if row.get("dh")]
        info = json.loads((HERE / "tl" / f"{v}_{s}.json.info.json").read_text())
        extra.setdefault(v, {})[s] = {"dh_wall_med": round(float(np.median(dh)), 1) if dh else None,
                                      "dh_wall_last": dh[-1] if dh else None, "lb_scale": info["lb_scale"]}
        print(v, s, extra[v][s])
(HERE / "extra.json").write_text(json.dumps(extra, indent=1))
