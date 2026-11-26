#!/usr/bin/env python3
"""The settings swipe's two gates (Plan Cordée P5), on recorded takes, with the shipped ``core.swipe`` code.

Gate 1 -- façade (truth): on each take, the setting the swipe picks is within ``--tol`` (1.5) points of truth recall
of the best candidate.  The swipe collects the way the app does: from a random start time, every frame is offered
to ``FrameCollector`` (the live setting's boxes, the live foreground) until it holds ``frames`` lost frames
(the live setting left a dancer group bare, >= ``min_gap_s`` apart); the candidates are scored on the kept frames
(``score_frame``) and ``decide`` names the pick.  ``--draws`` draws per take; a take too short to fill the
window is walked round again, up to 10 times (the clock runs on, as if the scene went on as recorded).  Green when,
on every take, the mean truth recall of the pick over the draws is within ``--tol`` of the best (the hub note's
measure); the share of draws more than ``--tol`` under the best and the worst pick are printed beside it, and
``--max-miss`` (default 1.0: off) also gates on that share.  « keep » and « no verdict » count as the live setting
(the app keeps what runs).  The median dancing time a window took to collect is printed too.

Gate 2 -- corpus (scenario score, known N): on each take, the swipe never names a setting whose scenario score
(``scoring.score_timeline``: drop + ghost + 0.3 fragmentation + 0.1 identity, lower is better) is worse than the
live setting's.  Same collection (the corpus takes are 15-130 s: most are walked round several times, so every
swipe gets its full window).  Green when no draw on any take names a worse setting.

Records (one per setting per take; the live setting first on the command line):

  ``<name>.jsonl``  one JSON object per processed frame, in frame order:
      ``frame``  int, the frame index (the same index across the settings of one take);
      ``t``      float seconds (optional; default ``frame / --fps``);
      ``low``    every YOLO box down to conf 0.02, ``[x0, y0, x1, y1, conf, n_keypoints]``, original px;
      ``fg``     the frame's foreground (only the live setting's is read): ``v`` valid (bool), ``ball`` every
                 group ``[x, y, area, bx0, by0, bx1, by1]`` (or ``b``: ``[x, y, area]``, the largest six), ``mx``
                 ``[x0, y0, ds]`` the mask's origin (original px) and downscale.
  ``<name>.masks.npz`` (beside the live record; optional): ``frame`` (n,), ``shape`` (h, w), ``packed`` (n, k) =
      ``np.packbits`` of each valid frame's uint8 mask.  Without it the referee falls back to group centres.
  ``<name>.info.json``: the setting -- ``model`` / ``imgsz`` / ``config.model`` / ``config.yolo_imgsz`` /
      ``config.yolo_shape``; for gate 2 also ``score`` (the scenario score) and ``scenario``.

Writers on mgr-ran: ``tmp_analysis/night-1126/scripts/cx_replay_n.py`` (the façade takes; ``fg.b`` only),
``tmp_analysis/swipe-1126/core/drv_facade.py`` and ``drv_corpus.py`` (+ ``ball``, ``mx`` and the masks).

Usage (from ``application/``):

  python tests/swipe_gate.py \\
      --facade "mur2 6" REC/jeudi0809_mur2__slot_6_..x1280m.jsonl P3/jeudi0809_mur2__slot_6_..l1920.jsonl ... \\
      --corpus texture-duo REC/corpus/texture-duo.x1280.jsonl REC/corpus/texture-duo.l1280.jsonl \\
      --truth-dir tests/truth0810 [--draws 2000] [--seed 1] [--json out.json]

Exit status: 0 both gates green, 1 a gate red, 2 bad input.  Does not import ``replay.py`` (no GPU).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent / "src", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.input_shape import parse_shape  # noqa: E402
from core.swipe import (Candidate, FrameCollector, Observation, SwipeParams, Tally, decide,  # noqa: E402
                        score_frame)


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
        with open(self.path) as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    break
                f = int(r["frame"])
                row = {"t": float(r["t"]) if r.get("t") is not None else f / fps,
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
        if fg.get("ball") is not None:
            groups = [(float(g[0]), float(g[1]), float(g[2])) for g in fg["ball"]]
        else:
            groups = [(float(g[0]), float(g[1]), float(g[2])) for g in fg.get("b") or []]
        groups.sort(key=lambda g: -g[2])
        mask, (x0, y0, ds) = None, (fg.get("mx") or [0.0, 0.0, 4])
        if with_mask and self.masks is not None and f in self.masks[0]:
            idx, shape, packed = self.masks
            mask = np.unpackbits(packed[idx[f]])[: shape[0] * shape[1]].reshape(shape)
        return Observation(groups=groups, mask=mask, x0=float(x0), y0=float(y0), ds=int(ds))


class Take:
    """The settings of one take, the live one first, scored once per frame."""

    def __init__(self, label: str, paths: List[str], fps: float = 20.0):
        self.label = label
        self.live = Record(paths[0], fps, live=True)
        self.records = [self.live] + [Record(p, fps) for p in paths[1:]]
        self.cands = [r.candidate for r in self.records]
        if len(set(self.cands)) != len(self.cands):
            raise SystemExit(f"{label}: two records of the same setting")
        common = set(self.live.frames)
        for r in self.records[1:]:
            common &= set(r.frames)
        self.order = sorted(common)
        self._scores: Dict[tuple, Dict[int, dict]] = {}

    def offers(self, params: SwipeParams):
        """(frame, t, groups-only observation or None, live boxes) for every frame, in order."""
        out = []
        for f in self.order:
            obs = self.live.observation(f, with_mask=False)
            out.append((f, self.live.frames[f]["t"], obs, self.live.frames[f]["low"]))
        return out

    def scores(self, params: SwipeParams) -> Dict[int, dict]:
        """frame -> {candidate: FrameScore} on every frame with a valid foreground and a dancer group."""
        key = (params.group_min_area, params.box_margin, params.conf, params.loose_min_area,
               getattr(params, "one_box_one_dancer", None))
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


# ── one swipe ────────────────────────────────────────────────────────────────

def swipe_once(take: Take, params: SwipeParams, start: int, offers=None, scores=None, laps: int = 10):
    """Offer the take's frames from ``start`` until the collector is ready -- a take too short for the window is
    walked round again (at most ``laps`` times, the clock running on: the scene goes on as recorded) -- then
    score the kept frames and decide (on what was kept, ready or not)."""
    offers = offers if offers is not None else take.offers(params)
    scores = scores if scores is not None else take.scores(params)
    col = FrameCollector(params)
    n = len(offers)
    span = offers[-1][1] - offers[0][1] + (offers[1][1] - offers[0][1] if n > 1 else 0.05)
    t0 = t_end = offers[start % n][1]
    on_wall = 0           # frames with a dancer group offered: the dancing time the window took
    for k in range(max(1, laps) * n):
        j = (start + k) % n
        f, t, obs, boxes = offers[j]
        t_end = t + span * ((start + k) // n)
        on_wall += obs is not None and bool(obs.dancer_groups(params))
        col.offer(t_end, f, obs, boxes)
        if col.ready:
            break
    col.elapsed_s = t_end - t0
    col.on_wall_frames = on_wall
    tallies = {c: Tally() for c in take.cands}
    for fr in col.frames:
        for c in take.cands:
            tallies[c].add(scores[fr.image][c], lost=fr.lost)
    return decide(tallies, take.live.candidate, params, lost_share=col.lost_share), col


def draws(take: Take, params: SwipeParams, n: int, seed: int):
    rng = random.Random(seed)
    offers, scores = take.offers(params), take.scores(params)
    out = []
    for _ in range(n):
        res, col = swipe_once(take, params, rng.randrange(len(offers)), offers, scores)
        out.append((res, col.lost_frames, len(col.frames), col.elapsed_s))
    return out


def _verdict(res) -> str:
    return "switch" if res.switch else ("keep" if res.winner is not None else "none")


# ── gate 1 ───────────────────────────────────────────────────────────────────

def truth_recall(take: Take, truth_dir: Path) -> Dict[Candidate, float]:
    import truth_score as ts
    out = {}
    for r in take.records:
        stem = r.path.name.split(".")[0]
        T = ts.load_truth(truth_dir / f"{stem}.npz")
        Z = ts.score_replay(T, ts.read_jsonl(r.path), conf=0.15)
        out[r.candidate] = 100.0 * ts.take_summary(T, Z)["det"]["wall"]
    return out


def gate1(takes: List[Take], truth: Dict[str, Dict[Candidate, float]], params: SwipeParams, n: int, seed: int,
          tol: float, max_miss: float):
    rows, green = [], True
    for take in takes:
        tr = truth[take.label]
        best = max(tr.values())
        res = draws(take, params, n, seed)
        picks = [tr[r.winner] if r.winner is not None else tr[take.live.candidate] for r, _, _, _ in res]
        miss = float(np.mean([p < best - tol for p in picks]))
        mean = float(np.mean(picks))
        ok = mean >= best - tol and miss <= max_miss
        green &= ok
        counts: Dict[str, int] = {}
        for r, _, _, _ in res:
            k = r.winner.label if (r.winner is not None and r.switch) else _verdict(r)
            counts[k] = counts.get(k, 0) + 1
        rows.append({"take": take.label, "best": round(best, 1), "pick_mean": round(mean, 2),
                     "miss_share": round(miss, 4), "worst": round(min(picks), 1), "green": ok,
                     "picks": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                     "truth": {c.label: round(v, 1) for c, v in tr.items()},
                     "frames_mean": round(float(np.mean([k for _, _, k, _ in res])), 1),
                     "collect_s_median": round(float(np.median([e for _, _, _, e in res])), 1)})
    return green, rows


# ── gate 2 ───────────────────────────────────────────────────────────────────

def gate2(takes: List[Take], params: SwipeParams, n: int, seed: int):
    rows, green = [], True
    for take in takes:
        score = {}
        for r in take.records:
            if "score" not in r.info:
                raise SystemExit(f"{r.path}: no scenario score in its .info.json")
            score[r.candidate] = float(r.info["score"])
        live = take.live.candidate
        res = draws(take, params, n, seed)
        worse = 0
        counts: Dict[str, int] = {}
        for r, _, _, _ in res:
            named = r.winner if (r.winner is not None and r.switch) else None
            if named is not None and score[named] > score[live]:
                worse += 1
            k = named.label if named is not None else _verdict(r)
            counts[k] = counts.get(k, 0) + 1
        ok = worse == 0
        green &= ok
        rows.append({"take": take.label, "scores": {c.label: round(v, 3) for c, v in score.items()},
                     "worse_named": worse, "green": ok, "verdicts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                     "lost_frames_mean": round(float(np.mean([k for _, k, _, _ in res])), 1)})
    return green, rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--facade", nargs="+", action="append", default=[], metavar="LABEL REC",
                    help="a façade take: its label, then its records, the live setting first")
    ap.add_argument("--corpus", nargs="+", action="append", default=[], metavar="LABEL REC",
                    help="a corpus take: its label, then its records, the live setting first")
    ap.add_argument("--truth-dir", default=str(HERE / "truth0810"))
    ap.add_argument("--draws", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--fps", type=float, default=20.0, help="clock of records without 't'")
    ap.add_argument("--tol", type=float, default=1.5, help="gate 1: points of truth recall under the best")
    ap.add_argument("--max-miss", type=float, default=1.0,
                    help="gate 1: share of draws allowed beyond --tol (default 1.0: reported, not gated)")
    ap.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                    help="override a SwipeParams field (exploration; the gate is the defaults)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    params = SwipeParams()
    for kv in a.sets:
        k, _, v = kv.partition("=")
        if not hasattr(params, k):
            print(f"unknown SwipeParams field {k}", file=sys.stderr)
            return 2
        setattr(params, k, type(getattr(params, k))(json.loads(v)))
    if not a.facade and not a.corpus:
        print("nothing to check: give --facade and/or --corpus", file=sys.stderr)
        return 2
    out = {"params": vars(params), "draws": a.draws, "seed": a.seed}
    green = True
    if a.facade:
        takes = [Take(x[0], x[1:], a.fps) for x in a.facade]
        truth = {t.label: truth_recall(t, Path(a.truth_dir)) for t in takes}
        g1, rows = gate1(takes, truth, params, a.draws, a.seed, a.tol, a.max_miss)
        green &= g1
        out["gate1"] = {"green": g1, "rows": rows}
        print(f"Gate 1 -- façade: the pick's truth recall against the best ({a.draws} windows per take, "
              f"{params.frames} lost frames >= {params.min_gap_s} s apart from a random start)")
        print(f"  {'take':10s} {'best':>6s} {'pick':>6s} {'>tol':>6s} {'worst':>6s} {'collect':>8s}  picks")
        for r in rows:
            print(f"  {r['take']:10s} {r['best']:6.1f} {r['pick_mean']:6.2f} {100 * r['miss_share']:5.1f}% "
                  f"{r['worst']:6.1f} {r['collect_s_median']:7.0f}s  {r['picks']}  {'GREEN' if r['green'] else 'RED'}")
        print("  (truth: " + "; ".join(f"{r['take']}: " + ", ".join(f"{k} {v}" for k, v in r['truth'].items())
                                     for r in rows) + ")")
    if a.corpus:
        takes = [Take(x[0], x[1:], a.fps) for x in a.corpus]
        g2, rows = gate2(takes, params, a.draws, a.seed)
        green &= g2
        out["gate2"] = {"green": g2, "rows": rows}
        print(f"Gate 2 -- corpus: never name a setting the scenario scores worse than the live one "
              f"({a.draws} windows per take)")
        for r in rows:
            print(f"  {r['take']:20s} scores {r['scores']}  worse named {r['worse_named']}  "
                  f"lost frames {r['lost_frames_mean']}  {r['verdicts']}  {'GREEN' if r['green'] else 'RED'}")
    print("GATES GREEN" if green else "GATE RED")
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1, default=str))
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main())
