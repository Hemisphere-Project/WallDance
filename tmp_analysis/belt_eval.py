#!/usr/bin/env python3
"""IR-belt analysis + IR budget for WallDance takes (remote-runnable over 4G).

Runs ``core/belt_detector.py`` over slot recordings and answers two questions:

1. **Does the belt work?** Per take: belt detection rate over time, belt DN
   (peak, mean) vs apparent band width and image position, body and wall DN
   next to the belt, false-positive candidates per frame after the static map,
   rejections by reason, detector cost, and (with YOLO poses) the share of
   dancer-frames whose belt is found at the hips by the gated mode, including
   the frames where YOLO had lost the dancer.
2. **Is the IR light enough at 30-40 m?** (``ir_budget``) The belt and body DN
   are normalised by exposure x gain (camlog per frame, else ``.meta`` v2, else
   CLI overrides), extrapolated with 1/d^2 to the target distances and to a
   20-25 ms exposure at <= 24 dB, and turned into the light multiplier (and
   projector count) that keeps the belt >= 120 DN and the body at a level YOLO
   still uses. An exposure ladder take (camlog plateaus) gives the DN-vs-exposure
   slope directly and checks linearity and saturation.

Runs three ways (the repo root comes from the cwd, like marker_eval.py):
  local   cd application && .venv/bin/python ../tmp_analysis/belt_eval.py --project P --slot 2
  remote  python extra/wdremote.py --slot dev py \\
              --with application/src/core/belt_detector.py --with tmp_analysis/marker_eval.py \\
              --with tmp_analysis/marker_evallib.py --with application/src/core/marker_model.py \\
              tmp_analysis/belt_eval.py -- --project P --slot 2 --empty-slot 1
          (--with BEFORE the script; output -> $WD_REMOTE_OUT, fetched to
          tmp_analysis/remote-runs/<stamp>/out)
  v1 takes (no camera state in the .meta):
          ... --exposure-us 49327 --gain-db 27.8 --distance-m 25

Outputs (small, for a 4G link): summary.json plus at most three JPEGs:
belt_sheet.jpg (detections / rejections / gated misses), belt_dn.jpg (DN vs
band width and image position), ir_budget.jpg (the budget table, ladder fit).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

TOOL_VERSION = "1.0"
REMOTE_WITH = ("application/src/core/belt_detector.py", "tmp_analysis/marker_eval.py",
               "tmp_analysis/marker_evallib.py", "application/src/core/marker_model.py")
SAT = 250                         # DN: a sample with any pixel >= this is saturated
WIDTH_BINS = (0, 10, 20, 40, 80, 10 ** 6)
REF_E_MS, REF_G_DB = 1.0, 0.0     # normalisation reference: DN per ms of exposure at 0 dB

cv2 = bd = ev = me = None         # set by import_helpers()


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------

def find_repo_root(explicit: Optional[str] = None) -> Path:
    cands = [Path(explicit)] if explicit else []
    if os.environ.get("WD_REPO_ROOT"):
        cands.append(Path(os.environ["WD_REPO_ROOT"]))
    cwd = Path.cwd().resolve()
    here = Path(__file__).resolve().parent
    cands += [cwd, *cwd.parents, here, *here.parents]
    for c in cands:
        if (c / "application" / "src" / "core").is_dir():
            return c.resolve()
    raise SystemExit(f"belt_eval: no application/src/core above {cwd}; pass --repo")


def import_helpers(root: Path) -> None:
    """Uploaded copies next to this script win (wdremote py --with), else the
    checkout's application/src/core and tmp_analysis/."""
    global cv2, bd, ev, me
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    for extra in (root / "application" / "src", root / "tmp_analysis"):
        if str(extra) not in sys.path:
            sys.path.append(str(extra))
    try:
        import cv2 as _cv2
        try:
            import belt_detector as _bd           # uploaded next to us
        except ImportError:
            from core import belt_detector as _bd
        import marker_eval as _me
        _me.import_helpers(root)
        _ev = _me.ev
    except ImportError as e:
        withs = " ".join(f"--with {w}" for w in REMOTE_WITH)
        raise SystemExit(f"belt_eval: helper import failed ({e}). Remote runs need (--with BEFORE the script):\n"
                         f"  python extra/wdremote.py --slot dev py {withs} tmp_analysis/belt_eval.py -- <args>")
    cv2, bd, ev, me = _cv2, _bd, _ev, _me


def rnd(v, nd=2):
    return ev.rnd(v, nd)


def pct(vals, q, nd=2):
    v = [float(x) for x in vals if x is not None and math.isfinite(float(x))]
    return round(float(np.percentile(v, q)), nd) if v else None


def p3(vals, nd=1):
    """[p10, p50, p90]"""
    return [pct(vals, q, nd) for q in (10, 50, 90)]


