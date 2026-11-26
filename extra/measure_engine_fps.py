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
# P1 (Plan Cordée rev 2): rectangles <model>_<height>x<width>.engine.  Kept under the table's "_rect" key
# ({model: {"HxW": fps}}): calib2.load_fps_table reads {model: {int imgsz: fps}} and skips "_" keys, so a
# rectangle can never cost a model its square entries.
_RECT_RE = re.compile(r"^(?P<model>.+-pose)_(?P<h>\d+)x(?P<w>\d+)\.engine$")


def parse_engine_name(name: str):
    """(model, table key, (height, width)) for a square or a rectangle engine file name, or None."""
    m = _ENGINE_RE.match(name)
    if m:
        sz = int(m.group("imgsz"))
        return m.group("model"), str(sz), (sz, sz)
    m = _RECT_RE.match(name)
    if m:
        h, w = int(m.group("h")), int(m.group("w"))
        return m.group("model"), f"{h}x{w}", (h, w)
    return None


def measure_engine(path: Path, imgsz) -> float:
    """``imgsz``: a square size or (height, width)."""
    from ultralytics import YOLO
    h, w = (imgsz, imgsz) if isinstance(imgsz, int) else (int(imgsz[0]), int(imgsz[1]))
    model = YOLO(str(path), task="pose")
    img = np.zeros((h, w, 3), dtype=np.uint8)
    arg = imgsz if isinstance(imgsz, int) else [h, w]
    for _ in range(WARMUP):
        model(img, imgsz=arg, verbose=False)
    t0 = time.perf_counter()
    for _ in range(RUNS):
        model(img, imgsz=arg, verbose=False)
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
        parsed = parse_engine_name(eng.name)
        if parsed is None:
            continue
        model, key, hw = parsed
        rect = hw[0] != hw[1] or "x" in key
        row = table.setdefault("_rect", {}).setdefault(model, {}) if rect else table.setdefault(model, {})
        if not args.force and key in row:
            continue
        try:
            fps = measure_engine(eng, hw if rect else hw[0])
        except Exception as e:  # one broken engine must not kill the table
            print(f"  {eng.name}: FAILED ({type(e).__name__}: {e})")
            continue
        row[key] = round(fps, 1)
        measured += 1
        print(f"  {model}@{key}: {fps:.1f} fps")
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
