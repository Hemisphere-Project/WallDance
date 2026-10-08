#!/usr/bin/env python3
"""D34 offline sweep: the identity-slot layer alone on recorded timelines, one ~1.7 s camera stall
injected at a time (every STEP seconds), before vs after D34.

The slot layer runs as in emitted_golden.py (shipped SlotParams; no belt, no snapshot: plain coasting
and smart hold).  A stall at frame F removes the frames F..F+G-1 (G = 1.7 s of frames) and the clock
jumps; before = the hold clocks count the jump, after = they count 3 frame periods (the motion model
keeps the jump in both).  The tracker output after the stall is the recorded one: that tracker saw
every frame, so it keeps the dancer more often than a live tracker after a 1.7 s jump would -- what
is measured is the slot layer's hold, with an optimistic tracker.

Per stall, over the 10 s after it, against the same frames of the run without a stall:
  lost   an id the run without a stall emits is absent (frames), and whether any was
  drop   (a stall that cost TouchDesigner an id at least once);
  extra  an id emitted that the run without a stall does not emit (frames: a held point
         the no-stall run had already released, or an id switch).

  sweep_offline.py [--step 2.0] [--json OUT]
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "application"
sys.path[:0] = [str(APP / "src"), str(APP / "tests")]
import emitted_golden as eg  # noqa: E402
from core.identity_slots import IdentitySlots  # noqa: E402
from slot_replay import candidates_from_row, hidden_from_row  # noqa: E402

STALL_S = 1.7
WIN_S = 10.0
TL = Path("/data/WallDance/tmp_analysis")
TIMELINES = [  # (name, path, fps, max dancers)
    ("mur30m-0710 s4 (V2)", TL / "stall_d34/tl/m30_ctrl.json", 19.516, 2),
    ("night s3 (s1)", TL / "flowcheck/night_rc_46a0262/s1_slot_3_20261006_212855.json", 20.0, 2),
    ("night s4 (s1)", TL / "flowcheck/night_rc_46a0262/s1_slot_4_20261006_213310.json", 20.0, 2),
    ("night s2c (s1)", TL / "flowcheck/night_rc_46a0262/s1_slot_2_20261006_214531.json", 20.0, 2),
]


def step(slots, r, t, tm, pos=None):
    cands = candidates_from_row(r)
    hid = hidden_from_row(r, set(slots.bound_keys()) - {c.key for c in cands})
    outs = slots.update(cands, t, hidden=hid, t_motion=tm)
    if pos is not None:
        pos.clear()
        pos.update({o.slot_id: (o.x, o.y) for o in outs})
    return sorted(o.slot_id for o in outs)


def on_ref(r, xy):
    """The point is within 0.75 h of a YOLO reference box of the frame (None: no reference)."""
    refs = r.get("ref")
    if refs is None:
        return None
    return any(((xy[0] - f["c"][0]) ** 2 + (xy[1] - f["c"][1]) ** 2) ** 0.5 <= 0.75 * float(f["h"])
               for f in refs)


def sweep(name, rows, fps, p, every_s):
    rows = sorted(rows, key=lambda r: r["frame"])
    n = len(rows)
    g = int(round(STALL_S * fps))
    w = int(WIN_S * fps)
    thr = 3.0 / fps
    every = max(1, int(round(every_s * fps)))
    starts = set(range(int(5 * fps), n - g - w, every))
    base = IdentitySlots(p)
    ids = []
    snaps = {}
    for i, r in enumerate(rows):
        if i in starts:
            snaps[i] = copy.deepcopy(base)
        t = i / fps
        ids.append(step(base, r, t, t))
    res = []
    for f in sorted(snaps):
        if not ids[f - 1]:
            continue                       # nobody emitted when the camera stalled
        out = {"frame": f, "t": round(f / fps, 2), "ids": ids[f - 1]}
        for mode in ("before", "after"):
            s = copy.deepcopy(snaps[f])
            off = 0.0 if mode == "before" else (g + 1) / fps - thr      # gap F-1 -> F+G, minus 3 periods
            lost = extra = extra_on = extra_off = 0
            pos = {}
            for k in range(f + g, f + g + w):
                tm = k / fps
                got = step(s, rows[k], tm - off, tm, pos)
                lost += sum(1 for i in ids[k] if i not in got)
                for i in got:
                    if i not in ids[k]:
                        extra += 1
                        on = on_ref(rows[k], pos[i])
                        extra_on += 1 if on else 0
                        extra_off += 1 if (on is False and rows[k].get("ref")) else 0
            out[mode] = lost
            out[mode + "_extra"] = extra
            out[mode + "_extra_on"] = extra_on
            out[mode + "_extra_off"] = extra_off
        res.append(out)
    nb = sum(1 for x in res if x["before"] > 0)
    na = sum(1 for x in res if x["after"] > 0)
    eb = sum(x["before_extra"] for x in res)
    ea = sum(x["after_extra"] for x in res)
    eob = sum(x["before_extra_on"] for x in res)
    eoa = sum(x["after_extra_on"] for x in res)
    efb = sum(x["before_extra_off"] for x in res)
    efa = sum(x["after_extra_off"] for x in res)
    print(f"{name}: {len(res)} stalls; an id lost (vs no stall) after {nb} of them before D34, "
          f"{na} after; lost frames {sum(x['before'] for x in res)} -> {sum(x['after'] for x in res)}; "
          f"extra point-frames {eb} -> {ea} (on a YOLO box {eob} -> {eoa}, off every YOLO box of the frame "
          f"{efb} -> {efa}, the rest: no YOLO box)")
    return {"take": name, "stalls": len(res), "drop_before": nb, "drop_after": na,
            "lost_before": sum(x["before"] for x in res), "lost_after": sum(x["after"] for x in res),
            "extra_before": eb, "extra_after": ea, "extra_on_before": eob, "extra_on_after": eoa,
            "extra_off_before": efb, "extra_off_after": efa,
            "detail": res}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=2.0)
    ap.add_argument("--json", default=str(TL / "stall_d34/sweep_offline.json"))
    a = ap.parse_args()
    out = []
    for fx_name in eg.fixtures():
        fx = eg.load_fixture(fx_name)
        if fx.get("ghost"):
            continue
        p = dataclasses.replace(eg.shipped_params(), max_dancers=int(fx["max_dancers"]))
        out.append(sweep(fx_name, fx["rows"], float(fx["manifest"].get("fps") or 19.8), p, a.step))
    for name, path, fps, nmax in TIMELINES:
        if not path.exists():
            print(f"{name}: missing {path}")
            continue
        p = dataclasses.replace(eg.shipped_params(), max_dancers=nmax)
        out.append(sweep(name, json.loads(path.read_text()), fps, p, a.step))
    Path(a.json).write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