def parse_slots(spec: Optional[str]) -> List[int]:
    if spec is None:
        return []
    out: List[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def parse_floats(spec: str) -> List[float]:
    return [float(x) for x in str(spec).split(",") if x.strip()]


# ---------------------------------------------------------------------------
# exposure / gain per frame
# ---------------------------------------------------------------------------

class CamTimeline:
    """frame -> (exposure_us, gain_db, source). Precedence: CLI override >
    camlog step function > .meta camera > unknown."""

    def __init__(self, meta: dict, a):
        cam = meta.get("camera") or {}
        ser = sorted((int(f), e, g) for f, e, g in (meta.get("_camlog_series") or []))
        self.series = [(f, float(e) if isinstance(e, (int, float)) else None,
                        float(g) if isinstance(g, (int, float)) else None) for f, e, g in ser]
        self.fs = np.array([s[0] for s in self.series]) if self.series else None
        self.e_meta = cam.get("exposure_us")
        self.g_meta = cam.get("gain_db")
        self.e_cli, self.g_cli = a.exposure_us, a.gain_db
        self.gamma = cam.get("gamma")
        self.black = cam.get("black_level")
        if self.e_cli is not None:
            self.e_src = "cli"
        elif self.series and any(s[1] is not None for s in self.series):
            self.e_src = "camlog"
        elif self.e_meta is not None:
            self.e_src = ".meta camera"
        else:
            self.e_src = "unknown"
        if self.g_cli is not None:
            self.g_src = "cli"
        elif self.series and any(s[2] is not None for s in self.series):
            self.g_src = "camlog"
        elif self.g_meta is not None:
            self.g_src = ".meta camera"
        else:
            self.g_src = "unknown"

    def _ser(self, f: int, k: int):
        i = int(np.searchsorted(self.fs, f, side="right")) - 1
        for j in range(max(0, i), -1, -1):          # last known value at or before f
            if self.series[j][k] is not None:
                return self.series[j][k]
        for j in range(max(0, i), len(self.series)):
            if self.series[j][k] is not None:
                return self.series[j][k]
        return None

    def at(self, f: int) -> Tuple[Optional[float], Optional[float]]:
        e = self.e_cli if self.e_src == "cli" else (self._ser(f, 1) if self.e_src == "camlog" else self.e_meta)
        g = self.g_cli if self.g_src == "cli" else (self._ser(f, 2) if self.g_src == "camlog" else self.g_meta)
        return (float(e) if e is not None else None, float(g) if g is not None else None)

    def plateaus(self, min_samples: int = 3, tol: float = 0.03) -> List[dict]:
        """Exposure ladder: runs of camlog samples with a constant exposure."""
        if not self.series or self.e_src != "camlog":
            return []
        runs, cur = [], None
        for f, e, g in self.series:
            if e is None:
                continue
            if cur is not None and abs(e - cur["e"]) <= tol * cur["e"] and (
                    g is None or cur["g"] is None or abs(g - cur["g"]) < 0.3):
                cur["n"] += 1
                cur["f1"] = f
            else:
                cur = {"e": e, "g": g, "f0": f, "f1": f, "n": 1}
                runs.append(cur)
        runs = [r for r in runs if r["n"] >= min_samples]
        if len({round(r["e"]) for r in runs}) < 2:
            return []
        return runs


# ---------------------------------------------------------------------------
# small image measurements
# ---------------------------------------------------------------------------

def box_median(g: np.ndarray, x0, y0, x1, y1) -> Optional[float]:
    H, W = g.shape[:2]
    x0, y0 = int(max(0, round(x0))), int(max(0, round(y0)))
    x1, y1 = int(min(W, round(x1))), int(min(H, round(y1)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return float(np.median(g[y0:y1, x0:x1]))


def belt_context(g: np.ndarray, b) -> Tuple[Optional[float], Optional[float]]:
    """(body DN just above the band, wall DN beside it) for a belt blob."""
    w, h = max(b.w, 4.0), max(b.h, 2.0)
    body = box_median(g, b.cx - 0.35 * w, b.cy - h / 2 - 2 - 0.6 * w, b.cx + 0.35 * w, b.cy - h / 2 - 2)
    side = [box_median(g, b.cx + sgn * (0.5 * w + 0.6 * w) - 0.3 * w, b.cy - h,
                       b.cx + sgn * (0.5 * w + 0.6 * w) + 0.3 * w, b.cy + h) for sgn in (-1, 1)]
    side = [s for s in side if s is not None]
    return body, (float(np.median(side)) if side else None)


KP_CONF = 0.3


def track_geometry(t: dict) -> Optional[dict]:
    """Waist prediction + torso / side boxes from a track (YOLO keypoints, or
    the tracker's carried keypoints on coasting frames)."""
    k, c, bb = t["kpts"], t["conf"], t["bbox"]
    H = float(bb[3])
    if H < 20:
        return None
    hips = [i for i in (11, 12) if c[i] > KP_CONF]
    sh = [i for i in (5, 6) if c[i] > KP_CONF]
    if hips:
        hx, hy = float(np.mean(k[hips, 0])), float(np.mean(k[hips, 1]))
        src = "hips"
    else:                                         # coasting / hips hidden: bbox proportions
        hx, hy = float(bb[0] + bb[2] / 2), float(bb[1] + 0.53 * H)
        src = "bbox"
    hipw = abs(float(k[11, 0] - k[12, 0])) if len(hips) == 2 else 0.0
    bw = float(np.clip(1.6 * hipw, 0.10 * H, 0.30 * H)) if hipw > 0 else 0.18 * H
    wy = hy - 0.04 * H                            # belt sits a little above the hip joints
    if sh and hips:
        sy = float(np.mean(k[sh, 1]))
        xs = k[sh + hips, 0]
        tx0, tx1 = float(xs.min()), float(xs.max())
        span = max(tx1 - tx0, 0.08 * H)
        torso = (tx0 + 0.2 * span, sy + 0.2 * (hy - sy), tx0 + 0.8 * span if tx1 > tx0 else tx0 + 0.8 * span,
                 hy - 0.25 * (hy - sy))
    else:
        cx = float(bb[0] + bb[2] / 2)
        torso = (cx - 0.07 * H, bb[1] + 0.25 * H, cx + 0.07 * H, bb[1] + 0.42 * H)
    sides = ((bb[0] - 0.35 * H, wy - 0.1 * H, bb[0] - 0.05 * H, wy + 0.1 * H),
             (bb[0] + bb[2] + 0.05 * H, wy - 0.1 * H, bb[0] + bb[2] + 0.35 * H, wy + 0.1 * H))
    mean_conf = float(np.mean(c[[5, 6, 11, 12, 13, 14]]))
    return {"x": hx, "y": wy, "gate": max(10.0, 0.15 * H), "bw": bw, "H": H, "src": src,
            "torso": torso, "sides": sides, "mean_conf": mean_conf}


# ---------------------------------------------------------------------------
# pose sources
# ---------------------------------------------------------------------------

def poses_for_take(a, root: Path, video: Path, cfg: dict):
    """-> (DumpPoses | PipelinePoses | None, label, auto-save path)."""
    spec = a.poses
    if spec in ("auto", "pipeline") and a.poses_dir:
        pd = Path(a.poses_dir)
        exact = pd / f"poses_{video.stem}_s{a.start}_n{a.frames}.pkl"
        cands = [exact] if exact.exists() else sorted(pd.glob(f"poses_{video.stem}_*.pkl"))
        if cands:
            spec = f"dump:{cands[0]}"
        elif spec == "auto":
            spec = "none"
    elif spec == "auto":
        spec = "none"
    if spec == "none":
        return None, "none", None
    if spec.startswith("dump:"):
        rows: List[dict] = []
        for p in spec[5:].split(","):
            with open(p, "rb") as fh:
                import pickle
                d = pickle.load(fh)
            if isinstance(d, dict) and d.get("video") and Path(str(d["video"])).stem != video.stem:
                continue                      # a dump of another take (multi-slot runs)
            rows += d["rows"] if isinstance(d, dict) else list(d)
        if not rows:
            return None, f"none (no dump rows for {video.name})", None
        return me.DumpPoses(rows, spec), f"dump {spec[5:]}", None
    if spec == "pipeline":
        model = (a.model or cfg.get("model") or "yolo11x-pose").replace(".pt", "")
        imgsz = int(a.imgsz or cfg.get("yolo_imgsz") or 1280)
        save = (Path(a.poses_dir) / f"poses_{video.stem}_s{a.start}_n{a.frames}.pkl") if a.poses_dir else None
        pp = me.PipelinePoses(root, cfg, model, imgsz, a.trt)
        return pp, pp.label, save
    raise SystemExit(f"--poses {spec!r}: auto | none | pipeline | dump:PATH[,PATH]")


# ---------------------------------------------------------------------------
# one take
# ---------------------------------------------------------------------------

def make_detector(a, static=None):
    p = bd.BeltParams()
    for k, v in (("floor_dn", a.floor), ("k_bg", a.k_bg), ("min_delta_dn", a.min_delta),
                 ("min_score", a.min_score), ("max_candidates", a.max_candidates)):
        if v is not None:
            setattr(p, k, type(getattr(p, k))(v))
    return bd.BeltDetector(p, static)


def build_static(a, root: Path, roi) -> Tuple[Optional[object], dict]:
    """Static map from the empty-stage slot of the same project (first
    ``--empty-frames`` frames, stride 2)."""
    if a.empty_slot is None:
        return None, {"source": None}
    takes = me.list_takes(root, a.project, a.empty_slot)
    if not takes:
        return None, {"source": f"slot {a.empty_slot}: no take", "cells": 0}
    v = takes[-1]
    det = make_detector(a)
    sm, n, cands = None, 0, 0
    for f, gfull, _g, _off, _t, shape in me.iter_video(v, 0, a.empty_frames, 2, None):
        if sm is None:
            sm = bd.StaticMap(shape, 8)
        blobs = [b for b in det.detect(gfull, roi, return_all=True) if b.reason != "small"]
        cands += len(blobs)
        sm.add(blobs)
        n += 1
    if sm is None:
        return None, {"source": f"{v.name}: unreadable", "cells": 0}
    sm.finalize(min_frac=0.1)
    return sm, {"source": v.name, "frames": n, "cells": sm.n_cells, "candidates_per_frame": rnd(cands / max(1, n), 3)}


class Tiles:
    """Contact-sheet tiles across the run (one sheet for all takes). Gated
    misses are pooled per track until the take ends, so the tracks set aside
    as static figures do not fill the sheet."""

    def __init__(self, cap: int):
        self.pools = {k: ev.TilePool(cap, "sample", seed=i) for i, k in
                      enumerate(("belt", "rejected", "miss"))}
        self.cap = cap
        self.pending: Dict[object, object] = {}

    def offer(self, kind: str, g, x, y, label_top: str, label_bot: str, marks=(), f=0, half=40, track=None):
        if self.cap <= 0:
            return

        def make():
            img, org = ev.crop(g, x, y, half)
            return {"img": img, "origin": org, "marks": list(marks), "top": label_top, "bottom": label_bot, "f": f}
        if track is not None:
            pool = self.pending.setdefault(track, ev.TilePool(self.cap, "sample", seed=len(self.pending)))
            pool.offer(0.0, make)
        else:
            self.pools[kind].offer(0.0, make)

    def commit_misses(self, exclude=()) -> None:
        for tid, pool in self.pending.items():
            if tid in exclude:
                continue
            for t in pool.tiles():
                self.pools["miss"].offer(0.0, lambda t=t: t)
        self.pending = {}


def analyze_take(a, root: Path, video: Path, tiles: Tiles, static_empty, static_info: dict,
                 role: str = "") -> dict:
    t_start = time.time()
    rig = me.parse_kv(a.rig)
    meta = ev.read_take_meta(video, rig)
    cfg, cfg_src = me.resolve_config(a, root, meta, None)
    cap = cv2.VideoCapture(str(video))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    n_total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    roi = me.resolve_roi(a.roi, cfg, W, H)
    fps = float(meta.get("actual_fps") or 20.0)
    cam = CamTimeline(meta, a)
    dist = a.distance_m if a.distance_m is not None else (meta.get("rig") or {}).get("camera_distance_m")
    ladder = cam.plateaus()
    poses, pose_label, pose_save = poses_for_take(a, root, video, cfg)
    det = make_detector(a, static_empty)

    frames = 0
    per_frame = []                     # (f, n_accepted, n_candidates)
    belts = []                         # accepted global blobs (dicts)
    rej = Counter()
    cost_g, cost_n, cost_n_pred = [], [], []
    tracks_rec = []                    # gated, per track-frame
    sat_blobs = 0
    start = a.start
    if role == "empty" and static_empty is not None and n_total > 2 * a.empty_frames:
        start = max(start, a.empty_frames)          # FP on frames the static map did not learn from
    for f, gfull, _g, _off, tracks, shape in me.iter_video(video, start, a.frames, a.stride, None, poses,
                                                            progress=a.progress):
        frames += 1
        e_us, g_db = cam.at(f)
        t0 = time.perf_counter()
        allb = det.detect(gfull, roi, return_all=True)
        cost_g.append((time.perf_counter() - t0) * 1000)
        for k, v in det.last.get("rejected", {}).items():
            rej[k] += v
        acc = [b for b in allb if b.reason is None]
        per_frame.append((f, len(acc), det.last.get("candidates", 0)))
        geo = []
        for t in tracks or []:
            gm = track_geometry(t)
            if gm is not None:
                geo.append((t, gm))
        for b in acc:
            body, wall = belt_context(gfull, b)
            on, at_hips = None, None
            if geo:
                on = any(t["bbox"][0] - 0.1 * gm["H"] <= b.cx <= t["bbox"][0] + t["bbox"][2] + 0.1 * gm["H"]
                         and t["bbox"][1] - 0.1 * gm["H"] <= b.cy <= t["bbox"][1] + 1.1 * gm["H"] for t, gm in geo)
                at_hips = any(math.hypot(b.cx - gm["x"], b.cy - gm["y"]) <= gm["gate"] for _t, gm in geo)
            rec = {"f": f, "cx": b.cx, "cy": b.cy, "w": b.w, "h": b.h, "ang": b.angle, "peak": b.peak,
                   "mean": b.mean, "bg": b.bg, "con": b.contrast, "sat": b.sat_frac, "score": b.score,
                   "pieces": b.pieces, "body": body, "wall": wall, "e": e_us, "g": g_db,
                   "on": on, "hips": at_hips}
            belts.append(rec)
            sat_blobs += b.peak >= SAT
            tiles.offer("belt", gfull, b.cx, b.cy, f"{video.stem[:6]} f{f}",
                        f"p{b.peak} w{b.w:.0f} s{b.score:.2f}", [(b.cx, b.cy, "blob")], f)
        for b in allb:
            if b.reason is not None and b.reason not in ("small",):
                tiles.offer("rejected", gfull, b.cx, b.cy, f"{b.reason}"[:12], f"f{f} p{b.peak}",
                            [(b.cx, b.cy, "blob")], f)
        if geo:
            preds = [(i, gm["x"], gm["y"], gm["gate"], gm["bw"]) for i, (_t, gm) in enumerate(geo)]
            t0 = time.perf_counter()
            res = det.detect_near(gfull, preds)
            dt = (time.perf_counter() - t0) * 1000
            cost_n.append(dt)
            cost_n_pred.append(dt / len(preds))
            for i, (t, gm) in enumerate(geo):
                b = res.get(i)
                win_pk = None
                x0, y0 = gm["x"] - 0.6 * gm["bw"], gm["y"] - 0.06 * gm["H"]
                x1, y1 = gm["x"] + 0.6 * gm["bw"], gm["y"] + 0.08 * gm["H"]
                xi0, yi0, xi1, yi1 = int(max(0, x0)), int(max(0, y0)), int(min(W, x1)), int(min(H, y1))
                if xi1 > xi0 and yi1 > yi0:
                    win_pk = int(gfull[yi0:yi1, xi0:xi1].max())
                tr = {"f": f, "id": t["id"], "fss": t["fss"], "H": gm["H"], "x": gm["x"], "y": gm["y"],
                      "src": gm["src"], "mean_conf": gm["mean_conf"],
                      "torso": box_median(gfull, *gm["torso"]),
                      "wall": (lambda s: float(np.median(s)) if s else None)(
                          [v for v in (box_median(gfull, *bx) for bx in gm["sides"]) if v is not None]),
                      "win_peak": win_pk, "e": e_us, "g": g_db, "hit": b is not None}
                if b is not None:
                    tr.update(peak=b.peak, mean=b.mean, w=b.w, h=b.h, sat=b.sat_frac, score=b.score,
                              dist=b.dist, bx=b.cx, by=b.cy)
                elif t["fss"] == 0 and gm["src"] == "hips" and gm["mean_conf"] >= 0.5:
                    tiles.offer("miss", gfull, gm["x"], gm["y"], f"miss id{t['id']}", f"f{f} pk{win_pk}",
                                [(gm["x"], gm["y"], "kp"), (gm["x"], gm["y"], "gate", gm["gate"])], f,
                                track=(video.stem, t["id"]))
                tracks_rec.append(tr)
    # static figures (a poster, a mannequin, a seated person) are not dancers: a
    # track whose centre and feet never move by half its height is set aside,
    # except on a ladder take (the dancer holds still on purpose)
    static_ids = []
    if tracks_rec and not ladder and not a.keep_static_tracks:
        by = defaultdict(list)
        for r in tracks_rec:
            by[r["id"]].append(r)
        for tid, rr in by.items():
            Hm = float(np.median([r["H"] for r in rr]))
            if len(rr) >= 20 and np.ptp([r["x"] for r in rr]) < 0.5 * Hm and np.ptp([r["y"] for r in rr]) < 0.5 * Hm:
                static_ids.append(tid)
        tracks_rec = [r for r in tracks_rec if r["id"] not in static_ids]
    tiles.commit_misses({(video.stem, i) for i in static_ids})
    if poses is not None:
        poses.close()
        if pose_save is not None and getattr(poses, "rows", None):
            me.save_poses(str(pose_save), poses.rows, video, a.start)

    # ---- post-hoc persistence: cells hit in >= 40 % of frames (not on the ladder take)
    persist_cells = set()
    use_persist = a.static in ("auto", "persist", "both") and not ladder and frames >= 50
    if use_persist and belts:
        cnt = Counter()
        for r in belts:
            cnt[(int(r["cx"]) // 8, int(r["cy"]) // 8)] += 1
        persist_cells = {c for c, n in cnt.items() if n >= 0.4 * frames}
        grown = set()
        for (cx, cy) in persist_cells:
            grown.update((cx + dx, cy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
        persist_cells = grown
    for r in belts:
        r["static"] = (int(r["cx"]) // 8, int(r["cy"]) // 8) in persist_cells
    live = [r for r in belts if not r["static"]]

    # ---- per-frame series (after the static map)
    acc_by_f = Counter(r["f"] for r in live)
    counts = [acc_by_f.get(f, 0) for f, _n, _c in per_frame]
    cand = [c for _f, _n, c in per_frame]
    bin_n = max(1, int(round(fps / max(1, a.stride))))
    rate_1s = [round(float(np.mean([c > 0 for c in counts[i:i + bin_n]])), 2) for i in range(0, len(counts), bin_n)]
    gaps, run = [], 0
    for c in counts:
        if c == 0:
            run += 1
        else:
            if run:
                gaps.append(run)
            run = 0
    if run:
        gaps.append(run)
    hist = Counter(min(c, 3) for c in counts)

    summ: dict = {
        "take": {"file": video.name, "role": role, "frames_total": n_total, "frames_analysed": frames,
                 "start": start, "stride": a.stride, "fps": fps, "shape": [H, W], "roi": roi,
                 "config_source": cfg_src, "poses": pose_label},
        "provenance": ev.public_meta(meta),
        "camera": {"exposure_source": cam.e_src, "gain_source": cam.g_src,
                   "exposure_us": p3([cam.at(f)[0] for f, _n, _c in per_frame if cam.at(f)[0] is not None], 0),
                   "gain_db": p3([cam.at(f)[1] for f, _n, _c in per_frame if cam.at(f)[1] is not None], 2),
                   "gamma": cam.gamma, "black_level": cam.black, "distance_m": dist,
                   "distance_source": "cli" if a.distance_m is not None else ("rig" if dist else "unknown")},
        "detector": {"params": {k: getattr(det.p, k) for k in ("floor_dn", "k_bg", "min_delta_dn", "min_score",
                                                                 "max_candidates")},
                     "static_empty": static_info, "static_persist_cells": len(persist_cells),
                     "cost_ms": {"global_p50": pct(cost_g, 50, 3), "global_p95": pct(cost_g, 95, 3),
                                 "global_max": rnd(max(cost_g) if cost_g else None, 3),
                                 "gated_frame_p50": pct(cost_n, 50, 3), "gated_frame_p95": pct(cost_n, 95, 3),
                                 "gated_per_pred_p50": pct(cost_n_pred, 50, 3),
                                 "cv2_threads": cv2.getNumThreads()}},
        "detection": {
            "frames_with_belt_pct": rnd(100.0 * np.mean([c > 0 for c in counts]) if counts else None, 1),
            "belts_per_frame": rnd(np.mean(counts) if counts else None, 3),
            "count_hist": {str(k) if k < 3 else "3+": int(v) for k, v in sorted(hist.items())},
            "rate_1s": rate_1s,
            "longest_gap_s": rnd(max(gaps) * a.stride / fps if gaps else 0.0, 2),
            "candidates_per_frame": rnd(np.mean(cand) if cand else None, 3),
            "rejected_per_frame": {k: rnd(v / max(1, frames), 3) for k, v in sorted(rej.items())},
            "static_post_hoc": sum(r["static"] for r in belts),
        },
        "belt": belt_stats(live, W, H),
    }
    if a.dancers:
        summ["detection"]["dancer_visible_pct_belt_only"] = rnd(
            100.0 * np.mean([min(c, a.dancers) / a.dancers for c in counts]) if counts else None, 1)
    if tracks_rec or static_ids:
        summ["poses"] = pose_stats(tracks_rec, live, frames) if tracks_rec else {}
        summ["poses"]["static_tracks_excluded"] = static_ids
        summ["detection"]["fp_off_dancer_per_frame"] = rnd(
            sum(1 for r in live if r["on"] is False) / max(1, frames), 3)
        summ["detection"]["on_dancer_not_hips_per_frame"] = rnd(
            sum(1 for r in live if r["on"] and not r["hips"]) / max(1, frames), 3)
    if role == "empty":
        summ["detection"]["fp_per_frame_empty_stage"] = summ["detection"]["belts_per_frame"]
    if ladder:
        summ["ladder"] = ladder_stats(ladder, live, tracks_rec, a)
    summ["_samples"] = {"belts": live, "tracks": tracks_rec, "meta_gamma": cam.gamma}
    summ["timing_s"] = round(time.time() - t_start, 1)
    return summ


def belt_stats(live: List[dict], W: int, H: int) -> dict:
    if not live:
        return {"n": 0}
    out = {"n": len(live),
           "peak_p10_p50_p90": p3([r["peak"] for r in live], 0),
           "mean_p10_p50_p90": p3([r["mean"] for r in live]),
           "width_px_p10_p50_p90": p3([r["w"] for r in live]),
           "height_px_p10_p50_p90": p3([r["h"] for r in live]),
           "contrast_p50": pct([r["con"] for r in live], 50),
           "score_p50": pct([r["score"] for r in live], 50),
           "saturated_pct": rnd(100.0 * np.mean([r["peak"] >= SAT for r in live]), 1),
           "split_pct": rnd(100.0 * np.mean([r["pieces"] > 1 for r in live]), 1),
           "body_dn_p10_p50_p90": p3([r["body"] for r in live if r["body"] is not None]),
           "wall_dn_p10_p50_p90": p3([r["wall"] for r in live if r["wall"] is not None])}
    byw = {}
    for lo, hi in zip(WIDTH_BINS[:-1], WIDTH_BINS[1:]):
        rr = [r for r in live if lo <= r["w"] < hi]
        if rr:
            byw[f"{lo}-{hi if hi < 10 ** 6 else 'inf'}"] = {
                "n": len(rr), "peak_p50": pct([r["peak"] for r in rr], 50, 0),
                "mean_p50": pct([r["mean"] for r in rr], 50, 1),
                "body_p50": pct([r["body"] for r in rr if r["body"] is not None], 50, 1),
                "wall_p50": pct([r["wall"] for r in rr if r["wall"] is not None], 50, 1),
                "saturated_pct": rnd(100.0 * np.mean([r["peak"] >= SAT for r in rr]), 1)}
    out["by_width_px"] = byw
    grid = {}
    for r in live:
        k = f"r{min(2, int(3 * r['cy'] / H))}c{min(2, int(3 * r['cx'] / W))}"
        grid.setdefault(k, []).append(r)
    out["by_image_third"] = {k: {"n": len(v), "peak_p50": pct([r["peak"] for r in v], 50, 0),
                                 "width_p50": pct([r["w"] for r in v], 50, 1)} for k, v in sorted(grid.items())}
    return out


def pose_stats(tr: List[dict], live: List[dict], frames: int) -> dict:
    ids = sorted({r["id"] for r in tr})
    per = {}
    for i in ids:
        rr = [r for r in tr if r["id"] == i]
        sk = [r for r in rr if r["fss"] == 0]
        co = [r for r in rr if r["fss"] > 0]
        per[str(i)] = {"frames": len(rr), "skeleton": len(sk), "coasting": len(co),
                       "hit_pct_skeleton": rnd(100.0 * np.mean([r["hit"] for r in sk]) if sk else None, 1),
                       "hit_pct_coasting": rnd(100.0 * np.mean([r["hit"] for r in co]) if co else None, 1),
                       "H_p50": pct([r["H"] for r in rr], 50, 0)}
    sk = [r for r in tr if r["fss"] == 0]
    co = [r for r in tr if r["fss"] > 0]
    reliable = [r for r in sk if r["mean_conf"] >= 0.5 and r["torso"] is not None]
    byH = {}
    for lo, hi in ((0, 120), (120, 200), (200, 350), (350, 10 ** 6)):
        rr = [r for r in sk if lo <= r["H"] < hi]
        if rr:
            byH[f"{lo}-{hi if hi < 10 ** 6 else 'inf'}"] = {
                "n": len(rr), "hit_pct": rnd(100.0 * np.mean([r["hit"] for r in rr]), 1),
                "win_peak_p50": pct([r["win_peak"] for r in rr if r["win_peak"] is not None], 50, 0),
                "torso_p50": pct([r["torso"] for r in rr if r["torso"] is not None], 50, 1)}
    # YOLO reliability vs body DN: skeleton share of track-frames per torso-DN bin
    rel = {}
    for lo, hi in ((0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 45), (45, 70), (70, 256)):
        rr = [r for r in tr if r["torso"] is not None and lo <= r["torso"] < hi]
        if rr:
            rel[f"{lo}-{hi}"] = {"n": len(rr), "skeleton_pct": rnd(100.0 * np.mean([r["fss"] == 0 for r in rr]), 1)}
    return {"track_frames": len(tr), "skeleton_frames": len(sk), "coasting_frames": len(co),
            "dancer_visible_pct_skeleton": rnd(100.0 * np.mean([r["hit"] for r in sk]) if sk else None, 1),
            "belt_rescue_pct_coasting": rnd(100.0 * np.mean([r["hit"] for r in co]) if co else None, 1),
            "hit_residual_px_p50_p90": [pct([r["dist"] for r in tr if r["hit"]], q, 1) for q in (50, 90)],
            "by_height_px": byH, "per_track": per,
            "torso_dn_reliable_p10_p50_p90": p3([r["torso"] for r in reliable]),
            "wall_dn_p50": pct([r["wall"] for r in tr if r["wall"] is not None], 50, 1),
            "skeleton_pct_by_torso_dn": rel}


def ladder_stats(ladder: List[dict], live: List[dict], tr: List[dict], a) -> dict:
    """Per exposure plateau: belt and body DN; DN-vs-exposure fit on the
    unsaturated plateaus (linearity, intercept ~ black level)."""
    rows = []
    for p in ladder:
        bb = [r for r in live if p["f0"] <= r["f"] <= p["f1"]]
        tt = [r for r in tr if p["f0"] <= r["f"] <= p["f1"]]
        rows.append({"exposure_us": rnd(p["e"], 0), "gain_db": rnd(p["g"], 2), "frames": [p["f0"], p["f1"]],
                     "belt_n": len(bb), "belt_peak_p50": pct([r["peak"] for r in bb], 50, 1),
                     "belt_mean_p50": pct([r["mean"] for r in bb], 50, 1),
                     "belt_sat_pct": rnd(100.0 * np.mean([r["peak"] >= SAT for r in bb]) if bb else None, 1),
                     "body_p50": pct([r["body"] for r in bb if r["body"] is not None]
                                     + [r["torso"] for r in tt if r["torso"] is not None], 50, 1),
                     "gated_win_peak_p50": pct([r["win_peak"] for r in tt if r["win_peak"] is not None], 50, 1)})
    out = {"plateaus": rows}
    for key in ("belt_mean_p50", "belt_peak_p50", "body_p50"):
        pts = [(r["exposure_us"] / 1000.0, r[key]) for r in rows
               if r[key] is not None and (key == "body_p50" or (r["belt_sat_pct"] or 0) < 20)]
        if len(pts) >= 2:
            x = np.array([p[0] for p in pts]); y = np.array([p[1] for p in pts])
            A = np.vstack([x, np.ones_like(x)]).T
            (slope, icpt), *_ = np.linalg.lstsq(A, y, rcond=None)
            yhat = A @ np.array([slope, icpt])
            ss = float(((y - y.mean()) ** 2).sum())
            r2 = 1.0 - float(((y - yhat) ** 2).sum()) / ss if ss > 0 else None
            out[key.replace("_p50", "") + "_fit"] = {"dn_per_ms": rnd(slope, 3), "intercept_dn": rnd(icpt, 1),
                                                     "r2": rnd(r2, 4), "n": len(pts)}
    return out


# ---------------------------------------------------------------------------
# IR budget
# ---------------------------------------------------------------------------

def linearize(dn: float, gamma: Optional[float], black: float) -> float:
    v = max(0.0, float(dn) - black)
    if gamma and abs(float(gamma) - 1.0) > 1e-3:      # camera gamma: out = in^(1/gamma)
        v = 255.0 * (v / 255.0) ** float(gamma)
    return v


def ir_budget(takes: Dict[str, dict], a) -> dict:
    """Pool the unsaturated belt samples (and body samples) of the on-axis
    takes, normalise to DN/ms at 0 dB, bring them to the wall distance, and
    extrapolate to the target distances / exposures at the max gain.

    Samples: with YOLO poses, the gated belt hits of moving tracks (each with
    the dancer's height H -> distance) and their torso DN; belt-only, every
    accepted belt after the static map (band width -> distance)."""
    raw, body, gamma, dist, notes, used = [], [], None, None, [], []
    have_poses = any((s.get("_samples") or {}).get("tracks") for s in takes.values())
    for name, s in takes.items():
        smp = s.get("_samples") or {}
        if s["take"].get("role") == "empty":
            continue
        if have_poses:
            cand = [{"peak": r["peak"], "mean": r["mean"], "w": r["w"], "h": r["h"], "sat": r["sat"],
                     "e": r["e"], "g": r["g"], "size": r["H"], "body": r["torso"]}
                    for r in smp.get("tracks", []) if r["hit"]]
        else:
            cand = [{"peak": r["peak"], "mean": r["mean"], "w": r["w"], "h": r["h"], "sat": r["sat"],
                     "e": r["e"], "g": r["g"], "size": r["w"], "body": r["body"]} for r in smp.get("belts", [])]
        cand = [r for r in cand if r["e"] and r["g"] is not None]
        if len([r for r in cand if r["peak"] < SAT]) < a.min_samples:
            continue
        used.append(name)
        gamma = gamma or smp.get("meta_gamma")
        dist = dist or s["camera"].get("distance_m")
        raw += cand
        for r in smp.get("tracks", []):
            # body samples only where the belt answered: the dancer is lit by the
            # on-axis source, so the torso DN scales with it (a silhouette against a
            # lamp-lit wall also gives YOLO skeletons, at ~0 DN: not a light budget)
            if (r["fss"] == 0 and r["hit"] and r["mean_conf"] >= 0.5 and r["torso"] is not None
                    and r["e"] and r["g"] is not None):
                body.append({"dn": r["torso"], "e": r["e"], "g": r["g"], "size": r["H"], "wall": r["wall"]})
    out: dict = {"takes_used": used, "sample_source": "gated hits at YOLO hips (moving tracks)" if have_poses
                 else "global detections after the static map (belt-only: may include false positives)"}
    if not raw:
        out["status"] = (f"no usable belt samples (a take needs >= --min-samples {a.min_samples} unsaturated "
                         "belt detections with known exposure/gain)")
        return out
    if not dist:
        notes.append("camera distance unknown: pass --distance-m or fill rig.camera_distance_m; 25 m assumed")
        dist = 25.0
    black = float(a.black_dn)
    unsat = [r for r in raw if r["peak"] < SAT and r["sat"] == 0]
    sat_share = 1.0 - len(unsat) / len(raw)

    def norm(dn, e_us, g_db):
        return linearize(dn, gamma, black) / ((e_us / 1000.0) / REF_E_MS * 10 ** ((g_db - REF_G_DB) / 20.0))

    # distance per sample from its apparent size (person height H with poses,
    # band width without): the smallest sizes are taken to be at the wall
    sizes = np.array([r["size"] for r in unsat], np.float64)
    spread = float(np.percentile(sizes, 80) / max(1e-6, np.percentile(sizes, 20))) if len(sizes) >= 10 else 1.0
    size_kind = "person height H" if have_poses else "band width"
    if a.wall_size_px:
        s_wall, s_src = float(a.wall_size_px), "cli --wall-size-px"
    else:
        s_wall = float(np.percentile(sizes, 10 if have_poses else 20))
        s_src = f"p{10 if have_poses else 20} of the samples' {size_kind}"
    model = a.distance_model if a.distance_model != "auto" else ("size" if spread > 1.6 else "wall")
    samples = []
    for r in unsat:
        d_i = dist * s_wall / r["size"] if model == "size" else dist
        if model == "size" and d_i < a.min_distance_frac * dist:
            continue                         # too near the camera to scale back to the wall reliably
        k = (d_i / dist) ** 2                # inverse square: bring the sample to the wall distance
        samples.append({"peak": norm(r["peak"], r["e"], r["g"]) * k, "mean": norm(r["mean"], r["e"], r["g"]) * k,
                        "body": norm(r["body"], r["e"], r["g"]) * k if r["body"] is not None else None,
                        "w": r["w"] * d_i / dist, "h": r["h"] * d_i / dist, "d": d_i})
    bodies = []
    for b_ in body:
        d_i = dist * s_wall / b_["size"] if model == "size" else dist
        if model == "size" and d_i < a.min_distance_frac * dist:
            continue
        bodies.append({"S": norm(b_["dn"], b_["e"], b_["g"]) * (d_i / dist) ** 2, "dn": b_["dn"], "d": d_i,
                       "wall": b_["wall"]})
    if model == "size":
        notes.append(f"sizes vary (p80/p20 = {spread:.1f}): distance from the {size_kind} ({s_src} = {s_wall:.0f} px "
                     f"taken as the wall at {dist:g} m), samples nearer than {a.min_distance_frac * dist:.0f} m "
                     f"dropped, the rest scaled back to the wall by (d/{dist:g})^2. If the far dancers were not "
                     f"at the wall, the multipliers are off by (d_true/{dist:g})^2")
    if len(samples) < a.min_samples:
        out.update(status=f"only {len(samples)} usable samples after the distance model (min {a.min_samples})",
                   notes=notes)
        return out
    S_pk = np.array([s_["peak"] for s_ in samples])
    S_mn = np.array([s_["mean"] for s_ in samples])
    ref = {"peak_med": float(np.median(S_pk)), "peak_p10": float(np.percentile(S_pk, 10)),
           "mean_med": float(np.median(S_mn))}
    if bodies:
        Sb = np.array([b_["S"] for b_ in bodies])
        body_src = "YOLO torso (skeleton frames with the belt lit, mean kpt conf >= 0.5, moving tracks)"
    else:
        Sb = np.array([s_["body"] for s_ in samples if s_["body"] is not None])
        body_src = "torso box above the detected belts"
    body_target, body_target_src = a.body_target_dn, "cli --body-target-dn"
    if body_target is None:
        if len(bodies) >= 50:
            raw_t = float(np.percentile([b_["dn"] for b_ in bodies], 10))
            body_target = max(raw_t, a.body_floor_dn)
            body_target_src = (f"data: p10 of the YOLO torso DN on reliable skeleton frames with the belt lit "
                               f"({raw_t:.0f} DN; YOLO worked at least this dark, conservative)"
                               + (f", floored at {a.body_floor_dn:g} DN (~5 sigma of the 28 dB noise)"
                                  if raw_t < a.body_floor_dn else ""))
        else:
            body_target = 20.0
            body_target_src = "assumption: 20 DN (no usable poses) -- pass --poses-dir / --body-target-dn"
    # exposure ladder (static dancer at the farthest / darkest spot): its slope
    # is a direct, worst-position DN-per-ms measurement at the wall
    ladder_note, S_lad = None, None
    for L in [s["ladder"] for s in takes.values() if s.get("ladder")]:
        fit = L.get("belt_peak_fit") or L.get("belt_mean_fit")
        if fit and fit.get("r2") is not None:
            gains = [p_["gain_db"] for p_ in L["plateaus"] if p_.get("gain_db") is not None]
            g_l = float(np.median(gains)) if gains else None
            ladder_note = (f"ladder: belt DN = {fit['dn_per_ms']} DN/ms x E + {fit['intercept_dn']} "
                           f"(R2 {fit['r2']}, {fit['n']} unsaturated plateaus"
                           + (f", gain {g_l:g} dB" if g_l is not None else "") + ")")
            if g_l is not None and fit["r2"] >= 0.9 and fit["dn_per_ms"] and fit["dn_per_ms"] > 0:
                S_lad = float(fit["dn_per_ms"]) / 10 ** ((g_l - REF_G_DB) / 20.0)
                if gamma and abs(float(gamma) - 1.0) > 1e-3:
                    ladder_note += " (camera gamma != 1: slope not linearised)"
            if fit["r2"] is not None and fit["r2"] < 0.9:
                notes.append(f"ladder fit R2 {fit['r2']} < 0.9: DN not linear in exposure (saturation, AE, "
                             "or the dancer moved) -- check the plateaus in the take summary")
    out.update({"distance_model": model, "wall_distance_m": dist, "size_kind": size_kind,
                "wall_size_px": rnd(s_wall, 1), "size_spread_p80_p20": rnd(spread, 2),
                "samples": len(samples), "samples_distance_m_p10_p50_p90": p3([s_["d"] for s_ in samples]),
                "saturated_share_excluded": rnd(sat_share, 3), "black_dn": black, "camera_gamma": gamma,
                "belt_dn_per_ms_0db_at_wall": {k: rnd(v, 4) for k, v in ref.items()},
                "body_raw_dn_p10_p50_p90": p3([b_["dn"] for b_ in bodies]) if bodies else None,
                "wall_raw_dn_p50_next_to_body": pct([b_["wall"] for b_ in bodies if b_["wall"] is not None], 50, 1)
                if bodies else None,
                "body_dn_per_ms_0db": {"median": rnd(float(np.median(Sb)), 4) if len(Sb) else None,
                                       "p10": rnd(float(np.percentile(Sb, 10)), 4) if len(Sb) else None,
                                       "n": int(len(Sb)), "source": body_src},
                "targets": {"belt_peak_dn": a.belt_target_dn, "body_dn": rnd(body_target, 1),
                            "body_source": body_target_src, "max_gain_db": a.max_gain_db},
                "ladder": ladder_note,
                "belt_dn_per_ms_0db_ladder": rnd(S_lad, 4) if S_lad else None})
    rows = []
    dists = sorted({float(dist)} | set(parse_floats(a.distances)))
    for d in dists:
        for e_ms in parse_floats(a.target_exposure_ms):
            k = e_ms * 10 ** (a.max_gain_db / 20.0) * (dist / d) ** 2
            belt_med, belt_p10 = ref["peak_med"] * k, ref["peak_p10"] * k
            body_med = float(np.median(Sb)) * k if len(Sb) else None
            m_belt = a.belt_target_dn / max(belt_p10, 1e-6)
            m_body = body_target / max(body_med, 1e-6) if body_med else None
            m = max(m_belt, m_body or 0.0)
            lad_dn = S_lad * k if S_lad else None
            rows.append({"distance_m": d, "exposure_ms": e_ms, "gain_db": a.max_gain_db,
                         "belt_peak_dn_ladder": rnd(min(lad_dn, 255.0), 1) if lad_dn else None,
                         "light_x_belt_ladder": rnd(a.belt_target_dn / lad_dn, 3) if lad_dn else None,
                         "belt_peak_dn_median": rnd(min(belt_med, 255.0), 1),
                         "belt_peak_dn_p10": rnd(min(belt_p10, 255.0), 1),
                         "body_dn_median": rnd(body_med, 1),
                         "band_w_px": rnd(float(np.median([s_["w"] for s_ in samples])) * dist / d, 1),
                         "band_h_px": rnd(float(np.median([s_["h"] for s_ in samples])) * dist / d, 1),
                         "light_x_belt": rnd(m_belt, 3), "light_x_body": rnd(m_body, 3),
                         "light_x_needed": rnd(m, 3),
                         "projectors_needed": int(math.ceil(a.projectors * m - 1e-9)) if m > 0 else None})
    out["table"] = rows
    for r in rows:
        if r["band_h_px"] is not None and r["band_h_px"] < 3.0:
            notes.append(f"{r['distance_m']:g} m: band ~{r['band_h_px']} px thick: partly unresolved, the peak "
                         "drops faster than 1/d^2 (blur, focus) -- treat that row as optimistic")
            break
    notes += [
        "illuminance ~ 1/d^2 from a projector at the camera; body and belt DN follow it while resolved; "
        "the retro return also rises a little as the observation angle shrinks with distance (ignored)",
        "DN ~ exposure x 10^(gain/20) until saturation (saturated samples excluded); the ladder take checks it",
        "belt multiplier uses the p10 belt (worst positions/poses), body multiplier the median torso",
        "motion blur at 20-25 ms spreads fast belts and lowers their peak (static samples dominate here)",
        "light multiplier is in units of today's total IR on the dancers (projector count x power x beam)",
    ]
    out["notes"] = notes
    out["status"] = "ok"
    return out


# ---------------------------------------------------------------------------
# images (<= 3 JPEGs)
# ---------------------------------------------------------------------------

def write_sheet(tiles: Tiles, out: Path, q: int) -> Optional[str]:
    parts = []
    for kind, title in (("belt", "accepted belts"), ("rejected", "rejected candidates"),
                        ("miss", "gated misses at YOLO hips")):
        tt = tiles.pools[kind].tiles()
        if tt:
            sh = ev.make_sheet(tt, f"{title} ({tiles.pools[kind].seen} seen, {len(tt)} shown)", cols=8, size=96)
            if sh is not None:
                parts.append(sh)
    if not parts:
        return None
    wmax = max(p.shape[1] for p in parts)
    img = np.vstack([np.pad(p, ((0, 0), (0, wmax - p.shape[1]), (0, 0))) for p in parts])
    ev.write_jpeg(out / "belt_sheet.jpg", img, quality=q, max_width=800)
    return "belt_sheet.jpg"


def write_plots(takes: Dict[str, dict], budget: dict, out: Path, q: int) -> List[str]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:                     # pragma: no cover
        print(f"[belt_eval] no matplotlib ({e}): plots skipped")
        return []
    files = []
    cols = ["#2a6fdb", "#e0612b", "#2aa876", "#a64fd1", "#c9a21a", "#d1406e", "#4a4a4a", "#1aa6b7"]
    fig, ax = plt.subplots(1, 2, figsize=(10, 4.2), dpi=80)
    any_pts = False
    gated = any(any(r["hit"] for r in (s.get("_samples") or {}).get("tracks", [])) for s in takes.values())
    for i, (name, s) in enumerate(takes.items()):
        smp = s.get("_samples") or {}
        if gated:      # with poses: the belts found at the YOLO hips (true belts), body = YOLO torso
            pts = [{"w": r["w"], "peak": r["peak"], "body": r["torso"], "cx": r["bx"], "cy": r["by"]}
                   for r in smp.get("tracks", []) if r["hit"]]
        else:          # belt-only: every accepted detection after the static map (FPs included)
            pts = [{"w": r["w"], "peak": r["peak"], "body": r["body"], "cx": r["cx"], "cy": r["cy"]}
                   for r in smp.get("belts", [])]
        if not pts:
            continue
        any_pts = True
        pts = pts[:: max(1, len(pts) // 1500)]
        c = cols[i % len(cols)]
        w = [r["w"] for r in pts]
        ax[0].scatter(w, [r["peak"] for r in pts], s=6, color=c, alpha=0.5, label=f"{name} belt peak")
        ax[0].scatter(w, [r["body"] if r["body"] is not None else np.nan for r in pts], s=6, color=c,
                      alpha=0.4, marker="x")
        ax[1].scatter([r["cx"] for r in pts], [r["cy"] for r in pts], c=[r["peak"] for r in pts], s=7,
                      cmap="viridis", vmin=0, vmax=255)
    if any_pts:
        ax[0].set_xscale("log")
        ax[0].axhline(SAT, color="#888", lw=0.8, ls="--")
        ax[0].axhline(120, color="#c00", lw=0.8, ls=":")
        ax[0].set_xlabel("band width (px, log)")
        ax[0].set_ylabel("DN (dots: belt peak, x: body)")
        ax[0].set_title("belts found at the YOLO hips (gated)" if gated else
                        "accepted detections (belt-only, FPs included)", fontsize=9)
        ax[0].legend(fontsize=7, loc="lower right")
        H, W = next(iter(takes.values()))["take"]["shape"]
        ax[1].set_xlim(0, W)
        ax[1].set_ylim(H, 0)
        ax[1].set_aspect("equal")
        ax[1].set_title("belt position, colour = peak DN", fontsize=9)
        fig.tight_layout()
        fig.savefig(out / "belt_dn.png")
        img = cv2.imread(str(out / "belt_dn.png"))
        (out / "belt_dn.png").unlink()
        ev.write_jpeg(out / "belt_dn.jpg", img, quality=q, max_width=800)
        files.append("belt_dn.jpg")
    plt.close(fig)
    rows = budget.get("table") or []
    if rows:
        import textwrap
        fig = plt.figure(figsize=(10, 2.6 + 0.25 * len(rows)), dpi=80)
        ax = fig.add_axes([0.01, 0.42, 0.98, 0.55])
        ax.axis("off")
        hdr = ["dist m", "exp ms", "gain dB", "belt pk p10", "belt pk p50", "body p50", "band w x h px",
               "light x belt", "light x body", "light x", "projectors", "ladder x belt"]
        cells = [[f"{r['distance_m']:g}", f"{r['exposure_ms']:g}", f"{r['gain_db']:g}", r["belt_peak_dn_p10"],
                  r["belt_peak_dn_median"], r["body_dn_median"], f"{r['band_w_px']} x {r['band_h_px']}",
                  r["light_x_belt"], r["light_x_body"], r["light_x_needed"], r["projectors_needed"],
                  r.get("light_x_belt_ladder") or "-"] for r in rows]
        tb = ax.table(cellText=cells, colLabels=hdr, loc="upper center", cellLoc="center")
        tb.auto_set_font_size(False)
        tb.set_fontsize(8)
        tb.scale(1, 1.25)
        t = budget.get("targets", {})
        txt = (f"targets: belt peak >= {t.get('belt_peak_dn')} DN, body >= {t.get('body_dn')} DN "
               f"({t.get('body_source') or ''}), gain <= {t.get('max_gain_db')} dB\n"
               f"samples: {budget.get('samples')} ({budget.get('sample_source')}), distance model "
               f"{budget.get('distance_model')} at {budget.get('wall_distance_m')} m; {a_projectors_note(budget)}")
        txt += "\n" + (budget.get("ladder") or "no exposure ladder in these takes")
        txt += "\nlight x = multiplier on today's IR on the dancers (belt: p10 sample, body: median); " \
               "projectors = today's count x light x"
        fig.text(0.01, 0.02, "\n".join(textwrap.fill(line, 150) for line in txt.split("\n")), fontsize=7,
                 va="bottom")
        fig.savefig(out / "ir_budget.png")
        plt.close(fig)
        img = cv2.imread(str(out / "ir_budget.png"))
        (out / "ir_budget.png").unlink()
        ev.write_jpeg(out / "ir_budget.jpg", img, quality=q, max_width=900)
        files.append("ir_budget.jpg")
    return files


def a_projectors_note(budget: dict) -> str:
    return f"takes: {', '.join(budget.get('takes_used', []))}"


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def print_take(name: str, s: dict) -> None:
    t, d, b, c = s["take"], s["detection"], s["belt"], s["camera"]
    cost = s["detector"]["cost_ms"]
    print(f"\n== {name} ({t['file']}, {t['frames_analysed']} frames /{t['stride']}, {s['timing_s']} s){' [' + t['role'] + ']' if t['role'] else ''}")
    print(f"   camera: exposure {c['exposure_us']} us ({c['exposure_source']}), gain {c['gain_db']} dB "
          f"({c['gain_source']}), distance {c['distance_m']} m ({c['distance_source']}); poses: {t['poses']}")
    print(f"   belt in {d['frames_with_belt_pct']} % of frames, {d['belts_per_frame']}/frame, longest gap "
          f"{d['longest_gap_s']} s, counts {d['count_hist']}; candidates/frame {d['candidates_per_frame']}, "
          f"rejected/frame {d['rejected_per_frame']}")
    if b.get("n"):
        print(f"   belt peak p10/50/90 {b['peak_p10_p50_p90']} DN, mean {b['mean_p10_p50_p90']}, width "
              f"{b['width_px_p10_p50_p90']} px, height {b['height_px_p10_p50_p90']} px, saturated "
              f"{b['saturated_pct']} %, body {b['body_dn_p10_p50_p90']}, wall {b['wall_dn_p10_p50_p90']}")
    if s.get("poses"):
        p = s["poses"]
        print(f"   YOLO: {p['skeleton_frames']} skeleton + {p['coasting_frames']} coasting track-frames; belt at "
              f"hips {p['dancer_visible_pct_skeleton']} % (skeleton), rescue {p['belt_rescue_pct_coasting']} % "
              f"(coasting); off-dancer FP/frame {d.get('fp_off_dancer_per_frame')}, on-dancer non-hip/frame "
              f"{d.get('on_dancer_not_hips_per_frame')}; torso DN reliable {p['torso_dn_reliable_p10_p50_p90']}")
    if s.get("ladder"):
        L = s["ladder"]
        print(f"   ladder: {[(r['exposure_us'], r['belt_peak_p50'], r['belt_mean_p50'], r['body_p50']) for r in L['plateaus']]}"
              f" fits {({k: v for k, v in L.items() if k.endswith('_fit')})}")
    print(f"   cost: global p50/p95 {cost['global_p50']}/{cost['global_p95']} ms, gated per prediction p50 "
          f"{cost['gated_per_pred_p50']} ms (cv2 threads {cost['cv2_threads']})")


def print_budget(bu: dict) -> None:
    print("\n== IR budget")
    if bu.get("status") != "ok":
        print(f"   {bu.get('status')}")
        for n in bu.get("notes", []):
            print(f"   - {n}")
        return
    t = bu["targets"]
    print(f"   takes {bu['takes_used']}, {bu['samples']} unsaturated belt samples, model {bu['distance_model']} "
          f"@ {bu['wall_distance_m']} m; belt DN/ms@0dB at the wall {bu['belt_dn_per_ms_0db_at_wall']}; "
          f"body {bu['body_dn_per_ms_0db']['median']} ({bu['body_dn_per_ms_0db']['source']})")
    print(f"   targets: belt >= {t['belt_peak_dn']} DN, body >= {t['body_dn']} DN [{t['body_source']}], "
          f"gain {t['max_gain_db']} dB")
    if bu.get("ladder"):
        print(f"   {bu['ladder']}")
    print("   dist  exp  belt p10/p50  body   band w x h   light x (belt/body) -> projectors")
    for r in bu["table"]:
        print(f"   {r['distance_m']:>4g} {r['exposure_ms']:>4g}  {r['belt_peak_dn_p10']:>6}/{r['belt_peak_dn_median']:<6} "
              f"{r['body_dn_median']!s:>6}  {r['band_w_px']} x {r['band_h_px']:<5} {r['light_x_needed']:>5} "
              f"({r['light_x_belt']}/{r['light_x_body']}) -> {r['projectors_needed']}"
              + (f"   [ladder: belt {r['belt_peak_dn_ladder']} DN -> x{r['light_x_belt_ladder']}]"
                 if r.get("light_x_belt_ladder") else ""))
    for n in bu.get("notes", []):
        print(f"   - {n}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="belt_eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    t = ap.add_argument_group("takes")
    t.add_argument("--project", required=True)
    t.add_argument("--slot", help="N, list 2,3 or range 2-9 (several takes -> one pooled budget)")
    t.add_argument("--take", default="-1", help="index among the slot's takes (-1 = newest) or a video path")
    t.add_argument("--start", type=int, default=0)
    t.add_argument("--frames", type=int, default=0, help="0 = to the end")
    t.add_argument("--stride", type=int, default=1)
    t.add_argument("--roi", default="auto", help="auto (project config) | none | x,y,w,h")
    t.add_argument("--config", default="auto")
    t.add_argument("--rig", action="append", metavar="KEY=VALUE", help="rig-sheet override for old .meta")
    t.add_argument("--repo")
    t.add_argument("--dancers", type=int, help="expected dancers (belt-only visible fraction)")
    t.add_argument("--keep-static-tracks", action="store_true",
                   help="keep tracks that never move (default: set aside as posters / bystanders)")
    s = ap.add_argument_group("static map / detector")
    s.add_argument("--empty-slot", type=int, help="empty-stage slot of the same project -> static map")
    s.add_argument("--empty-frames", type=int, default=300)
    s.add_argument("--static", default="auto", choices=["auto", "empty", "persist", "both", "none"],
                   help="auto = empty-slot map + post-hoc persistence (not on a ladder take)")
    s.add_argument("--floor", type=int)
    s.add_argument("--k-bg", type=float)
    s.add_argument("--min-delta", type=int)
    s.add_argument("--min-score", type=float)
    s.add_argument("--max-candidates", type=int)
    s.add_argument("--threads", type=int, default=4, help="cv2.setNumThreads (the app uses 4)")
    p = ap.add_argument_group("poses")
    p.add_argument("--poses", default="auto", help="auto | none | pipeline | dump:PATH[,PATH]")
    p.add_argument("--poses-dir", help="pose store: reuse poses_<take>_*.pkl, pipeline runs save there")
    p.add_argument("--trt", action="store_true")
    p.add_argument("--model")
    p.add_argument("--imgsz", type=int)
    c = ap.add_argument_group("camera overrides (v1 .meta)")
    c.add_argument("--exposure-us", type=float)
    c.add_argument("--gain-db", type=float)
    c.add_argument("--distance-m", type=float)
    c.add_argument("--black-dn", type=float, default=0.0, help="sensor black level (DN) subtracted")
    b = ap.add_argument_group("IR budget")
    b.add_argument("--belt-target-dn", type=float, default=120.0)
    b.add_argument("--body-target-dn", type=float, help="default: from YOLO poses, else 20 DN (assumption)")
    b.add_argument("--body-floor-dn", type=float, default=8.0, help="lowest body target derived from data")
    b.add_argument("--max-gain-db", type=float, default=24.0)
    b.add_argument("--target-exposure-ms", default="20,25")
    b.add_argument("--distances", default="30,40")
    b.add_argument("--projectors", type=int, default=2, help="projectors in today's takes")
    b.add_argument("--distance-model", default="auto", choices=["auto", "wall", "size"],
                   help="wall: every sample at the wall distance; size: distance from apparent size")
    b.add_argument("--wall-size-px", type=float,
                   help="size at the wall distance (person height H with poses, else band width); default p10/p20")
    b.add_argument("--min-distance-frac", type=float, default=0.6,
                   help="size model: drop samples nearer than this x the wall distance")
    b.add_argument("--min-samples", type=int, default=20)
    o = ap.add_argument_group("output")
    o.add_argument("--out", help="default: $WD_REMOTE_OUT, else tmp_analysis/belt-eval/<stamp>/")
    o.add_argument("--sheet", type=int, default=24, help="tiles per contact-sheet section (0 = none)")
    o.add_argument("--no-plots", action="store_true")
    o.add_argument("--jpeg-q", type=int, default=72)
    o.add_argument("--progress", type=int, default=0)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    root = find_repo_root(a.repo)
    import_helpers(root)
    cv2.setNumThreads(int(a.threads))
    if a.out:
        out = Path(a.out)
    elif os.environ.get("WD_REMOTE_OUT"):
        out = Path(os.environ["WD_REMOTE_OUT"])
    else:
        out = root / "tmp_analysis" / "belt-eval" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    # takes
    videos: List[Tuple[str, Path, str]] = []
    try:
        take_idx = int(a.take)
        take_file = None
    except ValueError:
        take_idx, take_file = -1, a.take
    if take_file:
        v = Path(take_file)
        for cnd in (v, Path.cwd() / v, root / v):
            if cnd.exists():
                videos.append((cnd.stem[:6], cnd.resolve(), ""))
                break
        else:
            raise SystemExit(f"--take {take_file}: not found")
    else:
        slots = parse_slots(a.slot)
        if not slots:
            raise SystemExit("pass --slot N (or a list / range) or --take FILE")
        for sl in slots:
            tk = me.list_takes(root, a.project, sl)
            if not tk:
                print(f"[belt_eval] slot {sl}: no take, skipped")
                continue
            role = "empty" if a.empty_slot is not None and sl == a.empty_slot else ""
            videos.append((f"slot{sl}", tk[take_idx], role))
    if not videos:
        raise SystemExit("no takes to analyse")
    # ROI for the static map: the first take's config
    meta0 = ev.read_take_meta(videos[0][1], me.parse_kv(a.rig))
    cfg0, _ = me.resolve_config(a, root, meta0, None)
    cap = cv2.VideoCapture(str(videos[0][1]))
    W0, H0 = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    roi0 = me.resolve_roi(a.roi, cfg0, W0, H0)
    static_empty, static_info = (None, {"source": None})
    if a.static in ("auto", "empty", "both"):
        static_empty, static_info = build_static(a, root, roi0)
    tiles = Tiles(a.sheet)
    takes: Dict[str, dict] = {}
    for name, v, role in videos:
        print(f"[belt_eval] {name}: {v.name}", flush=True)
        takes[name] = analyze_take(a, root, v, tiles, static_empty, static_info, role)
        print_take(name, takes[name])
    budget = ir_budget(takes, a)
    print_budget(budget)
    files = []
    if a.sheet > 0:
        f = write_sheet(tiles, out, a.jpeg_q)
        if f:
            files.append(f)
    if not a.no_plots:
        files += write_plots(takes, budget, out, a.jpeg_q)
    summ = {"tool": "belt_eval", "version": TOOL_VERSION, "created": datetime.now().isoformat(timespec="seconds"),
            "host": platform.node(), "argv": sys.argv[1:], "repo": str(root), "remote": bool(os.environ.get("WD_REMOTE")),
            "helpers": {"belt_detector": bd.__file__, "marker_eval": me.__file__, "marker_evallib": ev.__file__},
            "project": a.project,
            "takes": {k: {kk: vv for kk, vv in s.items() if kk != "_samples"} for k, s in takes.items()},
            "ir_budget": budget, "files": files}
    (out / "summary.json").write_text(json.dumps(ev.jsonable(summ), indent=1))
    total = sum((out / f).stat().st_size for f in files + ["summary.json"])
    print(f"\n[belt_eval] -> {out}  ({', '.join(files + ['summary.json'])}; {total / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
