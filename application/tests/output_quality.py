#!/usr/bin/env python3
"""Emitted-stream quality (CONT-6 scoring): tracker output vs identity slots.

``continuity.py`` measures *whether* an id is up (coverage, gaps, ids).  This
module adds *how the point moves* and *whether it stays on the same dancer*,
for any stream in the replay timeline row shape (``tracks`` with ``id`` +
``centroid`` + ``bbox``; ``ref`` = the frame's raw YOLO detections):

| key                  | definition |
|----------------------|------------|
| ``jitter_rest_pct``  | RMS of the 2nd difference of an id's emitted centroid, in % of the dancer height, on frames where the dancer is near-static: its anchor tracklet (below) stays within ``static_h`` x h over +-``static_win`` frames |
| ``ref_jitter_rest_pct`` | the same on the raw YOLO centroid alone (what smoothing has to remove) |
| ``lag_ms``           | lag of the emitted centroid behind the raw YOLO centroid on fast frames (raw YOLO speed > ``fast_h_per_s``): argmax over 0..``max_lag`` frames of the velocity cross-correlation, parabolic refinement |
| ``fast_err_h``       | mean distance emitted -> raw YOLO centroid on fast frames, in h (lag + centroid offset) |
| ``id_switches``      | pseudo-GT id switches: dancer anchors are linked frame to frame into tracklets; a switch is a tracklet whose matched emitted id changes (tracker id churn, or a slot swap) |
| ``coasting_share``   | share of emitted dancer-frames in state coasting (``belt_share`` likewise) |
| ``over_n_frames``    | frames emitting more ids than dancers (``N`` from the manifest, else the cap) |
| ``distinct_ids``     | distinct emitted ids |
| ``close_pair_frames`` | frames where two emitted points are within ``close_h`` x h (two ids on one body, or a duo in contact) |

**Anchors** (``anchors_of``) say where the dancers are on a frame independently
of the stream being scored: the raw YOLO detections plus the tracker's raw KF
positions that no detection explains (YOLO gaps).  Raw YOLO alone is too sparse
on the dark wall footage for the near-static test.

Pure stdlib (like ``scoring.py`` / ``continuity.py``) so it unit-tests instantly.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

import scoring

STATIC_H = 0.15        # near-static: anchor range < 0.15 h over the window
STATIC_WIN = 5         # +-5 frames (~0.5 s)
FAST_H_PER_S = 0.5     # fast: raw YOLO speed > 0.5 dancer height per second
MAX_LAG = 8            # frames searched for the lag
MATCH_TOL_H = 0.75     # emitted <-> ref / anchor match tolerance (x height)
LINK_TOL_H = 0.5       # anchor <-> anchor tracklet linking between consecutive frames
CLOSE_H = 0.35         # two emitted points closer than this (x h): one body, two ids?

Point = Tuple[float, float, float]   # x, y, h


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def emitted_timeline(rows: Sequence[dict]) -> List[dict]:
    """The identity-slot stream as a plain timeline (row shape of ``replay``):
    each row's ``emitted`` block promoted to ``reported``/``ids``/``tracks``,
    ``ref`` kept.  Rows without ``emitted`` are dropped (stream absent)."""
    out = []
    for r in rows:
        em = r.get("emitted")
        if em is None:
            continue
        row = {"frame": r["frame"], "abs_frame": r.get("abs_frame"),
               "reported": int(em["reported"]), "ids": list(em["ids"])}
        if "tracks" in em:
            row["tracks"] = em["tracks"]
        if "ref" in r:
            row["ref"] = r["ref"]
        out.append(row)
    return out


def _points(row: dict) -> List[Tuple[int, float, float, float, Optional[str]]]:
    """[(id, x, y, h, state)] for a row's tracks (centroid as emitted)."""
    pts = []
    for t in row.get("tracks") or []:
        c = t.get("centroid")
        b = t.get("bbox")
        if c is None and b is not None:
            c = [b[0] + b[2] / 2.0, b[1] + b[3] / 2.0]
        if c is None:
            continue
        h = float(b[3]) if b is not None else 1.0
        pts.append((int(t["id"]), float(c[0]), float(c[1]), max(1.0, h), t.get("state")))
    return pts


def _refs(row: dict, min_conf: Optional[float]) -> List[Point]:
    out = []
    for r in row.get("ref") or []:
        conf = r.get("conf")
        if min_conf is not None and conf is not None and conf < min_conf:
            continue
        out.append((float(r["c"][0]), float(r["c"][1]), max(1.0, float(r["h"]))))
    return out


