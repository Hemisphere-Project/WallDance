"""Read-only: for co-located merges of co-fed pairs on stored duo timelines, was the victim a slot-bound track,
and what did the emitted stream do in the next 3 s?"""
import json, sys, math
from collections import defaultdict
path, ph = sys.argv[1], float(sys.argv[2])
d = json.load(open(path))
merge_r = 0.3 * 0.95 * ph
ints = [{t["id"]: t for t in (fr.get("int") or [])} for fr in d]
cofed = defaultdict(int)

def bound(i):
    """slot id -> tracker id at frame i, matching the slot's raw centroid to a track's smoothed centroid."""
    out = {}
    for e in ((d[i].get("emitted") or {}).get("tracks") or []):
        best = min(ints[i].items(), key=lambda kv: math.dist(kv[1]["sm"], e["raw"]), default=None)
        if best is not None and math.dist(best[1]["sm"], e["raw"]) < 1.0:
            out[e["id"]] = best[0]
        else:
            out[e["id"]] = None
    return out

n = tot = 0
for i, cur in enumerate(ints):
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
            continue
        cands = [(math.dist(t["p"], o["p"]), oid) for oid, o in prev.items() if oid != tid and oid in cur and o["hits"] >= 15 and o["tsu"] <= 1]
        cands = [c for c in cands if c[0] <= merge_r]
        if not cands:
            continue
        dd, oid = min(cands)
        streak = 0; j = i - 1
        while j >= 0 and tid in ints[j] and oid in ints[j] and math.dist(ints[j][tid]["p"], ints[j][oid]["p"]) <= merge_r:
            streak += 1; j -= 1
        key = (min(tid, oid), max(tid, oid))
        if streak < 7:
            continue                    # the 8th close frame is the merge frame itself
        tot += 1
        b = bound(i - 1)
        slot_of_victim = [s for s, k in b.items() if k == tid]
        slot_of_keeper = [s for s, k in b.items() if k == oid]
        nxt = d[i:i + 60]
        lt2 = sum(1 for fr in nxt if len((fr.get("emitted") or {}).get("ids") or []) < 2)
        ref2 = sum(1 for fr in nxt if len(fr.get("ref") or []) >= 2)
        st = defaultdict(int)
        for fr in nxt:
            for e in ((fr.get("emitted") or {}).get("tracks") or []):
                if slot_of_victim and e["id"] == slot_of_victim[0]:
                    st[e.get("state")] += 1
        fed8 = sum(1 for j in range(max(0, i - 8), i) if ints[j].get(tid, {}).get("fss") == 0 and ints[j].get(oid, {}).get("fss") == 0)
        vfed = sum(1 for j in range(max(0, i - 8), i) if ints[j].get(tid, {}).get("fss") == 0)
        kfed = sum(1 for j in range(max(0, i - 8), i) if ints[j].get(oid, {}).get("fss") == 0)
        print("  f%-4d victim %d (hits %d, emit %s, slot %s, skeleton-fed %d/8) keeper %d (hits %d, slot %s, fed %d/8) dist %.2f h, both fed %d/8, life co-fed %d | "
              "next 60 f: <2 ids emitted on %d, >=2 ref dets on %d, victim-slot states %s"
              % (i, tid, t["hits"], t.get("emit"), slot_of_victim or "-", vfed, oid, prev[oid]["hits"], slot_of_keeper or "-", kfed,
                 dd / ph, fed8, cofed[key], lt2, ref2, dict(st)))
        if slot_of_victim:
            n += 1
print("%s: %d co-located merges (8 consecutive frames within %.0f px, both established, tsu <= 1); victim was the slot-bound track in %d"
      % (path.split("/")[-1], tot, merge_r, n))
