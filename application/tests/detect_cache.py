#!/usr/bin/env python3
"""YOLO detect-pass cache (TUNING.md Phase B — fast iteration keystone).

YOLO (yolo11x-pose @1280 ≈ 65 ms/frame) dominates a replay; the gate, motion
and tracker tuning we actually search over does NOT change YOLO's output.  So:

  B1 build_cache:  run the full CPU path ONCE over a window, capturing the raw
                   post-offset / pre-gate detections + the per-frame motion gray
                   (via FrameProcessor._cache_capture).
  B2 replay_from_cache:  rebuild only a tracker/gate/motion processor (no model
                   loaded) and re-run _track_detections from the cache, skipping
                   YOLO entirely → an order of magnitude faster, making a search
                   interactive.

Because both the build and the replay reuse the *same* pipeline methods
(_process_cpu for the front-end capture, _track_detections for the back-end),
cache replay is bit-identical to a live replay for any config that leaves the
YOLO front-end unchanged.  Front-end params (model, imgsz, confidence, enhance,
greyscale, gamma/clahe, ROI, bg-subtract) are baked into the cache KEY — change
one and you need a fresh cache.  Everything in _track_detections + motion (gate
θ_s/θ_m, exclusion, mog2 scale/var, person height, tracker params) is tunable
from the cache.

CLI:
    # build
    python tests/detect_cache.py build --scenario tests/scenarios/residence1-solo_slot4.json
    # replay-from-cache + score
    python tests/detect_cache.py replay --scenario tests/scenarios/residence1-solo_slot4.json --score
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np

# NOTE: ``replay`` is imported LAZILY inside the functions that need it, not at
# module top.  Importing replay runs its _bootstrap_cuda_libs() which os.execv's
# the process to fix LD_LIBRARY_PATH for torch -- fine for a standalone CLI, but
# it would restart an in-process pytest session.  Keeping it lazy lets the
# pure-Python cache key/path helpers (and their unit test) import this module
# without triggering the re-exec.

CACHE_FORMAT = 1
CACHE_DIR = Path(__file__).resolve().parent / "cache"

# Config keys that change YOLO's detections or the raw motion gray → baked into
# the cache key (changing any requires a rebuild).  Everything else is tunable
# from the cache.
REBUILD_KEYS = (
    "model", "yolo_imgsz", "confidence",
    "enhance_enabled", "enhance_lite", "enhance_force",
    "greyscale", "brightness_threshold", "denoise_strength",
    "clahe_clip", "gamma",
    "roi_enabled", "roi_x", "roi_y", "roi_w", "roi_h",
    "roi_source_w", "roi_source_h",
    "bg_subtract_enabled", "bg_subtract_sensitivity",
)


def cache_key(config: dict, video_name: str, start: int, frames: int,
              model_name: str, imgsz: int) -> dict:
    """Deterministic key dict describing what the cache's YOLO output depends on."""
    sub = {k: config.get(k) for k in REBUILD_KEYS}
    sub["model"] = model_name
    sub["yolo_imgsz"] = imgsz
    sub["_video"] = video_name
    sub["_start"] = start
    sub["_frames"] = frames
    return sub


def _key_hash(key: dict) -> str:
    blob = json.dumps(key, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:10]


