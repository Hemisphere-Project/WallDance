#!/usr/bin/env python3
"""D34 (camera stalls): before / after replays with injected stalls, TRT x@1280 on dev37.

  before = --set camera_stall_hold=false   (the hold clocks count the silence: the live app until D34)
  after  = the D34 default                 (camera-live hold clocks + the OSC keep-alive)
  ctrl   = no stall                        (the take as replayed until now)

mur30m-0710 slot 4 (the V2 settings of tmp_analysis/confirm_0710): its real stall (frames 295 -> 296,
~1.63 s for one frame interval -- the frames are missing from the recording, only the clock jumps) and
1.7 s stalls injected (frames skipped) at 33.8 / 43.0 / 47.1 s.  mur25m-night-common s3 (the flow
check's s1 settings, tmp_analysis/flowcheck/night_rc_46a0262) with 1.7 s stalls at 21 / 66 / 71 s.
n3p / m30p: the same without the empty-wall snapshot and the belt (plain coasting: the hold decides),
plus stalls that end where YOLO then misses the dancer for a while (n3p 522 -> 0.85 s, 1178 -> 1.65 s,
m30p 703 -> 1.5 s of coasting in the run without a stall).

  run_stalls.py [--wt WORKTREE] [--out DIR]     (cached: an existing timeline is not re-run)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

PY = "/data/WallDance/application/.venv/bin/python"
ENG = "/data/WallDance/models/dev37"
PRJ = Path("/data/WallDance/projects")

M30 = ["--project", "mur30m-0710", "--slot", "4",
       "--video", str(PRJ / "mur30m-0710/recordings/slot_4_20261007_201949.avi"),
       "--set", "confidence=0.15", "--set", "sensitivity_conf_seed=0.15",
       "--set", "tracker_intermittent_confirm=true", "--set", "tracking_mode=yolo_first",
       "--set", "yolo_imgsz=1280", "--set", "gamma=0.8", "--set", "clahe_clip=1.0",
       "--set", "mog2_var_threshold=8.0", "--set", "mog2_scale=0.5", "--set", "fg_enabled=true",
       "--set", "fg_plate=" + str(PRJ / "mur30m-0710/plates/flowcheck_flow_v2_slot_2_20261007_200742_1700.npz"),
       "--set", "person_height_px=253"]
N3 = ["--project", "mur25m-night-common", "--slot", "3",
      "--video", str(PRJ / "mur25m-night-common/recordings/slot_3_20261006_212855.avi"),
      "--set", "gamma=0.73", "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=16.0",
      "--set", "mog2_scale=0.5", "--set", "fg_enabled=true",
      "--set", "fg_plate=" + str(PRJ / "mur25m-night-common/plates/"
                                 "flowcheck_night_rc_46a0262_slot_1_20261006_211624_100.npz")]
PLAIN = ["--set", "fg_enabled=false", "--set", "use_ir_belt=false"]   # plain coasting: no snapshot, no belt
PLAN = [("m30", M30, ["296:1.58:gap", "660:1.7", "840:1.7", "920:1.7"]),
        ("n3", N3, ["420:1.7", "1320:1.7", "1420:1.7"]),
        ("n3p", N3 + PLAIN, ["420:1.7", "1320:1.7", "522:1.7", "1178:1.7"]),
        ("m30p", M30 + PLAIN, ["840:1.7", "703:1.7"])]


def tag(stall: str) -> str:
    return stall.replace(":", "_").replace(".", "_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wt", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--out", default="/data/WallDance/tmp_analysis/stall_d34/tl")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def run(name, args):
        tl = out / f"{name}.json"
        if tl.exists():
            print(f"[d34] {name} cached", flush=True)
            return
        t0 = time.time()
        cmd = ["nice", "-n", "5", PY, "tests/replay.py", *args, "--trt", "--engine-dir", ENG,
               "--imgsz", "1280", "--quality", "--internal", "--timeline", str(tl)]
        with open(out / f"{name}.log", "w") as log:
            r = subprocess.run(cmd, cwd=Path(a.wt) / "application", stdout=log, stderr=subprocess.STDOUT,
                               env=dict(os.environ))
        print(f"[d34] {name} rc={r.returncode} {time.time() - t0:.0f} s", flush=True)

    for take, base, stalls in PLAN:
        run(f"{take}_ctrl", base)
        for st in stalls:
            run(f"{take}_{tag(st)}_before", base + ["--stall", st, "--set", "camera_stall_hold=false"])
            run(f"{take}_{tag(st)}_after", base + ["--stall", st])
    print(f"[d34] done {time.strftime('%H:%M')}", flush=True)


if __name__ == "__main__":
    main()
