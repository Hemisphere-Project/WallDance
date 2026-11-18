"""Reduced re-run of the re-found case's time-base probe (tracker alone, no motion model). Writes nothing."""
import sys, math, time
import numpy as np
from core.tracker import DancerTracker, DancerTrack
H = 190.0; W = 1280
OFFS = np.array([[0,-.42],[-.03,-.45],[.03,-.45],[-.06,-.43],[.06,-.43],[-.12,-.28],[.12,-.28],
                 [-.17,-.1],[.17,-.1],[-.19,.05],[.19,.05],[-.09,.05],[.09,.05],[-.1,.27],[.1,.27],
                 [-.1,.47],[.1,.47]]) * H
def det(cx, cy):
    return (OFFS + np.array([cx, cy]), np.full(17, 0.8), np.array([cx - 0.2*H, cy - 0.5*H, 0.4*H, H]))
def pos(t, A, T): return 640.0 + A*H*math.sin(2*math.pi*t/T), 500.0
def run(fps, duty, A, T, seed, dur=60.0):
    DancerTrack._id_counter = 0
    rng = np.random.default_rng(seed)
    trk = DancerTracker(); trk.logger.enabled = False
    trk.set_frame_dimensions(W); trk.set_person_height(int(H))
    t = 0.0; dt = 1.0/fps; i = 0; rows = []
    while t < dur:
        cx, cy = pos(t, A, T)
        dets = [det(cx, cy)] if rng.random() < duty else []
        conf = trk.update(dets, frame_number=i)
        ok = any(math.hypot(c.get_smoothed_centroid()[0]-cx, c.get_smoothed_centroid()[1]-cy) <= 0.75*H for c in conf)
        rows.append((t, ok)); t += dt; i += 1
    first = next((r[0] for r in rows if r[1]), None)
    sc = [r for r in rows if first is not None and r[0] >= first]
    cov = sum(1 for r in sc if r[1]) / max(1, len(sc))
    hole = 0.0; cur = None
    for tt, ok in sc:
        if not ok: cur = tt if cur is None else cur
        elif cur is not None: hole = max(hole, tt - cur); cur = None
    return first, cov, hole
t0 = time.time()
for duty in (1.0, 0.6):
    for fps in (20, 10):
        R = [run(fps, duty, 1.0, 3.0, s) for s in range(3)]
        print(f"duty {duty} fps {fps}: first {np.median([r[0] if r[0] is not None else 99 for r in R]):.2f} s  cov {np.median([r[1] for r in R]):.3f}  hole med {np.median([r[2] for r in R]):.2f} s   [{time.time()-t0:.0f}s]", flush=True)
