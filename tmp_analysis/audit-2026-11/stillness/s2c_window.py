import json, sys
rows = json.load(open(sys.argv[1]))
a, b, step = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
for r in rows[a:b:step]:
    ref = [(round(d["c"][0]), round(d["c"][1]), round(d["h"]), d.get("conf")) for d in r.get("ref") or []]
    it = [(t["id"], round(t["p"][0]), round(t["p"][1]), t.get("tsu"), t.get("fss"), t.get("hits"), t.get("emit")) for t in r.get("int") or []]
    em = [(t["id"], t["state"], round(t["centroid"][0]), round(t["centroid"][1])) for t in (r.get("emitted") or {}).get("tracks") or []]
    fg = r.get("fg") or {}
    print(r["frame"], "ref", ref, "| int", it, "| em", em, "| fg", fg.get("v"), fg.get("r"), (fg.get("b") or [])[:3])
