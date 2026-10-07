#!/usr/bin/env python3
"""Why a hole: replay one take x variant and print, per 0.5 s around [t0, t1] (s, window-relative to the take start),
the emitted states, the YOLO refs, the internal tracker tracks and the foreground summary.
  python extra/wdremote.py --slot dev py --with tmp_analysis/plan25m/validate_night.py tmp_analysis/plan25m/hole_diag.py -- --take s4 --variant oldcalfg --t0 30 --t1 48"""
import argparse, json, os, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_night as vn
ap = argparse.ArgumentParser(); ap.add_argument("--take"); ap.add_argument("--variant"); ap.add_argument("--t0", type=float)
ap.add_argument("--t1", type=float); a = ap.parse_args()
slot, stem, roi, (lo, hi), pk = vn.TAKES[a.take]
args = list(vn.REC) + list(vn.VARIANTS[a.variant]) + ["--set", "roi_enabled=true"]
for k, v in roi.items(): args += ["--set", f"{k}={v}"]
if a.variant in ("fg", "nobelt", "fgh150", "oldcalfg"): args += ["--set", f"fg_plate={vn.plate_rel(pk)}"]
tl = Path(os.environ.get("WD_REMOTE_OUT", ".")) / "tl.json"
subprocess.run([sys.executable, "tests/replay.py", "--project", vn.P, "--slot", str(slot), "--video",
                f"../projects/{vn.P}/recordings/{stem}.avi", "--model", "yolo11x-pose", "--imgsz", "1280", "--trt",
                "--quality", "--internal", "--timeline", str(tl), "--start", str(int(a.t0 * 20) - 200 if a.t0 * 20 > 200 else 0),
                "--frames", str(int((a.t1 - a.t0) * 20) + 200)] + args, check=True, capture_output=True)
rows = json.load(open(tl)); tl.unlink()
for r in rows:
    f = r.get("abs_frame", r["frame"])
    if not (a.t0 * 20 <= f <= a.t1 * 20) or f % 10: continue
    em = [(e["id"], e.get("state"), [round(v) for v in e["centroid"]]) for e in (r.get("emitted") or {}).get("tracks") or []]
    refs = [([round(v) for v in d["c"]], round(d["h"]), round(d.get("conf") or 0, 2)) for d in r.get("ref") or []]
    ints = [(t["id"], t.get("emit"), t.get("fss"), t.get("tsu")) for t in r.get("int") or []]
    print(f"t={f / 20:5.1f} out={em} ref={refs} int={ints} fg={r.get('fg')}")
