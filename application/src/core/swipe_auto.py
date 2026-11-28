"""The autonomous swipe (Plan Cordee P6): the settings swipe, run by the app itself.

P5's swipe waits for the operator to press a button.  Here the app looks by itself, in the background, while
dancers are on the wall, and says or does something only when the referee (``core.swipe.decide``) proves another
setting better:

* **a suggestion** -- the same setting named by two verdicts in a row (the streak, as a switch needs it), and the
  « slower » rule lets it through -- or, for a model more than 15 % slower, the budget rule (``advise``): its gain
  clear on both looks and the frame the show would have with it under the 15 fps floor, the line saying that cost:
  one line with an « Apply now » button; the operator decides (while that Apply is held back in this build,
  ``apply_released``: advice only, « note the time and this line », no button).  One verdict says nothing: the
  next look comes at once, to confirm it or not.  The referee already did the statistics (an unbiased sample, a
  paired test corrected for the number of settings compared, a higher bar for a costlier setting): no second
  threshold is stacked on it.  Two verdicts are « in a row » when the second comes at most ``streak_gap_s`` after
  the first, with no look skipped for lack of room and no empty pool between them.  Dismiss on it means « not
  now » (``dismissed``): the streak ends, and that setting is neither suggested nor switched to by itself for a
  while (``snoozed``) or until the scene changes;
* **an automatic switch** -- mode ``auto`` only, and only for the SEAMLESS pair: the other model at the same
  network input, its engine resident, swapped between two frames (a size change needs another engine and a few
  seconds without tracking, as a manual change does: a better input size is always a suggestion).  It needs two
  verdicts in a row, on disjoint frames, naming the same setting with a gain of ``definite_gain`` or more; a
  winner no slower than ``slower`` x the live setting's measured time, the two times measured alike
  (``alike_ms``) -- the strict rule, whatever the floor lets a suggestion name; the dwell since the last switch;
  and never back to the setting just left before ``back_s``.

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
  a light change): a rarer, shorter **probe** instead;
* **a settled show backs off.**  After a « keep » verdict (the referee judged and named nothing), a suggestion
  shown or confirmed, or a winner the « slower » rule or the floor refuses, no run for ``keep_wait_s``, doubled
  at each in a row up to ``keep_wait_max_s``; a verdict naming another setting once, a switch or a ``reset`` ends
  it.  While it waits, the one free signal wakes it: when the live setting leaves a
  dancer bare on clearly more of the fresh frames than on the frames of any keep of the series (``wake_lost``
  more, as a share), the next run comes at once -- the scene may have turned against the live model.  (The
  highest, not the last: on one 150-frame window to the next the dancing alone moves that share by up to 30
  points on the 08/10 takes.)

This module is the policy only: no torch, no cv2, no threads, no clock of its own (every call takes ``now``).
The controller (``runtime.swipe_controller``) feeds it and acts on its advice.  Operator sentences are ASCII.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import core.config as core_config      # SWIPE_AUTO and _RELEASED, read when needed (a test or a gate driver sets them)
from core.input_shape import target_hw
from core.swipe import Candidate, FrameScore, SwipeFrame, SwipeParams, SwipeResult

MODE_OFF, MODE_SUGGEST, MODE_AUTO = "off", "suggest", "auto"
MODES = (MODE_OFF, MODE_SUGGEST, MODE_AUTO)
# the combo's words for each mode (the GUI's items, the app's syncs, the word of a toast without "Auto: ")
LABELS = {MODE_OFF: "Auto: off", MODE_SUGGEST: "Auto: suggest", MODE_AUTO: "Auto: switch"}


# a project's swipe_auto that names no mode (a hand edit, a value cut short): the schema keeps this word in its place
# (``core.config_schema.validate_flat``), and a project's load reads it as off and says so -- an unreadable setting
# never turns a background job on, as an absent key would with the default
UNREADABLE = "unreadable"


def parse_mode(value) -> str:
    """A project's ``swipe_auto`` value -> one of ``MODES`` (anything else: off)."""
    v = str(value or "").strip().lower()
    return v if v in MODES else MODE_OFF


