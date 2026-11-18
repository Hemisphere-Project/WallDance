"""tracker#8: resurrection reach, its keypoint 'shape' gate, and the velocity projection."""
from common import *
from core.config import TRACKER_CENTER_EXIT_RESURRECT_BOOST

def dormant_b(moving=False, a_at=None):
    """B tracked 60 frames (ending at x=900, centre zone), then unseen until it goes dormant. Optional A stays tracked."""
    t = mk(); f = 0
    for i in range(60):
        xb = 900.0 - (59 - i) * (6.0 if moving else 0.0)
        dets = [det(xb, 600)] + ([det(a_at, 600)] if a_at is not None else [])
        t.update(dets, frame_number=f); f += 1
    n = 0
    while not t._dormant and n < 400:
        t.update([det(a_at, 600)] if a_at is not None else [], frame_number=f); f += 1; n += 1
    return t, f, n

t, f, n = dormant_b(moving=True)
s = t._dormant[0]
print("B walked at 6 px/frame, then vanished: dormant after %d unmatched frames; snapshot velocity |v| = %.4f px/f, "
      "projected - last position = %.3f px; edge_exit=%s occluded=%s was_reported=%s"
      % (n, float(np.linalg.norm(s.velocity)), float(np.linalg.norm(s.projected_position() - s.last_position)),
         s.exited_from_edge, s.was_occluded, s.was_reported))
bid = s.track_id
gate = t.distance_threshold
mult = min(1.0 + 0.5 + (TRACKER_CENTER_EXIT_RESURRECT_BOOST - 1.0), 2.5)
print("position gate for this snapshot: %.0f px x %.1f = %.0f px (%.2f h); keypoint gate: 0.8 x %.0f = %.0f px (%.2f h), raw image coords"
      % (gate, mult, gate * mult, gate * mult / H, gate, 0.8 * gate, 0.8 * gate / H))

print("\nB reappears ALONE, d from its last position:")
for d_h, kind in ((0.5, "skeleton"), (0.7, "skeleton"), (0.8, "skeleton"), (1.5, "skeleton"), (2.2, "skeleton"),
                  (1.5, "blob (zero-confidence synthetic)"), (2.2, "blob (zero-confidence synthetic)"),
                  (0.5, "skeleton, other pose (arms up, same place)")):
    t, f, n = dormant_b()
    x = 900.0 + d_h * H
    if kind.startswith("skeleton, other"):
        k, c, b = det(x, 600)
        k[7:11, 1] -= 0.5 * H            # elbows + wrists raised by half a body height
        k[13:17, 0] += 0.4 * H           # legs swung sideways (aerial pose)
        d = (k, c, b)
    elif kind.startswith("skeleton"):
        d = det(x, 600)
    else:
        d = synth(x, 600)
    out = t.update([d], frame_number=f)
    e = [x_ for x_ in names(t, f) if x_ in ("RESURRECT", "NEW_TRACK")]
    print("  %.1f h, %-42s -> %s, track ids %s, reported this frame %s"
          % (d_h, kind, e, [tr.track_id for tr in t.tracks], [tr.track_id for tr in out]))

print("\nB reappears NEXT TO its partner A (A tracked throughout at x=600; B last seen at x=900 = 2.5 h away):")
for s_h in (0.3, 0.5, 0.8, 0.9, 1.2):
    t, f, n = dormant_b(a_at=600.0)
    x = 600.0 + s_h * H
    ev_ = None
    for i in range(3):
        out = t.update([det(600, 600), det(x, 600)], frame_number=f)
        if i == 0:
            ev_ = [x_ for x_ in names(t, f) if x_ not in ("MATCH", "DISPLACEMENT_GATE", "MAHALANOBIS_GATE", "MATCH_REJECTED", "OCCLUDED")]
        f += 1
    print("  %.1f h from A (%.1f h from B's last position): %s; tracks after 3 frames %s, dormant %s"
          % (s_h, abs(900 - x) / H, ev_, [tr.track_id for tr in t.tracks], [d_.track_id for d_ in t._dormant]))
