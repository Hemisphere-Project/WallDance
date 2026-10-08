#!/usr/bin/env python3
"""A/B the IDS camera stalls against the sensor crop, without the app.

The 2026-10-06/07 laptop logs: 91 stalls (1651-1715 ms, every buffer queued) in
~74 min of live acquisition at the landscape crops (1776x1300/1304, 1824x1264),
none in ~28 min at 1488x1528 -- same exposure (10-25 ms), gain and processing
load. This alternates crops (ABAB...) on the bare camera and counts stalls with
the camera's own frame id across each one (ids_camera._track_frame):
"camera_silent" (id +1) vs "lost_in_transfer" (id +N), plus the stream counters.

Run it on the laptop with the app CLOSED (the camera is exclusive), e.g.::

    wdremote --slot dev py extra/ids_stall_ab.py -- --minutes 5 --reps 2
    wdremote --slot dev py extra/ids_stall_ab.py -- --geom 0.974 --geom 1.37 \\
        --geom 1.37@1600000 --gpu --minutes 5

``--geom RATIO[@PIXELS]`` = the app's crop model (W/H ratio, pixel budget; default
budget = the app's IDS_CROP_PIXELS). ``--gpu`` reads frames through read_gpu() (the
app's pinned upload path) instead of read(). Results: stdout + stall_ab.json in
$WD_REMOTE_OUT (or --out).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "application" / "src"))


def parse_geom(text: str, default_pixels: int):
    """"1.37" -> (1.37, default_pixels); "1.37@1600000" -> (1.37, 1600000)."""
    ratio, _, pixels = text.partition("@")
    return float(ratio), int(pixels) if pixels else int(default_pixels)


def summarize(runs):
    """Per geometry: minutes, frames, stalls, stalls/min, kinds, gap range."""
    out = {}
    for r in runs:
        g = out.setdefault(r["geom"], {"size": r["size"], "minutes": 0.0, "frames": 0,
                                       "stalls": 0, "kinds": {}, "gaps_ms": []})
        g["minutes"] += r["seconds"] / 60.0
        g["frames"] += r["frames"]
        g["stalls"] += len(r["stalls"])
        for s in r["stalls"]:
            g["kinds"][s["kind"]] = g["kinds"].get(s["kind"], 0) + 1
            g["gaps_ms"].append(s["gap_ms"])
    for g in out.values():
        g["per_min"] = round(g["stalls"] / g["minutes"], 2) if g["minutes"] else 0.0
        g["minutes"] = round(g["minutes"], 2)
        gaps = sorted(g.pop("gaps_ms"))
        g["gap_ms_min_max"] = [gaps[0], gaps[-1]] if gaps else None
    return out


def run_one(ratio, pixels, args):
    from camera.ids_camera import IDSCamera, IDSCameraSettings
    from core.config import IDS_USER_SET
    st = IDSCameraSettings(crop_pixels=pixels, crop_ratio=ratio, target_fps=args.fps,
                           exposure_auto=False, exposure_us=args.exposure_us,
                           gain_auto=False, gain_db=args.gain_db, newest_only=True,
                           prefer_high_bit_depth=False, user_set=IDS_USER_SET)
    cam = IDSCamera(st)
    stalls = []
    track = cam._track_frame

    def tracked(*a, **k):
        rec = track(*a, **k)
        if rec is not None:
            stalls.append(rec)
        return rec

    cam._track_frame = tracked              # the acquisition thread looks it up per frame
    if not cam.open(None) or not cam.start_acquisition():
        cam.close()
        raise SystemExit("could not open the IDS camera (is the app running?)")
    size = f"{cam.state.width}x{cam.state.height}"
    print(f"[ab] {ratio}@{pixels} -> {size}, {args.minutes} min, "
          f"exp {args.exposure_us:.0f} us, gain {args.gain_db} dB, {'read_gpu' if args.gpu else 'read'}")
    before = cam.get_stream_diagnostics()
    t0 = time.perf_counter()
    f0 = cam.state.frame_count
    read = cam.read_gpu if args.gpu else cam.read
    while time.perf_counter() - t0 < args.minutes * 60.0:
        read()
        time.sleep(0.005)
    seconds = time.perf_counter() - t0
    frames = cam.state.frame_count - f0
    after = cam.get_stream_diagnostics()
    cam.stop_acquisition()
    cam.close()
    counters = {k: v - before["counters"].get(k, 0) for k, v in after["counters"].items()
                if v != before["counters"].get(k, 0)}
    rec = {"geom": f"{ratio}@{pixels}", "size": size, "seconds": round(seconds, 1),
           "frames": frames, "fps": round(frames / seconds, 2) if seconds else 0.0,
           "stalls": stalls, "stream_counters_delta": counters,
           "incomplete": after["incomplete"] - before["incomplete"]}
    print(f"[ab]   {frames} frames ({rec['fps']} fps), {len(stalls)} stall(s) "
          f"{[(s['gap_ms'], s['kind']) for s in stalls]} counters {counters}")
    return rec


def main(argv=None):
    from core.config import IDS_CROP_PIXELS
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--geom", action="append",
                    help="RATIO[@PIXELS], repeatable (default: 0.974 and 1.37, the show crops)")
    ap.add_argument("--minutes", type=float, default=5.0, help="per run")
    ap.add_argument("--reps", type=int, default=2, help="ABAB... rounds")
    ap.add_argument("--fps", type=float, default=20.0)
    ap.add_argument("--exposure-us", type=float, default=25000.0)
    ap.add_argument("--gain-db", type=float, default=30.0)
    ap.add_argument("--gpu", action="store_true", help="read through read_gpu() (pinned upload)")
    ap.add_argument("--out", default=os.environ.get("WD_REMOTE_OUT", "."))
    args = ap.parse_args(argv)
    geoms = [parse_geom(g, IDS_CROP_PIXELS) for g in (args.geom or ["0.974", "1.37"])]
    runs = []
    for _ in range(max(1, args.reps)):
        for ratio, pixels in geoms:
            runs.append(run_one(ratio, pixels, args))
    summary = summarize(runs)
    print(json.dumps(summary, indent=1))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stall_ab.json").write_text(json.dumps({"args": vars(args), "summary": summary,
                                                   "runs": runs}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
