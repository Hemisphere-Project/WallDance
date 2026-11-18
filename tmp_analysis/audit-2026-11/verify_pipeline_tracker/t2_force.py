"""tracker#2: an unmatched detection is forced onto the track past the displacement gate."""
from common import *

print("gates (px) for H=%d: match %s, new-track %s (x1.5 centre = %s), duplicate %s, displacement %.1f, close-accept %.1f"
      % (H, mk().distance_threshold, mk().new_track_min_distance, int(mk().new_track_min_distance * 1.5),
         mk().duplicate_distance, mk().distance_threshold * config.TRACKER_MAX_DISPLACEMENT_RATIO,
         H * config.TRACKER_CLOSE_ACCEPT_RATIO))

for label, off_h, with_real in (("ghost at 0.60 h, dancer missed", 0.60, False),
                                ("ghost at 0.80 h, dancer missed", 0.80, False),
                                ("ghost at 0.90 h, dancer missed", 0.90, False),
                                ("ghost at 0.60 h, dancer ALSO detected", 0.60, True)):
    t = mk()
    X, Y = 640.0, 600.0
    for f in range(30):
        out = t.update([det(X, Y)], frame_number=f)
    tr = t.tracks[0]
    before = tr.get_centroid().copy()
    ghost = det(X + off_h * H, Y)
    dets = ([det(X, Y)] if with_real else []) + [ghost]
    out = t.update(dets, frame_number=30)
    tr = [x for x in t.tracks if x.track_id == 1][0]
    after = tr.get_centroid()
    print("\n== %s ==" % label)
    print("  events:", [(e, {k: v for k, v in (d or {}).items() if k in ("raw_disp", "gate", "dist", "cost", "threshold", "track_id", "near_track_id")})
                        for e, d in ev(t, 30)])
    print("  track 1 KF x: %.1f -> %.1f (jump %.2f h); last measured x %.1f; tracks now %s; reported ids %s"
          % (before[0], after[0], (after[0] - before[0]) / H, tr.get_last_known_position()[0],
             [x.track_id for x in t.tracks], [x.track_id for x in out]))
    if not with_real and off_h < 0.85:
        # next frame: the real dancer is detected again at the true position
        out = t.update([det(X, Y)], frame_number=31)
        print("  next frame (dancer back at true x=%.0f): events %s" % (X, names(t, 31)))
        print("  ", state(t))
