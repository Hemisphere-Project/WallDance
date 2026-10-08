#!/usr/bin/env python3
"""Start-of-take hole: decompose the delay from a flow-check / replay timeline (--quality --internal).

Per take: first YOLO det (any conf / >= 0.3 / >= 0.5), first internal tracker track, first frame the
tracker reports a track (warm-up passed), first emitted point (slot layer).  --trace prints the
frames in between (dets, internal tracks with their emit reason, reported ids, emitted slots).

  decompose.py [--trace] [--start N] timeline.json [...]
"""
import json
import sys
from pathlib import Path

FPS = 20.0


def first(rows, pred):
    for r in rows:
        if pred(r):
            return r["frame"]
    return None


def fmt(f, base=0):
    return "-" if f is None else f"{f - base:5d} ({(f - base) / FPS:5.2f}s)"


def refs(r):
    return [x for x in (r.get("ref") or []) if isinstance(x, dict)]


def analyse(path, start=0):
    rows = sorted(json.loads(Path(path).read_text()), key=lambda r: r["frame"])
    rows = [r for r in rows if r["frame"] >= start]
    f_any = first(rows, lambda r: refs(r))
    f_30 = first(rows, lambda r: any((x.get("conf") or 0) >= 0.3 for x in refs(r)))
    f_50 = first(rows, lambda r: any((x.get("conf") or 0) >= 0.5 for x in refs(r)))
    f_int = first(rows, lambda r: r.get("int"))
    f_rep = first(rows, lambda r: r.get("tracks"))
    f_emit = first(rows, lambda r: (r.get("emitted") or {}).get("tracks"))
    # sustained YOLO: first frame from which >= 50 % of the next 20 frames have a det >= 0.5
    f_sus = None
    for i in range(len(rows)):
        w = rows[i:i + 20]
        if len(w) == 20 and sum(1 for r in w if any((x.get("conf") or 0) >= 0.5 for x in refs(r))) >= 10:
            f_sus = rows[i]["frame"]
            break
    return {"name": Path(path).stem, "any": f_any, "c30": f_30, "c50": f_50, "sus50": f_sus,
            "int": f_int, "rep": f_rep, "emit": f_emit, "rows": rows}


def trace(a, until=None, every=1):
    rows = a["rows"]
    lo = (a["any"] or 0)
    hi = until or ((a["emit"] or lo + 200) + 5)
    for r in rows:
        f = r["frame"]
        if f < lo or f > hi or (f - lo) % every:
            continue
        ref = " ".join(f"{x.get('conf', 0):.2f}" for x in refs(r))
        ints = " ".join(f"#{t['id']}:{t.get('emit')}/h{t['hits']}/tsu{t['tsu']}/fss{t['fss']}/H{t['h']:.0f}"
                        for t in (r.get("int") or []))
        em = (r.get("emitted") or {}).get("tracks") or []
        print(f"  {f:5d} {f / FPS:6.2f}s ref[{ref:14s}] int[{ints}] rep={r.get('ids')} "
              f"emit={[(e['id'], e.get('state')) for e in em]}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    start = 0
    if "--start" in argv:
        i = argv.index("--start")
        start = int(argv[i + 1])
        del argv[i:i + 2]
    tr = "--trace" in argv
    args = [a for a in argv if not a.startswith("--")]
    print(f"{'take':40s} {'first det':>15s} {'det>=.3':>15s} {'det>=.5':>15s} {'sust>=.5':>15s} "
          f"{'int track':>15s} {'reported':>15s} {'emitted':>15s}")
    for p in args:
        a = analyse(p, start)
        print(f"{a['name']:40s} {fmt(a['any'], start):>15s} {fmt(a['c30'], start):>15s} "
              f"{fmt(a['c50'], start):>15s} {fmt(a['sus50'], start):>15s} {fmt(a['int'], start):>15s} "
              f"{fmt(a['rep'], start):>15s} {fmt(a['emit'], start):>15s}")
        if tr:
            trace(a)
