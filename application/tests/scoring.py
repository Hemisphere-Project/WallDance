#!/usr/bin/env python3
"""Field-priority detection scoring (TUNING.md Phase A2 — the trustworthy objective).

Collapses a per-frame *reported-vs-expected* dancer-count timeline into one
weighted scalar **plus a component breakdown**, so detection tuning optimises a
ground-truth objective instead of the old conflated proxies (``avg_detections``,
``zero_detection_frames`` -- a fall in either could be ghost-removal *or*
dancer-loss; only known-N disambiguates).

Field priorities (operator-confirmed, see memory / TUNING.md §2):
  ghosts and drops are the real pains; ID swaps are acceptable (OSC needs
  positions + rough identity only).  So the score is dominated by drop-rate and
  ghost-rate; fragmentation (how often coverage breaks) matters less; ID
  instability matters least.

The "reported" count is the OSC-faithful signal: ``len(tracks)`` returned by
``FrameProcessor.process()`` -- exactly what ``OSCSender.send_frame`` emits.
A track-confirmation warmup (``TRACK_WARMUP_THRESHOLD`` consecutive matches) is
excluded at the window start so an arbitrary mid-recording cut point isn't
penalised for the tracker being cold there.

Pure stdlib: no torch / cv2 import, so it unit-tests instantly and is reusable
by tune.py (Phase C) and the eventual known-N calibration product feature.

CLI:
    python tests/scoring.py --scenario tests/scenarios/residence1-solo_slot4.json \
                            --timeline /tmp/tl_slot4.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

# Lower score = better.  A drop-frame and a ghost-frame cost the same by default
# (both are real field pains); fragmentation and ID instability are secondary.
DEFAULT_WEIGHTS = {
    "drop": 1.0,    # per unit drop_rate   (fraction of dancer-presence under-reported)
    "ghost": 1.0,   # per unit ghost_rate  (spurious reported-dancer-frames per scored frame)
    "frag": 0.3,    # per unit episode density (coverage breaks per scored frame)
    "id": 0.1,      # per unit ID-instability (swaps are acceptable)
}


# --------------------------------------------------------------------------- #
# Manifest helpers
# --------------------------------------------------------------------------- #
def load_scenario(path: str | Path) -> dict:
    """Load and lightly validate a scenario manifest."""
    m = json.loads(Path(path).read_text())
    for req in ("project", "slot", "start", "frames", "expected_count"):
        if req not in m:
            raise ValueError(f"scenario {path}: missing required field '{req}'")
    return m


def expected_at(manifest: dict, rel_frame: int) -> int:
    """Expected dancer count at a window-relative frame index.

    ``expected_count`` is either a constant int, or a list of ranges:
        [{"from": 0, "to": 120, "n": 1}, {"from": 121, "to": 200, "n": 0}, ...]
    where ``from``/``to`` are inclusive window-relative indices.  The first
    matching range wins; unmatched frames fall back to ``default`` (0).
    """
    ec = manifest["expected_count"]
    if isinstance(ec, int):
        return ec
    default = 0
    for rng in ec:
        if "default" in rng:
            default = int(rng["default"])
            continue
        if int(rng["from"]) <= rel_frame <= int(rng["to"]):
            return int(rng["n"])
    return default


def max_expected(manifest: dict) -> int:
    """Largest expected count anywhere in the window (for ID-instability norm)."""
    ec = manifest["expected_count"]
    if isinstance(ec, int):
        return ec
    return max((int(r["n"]) for r in ec if "n" in r), default=0)


# --------------------------------------------------------------------------- #
# Episode counting
# --------------------------------------------------------------------------- #
def _episodes(flags: Sequence[bool]) -> List[List[int]]:
    """Contiguous runs of True -> list of [start_idx, end_idx] (inclusive)."""
    runs: List[List[int]] = []
    start = None
    for i, f in enumerate(flags):
        if f and start is None:
            start = i
        elif not f and start is not None:
            runs.append([start, i - 1])
            start = None
    if start is not None:
        runs.append([start, len(flags) - 1])
    return runs


# --------------------------------------------------------------------------- #
# Core scoring
# --------------------------------------------------------------------------- #
def score_timeline(
    timeline: List[dict],
    manifest: dict,
    weights: Optional[Dict[str, float]] = None,
) -> dict:
    """Score a per-frame reported-count timeline against a scenario manifest.

    Args:
        timeline: list of ``{"frame": <window-relative int>, "reported": <int>,
                  "ids": [<int>, ...] (optional)}``.  Order-independent; sorted
                  by ``frame`` internally.
        manifest: a loaded scenario dict (``expected_count``, ``warmup``,
                  ``fps``).
        weights: override for ``DEFAULT_WEIGHTS``.

    Returns a dict with the scalar ``score`` (lower=better) and a full
    ``components`` breakdown so a search can see *what* moved.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    warmup = int(manifest.get("warmup", 0))
    fps = float(manifest.get("fps", 0) or 0)

    rows = sorted(timeline, key=lambda r: r["frame"])
    scored = [r for r in rows if r["frame"] >= warmup]

    n_scored = len(scored)
    rel_frames = [r["frame"] for r in scored]   # window-relative frame per scored index
    deficits, excesses = [], []
    drop_flags, ghost_flags = [], []
    expected_frames = 0  # sum of N_f over scored frames (dancer-presence frames)
    abs_err_sum = 0
    id_seq: List[Optional[int]] = []   # primary id per scored frame (min id), None if reported==0
    reported_flags: List[bool] = []
    distinct_ids = set()

    for r in scored:
        rel = r["frame"]
        rep = int(r["reported"])
        n = expected_at(manifest, rel)
        expected_frames += n
        deficit = max(0, n - rep)
        excess = max(0, rep - n)
        deficits.append(deficit)
        excesses.append(excess)
        drop_flags.append(deficit > 0)
        ghost_flags.append(excess > 0)
        reported_flags.append(rep > 0)
        abs_err_sum += abs(rep - n)
        ids = r.get("ids") or []
        for i in ids:
            distinct_ids.add(int(i))
        id_seq.append(min(int(i) for i in ids) if ids else None)

    missed_frames = sum(deficits)
    ghost_frames_total = sum(excesses)
    reported_frames = sum(reported_flags)

    drop_rate = missed_frames / expected_frames if expected_frames else 0.0
    ghost_rate = ghost_frames_total / n_scored if n_scored else 0.0

    drop_eps = _episodes(drop_flags)
    ghost_eps = _episodes(ghost_flags)
    n_drop_eps, n_ghost_eps = len(drop_eps), len(ghost_eps)
    frag_rate = (n_drop_eps + n_ghost_eps) / n_scored if n_scored else 0.0

    longest_drop = max((e[1] - e[0] + 1 for e in drop_eps), default=0)

    # Map episode indices (positions in the scored array) back to actual
    # window-relative frame numbers, robust to the warmup offset and any gaps.
    def _span_rel(ep):
        return [rel_frames[ep[0]], rel_frames[ep[1]]]

    drop_spans_rel = [_span_rel(e) for e in drop_eps]
    drop_spans_abs = [[manifest["start"] + a, manifest["start"] + b]
                      for a, b in drop_spans_rel]

    # ID instability (lightly weighted, BOUNDED so swaps -- which are acceptable
    # -- can never dominate the drop/ghost signal).  Two bounded sub-terms:
    #   * fragmentation fraction: share of distinct ids beyond the expected max.
    #   * switch rate: primary-id changes between *consecutive reported* frames
    #     (a true mid-presence swap), normalised by reported frames.  A new id
    #     after a drop gap is NOT counted here -- that's already paid as a drop.
    max_n = max_expected(manifest)
    excess_ids = max(0, len(distinct_ids) - max_n)
    frag_fraction = excess_ids / len(distinct_ids) if distinct_ids else 0.0
    id_switches = 0
    for k in range(1, len(scored)):
        a, b = id_seq[k - 1], id_seq[k]
        if a is not None and b is not None and a != b:
            id_switches += 1
    switch_rate = id_switches / reported_frames if reported_frames else 0.0
    id_pen = frag_fraction + switch_rate

    score = (
        w["drop"] * drop_rate
        + w["ghost"] * ghost_rate
        + w["frag"] * frag_rate
        + w["id"] * id_pen
    )

    def _sec(frames: int) -> float:
        return round(frames / fps, 2) if fps else 0.0

    return {
        "score": round(score, 5),
        "scenario": manifest.get("name", manifest.get("project")),
        "weights": w,
        "components": {
            "drop_rate": round(drop_rate, 5),
            "ghost_rate": round(ghost_rate, 5),
            "frag_rate": round(frag_rate, 5),
            "id_pen": round(id_pen, 5),
            "weighted": {
                "drop": round(w["drop"] * drop_rate, 5),
                "ghost": round(w["ghost"] * ghost_rate, 5),
                "frag": round(w["frag"] * frag_rate, 5),
                "id": round(w["id"] * id_pen, 5),
            },
        },
        "raw": {
            "scored_frames": n_scored,
            "reported_frames": reported_frames,
            "warmup_excluded": warmup,
            "expected_dancer_frames": expected_frames,
            "missed_dancer_frames": missed_frames,
            "missed_dancer_seconds": _sec(missed_frames),
            "ghost_dancer_frames": ghost_frames_total,
            "ghost_dancer_seconds": _sec(ghost_frames_total),
            "mean_abs_count_error": round(abs_err_sum / n_scored, 5) if n_scored else 0.0,
            "drop_episodes": n_drop_eps,
            "ghost_episodes": n_ghost_eps,
            "longest_drop_frames": longest_drop,
            "longest_drop_seconds": _sec(longest_drop),
            "distinct_ids": len(distinct_ids),
            "excess_ids": excess_ids,
            "id_switches": id_switches,
            # window-relative episode spans, plus absolute frames for eyeballing
            "drop_episode_spans_rel": drop_spans_rel,
            "drop_episode_spans_abs": drop_spans_abs,
        },
    }


