#!/usr/bin/env python3
"""Contact sheets to classify a take (PLAN_25M A1): what it represents and how many dancers are on the wall.

Two sheets per take, strongly brightened (gamma 2.6 + CLAHE, visualisation only):
  <take>_full.jpg  every --full-stride frames, whole frame (context: operator near the lens, lights, ROI)
  <take>_roi.jpg   every --stride frames, the ROI band, with overlays from a replay timeline if given:
                   YOLO detections (yellow, conf), emitted slot points (green live / orange coasting / cyan belt)
Usage (from application/):
  .venv/bin/python ../tmp_analysis/plan25m/take_sheets.py --video V --out-dir D [--timeline T.json] [--roi x,y,w,h]
  .venv/bin/python ../tmp_analysis/plan25m/take_sheets.py --project P --slot N          (newest take of the slot)
On the laptop over 4G:  python extra/wdremote.py py tmp_analysis/plan25m/take_sheets.py -- --project P --slot N
(output goes to $WD_REMOTE_OUT, ~150-300 KB per take).
"""
import argparse, json, os, sys
import cv2, numpy as np

def brighten(g):
    lut = (np.power(np.arange(256) / 255.0, 1 / 2.6) * 255).astype(np.uint8)
    g = cv2.LUT(g, lut)
    return cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)

STATE_COL = {"live": (60, 220, 60), "coasting": (0, 160, 255), "belt": (255, 220, 0)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video"); ap.add_argument("--out-dir", default=os.environ.get("WD_REMOTE_OUT", "."))
    ap.add_argument("--project"); ap.add_argument("--slot", type=int)
    ap.add_argument("--timeline"); ap.add_argument("--roi", default=None)
    ap.add_argument("--stride", type=int, default=25); ap.add_argument("--full-stride", type=int, default=100)
    ap.add_argument("--tile-w", type=int, default=420); ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--max-tiles", type=int, default=48)
    a = ap.parse_args()
    if not a.video:
        import glob
        root = next((r for r in (".", "..", "../..") if os.path.isdir(os.path.join(r, "projects"))), "..")
        takes = sorted(glob.glob(os.path.join(root, "projects", a.project, "recordings", f"slot_{a.slot}_*.avi")))
        if not takes:
            raise SystemExit(f"no take for {a.project} slot {a.slot}")
        a.video = takes[-1]
    os.makedirs(a.out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(a.video))[0]
    rows = {}
    if a.timeline:
        for r in json.load(open(a.timeline)):
            rows[r.get("abs_frame", r["frame"])] = r
    cap = cv2.VideoCapture(a.video); n = int(cap.get(7)); W = int(cap.get(3)); H = int(cap.get(4))
    roi = tuple(int(v) for v in a.roi.split(",")) if a.roi else (0, 0, W, H)
    full_tiles, roi_tiles = [], []
    stride = max(a.stride, int(np.ceil(n / a.max_tiles)))
    for f in range(n):
        ok = cap.grab()
        if not ok: break
        want_full = f % a.full_stride == 0; want_roi = f % stride == 0
        if not (want_full or want_roi): continue
        ok, fr = cap.retrieve()
        g = brighten(fr[:, :, 0] if fr.ndim == 3 else fr)
        if want_full:
            t = cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)
            x, y, w, h = roi; cv2.rectangle(t, (x, y), (x + w, y + h), (255, 0, 255), 4)
            s = a.tile_w / W; t = cv2.resize(t, (a.tile_w, int(H * s)), interpolation=cv2.INTER_AREA)
            cv2.putText(t, f"f{f}", (6, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
            full_tiles.append(t)
        if want_roi:
            x, y, w, h = roi
            t = cv2.cvtColor(g[y:y + h, x:x + w], cv2.COLOR_GRAY2BGR)
            r = rows.get(f)
            lab = f"f{f}"
            if r is not None:
                for d in r.get("ref") or []:
                    cx, cy, hh = d["c"][0] - x, d["c"][1] - y, d["h"]
                    cv2.rectangle(t, (int(cx - 0.2 * hh), int(cy - 0.5 * hh)), (int(cx + 0.2 * hh), int(cy + 0.5 * hh)), (0, 255, 255), 2)
                    cv2.putText(t, f"{d.get('conf') or 0:.2f}", (int(cx - 0.2 * hh), int(cy - 0.5 * hh) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
                for e in (r.get("emitted") or {}).get("tracks") or []:
                    cx, cy = e["centroid"][0] - x, e["centroid"][1] - y
                    col = STATE_COL.get(e.get("state"), (255, 255, 255))
                    cv2.circle(t, (int(cx), int(cy)), 14, col, -1)
                    cv2.putText(t, f"D{e['id']}", (int(cx) + 16, int(cy) + 8), cv2.FONT_HERSHEY_SIMPLEX, 1.1, col, 3)
                em = (r.get("emitted") or {}).get("tracks") or []
                lab += f" det{len(r.get('ref') or [])} pts{len(em)}"
            s = a.tile_w / w; t = cv2.resize(t, (a.tile_w, int(h * s)), interpolation=cv2.INTER_AREA)
            cv2.putText(t, lab, (6, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
            roi_tiles.append(t)
    for kind, tiles in (("full", full_tiles), ("roi", roi_tiles)):
        if not tiles: continue
        th, tw = tiles[0].shape[:2]
        tiles = [cv2.resize(t, (tw, th)) if t.shape[:2] != (th, tw) else t for t in tiles]
        while len(tiles) % a.cols: tiles.append(np.zeros_like(tiles[0]))
        grid = np.vstack([np.hstack(tiles[i:i + a.cols]) for i in range(0, len(tiles), a.cols)])
        p = os.path.join(a.out_dir, f"{name}_{kind}.jpg"); cv2.imwrite(p, grid, [cv2.IMWRITE_JPEG_QUALITY, 80])
        print(p, grid.shape)
main()
