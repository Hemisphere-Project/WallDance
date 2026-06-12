#!/usr/bin/env python3
"""Phase 2b shared infrastructure (ROADMAP 4.2 Phase 2b: imgsz x model benchmark).

Design (see tmp_analysis/phase2b/SUMMARY.md when written):
  - One standard detect_cache build per scenario at CONF_FLOOR with the pinned
    model+imgsz = the GRAY STORE (motion grays are model/imgsz-independent).
  - Per (scenario, model, imgsz) CELL: a fast dets-only build capturing the
    PRE-dup-filter, ROI-local detections + their YOLO box confs.
  - Scoring replays a cell at threshold tau by re-running the live order:
    tau filter -> _filter_duplicate_detections -> _offset_detections ->
    _feed_motion_detectors -> _track_detections, then scoring.score_timeline.
    (Filtering BEFORE the dup-filter is required: its keep rule is area-sorted,
    not conf-sorted, so post-dup filtering would not match a real run at tau.)

ASCII-only output (PowerShell cp1252 trap).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "application" / "tests"
SRC = REPO / "application" / "src"
for _p in (TESTS, SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

PHASE_DIR = Path(__file__).resolve().parent
CELLS_DIR = PHASE_DIR / "cells"
RESULTS_DIR = PHASE_DIR / "results"
PROGRESS_DIR = PHASE_DIR / "progress"
GRAYSTORE_INDEX = PHASE_DIR / "graystore_index.json"

CONF_FLOOR = 0.05
MODELS = (
    "yolo11n-pose", "yolo11s-pose", "yolo11m-pose", "yolo11l-pose", "yolo11x-pose",
    "yolo26n-pose", "yolo26s-pose", "yolo26m-pose", "yolo26l-pose", "yolo26x-pose",
)
IMGSZ_PRESETS = (640, 800, 960, 1280, 1536, 1920)  # = calib2 presets
TAU_GRID = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.65)
PRUNE_NET_MIN = 30.0  # px predicted net height; "far under any plausible knee"

CELL_FORMAT = 1


# --------------------------------------------------------------------------- #
# Scenario / cell enumeration
# --------------------------------------------------------------------------- #
def load_scenarios(only: Optional[List[str]] = None) -> List[dict]:
    """All 12 manifests, each augmented with _config (pinned) + _video (path)."""
    import replay
    import scoring
    out = []
    for f in sorted((TESTS / "scenarios").glob("*.json")):
        m = scoring.load_scenario(f)
        if only and m["name"] not in only:
            continue
        m["_config"] = replay.scenario_config(m)
        video = replay._find_recording(m["project"], m["slot"])
        if video is None:
            raise SystemExit(f"recording missing for {m['name']}")
        replay.check_fingerprint(m, video)
        m["_video"] = str(video)
        out.append(m)
    return out


def probe_long_side(config: dict, video: str) -> float:
    """Long side of the YOLO input image (ROI crop if enabled, else frame)."""
    if config.get("roi_enabled"):
        return float(max(int(config.get("roi_w", 0)), int(config.get("roi_h", 0))))
    import cv2
    cap = cv2.VideoCapture(video)
    try:
        w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    finally:
        cap.release()
    return float(max(w, h))


def net_height(config: dict, imgsz: int, long_side: float) -> float:
    """Predicted dancer height in YOLO-input px (calib2.select_imgsz's metric)."""
    ph = float(config.get("person_height_px", 0) or 0)
    if ph <= 0 or long_side <= 0:
        return 0.0
    return ph * imgsz / long_side


def cell_imgsz_list(config: dict, long_side: float) -> List[int]:
    """Presets surviving the net-height prune for one scenario."""
    return [s for s in IMGSZ_PRESETS
            if net_height(config, s, long_side) >= PRUNE_NET_MIN]


def cell_path(scenario: str, model: str, imgsz: int) -> Path:
    return CELLS_DIR / f"{scenario}__{model}__{imgsz}.pkl"


def save_cell(path: Path, payload: dict) -> None:
    import pickle
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)


def load_cell(path: Path) -> dict:
    import pickle
    with open(path, "rb") as f:
        payload = pickle.load(f)
    if payload.get("format") != CELL_FORMAT:
        raise ValueError(f"cell format {payload.get('format')} != {CELL_FORMAT}")
    return payload


# --------------------------------------------------------------------------- #
# Gray store
# --------------------------------------------------------------------------- #
def graystore_config(config: dict) -> dict:
    cfg = dict(config)
    cfg["confidence"] = CONF_FLOOR
    return cfg


def graystore_cache_path(manifest: dict) -> Path:
    import detect_cache
    cfg = graystore_config(manifest["_config"])
    key = detect_cache.cache_key(
        cfg, Path(manifest["_video"]).name, int(manifest["start"]),
        int(manifest["frames"]), cfg.get("model", "yolo11x-pose"),
        int(cfg.get("yolo_imgsz", 1280)))
    return detect_cache.cache_path_for(key)


