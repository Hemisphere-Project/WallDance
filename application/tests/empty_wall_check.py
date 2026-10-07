#!/usr/bin/env python3
"""Empty-wall YOLO check (core/empty_wall.py, D29) offline: drives the real pipeline over an EMPTY take
the way Calibrate does (each ladder candidate on the next frames, the take looping) and prints the
result.  ``--full`` measures every candidate instead of stopping at the first clean one.

  python tests/empty_wall_check.py --project mur25m-ceinture-0610 --take slot_1_20261006_211624.avi \\
      --start 100 --span 200 --gamma 1.8 --clahe 1.5 --trt [--full] [--set k=v ...] [--json out.json]

Without --gamma / --clahe the project config's values are the start (what Calibrate would have
picked is in the config after a Calibrate).  The live confidence is the config's (or --conf).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay as R  # noqa: E402  (bootstraps the CUDA libs, puts src/ on the path)

import cv2  # noqa: E402

from core.empty_wall import CandidateResult, EmptyWallCheck, ladder  # noqa: E402


def _frames(video, start, span, xf):
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    while True:                                   # the app's playback loops the take
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        for _ in range(span):
            ok, frame = cap.read()
            if not ok:
                break
            yield xf.apply(frame)


def _set(proc, g, c):
    proc.enhancer.gamma = float(g)
    proc.enhancer.clahe_clip = float(c)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True)
    ap.add_argument("--take", help="recording file name in the project's recordings/")
    ap.add_argument("--video", help="explicit video path (instead of --take)")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--span", type=int, default=200, help="frames of the take to loop over")
    ap.add_argument("--gamma", type=float)
    ap.add_argument("--clahe", type=float)
    ap.add_argument("--conf", type=float, help="live confidence (default: the config's)")
    ap.add_argument("--frames", type=int, default=30, help="observed frames per candidate")
    ap.add_argument("--full", action="store_true", help="measure every candidate")
    ap.add_argument("--model", default=None)
    ap.add_argument("--imgsz", type=int, default=None)
    ap.add_argument("--trt", action="store_true")
    ap.add_argument("--engine-dir", default=None)
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    if a.engine_dir:
        os.environ["WD_ENGINE_DIR"] = a.engine_dir
    config = R._latest_config(a.project)
    if config is None:
        raise SystemExit(f"no config for {a.project}")
    R.apply_overrides(config, a.sets)
    video = Path(a.video) if a.video else R.PROJECTS_DIR / a.project / "recordings" / a.take
    model = a.model or config.get("model", "yolo11x-pose")
    imgsz = a.imgsz or int(config.get("yolo_imgsz", 1280))
    proc = R._build_processor(config, model, imgsz, use_gpu_path=True, use_trt=a.trt)
    g0 = a.gamma if a.gamma is not None else float(proc.enhancer.gamma)
    c0 = a.clahe if a.clahe is not None else float(proc.enhancer.clahe_clip)
    conf = a.conf if a.conf is not None else float(proc.settings.confidence)
    frames = _frames(video, a.start, a.span, R.input_transform_for(config))

    if a.full:
        chk = EmptyWallCheck(g0, c0, conf, frames=a.frames)
        proc.settings.confidence = chk.conf_limit
        tried = []
        for g, c in ladder(g0, c0):
            _set(proc, g, c)
            cr = CandidateResult(g, c)
            for i in range(chk.settle + a.frames):
                proc.process(next(frames), need_preview=False, frame_number=i)
                if i < chk.settle:
                    continue
                dets = list(proc.last_raw_dets)
                cr.frames += 1
                if any(d[0] >= chk.conf_limit for d in dets):
                    cr.ghost_frames += 1
                top = max(dets, key=lambda d: d[0], default=None)
                if top is not None and top[0] > cr.max_conf:
                    cr.max_conf, cr.worst = float(top[0]), tuple(float(v) for v in top[:4])
            tried.append(cr)
            print(f"{cr.label}: {cr.ghost_frames}/{cr.frames} ghost frames, max conf {cr.max_conf:.2f}"
                  + (f" at x={cr.worst[1]:.0f} y={cr.worst[2]:.0f} h={cr.worst[3]:.0f}" if cr.worst else ""),
                  flush=True)
        out = {"limit": chk.conf_limit, "tried": [c.__dict__ for c in tried]}
    else:
        chk = EmptyWallCheck(g0, c0, conf, frames=a.frames)
        proc.settings.confidence = chk.conf_limit
        _set(proc, *chk.current())
        i = 0
        while not chk.done:
            proc.process(next(frames), need_preview=False, frame_number=i)
            i += 1
            nxt = chk.feed(list(proc.last_raw_dets))
            if nxt is not None:
                _set(proc, *nxt)
        print(chk.result.summary())
        print(chk.result.log_line())
        out = {"limit": chk.conf_limit, "gamma": chk.result.gamma, "clahe": chk.result.clahe,
               "clean": chk.result.clean, "frames_used": i, "tried": [c.__dict__ for c in chk.tried]}
    out.update({"project": a.project, "video": str(video), "start": a.start, "gamma0": g0, "clahe0": c0,
                "conf": conf, "imgsz": imgsz})
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1))
    target = os.environ.get("WD_REMOTE_OUT")
    if target:
        Path(target, "empty_wall.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