def cache_path_for(key: dict) -> Path:
    stem = f"{Path(key['_video']).stem}_s{key['_start']}_n{key['_frames']}_{_key_hash(key)}"
    return CACHE_DIR / f"{stem}.pkl"


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build_cache(
    video_path: str,
    config: dict,
    *,
    model_name: str = "yolo11x-pose",
    imgsz: int = 1280,
    start_frame: int = 0,
    max_frames: Optional[int] = None,
    out_path: Optional[Path] = None,
) -> Path:
    """Run the full CPU path once, capturing per-frame (detections, motion gray)."""
    import replay
    key = cache_key(config, Path(video_path).name, start_frame,
                    max_frames or 0, model_name, imgsz)
    out_path = Path(out_path) if out_path else cache_path_for(key)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    proc = replay._build_processor(config, model_name, imgsz, load_model=True)
    proc.tracker.reset()  # deterministic track IDs (global counter)

    captured: List[dict] = []

    def _capture(detections, gray, roi_x, roi_y, ow, oh):
        # PNG-encode the gray (grayscale, compresses well); keep detection
        # arrays as-is (copied — process() reuses buffers downstream).
        ok, png = cv2.imencode(".png", gray)
        captured.append({
            "dets": [(k.copy(), c.copy(), b.copy()) for (k, c, b) in detections],
            "gray_png": png.tobytes(),
            "roi_x": int(roi_x), "roi_y": int(roi_y),
            "ow": int(ow), "oh": int(oh),
        })

    proc._cache_capture = _capture

    tmp = tempfile.mkdtemp(prefix="wd_cachebuild_")
    proc.tracker.logger.start_session(tmp)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video_path}")
    if start_frame:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    t0 = time.time()
    processed = 0
    try:
        while True:
            if max_frames is not None and processed >= max_frames:
                break
            ok, frame = cap.read()
            if not ok:
                break
            proc.process(frame, need_preview=False, frame_number=processed)
            processed += 1
    finally:
        cap.release()
        proc.tracker.logger.close()
    build_s = time.time() - t0

    payload = {
        "format": CACHE_FORMAT,
        "key": key,
        "meta": {
            "video": Path(video_path).name,
            "model": model_name,
            "imgsz": imgsz,
            "start_frame": start_frame,
            "frames": processed,
            "build_seconds": round(build_s, 2),
        },
        "frames": captured,
    }
    with open(out_path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    size_mb = out_path.stat().st_size / 1e6
    print(f"built cache: {out_path.name}  ({processed} frames, "
          f"{build_s:.1f}s, {size_mb:.1f} MB)")
    return out_path


def load_cache(path: Path) -> dict:
    with open(path, "rb") as f:
        payload = pickle.load(f)
    if payload.get("format") != CACHE_FORMAT:
        raise ValueError(f"cache format {payload.get('format')} != {CACHE_FORMAT}")
    return payload


# --------------------------------------------------------------------------- #
# Replay from cache
# --------------------------------------------------------------------------- #
def replay_from_cache(
    cache: dict,
    config: dict,
    *,
    log_dir: Optional[str] = None,
    reuse_grays: bool = False,
    track_details: bool = False,
) -> Dict:
    """Re-run gate + motion + tracker from a cache, skipping YOLO.

    ``config`` supplies the tunable (post-YOLO) params; the YOLO front-end is
    already baked into the cache.  Returns the same summary dict (incl.
    ``per_frame``) as ``replay.replay_recording`` so it is directly comparable.

    ``reuse_grays``: memoise the PNG-decoded motion grays on the cache dict
    (``cache["_decoded"]``) so a search re-using one cache across many evals pays
    the ~12 ms/frame imdecode once.  Costs ~one extra decoded gray set in RAM.
    """
    import replay
    meta = cache["meta"]
    # No model needed — we only drive _track_detections.
    proc = replay._build_processor(
        config, meta["model"], meta["imgsz"], load_model=False)
    proc.tracker.reset()  # deterministic track IDs across build+replay / search

    tmp = log_dir or tempfile.mkdtemp(prefix="wd_cachereplay_")
    proc.tracker.logger.start_session(tmp)

    grays = None
    if reuse_grays:
        grays = cache.get("_decoded")
        if grays is None:
            grays = [cv2.imdecode(np.frombuffer(fr["gray_png"], np.uint8),
                                  cv2.IMREAD_GRAYSCALE) for fr in cache["frames"]]
            cache["_decoded"] = grays

    per_frame = []
    for i, fr in enumerate(cache["frames"]):
        gray = grays[i] if grays is not None else cv2.imdecode(
            np.frombuffer(fr["gray_png"], np.uint8), cv2.IMREAD_GRAYSCALE)
        proc._feed_motion_detectors(gray)
        dets = [(k, c, b) for (k, c, b) in fr["dets"]]
        timing: Dict[str, float] = {}
        tracks = proc._track_detections(
            dets, fr["roi_x"], fr["roi_y"], fr["ow"], fr["oh"], i, timing)
        per_frame.append(replay.per_frame_record(
            i, meta["start_frame"] + i, tracks, track_details))
    proc.tracker.logger.close()

    return replay._summary_from_log(
        tmp, meta["video"], meta["model"], meta["imgsz"],
        meta["start_frame"], len(cache["frames"]), per_frame)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _resolve(args):
    """Resolve config/video/window from --scenario or explicit flags."""
    import replay
    scenario = None
    if args.scenario:
        import scoring
        scenario = scoring.load_scenario(args.scenario)
        args.project = args.project or scenario["project"]
        if args.slot is None:
            args.slot = scenario["slot"]
        if not args.start:
            args.start = scenario["start"]
        if args.frames is None:
            args.frames = scenario["frames"]
    config = replay._latest_config(args.project) or {}
    video = args.video or replay._find_recording(args.project, args.slot)
    if not video:
        sys.exit(f"no recording found for {args.project} slot {args.slot}")
    model_name = args.model or config.get("model", "yolo11x-pose")
    imgsz = args.imgsz or int(config.get("yolo_imgsz", 1280))
    return scenario, config, str(video), model_name, imgsz


def main():
    ap = argparse.ArgumentParser(description="YOLO detect-pass cache (TUNING Phase B)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("build", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--scenario", default=None)
        p.add_argument("--project", default=None)
        p.add_argument("--slot", type=int, default=None)
        p.add_argument("--video", default=None)
        p.add_argument("--model", default=None)
        p.add_argument("--imgsz", type=int, default=None)
        p.add_argument("--start", type=int, default=0)
        p.add_argument("--frames", type=int, default=None)
        if name == "replay":
            p.add_argument("--score", action="store_true")
            p.add_argument("--timeline", default=None)
    args = ap.parse_args()

    scenario, config, video, model_name, imgsz = _resolve(args)
    key = cache_key(config, Path(video).name, args.start, args.frames or 0,
                    model_name, imgsz)
    path = cache_path_for(key)

    if args.cmd == "build":
        build_cache(video, config, model_name=model_name, imgsz=imgsz,
                    start_frame=args.start, max_frames=args.frames,
                    out_path=path)
        return

    # replay
    if not path.exists():
        sys.exit(f"no cache at {path} — run `build` first")
    cache = load_cache(path)
    t0 = time.time()
    summary = replay_from_cache(cache, config)
    dt = time.time() - t0
    per_frame = summary.pop("per_frame", [])
    print(json.dumps(summary, indent=2))
    print(f"\ncache replay: {len(per_frame)} frames in {dt:.2f}s "
          f"({1000*dt/max(1,len(per_frame)):.1f} ms/frame)")
    if args.timeline:
        Path(args.timeline).write_text(json.dumps(per_frame))
        print(f"wrote {args.timeline}")
    if args.score:
        if scenario is None:
            sys.exit("--score requires --scenario")
        import scoring
        print("\n=== SCORE ===")
        print(json.dumps(scoring.score_timeline(per_frame, scenario), indent=2))


if __name__ == "__main__":
    main()
