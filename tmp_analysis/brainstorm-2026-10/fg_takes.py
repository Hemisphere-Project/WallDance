#!/usr/bin/env python3
"""Clean-plate foreground N-lock on field takes, in one go (PLAN_25M B3b; runs on the laptop over 4G).

For each slot: decode the newest take (ROI from the project config, 4x downscale), use the newest take of
--plate-slot (the empty wall) as the plate, find the replay timeline that B3 left in tmp_analysis/remote/
(the summary.json whose "video" is this take), run nlock.py --variant fg --bg plate, and write one JSON line
per take into $WD_REMOTE_OUT (or --out).  Scored against YOLO >= 0.5 with N dancers (--n).

  python extra/wdremote.py py --with tmp_analysis/brainstorm-2026-10/fg_lib.py \
      --with tmp_analysis/brainstorm-2026-10/nlock.py tmp_analysis/brainstorm-2026-10/fg_takes.py -- \
      --project mur25m-ceinture-0610 --slots 4,5,6,9 --plate-slot 1 --n 2
"""
import argparse, glob, json, os, subprocess, sys, tempfile
import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = next((r for r in (".", "..", "../..") if os.path.isdir(os.path.join(r, "projects"))), "..")
sys.path.insert(0, os.path.join(ROOT, "application", "tests"))


def newest(project, slot):
    takes = sorted(glob.glob(os.path.join(ROOT, "projects", project, "recordings", f"slot_{slot}_*.avi")))
    return takes[-1] if takes else None


def decode(video, roi, ds=4, max_frames=0):
    x0, y0, w, h = roi
    cap = cv2.VideoCapture(video); fr = []
    while True:
        ok, f = cap.read()
        if not ok or (max_frames and len(fr) >= max_frames):
            break
        g = f[y0:y0 + h, x0:x0 + w, 0].astype(np.float32)
        fr.append(cv2.resize(g, (w // ds, h // ds), interpolation=cv2.INTER_AREA).astype(np.float16))
    return np.stack(fr)


def find_timeline(video):
    base = os.path.basename(video); best = None
    for sp in glob.glob(os.path.join(ROOT, "tmp_analysis", "remote", "*", "summary.json")):
        try:
            s = json.load(open(sp))
        except Exception:
            continue
        tl = os.path.join(os.path.dirname(sp), "timeline.json")
        if os.path.basename(str(s.get("video", ""))) == base and os.path.exists(tl):
            if best is None or os.path.getmtime(tl) > os.path.getmtime(best):
                best = tl
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True); ap.add_argument("--slots", required=True)
    ap.add_argument("--plate-slot", type=int, default=1); ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--max-frames", type=int, default=4000); ap.add_argument("--out", default=os.environ.get("WD_REMOTE_OUT", "."))
    ap.add_argument("--timeline", action="append", default=[], help="slot=path override")
    a = ap.parse_args()
    import replay
    cfg = replay._latest_config(a.project)
    meta_roi = None
    if cfg.get("roi_enabled") and cfg.get("roi_w"):
        meta_roi = (int(cfg["roi_x"]), int(cfg["roi_y"]), int(cfg["roi_w"]), int(cfg["roi_h"]))
    plate_v = newest(a.project, a.plate_slot)
    if plate_v is None:
        raise SystemExit(f"no take in plate slot {a.plate_slot}")
    cap = cv2.VideoCapture(plate_v); W, H = int(cap.get(3)), int(cap.get(4)); cap.release()
    roi = meta_roi or (0, 0, W, H)
    tmp = tempfile.mkdtemp(prefix="fgtakes_")
    np.save(os.path.join(tmp, "plate.npy"), decode(plate_v, roi, max_frames=600))
    overrides = dict(kv.split("=", 1) for kv in a.timeline)
    os.makedirs(a.out, exist_ok=True)
    for slot in [int(s) for s in a.slots.split(",")]:
        v = newest(a.project, slot)
        if v is None:
            print(f"slot {slot}: no take"); continue
        tl = overrides.get(str(slot)) or find_timeline(v)
        if tl is None:
            print(f"slot {slot}: no replay timeline for {os.path.basename(v)} (run B3 first)"); continue
        meta = json.load(open(v + ".meta")) if os.path.exists(v + ".meta") else {}
        man = {"name": f"{a.project}-s{slot}", "project": a.project, "slot": slot, "start": 0, "frames": 10 ** 9, "warmup": 15,
               "fps": float(meta.get("actual_fps", 19.8)), "expected_count": a.n,
               "reference": {"min_conf": 0.5, "tol_h": 0.75, "exclude_spots": []},
               "config": dict(cfg, roi_x=roi[0], roi_y=roi[1])}
        mp = os.path.join(tmp, f"m{slot}.json"); json.dump(man, open(mp, "w"))
        sp = os.path.join(tmp, f"s{slot}.npy"); np.save(sp, decode(v, roi, max_frames=a.max_frames))
        res = subprocess.run([sys.executable, os.path.join(HERE, "nlock.py"), sp, tl, mp, "--variant", "fg", "--bg", "plate",
                              "--plate_npy", os.path.join(tmp, "plate.npy"), "--gain_norm", "1", "--max_frame", str(a.max_frames)],
                             capture_output=True, text=True)
        line = next((l for l in res.stdout.splitlines()[::-1] if l.startswith("{")), None)
        print(f"slot {slot} ({os.path.basename(v)}): {line or res.stderr[-400:]}")
        if line:
            open(os.path.join(a.out, f"fg_slot{slot}.json"), "w").write(line + "\n")
        os.remove(sp)


main()
