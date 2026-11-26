#!/usr/bin/env python3
"""The settings swipe's gates (Plan Cordee P5), on recorded takes, with the shipped ``core.swipe`` -- per swipe, as
the app runs it.

Gate 1 -- facade (truth).  For every take and every LIVE setting among the take's records:

* the candidate set is the planner's (``plan_set``, the rule ``core.swipe_runner.plan_candidates`` follows since
  the P5 review): the dancer's height at the live input (``--dancer-px``, else measured: the median height of the
  live setting's confident boxes on the wall, times the input's letterbox scale) >= 200 px -> the live setting and
  the other model at the live input; under 200 px -> the live setting and both models at the LARGEST input among
  the take's records.  ``--pair`` gates the background swipe's set instead (P6): the live setting and the other
  model at the live input, whatever the height;
* the operator's excluded cells are dropped from every setting's boxes (the pipeline drops them before tracking);
* the collection is the app's: from a random start, every frame offered to ``FrameCollector`` with the live
  boxes at the project's confidence (``--conf``), until it is ready or ``--timeout-s`` passed (190 s: 150 frames
  1 s apart need two and a half minutes of dancing -- the controller's collect timeout must allow it); every
  planned setting scored on the kept frames; ``decide``;
* ``--draws`` (2,000) swipes; truth recall per setting from ``truth_score`` at the same confidence.

Clauses, per take x live setting (``best`` = the best truth in the planned set):

  (i)   a setting whose truth is below the live one's is named on at most 1 % of swipes;
  (ii)  a named setting is within 1.5 points of ``best`` on at least 95 % of the swipes that name one;
  (iii) when ``best`` is 10 points or more above the live setting, it or one within 1.5 points of it is named on
        at least 95 % of swipes;
  (iv)  reported, not gated: when ``best`` is 1.5 to 10 points above the live setting, the share of swipes that
        keep it (a real gain missed).

Gate 2 -- corpus (scenario score, known N): on each take, the swipe never names a setting whose scenario score is
worse than the live setting's (``--corpus``; same collection, the live setting first, no truth).  Every corpus
plate on mgr-ran is a median with dancers in it: this gate passes by refusal (« no verdict »).

Records (one per setting per take; the take's FIRST record carries the foreground):

  ``<name>.jsonl``  one JSON object per processed frame, in frame order: ``frame``; ``t`` (optional, default
      ``frame / --fps``; a record whose info says ``clock: uniform`` with an ``actual_fps`` is re-timed to that
      rate); ``low`` = every YOLO box down to conf 0.02, ``[x0, y0, x1, y1, conf, n_keypoints]``, original
      px; on the first record ``fg``: ``v`` valid, ``ball`` every group ``[x, y, area, bx0, by0, bx1, by1]`` (or
      ``b``: ``[x, y, area]``), ``mx`` ``[x0, y0, ds]``.
  ``<name>.masks.npz`` (beside the first record; optional): ``frame``, ``shape``, ``packed`` = ``np.packbits`` of
      each valid frame's mask.  Without it the referee falls back to group centres.
  ``<name>.info.json``: the setting (``model`` / ``imgsz`` / ``config.yolo_shape``); in ``config`` the ROI and the
      exclusion (``exclusion_grid``, ``exclusion_cells``, ``exclusion_manual_add``, ``exclusion_manual_remove``);
      for gate 2 also ``score``.

Writers on mgr-ran: ``tmp_analysis/night-1126/scripts/cx_replay_n.py``, ``tmp_analysis/swipe-1126/core/
drv_facade.py`` / ``drv_corpus.py``.  ``tests/test_swipe_records.py`` runs gate 1 when the records are present.

  python tests/swipe_gate.py --facade "mur2 6" REC/<stem>.x1280m.jsonl REC/<stem>.l1280.jsonl P3/<stem>.x1760.jsonl \\
      ... --truth-dir tests/truth0810 [--pair] [--live "l @ 1280"] [--draws 2000] [--conf 0.15] [--json out.json]

Exit status: 0 green, 1 a clause red, 2 bad input.  Does not import ``replay.py`` (no GPU).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent / "src", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.input_shape import letterbox_scale, parse_shape, target_hw  # noqa: E402
from core.swipe import (Candidate, FrameCollector, Observation, SwipeParams, Tally, decide,  # noqa: E402
                        score_frame)

LARGE_NET_PX = 200.0          # the planner's: over this the input size no longer matters
TIMEOUT_S = 190.0             # the controller must wait this long to collect 150 frames 1 s apart (it waited 120 s)


# ── records ──────────────────────────────────────────────────────────────────

def _info(path: Path) -> dict:
    p = path.with_name(path.name[: -len(".jsonl")] + ".info.json")
    return json.loads(p.read_text()) if p.exists() else {}


def candidate_of(info: dict, fallback: str) -> Candidate:
    cfg = info.get("config") or {}
    model = cfg.get("model") or info.get("model")
    imgsz = cfg.get("yolo_imgsz") or info.get("imgsz")
    if not model or not imgsz:
        raise SystemExit(f"{fallback}: its .info.json names no model / size")
    return Candidate(str(model), int(imgsz), parse_shape(cfg.get("yolo_shape")))


class Record:
    """One setting's replay of one take."""

    def __init__(self, path, fps: float = 20.0, live: bool = False):
        self.path = Path(path)
        self.info = _info(self.path)
        self.candidate = candidate_of(self.info, str(self.path))
        self.frames: Dict[int, dict] = {}
        # a replay written on a uniform 20 fps clock (``clock`` "uniform") is re-timed to the camera's real rate:
        # the swipe's « 1 s apart » is wall-clock time in the app
        real = self.info.get("actual_fps") if self.info.get("clock") == "uniform" else None
        with open(self.path) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    break
                f = int(r["frame"])
                row = {"t": f / float(real) if real else (float(r["t"]) if r.get("t") is not None else f / fps),
                       "low": [tuple(float(v) for v in b[:5]) for b in r.get("low") or []]}
                if live:
                    row["fg"] = r.get("fg")
                self.frames[f] = row
        self.masks = None
        mp = self.path.with_name(self.path.name[: -len(".jsonl")] + ".masks.npz")
        if live and mp.exists():
            z = np.load(mp)
            self.masks = ({int(f): i for i, f in enumerate(z["frame"])}, tuple(int(v) for v in z["shape"]), z["packed"])

    def observation(self, f: int, with_mask: bool = True) -> Optional[Observation]:
        fg = self.frames[f].get("fg") if f in self.frames else None
        if not fg or not fg.get("v"):
            return None
        group_boxes = None
        if fg.get("ball") is not None:
            ball = sorted(fg["ball"], key=lambda g: -float(g[2]))
            groups = [(float(g[0]), float(g[1]), float(g[2])) for g in ball]
            if all(len(g) >= 7 for g in ball):
                group_boxes = [tuple(float(v) for v in g[3:7]) for g in ball]
        else:
            groups = sorted(((float(g[0]), float(g[1]), float(g[2])) for g in fg.get("b") or []), key=lambda g: -g[2])
        mask, (x0, y0, ds) = None, (fg.get("mx") or [0.0, 0.0, 4])
        if with_mask and self.masks is not None and f in self.masks[0]:
            idx, shape, packed = self.masks
            mask = np.unpackbits(packed[idx[f]])[: shape[0] * shape[1]].reshape(shape)
        return Observation(groups=groups, mask=mask, x0=float(x0), y0=float(y0), ds=int(ds), group_boxes=group_boxes)


