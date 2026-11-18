"""Read-only: per-5s overview of a recorded replay timeline (s2c: dark, 2 min still)."""
import json, sys, math
from collections import Counter
p = sys.argv[1]
fps = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
rows = json.load(open(p))
print("rows", len(rows), "=> %.1f s at %.1f fps" % (len(rows) / fps, fps))
step = int(5 * fps)
print("t0   | yolo_frames conf_med  ref_h | trk src(y/b/c/s) emit(ok/frozen/warmup/none) | slot live/belt/fg/coast/none | x,y slot | fg blob area med | fg valid")
for a in range(0, len(rows), step):
    seg = rows[a:a + step]
    ny = sum(1 for r in seg if r.get("ref"))
    confs = sorted(d["conf"] for r in seg for d in (r.get("ref") or []) if d.get("conf") is not None)
    hs = sorted(d["h"] for r in seg for d in (r.get("ref") or []))
    src = Counter()
    emit = Counter()
    for r in seg:
        it = r.get("int") or []
        if not it:
            emit["none"] += 1
        for t in it:
            emit[t.get("emit")] += 1
        for t in r.get("tracks") or []:
            src[t.get("src")] += 1
    st = Counter()
    xs = []
    for r in seg:
        em = (r.get("emitted") or {}).get("tracks") or []
        if not em:
            st["none"] += 1
        for t in em:
            st[t.get("state")] += 1
            xs.append(t["centroid"])
    fga = []
    fgv = 0
    for r in seg:
        fg = r.get("fg") or {}
        fgv += 1 if fg.get("v") else 0
        em = (r.get("emitted") or {}).get("tracks") or []
        if em and fg.get("b"):
            cx, cy = em[0]["centroid"]
            near = [b[2] for b in fg["b"] if math.hypot(b[0] - cx, b[1] - cy) < 100]
            fga.append(max(near) if near else 0)
    med = lambda v: (sorted(v)[len(v) // 2] if v else None)
    xy = ("%4.0f,%4.0f" % (med([x[0] for x in xs]), med([x[1] for x in xs]))) if xs else "   -     "
    print("%5.1f | %3d/%3d  %s  %s | y%3d b%3d c%3d s%3d | ok%3d fr%3d wu%3d no%3d | L%3d B%3d F%3d C%3d N%3d | %s | %s | %d" % (
        a / fps, ny, len(seg), ("%.2f" % med(confs)) if confs else " -  ", ("%4.0f" % med(hs)) if hs else "  - ",
        src["yolo"], src["bridge"], src["coast"], src["synthetic"],
        emit["ok"], emit["frozen"], emit["warmup"], emit["none"],
        st["live"], st["belt"], st["fg"], st["coasting"], st["none"], xy, med(fga), fgv))
