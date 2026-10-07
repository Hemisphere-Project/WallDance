#!/usr/bin/env python3
"""Cost of a zoom crop on TensorRT: build an x@640 engine in a scratch dir (the shared models/ stays
untouched) and time it against the dev37 x@960 / x@1280 engines through the same ultralytics predict call
(batch 1, FP16), so the ratio carries to the laptop's measured x@1280 (21 ms engine-only).

  python zoom_cost.py SCRATCH_DIR
"""
import shutil
import statistics as st
import sys
import time
from pathlib import Path

import numpy as np


def bench(path, imgsz, n=150):
    from ultralytics import YOLO
    m = YOLO(str(path), task="pose")
    img = (np.random.default_rng(0).random((imgsz, imgsz, 3)) * 255).astype(np.uint8)
    for _ in range(15):
        m.predict(img, imgsz=imgsz, half=True, verbose=False)
    ts = []
    for _ in range(n):
        t0 = time.perf_counter()
        m.predict(img, imgsz=imgsz, half=True, verbose=False)
        ts.append((time.perf_counter() - t0) * 1000)
    return st.median(ts), sorted(ts)[int(0.9 * (n - 1))]


def main():
    scratch = Path(sys.argv[1])
    scratch.mkdir(parents=True, exist_ok=True)
    pt = scratch / "yolo11x-pose.pt"
    if not pt.exists():
        shutil.copy("/data/WallDance/models/yolo11x-pose.pt", pt)
    eng = scratch / "yolo11x-pose.engine"
    if not eng.exists():
        from ultralytics import YOLO
        YOLO(str(pt)).export(format="engine", imgsz=640, half=True, device=0, verbose=False)
    out = {640: bench(eng, 640)}
    for sz in (960, 1280):
        out[sz] = bench(Path(f"/data/WallDance/models/dev37/yolo11x-pose_{sz}.engine"), sz)
    for sz, (med, p90) in sorted(out.items()):
        print(f"x@{sz}: median {med:.1f} ms, p90 {p90:.1f} ms (ultralytics predict, shared GPU)")
    r = out[640][0] / out[1280][0]
    print(f"x@640 / x@1280 = {r:.2f} -> laptop estimate {21.3 * r:.1f} ms per crop (x@1280 = 21.3 ms there)")


if __name__ == "__main__":
    main()
