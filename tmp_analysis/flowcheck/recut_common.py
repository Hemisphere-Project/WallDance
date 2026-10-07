#!/usr/bin/env python3
"""Cut a project's takes to the sensor area they ALL share (lossless FFV1, frame-exact), so one empty-wall
snapshot / ROI applies to every take.  2026-10-07: the night project's takes alternate two IDS crops (a bug,
fixed in 9659349): landscape 1776x1300 at sensor (456,116) and portrait 1488x1528 at (600,4); they share
1488x1300 at (600,116).  Each take's sensor rect comes from its .meta (camera.nodes OffsetX/OffsetY/Width/
Height); a take without a camera block (an old build) takes the rect of the other takes of its frame size
(verify the alignment separately, e.g. phase correlation on a static region).

  python recut_common.py --src /data/WallDance/projects/mur25m-ceinture-0610 \\
      --dst /data/WallDance/projects/mur25m-night-common [--jobs 3] [--dry-run]

Writes <dst>/recordings/<same names>.avi + .meta (size, the cut, provenance) + .camlog copies, and checks
the frame counts.  Also prints the crop (x0, y0) per take, for mapping ROIs into the common frame.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def sensor_rect(meta: dict):
    n = ((meta.get("camera") or {}).get("nodes")) or {}
    try:
        return (int(n["OffsetX"]), int(n["OffsetY"]), int(n["Width"]), int(n["Height"]))
    except (KeyError, TypeError, ValueError):
        return None


def probe(path: Path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=width,height,nb_read_packets,pix_fmt", "-of", "json",
                          str(path)], capture_output=True, text=True, check=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"]), int(s["nb_read_packets"]), s["pix_fmt"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src, dst = Path(a.src) / "recordings", Path(a.dst) / "recordings"
    takes = sorted(src.glob("*.avi"))
    metas = {t: json.loads(Path(str(t) + ".meta").read_text()) if Path(str(t) + ".meta").exists() else {}
             for t in takes}
    rects = {t: sensor_rect(m) for t, m in metas.items()}
    sizes = {t: probe(t)[:2] for t in takes}
    by_size = {}
    for t, r in rects.items():
        if r is not None:
            by_size.setdefault(sizes[t], set()).add(r)
    for t in takes:                                     # old build: no camera block
        if rects[t] is None:
            cands = by_size.get(sizes[t], set())
            if len(cands) != 1:
                raise SystemExit(f"{t.name}: no camera block and {len(cands)} rects of its size")
            rects[t] = next(iter(cands))
            print(f"{t.name}: no camera block -> assumed sensor rect {rects[t]} (frame size {sizes[t]})")
    x0 = max(r[0] for r in rects.values()); y0 = max(r[1] for r in rects.values())
    x1 = min(r[0] + r[2] for r in rects.values()); y1 = min(r[1] + r[3] for r in rects.values())
    W, H = x1 - x0, y1 - y0
    print(f"common sensor area: {W}x{H} at ({x0},{y0})")
    plan = []
    for t in takes:
        rx, ry, _rw, _rh = rects[t]
        cx, cy = x0 - rx, y0 - ry
        plan.append((t, cx, cy))
        print(f"  {t.name}: crop {W}x{H} at frame ({cx},{cy})")
    if a.dry_run:
        return
    dst.mkdir(parents=True, exist_ok=True)

    def one(item):
        t, cx, cy = item
        out = dst / t.name
        w, h, n_in, pix = probe(t)
        if not out.exists() or probe(out)[2] != n_in:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(t), "-vf", f"crop={W}:{H}:{cx}:{cy}",
                            "-fps_mode", "passthrough", "-c:v", "ffv1", "-level", "3", "-pix_fmt", pix,
                            "-an", str(out)], check=True)
        w2, h2, n_out, _ = probe(out)
        meta = dict(metas[t])
        meta.update({"size": [W, H], "file": t.name,
                     "recut": {"from": str(t), "frame_crop": [cx, cy, W, H], "sensor_area": [x0, y0, W, H],
                               "source_size": [w, h], "frames_in": n_in, "frames_out": n_out,
                               "tool": "tmp_analysis/flowcheck/recut_common.py",
                               "why": "night takes alternate two IDS crops; one common frame so one empty-wall "
                                      "snapshot / ROI applies to every take"}})
        Path(str(out) + ".meta").write_text(json.dumps(meta, indent=1))
        cam = Path(str(t) + ".camlog.jsonl")
        if cam.exists():
            shutil.copy2(cam, str(out) + ".camlog.jsonl")
        return t.name, n_in, n_out, (w2, h2)

    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for name, n_in, n_out, size in ex.map(one, plan):
            print(f"{name}: {n_in} -> {n_out} frames, {size[0]}x{size[1]} {'OK' if n_in == n_out else 'MISMATCH'}",
                  flush=True)


if __name__ == "__main__":
    main()
