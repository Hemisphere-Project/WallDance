"""Empty-wall YOLO check (D29, 2026-10-07): the last stage of Calibrate.

Calibrate judged the enhancement only by the MOG2 false-positive rate.  On the 2026-10-07 10:33
re-calibration of the night project (the empty-wall playback, a dark noisy scene) it picked gamma 1.8 /
CLAHE 1.5, and YOLO then confirmed false persons on the empty wall at 0.16-0.33 (a still "person" by
the equipment left of the door) on every night take; the previous calibration (gamma 0.73 / CLAHE 2.5)
had none.  Brightening helps YOLO see a dim dancer, but past some point it makes persons out of the
wall.  So after the scene window, the pipeline runs on the same empty wall with each candidate of a
ladder, strongest first, and keeps the first one where YOLO finds nobody:

  gamma g0 (the calibrated one) -> 2/3 and 1/3 of the way to 1.0 -> 1.0 -> 0.8,
  then CLAHE lowered at the weakest gamma.

A candidate passes when none (``max_ghost_frames`` = 0) of its ``frames`` (120, 6 s; a rung with a ghost
fails at its first one, so only the kept rung is observed that long) observed frames holds a raw
YOLO person (before the tracker and every filter) with a box confidence >= ``margin`` x the live
confidence: the margin leaves room for the operator's sensitivity dial.  A SPORADIC ghost is enough to
fail: gamma 1.0 showed one stray frame in 40 (conf 0.14-0.15) on 3 of 4 runs on the bright empty take, and
under the app's conditions (project ROI, whole dark takes) that same rare false person at the door equipment
(conf 0.17-0.28) stole the dancer's bridged track on s2c / s4 / s6 (2026-10-07 demo; gamma 0.8 and 0.73: none).
(Earlier tolerances, 2 of 30 then 1 of 40, let it through.)  The project's calibrated enhancement, when
there is one, is tried FIRST (``keep``) and kept when it passes.  When no candidate passes,
the weakest is kept and the result names where the ghost is (an object to remove or an exclusion to
paint).  Pure logic: the caller sets the enhancer from ``current()`` and feeds each processed frame's
raw detections (``FrameProcessor.last_raw_dets``: conf, x, y, h in original px).

Measured on the night project's bright empty take (slot_1 21:16, wall band, x@1280,
``tests/empty_wall_check.py --full``): from the 10:33 values, gamma 1.8 / CLAHE 1.5 -> a person-sized
ghost by the equipment (x 654, y 685, h 174) up to conf 0.56 in 17/30 frames; 1.53 -> 8/30 (max 0.24);
1.27 -> 1-3/30 (0.21); 1.0 and below -> none.  Last night's gamma 0.73 / CLAHE 2.5 -> none.

X3 (roadmap 2026-11-25, emptiness first).  On 08/10 a dancer was in view at the 20:06 Calibrate: every
rung "failed" on her and the ladder walked the enhancement down to gamma 0.8 / CLAHE 1.0, which the whole
`mur` evening then ran at (worth about 18 points of recall).  A ghost the enhancement makes and a person
differ in how often YOLO sees them at a neutral enhancement: the ghost above, at gamma 1.0, in 0 of 30
frames (a stray 1 in 40 on the bright take); a dancer in most frames.  So ``PersonWatch`` watches
``PERSON_FRAMES`` frames and calls it a person when at least ``PERSON_MIN_FRAC`` of them hold a box at
the live confidence.  Calibrate runs it first, at gamma <= ``NEUTRAL_GAMMA``, and stops when someone is
there (``where`` says where); and the ladder runs it on a rung at or under neutral before stepping lower:
it never goes under neutral because of a person -- a person there ends the check with ``person`` set,
and the caller keeps the enhancement it had.  ``EmptyWallResult.ceiling`` records the strongest clean rung.

X3m (27/11, the show laptop).  A shape of the room the detector took for a person stopped Calibrate, and kept
stopping it once the technician had masked it (the Exclusion Mask): the watch and the ladder read the raw boxes
from before RUN's exclusion step.  Calibrate now judges the wall as RUN will: the caller leaves out the boxes
in an excluded cell (``MaskedBoxes``, with the pipeline's own test on the box's centre) and the log says so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

GAMMA_FLOOR = 0.8          # AUTOCAL_GAMMA_BOUNDS[0]
CLAHE_STEPS = (1.5, 1.0)   # tried at the weakest gamma when the calibrated clip is above them
NEUTRAL_GAMMA = 1.0        # X3: the gamma the person watch runs at; the ladder never goes under it for a person
PERSON_FRAMES = 30         # X3: frames watched to tell a person (most frames) from a ghost (a stray frame)
PERSON_MIN_FRAC = 0.2      # X3: ... of which this share holds a box at the live confidence = someone is there
PERSON_SETTLE = 4          # X3: frames before the watch counts, after an enhancement change
# X3m: the two ways out for an object the detector takes for a person, the mask first (the app's own words)
OBJECT_WAYS_OUT = "exclude it (1 Rig > Exclusion Mask) or move the Region of Interest off it"


@dataclass
class MaskedBoxes:
    """X3m (27/11, the show laptop): the raw boxes Calibrate leaves out because RUN drops them -- they sit in
    an excluded cell of the Exclusion Mask.  ``zone_ok`` is the pipeline's own test
    (``FrameProcessor._zone_ok_fn``: an original-px point -> outside the excluded cells), applied to the box's
    centre as RUN's exclusion step does.  ``keep`` returns one frame's boxes without those and counts what it
    left out, for the log (remote support can tell a box was ignored)."""
    frames: int = 0                        # frames fed
    hit_frames: int = 0                    # ... holding at least one box left out
    boxes: int = 0                         # boxes left out
    top: Optional[Tuple[float, float, float, float]] = None   # conf, x, y, h of the strongest one left out

    def keep(self, dets: Iterable[Sequence[float]], zone_ok=None) -> list:
        dets = list(dets)
        self.frames += 1
        if zone_ok is None:
            return dets
        kept, out = [], []
        for d in dets:
            (kept if zone_ok(float(d[1]), float(d[2])) else out).append(d)
        if out:
            self.hit_frames += 1
            self.boxes += len(out)
            s = max(out, key=lambda d: float(d[0]))
            if self.top is None or float(s[0]) > self.top[0]:
                self.top = tuple(float(v) for v in s[:4])
        return kept

    def note(self) -> str:
        """'' when nothing was left out, else what and where (the log's words)."""
        if not self.boxes or self.top is None:
            return ""
        c, x, y, h = self.top
        return (f"ignored (Exclusion Mask): {self.boxes} box(es) in excluded cells in {self.hit_frames}/"
                f"{self.frames} frames, strongest conf={c:.2f} at x={x:.0f} y={y:.0f} h={h:.0f}")


@dataclass
class Sighting:
    """Where someone is (original px): the place seen most often (``PersonWatch.sighting``); ``hits`` of
    ``frames`` watched held a person."""
    x: float
    y: float
    h: float
    conf: float
    hits: int
    frames: int

    def where(self, region: Optional[Tuple[float, float, float, float]] = None) -> str:
        """'middle left of the wall (x 512, y 560)': thirds of ``region`` (the ROI, x, y, w, h), else of
        nothing -- the coordinates alone."""
        at = f"x {self.x:.0f}, y {self.y:.0f}"
        if region is None or region[2] <= 0 or region[3] <= 0:
            return f"at {at}"
        rx, ry, rw, rh = (float(v) for v in region)
        u, v = (self.x - rx) / rw, (self.y - ry) / rh
        if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0):
            return f"outside the Region of Interest ({at})"
        row = ("top", "middle", "bottom")[min(2, int(v * 3))]
        col = ("left", "centre", "right")[min(2, int(u * 3))]
        place = "centre" if (row, col) == ("middle", "centre") else f"{row} {col}"
        return f"{place} of the wall ({at})"


