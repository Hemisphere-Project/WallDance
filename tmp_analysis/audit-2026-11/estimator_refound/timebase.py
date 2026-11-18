"""Synthetic, GPU-free probe of the tracker's time base: one dancer on a pendulum-like swing,
perfect YOLO-like detections, fed at 20 fps, at 10 fps, and at 20 fps with 1.7 s camera stalls.
Reads the tracker's REPORTED stream only.  Writes nothing."""
import sys, math, os
import numpy as np
import core.config as config
from core.tracker import DancerTracker, DancerTrack

H = 190.0          # dancer height in tracker px (25 m: ~188 px, REVIEW 1.7)
W = 1280
OFFS = np.array([[0,-.42],[-.03,-.45],[.03,-.45],[-.06,-.43],[.06,-.43],[-.12,-.28],[.12,-.28],
                 [-.17,-.1],[.17,-.1],[-.19,.05],[.19,.05],[-.09,.05],[.09,.05],[-.1,.27],[.1,.27],
                 [-.1,.47],[.1,.47]]) * H

def det(cx, cy):
    k = OFFS + np.array([cx, cy]); c = np.full(17, 0.8)
    b = np.array([cx - 0.2*H, cy - 0.5*H, 0.4*H, H])
    return (k, c, b)

def pos(t, A, T):
    return 640.0 + A*H*math.sin(2*math.pi*t/T), 500.0

def run(name, fps, dur, A, T, stalls=(), intermittent=None):
    DancerTrack._id_counter = 0
    trk = DancerTracker(); trk.logger.enabled = False
    if intermittent is not None: trk.intermittent_confirm = intermittent
    trk.set_frame_dimensions(W); trk.set_person_height(int(H))
    times = []
    t = 0.0; dt = 1.0/fps
    stall_iter = sorted(stalls)
    while t < dur:
        times.append(t)
        t += dt
        if stall_iter and t >= stall_iter[0][0]:
            t += stall_iter[0][1]; stall_iter.pop(0)
    rows = []
    for i, t in enumerate(times):
        cx, cy = pos(t, A, T)
        conf = trk.update([det(cx, cy)], frame_number=i)
        ids = [c.track_id for c in conf]
        ok = False
        for c in conf:
            p = c.get_smoothed_centroid()
            if math.hypot(p[0]-cx, p[1]-cy) <= 0.75*H: ok = True
        rows.append((t, ids, ok))
    warm = 2.0
    scored = [r for r in rows if r[0] >= warm]
    cov = sum(1 for r in scored if r[2]) / len(scored)
    all_ids = sorted({i for r in scored for i in r[1]})
    # longest hole in seconds (time between consecutive on-dancer reported frames)
    last = None; hole = 0.0; nholes = 0
    for t, ids, ok in scored:
        if ok:
            if last is not None and t - last > 1.5/fps + 1e-9:
                gap = t - last
                # do not count the stall itself: subtract known stall durations inside (last, t]
                for s0, sd in stalls:
                    if last < s0 + sd and s0 <= t: gap -= sd
                if gap > 1.5/fps + 1e-9:
                    hole = max(hole, gap); nholes += 1
            last = t
    peak = 2*math.pi*A/T
    print(f"{name:34s} fps={fps:>4} peak={peak:4.1f} h/s = {peak/fps:5.3f} h/frame | on-dancer reported {cov:6.1%} | "
          f"ids {len(all_ids):2d} | holes {nholes:2d} longest {hole:4.2f} s (stall time excluded) | internal tracks minted {DancerTrack._id_counter}")

print("gates: displacement", config.TRACKER_MAX_DISPLACEMENT_RATIO*config.TRACKER_MATCH_GATE_RATIO, "h/frame; creation (centre)",
      config.TRACKER_NEW_TRACK_GATE_RATIO*config.TRACKER_CENTER_NEW_TRACK_GATE_MULT, "h; warm-up", config.TRACK_WARMUP_THRESHOLD, "frames")
for A, T in ((1.0, 3.0), (1.5, 2.5), (1.5, 2.0)):
    run(f"swing A={A}h T={T}s  20 fps", 20, 60, A, T)
    run(f"swing A={A}h T={T}s  10 fps", 10, 60, A, T)
    run(f"swing A={A}h T={T}s  20 fps + 3 stalls 1.7 s", 20, 60, A, T, stalls=((15.0,1.7),(30.2,1.7),(45.9,1.7)))
