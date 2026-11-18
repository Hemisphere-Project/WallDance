"""Same probe with imperfect YOLO duty (seeded i.i.d. misses), tracker alone (no motion model:
no bridge, no synthetic blobs -> pessimistic for the shipped pipeline; isolates the frame-based
warm-up / decay / gates).  Writes nothing."""
import sys, math
import numpy as np
import core.config as config
from core.tracker import DancerTracker, DancerTrack
H = 190.0; W = 1280
OFFS = np.array([[0,-.42],[-.03,-.45],[.03,-.45],[-.06,-.43],[.06,-.43],[-.12,-.28],[.12,-.28],
                 [-.17,-.1],[.17,-.1],[-.19,.05],[.19,.05],[-.09,.05],[.09,.05],[-.1,.27],[.1,.27],
                 [-.1,.47],[.1,.47]]) * H
def det(cx, cy):
    return (OFFS + np.array([cx, cy]), np.full(17, 0.8), np.array([cx - 0.2*H, cy - 0.5*H, 0.4*H, H]))
def pos(t, A, T): return 640.0 + A*H*math.sin(2*math.pi*t/T), 500.0

def run(fps, duty, A, T, seed, dur=120.0, stalls=()):
    DancerTrack._id_counter = 0
    rng = np.random.default_rng(seed)
    trk = DancerTracker(); trk.logger.enabled = False
    trk.set_frame_dimensions(W); trk.set_person_height(int(H))
    t = 0.0; dt = 1.0/fps; i = 0; st = sorted(stalls)
    rows = []
    while t < dur:
        cx, cy = pos(t, A, T)
        dets = [det(cx, cy)] if rng.random() < duty else []
        conf = trk.update(dets, frame_number=i)
        ok = any(math.hypot(c.get_smoothed_centroid()[0]-cx, c.get_smoothed_centroid()[1]-cy) <= 0.75*H for c in conf)
        rows.append((t, ok, [c.track_id for c in conf]))
        t += dt; i += 1
        if st and t >= st[0][0]: t += st[0][1]; st.pop(0)
    first = next((r[0] for r in rows if r[1]), None)
    sc = [r for r in rows if first is not None and r[0] >= first]
    cov = sum(1 for r in sc if r[1]) / max(1, len(sc))
    hole = 0.0; cur = None
    for tt, ok, _ in sc:
        if not ok:
            cur = tt if cur is None else cur
        elif cur is not None:
            hole = max(hole, tt - cur); cur = None
    ids = len({i for r in sc for i in r[2]})
    return first, cov, hole, ids, DancerTrack._id_counter

print("tracker alone, 1 dancer, swing A=1.0 h T=3 s (peak 2.1 h/s), 120 s, 8 seeds; intermittent confirm ON (D33 default)")
print(f"{'duty':>5} {'fps':>4} | first report s (med) | on-dancer after first (med) | longest hole s (med / max) | reported ids (med) | minted (med)")
for duty in (1.0, 0.8, 0.6, 0.45):
    for fps in (20, 10):
        R = [run(fps, duty, 1.0, 3.0, s) for s in range(8)]
        f = np.median([r[0] if r[0] is not None else 120 for r in R]); c = np.median([r[1] for r in R])
        h = [r[2] for r in R]; ids = np.median([r[3] for r in R]); m = np.median([r[4] for r in R])
        print(f"{duty:5.2f} {fps:4d} | {f:8.2f}             | {c:8.1%}                    | {np.median(h):5.2f} / {max(h):5.2f}             | {ids:4.0f}               | {m:4.0f}")
