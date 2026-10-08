#!/usr/bin/env python3
"""Scene measurements for the 30 m confirmation (no YOLO; CPU): per frame of each mur30m-0710 take

  gt    the walker, independent of the tracker: pixels that differ from BOTH the take's empty start and its
        empty end (median frames) by > 10 DN, opened, then closed with a tall kernel; the largest blob's box
        (x, y, w, h), its area, and the frame's total foreground. Static changes during a take (the belt hung on
        the stand in slot 2, the stand moved in slot 4) match one of the two backgrounds and drop out.
  dn    8-bit DN as recorded: door, plain wall, floor at the wall base (fixed boxes), and the walker's torso
        (from the V1 YOLO box, conf >= 0.5, when the walker is at the far wall): median of the torso box and
        median of its foreground pixels
  belt  BeltDetector.detect (global mode, no static map) on the far-wall band: every accepted blob (cx, cy, w,
        h, peak, mean, score), tagged "stand" (within 30 px of the stand's belt, found in the empty end / start)
        or "worn" (inside the walker's box)
-> scene_<take>.json.  Also the 25 m night takes (mur25m-night-common): DN and belt on the dancer from the
flow-check baseline timelines' YOLO boxes (no gt pass).

  scene_measure.py [--takes 30|25|all]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/data/WallDance/application/src")
import cv2  # noqa: E402
import numpy as np  # noqa: E402
from core.belt_detector import BeltDetector, BeltParams  # noqa: E402

HERE = Path(__file__).resolve().parent
P30 = Path("/data/WallDance/projects/mur30m-0710/recordings")
P25 = Path("/data/WallDance/projects/mur25m-night-common/recordings")
NIGHT = Path("/data/WallDance/tmp_analysis/flowcheck/night_rc_46a0262")
# take: (empty start [a, b), empty end [a, None)) from the motion arrays (tmp_analysis/laptop_0710/motion_*.npy)
T30 = {"slot_2_20261007_200742": ((2, 50), (1700, None)),
       "slot_3_20261007_201246": ((2, 18), (1072, None)),
       "slot_4_20261007_201949": ((2, 48), (1242, None)),
       "slot_5_20261007_202412": ((2, 50), (1058, None))}
# fixed boxes (x0, y0, x1, y1), original px of each night's frame
BOX30 = {"door": (790, 450, 960, 700), "wall": (1200, 420, 1450, 560), "floor": (400, 760, 1400, 850)}
BOX25 = {"door": (690, 450, 860, 700), "wall": (1100, 420, 1350, 560), "floor": (300, 760, 1300, 850)}  # = BOX30 - 100 px in x (same wall scale)
BAND30 = (300, 420, 1500, 800)      # far-wall band for the belt pass (x0, y0, x1, y1)
BAND25 = (200, 380, 1300, 820)
T25 = {"slot_2_20261006_211831": "s2a bright, still", "slot_3_20261006_212855": "s3 bright, moving",
       "slot_4_20261006_213310": "s4 dark, moving", "slot_6_20261006_214306": "s6 dark, entry/exit",
       "slot_2_20261006_214531": "s2c dark, still at the back", "slot_7_20261006_220640": "s7 dark, moving"}


def gray_frames(video: Path, start=0, stop=None):
    cap = cv2.VideoCapture(str(video))
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    i = start
    while stop is None or i < stop:
        ok, fr = cap.read()
        if not ok:
            break
        yield i, (fr[:, :, 0] if fr.ndim == 3 else fr)
        i += 1
    cap.release()


def median_bg(video: Path, a, b):
    fr = [g.copy() for _i, g in gray_frames(video, a, b)]
    return np.median(np.stack(fr), axis=0).astype(np.int16)


def box_stats(g, box):
    x0, y0, x1, y1 = box
    v = g[y0:y1, x0:x1]
    return {"med": float(np.median(v)), "mean": round(float(v.mean()), 2), "p90": float(np.percentile(v, 90))}


def belt_blobs(det, g, band):
    x0, y0, x1, y1 = band
    out = []
    for b in det.detect(g, roi=(x0, y0, x1 - x0, y1 - y0)):
        x_0, y_0, x_1, y_1 = b.bbox
        win = g[y_0:y_1 + 1, x_0:x_1 + 1]
        out.append({"cx": round(b.cx, 1), "cy": round(b.cy, 1), "w": round(b.w, 1), "h": round(b.h, 1),
                    "peak": int(b.peak), "mean": round(float(b.mean), 1), "win_mean": round(float(win.mean()), 1),
                    "bg": round(float(b.bg), 1), "score": round(float(b.score), 2), "sat": round(float(b.sat_frac), 3)})
    return out


def yolo_boxes(timeline: Path):
    """frame -> best V1/baseline YOLO box (cx, cy, h, conf) with conf >= 0.5 (raw if present, else ref)."""
    rows = json.loads(timeline.read_text())
    out = {}
    for r in rows:
        best = None
        for d in r.get("raw") or []:
            if d[0] >= 0.5 and (best is None or d[0] > best[3]):
                best = (d[1], d[2], d[3], d[0])
        if best is None:
            for d in r.get("ref") or []:
                if isinstance(d, dict) and (d.get("conf") or 0) >= 0.5 and (best is None or d["conf"] > best[3]):
                    best = (d["c"][0], d["c"][1], d["h"], d["conf"])
        if best is not None:
            out[r["frame"]] = best
    return out


def torso(g, fgmask, cx, cy, h):
    top = cy - h / 2
    x0, x1 = int(cx - 0.12 * h), int(cx + 0.12 * h) + 1
    y0, y1 = int(top + 0.2 * h), int(top + 0.45 * h) + 1
    v = g[max(0, y0):y1, max(0, x0):x1]
    if v.size == 0:
        return None
    res = {"med": float(np.median(v)), "p90": float(np.percentile(v, 90))}
    if fgmask is not None:
        m = fgmask[max(0, y0):y1, max(0, x0):x1] > 0
        if m.sum() >= 10:
            res["fg_med"] = float(np.median(v[m]))
    return res


def stand_positions(video: Path, segs, det):
    pos = []
    for a, b in segs:
        found = []
        for i, g in gray_frames(video, a, b if b is not None else a + 40):
            bl = [x for x in belt_blobs(det, g, (900, 550, 1300, 760))]
            if bl:
                found.append((bl[0]["cx"], bl[0]["cy"]))
        if found:
            pos.append((round(float(np.median([f[0] for f in found])), 1),
                        round(float(np.median([f[1] for f in found])), 1), len(found)))
        else:
            pos.append(None)
    return pos


def measure30(stem: str):
    video = P30 / f"{stem}.avi"
    (sa, sb), (ea, eb) = T30[stem]
    bg0 = median_bg(video, sa, sb)
    bg1 = median_bg(video, ea, eb)
    det = BeltDetector(BeltParams())
    stands = stand_positions(video, [(sa, sb), (ea, eb)], det)
    yb = yolo_boxes(HERE / "tl" / f"V1_{stem}.json")
    kc = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 41))
    ko = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    rows = []
    for i, g in gray_frames(video):
        gi = g.astype(np.int16)
        d = np.minimum(np.abs(gi - bg0), np.abs(gi - bg1))
        m = (d > 10).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, ko)
        mc = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kc)
        n, lab, st, _c = cv2.connectedComponentsWithStats(mc)
        row = {"f": i, "fg": int(m.sum())}
        if n > 1:
            k = 1 + int(np.argmax(st[1:, 4]))
            x, y, w, h, a = (int(v) for v in st[k])
            row["gt"] = [x, y, w, h, a]
            if n > 2:
                k2 = 1 + int(np.argsort(st[1:, 4])[-2])
                if st[k2, 4] > 400:
                    row["gt2"] = [int(v) for v in st[k2]]
        if i % 5 == 0:
            row["dn"] = {k: box_stats(g, b) for k, b in BOX30.items()}
        if i in yb:
            cx, cy, h, conf = yb[i]
            row["yolo"] = [round(cx, 1), round(cy, 1), round(h, 1), round(conf, 3)]
            if h <= 240 and cy + h / 2 >= 680:
                row["torso"] = torso(g, m, cx, cy, h)
        bl = belt_blobs(det, g, (BAND30[0], BAND30[1], BAND30[2], BAND30[3]))
        if bl:
            row["belt"] = bl
        rows.append(row)
    out = {"take": stem, "stands": stands, "boxes": BOX30, "rows": rows}
    (HERE / "scene").mkdir(exist_ok=True)
    (HERE / "scene" / f"scene_{stem}.json").write_text(json.dumps(out))
    print(f"[scene] {stem}: {len(rows)} frames, stand belt at {stands}", flush=True)


def measure25(stem: str):
    video = P25 / f"{stem}.avi"
    tl = NIGHT / f"base_{stem}.json"
    yb = yolo_boxes(tl)
    det = BeltDetector(BeltParams())
    rows = []
    for i, g in gray_frames(video):
        row = {"f": i}
        if i % 5 == 0:
            row["dn"] = {k: box_stats(g, b) for k, b in BOX25.items()}
        if i in yb:
            cx, cy, h, conf = yb[i]
            row["yolo"] = [round(cx, 1), round(cy, 1), round(h, 1), round(conf, 3)]
            if h <= 260:
                row["torso"] = torso(g, None, cx, cy, h)
                x0, y0 = int(cx - 0.6 * h), int(cy - 0.6 * h)
                bl = belt_blobs(det, g, (max(0, x0), max(0, y0), int(cx + 0.6 * h), int(cy + 0.6 * h)))
                if bl:
                    row["belt"] = bl
        rows.append(row)
    (HERE / "scene").mkdir(exist_ok=True)
    (HERE / "scene" / f"scene25_{stem}.json").write_text(json.dumps({"take": stem, "boxes": BOX25, "rows": rows}))
    print(f"[scene] {stem}: {len(rows)} frames, {len(yb)} YOLO frames", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", default="all")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    cv2.setNumThreads(2)
    if a.takes in ("30", "all"):
        for s in T30:
            if a.only and a.only not in s:
                continue
            measure30(s)
    if a.takes in ("25", "all"):
        for s in T25:
            if a.only and a.only not in s:
                continue
            measure25(s)


if __name__ == "__main__":
    main()