class PersonWatch:
    """X3: is someone in view?  ``frames`` frames (after ``settle``) of raw detections (conf, x, y, h);
    a person when at least ``min_frac`` of them hold a box at ``confidence`` or more.  Decides early once
    the count is reached or can no longer be."""

    def __init__(self, confidence: float, *, frames: int = PERSON_FRAMES, min_frac: float = PERSON_MIN_FRAC,
                 settle: int = 0):
        self.confidence = float(confidence)
        self.frames = max(1, int(frames))
        self.need = max(1, int(math.ceil(float(min_frac) * self.frames)))
        self.settle = max(0, int(settle))
        self.seen = 0
        self.hits = 0
        self._tops: List[Tuple[float, float, float, float]] = []
        self.person: Optional[bool] = None          # None while watching

    @property
    def done(self) -> bool:
        return self.person is not None

    def feed(self, dets: Iterable[Sequence[float]]) -> None:
        if self.done:
            return
        if self.settle > 0:
            self.settle -= 1
            return
        self.seen += 1
        top = max((d for d in dets if float(d[0]) >= self.confidence), key=lambda d: float(d[0]), default=None)
        if top is not None:
            self.hits += 1
            self._tops.append(tuple(float(v) for v in top[:4]))
        if self.hits >= self.need:
            self.person = True
        elif self.seen - self.hits > self.frames - self.need:
            self.person = False

    @property
    def sighting(self) -> Optional[Sighting]:
        """The place seen most often: the strongest box of each frame, grouped within half a body height
        (two dancers side by side stay two places, never their midpoint); the median of the largest group."""
        tops = self._tops
        if not tops:
            return None
        near = lambda a, b: max(abs(a[1] - b[1]), abs(a[2] - b[2])) <= 0.5 * max(a[3], b[3], 1.0)
        best = max(tops, key=lambda t: sum(near(t, u) for u in tops))
        group = [u for u in tops if near(best, u)]
        med = lambda i: sorted(t[i] for t in group)[len(group) // 2]
        return Sighting(x=med(1), y=med(2), h=med(3), conf=max(t[0] for t in group),
                        hits=self.hits, frames=self.seen)


@dataclass
class CandidateResult:
    gamma: float
    clahe: float
    frames: int = 0
    ghost_frames: int = 0
    max_conf: float = 0.0
    worst: Optional[Tuple[float, float, float, float]] = None   # conf, x, y, h of the strongest ghost
    watched: int = 0                       # X3: frames of the person watch after this rung failed
    person_hits: int = 0                   # ... holding a box at the live confidence

    @property
    def label(self) -> str:
        return f"gamma {self.gamma:.2f} / CLAHE {self.clahe:.1f}"


@dataclass
class EmptyWallResult:
    gamma: float
    clahe: float
    clean: bool
    conf_limit: float
    tried: List[CandidateResult] = field(default_factory=list)
    kept_current: bool = False             # the project's calibrated enhancement passed: left as it was
    person: Optional[Sighting] = None      # X3: someone in view -- the check stopped there, nothing to keep
    region: Optional[Tuple[float, float, float, float]] = None   # the ROI, for ``where``
    masked: Optional[MaskedBoxes] = None   # X3m: the boxes left out as RUN drops them (the caller's tally)

    @property
    def ceiling(self) -> Optional[Tuple[float, float]]:
        """X3: the strongest setting found clean on the empty wall (the kept rung), None if none was.  When
        ``kept_current``, the project's enhancement passed first and stronger rungs were not tried."""
        return (self.gamma, self.clahe) if self.clean else None

    def summary(self) -> str:
        lines = [f"Empty-wall YOLO check (persons >= {self.conf_limit:.2f}):"]
        for c in self.tried:
            mark = "ok" if c.ghost_frames == 0 else f"{c.ghost_frames}/{c.frames} frames"
            near = f", max {c.max_conf:.2f}" if c.max_conf > 0 else ""
            watch = f", someone in {c.person_hits}/{c.watched} more" if c.watched else ""
            lines.append(f"  {c.label}: {mark}{near}{watch}")
        if self.person is not None:
            lines.append(f"Someone is in view, {self.person.where(self.region)}: the check stopped there "
                         f"and lowered nothing. If it is a person: clear the wall. If it is an object: "
                         f"{OBJECT_WAYS_OUT}. Then Calibrate again.")
        elif self.clean and self.kept_current:
            lines.append(f"Kept the current gamma {self.gamma:.2f} / CLAHE {self.clahe:.1f}: YOLO sees nobody "
                         "on the empty wall with it")
        elif self.clean:
            lines.append(f"Kept gamma {self.gamma:.2f} / CLAHE {self.clahe:.1f}")
        else:
            w = max((c for c in self.tried if c.worst), key=lambda c: c.worst[0], default=None)
            where = f" near x={w.worst[1]:.0f}, y={w.worst[2]:.0f}" if w else ""
            lines.append(f"YOLO now and then sees a person on the EMPTY wall{where}, even at the weakest "
                         f"setting (kept): if an object there looks like a person, {OBJECT_WAYS_OUT}, "
                         "then Calibrate again.")
        return "\n".join(lines)

    def log_line(self) -> str:
        tried = "; ".join(f"{c.gamma:.2f}/{c.clahe:.1f}:{c.ghost_frames}/{c.frames}@{c.max_conf:.2f}"
                          + (f"+watch {c.person_hits}/{c.watched}" if c.watched else "")
                          for c in self.tried)
        masked = self.masked.note() if self.masked is not None else ""
        masked = f"; {masked}" if masked else ""           # X3m: say when a box was left out as RUN drops it
        if self.person is not None:
            p = self.person
            return (f"[EmptyWall] stopped: person at x={p.x:.0f} y={p.y:.0f} h={p.h:.0f} conf={p.conf:.2f} "
                    f"({p.hits}/{p.frames} frames at gamma={self.gamma:.2f} clahe={self.clahe:.1f}) "
                    f"limit={self.conf_limit:.2f} tried {tried}{masked}")
        return (f"[EmptyWall] kept gamma={self.gamma:.2f} clahe={self.clahe:.1f} clean={self.clean} "
                f"limit={self.conf_limit:.2f} tried {tried}{masked}")


def ladder(gamma0: float, clahe0: float) -> List[Tuple[float, float]]:
    """Candidates, strongest first (see the module doc)."""
    g0 = float(gamma0)
    if g0 > 1.0:
        gammas = [g0, 1.0 + (g0 - 1.0) * 2 / 3, 1.0 + (g0 - 1.0) / 3, 1.0, GAMMA_FLOOR]
    else:
        gammas = [g0, GAMMA_FLOOR] if g0 > GAMMA_FLOOR else [g0]
    out: List[Tuple[float, float]] = []
    for g in gammas:
        if not out or out[-1][0] - g >= 0.05:
            out.append((g, float(clahe0)))
    g_last = out[-1][0]
    for c in CLAHE_STEPS:
        if c < out[-1][1] - 0.05:
            out.append((g_last, c))
    return out


class EmptyWallCheck:
    def __init__(self, gamma0: float, clahe0: float, confidence: float, *, frames: int = 120,
                 settle: int = 4, margin: float = 0.8, max_ghost_frames: int = 0,
                 keep: Optional[Tuple[float, float]] = None,
                 person_frames: int = PERSON_FRAMES, person_min_frac: float = PERSON_MIN_FRAC,
                 region: Optional[Tuple[float, float, float, float]] = None):
        """``keep``: the project's already calibrated (gamma, CLAHE), tried FIRST and kept when YOLO
        sees nobody with it -- Calibrate must not trade a working enhancement for the scene formula's
        pick (2026-10-07 demo: gamma 0.73 / CLAHE 2.5 -> 1.0 / 1.5 let a sporadic false person at the
        door equipment steal the dancer's track on the dark takes).

        X3: a rung at or under ``NEUTRAL_GAMMA`` that fails is watched ``person_frames`` more frames
        (``PersonWatch`` at the live ``confidence``) before the ladder steps lower; a person there ends
        the check (``result.person``).  ``person_frames=0`` turns the watch off.  ``region``: the ROI
        (x, y, w, h), only for the result's ``where``."""
        self.conf_limit = max(0.05, float(margin) * float(confidence))
        self.person_conf = float(confidence)
        self.person_frames = int(person_frames)
        self.person_min_frac = float(person_min_frac)
        self.region = tuple(region) if region is not None else None
        self._watch: Optional[PersonWatch] = None
        self.frames = int(frames)
        self.settle = int(settle)
        self.max_ghost_frames = int(max_ghost_frames)
        self.steps = ladder(gamma0, clahe0)
        self._keep = None
        if keep is not None:
            kg, kc = float(keep[0]), float(keep[1])
            same = lambda s: abs(s[0] - kg) < 0.02 and abs(s[1] - kc) < 0.05
            self.steps = [(kg, kc)] + [s for s in self.steps if not same(s)]
            self._keep = (kg, kc)
        self._i = 0
        self._seen = 0
        self.tried: List[CandidateResult] = [CandidateResult(*self.steps[0])]
        self.result: Optional[EmptyWallResult] = None

    @property
    def done(self) -> bool:
        return self.result is not None

    def current(self) -> Optional[Tuple[float, float]]:
        return None if self.done else self.steps[self._i]

    def progress(self) -> float:
        per = self.settle + self.frames
        return min(1.0, (self._i * per + self._seen) / float(per * len(self.steps)))

    def feed(self, dets: Iterable[Sequence[float]]) -> Optional[Tuple[float, float]]:
        """One processed frame's raw detections (conf, x, y, h).  Returns the candidate to apply
        when it changes (the next rung), else None; ``done`` / ``result`` when finished."""
        if self.done:
            return None
        dets = list(dets)
        if self._watch is not None:                # X3: a failed rung at / under neutral -- someone there?
            return self._feed_watch(dets)
        self._seen += 1
        if self._seen <= self.settle:              # the enhancer change reaches YOLO a frame or two later
            return None
        c = self.tried[-1]
        c.frames += 1
        ghosts = [d for d in dets if float(d[0]) >= self.conf_limit]
        top = max(dets, key=lambda d: float(d[0]), default=None)
        if top is not None and float(top[0]) > c.max_conf:
            c.max_conf = float(top[0])
            c.worst = tuple(float(v) for v in top[:4])
        if ghosts:
            c.ghost_frames += 1
        if c.ghost_frames > self.max_ghost_frames or c.frames >= self.frames:
            if c.ghost_frames <= self.max_ghost_frames:
                self.result = EmptyWallResult(c.gamma, c.clahe, True, self.conf_limit, self.tried,
                                              kept_current=self._keep is not None and self._i == 0,
                                              region=self.region)
                return None
            if self.person_frames > 0 and c.gamma <= NEUTRAL_GAMMA + 0.02:
                self._watch = PersonWatch(self.person_conf, frames=self.person_frames,
                                          min_frac=self.person_min_frac)
                return None
            return self._next_rung()
        return None

    def _feed_watch(self, dets) -> Optional[Tuple[float, float]]:
        w = self._watch
        w.feed(dets)
        if not w.done:
            return None
        self._watch = None
        c = self.tried[-1]
        c.watched, c.person_hits = w.seen, w.hits
        if w.person:                               # never under neutral because of a person: stop here
            self.result = EmptyWallResult(c.gamma, c.clahe, False, self.conf_limit, self.tried,
                                          person=w.sighting, region=self.region)
            return None
        return self._next_rung()                   # a ghost, not a person: on down the ladder

    def _next_rung(self) -> Optional[Tuple[float, float]]:
        c = self.tried[-1]
        if self._i + 1 >= len(self.steps):
            self.result = EmptyWallResult(c.gamma, c.clahe, False, self.conf_limit, self.tried,
                                          region=self.region)
            return None
        self._i += 1
        self._seen = 0
        self.tried.append(CandidateResult(*self.steps[self._i]))
        return self.steps[self._i]
