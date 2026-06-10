"""Corpus survey (Layer 1): scene + YOLO characterization of every annotated slot.

Per CORPUS_NOTES.md slot, on a chosen window:
  * calib1-style scene report (SceneCalibrator): brightness, clip, uniformity,
    focus, temporal noise sigma, empirical var x scale sweep (raw-gray feed).
  * low-threshold YOLO sampling pass (yolo11x-pose @1280, conf floor 0.05) on
    ~32 evenly sampled frames, raw AND auto-enhanced (per-scene auto gamma +
    CLAHE 2.5), recording every candidate box (conf, h, kp stats) so
    confidence operating curves vs annotated N can be computed offline.
  * a brightened 4-frame montage jpg for visual verification.

Run with the application venv from the application/ directory:
    .venv/Scripts/python.exe ../tmp_analysis/corpus_survey.py [--only PROJ:SLOT]
Outputs into tmp_analysis/survey/<project>_slot<N>.json + montage jpg.
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

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APP = REPO / "application"
SRC = APP / "src"
for p in (str(SRC), str(APP), str(APP / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

PROJECTS = REPO / "projects"
OUT = HERE / "survey"
OUT.mkdir(parents=True, exist_ok=True)

MODEL_PATH = REPO / "models" / "yolo11x-pose.pt"
IMGSZ = 1280
CONF_FLOOR = 0.05
N_SAMPLES = 32
NOISE_BLOCK = 90  # frames fed to SceneCalibrator
THRESHOLDS = [0.15, 0.25, 0.35, 0.50, 0.65]
KP_VIS = 0.30  # keypoint visibility threshold (KEYPOINT_CONFIDENCE)

# project, slot, start, window_len (None = to end), expected N (None = varying),
# expected range (lo, hi) when "up to", note
SLOTS = [
    ("0-TEST-verydark", 1, 0, None, 1, None, "walker whole floor, very dark"),
    ("0-TEST-verydark", 2, 0, None, 1, None, "very dark"),
    ("0-TEST-verydark", 3, 0, None, 2, None, "2 people"),
    ("0-TEST-verydark", 4, 0, None, None, (1, 4), "up to 4, enter/leave"),
    ("0-TEST-verydark", 5, 0, None, None, (1, 4), "up to 4, enter/leave"),
    ("0-TEST-verydark", 6, 2000, 600, None, (1, 4), "up to 4, enter/leave"),
    ("0-TEST-phones", 1, 200, 600, 4, None, "4 dancers wall, day, phone"),
    ("0-TEST-phones", 2, 200, 600, 3, None, "3 dancers far/small, poor"),
    ("0-TEST-phones", 5, 200, 600, 6, None, "6 dancers, moving camera"),
    ("0-TEST-phones", 6, 100, 600, 6, None, "6 dancers, moving cam, floor shot"),
    ("0-TEST-cantine", 1, 0, None, None, (0, 2), "close IDS test, dinner room"),
    ("1_TANGO_HANGAR-texturedbg", 1, 0, None, 1, None, "test walker, textured bg"),
    ("1_TANGO_HANGAR-texturedbg", 3, 800, 600, 1, None, "floor dancer static start"),
    ("1_TANGO_HANGAR-texturedbg", 4, 2500, 600, 1, None, "wall-hanged dancer"),
    ("1_TANGO_HANGAR-texturedbg", 5, 1000, 600, 2, None, "duo moving together"),
    ("2_TANGO_HANGAR-whitebg", 6, 700, 600, 1, None, "floor dancer, day1 evening"),
    ("2_TANGO_HANGAR-whitebg", 7, 300, 600, 1, None, "wall-hanged, day1 evening"),
    ("2_TANGO_HANGAR-whitebg", 8, 500, 600, 1, None, "floor dancer"),
    ("2_TANGO_HANGAR-whitebg", 9, 600, 600, 1, None, "wall-hanged"),
    ("2_TANGO_HANGAR-whitebg", 1, 0, None, 1, None, "test walker day2"),
    ("2_TANGO_HANGAR-whitebg", 4, 0, None, 1, None, "test walker day2 pm"),
    ("3_TANGO_HANGAR-whitebg2", 1, 0, None, 1, None, "test walker"),
    ("3_TANGO_HANGAR-whitebg2", 2, 2000, 600, 1, None, "floor dancer"),
    ("3_TANGO_HANGAR-whitebg2", 3, 1500, 600, 1, None, "REGRESSION slot3 (golden window 1500+300)"),
    ("3_TANGO_HANGAR-whitebg2", 4, 1500, 600, 1, None, "REGRESSION slot4 aerial (golden window)"),
    ("3_TANGO_HANGAR-whitebg2", 5, 2000, 600, 1, None, "wall-hanged"),
    ("4_TANGO_HANGAR-whitebg3", 1, 800, 600, 1, None, "floor dancer"),
    ("4_TANGO_HANGAR-whitebg3", 2, 100, 600, 2, None, "duo together/separate"),
    ("4_TANGO_HANGAR-whitebg3", 3, 0, None, None, (4, 5), "4-5 test walkers"),
    ("5_TANGO_HANGAR-testflou", 4, 800, 600, 2, None, "blurry, 2 standing"),
    ("5_TANGO_HANGAR-testflou", 5, 200, 600, 1, None, "blurry walker, 7.4fps"),
    ("5_TANGO_HANGAR-testflou", 6, 900, 600, 1, None, "blurry, running fast"),
    ("6_TANGO_TOGO-night", 1, 0, None, 1, None, "building facade, night"),
    ("6_TANGO_TOGO-night", 2, 0, None, 1, None, "wall-hanged, night"),
    ("7_TANGO_TOGO-day", 5, 1000, 600, 2, None, "1 walking + 1 sitting balcony, day"),
    ("7_TANGO_TOGO-day", 6, 0, None, 2, None, "1 static shadow + 1 balcony"),
    ("7_TANGO_TOGO-day", 8, 500, 600, 2, None, "1 static shadow + 1 balcony"),
    ("7_TANGO_TOGO-day", 9, 2500, 600, 2, None, "1 walking + 1 balcony"),
]


def find_recording(project: str, slot: int):
    rec = PROJECTS / project / "recordings"
    hits = sorted(rec.glob(f"slot_{slot}_*.avi")) + sorted(rec.glob(f"slot_{slot}_*.mp4")) \
        + sorted(rec.glob(f"slot_{slot}_*.mov"))
    return hits[0] if hits else None


def auto_gamma(median_luma: float, target: float = 110.0) -> float:
    med = max(float(median_luma), 1.0)
    if med >= target:
        return 1.0
    g = math.log(med / 255.0) / math.log(target / 255.0)
    return float(np.clip(g, 0.8, 2.2))


def apply_gamma(gray: np.ndarray, gamma: float) -> np.ndarray:
    if abs(gamma - 1.0) < 1e-3:
        return gray
    lut = (np.power(np.arange(256) / 255.0, 1.0 / gamma) * 255.0).astype(np.uint8)
    return cv2.LUT(gray, lut)


def enhance_for_yolo(frame: np.ndarray, gamma: float, clahe) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    g = apply_gamma(gray, gamma)
    g = clahe.apply(g)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def brighten_for_view(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    g = apply_gamma(gray, 2.2)
    g = cv2.createCLAHE(3.0, (8, 8)).apply(g)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def yolo_pass(model, frame: np.ndarray):
    """All candidate detections at the conf floor: [conf, h, w, kp_vis, kp_conf_vis, cx, cy]."""
    r = model.predict(frame, imgsz=IMGSZ, conf=CONF_FLOOR, iou=0.45,
                      verbose=False, half=False)[0]
    dets = []
    if r.boxes is None or len(r.boxes) == 0:
        return dets
    xywh = r.boxes.xywh.cpu().numpy()
    confs = r.boxes.conf.cpu().numpy()
    kpc = None
    if r.keypoints is not None and r.keypoints.conf is not None:
        kpc = r.keypoints.conf.cpu().numpy()
    for i in range(len(confs)):
        kp_vis, kp_conf_vis = 0, 0.0
        if kpc is not None and i < len(kpc):
            vis = kpc[i] >= KP_VIS
            kp_vis = int(vis.sum())
            kp_conf_vis = float(kpc[i][vis].mean()) if kp_vis else 0.0
        cx, cy, w, h = (float(x) for x in xywh[i])
        dets.append([round(float(confs[i]), 4), round(h, 1), round(w, 1),
                     kp_vis, round(kp_conf_vis, 4), round(cx, 1), round(cy, 1)])
    return dets


def scene_block(cap, start: int, model_unused, fps_hint: float):
    """Feed NOISE_BLOCK consecutive raw-gray frames to SceneCalibrator."""
    from calibration import SceneCalibrator
    cal = SceneCalibrator(window_frames=NOISE_BLOCK)
    cal.start()
    cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    n = 0
    while n < NOISE_BLOCK:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cal.feed(gray, [], fps_hint, time.time(),
                 brightness=float(gray.mean()), report_frame=frame)
        n += 1
    if n < 10:
        return None
    r = cal.compute()
    return {
        "frames": r.frames,
        "noise_sigma": round(r.noise_sigma, 3),
        "var_threshold": r.var_threshold,
        "mog2_scale": r.mog2_scale,
        "var_fp_rate": round(r.var_fp_rate, 6),
        "var_saturated": r.var_saturated,
        "clahe_value": r.clahe_value,
        "brightness_mean": round(r.brightness_mean, 2),
        "brightness_cv": round(r.brightness_cv, 4),
        "exposure_stable": r.exposure_stable,
        "focus_score": round(r.focus_score, 1),
        "clip_high_pct": round(r.clip_high_pct, 3),
        "clip_low_pct": round(r.clip_low_pct, 3),
        "uniformity": round(r.uniformity, 4),
        "dark_tile": list(r.dark_tile) if r.dark_tile else None,
    }


def survey_slot(model, project, slot, start, length, n_expected, n_range, note):
    video = find_recording(project, slot)
    if video is None:
        return {"project": project, "slot": slot, "error": "recording not found"}
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return {"project": project, "slot": slot, "error": "cannot open"}
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    start = min(start, max(0, total - 120))
    end = total if length is None else min(total, start + length)

    res = {
        "project": project, "slot": slot, "video": video.name,
        "frame_size": [W, H], "total_frames": total, "fps": round(fps, 3),
        "window": [start, end], "expected_n": n_expected,
        "expected_range": list(n_range) if n_range else None, "note": note,
    }

    # --- scene block (consecutive frames from window middle) ---
    block_start = start + max(0, (end - start - NOISE_BLOCK) // 2)
    res["scene"] = scene_block(cap, block_start, model, fps)

    # median luma for auto-gamma comes from the scene block
    med_luma = res["scene"]["brightness_mean"] if res["scene"] else 60.0
    gamma = auto_gamma(med_luma)
    clahe = cv2.createCLAHE(2.5, (8, 8))
    res["auto_gamma"] = round(gamma, 3)

    # --- sampled YOLO pass ---
    idxs = np.linspace(start, end - 1, min(N_SAMPLES, end - start)).astype(int)
    frames_for_montage = []
    raw_rows, enh_rows = [], []
    for k, idx in enumerate(idxs):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok:
            continue
        raw_rows.append({"frame": int(idx), "dets": yolo_pass(model, frame)})
        enh = enhance_for_yolo(frame, gamma, clahe)
        enh_rows.append({"frame": int(idx), "dets": yolo_pass(model, enh)})
        if k % max(1, len(idxs) // 4) == 0 and len(frames_for_montage) < 4:
            frames_for_montage.append((int(idx), frame.copy()))
    res["yolo_raw"] = raw_rows
    res["yolo_enhanced"] = enh_rows
    cap.release()

    # --- montage ---
    if frames_for_montage:
        tiles = []
        for idx, fr in frames_for_montage:
            v = brighten_for_view(fr)
            scale = 480.0 / v.shape[1]
            v = cv2.resize(v, (480, int(v.shape[0] * scale)))
            cv2.putText(v, f"f{idx}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 255, 255), 2)
            tiles.append(v)
        h = min(t.shape[0] for t in tiles)
        tiles = [t[:h] for t in tiles]
        mont = np.hstack(tiles)
        cv2.imwrite(str(OUT / f"{project}_slot{slot}_montage.jpg"), mont,
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None, help="PROJECT:SLOT filter")
    args = ap.parse_args()

    from ultralytics import YOLO
    model = YOLO(str(MODEL_PATH))

    todo = SLOTS
    if args.only:
        proj, _, sl = args.only.partition(":")
        todo = [s for s in SLOTS if s[0] == proj and (not sl or s[1] == int(sl))]

    for (project, slot, start, length, n, n_range, note) in todo:
        out_path = OUT / f"{project}_slot{slot}.json"
        if out_path.exists():
            print(f"skip (exists): {project} slot {slot}")
            continue
        t0 = time.time()
        print(f"survey: {project} slot {slot} ...", flush=True)
        try:
            res = survey_slot(model, project, slot, start, length, n, n_range, note)
        except Exception as e:  # keep the sweep going, record the failure
            res = {"project": project, "slot": slot, "error": repr(e)}
        res["elapsed_s"] = round(time.time() - t0, 1)
        out_path.write_text(json.dumps(res))
        err = res.get("error")
        print(f"  done in {res['elapsed_s']}s" + (f"  ERROR: {err}" if err else ""),
              flush=True)


if __name__ == "__main__":
    main()
