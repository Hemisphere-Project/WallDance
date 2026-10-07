#!/usr/bin/env python3
"""Contact sheet of the frames where the emitted stream has more points than dancers (N = 1 takes):
replay one take x variant (validate_night.py's settings), then crop around every emitted point on up to
24 such frames (brightened), with the slot id / state and the YOLO refs (yellow).  One JPEG comes back.
  python extra/wdremote.py --slot dev py --with tmp_analysis/plan25m/validate_night.py tmp_analysis/plan25m/ghost_sheet.py -- --take s6 --variant fg"""
import argparse, json, os, subprocess, sys
from pathlib import Path
import cv2, numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_night as vn

ap = argparse.ArgumentParser(); ap.add_argument("--take", default="s6"); ap.add_argument("--variant", default="fg")
ap.add_argument("--n", type=int, default=24); a = ap.parse_args()
slot, stem, roi, (lo, hi), pk = vn.TAKES[a.take]
args = list(vn.REC) + list(vn.VARIANTS[a.variant]) + ["--set", "roi_enabled=true"]
for k, v in roi.items(): args += ["--set", f"{k}={v}"]
if a.variant in ("fg", "nobelt", "fgh150", "oldcalfg"): args += ["--set", f"fg_plate={vn.plate_rel(pk)}"]
out = Path(os.environ.get("WD_REMOTE_OUT", ".")); tl = out / "tl.json"
subprocess.run([sys.executable, "tests/replay.py", "--project", vn.P, "--slot", str(slot), "--video",
                f"../projects/{vn.P}/recordings/{stem}.avi", "--model", "yolo11x-pose", "--imgsz", "1280", "--trt",
                "--quality", "--internal", "--timeline", str(tl)] + args, check=True, capture_output=True)
rows = {r["frame"]: r for r in json.load(open(tl))}
ghost = [f for f in sorted(rows) if lo <= f <= hi and len((rows[f].get("emitted") or {}).get("tracks") or []) > 1]
pick = ghost[:: max(1, len(ghost) // a.n)][: a.n]
cap = cv2.VideoCapture(f"../projects/{vn.P}/recordings/{stem}.avi"); lut = (np.power(np.arange(256) / 255.0, 1 / 2.4) * 255).astype(np.uint8)
COL = {"live": (60, 220, 60), "coasting": (0, 160, 255), "belt": (255, 220, 0), "fg": (235, 235, 235)}
tiles, i, want = [], 0, set(pick)
while want:
    ok, fr = cap.read()
    if not ok: break
    if i in want:
        want.discard(i); r = rows[i]; g = cv2.cvtColor(cv2.LUT(fr[:, :, 0], lut), cv2.COLOR_GRAY2BGR)
        em = r["emitted"]["tracks"]
        xs = [e["centroid"][0] for e in em]; ys = [e["centroid"][1] for e in em]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        half = max(220, (max(xs) - min(xs)) / 2 + 160, (max(ys) - min(ys)) / 2 + 160)
        for d in r.get("ref") or []:
            hh = d["h"]; c = d["c"]
            cv2.rectangle(g, (int(c[0] - .2 * hh), int(c[1] - .5 * hh)), (int(c[0] + .2 * hh), int(c[1] + .5 * hh)), (0, 255, 255), 2)
            cv2.putText(g, f"{d.get('conf') or 0:.2f}", (int(c[0] - .2 * hh), int(c[1] - .5 * hh) - 5), 0, 0.9, (0, 255, 255), 2)
        for e in em:
            col = COL.get(e.get("state"), (255, 0, 255)); x, y = int(e["centroid"][0]), int(e["centroid"][1])
            cv2.circle(g, (x, y), 12, col, -1); cv2.putText(g, f"D{e['id']} {e.get('state')}", (x + 14, y), 0, 1.0, col, 2)
        x0, y0 = int(max(0, cx - half)), int(max(0, cy - half)); x1, y1 = int(min(g.shape[1], cx + half)), int(min(g.shape[0], cy + half))
        t = cv2.resize(g[y0:y1, x0:x1], (300, 300)); cv2.putText(t, f"f{i}", (6, 24), 0, 0.8, (255, 255, 0), 2); tiles.append(t)
    i += 1
while len(tiles) % 6: tiles.append(np.zeros((300, 300, 3), np.uint8))
sheet = np.vstack([np.hstack(tiles[k:k + 6]) for k in range(0, len(tiles), 6)])
cv2.imwrite(str(out / f"ghosts_{a.take}_{a.variant}.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 70])
tl.unlink(); print(json.dumps({"ghost_frames": len(ghost), "shown": len(pick)}))
