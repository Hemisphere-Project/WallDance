#!/usr/bin/env python3
"""Measure per-(model, imgsz) engine inference fps -> models/fps_table.json.

ROADMAP P-6 / Phase 2b: per-model fps cost factors are PER-RIG — measure them
once at engine-build time instead of assuming the imgsz^-2 law (which Phase 2b
measured breaking below ~960, where fixed overhead dominates). calib2 consumes
the table for the imgsz FPS budget and the report-only model advisory.

Run after build_engines.sh (it invokes this automatically). Re-running only
re-measures engines missing from the table unless --force.
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
TABLE_PATH = MODELS_DIR / "fps_table.json"
WARMUP = 5
RUNS = 30
_ENGINE_RE = re.compile(r"^(?P<model>.+-pose)_(?P<imgsz>\d+)\.engine$")


def measure_engine(path: Path, imgsz: int) -> float:
    from ultralytics import YOLO
    model = YOLO(str(path), task="pose")
    img = np.zeros((imgsz, imgsz, 3), dtype=np.uint8)
    for _ in range(WARMUP):
        model(img, imgsz=imgsz, verbose=False)
    t0 = time.perf_counter()
    for _ in range(RUNS):
        model(img, imgsz=imgsz, verbose=False)
    dt = (time.perf_counter() - t0) / RUNS
    return 1.0 / dt if dt > 0 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-measure engines already in the table")
    args = ap.parse_args()

    table = {}
    if TABLE_PATH.exists():
        try:
            table = json.loads(TABLE_PATH.read_text())
        except ValueError:
            table = {}

    engines = sorted(MODELS_DIR.glob("*.engine"))
    if not engines:
        print(f"no engines in {MODELS_DIR} - run build_engines.sh first")
        return
    measured = 0
    for eng in engines:
        m = _ENGINE_RE.match(eng.name)
        if not m:
            continue
        model, imgsz = m.group("model"), int(m.group("imgsz"))
        if not args.force and str(imgsz) in table.get(model, {}):
            continue
        try:
            fps = measure_engine(eng, imgsz)
        except Exception as e:  # one broken engine must not kill the table
            print(f"  {eng.name}: FAILED ({type(e).__name__}: {e})")
            continue
        table.setdefault(model, {})[str(imgsz)] = round(fps, 1)
        measured += 1
        print(f"  {model}@{imgsz}: {fps:.1f} fps")
        # Persist incrementally so an interrupt keeps prior measurements.
        table["_meta"] = {
            "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "warmup": WARMUP, "runs": RUNS, "format": "engine",
        }
        TABLE_PATH.write_text(json.dumps(table, indent=1, sort_keys=True))
    print(f"fps table: {TABLE_PATH} ({measured} newly measured, "
          f"{sum(1 for k in table if not k.startswith('_'))} models)")


if __name__ == "__main__":
    main()
