#!/usr/bin/env python3
"""Night takes (mur25m-ceinture-0610) A/B on the laptop: replay each take x variant through the
app pipeline, trim to the 'operator at the wall' window, score with N = 1, and write ONE JSON
line per run (4G: only this comes back).  Run on the DEV slot from application/:

  python extra/wdremote.py --slot dev py tmp_analysis/plan25m/validate_night.py -- \
      [--takes s2c,s4] [--variants base,belt,fg] [--extra k=v,k=v]   (config: the project's latest)

Variants: base = belt_backing off, no plate (the shipped c01f3ff behaviour); belt = belt backing;
fg = belt backing + the clean plate of the take's crop; nobelt = fg without the belt.
"""
import argparse, json, os, subprocess, sys, tempfile, time
from collections import Counter
from pathlib import Path

HERE = Path.cwd()                       # application/
sys.path.insert(0, str(HERE / "tests")); sys.path.insert(0, str(HERE / "src"))
P = "mur25m-ceinture-0610"
REC = ["--set", "tracking_mode=yolo_first", "--set", "confidence=0.15", "--set", "tracker_intermittent_confirm=true"]
LAND = dict(roi_x=173, roi_y=401, roi_w=1304, roi_h=566, roi_source_w=1776, roi_source_h=1300)
PORT = dict(roi_x=29, roi_y=513, roi_w=1304, roi_h=566, roi_source_w=1488, roi_source_h=1528)
# take: (slot, file, roi, wall window [abs frames, from the 2026-10-07 morning replays], plate)
TAKES = {
    "s2a": (2, "slot_2_20261006_211831", LAND, (259, 5617), "land"),   # bright, still positions
    "s3":  (3, "slot_3_20261006_212855", PORT, (227, 1833), "port"),   # bright, moving (plate is dark)
    "s4":  (4, "slot_4_20261006_213310", PORT, (181, 2135), "port"),   # dark, moving
    "s6":  (6, "slot_6_20261006_214306", LAND, (286, 1128), "land"),   # dark, entry/exit (plate is bright)
    "s2c": (2, "slot_2_20261006_214531", LAND, (174, 4995), "land"),   # dark, 2 min still at the back
    "s7":  (7, "slot_7_20261006_220640", PORT, (244, 813), "port"),    # dark, moving (old build)
}
PLATES = {"land": ("slot_1_20261006_211624.avi", 100, 40), "port": ("slot_2_20261006_214010.avi", 20, 40)}
VARIANTS = {
    "base":   ["--set", "belt_backing=false", "--set", "fg_enabled=false"],
    "belt":   ["--set", "belt_backing=true", "--set", "fg_enabled=false"],
    "fg":     ["--set", "belt_backing=true", "--set", "fg_enabled=true"],
    "nobelt": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "use_ir_belt=false"],
    # the project's person_height_px is 45 (Calib2 never ran: 0 samples) while bodies at the wall are
    # 120-190 px: the size gate (0.3-2.5 x) drops them in frames with >= 2 detections, tracker gates too tight
    "h150":   ["--set", "belt_backing=false", "--set", "fg_enabled=false", "--set", "person_height_px=150"],
    "fgh150": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "person_height_px=150"],
    # base with LAST NIGHT's calibration (the 2026-10-06 21:03 Aim: gamma 0.73, CLAHE 2.5, MOG2 var 8 /
    # scale 0.7) instead of the project's latest (re-calibrated 2026-10-07 10:33 on the empty-wall take)
    "oldcal": ["--set", "belt_backing=false", "--set", "fg_enabled=false", "--set", "gamma=0.73",
               "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0", "--set", "mog2_scale=0.7"],
    "oldcalfgh150": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "gamma=0.73",
                     "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0", "--set", "mog2_scale=0.7",
                     "--set", "person_height_px=150"],
    # v2 (2026-10-07 noon): foreground with the selective plate update + a new dancer must have
    # moved 0.25 h (skeleton AND movement to open a slot; a still dancer keeps the slot it has)
    "v2old": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
              "--set", "gamma=0.73", "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0",
              "--set", "mog2_scale=0.7"],
    "v2new": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25"],
    # v3: v2 with the new-dancer travel weighted by YOLO confidence (still + confident enters), and
    # the same with the automatic person height (core/auto_height.py) instead of the stale 45 px
    "v3old": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
              "--set", "gamma=0.73", "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0",
              "--set", "mog2_scale=0.7"],
    "v3new": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25"],
    "v3oldah": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
                "--set", "gamma=0.73", "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0",
                "--set", "mog2_scale=0.7", "--set", "auto_height=true"],
    "v3newah": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
                "--set", "auto_height=true"],
    # v5 = the v3 flags on the code with the belt / plate / re-appearance waivers (3cde9b3 + the prior);
    # height_guard=false keeps the stale 45 px as in the v3/v5 runs
    "v5old": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
              "--set", "gamma=0.73", "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0",
              "--set", "mog2_scale=0.7", "--set", "height_guard=false"],
    "v5new": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
              "--set", "height_guard=false"],
    # v6 = v5 + the height guard (D29 default): the stale 45 px is replaced by the measured height
    "v6old": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
              "--set", "gamma=0.73", "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0",
              "--set", "mog2_scale=0.7", "--set", "height_guard=true"],
    "v6new": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
              "--set", "height_guard=true"],
    # input-size bench (Thomas 2026-10-07: tomorrow ~30 m, smaller dancers): v6old at other YOLO input sizes --
    # a smaller input = smaller dancers in YOLO's eyes (night bodies ~127 px in a ~1320 px ROI: 800 -> 77 px,
    # 960 -> 92, 1280 -> 123, 1536 -> 148); argparse keeps the last --imgsz
    **{f"v6old_{sz}": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
                       "--set", "gamma=0.73", "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0",
                       "--set", "mog2_scale=0.7", "--set", "height_guard=true", "--imgsz", str(sz)]
       for sz in (800, 960, 1536)},
    # what a Calibrate on slot_1 (the bright empty take) now gives: the scene step as at 10:33 (gamma 1.8 capped,
    # CLAHE 1.5, MOG2 16 @ 0.5), then the empty-wall check steps gamma down to 1.0 at confidence 0.15
    # (cal1*: slot_1's LAND plate for every take, as after a single Calibrate on slot_1 -- the PORT takes then
    # run without a plate: "other camera crop"); cal1b = the ladder stopping one rung earlier (gamma 1.27)
    "cal1": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
             "--set", "gamma=1.0", "--set", "clahe_clip=1.5", "--set", "mog2_var_threshold=16.0",
             "--set", "mog2_scale=0.5", "--set", "height_guard=true"],
    "cal1c": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
              "--set", "gamma=0.8", "--set", "clahe_clip=1.5", "--set", "mog2_var_threshold=16.0",
              "--set", "mog2_scale=0.5", "--set", "height_guard=true"],
    "cal1b": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0.25",
              "--set", "gamma=1.27", "--set", "clahe_clip=1.5", "--set", "mog2_var_threshold=16.0",
              "--set", "mog2_scale=0.5", "--set", "height_guard=true"],
    # D31 isolation: v6old without the entry rule (a new dancer needs no movement)
    "v6oldne": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "entry_min_travel_h=0",
                "--set", "gamma=0.73", "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0",
                "--set", "mog2_scale=0.7", "--set", "height_guard=true"],
    "oldcalfg": ["--set", "belt_backing=true", "--set", "fg_enabled=true", "--set", "gamma=0.73",
                 "--set", "clahe_clip=2.5", "--set", "mog2_var_threshold=8.0", "--set", "mog2_scale=0.7"],
}


