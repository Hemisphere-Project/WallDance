"""Read-only stats on the real s2c timeline (dark, operator still at the far wall)."""
import json, sys, math
import numpy as np
rows = json.load(open(sys.argv[1]))
fps = 20.0
A, B = 1200, 3600          # 60..180 s: still at (852, 677), box h ~126 px
seg = rows[A:B]
# 1. YOLO presence + gap runs
has = [1 if r.get("ref") else 0 for r in seg]
runs, cur = [], 0
for h in has:
    if h == 0:
        cur += 1
    elif cur:
        runs.append(cur); cur = 0
if cur: runs.append(cur)
print("still window frames", len(seg), "YOLO det frames", sum(has), "(%.1f %%)" % (100 * sum(has) / len(seg)))
print("YOLO gap runs: n=%d max=%d frames; histogram:" % (len(runs), max(runs) if runs else 0),
      {k: runs.count(k) for k in sorted(set(runs))})
confs = [d["conf"] for r in seg for d in r.get("ref") or [] if d.get("conf") is not None]
print("box conf: p05 %.2f p25 %.2f med %.2f p75 %.2f min %.2f; share < 0.25: %.1f %%, < 0.20: %.1f %%" % (
    np.percentile(confs, 5), np.percentile(confs, 25), np.median(confs), np.percentile(confs, 75), min(confs),
    100 * np.mean(np.array(confs) < 0.25), 100 * np.mean(np.array(confs) < 0.20)))
# 2. slot states
from collections import Counter
st = Counter()
for r in seg:
    em = (r.get("emitted") or {}).get("tracks") or []
    st[em[0]["state"] if em else "none"] += 1
print("slot states:", dict(st))
# 3. the static test: p90 distance from the median over 5 s windows of the slot's measured (unsmoothed) position
pts = []
for r in seg:
    em = (r.get("emitted") or {}).get("tracks") or []
    if em and em[0]["state"] != "coasting":
        pts.append((r["frame"] / fps, em[0]["raw"][0], em[0]["raw"][1], em[0]["bbox"][3]))
pts = np.array(pts)
res = []
for i in range(0, len(pts) - 100, 10):
    w = pts[(pts[:, 0] >= pts[i, 0]) & (pts[:, 0] < pts[i, 0] + 5.0)]
    if len(w) < 60:
        continue
    med = np.median(w[:, 1:3], axis=0)
    d = np.hypot(w[:, 1] - med[0], w[:, 2] - med[1])
    res.append(np.percentile(d, 90) / np.median(w[:, 3]))
res = np.array(res)
print("5 s windows: n=%d  p90 spread / h: min %.4f  p10 %.4f  median %.4f  p90 %.4f  max %.4f ; share below static_spread_h 0.06: %.1f %%" % (
    len(res), res.min(), np.percentile(res, 10), np.median(res), np.percentile(res, 90), res.max(), 100 * np.mean(res < 0.06)))
# same on the tracker's reported EMA centroid (candidate x, y)
pts = []
for r in seg:
    for t in r.get("tracks") or []:
        pts.append((r["frame"] / fps, t["centroid"][0], t["centroid"][1], t["bbox"][3]))
pts = np.array(pts)
res = []
for i in range(0, len(pts) - 100, 10):
    w = pts[(pts[:, 0] >= pts[i, 0]) & (pts[:, 0] < pts[i, 0] + 5.0)]
    if len(w) < 60:
        continue
    med = np.median(w[:, 1:3], axis=0)
    d = np.hypot(w[:, 1] - med[0], w[:, 2] - med[1])
    res.append(np.percentile(d, 90) / np.median(w[:, 3]))
res = np.array(res)
print("tracker EMA centroid, 5 s windows: p90 spread / h: min %.4f median %.4f max %.4f ; share < 0.06: %.1f %%" % (
    res.min(), np.median(res), res.max(), 100 * np.mean(res < 0.06)))
# 4. frames where YOLO has a detection but nothing is emitted (whole take), and holes
n_det_no_point, holes, cur = 0, [], 0
for r in rows:
    em = (r.get("emitted") or {}).get("tracks") or []
    refs = [d for d in r.get("ref") or []]
    if refs and not em:
        n_det_no_point += 1
        cur += 1
    else:
        if cur: holes.append(cur)
        cur = 0
print("whole take: frames with a YOLO detection and NO emitted point: %d ; runs >= 20 frames: %s" % (
    n_det_no_point, [h for h in holes if h >= 20]))
# 5. fg blob area under the slot, per 10 s
print("fg blob area near the slot (median per 10 s):")
for a in range(A, B, 200):
    v = []
    for r in rows[a:a + 200]:
        fg = r.get("fg") or {}
        em = (r.get("emitted") or {}).get("tracks") or []
        if em and fg.get("b"):
            cx, cy = em[0]["centroid"]
            near = [b[2] for b in fg["b"] if math.hypot(b[0] - cx, b[1] - cy) < 100]
            v.append(max(near) if near else 0)
    print("  %5.0f s: %s" % (a / fps, int(np.median(v)) if v else None), end="")
print()
