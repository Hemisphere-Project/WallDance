"""Isolated inference benchmark per (model, imgsz, backend): pure backend forward timed with
CUDA events (GPU time) + wall, and the full ultralytics model(tensor) call as the app makes it.
Run from application/: .venv/bin/python engine_bench.py --out engine_bench.json
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/data/WallDance/application/tests")
import replay  # noqa: E402,F401
import numpy as np  # noqa: E402
import torch  # noqa: E402
from ultralytics import YOLO  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
ap.add_argument("--reps", type=int, default=100)
ap.add_argument("--pt", action="store_true", help="also bench PyTorch .pt fp32/fp16")
args = ap.parse_args()
M = Path("/data/WallDance/models")
res = {"load": os.getloadavg()}


def bench(fn, reps):
    s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
    for _ in range(10):
        fn()
    torch.cuda.synchronize()
    gpu, wall = [], []
    for _ in range(reps):
        t0 = time.perf_counter()
        s.record()
        fn()
        e.record()
        torch.cuda.synchronize()
        wall.append((time.perf_counter() - t0) * 1000)
        gpu.append(s.elapsed_time(e))
    g, w = np.asarray(gpu), np.asarray(wall)
    return {"gpu_p50": round(float(np.median(g)), 2), "gpu_min": round(float(g.min()), 2),
            "wall_p50": round(float(np.median(w)), 2), "wall_min": round(float(w.min()), 2),
            "wall_p95": round(float(np.percentile(w, 95)), 2)}


cfgs = [(m, s) for m in ("yolo11x-pose", "yolo11l-pose") for s in (960, 1280)]
for m, sz in cfgs:
    x = torch.rand(1, 3, sz, sz, device="cuda")
    eng = M / f"{m}_{sz}.engine"
    if eng.exists():
        model = YOLO(str(eng), task="pose")
        model(x, imgsz=sz, conf=0.5, verbose=False)  # build predictor
        backend = model.predictor.model
        res[f"trt_{m}_{sz}_backend_forward"] = bench(lambda: backend(x), args.reps)
        res[f"trt_{m}_{sz}_ultralytics_call"] = bench(
            lambda: model(x, imgsz=sz, conf=0.5, iou=0.45, verbose=False), args.reps // 2)
        print(m, sz, res[f"trt_{m}_{sz}_backend_forward"], res[f"trt_{m}_{sz}_ultralytics_call"], flush=True)
        del model, backend
        torch.cuda.empty_cache()
    if args.pt:
        for half in (False, True):
            model = YOLO(str(M / f"{m}.pt"))
            model(x, imgsz=sz, conf=0.5, half=half, verbose=False)
            backend = model.predictor.model
            xx = x.half() if half else x
            with torch.inference_mode():
                res[f"pt_{'fp16' if half else 'fp32'}_{m}_{sz}_backend_forward"] = bench(lambda: backend(xx), args.reps // 4)
            print(m, sz, "pt", half, res[f"pt_{'fp16' if half else 'fp32'}_{m}_{sz}_backend_forward"], flush=True)
            del model, backend
            torch.cuda.empty_cache()
res["load_after"] = os.getloadavg()
Path(args.out).write_text(json.dumps(res, indent=1))