def is_mode(value) -> bool:
    """Whether a project's ``swipe_auto`` value names a mode (``parse_mode`` reads anything else as off)."""
    return str(value or "").strip().lower() in MODES


def apply_released() -> bool:
    """Whether this build lets anyone apply a background suggestion (``core.config.SWIPE_AUTO_APPLY_RELEASED``, read
    at each call).  False: a suggestion is advice only -- its line says « note the time and this line », no Apply
    on it.  Neither the operator's own swipe nor the modes depend on it.  Anything but True holds it back."""
    return core_config.SWIPE_AUTO_APPLY_RELEASED is True


def released_modes() -> Tuple[str, ...]:
    """The modes this build lets anyone set (``core.config.SWIPE_AUTO_RELEASED``, read at each call), in the order
    of ``MODES``; off always.  The one source of the combo's items and of what the app accepts."""
    released = core_config.SWIPE_AUTO_RELEASED
    return tuple(m for m in MODES if m == MODE_OFF or m in released)


def default_mode() -> str:
    """The mode when nothing says otherwise -- a project with no ``swipe_auto`` key, a new project: the wished
    default (``core.config.SWIPE_AUTO``) when this build releases it (``released_modes``, read at each call), else
    off.  The one source: nothing else reads ``SWIPE_AUTO``."""
    wished = parse_mode(core_config.SWIPE_AUTO)
    return wished if wished in released_modes() else MODE_OFF


def mode_word(mode) -> str:
    """The combo's word for ``mode``: off, suggest, switch."""
    return LABELS[parse_mode(mode)].removeprefix("Auto: ")


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
    slower: float = 1.15          # never to a setting more than this many times the live one's inference time
                                  # (15 % slower passes: the measure's own noise, see ``alike_ms``) ...
    speed_q: float = 10.0         # ... both times this low percentile of their inferences over the run (``alike_ms``);
                                  # either one unknown: refused.  The strict rule: an automatic switch's, and a
                                  # suggestion's but for the budget rule below (``advise``)
    floor_ms: float = core_config.SWIPE_AUTO_FRAME_FLOOR_MS     # a slower model may be suggested when the frame
                                  # the show would have with it stays under this, at its 95th percentile ...
    cost_factor: float = core_config.SWIPE_ADVICE_COST_FACTOR   # ... estimated: the live frames' + this x the
                                  # difference of the two inference times (``frame_cost``)
    streak_gap_s: float = core_config.SWIPE_AUTO_STREAK_GAP_S   # two verdicts this far apart are not « in a row »
    keep_wait_s: float = 300.0    # after a « keep » verdict, no run for this long ...
    keep_wait_max_s: float = 1800.0   # ... doubled at each keep in a row, up to this
    wake_lost: float = 0.10       # ... unless the live setting's share of fresh frames with a dancer bare rose
                                  # this much above the highest share of the keeps in a row (0.10 = 10 points)
    window_s: float = 0.0         # unused since 26/11 (freshness is in frames: ``run_frames``); kept for callers
    min_lost: int = 0             # unused since 26/11 (the pre-filter ``headroom`` replaces it); kept for callers
    definite_z: float = 0.0       # unused since 26/11 (the referee does the test); kept for callers


def alike_ms(cand_times: Sequence[float], live_times: Sequence[float],
             q: float = 10.0) -> Tuple[Optional[float], Optional[float]]:
    """(the candidate's inference time, the live model's) for the « slower » rule, measured alike: the ``q``-th
    percentile of each one's times over the same background run -- the candidate's inferences there, the live
    model's network passes that the run sampled (None where there is none).

    The two are not measured alike: the candidate runs on the looker's thread beside the live loop (the same GPU,
    the same interpreter, no CUDA graph), the live model in the loop.  The candidate's median read 1.3 to 2 times
    its time once live (mgr-ran, 26/11: l @ 1280 18.8-24.4 ms in the background, 13.2-13.9 live; x @ 1920 x 1056
    32.1 against 20.7), by how much depending on the machine and its load -- the rule called l slower than x when it
    is about 20 % faster.  The low end of each is the time nobody got in the way: on mgr-ran, a second copy of the
    LIVE engine run in the background beside the candidate read at its 10th percentile 0.98-1.14 times the live
    model's own 10th percentile, at its median 1.5-1.9 times (tmp_analysis/swipe-1126/live/p6h/tools/bgtime.py,
    x and l at 1280 and 1920 x 1056, back to back and in real time)."""
    def pct(values: Sequence[float]) -> Optional[float]:
        v = sorted(float(x) for x in values if x is not None and x > 0)
        if not v:
            return None
        k = (len(v) - 1) * min(100.0, max(0.0, float(q))) / 100.0
        i = int(k)
        j = min(i + 1, len(v) - 1)
        return v[i] + (v[j] - v[i]) * (k - i)
    return pct(cand_times), pct(live_times)


