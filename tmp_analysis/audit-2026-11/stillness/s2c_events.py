import json, sys
rows = json.load(open(sys.argv[1]))
a, b = int(sys.argv[2]), int(sys.argv[3])
prev = None
last_yolo = None
for r in rows[a:b]:
    em = (r.get("emitted") or {}).get("tracks") or []
    st = em[0]["state"] if em else "none"
    it = r.get("int") or []
    tk = ",".join("%d:%s:tsu%d:fss%d" % (t["id"], t.get("emit"), t.get("tsu") or 0, t.get("fss") or 0) for t in it) or "-"
    emit = ",".join(sorted(set(str(t.get("emit")) for t in it))) or "-"
    srcs = ",".join(sorted(set(str(t.get("src")) for t in r.get("tracks") or []))) or "-"
    key = (st, emit, bool(r.get("ref")), srcs)
    if key != prev:
        print("f%5d t=%6.2f  slot=%-8s  trk_emit=%-14s reported_src=%-10s yolo=%d   int=[%s]" % (
            r["frame"], r["frame"] / 20.0, st, emit, srcs, 1 if r.get("ref") else 0, tk[:90]))
        prev = key
