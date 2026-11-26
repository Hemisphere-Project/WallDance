#!/usr/bin/env python3
"""Time the whole processing loop on a recorded take (Plan Cordée rev 2, P2).

The show's budget is the loop, not YOLO alone: ``process_wall`` (the main loop's wall time of one
``FrameProcessor.process`` call), with ``yolo`` and ``mog2_feed`` (the motion feed's submit -> done window,
which overlaps enhance + YOLO) and the foreground's own time.  Each run replays ``--frames`` frames of the
take (after a 30-frame warm-up, from ``--skip``) through the real pipeline with the project's recorded config,
the plate the app had loaded, and one engine; the GPU is polled once a second (temperature, load, memory).
Read-only on the project; writes only ``--out``.

  python extra/measure_loop.py --project jeudi0809_mur2 --take slot_7_20261008_220951.avi \\
      --run yolo11x-pose:1280 --run yolo11l-pose:1280:1920x1056 [--engine-dir DIR] [--frames 1200] [--out p2.json]

On 2026-11-26 the laptop (RTX 5080 Laptop) ran it with TouchDesigner open on a copy of the show patch: all
five engines under the 55 ms p95 gate (docs/PLAN_CORDEE_2026-11.md §5, P2).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "application" / "tests"))
sys.path.insert(0, str(ROOT / "application" / "src"))
import replay as R  # noqa: E402  (bootstraps the venv's CUDA libs on Linux)

import cv2  # noqa: E402
import numpy as np  # noqa: E402


def _smi():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,power.draw",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
        return [float(v) for v in r.stdout.strip().splitlines()[0].split(",")]
    except Exception:  # noqa: BLE001
        return None


class _Poll(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.rows, self.stop = [], False

    def run(self):
        while not self.stop:
            v = _smi()
            if v:
                self.rows.append(v)
            time.sleep(1.0)


def _pct(a, q):
    return round(float(np.percentile(a, q)), 1) if len(a) else None


def plate_for(project_dir: Path, started_at: str):
    """The plate the app had loaded when the take started: the newest plates/plate_<stamp>.npz before it."""
    stamp = started_at.replace("-", "").replace(":", "").replace("T", "_")[:15]
    best = None
    for p in sorted((project_dir / "plates").glob("plate_*.npz")):
        if p.stem[6:] <= stamp:
            best = p
    return best


def time_run(video: Path, meta: dict, plate, model: str, imgsz: int, shape: str, frames: int, skip: int) -> dict:
    if shape:
        import importlib.util
        if importlib.util.find_spec("core.input_shape") is None:
            raise RuntimeError("this tree has no rectangular input (P1): it would time the square")
    config = json.loads(json.dumps(meta["config"]))
    config["model"], config["yolo_imgsz"] = model, imgsz
    if shape:
        config["yolo_shape"] = shape
    else:
        config.pop("yolo_shape", None)
    if plate:
        config["fg_plate_path"] = str(plate)
    config, _ = R.fill_detection_defaults(config)
    proc = R._build_processor(config, model, imgsz, use_gpu_path=True, use_trt=True)
    xf = R.input_transform_for(config)
    proc.tracker.reset()
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, skip)
    wall, yolo, mog, fg = [], [], [], []
    poll = _Poll()
    poll.start()
    for i in range(frames + 30):
        ok, fr = cap.read()
        if not ok:
            break
        fr = xf.apply(fr)
        t0 = time.perf_counter()
        _tracks, _enh, timing, _lat = proc.process(fr, need_preview=False, frame_number=skip + i)
        dt = (time.perf_counter() - t0) * 1000.0
        if i < 30:
            continue
        wall.append(dt)
        yolo.append(float(timing.get("yolo", 0)))
        mog.append(float(timing.get("mog2_feed", 0)))
        fg.append(float(getattr(proc, "_last_fg_ms", 0.0)))
    poll.stop = True
    poll.join(2)
    cap.release()
    g = np.array(poll.rows) if poll.rows else np.zeros((0, 4))
    return {"model": model, "shape": shape or str(imgsz), "n": len(wall),
            "process_wall_p50": _pct(wall, 50), "process_wall_p95": _pct(wall, 95), "process_wall_max": _pct(wall, 100),
            "yolo_p50": _pct(yolo, 50), "yolo_p95": _pct(yolo, 95), "mog2_feed_p50": _pct(mog, 50),
            "mog2_feed_p95": _pct(mog, 95), "fg_ms_p50": _pct(fg, 50), "fg_ms_p95": _pct(fg, 95),
            "gpu_temp_max": float(g[:, 0].max()) if len(g) else None,
            "gpu_util_p50": _pct(g[:, 1], 50) if len(g) else None, "gpu_mem_max": float(g[:, 2].max()) if len(g) else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--projects", default=str(ROOT / "projects"))
    ap.add_argument("--project", required=True)
    ap.add_argument("--take", required=True)
    ap.add_argument("--run", action="append", required=True, metavar="MODEL:IMGSZ[:HxW]")
    ap.add_argument("--engine-dir", default=None)
    ap.add_argument("--frames", type=int, default=1200)
    ap.add_argument("--skip", type=int, default=1500, help="first frame (both dancers on the wall in the 08/10 takes)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    if a.engine_dir:
        os.environ["WD_ENGINE_DIR"] = a.engine_dir
    pdir = Path(a.projects) / a.project
    video = pdir / "recordings" / a.take
    meta = json.loads(Path(str(video) + ".meta").read_text())
    plate = plate_for(pdir, meta["started_at"])
    res = {"take": f"{a.project}/{a.take}", "frames": a.frames, "skip": a.skip, "plate": str(plate) if plate else None,
           "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "runs": []}
    for spec in a.run:
        parts = spec.split(":")
        model, imgsz, shape = parts[0], int(parts[1]), (parts[2] if len(parts) > 2 else "")
        try:
            row = time_run(video, meta, plate, model, imgsz, shape, a.frames, a.skip)
        except Exception as exc:  # noqa: BLE001 - one missing engine must not stop the others
            row = {"model": model, "shape": shape or str(imgsz), "error": f"{type(exc).__name__}: {exc}"[:300]}
        res["runs"].append(row)
        print(json.dumps(row), flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
