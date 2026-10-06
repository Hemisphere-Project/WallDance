#!/usr/bin/env python3
"""Ghost spots from a replay timeline -> exclusion proposals (PLAN_25M; CONT-9 done offline).

Run a low-confidence replay of the EMPTY-wall take with reference detections recorded, then cluster them:
  laptop:  python extra/wdremote.py --slot dev replay --timeline -- --project P --slot 1 --model yolo11x-pose \
               --imgsz 1280 --trt --quality --set confidence=0.10
  here:    python3 tmp_analysis/plan25m/ghost_spots.py tmp_analysis/remote-runs/<stamp>/timeline.json --empty
On a take WITH dancers (no --empty), only clusters that recur AND never move are kept (fixed figures).
Prints the clusters (position, size, share of frames, confidence) and the ExcludeAt commands to paint them.
Paint only where dancers never go: an excluded cell hides a dancer passing through it.
"""
import argparse, json, math, statistics as st, sys

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline"); ap.add_argument("--empty", action="store_true", help="empty-stage take: every recurring det is a ghost")
    ap.add_argument("--min-share", type=float, default=0.03, help="share of frames a spot must be detected in")
    ap.add_argument("--max-spread-h", type=float, default=0.15, help="(with dancers) p90 spread of a fixed figure, x h")
    ap.add_argument("--min-conf", type=float, default=0.0)
    a = ap.parse_args()
    data = json.load(open(a.timeline))
    rows = data["frames"] if isinstance(data, dict) and "frames" in data else data
    n = len(rows)
    dets = [(r.get("abs_frame", r.get("frame")), d["c"][0], d["c"][1], d["h"], d.get("conf") or 0.0)
            for r in rows for d in (r.get("ref") or []) if (d.get("conf") or 0.0) >= a.min_conf]
    clusters = []      # [x, y, h, [members]]
    for f, x, y, h, c in sorted(dets, key=lambda d: -d[4]):
        best = None
        for cl in clusters:
            if math.hypot(cl[0] - x, cl[1] - y) < 0.5 * max(cl[2], h):
                best = cl; break
        if best is None:
            clusters.append([x, y, h, [(f, x, y, h, c)]])
        else:
            best[3].append((f, x, y, h, c))
            k = len(best[3]); best[0] += (x - best[0]) / k; best[1] += (y - best[1]) / k; best[2] += (h - best[2]) / k
    out = []
    for x, y, h, mem in clusters:
        frames = len({m[0] for m in mem}); share = frames / max(1, n)
        if share < a.min_share:
            continue
        mx, my = st.median(m[1] for m in mem), st.median(m[2] for m in mem)
        d = sorted(math.hypot(m[1] - mx, m[2] - my) for m in mem); p90 = d[int(0.9 * (len(d) - 1))]
        if not a.empty and p90 > a.max_spread_h * h:
            continue
        out.append((share, mx, my, h, p90 / max(1.0, h), st.median(m[4] for m in mem)))
    out.sort(reverse=True)
    print(f"{a.timeline}: {n} frames, {len(dets)} detections, {len(out)} ghost spot(s) ({'empty take' if a.empty else 'fixed figures only'})")
    print(f"{'x':>7}{'y':>7}{'h':>6}{'share':>7}{'spread/h':>9}{'conf':>6}")
    for share, x, y, h, sp, c in out:
        print(f"{x:7.0f}{y:7.0f}{h:6.0f}{share:7.2f}{sp:9.3f}{c:6.2f}")
    if out:
        print("# exclusion proposals (frame px; check on the snapshot that no dancer passes there):")
        for share, x, y, h, sp, c in out:
            print(f"python3 extra/wdremote.py cmd ExcludeAt x={x:.0f} y={y:.0f}")

main()
