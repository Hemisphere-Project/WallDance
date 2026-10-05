#!/usr/bin/env python3
"""Who feeds whom: attribute every committed match to (source = YOLO | motion-
synthetic) x (target = primary dancer track | other track) for an N=1 run.
Also counts displacement/Mahalanobis gate hits on the primary track and
tentative-track long-range captures (raw_dist > 1 h)."""
import json, sys, collections
from pathlib import Path

run, log = sys.argv[1], sys.argv[2]
rows = {r["f"]: r for r in json.loads(Path(run).read_text())["rows"]}
nyolo = {f: r["n_dup"] - r["xv_rej"] - r["excl_rej"] for f, r in rows.items()}
ph = next(iter(rows.values()))["ph"]


def primary(f):
    r = rows.get(f)
    if not r:
        return None
    if r["rep"]:
        return max(r["rep"], key=lambda t: next((x["hits"] for x in r["trk"] if x["id"] == t["id"]), 0))["id"]
    if r["trk"]:
        return max(r["trk"], key=lambda t: t["hits"])["id"]
    return None


c = collections.Counter()
longcap = collections.Counter()
gates = collections.Counter()
newt = collections.Counter()
for line in open(log):
    e = json.loads(line)
    ev, f, d = e["event"], e.get("frame"), e.get("data", {})
    if ev in ("MATCH", "CLOSE_ACCEPT", "FORCE_UPDATE", "FALLBACK_UPDATE"):
        src = "synthetic" if d["det"] >= nyolo.get(f, 99) else "yolo"
        p = primary(f - 1) or primary(f)
        tgt = "primary" if d["track_id"] == p else "other"
        if tgt == "other":
            r = rows.get(f)
            tp = next((t["p"] for t in r["trk"] if t["id"] == d["track_id"]), None) if r else None
            pp = next((t["p"] for t in r["trk"] if t["id"] == p), None) if r else None
            if tp and pp:
                dd = ((tp[0]-pp[0])**2 + (tp[1]-pp[1])**2) ** .5
                tgt = "other_near_dancer(<1.5h)" if dd < 1.5 * ph / 0.988 else "other_far"
        c[(ev, src, tgt)] += 1
        rd = d.get("raw_dist", d.get("dist", 0))
        if tgt != "primary" and rd and rd > ph:
            longcap[(ev, src)] += 1
    elif ev in ("DISPLACEMENT_GATE", "MAHALANOBIS_GATE"):
        p = primary(f - 1)
        if d["track_id"] == p:
            src = "synthetic" if d["det"] >= nyolo.get(f, 99) else "yolo"
            gates[(ev, src)] += 1
    elif ev == "NEW_TRACK":
        newt["new"] += 1
tot = sum(c.values())
print("person_height(tracker px)", ph)
print("committed matches by (event, source, target):")
for k, v in sorted(c.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v} ({100*v/tot:.1f}%)")
src_tot = collections.Counter(); src_other = collections.Counter()
for (ev, s, t), v in c.items():
    src_tot[s] += v
    if t != "primary":
        src_other[s] += v
for s in src_tot:
    print(f"  {s}: {src_tot[s]} matches, {src_other[s]} ({100*src_other[s]/src_tot[s]:.1f}%) fed a NON-primary track")
print("long-range (>1h) captures by non-primary tracks:", dict(longcap))
print("gate rejections of a det against the PRIMARY track:", dict(gates))
