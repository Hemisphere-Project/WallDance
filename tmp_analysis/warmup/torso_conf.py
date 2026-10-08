#!/usr/bin/env python3
"""What the tracker receives on a take's first frames: per YOLO det, box height and the confidences of the
shoulders (5, 6) and hips (11, 12) -- does the walker give a confident torso?  torso_conf.py take from to"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WD_ENGINE_DIR", "/data/WallDance/models/dev37")
import primed_replay as P  # noqa: E402
R = P.R

take, lo, hi = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
config = R._latest_config("mur25m-night-common")
proc = R._build_processor(config, "yolo11x-pose", 1280, use_gpu_path=True, use_trt=True)
xf = R.input_transform_for(config)
proc.tracker.reset()
clock = R._attach_frame_clock(proc, 20.0)
import tempfile  # noqa: E402
proc.tracker.logger.start_session(tempfile.mkdtemp())
orig = proc.tracker.update
cur = {"f": 0}


def spy(detections, frame_number=None, **kw):
    if lo <= cur["f"] <= hi:
        for k, c, b in detections:
            if not (c > 0).any():
                print(f"{cur['f']:4d} synthetic h={b[3]:.0f}")
                continue
            sh = [round(float(c[i]), 2) for i in (5, 6)]
            hp = [round(float(c[i]), 2) for i in (11, 12)]
            print(f"{cur['f']:4d} yolo h={b[3]:.0f} x={b[0] + b[2] / 2:.0f} sh={sh} hip={hp} "
                  f"n>.5={int((c > 0.5).sum())}")
    return orig(detections, frame_number=frame_number, **kw)


proc.tracker.update = spy
for i, fr in enumerate(P.frames_of(R.PROJECTS_DIR / "mur25m-night-common" / "recordings" / take, 0, hi + 1)):
    cur["f"] = i
    clock["frame"] = i
    proc.process(xf.apply(fr), need_preview=False, frame_number=i)
