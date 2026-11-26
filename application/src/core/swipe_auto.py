"""The autonomous swipe (Plan Cordee P6): the settings swipe, run by the app itself.

P5's swipe waits for the operator to press a button.  Here the app looks by itself, in the background, while
dancers are on the wall, and says or does something only when it found better:

* **marginally better** -- the verdict of ``core.swipe.decide`` names another setting: a warning with an
  « Apply now » button; the operator decides;
* **definitely better** -- a large gain, far out of the noise, on several runs in a row that looked at different
  frames: in mode ``auto`` the app switches by itself and tells the operator; in mode ``suggest`` it is the same
  warning, worded stronger.

What it must never do (Thomas, 26/11, and the day's measures):

* **work on an empty wall.**  The app and the camera may be on long before a dancer enters: a background run
  starts only when fresh frames show dancers on the wall (``due``), never on a timer alone;
* **black out the show.**  By itself it only makes a *seamless* switch: another model at the SAME network input,
  already loaded, swapped between two frames (the tracker works in the network's coordinates, so a size change
  breaks its tracks and needs the reload a manual change does).  A better input size is always a suggestion;
* **flap.**  After any switch nothing switches by itself for ``dwell_s``, and never back to the setting just left
  before ``back_s``;
* **slow the loop.**  Never by itself to a setting whose inference takes ``slower`` times the live one's.

This module is the policy only: no torch, no cv2, no threads, no clock of its own (every call takes ``now``).
The controller (``runtime.swipe_controller``) feeds it and acts on its advice.  Operator sentences are ASCII.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from core.input_shape import target_hw
from core.swipe import Candidate, SwipeResult

MODE_OFF, MODE_SUGGEST, MODE_AUTO = "off", "suggest", "auto"
MODES = (MODE_OFF, MODE_SUGGEST, MODE_AUTO)


def parse_mode(value) -> str:
    """A project's ``swipe_auto`` value -> one of ``MODES`` (anything else: off)."""
    v = str(value or "").strip().lower()
    return v if v in MODES else MODE_OFF


def seamless(a: Candidate, b: Candidate) -> bool:
    """Same network input: the model can be swapped between two frames, the tracks carry over."""
    return target_hw(a.imgsz, a.shape) == target_hw(b.imgsz, b.shape)


@dataclass
class AutoParams:
    window_s: float = 120.0       # a background run looks only at frames this fresh: dancers on the wall NOW
    min_lost: int = 20            # frames in the window where the live setting leaves a dancer bare ...
    min_covered: int = 5          # ... and frames where it has a box on a dancer: somebody really is there
    every_s: float = 120.0        # from the end of a background run to the next
    probe_every_s: float = 300.0  # the foreground shows groups but the live setting has no box at all (blind,
                                  # or nobody: a technician's shadow, a light change): a rarer, shorter look
    probe_frames: int = 15
    definite_gain: float = 0.10   # « definitely better »: this share of the dancers gained ...
    definite_z: float = 3.0       # ... this many standard errors out of the noise ...
    wins: int = 2                 # ... on this many runs in a row, each on frames the previous one did not see
    dwell_s: float = 300.0        # after any switch nothing switches by itself for this long
    back_s: float = 900.0         # ... and never back to the setting just left before this long
    slower: float = 1.15          # never by itself to a setting this many times the live one's inference time


@dataclass
class AutoAdvice:
    kind: str = "none"                    # "none" | "suggest" | "apply"
    level: str = ""                       # "marginal" | "definite"
    winner: Optional[Candidate] = None
    seamless: bool = False                # the winner runs at the live network input
    streak: int = 0                       # definite runs in a row that named it, on different frames
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

    # -- when to look -------------------------------------------------------------------------------------
    def due(self, now: float, lost_fresh: int, covered_fresh: int, busy: bool = False) -> str:
        """"run", "probe" or "idle".  ``lost_fresh`` / ``covered_fresh``: kept frames no older than
        ``params.window_s``; ``busy``: a swipe, a model load, a calibration or a plate capture is running."""
        p = self.params
        if self.mode == MODE_OFF or busy:
            return "idle"
        if self._last_end is not None and now - self._last_end < p.every_s:
            return "idle"
        if lost_fresh < p.min_lost:
            return "idle"                 # an empty wall, or a live setting that loses nobody: nothing to do
        if covered_fresh >= p.min_covered:
            return "run"
        if self._last_probe is None or now - self._last_probe >= p.probe_every_s:
            return "probe"
        return "idle"

    def candidates(self, planned: Iterable[Candidate], live: Candidate) -> List[Candidate]:
        """What a background run tries, among the planner's candidates: the live setting and the settings at
        its own network input (a switch to them is seamless).  Other sizes are the operator's swipe."""
        out = [live]
        out += [c for c in planned if c != live and seamless(c, live)]
        return out

    def ran(self, now: float, probe: bool = False) -> None:
        """A background run (or probe) just ended, whatever it found."""
        self._last_end = float(now)
        if probe:
            self._last_probe = float(now)

    # -- what to do with a verdict ------------------------------------------------------------------------
    def advise(self, now: float, result: Optional[SwipeResult], newest_frame_t: Optional[float] = None,
               ms: Optional[Dict[Candidate, float]] = None) -> AutoAdvice:
        """The advice for one finished run.  ``newest_frame_t``: the newest frame it looked at (the next run
        counts toward the streak only on frames newer than that); ``ms``: inference time per candidate."""
        p = self.params
        if self.mode == MODE_OFF or result is None or result.winner is None or not result.switch:
            self._streak_winner, self._streak = None, 0
            return AutoAdvice()
        winner, live = result.winner, result.current
        row = next((r for r in result.rows if r.candidate == winner), None)
        cur = next((r for r in result.rows if r.candidate == live), None)
        gain = row.gain if row else 0.0
        noise = row.noise if row else 0.0
        definite = gain >= p.definite_gain and gain >= p.definite_z * noise
        fresh = self._seen_until is None or newest_frame_t is None or newest_frame_t > self._seen_until
        # the streak counts DEFINITE runs in a row for one winner, each on frames the last one did not see
        if not definite:
            self._streak_winner, self._streak = winner, 0
        elif winner != self._streak_winner or self._streak == 0:
            self._streak_winner, self._streak = winner, 1
        elif fresh:
            self._streak += 1
        if newest_frame_t is not None:
            self._seen_until = max(self._seen_until or newest_frame_t, newest_frame_t)
        same_input = seamless(winner, live)
        said = (f"{winner.label} covers {100 * (row.coverage if row else 0):.0f} % of the dancers, "
                f"{live.label} {100 * (cur.coverage if cur else 0):.0f} %")
        adv = AutoAdvice(kind="suggest", level="definite" if definite else "marginal", winner=winner,
                         seamless=same_input, streak=self._streak)
        if not definite:
            adv.text = f"{said}: it looks better - Apply now?"
            return adv
        why = ""
        if self.mode != MODE_AUTO:
            why = "automatic switching is off"
        elif not same_input:
            why = "another input size needs a few seconds without tracking"
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
            adv.text = f"{said}: clearly better - Apply now ({why})"
            return adv
        adv.kind = "apply"
        adv.text = f"switching to {winner.label}: {said}"
        return adv

    def switched(self, now: float, old: Candidate, new: Candidate) -> None:
        """The live setting changed, by the app or by the operator: the dwell starts, the streak is over."""
        self._switched_at = float(now)
        self._left = old
        self._streak_winner, self._streak = None, 0
