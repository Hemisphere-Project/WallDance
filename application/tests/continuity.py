#!/usr/bin/env python3
"""Continuity metrics C1-C10 (TEST-1; audit 2026-10 ``01-continuity.md`` §3.3).

``scoring.py`` answers "how many dancers were reported vs expected".  This
module answers what the OSC consumer actually *feels* over a long span: does the
emitted id stream stay up, how long are the holes, does the same id come back,
are extra ids duplicates on the dancer or ghosts in the scenery, and is the
emitted point on the dancer at all.

| #   | key(s)                                         | definition |
|-----|------------------------------------------------|------------|
| C1  | ``coverage``                                   | sum(min(emitted, N)) / sum(N) (= 1 - drop_rate) |
| C2  | ``gaps_per_min`` (+ ``drop_episodes``)         | drop episodes (runs of emitted < N) per minute |
| C3  | ``gap_p50_s`` / ``gap_p90_s`` / ``gap_max_s``, ``gaps_ge_0_5s`` / ``gaps_ge_1s`` | gap-length distribution |
| C4  | ``ids_per_dancer`` (+ ``distinct_ids``)        | distinct emitted ids / max N over the span |
| C5  | ``reassoc_rate`` (+ ``reassoc_same/new``)      | share of gaps after which an id from before the gap returns |
| C6  | ``handovers`` / ``handovers_per_min``          | emitted-id change between consecutive covered frames (no gap) |
| C7  | ``dup_rate`` / ``ghost_extra_rate``, ``dup_frames`` / ``ghost_frames`` | extra ids within ``tol_h`` x h of a dancer (duplicates) vs elsewhere (ghosts) |
| C8  | ``spatial_validity``                           | share of reference dancer positions with an emitted centroid within ``tol_h`` x h |
| C9  | ``acquisition_frames`` / ``acquisition_s``     | frames from the first dancer evidence to the first emission (no warm-up exclusion) |
| C10 | ``freeze_share``                               | emitted track-frames whose centroid equals the same id's previous one |

Timeline rows (``replay.py`` per-frame records, ``frame`` window-relative)::

    {"frame": 12, "abs_frame": 1512, "reported": 1, "ids": [3],
     "tracks": [{"id": 3, "bbox": [x, y, w, h], "centroid": [x, y]}],   # optional
     "ref":    [{"c": [x, y], "h": 180.0, "conf": 0.71}]}              # optional

``tracks`` (``replay.py --details``, implied by ``--score``) enables C7 by
proximity and C10.  ``ref`` is the pseudo ground truth: the YOLO detections of
that frame in original-frame px (``replay.py --score`` records them).  C8 and
the reference basis of C7/C9 need it; without it they fall back (C7 proximity,
C9 expected-count) or report ``None``.  Markers will later supply a real ``ref``
(audit §5 hook 5) without changing this module.

Reference filtering, per manifest (all optional)::

    "reference": {"min_conf": 0.5,          # YOLO box conf floor (None conf = passed tau)
                  "tol_h": 0.75,            # spatial tolerance, x the reference height
                  "exclude_spots": [[x, y, r], ...]}   # recurring fixed scenery dets

Pure stdlib (no numpy/cv2) so it unit-tests instantly, like ``scoring.py``.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import scoring

DEFAULT_TOL_H = 0.75
DEFAULT_REF_MIN_CONF = 0.5
DEFAULT_WINDOW = 300

# Class-A pass line for a 300-frame window (CORPUS_ANALYSIS §8) — the default
# line of the per-window pass-rate when a manifest carries no ``window_pass``.
DEFAULT_WINDOW_PASS = {"class": "A", "drop_rate": 0.05, "ghost_rate": 0.05,
                       "longest_drop_s": 1.0}

# Long-span class-A pass line proposed in 01-continuity §3.3 (spans >= 4 min).
# Keys are ``<metric>_min`` / ``<metric>_max`` over ``continuity_metrics`` keys
# (see ``scoring.evaluate_pass``).
LONG_SPAN_PASS_A = {"class": "A", "span": "long",
                    "coverage_min": 0.98, "gaps_ge_1s_max": 0,
                    "gaps_per_min_max": 5.0, "ids_per_dancer_max": 1.5,
                    "spatial_validity_min": 0.97}

# C-number -> headline key, for printing / docs.
C_KEYS = {
    "C1": "coverage", "C2": "gaps_per_min", "C3": "gap_max_s",
    "C4": "ids_per_dancer", "C5": "reassoc_rate", "C6": "handovers",
    "C7": "dup_rate", "C8": "spatial_validity", "C9": "acquisition_s",
    "C10": "freeze_share",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _runs(flags: Sequence[bool]) -> List[tuple]:
    """Contiguous runs of True -> [(start_idx, end_idx)] (inclusive)."""
    return [tuple(e) for e in scoring._episodes(flags)]


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank quantile (matches the audit's contmetrics.py)."""
    if not values:
        return None
    s = sorted(values)
    return s[int(q * (len(s) - 1))]


