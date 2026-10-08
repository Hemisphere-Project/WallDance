#!/usr/bin/env python3
"""D34: before / after on the injected camera stalls (run_stalls.py timelines).

Per stall, from the frame that ends it to 10 s later, the emitted (identity-slot) stream of
ctrl (no stall) / before (hold clocks count the silence) / after (D34):
  ids_before  the ids emitted on the last frame before the stall
  gone        frames (first 10 s after) where one of those ids is absent from the stream
  drop_at     seconds after the stall when the first of those ids went absent (None: never)
  new_ids     ids emitted after the stall that were not emitted before it (an id change)
  on          share of the frames with a YOLO reference box whose box has an emitted point
              within 0.75 h (a point on the dancer)
  extra       frames with more points than reference boxes (and > 1 point)
plus, for before / after, what the OSC keep-alive re-sent during the silence.  Also checks that the
frames before the stall are identical in the three runs (a stall cannot act before it happens).

  analyse.py [--tl DIR] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

WIN_S = 10.0
FPS = {"m30": 19.516, "n3": 20.0, "m30p": 19.516, "n3p": 20.0}       # the takes' .meta actual_fps (replay.py's frame clock)


def load(p: Path):
    return sorted(json.loads(p.read_text()), key=lambda r: r["frame"])


def em(r):
    return [(t["id"], t.get("state"), t["centroid"][0], t["centroid"][1])
            for t in (r.get("emitted") or {}).get("tracks") or []]


def by_abs(rows):
    return {r["abs_frame"]: r for r in rows}


def window_metrics(rows_after, ids_before, fps, t_end_abs):
    gone = 0
    drop_at = None
    new_ids = set()
    on_n = on_d = extra = 0
    for r in rows_after:
        e = em(r)
        ids = {x[0] for x in e}
        dt = (r["abs_frame"] - rows_after[0]["abs_frame"]) / fps
        if any(i not in ids for i in ids_before):
            gone += 1
            if drop_at is None:
                drop_at = round(dt, 2)
        new_ids |= ids - set(ids_before)
        refs = r.get("ref") or []
        if refs:
            on_d += 1
            for ref in refs:
                h = max(1.0, float(ref.get("h") or 1.0))
                cx, cy = float(ref["c"][0]), float(ref["c"][1])
                if any(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 <= 0.75 * h for _i, _s, x, y in e):
                    on_n += 1
                    break
        if len(e) > max(1, len(refs)):
            extra += 1
    return {"gone": gone, "drop_at": drop_at, "new_ids": sorted(new_ids),
            "on": round(on_n / on_d, 3) if on_d else None, "extra": extra}


def analyse_stall(tl: Path, take: str, stall: str):
    f0, sec = int(stall.split(":")[0]), float(stall.split(":")[1])
    tag = stall.replace(":", "_").replace(".", "_")
    fps = FPS[take]
    runs = {k: load(tl / f"{take}_{tag}_{k}.json") for k in ("before", "after")}
    runs["ctrl"] = load(tl / f"{take}_ctrl.json")
    res = {"stall": stall}
    pre = {k: [em(r) for r in rows if r["abs_frame"] < f0] for k, rows in runs.items()}
    res["pre_identical"] = pre["ctrl"] == pre["before"] == pre["after"]
    last_pre = [r for r in runs["after"] if r["abs_frame"] < f0][-1]
    ids_before = sorted({x[0] for x in em(last_pre)})
    res["ids_before"] = ids_before
    res["states_before"] = [x[1] for x in em(last_pre)]
    resume = next(r for r in runs["after"] if r.get("stall"))["abs_frame"]
    for k, rows in runs.items():
        if k == "ctrl":
            start = resume              # the same frames as the stall runs after their stall
        else:
            st = next(r for r in rows if r.get("stall"))
            start = st["abs_frame"]
            res[f"{k}_stall"] = {kk: st["stall"][kk] for kk in ("silence_s", "removed_s", "resends", "resent_ids")}
            res[f"{k}_stall"]["resent_states"] = [s[1] for s in st["stall"]["resent_states"] if s[1] != "lost"]
        after = [r for r in rows if start <= r["abs_frame"] < start + int(WIN_S * fps)]
        res[k] = window_metrics(after, ids_before, fps, start + int(WIN_S * fps))
        res[k]["first"] = [(x[0], x[1]) for x in em(after[0])] if after else None
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tl", default="/data/WallDance/tmp_analysis/stall_d34/tl")
    ap.add_argument("--json", default="/data/WallDance/tmp_analysis/stall_d34/results.json")
    a = ap.parse_args()
    tl = Path(a.tl)
    out = []
    for take, stalls in (("m30", ["296:1.58:gap", "660:1.7", "840:1.7", "920:1.7"]),
                         ("n3", ["420:1.7", "1320:1.7", "1420:1.7"]),
                         ("n3p", ["420:1.7", "1320:1.7", "522:1.7", "1178:1.7"]),
                         ("m30p", ["840:1.7", "703:1.7"])):
        for st in stalls:
            try:
                r = analyse_stall(tl, take, st)
            except FileNotFoundError as exc:
                print(f"{take} {st}: missing ({exc.filename})")
                continue
            r["take"] = take
            out.append(r)
            print(f"{take} {st}: ids {r['ids_before']} {r['states_before']}  pre-identical {r['pre_identical']}")
            for k in ("ctrl", "before", "after"):
                s = r.get(f"{k}_stall")
                ks = (f"  silence {s['silence_s']} s, removed {s['removed_s']} s, re-sent {s['resends']}x "
                      f"ids {s['resent_ids']} {sorted(set(s['resent_states']))}") if s else ""
                print(f"   {k:6s} first {r[k]['first']}  gone {r[k]['gone']}  drop_at {r[k]['drop_at']}  "
                      f"new ids {r[k]['new_ids']}  on {r[k]['on']}  extra {r[k]['extra']}{ks}")
    Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
