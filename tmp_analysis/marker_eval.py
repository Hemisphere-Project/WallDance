#!/usr/bin/env python3
"""IR-marker eval harness (MRK-1; docs/audit-2026-10/02-ir-markers.md §6-§7).

It analyses WallDance slot recordings of dancers wearing retroreflective
wrist/ankle (+ harness) markers and prints the Phase-0a metrics M1-M7 with
their go/no-go status. It also runs the synthetic studies of MRK-2 on
marker-less footage. Docs: docs/MARKERS_PHASE0A.md.

Modes
  info       .meta v2 provenance of one take or of every take of a project
             (exposure/gain/AE, rig sheet, camlog, shoot-brief warnings). Cheap.
  floor      empty-stage take: brightness tail, glint map, fixed/moving chains,
             FP/frame after the glint map (M2), max_natural vs T (M3).
  assoc      Tier-A association against YOLO keypoints: recall (M1),
             saturation and area vs distance (M3/M4), non-marker blobs on dancers
             (M2), duo contested/wrong-dancer (M7), residuals -> proposed gate.
  centroid   offset-vote centroid error through simulated YOLO gaps (M6), on
             real blobs (default) or ``--markers keypoints``: the 02 §3.0
             synthetic study, which needs no video pass.
  occlusion  markers visible per dancer-frame, 0-marker run lengths (M5).
  sweep      threshold x gate x persistence grid -> recall/FP table + PR plot.
  all        assoc + centroid + occlusion (+ tail/FP) in one video pass.
  phase0a    the whole §6.2 take plan of one project: floor on slot 1 (glint
             map, T), then `all` on slots 2-8, plus the aggregated go/no-go.

Pose sources (``--poses``)
  pipeline         run the real GPU pipeline (``tests/replay.py``
                   ``_build_processor``; ``--trt`` for the TensorRT show path)
  cache[:PATH]     a TRT detect cache (``tests/detect_cache.py build``): cached
                   dets + raw ROI gray, replayed through the tracker. No YOLO and
                   no video decode.
  dump:PATH        a pose dump (pickle of rows: f,id,kpts,conf,bbox,fss), e.g.
                   one written by ``--save-poses`` or the audit's pose_dump.py
  none             floor/info

Synthetic injection (MRK-2): ``--inject disc|streak`` renders markers at the
YOLO wrist/ankle keypoints (+ ``--inject-harness``) of a marker-less take,
interpolated through YOLO gaps. Streak length = keypoint speed x exposure x fps.
Every mode then runs on the injected frames, and the summary adds the
detector's true recall against the injected ground truth.

Runs three ways (it resolves the repo from the cwd, not from __file__):
  local   application/.venv/bin/python tmp_analysis/marker_eval.py all --project P --slot 4
  remote  python extra/wdremote.py py --with tmp_analysis/marker_evallib.py \\
              --with application/src/core/marker_model.py \\
              tmp_analysis/marker_eval.py -- all --project P --slot 4 --trt
          (--with must come BEFORE the script: everything after it goes to the script.
          Output -> $WD_REMOTE_OUT, fetched to tmp_analysis/remote-runs/<stamp>/out)
  cache   ... all --scenario hangar-aerial --poses cache
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import platform
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

TOOL_VERSION = "1.0"
REMOTE_HELPERS = ("tmp_analysis/marker_evallib.py", "application/src/core/marker_model.py")
PHASE0A_SLOTS = "floor=1,static=2,holds=3,aerial=4,fast=5,occlusion=6,duo=7,ladder=8"
POSE_MODES = ("assoc", "centroid", "occlusion", "sweep", "all")

try:        # importable as-is when application/src is already on sys.path (tests)
    import cv2
    import marker_evallib as ev
    mm = ev.mm
except ImportError:  # script run: main() fixes sys.path, then import_helpers()
    ev = mm = cv2 = None


# ---------------------------------------------------------------------------
# Environment: repo root, imports
# ---------------------------------------------------------------------------

def find_repo_root(explicit: Optional[str] = None) -> Path:
    """The checkout root: ``--repo``, ``$WD_REPO_ROOT``, else the first
    ancestor of the cwd (``application/`` under wdremote) or of this file that
    holds ``application/src/core``. On the laptop this script lives in a
    scratch dir, so the cwd decides."""
    cands = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("WD_REPO_ROOT"):
        cands.append(Path(os.environ["WD_REPO_ROOT"]))
    cwd = Path.cwd().resolve()
    cands += [cwd, *cwd.parents]
    here = Path(__file__).resolve().parent
    cands += [here, *here.parents]
    for c in cands:
        if (c / "application" / "src" / "core").is_dir():
            return c.resolve()
    raise SystemExit("marker_eval: cannot find the repo root (no application/src/core above "
                     f"{cwd}); pass --repo")


def import_helpers(root: Path) -> None:
    global ev, mm, cv2
    src = str(root / "application" / "src")
    if src not in sys.path:
        sys.path.append(src)        # append: an uploaded marker_model.py next to us wins
    try:
        import marker_evallib as _ev  # noqa: WPS433
    except ImportError as e:
        withs = " ".join(f"--with {h}" for h in REMOTE_HELPERS)
        raise SystemExit(f"marker_eval: helper import failed ({e}). Remote runs need (--with BEFORE the script):\n"
                         f"  python extra/wdremote.py py {withs} tmp_analysis/marker_eval.py -- <args>")
    import cv2 as _cv2
    ev, mm, cv2 = _ev, _ev.mm, _cv2


def import_replay(root: Path):
    """tests/replay.py (it may re-exec the process once on Linux to fix
    LD_LIBRARY_PATH for torch; argv is preserved, so that is harmless here)."""
    tests = str(root / "application" / "tests")
    if tests not in sys.path:
        sys.path.append(tests)
    import replay  # noqa: WPS433
    return replay


# ---------------------------------------------------------------------------
# Take / config / ROI resolution
# ---------------------------------------------------------------------------

def list_takes(root: Path, project: str, slot: Optional[int] = None) -> List[Path]:
    rec = root / "projects" / project / "recordings"
    pat = f"slot_{slot}_*" if slot is not None else "slot_*"
    return sorted(p for p in rec.glob(pat) if p.suffix.lower() in (".avi", ".mp4", ".mkv", ".mov"))


def load_scenario(root: Path, name: str) -> dict:
    p = Path(name)
    if not p.suffix:
        p = root / "application" / "tests" / "scenarios" / f"{name}.json"
    elif not p.is_absolute() and not p.exists():
        p = root / "application" / p if (root / "application" / p).exists() else root / p
    return json.loads(p.read_text())


def resolve_take(a, root: Path) -> Tuple[Optional[Path], Optional[dict]]:
    scen = load_scenario(root, a.scenario) if a.scenario else None
    if scen:
        a.project = a.project or scen["project"]
        a.slot = a.slot if a.slot is not None else scen["slot"]
        if a.start is None:
            a.start = scen.get("start", 0)
        if a.frames is None:
            a.frames = scen.get("frames", 0)
    if a.start is None:
        a.start = 0
    if a.frames is None:
        a.frames = 0
    if a.video:
        v = Path(a.video)
        for c in (v, Path.cwd() / v, root / v):
            if c.exists():
                return c.resolve(), scen
        raise SystemExit(f"video not found: {a.video}")
    if a.project and a.slot is not None:
        takes = list_takes(root, a.project, a.slot)
        if not takes:
            raise SystemExit(f"no recording for project {a.project!r} slot {a.slot} "
                             f"under {root / 'projects' / a.project / 'recordings'}")
        return takes[a.take], scen
    return None, scen


def flatten_config(cfg: dict) -> dict:
    if isinstance(cfg, dict) and "profiles" in cfg:
        try:
            import core.config_schema as cs  # noqa: WPS433
            return cs.flatten(cfg)
        except Exception:
            return cfg
    return cfg


def project_config(root: Path, project: Optional[str]) -> Optional[dict]:
    if not project:
        return None
    pdir = root / "projects" / project
    cfgs = sorted((f for f in pdir.glob("*.json") if not f.name.startswith("_")),
                  key=lambda f: f.stat().st_mtime, reverse=True)
    if not cfgs:
        return None
    try:
        return flatten_config(json.loads(cfgs[0].read_text()))
    except (OSError, json.JSONDecodeError):
        return None


def resolve_config(a, root: Path, meta: dict, scen: Optional[dict]) -> Tuple[dict, str]:
    """Config for the pipeline/tracker and the ROI. ``--config``: a JSON path,
    or scenario|meta|project|auto (auto = scenario > take .meta > project)."""
    src = a.config
    if src not in ("auto", "scenario", "meta", "project"):
        return flatten_config(json.loads(Path(src).read_text())), f"file:{src}"
    order = ["scenario", "meta", "project"] if src == "auto" else [src]
    for s in order:
        if s == "scenario" and scen and scen.get("config") is not None:
            return json.loads(json.dumps(scen["config"])), "scenario"
        if s == "meta" and meta.get("_raw_config"):
            return flatten_config(dict(meta["_raw_config"])), "take .meta"
        if s == "project":
            c = project_config(root, a.project)
            if c:
                return c, "project latest config"
    return {}, "none (defaults)"


def resolve_roi(spec: str, cfg: dict, w: int, h: int) -> Optional[Tuple[int, int, int, int]]:
    if spec == "none":
        return None
    if spec != "auto":
        x, y, rw, rh = (int(v) for v in spec.split(","))
    else:
        if not cfg.get("roi_enabled") or not cfg.get("roi_w") or not cfg.get("roi_h"):
            return None
        x, y, rw, rh = (int(cfg.get(k, 0)) for k in ("roi_x", "roi_y", "roi_w", "roi_h"))
        sw, sh = int(cfg.get("roi_source_w") or w), int(cfg.get("roi_source_h") or h)
        if (sw, sh) != (w, h) and sw > 0 and sh > 0:
            fx, fy = w / sw, h / sh
            x, y, rw, rh = int(x * fx), int(y * fy), int(rw * fx), int(rh * fy)
    x, y = max(0, x), max(0, y)
    rw, rh = min(rw, w - x), min(rh, h - y)
    return (x, y, rw, rh) if rw > 0 and rh > 0 else None


def parse_kv(items: Sequence[str]) -> dict:
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            out[k.strip()] = float(v) if v.strip().replace(".", "", 1).replace("-", "", 1).isdigit() else v.strip()
        except ValueError:
            out[k.strip()] = v.strip()
    return out


# ---------------------------------------------------------------------------
# Pose sources
# ---------------------------------------------------------------------------

def track_row(t, f: int) -> dict:
    """pose_dump.py row format (float32 arrays keep pickles small)."""
    fss = t.frames_since_skeleton
    return {"f": int(f), "id": int(t.track_id),
            "kpts": np.asarray(t.keypoints, np.float32), "conf": np.asarray(t.confidence, np.float32),
            "bbox": np.asarray(t.bbox, np.float32), "fss": int(fss if fss is not None else -1)}


class DumpPoses:
    inline = False

    def __init__(self, rows: List[dict], label: str = "dump"):
        self.rows = rows
        self.label = label
        self.by_f: Dict[int, List[dict]] = defaultdict(list)
        for r in rows:
            self.by_f[int(r["f"])].append(ev.norm_track(r))

    def get(self, f: int, frame=None) -> List[dict]:
        return self.by_f.get(int(f), [])

    def close(self):
        pass


def load_dump(path: str) -> List[dict]:
    with open(path, "rb") as fh:
        d = pickle.load(fh)
    return d["rows"] if isinstance(d, dict) else list(d)


class PipelinePoses:
    """The real GPU pipeline per frame (pose_dump.py style)."""
    inline = True

    def __init__(self, root: Path, cfg: dict, model: str, imgsz: int, trt: bool):
        replay = import_replay(root)
        self.proc = replay._build_processor(cfg, model, imgsz, use_gpu_path=True, use_trt=trt)
        self.proc.tracker.reset()
        self.logdir = tempfile.mkdtemp(prefix="wd_markereval_")
        self.proc.tracker.logger.start_session(self.logdir)
        self.rows: List[dict] = []
        self.n = 0
        self.label = f"pipeline ({model}@{imgsz}, {'TRT' if trt else 'PyTorch'})"
        self.ms: List[float] = []

    def get(self, f: int, frame=None) -> List[dict]:
        t0 = time.perf_counter()
        tracks, *_ = self.proc.process(frame, need_preview=False, frame_number=self.n)
        self.ms.append((time.perf_counter() - t0) * 1000)
        self.n += 1
        rows = [track_row(t, f) for t in tracks]
        self.rows.extend(rows)
        return [ev.norm_track(r) for r in rows]

    def close(self):
        try:
            self.proc.tracker.logger.close()
        except Exception:
            pass


def find_cache(root: Path, video_name: str, start: int, frames: int, cfg: dict) -> Path:
    """The TRT detect cache for this window: the exact key of
    ``tests/detect_cache.py`` (config front-end params + window + 'trt') when
    that file exists, else the newest cache of the same video/window (a note
    is printed: its front-end config may differ from ``cfg``)."""
    cdir = root / "application" / "tests" / "cache"
    stem = Path(video_name).stem
    try:
        tests = str(root / "application" / "tests")
        if tests not in sys.path:
            sys.path.append(tests)
        import detect_cache as dc  # pure key helpers; does not import replay
        model = (cfg.get("model") or "yolo11x-pose").replace(".pt", "")
        key = dc.cache_key(cfg, video_name, start, frames, model, int(cfg.get("yolo_imgsz", 1280)), path="trt")
        exact = cdir / dc.cache_path_for(key).name
        if exact.exists():
            return exact
    except Exception:
        exact = None
    pat = f"{stem}_s{start}_n{frames}_*.pkl" if frames else f"{stem}_s*_n*_*.pkl"
    hits = sorted(cdir.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        avail = sorted(p.name for p in cdir.glob(f"{stem}_*.pkl"))
        raise SystemExit(f"no detect cache {pat} in {cdir}; available: {avail or 'none'} "
                         "(build: python tests/detect_cache.py build --scenario ... | --project P --slot N "
                         "--start S --frames N)")
    print(f"[marker_eval] no exact-key cache ({exact.name if exact else '?'}); using newest {hits[0].name}")
    return hits[0]


class CacheSource:
    """Frames + poses from a TRT detect cache: the raw ROI gray (decoded PNG)
    is what the marker detector sees live; dets replay through the tracker."""

    def __init__(self, root: Path, path: Path, cfg: dict, start: Optional[int], frames: int):
        with open(path, "rb") as fh:
            self.payload = pickle.load(fh)
        self.meta = self.payload["meta"]
        self.path = path
        self.frames = self.payload["frames"]
        if self.frames and "space" not in self.frames[0]:
            raise SystemExit(f"{path.name}: legacy CPU detect cache (pre-Track-P: full-frame dets, "
                             "no tracker space). Rebuild it: python tests/detect_cache.py build ...")
        replay = import_replay(root)
        self.proc = replay._build_processor(cfg, self.meta["model"], self.meta["imgsz"], load_model=False)
        self.proc.tracker.reset()
        self.logdir = tempfile.mkdtemp(prefix="wd_markereval_cache_")
        self.proc.tracker.logger.start_session(self.logdir)
        self.start = int(self.meta["start_frame"])
        self.label = f"cache {path.name} ({self.meta.get('path', '?')}, {len(self.frames)} frames)"
        self.rows: List[dict] = []

    def iterate(self, nframes: int = 0, stride: int = 1):
        for i, fr in enumerate(self.frames):
            if nframes and i >= nframes:
                break
            gray = (cv2.imdecode(np.frombuffer(fr["gray_png"], np.uint8), cv2.IMREAD_GRAYSCALE)
                    if fr["gray_png"] is not None else None)
            dets = [(k, c, b) for (k, c, b) in fr["dets"]]
            tracks = self.proc.replay_gpu_cached(dets, fr["space"], gray, fr["ow"], fr["oh"], i, {})
            f = self.start + i
            rows = [track_row(t, f) for t in tracks]
            self.rows.extend(rows)
            if gray is None:
                raise SystemExit("detect cache has no motion gray (motion detectors off when built)")
            if i % stride:
                continue
            off = (int(fr["space"]["roi_x"]), int(fr["space"]["roi_y"]))
            yield f, None, gray, off, [ev.norm_track(r) for r in rows], (int(fr["oh"]), int(fr["ow"]))

    def close(self):
        try:
            self.proc.tracker.logger.close()
        except Exception:
            pass


def iter_video(video: Path, start: int, nframes: int, stride: int, roi, poses=None, progress: int = 0):
    """Yields (f, gray_full, gray, offset, tracks, (h, w)). Inline pose sources
    (pipeline) see every frame; the analysis sees every ``stride``-th one."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    mono = None
    i = 0
    t0 = time.time()
    try:
        need_all = poses is not None and poses.inline
        while nframes <= 0 or i < nframes:
            if i % stride and not need_all:
                if not cap.grab():          # skipped frame: no decode/convert
                    break
                i += 1
                continue
            ok, frame = cap.read()
            if not ok:
                break
            f = start + i
            tracks = poses.get(f, frame) if (poses is not None and poses.inline) else None
            if i % stride == 0:
                gray_full, mono = ev.to_gray(frame, mono)
                if roi:
                    x, y, w, h = roi
                    gray, off = gray_full[y:y + h, x:x + w], (x, y)
                else:
                    gray, off = gray_full, (0, 0)
                if tracks is None and poses is not None:
                    tracks = poses.get(f)
                yield f, gray_full, gray, off, tracks or [], gray_full.shape[:2]
            i += 1
            if progress and i % progress == 0:
                dt = time.time() - t0
                print(f"  .. {i} frames ({i / max(dt, 1e-6):.1f} fps)", flush=True)
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Synthetic injection (MRK-2)
# ---------------------------------------------------------------------------

