"""Dump per-frame reported tracks (keypoints/conf/centroid/fss) from the REAL
pipeline (GPU path, PyTorch weights, no TRT) over a recording window.

Feeds synth_markers.py (simulated wrist/ankle markers on real pose sequences).
Scratch only.  Run from /data/WallDance/application:
  uv run --no-sync python <scratch>/pose_dump.py --scenario hangar-aerial \
      --start 0 --frames 5088 --out <scratch>/poses/aerial.npz
  uv run --no-sync python <scratch>/pose_dump.py --project tango-H --slot 8 ...
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import tempfile
from pathlib import Path

APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "tests"))
import replay  # noqa: E402  (bootstraps LD_LIBRARY_PATH + sys.path)

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario")
    ap.add_argument("--project")
    ap.add_argument("--slot", type=int)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--frames", type=int, default=1000)
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.scenario:
        man = json.loads((APP / "tests" / "scenarios" / f"{args.scenario}.json").read_text())
        cfg = replay.scenario_config(man)
        video = replay._find_recording(man["project"], man["slot"])
    else:
        cfg = replay._latest_config(args.project)
        video = replay._find_recording(args.project, args.slot)
    imgsz = args.imgsz or int(cfg.get("yolo_imgsz", 1280))
    model = cfg.get("model", "yolo11x-pose").replace(".pt", "")
    proc = replay._build_processor(cfg, model, imgsz, use_gpu_path=True, use_trt=False)
    proc.tracker.reset()
    proc.tracker.logger.start_session(tempfile.mkdtemp(prefix="wd_posedump_"))
    cap = cv2.VideoCapture(str(video))
    if args.start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, args.start)
    rows = []
    n = 0
    while n < args.frames:
        ok, frame = cap.read()
        if not ok:
            break
        tracks, _e, _t, _l = proc.process(frame, need_preview=False, frame_number=n)
        for t in tracks:
            rows.append({
                "f": args.start + n, "id": int(t.track_id),
                "kpts": np.asarray(t.keypoints, np.float32),
                "conf": np.asarray(t.confidence, np.float32),
                "bbox": np.asarray(t.bbox, np.float32),
                "fss": int(t.frames_since_skeleton if t.frames_since_skeleton is not None else -1),
                "sc": np.asarray(t.smoothed_centroid, np.float32),
                "craw": (np.asarray(t.centroid_raw, np.float32)
                         if t.centroid_raw is not None else None),
            })
        n += 1
        if n % 500 == 0:
            print(f"{n} frames, {len(rows)} track-rows", flush=True)
    cap.release()
    proc.tracker.logger.close()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "wb") as f:
        pickle.dump({"video": str(video), "imgsz": imgsz, "cfg_person_h": cfg.get("person_height_px"),
                     "frames": n, "rows": rows}, f)
    print(f"done: {n} frames, {len(rows)} rows -> {args.out}")


if __name__ == "__main__":
    main()
