"""The autonomous swipe (Plan Cordee P6): the settings swipe, run by the app itself.

P5's swipe waits for the operator to press a button.  Here the app looks by itself, in the background, while
dancers are on the wall, and says or does something only when the referee (``core.swipe.decide``) proves another
setting better:

* **a suggestion** -- any verdict that names another setting: one line with an « Apply now » button; the operator
  decides.  The referee already did the statistics (an unbiased sample, a paired test corrected for the number
  of settings compared, a higher bar for a costlier setting): no second threshold is stacked on it;
* **an automatic switch** -- mode ``auto`` only, and only for the SEAMLESS pair: the other model at the same
  network input, its engine resident, swapped between two frames (a size change needs another engine and a few
  seconds without tracking, as a manual change does: a better input size is always a suggestion).  It needs two verdicts in a row, on disjoint frames, naming the same setting with a
  gain of ``definite_gain`` or more; a winner no slower than ``slower`` x the live setting's measured time; the
  dwell since the last switch; and never back to the setting just left before ``back_s``.

When it looks (``due``), in frames, not seconds:

* **nothing on the wall, nothing done.**  The app and the camera may be on for hours before anyone climbs: a frame
  without a dancer group is never kept, so an empty wall gives no run and no inference.  Kept frames older than
  ``SwipeParams.max_age_s`` (300 s) leave the pool, so a half-full pool empties within five minutes of the last
  dancer; two passages less than ``max_age_s`` apart are joined (the same scene: the frames stay an unbiased
  sample of it);
* **a run needs ``run_frames`` frames with dancers that no earlier run looked at** (``fresh``: newer than the
  newest frame of the last run), so two runs never share a frame and the two-verdict rule sees disjoint samples;
* **a free pre-filter** (``headroom``): from the live setting's own boxes on those frames -- no inference -- the
  most any setting could gain over it (the dancers it leaves bare, its boxes on nobody).  When that cannot clear
  the referee's bar for the other model, no run;
* the live setting has a box on almost none of the fresh frames (blind, or nobody there: a technician's shadow,
  a light change): a rarer, shorter **probe** instead.

This module is the policy only: no torch, no cv2, no threads, no clock of its own (every call takes ``now``).
The controller (``runtime.swipe_controller``) feeds it and acts on its advice.  Operator sentences are ASCII.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence

from core.input_shape import target_hw
from core.swipe import Candidate, FrameScore, SwipeFrame, SwipeParams, SwipeResult

MODE_OFF, MODE_SUGGEST, MODE_AUTO = "off", "suggest", "auto"
MODES = (MODE_OFF, MODE_SUGGEST, MODE_AUTO)


def parse_mode(value) -> str:
    """A project's ``swipe_auto`` value -> one of ``MODES`` (anything else: off)."""
    v = str(value or "").strip().lower()
    return v if v in MODES else MODE_OFF


def seamless(a: Candidate, b: Candidate) -> bool:
    """Same network input: the model can be swapped between two frames, the tracks carry over."""
    return target_hw(a.imgsz, a.shape) == target_hw(b.imgsz, b.shape)


def referee_params(live_conf: Optional[float] = None, base: Optional[SwipeParams] = None) -> SwipeParams:
    """The referee's parameters for a background run: every setting scored at max(the referee's cut, the project's
    confidence).  The live boxes were cut at the project's confidence, and the other model would run at it after a
    switch: scored lower, it would be credited with boxes the show never gets."""
    p = base or SwipeParams()
    if live_conf is not None and float(live_conf) > p.conf:
        return replace(p, conf=float(live_conf))
    return p


def bar_for(live: Candidate, other: Candidate, params: Optional[SwipeParams] = None) -> float:
    """The gain ``decide`` asks of ``other`` over ``live`` before naming it (``min_gain`` + ``cost_gain`` per
    doubling of the cost per frame), from the models' costs."""
    p = params or SwipeParams()
    ratio = other.cost / live.cost if live.cost > 0 else 1.0
    return p.min_gain + p.cost_gain * max(0.0, math.log2(max(ratio, 1e-9)))


def headroom(live_scores: Sequence[FrameScore], params: Optional[SwipeParams] = None) -> Optional[float]:
    """The most another setting could gain over the live one on these frames (a share of the dancers), from the
    live setting's own scores (``score_frame(frame.obs, frame.boxes, params)``: no inference): it covers every
    dancer group the live setting leaves bare and puts no box on nobody.  An upper bound of ``decide``'s gain --
    the referee counts only groups some setting covers, so the bound takes the bare groups as covered.  None when
    the live setting covers no group at all (no bound: blind, or nobody there)."""
    p = params or SwipeParams()
    covered = sum(s.covered for s in live_scores)
    bare = sum(s.groups - s.covered for s in live_scores)
    loose = sum(s.loose for s in live_scores)
    if covered <= 0:
        return None
    w = p.loose_weight
    return max((bare + w * loose) / (covered + bare), w * loose / covered)


