#!/usr/bin/env python3
"""Replay a take like tests/replay.py (TRT, --quality --internal rows), optionally PRIMED first with frames
of another recording (the empty wall), as the live app has been running before the dancers enter.

The prime frames go through the same FrameProcessor (MOG2, tracker, slots, height guard, frame clock)
but write no timeline rows; the take's rows are numbered from 0 (frame 0 = the take's --start frame).

  primed_replay.py --project P --take slot_6_....avi [--start 0] [--frames 600]
      [--prime slot_2_20261006_214010.avi:20:300] [--set key=value ...] [--timeline out.json]
      [--engine-dir /data/WallDance/models/dev37] [--log-dir DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "application"
sys.path.insert(0, str(APP / "tests"))
import replay as R  # noqa: E402  (re-execs with the CUDA libs on LD_LIBRARY_PATH)

import cv2  # noqa: E402


def frames_of(path: Path, start: int, n: int | None):
    cap = cv2.VideoCapture(str(path))
    if start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
    k = 0
    try:
        while n is None or k < n:
            ok, fr = cap.read()
            if not ok:
                break
            yield fr
            k += 1
    finally:
        cap.release()


def run(project: str, take: str, start: int, frames: int | None, prime: str | None, sets: list,
        timeline: str | None, log_dir: str | None, fps: float = 20.0, quiet: bool = False,
        scenario: str | None = None) -> list:
    if scenario:                                   # a corpus scenario: its pinned config, take, start, frames
        import scoring
        man = scoring.load_scenario(scenario)
        config = R.scenario_config(man)
        config.setdefault("max_dancers", max(1, scoring.max_expected(man)))
        project = man["project"]
        take = take or str(R.PROJECTS_DIR / project / man["video"] if man.get("video")
                           else R._find_recording(project, man["slot"]))   # absolute: rec / take keeps it
        start = start or int(man.get("start") or 0)
        frames = frames or man.get("frames")
        fps = R._stream_fps(man, Path(take))          # the manifest's fps, else the .meta, as replay.py
    else:
        config = R._latest_config(project)
    R.apply_overrides(config, sets)
    if config.get("fg_plate"):
        plate = Path(str(config["fg_plate"]))
        config["fg_plate_path"] = str(plate if plate.is_absolute() else R.PROJECTS_DIR / project / plate)
    rec = R.PROJECTS_DIR / project / "recordings"
    if not scenario:
        fps = R._stream_fps(None, rec / take)       # the take's .meta actual_fps, as replay.py does
    model = config.get("model", "yolo11x-pose")
    imgsz = int(config.get("yolo_imgsz", 1280))
    proc = R._build_processor(config, model, imgsz, use_gpu_path=True, use_trt=True)
    xf = R.input_transform_for(config)
    proc.tracker.reset()
    ref_holder = R._attach_reference_capture(proc)
    inner = proc._cache_capture_gpu

    def _kpt_hook(dets, space, gray, ow, oh):     # per det (aligned with ref): keypoints > 0.5, torso ok
        ref_holder["k"] = [[int((c > 0.5).sum()), int(bool((c[[5, 6]] >= 0.5).any() and (c[[11, 12]] >= 0.5).any()))]
                           for (_k, c, _b) in dets]
        inner(dets, space, gray, ow, oh)

    proc._cache_capture_gpu = _kpt_hook
    orig_guard = proc._guard_person_height

    def _raw_hook(detections, inv_lb):              # RAW YOLO dets, before the size gate (person-height free)
        raw = []
        for (box_conf, cx, cy, h), (_k, c, _b) in zip(getattr(proc, "last_raw_dets", []) or [], detections):
            tor = int(bool((c[[5, 6]] >= 0.5).any() and (c[[11, 12]] >= 0.5).any()))
            raw.append([round(float(box_conf), 3), round(cx, 1), round(cy, 1), round(h, 1), int((c > 0.5).sum()), tor])
        ref_holder["raw"] = raw
        return orig_guard(detections, inv_lb)

    proc._guard_person_height = _raw_hook
    clock = R._attach_frame_clock(proc, fps)
    tmp = log_dir or tempfile.mkdtemp(prefix="wd_primed_")
    proc.tracker.logger.start_session(tmp)
    k = 0                                     # global frame counter (prime + take): the clock / frame_number
    n_prime = 0
    if prime:
        pv, ps, pn = prime.split(":")
        while n_prime < int(pn):                       # loops a short empty take (like the app's playback)
            got = 0
            for fr in frames_of(rec / pv, int(ps), int(pn) - n_prime):
                clock["frame"] = k
                proc.process(xf.apply(fr), need_preview=False, frame_number=k)
                k += 1
                n_prime += 1
                got += 1
            if not got:
                break
    rows = []
    for i, fr in enumerate(frames_of(rec / take, start, frames)):
        ref_holder["ref"] = None
        ref_holder["k"] = None
        ref_holder["raw"] = None
        clock["frame"] = k
        tracks, _e, _t, _l = proc.process(xf.apply(fr), need_preview=False, frame_number=k)
        row = R.per_frame_record(i, start + i, tracks, True, emitted=getattr(proc, "last_emitted", None))
        if ref_holder["ref"] is not None:
            row["ref"] = ref_holder["ref"]
            for x, (nk, tor) in zip(row["ref"], ref_holder.get("k") or []):
                x["nk"], x["tor"] = nk, tor
        row["int"] = R.internal_tracks(proc.tracker, ref_holder["space"])
        if ref_holder.get("raw"):
            row["raw"] = ref_holder["raw"]          # [box conf, cx, cy, h, n kpts > 0.5, torso ok]
        row["ph"] = int(proc.settings.person_height_px)
        fg = getattr(proc, "last_fg", None)
        if fg is not None:
            row["fg"] = {"v": bool(fg.valid), "r": round(float(fg.fg_ratio), 4)}
        rows.append(row)
        k += 1
    proc.tracker.logger.close()
    if timeline:
        Path(timeline).parent.mkdir(parents=True, exist_ok=True)
        Path(timeline).write_text(json.dumps(rows))
    if not quiet:
        print(f"[primed] {take} start {start}: {len(rows)} frames after {n_prime} prime frames -> {timeline}")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="mur25m-night-common")
    ap.add_argument("--take", default=None)
    ap.add_argument("--scenario", default=None, help="a tests/scenarios manifest instead of --project/--take")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--frames", type=int, default=None)
    ap.add_argument("--prime", default=None, help="video:start:nframes (in the project's recordings)")
    ap.add_argument("--set", dest="sets", action="append", default=[])
    ap.add_argument("--timeline", default=None)
    ap.add_argument("--log-dir", default=None)
    ap.add_argument("--engine-dir", default="/data/WallDance/models/dev37")
    a = ap.parse_args()
    os.environ["WD_ENGINE_DIR"] = a.engine_dir
    run(a.project, a.take, a.start, a.frames, a.prime, a.sets, a.timeline, a.log_dir, scenario=a.scenario)


if __name__ == "__main__":
    main()
