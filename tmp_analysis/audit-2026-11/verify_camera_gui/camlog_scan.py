import json, sys, glob, os
for p in sorted(glob.glob("projects/mur30m-0710/recordings/*.camlog.jsonl") + glob.glob("projects/mur25m-night-common/recordings/*.camlog.jsonl")):
    rows = [json.loads(l) for l in open(p) if l.strip()]
    if not rows: 
        print(p, "empty"); continue
    keys = sorted(rows[-1].keys())
    # frames deltas between consecutive 1 Hz samples
    fr = [(r.get("t"), r.get("frames")) for r in rows]
    gaps = []
    for (t0,f0),(t1,f1) in zip(fr, fr[1:]):
        if f0 is None or f1 is None: continue
        dt = t1-t0; df = f1-f0
        if df < 0.5*20*dt: gaps.append((round(t1-fr[0][0],1), round(dt,2), df))
    st = rows[-1].get("stream") or rows[-1].get("diag") or {}
    # find any nested dict with 'stalls'
    def find(d, k):
        if isinstance(d, dict):
            if k in d: return d
            for v in d.values():
                r = find(v, k)
                if r is not None: return r
        return None
    sd = find(rows[-1], "recent_stalls") or {}
    allst = {}
    for r in rows:
        d = find(r, "recent_stalls") or {}
        for s in d.get("recent_stalls", []):
            allst[s.get("n")] = s
    gms = sorted(s.get("gap_ms") for s in allst.values() if s.get("gap_ms") is not None)
    print(os.path.basename(p), "| samples", len(rows), "| dur", round(fr[-1][0]-fr[0][0],1), "s | frames", fr[-1][1],
          "| stalls(cum)", sd.get("stalls"), "| distinct logged", len(allst), "| gap_ms", gms[:40], "| max", (max(gms) if gms else None))
    if gaps: print("   low-frame seconds (t, dt, dframes):", gaps[:12], "..." if len(gaps)>12 else "")
print("keys of a sample:", keys)