def plate_rel(kind):
    take, start, n = PLATES[kind]
    rel = f"plates/plate_{Path(take).stem}_{start}.npz"
    if not (HERE.parent / "projects" / P / rel).exists():
        subprocess.run([sys.executable, "tests/make_plate.py", "--project", P, "--take", take,
                        "--start", str(start), "--frames", str(n)], check=True)
    return rel


def holes(rows, fps=20.0):
    out, start = [], None
    for r in rows:
        em = (r.get("emitted") or {}).get("tracks") or []
        if not em and start is None:
            start = r["frame"]
        elif em and start is not None:
            if r["frame"] - start >= fps:
                out.append([round(start / fps, 1), round((r["frame"] - start) / fps, 1)])
            start = None
    if start is not None and rows and rows[-1]["frame"] - start >= fps:
        out.append([round(start / fps, 1), round((rows[-1]["frame"] - start) / fps, 1)])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--takes", default=",".join(TAKES)); ap.add_argument("--variants", default="base,belt,fg")
    ap.add_argument("--extra", default="", help="extra --set k=v,k=v for every run")
    a = ap.parse_args()
    import output_quality
    out_dir = Path(os.environ.get("WD_REMOTE_OUT", "."))
    res = out_dir / "results.jsonl"
    for tk in a.takes.split(","):
        slot, stem, roi, (lo, hi), pk = TAKES[tk]
        for var in a.variants.split(","):
            args = list(REC) + list(VARIANTS[var])
            for k, v in roi.items():
                args += ["--set", f"{k}={v}"]
            args += ["--set", "roi_enabled=true"]
            if var.startswith("cal1"):
                args += ["--set", f"fg_plate={plate_rel('land')}"]
            elif "fg" in var or var.startswith("v"):
                args += ["--set", f"fg_plate={plate_rel(pk)}"]
            for kv in filter(None, a.extra.split(",")):
                args += ["--set", kv]
            tl = Path(tempfile.gettempdir()) / f"vn_{tk}_{var}.json"
            t0 = time.time()
            cmd = [sys.executable, "tests/replay.py", "--project", P, "--slot", str(slot),
                   "--video", f"../projects/{P}/recordings/{stem}.avi", "--model", "yolo11x-pose",
                   "--imgsz", "1280", "--trt", "--quality", "--internal", "--timeline", str(tl)] + args
            pr = subprocess.run(cmd, capture_output=True, text=True)
            row = {"take": tk, "variant": var, "rc": pr.returncode, "wall_s": round(time.time() - t0)}
            if pr.returncode != 0:
                row["err"] = (pr.stderr or pr.stdout)[-600:]
            else:
                rows = sorted(json.load(open(tl)), key=lambda r: r["frame"])
                win = [dict(r, frame=r["frame"] - lo) for r in rows if lo <= r["frame"] <= hi]
                man = {"name": tk, "start": 0, "frames": 10 ** 9, "warmup": 15, "fps": 20.0, "expected_count": 1,
                       "reference": {"min_conf": 0.5, "tol_h": 0.75, "exclude_spots": []}}
                q = output_quality.compare_streams(win, man, fps=20.0).get("emitted", {})
                pick = lambda d, k: d.get(k) if isinstance(d, dict) else None
                cont, qual = q.get("continuity", {}), q.get("quality", {})
                row.update({k: pick(cont, k) for k in ("coverage", "gap_max_s", "gaps_ge_1s", "on_dancer")})
                row.update({k: pick(qual, k) for k in ("coasting_share", "jitter_rest_pct", "lag_ms", "id_switches",
                                                       "over_n_frames", "distinct_ids")})
                st = Counter()
                for r in win:
                    for e in (r.get("emitted") or {}).get("tracks") or []:
                        st[e.get("state")] += 1
                row["states"] = dict(st)
                # frames with more points than the one dancer: which states the points were in
                gs = Counter()
                for r in win:
                    em = (r.get("emitted") or {}).get("tracks") or []
                    if len(em) > 1:
                        gs["+".join(sorted(e.get("state") or "?" for e in em))] += 1
                row["ghost_states"] = dict(gs)
                row["holes"] = holes(win)
                fgl = [l for l in pr.stdout.splitlines() if "[Foreground]" in l][:3]
                row["fg_log"] = fgl
                row["height_log"] = [l for l in pr.stdout.splitlines() if "[HeightGuard]" in l][:3]
            with open(res, "a") as f:
                f.write(json.dumps(row) + "\n")
            print(json.dumps({k: row.get(k) for k in ("take", "variant", "rc", "coverage", "gap_max_s", "gaps_ge_1s",
                                                       "coasting_share", "on_dancer", "over_n_frames", "lag_ms",
                                                       "ghost_states", "wall_s")}), flush=True)
            try:
                tl.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
