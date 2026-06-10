"""Generate ground-truth verification sheets for the operator pass (Phase 0).

One JPG per scenario manifest: every ~20th frame of the scored window,
strongly brightened (gamma 2.6 + CLAHE -- visualization only, NOT the
detector's path), tiled in a grid with absolute frame numbers.  The operator
counts visible people per tile and confirms/corrects expected_count
(constant or per-range) per scenarios/README.md.

Run from application/:  .venv/Scripts/python.exe ../tmp_analysis/gen_gt_sheets.py
Outputs: tmp_analysis/gt_sheets/<name>.jpg
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
APP = REPO / "application"
for p in (str(APP / "tests"), str(APP / "src"), str(APP)):
    if p not in sys.path:
        sys.path.insert(0, p)

import replay  # noqa: E402

OUT = HERE / "gt_sheets"
OUT.mkdir(parents=True, exist_ok=True)

STRIDE = 20
COLS = 5
TILE_W = 300


def brighten(frame: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    lut = (np.power(np.arange(256) / 255.0, 1 / 2.6) * 255).astype(np.uint8)
    g = cv2.createCLAHE(3.0, (8, 8)).apply(cv2.LUT(g, lut))
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def sheet_for(manifest_path: Path) -> None:
    m = json.loads(manifest_path.read_text())
    video = replay._find_recording(m["project"], m["slot"])
    if video is None:
        print(f"SKIP {m['name']}: recording missing")
        return
    cap = cv2.VideoCapture(str(video))
    tiles = []
    for idx in range(m["start"], m["start"] + m["frames"], STRIDE):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, fr = cap.read()
        if not ok:
            break
        v = brighten(fr)
        s = TILE_W / v.shape[1]
        v = cv2.resize(v, (TILE_W, int(v.shape[0] * s)))
        cv2.putText(v, f"{idx}", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 255), 2)
        tiles.append(v)
    cap.release()
    if not tiles:
        print(f"SKIP {m['name']}: no frames")
        return
    th = min(t.shape[0] for t in tiles)
    tiles = [t[:th] for t in tiles]
    rows = []
    for i in range(0, len(tiles), COLS):
        row = tiles[i:i + COLS]
        while len(row) < COLS:
            row.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(row))
    sheet = np.vstack(rows)
    header = np.zeros((46, sheet.shape[1], 3), np.uint8)
    cv2.putText(header,
                f"{m['name']}  --  {m['project']} slot {m['slot']}  "
                f"[{m['start']},{m['start']+m['frames']})  expected N = "
                f"{m['expected_count']}  (verify: count people per tile)",
                (8, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    out = OUT / f"{m['name']}.jpg"
    cv2.imwrite(str(out), np.vstack([header, sheet]),
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    print("wrote", out, f"({len(tiles)} tiles)")


def main():
    scen = APP / "tests" / "scenarios"
    for p in sorted(scen.glob("*.json")) + sorted((scen / "drafts").glob("*.json")):
        sheet_for(p)


if __name__ == "__main__":
    main()