@dataclass
class AutoParams:
    run_frames: int = 150         # a run needs this many kept frames with dancers that no earlier run looked at
                                  # (the referee's ``SwipeParams.frames``: the gate holds for 150)
    min_covered: int = 5          # ... among them, frames where the live setting left no dancer bare; fewer,
                                  # and no headroom bound (it covers nobody): a probe instead (blind, or nobody)
    every_s: float = 0.0          # an extra floor between two runs (the frame rule already spaces them)
    none_wait_s: float = 900.0    # after a run the referee could not judge (an untrusted plate, too few dancers),
                                  # no run for this long: the plate does not change until someone takes a new one
    probe_every_s: float = 300.0  # at most one probe this often
    probe_frames: int = 15        # frames a probe looks at (the newest fresh ones)
    definite_gain: float = 0.05   # an automatic switch needs this gain (share of the dancers) ...
    wins: int = 2                 # ... on this many verdicts in a row naming the same setting, on disjoint frames
    dwell_s: float = 300.0        # after any switch nothing switches by itself for this long
    back_s: float = 900.0         # ... and never back to the setting just left before this long
    slower: float = 1.15          # never by itself to a setting this many times the live one's inference time
    window_s: float = 0.0         # unused since 26/11 (freshness is in frames: ``run_frames``); kept for callers
    min_lost: int = 0             # unused since 26/11 (the pre-filter ``headroom`` replaces it); kept for callers
    definite_z: float = 0.0       # unused since 26/11 (the referee does the test); kept for callers


@dataclass
class AutoAdvice:
    kind: str = "none"                    # "none" | "suggest" | "apply"
    level: str = ""                       # "marginal" | "definite" (gain >= definite_gain)
    winner: Optional[Candidate] = None
    seamless: bool = False                # the winner runs at the live network input
    streak: int = 0                       # definite verdicts in a row that named it, on disjoint frames
    text: str = ""                        # one ASCII sentence for the operator
    why_not_auto: str = ""                # mode auto, definite, yet only suggested: the reason, in plain words


