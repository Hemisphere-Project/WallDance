#!/usr/bin/env python3
"""Release gate B4 (PLAN_25M): PoseRunner A/B on the laptop.  Each scenario is replayed twice through the
TRT show path, with the PERF-3 PoseRunner (default) and with WD_POSE_RUNNER=0 (the plain ultralytics call),
and the two summaries + timelines are compared byte for byte.  One JSON line per scenario comes back.
Run on the DEV slot from application/:

  python extra/wdremote.py --slot dev py tmp_analysis/plan25m/ab_poserunner.py -- [--scenarios hangar-aerial,white-duo]
"""
import argparse, hashlib, json, os, subprocess, sys, time
from pathlib import Path


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def first_diff(a, b):
    ra, rb = json.loads(Path(a).read_text()), json.loads(Path(b).read_text())
    for i, (x, y) in enumerate(zip(ra, rb)):
        if x != y:
            return {"row": i, "frame": x.get("frame") if isinstance(x, dict) else None}
    return {"len": [len(ra), len(rb)]} if len(ra) != len(rb) else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", default="hangar-aerial,white-duo")
    a = ap.parse_args()
    out = Path(os.environ.get("WD_REMOTE_OUT", "."))
    res = out / "ab_poserunner.jsonl"
    for s in a.scenarios.split(","):
        files = {}
        row = {"scenario": s}
        for mode in ("runner", "plain"):
            env = dict(os.environ)
            if mode == "plain":
                env["WD_POSE_RUNNER"] = "0"
            tl, sm = out / f"{s}_{mode}_timeline.json", out / f"{s}_{mode}_summary.json"
            t0 = time.time()
            pr = subprocess.run([sys.executable, "tests/replay.py", "--scenario", f"tests/scenarios/{s}.json",
                                 "--trt", "--score", "--timeline", str(tl), "--out", str(sm)],
                                capture_output=True, text=True, env=env)
            row[f"{mode}_rc"], row[f"{mode}_s"] = pr.returncode, round(time.time() - t0)
            if pr.returncode != 0:
                row[f"{mode}_err"] = (pr.stderr or pr.stdout)[-400:]
                continue
            files[mode] = (tl, sm)
            row[f"{mode}_poserunner_log"] = [l for l in pr.stdout.splitlines() if "PoseRunner" in l][:2]
        if len(files) == 2:
            (tl_a, sm_a), (tl_b, sm_b) = files["runner"], files["plain"]
            row["timeline_equal"] = sha(tl_a) == sha(tl_b)
            row["summary_equal"] = sha(sm_a) == sha(sm_b)
            if not row["timeline_equal"]:
                row["first_diff"] = first_diff(tl_a, tl_b)
            for p in (tl_a, tl_b):                       # keep only the small summaries for the 4G link
                Path(p).unlink()
        with open(res, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