def slower_rule(cand_ms: Optional[float], live_ms: Optional[float], slower: float) -> str:
    """The strict « slower » rule, an automatic switch's, whatever the floor: "" when the candidate's time is at
    most ``slower`` times the live model's (the two measured alike, ``alike_ms``), else why not -- either one not
    known included (fail closed)."""
    if not cand_ms or not live_ms:
        return "its time or the live model's is not known"
    if cand_ms > slower * live_ms:
        return f"it is more than {100 * (slower - 1):.0f} % slower ({cand_ms:.1f} ms a frame against {live_ms:.1f})"
    return ""


def frame_cost(frame_ms: Optional[float], cand_ms: Optional[float], live_ms: Optional[float],
               factor: float) -> Optional[float]:
    """The advice's estimate of the frame the show would have with the candidate live, at its 95th percentile:
    ``frame_ms`` (the live frames' now, over the 3 s before the look) + ``factor`` x (the candidate's inference
    time - the live model's, measured alike).  Not a plain substitution of the two times: on the show laptop beside
    TouchDesigner the frame grew 1.7 times what the inference grew (``core.config.SWIPE_ADVICE_COST_FACTOR``).
    None when any of the three is not known."""
    if not frame_ms or not cand_ms or not live_ms:
        return None
    return float(frame_ms) + float(factor) * (float(cand_ms) - float(live_ms))


# an estimate this close to the floor is printed with it to one decimal: in whole milliseconds both sides of « over »
# could read the same (« about 67 ms: over the 15 fps floor (67 ms) », 67.0 against 66.7)
FLOOR_EDGE_MS = 1.0


def edge_places(est: Optional[float], floor_ms: float) -> int:
    """The decimals of an estimate printed beside the floor, and of the floor and the frame printed with it: 1 when
    ``est`` is within ``FLOOR_EDGE_MS`` of ``floor_ms``, else 0 (whole milliseconds)."""
    return 1 if est is not None and abs(float(est) - float(floor_ms)) <= FLOOR_EDGE_MS else 0


def floor_words(floor_ms: float, est: Optional[float] = None) -> str:
    """The floor in the technician's terms: « 15 fps (67 ms) » -- « 15 fps (66.7 ms) » beside an estimate ``est``
    within ``FLOOR_EDGE_MS`` of it."""
    return f"{1000.0 / floor_ms:.0f} fps ({floor_ms:.{edge_places(est, floor_ms)}f} ms)"


# a slower model the budget rule lets through: the figures its line gives instead of the two inference times (``d``:
# edge_places)
COST = "a frame would take about {:.{d}f} ms against {:.{d}f}; the floor is {}"
# ... and one clearly better that the floor refuses: the look's row and the advice's reason, nothing shown
OVER_FLOOR = ("{} would see more ({:.0f} % against {:.0f} %) but a frame would take about {:.{d}f} ms: over the "
              "{:.0f} fps floor ({:.{d}f} ms)")
# what holds an automatic switch to a slower model back, whatever the floor: the strict rule
NEVER_BY_ITSELF = "the app never switches to it by itself"

SEEN_ONCE = "seen once, it looks again"
DWELL = "the setting changed a moment ago"
JUST_LEFT = "it is the setting just left"


# the end of a suggestion's line while its Apply is held back (``apply_released``): what a man alone at the desk can
# do with it during the show -- the time and the line, read afterwards ...
NOTE_IT = "note the time and this line"
# ... and the same, said by its toast and by the refusal of an Apply that still reaches it (not beside the line)
NOTE_THE_LINE = "note the time and the line"


