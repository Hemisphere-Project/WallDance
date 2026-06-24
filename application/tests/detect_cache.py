#!/usr/bin/env python3
"""YOLO detect-pass cache (TUNING.md Phase B — fast iteration keystone).

YOLO (yolo11x-pose @1280 ≈ 65 ms/frame) dominates a replay; the gate, motion
and tracker tuning we actually search over does NOT change YOLO's output.  So:

  B1 build_cache_gpu:  run the GPU/TRT show path ONCE over a window, capturing
                   the letterbox-space detections + the _TrackerSpace + the
                   per-frame motion gray (via FrameProcessor._cache_capture_gpu).
  B2 replay_from_cache_gpu:  rebuild only a tracker/gate/motion processor (no
                   model loaded) and re-run _post_yolo_chain via
                   FrameProcessor.replay_gpu_cached, skipping YOLO → an order of
                   magnitude faster, making a search interactive.

Track P (2026-06): the harness runs the GPU+TRT show path (the CPU detect-cache
was removed).  Cache replay equals a direct `replay.py --trt` run (proven by
tests/test_gpu_cache_fidelity.py).  Front-end params (model, imgsz, confidence, enhance,
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
              model_name: str, imgsz: int, path: str = "cpu") -> dict:
    """Deterministic key dict describing what the cache's YOLO output depends on.

    ``path`` distinguishes the CPU detect-pass from the GPU/TRT one (Track P
    Stage 1) — they produce different detections.  Default "cpu" omits the key
    so existing CPU cache hashes are unchanged."""
    sub = {k: config.get(k) for k in REBUILD_KEYS}
    sub["model"] = model_name
    sub["yolo_imgsz"] = imgsz
    sub["_video"] = video_name
    sub["_start"] = start
    sub["_frames"] = frames
    if path != "cpu":
        sub["_path"] = path
    return sub


def _key_hash(key: dict) -> str:
    blob = json.dumps(key, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:10]


def cache_path_for(key: dict) -> Path:
    stem = f"{Path(key['_video']).stem}_s{key['_start']}_n{key['_frames']}_{_key_hash(key)}"
    return CACHE_DIR / f"{stem}.pkl"


def load_cache(path: Path) -> dict:
    with open(path, "rb") as f:
        payload = pickle.load(f)
    if payload.get("format") != CACHE_FORMAT:
        raise ValueError(f"cache format {payload.get('format')} != {CACHE_FORMAT}")
    return payload


# --------------------------------------------------------------------------- #
# GPU/TRT detect-pass cache (Track P Stage 1) — the show-path equivalent of the
# CPU functions above.  Captures letterbox-space detections + the _TrackerSpace
# + motion gray from the GPU front-end, replays through _post_yolo_chain.
# --------------------------------------------------------------------------- #
def build_cache_gpu(
    video_path: str,
    config: dict,
    *,
    model_name: str = "yolo11x-pose",
    imgsz: int = 1280,
    start_frame: int = 0,
    max_frames: Optional[int] = None,
    out_path: Optional[Path] = None,
    use_trt: bool = True,
) -> Path:
    """Run the GPU/TRT path once, capturing per-frame the letterbox-space
    detections + tracker space + motion gray (the show-path detect-pass)."""
    import replay
    path_tag = "trt" if use_trt else "gpu"
    key = cache_key(config, Path(video_path).name, start_frame,
                    max_frames or 0, model_name, imgsz, path=path_tag)
    out_path = Path(out_path) if out_path else cache_path_for(key)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    proc = replay._build_processor(config, model_name, imgsz, load_model=True,
                                   use_gpu_path=True, use_trt=use_trt)
    proc.tracker.reset()  # deterministic track IDs (global counter)

    captured: List[dict] = []

    def _capture(dets, space, gray, ow, oh):
        gray_png = None
        if gray is not None:
            ok, png = cv2.imencode(".png", gray)
            gray_png = png.tobytes()
        captured.append({
            "dets": [(k.copy(), c.copy(), b.copy()) for (k, c, b) in dets],
            "gray_png": gray_png,
            "space": {
                "person_height": int(space.person_height),
                "scale": float(space.scale),
                "pad_x": float(space.pad_x),
                "pad_y": float(space.pad_y),
                "roi_x": int(space.roi_x),
                "roi_y": int(space.roi_y),
                "frame_width": int(space.frame_width),
            },
            "ow": int(ow), "oh": int(oh),
        })

    proc._cache_capture_gpu = _capture

    tmp = tempfile.mkdtemp(prefix="wd_gpucachebuild_")
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
            "path": path_tag,
        },
        "frames": captured,
    }
    with open(out_path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    size_mb = out_path.stat().st_size / 1e6
    print(f"built GPU cache ({path_tag}): {out_path.name}  ({processed} frames, "
          f"{build_s:.1f}s, {size_mb:.1f} MB)")
    return out_path


def replay_from_cache_gpu(
    cache: dict,
    config: dict,
    *,
    log_dir: Optional[str] = None,
    reuse_grays: bool = False,
    track_details: bool = False,
    frame_skip: int = 1,
) -> Dict:
    """Re-run the GPU post-YOLO chain from a TRT cache, skipping YOLO.  Mirrors
    ``replay_from_cache`` but drives ``proc.replay_gpu_cached`` (letterbox space)
    instead of ``_track_detections`` (full-frame CPU)."""
    import replay
    meta = cache["meta"]
    proc = replay._build_processor(
        config, meta["model"], meta["imgsz"], load_model=False)
    proc.tracker.reset()  # deterministic track IDs across build+replay / search

    tmp = log_dir or tempfile.mkdtemp(prefix="wd_gpucachereplay_")
    proc.tracker.logger.start_session(tmp)

    def _decode(fr):
        if fr["gray_png"] is None:
            return None
        return cv2.imdecode(np.frombuffer(fr["gray_png"], np.uint8),
                            cv2.IMREAD_GRAYSCALE)

    grays = None
    if reuse_grays:
        grays = cache.get("_decoded")
        if grays is None:
            grays = [_decode(fr) for fr in cache["frames"]]
            cache["_decoded"] = grays

    stride = max(1, int(frame_skip))
    per_frame = []
    kept = 0
    for i, fr in enumerate(cache["frames"]):
        if stride > 1 and (i % stride) != 0:
            continue
        gray = grays[i] if grays is not None else _decode(fr)
        dets = [(k, c, b) for (k, c, b) in fr["dets"]]
        timing: Dict[str, float] = {}
        tracks = proc.replay_gpu_cached(
            dets, fr["space"], gray, fr["ow"], fr["oh"], kept, timing)
        per_frame.append(replay.per_frame_record(
            kept, meta["start_frame"] + i, tracks, track_details))
        kept += 1
    proc.tracker.logger.close()

    return replay._summary_from_log(
        tmp, meta["video"], meta["model"], meta["imgsz"],
        meta["start_frame"], kept, per_frame)


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
    if scenario is not None:
        config = replay.scenario_config(scenario)
    else:
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
                    model_name, imgsz, path="trt")
    path = cache_path_for(key)

    if args.cmd == "build":
        build_cache_gpu(video, config, model_name=model_name, imgsz=imgsz,
                        start_frame=args.start, max_frames=args.frames,
                        out_path=path)
        return

    # replay
    if not path.exists():
        sys.exit(f"no cache at {path} — run `build` first")
    cache = load_cache(path)
    t0 = time.time()
    summary = replay_from_cache_gpu(cache, config)
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
