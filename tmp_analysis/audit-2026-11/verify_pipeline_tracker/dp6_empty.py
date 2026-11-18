"""detection-pipeline#6: at L > 1 the empty wall is never sent on OSC."""
from chain import *

for L in (1, 4):
    p, clock, rec = mk_proc()
    p.set_output_smoothing(L)
    fr = 0
    for _ in range(60):                       # one dancer, 3 s
        step(p, clock, rec, [det(640, 600)], fr); fr += 1
    for _ in range(120):                      # then an empty wall, 6 s (slot coast 2 s, then lost)
        step(p, clock, rec, [], fr); fr += 1
    counts = [(m[0], m[2]) for m in rec.msgs if m[1] == "/walldance/count"]
    zero = [c for c in counts if c[1][0] == 0]
    print("L=%d: %d /walldance/count messages over %d frames; last = frame %d %s; count-0 messages after the dancer left: %d%s"
          % (L, len(counts), fr, counts[-1][0], counts[-1][1], len([z for z in zero if z[0] >= 60]),
             "" if not zero else " (first at frame %d)" % next(z[0] for z in zero if z[0] >= 60)))
    print("      last_emitted at the end:", [t.track_id for t in p.last_emitted],
          " slot states:", p.slot_states())