def _r(x, nd=4):
    return None if x is None else round(x, nd)


def _ref_cfg(manifest: dict) -> dict:
    ref = dict(manifest.get("reference") or {})
    ref.setdefault("min_conf", DEFAULT_REF_MIN_CONF)
    ref.setdefault("tol_h", DEFAULT_TOL_H)
    ref.setdefault("exclude_spots", [])
    return ref


def _space_get(space, key):
    """``_TrackerSpace`` attribute or detect-cache ``space`` dict entry."""
    return space[key] if isinstance(space, dict) else getattr(space, key)


def reference_from_dets(dets, space, box_confs=None) -> List[dict]:
    """A frame's ``ref`` list from its YOLO detections.

    ``dets`` are ``(keypoints, conf, bbox)`` in letterbox tracker space -- what
    the GPU detect-cache hook sees after YOLO + duplicate filtering -- and
    ``space`` the ``_TrackerSpace`` (or the cache's dict of it).  Boxes are
    mapped to original-frame px, the space of the emitted ``tracks[].centroid``.
    ``box_confs[i]`` is the YOLO box conf of ``dets[i]`` (None = unknown: the
    det already cleared YOLO's tau).  ``reference_positions`` then applies the
    manifest's conf floor and fixed-spot exclusion.
    """
    s = float(_space_get(space, "scale")) or 1.0
    px, py = float(_space_get(space, "pad_x")), float(_space_get(space, "pad_y"))
    rx, ry = float(_space_get(space, "roi_x")), float(_space_get(space, "roi_y"))
    out = []
    for i, (_k, _c, b) in enumerate(dets):
        x, y, w, h = (float(v) for v in list(b)[:4])
        conf = box_confs[i] if box_confs is not None and i < len(box_confs) else None
        out.append({"c": [round((x + w / 2 - px) / s + rx, 1),
                          round((y + h / 2 - py) / s + ry, 1)],
                    "h": round(h / s, 1),
                    "conf": None if conf is None else round(float(conf), 3)})
    return out


def reference_positions(row: dict, n_expected: int, ref_cfg: dict) -> List[dict]:
    """The row's usable pseudo-GT dancer refs: conf >= min_conf (unknown conf
    passes -- the det already cleared YOLO's tau), not on an excluded fixed spot,
    top-``n_expected`` by conf.  ``[]`` when the row carries no refs."""
    refs = row.get("ref") or []
    min_conf = ref_cfg.get("min_conf")
    spots = ref_cfg.get("exclude_spots") or []
    out = []
    for r in refs:
        conf = r.get("conf")
        if conf is not None and min_conf is not None and conf < min_conf:
            continue
        cx, cy = r["c"]
        if any(math.hypot(cx - sx, cy - sy) <= sr for sx, sy, sr in spots):
            continue
        out.append(r)
    out.sort(key=lambda r: -(r.get("conf") if r.get("conf") is not None else 1.0))
    return out[:max(0, n_expected)]


def _track_points(row: dict) -> Optional[List[tuple]]:
    """[(id, (x, y), h)] for emitted tracks, or None without track details."""
    tracks = row.get("tracks")
    if tracks is None:
        return None
    pts = []
    for t in tracks:
        c = t.get("centroid")
        b = t.get("bbox")
        if c is None and b is not None:
            c = [b[0] + b[2] / 2.0, b[1] + b[3] / 2.0]
        if c is None:
            continue
        h = float(b[3]) if b is not None else None
        pts.append((int(t["id"]), (float(c[0]), float(c[1])), h))
    return pts


