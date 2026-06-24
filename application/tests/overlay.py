#!/usr/bin/env python3
"""Headless overlay render + failure flagging (TUNING.md Phase D).

Makes the Phase-A count-vs-N failures *visible* so a human can verify them
(D1) and auto-flags the problem frames into a contact sheet (D2).  Built to
answer concrete questions like "are the 15 ghost frames the Phase-C tuned config
introduced on slot4 real spurious tracks, or is the score mis-reading?".

Tracks come from the authoritative replay (cache by default = Phase B, or
``--full``), via ``track_details=True`` so each reported track carries its
bbox/centroid/bridged flag.  Frames come from the original recording, brightened
(strong gamma+CLAHE) because the IR footage is near-black.  Track coords are
original-frame space (the CPU path), so they overlay directly.

Outputs (under --out-dir):
  * ``contact_<scenario>[_tag].png`` — montage of flagged frames (+context),
    red border = over-count/ghost, orange = drop; header shows abs frame,
    reported/N, status.  (D2)
  * ``<scenario>[_tag].mp4`` with --mp4 — full annotated window.  (D1)

Usage:
    python tests/overlay.py --scenario tests/scenarios/residence1-solo_slot4.json
    python tests/overlay.py --scenario ...slot4.json --set mog2_var_threshold=8 \
                            --set mog2_scale=0.7 --tag tuned --mp4
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

import detect_cache
import scoring

import cv2
import numpy as np

# Strong, fixed visualisation enhancement (NOT the detector's path) so the dark
# IR footage is countable by eye.
_VIS_GAMMA = 0.40
_VIS_LUT = np.array([((i / 255.0) ** _VIS_GAMMA) * 255 for i in range(256)],
                    dtype=np.uint8)


def _brighten(frame: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.LUT(gray, _VIS_LUT)
    gray = cv2.createCLAHE(clipLimit=4.0, tileGridSize=(8, 8)).apply(gray)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


# status -> (label, BGR border colour)
_STATUS = {
    "ok": ("OK", (90, 90, 90)),
    "drop": ("DROP", (0, 140, 255)),     # orange
    "over": ("GHOST/OVER", (0, 0, 255)),  # red
    "warmup": ("warmup", (60, 60, 60)),
}


def _frame_status(rec: dict, manifest: dict) -> str:
    if rec["frame"] < int(manifest.get("warmup", 0)):
        return "warmup"
    n = scoring.expected_at(manifest, rec["frame"])
    r = rec["reported"]
    if r < n:
        return "drop"
    if r > n:
        return "over"
    return "ok"


def _draw(frame: np.ndarray, rec: dict, manifest: dict, roi) -> np.ndarray:
    """Annotate a brightened full-res frame in place; return it."""
    vis = _brighten(frame)
    h, w = vis.shape[:2]
    n = scoring.expected_at(manifest, rec["frame"])
    status = _frame_status(rec, manifest)

    if roi is not None:
        rx, ry, rw, rh = roi
        cv2.rectangle(vis, (rx, ry), (rx + rw, ry + rh), (70, 70, 70), 2)

    for t in rec.get("tracks", []):
        x, y, bw, bh = (int(v) for v in t["bbox"])
        col = (255, 0, 255) if t["bridged"] else (0, 255, 255)  # magenta vs cyan
        cv2.rectangle(vis, (x, y), (x + bw, y + bh), col, 3)
        cx, cy = (int(c) for c in t["centroid"])
        cv2.circle(vis, (cx, cy), 6, col, -1)
        tag = f"D{t['id']}" + ("*" if t["bridged"] else "")
        cv2.putText(vis, tag, (x, max(0, y - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                    1.1, col, 3)

    label, border = _STATUS[status]
    cv2.rectangle(vis, (0, 0), (w - 1, h - 1), border, 16)
    head = f"f{rec['abs_frame']}  rep={rec['reported']} N={n}  {label}"
    cv2.rectangle(vis, (0, 0), (w, 70), (0, 0, 0), -1)
    cv2.putText(vis, head, (16, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.3,
                border if status != "ok" else (255, 255, 255), 3)
    return vis


def _resolve_roi(config: dict, w: int, h: int):
    if not config.get("roi_enabled"):
        return None
    sw = config.get("roi_source_w", w) or w
    sh = config.get("roi_source_h", h) or h
    sx, sy = w / sw, h / sh
    return (int(config["roi_x"] * sx), int(config["roi_y"] * sy),
            int(config["roi_w"] * sx), int(config["roi_h"] * sy))


def render(scenario_path: str, overrides: dict, out_dir: str, *,
           use_cache: bool = True, mp4: bool = False, context: int = 1,
           tag: str = "", max_sheet: int = 30, cols: int = 5) -> dict:
    import replay
    manifest = scoring.load_scenario(scenario_path)
    base = replay.scenario_config(manifest)
    config = {**base, **overrides}
    video = replay._find_recording(manifest["project"], manifest["slot"])
    if not video:
        raise SystemExit(f"no recording for {manifest['project']} slot {manifest['slot']}")
    model = config.get("model", "yolo11x-pose")
    imgsz = int(config.get("yolo_imgsz", 1280))

    # 1. Authoritative tracks (with spatial detail).
    if use_cache:
        key = detect_cache.cache_key(config, Path(video).name, manifest["start"],
                                     manifest["frames"], model, imgsz, path="trt")
        cpath = detect_cache.cache_path_for(key)
        if not cpath.exists():
            detect_cache.build_cache_gpu(str(video), config, model_name=model, imgsz=imgsz,
                                         start_frame=manifest["start"],
                                         max_frames=manifest["frames"], out_path=cpath)
        summary = detect_cache.replay_from_cache_gpu(
            detect_cache.load_cache(cpath), config, track_details=True)
    else:
        summary = replay.replay_recording(
            str(video), config, model_name=model, imgsz=imgsz,
            start_frame=manifest["start"], max_frames=manifest["frames"],
            track_details=True)
    per_frame = {r["frame"]: r for r in summary["per_frame"]}
    score = scoring.score_timeline(summary["per_frame"], manifest)

    # 2. Flag count != N frames (+context), respecting warmup.
    flagged = sorted({r["frame"] for r in summary["per_frame"]
                      if _frame_status(r, manifest) in ("drop", "over")})
    ctx = sorted({f + d for f in flagged for d in range(-context, context + 1)
                  if 0 <= f + d < manifest["frames"]})

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    suffix = f"_{tag}" if tag else ""
    name = manifest["name"]

    # 3. Read frames + render.
    cap = cv2.VideoCapture(str(video))
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    roi = _resolve_roi(config, W, H)

    writer = None
    if mp4:
        mp4_path = out / f"{name}{suffix}.mp4"
        writer = cv2.VideoWriter(str(mp4_path), cv2.VideoWriter_fourcc(*"mp4v"),
                                 float(manifest.get("fps", 20)), (W, H))

    cap.set(cv2.CAP_PROP_POS_FRAMES, manifest["start"])
    tiles = []
    sheet_set = set(ctx[:max_sheet] if len(ctx) > max_sheet else ctx)
    for i in range(manifest["frames"]):
        ok, frame = cap.read()
        if not ok:
            break
        rec = per_frame.get(i, {"frame": i, "abs_frame": manifest["start"] + i,
                                "reported": 0, "ids": [], "tracks": []})
        if writer is not None:
            writer.write(_draw(frame, rec, manifest, roi))
        if i in sheet_set:
            tiles.append(_draw(frame, rec, manifest, roi))
    cap.release()
    if writer is not None:
        writer.release()

    # 4. Contact sheet montage.
    sheet_path = None
    if tiles:
        tw = 460
        th = int(tw * H / W)
        rows = (len(tiles) + cols - 1) // cols
        grid = np.zeros((rows * th, cols * tw, 3), dtype=np.uint8)
        for k, t in enumerate(tiles):
            r, c = divmod(k, cols)
            grid[r*th:(r+1)*th, c*tw:(c+1)*tw] = cv2.resize(t, (tw, th))
        sheet_path = out / f"contact_{name}{suffix}.png"
        cv2.imwrite(str(sheet_path), grid)

    return {
        "scenario": name,
        "tag": tag,
        "score": score["score"],
        "components": score["components"],
        "flagged_frames_rel": flagged,
        "flagged_frames_abs": [manifest["start"] + f for f in flagged],
        "n_flagged": len(flagged),
        "contact_sheet": str(sheet_path) if sheet_path else None,
        "mp4": str(out / f"{name}{suffix}.mp4") if mp4 else None,
    }


def main():
    import replay
    ap = argparse.ArgumentParser(description="Overlay render + failure flagging (TUNING Phase D)")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--full", action="store_true", help="run full pipeline instead of the cache")
    ap.add_argument("--mp4", action="store_true", help="also write a full annotated MP4")
    ap.add_argument("--context", type=int, default=1, help="frames of context around each flag")
    ap.add_argument("--max-sheet", type=int, default=30, help="max tiles on the contact sheet")
    ap.add_argument("--tag", default="", help="suffix for output filenames (e.g. tuned)")
    ap.add_argument("--out-dir", default=None, help="output dir (default: tests/overlays)")
    args = ap.parse_args()

    overrides = replay.apply_overrides({}, args.sets)
    out_dir = args.out_dir or str(Path(__file__).resolve().parent / "overlays")
    result = render(args.scenario, overrides, out_dir, use_cache=not args.full,
                    mp4=args.mp4, context=args.context, tag=args.tag,
                    max_sheet=args.max_sheet)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
