"""Probe whether a TRT engine loads on this box (TRT version tag)."""
import sys
sys.path.insert(0, "/data/WallDance/application/tests")
import replay as R  # noqa  (CUDA libs bootstrap)
import numpy as np
from ultralytics import YOLO
for p in sys.argv[1:]:
    try:
        m = YOLO(p, task="pose")
        sz = int(p.rsplit("_", 1)[1].split(".")[0])
        m.predict(np.zeros((sz, sz, 3), np.uint8), imgsz=sz, verbose=False)
        print("OK", p)
    except Exception as e:  # noqa
        print("FAIL", p, type(e).__name__, str(e)[:300])
