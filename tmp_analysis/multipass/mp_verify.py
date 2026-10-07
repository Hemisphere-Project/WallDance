#!/usr/bin/env python3
"""E4 -- verification pass: does a second, zoomed look at a candidate separate ghosts from people?

Candidates come from a replay timeline's internal tracker tracks (``--internal``): a track that YOLO kept
seeing (fss 0) but that never moved (90 % of its positions within 0.3 h, >= 30 seen frames) is labelled ``static``; one
that travelled > 1 h is ``moving``.  On the ghost-guard scenes the static ones are the known ghosts (the
bdx1005-s5 dark figure by the door, the facade's windows) and the moving ones are people.  ``--fixed``
adds a known position instead (the night empty take's equipment ghost).

For up to --per frames of each candidate where YOLO saw it, the candidate's box confidence under:
  full      the scene's pass (production imgsz, production enhancement) -- what the tracker got;
  full_n    the same with neutral enhancement (gamma 1, no CLAHE);
  zoom_g / zoom_n / zoom_l   a k x h crop at native resolution, letterboxed to 640, with the scene's /
            neutral / crop-local enhancement.
A verification pass separates them if ghosts fall below a threshold that people stay above.

  python mp_verify.py --scenario bdx1005-s5-ghost --timeline T.json --out O.json
  python mp_verify.py --video V.avi --roi 173,401,1304,566 --gamma 1.8 --clahe 1.5 --imgsz 1280 \\
      --fixed 654,685,174,static --frames 100:300:5 --out O.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mp_study import APP, R, Runner, enhance, full_pass, match, zoom_pass  # noqa: E402,F401


def candidates_from_timeline(rows, per=40, min_seen=30, any_src=False):
    """``any_src``: every frame of a track counts, also motion-only ones (extreme dark: YOLO hardly sees
    anything, the tracker runs on motion blobs) -- 'moving' then means a moving motion track."""
    seen = {}
    for r in rows:
        for t in r.get("int") or []:
            if (any_src or t.get("fss") == 0) and t.get("p") and t.get("h"):
                seen.setdefault(t["id"], []).append((r["abs_frame"], float(t["p"][0]), float(t["p"][1]), float(t["h"])))
    out = []
    for tid, obs in seen.items():
        if len(obs) < min_seen:
            continue
        xs, ys, hs = [o[1] for o in obs], [o[2] for o in obs], [o[3] for o in obs]
        mx, my, mh = st.median(xs), st.median(ys), st.median(hs)
        d = np.sort(np.hypot(np.array(xs) - mx, np.array(ys) - my))
        spread = float(d[int(0.9 * (len(d) - 1))])            # p90: a track can swallow a passer-by once
        travel = float(np.percentile(xs, 90) - np.percentile(xs, 10) + np.percentile(ys, 90) - np.percentile(ys, 10))
        label = "static" if spread < 0.3 * mh else ("moving" if travel > 1.0 * mh else None)
        if label is None:
            continue
        step = max(1, len(obs) // per)
        for f, x, y, h in obs[::step][:per]:
            out.append({"tid": tid, "label": label, "f": f, "cx": x, "cy": y, "h": h})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario")
    ap.add_argument("--timeline")
    ap.add_argument("--video")
    ap.add_argument("--roi", help="x,y,w,h (with --video)")
    ap.add_argument("--gamma", type=float)
    ap.add_argument("--clahe", type=float)
    ap.add_argument("--imgsz", type=int)
    ap.add_argument("--fixed", action="append", default=[], help="x,y,h,label")
    ap.add_argument("--frames", default=None, help="a:b:step for --fixed")
    ap.add_argument("--per", type=int, default=40)
    ap.add_argument("--any-src", action="store_true", help="motion-only track frames count too")
    ap.add_argument("--k", type=float, default=3.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="/data/WallDance/models/yolo11x-pose.pt")
    a = ap.parse_args()

    if a.scenario:
        man = json.load(open(APP / "tests" / "scenarios" / f"{a.scenario}.json"))
        cfg = R.scenario_config(man)
        if "profiles" in cfg:
            import core.config_schema as cs
            cfg = cs.flatten(cfg)
        video = R.PROJECTS_DIR / man["project"] / "recordings" / man["recording_fingerprint"]["file"]
        xf = R.input_transform_for(cfg)
    else:
        cfg, video, xf = {}, Path(a.video), None
    gamma = a.gamma if a.gamma is not None else float(cfg.get("gamma", 1.0))
    clahe = a.clahe if a.clahe is not None else float(cfg.get("clahe_clip", 2.0))
    imgsz = a.imgsz or int(cfg.get("yolo_imgsz", 1280))
    cap = cv2.VideoCapture(str(video))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if a.roi:
        roi = tuple(int(v) for v in a.roi.split(","))
    elif cfg.get("roi_enabled") and cfg.get("roi_w"):
        roi = (int(cfg["roi_x"]), int(cfg["roi_y"]), int(min(cfg["roi_w"], W - cfg["roi_x"])),
               int(min(cfg["roi_h"], H - cfg["roi_y"])))
    else:
        roi = (0, 0, W, H)
    cands = []
    if a.timeline:
        cands = candidates_from_timeline(json.load(open(a.timeline)), per=a.per, any_src=a.any_src)
    if a.fixed:
        f0, f1, fs = (int(v) for v in a.frames.split(":"))
        for spec in a.fixed:
            x, y, h, label = spec.split(",")
            cands += [{"tid": f"fixed{x}", "label": label, "f": f, "cx": float(x), "cy": float(y), "h": float(h)}
                      for f in range(f0, f1, fs)]
    run = Runner(a.model)
    res = {"scenario": a.scenario or str(video), "roi": roi, "gamma": gamma, "clahe": clahe, "imgsz": imgsz, "rows": []}
    by_frame = {}
    for c in cands:
        by_frame.setdefault(c["f"], []).append(c)
    for f in sorted(by_frame):
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            continue
        if xf is not None:
            frame = xf.apply(frame)
        full = full_pass(run, frame, roi, imgsz, 0.05, gamma, clahe, "full")
        full_n = full_pass(run, frame, roi, imgsz, 0.05, 1.0, 0.0, "full_n")
        for c in by_frame[f]:
            row = dict(c)
            for name, dets in (("full", full), ("full_n", full_n)):
                m = match(dets, c["cx"], c["cy"], c["h"])
                row[name] = round(m["conf"], 3) if m else 0.0
            for name, g, cl, loc in (("zoom_g", gamma, clahe, False), ("zoom_n", 1.0, 0.0, False),
                                     ("zoom_l", gamma, clahe, True)):
                dets = zoom_pass(run, frame, roi, c["cx"], c["cy"], c["h"], a.k, 640, 0.05, g, cl, loc, name)
                m = match(dets, c["cx"], c["cy"], c["h"])
                row[name] = round(m["conf"], 3) if m else 0.0
            res["rows"].append(row)
    Path(a.out).write_text(json.dumps(res))
    for label in ("static", "moving"):
        rows = [r for r in res["rows"] if r["label"] == label]
        if not rows:
            continue
        tids = sorted({str(r["tid"]) for r in rows})
        print(f"{label:7s} n={len(rows):4d} tracks={len(tids)}  " + "  ".join(
            f"{k}: p50 {st.median([r[k] for r in rows]):.2f} >=.25 {100 * sum(r[k] >= .25 for r in rows) / len(rows):3.0f}%"
            for k in ("full", "full_n", "zoom_g", "zoom_n", "zoom_l")))


if __name__ == "__main__":
    main()
