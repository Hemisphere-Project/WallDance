#!/usr/bin/env python3
"""Count-based continuity metrics for a reported-id timeline vs known N.

Designed for "position + rough identity": what the OSC consumer feels.
  coverage        : sum(min(rep,N)) / sum(N)          (1 - drop_rate)
  drop episodes   : runs of frames with rep < N (after warmup)
  gap stats       : median / p90 / max gap (s); gaps >= 0.5 s, >= 1 s
  gaps_per_min    : drop episodes per minute of scene
  mean_run_s      : mean length of fully-covered runs (MTBF-like)
  id_per_dancer   : distinct reported ids / max(N)
  reassoc         : for N==1 scenes: after each drop gap, did the SAME id come back?
  new_id_events   : first appearances of an id (beyond the first N)
  id_switch       : primary id changes between consecutive covered frames (no gap)
"""
from __future__ import annotations
import json, statistics, sys
from pathlib import Path

sys.path.insert(0, "/data/WallDance/application/tests")
import scoring  # noqa: E402


def runs(flags):
    out, s = [], None
    for i, f in enumerate(flags):
        if f and s is None:
            s = i
        elif not f and s is not None:
            out.append((s, i - 1)); s = None
    if s is not None:
        out.append((s, len(flags) - 1))
    return out


def cont_metrics(timeline, manifest, fps=None, warmup=None):
    fps = fps or float(manifest.get("fps", 20) or 20)
    warmup = manifest.get("warmup", 15) if warmup is None else warmup
    rows = sorted(timeline, key=lambda r: r["frame"])
    rows = [r for r in rows if r["frame"] >= warmup]
    N = [scoring.expected_at(manifest, r["frame"]) for r in rows]
    rep = [r["reported"] for r in rows]
    ids = [r.get("ids") or [] for r in rows]
    exp = sum(N)
    cov = sum(min(a, b) for a, b in zip(rep, N)) / exp if exp else 1.0
    drop = [n > 0 and r < n for r, n in zip(rep, N)]
    eps = runs(drop)
    gl = [(b - a + 1) / fps for a, b in eps]
    full = [not d and n > 0 for d, n in zip(drop, N)]
    fr = runs(full)
    maxn = max(N) if N else 0
    distinct = sorted({i for x in ids for i in x})
    # re-association across gaps (meaningful for N == 1)
    reassoc_same = reassoc_new = 0
    for a, b in eps:
        before = ids[a - 1] if a > 0 else []
        after = ids[b + 1] if b + 1 < len(ids) else []
        if not before or not after:
            continue
        if set(after) & set(before):
            reassoc_same += 1
        else:
            reassoc_new += 1
    switches = 0
    for k in range(1, len(rows)):
        if ids[k - 1] and ids[k] and not drop[k] and not drop[k - 1]:
            if min(ids[k - 1]) != min(ids[k]):
                switches += 1
    ghost = sum(max(0, r - n) for r, n in zip(rep, N)) / len(rows) if rows else 0
    dur_min = len(rows) / fps / 60
    return {
        "frames": len(rows), "coverage": round(cov, 4), "ghost_rate": round(ghost, 4),
        "drop_eps": len(eps),
        "gaps_per_min": round(len(eps) / dur_min, 2) if dur_min else 0,
        "gap_med_s": round(statistics.median(gl), 2) if gl else 0,
        "gap_p90_s": round(sorted(gl)[int(0.9 * (len(gl) - 1))], 2) if gl else 0,
        "gap_max_s": round(max(gl), 2) if gl else 0,
        "gaps_ge_0.5s": sum(g >= 0.5 for g in gl), "gaps_ge_1s": sum(g >= 1.0 for g in gl),
        "mean_run_s": round(statistics.mean([(b - a + 1) / fps for a, b in fr]), 2) if fr else 0,
        "distinct_ids": len(distinct), "id_per_dancer": round(len(distinct) / maxn, 2) if maxn else None,
        "reassoc_same": reassoc_same, "reassoc_new": reassoc_new,
        "id_switch_no_gap": switches,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--timeline", required=True)
    a = ap.parse_args()
    m = scoring.load_scenario(a.scenario)
    tl = json.loads(Path(a.timeline).read_text())
    if isinstance(tl, dict):
        tl = tl.get("per_frame") or tl.get("rows")
    print(json.dumps(cont_metrics(tl, m), indent=1))
