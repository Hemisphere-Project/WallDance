#!/usr/bin/env python3
"""Brightened contact sheet of frames (for eyeballing N / dancer presence).
usage: sheet.py VIDEO OUT.png FRAME [FRAME ...] [--cols 8] [--w 240] [--marks rows.json]
Optionally overlays reported-track centroids (green) and raw top det (red) from a
drive.py replay json (--marks), keyed by abs frame."""
import sys, json, argparse
import cv2, numpy as np

ap = argparse.ArgumentParser()
ap.add_argument("video"); ap.add_argument("out"); ap.add_argument("frames", nargs="+", type=int)
ap.add_argument("--cols", type=int, default=8); ap.add_argument("--w", type=int, default=240)
ap.add_argument("--marks"); ap.add_argument("--gamma", type=float, default=2.6)
ap.add_argument("--crop", default=None, help="x,y,w,h crop in original px")
a = ap.parse_args()
marks = {}
if a.marks:
    d = json.load(open(a.marks))
    for r in d["rows"]:
        marks[r["abs"]] = r
cap = cv2.VideoCapture(a.video)
lut = np.array([((i / 255.0) ** (1.0 / a.gamma)) * 255 for i in range(256)], np.uint8)
clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
tiles = []
for f in a.frames:
    cap.set(cv2.CAP_PROP_POS_FRAMES, f)
    ok, img = cap.read()
    if not ok:
        continue
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = clahe.apply(cv2.LUT(g, lut))
    im = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
    r = marks.get(f)
    if r:
        for t in r.get("rep", []):
            if t.get("c"):
                cv2.circle(im, (int(t["c"][0]), int(t["c"][1])), 18, (0, 255, 0), 4)
                cv2.putText(im, str(t["id"]), (int(t["c"][0]) + 20, int(t["c"][1])), 0, 1.6, (0, 255, 0), 3)
        for tr in r.get("trk", []):
            if not any(t["id"] == tr["id"] for t in r.get("rep", [])):
                cv2.circle(im, (int(tr["p"][0]), int(tr["p"][1])), 14, (0, 200, 255), 3)
        for k, d in enumerate(r.get("top", [])[:2]):
            col = (0, 0, 255) if d["bc"] >= r.get("tau", 0.5) else (255, 0, 255)
            cx, cy, h = d["c"][0], d["c"][1], d["h"]
            cv2.rectangle(im, (int(cx - h * 0.3), int(cy - h / 2)), (int(cx + h * 0.3), int(cy + h / 2)), col, 3)
            cv2.putText(im, f"{d['bc']:.2f}", (int(cx - h * 0.3), int(cy - h / 2) - 6), 0, 1.2, col, 3)
    if a.crop:
        x, y, w, h = map(int, a.crop.split(","))
        im = im[y:y + h, x:x + w]
    s = a.w / im.shape[1]
    im = cv2.resize(im, (a.w, int(im.shape[0] * s)))
    cv2.putText(im, str(f), (4, 22), 0, 0.7, (0, 255, 255), 2)
    tiles.append(im)
cols = a.cols
th = max(t.shape[0] for t in tiles)
rows = []
for i in range(0, len(tiles), cols):
    row = tiles[i:i + cols]
    row = [cv2.copyMakeBorder(t, 0, th - t.shape[0], 0, 2, cv2.BORDER_CONSTANT) for t in row]
    while len(row) < cols:
        row.append(np.zeros_like(row[0]))
    rows.append(np.hstack(row))
cv2.imwrite(a.out, np.vstack(rows))
print("wrote", a.out, len(tiles))
