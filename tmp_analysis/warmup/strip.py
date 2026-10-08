#!/usr/bin/env python3
"""Image strip of a take at given frames with the timeline's dets / internal tracks / emitted points.
strip.py video timeline.json out.jpg f1,f2,...  (frames are timeline frame numbers; --offset N adds N
to read the video, for a primed timeline whose frame 0 is a later video frame)"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

video, tl, out, frames = sys.argv[1], sys.argv[2], sys.argv[3], [int(x) for x in sys.argv[4].split(",")]
rows = {r["frame"]: r for r in json.loads(Path(tl).read_text())}
clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
cap = cv2.VideoCapture(video)
tiles = []
for f in frames:
    cap.set(cv2.CAP_PROP_POS_FRAMES, f)
    ok, fr = cap.read()
    if not ok:
        continue
    g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
    img = cv2.cvtColor(clahe.apply(g), cv2.COLOR_GRAY2BGR)
    r = rows.get(f, {})
    for x in r.get("ref") or []:
        if isinstance(x, dict):
            cx, cy, h = x["c"][0], x["c"][1], x.get("h") or 100
            cv2.rectangle(img, (int(cx - .2 * h), int(cy - .5 * h)), (int(cx + .2 * h), int(cy + .5 * h)), (0, 220, 255), 3)
            cv2.putText(img, f"{x.get('conf', 0):.2f}", (int(cx - .2 * h), int(cy - .5 * h) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 220, 255), 3)
    for t in r.get("int") or []:
        cv2.circle(img, (int(t["p"][0]), int(t["p"][1])), 14, (255, 120, 0), -1)
        cv2.putText(img, f"#{t['id']} {t.get('emit')}", (int(t["p"][0]) + 16, int(t["p"][1])),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 120, 0), 3)
    for e in (r.get("emitted") or {}).get("tracks") or []:
        c = e["centroid"]
        cv2.circle(img, (int(c[0]), int(c[1])), 22, (80, 220, 80), -1)
    cv2.putText(img, f"{f / 20:.2f}s", (12, 60), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (255, 255, 255), 4)
    tiles.append(cv2.resize(img, (400, int(400 * img.shape[0] / img.shape[1]))))
cv2.imwrite(out, np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 75])
