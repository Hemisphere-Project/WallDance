#!/usr/bin/env python3
"""The autonomous swipe's gates (Plan Cordee P6): the policy (``core.swipe_auto``) driven by a stream simulator on
recorded takes, with the shipped referee (``core.swipe``) -- hand-run, CPU only, records from mgr-ran.

The simulator plays a take in time order, frame by frame, as the app sees it in RUN, looped for as long as asked
(the clock runs on), optionally with empty stretches (``--gap-every-s`` / ``--gap-s``: nobody on the wall, the
frames offered with no foreground group):

* the live setting's boxes are its record's boxes at the project's confidence, minus the operator's excluded cells
  (as ``swipe_gate.Take`` loads them); they feed ``FrameCollector.offer`` with the frame's foreground (one mask
  record per take serves every setting);
* every ``--check-s`` seconds the controller takes the collector's ``fresh`` frames (``SwipeAuto.fresh``: none an
  earlier run looked at), computes the free pre-filter (``headroom`` from the live setting's own scores, minus
  ``bar_for`` the other model) and asks ``SwipeAuto.due``; a run scores ``SwipeAuto.candidates`` (the live
  setting and the other model at its input) on the newest ``run_frames`` fresh frames with ``decide``, at
  ``referee_params(project confidence)``; it lasts frames x (candidates - 1) x ``--pace-s`` (one candidate
  inference every 0.2 s), then ``ran`` and ``advise``; a probe looks at the newest ``probe_frames``;
* on « apply » the live setting really changes: from then on the live boxes come from the winner's record, the
  collector starts again and ``switched`` is called;
* ``--splice-at-s`` (SYNTHETIC): from that second on, each model's records are swapped with the other model's at
  the same input -- as if the light had turned against the live model -- for the back-off's wake-up, which no
  recorded take exercises (on 08/10 the best setting never changes).

Per stream it counts runs, skipped runs (the pre-filter), probes, « keep » verdicts, runs the wake-up called,
suggestions (and whether each named a setting the truth says is worse than the live one), automatic switches (and
whether each was to a worse setting), flip-flops (a switch back to a setting left earlier), the time of the first
switch, the truth recall per live setting, and after a splice the time to the first run, suggestion and switch.

Gates (exit 1 when red):

* ``--gate stay``:   from the take's best setting (or the given ``--live``): no automatic switch, no suggestion of
  a worse setting;
* ``--gate switch``: from ``--live``: exactly one automatic switch, to ``--expect``, no flip-flop;
* ``--gate silent``: an empty wall: no run, no suggestion, no switch;
* ``--gate quiet``:  an untrusted plate: no suggestion, no switch (runs are how it learns the plate is untrusted:
  after one, ``none_wait_s`` spaces them);
* ``--gate none``:   only the table.

Records: as ``swipe_gate.py`` (the first record of a take carries ``fg`` and has ``<name>.masks.npz`` beside it;
``low`` = every box >= 0.02, original px; ``.info.json`` names the setting, the clock -- ``clock: uniform`` with an
``actual_fps`` is re-timed -- and the ROI and excluded cells).  Writers on mgr-ran: ``tmp_analysis/night-1126/
scripts/cx_replay_n.py``, ``tmp_analysis/swipe-1126/core/drv_facade.py`` / ``drv_corpus.py``.  Does not import
``replay.py``.

  python tests/swipe_auto_gate.py --take "mur2 6" REC/<stem>.x1280m.jsonl REC/<stem>.l1280.jsonl P3/<stem>.x1760.jsonl \\
      ... --truth-dir tests/truth0810 --live "x @ 1920 x 1056" --gate switch --expect "l @ 1920 x 1056" \\
      --mode auto --hours 12 --starts 4
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent / "src", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from core.swipe import Candidate, FrameCollector, SwipeParams, Tally, decide  # noqa: E402
from core.swipe_auto import (MODE_AUTO, AutoParams, SwipeAuto, bar_for, headroom,  # noqa: E402
                             referee_params)
from swipe_gate import Take  # noqa: E402

# The laptop's engine-only p50 (P2, 2026-11-26, ms), what ``advise``'s « slower » rule reads.  l @ 1280 was not
# timed: scaled from l @ 1920 x 1056 by the pixel count.
LAPTOP_MS = {"x @ 1280": 22.3, "x @ 1760 x 960": 23.6, "x @ 1920 x 1056": 29.1, "l @ 1760 x 960": 15.5,
             "l @ 1920 x 1056": 18.8, "l @ 1280": 15.2}


class Stream:
    """One take's settings, foreground, clock and truth per setting and frame."""

    def __init__(self, label: str, paths: List[str], truth_dir: Optional[Path] = None, conf: float = 0.15):
        self.label = label
        self.take = Take(label, paths)
        self.live_rec = self.take.live
        self.order = self.take.order
        info = self.live_rec.info
        self.fps = float(info.get("actual_fps") or info.get("fps") or 20.0)
        self.obs_g = {f: self.live_rec.observation(f, with_mask=False) for f in self.order}
        self.truth = self._truth(truth_dir, conf) if truth_dir is not None else None

    def by_label(self, label: str) -> Candidate:
        return self.take.label_of(label)

    def boxes(self, c: Candidate, f: int, conf: float):
        return [b for b in self.take.by[c].frames[f]["low"] if b[4] >= conf]

    def _truth(self, truth_dir: Path, conf: float):
        """setting -> (n_base[f], n_det[f]) over frame index (truth_score's base: own or shared group, not on the
        roof), at ``conf``; None when the take has no truth file."""
        import truth_score as ts
        stem = self.live_rec.path.name.split(".")[0]
        tp = Path(truth_dir) / f"{stem}.npz"
        if not tp.exists():
            return None
        T = ts.load_truth(tp)
        out = {}
        for c, r in self.take.by.items():
            Z = ts.score_replay(T, ts.read_jsonl(r.path), conf=conf)
            roof = Z["y"] < ts.ROOF[T.size] + 100
            base = (Z["state"] < 2) & ~roof
            fr = Z["frame"].astype(int)
            out[c] = (np.bincount(fr[base], minlength=T.frames).astype(float),
                      np.bincount(fr[base & Z["det"].astype(bool)], minlength=T.frames).astype(float))
        return out

    def recall(self, c: Candidate) -> Optional[float]:
        if self.truth is None:
            return None
        nb, nd = self.truth[c]
        return 100.0 * nd.sum() / nb.sum() if nb.sum() else None


