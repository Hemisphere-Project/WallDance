"""detection-pipeline#3: track-blind dedupe: small-merge splice, weak-near-strong delete, containment splice."""
from chain import *

p, clock, rec = mk_proc(osc=False)
ph = int(H)
f = lambda dets: FrameProcessor._filter_duplicate_detections(p, dets, effective_person_height=ph)

# (a) small-merge: an undersize detection 1.2 h away donates its better keypoints
main = det(600, 600, conf=0.6)
small = det(600 + 1.2 * H, 600, h=0.25 * H, conf=0.9)          # 30 px < 36 px gate
c0 = kcent(main)
out = f([main, small])
c1 = kcent(out[0])
moved = int(np.sum(np.linalg.norm(out[0][0] - det(600, 600)[0], axis=1) > 1.0))
print("(a) undersize det (30 px) 1.2 h from a valid one (kpt conf 0.9 vs 0.6):")
print("    out dets %d; %d/17 keypoints of the valid det replaced; measured centroid x %.1f -> %.1f (%.2f h)"
      % (len(out), moved, c0[0], c1[0], (c1[0] - c0[0]) / H))
main = det(600, 600, conf=0.6)
k, c, b = det(600 + 1.2 * H, 600, h=0.25 * H, conf=0.3)
c[[9, 10]] = 0.95                                             # only two "wrists" better than the dancer's
out = f([main, (k, c, b)])
c1 = kcent(out[0])
print("    same with only 2 better keypoints: centroid x %.1f -> %.1f (%.2f h), box unchanged %s"
      % (c0[0], c1[0], (c1[0] - c0[0]) / H, np.allclose(out[0][2], main[2])))

# (b) weak skeleton within 1.5 h of a strong one is deleted (no track consulted)
for sep, weak_conf in ((1.0, 0.45), (1.4, 0.45), (1.6, 0.45), (1.0, 0.55)):
    a = det(600, 600, conf=0.9)
    bdet = det(600 + sep * H, 600, conf=weak_conf)
    out = f([a, bdet])
    print("(b) two full-size dancers %.1f h apart, B mean kpt conf %.2f (17 kpts): %d -> %d detections"
          % (sep, weak_conf, 2, len(out)))
k, c, b = det(600 + 1.0 * H, 600, conf=0.9)
c[7:] = 0.0                                                    # only head + shoulders visible (7 kpts)
out = f([det(600, 600), (k, c, b)])
print("(b) B with 7 confident keypoints (0.9), 1.0 h from A: 2 -> %d detections" % len(out))

# (c) containment: a box whose centre is inside a larger one and < half its area is suppressed + spliced
a = det(600, 600, conf=0.6)
kb, cb_, bb = det(600 + 0.1 * H, 600 + 0.2 * H, h=0.6 * H, conf=0.9)   # crouched / partly hidden partner
out = f([a, (kb, cb_, bb)])
print("(c) partner box (0.6 h tall, centre inside A's box, area ratio %.2f): 2 -> %d detections; "
      "A's centroid %.1f,%.1f -> %.1f,%.1f"
      % ((bb[2] * bb[3]) / (a[2][2] * a[2][3]), len(out), *kcent(det(600, 600, conf=0.6)), *kcent(out[0])))

# (d) track-blindness: the same weak detection is deleted although a confirmed track sits on it
p, clock, rec = mk_proc(osc=False)
for fr in range(40):
    step(p, clock, rec, [det(600, 600), det(720, 600)], fr)       # two dancers 1.0 h apart, both confirmed
ids = [t.track_id for t in p.tracker.tracks]
gated = FrameProcessor._filter_duplicate_detections(p, [det(600, 600), det(720, 600, conf=0.45)], effective_person_height=ph)
print("(d) two confirmed tracks %s; frame with B at kpt conf 0.45: detections after dedupe = %d (B never reaches the tracker)"
      % (ids, len(gated)))

# crossval EMA start
from core.config import MOTION_CROSSVAL_EMA_ALPHA
print("crossval EMA alpha %.2f: a detection in a cell with no detection last frame is credited %.2f x its raw motion"
      % (MOTION_CROSSVAL_EMA_ALPHA, MOTION_CROSSVAL_EMA_ALPHA))
