#!/usr/bin/env python3
"""Aggregate mp_study.py outputs: per scenario, recall of each pass on live frames (by threshold), keypoint
confidence, net dancer size; on gap frames (YOLO lost the dancer) the find rate of each pass vs the decoy
false-find rate; the union of the global-gamma alternatives.

  python mp_report.py study/*.json [--md]
"""
import json
import statistics as st
import sys


def rate(rows, key, thr):
    n = len(rows)
    if not n:
        return None
    return sum(1 for r in rows if r.get(key) and r[key][0] >= thr) / n


def kpt(rows, key):
    v = [r[key][1] for r in rows if r.get(key)]
    return st.mean(v) if v else None


def fmt(v, pct=True):
    if v is None:
        return "-"
    return f"{100 * v:.0f}" if pct else f"{v:.2f}"


def main():
    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    for fpath in files:
        d = json.load(open(fpath))
        live, gap = d["live"], d["gap"]
        sizes = d["sizes"]
        print(f"\n## {d['scenario']}  (ROI {d['roi'][2]}x{d['roi'][3]}, gamma {d['gamma']}, CLAHE {d['clahe']}, "
              f"live n={len(live)}, gap n={len(gap)})")
        if live:
            hs = [r["h"] for r in live]
            print(f"dancer height px: median {st.median(hs):.0f} (p10 {sorted(hs)[len(hs)//10]:.0f})  net px at "
                  + ", ".join(f"{s}: {st.median([r['net'][str(s)] if str(s) in r['net'] else r['net'][s] for r in live]):.0f}" for s in sizes))
            print("LIVE   recall% @0.15/0.25/0.5 | mean kpt conf")
            for key in [f"full{s}" for s in sizes] + ["zoom_g", "zoom_l"]:
                print(f"  {key:9s} {fmt(rate(live, key, .15)):>4} {fmt(rate(live, key, .25)):>4} {fmt(rate(live, key, .5)):>4}"
                      f" | {fmt(kpt(live, key), False)}")
        if gap:
            print("GAP    found% @0.10/0.15/0.25 | decoy (false) % @0.10/0.15/0.25")
            for key, dk in (("full1280", None), ("zoom_g", "decoy_g"), ("zoom_l", "decoy_l"),
                            ("full1280_g_lo", None), ("full1280_g_hi", None)):
                dec = [r for r in gap if dk and dk in r]
                line = f"  {key:14s} {fmt(rate(gap, key, .10)):>4} {fmt(rate(gap, key, .15)):>4} {fmt(rate(gap, key, .25)):>4}"
                if dk:
                    line += f" | {fmt(rate(dec, dk, .10)):>4} {fmt(rate(dec, dk, .15)):>4} {fmt(rate(dec, dk, .25)):>4}  (n={len(dec)})"
                print(line)
            for thr in (0.15, 0.25):
                union = sum(1 for r in gap if any(r.get(k) and r[k][0] >= thr
                                                  for k in ("full1280", "full1280_g_lo", "full1280_g_hi"))) / len(gap)
                zu = sum(1 for r in gap if any(r.get(k) and r[k][0] >= thr for k in ("zoom_g", "zoom_l"))) / len(gap)
                print(f"  union of the 3 global gammas @{thr}: {fmt(union)}%   union zoom g+l: {fmt(zu)}%")
        print(f"ms/call (PyTorch, shared GPU): {d.get('ms_per_call')}")


if __name__ == "__main__":
    main()