def suggestion_text(said: str, seen: int, held: str = "") -> str:
    """The suggestion's line, one wording whatever the gain: « <said>, seen twice: Apply now? », or « <said>, seen
    twice: note the time and this line » while its Apply is held back (``apply_released``); ``held``: what holds an
    automatic switch back, in parentheses after it."""
    times = "twice" if seen == 2 else f"{seen} times"
    ask = "Apply now?" if apply_released() else NOTE_IT
    return f"{said}, seen {times}: {ask}" + (f" ({held})" if held else "")


NOT_CONFIRMED_AT = "not confirmed at the last look ({})"


def unconfirmed_text(said: str, at: str, why: str = "") -> str:
    """The line of a suggestion the last look did not confirm: no « Apply now? », the figures of the looks that
    named it and when the last look was (``at``: HH:MM) -- « l @ 1280 covered 99 % of the dancers, x @ 1280 76 %
    (6.0 ms a frame against 6.0) - not confirmed at the last look (21:40) »; ``why``: the reason the policy refused it
    at that look (the « slower » rule, the floor), after a colon."""
    said = said.replace(" covers ", " covered ", 1)     # ``said`` is advise's: those looks are over
    return f"{said} - {NOT_CONFIRMED_AT.format(at)}" + (f": {why}" if why else "")


@dataclass
class AutoAdvice:
    kind: str = "none"                    # "none" | "seen" (named once: nothing shown) | "suggest" | "apply"
    level: str = ""                       # "marginal" | "definite" (gain >= definite_gain)
    winner: Optional[Candidate] = None
    seamless: bool = False                # the winner runs at the live network input
    streak: int = 0                       # verdicts in a row that named it, on disjoint frames
    text: str = ""                        # one ASCII sentence for the operator
    why_not_auto: str = ""                # not switched by itself: the reason, in plain words (/status)
    said: str = ""                        # the figures: both coverages, both times
    held: str = ""                        # what holds an automatic switch back, said on the line
    too_slow: str = ""                    # a slower winner the budget rule let through as advice: the strict
                                          # « slower » rule's reason, never switched to by itself
    outcome: str = ""                     # what the look's row says instead of the referee's reason (the floor)


