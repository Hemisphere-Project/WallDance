#!/usr/bin/env python3
"""D34 gate: the operator-flow check on the 2026-10-06 night takes (tmp_analysis/flowcheck/night_flow.sh,
same takes / labels / settings) from this tree, then the comparison with a reference run
(default night_rc_46a0262): every timeline's emitted stream and every take's events must be equal.

  flow_gate.py run     [--wt WORKTREE] [--out DIR]
  flow_gate.py compare [--out DIR] [--ref DIR]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

PY = "/data/WallDance/application/.venv/bin/python"
ENG = "/data/WallDance/models/dev37"
FC = Path("/data/WallDance/tmp_analysis/flowcheck")
S2A, S2C, S3 = "slot_2_20261006_211831.avi", "slot_2_20261006_214531.avi", "slot_3_20261006_212855.avi"
S4, S6, S7 = "slot_4_20261006_213310.avi", "slot_6_20261006_214306.avi", "slot_7_20261006_220640.avi"
RUNS = [("s1", "slot_1_20261006_211624.avi:100", [S6, S7, S4, S3, S2C, S2A]),
        ("dark", "slot_2_20261006_214010.avi:20", [S6, S7, S4, S2C])]


def run(wt: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for label, empty, takes in RUNS:
        cmd = ["nice", "-n", "5", PY, "tests/flow_check.py", "--project", "mur25m-night-common", "--empty", empty,
               "--takes", ",".join(takes), "--label", label, "--out", str(out), "--engine-dir", ENG]
        with open(out / f"{label}.log", "w") as log:
            r = subprocess.run(cmd, cwd=wt / "application", stdout=log, stderr=subprocess.STDOUT)
        print(f"[gate] {label} rc={r.returncode}", flush=True)


def emitted(path: Path):
    rows = sorted(json.loads(path.read_text()), key=lambda r: r["frame"])
    return [[r["frame"], [[t["id"], t.get("state"), t["centroid"], t["bbox"]]
                          for t in (r.get("emitted") or {}).get("tracks") or []]] for r in rows]


def _events(ev: dict) -> dict:
    ev = json.loads(json.dumps(ev))
    for v in ev.values():
        for h in v.get("holes") or []:
            h.pop("empty_s", None)
            h.pop("h_med", None)
    return ev


def compare(out: Path, ref: Path) -> int:
    bad = 0
    for tl in sorted(ref.glob("*_slot_*.json")):
        mine = out / tl.name
        if not mine.exists():
            print(f"[gate] {tl.name}: MISSING")
            bad += 1
            continue
        a, b = emitted(tl), emitted(mine)
        same = a == b
        diff = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        print(f"[gate] {tl.name}: {'identical' if same else f'{diff} frames differ'} ({len(a)} frames)")
        bad += 0 if same else 1
    for label, _e, _t in RUNS:
        ra, rb = json.loads((ref / f"{label}.json").read_text()), json.loads((out / f"{label}.json").read_text())
        for ta, tb in zip(ra["takes"], rb["takes"]):
            # flow_check.analyse gained two hole fields after 46a0262 (c30b6e9: empty_s, h_med)
            same = _events(ta["events"]) == _events(tb["events"])
            print(f"[gate] {label} {ta['take']}: events {'identical' if same else 'DIFFER'}")
            bad += 0 if same else 1
        ca, cb = ra["calibrate"]["after"], rb["calibrate"]["after"]
        print(f"[gate] {label} Calibrate: {'identical' if ca == cb else f'{ca} vs {cb}'}")
        bad += 0 if ca == cb else 1
    print(f"[gate] {'PASS' if not bad else f'{bad} differences'}")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("run", "compare"))
    ap.add_argument("--wt", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--out", default=str(FC / "night_d34"))
    ap.add_argument("--ref", default=str(FC / "night_rc_46a0262"))
    a = ap.parse_args()
    if a.what == "run":
        run(Path(a.wt), Path(a.out))
    else:
        sys.exit(1 if compare(Path(a.out), Path(a.ref)) else 0)


if __name__ == "__main__":
    main()
