"""Layer 2: full-pipeline replays of selected corpus windows.

Each entry replays through the real CPU process() path with the project's
current (latest) config, optionally with overrides, saves summary + detailed
timeline, and scores against the annotated dancer count when constant.

Run from application/:  .venv/Scripts/python.exe ../tmp_analysis/run_replays.py [--only LABEL]
Outputs: tmp_analysis/replays/<label>.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APP = REPO / "application"
for p in (str(APP / "tests"), str(APP / "src"), str(APP)):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT = HERE / "replays"
OUT.mkdir(parents=True, exist_ok=True)

# label, project, slot, start, frames, expected N (None = varying), overrides
RUNS = [
    ("wb2_s3_proj",  "3_TANGO_HANGAR-whitebg2", 3, 1500, 300, 1, {}),
    ("wb2_s4_proj",  "3_TANGO_HANGAR-whitebg2", 4, 1500, 300, 1, {}),
    ("tex_s5_proj",  "1_TANGO_HANGAR-texturedbg", 5, 1000, 400, 2, {}),
    ("tex_s4_proj",  "1_TANGO_HANGAR-texturedbg", 4, 2500, 400, 1, {}),
    ("wb3_s2_proj",  "4_TANGO_HANGAR-whitebg3", 2, 100, 400, 2, {}),
    ("wb3_s3_proj",  "4_TANGO_HANGAR-whitebg3", 3, 0, 400, None, {}),
    ("flou_s6_proj", "5_TANGO_HANGAR-testflou", 6, 900, 400, 1, {}),
    ("togon_s1_proj", "6_TANGO_TOGO-night", 1, 0, 330, 1, {}),
    ("togod_s9_proj", "7_TANGO_TOGO-day", 9, 2500, 400, 2, {}),
    ("togod_s8_proj", "7_TANGO_TOGO-day", 8, 500, 400, 2, {}),
    ("dark_s4_proj", "0-TEST-verydark", 4, 0, 400, None, {}),
    ("phones_s1_proj", "0-TEST-phones", 1, 200, 400, 4, {}),
]

# "Calibrated" pass: what calib1+calib2 would plausibly set per scene
# (survey-derived heights/ratios/conf, auto gamma, var 8 / scale 0.7,
#  imgsz 1280 to match the survey operating points).
_CAL_COMMON = {"enhance_enabled": True, "enhance_force": False,
               "brightness_threshold": 131, "clahe_clip": 2.5,
               "mog2_var_threshold": 8, "mog2_scale": 0.7,
               "yolo_imgsz": 1280, "greyscale": True}

RUNS += [
    ("wb2_s3_cal", "3_TANGO_HANGAR-whitebg2", 3, 1500, 300, 1, {
        **_CAL_COMMON, "gamma": 2.2, "confidence": 0.5, "person_height_px": 286,
        "person_height_min_ratio": 0.38, "person_height_max_ratio": 1.5}),
    ("wb2_s4_cal", "3_TANGO_HANGAR-whitebg2", 4, 1500, 300, 1, {
        **_CAL_COMMON, "gamma": 2.2, "confidence": 0.5, "person_height_px": 190,
        "person_height_min_ratio": 0.52, "person_height_max_ratio": 1.63}),
    ("tex_s4_cal", "1_TANGO_HANGAR-texturedbg", 4, 2500, 400, 1, {
        **_CAL_COMMON, "gamma": 1.3, "confidence": 0.15, "person_height_px": 265,
        "person_height_min_ratio": 0.47, "person_height_max_ratio": 1.75}),
    ("tex_s5_cal", "1_TANGO_HANGAR-texturedbg", 5, 1000, 400, 2, {
        **_CAL_COMMON, "gamma": 1.35, "confidence": 0.15, "person_height_px": 327,
        "person_height_min_ratio": 0.49, "person_height_max_ratio": 1.5}),
    ("wb3_s2_cal", "4_TANGO_HANGAR-whitebg3", 2, 100, 400, 2, {
        **_CAL_COMMON, "gamma": 2.2, "confidence": 0.25, "person_height_px": 206,
        "person_height_min_ratio": 0.41, "person_height_max_ratio": 1.68}),
    ("wb3_s3_cal", "4_TANGO_HANGAR-whitebg3", 3, 0, 400, None, {
        **_CAL_COMMON, "gamma": 2.2, "confidence": 0.25, "person_height_px": 343,
        "person_height_min_ratio": 0.8, "person_height_max_ratio": 1.5}),
    ("flou_s6_cal", "5_TANGO_HANGAR-testflou", 6, 900, 400, 1, {
        **_CAL_COMMON, "gamma": 2.2, "confidence": 0.5, "person_height_px": 174,
        "person_height_min_ratio": 0.8, "person_height_max_ratio": 1.78,
        "roi_enabled": False}),
    ("togon_s1_cal", "6_TANGO_TOGO-night", 1, 0, 330, 1, {
        **_CAL_COMMON, "gamma": 2.2, "confidence": 0.5, "person_height_px": 200,
        "person_height_min_ratio": 0.3, "person_height_max_ratio": 2.5,
        "roi_enabled": False}),
    ("togod_s9_cal", "7_TANGO_TOGO-day", 9, 2500, 400, 2, {
        **_CAL_COMMON, "gamma": 2.2, "confidence": 0.65, "person_height_px": 123,
        "person_height_min_ratio": 0.8, "person_height_max_ratio": 1.53,
        "roi_enabled": False}),
    ("phones_s1_cal", "0-TEST-phones", 1, 200, 400, 4, {
        **_CAL_COMMON, "gamma": 1.0, "yolo_imgsz": 1920, "confidence": 0.15,
        "person_height_px": 102, "person_height_min_ratio": 0.68,
        "person_height_max_ratio": 1.5, "roi_enabled": False}),
]


def run_one(label, project, slot, start, frames, n, overrides):
    import replay
    import scoring

    config = replay._latest_config(project) or {}
    config.update(overrides)
    video = replay._find_recording(project, slot)
    if video is None:
        return {"label": label, "error": "no recording"}
    model_name = config.get("model", "yolo11x-pose")
    imgsz = int(config.get("yolo_imgsz", 1280))

    t0 = time.time()
    summary = replay.replay_recording(
        str(video), config, model_name=model_name, imgsz=imgsz,
        start_frame=start, max_frames=frames, track_details=True)
    summary["elapsed_s"] = round(time.time() - t0, 1)
    summary["label"] = label
    summary["project"] = project
    summary["slot"] = slot
    summary["overrides"] = overrides
    summary["config_used"] = {k: config.get(k) for k in (
        "model", "yolo_imgsz", "confidence", "person_height_px", "tracking_mode",
        "greyscale", "gamma", "clahe_clip", "brightness_threshold", "enhance_enabled",
        "roi_enabled", "roi_x", "roi_y", "roi_w", "roi_h",
        "mog2_scale", "mog2_var_threshold", "exclusion_cells") if k in config}

    if n is not None:
        manifest = {"name": label, "project": project, "slot": slot,
                    "start": start, "frames": frames, "warmup": 15,
                    "fps": 19.8, "expected_count": n}
        summary["score"] = scoring.score_timeline(summary["per_frame"], manifest)
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    for (label, project, slot, start, frames, n, overrides) in RUNS:
        if args.only and args.only != label:
            continue
        out = OUT / f"{label}.json"
        if out.exists() and not args.force:
            print(f"skip (exists): {label}")
            continue
        print(f"replay: {label} ({project} slot {slot}, {frames}f @ {start})",
              flush=True)
        try:
            res = run_one(label, project, slot, start, frames, n, overrides)
        except Exception as e:
            res = {"label": label, "error": repr(e)}
        out.write_text(json.dumps(res))
        if "error" in res:
            print(f"  ERROR: {res['error']}", flush=True)
        else:
            sc = res.get("score", {}).get("score")
            print(f"  done {res['elapsed_s']}s  tracks={res['total_tracks']} "
                  f"ghosts={res['ghost_tracks']} zero={res['zero_detection_frames']} "
                  f"avg={res['avg_detections']}" + (f" score={sc}" if sc is not None else ""),
                  flush=True)


if __name__ == "__main__":
    main()