def exclusion_filter(info: dict):
    """The operator's excluded cells as the pipeline applies them before tracking (``FrameProcessor.
    _apply_exclusion`` via ``ExclusionMaskBuilder.excluded``): a box whose centre, normalised over the ROI, falls in
    an effective cell ``(cells | manual_add) - manual_remove`` of the ``exclusion_grid`` (cols, rows) is dropped.
    The pipeline's exception for a box near a recently updated track is not mirrored (no tracks here).  None when
    the config names no cell."""
    cfg = info.get("config") or {}
    grid = cfg.get("exclusion_grid")
    cells = ({tuple(c) for c in cfg.get("exclusion_cells") or []}
             | {tuple(c) for c in cfg.get("exclusion_manual_add") or []}) \
        - {tuple(c) for c in cfg.get("exclusion_manual_remove") or []}
    if not grid or not cells or not cfg.get("roi_w"):
        return None
    gx, gy = int(grid[0]), int(grid[1])
    x0, y0, w, h = float(cfg.get("roi_x", 0)), float(cfg.get("roi_y", 0)), float(cfg["roi_w"]), float(cfg["roi_h"])

    def keep(b) -> bool:
        nx, ny = ((b[0] + b[2]) * 0.5 - x0) / w, ((b[1] + b[3]) * 0.5 - y0) / h
        return not (0.0 <= nx < 1.0 and 0.0 <= ny < 1.0 and (int(nx * gx), int(ny * gy)) in cells)
    return keep


