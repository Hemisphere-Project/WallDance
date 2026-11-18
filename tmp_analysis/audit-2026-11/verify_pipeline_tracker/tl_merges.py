"""Read-only: find co-located merges of CO-FED pairs in stored Oct-8 duo timelines (tmp_analysis), i.e. on real footage."""
import json, sys, math
from collections import defaultdict
path, ph = sys.argv[1], float(sys.argv[2])
d = json.load(open(path))
fps = 19.8
merge_r = 0.3 * 0.95 * ph
ints = [{t["id"]: t for t in (fr.get("int") or [])} for fr in d]
cofed = defaultdict(int)
first_seen = {}
events = []
for i, cur in enumerate(ints):
    for tid in cur:
        first_seen.setdefault(tid, i)
    ids = sorted(cur)
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            ta, tb = cur[ids[a]], cur[ids[b]]
            if ta["hits"] >= 15 and tb["hits"] >= 15 and ta["fss"] == 0 and tb["fss"] == 0:
                cofed[(ids[a], ids[b])] += 1
    if i == 0:
        continue
    prev = ints[i - 1]
    for tid, t in prev.items():
        if tid in cur or t["hits"] < 15 or t["tsu"] > 1:
            continue                      # still alive, or not an established recently-fed track
        near = [(math.dist(t["p"], o["p"]), oid) for oid, o in cur.items() if o["hits"] >= 15]
        # the keeper's position at frame i (after the merge frame); the merge test used both at frame i
        near = [(dd, oid) for dd, oid in near if dd <= merge_r * 1.6]
        if not near:
            continue
        dd, oid = min(near)
        key = (min(tid, oid), max(tid, oid))
        # consecutive close frames before the vanish
        streak = 0
        j = i - 1
        while j >= 0 and tid in ints[j] and oid in ints[j] and math.dist(ints[j][tid]["p"], ints[j][oid]["p"]) <= merge_r:
            streak += 1; j -= 1
        both_fed = sum(1 for j in range(max(0, i - 8), i) if tid in ints[j] and oid in ints[j]
                       and ints[j][tid]["fss"] == 0 and ints[j][oid]["fss"] == 0)
        nref = [len(d[j].get("ref") or []) for j in range(max(0, i - 8), i + 1)]
        em_before = len((d[i - 1].get("emitted") or {}).get("ids") or [])
        after = next((j - i for j in range(i, len(d)) if len((d[j].get("emitted") or {}).get("ids") or []) >= 2), None)
        hole = 0
        j = i
        while j < len(d) and len((d[j].get("emitted") or {}).get("ids") or []) < 2:
            hole += 1; j += 1
        events.append((i, tid, t["hits"], oid, cur[oid]["hits"], dd / ph, streak, both_fed, cofed[key], nref, em_before, hole))
n2 = sum(1 for fr in d if len((fr.get("emitted") or {}).get("ids") or []) >= 2)
print("%s: %d frames, person height %.0f px (merge radius %.0f px); emitted 2 ids on %d frames (%.1f %%)"
      % (path.split("/")[-1], len(d), ph, merge_r, n2, 100.0 * n2 / len(d)))
for e in events:
    kind = ("CO-LOCATED merge (pair co-fed %d frames in its life: takeover merge is vetoed at >= 3)" % e[8]) if e[8] >= 3 and e[6] >= 7 \
        else ("merge/kill, pair co-fed %d, close streak %d" % (e[8], e[6]))
    print("  f%-4d (%.1f s) track %d (hits %d) vanished next to track %d (hits %d) at %.2f h; close for %d consecutive frames; "
          "both skeleton-fed on %d of the last 8 frames; ref detections per frame around it %s; emitted ids before: %d; "
          "frames until 2 ids are emitted again: %d (%.2f s) -> %s"
          % (e[0], e[0] / fps, e[1], e[2], e[3], e[4], e[5], e[6], e[7], e[9], e[10], e[11], e[11] / fps, kind))
