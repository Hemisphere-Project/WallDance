#!/usr/bin/env python3
"""Build a take's presence truth (Plan Cordée rev 2, M1): who is on the wall, where, on every frame.

Two steps, both independent of the detector under test:

``blobs``   foreground groups per frame from a half-size portrait proxy of the take.
            bg = per-pixel temporal median of ~120 frames spread over the take, recomputed once with the first
            pass's foreground masked out, and patched where a dancer hung still long enough to be the median;
            fg = |frame - bg| > T inside the wall polygon, opened 3x3; groups = fg components joined when closer
            than about half a body (a dancer with her belt and shadow, or two dancers in contact).
            -> ``<stem>.blobs.npz`` (per frame [cx, cy, x0, y0, x1, y1, area, mean signed diff, max level],
            full-size portrait px) and ``<stem>.bg.png`` (half size).

``build``   WHO is on the wall is declared by hand in ``<stem>.plan.json``; WHERE comes from the groups:
              {"n0": 2, "seeds": [[x, y], ...],
               "events": [{"f": 2145, "type": "in", "near": [517, 84]}, {"f": 4968, "type": "out", "near": [505, 1692]}],
               "exclude_boxes": [[x0, y0, x1, y1] or [x0, y0, x1, y1, f0, f1]], "h_take": 150, "note": "..."}
            Tracks take groups one to one (Hungarian, distance gate growing with the time without a group); a
            track left without one shares the nearest group within 180 px (contact) or coasts; a sizeable group
            nobody took goes to the nearest coasting track (speed-limited) or splits a sharing pair. No track is
            born or dropped outside the declared events. -> ``<stem>.npz`` (see ``truth_score.Truth``).

``import``  convert a counter-analysis ``<stem>.truth.json`` (and its bg.png and the take's .meta) to the
            compact ``<stem>.npz`` the repo keeps.

  truth_build.py blobs <proxy.mp4> <outdir> [--thr 22]
  truth_build.py build <stem> --blobs DIR --plans DIR --out DIR [--meta take.avi.meta] [--replay stem.ship.jsonl]
  truth_build.py import <truth.json> --bg bg.png --meta take.avi.meta --out DIR
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from truth_score import WALL, Truth, in_poly  # noqa: E402


# ── take metadata kept with the truth ───────────────────────────────────────

def take_meta(meta_path) -> dict:
    m = json.loads(Path(meta_path).read_text())
    return {"start": m["started_at"][11:19], "fps": m.get("actual_fps"),
            "exp_ms": round(m["camera"]["nodes"]["ExposureTime"] / 1000, 1),
            "gain_db": round(m["camera"].get("gain_db", 0), 1), "enhance": bool(m["config"].get("enhance_enabled")),
            "gamma": round(m["config"].get("gamma", 1), 2), "clahe": m["config"].get("clahe_clip")}


def bg_under(bg, box):
    """Mean level and clipped share (>= 250) of the half-size background under a full-size box."""
    x0, y0, x1, y1 = [int(v // 2) for v in box]
    patch = bg[max(0, y0):max(1, y1), max(0, x0):max(1, x1)]
    if not patch.size:
        return 0.0, 0.0
    return float(patch.mean()), float((patch >= 250).mean())


def _finish(stem, frames, size, h_take, dancers, boxes, bg, meta, plan) -> Truth:
    f, d, x, y, st, bx, bgv, cl = [], [], [], [], [], [], [], []
    for name in sorted(dancers):
        k = ord(name[0]) - 65
        for fr, p in dancers[name].items():
            b = [round(v) for v in boxes[name][fr]]
            f.append(int(fr)); d.append(k); x.append(p[0]); y.append(p[1]); st.append(int(p[2])); bx.append(b)
            m, c = bg_under(bg, b) if bg is not None else (0.0, 0.0)
            bgv.append(m); cl.append(c)
    return Truth(stem, frames, size, h_take, np.array(f), np.array(d), np.array(x, np.float64), np.array(y, np.float64),
                 np.array(st), np.array(bx).reshape(-1, 4), np.array(bgv), np.array(cl), meta, plan)


def import_json(truth_json, bg_png, meta_path, out_dir) -> Path:
    import cv2
    T = json.loads(Path(truth_json).read_text())
    bg = cv2.imread(str(bg_png), 0) if bg_png else None
    meta = take_meta(meta_path) if meta_path else {}
    t = _finish(T["stem"], T["frames"], T["size"], T["h_take"], T["dancers"], T["boxes"], bg, meta, T.get("plan") or {})
    out = Path(out_dir) / f"{T['stem']}.npz"
    t.save(out)
    return out


# ── blobs ────────────────────────────────────────────────────────────────────

def _read_all(path, step=1):
    import cv2
    cap = cv2.VideoCapture(str(path))
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        if i % step == 0:
            yield i, fr[..., 0]
        i += 1
    cap.release()


def fg_groups(g, bg, wall, thr, join):
    import cv2
    d = g.astype(np.float32) - bg
    m = ((np.abs(d) > thr) & wall).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    if not m.any():
        return [], m
    grp = cv2.dilate(m, join)
    n, lab, st, _ = cv2.connectedComponentsWithStats(grp)
    out = []
    for k in range(1, n):
        x, y, w, h = st[k, 0], st[k, 1], st[k, 2], st[k, 3]
        sel = (lab[y:y + h, x:x + w] == k) & (m[y:y + h, x:x + w] > 0)
        area = int(sel.sum())
        if area < 10:
            continue
        ys, xs = np.nonzero(sel)
        ys = ys + y
        xs = xs + x
        out.append([2.0 * xs.mean(), 2.0 * ys.mean(), 2.0 * xs.min(), 2.0 * ys.min(), 2.0 * xs.max() + 2, 2.0 * ys.max() + 2,
                    4.0 * area, float(d[ys, xs].mean()), float(g[ys, xs].max())])
    return out, m


def blobs(proxy, out_dir, stem=None, thr=22.0, samples=120):
    import cv2
    proxy = Path(proxy)
    stem = stem or proxy.name.split(".")[0]
    cap = cv2.VideoCapture(str(proxy))
    n = int(cap.get(7))
    w, h = int(cap.get(3)), int(cap.get(4))
    cap.release()
    size = (w * 2, h * 2)
    poly = np.array(WALL[size], np.float32) / 2.0
    wall = np.zeros((h, w), np.uint8)
    cv2.fillPoly(wall, [poly.astype(np.int32)], 1)
    wall = wall.astype(bool)
    step = max(1, n // samples)
    samp = np.stack([g for _, g in _read_all(proxy, step)])
    bg = np.median(samp, 0).astype(np.float32)
    join = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 41))     # half-size px: ~0.3 x 0.5 body
    masked = samp.astype(np.float32)
    for i in range(len(samp)):
        _, m = fg_groups(samp[i], bg, wall, thr, join)
        masked[i][cv2.dilate(m, np.ones((9, 9), np.uint8)) > 0] = np.nan
    with np.errstate(all="ignore"):
        bg2 = np.nanmedian(masked, 0)
    bg2 = np.where(np.isnan(bg2), bg, bg2).astype(np.float32)
    # a dancer who hangs at one place for most of the take IS the temporal median there: the wall is smooth, so
    # replace what stands out of a wide spatial median (inside the wall, away from its edges) by that median
    inner = cv2.erode(wall.astype(np.uint8), np.ones((81, 81), np.uint8)) > 0
    smooth = cv2.medianBlur(np.clip(bg2, 0, 255).astype(np.uint8), 101).astype(np.float32)
    anom = ((np.abs(bg2 - smooth) > 12) & inner).astype(np.uint8)
    anom = cv2.morphologyEx(anom, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))      # not the ropes
    anom = cv2.dilate(anom, np.ones((15, 15), np.uint8)) > 0
    bg2 = np.where(anom, smooth, bg2).astype(np.float32)
    static_share = float(np.isnan(masked).mean(0)[wall].max())
    del masked, samp
    frames = []
    for _, g in _read_all(proxy):
        gr, _ = fg_groups(g, bg2, wall, thr, join)
        frames.append(np.array(gr, np.float32).reshape(-1, 9))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out / f"{stem}.bg.png"), np.clip(bg2, 0, 255).astype(np.uint8))
    idx = np.cumsum([0] + [len(f) for f in frames])
    np.savez_compressed(out / f"{stem}.blobs.npz", groups=np.concatenate(frames) if frames else np.zeros((0, 9), np.float32),
                        idx=idx, size=np.array(size), thr=thr, static_share=static_share)
    return out / f"{stem}.blobs.npz"


# ── build ────────────────────────────────────────────────────────────────────

def replay_candidates(rows, poly, ora_min=0.10, low_min=0.05):
    """Per frame: detector boxes that support the truth's body height, [(cx, cy, h, w, conf, src)]."""
    out = []
    for r in rows:
        c = []
        for src, key, thr in (("o", "ora", ora_min), ("s", "low", low_min)):
            for b in r.get(key) or []:
                if b[4] < thr:
                    continue
                w, h = b[2] - b[0], b[3] - b[1]
                cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                if not (40 <= h <= 340 and w <= 300) or not in_poly(cx, cy, poly):
                    continue
                c.append([cx, cy, h, w, b[4], src])
        c.sort(key=lambda v: -v[4])
        keep = []
        for v in c:
            dup = False
            for k in keep:
                if math.hypot(v[0] - k[0], v[1] - k[1]) < 0.35 * max(v[2], k[2]):
                    dup = True
                    if v[5] not in k[5]:
                        k[5] += v[5]
                    break
            if not dup:
                keep.append(v)
        out.append(keep)
    return out


