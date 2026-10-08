#!/usr/bin/env python3
"""Replay mur30m-0710 takes like tests/replay.py --trt --quality --internal (same rows, same frame clock = the
take's .meta actual_fps), plus per-frame extras for the 30 m confirmation (observation only, app code untouched):

  raw  [box conf, cx, cy, h, n kpts > 0.5, torso ok]  every YOLO det before the size gate (original px)
  ph   the person height in force (settings.person_height_px: what the height guard settled on)
  dh   processor.dancer_height median (the readiness "dancer size" input), None before 2 s of skeletons
  int  every internal track + "own" (its own size, original px; None before 20 torsos) and "est"
  ref  YOLO dets (as replay.py) + nk / tor
  fg   the snapshot foreground summary (as replay.py)
and, per take, the belt static map (StaticMap cells) at the end: <timeline>.belt_static.npz.

  run_variant.py --takes slot_2_...avi,slot_3_...avi --out DIR --label V1 \
      --ph 150,253,253,253 --set confidence=0.15 ... [--engine-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "tests"))
import replay as R  # noqa: E402  (re-execs with the CUDA libs on LD_LIBRARY_PATH)

import cv2  # noqa: E402
import numpy as np  # noqa: E402

PROJECT = "mur30m-0710"


def internal_tracks_own(tracker, space):
    out = R.internal_tracks(tracker, space)
    if space is None:
        return out
    g = space
    get = (lambda k: g[k]) if isinstance(g, dict) else (lambda k: getattr(g, k))
    s = float(get("scale")) or 1.0
    by_id = {int(t.track_id): t for t in tracker.tracks}
    for d in out:
        t = by_id.get(d["id"])
        if t is None:
            continue
        own = t.own_height() if hasattr(t, "own_height") else None
        d["own"] = None if own is None else round(float(own) / s, 1)
        d["est"] = bool(getattr(t, "is_established", False))
    return out


def run_take(take: str, sets: list, timeline: Path, fps_override: float | None = None) -> dict:
    config = R._latest_config(PROJECT)
    R.apply_overrides(config, sets)
    if config.get("fg_plate"):
        plate = Path(str(config["fg_plate"]))
        config["fg_plate_path"] = str(plate if plate.is_absolute() else R.PROJECTS_DIR / PROJECT / plate)
    video = R.PROJECTS_DIR / PROJECT / "recordings" / take
    fps = fps_override or R._stream_fps(None, video)
    model = config.get("model", "yolo11x-pose")
    imgsz = int(config.get("yolo_imgsz", 1280))
    proc = R._build_processor(config, model, imgsz, use_gpu_path=True, use_trt=True)
    xf = R.input_transform_for(config)
    proc.tracker.reset()
    holder = R._attach_reference_capture(proc)
    inner = proc._cache_capture_gpu

    def _kpt_hook(dets, space, gray, ow, oh):
        holder["k"] = [[int((c > 0.5).sum()), int(bool((c[[5, 6]] >= 0.5).any() and (c[[11, 12]] >= 0.5).any()))]
                       for (_k, c, _b) in dets]
        inner(dets, space, gray, ow, oh)

    proc._cache_capture_gpu = _kpt_hook
    orig_guard = proc._guard_person_height

    def _raw_hook(detections, inv_lb):
        raw = []
        for (box_conf, cx, cy, h), (_k, c, _b) in zip(getattr(proc, "last_raw_dets", []) or [], detections):
            tor = int(bool((c[[5, 6]] >= 0.5).any() and (c[[11, 12]] >= 0.5).any()))
            raw.append([round(float(box_conf), 3), round(cx, 1), round(cy, 1), round(h, 1),
                        int((c > 0.5).sum()), tor])
        holder["raw"] = raw
        return orig_guard(detections, inv_lb)

    proc._guard_person_height = _raw_hook
    clock = R._attach_frame_clock(proc, fps)
    tmp = tempfile.mkdtemp(prefix="wd_c0710_")
    proc.tracker.logger.start_session(tmp)
    guard_events = []
    rows = []
    cap = cv2.VideoCapture(str(video))
    t0 = time.time()
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        holder["ref"] = None
        holder["k"] = None
        holder["raw"] = None
        clock["frame"] = i
        tracks, _e, _t, _l = proc.process(xf.apply(fr), need_preview=False, frame_number=i)
        row = R.per_frame_record(i, i, tracks, True, emitted=getattr(proc, "last_emitted", None))
        if holder["ref"] is not None:
            row["ref"] = holder["ref"]
            for x, (nk, tor) in zip(row["ref"], holder.get("k") or []):
                x["nk"], x["tor"] = nk, tor
        row["int"] = internal_tracks_own(proc.tracker, holder["space"])
        row["raw"] = holder.get("raw") or []
        row["ph"] = int(proc.settings.person_height_px)
        dh = getattr(proc, "dancer_height", None)
        row["dh"] = None if not dh else round(float(dh[0]), 1)
        ev = getattr(proc, "height_guard_event", None)
        if ev is not None:
            guard_events.append({"frame": i, "t": round(i / fps, 2), "old": ev[0], "new": ev[1]})
            proc.height_guard_event = None
        fg = getattr(proc, "last_fg", None)
        if fg is not None:
            row["fg"] = {"v": bool(fg.valid), "why": fg.reason, "r": round(float(fg.fg_ratio), 4),
                         "g": round(float(fg.gain), 3),
                         "b": [[round(b.x), round(b.y), round(b.area)] for b in fg.blobs[:6]]}
        rows.append(row)
        i += 1
    cap.release()
    proc.tracker.logger.close()
    timeline.parent.mkdir(parents=True, exist_ok=True)
    timeline.write_text(json.dumps(rows))
    st = getattr(proc, "_belt_static", None)
    if st is not None:
        st.save(str(timeline) + ".belt_static.npz")
    info = {"take": take, "frames": len(rows), "fps": fps, "imgsz": imgsz, "wall_s": round(time.time() - t0),
            "guard_events": guard_events, "lb_scale": float(getattr(proc, "last_lb_scale", 0.0) or 0.0),
            "belt_state": getattr(proc, "_belt_state", None),
            "config": {k: config.get(k) for k in (
                "confidence", "yolo_imgsz", "tracking_mode", "tracker_intermittent_confirm", "person_height_px",
                "gamma", "clahe_clip", "mog2_var_threshold", "mog2_scale", "fg_enabled", "fg_plate", "max_dancers",
                "use_ir_belt", "static_ghost_guard", "coast_s", "stability", "roi_enabled", "height_guard",
                "tracker_own_height_gates")}}
    Path(str(timeline) + ".info.json").write_text(json.dumps(info, indent=1))
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--ph", default=None, help="per-take person_height_px, comma-separated (as recorded)")
    ap.add_argument("--set", dest="sets", action="append", default=[])
    ap.add_argument("--engine-dir", default="/data/WallDance/tmp_analysis/confirm_0710/engines")
    a = ap.parse_args()
    os.environ["WD_ENGINE_DIR"] = a.engine_dir
    takes = [t for t in a.takes.split(",") if t]
    phs = [int(x) for x in a.ph.split(",")] if a.ph else [None] * len(takes)
    for take, ph in zip(takes, phs):
        sets = list(a.sets) + ([f"person_height_px={ph}"] if ph else [])
        tl = Path(a.out) / f"{a.label}_{Path(take).stem}.json"
        if tl.exists():
            print(f"[c0710] {a.label} {take}: cached")
            continue
        info = run_take(take, sets, tl)
        print(f"[c0710] {a.label} {take}: {info['frames']} frames in {info['wall_s']} s, imgsz {info['imgsz']}, "
              f"guard {info['guard_events']}", flush=True)


if __name__ == "__main__":
    main()
