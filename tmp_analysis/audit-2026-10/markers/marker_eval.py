"""Marker-eval harness prototype (scratch; NOT in the repo).

Extends tmp_analysis/marker_spike.py from a per-frame blob COUNT into a
per-blob FEATURE + TEMPORAL analysis that runs directly on WallDance slot
recordings:

  * verifies the recording is mono-replicated (B==G==R) and lossless-looking
  * per frame: brightness tail (max, p99.9, p99.99, #255 px)
  * per threshold T: connected components (8-conn) -> blob features
      area, bbox w/h, intensity-weighted centroid, peak, n_sat (==255),
      fill (area / bbox area), elongation (sqrt(l1/l2) of 2nd moments)
  * candidate filter (area range + peak floor)
  * temporal linking (greedy NN within --link-px) -> chains; a chain that
    lives >= --static-min-frames and never moves > --static-px is a FIXED
    glint (exclusion-mask fodder); everything else is a MOVING candidate
    (the dangerous class: something bright that moves like a dancer would)
  * per-frame JSONL of blobs at the primary threshold (for later fusion work)
  * contact sheet of the brightest candidates (crops, brightened)

Usage (from /data/WallDance/application):
  uv run --no-sync python <scratch>/marker_eval.py --video <avi> \
      --thresholds 160,200,230,245,254 --primary 230 --frames 2000 --stride 1 \
      --out <scratch>/out/<name>
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np


def blob_features(gray, labels, stats, cents, i):
    x, y, w, h, area = (int(v) for v in stats[i])
    sub = gray[y:y + h, x:x + w]
    m = labels[y:y + h, x:x + w] == i
    vals = sub[m].astype(np.float64)
    peak = int(vals.max())
    n_sat = int((vals >= 255).sum())
    ys, xs = np.nonzero(m)
    wts = vals
    cx = float((xs * wts).sum() / wts.sum()) + x
    cy = float((ys * wts).sum() / wts.sum()) + y
    if area >= 3:
        cov = np.cov(np.vstack([xs, ys]).astype(np.float64))
        ev = np.linalg.eigvalsh(cov)
        elong = float(np.sqrt(max(ev[1], 1e-6) / max(ev[0], 1e-6)))
    else:
        elong = 1.0
    return {
        "x": round(cx, 2), "y": round(cy, 2), "area": area, "w": w, "h": h,
        "peak": peak, "n_sat": n_sat, "fill": round(area / float(w * h), 3),
        "elong": round(elong, 2), "mean": round(float(vals.mean()), 1),
    }


def detect(gray, T, amin, amax, peak_min):
    _, mask = cv2.threshold(gray, T - 1, 255, cv2.THRESH_BINARY)  # >= T
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < amin or a > amax:
            continue
        f = blob_features(gray, labels, stats, cents, i)
        if f["peak"] < peak_min:
            continue
        out.append(f)
    return out


class Linker:
    """Greedy NN chain linker -> fixed vs moving candidate chains."""

    def __init__(self, link_px, max_gap=2):
        self.link_px = link_px
        self.max_gap = max_gap
        self.active = []   # dicts: last_xy, last_f, first_f, pts, n
        self.done = []

    def step(self, fidx, blobs):
        used = set()
        for ch in self.active:
            best, bd = None, self.link_px
            for j, b in enumerate(blobs):
                if j in used:
                    continue
                d = np.hypot(b["x"] - ch["last_xy"][0], b["y"] - ch["last_xy"][1])
                if d < bd:
                    best, bd = j, d
            if best is not None:
                used.add(best)
                b = blobs[best]
                ch["last_xy"] = (b["x"], b["y"])
                ch["last_f"] = fidx
                ch["pts"].append((b["x"], b["y"]))
                ch["n"] += 1
        still = []
        for ch in self.active:
            if fidx - ch["last_f"] > self.max_gap:
                self.done.append(ch)
            else:
                still.append(ch)
        self.active = still
        for j, b in enumerate(blobs):
            if j not in used:
                self.active.append({"last_xy": (b["x"], b["y"]), "last_f": fidx,
                                    "first_f": fidx, "pts": [(b["x"], b["y"])], "n": 1})

    def finish(self):
        self.done.extend(self.active)
        self.active = []
        return self.done


def classify_chains(chains, static_px, static_min_frames):
    fixed, moving, short = [], [], []
    for ch in chains:
        pts = np.asarray(ch["pts"])
        span = float(np.linalg.norm(pts.max(0) - pts.min(0))) if len(pts) > 1 else 0.0
        ch["span"] = span
        ch["life"] = ch["last_f"] - ch["first_f"] + 1
        if ch["n"] >= static_min_frames and span <= static_px:
            fixed.append(ch)
        elif ch["n"] >= 3:
            moving.append(ch)
        else:
            short.append(ch)
    return fixed, moving, short


def bench(gray, T, reps=50):
    res = {}
    t0 = time.perf_counter()
    for _ in range(reps):
        _, m = cv2.threshold(gray, T - 1, 255, cv2.THRESH_BINARY)
        cv2.connectedComponentsWithStats(m, connectivity=8)
    res["cpu_full_thresh_cc_ms"] = (time.perf_counter() - t0) / reps * 1000
    t0 = time.perf_counter()
    for _ in range(reps):
        _, m = cv2.threshold(gray, T - 1, 255, cv2.THRESH_BINARY)
        nz = cv2.findNonZero(m)
    res["cpu_full_thresh_findnonzero_ms"] = (time.perf_counter() - t0) / reps * 1000
    # 2x2 max-pool then CC (keeps saturated points; halves resolution)
    t0 = time.perf_counter()
    for _ in range(reps):
        small = cv2.dilate(gray, np.ones((2, 2), np.uint8))[::2, ::2]
        _, m = cv2.threshold(small, T - 1, 255, cv2.THRESH_BINARY)
        cv2.connectedComponentsWithStats(m, connectivity=8)
    res["cpu_maxpool2_cc_ms"] = (time.perf_counter() - t0) / reps * 1000
    try:
        import torch
        if torch.cuda.is_available():
            g = torch.from_numpy(gray).cuda()
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            for _ in range(reps):
                idx = (g >= T).nonzero()
                idx_cpu = idx.cpu()
            torch.cuda.synchronize()
            res["gpu_torch_thresh_nonzero_dl_ms"] = (time.perf_counter() - t0) / reps * 1000
    except Exception as e:  # pragma: no cover
        res["gpu_err"] = str(e)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--thresholds", default="160,200,230,245,254")
    ap.add_argument("--primary", type=int, default=230)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--frames", type=int, default=1000)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--min-area", type=int, default=2)
    ap.add_argument("--max-area", type=int, default=5000)
    ap.add_argument("--peak-min", type=int, default=0)
    ap.add_argument("--link-px", type=float, default=12.0)
    ap.add_argument("--static-px", type=float, default=4.0)
    ap.add_argument("--static-min-frames", type=int, default=10)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheet", type=int, default=24, help="# crops in contact sheet")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    Ts = [int(t) for t in args.thresholds.split(",")]
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {args.video}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if args.start:
        cap.set(cv2.CAP_PROP_POS_FRAMES, args.start)

    per_T = {T: {"counts": [], "areas": [], "peaks": [], "elong": [], "fill": []} for T in Ts}
    tail = {"max": [], "p999": [], "p9999": [], "n255": [], "mean": []}
    linkers = {T: Linker(args.link_px) for T in Ts}
    mono_check = None
    jl = open(out / "blobs.jsonl", "w")
    sheet_pool = []   # (peak, area, fidx, crop)
    fidx = processed = 0
    bench_res = None
    while processed < args.frames:
        ok, frame = cap.read()
        if not ok:
            break
        if fidx % args.stride:
            fidx += 1
            continue
        if frame.ndim == 3:
            if mono_check is None:
                mono_check = bool(np.array_equal(frame[:, :, 0], frame[:, :, 1])
                                  and np.array_equal(frame[:, :, 0], frame[:, :, 2]))
            gray = (np.ascontiguousarray(frame[:, :, 0]) if mono_check
                    else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        else:
            gray = frame
        if bench_res is None:
            bench_res = bench(gray, args.primary)
        flat = gray.ravel()
        tail["max"].append(int(flat.max()))
        tail["mean"].append(float(flat.mean()))
        tail["n255"].append(int((flat >= 255).sum()))
        q = np.partition(flat, [int(len(flat) * 0.999), int(len(flat) * 0.9999)])
        tail["p999"].append(int(q[int(len(flat) * 0.999)]))
        tail["p9999"].append(int(q[int(len(flat) * 0.9999)]))
        for T in Ts:
            if tail["max"][-1] < T:
                blobs = []
            else:
                blobs = detect(gray, T, args.min_area, args.max_area, args.peak_min)
            d = per_T[T]
            d["counts"].append(len(blobs))
            for b in blobs:
                d["areas"].append(b["area"]); d["peaks"].append(b["peak"])
                d["elong"].append(b["elong"]); d["fill"].append(b["fill"])
            linkers[T].step(processed, blobs)
            if T == args.primary:
                jl.write(json.dumps({"f": args.start + fidx, "blobs": blobs}) + "\n")
                for b in blobs:
                    x0 = int(max(0, b["x"] - 24)); y0 = int(max(0, b["y"] - 24))
                    crop = gray[y0:y0 + 48, x0:x0 + 48].copy()
                    sheet_pool.append((b["peak"], b["area"], args.start + fidx, crop))
                    if len(sheet_pool) > 400:
                        sheet_pool.sort(key=lambda r: (-r[0], -r[1]))
                        sheet_pool = sheet_pool[:200]
        processed += 1
        fidx += 1
    cap.release()
    jl.close()

    summ = {
        "video": args.video, "total_frames": total, "start": args.start,
        "sampled": processed, "stride": args.stride,
        "mono_replicated": mono_check, "shape": list(gray.shape),
        "bench_ms": {k: round(v, 3) if isinstance(v, float) else v for k, v in (bench_res or {}).items()},
        "tail": {
            "max_median": int(np.median(tail["max"])), "max_max": int(np.max(tail["max"])),
            "p999_median": int(np.median(tail["p999"])), "p9999_median": int(np.median(tail["p9999"])),
            "p9999_max": int(np.max(tail["p9999"])),
            "frames_with_255_pct": round(100 * float(np.mean(np.array(tail["n255"]) > 0)), 2),
            "mean_luma_median": round(float(np.median(tail["mean"])), 2),
        },
        "per_T": {},
    }
    for T in Ts:
        d = per_T[T]
        c = np.array(d["counts"])
        chains = linkers[T].finish()
        fixed, moving, short = classify_chains(chains, args.static_px, args.static_min_frames)
        moving_frames = set()
        for ch in moving:
            moving_frames.update(range(ch["first_f"], ch["last_f"] + 1))
        summ["per_T"][T] = {
            "frames_ge1_pct": round(100 * float(np.mean(c >= 1)), 2),
            "frames_ge2_pct": round(100 * float(np.mean(c >= 2)), 2),
            "mean_per_frame": round(float(c.mean()), 3),
            "max_per_frame": int(c.max()) if len(c) else 0,
            "area_p50_p95_max": ([int(np.percentile(d["areas"], 50)), int(np.percentile(d["areas"], 95)),
                                  int(np.max(d["areas"]))] if d["areas"] else None),
            "peak_p50": int(np.percentile(d["peaks"], 50)) if d["peaks"] else None,
            "chains_fixed": len(fixed), "chains_moving": len(moving), "chains_short_lt3": len(short),
            "frames_touched_by_moving_chain_pct": round(100 * len(moving_frames) / max(1, processed), 2),
            "fixed_positions": [[round(float(np.mean([p[0] for p in ch["pts"]])), 0),
                                 round(float(np.mean([p[1] for p in ch["pts"]])), 0), ch["n"]]
                                for ch in sorted(fixed, key=lambda c: -c["n"])[:8]],
            "moving_examples": [[round(ch["pts"][0][0]), round(ch["pts"][0][1]), ch["n"], round(ch["span"], 1)]
                                for ch in sorted(moving, key=lambda c: -c["n"])[:8]],
        }
    with open(out / "summary.json", "w") as f:
        json.dump(summ, f, indent=1)

    # contact sheet (brightened x4 then clipped) of the brightest candidates
    if sheet_pool:
        sheet_pool.sort(key=lambda r: (-r[0], -r[1]))
        tiles = []
        for peak, area, fi, crop in sheet_pool[:args.sheet]:
            c = np.zeros((48, 48), np.uint8)
            c[:crop.shape[0], :crop.shape[1]] = crop
            c = cv2.resize(c, (96, 96), interpolation=cv2.INTER_NEAREST)
            c = cv2.cvtColor(c, cv2.COLOR_GRAY2BGR)
            cv2.putText(c, f"{fi}", (2, 10), cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 255, 0), 1)
            cv2.putText(c, f"p{peak} a{area}", (2, 92), cv2.FONT_HERSHEY_PLAIN, 0.7, (0, 255, 255), 1)
            tiles.append(c)
        while len(tiles) % 8:
            tiles.append(np.zeros_like(tiles[0]))
        rows = [np.hstack(tiles[i:i + 8]) for i in range(0, len(tiles), 8)]
        cv2.imwrite(str(out / "sheet.png"), np.vstack(rows))
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
