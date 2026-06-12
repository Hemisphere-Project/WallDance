#!/usr/bin/env python3
"""Phase 2b pre-check (ROADMAP 4.2 Phase 2b deliverable (b) prerequisite).

Runs yolo11x-pose and yolo26x-pose through the REAL extraction path
(replay._build_processor -> proc.process -> _extract_detections capture hook)
on one scene, and compares decode/conf semantics:

  - keypoint tensor shape (must be 17x(2+1) COCO pose for the tracker)
  - box-conf coverage via proc._last_box_confs (the tau filter + 5a seed input)
  - box-conf distributions (NMS-free yolo26 may calibrate conf differently)
  - per-frame det counts + IoU-matched conf pairing 11 vs 26
  - per-frame YOLO latency on this rig (.pt fp32, the quality-surface path)

Usage:  .venv python tmp_analysis/phase2b/precheck_yolo26.py
Writes: tmp_analysis/phase2b/precheck_yolo26.json + stdout report (ASCII only).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "application" / "tests"
SRC = REPO / "application" / "src"
for p in (TESTS, SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import cv2  # noqa: E402

import replay  # noqa: E402
import scoring  # noqa: E402
from pipeline import FrameProcessor  # noqa: E402

SCENARIO = TESTS / "scenarios" / "hangar-floor.json"
N_FRAMES = 100
CONF_FLOOR = 0.05
MODELS = ("yolo11x-pose", "yolo26x-pose")


def run_model(model_name: str, config: dict, video: str, imgsz: int,
              start: int, n_frames: int) -> dict:
    cfg = dict(config)
    cfg["confidence"] = CONF_FLOOR
    proc = replay._build_processor(cfg, model_name, imgsz, load_model=True)
    proc.tracker.reset()

    frames_out = []

    def _capture(detections, gray, roi_x, roi_y, ow, oh):
        confs = proc._last_box_confs
        dets = []
        for (k, c, b) in detections:
            bc = confs.get(FrameProcessor._bbox_conf_key(b))
            dets.append({
                "kp_shape": list(np.asarray(k).shape),
                "kpconf_shape": list(np.asarray(c).shape),
                "bbox": [float(x) for x in b],
                "box_conf": None if bc is None else float(bc),
                "kp_conf_mean": float(np.mean(c)),
            })
        frames_out.append(dets)

    proc._cache_capture = _capture
    import tempfile
    proc.tracker.logger.start_session(tempfile.mkdtemp(prefix="wd_precheck_"))

    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video}")
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    yolo_ms = []
    total_ms = []
    t_wall = time.time()
    for i in range(n_frames):
        ok, frame = cap.read()
        if not ok:
            break
        _, _, timing, lat = proc.process(frame, need_preview=False, frame_number=i)
        yolo_ms.append(timing.get("yolo", 0.0))
        total_ms.append(lat)
    cap.release()
    proc.tracker.logger.close()
    wall = time.time() - t_wall

    all_dets = [d for fr in frames_out for d in fr]
    box_confs = [d["box_conf"] for d in all_dets if d["box_conf"] is not None]
    heights = [d["bbox"][3] for d in all_dets]
    kp_shapes = sorted({tuple(d["kp_shape"]) for d in all_dets})

    def pct(a, q):
        return float(np.percentile(a, q)) if a else None

    return {
        "model": model_name,
        "frames": len(frames_out),
        "wall_s": round(wall, 1),
        "yolo_ms_median": round(float(np.median(yolo_ms)), 1),
        "yolo_ms_p90": round(pct(yolo_ms, 90), 1),
        "total_ms_median": round(float(np.median(total_ms)), 1),
        "dets_total": len(all_dets),
        "dets_per_frame_mean": round(len(all_dets) / max(1, len(frames_out)), 3),
        "kp_shapes": [list(s) for s in kp_shapes],
        "box_conf_coverage": round(len(box_confs) / max(1, len(all_dets)), 4),
        "box_conf": {
            "min": pct(box_confs, 0), "p05": pct(box_confs, 5),
            "p25": pct(box_confs, 25), "p50": pct(box_confs, 50),
            "p75": pct(box_confs, 75), "p95": pct(box_confs, 95),
            "max": pct(box_confs, 100),
        },
        "kp_conf_mean_overall": round(float(np.mean(
            [d["kp_conf_mean"] for d in all_dets])), 4) if all_dets else None,
        "height_p50": pct(heights, 50),
        "counts_at_tau": {
            str(t): sum(1 for c in box_confs if c >= t)
            for t in (0.05, 0.15, 0.25, 0.35, 0.50)
        },
        "_frames": frames_out,
    }


def iou_xywh(a, b):
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax1 + aw, bx1 + bw), min(ay1 + ah, by1 + bh)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def match_models(fr11, fr26):
    """Per-frame greedy IoU>=0.5 pairing; returns matched conf pairs + counts."""
    pairs = []
    only11 = only26 = 0
    for d11, d26 in zip(fr11, fr26):
        used = set()
        for a in d11:
            best, bj = 0.0, None
            for j, b in enumerate(d26):
                if j in used:
                    continue
                v = iou_xywh(a["bbox"], b["bbox"])
                if v > best:
                    best, bj = v, j
            if best >= 0.5 and a["box_conf"] is not None \
                    and d26[bj]["box_conf"] is not None:
                used.add(bj)
                pairs.append((a["box_conf"], d26[bj]["box_conf"]))
            else:
                only11 += 1
        only26 += len(d26) - len(used)
    return pairs, only11, only26


def main():
    manifest = scoring.load_scenario(str(SCENARIO))
    config = replay.scenario_config(manifest)
    video = replay._find_recording(manifest["project"], manifest["slot"])
    if video is None:
        sys.exit("recording not found")
    replay.check_fingerprint(manifest, video)
    imgsz = int(config.get("yolo_imgsz", 1280))
    start = int(manifest["start"])

    print(f"scene={manifest['name']} imgsz={imgsz} start={start} "
          f"n={N_FRAMES} conf_floor={CONF_FLOOR}")
    results = {}
    for m in MODELS:
        print(f"--- {m} ---")
        r = run_model(m, config, str(video), imgsz, start, N_FRAMES)
        results[m] = r
        view = {k: v for k, v in r.items() if not k.startswith("_")}
        print(json.dumps(view, indent=2))

    pairs, only11, only26 = match_models(
        results[MODELS[0]]["_frames"], results[MODELS[1]]["_frames"])
    if pairs:
        a = np.array(pairs)
        cmp_block = {
            "matched_pairs": len(pairs),
            "only_in_11x": only11,
            "only_in_26x": only26,
            "conf_corr": round(float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]), 4),
            "conf_mean_11x": round(float(a[:, 0].mean()), 4),
            "conf_mean_26x": round(float(a[:, 1].mean()), 4),
            "conf_delta_26_minus_11_p50": round(
                float(np.median(a[:, 1] - a[:, 0])), 4),
        }
    else:
        cmp_block = {"matched_pairs": 0, "only_in_11x": only11,
                     "only_in_26x": only26}
    print("--- 11x vs 26x matched-box comparison ---")
    print(json.dumps(cmp_block, indent=2))

    out = {
        "scene": manifest["name"], "imgsz": imgsz, "start": start,
        "n_frames": N_FRAMES, "conf_floor": CONF_FLOOR,
        "models": {m: {k: v for k, v in r.items() if not k.startswith("_")}
                   for m, r in results.items()},
        "comparison": cmp_block,
    }
    out_path = Path(__file__).with_suffix(".json")
    out_path.write_text(json.dumps(out, indent=2))
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
