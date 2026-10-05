#!/usr/bin/env python3
"""Per-track lifecycle table from a drive.py run + its tracker JSONL.
birth source (YOLO vs motion-synthetic), max hits, reported frames, end reason."""
import json, sys, collections, math
from pathlib import Path

run, log = sys.argv[1], sys.argv[2]
rows = json.loads(Path(run).read_text())["rows"]
births, synth = {}, collections.defaultdict(list)
ends = {}
events = collections.Counter()
for line in open(log):
    e = json.loads(line)
    ev, fr, d = e["event"], e.get("frame"), e.get("data", {})
    if ev == "MOTION_SYNTHETIC_DET":
        synth[fr].append(d["blob_centroid"])
    elif ev == "NEW_TRACK":
        births[d["track_id"]] = (fr, d["position"], d["min_dist"], d["gate"], d["is_edge"])
    elif ev == "TRACK_MERGED":
        ends.setdefault(d["victim_id"], ("merged_" + d.get("mode", "dup"), fr, d["keeper_id"]))
    elif ev == "DORMANT":
        ends[d["track_id"]] = ("dormant", fr, None)
    elif ev == "GHOST_EXPIRED":
        ends[d["track_id"]] = ("ghost_expired", fr, None)
    elif ev == "KILL_SHADOW":
        ends[d["track_id"]] = ("shadow_killed", fr, d["parent_id"])
    elif ev == "RESURRECT":
        ends.pop(d["track_id"], None)
        events["resurrect"] += 1
rep = collections.Counter(); maxhits = collections.Counter(); first_rep = {}
for r in rows:
    for t in r["trk"]:
        maxhits[t["id"]] = max(maxhits[t["id"]], t["hits"])
    for t in r["rep"]:
        rep[t["id"]] += 1
        first_rep.setdefault(t["id"], r["f"])
src = collections.Counter(); fate = collections.Counter(); rep_by_src = collections.Counter()
tab = []
for tid, (fr, pos, md, gate, edge) in sorted(births.items()):
    s = "synthetic" if any(math.dist(pos, c) < 1.0 for c in synth.get(fr, [])) else "yolo"
    end = ends.get(tid, ("alive_at_end", None, None))
    src[s] += 1
    fate[(s, end[0], "reported" if rep[tid] else "never_rep")] += 1
    if rep[tid]:
        rep_by_src[s] += 1
    tab.append((tid, fr, s, round(md, 1), gate, maxhits[tid], rep[tid], end[0], end[2]))
print("births by source", dict(src))
print("reported tracks by source", dict(rep_by_src))
print("fate (source,end,reported):")
for k, v in sorted(fate.items(), key=lambda x: -x[1]):
    print("  ", k, v)
print("resurrects", events["resurrect"])
print("tracks reported (id: frames):", {k: v for k, v in sorted(rep.items())})
print("\nid  birth src  min_dist gate maxhits repfr end keeper")
for t in tab:
    if t[6] or t[5] >= 10:
        print(*t)
