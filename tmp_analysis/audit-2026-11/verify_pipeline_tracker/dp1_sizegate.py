"""detection-pipeline#1: the size gate only runs when a frame has >= 2 detections."""
from chain import *

p, clock, rec = mk_proc()
ph = int(H)
lo, hi = ph * p.settings.person_height_min_ratio, ph * p.settings.person_height_max_ratio
print("person height %d px -> size gate %.0f..%.0f px" % (ph, lo, hi))
big = det(640, 640, h=5.0 * H)       # 600 px: somebody filling the lens
tiny = det(200, 200, h=0.15 * H)     # 18 px: a far speck
ok = det(1000, 600)                  # a 120 px dancer, > 1.5 h from both
f = FrameProcessor._filter_duplicate_detections
for label, dets in (("lone oversize (600 px)", [big]), ("lone undersize (18 px)", [tiny]),
                    ("oversize + a normal dancer", [big, ok]), ("undersize + a normal dancer", [tiny, ok]),
                    ("oversize + undersize, no valid one", [big, tiny])):
    out = f(p, list(dets), effective_person_height=ph)
    print("  %-36s in heights %s -> out heights %s" % (label, [int(d[2][3]) for d in dets], [int(d[2][3]) for d in out]))

# end to end: a lone 600 px person for 2 s, through gate -> tracker -> slots -> OSC
for label, extra in (("lone 600 px person (5 x the person height)", []), ("same + one 120 px dancer in view", [ok])):
    p, clock, rec = mk_proc()
    for fr in range(40):
        gated = FrameProcessor._filter_duplicate_detections(
            p, [det(640 + fr, 640, h=5.0 * H)] + extra, effective_person_height=ph)   # as pipeline.py:748
        out = step(p, clock, rec, gated, fr)
    counts = [m for m in rec.msgs if m[1] == "/walldance/count"]
    cents = [m for m in rec.msgs if m[1] == "/walldance/dancer/centroid" and m[0] == 39]
    print("\n%s, 40 frames:" % label)
    print("  tracker tracks (id, box h):", [(t.track_id, int(t.bbox[3])) for t in p.tracker.tracks])
    print("  last OSC count:", counts[-1][2], " first frame with count>=1:",
          next((m[0] for m in counts if m[2][0] >= 1), None))
    print("  OSC centroids at frame 39 (id, x, y normalised):", [[m[2][0], round(m[2][1], 3), round(m[2][2], 3)] for m in cents])
    print("  slot boxes h at frame 39:", [int(t.bbox[3]) for t in p.last_emitted])
