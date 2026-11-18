"""Motion evidence (MOG2 + frame-diff) under dusk light drift (audit repro, CPU only, synthetic).

Drives the real core.motion_detector.MotionDetector the way the pipeline does: the raw ROI gray goes
through the enhancer's gamma LUT (pipeline._fixed_gamma_for_motion) and then MotionDetector.feed().
  D1  the light ratio at which the low-light switch (post-gamma ROI mean < 55) flips, per gamma
  D2  frame-diff evidence of a moving dancer and of an empty box as the light falls
      (crossval / cold blobs: 15 DN, ratio >= 0.02, x1.2 in low light; tracker bridge: 6 DN, >= 0.02)
  D3  the flip itself: MOG2 mask and cold-blob candidates while the mean crosses 55 at the dusk rate
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from scene import Scene, PH, W, H, gamma_lut  # noqa: E402

from core.motion_detector import MotionDetector  # noqa: E402
from core.config import (MOTION_LOWLIGHT_LUMA_THRESHOLD, MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO,  # noqa: E402
                         MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT, MOTION_BRIDGE_FRAME_DIFF_THRESHOLD,
                         MOTION_BRIDGE_FRAME_DIFF_MIN_RATIO, MOTION_FIRST_ASPECT_RANGE,
                         MOTION_FIRST_INCLUDE_SHADOWS, MOTION_BRIDGE_MOG2_VAR_THRESHOLD,
                         MOTION_BRIDGE_MOG2_SCALE)

GAMMAS = (1.54, 1.27, 1.0, 0.8, 0.73)   # seed for 70 -> 110; a ladder rung; ladder 1.0; ladder floor; D28 project


def section_d1():
    print("\n=== D1  low-light switch: light ratio at which the post-gamma ROI mean crosses "
          f"{MOTION_LOWLIGHT_LUMA_THRESHOLD} (servo target: raw median 70) ===")
    sc = Scene(seed=1, day_frac=0.7)
    for g in GAMMAS:
        lut = gamma_lut(g)
        mean_at = lambda day: float(cv2.LUT(np.clip(np.rint(sc.clean(day=day)), 0, 255).astype(np.uint8), lut).mean())
        m1, m0 = mean_at(1.0), mean_at(0.0)
        if m1 < MOTION_LOWLIGHT_LUMA_THRESHOLD:
            where = "already in low light at Calibrate (no flip during the show)"
        elif m0 >= MOTION_LOWLIGHT_LUMA_THRESHOLD:
            where = "never flips down to 0.31x"
        else:
            lo, hi = 0.0, 1.0
            for _ in range(30):
                mid = 0.5 * (lo + hi)
                if mean_at(mid) < MOTION_LOWLIGHT_LUMA_THRESHOLD:
                    lo = mid
                else:
                    hi = mid
            where = f"flips at light {sc.light_ratio_true(hi):.2f}x"
        # pure-gain equivalent, analytic on the median: 255 * (55/255)^gamma / 70
        ana = 255.0 * (MOTION_LOWLIGHT_LUMA_THRESHOLD / 255.0) ** g / 70.0
        print(f"gamma {g:4.2f}: post-gamma mean {m1:5.1f} at Calibrate, {m0:5.1f} at night (0.31x) -> {where}"
              f"   [uniform-wall formula: {ana:4.2f}x]")


def run_md(sc, gamma, day, dancers_fn, n, var=None):
    md = MotionDetector()
    if var is not None:
        md.set_var_threshold(var)
    lut = gamma_lut(gamma)
    return md, lut


def section_d2():
    print("\n=== D2  frame-diff evidence vs light (moving dancer 3.25 px/frame = 0.5 h/s; empty box) ===")
    print("    ratio = share of box pixels whose frame-to-frame change exceeds the threshold; gate >= "
          f"{MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO} (x{MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT} in low light)")
    for sigma70 in (2.5, 5.0):
        for gamma in (1.54, 1.0, 0.8):
            sc = Scene(seed=1, day_frac=0.7, sigma70=sigma70)
            lut = gamma_lut(gamma)
            print(f"-- sensor noise {sigma70} DN at 70 DN, gamma {gamma}")
            for day in (1.0, 0.75, 0.5, 0.25, 0.0):
                md = MotionDetector()
                r15 = {1.5: [], 0.7: [], 1.15: []}
                r6 = {1.5: [], 0.7: [], 1.15: []}
                e15, e6 = [], []
                for i in range(40):
                    x = 300 + 3.25 * i
                    dancers = [(x, 150, 1.5), (x + 300, 280, 0.7), (x + 600, 400, 1.15)]
                    gray = cv2.LUT(sc.frame(day=day, dancers=dancers), lut)
                    md.feed(gray)
                    if i < 5:
                        continue
                    for d in dancers:
                        bx, by, bw, bh = d[0] - 0.2 * PH, d[1] - 0.5 * PH, 0.4 * PH, PH
                        r15[d[2]].append(md.frame_diff_blob_in_bbox(bx, by, bw, bh, min_ratio=0.02)[1])
                        r6[d[2]].append(md.frame_diff_blob_in_bbox(
                            bx, by, bw, bh, threshold=MOTION_BRIDGE_FRAME_DIFF_THRESHOLD,
                            min_ratio=MOTION_BRIDGE_FRAME_DIFF_MIN_RATIO)[1])
                    e15.append(md.frame_diff_blob_in_bbox(1100, 60, 0.4 * PH, PH, min_ratio=0.02)[1])
                    e6.append(md.frame_diff_blob_in_bbox(
                        1100, 60, 0.4 * PH, PH, threshold=MOTION_BRIDGE_FRAME_DIFF_THRESHOLD,
                        min_ratio=MOTION_BRIDGE_FRAME_DIFF_MIN_RATIO)[1])
                low = md.last_brightness < MOTION_LOWLIGHT_LUMA_THRESHOLD
                thr = MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO * (MOTION_CROSSVAL_LOWLIGHT_RATIO_MULT if low else 1.0)
                f = lambda a: f"{float(np.median(a)):5.3f}"
                flag = lambda a: "pass" if float(np.median(a)) >= thr else "FAIL"
                print(f"  light {sc.light_ratio_true(day):4.2f}x mean {md.last_brightness:5.1f} "
                      f"{'LOW ' if low else 'norm'} gate {thr:5.3f} | 15 DN: rho1.5 {f(r15[1.5])} {flag(r15[1.5])}"
                      f"  rho0.7 {f(r15[0.7])} {flag(r15[0.7])}  rho1.15 {f(r15[1.15])} {flag(r15[1.15])}"
                      f"  empty {f(e15)} {'GHOST-OPEN' if float(np.median(e15)) >= thr else 'ok'}"
                      f" | 6 DN: rho1.5 {f(r6[1.5])}  rho0.7 {f(r6[0.7])}  rho1.15 {f(r6[1.15])}  empty {f(e6)}"
                      f"{' NOISE>=0.02' if float(np.median(e6)) >= MOTION_BRIDGE_FRAME_DIFF_MIN_RATIO else ''}")


def section_d3():
    print("\n=== D3  the low-light flip at the dusk rate (gamma 1.27, MOG2 var 16 and the default "
          f"{MOTION_BRIDGE_MOG2_VAR_THRESHOLD}, scale {MOTION_BRIDGE_MOG2_SCALE}; 3 moving dancers) ===")
    gamma = 1.27
    lut = gamma_lut(gamma)
    for var in (16.0, float(MOTION_BRIDGE_MOG2_VAR_THRESHOLD)):
        sc = Scene(seed=1, day_frac=0.7)
        # find the daylight share where the mean (with the dancers) is ~55.4, then cross at the 20 min rate
        def mean_at(day, t=0.0):
            return float(cv2.LUT(np.clip(np.rint(sc.clean(day=day, dancers=dancers_at(t))), 0, 255)
                                 .astype(np.uint8), lut).mean())

        def dancers_at(t):
            return [(350 + 150 * np.sin(2 * np.pi * t / 40.0), 280 + 60 * np.sin(2 * np.pi * t / 23.0), 1.5),
                    (640 + 30 * np.sin(2 * np.pi * t / 9.0), 250, 0.7),
                    (930 + 120 * np.sin(2 * np.pi * t / 15.0), 300, 1.15)]
        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            if mean_at(mid) < MOTION_LOWLIGHT_LUMA_THRESHOLD + 0.4:
                lo = mid
            else:
                hi = mid
        day0 = hi
        rate = 1.0 / 1200.0                     # daylight share per second (20 min dusk)
        md = MotionDetector()
        md.set_var_threshold(var)
        n_warm, n_run = 400, 1200               # 20 s warm-up at a fixed light, then 60 s of ramp
        toggles, low_prev = 0, None
        rows = []
        for i in range(n_warm + n_run):
            t = i / 20.0
            day = day0 - max(0.0, (i - n_warm) / 20.0) * rate
            dancers = dancers_at(t)
            gray = cv2.LUT(sc.frame(day=day, dancers=dancers), lut)
            md.feed(gray)
            low = md.last_brightness < MOTION_LOWLIGHT_LUMA_THRESHOLD
            if i >= n_warm:
                if low_prev is not None and low != low_prev:
                    toggles += 1
                # foreground outside the dancers' boxes (clean mask = definite fg, opened)
                m = md._clean_mask.copy()
                s = md._scale
                for d in dancers:
                    x0, x1 = int((d[0] - 0.4 * PH) * s), int((d[0] + 0.4 * PH) * s)
                    y0, y1 = int((d[1] - 0.7 * PH) * s), int((d[1] + 0.7 * PH) * s)
                    m[max(0, y0):y1, max(0, x0):x1] = 0
                bg_fg = float((m > 0).mean())
                blobs = md.detect(PH, aspect_range=MOTION_FIRST_ASPECT_RANGE,
                                  include_shadows=MOTION_FIRST_INCLUDE_SHADOWS)
                off = [b for b in blobs
                       if all(abs(b.centroid[0] - d[0]) > 0.6 * PH or abs(b.centroid[1] - d[1]) > 0.8 * PH
                              for d in dancers)]
                gated = [b for b in off if md.frame_diff_blob_in_bbox(
                    float(b.bbox[0]), float(b.bbox[1]), float(b.bbox[2]), float(b.bbox[3]),
                    min_ratio=MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO)[1] >= MOTION_CROSSVAL_FRAMEDIFF_MIN_RATIO]
                rows.append((i - n_warm, md.last_brightness, low, low_prev is not None and low != low_prev,
                             bg_fg, len(off), len(gated)))
            low_prev = low
        r = np.array([(a, b, c, d, e, f, g) for a, b, c, d, e, f, g in rows], dtype=float)
        tog = r[r[:, 3] > 0]
        first, last = (int(tog[0, 0]), int(tog[-1, 0])) if len(tog) else (None, None)
        print(f"-- var {var:.0f}: mean {r[0, 1]:.2f} -> {r[-1, 1]:.2f} over {n_run / 20:.0f} s; "
              f"low-light toggles {toggles} (frames {first}..{last})")
        for name, sel in (("before the first toggle", r[:, 0] < (first if first is not None else 1e9)),
                          ("toggle frames", r[:, 3] > 0),
                          ("the 20 frames after each toggle",
                           np.array([any(0 < a - b <= 20 for b in tog[:, 0]) for a in r[:, 0]])),
                          ("after the last toggle + 100 frames",
                           r[:, 0] > (last + 100 if last is not None else 1e9))):
            q = r[sel]
            if not len(q):
                print(f"   {name:<36}: no frames")
                continue
            print(f"   {name:<36}: frames {len(q):4d}  background MOG2 fg share median {np.median(q[:, 4]):.4f} "
                  f"max {q[:, 4].max():.4f} | person-sized blobs off the dancers per frame: mean {q[:, 5].mean():.2f} "
                  f"max {int(q[:, 5].max())} | passing the frame-diff gate: mean {q[:, 6].mean():.2f} max {int(q[:, 6].max())}")


if __name__ == "__main__":
    t0 = time.time()
    for w in (sys.argv[1:] or ["d1", "d2", "d3"]):
        globals()[f"section_{w}"]()
    print(f"\n[{time.time() - t0:.0f} s]")
