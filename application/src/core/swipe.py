"""Settings swipe (Plan Cordée P5): which model and input size suit THIS scene?

The usable settings are few: yolo11l or yolo11x, from the 1280 square to the ROI's own rectangle.  Which one
suits depends on the scene (light, contrast, wall texture), so instead of choosing once by hand the app keeps a
few frames where the live setting leaves a dancer without a box, runs them through the candidates, and names
the one that covers the dancers best.

A live comparison has no truth.  The referee is the foreground (``core.foreground``: the frame against the
empty-wall plate), which does not depend on the model: a candidate is scored by the **dancer groups with a
box on them**, and guarded by its **loose boxes** -- boxes with no foreground under them.  On the 08/10 façade
takes that score ranks x / l at 1760 x 960 / 1920 x 1056 like the hand truth, and 30 lost frames pick a setting
within about a point of the best (hub note 2026-11-26-settings-swipe).

Three parts, all free of torch and cv2 so the gates run anywhere:

* ``Observation`` + ``score_frame``: one frame's foreground against one candidate's boxes;
* ``FrameCollector``: which live frames are kept for the swipe;
* ``Tally`` + ``decide``: the candidates' totals, and the verdict the operator reads.

Coordinates are original camera pixels everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.input_shape import Shape, engine_suffix

Box = Tuple[float, float, float, float, float]        # x0, y0, x1, y1, conf
Group = Tuple[float, float, float]                    # centroid x, y, area (px^2)


@dataclass(frozen=True)
class Candidate:
    """One setting the swipe can try: a model and its network input."""
    model: str                        # "yolo11l-pose"
    imgsz: int                        # the square size; with ``shape`` it stays the project's yolo_imgsz
    shape: Optional[Shape] = None     # (height, width) rectangle, None for the square

    @property
    def key(self) -> str:
        return f"{self.model}@{engine_suffix(self.imgsz, self.shape)}"

    @property
    def label(self) -> str:
        """What the operator reads: ``l @ 1920 x 1056``, ``x @ 1280``."""
        short = self.model.replace("yolo11", "").replace("-pose", "") or self.model
        size = f"{self.shape[0]} x {self.shape[1]}" if self.shape else str(int(self.imgsz))
        return f"{short} @ {size}"

    @property
    def engine_file(self) -> str:
        return f"{self.model}_{engine_suffix(self.imgsz, self.shape)}.engine"


@dataclass
class SwipeParams:
    frames: int = 30                  # frames a swipe runs on
    min_gap_s: float = 0.5            # between two kept frames (neighbours say the same thing)
    group_min_area: float = 300.0     # px^2: a foreground group this large is a dancer to cover
    box_margin: float = 0.10          # a box covers a group whose centre lies within it, grown by this share
    conf: float = 0.15                # boxes under this confidence do not count
    min_groups: int = 20              # fewer dancer groups than this in the kept frames: no verdict
    min_gain: float = 0.03            # the winner must cover this much more than the live setting to be named
    loose_floor: float = 0.05         # loose boxes per frame every setting is allowed
    loose_ratio: float = 1.5          # ... or this many times the live setting's, whichever is larger


@dataclass
class Observation:
    """What the referee knows of one frame, whatever the model: its foreground."""
    groups: List[Group]                       # every foreground group, largest first
    mask: Optional[np.ndarray] = None         # uint8 foreground mask (ROI, downscaled ``ds`` times)
    x0: float = 0.0                           # original px of the mask's top-left corner
    y0: float = 0.0
    ds: int = 4

    @classmethod
    def from_fg(cls, fg) -> Optional["Observation"]:
        """From a ``core.foreground.FgFrame``; None when the foreground is off or invalid for this frame
        (no plate, another exposure or crop, lights changed): such a frame cannot referee anything."""
        if fg is None or not getattr(fg, "valid", False) or getattr(fg, "mask", None) is None:
            return None
        groups = sorted(((float(b.x), float(b.y), float(b.area)) for b in fg.blobs), key=lambda g: -g[2])
        return cls(groups=groups, mask=fg.mask, x0=float(fg.x0), y0=float(fg.y0), ds=int(fg.ds))

    def dancer_groups(self, params: SwipeParams) -> List[Group]:
        return [g for g in self.groups if g[2] >= params.group_min_area]


def box_holds(box: Sequence[float], x: float, y: float, margin: float = 0.0) -> bool:
    x0, y0, x1, y1 = box[:4]
    w, h = x1 - x0, y1 - y0
    return x0 - margin * w <= x <= x1 + margin * w and y0 - margin * h <= y <= y1 + margin * h


@dataclass
class FrameScore:
    groups: int = 0       # dancer groups in the frame
    covered: int = 0      # ... with a box on them
    boxes: int = 0        # boxes at or over the confidence
    loose: int = 0        # ... with no foreground group under them


def bare_groups(obs: Observation, boxes: Sequence[Box], params: SwipeParams) -> List[Group]:
    """The dancer groups no box covers: what makes a frame a « lost dancer » frame."""
    kept = [b for b in boxes if b[4] >= params.conf]
    return [g for g in obs.dancer_groups(params)
            if not any(box_holds(b, g[0], g[1], params.box_margin) for b in kept)]


def score_frame(obs: Observation, boxes: Sequence[Box], params: SwipeParams) -> FrameScore:
    kept = [b for b in boxes if b[4] >= params.conf]
    dancers = obs.dancer_groups(params)
    covered = sum(any(box_holds(b, g[0], g[1], params.box_margin) for b in kept) for g in dancers)
    loose = sum(not any(box_holds(b, g[0], g[1]) for g in obs.groups) for b in kept)
    return FrameScore(groups=len(dancers), covered=covered, boxes=len(kept), loose=loose)


@dataclass
class Tally:
    """One candidate's totals over the swipe's frames."""
    frames: int = 0
    groups: int = 0
    covered: int = 0
    boxes: int = 0
    loose: int = 0
    ms: List[float] = field(default_factory=list)     # inference time per frame

    def add(self, score: FrameScore, ms: Optional[float] = None) -> None:
        self.frames += 1
        self.groups += score.groups
        self.covered += score.covered
        self.boxes += score.boxes
        self.loose += score.loose
        if ms is not None:
            self.ms.append(float(ms))

    @property
    def coverage(self) -> float:
        return self.covered / self.groups if self.groups else 0.0

    @property
    def loose_per_frame(self) -> float:
        return self.loose / self.frames if self.frames else 0.0

    @property
    def ms_p50(self) -> Optional[float]:
        return float(np.median(self.ms)) if self.ms else None


@dataclass
class SwipeRow:
    candidate: Candidate
    coverage: float
    loose_per_frame: float
    ms_p50: Optional[float]
    ok: bool = True           # False: set aside (too many loose boxes, too slow); ``note`` says why
    note: str = ""


@dataclass
class SwipeResult:
    current: Candidate
    winner: Optional[Candidate]       # the best setting, None when there is no verdict
    switch: bool                      # True: the winner is another setting, worth applying
    rows: List[SwipeRow]              # every candidate, best first
    reason: str                       # one plain sentence for the operator
    frames: int = 0
    groups: int = 0

    def headline(self) -> str:
        return self.reason


def decide(tallies: Dict[Candidate, Tally], current: Candidate,
           params: Optional[SwipeParams] = None) -> SwipeResult:
    """The verdict.  A candidate with too many loose boxes is set aside; among the others the one that covers
    the most dancer groups wins, and it is named only when it beats the live setting by ``min_gain``."""
    p = params or SwipeParams()
    cur = tallies.get(current)
    frames = max((t.frames for t in tallies.values()), default=0)
    groups = max((t.groups for t in tallies.values()), default=0)
    loose_cap = max(p.loose_floor, p.loose_ratio * (cur.loose_per_frame if cur else 0.0))
    rows = []
    for cand, t in tallies.items():
        ok = cand == current or t.loose_per_frame <= loose_cap
        note = "" if ok else f"{t.loose_per_frame:.2f} boxes a frame on nobody"
        rows.append(SwipeRow(cand, t.coverage, t.loose_per_frame, t.ms_p50, ok, note))
    rows.sort(key=lambda r: (not r.ok, -r.coverage))
    if groups < p.min_groups:
        return SwipeResult(current, None, False, rows, frames=frames, groups=groups,
                           reason=f"no verdict: {groups} dancer groups seen, {p.min_groups} needed")
    best = next((r for r in rows if r.ok), None)
    if best is None:
        return SwipeResult(current, None, False, rows, "no verdict: no setting passed", frames, groups)
    cur_cov = cur.coverage if cur else 0.0
    if best.candidate == current or best.coverage - cur_cov < p.min_gain:
        return SwipeResult(current, current, False, rows, frames=frames, groups=groups,
                           reason=f"keep {current.label}: it covers {100 * cur_cov:.0f} % of the dancers, "
                                  f"no other setting does clearly better")
    return SwipeResult(current, best.candidate, True, rows, frames=frames, groups=groups,
                       reason=f"{best.candidate.label} covers {100 * best.coverage:.0f} % of the dancers, "
                              f"{current.label} {100 * cur_cov:.0f} %")


@dataclass
class SwipeFrame:
    t: float                  # seconds (the loop's clock)
    image: np.ndarray         # the raw camera frame, as the pipeline received it
    obs: Observation
    lost: bool                # the live setting left a dancer group bare on it


class FrameCollector:
    """Keeps the frames a swipe will run on.  A frame where the live setting leaves a dancer group bare is
    the informative kind (30 of them say as much as 100 random frames); frames with a covered dancer fill
    the rest, so a scene the live setting already handles still gets a verdict (« keep »)."""

    def __init__(self, params: Optional[SwipeParams] = None):
        self.params = params or SwipeParams()
        self._lost: List[SwipeFrame] = []
        self._seen: List[SwipeFrame] = []
        self._last_t: Optional[float] = None

    def reset(self) -> None:
        self._lost.clear()
        self._seen.clear()
        self._last_t = None

    def offer(self, t: float, image: np.ndarray, obs: Optional[Observation],
              boxes: Sequence[Box]) -> bool:
        """One live frame with its foreground and the live setting's boxes; True when it was kept.
        ``image`` is stored as given: pass a copy if the caller reuses its buffer."""
        p = self.params
        if obs is None or not obs.dancer_groups(p):
            return False
        if self._last_t is not None and 0 <= t - self._last_t < p.min_gap_s:
            return False
        lost = bool(bare_groups(obs, boxes, p))
        pool = self._lost if lost else self._seen
        if len(pool) >= p.frames:
            if lost:
                return False
            pool.pop(0)               # covered frames: keep the newest
        pool.append(SwipeFrame(float(t), image, obs, lost))
        self._last_t = float(t)
        return True

    @property
    def lost_frames(self) -> int:
        return len(self._lost)

    @property
    def frames(self) -> List[SwipeFrame]:
        """The swipe's frames: the lost ones first, covered ones to fill up to ``params.frames``."""
        fill = self._seen[-max(0, self.params.frames - len(self._lost)):] if len(self._lost) < self.params.frames else []
        return list(self._lost) + list(fill)

    @property
    def progress(self) -> float:
        return min(1.0, len(self._lost) / float(self.params.frames)) if self.params.frames else 1.0

    @property
    def ready(self) -> bool:
        return len(self._lost) >= self.params.frames
