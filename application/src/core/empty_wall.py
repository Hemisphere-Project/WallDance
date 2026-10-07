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

A candidate passes when at most ``max_ghost_frames`` of its ``frames`` observed frames hold a raw YOLO
person (before the tracker and every filter) with a box confidence >= ``margin`` x the live
confidence: the margin leaves room for the operator's sensitivity dial.  When no candidate passes,
the weakest is kept and the result names where the ghost is (an object to remove or an exclusion to
paint).  Pure logic: the caller sets the enhancer from ``current()`` and feeds each processed frame's
raw detections (``FrameProcessor.last_raw_dets``: conf, x, y, h in original px).

Measured on the night project's bright empty take (slot_1 21:16, wall band, x@1280,
``tests/empty_wall_check.py --full``): from the 10:33 values, gamma 1.8 / CLAHE 1.5 -> a person-sized
ghost by the equipment (x 654, y 685, h 174) up to conf 0.56 in 17/30 frames; 1.53 -> 8/30 (max 0.24);
1.27 -> 1-3/30 (0.21); 1.0 and below -> none.  Last night's gamma 0.73 / CLAHE 2.5 -> none.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

GAMMA_FLOOR = 0.8          # AUTOCAL_GAMMA_BOUNDS[0]
CLAHE_STEPS = (1.5, 1.0)   # tried at the weakest gamma when the calibrated clip is above them


@dataclass
class CandidateResult:
    gamma: float
    clahe: float
    frames: int = 0
    ghost_frames: int = 0
    max_conf: float = 0.0
    worst: Optional[Tuple[float, float, float, float]] = None   # conf, x, y, h of the strongest ghost

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

    def summary(self) -> str:
        lines = [f"Empty-wall YOLO check (persons >= {self.conf_limit:.2f}):"]
        for c in self.tried:
            mark = "ok" if c.ghost_frames == 0 else f"{c.ghost_frames}/{c.frames} frames"
            near = f", max {c.max_conf:.2f}" if c.max_conf > 0 else ""
            lines.append(f"  {c.label}: {mark}{near}")
        if self.clean:
            lines.append(f"Kept gamma {self.gamma:.2f} / CLAHE {self.clahe:.1f}")
        else:
            w = max((c for c in self.tried if c.worst), key=lambda c: c.worst[0], default=None)
            where = f" near x={w.worst[1]:.0f}, y={w.worst[2]:.0f}" if w else ""
            lines.append(f"YOLO sees a person on the EMPTY wall{where} even at the weakest setting: "
                         "remove the object or paint an exclusion there, then calibrate again.")
        return "\n".join(lines)

    def log_line(self) -> str:
        tried = "; ".join(f"{c.gamma:.2f}/{c.clahe:.1f}:{c.ghost_frames}/{c.frames}@{c.max_conf:.2f}"
                          for c in self.tried)
        return (f"[EmptyWall] kept gamma={self.gamma:.2f} clahe={self.clahe:.1f} clean={self.clean} "
                f"limit={self.conf_limit:.2f} tried {tried}")


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
    def __init__(self, gamma0: float, clahe0: float, confidence: float, *, frames: int = 30,
                 settle: int = 4, margin: float = 0.8, max_ghost_frames: int = 2):
        self.conf_limit = max(0.05, float(margin) * float(confidence))
        self.frames = int(frames)
        self.settle = int(settle)
        self.max_ghost_frames = int(max_ghost_frames)
        self.steps = ladder(gamma0, clahe0)
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
                self.result = EmptyWallResult(c.gamma, c.clahe, True, self.conf_limit, self.tried)
                return None
            if self._i + 1 >= len(self.steps):
                self.result = EmptyWallResult(c.gamma, c.clahe, False, self.conf_limit, self.tried)
                return None
            self._i += 1
            self._seen = 0
            self.tried.append(CandidateResult(*self.steps[self._i]))
            return self.steps[self._i]
        return None
