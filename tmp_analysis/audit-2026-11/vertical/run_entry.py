#!/usr/bin/env python3
"""Entries: top (descending 0.3 h/s) vs side (0.3 h/s), ideal and degraded YOLO, with/without motion evidence."""
import sys
import io
import contextlib
from vsim import *

sc = Scene()
rx, ry, rw, rh = sc.roi
H = sc.H
V = 0.3 * H            # px/s
cx_mid = rx + rw / 2


def top_entry(t, v=V, x=cx_mid):
    top = ry - H + v * t                 # t=0: the feet touch the ROI top border
    return (x, min(top, ry + 1.8 * H))


def top_entry_head_first(t, v=V):        # an inverted dancer: modelled by the bottom-entry geometry
    return None


def side_entry(t, v=V):
    cx = rx - BOX_W * H / 2 + v * t
    return (min(cx, rx + 3 * H), ry + 2.0 * H)


def bottom_entry(t, v=V):
    top = ry + rh - v * t                # head first from the floor line
    return (cx_mid, max(top, ry + 1.8 * H))


print("scene: frame %dx%d ROI %s H=%.0f px; letterbox scale %.4f pad_x %d pad_y %d; tracker edge zone = %.0f lb px = %.2f h each side"
      % (sc.frame_w, sc.frame_h, sc.roi, H, *sc.lb, 0.12 * rw * sc.lb[0], 0.12 * rw / H))
print("shipped: max_age %d, edge x%.1f, established x%.1f, warmup thr %d, slots coast_s %.1f smart_hold %s static_guard %s"
      % (C.TRACKER_MAX_AGE, C.TRACKER_EDGE_EXIT_AGE_MULT, C.TRACKER_ESTABLISHED_MAX_AGE_MULT,
         C.TRACK_WARMUP_THRESHOLD, C.IDENTITY_SLOTS_COAST_S, C.IDENTITY_SLOTS_SMART_HOLD, C.IDENTITY_SLOTS_STATIC_GUARD))

N = 260
CASES = (
    ("top 0.3h/s feet-first vis_min .30", top_entry, {}),
    ("top 0.3h/s feet-first vis_min .50", top_entry, dict(vis_min=0.5)),
    ("top 0.3h/s feet-first p_partial .5", top_entry, dict(p_partial=0.5)),
    ("top 0.3h/s feet-first p_partial .33", top_entry, dict(p_partial=0.33)),
    ("bottom 0.3h/s head-first vis_min .30", bottom_entry, {}),
    ("side 0.3h/s vis_min .30", side_entry, {}),
    ("top 1.0h/s feet-first vis_min .30", lambda t: top_entry(t, v=1.0 * H), {}),
    ("side 1.0h/s vis_min .30", lambda t: side_entry(t, v=1.0 * H), {}),
)
for motion in (True, False):
    print(f"\n===== ENTRY, motion evidence {'ON (synthetic gray: bright body on a dark wall)' if motion else 'OFF (YOLO only: gray=None)'} =====")
    for label, pos, kw in CASES:
        with contextlib.redirect_stdout(io.StringIO()):
            rows, proc = run(sc, [Body(pos, **kw)], N, motion=motion)
        entry_report(sc, rows, label=label)
        if "-v" in sys.argv and label == CASES[0][0]:
            timeline(rows, every=5, upto=4.5)
