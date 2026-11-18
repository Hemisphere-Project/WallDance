"""tracker#2 (second rule): FALLBACK_UPDATE takes the first unmatched track in list order within 0.95 h, not the nearest."""
from common import *
t = mk(); f = 0
B, C, A = (660.0, 500.0), (660.0, 680.0), (600.0, 600.0)
for _ in range(30):
    t.update([det(*B), det(*C), det(*A)], frame_number=f); f += 1
print("tracks in list order (id, last measured):", [(tr.track_id, [round(float(v)) for v in tr.get_last_known_position()]) for tr in t.tracks])
g = det(660.0, 600.0)
gc = kcent(g)
print("ghost centroid %s; distances to tracks: %s" % ([round(float(v)) for v in gc],
      {tr.track_id: round(float(np.linalg.norm(gc - tr.get_last_known_position()))) for tr in t.tracks}))
t.update([det(*A), g], frame_number=f)       # frame 30: A detected, B and C missed, plus one ghost
print("events:", [(e, {k: v for k, v in (d or {}).items() if k in ("track_id", "raw_disp", "gate", "dist", "cost")}) for e, d in ev(t, f)
                  if e not in ("OCCLUDED",)])
