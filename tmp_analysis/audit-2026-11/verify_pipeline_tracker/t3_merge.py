"""tracker#3: _merge_duplicate_tracks on two REAL, simultaneously detected dancers in contact."""
from common import *

def run(sep_contact_h, label, occlude=False, contact_frames=12):
    t = mk()
    Y = 600.0
    xa, xb = 500.0, 800.0            # 2.5 h apart
    f = 0
    for _ in range(40):              # both established, co-fed 40 frames
        out = t.update([det(xa, Y), det(xb, Y)], frame_number=f); f += 1
    ids0 = sorted(tr.track_id for tr in t.tracks)
    cofed = dict(t._pair_cofed)
    # approach: B walks to A at 6 px/frame until sep_contact_h
    target = xa + sep_contact_h * H
    while xb > target:
        xb = max(target, xb - 6.0)
        out = t.update([det(xa, Y), det(xb, Y)], frame_number=f); f += 1
    print("\n== %s ==" % label)
    print("  before contact: tracks %s, pair co-fed frames %s, reported %s" % (ids0, cofed, [x.track_id for x in out]))
    merged_at = None
    for i in range(contact_frames):
        dets = [det(xa, Y)] if occlude else [det(xa, Y), det(xb, Y)]
        out = t.update(dets, frame_number=f)
        n = names(t, f)
        if "TRACK_MERGED" in n and merged_at is None:
            merged_at = i + 1
            print("  contact frame %d: %s" % (i + 1, [(e, d) for e, d in ev(t, f) if e == "TRACK_MERGED"]))
        f += 1
    print("  after %d contact frames (%s): tracks %s, dormant %s, reported %s, merged at contact frame %s"
          % (contact_frames, "B hidden" if occlude else "both detected", [x.track_id for x in t.tracks],
             [s.track_id for s in t._dormant], [x.track_id for x in out], merged_at))
    if merged_at is None:
        return
    # separation: B walks away 6 px/frame; when does B get a track again, and when is it reported?
    first_track = first_rep = None
    for i in range(80):
        xb += 6.0
        out = t.update([det(xa, Y), det(xb, Y)], frame_number=f)
        n = names(t, f)
        if first_track is None and ("NEW_TRACK" in n or "RESURRECT" in n):
            first_track = (i + 1, (xb - xa) / H, [e for e in n if e in ("NEW_TRACK", "RESURRECT")])
        if first_track is None and i < 3:
            print("  separating frame %d (sep %.2f h): %s" % (i + 1, (xb - xa) / H, n))
        if first_rep is None and len(out) >= 2:
            first_rep = (i + 1, (xb - xa) / H, [x.track_id for x in out])
        f += 1
    print("  B gets a track again at separating frame %s; two ids reported again at %s  (frame, separation in h, ...)"
          % (first_track, first_rep))

print("merge radius = 0.3 x match gate = %.1f px = %.3f h; frames needed = %d"
      % (mk().distance_threshold * config.TRACKER_DUPLICATE_MERGE_PROXIMITY,
         mk().distance_threshold * config.TRACKER_DUPLICATE_MERGE_PROXIMITY / H, config.TRACKER_DUPLICATE_MERGE_FRAMES))
run(0.25, "duo in contact, centroids 0.25 h apart, BOTH skeletons detected every frame")
run(0.32, "control: 0.32 h apart, both detected")
run(0.25, "B hidden behind A (no detection for B) while 0.25 h apart", occlude=True, contact_frames=25)
