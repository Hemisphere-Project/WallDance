#!/usr/bin/env python3
"""Phase 2b TRT FP16 spot-check (ROADMAP 4.2 Phase 2b: "spot-check one scene
on TRT FP16 for conf drift before trusting the transfer").

Runs hangar-floor through the real extraction path twice with yolo11x-pose
@960: (a) .pt fp32 (the quality-surface path used in the benchmark) and
(b) the TensorRT FP16 engine (the show path). Compares matched-box (IoU>=0.5)
confidences. Small drift => the .pt-measured tau/quality transfer.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

import p2b_common as C

import cv2  # noqa: E402
import replay  # noqa: E402
from pipeline import FrameProcessor  # noqa: E402

SCENE = "hangar-floor"
N_FRAMES = 100
CONF_FLOOR = 0.05


def run(proc, video, start, n_frames):
    frames_out = []

    def _capture(detections, gray, roi_x, roi_y, ow, oh):
        confs = proc._last_box_confs
        frames_out.append([
            {"bbox": [float(x) for x in b],
             "box_conf": confs.get(FrameProcessor._bbox_conf_key(b))}
            for (_, _, b) in detections])

    proc._cache_capture = _capture
    proc.tracker.reset()
    proc.tracker.logger.start_session(tempfile.mkdtemp(prefix="wd_trtchk_"))
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    yolo_ms = []
    for i in range(n_frames):
        ok, frame = cap.read()
        if not ok:
            break
        _, _, timing, _ = proc.process(frame, need_preview=False, frame_number=i)
        yolo_ms.append(timing.get("yolo", 0.0))
    cap.release()
    proc.tracker.logger.close()
    return frames_out, float(np.median(yolo_ms))


def main():
    from p2b_equiv import frame_agreement  # noqa: F401 (reuse if needed)
    from precheck_yolo26 import iou_xywh

    m = C.load_scenarios(only=[SCENE])[0]
    config = m["_config"]
    imgsz = int(config.get("yolo_imgsz", 960))
    cfg = dict(config)
    cfg["confidence"] = CONF_FLOOR

    # (a) .pt fp32 — the benchmark path
    proc_pt = replay._build_processor(cfg, "yolo11x-pose", imgsz, load_model=True)
    fr_pt, ms_pt = run(proc_pt, m["_video"], int(m["start"]), N_FRAMES)

    # (b) TRT FP16 engine — the show path
    from ultralytics import YOLO
    eng_path = C.REPO / "models" / f"yolo11x-pose_{imgsz}.engine"
    if not eng_path.exists():
        sys.exit(f"engine missing: {eng_path}")
    proc_trt = replay._build_processor(cfg, "yolo11x-pose", imgsz,
                                       load_model=False)
    proc_trt.model = YOLO(str(eng_path), task="pose")
    fr_trt, ms_trt = run(proc_trt, m["_video"], int(m["start"]), N_FRAMES)

    pairs = []
    only_pt = only_trt = 0
    for da, db in zip(fr_pt, fr_trt):
        used = set()
        for a in da:
            best, bj = 0.0, None
            for j, b in enumerate(db):
                if j in used:
                    continue
                v = iou_xywh(a["bbox"], b["bbox"])
                if v > best:
                    best, bj = v, j
            if best >= 0.5 and a["box_conf"] is not None \
                    and db[bj]["box_conf"] is not None:
                used.add(bj)
                pairs.append((a["box_conf"], db[bj]["box_conf"]))
            else:
                only_pt += 1
        only_trt += len(db) - len(used)

    a = np.array(pairs)
    rep = {
        "scene": SCENE, "imgsz": imgsz, "n_frames": N_FRAMES,
        "dets_pt": int(sum(len(f) for f in fr_pt)),
        "dets_trt": int(sum(len(f) for f in fr_trt)),
        "matched": len(pairs), "only_pt": only_pt, "only_trt": only_trt,
        "yolo_ms_pt_fp32": round(ms_pt, 1),
        "yolo_ms_trt_fp16": round(ms_trt, 1),
        "conf_delta_trt_minus_pt": {
            "median": round(float(np.median(a[:, 1] - a[:, 0])), 4),
            "mean": round(float(np.mean(a[:, 1] - a[:, 0])), 4),
            "p95_abs": round(float(np.percentile(np.abs(a[:, 1] - a[:, 0]), 95)), 4),
            "max_abs": round(float(np.max(np.abs(a[:, 1] - a[:, 0]))), 4),
        } if len(pairs) else None,
    }
    print(json.dumps(rep, indent=2))
    (C.PHASE_DIR / "trt_spotcheck.json").write_text(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
