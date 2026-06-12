#!/usr/bin/env python3
"""Phase 2b build phase (GPU): gray stores + dets-only cells.

  graystore : one standard detect_cache build per scenario at CONF_FLOOR with
              the pinned model+imgsz (grays are model/imgsz-independent).
  cells     : per (scenario, model, imgsz) fast dets-only pass -- motion model
              disabled, tracking stubbed, PRE-dup-filter ROI-local detections
              + box confs captured. Resumable (skips existing cell pkls).

Usage:
  python p2b_build.py graystore [--only NAME ...]
  python p2b_build.py cells [--shard K --num-shards N] [--only NAME ...]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import p2b_common as C


def ensure_graystore(manifests) -> dict:
    """Build (or reuse) the per-scenario gray-store caches; update the index."""
    import detect_cache
    index = {}
    if C.GRAYSTORE_INDEX.exists():
        index = json.loads(C.GRAYSTORE_INDEX.read_text())
    for m in manifests:
        name = m["name"]
        path = C.graystore_cache_path(m)
        if path.exists():
            index[name] = str(path)
            C.heartbeat("build_graystore", scenario=name, status="exists",
                        path=path.name)
            continue
        cfg = C.graystore_config(m["_config"])
        t0 = time.time()
        detect_cache.build_cache(
            m["_video"], cfg,
            model_name=cfg.get("model", "yolo11x-pose"),
            imgsz=int(cfg.get("yolo_imgsz", 1280)),
            start_frame=int(m["start"]), max_frames=int(m["frames"]),
            out_path=path)
        index[name] = str(path)
        C.heartbeat("build_graystore", scenario=name, status="built",
                    secs=round(time.time() - t0, 1), path=path.name)
        C.GRAYSTORE_INDEX.write_text(json.dumps(index, indent=1))
    C.GRAYSTORE_INDEX.write_text(json.dumps(index, indent=1))
    return index


def build_det_cell(manifest: dict, model: str, imgsz: int,
                   long_side: float) -> dict:
    """Fast dets-only pass: full front-end + YOLO, no motion, no tracking."""
    import cv2
    import numpy as np
    import replay
    from pipeline import FrameProcessor

    cfg = dict(manifest["_config"])
    cfg["confidence"] = C.CONF_FLOOR
    proc = replay._build_processor(cfg, model, imgsz, load_model=True)
    proc.motion_model = None  # kills the per-frame MOG2 feed (views go None)
    proc.tracker.reset()
    bbox_key = FrameProcessor._bbox_conf_key

    frames_data = []

    orig_dup = proc._filter_duplicate_detections  # noqa: F841 (fidelity note)

    def patched_dup(dets, effective_person_height=None):
        confs = proc._last_box_confs
        frames_data.append({
            "dets": [(k.copy(), c.copy(), b.copy()) for (k, c, b) in dets],
            "box_confs": [confs.get(bbox_key(b)) for (_, _, b) in dets],
        })
        return []  # skip downstream work; scoring re-runs the real dup-filter

    def track_stub(dets, roi_x, roi_y, ow, oh, frame_number, timing):
        fr = frames_data[-1]
        fr["roi_x"], fr["roi_y"] = int(roi_x), int(roi_y)
        fr["ow"], fr["oh"] = int(ow), int(oh)
        return []

    proc._filter_duplicate_detections = patched_dup
    proc._track_detections = track_stub

    cap = cv2.VideoCapture(manifest["_video"])
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {manifest['_video']}")
    start = int(manifest["start"])
    n_frames = int(manifest["frames"])
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    yolo_ms = []
    t0 = time.time()
    processed = 0
    try:
        while processed < n_frames:
            ok, frame = cap.read()
            if not ok:
                break
            _, _, timing, _ = proc.process(
                frame, need_preview=False, frame_number=processed)
            yolo_ms.append(timing.get("yolo", 0.0))
            processed += 1
    finally:
        cap.release()
    build_s = time.time() - t0

    if len(frames_data) != processed:
        raise RuntimeError(
            f"capture count {len(frames_data)} != processed {processed}")
    n_dets = sum(len(fr["dets"]) for fr in frames_data)
    missing_conf = sum(
        1 for fr in frames_data for bc in fr["box_confs"] if bc is None)

    return {
        "format": C.CELL_FORMAT,
        "scenario": manifest["name"],
        "model": model,
        "imgsz": imgsz,
        "conf_floor": C.CONF_FLOOR,
        "start": start,
        "video": Path(manifest["_video"]).name,
        "n_frames": processed,
        "roi_long_side": long_side,
        "net_height": round(C.net_height(manifest["_config"], imgsz, long_side), 1),
        "n_dets": n_dets,
        "n_dets_missing_box_conf": missing_conf,
        "yolo_ms_median": round(float(np.median(yolo_ms)), 1) if yolo_ms else None,
        "yolo_ms_p90": round(float(np.percentile(yolo_ms, 90)), 1) if yolo_ms else None,
        "build_s": round(build_s, 1),
        "frames_data": frames_data,
    }


def run_cells(manifests, shard: int, num_shards: int) -> None:
    import numpy as np  # noqa: F401 (worker import warm-up)
    todo = []
    for si, m in enumerate(manifests):
        if si % num_shards != shard:
            continue
        long_side = C.probe_long_side(m["_config"], m["_video"])
        for model in C.MODELS:
            for imgsz in C.cell_imgsz_list(m["_config"], long_side):
                todo.append((m, model, imgsz, long_side))
    done = 0
    hb = f"build_cells_shard{shard}"
    C.heartbeat(hb, status="start", n_cells=len(todo))
    for m, model, imgsz, long_side in todo:
        C.arm_stall_dump(900)
        path = C.cell_path(m["name"], model, imgsz)
        if path.exists():
            done += 1
            continue
        t0 = time.time()
        payload = build_det_cell(m, model, imgsz, long_side)
        C.save_cell(path, payload)
        done += 1
        C.heartbeat(hb, scenario=m["name"], model=model, imgsz=imgsz,
                    frames=payload["n_frames"], dets=payload["n_dets"],
                    yolo_ms=payload["yolo_ms_median"],
                    secs=round(time.time() - t0, 1),
                    done=done, total=len(todo))
    C.heartbeat(hb, status="done", n_cells=len(todo))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("graystore", "cells"))
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--num-shards", type=int, default=1)
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    manifests = C.load_scenarios(only=args.only)
    if args.phase == "graystore":
        ensure_graystore(manifests)
    else:
        run_cells(manifests, args.shard, args.num_shards)


if __name__ == "__main__":
    main()
