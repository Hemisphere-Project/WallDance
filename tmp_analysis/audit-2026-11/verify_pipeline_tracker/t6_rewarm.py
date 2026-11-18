"""tracker#6: a live track re-acquired after a gap re-warms; a resurrected dormant id is emitted at once.
Tracker level, then the emitted slot stream (full post-YOLO chain, slots ON = shipped default)."""
from chain import *

print("TRACKER level (still dancer, no motion evidence): gap in frames -> frames from the first returning detection to re-emission")
for gap in (3, 6, 8, 10, 20, 30, 60, 120, 134, 136, 200):
    t = mk(); f = 0
    for _ in range(60):
        out = t.update([det(640, 600)], frame_number=f); f += 1
    hidden_at = None
    for i in range(gap):
        out = t.update([], frame_number=f); f += 1
        if hidden_at is None and not out:
            hidden_at = i + 1
    delay = None; how = None
    for i in range(40):
        out = t.update([det(640, 600)], frame_number=f)
        if i == 0:
            how = [e for e in names(t, f) if e in ("MATCH", "CLOSE_ACCEPT", "FORCE_UPDATE", "RESURRECT", "NEW_TRACK", "FALLBACK_UPDATE")]
            wu0 = t.tracks[-1]._warmup_score
        f += 1
        if out:
            delay = i; ids = [x.track_id for x in out]; break
    print("  gap %3d f (%.2f s): hidden after miss #%s; return via %s (warm-up %.1f); re-emitted after %s further frames (%.2f s), id %s"
          % (gap, gap / 20.0, hidden_at, how, wu0, delay, (delay or 0) / 20.0, ids))

print("\nEMITTED stream (identity slots ON, coast %.1f s, no plate, no belt): same scenario, OSC /walldance/count" % 2.0)
for gap in (6, 20, 30, 39, 45, 60, 120, 136, 200):
    p, clock, rec = mk_proc()
    fr = 0
    for _ in range(60):
        step(p, clock, rec, [det(640, 600)], fr); fr += 1
    for _ in range(gap):
        step(p, clock, rec, [], fr); fr += 1
    back = fr
    for _ in range(60):
        step(p, clock, rec, [det(640, 600)], fr); fr += 1
    counts = {m[0]: m[2] for m in rec.msgs if m[1] == "/walldance/count"}
    hole = [k for k in range(60, fr) if counts[k][0] == 0]
    first_after = next((k for k in range(back, fr) if counts[k][0] >= 1), None)
    ids = sorted({tuple(counts[k][1:]) for k in range(back, fr) if counts[k][0] >= 1})
    print("  gap %3d f (%.2f s): frames with count 0 = %3d (%.2f s)%s; first emitted frame after the dancer is seen again: +%s f; slot ids after: %s"
          % (gap, gap / 20.0, len(hole), len(hole) / 20.0,
             "" if not hole else " [frames %d..%d]" % (hole[0] - 60, hole[-1] - 60),
             None if first_after is None else first_after - back, ids))