def decode_grays(cache_path: Path) -> list:
    """Decode the gray store's PNGs once; returns list of np arrays."""
    import cv2
    import numpy as np
    import detect_cache
    cache = detect_cache.load_cache(cache_path)
    grays = [cv2.imdecode(np.frombuffer(fr["gray_png"], np.uint8),
                          cv2.IMREAD_GRAYSCALE) for fr in cache["frames"]]
    return grays


# --------------------------------------------------------------------------- #
# tau filter + replay (the live-order chain from a cell)
# --------------------------------------------------------------------------- #
def tau_membership_hash(cell: dict, tau: float) -> str:
    """Hash of exactly which dets survive tau -- identical hash => identical replay."""
    h = hashlib.sha1()
    for fr in cell["frames_data"]:
        kept = tuple(i for i, bc in enumerate(fr["box_confs"])
                     if bc is None or bc >= tau)
        h.update(repr(kept).encode())
    return h.hexdigest()


def replay_cell(cell: dict, grays: list, config: dict, manifest: dict,
                tau: float, collect_track_confs: bool = False) -> Tuple[dict, list]:
    """Replay one cell at threshold tau through the real post-YOLO chain.

    Returns (summary_with_per_frame, reported_track_box_confs).
    """
    import replay
    from pipeline import FrameProcessor

    n = len(cell["frames_data"])
    if len(grays) < n:
        raise RuntimeError(
            f"gray store has {len(grays)} frames < cell {n} "
            f"({cell['scenario']} {cell['model']}@{cell['imgsz']})")

    proc = replay._build_processor(
        dict(config, confidence=tau), cell["model"], cell["imgsz"],
        load_model=False)
    proc.tracker.reset()
    tmp = tempfile.mkdtemp(prefix="wd_p2b_")
    proc.tracker.logger.start_session(tmp)
    bbox_key = FrameProcessor._bbox_conf_key

    per_frame = []
    track_confs: List[float] = []
    start = int(manifest["start"])
    for i, fr in enumerate(cell["frames_data"]):
        dets = []
        confmap = {}
        for (k, c, b), bc in zip(fr["dets"], fr["box_confs"]):
            if bc is not None and bc < tau:
                continue
            k2, c2, b2 = k.copy(), c.copy(), b.copy()  # dup-filter mutates kpts
            dets.append((k2, c2, b2))
            if bc is not None:
                confmap[bbox_key(b2)] = bc
        proc._last_box_confs = confmap
        dets = proc._filter_duplicate_detections(dets)
        dets = proc._offset_detections(dets, fr["roi_x"], fr["roi_y"])
        proc._feed_motion_detectors(grays[i])
        timing: Dict[str, float] = {}
        tracks = proc._track_detections(
            dets, fr["roi_x"], fr["roi_y"], fr["ow"], fr["oh"], i, timing)
        per_frame.append(replay.per_frame_record(i, start + i, tracks))
        if collect_track_confs:
            for t in tracks:
                bc = getattr(t, "box_conf", None)
                if bc is not None:
                    track_confs.append(float(bc))
    proc.tracker.logger.close()
    summary = replay._summary_from_log(
        tmp, cell["video"], cell["model"], cell["imgsz"], start, n, per_frame)
    shutil.rmtree(tmp, ignore_errors=True)
    return summary, track_confs


def seed_tau_from_track_confs(track_confs: list) -> Optional[float]:
    """calib2 Phase 2 (5)a seed rule: clamp(p05(pooled box confs) - margin)."""
    if not track_confs:
        return None
    import numpy as np
    try:
        from config import AUTOCAL2_CONF_MARGIN, AUTOCAL2_CONF_BOUNDS
    except ImportError:
        AUTOCAL2_CONF_MARGIN, AUTOCAL2_CONF_BOUNDS = 0.05, (0.15, 0.65)
    p05 = float(np.percentile(track_confs, 5.0))
    lo, hi = AUTOCAL2_CONF_BOUNDS
    return round(min(hi, max(lo, p05 - AUTOCAL2_CONF_MARGIN)), 3)


# --------------------------------------------------------------------------- #
# Progress heartbeat
# --------------------------------------------------------------------------- #
def heartbeat(name: str, **fields) -> None:
    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": round(time.time(), 1),
           "t": time.strftime("%H:%M:%S"), **fields}
    line = json.dumps(rec)
    with open(PROGRESS_DIR / f"{name}.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"[{name}] {line}", flush=True)


def arm_stall_dump(timeout_s: int = 900) -> None:
    """Dump all thread stacks to stderr if nothing re-arms within timeout."""
    import faulthandler
    faulthandler.cancel_dump_traceback_later()
    faulthandler.dump_traceback_later(timeout_s, repeat=True, exit=False)
