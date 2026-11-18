"""detection-pipeline#4: the display gamma is applied to the MOG2 / frame-diff input; a slider move is a
global level step for the background model (learn rate 0.001) with no reset."""
from chain import *
import cv2
from core.config import (MOTION_LOWLIGHT_LUMA_THRESHOLD, MOTION_BRIDGE_MOG2_LEARN_RATE,
                         MOTION_FIRST_ASPECT_RANGE, MOTION_FIRST_INCLUDE_SHADOWS)

rng = np.random.default_rng(7)
N = 640
yy, xx = np.mgrid[0:N, 0:N]
base = 30 + 10 * np.sin(xx / 90.0) * np.cos(yy / 70.0) + rng.normal(0, 5, (N, N))     # a dark textured wall, mean ~30 DN
base = cv2.GaussianBlur(base.astype(np.float32), (0, 0), 1.5)

def frame():
    return np.clip(base + rng.normal(0, 2.0, (N, N)), 0, 255).astype(np.uint8)        # sensor noise sigma 2 DN

def run(g1, g2, var=16.0, scale=0.7, pre=300, post=400):
    p, clock, rec = mk_proc(osc=False)
    p.set_motion_var_threshold(var)
    p.set_motion_scale(scale)
    md = p.motion_model.detector
    def feed(g):
        p.enhancer.gamma = g                         # what app._cb_gamma_change does (app.py:1637-1641)
        p._feed_motion_detectors(frame())            # pipeline.py:1773-1788
        m = md._fg_mask
        blobs = md.detect(int(H), aspect_range=MOTION_FIRST_ASPECT_RANGE,
                          include_shadows=MOTION_FIRST_INCLUDE_SHADOWS, allow_during_warmup=True)
        pres = md.motion_ratio_in_bbox(300, 300, 0.35 * H, H, use_clean_mask=False, include_shadows=True,
                                       require_coherence=False)
        _b, fd = md.frame_diff_blob_in_bbox(300.0, 300.0, 0.35 * H, H, min_ratio=0.02)
        return (float((m == 255).mean()), float((md._clean_mask > 0).mean()), len(blobs), pres, fd, md.last_brightness)
    for _ in range(pre):
        s = feed(g1)
    print("\ngamma %.2f -> %.2f (var %g @ scale %.1f, MOG2 learn rate %g, low-light switch below luma %d)"
          % (g1, g2, var, scale, MOTION_BRIDGE_MOG2_LEARN_RATE, MOTION_LOWLIGHT_LUMA_THRESHOLD))
    print("  steady state before: fg %.3f%%, clean fg %.3f%%, blobs %d, presence ratio in a dancer box %.3f, frame-diff %.3f, motion-gray luma %.0f (%s)"
          % (100 * s[0], 100 * s[1], s[2], s[3], s[4], s[5], "low-light: var x2" if s[5] < MOTION_LOWLIGHT_LUMA_THRESHOLD else "normal"))
    back = None
    rows = []
    for i in range(1, post + 1):
        s = feed(g2)
        if i in (1, 2, 5, 20, 50, 100, 104, 106, 110, 150, 400):
            rows.append("    +%3d f: fg %6.2f%%, clean %6.2f%%, blobs %d, presence %.3f, frame-diff %.3f" % (i, 100 * s[0], 100 * s[1], s[2], s[3], s[4]))
        if back is None and s[0] < 0.05:
            back = i
    print("\n".join(rows))
    print("  MOG2 mask back under 5 %% foreground after %s frames (= %s s at 20 fps); luma now %.0f (%s)"
          % (back, None if back is None else round(back / 20.0, 1), s[5],
             "low-light: var x2" if s[5] < MOTION_LOWLIGHT_LUMA_THRESHOLD else "normal"))

run(1.0, 1.5)
run(0.73, 1.8)      # the two calibrations the night project actually had (PLAN_25M C.12: 0.73 -> 1.8)
run(1.0, 1.1)
