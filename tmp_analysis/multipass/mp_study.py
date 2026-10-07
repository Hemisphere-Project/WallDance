#!/usr/bin/env python3
"""Multi-pass study (2026-10-07, Thomas's brainstorm): what a second YOLO pass buys, measured offline on the
recorded scenarios with the scenario's own ROI + enhancement, yolo11x-pose (PyTorch FP16, dev37).

Truth comes from a replay timeline of the same scenario (``replay.py --quality --internal``):
  * live frames  -- an emitted slot in state ``live`` (YOLO-backed): its box centre / height;
  * gap frames   -- a slot coasting / held by the belt / the plate / weak (YOLO lost it): the position is
                    interpolated between the slot's live frames around the gap (<= 3 s).

Passes:
  FULL(s)   the whole ROI letterboxed to s (what the app does), s in --sizes;
  ZOOM      a square crop of side k x h around the dancer (native pixels, clipped), letterboxed to --zoom-size;
            enhancement ``g`` = the scene's gamma/CLAHE, ``l`` = local: gamma seeded from the crop's own mean.
Per dancer and pass: matched (centre within 0.5 h, height 0.5-2 x) -> box conf + mean keypoint conf.
Gap frames add a DECOY zoom at a position >= 2 h from every dancer (what a search pass "finds" on an empty
part of the wall) and FULL 1280 at two other gammas (would alternating the global setting catch it?).

  python mp_study.py --scenario white-duo-full --timeline T.json [--live-every 6] [--max-gap 400] --out O.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

APP = Path(__import__("os").environ.get("WD_APP", "/data/WallDance/application"))
sys.path.insert(0, str(APP / "tests"))
sys.path.insert(0, str(APP / "src"))
import replay as R  # noqa: E402

LIVE, GAPS = ("live",), ("coasting", "belt", "fg", "weak")


def enhance(bgr, gamma, clahe):
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    if clahe and clahe > 0:
        g = cv2.createCLAHE(clipLimit=float(clahe), tileGridSize=(8, 8)).apply(g)
    if gamma and abs(gamma - 1.0) > 1e-3:
        lut = (255.0 * (np.arange(256) / 255.0) ** (1.0 / float(gamma))).clip(0, 255).astype(np.uint8)
        g = cv2.LUT(g, lut)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def local_gamma(gray_crop, target=110.0, bounds=(0.8, 4.0)):
    b = float(np.clip(gray_crop.mean(), 1.0, 250.0))
    return float(np.clip(math.log(b / 255.0) / math.log(target / 255.0), *bounds))


class Runner:
    def __init__(self, model_path, half=True):
        from ultralytics import YOLO
        self.m = YOLO(model_path)
        self.half = half
        self.calls = {}

    def predict(self, img, imgsz, conf, tag):
        t0 = time.perf_counter()
        r = self.m.predict(img, imgsz=int(imgsz), conf=float(conf), iou=0.45, half=self.half,
                           verbose=False, max_det=30)[0]
        dt = (time.perf_counter() - t0) * 1000
        n, s = self.calls.get(tag, (0, 0.0))
        self.calls[tag] = (n + 1, s + dt)
        out = []
        if r.boxes is None or len(r.boxes) == 0:
            return out
        xyxy = r.boxes.xyxy.cpu().numpy()
        bc = r.boxes.conf.cpu().numpy()
        kc = r.keypoints.conf.cpu().numpy() if r.keypoints is not None and r.keypoints.conf is not None else None
        for i in range(len(xyxy)):
            x1, y1, x2, y2 = xyxy[i]
            out.append({"cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2, "h": y2 - y1, "conf": float(bc[i]),
                        "kpt": float(kc[i].mean()) if kc is not None else 0.0})
        return out


def match(dets, cx, cy, h):
    best = None
    for d in dets:
        if abs(d["cx"] - cx) <= 0.5 * h and abs(d["cy"] - cy) <= 0.5 * h and 0.5 <= d["h"] / max(h, 1) <= 2.0:
            if best is None or d["conf"] > best["conf"]:
                best = d
    return best


def full_pass(run, frame, roi, size, conf, gamma, clahe, tag):
    x, y, w, h = roi
    crop = enhance(frame[y:y + h, x:x + w], gamma, clahe)
    dets = run.predict(crop, size, conf, tag)
    for d in dets:
        d["cx"] += x
        d["cy"] += y
    return dets


def zoom_pass(run, frame, roi, cx, cy, h, k, size, conf, gamma, clahe, local, tag):
    side = max(96, int(k * h))
    rx, ry, rw, rh = roi
    x0 = int(np.clip(cx - side / 2, rx, max(rx, rx + rw - side)))
    y0 = int(np.clip(cy - side / 2, ry, max(ry, ry + rh - side)))
    x1, y1 = min(rx + rw, x0 + side), min(ry + rh, y0 + side)
    crop = frame[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    g = local_gamma(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)) if local else gamma
    dets = run.predict(enhance(crop, g, clahe), size, conf, tag)
    for d in dets:
        d["cx"] += x0
        d["cy"] += y0
    return dets


def truth_from_timeline(rows, max_gap=60):
    """{frame: [(slot_id, kind, cx, cy, h)]}.  ``live``: the slot's track got a YOLO skeleton this frame
    (fss 0) -- centre = the raw KF centroid; ``gap``: the slot is emitted but YOLO did not see it (fss > 0 or
    a hold state) -- centre / height interpolated between the seen frames around it (<= max_gap frames)."""
    seen, unseen = {}, {}
    for r in rows:
        f = r["abs_frame"]
        for e in (r.get("emitted") or {}).get("tracks") or []:
            sid, b = e["id"], e["bbox"]
            c = e.get("raw") or [b[0] + b[2] / 2, b[1] + b[3] / 2]
            if e.get("fss") == 0 and e.get("state") in LIVE:
                seen.setdefault(sid, {})[f] = (float(c[0]), float(c[1]), float(b[3]))
            else:
                unseen.setdefault(sid, set()).add(f)
    out = {}
    for sid, lv in seen.items():
        fs = sorted(lv)
        for f in fs:
            out.setdefault(f, []).append((sid, "live") + lv[f])
        for a, b in zip(fs, fs[1:]):
            if 1 < b - a <= max_gap:
                for g in range(a + 1, b):
                    if g in unseen.get(sid, ()):
                        t = (g - a) / (b - a)
                        pa, pb = lv[a], lv[b]
                        out.setdefault(g, []).append((sid, "gap") + tuple(pa[i] + t * (pb[i] - pa[i]) for i in range(3)))
    return out


def decoy(cx, cy, h, people, roi):
    rx, ry, rw, rh = roi
    for dx in (rw / 2, -rw / 2, rw / 3, -rw / 3, rw / 4, -rw / 4):
        x = cx + dx
        if not rx + h <= x <= rx + rw - h:
            continue
        if all(math.hypot(x - p[2], cy - p[3]) >= 2.0 * max(h, p[4]) for p in people):
            return x, cy
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="/data/WallDance/models/yolo11x-pose.pt")
    ap.add_argument("--sizes", default="480,640,800,960,1280")
    ap.add_argument("--zoom-size", type=int, default=640)
    ap.add_argument("--k", type=float, default=3.0)
    ap.add_argument("--live-every", type=int, default=6)
    ap.add_argument("--max-gap", type=int, default=400)
    ap.add_argument("--conf", type=float, default=0.05, help="pass threshold (thresholds applied post hoc)")
    a = ap.parse_args()

    man = json.load(open(APP / "tests" / "scenarios" / f"{a.scenario}.json"))
    cfg = R.scenario_config(man)
    if "profiles" in cfg:
        import core.config_schema as config_schema
        cfg = config_schema.flatten(cfg)
    fp = (man.get("recording_fingerprint") or {}).get("file")
    video = R.PROJECTS_DIR / man["project"] / "recordings" / fp
    gamma, clahe = float(cfg.get("gamma", 1.0)), float(cfg.get("clahe_clip", 2.0))
    cap = cv2.VideoCapture(str(video))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if cfg.get("roi_enabled") and cfg.get("roi_w"):
        roi = (int(cfg["roi_x"]), int(cfg["roi_y"]), int(min(cfg["roi_w"], W - cfg["roi_x"])),
               int(min(cfg["roi_h"], H - cfg["roi_y"])))
    else:
        roi = (0, 0, W, H)
    xf = R.input_transform_for(cfg)
    rows = sorted(json.load(open(a.timeline)), key=lambda r: r["abs_frame"])
    truth = truth_from_timeline(rows)
    live_fs = sorted(f for f, ps in truth.items() if any(p[1] == "live" for p in ps))[::a.live_every]
    gap_fs = sorted(f for f, ps in truth.items() if any(p[1] == "gap" for p in ps))
    if len(gap_fs) > a.max_gap:
        gap_fs = [gap_fs[int(i * len(gap_fs) / a.max_gap)] for i in range(a.max_gap)]
    todo = sorted(set(live_fs) | set(gap_fs))
    sizes = [int(s) for s in a.sizes.split(",")]
    run = Runner(a.model)
    res = {"scenario": a.scenario, "roi": roi, "gamma": gamma, "clahe": clahe, "frame_size": [W, H],
           "sizes": sizes, "zoom_size": a.zoom_size, "k": a.k, "live": [], "gap": []}
    t_start = time.time()
    for f in todo:
        cap.set(cv2.CAP_PROP_POS_FRAMES, f)
        ok, frame = cap.read()
        if not ok:
            continue
        frame = xf.apply(frame)
        people = truth[f]
        is_live = f in set(live_fs)
        full = {s: full_pass(run, frame, roi, s, a.conf, gamma, clahe, f"full{s}")
                for s in (sizes if is_live else [1280])}
        for p in people:
            sid, kind, cx, cy, h = p
            if kind == "live" and not is_live:
                continue
            if kind == "gap" and f not in set(gap_fs):
                continue
            row = {"f": f, "sid": sid, "h": round(h, 1), "net": {s: round(h * s / max(roi[2], roi[3]), 1) for s in sizes}}
            for s, dets in full.items():
                m = match(dets, cx, cy, h)
                row[f"full{s}"] = [round(m["conf"], 3), round(m["kpt"], 3)] if m else None
            for loc in (False, True):
                tag = "zoom_l" if loc else "zoom_g"
                m = match(zoom_pass(run, frame, roi, cx, cy, h, a.k, a.zoom_size, a.conf, gamma, clahe, loc, tag),
                          cx, cy, h)
                row[tag] = [round(m["conf"], 3), round(m["kpt"], 3)] if m else None
            if kind == "gap":
                dc = decoy(cx, cy, h, people, roi)
                if dc is not None:
                    for loc in (False, True):
                        tag = "decoy_l" if loc else "decoy_g"
                        dd = zoom_pass(run, frame, roi, dc[0], dc[1], h, a.k, a.zoom_size, a.conf, gamma, clahe, loc, tag)
                        m = match(dd, dc[0], dc[1], h)
                        row[tag] = [round(m["conf"], 3), round(m["kpt"], 3)] if m else None
                for gname, gm in (("g_lo", 1 / 1.6), ("g_hi", 1.6)):
                    dets = full_pass(run, frame, roi, 1280, a.conf, gamma * gm, clahe, f"full1280_{gname}")
                    m = match(dets, cx, cy, h)
                    row[f"full1280_{gname}"] = [round(m["conf"], 3), round(m["kpt"], 3)] if m else None
            res["live" if kind == "live" else "gap"].append(row)
    res["ms_per_call"] = {k: round(v[1] / max(v[0], 1), 1) for k, v in run.calls.items()}
    res["wall_s"] = round(time.time() - t_start)
    Path(a.out).write_text(json.dumps(res))
    print(f"{a.scenario}: live {len(res['live'])} gap {len(res['gap'])} in {res['wall_s']} s; ms/call {res['ms_per_call']}")


if __name__ == "__main__":
    main()