class Injector:
    """Renders markers at the YOLO extremity keypoints (+ hip midpoint) of a
    pose timeline. Through YOLO gaps of <= ``max_gap`` frames the slots are
    linearly interpolated between the surrounding skeleton frames.
    ``kind='streak'`` moves each marker by (keypoint speed x exposure x fps)
    during the exposure; ``'disc'`` renders static discs."""

    def __init__(self, rows: List[dict], *, kind: str, marker_cm: float, level: float,
                 psf: float, exposure_us: float, fps: float, harness: bool,
                 max_gap: int = 20, smooth: int = 0):
        self.kind, self.level, self.psf = kind, float(level), float(psf)
        self.harness = harness
        self.exp_frames = float(exposure_us) * 1e-6 * float(fps)   # exposure as a frame fraction
        S = len(mm.EXT_KPTS) + (1 if harness else 0)
        by: Dict[int, Dict[int, Tuple[np.ndarray, np.ndarray, float]]] = defaultdict(dict)
        for r in rows:
            if r["fss"] != 0:
                continue
            pts, vis = mm.extremity_points(r["kpts"], r["conf"], harness=harness)
            by[int(r["id"])][int(r["f"])] = (pts, vis, float(r["bbox"][3]))
        self.at: Dict[int, List[tuple]] = defaultdict(list)
        n_interp = 0
        for tid, fr in by.items():
            fs = sorted(fr)
            series: Dict[int, Tuple[np.ndarray, np.ndarray, float]] = dict(fr)
            for fa, fb in zip(fs[:-1], fs[1:]):
                if 1 < fb - fa <= max_gap + 1:
                    pa, va, ha = fr[fa]
                    pb, vb, hb = fr[fb]
                    for f in range(fa + 1, fb):
                        w = (f - fa) / (fb - fa)
                        series[f] = (pa * (1 - w) + pb * w, va & vb, ha * (1 - w) + hb * w)
                        n_interp += 1
            if smooth > 1:
                series = self._smooth(series, smooth)
            for f, (pts, vis, H) in series.items():
                prev = series.get(f - 1)
                d = mm.marker_diameter_px(marker_cm, H)
                # per-frame motion of each marker: matched to the NEAREST previous
                # marker (Hungarian, identity-free) rather than the same YOLO label,
                # so the ~21 % L/R label flips do not render as huge fake streaks
                vel = np.zeros((S, 2))
                if prev is not None and kind == "streak" and prev[1].any():
                    pp = prev[0][prev[1]]
                    idx = mm.associate_points(np.where(vis[:, None], pts, np.nan), pp)
                    ok = idx >= 0
                    vel[ok] = pts[ok] - pp[idx[ok]]
                for s in range(S):
                    if not vis[s]:
                        continue
                    v = vel[s] * self.exp_frames
                    self.at[f].append((tid, s, float(pts[s][0]), float(pts[s][1]), d,
                                       float(v[0]), float(v[1])))
        self.n_interp = n_interp
        self.stats = {"markers": 0, "gt_hit": 0, "err_px": [], "streak_px": []}

    @staticmethod
    def _smooth(series, w):
        fs = sorted(series)
        out = {}
        for f in fs:
            win = [series[g] for g in range(f - w // 2, f + w // 2 + 1) if g in series]
            vis = series[f][1]
            pts = np.mean([p for p, _, _ in win], axis=0)
            out[f] = (pts, vis, series[f][2])
        return out

    def apply(self, gray: np.ndarray, off, f: int) -> List[tuple]:
        gt = []
        for tid, s, x, y, d, vx, vy in self.at.get(f, []):
            if mm.render_marker(gray, (x, y), diameter=d, level=self.level, velocity=(vx, vy),
                                psf_sigma=self.psf, offset=off) is not None:
                gt.append((x, y, d, float(np.hypot(vx, vy)), tid, s))
        return gt

    def score(self, gt: List[tuple], blobs: List[dict]) -> None:
        if not gt:
            return
        B = np.array([(b["x"], b["y"]) for b in blobs]) if blobs else np.zeros((0, 2))
        for x, y, d, L, _tid, _s in gt:
            self.stats["markers"] += 1
            self.stats["streak_px"].append(L)
            if not len(B):
                continue
            dist = np.linalg.norm(B - np.array([x, y])[None], axis=1)
            j = int(np.argmin(dist))
            if dist[j] <= max(3.0, 0.5 * d + 0.5 * L + 1.5):
                self.stats["gt_hit"] += 1
                self.stats["err_px"].append(float(dist[j]))

    def summary(self) -> dict:
        st = self.stats
        n = st["markers"]
        return {"kind": self.kind, "level_dn": self.level, "psf_sigma": self.psf,
                "exposure_frames": ev.rnd(self.exp_frames, 3), "interpolated_frames": self.n_interp,
                "markers": n, "gt_recall": ev.rnd(st["gt_hit"] / n, 4) if n else None,
                "centroid_err_px_p50_p90": [ev.pct(st["err_px"], 50, 2), ev.pct(st["err_px"], 90, 2)],
                "streak_px_p50_p90": [ev.pct(st["streak_px"], 50, 1), ev.pct(st["streak_px"], 90, 1)]}


# ---------------------------------------------------------------------------
# Glint map from an empty-stage take
# ---------------------------------------------------------------------------

def build_glint(video: Path, start: int, n: int, stride: int, roi, T_lo: int, cell: int,
                min_area: int) -> Tuple["ev.GlintMap", dict]:
    """Two short passes over the empty-stage frames: (1) blobs at ``T_lo`` ->
    glint map, (2) natural maximum outside the map -> recommended T (§2.3)."""
    gm = None
    for f, gfull, gray, off, _t, shape in iter_video(video, start, n, stride, roi):
        if gm is None:
            gm = ev.GlintMap(shape, cell)
        gm.add(ev.detect_blobs(gray, T_lo, min_area=1, offset=off))
    if gm is None:
        raise SystemExit(f"glint take {video} has no frames")
    gm.finalize(1)
    fs = ev.FloorStats()
    for f, gfull, gray, off, _t, shape in iter_video(video, start, n, stride, roi):
        fs.add_natural(gray, gm, off)
    mn = np.array(fs.max_natural)
    info = {"video": video.name, "frames": len(mn), "T_lo": T_lo, "cells": gm.n_cells,
            "max_natural": int(mn.max()), "max_natural_p999": int(np.percentile(mn, 99.9)),
            "T_recommended": ev.recommend_threshold(float(np.percentile(mn, 99.9)))}
    gm.T = info["T_recommended"]
    return gm, info


# ---------------------------------------------------------------------------
# One take
# ---------------------------------------------------------------------------

def default_out(a, root: Path, name: str) -> Path:
    if a.out:
        return Path(a.out)
    if os.environ.get("WD_REMOTE_OUT"):
        return Path(os.environ["WD_REMOTE_OUT"])
    return root / "tmp_analysis" / "marker_eval_out" / name


def analyze_take(a, root: Path, video: Optional[Path], scen: Optional[dict], out: Path,
                 mode: str, *, glint=None, glint_info=None, T_override=None,
                 quiet: bool = False) -> dict:
    t_start = time.time()
    out.mkdir(parents=True, exist_ok=True)
    rig = parse_kv(a.rig)
    meta = ev.read_take_meta(video, rig) if video is not None else {"present": False, "shoot_brief": {}}
    cfg, cfg_src = resolve_config(a, root, meta, scen)
    fps = float(meta.get("actual_fps") or (scen or {}).get("fps") or 20.0)
    cam = meta.get("camera") or {}
    if a.exposure_us:
        exposure_us, expo_src = a.exposure_us, "cli"
    elif cam.get("exposure_us"):
        exposure_us, expo_src = cam["exposure_us"], "take .meta camera"
    elif cfg.get("ids_exposure_us"):
        exposure_us, expo_src = cfg["ids_exposure_us"], f"config ids_exposure_us ({cfg_src}; may postdate the take)"
    else:
        exposure_us, expo_src = None, "unknown"
    summ: dict = {"tool": "marker_eval", "version": TOOL_VERSION, "mode": mode,
                  "created": datetime.now().isoformat(timespec="seconds"), "host": platform.node(),
                  "argv": sys.argv[1:], "repo": str(root),
                  "helpers": {"marker_evallib": ev.__file__, "marker_model": mm.__file__},
                  "remote": bool(os.environ.get("WD_REMOTE"))}

    # ---- poses ----------------------------------------------------------
    poses_spec = a.poses
    if poses_spec == "auto":
        poses_spec = "pipeline" if mode in POSE_MODES else "none"
    if mode == "floor":
        poses_spec = "none"
    auto_save = None
    if a.poses_dir and poses_spec == "pipeline" and video is not None:
        # per-take pose store: YOLO runs once per take/window, later runs reuse it
        pd = Path(a.poses_dir)
        cand = pd / f"poses_{video.stem}_s{a.start}_n{a.frames}.pkl"
        if cand.exists():
            poses_spec = f"dump:{cand}"
            print(f"[marker_eval] reusing poses {cand}")
        else:
            auto_save = cand
    cache_src = None
    poses = None
    dump_rows: List[dict] = []
    if poses_spec.startswith("dump:"):
        for p in poses_spec[5:].split(","):
            dump_rows += load_dump(p)
        poses = DumpPoses(dump_rows, f"dump {poses_spec[5:]}")
    elif poses_spec.startswith("cache"):
        cpath = Path(poses_spec[6:]) if poses_spec.startswith("cache:") else \
            find_cache(root, video.name if video else "", a.start, a.frames, cfg)
        cache_src = CacheSource(root, cpath, cfg, a.start, a.frames)
    elif poses_spec == "pipeline":
        model = (a.model or cfg.get("model") or "yolo11x-pose").replace(".pt", "")
        imgsz = int(a.imgsz or cfg.get("yolo_imgsz") or 1280)
        poses = PipelinePoses(root, cfg, model, imgsz, a.trt)
    elif poses_spec != "none":
        raise SystemExit(f"unknown --poses {poses_spec}")

    # ---- keypoint-only synthetic study (no video pass) ---------------------
    if mode == "centroid" and a.markers == "keypoints":
        if isinstance(poses, PipelinePoses):
            for _ in iter_video(video, a.start, a.frames, 1, None, poses, a.progress):
                pass
            dump_rows = poses.rows
            poses.close()
        elif cache_src is not None:
            for _ in cache_src.iterate(a.frames):
                pass
            dump_rows = cache_src.rows
            cache_src.close()
        if not dump_rows:
            raise SystemExit("no poses for the keypoint study")
        for target in (a.save_poses, auto_save):
            if target and isinstance(poses, PipelinePoses):
                save_poses(str(target), dump_rows, video, a.start)
        studies, per = [], {}
        for label, rows in group_rows(dump_rows, poses_spec):
            by = ev.tracks_from_rows(rows)
            studies.append(ev.gap_study(by, require_all=True))
            nfr = sum(len(v) for v in by.values())
            per[label] = {"yolo_track_frames": nfr, "tracks": len(by),
                          "all4_visible_pct": ev.rnd(100 * np.mean([v["vis"].all() for t in by.values()
                                                                    for v in t.values()]), 1) if nfr else 0}
        st = ev.summarize_study(ev.merge_studies(studies))
        st["inputs"] = per
        st["r_table"] = {str(k): {str(kk): list(vv) for kk, vv in v.items()}
                         for k, v in ev.study_r_table(st["gap_table"]).items()}
        summ["centroid"] = {"markers": "keypoints (02 §3.0 synthetic study)", **st}
        summ["gates"] = {"M6": ev.gate_m6(st)}
        summ["take"] = {"video": str(video) if video else None, "poses": poses_spec}
        summ["timing_s"] = ev.rnd(time.time() - t_start, 1)
        write_summary(out, summ)
        if not quiet:
            print(ev.format_gap_table(st["gap_table"]))
            print("kinematics:", json.dumps(st["kinematics_per_frame_over_H"]))
            print(ev.format_gates(summ["gates"]))
        return summ

    # ---- frame source ---------------------------------------------------
    if cache_src is not None and a.inject != "none":
        print("[marker_eval] inject: cache pose pre-pass ...", flush=True)
        for _ in cache_src.iterate(a.frames):
            pass
        dump_rows = cache_src.rows
        cache_src.close()
        cache_src = CacheSource(root, cache_src.path, cfg, a.start, a.frames)
    if cache_src is not None:
        frames_iter = cache_src.iterate(a.frames, a.stride)
        shape0 = (int(cache_src.frames[0]["oh"]), int(cache_src.frames[0]["ow"]))
        roi = None
        region_of = lambda off, g: (off[0], off[1], off[0] + g.shape[1], off[1] + g.shape[0])  # noqa: E731
        total = len(cache_src.frames)
        pose_label = cache_src.label
    else:
        if video is None:
            raise SystemExit("no take: pass --project/--slot, --video or --scenario")
        cap = cv2.VideoCapture(str(video))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        shape0 = (int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)))
        cap.release()
        roi = resolve_roi(a.roi, cfg, shape0[1], shape0[0])
        stride = a.stride
        if mode in ("centroid", "occlusion", "all", "phase0a") and stride != 1:
            print(f"[marker_eval] {mode}: stride forced to 1 (gap study / persistence need every frame)")
            stride = a.stride = 1
        if a.inject != "none" and isinstance(poses, PipelinePoses):
            # injection interpolates through gaps -> needs the whole timeline first
            print("[marker_eval] inject: pose pre-pass ...", flush=True)
            for _ in iter_video(video, a.start, a.frames, 1, None, poses, a.progress):
                pass
            poses.close()
            dump_rows = poses.rows
            poses = DumpPoses(dump_rows, poses.label + " (pre-pass)")
        frames_iter = iter_video(video, a.start, a.frames, stride, roi, poses, a.progress)
        region_of = lambda off, g: (off[0], off[1], off[0] + g.shape[1], off[1] + g.shape[0])  # noqa: E731
        pose_label = poses.label if poses is not None else "none"

    # ---- injection --------------------------------------------------------
    injector = None
    if a.inject != "none":
        rows = dump_rows or (poses.rows if isinstance(poses, DumpPoses) else [])
        if not rows:
            raise SystemExit("--inject needs poses (pipeline, dump or a pre-pass)")
        injector = Injector(rows, kind=a.inject, marker_cm=a.marker_cm, level=a.inject_level,
                            psf=a.inject_psf, exposure_us=exposure_us or 25000.0, fps=fps,
                            harness=a.inject_harness, smooth=a.inject_smooth)
    writer = None
    if a.write_video and cache_src is None:
        writer = cv2.VideoWriter(a.write_video, cv2.VideoWriter_fourcc(*"FFV1"), 30.0,
                                 (shape0[1], shape0[0]))
        if not writer.isOpened():
            raise SystemExit(f"cannot open FFV1 writer for {a.write_video}")

    # ---- glint / threshold -------------------------------------------------
    T_lo = a.glint_t
    if glint is None and a.glint_map:
        glint = ev.GlintMap.load(a.glint_map)
        glint_info = {"loaded": a.glint_map, "cells": glint.n_cells, "T_recommended": glint.T}
    if glint is None and (a.glint_slot is not None or a.glint_video):
        if a.glint_video:
            gvid = Path(a.glint_video)
        else:
            gtakes = list_takes(root, a.project, a.glint_slot) if a.project else []
            if not gtakes:
                raise SystemExit(f"--glint-slot {a.glint_slot}: no take in project {a.project!r}")
            gvid = gtakes[-1]
        gcfg = resolve_config(a, root, ev.read_take_meta(gvid), None)[0]
        gcap = cv2.VideoCapture(str(gvid))
        gshape = (int(gcap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(gcap.get(cv2.CAP_PROP_FRAME_WIDTH)))
        gcap.release()
        groi = resolve_roi(a.roi, gcfg, gshape[1], gshape[0])
        print(f"[marker_eval] glint map from {gvid.name} ({a.glint_frames} frames @ stride 2) ...", flush=True)
        glint, glint_info = build_glint(gvid, 0, a.glint_frames, 2, groi, T_lo, 8, a.min_area)
    self_glint = mode == "floor" and glint is None
    thresholds = [int(t) for t in a.thresholds.split(",")] if a.thresholds else []
    if T_override is not None:
        T = int(T_override)
    elif a.threshold == "auto":
        T = int(glint.T) if glint is not None and glint.T else 160
    else:
        T = int(a.threshold)
    if mode == "floor":
        # a 10-DN grid: with --threshold auto, M2 is read at the first grid value
        # >= the §2.3 recommendation, and phase0a hands that T to the other takes
        thresholds = sorted(set(thresholds or (list(range(100, 251, 10)) + [254])) | {T})
    elif mode == "sweep":
        thresholds = sorted(set(thresholds or [100, 130, 160, 200, 230, 250]) | {T})
    else:
        thresholds = [T]
    gates_h = [float(g) for g in a.gates.split(",")] if mode == "sweep" else [a.gate_h]
    glint_frames_self = 0
    if self_glint:
        n_window = (a.frames or max(0, total - a.start)) // max(1, a.stride)
        glint_frames_self = min(a.glint_frames // max(1, a.stride), max(1, n_window // 2))

    # ---- accumulators -----------------------------------------------------
    expo_of = ev.exposure_lookup({"series": meta.get("_camlog_series")} if meta.get("_camlog_series") else None,
                                 cam.get("exposure_us"))
    pm = ev.PoseMetrics(harness=a.harness, unocc_conf=a.unocc_conf, fast_h=a.fast_h,
                        gate_h=a.gate_h, exposure_of=expo_of) if mode in POSE_MODES else None
    floor = ev.FloorStats()
    frame_ids: List[int] = []
    tail_every = 1 if mode == "floor" else 10
    linkers = {T_: ev.ChainLinker(link_px=a.link_px * max(1, a.stride), persist_k=a.persist, persist_n=3)
               for T_ in thresholds}
    per_T = {T_: {"counts": [], "fp_raw": [], "fp_persist": [], "areas": [], "peaks": []} for T_ in thresholds}
    sweep = defaultdict(lambda: [0, 0, 0, 0, 0, 0])   # (T,g) -> unocc, hit, assoc, fp_raw, fp_persist, frames
    det_ms, det_ms_empty = [], []
    pools = {"hits_far": ev.TilePool(a.sheet // 3, seed=1), "hits_mid": ev.TilePool(a.sheet // 3, seed=2),
             "hits_near": ev.TilePool(a.sheet - 2 * (a.sheet // 3), seed=3),
             "misses": ev.TilePool(a.sheet, seed=4), "fp": ev.TilePool(a.sheet, mode="top"),
             "floor": ev.TilePool(a.sheet, mode="top")}
    overview_every = None
    overviews: List[Tuple[int, np.ndarray]] = []
    jl = ev.JsonlWriter(out / "frames.jsonl.gz" if a.jsonl else None)
    if a.threads:
        cv2.setNumThreads(a.threads)
    H_terciles = None
    n_proc = 0
    mono_checked = None
    gbuild = None
    if not quiet:
        print(f"[marker_eval] {mode}: {video.name if video else cache_src.path.name} "
              f"start={a.start} frames={a.frames or 'all'} stride={a.stride} T={T} "
              f"poses={pose_label} roi={roi} inject={a.inject}", flush=True)
    est_frames = (a.frames or max(1, total - a.start))
    if a.overview > 0:
        overview_every = max(1, est_frames // (a.overview + 1))

    for f, gfull, gray, off, tracks, shape in frames_iter:
        if injector is not None:
            gt = injector.apply(gray, off, f)
        else:
            gt = None
        if writer is not None and gfull is not None:
            writer.write(cv2.merge([gfull, gfull, gfull]))
        region = region_of(off, gray)
        if pm is not None:
            pm.region = region
        # glint self-build (floor)
        if self_glint and n_proc < glint_frames_self:
            if gbuild is None:
                gbuild = ev.GlintMap(shape, 8)
            gbuild.add(ev.detect_blobs(gray, T_lo, min_area=1, offset=off))
            n_proc += 1
            if n_proc == glint_frames_self:
                glint = gbuild.finalize(1)
                glint_info = {"built_from": "this take", "frames": glint_frames_self, "T_lo": T_lo,
                              "cells": glint.n_cells}
            continue
        n_proc += 1
        frame_ids.append(f)
        if n_proc % tail_every == 0:
            floor.add_tail(gray)
            floor.add_natural(gray, glint, off)
        blobs_T = {}
        for T_ in thresholds:
            st: dict = {}
            t0 = time.perf_counter()
            bl = ev.detect_blobs(gray, T_, cell=a.cell, min_area=a.min_area, max_area=a.max_area,
                                 offset=off, stats=st)
            dt = (time.perf_counter() - t0) * 1000
            if T_ == T:
                (det_ms_empty if st.get("n_px", 0) == 0 else det_ms).append(dt)
            if glint is not None:
                glint.flag(bl)
            linkers[T_].step(n_proc, bl)
            d = per_T[T_]
            d["counts"].append(len(bl))
            n_raw, n_persist = ev.count_fp(bl)
            d["fp_raw"].append(n_raw)
            d["fp_persist"].append(n_persist)
            for b in bl[:50]:
                d["areas"].append(b["area"]); d["peaks"].append(b["peak"])
            blobs_T[T_] = bl
        blobs = blobs_T[T]
        if injector is not None:
            injector.score(gt, blobs)
        if mode == "floor":
            for b in blobs:
                if b.get("static"):
                    continue
                pools["floor"].offer(b["peak"] * 1000 + b["area"], lambda b=b: tile_for(gray, off, b, f))
        A = None
        if pm is not None:
            pm.observe_heights(tracks)
            A = ev.associate(tracks, blobs, g=a.gate_h, kpt_conf_min=a.kpt_conf, harness=a.harness,
                             H_of=pm.H_of)
            pm.add_frame(f, tracks, blobs, A)
            collect_tiles(pools, gray, off, f, tracks, blobs, A, pm, a)
            if mode == "sweep":
                sweep_step(sweep, tracks, blobs_T, gates_h, pm, a)
        if overview_every and (n_proc % overview_every == 0) and len(overviews) < a.overview:
            overviews.append((f, ev.overview_image(gray, off, blobs, tracks, A, label=f"f{f} T{T}")))
        if a.jsonl:
            jl.write({"f": f, "blobs": blobs, "tracks": [
                {"id": t["id"], "fss": t["fss"], "H": t["H"], "bbox": t["bbox"],
                 "ext": [[*t["kpts"][k], t["conf"][k]] for k in mm.EXT_KPTS]} for t in tracks],
                "assoc": [[A.rows[r][0], A.rows[r][1], b, d, c] for r, b, d, c in A.pairs] if A else []})
    jl.close()
    if writer is not None:
        writer.release()
        if video is not None:
            # a v2 sidecar, clearly marked synthetic: the camera block only carries
            # the exposure the streaks were rendered with (source = "synthetic")
            m = {"actual_fps": fps, "frames": n_proc, "meta_version": 2, "codec": "FFV1",
                 "container_fps": 30.0, "size": [shape0[1], shape0[0]],
                 "camera": {"source": "synthetic", "nodes": {"ExposureTime": exposure_us}},
                 "rig": {"markers": f"synthetic {a.inject} {a.marker_cm:g} cm at YOLO kpts"
                                    + (" + harness" if a.inject_harness else ""),
                         "notes": f"MRK-2 injection from {video.name} f{a.start}+"},
                 "config": meta.get("_raw_config") or cfg,
                 "synthetic": {"source": video.name, "start": a.start, "inject": a.inject,
                               "marker_cm": a.marker_cm, "level": a.inject_level, "psf": a.inject_psf,
                               "exposure_us": exposure_us, "exposure_source": expo_src}}
            Path(a.write_video + ".meta").write_text(json.dumps(m, indent=1))
    if poses is not None:
        poses.close()
    if cache_src is not None:
        cache_src.close()
    for target in (a.save_poses, auto_save):
        if not target:
            continue
        rows = dump_rows or getattr(poses, "rows", None) or (cache_src.rows if cache_src else [])
        if rows:
            save_poses(str(target), rows, video, a.start)

    # ---- summary ----------------------------------------------------------
    files: List[str] = []
    summ["take"] = {"video": str(video) if video else None, "file": video.name if video else None,
                    "bytes": video.stat().st_size if video and video.exists() else None,
                    "frames_total": total, "start": a.start, "frames": a.frames, "stride": a.stride,
                    "frames_analysed": n_proc - glint_frames_self, "shape": list(shape0), "roi": roi,
                    "fps": fps, "config_source": cfg_src, "poses": pose_label,
                    "exposure_us": exposure_us, "exposure_source": expo_src}
    summ["provenance"] = ev.public_meta(meta)
    if rig:
        summ["provenance"]["rig_cli"] = rig
    summ["detector"] = {"T": T, "thresholds": thresholds, "cell": a.cell, "min_area": a.min_area,
                        "max_area": a.max_area, "gate_h": a.gate_h, "persist": f"{a.persist}/3",
                        "link_px": a.link_px, "glint": glint_info,
                        "cost_ms": {"nonempty_p50": ev.pct(det_ms, 50, 2), "nonempty_p95": ev.pct(det_ms, 95, 2),
                                    "empty_p50": ev.pct(det_ms_empty, 50, 3), "empty_p95": ev.pct(det_ms_empty, 95, 3),
                                    "max": ev.rnd(max(det_ms + det_ms_empty), 2) if (det_ms or det_ms_empty) else None,
                                    "cv2_threads": cv2.getNumThreads()}}
    if isinstance(poses, PipelinePoses) and poses.ms:
        summ["take"]["pipeline_ms_p50"] = ev.pct(poses.ms, 50, 1)
    floor_sum = floor.summary(T)
    per_T_sum = {}
    for T_ in thresholds:
        d = per_T[T_]
        c = np.array(d["counts"]) if d["counts"] else np.zeros(1)
        fixed, moving, short = ev.classify_chains(linkers[T_].finish(), a.static_px, a.static_min_frames)
        per_T_sum[str(T_)] = {
            "frames_ge1_pct": ev.rnd(100 * float(np.mean(c >= 1)), 2),
            "mean_per_frame": ev.rnd(float(c.mean()), 3), "max_per_frame": int(c.max()),
            "fp_per_frame_raw": ev.rnd(float(np.mean(d["fp_raw"])) if d["fp_raw"] else 0, 4),
            "fp_per_frame": ev.rnd(float(np.mean(d["fp_persist"])) if d["fp_persist"] else 0, 4),
            "area_p50_p95": [ev.pct(d["areas"], 50, 0), ev.pct(d["areas"], 95, 0)],
            "peak_p50": ev.pct(d["peaks"], 50, 0),
            "chains_fixed": len(fixed), "chains_moving": len(moving), "chains_short_lt3": len(short),
            "fixed_positions": [[round(ch["mean"][0]), round(ch["mean"][1]), ch["n"]]
                                for ch in sorted(fixed, key=lambda c: -c["n"])[:6]],
            "moving_examples": [[round(ch["mean"][0]), round(ch["mean"][1]), ch["n"], round(ch["span"], 1)]
                                for ch in sorted(moving, key=lambda c: -c["n"])[:6]]}
    T_eval = T
    if mode == "floor":
        reco = floor_sum.get("T_recommended")
        if a.threshold == "auto" and T_override is None and reco:
            cands = [t for t in thresholds if t >= reco]
            T_eval = min(cands) if cands else max(thresholds)
        d = per_T[T_eval]
        floor_sum.update(ev.fp_summary(frame_ids, d["fp_raw"], d["fp_persist"],
                                       time_bin=max(1, int(10 * fps / max(1, a.stride)))))
        floor_sum["T_eval"] = T_eval
        floor_sum["max_natural_over_T"] = (ev.rnd(floor_sum["max_natural"]["max"] / T_eval, 3)
                                           if floor_sum.get("max_natural") else None)
        floor_sum["per_T"] = per_T_sum
        summ["floor"] = floor_sum
    else:
        summ["floor_like"] = {"tail": floor_sum.get("tail"), "max_natural": floor_sum.get("max_natural"),
                              "per_T": per_T_sum}
    gates: Dict[str, dict] = {}
    if pm is not None:
        m1 = pm.m1()
        m34 = pm.m3_m4()
        m2b = pm.m2b()
        m5 = pm.m5()
        m7 = pm.m7()
        if mode in ("assoc", "all", "sweep"):
            summ["assoc"] = {"M1": m1, "M2_dancer": m2b, "M3_M4": m34, "M7": m7}
            gates["M1"] = ev.gate_m1(m1)
            gates["M2"] = ev.gate_m2(None, m2b)
            gates["M3"] = ev.gate_m3(m34, None, T)
            gates["M4"] = {"status": "INFO", "value": m34.get("m4_area_by_H")}
            gates["M7"] = ev.gate_m7(m7)
        if mode in ("occlusion", "all"):
            summ["occlusion"] = {"M5": m5}
            gates["M5"] = ev.gate_m5(m5, aerial=True)
        if mode in ("centroid", "all"):
            st = ev.summarize_study(ev.gap_study(pm.study, require_all=a.study_require_all))
            summ["centroid"] = {"markers": "real blobs (Tier-A labels, unlabelled through the gap)", **st}
            gates["M6"] = ev.gate_m6(st)
        if mode == "sweep":
            summ["sweep"] = sweep_summary(sweep, out, files)
    if mode == "floor":
        gates["M2"] = ev.gate_m2(floor_sum, None)
        gates["M3"] = ev.gate_m3(None, floor_sum, T_eval)
    if injector is not None:
        summ["inject"] = injector.summary()
    summ["gates"] = gates
    summ["gates_spec"] = {k: ev.GATES[k] for k in gates}
    # ---- sheets ----------------------------------------------------------
    if a.sheet > 0:
        hits = pools["hits_far"].tiles() + pools["hits_mid"].tiles() + pools["hits_near"].tiles()
        for name, tiles, title in (
                ("sheet_hits.jpg", hits, "associated blobs: far | mid | near (red + = YOLO kpt, green o = blob)"),
                ("sheet_misses.jpg", pools["misses"].tiles(), "unoccluded keypoints with NO blob in the gate (yellow = gate)"),
                ("sheet_fp.jpg", pools["fp"].tiles(), "persistent non-marker blobs: off-dancer or on a skeleton dancer w/o slot"),
                ("sheet_floor.jpg", pools["floor"].tiles(), f"brightest non-static blobs at T={T}")):
            img = ev.make_sheet(tiles, f"{video.name if video else ''} {title}")
            if img is not None:
                ev.write_jpeg(out / name, img, quality=a.jpeg_q)
                files.append(name)
        for f, img in overviews:
            name = f"overview_f{f}.jpg"
            ev.write_jpeg(out / name, img, quality=a.jpeg_q, max_width=640)
            files.append(name)
    if glint is not None and glint_info and (glint_info.get("built_from") or glint_info.get("video")):
        glint.save(out / "glint_map.npz")
        cv2.imwrite(str(out / "glint_map.png"), (glint.cells * 255).astype(np.uint8))
        files += ["glint_map.npz", "glint_map.png"]
    summ["files"] = files
    summ["timing_s"] = ev.rnd(time.time() - t_start, 1)
    write_summary(out, summ)
    if not quiet:
        print_report(summ)
    return summ


def tile_for(gray, off, b, f, half: int = 24) -> dict:
    img, origin = ev.crop(gray, b["x"], b["y"], half, off)
    return {"img": img, "origin": origin, "marks": [(b["x"], b["y"], "blob")], "f": f,
            "top": f"f{f}", "bottom": f"p{b['peak']} a{b['area']} e{b['elong']:.1f}"}


def collect_tiles(pools, gray, off, f, tracks, blobs, A, pm, a) -> None:
    """Feed the contact-sheet pools from one frame (crops are cut now, while
    the frame is in memory; rendering happens at the end)."""
    if a.sheet <= 0:
        return
    sb = A.slot_blob()
    names = list(mm.SLOT_NAMES) + [mm.HARNESS_SLOT]
    for ti, t in enumerate(tracks):
        if t["fss"] != 0:
            continue
        H = pm.H_of(t)
        pts, _ = mm.extremity_points(t["kpts"], t["conf"], harness=a.harness, conf_min=0.0)
        conf = t["conf"][list(mm.EXT_KPTS)]
        if a.harness:
            conf = np.append(conf, t["conf"][list(mm.HIP_KPTS)].min())
        gate = max(4.0, a.gate_h * H)
        for s in range(len(pts)):
            if conf[s] < a.unocc_conf:
                continue
            got = sb.get((ti, s))
            p = pts[s]
            if got is not None:
                b = blobs[got[0]]
                key = "hits_far" if H < 150 else ("hits_mid" if H < 300 else "hits_near")

                def mk(p=p, b=b, s=s, t=t, H=H):
                    img, origin = ev.crop(gray, p[0], p[1], 24, off)
                    return {"img": img, "origin": origin, "f": f,
                            "marks": [(p[0], p[1], "kp"), (b["x"], b["y"], "blob")],
                            "top": f"f{f} {names[s]} H{int(H)}", "bottom": f"p{b['peak']} a{b['area']}"}
                pools[key].offer(0, mk)
            else:
                half = int(min(48, max(16, gate)))

                def mk2(p=p, s=s, H=H, half=half, gate=gate):
                    img, origin = ev.crop(gray, p[0], p[1], half, off)
                    return {"img": img, "origin": origin, "f": f,
                            "marks": [(p[0], p[1], "kp"), (p[0], p[1], "gate", gate)],
                            "top": f"f{f} {names[s]}", "bottom": f"H{int(H)} c{conf[s]:.2f}"}
                pools["misses"].offer(0, mk2)
    for bi, b in enumerate(blobs):
        if pm.zones[bi] in (ev.ZONE_FREE, ev.ZONE_ON_SKELETON) and not b.get("static") and b.get("persistent"):
            pools["fp"].offer(b["peak"] * 1000 + b["area"], lambda b=b: tile_for(gray, off, b, f))


def sweep_step(acc, tracks, blobs_T, gates_h, pm, a) -> None:
    for T_, blobs in blobs_T.items():
        for g in gates_h:
            A = ev.associate(tracks, blobs, g=g, kpt_conf_min=a.kpt_conf, harness=a.harness, H_of=pm.H_of)
            row = acc[(T_, g)]
            B = np.array([(b["x"], b["y"]) for b in blobs]) if blobs else np.zeros((0, 2))
            for t in tracks:
                if t["fss"] != 0:
                    continue
                H = pm.H_of(t)
                pts, _ = mm.extremity_points(t["kpts"], t["conf"], harness=a.harness, conf_min=0.0)
                conf = t["conf"][list(mm.EXT_KPTS)]
                if a.harness:
                    conf = np.append(conf, t["conf"][list(mm.HIP_KPTS)].min())
                for s in range(len(pts)):
                    if conf[s] < a.unocc_conf or not pm._inside(pts[s]):
                        continue
                    row[0] += 1
                    if len(B):
                        streak = np.array([b.get("streak", 0.0) for b in blobs])
                        row[1] += bool((np.linalg.norm(B - pts[s][None], axis=1) <= max(4.0, g * H) + 0.5 * streak).any())
            row[2] += int((A.owner >= 0).sum())
            # FPs: off-body (free) or on a skeleton dancer but no slot; blobs on a
            # coasting (YOLO-gap) track are Tier-B candidates, not FPs
            zones = ev.blob_zones(tracks, blobs, A, margin_h=pm.body_margin_h, H_of=pm.H_of)
            fps = [b for b, z in zip(blobs, zones)
                   if z in (ev.ZONE_FREE, ev.ZONE_ON_SKELETON) and not b.get("static")]
            row[3] += len(fps)
            row[4] += sum(1 for b in fps if b.get("persistent"))
            row[5] += 1


def sweep_summary(acc, out: Path, files: List[str]) -> dict:
    table = []
    for (T_, g), (unocc, hit, assoc, fp_raw, fp_p, n) in sorted(acc.items()):
        rec = hit / unocc if unocc else None
        table.append({"T": T_, "gate_h": g, "recall": ev.rnd(rec, 4),
                      "fp_per_frame_raw": ev.rnd(fp_raw / max(1, n), 4),
                      "fp_per_frame_persist": ev.rnd(fp_p / max(1, n), 4),
                      "precision_raw": ev.rnd(assoc / (assoc + fp_raw), 4) if assoc + fp_raw else None,
                      "precision_persist": ev.rnd(assoc / (assoc + fp_p), 4) if assoc + fp_p else None,
                      "N_slots": unocc})
    ok = [r for r in table if r["recall"] is not None and r["fp_per_frame_persist"] <= 0.01]
    best = max(ok, key=lambda r: (r["recall"], -r["fp_per_frame_persist"], r["T"])) if ok else None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(6, 4), dpi=90)
        for g in sorted({r["gate_h"] for r in table}):
            rows = [r for r in table if r["gate_h"] == g and r["recall"] is not None]
            ax.plot([r["fp_per_frame_persist"] for r in rows], [r["recall"] for r in rows], "o-",
                    label=f"gate {g} H")
            for r in rows:
                ax.annotate(str(r["T"]), (r["fp_per_frame_persist"], r["recall"]), fontsize=7)
        ax.axhline(0.95, color="g", lw=0.6, ls="--"); ax.axvline(0.01, color="r", lw=0.6, ls="--")
        ax.set_xlabel("FP / frame (persistent, non-static, unassociated)"); ax.set_ylabel("recall (M1)")
        ax.set_xscale("symlog", linthresh=0.001); ax.legend(fontsize=7); ax.grid(alpha=0.3)
        fig.tight_layout(); fig.savefig(out / "pr_curve.png"); plt.close(fig)
        files.append("pr_curve.png")
    except Exception as e:  # matplotlib is optional
        print(f"[marker_eval] PR plot skipped: {e}")
    return {"table": table, "best_recall_at_fp<=0.01": best}


def group_rows(rows: List[dict], spec: str):
    """Split concatenated dumps back per input file when several were given."""
    if spec.startswith("dump:") and "," in spec:
        out = []
        for p in spec[5:].split(","):
            out.append((Path(p).stem, load_dump(p)))
        return out
    return [(Path(spec[5:]).stem if spec.startswith("dump:") else spec, rows)]


def save_poses(path: str, rows: List[dict], video: Optional[Path], start: int) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as fh:
        pickle.dump({"video": str(video) if video else None, "start": start, "rows": rows,
                     "frames": len({r["f"] for r in rows})}, fh, protocol=pickle.HIGHEST_PROTOCOL)
    print(f"[marker_eval] poses -> {p} ({len(rows)} rows)")


def write_summary(out: Path, summ: dict) -> None:
    (out / "summary.json").write_text(json.dumps(ev.jsonable(summ), indent=1))


def print_report(s: dict) -> None:
    t = s.get("take", {})
    print(f"\n=== marker_eval {s['mode']}: {t.get('file')}  frames {t.get('frames_analysed')} "
          f"({s.get('timing_s')} s) ===")
    pv = s.get("provenance", {})
    cam = pv.get("camera", {})
    rig = pv.get("rig", {})
    print(f"meta v{pv.get('meta_version')}  fps {pv.get('actual_fps')}  exposure {cam.get('exposure_us')} us  "
          f"gain {cam.get('gain_db')} dB  AE {cam.get('exposure_auto')}/{cam.get('gain_auto')}  "
          f"IR offset {rig.get('illuminator_offset_cm')} cm  dist {rig.get('camera_distance_m')} m  "
          f"markers {rig.get('markers')!r}")
    for w in pv.get("shoot_brief", {}).get("warnings", []):
        print(f"  ! {w}")
    if pv.get("shoot_brief", {}).get("missing"):
        print(f"  missing: {', '.join(pv['shoot_brief']['missing'])}")
    d = s.get("detector", {})
    print(f"detector T={d.get('T')} cost {d.get('cost_ms')}")
    if s.get("floor"):
        fl = s["floor"]
        print(f"floor: tail {fl.get('tail')}\n       max_natural {fl.get('max_natural')}  "
              f"T_recommended {fl.get('T_recommended')}  FP/frame {fl.get('fp_per_frame')}")
    if s.get("inject"):
        print(f"inject: {s['inject']}")
    if s.get("assoc"):
        m1 = s["assoc"]["M1"]
        print(f"M1 recall {m1.get('recall')}  by distance "
              f"{ {k: v['recall'] for k, v in m1.get('by_distance', {}).items()} }  fast {m1.get('fast', {}).get('recall')}")
        m34 = s["assoc"]["M3_M4"]
        print(f"M3 saturated {m34.get('saturated_pct')} %  residual/H p50/p90/p95 {m34.get('residual_H_p50_p90_p95')}"
              f"  proposed gate {m34.get('proposed_gate_H')} H")
    if s.get("occlusion"):
        print(f"M5 {s['occlusion']['M5']}")
    if s.get("centroid"):
        gt = s["centroid"]["gap_table"]
        keep = {k: v for k, v in gt.items() if k.split("_")[0] in ("n2", "n4", "hip1", "all5")}
        print(ev.format_gap_table(keep, ests=("hold", "offset_ul", "offset_rb")))
    if s.get("sweep"):
        print(f"sweep best (fp<=0.01): {s['sweep']['best_recall_at_fp<=0.01']}")
    print("gates (02 s6.3):")
    print(ev.format_gates(s.get("gates", {})))
    print(f"files: {', '.join(s.get('files', []))}")


# ---------------------------------------------------------------------------
# info + phase0a
# ---------------------------------------------------------------------------

def mode_info(a, root: Path, out: Path) -> dict:
    if a.video or a.slot is not None or a.scenario:
        v, _ = resolve_take(a, root)
        takes = [v]
    elif a.project:
        takes = list_takes(root, a.project)
    else:
        raise SystemExit("info: pass --project (all takes) or --project/--slot / --video")
    rig = parse_kv(a.rig)
    rows = []
    for v in takes:
        m = ev.read_take_meta(v, rig)
        cap = cv2.VideoCapture(str(v))
        nfr = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if cap.isOpened() else None
        size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))) if cap.isOpened() else None
        cap.release()
        m = ev.public_meta(m)
        m["container_frames"] = nfr
        m["frame_size"] = size
        m["bytes"] = v.stat().st_size
        rows.append(m)
        cam, rig_ = m.get("camera", {}), m.get("rig", {})
        print(f"{v.name:<34} v{m.get('meta_version')} {nfr} fr {m.get('actual_fps')} fps {size} "
              f"{m.get('codec', '?')} {v.stat().st_size / 1e9:.2f} GB | exp {cam.get('exposure_us')} us "
              f"gain {cam.get('gain_db')} dB AE {cam.get('exposure_auto')}/{cam.get('gain_auto')} | "
              f"IR off {rig_.get('illuminator_offset_cm')} cm dist {rig_.get('camera_distance_m')} m "
              f"markers {rig_.get('markers')!r}")
        for w in m["shoot_brief"]["warnings"]:
            print(f"    ! {w}")
        if m["shoot_brief"]["missing"]:
            print(f"    missing: {', '.join(m['shoot_brief']['missing'])}")
    summ = {"tool": "marker_eval", "version": TOOL_VERSION, "mode": "info", "project": a.project,
            "created": datetime.now().isoformat(timespec="seconds"), "takes": rows}
    out.mkdir(parents=True, exist_ok=True)
    write_summary(out, summ)
    return summ


def mode_phase0a(a, root: Path, out: Path) -> dict:
    """§6.2 take plan of one project: slot 1 floor (glint map + T), then
    `all` on every other present slot, and the aggregated §6.3 gates."""
    if not a.project:
        raise SystemExit("phase0a: --project required")
    roles = {}
    for item in a.slots.split(","):
        k, _, v = item.partition("=")
        roles[k.strip()] = int(v)
    a.sheet = min(a.sheet, 24)
    a.overview = min(a.overview, 2)
    results: Dict[str, dict] = {}
    glint, glint_info, T = None, None, None
    t0 = time.time()
    order = sorted(roles.items(), key=lambda kv: (kv[0] != "floor", kv[1]))
    for role, slot in order:
        takes = list_takes(root, a.project, slot)
        if not takes:
            print(f"[phase0a] {role} (slot {slot}): no take -- skipped")
            continue
        video = takes[a.take]
        sub = out / f"{role}_s{slot}"
        a.slot, a.video, a.start, a.frames = slot, None, a.p_start, a.p_frames
        print(f"\n[phase0a] {role} (slot {slot}): {video.name}", flush=True)
        if role == "floor":
            s = analyze_take(a, root, video, None, sub, "floor", quiet=False)
            gpath = sub / "glint_map.npz"
            if gpath.exists():
                glint = ev.GlintMap.load(gpath)
                glint_info = {"from": f"slot {slot}", "cells": glint.n_cells}
            fl = s.get("floor", {})
            # the T M2/M3 were evaluated at (= the first grid value >= the §2.3
            # recommendation with --threshold auto), so every take shares one T
            T = fl.get("T_eval") or fl.get("T_recommended")
            if glint is not None and T:
                glint.T = T
        else:
            T_use = None if a.threshold != "auto" else T
            s = analyze_take(a, root, video, None, sub, "all", glint=glint, glint_info=glint_info,
                             T_override=T_use, quiet=False)
        results[role] = s
    agg = aggregate_phase0a(results)
    summ = {"tool": "marker_eval", "version": TOOL_VERSION, "mode": "phase0a", "project": a.project,
            "created": datetime.now().isoformat(timespec="seconds"), "roles": {r: s for r, s in roles.items()},
            "T": T, "gates": agg, "gates_spec": ev.GATES, "timing_s": ev.rnd(time.time() - t0, 1),
            "per_take": {r: {"file": s.get("take", {}).get("file"), "gates": s.get("gates"),
                             "dir": f"{r}_s{roles[r]}"} for r, s in results.items()}}
    (out / "phase0a.json").write_text(json.dumps(ev.jsonable(summ), indent=1))
    print("\n=== Phase 0a go/no-go (02 s6.3) ===")
    print(ev.format_gates(agg))
    go = all(agg.get(m, {}).get("status") == "PASS" for m in ("M1", "M2", "M3", "M5", "M6"))
    print(f"Phase 0a GO (M1, M2, M3, M5, M6 pass; M9 is manual): {'YES' if go else 'NO'}")
    return summ


def aggregate_phase0a(res: Dict[str, dict]) -> Dict[str, dict]:
    def m1_of(r):
        return ((res.get(r) or {}).get("assoc") or {}).get("M1") or {}
    pooled = [m1_of(r) for r in ("static", "aerial", "fast") if m1_of(r).get("N")]
    gates: Dict[str, dict] = {}
    if pooled:
        N = sum(m["N"] for m in pooled)
        rec = sum(m["N"] * m["recall"] for m in pooled) / N
        far_src = m1_of("static") if m1_of("static").get("N") else None
        fars = [far_src] if far_src else pooled
        fN = sum(m["by_distance"]["far"]["N"] for m in fars)
        frec = (sum(m["by_distance"]["far"]["N"] * (m["by_distance"]["far"]["recall"] or 0) for m in fars) / fN
                if fN else None)
        fast = m1_of("fast")
        comb = {"N": N, "recall": ev.rnd(rec, 4),
                "by_distance": {"far": {"N": fN, "recall": ev.rnd(frec, 4)}},
                "fast": {"N": fast.get("N", 0), "recall": fast.get("recall")}}
        gates["M1"] = ev.gate_m1(comb)
        gates["M1"]["source"] = "pooled static+aerial+fast; far = static take; fast = fast take (all its marker-frames)"
    else:
        gates["M1"] = ev._g("N/A", None, "no static/aerial/fast take with poses")
    fl = (res.get("floor") or {}).get("floor")
    m2b = ((res.get("aerial") or {}).get("assoc") or {}).get("M2_dancer")
    gates["M2"] = ev.gate_m2(fl, m2b)
    m3s = [((res.get(r) or {}).get("assoc") or {}).get("M3_M4") or {} for r in ("aerial", "fast", "static")]
    m3s = [m for m in m3s if m.get("N")]
    m3 = None
    if m3s:
        N = sum(m["N"] for m in m3s)
        m3 = {"N": N, "saturated_pct": ev.rnd(sum(m["N"] * m["saturated_pct"] for m in m3s) / N, 2)}
    T = ((res.get("floor") or {}).get("floor") or {}).get("T_eval") or 160
    gates["M3"] = ev.gate_m3(m3, fl, T)
    lad = ((res.get("ladder") or {}).get("assoc") or {}).get("M3_M4") or {}
    if lad.get("saturated_pct_by_exposure_us"):
        gates["M3"]["ladder_by_exposure_us"] = lad["saturated_pct_by_exposure_us"]
    st = ((res.get("static") or {}).get("assoc") or {}).get("M3_M4") or {}
    gates["M4"] = {"status": "INFO", "value": st.get("m4_area_by_H")}
    m5 = ((res.get("aerial") or {}).get("occlusion") or {}).get("M5")
    gates["M5"] = ev.gate_m5(m5) if m5 else ev._g("N/A", None, "no aerial take")
    occ = ((res.get("occlusion") or {}).get("occlusion") or {}).get("M5")
    if occ:
        gates["M5"]["occlusion_take"] = {k: occ.get(k) for k in ("ge1_pct", "ge2_pct", "zero_runs")}
    cen = (res.get("aerial") or {}).get("centroid")
    gates["M6"] = ev.gate_m6(cen) if cen else ev._g("N/A", None, "no aerial take")
    m7 = ((res.get("duo") or {}).get("assoc") or {}).get("M7")
    gates["M7"] = ev.gate_m7(m7) if m7 else ev._g("N/A", None, "no duo take (required only if duos are in the show)")
    gates["M8"] = ev._g("N/A", None, "Phase-2 gate (replay markers on vs off)")
    gates["M9"] = ev._g("MANUAL", None, "audience-side phone video incl. near the projector/front light")
    return gates


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="marker_eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["info", "floor", "assoc", "centroid", "occlusion", "sweep", "all", "phase0a"])
    src = ap.add_argument_group("take")
    src.add_argument("--project")
    src.add_argument("--slot", type=int)
    src.add_argument("--take", type=int, default=-1, help="index among the slot's takes (default -1 = newest)")
    src.add_argument("--video", help="path to a recording (abs, or relative to cwd / repo)")
    src.add_argument("--scenario", help="tests/scenarios/<name>.json: project/slot/window/pinned config")
    src.add_argument("--start", type=int, default=None)
    src.add_argument("--frames", type=int, default=None, help="0 = to the end")
    src.add_argument("--stride", type=int, default=1)
    src.add_argument("--roi", default="auto", help="auto (from config) | none | x,y,w,h")
    src.add_argument("--config", default="auto", help="auto|scenario|meta|project|<json path>")
    src.add_argument("--rig", action="append", metavar="KEY=VALUE",
                     help="fill missing rig-sheet facts (old .meta), e.g. illuminator_offset_cm=8")
    src.add_argument("--repo", help="repo root (default: from the cwd)")
    ps = ap.add_argument_group("poses")
    ps.add_argument("--poses", default="auto", help="auto | pipeline | cache[:PATH] | dump:PATH[,PATH] | none")
    ps.add_argument("--trt", action="store_true", help="pipeline: TensorRT engine (show path)")
    ps.add_argument("--model")
    ps.add_argument("--imgsz", type=int)
    ps.add_argument("--save-poses", help="write the pose rows (pickle) for re-use with --poses dump:")
    ps.add_argument("--poses-dir", help="per-take pose store: reuse <dir>/poses_<take>_s<start>_n<frames>.pkl "
                                        "if present, else run the pipeline and save it there")
    ps.add_argument("--markers", choices=["blobs", "keypoints"], default="blobs",
                    help="centroid: real blobs, or markers simulated at the YOLO keypoints (02 §3.0)")
    ps.add_argument("--study-require-all", action="store_true",
                    help="centroid on blobs: require all 4 markers through the gap (exact 02 §3.0 method)")
    dt = ap.add_argument_group("detector / association")
    dt.add_argument("--threshold", default="160", help="primary T, or 'auto' (glint take's §2.3 value)")
    dt.add_argument("--thresholds", default="", help="floor/sweep: comma list")
    dt.add_argument("--cell", type=int, default=4)
    dt.add_argument("--min-area", type=int, default=2)
    dt.add_argument("--max-area", type=int, default=0)
    dt.add_argument("--gate-h", type=float, default=0.20, help="association gate g (x H)")
    dt.add_argument("--gates", default="0.1,0.15,0.2,0.3", help="sweep: gate list")
    dt.add_argument("--persist", type=int, default=2, help="k of the last 3 frames for persistence")
    dt.add_argument("--link-px", type=float, default=40.0, help="chain link gate px/frame")
    dt.add_argument("--static-px", type=float, default=4.0)
    dt.add_argument("--static-min-frames", type=int, default=10)
    dt.add_argument("--kpt-conf", type=float, default=0.1, help="min keypoint conf to predict a slot")
    dt.add_argument("--unocc-conf", type=float, default=0.5, help="keypoint conf = 'unoccluded' pseudo-GT")
    dt.add_argument("--fast-h", type=float, default=0.2, help="M1 fast subset: keypoint speed >= x H/frame")
    dt.add_argument("--harness", action="store_true", help="associate a 5th (hip-midpoint) harness slot")
    dt.add_argument("--glint-slot", type=int, help="empty-stage slot of the same project -> glint map + auto T")
    dt.add_argument("--glint-video")
    dt.add_argument("--glint-map", help="glint_map.npz from an earlier floor run")
    dt.add_argument("--glint-frames", type=int, default=600)
    dt.add_argument("--glint-t", type=int, default=100, help="T_lo for the glint map")
    dt.add_argument("--threads", type=int, default=0, help="cv2.setNumThreads (1 ~ the motion worker)")
    ij = ap.add_argument_group("synthetic injection (MRK-2)")
    ij.add_argument("--inject", choices=["none", "disc", "streak"], default="none")
    ij.add_argument("--marker-cm", type=float, default=5.0)
    ij.add_argument("--inject-level", type=float, default=2000.0,
                    help="static marker level in DN (>255 = saturating margin; ~140-360 ~ 1 deg off-axis)")
    ij.add_argument("--inject-psf", type=float, default=0.6)
    ij.add_argument("--inject-harness", action="store_true")
    ij.add_argument("--inject-smooth", type=int, default=0, help="moving-average window on keypoints")
    ij.add_argument("--exposure-us", type=float, default=None, help="default: .meta, else config ids_exposure_us")
    ij.add_argument("--write-video", help="also write the injected take (FFV1 .avi + .meta)")
    op = ap.add_argument_group("output")
    op.add_argument("--out", help="default: $WD_REMOTE_OUT, else tmp_analysis/marker_eval_out/<name>")
    op.add_argument("--sheet", type=int, default=48, help="max tiles per contact sheet (0 = none)")
    op.add_argument("--overview", type=int, default=3, help="downscaled annotated frames")
    op.add_argument("--jpeg-q", type=int, default=75)
    op.add_argument("--jsonl", action="store_true", help="per-frame JSONL (gzip)")
    op.add_argument("--progress", type=int, default=500)
    ph = ap.add_argument_group("phase0a")
    ph.add_argument("--slots", default=PHASE0A_SLOTS, help="role=slot mapping (02 §6.2)")
    ph.add_argument("--p-start", type=int, default=0)
    ph.add_argument("--p-frames", type=int, default=0)
    return ap


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    root = find_repo_root(a.repo)
    import_helpers(root)
    if a.mode == "info":
        mode_info(a, root, default_out(a, root, f"info_{a.project or 'take'}"))
        return 0
    if a.mode == "phase0a":
        mode_phase0a(a, root, default_out(a, root, f"phase0a_{a.project}"))
        return 0
    video, scen = resolve_take(a, root)
    name = f"{a.mode}_{(video.stem if video else 'cache')}_s{a.start}"
    analyze_take(a, root, video, scen, default_out(a, root, name), a.mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
