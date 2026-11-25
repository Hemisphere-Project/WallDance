#!/usr/bin/env python3
"""F1's gate tool (Plan Cordée rev 2): the app's own clean-plate foreground on a take, against the 08/10 truth
and against the boxes a network is sure of.

For every ``--every``-th frame of the take: the raw frame, rotated as the app sees it, its ROI, through
``core.foreground.ForegroundDetector`` with a given plate (no plate update, as a project load would start).
Then three measures:

* **under sure boxes** -- of the boxes a network gives at conf >= 0.5 (``low`` and ``ora`` of a replay jsonl),
  body-sized (60-330 px), on the wall and below the roof line + 100: the share with >= 64 px^2 of foreground
  inside the box itself. It does not use the truth.
* **on the dancer** -- share of truth dancer-frames (own or shared group, not coasted) with a foreground group
  on her (``truth_score.on``).
* **groups on nobody** per frame, inside the wall polygon (the truth's domain: it knows nobody outside the wall,
  e.g. on the roof parapet the ROI may include). Groups are the mask's components joined at about half a body (as
  the truth's own groups), at least 64 px^2. Groups outside the wall are counted apart.

  fg_eval.py <project> <take.avi> --plate plate.npz [--truth tests/truth0810/<stem>.npz] [--replay stem.ship.jsonl]
             [--every 3] [--set k_frame=10 --set clip_aware=false ...] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import replay as R  # noqa: E402  (bootstraps the venv's CUDA libs and src/ on sys.path)

import cv2  # noqa: E402

from core.foreground import CleanPlate, FgParams, ForegroundDetector  # noqa: E402
from truth_score import WALL, Truth, in_poly, on, read_jsonl  # noqa: E402

PROJECTS = HERE.parent.parent / "projects"


def groups_of(mask, x0, y0, ds, join):
    """Components of the mask joined by ``join``, each >= 64 px^2: [(cx, cy, area)] in original px."""
    if mask is None or not mask.any():
        return []
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, join))
    out = []
    for k in range(1, n):
        x, y, w, h = st[k, :4]
        sel = (lab[y:y + h, x:x + w] == k) & (mask[y:y + h, x:x + w] > 0)
        area = int(sel.sum()) * ds * ds
        if area < 64:
            continue
        ys, xs = np.nonzero(sel)
        out.append((x0 + ds * (xs.mean() + x + 0.5), y0 + ds * (ys.mean() + y + 0.5), area))
    return out


def evaluate(project, take, plate_path, truth=None, replay_rows=None, every=3, params=None, max_frames=None):
    video = PROJECTS / project / "recordings" / take
    meta = json.loads(Path(str(video) + ".meta").read_text())
    cfg = meta["config"]
    xf = R.input_transform_for(cfg)
    plate = CleanPlate.load(plate_path)
    det = ForegroundDetector(plate, params or FgParams())
    ds = plate.ds
    join = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 21) if ds == 4 else (25, 41))
    rows = {int(r["frame"]): r for r in replay_rows} if replay_rows else {}
    cap = cv2.VideoCapture(str(video))
    sure_n = sure_hit = 0
    d_n = d_hit = 0
    off = 0
    outside = 0
    frames = 0
    invalid = {}
    thr = []
    ms = []
    i = 0
    poly = None
    while True:
        if max_frames is not None and i >= max_frames:
            break
        if i % every:
            if not cap.grab():
                break
            i += 1
            continue
        ok, fr = cap.read()
        if not ok:
            break
        fr = xf.apply(fr)
        H, W = fr.shape[:2]
        if poly is None:
            poly = WALL.get((W, H))
        if cfg.get("roi_enabled") and cfg.get("roi_w") and cfg.get("roi_h"):
            rx, ry = max(0, int(cfg["roi_x"])), max(0, int(cfg["roi_y"]))
            rx1, ry1 = min(W, rx + int(cfg["roi_w"])), min(H, ry + int(cfg["roi_h"]))
        else:
            rx, ry, rx1, ry1 = 0, 0, W, H
        t0 = time.perf_counter()
        f = det.process(fr[ry:ry1, rx:rx1, 0], rx, ry, (W, H))
        ms.append((time.perf_counter() - t0) * 1000)
        frames += 1
        if not f.valid:
            invalid[f.reason] = invalid.get(f.reason, 0) + 1
        else:
            thr.append(det.last_thr)
        mask = f.mask if f.valid else None
        grp = groups_of(mask, f.x0, f.y0, ds, join)
        # boxes a network is sure of
        r = rows.get(i)
        if r is not None and poly is not None:
            sure = [b for b in (r.get("low") or []) if b[4] >= 0.5] + [b for b in (r.get("ora") or []) if b[4] >= 0.5]
            for b in sure:
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                if not (60 <= b[3] - b[1] <= 330 and in_poly(cx, cy, poly) and cy > poly[0][1] + 100):
                    continue
                sure_n += 1
                if mask is not None:
                    a0, b0 = int((b[0] - f.x0) // ds), int((b[1] - f.y0) // ds)
                    a1, b1 = int((b[2] - f.x0) // ds) + 1, int((b[3] - f.y0) // ds) + 1
                    under = mask[max(0, b0):max(0, b1), max(0, a0):max(0, a1)]
                    sure_hit += int(under.sum()) * ds * ds >= 64
        if truth is not None:
            present = [(p, bb) for _, p, bb, _, _ in truth.at(i) if p[2] < 2]
            for p, bb in present:
                d_n += 1
                d_hit += any(on(gx, gy, p, bb, truth.h_take) for gx, gy, _ in grp)
            for gx, gy, _ in grp:
                if poly is not None and not in_poly(gx, gy, poly):
                    outside += 1
                elif not any(on(gx, gy, p, bb, truth.h_take) for p, bb in present):
                    off += 1
        i += 1
    return {"project": project, "take": take, "plate": str(plate_path), "frames": frames, "every": every,
            "invalid": invalid, "valid_share": round(1 - sum(invalid.values()) / max(1, frames), 4),
            "thr_median": round(float(np.median(thr)), 2) if thr else None,
            "ms_median": round(float(np.median(ms)), 2) if ms else None,
            "sure_boxes": sure_n, "fg_under_sure": round(sure_hit / sure_n, 4) if sure_n else None,
            "dancer_frames": d_n, "group_on_dancer": round(d_hit / d_n, 4) if d_n else None,
            "groups_on_nobody_per_frame": round(off / max(1, frames), 3) if truth is not None else None,
            "groups_outside_wall_per_frame": round(outside / max(1, frames), 3) if truth is not None else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("project")
    ap.add_argument("take")
    ap.add_argument("--plate", required=True)
    ap.add_argument("--truth", default=None)
    ap.add_argument("--replay", default=None)
    ap.add_argument("--every", type=int, default=3)
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="PARAM=VALUE",
                    help="an FgParams field, e.g. k_frame=10, clip_aware=false")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    params = FgParams()
    for kv in a.sets:
        k, v = kv.split("=", 1)
        setattr(params, k, R.parse_set_value(v))
    truth = Truth.load(a.truth) if a.truth else None
    rows = list(read_jsonl(a.replay)) if a.replay else None
    res = evaluate(a.project, a.take, a.plate, truth, rows, a.every, params, a.max)
    res["params"] = a.sets
    print(json.dumps(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
