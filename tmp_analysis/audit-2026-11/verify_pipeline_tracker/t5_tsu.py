"""tracker#5: time_since_update under occlusion ageing, and what reads it."""
from common import *
from core.config import MOTION_CROSSVAL_BYPASS_MAX_AGE

t = mk()
Y = 600.0
xa, xb = 500.0, 580.0          # 0.67 h apart: inside the occlusion radius (0.95 h), outside the merge radius
f = 0
vx = 4.0                       # B walks toward A at 4 px/frame before being hidden
xb0 = xb + 40 * vx
for i in range(40):
    out = t.update([det(xa, Y), det(xb0 - i * vx, Y)], frame_number=f); f += 1
B = [tr for tr in t.tracks if tr.track_id == 2][0]
print("B before occlusion: x %.1f, KF vx %.2f px/f, tsu %d, wu %.1f" % (B.get_centroid()[0], B.get_velocity()[0], B.time_since_update, B._warmup_score))
print("occluded frames (only A detected); per frame: tsu after update | KF x | KF vx | warm-up | occluded | last measured x | emitted ids")
rows = []
for i in range(1, 46):
    tsu_at_predict = B.time_since_update
    out = t.update([det(xa, Y)], frame_number=f); f += 1
    alive = any(tr.track_id == 2 for tr in t.tracks)
    if not alive:
        print("  frame %d: track 2 no longer in tracker.tracks; events %s" % (i, [e for e in names(t, f - 1) if e != "MATCH"]))
        break
    rows.append((i, tsu_at_predict, B.time_since_update, B.get_centroid()[0], B.get_velocity()[0], B._warmup_score, B._occluded,
                 B.get_last_known_position()[0], [x.track_id for x in out]))
for r in rows:
    if r[0] in (1, 2, 5, 9, 10, 11, 12, 15, 20, 21, 22, 30, 31, 40, 45):
        print("  occl frame %2d: tsu seen by predict %d -> after frame %d | x %.1f | vx %.2f | wu %.1f | occ %s | last meas x %.1f | emitted %s" % r)
bypass = sum(1 for r in rows if r[2] <= MOTION_CROSSVAL_BYPASS_MAX_AGE)
disp = sum(1 for r in rows if r[1] + 1 <= 1)
print("frames (of %d occluded) where pipeline's crossval bypass still sees tsu <= %d: %d;  where the displacement gate (tsu <= 1 at match time) still applies: %d;  "
      "frames where predict() skipped friction + warm-up decay (tsu == 0): %d"
      % (len(rows), MOTION_CROSSVAL_BYPASS_MAX_AGE, bypass, disp, sum(1 for r in rows if r[1] == 0)))
# control: same miss with no partner nearby (plain coasting)
t = mk(); f = 0
for i in range(40):
    out = t.update([det(xb0 - i * vx, Y)], frame_number=f); f += 1
C = t.tracks[0]
for i in range(1, 12):
    out = t.update([], frame_number=f); f += 1
print("control, same dancer missed with nobody near: after 11 misses tsu %d, vx %.2f, wu %.1f, emitted %s"
      % (C.time_since_update, C.get_velocity()[0], C._warmup_score, [x.track_id for x in out]))
# newborn flagged occluded
t = mk(); f = 0
for i in range(30):
    out = t.update([det(500, Y)], frame_number=f); f += 1
out = t.update([det(500, Y), det(500 + 0.9 * H, Y)], frame_number=f)
print("newborn 0.9 h from a matched track: events %s; new track _occluded=%s"
      % (names(t, f), [(tr.track_id, tr._occluded) for tr in t.tracks]))