def _classify_extras(row: dict, n: int, refs: List[dict], tol_h: float):
    """(duplicates, ghosts, basis) for one frame emitting more than N ids.

    reference basis: an emitted track is *on a dancer* when within tol_h x h of a
    ref; duplicates = on-dancer tracks beyond one per covered dancer.
    proximity basis (no ref): tracks within tol_h x max(h) of each other form a
    cluster (the same body); duplicates = sum(cluster size - 1).
    Ghosts are the remaining extras."""
    rep = int(row["reported"])
    extras = max(0, rep - n)
    if extras == 0:
        return 0, 0, None
    pts = _track_points(row)
    if refs and pts:
        on = 0
        covered = set()
        for _tid, (x, y), _h in pts:
            hit = None
            for k, r in enumerate(refs):
                if math.hypot(x - r["c"][0], y - r["c"][1]) <= tol_h * float(r["h"]):
                    hit = k
                    break
            if hit is not None:
                on += 1
                covered.add(hit)
        dups = min(extras, max(0, on - len(covered)))
        return dups, extras - dups, "reference"
    if pts:
        parent = list(range(len(pts)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                hi = pts[i][2] or 0.0
                hj = pts[j][2] or 0.0
                lim = tol_h * max(hi, hj)
                (xi, yi), (xj, yj) = pts[i][1], pts[j][1]
                if lim > 0 and math.hypot(xi - xj, yi - yj) <= lim:
                    parent[find(i)] = find(j)
        clusters: Dict[int, int] = {}
        for i in range(len(pts)):
            clusters[find(i)] = clusters.get(find(i), 0) + 1
        dups = min(extras, sum(c - 1 for c in clusters.values()))
        return dups, extras - dups, "proximity"
    return 0, 0, "unknown"


# --------------------------------------------------------------------------- #
# C1-C10
# --------------------------------------------------------------------------- #
def continuity_metrics(timeline: List[dict], manifest: dict,
                       fps: Optional[float] = None) -> dict:
    """C1-C10 for a per-frame timeline against a scenario manifest.

    C1-C8 and C10 are computed over the scored frames (``frame >= warmup``, as
    ``scoring.score_timeline``); C9 deliberately uses every frame -- the
    warm-up exclusion is exactly what hides acquisition latency.
    """
    fps = float(fps or manifest.get("fps") or 20.0)
    warmup = int(manifest.get("warmup", 0))
    ref_cfg = _ref_cfg(manifest)
    tol_h = float(ref_cfg["tol_h"])

    all_rows = sorted(timeline, key=lambda r: r["frame"])
    rows = [r for r in all_rows if r["frame"] >= warmup]
    N = [scoring.expected_at(manifest, r["frame"]) for r in rows]
    rep = [int(r["reported"]) for r in rows]
    ids = [[int(i) for i in (r.get("ids") or [])] for r in rows]
    n_rows = len(rows)
    dur_min = n_rows / fps / 60.0 if n_rows else 0.0

    # ---- C1 coverage / ghost rate
    exp = sum(N)
    coverage = sum(min(a, b) for a, b in zip(rep, N)) / exp if exp else 1.0
    ghost_rate = (sum(max(0, a - b) for a, b in zip(rep, N)) / n_rows
                  if n_rows else 0.0)

    # ---- C2/C3 gaps
    drop = [n > 0 and r < n for r, n in zip(rep, N)]
    eps = _runs(drop)
    gap_s = [(b - a + 1) / fps for a, b in eps]
    covered = [not d and n > 0 for d, n in zip(drop, N)]
    full_runs = _runs(covered)

    # ---- C4 ids
    max_n = max(N) if N else 0
    distinct = sorted({i for x in ids for i in x})

    # ---- C5 re-association across each gap (ids just outside the episode)
    same = new = 0
    for a, b in eps:
        before = set(ids[a - 1]) if a > 0 else set()
        after = set(ids[b + 1]) if b + 1 < n_rows else set()
        if not before or not after:
            continue
        if before & after:
            same += 1
        else:
            new += 1

    # ---- C6 handovers: an id slot changes between two covered frames
    handovers = primary_switches = 0
    for k in range(1, n_rows):
        if not (covered[k - 1] and covered[k]):
            continue
        prev, cur = set(ids[k - 1]), set(ids[k])
        if prev and cur:
            need = min(N[k], len(prev), len(cur))
            if len(prev & cur) < need:
                handovers += 1
            if min(prev) != min(cur):
                primary_switches += 1

    # ---- C7 duplicates vs ghosts, C8 spatial validity
    have_refs = any("ref" in r for r in all_rows)
    have_tracks = any("tracks" in r for r in all_rows)
    dup_total = ghost_total = dup_frames = ghost_frames = multi = 0
    basis = {"reference": 0, "proximity": 0, "unknown": 0}
    checked = misplaced = 0
    for r, n in zip(rows, N):
        refs = reference_positions(r, n, ref_cfg) if n > 0 else []
        if int(r["reported"]) > n:
            multi += 1
            d, g, how = _classify_extras(r, n, refs, tol_h)
            basis[how] += 1
            dup_total += d
            ghost_total += g
            dup_frames += d > 0
            ghost_frames += g > 0
        if refs and int(r["reported"]) > 0:
            pts = _track_points(r)
            if pts:
                for ref in refs:
                    dmin = min(math.hypot(x - ref["c"][0], y - ref["c"][1])
                               for _i, (x, y), _h in pts)
                    checked += 1
                    misplaced += dmin > tol_h * float(ref["h"])

    # ---- C9 acquisition latency (all frames, no warm-up exclusion)
    acq_frames = None
    acq_basis = "expected"
    first_ev = None
    if have_refs:
        for r in all_rows:
            n = scoring.expected_at(manifest, r["frame"])
            if n > 0 and reference_positions(r, n, ref_cfg):
                first_ev = r["frame"]
                acq_basis = "reference"
                break
    if first_ev is None:
        for r in all_rows:
            if scoring.expected_at(manifest, r["frame"]) > 0:
                first_ev = r["frame"]
                break
    if first_ev is not None:
        for r in all_rows:
            if r["frame"] >= first_ev and int(r["reported"]) > 0:
                acq_frames = r["frame"] - first_ev
                break

    # ---- C10 freeze share (same id, identical centroid on consecutive frames)
    frozen = comparable = 0
    prev_pts: Optional[Dict[int, tuple]] = None
    prev_frame = None
    for r in rows:
        pts = _track_points(r)
        cur = {tid: c for tid, c, _h in pts} if pts is not None else None
        if (cur is not None and prev_pts is not None
                and prev_frame is not None and r["frame"] == prev_frame + 1):
            for tid, c in cur.items():
                if tid in prev_pts:
                    comparable += 1
                    pc = prev_pts[tid]
                    frozen += abs(c[0] - pc[0]) < 1e-6 and abs(c[1] - pc[1]) < 1e-6
        prev_pts, prev_frame = cur, r["frame"]

    return {
        "frames": n_rows,
        "fps": round(fps, 3),
        "duration_s": round(n_rows / fps, 2) if fps else 0.0,
        # C1
        "coverage": round(coverage, 4),
        "ghost_rate": round(ghost_rate, 4),
        # C2
        "drop_episodes": len(eps),
        "gaps_per_min": round(len(eps) / dur_min, 2) if dur_min else 0.0,
        # C3
        "gap_p50_s": _r(statistics.median(gap_s), 2) if gap_s else 0.0,
        "gap_p90_s": _r(_quantile(gap_s, 0.9), 2) if gap_s else 0.0,
        "gap_max_s": _r(max(gap_s), 2) if gap_s else 0.0,
        "gaps_ge_0_5s": sum(g >= 0.5 for g in gap_s),
        "gaps_ge_1s": sum(g >= 1.0 for g in gap_s),
        "mean_run_s": (round(statistics.mean((b - a + 1) / fps for a, b in full_runs), 2)
                       if full_runs else 0.0),
        # C4
        "distinct_ids": len(distinct),
        "ids_per_dancer": round(len(distinct) / max_n, 2) if max_n else None,
        # C5
        "reassoc_same": same,
        "reassoc_new": new,
        "reassoc_rate": round(same / (same + new), 4) if (same + new) else None,
        # C6
        "handovers": handovers,
        "handovers_per_min": round(handovers / dur_min, 2) if dur_min else 0.0,
        "primary_id_switches": primary_switches,
        # C7
        "multi_id_frames": multi,
        "dup_frames": dup_frames,
        "ghost_frames": ghost_frames,
        "dup_rate": round(dup_total / n_rows, 4) if n_rows else 0.0,
        "ghost_extra_rate": round(ghost_total / n_rows, 4) if n_rows else 0.0,
        "c7_basis": {k: v for k, v in basis.items() if v},
        # C8
        "spatial_checked": checked,
        "spatial_misplaced": misplaced,
        "spatial_validity": round(1.0 - misplaced / checked, 4) if checked else None,
        # C9
        "acquisition_frames": acq_frames,
        "acquisition_s": round(acq_frames / fps, 2) if acq_frames is not None else None,
        "acquisition_basis": acq_basis,
        # C10
        "freeze_share": round(frozen / comparable, 4) if comparable else None,
        "inputs": {"ref": have_refs, "tracks": have_tracks},
    }


# --------------------------------------------------------------------------- #
# Per-window pass rate
# --------------------------------------------------------------------------- #
def _shift_expected(ec, offset: int):
    """A manifest ``expected_count`` re-based to a window starting at ``offset``."""
    if isinstance(ec, int):
        return ec
    out = []
    for rng in ec:
        if "default" in rng:
            out.append(dict(rng))
        else:
            out.append({"from": int(rng["from"]) - offset,
                        "to": int(rng["to"]) - offset, "n": rng["n"]})
    return out


def window_pass_rate(timeline: List[dict], manifest: dict,
                     window: Optional[int] = None) -> dict:
    """Score consecutive non-overlapping ``window``-frame windows of a (long)
    timeline with the official scorer and its window pass line.

    The tracker runs continuously, so only the first window keeps the
    manifest's warm-up exclusion (the later ones start warm -- the audit's
    "7 of 16 slot-4 windows pass" measure).  Only full windows count.
    The line is ``manifest["window_pass"]`` (``window`` key = size), else the
    manifest's own ``pass`` block when it is a classic window line, else class A.
    """
    wp = manifest.get("window_pass")
    if wp is None:
        legacy = manifest.get("pass") or {}
        wp = legacy if any(k in legacy for k in DEFAULT_WINDOW_PASS) else DEFAULT_WINDOW_PASS
    size = int(window or wp.get("window", DEFAULT_WINDOW))
    line = {k: v for k, v in wp.items() if k != "window"}
    rows = sorted(timeline, key=lambda r: r["frame"])
    if not rows or size <= 0:
        return {"window": size, "n_windows": 0, "n_pass": 0, "pass_rate": None,
                "windows": [], "line": line}
    first = rows[0]["frame"]
    last = rows[-1]["frame"]
    start_abs = int(manifest.get("start", 0))
    out = []
    w0 = first
    k = 0
    while w0 + size - 1 <= last:
        sub = [dict(r, frame=r["frame"] - w0) for r in rows if w0 <= r["frame"] < w0 + size]
        sub_m = dict(manifest)
        sub_m["start"] = start_abs + w0
        sub_m["frames"] = size
        sub_m["warmup"] = int(manifest.get("warmup", 0)) if k == 0 else 0
        sub_m["expected_count"] = _shift_expected(manifest["expected_count"], w0)
        sub_m["pass"] = line
        res = scoring.score_timeline(sub, sub_m)
        verdict = scoring.evaluate_pass(res, sub_m)
        out.append({
            "start": w0, "start_abs": start_abs + w0,
            "passed": verdict["passed"],
            "drop_rate": res["components"]["drop_rate"],
            "ghost_rate": res["components"]["ghost_rate"],
            "longest_drop_s": res["raw"]["longest_drop_seconds"],
            "distinct_ids": res["raw"]["distinct_ids"],
        })
        w0 += size
        k += 1
    n_pass = sum(w["passed"] for w in out)
    worst = max(out, key=lambda w: (w["drop_rate"] + w["ghost_rate"],
                                    w["longest_drop_s"])) if out else None
    return {
        "window": size,
        "line": line,
        "n_windows": len(out),
        "n_pass": n_pass,
        "pass_rate": round(n_pass / len(out), 4) if out else None,
        "worst": worst,
        "windows": out,
    }


def continuity_report(timeline: List[dict], manifest: dict,
                      fps: Optional[float] = None,
                      window: Optional[int] = None) -> dict:
    """``continuity_metrics`` + ``window_pass_rate`` in one dict (the CLI block)."""
    return {
        "metrics": continuity_metrics(timeline, manifest, fps=fps),
        "windows": window_pass_rate(timeline, manifest, window=window),
    }


def format_report(report: dict) -> str:
    """Compact human block for ``replay.py --score``."""
    m = report["metrics"]
    w = report["windows"]

    def f(v, fmt="{:.4f}"):
        return "n/a" if v is None else fmt.format(v)
    lines = [
        f"C1  coverage          {f(m['coverage'])}   (ghost_rate {f(m['ghost_rate'])})",
        f"C2  gaps/min          {f(m['gaps_per_min'], '{:.2f}')}   ({m['drop_episodes']} drop episodes"
        f" over {m['duration_s']} s)",
        f"C3  gap p50/p90/max   {f(m['gap_p50_s'], '{:.2f}')} / {f(m['gap_p90_s'], '{:.2f}')} /"
        f" {f(m['gap_max_s'], '{:.2f}')} s   (>=0.5 s: {m['gaps_ge_0_5s']}, >=1 s: {m['gaps_ge_1s']})",
        f"C4  ids/dancer        {f(m['ids_per_dancer'], '{:.2f}')}   ({m['distinct_ids']} distinct ids)",
        f"C5  re-association    {f(m['reassoc_rate'])}   (same {m['reassoc_same']} / new {m['reassoc_new']})",
        f"C6  handovers         {m['handovers']}   ({f(m['handovers_per_min'], '{:.2f}')}/min;"
        f" primary-id switches {m['primary_id_switches']})",
        f"C7  dup / ghost rate  {f(m['dup_rate'])} / {f(m['ghost_extra_rate'])}   "
        f"(multi-id frames {m['multi_id_frames']}: dup {m['dup_frames']}, ghost {m['ghost_frames']};"
        f" basis {m['c7_basis'] or '-'})",
        f"C8  spatial validity  {f(m['spatial_validity'])}   "
        f"({m['spatial_misplaced']} misplaced / {m['spatial_checked']} checked)",
        f"C9  acquisition       {f(m['acquisition_s'], '{:.2f}')} s   "
        f"({m['acquisition_frames']} frames, basis {m['acquisition_basis']})",
        f"C10 freeze share      {f(m['freeze_share'])}",
    ]
    if w.get("n_windows"):
        lines.append(
            f"windows ({w['window']} f) pass {w['n_pass']}/{w['n_windows']}"
            f" = {f(w['pass_rate'])}   worst @{w['worst']['start_abs']}:"
            f" drop {w['worst']['drop_rate']:.3f}, longest {w['worst']['longest_drop_s']} s")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Continuity metrics C1-C10 for a replay timeline")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--timeline", required=True,
                    help="timeline JSON (list of rows or a replay summary with per_frame)")
    ap.add_argument("--window", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="print the full JSON report")
    args = ap.parse_args()
    manifest = scoring.load_scenario(args.scenario)
    timeline = scoring._load_timeline(args.timeline)
    rep = continuity_report(timeline, manifest, window=args.window)
    rep["pass"] = scoring.evaluate_pass(
        scoring.score_timeline(timeline, manifest), manifest, continuity=rep["metrics"])
    print(json.dumps(rep, indent=2) if args.json else format_report(rep))


if __name__ == "__main__":
    main()
