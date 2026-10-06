#!/usr/bin/env python3
"""TEST-2: emitted-stream goldens for the identity-slot output layer (PLAN_25M A7).

The slot layer is output-only and deterministic, so a replay timeline recorded once
(``replay.py --score --internal --timeline``: the tracker's reported tracks, its internal
tracks and the frame's YOLO detections) is enough to re-run it in milliseconds, without a
GPU or the recordings.  Each fixture in ``tests/golden/emitted/<name>.json.gz`` holds a
stripped timeline + the scoring manifest (+ an optional injected static ghost); the
golden ``<name>.golden.json`` holds the per-frame emitted stream (slot ids, states,
positions) and the demo-KPI block it scored, for the shipped SlotParams defaults.

  python tests/emitted_golden.py --update            # regenerate every golden (after an intended change)
  python tests/emitted_golden.py --build NAME TIMELINE MANIFEST --max-dancers N [--ghost x,y,h] [--frames a:b]
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import gzip
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for p in (_HERE.parent / "src", _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.identity_slots import IdentitySlots, SlotParams  # noqa: E402
from core.config import (IDENTITY_SLOTS_COAST_S, IDENTITY_SLOTS_FILTER_INPUT,  # noqa: E402
                         IDENTITY_SLOTS_STABILITY, IDENTITY_SLOTS_STATIC_GUARD,
                         IDENTITY_SLOTS_STATIC_RELEASE_S)
import output_quality  # noqa: E402
import scoring  # noqa: E402
from slot_replay import candidates_from_row, hidden_from_row  # noqa: E402

DIR = _HERE / "golden" / "emitted"
KPI_KEYS = (("continuity", "coverage"), ("continuity", "on_dancer"), ("continuity", "spatial_validity"),
            ("continuity", "gap_max_s"), ("quality", "coasting_share"), ("quality", "over_n_frames"),
            ("quality", "id_switches"), ("quality", "jitter_rest_pct"))
_TRACK_KEYS = ("id", "bbox", "centroid", "raw", "hits", "age", "fss", "tsu", "src")
_INT_KEYS = ("id", "sm", "p", "h", "hits", "fss", "tsu", "emit")


def _r(v):
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, list):
        return [_r(x) for x in v]
    return v


def strip_rows(rows, a=None, b=None):
    out = []
    for r in rows:
        f = r.get("abs_frame", r["frame"])
        if (a is not None and f < a) or (b is not None and f >= b):
            continue
        out.append({"frame": (f - a) if a is not None else r["frame"],
                    "abs_frame": f, "reported": int(r.get("reported", len(r.get("tracks") or []))),
                    "ids": list(r.get("ids") or []),
                    "tracks": [{k: _r(t.get(k)) for k in _TRACK_KEYS if t.get(k) is not None} for t in r.get("tracks") or []],
                    "int": [{k: _r(t.get(k)) for k in _INT_KEYS if t.get(k) is not None} for t in r.get("int") or []],
                    "ref": [{k: _r(d.get(k)) for k in ("c", "h", "conf")} for d in r.get("ref") or []]})
    return out


def inject_ghost(rows, ghost):
    x, y, h = ghost
    out = []
    for r in rows:
        r = dict(r)
        f = r.get("abs_frame", r["frame"])
        r["tracks"] = list(r.get("tracks") or []) + [{
            "id": 900000, "bbox": [x - 0.2 * h, y - 0.5 * h, 0.4 * h, h], "centroid": [x, y], "raw": [x, y],
            "hits": f + 1, "age": f + 1, "fss": 0, "tsu": 0, "src": "yolo"}]
        out.append(r)
    return out


def load_fixture(name):
    return json.loads(gzip.decompress((DIR / f"{name}.json.gz").read_bytes()))


def fixtures():
    return sorted(p.name[:-len(".json.gz")] for p in DIR.glob("*.json.gz"))


def shipped_params() -> SlotParams:
    """The SlotParams the app builds from core.config (pipeline._run_identity_slots)."""
    g = bool(IDENTITY_SLOTS_STATIC_GUARD)
    return SlotParams(stability=IDENTITY_SLOTS_STABILITY, coast_s=IDENTITY_SLOTS_COAST_S,
                      static_guard=g, static_yield=g, static_release_s=IDENTITY_SLOTS_STATIC_RELEASE_S,
                      filter_input=IDENTITY_SLOTS_FILTER_INPUT)


def simulate(fx, params: SlotParams | None = None):
    """Run the slot layer over a fixture -> (rows with `emitted`, KPI dict)."""
    p = params or shipped_params()
    p = dataclasses.replace(p, max_dancers=int(fx["max_dancers"]))
    rows = fx["rows"]
    man = copy.deepcopy(fx["manifest"])
    if fx.get("ghost"):
        rows = inject_ghost(rows, fx["ghost"])
        man.setdefault("reference", {}).setdefault("exclude_spots", [])
        g = fx["ghost"]
        man["reference"]["exclude_spots"] = list(man["reference"]["exclude_spots"]) + [[g[0], g[1], 0.5 * g[2]]]
    fps = float(man.get("fps") or 19.8)
    slots = IdentitySlots(p)
    out = []
    for r in sorted(rows, key=lambda r: r["frame"]):
        t = r.get("abs_frame", r["frame"]) / fps
        cands = candidates_from_row(r)
        res = slots.update(cands, t, hidden=hidden_from_row(r, set(slots.bound_keys()) - {c.key for c in cands}))
        row = dict(r)
        row["emitted"] = {"reported": len(res), "ids": sorted(o.slot_id for o in res),
                          "tracks": [{"id": o.slot_id, "bbox": [o.x - o.w / 2, o.y - o.h / 2, o.w, o.h],
                                      "centroid": [o.x, o.y], "state": o.state} for o in res]}
        out.append(row)
    rep = output_quality.compare_streams(out, man, fps=fps)["emitted"]
    kpi = {f"{sec}.{k}": (rep.get(sec) or {}).get(k) for sec, k in KPI_KEYS}
    return out, kpi


def stream_of(rows):
    return [[r["frame"], [[t["id"], t["state"], round(t["centroid"][0], 1), round(t["centroid"][1], 1)]
                          for t in r["emitted"]["tracks"]]] for r in rows]


def write_golden(name):
    fx = load_fixture(name)
    rows, kpi = simulate(fx)
    g = {"fixture": name, "params": dataclasses.asdict(dataclasses.replace(shipped_params(), max_dancers=int(fx["max_dancers"]))),
         "kpi": kpi, "stream": stream_of(rows)}
    (DIR / f"{name}.golden.json").write_text(json.dumps(g, separators=(",", ":")))
    print(f"golden {name}: " + ", ".join(f"{k.split('.')[1]}={v}" for k, v in kpi.items()))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--build", nargs=3, metavar=("NAME", "TIMELINE", "MANIFEST"))
    ap.add_argument("--max-dancers", type=int, default=2)
    ap.add_argument("--ghost", default=None)
    ap.add_argument("--frames", default=None, help="a:b absolute frame window")
    ap.add_argument("--source", default="")
    a = ap.parse_args()
    DIR.mkdir(parents=True, exist_ok=True)
    if a.build:
        name, tl, man = a.build
        fa = fb = None
        if a.frames:
            fa, fb = (int(v) for v in a.frames.split(":"))
        rows = strip_rows(scoring._load_timeline(tl), fa, fb)
        m = json.loads(Path(man).read_text())
        if fa is not None:
            m = dict(m, start=fa, frames=fb - fa)
        fx = {"name": name, "manifest": m, "max_dancers": a.max_dancers,
              "ghost": [float(v) for v in a.ghost.split(",")] if a.ghost else None,
              "source": a.source, "rows": rows}
        (DIR / f"{name}.json.gz").write_bytes(gzip.compress(json.dumps(fx, separators=(",", ":")).encode(), 9))
        write_golden(name)
    if a.update:
        for name in fixtures():
            write_golden(name)


if __name__ == "__main__":
    main()
