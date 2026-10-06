#!/usr/bin/env python3
"""Re-run the identity-slot output layer offline over a replay timeline.

The slot layer (``core/identity_slots.py``) is output-only: it consumes the
tracker's reported tracks and never feeds back.  So a timeline recorded once
with ``replay.py --score|--quality --timeline T.json`` (rows carry the reported
tracks with ``raw``/``hits``/``fss``/...) is enough to try any slot setting in
seconds, without YOLO:

    python tests/slot_replay.py --timeline T.json --scenario tests/scenarios/white-duo.json \
        --set stability=0.6 --set coast_s=2.0

Without ``--scenario`` (field takes) pass ``--fps`` and ``--set max_dancers=N``.
Prints the tracker-vs-emitted comparison (``output_quality``).  ``simulate`` is
importable for sweeps.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
for p in (_HERE.parent / "src", _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.identity_slots import (IdentitySlots, SlotCandidate,  # noqa: E402
                                 SlotParams)
import output_quality  # noqa: E402
import scoring  # noqa: E402


def candidates_from_row(row: dict, position: str = "smoothed") -> List[SlotCandidate]:
    out = []
    for t in row.get("tracks") or []:
        b = t["bbox"]
        xy = t.get("raw") if position == "raw" else None
        if xy is None:
            xy = t.get("centroid") or [b[0] + b[2] / 2.0, b[1] + b[3] / 2.0]
        raw = t.get("raw")
        out.append(SlotCandidate(
            key=int(t["id"]), x=float(xy[0]), y=float(xy[1]),
            fx=None if raw is None else float(raw[0]), fy=None if raw is None else float(raw[1]),
            w=float(b[2]), h=float(b[3]), hits=int(t.get("hits", 99)),
            age=int(t.get("age", 99)), fss=t.get("fss"), tsu=int(t.get("tsu", 0)),
            src=t.get("src")))
    return out


def hidden_from_row(row: dict, keys: set, position: str = "smoothed") -> Dict[int, SlotCandidate]:
    """Hidden (alive, unreported) internal tracks for the bound ``keys``, from
    the row's ``int`` block (``replay.py --internal``)."""
    out: Dict[int, SlotCandidate] = {}
    if not keys:
        return out
    for t in row.get("int") or []:
        k = int(t["id"])
        if k not in keys:
            continue
        xy = t["p"] if position == "raw" else t.get("sm", t["p"])
        h = float(t["h"])
        out[k] = SlotCandidate(key=k, x=float(xy[0]), y=float(xy[1]), w=0.4 * h, h=h,
                               hits=int(t["hits"]), fss=int(t["fss"]), tsu=int(t["tsu"]))
    return out


def simulate(rows: List[dict], params: SlotParams, fps: float,
             position: str = "smoothed") -> List[dict]:
    """Rows with a fresh ``emitted`` block from the slot layer (input rows are
    not modified)."""
    slots = IdentitySlots(params)
    out = []
    for r in sorted(rows, key=lambda r: r["frame"]):
        t = (r.get("abs_frame", r["frame"])) / fps
        cands = candidates_from_row(r, position)
        res = slots.update(cands, t, hidden=hidden_from_row(
            r, set(slots.bound_keys()) - {c.key for c in cands}, position))
        em_tracks = [{"id": o.slot_id,
                      "bbox": [o.x - o.w / 2.0, o.y - o.h / 2.0, o.w, o.h],
                      "centroid": [o.x, o.y], "state": o.state,
                      "key": o.key} for o in res]
        row = dict(r)
        row["emitted"] = {"reported": len(res), "ids": sorted(o.slot_id for o in res),
                          "tracks": em_tracks}
        if slots.events:
            row["slot_events"] = list(slots.events)
        out.append(row)
    return out


def params_with(sets: List[str], base: Optional[SlotParams] = None) -> SlotParams:
    p = base or SlotParams()
    for kv in sets or []:
        k, _, v = kv.partition("=")
        k = k.strip()
        if not hasattr(p, k):
            raise SystemExit(f"unknown slot param {k!r}")
        setattr(p, k, type(getattr(p, k))(json.loads(v)))
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--timeline", required=True)
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--fps", type=float, default=None)
    ap.add_argument("--position", choices=("raw", "smoothed"), default="smoothed")
    ap.add_argument("--set", dest="sets", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rows = scoring._load_timeline(a.timeline)
    manifest = scoring.load_scenario(a.scenario) if a.scenario else None
    fps = a.fps or (manifest or {}).get("fps") or 20.0
    p = SlotParams()
    if manifest is not None:
        p.max_dancers = max(1, scoring.max_expected(manifest))
    p = params_with(a.sets, p)
    sim = simulate(rows, p, fps, a.position)
    rep = output_quality.compare_streams(sim, manifest, fps=fps,
                                         max_dancers=None if manifest else p.max_dancers)
    print(output_quality.format_comparison(rep))
    if a.json:
        print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