def score_multi(
    scenario_timelines: List[tuple],
    weights: Optional[Dict[str, float]] = None,
) -> dict:
    """Aggregate scores across scenarios so settings *generalise* (Phase C3 prep).

    Args:
        scenario_timelines: list of ``(manifest, timeline)`` tuples.

    The aggregate score is the **mean** per-scenario score (equal weight per
    scenario, so a config can't win by overfitting one).  Also reports the
    worst (max) single-scenario score.
    """
    per = []
    for manifest, timeline in scenario_timelines:
        per.append(score_timeline(timeline, manifest, weights))
    scores = [p["score"] for p in per]
    return {
        "mean_score": round(sum(scores) / len(scores), 5) if scores else 0.0,
        "worst_score": round(max(scores), 5) if scores else 0.0,
        "per_scenario": {p["scenario"]: p["score"] for p in per},
        "details": per,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_timeline(path: str | Path) -> List[dict]:
    data = json.loads(Path(path).read_text())
    # Accept either a bare list, or a replay summary dict carrying "per_frame".
    if isinstance(data, dict) and "per_frame" in data:
        data = data["per_frame"]
    return data


def main():
    ap = argparse.ArgumentParser(description="Score a reported-count timeline against a scenario")
    ap.add_argument("--scenario", required=True, help="scenario manifest JSON")
    ap.add_argument("--timeline", required=True,
                    help="timeline JSON (list of {frame,reported,ids} or replay summary with per_frame)")
    ap.add_argument("--weights", default=None,
                    help='JSON weight overrides, e.g. \'{"drop":2.0,"ghost":1.0}\'')
    args = ap.parse_args()

    manifest = load_scenario(args.scenario)
    timeline = _load_timeline(args.timeline)
    weights = json.loads(args.weights) if args.weights else None
    result = score_timeline(timeline, manifest, weights)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