@dataclass
class Event:
    t: float
    kind: str                 # "run" | "probe" | "skip" | "keep" | "suggest" | "apply"
    live: str
    winner: str = ""
    level: str = ""
    gain: float = 0.0
    worse: bool = False       # the named setting's truth is under the live one's
    why: str = ""


@dataclass
class StreamRun:
    events: List[Event] = field(default_factory=list)
    switches: List[Tuple[float, str, str]] = field(default_factory=list)
    det: Dict[str, List[float]] = field(default_factory=dict)
    seconds: float = 0.0
    gpu_s: float = 0.0
    woken: int = 0
    splice_s: Optional[float] = None

    def count(self, kind: str) -> int:
        return sum(e.kind == kind for e in self.events)

    def flip_flops(self) -> int:
        left = set()
        n = 0
        for _, a, b in self.switches:
            n += b in left
            left.add(a)
        return n


def simulate(st: Stream, live0: Candidate, mode: str = MODE_AUTO, ap: Optional[AutoParams] = None,
             sp: Optional[SwipeParams] = None, start: int = 0, hours: float = 1.0, live_conf: float = 0.15,
             check_s: float = 2.0, pace_s: float = 0.2, ms: Optional[Dict[str, float]] = None,
             gap_every_s: float = 0.0, gap_s: float = 0.0, prefilter: bool = True,
             splice_at_s: Optional[float] = None) -> StreamRun:
    """One stream of ``hours`` from frame index ``start`` of the take (looped), ``live0`` live at the start;
    ``splice_at_s``: SYNTHETIC -- from then on each model plays the other model's record at the same input."""
    ap = ap or AutoParams()
    sp = referee_params(live_conf, sp)
    scores = st.take.scores(sp)
    auto = SwipeAuto(mode, ap)
    live = live0
    col = FrameCollector(sp)
    out = StreamRun(splice_s=splice_at_s)
    twin = {c: o for c in st.take.cands for o in st.take.cands
            if o.model != c.model and o.imgsz == c.imgsz and o.shape == c.shape}

    def rec(c: Candidate, at: float) -> Candidate:
        """The record setting ``c`` plays at ``at``: its own, or after the splice its twin's."""
        return twin.get(c, c) if splice_at_s is not None and at >= splice_at_s else c

    n = len(st.order)
    dt = 1.0 / st.fps
    total_s = hours * 3600.0
    ms_c = {c: ms[c.label] for c in st.take.cands if ms and c.label in ms} if ms else None
    tr0 = {c: st.recall(c) for c in st.take.cands}
    t, k, next_check, pending = 0.0, 0, 0.0, None
    next_gap = gap_every_s if gap_every_s > 0 else None
    while t < total_s:
        if next_gap is not None and t >= next_gap:          # nobody on the wall for gap_s
            t_end = t + gap_s
            while t < t_end:
                col.offer(t, None, None, [])
                t += dt
            next_gap = t + gap_every_s
            continue
        f = st.order[(start + k) % n]
        k += 1
        col.offer(t, f, st.obs_g[f], st.boxes(rec(live, t), f, live_conf))
        if st.truth is not None:
            nb, nd = st.truth[rec(live, t)]
            acc = out.det.setdefault(live.label, [0.0, 0.0])
            acc[0] += nb[f]
            acc[1] += nd[f]
        if pending is not None and t >= pending["end"]:
            res, probe = pending["result"], pending["probe"]
            auto.ran(t, probe=probe)
            if not probe:
                adv = auto.advise(t, res, newest_frame_t=pending["newest"], ms=ms_c)
                if res.winner is not None and not res.switch:
                    out.events.append(Event(t, "keep", live.label))
                if adv.kind != "none":
                    row = next((r for r in res.rows if r.candidate == adv.winner), None)
                    tw, tl = tr0[rec(adv.winner, t)], tr0[rec(live, t)]
                    worse = tw is not None and tl is not None and tw < tl
                    out.events.append(Event(t, adv.kind, live.label, adv.winner.label, adv.level,
                                            row.gain if row else 0.0, worse, adv.why_not_auto))
                if adv.kind == "apply":
                    out.switches.append((t, live.label, adv.winner.label))
                    auto.switched(t, live, adv.winner)
                    live = adv.winner
                    col.reset()
            pending = None
        if pending is None and t >= next_check:
            next_check = t + check_s
            fresh = auto.fresh(col.frames_at(t))
            if fresh:
                cands = auto.candidates(st.take.cands, live)
                lost = sum(1 for fr in fresh if fr.lost)
                room = None
                if prefilter and len(cands) > 1:
                    hr = headroom([scores[fr.image][rec(live, fr.t)] for fr in fresh], sp)
                    if hr is not None:
                        room = hr - min(bar_for(live, c, sp) for c in cands[1:])
                newest = max(fr.t for fr in fresh)
                skipped0, woken0 = auto.skipped, auto.woken
                d = auto.due(t, lost, len(fresh) - lost, room=room, newest_frame_t=newest)
                if auto.skipped > skipped0:
                    out.events.append(Event(t, "skip", live.label))
                if auto.woken > woken0:
                    out.events.append(Event(t, "wake", live.label))
                if d in ("run", "probe") and len(cands) > 1:
                    use = sorted(fresh, key=lambda fr: fr.t)[-(ap.run_frames if d == "run" else ap.probe_frames):]
                    tallies = {c: Tally() for c in cands}
                    for fr in use:
                        for c in cands:
                            tallies[c].add(scores[fr.image][rec(c, fr.t)], lost=fr.lost)
                    res = decide(tallies, live, sp, lost_share=col.lost_share)
                    dur = len(use) * (len(cands) - 1) * pace_s
                    out.gpu_s += dur
                    out.events.append(Event(t, d, live.label))
                    pending = {"end": t + dur, "result": res, "probe": d == "probe",
                               "newest": max(fr.t for fr in use)}
        t += dt
    out.seconds = t
    return out