class SwipeAuto:
    """The policy's memory: when it last looked, what the last runs named, when the setting last changed."""

    def __init__(self, mode: str = MODE_OFF, params: Optional[AutoParams] = None):
        self.mode = parse_mode(mode)
        self.params = params or AutoParams()
        self.reset()

    def reset(self) -> None:
        """Another project, ROI, plate, camera or picture enhancement, or the reference watch's alert said or gone:
        nothing learned so far describes this scene (a Dismiss's snooze ends too)."""
        self._last_end: Optional[float] = None       # end of the last background run or probe
        self._last_probe: Optional[float] = None
        self._streak_winner: Optional[Candidate] = None
        self._streak = 0                             # verdicts in a row naming it ...
        self._definite = 0                           # ... the last ones of them with a gain of definite_gain or more
        self._streak_at: Optional[float] = None      # ... the last of them
        self._seen_until: Optional[float] = None     # newest frame the last run looked at
        self._switched_at: Optional[float] = None
        self._left: Optional[Candidate] = None       # the setting the last switch moved away from
        self._none_at: Optional[float] = None        # the last run the referee could not judge
        self._keeps = 0                              # « keep » verdicts in a row
        self._keep_at: Optional[float] = None        # the last of them
        self._keep_lost: Optional[float] = None      # the highest lost share of those keeps
        self._run_lost: Optional[float] = None       # the lost share of the fresh frames the last run was given
        self._run_woken = False                      # ... and whether the lost share called it
        self.skipped = 0                             # runs the pre-filter saved
        self.woken = 0                               # runs the lost share called during a back-off
        self._snoozed: Optional[Candidate] = None    # the setting the operator dismissed (« not now ») ...
        self._snoozed_until: Optional[float] = None  # ... until then

    def _end_streak(self) -> None:
        self._streak_winner, self._streak, self._definite, self._streak_at = None, 0, 0, None

    def _look_now(self) -> None:
        self._keeps, self._keep_at, self._keep_lost = 0, None, None

    def _back_off(self, now: float) -> None:
        """No run for ``keep_wait`` from now, doubled at each in a row; after a run the lost share called, from the
        start again (its frames may straddle the change: the next scheduled run, keep_wait_s on, sees it whole)."""
        self._keeps = 1 if self._run_woken else self._keeps + 1
        self._keep_at = float(now)
        if self._run_lost is not None:   # the highest lost share of this series: the dancing's own swing stays under it
            self._keep_lost = max(self._keep_lost or 0.0, self._run_lost)

    # -- when to look -------------------------------------------------------------------------------------
    def fresh(self, frames: Sequence[SwipeFrame]) -> List[SwipeFrame]:
        """The kept frames no earlier run looked at (newer than the newest frame of the last run), from the whole
        pool (``frames``): an empty pool -- no dancer for the pool's age, ``SwipeParams.max_age_s`` -- ends the
        streak (the next verdict is on another scene)."""
        if not frames:
            self._end_streak()
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
        share = lost_fresh / float(lost_fresh + covered_fresh)
        woke = False
        if self._keep_at is not None and now - self._keep_at < self.keep_wait():
            if self._keep_lost is None or share < self._keep_lost + p.wake_lost - 1e-9:
                return "idle"             # settled: the referee kept this setting a moment ago
            woke = True                   # it loses clearly more dancers than then: look now
        if covered_fresh < p.min_covered and room is None:
            if self._last_probe is None or now - self._last_probe >= p.probe_every_s:
                return "probe"
            return "idle"
        if room is not None and room < 0:
            self.skipped += 1             # the live setting leaves no room for the bar: no inference ...
            self._end_streak()            # ... and the verdicts before and after it are not in a row
            if newest_frame_t is not None:
                self._seen_until = max(self._seen_until or newest_frame_t, float(newest_frame_t))
            return "idle"
        self._run_lost, self._run_woken = share, woke
        self.woken += woke
        return "run"

    def keep_wait(self) -> float:
        """How long the back-off after the last « keep » verdict lasts (0: none)."""
        p = self.params
        if self._keeps <= 0:
            return 0.0
        return min(p.keep_wait_s * 2.0 ** (self._keeps - 1), max(p.keep_wait_max_s, p.keep_wait_s))

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
               ms: Optional[Dict[Candidate, float]] = None, frame_ms: Optional[float] = None) -> AutoAdvice:
        """The advice for one finished run.  ``newest_frame_t``: the newest frame it looked at (the next run
        counts toward the streak only on frames newer than that); ``ms``: inference time per candidate, measured
        alike (``alike_ms``); ``frame_ms``: the live frames' time at their 95th percentile over the 3 s before the
        look began (not during it: its own inferences slow them), None when not measured.

        A setting named once is « seen » (nothing shown, the next look comes at once); named on ``wins`` looks in a
        row it is suggested, or switched to in mode auto when the switch's own conditions hold, and the looks back
        off as after a « keep ».  Two rules on its time (the owner, 28/11: a suggestion may name a costlier model
        when it brings much more, as long as the show keeps 15 fps):

        * an automatic switch, the strict « slower » rule (``slower_rule``): never to a setting more than
          ``slower`` times the live model's time (15 % slower: the measure's own noise lets up to that through), nor
          to one whose time or the live model's is not known (fail closed) -- whatever the floor says;
        * a suggestion (the advice shown, its Apply released or not), the same, but for the budget rule: a setting
          more than ``slower`` times as slow may still be suggested when its gain is ``definite_gain`` or more on
          each of the ``wins`` looks in a row and the frame the show would have with it (``frame_cost``) stays
          under ``floor_ms`` -- its line then says that frame and the floor.  Clearly better but over the floor:
          nothing shown, the reason in the look's row (``outcome``), the looks back off as after a « keep ».  No
          ``frame_ms``, or either time unknown: refused, as by the strict rule."""
        p = self.params
        fresh = self._seen_until is None or newest_frame_t is None or newest_frame_t > self._seen_until
        if newest_frame_t is not None:
            self._seen_until = max(self._seen_until or newest_frame_t, newest_frame_t)
        named = result is not None and result.winner is not None and result.switch
        if result is not None and result.winner is None:
            self._none_at = float(now)    # no verdict: an untrusted plate or too few dancers
        elif result is not None and not result.switch:
            self._back_off(now)           # « keep »: judged, nothing better
        if self.mode == MODE_OFF or not named:
            self._end_streak()
            return AutoAdvice()
        winner, live = result.winner, result.current
        if self.snoozed(now) == winner:
            # dismissed while this run went on: nothing said, nothing switched, no streak (a new one after)
            self._end_streak()
            self._look_now()
            return AutoAdvice(winner=winner, why_not_auto="dismissed: not now")
        row = next((r for r in result.rows if r.candidate == winner), None)
        cur = next((r for r in result.rows if r.candidate == live), None)
        gain = row.gain if row else 0.0
        definite = gain >= p.definite_gain
        level = "definite" if definite else "marginal"
        mw, ml = (ms or {}).get(winner), (ms or {}).get(live)
        cov_w, cov_l = 100 * (row.coverage if row else 0), 100 * (cur.coverage if cur else 0)
        too_slow = slower_rule(mw, ml, p.slower)       # the strict rule: "" lets it through
        cost = ""                                      # a slower winner's line: the frame with it, the floor
        if too_slow:
            # the budget rule, a suggestion's only: a gain over the definite bar (on both looks: see the streak
            # below) and the frame the show would have with it under the floor
            est = frame_cost(frame_ms, mw, ml, p.cost_factor) if definite else None
            if est is not None and est > p.floor_ms:
                # clearly better, too costly for the floor: nothing shown, no streak; judged -- the row says why
                over = OVER_FLOOR.format(winner.label, cov_w, cov_l, est, 1000.0 / p.floor_ms, p.floor_ms,
                                         d=edge_places(est, p.floor_ms))
                self._end_streak()
                self._back_off(now)
                return AutoAdvice(level=level, winner=winner, why_not_auto=over, outcome=over)
            if est is None:
                # the strict rule refuses it: neither suggested nor switched to, no streak; judged, nothing to say
                self._end_streak()
                self._back_off(now)
                return AutoAdvice(level=level, winner=winner, why_not_auto=too_slow)
            cost = COST.format(est, frame_ms, floor_words(p.floor_ms, est), d=edge_places(est, p.floor_ms))
        # the streak: verdicts in a row naming one winner, each on frames the last one did not see, at most
        # streak_gap_s apart (a skipped look or an empty pool between them ended it already)
        if (winner != self._streak_winner or self._streak == 0
                or (self._streak_at is not None and now - self._streak_at > p.streak_gap_s)):
            self._streak_winner, self._streak, self._definite = winner, 1, int(definite)
        elif fresh:
            self._streak += 1
            self._definite = self._definite + 1 if definite else 0
        self._streak_at = float(now)
        same_input = seamless(winner, live)
        said = (f"{winner.label} covers {cov_w:.0f} % of the dancers, {live.label} {cov_l:.0f} % "
                f"({cost or f'{mw:.1f} ms a frame against {ml:.1f}'})")
        adv = AutoAdvice(kind="seen", level=level, winner=winner, seamless=same_input, streak=self._streak,
                         text=said, said=said, too_slow=too_slow)
        if self._streak < p.wins or (too_slow and self._definite < p.wins):
            # seen once -- a slower winner: not yet over the definite bar on wins looks in a row: the next look
            # comes at once, to confirm it or not
            self._look_now()
            adv.why_not_auto = SEEN_ONCE
            if self.mode == MODE_AUTO and same_input and definite and not too_slow:
                adv.held = self._holds(now, winner)    # a suggestion on screen it confirms says what holds NOW
            return adv
        self._back_off(now)               # shown (or switched to): the looks back off while the operator reads it
        why = held = ""
        if self.mode != MODE_AUTO:
            why = "Auto: suggest does not switch by itself"
        elif not same_input:
            why = "another input size needs a few seconds without tracking: apply it by hand"
        elif too_slow:
            why = f"{too_slow}: {NEVER_BY_ITSELF}"       # the strict rule: advice only, whatever the floor
        elif not definite:
            why = f"the gain is under {100 * p.definite_gain:.0f} points"
        elif self._definite < p.wins:
            why = f"{p.wins} looks in a row over {100 * p.definite_gain:.0f} points needed"
        else:
            why = held = self._holds(now, winner)
        if why:
            adv.kind, adv.why_not_auto, adv.held = "suggest", why, held
            adv.text = suggestion_text(said, self._streak, held)
            return adv
        adv.kind = "apply"
        adv.text = f"switching to {winner.label}: {said}"
        return adv

    def _holds(self, now: float, winner: Candidate) -> str:
        """What holds an automatic switch to ``winner`` back at ``now``, said on the suggestion's line: the dwell
        after a switch, or the way back to the setting just left; "" when neither holds."""
        p = self.params
        if self._switched_at is not None and now - self._switched_at < p.dwell_s:
            return DWELL
        if winner == self._left and self._switched_at is not None and now - self._switched_at < p.back_s:
            return JUST_LEFT
        return ""

    # -- a suggestion on screen -----------------------------------------------------------------------------
    def unconfirmed(self) -> None:
        """A look did not confirm the suggestion on screen (a « keep », another winner, no verdict -- the referee's
        refusal among them --, a refusal of the « slower » rule or of the floor, no room for a gain): the next look
        comes as soon as a full pool of fresh frames is kept (``run_frames``: about 150 s of dancing), not after the
        back-off nor after the wait that follows a look the referee could not judge -- a second look that does not
        confirm it withdraws it.  The back-off's series is kept: a look that confirms it backs off as before."""
        self._keep_at = None
        self._none_at = None

    def withdrawn(self) -> None:
        """A suggestion went because the comparison it was made on no longer holds (the excluded cells, the live
        setting, the scene changed): the streak ends with it -- the next suggestion needs two looks in a row on the
        new comparison."""
        self._end_streak()

    def failed(self, now: float, newest_frame_t: Optional[float] = None) -> None:
        """A look raised: no look for ``keep_wait`` from now, as after a « keep » (doubled if the next one fails
        too) -- its frames stay fresh, and the next look would run the same 150 inferences at once.
        ``newest_frame_t``: the newest frame of a look that raised while a suggestion was on screen (``unconfirmed``
        follows, which ends the wait): its frames count as looked at, so the next look waits for a full fresh pool
        rather than run on them at once."""
        self._back_off(now)
        if newest_frame_t is not None:
            self._seen_until = max(self._seen_until or newest_frame_t, float(newest_frame_t))

    # -- the operator's Dismiss ----------------------------------------------------------------------------
    def dismissed(self, now: float, winner: Candidate, for_s: float) -> None:
        """The operator dismissed the suggestion of ``winner``: not now.  The streak is over, and that setting is
        neither suggested nor switched to by itself for ``for_s`` (``snoozed``), or until ``reset`` (a new scene),
        ``end_snooze`` or ``turned_off``; after it, a new streak."""
        self._end_streak()
        self._snoozed, self._snoozed_until = winner, float(now) + max(0.0, float(for_s))

    def snoozed(self, now: float) -> Optional[Candidate]:
        """The setting a Dismiss put aside, while it lasts; None once it is over."""
        if self._snoozed is not None and now >= self._snoozed_until:
            self._snoozed, self._snoozed_until = None, None
        return self._snoozed

    def end_snooze(self) -> None:
        """A project load: a setting put aside by a Dismiss may be suggested again."""
        self._snoozed, self._snoozed_until = None, None

    def turned_off(self) -> None:
        """The mode went off: what this session's mode was told and saw ends with it, so a mode set again starts
        fresh -- the snooze of a Dismiss ends, and so does the streak (a verdict seen before off never counts
        towards a switch after it).  The dwell after a switch, the way back and the back-offs stay: they keep the
        show from a switch too soon, whatever the mode did meanwhile."""
        self._end_streak()
        self.end_snooze()

    def switched(self, now: float, old: Candidate, new: Candidate) -> None:
        """The live setting changed, by the app or by the operator: the dwell starts, the streak is over (the
        frames kept so far describe the old setting: the controller starts its pool again)."""
        self._switched_at = float(now)
        self._left = old
        self._end_streak()
        self._seen_until = None
        self._none_at = None
        self._keeps, self._keep_at, self._keep_lost = 0, None, None