def _greedy_match(a: List[Tuple[float, float]], b: List[Point],
                  tol_h: float) -> Dict[int, int]:
    """Greedy nearest matching a[i] -> b[j] within tol_h x b.h (small N)."""
    pairs = []
    for i, (x, y) in enumerate(a):
        for j, (bx, by, bh) in enumerate(b):
            d = math.hypot(x - bx, y - by)
            if d <= tol_h * bh:
                pairs.append((d / bh, i, j))
    pairs.sort()
    out: Dict[int, int] = {}
    used_b = set()
    for _d, i, j in pairs:
        if i in out or j in used_b:
            continue
        out[i] = j
        used_b.add(j)
    return out


def anchors_of(row: dict, min_conf: Optional[float],
               tol_h: float = LINK_TOL_H) -> List[Point]:
    """Where the dancers are on a frame, independent of the stream being
    scored: the raw YOLO detections, plus the tracker's raw (KF) positions of
    its reported tracks that no detection explains (YOLO gaps)."""
    out = list(_refs(row, min_conf))
    for t in row.get("tracks") or []:
        xy = t.get("raw") or t.get("centroid")
        b = t.get("bbox")
        if xy is None or b is None:
            continue
        h = max(1.0, float(b[3]))
        if all(math.hypot(xy[0] - x, xy[1] - y) > tol_h * max(h, rh) for x, y, rh in out):
            out.append((float(xy[0]), float(xy[1]), h))
    return out


def link_tracklets(frames: Sequence[int], points: Sequence[List[Point]],
                   link_tol_h: float = LINK_TOL_H) -> List[Dict[int, int]]:
    """Per frame, {point index -> tracklet id}: points linked between
    consecutive frames (greedy, within ``link_tol_h`` x h).  A missed frame
    breaks the tracklet (identity is only trusted while continuously seen)."""
    out: List[Dict[int, int]] = []
    prev: List[Point] = []
    prev_ids: Dict[int, int] = {}
    prev_frame = None
    nxt = 0
    for f, pts in zip(frames, points):
        ids: Dict[int, int] = {}
        if prev and prev_frame is not None and f == prev_frame + 1:
            m = _greedy_match([(x, y) for x, y, _h in pts], prev, link_tol_h)
            for i, j in m.items():
                ids[i] = prev_ids[j]
        for i in range(len(pts)):
            if i not in ids:
                ids[i] = nxt
                nxt += 1
        out.append(ids)
        prev, prev_ids, prev_frame = pts, ids, f
    return out