def _row(st: Stream, live: Candidate, runs: List[StreamRun], expect: str = "") -> dict:
    hours = sum(r.seconds for r in runs) / 3600.0
    sw = [s for r in runs for s in r.switches]
    sugg = [e for r in runs for e in r.events if e.kind == "suggest"]
    first = [r.switches[0][0] for r in runs if r.switches]
    worse_sw = sum(1 for r in runs for e in r.events if e.kind == "apply" and e.worse)
    after = {"run": [], "suggest": [], "switch": []}      # after a (synthetic) splice: seconds to each, per stream
    for r in runs:
        if r.splice_s is None:
            continue
        ev = [e for e in r.events if e.t >= r.splice_s]
        for key, kinds in (("run", ("run",)), ("suggest", ("suggest", "apply"))):
            x = next((e.t for e in ev if e.kind in kinds), None)
            after[key].append(None if x is None else round(x - r.splice_s))
        x = next((t for t, _, _ in r.switches if t >= r.splice_s), None)
        after["switch"].append(None if x is None else round(x - r.splice_s))
    rec_after = {}
    for r in runs:
        for lab, (nb, nd) in r.det.items():
            acc = rec_after.setdefault(lab, [0.0, 0.0])
            acc[0] += nb
            acc[1] += nd
    return {"take": st.label, "live": live.label, "streams": len(runs), "hours": round(hours, 1),
            "runs": sum(r.count("run") for r in runs),
            "runs_per_h": round(sum(r.count("run") for r in runs) / hours, 2),
            "keeps": sum(r.count("keep") for r in runs), "wakes": sum(r.count("wake") for r in runs),
            "skipped": sum(r.count("skip") for r in runs),
            "probes": sum(r.count("probe") for r in runs), "gpu_s_per_h": round(sum(r.gpu_s for r in runs) / hours, 1),
            "suggestions": len(sugg), "suggest_worse": sum(e.worse for e in sugg),
            "suggest_named": sorted({e.winner for e in sugg}),
            "switches": len(sw), "worse_switches": worse_sw, "flip_flops": sum(r.flip_flops() for r in runs),
            "per_stream_switches": [len(r.switches) for r in runs],
            "first_switch_s": [round(x) for x in first], "to": sorted({b for _, _, b in sw}),
            "expect_ok": (all(len(r.switches) == 1 and r.switches[0][2] == expect for r in runs) if expect else None),
            "after_splice_s": after if any(r.splice_s is not None for r in runs) else None,
            "recall": {k: round(100 * v[1] / v[0], 1) for k, v in rec_after.items() if v[0]}}