class Take:
    """The settings of one take (the first record carries the foreground), boxes filtered by the exclusion."""

    def __init__(self, label: str, paths: List[str], fps: float = 20.0, exclusion: bool = True):
        self.label = label
        self.live = Record(paths[0], fps, live=True)
        self.records = [self.live] + [Record(p, fps) for p in paths[1:]]
        self.cands = [r.candidate for r in self.records]
        if len(set(self.cands)) != len(self.cands):
            raise SystemExit(f"{label}: two records of the same setting")
        self.by = {r.candidate: r for r in self.records}
        keep = exclusion_filter(self.live.info) if exclusion else None
        if keep is not None:
            for r in self.records:
                for fr in r.frames.values():
                    fr["low"] = [b for b in fr["low"] if keep(b)]
        common = set(self.live.frames)
        for r in self.records[1:]:
            common &= set(r.frames)
        self.order = sorted(common)
        self._scores: Dict[tuple, Dict[int, dict]] = {}
        cfg = self.live.info.get("config") or {}
        self.roi = (int(cfg.get("roi_w") or 0), int(cfg.get("roi_h") or 0))

    def label_of(self, text: str) -> Candidate:
        for c in self.cands:
            if c.label == text:
                return c
        raise SystemExit(f"{self.label}: no record for {text!r} (have {[c.label for c in self.cands]})")

    def scores(self, params: SwipeParams) -> Dict[int, dict]:
        """frame -> {candidate: FrameScore} on every frame with a valid foreground and a dancer group."""
        key = (params.group_min_area, params.box_margin, params.conf, params.loose_min_area,
               params.one_box_one_dancer, params.cover_share)
        if key not in self._scores:
            out = {}
            for f in self.order:
                obs = self.live.observation(f, with_mask=False)
                if obs is None or not obs.dancer_groups(params):
                    continue
                obs = self.live.observation(f)
                out[f] = {r.candidate: score_frame(obs, r.frames[f]["low"], params) for r in self.records}
            self._scores[key] = out
        return self._scores[key]

    def scale(self, c: Candidate) -> float:
        return letterbox_scale(self.roi[0], self.roi[1], c.imgsz, c.shape) if self.roi[0] else 1.0

    def dancer_px(self, live: Candidate, conf: float = 0.5) -> Optional[float]:
        """The dancer's height at the live input: the median height of the live setting's confident boxes."""
        hs = [b[3] - b[1] for f in self.order for b in self.by[live].frames[f]["low"] if b[4] >= conf]
        return float(np.median(hs)) * self.scale(live) if hs else None


def plan_set(take: Take, live: Candidate, dancer_px: Optional[float], pair: bool = False) -> List[Candidate]:
    """The planner's candidates (the rule ``plan_candidates`` follows since the P5 review), among the take's
    records: the live setting first."""
    models = sorted({c.model for c in take.cands})
    here = target_hw(live.imgsz, live.shape)
    other_here = [c for c in take.cands if c != live and target_hw(c.imgsz, c.shape) == here and c.model != live.model]
    if pair or (dancer_px is not None and dancer_px >= LARGE_NET_PX):
        return [live] + other_here[:1]
    out = [live]
    for m in models:
        mine = sorted((c for c in take.cands if c.model == m), key=lambda c: -take.scale(c))
        if mine and mine[0] not in out:
            out.append(mine[0])
    return out


# ── one swipe ────────────────────────────────────────────────────────────────

def offers_for(take: Take, live: Candidate, params: SwipeParams):
    out = []
    for f in take.order:
        obs = take.live.observation(f, with_mask=False)
        boxes = [b for b in take.by[live].frames[f]["low"] if b[4] >= params.conf]
        out.append((f, take.live.frames[f]["t"], obs, boxes))
    return out