def _rest_jitter(series: Dict[int, Dict[int, tuple]], trk_pos: Dict[int, Dict[int, Point]],
                 static_h: float, static_win: int) -> Tuple[float, int]:
    """Sum of squared 2nd differences (in h) and the count, over frames whose
    anchor tracklet is near-static (>= 70 % of the window seen).  Frames at a
    slot state change (coasting -> live: a re-acquisition correction, not
    shaking) are left out."""
    sq = 0.0
    n = 0
    for s in series.values():
        for f, e in s.items():
            x, y, h, _ref, trk = e[:5]
            if trk is None or (f - 1) not in s or (f + 1) not in s:
                continue
            states = {w[5] if len(w) > 5 else None for w in (s[f - 1], e, s[f + 1])}
            if len(states) > 1:
                continue
            tp = trk_pos.get(trk, {})
            win = [tp.get(f + d) for d in range(-static_win, static_win + 1)]
            have = [w for w in win if w is not None]
            if len(have) < 0.7 * len(win):
                continue
            rh = have[len(have) // 2][2]
            if (max(w[0] for w in have) - min(w[0] for w in have) > static_h * rh
                    or max(w[1] for w in have) - min(w[1] for w in have) > static_h * rh):
                continue
            a, b = s[f - 1], s[f + 1]
            d2 = math.hypot(a[0] - 2 * x + b[0], a[1] - 2 * y + b[1]) / h
            sq += d2 * d2
            n += 1
    return sq, n


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def stream_quality(rows: Sequence[dict], *, fps: float = 20.0,
                   n_expected=None, min_conf: Optional[float] = 0.5,
                   anchors: Optional[Dict[int, List[Point]]] = None,
                   static_h: float = STATIC_H, static_win: int = STATIC_WIN,
                   fast_h_per_s: float = FAST_H_PER_S, max_lag: int = MAX_LAG,
                   tol_h: float = MATCH_TOL_H, warmup: int = 0,
                   close_h: float = None) -> dict:
    """Quality metrics for one stream (see module doc).  ``n_expected`` is the
    manifest (``scoring.expected_at``) or an int cap, or None.  ``anchors``
    ({frame: [(x, y, h)]}, see ``anchors_of``) locate the dancers for the
    near-static test and the id-switch tracklets; default = the raw YOLO refs."""
    close_h = CLOSE_H if close_h is None else close_h
    rows = sorted((r for r in rows if r["frame"] >= warmup), key=lambda r: r["frame"])
    frames = [r["frame"] for r in rows]
    if anchors is None:
        anc = [_refs(r, min_conf) for r in rows]
    else:
        anc = [anchors.get(f) or [] for f in frames]
    atl = link_tracklets(frames, anc)
    trk_pos: Dict[int, Dict[int, Point]] = {}
    for f, pts, ids in zip(frames, anc, atl):
        for i, pt in enumerate(pts):
            trk_pos.setdefault(ids[i], {})[f] = pt

    # per id: frame -> (x, y, h, ref point or None, anchor tracklet or None)
    series: Dict[int, Dict[int, tuple]] = {}
    states = {"live": 0, "coasting": 0, "belt": 0}
    n_emit = over_n = close = 0
    distinct = set()
    for r, pts_a, ids_a in zip(rows, anc, atl):
        pts = _points(r)
        refs = _refs(r, min_conf)
        n = None
        if isinstance(n_expected, dict):
            n = scoring.expected_at(n_expected, r["frame"])
        elif n_expected is not None:
            n = int(n_expected)
        if n is not None and int(r.get("reported", len(pts))) > n:
            over_n += 1
        if any(math.hypot(a[1] - b[1], a[2] - b[2]) < close_h * max(a[3], b[3])
               for i, a in enumerate(pts) for b in pts[i + 1:]):
            close += 1
        xy = [(x, y) for _i, x, y, _h, _s in pts]
        m_ref = _greedy_match(xy, refs, tol_h)
        m_anc = _greedy_match(xy, pts_a, tol_h)
        for k, (tid, x, y, h, st) in enumerate(pts):
            distinct.add(tid)
            n_emit += 1
            if st in states:
                states[st] += 1
            ref = refs[m_ref[k]] if k in m_ref else None
            trk = ids_a[m_anc[k]] if k in m_anc else None
            series.setdefault(tid, {})[r["frame"]] = (x, y, h, ref, trk, st)

    # ---- jitter at rest: the stream, and the raw YOLO centroid for context
    sq, n_rest = _rest_jitter(series, trk_pos, static_h, static_win)
    rtl = link_tracklets(frames, [_refs(r, min_conf) for r in rows])
    rpos: Dict[int, Dict[int, Point]] = {}
    ref_series: Dict[int, Dict[int, tuple]] = {}
    for r, ids in zip(rows, rtl):
        for i, pt in enumerate(_refs(r, min_conf)):
            rpos.setdefault(ids[i], {})[r["frame"]] = pt
            ref_series.setdefault(ids[i], {})[r["frame"]] = (pt[0], pt[1], pt[2], pt, ids[i])
    sq_ref, n_rest_ref = _rest_jitter(ref_series, rpos, static_h, static_win)

    # ---- lag on fast frames.  The emitted point is modelled as the raw YOLO
    # centroid delayed by k frames plus a constant offset (keypoint centroid vs
    # box centre): for each k the offset is fitted (mean residual, in h) and the
    # lag is the k with the smallest residual spread (parabolic refinement).
    # Only frames with the raw YOLO point at every delay 0..max_lag count, so
    # each k is judged on the same frames.
    err_sum = 0.0
    n_fast = 0
    res: List[List[Tuple[float, float]]] = []     # per fast frame: [(rx, ry)] for k
    for s in series.values():
        for f in sorted(s):
            cur = s[f]
            a, b = s.get(f - 2), s.get(f + 2)
            if cur[3] is None or a is None or b is None or a[3] is None or b[3] is None:
                continue
            rh = cur[3][2]
            vr = math.hypot(b[3][0] - a[3][0], b[3][1] - a[3][1]) / 4.0 * fps / rh
            if vr < fast_h_per_s:
                continue
            past = [s.get(f - k) for k in range(max_lag + 1)]
            if any(p is None or p[3] is None for p in past):
                continue
            n_fast += 1
            err_sum += math.hypot(cur[0] - cur[3][0], cur[1] - cur[3][1]) / rh
            res.append([((cur[0] - p[3][0]) / rh, (cur[1] - p[3][1]) / rh) for p in past])
    spread: List[Optional[float]] = [None] * (max_lag + 1)
    if len(res) >= 10:
        for k in range(max_lag + 1):
            mx = sum(r[k][0] for r in res) / len(res)
            my = sum(r[k][1] for r in res) / len(res)
            spread[k] = sum((r[k][0] - mx) ** 2 + (r[k][1] - my) ** 2 for r in res) / len(res)
    corr = spread   # reported as lag_corr (residual spread per delay, h^2)
    lag_frames = None
    valid = [(v, k) for k, v in enumerate(spread) if v is not None]
    if valid:
        _v, k = min(valid)
        lag_frames = float(k)
        if 0 < k < max_lag:
            y0, y1, y2 = spread[k - 1], spread[k], spread[k + 1]
            den = y0 - 2 * y1 + y2
            if den > 0:
                lag_frames = k + 0.5 * (y0 - y2) / den

    # ---- pseudo-GT id switches: an anchor tracklet's matched id changes
    by_trk: Dict[int, List[Tuple[int, int]]] = {}
    for tid, s in series.items():
        for f, (_x, _y, _h, _ref, trk, *_st) in s.items():
            if trk is not None:
                by_trk.setdefault(trk, []).append((f, tid))
    switches = 0
    for seq in by_trk.values():
        seq.sort()
        last = None
        for _f, tid in seq:
            if last is not None and tid != last:
                switches += 1
            last = tid
    n_rows = len(rows)
    dur_min = n_rows / fps / 60.0 if n_rows else 0.0
    return {
        "frames": n_rows,
        "distinct_ids": len(distinct),
        "emitted_dancer_frames": n_emit,
        "over_n_frames": over_n if n_expected is not None else None,
        "close_pair_frames": close,
        "coasting_share": round(states["coasting"] / n_emit, 4) if n_emit else 0.0,
        "belt_share": round(states["belt"] / n_emit, 4) if n_emit else 0.0,
        "jitter_rest_pct": round(100 * math.sqrt(sq / n_rest), 3) if n_rest else None,
        "ref_jitter_rest_pct": round(100 * math.sqrt(sq_ref / n_rest_ref), 3) if n_rest_ref else None,
        "rest_frames": n_rest,
        "lag_ms": round(1000.0 * lag_frames / fps, 1) if lag_frames is not None else None,
        "lag_resid": [None if c is None else round(c, 5) for c in corr],
        "fast_frames": n_fast,
        "fast_err_h": round(err_sum / n_fast, 3) if n_fast else None,
        "id_switches": switches,
        "id_switches_per_min": round(switches / dur_min, 2) if dur_min else 0.0,
    }


def compare_streams(rows: Sequence[dict], manifest: Optional[dict], *,
                    fps: float = 20.0, max_dancers: Optional[int] = None) -> dict:
    """Tracker-reported stream vs the emitted (identity-slot) stream on one
    replay timeline: continuity C1-C10 (when a manifest gives N) + quality."""
    import continuity
    streams = {"tracker": list(rows)}
    em = emitted_timeline(rows)
    if em:
        streams["emitted"] = em
    n_exp = manifest if manifest is not None else max_dancers
    warmup = int(manifest.get("warmup", 0)) if manifest is not None else 0
    # Motion quality uses every YOLO detection that passed tau (the C8 conf
    # floor would leave the dark wall footage with almost no reference).
    min_conf = None
    anchors = {r["frame"]: anchors_of(r, min_conf) for r in rows}
    out = {}
    for name, tl in streams.items():
        block = {"quality": stream_quality(tl, fps=fps, n_expected=n_exp,
                                           min_conf=min_conf, warmup=warmup,
                                           anchors=anchors)}
        if manifest is not None:
            cm = continuity.continuity_metrics(tl, manifest, fps=fps)
            block["continuity"] = {k: cm[k] for k in (
                "coverage", "ghost_rate", "drop_episodes", "gap_max_s", "gaps_ge_1s",
                "distinct_ids", "ids_per_dancer", "multi_id_frames", "dup_frames",
                "ghost_frames", "spatial_validity", "acquisition_s", "freeze_share")}
        out[name] = block
    return out


def format_comparison(report: dict) -> str:
    """One line per stream: the headline numbers of the CONT-6 report."""
    def f(v, fmt="{:.3f}"):
        return "n/a" if v is None else fmt.format(v)
    lines = [f"{'stream':8} {'cov':>6} {'ids':>4} {'>N fr':>5} {'gapmax':>6} "
             f"{'jit%h':>6} {'refjit':>6} {'lag ms':>6} {'fastErr':>7} {'idsw':>4} {'coast':>6}"]
    for name, b in report.items():
        q = b["quality"]
        c = b.get("continuity") or {}
        lines.append(
            f"{name:8} {f(c.get('coverage'), '{:.4f}'):>6} {q['distinct_ids']:>4} "
            f"{f(q['over_n_frames'], '{}'):>5} {f(c.get('gap_max_s'), '{:.2f}'):>6} "
            f"{f(q['jitter_rest_pct'], '{:.2f}'):>6} {f(q['ref_jitter_rest_pct'], '{:.2f}'):>6} "
            f"{f(q['lag_ms'], '{:.0f}'):>6} {f(q['fast_err_h'], '{:.3f}'):>7} "
            f"{q['id_switches']:>4} {f(q['coasting_share'], '{:.3f}'):>6}")
    return "\n".join(lines)
