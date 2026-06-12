#!/usr/bin/env python3
"""Venue fit: which optic works for a given stage, and from what distance.

Answers the two operator questions (docs/OPTICS.md "Venue fit"):
  1) I have a stage WxH and a spot at distance D - does a lens fit?
       python extra/venue_fit.py --stage 12x8 --distance 20
  2) I have a stage WxH - what distance range should I ask the organiser for?
       python extra/venue_fit.py --stage 12x8

Geometry: IDS U3-34E0XCP (IMX664 mono, 2.9 um, usable 2688x1528) with the
standard on-device crop budget (IDS_CROP_PIXELS ~ 2.3 MP, IDS_RATIO 0.5-2.0).
Floors are the Phase 2b corpus-measured detection floors (ROADMAP 4.2 2b).
Pure stdlib - runs anywhere.
"""
from __future__ import annotations

import argparse
import math
import sys

PIXEL_MM = 0.0029          # IMX664 pixel pitch
SENSOR_W_PX = 2688         # usable sensor (IDS spec)
SENSOR_H_PX = 1528
BUDGET_PX = 1528 * 1528    # IDS_CROP_PIXELS default (~2.3 MP)
RATIO_MAX = 2.0            # IDS_RATIO clamp (W/H)

LENSES = {                 # focal length mm
    "M118FM08 (8mm)": 8.0,
    "M118FM06 (6mm)": 6.0,
}
FLOOR_COMFORT_PX = 110.0   # Phase 2b: net-height target met without gymnastics
FLOOR_WORKABLE_PX = 70.0   # Phase 2b: tight ROI + imgsz 1920 + yolo11x territory


def d_min_coverage(f: float, w: float, h: float, full_sensor: bool) -> tuple:
    """Smallest camera distance (m) at which the capture can frame WxH.

    Returns (d_min, binding_constraint_name)."""
    terms = {
        "sensor height (1528 px)": f * h / (PIXEL_MM * SENSOR_H_PX),
        "sensor width (2688 px)": f * w / (PIXEL_MM * SENSOR_W_PX),
    }
    if not full_sensor:
        terms["2.3 MP crop budget"] = (
            f * math.sqrt(w * h) / (PIXEL_MM * math.sqrt(BUDGET_PX)))
        terms["crop ratio clamp (W/H <= 2)"] = (
            f * w / (PIXEL_MM * math.sqrt(BUDGET_PX * RATIO_MAX)))
    name = max(terms, key=lambda k: terms[k])
    return terms[name], name


def d_max_dancer(f: float, dancer_m: float, floor_px: float) -> float:
    """Largest camera distance (m) keeping the dancer above the px floor."""
    return f * dancer_m * 1000.0 / (PIXEL_MM * 1000.0 * floor_px)


def dancer_px(f: float, dancer_m: float, d: float) -> float:
    return f * dancer_m / (PIXEL_MM * d)


def fmt(v: float) -> str:
    return f"{v:.1f}"


def main():
    ap = argparse.ArgumentParser(
        description="Which optic fits a stage, and from what distance "
                    "(see docs/OPTICS.md)")
    ap.add_argument("--stage", required=True,
                    help="stage size WxH in meters, e.g. 12x8")
    ap.add_argument("--distance", type=float, default=None,
                    help="available camera distance in meters (optional)")
    ap.add_argument("--dancer", type=float, default=1.70,
                    help="dancer height in meters (default 1.70)")
    ap.add_argument("--full-sensor", action="store_true",
                    help="assume full-sensor capture (no 2.3 MP crop budget; "
                         "costs fps)")
    args = ap.parse_args()

    try:
        w, h = (float(v) for v in args.stage.lower().split("x"))
    except ValueError:
        sys.exit("--stage must look like 12x8 (meters)")

    print(f"stage {fmt(w)} x {fmt(h)} m, dancer {args.dancer:.2f} m, "
          f"{'full sensor' if args.full_sensor else 'standard 2.3 MP crop'}")
    print()
    fitting = []
    for name, f in LENSES.items():
        dmin, binding = d_min_coverage(f, w, h, args.full_sensor)
        dmax_c = d_max_dancer(f, args.dancer, FLOOR_COMFORT_PX)
        dmax_w = d_max_dancer(f, args.dancer, FLOOR_WORKABLE_PX)
        print(f"--- {name} ---")
        print(f"  coverage needs D >= {fmt(dmin)} m (binding: {binding})")
        if dmin <= dmax_c:
            print(f"  COMFORTABLE distance range: {fmt(dmin)} - {fmt(dmax_c)} m"
                  f"  (dancer >= {FLOOR_COMFORT_PX:.0f} px)")
        else:
            print(f"  no comfortable range (coverage needs {fmt(dmin)} m but "
                  f"dancer drops below {FLOOR_COMFORT_PX:.0f} px past "
                  f"{fmt(dmax_c)} m)")
        if dmin <= dmax_w:
            print(f"  workable distance range:    {fmt(dmin)} - {fmt(dmax_w)} m"
                  f"  (dancer >= {FLOOR_WORKABLE_PX:.0f} px; tight ROI + "
                  f"imgsz 1920 + yolo11x, degraded on dark scenes)")
        else:
            print("  does not fit this stage at any distance")

        if args.distance is not None:
            d = args.distance
            px = dancer_px(f, args.dancer, d)
            if d < dmin:
                verdict = (f"TOO CLOSE - cannot frame the stage "
                           f"(needs >= {fmt(dmin)} m)")
            elif d <= dmax_c:
                verdict = f"FITS comfortably (dancer ~ {px:.0f} px)"
            elif d <= dmax_w:
                verdict = (f"WORKABLE only (dancer ~ {px:.0f} px - tight ROI, "
                           f"imgsz 1920, yolo11x; avoid for dark venues)")
            else:
                verdict = (f"TOO FAR - dancer ~ {px:.0f} px < "
                           f"{FLOOR_WORKABLE_PX:.0f} px floor")
            print(f"  at D = {fmt(d)} m: {verdict}")
            if dmin <= d <= dmax_w:
                fitting.append((name, px))
        print()

    if args.distance is not None:
        if fitting:
            best = max(fitting, key=lambda t: t[1])
            print(f"RECOMMENDATION: {best[0]} (dancer ~ {best[1]:.0f} px at "
                  f"{fmt(args.distance)} m)")
        else:
            print("RECOMMENDATION: neither lens fits at this distance - "
                  "renegotiate the camera spot (see ranges above), tighten "
                  "the framed area, or accept partial-stage coverage.")


if __name__ == "__main__":
    main()