def swipe_once(take: Take, live: Candidate, cands: Sequence[Candidate], params: SwipeParams, start: int,
               offers, scores, timeout_s: float = TIMEOUT_S, laps: int = 10):
    """Offer the take's frames from ``start`` (a short take is walked round again, the clock running on) until the
    collector is ready or ``timeout_s`` passed; score the planned settings on the kept frames; decide."""
    col = FrameCollector(params)
    n = len(offers)
    span = offers[-1][1] - offers[0][1] + (offers[1][1] - offers[0][1] if n > 1 else 0.05)
    t0 = None
    for k in range(max(1, laps) * n):
        f, t, obs, boxes = offers[(start + k) % n]
        t = t + span * ((start + k) // n)
        t0 = t if t0 is None else t0
        col.offer(t, f, obs, boxes)
        if col.ready or t - t0 >= timeout_s:
            break
    tallies = {c: Tally() for c in cands}
    for fr in col.frames:
        for c in cands:
            tallies[c].add(scores[fr.image][c], lost=fr.lost)
    return decide(tallies, live, params, lost_share=col.lost_share), col


def truth_recall(take: Take, truth_dir: Path, conf: float = 0.15) -> Dict[Candidate, float]:
    import truth_score as ts
    out = {}
    for r in take.records:
        stem = r.path.name.split(".")[0]
        T = ts.load_truth(truth_dir / f"{stem}.npz")
        Z = ts.score_replay(T, ts.read_jsonl(r.path), conf=conf)
        out[r.candidate] = 100.0 * ts.take_summary(T, Z)["det"]["wall"]
    return out


def clause_row(take: Take, live: Candidate, cands: List[Candidate], truth: Dict[Candidate, float],
               params: SwipeParams, draws: int, seed: int, timeout_s: float = TIMEOUT_S) -> dict:
    rng = random.Random(seed)
    offers = offers_for(take, live, params)
    scores = take.scores(params)
    tl = truth[live]
    best = max(truth[c] for c in cands)
    named, worse, near, keep, none = 0, 0, 0, 0, 0
    counts: Dict[str, int] = {}
    for _ in range(draws):
        res, _col = swipe_once(take, live, cands, params, rng.randrange(len(offers)), offers, scores, timeout_s)
        if res.winner is not None and res.switch:
            w = res.winner
            named += 1
            worse += truth[w] < tl - 1e-9
            near += truth[w] >= best - 1.5
            k = w.label
        elif res.winner is not None:
            keep += 1
            k = "keep"
        else:
            none += 1
            k = "no verdict"
        counts[k] = counts.get(k, 0) + 1
    row = {"take": take.label, "live": live.label, "cands": [c.label for c in cands], "live_truth": round(tl, 1),
           "best": round(best, 1), "draws": draws,
           "worse": worse / draws,                                         # (i)
           "near_of_named": (near / named) if named else None,             # (ii)
           "near_of_all": near / draws,                                    # (iii) when best >= live + 10
           "keep": keep / draws, "none": none / draws,
           "named": dict(sorted(counts.items(), key=lambda kv: -kv[1]))}
    gap = best - tl
    row["clause_i"] = row["worse"] <= 0.01
    row["clause_ii"] = named == 0 or row["near_of_named"] >= 0.95
    row["clause_iii"] = (gap < 10.0) or row["near_of_all"] >= 0.95
    row["missed_small_gain"] = row["keep"] + row["none"] if 1.5 < gap < 10.0 else None   # (iv), not gated
    row["green"] = row["clause_i"] and row["clause_ii"] and row["clause_iii"]
    return row


def gate1(takes: List[Take], truth_dir: Path, params: SwipeParams, draws: int, seed: int, pair: bool,
          lives: Optional[Sequence[str]] = None, dancer_px: Optional[float] = None, timeout_s: float = TIMEOUT_S):
    rows, green = [], True
    for take in takes:
        truth = truth_recall(take, truth_dir, params.conf)
        for live in take.cands:
            if lives and live.label not in lives:
                continue
            h = dancer_px * take.scale(live) / take.scale(take.cands[0]) if dancer_px else take.dancer_px(live)
            cands = plan_set(take, live, h, pair)
            if len(cands) < 2:
                continue
            row = clause_row(take, live, cands, truth, params, draws, seed, timeout_s)
            row["dancer_px"] = round(h, 1) if h else None
            rows.append(row)
            green &= row["green"]
    return green, rows


def gate2(takes: List[Take], params: SwipeParams, draws: int, seed: int, timeout_s: float = TIMEOUT_S):
    rows, green = [], True
    for take in takes:
        score = {}
        for r in take.records:
            if "score" not in r.info:
                raise SystemExit(f"{r.path}: no scenario score in its .info.json")
            score[r.candidate] = float(r.info["score"])
        live = take.cands[0]
        rng = random.Random(seed)
        offers, scores = offers_for(take, live, params), take.scores(params)
        worse, counts = 0, {}
        for _ in range(draws):
            res, _col = swipe_once(take, live, take.cands, params, rng.randrange(len(offers)), offers, scores,
                                   timeout_s=1e9)
            named = res.winner if (res.winner is not None and res.switch) else None
            worse += named is not None and score[named] > score[live]
            k = named.label if named is not None else ("keep" if res.winner is not None else "no verdict")
            counts[k] = counts.get(k, 0) + 1
        ok = worse == 0
        green &= ok
        rows.append({"take": take.label, "scores": {c.label: round(v, 3) for c, v in score.items()},
                     "worse_named": worse, "green": ok, "verdicts": counts})
    return green, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--facade", nargs="+", action="append", default=[], metavar="LABEL REC",
                    help="a facade take: its label, then its records (the first carries the foreground)")
    ap.add_argument("--corpus", nargs="+", action="append", default=[], metavar="LABEL REC",
                    help="a corpus take: its label, then its records, the live setting first")
    ap.add_argument("--truth-dir", default=str(HERE / "truth0810"))
    ap.add_argument("--pair", action="store_true", help="gate the background swipe's pair (P6) instead of the plan")
    ap.add_argument("--live", action="append", default=[], help="only these live settings (labels)")
    ap.add_argument("--dancer-px", type=float, default=None, help="the dancer's height at the FIRST record's input")
    ap.add_argument("--draws", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--conf", type=float, default=0.15, help="the project's confidence (the referee scores there)")
    ap.add_argument("--timeout-s", type=float, default=TIMEOUT_S)
    ap.add_argument("--no-exclusion", action="store_true", help="keep the operator's excluded cells' boxes")
    ap.add_argument("--fps", type=float, default=20.0, help="clock of records without 't'")
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                    help="override a SwipeParams field (exploration; the gate is the defaults)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    params = replace(SwipeParams(), conf=a.conf)
    for kv in a.sets:
        k, _, v = kv.partition("=")
        if not hasattr(params, k):
            print(f"unknown SwipeParams field {k}", file=sys.stderr)
            return 2
        setattr(params, k, type(getattr(params, k))(json.loads(v)))
    if not a.facade and not a.corpus:
        print("nothing to check: give --facade and/or --corpus", file=sys.stderr)
        return 2
    out = {"params": vars(params), "draws": a.draws, "seed": a.seed, "pair": a.pair}
    green = True
    if a.facade:
        takes = [Take(x[0], x[1:], a.fps, exclusion=not a.no_exclusion) for x in a.facade]
        g1, rows = gate1(takes, Path(a.truth_dir), params, a.draws, a.seed, a.pair, a.live, a.dancer_px, a.timeout_s)
        green &= g1
        out["gate1"] = {"green": g1, "rows": rows}
        what = "the background pair" if a.pair else "the planned set"
        print(f"Gate 1 -- facade, per swipe ({a.draws} swipes, {what}, "
              f"conf {a.conf}, {params.frames} frames >= {params.min_gap_s} s apart): (i) worse than live <= 1 %, "
              f"(ii) named within 1.5 of the best >= 95 %, (iii) best named when >= 10 above live >= 95 %")
        for r in rows:
            nn = "-" if r["near_of_named"] is None else f"{100 * r['near_of_named']:.1f}%"
            iv = "" if r["missed_small_gain"] is None else f" (iv) missed {100 * r['missed_small_gain']:.1f}%"
            print(f"  {r['take']:7s} live {r['live']:16s} {r['live_truth']:5.1f} best {r['best']:5.1f} "
                  f"(i) {100 * r['worse']:5.2f}% (ii) {nn:>6s} (iii) {100 * r['near_of_all']:5.1f}%{iv} "
                  f"{r['named']}  {'GREEN' if r['green'] else 'RED'}")
    if a.corpus:
        takes = [Take(x[0], x[1:], a.fps, exclusion=not a.no_exclusion) for x in a.corpus]
        g2, rows = gate2(takes, params, a.draws, a.seed)
        green &= g2
        out["gate2"] = {"green": g2, "rows": rows}
        print(f"Gate 2 -- corpus: never name a setting the scenario scores worse than the live one ({a.draws} swipes)")
        for r in rows:
            print(f"  {r['take']:20s} scores {r['scores']}  worse named {r['worse_named']}  {r['verdicts']}  "
                  f"{'GREEN' if r['green'] else 'RED'}")
    print("GATES GREEN" if green else "GATE RED")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str))
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main())
