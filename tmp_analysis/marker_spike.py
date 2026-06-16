"""Phase-0b marker-detection prototype (Track: tracking-robustness / IR markers).

A throwaway spike for the IR-retroreflective-marker direction (see the plan at
~/.claude/plans/i-have-a-propsective-snug-cupcake.md). It does the whole marker
stage: threshold the IR frame high → connected-components → bright-blob centroids.

Two uses:
  1. BASELINE (now, on existing footage WITHOUT markers): how many near-saturated
     bright spots occur NATURALLY at a high threshold? That is the false-positive
     floor a real retroreflective marker would have to clear. Few/none ⇒ markers
     separate cleanly; many ⇒ we need the exclusion mask / coding.
  2. VALIDATE (later, on a Phase-0a recording WITH markers): does the marker show
     up as a clean, isolated saturated blob across distance/angle?

Run (from repo root, venv at application/.venv):
  application/.venv/Scripts/python.exe tmp_analysis/marker_spike.py \
      --video projects/1_TANGO_HANGAR-texturedbg/recordings/slot_1_*.avi \
      --thresholds 230,245,254 --frames 400 --stride 2
"""
import argparse
import glob
from pathlib import Path

import cv2
import numpy as np


def analyze(video, thresholds, max_frames, stride, min_area, max_area):
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {video}")
    per_thresh = {t: [] for t in thresholds}      # per-frame qualifying-blob counts
    biggest = {t: [] for t in thresholds}          # per-frame largest qualifying area
    frame_max = []                                  # per-frame max gray value
    sat_frac = []                                   # fraction of pixels == 255
    fidx = processed = 0
    while max_frames is None or processed < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if fidx % stride == 0:
            gray = (cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    if frame.ndim == 3 else frame)
            frame_max.append(int(gray.max()))
            sat_frac.append(float(np.mean(gray >= 255)))
            for t in thresholds:
                _, mask = cv2.threshold(gray, t, 255, cv2.THRESH_BINARY)
                n, _lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
                areas = [int(stats[i, cv2.CC_STAT_AREA]) for i in range(1, n)
                         if min_area <= stats[i, cv2.CC_STAT_AREA] <= max_area]
                per_thresh[t].append(len(areas))
                biggest[t].append(max(areas) if areas else 0)
            processed += 1
        fidx += 1
    cap.release()
    return per_thresh, biggest, frame_max, sat_frac, processed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="path (globs ok)")
    ap.add_argument("--thresholds", default="230,245,254",
                    help="comma list of 0-255 brightness thresholds to sweep")
    ap.add_argument("--frames", type=int, default=400)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--min-area", type=int, default=2, help="px, blob area floor")
    ap.add_argument("--max-area", type=int, default=2000, help="px, blob area cap")
    args = ap.parse_args()

    matches = sorted(glob.glob(args.video))
    video = matches[0] if matches else args.video
    thresholds = [int(t) for t in args.thresholds.split(",")]

    per, biggest, fmax, sat, n = analyze(
        video, thresholds, args.frames, args.stride, args.min_area, args.max_area)

    print(f"\n=== marker baseline: {Path(video).name}  ({n} frames sampled) ===")
    fmax = np.array(fmax)
    sat = np.array(sat)
    print(f"frame max-gray: min {fmax.min()}  median {int(np.median(fmax))}  "
          f"max {fmax.max()}   (255 = saturated)")
    print(f"saturated-pixel fraction: median {np.median(sat)*100:.4f}%  "
          f"max {sat.max()*100:.4f}%")
    print(f"\nat threshold T, # qualifying bright blobs/frame "
          f"(area {args.min_area}-{args.max_area}px) - this is the GLINT FLOOR:")
    print(f"  {'T':>4} | {'mean':>6} {'median':>6} {'max':>4} | "
          f"{'%frames 0':>9} {'%>=1':>6} {'%>=2':>6} | {'big area':>8}")
    for t in thresholds:
        c = np.array(per[t])
        b = np.array(biggest[t])
        z = np.mean(c == 0) * 100
        ge1 = np.mean(c >= 1) * 100
        ge2 = np.mean(c >= 2) * 100
        print(f"  {t:>4} | {c.mean():>6.2f} {int(np.median(c)):>6} {c.max():>4} | "
              f"{z:>8.1f}% {ge1:>5.1f}% {ge2:>5.1f}% | {b.max():>8}")
    print("\nread: a marker (saturated retroreflector) sits at/near 255. Pick the "
          "lowest T where the GLINT FLOOR (%>=1 on this marker-LESS footage) is ~0 "
          "- that is the headroom a real marker would exploit.")


if __name__ == "__main__":
    main()