def main(argv=None) -> int:
    a_ = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a_.add_argument("--take", nargs="+", action="append", default=[], metavar="LABEL REC")
    a_.add_argument("--truth-dir", default=None)
    a_.add_argument("--live", action="append", default=[], help="live setting label(s); default: every setting")
    a_.add_argument("--gate", choices=("stay", "switch", "silent", "quiet", "none"), default="none")
    a_.add_argument("--expect", default="", help="gate switch: the label it must switch to")
    a_.add_argument("--mode", default=MODE_AUTO)
    a_.add_argument("--hours", type=float, default=12.0)
    a_.add_argument("--starts", type=int, default=4, help="streams per live setting, from random start frames")
    a_.add_argument("--seed", type=int, default=1)
    a_.add_argument("--live-conf", type=float, default=0.15)
    a_.add_argument("--gap-every-s", type=float, default=0.0)
    a_.add_argument("--gap-s", type=float, default=0.0)
    a_.add_argument("--no-prefilter", action="store_true")
    a_.add_argument("--no-ms", action="store_true", help="no inference times: the « slower » rule off")
    a_.add_argument("--splice-at-s", type=float, default=None,
                    help="SYNTHETIC: from this second each model plays the other model's record at its input")
    a_.add_argument("--set", dest="sets", action="append", default=[], metavar="KEY=VALUE",
                    help="override an AutoParams field (exploration; the gate is the defaults)")
    a_.add_argument("--json", default=None)
    a = a_.parse_args(argv)
    if not a.take:
        print("no --take", file=sys.stderr)
        return 2
    ap = AutoParams()
    for kv in a.sets:
        k, _, v = kv.partition("=")
        if not hasattr(ap, k):
            print(f"unknown AutoParams field {k}", file=sys.stderr)
            return 2
        setattr(ap, k, type(getattr(ap, k))(json.loads(v)))
    rows, green = [], True
    for x in a.take:
        st = Stream(x[0], x[1:], Path(a.truth_dir) if a.truth_dir else None, conf=max(0.15, a.live_conf))
        lives = [st.by_label(lab) for lab in a.live] if a.live else list(st.take.cands)
        for live in lives:
            rng = random.Random(a.seed)
            runs = [simulate(st, live, a.mode, ap, None, rng.randrange(len(st.order)), a.hours, a.live_conf,
                             ms=None if a.no_ms else LAPTOP_MS, gap_every_s=a.gap_every_s, gap_s=a.gap_s,
                             prefilter=not a.no_prefilter, splice_at_s=a.splice_at_s)
                    for _ in range(a.starts)]
            row = _row(st, live, runs, a.expect if a.gate == "switch" else "")
            if a.gate == "stay":
                row["green"] = row["switches"] == 0 and row["suggest_worse"] == 0
            elif a.gate == "switch":
                row["green"] = bool(row["expect_ok"]) and row["flip_flops"] == 0
            elif a.gate == "silent":
                row["green"] = row["runs"] == 0 and row["suggestions"] == 0 and row["switches"] == 0
            elif a.gate == "quiet":
                row["green"] = row["suggestions"] == 0 and row["switches"] == 0
            else:
                row["green"] = True
            green &= row["green"]
            rows.append(row)
            print(f"  {row['take']:10s} live {row['live']:16s} {row['hours']:6.1f} h: runs {row['runs']} "
                  f"({row['runs_per_h']}/h; keeps {row['keeps']}, woken {row['wakes']}) skipped "
                  f"{row['skipped']} probes {row['probes']} gpu {row['gpu_s_per_h']} s/h | suggestions "
                  f"{row['suggestions']} (worse {row['suggest_worse']}) {row['suggest_named']} | switches "
                  f"{row['switches']} (worse {row['worse_switches']}, flip-flops {row['flip_flops']}) "
                  f"first at {row['first_switch_s']} s to {row['to']} | recall {row['recall']}"
                  f"{'' if not row['after_splice_s'] else ' | SYNTHETIC splice, s to ' + str(row['after_splice_s'])}"
                  f"{'' if a.gate == 'none' else ('  GREEN' if row['green'] else '  RED')}", flush=True)
    if a.gate != "none":
        print("GATE GREEN" if green else "GATE RED")
    if a.json:
        Path(a.json).write_text(json.dumps({"params": vars(ap), "rows": rows}, indent=1, default=str))
    return 0 if green else 1


if __name__ == "__main__":
    sys.exit(main())