class SwipeAuto:
    """The policy's memory: when it last looked, what the last runs named, when the setting last changed."""

    def __init__(self, mode: str = MODE_OFF, params: Optional[AutoParams] = None):
        self.mode = parse_mode(mode)
        self.params = params or AutoParams()
        self.reset()

    def reset(self) -> None:
        """Another project, ROI, plate or camera: nothing learned so far describes this scene."""
        self._last_end: Optional[float] = None       # end of the last background run or probe
        self._last_probe: Optional[float] = None
        self._streak_winner: Optional[Candidate] = None
        self._streak = 0
        self._seen_until: Optional[float] = None     # newest frame the last run looked at
        self._switched_at: Optional[float] = None
        self._left: Optional[Candidate] = None       # the setting the last switch moved away from
        self._none_at: Optional[float] = None        # the last run the referee could not judge
        self.skipped = 0                             # runs the pre-filter saved

    # -- when to look -------------------------------------------------------------------------------------
    def fresh(self, frames: Sequence[SwipeFrame]) -> List[SwipeFrame]:
        """The kept frames no earlier run looked at (newer than the newest frame of the last run)."""
        if self._seen_until is None:
            return list(frames)
        return [f for f in frames if f.t > self._seen_until]

    def due(self, now: float, lost_fresh: int, covered_fresh: int, busy: bool = False,
            room: Optional[float] = None, newest_frame_t: Optional[float] = None) -> str:
        """"run", "probe" or "idle".  ``lost_fresh`` / ``covered_fresh``: the ``fresh`` frames on which the live
        setting left a dancer bare / left none bare (``SwipeFrame.lost``; their sum is the count of fresh frames,
        a run needs ``run_frames``); a probe when ``covered_fresh`` < ``min_covered`` and ``room`` is None; ``busy``: a
        swipe, a model load, a calibration or a plate capture is running; ``room``: ``headroom`` on those frames
        minus ``bar_for(live, other)`` (None: unknown or no bound) -- a run that cannot clear the bar is not run,
        and its frames count as looked at (``newest_frame_t``: the newest of them)."""
        p = self.params
        if self.mode == MODE_OFF or busy:
            return "idle"
        if self._last_end is not None and p.every_s > 0 and now - self._last_end < p.every_s:
            return "idle"
        if self._none_at is not None and now - self._none_at < p.none_wait_s:
            return "idle"                 # the referee said it cannot judge this scene: wait
        if lost_fresh + covered_fresh < p.run_frames:
            return "idle"                 # an empty wall keeps nothing: no run, no inference
        if covered_fresh < p.min_covered and room is None:
            if self._last_probe is None or now - self._last_probe >= p.probe_every_s:
                return "probe"
            return "idle"
        if room is not None and room < 0:
            self.skipped += 1             # the live setting leaves no room for the bar: no inference
            if newest_frame_t is not None:
                self._seen_until = max(self._seen_until or newest_frame_t, float(newest_frame_t))
            return "idle"
        return "run"

    def candidates(self, planned: Iterable[Candidate], live: Candidate) -> List[Candidate]:
        """What a background run tries, among the planner's candidates: the live setting and the settings at
        its own network input (a switch to them is seamless).  Other sizes are the operator's swipe."""
        out = [live]
        out += [c for c in planned if c != live and seamless(c, live)]
        return out

    def ran(self, now: float, probe: bool = False) -> None:
        """A background run (or probe) just ended, whatever it found.  (A run's frames stop being fresh when its
        verdict goes through ``advise``, which takes the newest of them.)"""
        self._last_end = float(now)
        if probe:
            self._last_probe = float(now)

    # -- what to do with a verdict ------------------------------------------------------------------------
    def advise(self, now: float, result: Optional[SwipeResult], newest_frame_t: Optional[float] = None,
               ms: Optional[Dict[Candidate, float]] = None) -> AutoAdvice:
        """The advice for one finished run.  ``newest_frame_t``: the newest frame it looked at (the next run
        counts toward the streak only on frames newer than that); ``ms``: inference time per candidate."""
        p = self.params
        fresh = self._seen_until is None or newest_frame_t is None or newest_frame_t > self._seen_until
        if newest_frame_t is not None:
            self._seen_until = max(self._seen_until or newest_frame_t, newest_frame_t)
        if result is not None and result.winner is None:
            self._none_at = float(now)    # no verdict: an untrusted plate or too few dancers
        if self.mode == MODE_OFF or result is None or result.winner is None or not result.switch:
            self._streak_winner, self._streak = None, 0
            return AutoAdvice()
        winner, live = result.winner, result.current
        row = next((r for r in result.rows if r.candidate == winner), None)
        cur = next((r for r in result.rows if r.candidate == live), None)
        gain = row.gain if row else 0.0
        definite = gain >= p.definite_gain
        # the streak counts DEFINITE verdicts in a row for one winner, each on frames the last one did not see
        if not definite:
            self._streak_winner, self._streak = winner, 0
        elif winner != self._streak_winner or self._streak == 0:
            self._streak_winner, self._streak = winner, 1
        elif fresh:
            self._streak += 1
        same_input = seamless(winner, live)
        said = (f"{winner.label} covers {100 * (row.coverage if row else 0):.0f} % of the dancers, "
                f"{live.label} {100 * (cur.coverage if cur else 0):.0f} %")
        adv = AutoAdvice(kind="suggest", level="definite" if definite else "marginal", winner=winner,
                         seamless=same_input, streak=self._streak)
        why = ""
        if self.mode != MODE_AUTO:
            why = "automatic switching is off"
        elif not same_input:
            why = "another input size needs a few seconds without tracking: apply it by hand"
        elif not definite:
            why = f"the gain is under {100 * p.definite_gain:.0f} points"
        elif self._streak < p.wins:
            why = f"seen {self._streak} time(s), {p.wins} needed"
        elif self._switched_at is not None and now - self._switched_at < p.dwell_s:
            why = "the setting changed a moment ago"
        elif winner == self._left and self._switched_at is not None and now - self._switched_at < p.back_s:
            why = "it is the setting just left"
        elif ms and ms.get(winner) and ms.get(live) and ms[winner] > p.slower * ms[live]:
            why = f"it is slower ({ms[winner]:.0f} ms a frame against {ms[live]:.0f})"
        if why:
            adv.why_not_auto = why
            adv.text = (f"{said}: clearly better - Apply now ({why})" if definite
                        else f"{said}: it looks better - Apply now?")
            return adv
        adv.kind = "apply"
        adv.text = f"switching to {winner.label}: {said}"
        return adv

    def switched(self, now: float, old: Candidate, new: Candidate) -> None:
        """The live setting changed, by the app or by the operator: the dwell starts, the streak is over (the
        frames kept so far describe the old setting: the controller starts its pool again)."""
        self._switched_at = float(now)
        self._left = old
        self._streak_winner, self._streak = None, 0
        self._seen_until = None
        self._none_at = None