def build(stem, blobs_npz, plan, bg=None, meta=None, replay_rows=None):
    """The declared-presence tracker over the foreground groups. Returns (Truth, warnings, summary)."""
    from scipy.optimize import linear_sum_assignment
    z = np.load(blobs_npz)
    G, idx = z["groups"], z["idx"]
    size = tuple(int(v) for v in z["size"])
    n = len(idx) - 1
    a_min = float(plan.get("a_min", 40.0))
    a_start = float(plan.get("a_start", 120.0))
    share_dist = float(plan.get("share_dist", 180.0))
    excl = plan.get("exclude_boxes", [])
    events = sorted(plan.get("events", []), key=lambda e: e["f"])
    W = int(plan.get("window", 150))

    def groups_at(f):
        g = G[idx[f]:idx[f + 1]]
        return [r for r in g if r[6] >= a_min and not any(
            e[0] <= r[0] <= e[2] and e[1] <= r[1] <= e[3] and (len(e) < 6 or e[4] <= f <= e[5]) for e in excl)]

    tracks = []

    def new_track(f, x, y, box, st):
        t = {"f": [f], "p": [[float(x), float(y), st]], "b": [[float(v) for v in box]], "x": float(x), "y": float(y),
             "last_f": f, "alive": True, "area": []}
        tracks.append(t)
        return t

    g0 = sorted(groups_at(0), key=lambda r: -r[6])
    seeds = plan.get("seeds")
    if seeds:
        for sx, sy in seeds:
            near = min(g0, key=lambda r: math.hypot(r[0] - sx, r[1] - sy)) if g0 else None
            if near is not None and math.hypot(near[0] - sx, near[1] - sy) < 200:
                new_track(0, near[0], near[1], near[2:6], 0)
            else:
                new_track(0, sx, sy, [sx - 30, sy - 60, sx + 30, sy + 60], 2)
    else:
        for k in range(int(plan.get("n0", 0))):
            r = g0[k] if k < len(g0) else (g0[0] if g0 else None)
            if r is None:
                raise ValueError("n0 > 0 but no group at frame 0: give seeds")
            new_track(0, r[0], r[1], r[2:6], 0 if k < len(g0) else 1)
    pending_in = [dict(e) for e in events if e["type"] == "in"]
    outs = [e for e in events if e["type"] == "out"]
    warns = []

    for f in range(1, n):
        g = groups_at(f)
        act = [t for t in tracks if t["alive"]]
        for e in [e for e in outs if e["f"] == f]:
            if act:
                nx, ny = e.get("near", [None, None])
                t = min(act, key=lambda t: math.hypot(t["x"] - nx, t["y"] - ny)) if nx is not None else act[-1]
                t["alive"] = False
                act = [q for q in act if q is not t]
        assigned = {}
        if g and act:
            cost = np.full((len(act), len(g)), 1e6)
            for i, t in enumerate(act):
                gap = f - t["last_f"]
                gate = min(520.0, 70.0 + 22.0 * gap)
                for j, r in enumerate(g):
                    d = math.hypot(r[0] - t["x"], r[1] - t["y"])
                    if d <= gate:
                        cost[i, j] = d + 2.0 * gap - 0.002 * min(r[6], 3000)
            ri, ci = linear_sum_assignment(cost)
            for i, j in zip(ri, ci):
                if cost[i, j] < 1e5:
                    assigned[i] = j
        used = set(assigned.values())
        state = {}
        for i, t in enumerate(act):
            if i in assigned:
                state[i] = (assigned[i], 0)
            else:
                best = None
                for j, r in enumerate(g):
                    d = math.hypot(r[0] - t["x"], r[1] - t["y"])
                    if d <= share_dist and (best is None or d < best[0]):
                        best = (d, j)
                if best is not None:
                    state[i] = (best[1], 0 if best[1] not in used else 1)
                    used.add(best[1])
        for j, r in enumerate(g):
            if j in used or r[6] < a_start:
                continue
            cands = []
            for i, t in enumerate(act):
                d = math.hypot(r[0] - t["x"], r[1] - t["y"])
                if i not in state:
                    if d <= min(750.0, 80.0 + 25.0 * (f - t["last_f"])):
                        cands.append((d, i))
                elif state[i][1] == 1 and d <= 220.0 and r[6] >= max(a_start, 0.2 * g[state[i][0]][6]):
                    cands.append((d + 40.0, i))      # the pair splits: the free part must be a body, not a speck
            if cands:
                _, i = min(cands)
                state[i] = (j, 0)
                used.add(j)
        for i, t in enumerate(act):
            if i not in state:
                continue
            j, st = state[i]
            r = g[j]
            for ff in range(t["last_f"] + 1, f):
                t["f"].append(ff); t["p"].append([t["x"], t["y"], 2]); t["b"].append(t["b"][-1])
            t["x"], t["y"], t["last_f"] = float(r[0]), float(r[1]), f
            t["f"].append(f); t["p"].append([float(r[0]), float(r[1]), st]); t["b"].append([float(v) for v in r[2:6]])
            t["area"].append(float(r[6]))
        for e in list(pending_in):
            if f < e["f"] - W:
                continue
            nx, ny = e["near"]
            rad = float(e.get("radius", 260.0))
            free = [(math.hypot(r[0] - nx, r[1] - ny), j) for j, r in enumerate(g)
                    if j not in used and r[6] >= float(e.get("a_start", a_start)) and math.hypot(r[0] - nx, r[1] - ny) <= rad]
            if free:
                e["seen"] = e.get("seen", 0) + 1
                if e["seen"] >= int(e.get("persist", 6)):
                    _, j = min(free)
                    new_track(f, g[j][0], g[j][1], g[j][2:6], 0)
                    used.add(j)
                    pending_in.remove(e)
            else:
                e["seen"] = 0
                if f > e["f"] + W:
                    new_track(f, nx, ny, [nx - 30, ny - 60, nx + 30, ny + 60], 2)
                    warns.append(f"entry at {e['f']} not seen as a free group by {f}: seeded at {e['near']}")
                    pending_in.remove(e)
    for t in tracks:
        if t["alive"]:
            for ff in range(t["last_f"] + 1, n):
                t["f"].append(ff); t["p"].append([t["x"], t["y"], 2]); t["b"].append(t["b"][-1])

    cands = replay_candidates(replay_rows, WALL[size]) if replay_rows else [[] for _ in range(n)]
    for t in tracks:
        P = np.array([[p[0], p[1]] for p in t["p"]], float)
        st = np.array([p[2] for p in t["p"]], int)
        if len(st) > 2:
            good = np.nonzero(st != 2)[0]
            if len(good) >= 2:
                k = np.arange(len(st))
                P[:, 0] = np.interp(k, good, P[good, 0])
                P[:, 1] = np.interp(k, good, P[good, 1])
            d = np.hypot(np.diff(P[:, 0]), np.diff(P[:, 1]))
            for j in np.nonzero(d > 100.0)[0]:
                a0, a1 = max(0, j - 10), min(len(st), j + 12)
                st[a0:a1] = np.where(st[a0:a1] == 1, 1, 3)
            t["p"] = [[float(P[i, 0]), float(P[i, 1]), int(st[i])] for i in range(len(st))]
        sup, hs = 0, []
        for f, p, b in zip(t["f"], t["p"], t["b"]):
            for c in cands[f] if f < len(cands) else []:
                if b[0] - 40 <= c[0] <= b[2] + 40 and b[1] - 40 <= c[1] <= b[3] + 40 and c[4] >= 0.25:
                    sup += 1
                    hs.append(c[2])
                    break
        t["support"] = sup / max(1, len(t["f"]))
        t["h"] = float(np.median(hs)) if hs else 0.0
    tracks.sort(key=lambda t: t["f"][0])
    hs = [t["h"] for t in tracks if t["h"] > 0]
    h_take = float(plan.get("h_take", np.median(hs) if hs else 150.0))
    dancers, boxes, summary = {}, {}, {}
    for k, t in enumerate(tracks):
        nme = chr(65 + k)
        dancers[nme] = {str(f): [round(p[0], 1), round(p[1], 1), int(p[2])] for f, p in zip(t["f"], t["p"])}
        boxes[nme] = {str(f): [round(v) for v in b] for f, b in zip(t["f"], t["b"])}
        st = np.array([p[2] for p in t["p"]])
        summary[nme] = {"first": t["f"][0], "last": t["f"][-1], "frames": len(t["f"]),
                        "own": round(float((st == 0).mean()), 3), "contact": round(float((st == 1).mean()), 3),
                        "coast": round(float((st == 2).mean()), 3), "uncertain": round(float((st == 3).mean()), 3),
                        "support": round(t["support"], 3), "h": round(t["h"], 1)}
    T = _finish(stem, n, size, h_take, dancers, boxes, bg, meta or {}, plan)
    return T, warns, summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("blobs")
    a1.add_argument("proxy")
    a1.add_argument("out")
    a1.add_argument("--stem", default=None)
    a1.add_argument("--thr", type=float, default=22.0)
    a2 = sub.add_parser("build")
    a2.add_argument("stem")
    a2.add_argument("--blobs", required=True)
    a2.add_argument("--plans", required=True)
    a2.add_argument("--out", required=True)
    a2.add_argument("--meta", default=None)
    a2.add_argument("--replay", default=None, help="a replay jsonl whose boxes give the body height")
    a3 = sub.add_parser("import")
    a3.add_argument("truth_json")
    a3.add_argument("--bg", default=None)
    a3.add_argument("--meta", default=None)
    a3.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "blobs":
        print(blobs(a.proxy, a.out, a.stem, a.thr))
    elif a.cmd == "build":
        import cv2
        from truth_score import read_jsonl
        plan = json.loads((Path(a.plans) / f"{a.stem}.plan.json").read_text())
        bgp = Path(a.blobs) / f"{a.stem}.bg.png"
        bg = cv2.imread(str(bgp), 0) if bgp.exists() else None
        bz = Path(a.blobs) / f"{a.stem}.blobs.npz"
        if not bz.exists():
            bz = Path(a.blobs) / f"{a.stem}.npz"
        rows = list(read_jsonl(a.replay)) if a.replay else None
        T, warns, summary = build(a.stem, bz, plan, bg, take_meta(a.meta) if a.meta else None, rows)
        T.save(Path(a.out) / f"{a.stem}.npz")
        print(a.stem, "frames", T.frames, "h_take", round(T.h_take), warns or "")
        for k, v in summary.items():
            print("  ", k, json.dumps(v))
    else:
        print(import_json(a.truth_json, a.bg, a.meta, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
