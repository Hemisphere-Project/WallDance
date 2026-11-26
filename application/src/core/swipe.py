"""Settings swipe (Plan Cordée P5): which model and input size suit THIS scene?

The usable settings are few: yolo11l or yolo11x, from the 1280 square to the ROI's own rectangle.  Which one
suits depends on the scene (light, contrast, wall texture), so instead of choosing once by hand the app keeps
frames with dancers on the wall, runs them through the candidates, and names the one that covers the dancers
best -- when it is proven better than the live one.

A live comparison has no truth.  The referee is the foreground (``core.foreground``: the frame against the
empty-wall plate), which does not depend on the model:

* a candidate is scored by the **dancer groups with a box on them** (a foreground group of ``group_min_area``
  px^2 or more whose centre lies in a box grown by ``box_margin``, or -- two dancers in contact, a dancer and her
  shadow -- of which a box holds ``cover_share`` of the foreground; one box covers one group); the dancers are the
  groups some candidate covers -- a group nobody covers (a rope, a patch of light the plate does not know) says
  nothing about which setting is better;
* and guarded by its **boxes on nobody**: a box with less than ``loose_min_area`` px^2 of foreground under it
  (the mask, as F3 measures it).  The rule shipped first -- no foreground group *centre* inside the box -- read
  0.3-0.4 such boxes a frame for every setting on the 08/10 takes mur2 7 and 8: the dancer's group (dancer and
  shadow, two dancers joined) has its centre beside her box.  With the mask, a box on a dancer always has
  foreground under it; a box on a figure the empty wall already shows has none (hub note
  2026-11-26-settings-swipe, swipe-core report).  Without a mask the centre rule is the fallback.

A candidate's **net** is (dancers covered - ``loose_weight`` x boxes on nobody) / dancers: a box on nobody costs
as much as a dancer left bare (the scenario score weighs drops and ghosts alike).  A candidate is never judged on
its confidence: a second look confirms ghosts as readily as dancers (the 07/10 multi-pass study).

The frames: the newest ``frames`` frames with a dancer group on the wall, at least ``min_gap_s`` apart, WHATEVER
the live setting did on them.  (Until 26/11 the swipe kept the frames where the live setting lost a dancer: picked
by the live model's own failures, they favour any other model, and with a good live setting a worse one was named
on up to 14 % of swipes -- the P5 review's I5.)  On an unbiased sample the paired, frame-by-frame difference between
two settings is an unbiased estimate of their difference on the stream; « lost » stays a flag on each frame (the
operator's progress, the background swipe's trigger) and chooses nothing.

The verdict: something changes only when a setting is proven better than the live one -- its net beats the live
one's by its bar AND by enough standard errors for the number of settings compared: the error comes from batch
means over consecutive frames (neighbouring frames are not independent), the z from ``alpha`` shared out over the
alternatives (Bonferroni), and the bar is ``min_gain`` plus ``cost_gain`` for every doubling of the cost per
frame over the live one's (measured ms when the tallies carry them, else ``Candidate.cost``): a setting that costs
more must gain more.  Then the setting with the best net among those clearing their bar is named (one at least as
good as a proven one is better too), and among those with its network input within ``tie`` of it, the cheapest;
across input sizes the better net wins.  Too few dancers, or nothing clearly better: « keep » or « no
verdict », in one plain sentence.

The plate check: when even the cleanest of the settings that see the dancers puts more than ``referee_max_loose``
boxes a frame where the empty-wall picture shows nobody, the picture is not of this wall as it is now (people
baked into a median, another light, a figure every model sees): no verdict.  One ghost-prone model does not
trip it.  It cannot see a picture that merely misses the light change of the last minutes (the foreground then
floods or vanishes; the grouping handles the first, the dancer count the second).

The defaults (150 frames 1 s apart: two and a half minutes of dancing, 15 s of inference on the laptop for the
planner's three settings) come from ``tests/swipe_gate.py`` on the 08/10 facade takes, per swipe, for every live
setting -- three takes of one evening: fitted there, and the window size by mur2 8 alone.

Three parts, all free of torch and cv2 so the gates run anywhere:

* ``Observation`` + ``score_frame``: one frame's foreground against one candidate's boxes;
* ``FrameCollector``: which live frames are kept for the swipe;
* ``Tally`` + ``decide``: the candidates' totals, and the verdict the operator reads.

Coordinates are original camera pixels everywhere.  ``tests/swipe_gate.py`` checks the rule on recorded takes.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.input_shape import Shape, engine_suffix, target_hw

Box = Tuple[float, float, float, float, float]        # x0, y0, x1, y1, conf
Group = Tuple[float, float, float]                    # centroid x, y, area (px^2)

# GPU cost per network pixel, relative to yolo11x.  The laptop's engine-only p50 (P2, 2026-11-26): l @ 1920 x 1056
# 18.8 ms against x @ 1920 x 1056 29.1 ms (0.65); l @ 1760 x 960 15.5 ms, x @ 1280 22.3 ms.
MODEL_COST = {"n": 0.15, "s": 0.25, "m": 0.45, "l": 0.65, "x": 1.0}


def _short(model: str) -> str:
    return model.replace("yolo11", "").replace("yolo26", "").replace("-pose", "") or model


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
        size = f"{self.shape[0]} x {self.shape[1]}" if self.shape else str(int(self.imgsz))
        return f"{_short(self.model)} @ {size}"

    @property
    def engine_file(self) -> str:
        return f"{self.model}_{engine_suffix(self.imgsz, self.shape)}.engine"

    @property
    def cost(self) -> float:
        """Relative GPU cost of one frame (megapixels x the model's weight, yolo11x = 1): the cheaper of two
        settings within noise of each other is named."""
        h, w = target_hw(self.imgsz, self.shape)
        return MODEL_COST.get(_short(self.model), 1.0) * h * w / 1e6


@dataclass
class SwipeParams:
    frames: int = 150                 # frames a swipe runs on: the newest with a dancer group, ``min_gap_s`` apart
                                      # (one inference each per candidate; the live setting's come free).  With 60
                                      # or 90 a good live setting was answered by a worse one on 08/10 mur2 8; with
                                      # 120 a 15-point gain was missed on 12 % of mur2 8 swipes
    min_gap_s: float = 1.0            # between two kept frames (neighbours say the same thing)
    group_min_area: float = 300.0     # px^2: a foreground group this large is a dancer to cover
    box_margin: float = 0.10          # a box covers a group whose centre lies within it, grown by this share ...
    cover_share: float = 0.25         # ... or that holds this share of the group's foreground (mask + group box)
    conf: float = 0.15                # boxes under this confidence do not count: give the project's confidence
                                      # (the live boxes are cut there, and a winner would run there)
    min_groups: int = 20              # fewer dancer groups than this in the kept frames: no verdict
    min_gain: float = 0.03            # the winner must cover this much more than the live setting to be named
    loose_floor: float = 0.05         # boxes on nobody per frame every setting is allowed ...
    loose_ratio: float = 1.5          # ... or this many times the live setting's or the settings' median
    max_age_s: float = 300.0          # a kept frame older than this (against the newest offered) is dropped
    covered_frames: int = 0           # unused since 26/11 (frames are no longer chosen by kind); kept for callers
    loose_min_area: float = 64.0      # px^2 of foreground under a box below which it is a box on nobody
    loose_weight: float = 1.0         # a box on nobody costs this many dancers left bare
    noise_z: float = 0.0              # a floor under the z the test asks (``alpha``, the number of alternatives)
    alpha: float = 0.05               # one-sided risk, shared over the alternatives, of naming a setting on noise
    noise_block: int = 2              # frames per batch for the standard error (frames 1 s apart correlate a little:
                                      # lag-1 autocorrelation 0.0-0.2 on 08/10)
    cost_gain: float = 0.05           # extra gain asked per doubling of the cost per frame over the live one's
    tie: float = 0.01                 # two settings closer than this share of the dancers are as good
    tie_z: float = 0.0                # ... or closer than this many standard errors (0: the margin alone)
    tie_across_inputs: bool = False   # True: a smaller input can tie with the best too.  Off: the referee is blind
                                      # alike to every setting at one size (dancers too small to make a group),
                                      # so across sizes the better net wins -- l @ 1280 is 2.3-4.5 points of truth
                                      # under l @ 1920 x 1056 on 08/10 yet ties with it on a window
    one_box_one_dancer: bool = True   # a box covers at most one dancer group (as the truth counts: one box, one
                                      # dancer) -- else one box over two dancers in contact counts for both
    referee_max_loose: float = 0.10   # the CLEANEST setting's boxes on nobody per frame above which the empty-wall
                                      # picture is not trusted: no verdict (08/10's real plate: 0.004-0.011 for
                                      # the cleanest; the corpus's median plates: 0.38 and more)


@dataclass
class Observation:
    """What the referee knows of one frame, whatever the model: its foreground."""
    groups: List[Group]                       # every foreground group, largest first
    mask: Optional[np.ndarray] = None         # uint8 foreground mask (ROI, downscaled ``ds`` times)
    x0: float = 0.0                           # original px of the mask's top-left corner
    y0: float = 0.0
    ds: int = 4
    group_boxes: Optional[List[Tuple[float, float, float, float]]] = None   # each group's box (original px),
                                              # in the order of ``groups``; None when unknown

    @classmethod
    def from_fg(cls, fg) -> Optional["Observation"]:
        """From a ``core.foreground.FgFrame``; None when the foreground is off or invalid for this frame
        (no plate, another exposure or crop, lights changed): such a frame cannot referee anything."""
        if fg is None or not getattr(fg, "valid", False) or getattr(fg, "mask", None) is None:
            return None
        blobs = sorted(fg.blobs, key=lambda b: -float(b.area))
        groups = [(float(b.x), float(b.y), float(b.area)) for b in blobs]
        boxes = [tuple(float(v) for v in b.bbox) for b in blobs] if all(getattr(b, "bbox", None) for b in blobs) \
            else None
        return cls(groups=groups, mask=fg.mask, x0=float(fg.x0), y0=float(fg.y0), ds=int(fg.ds), group_boxes=boxes)

    def dancer_groups(self, params: SwipeParams) -> List[Group]:
        return [g for g in self.groups if g[2] >= params.group_min_area]

    def _dancers(self, params: SwipeParams) -> List[Tuple[Group, Optional[Tuple[float, float, float, float]]]]:
        boxes = self.group_boxes if self.group_boxes is not None and len(self.group_boxes) == len(self.groups) \
            else [None] * len(self.groups)
        return [(g, bb) for g, bb in zip(self.groups, boxes) if g[2] >= params.group_min_area]

    def fg_in(self, a: Sequence[float], b: Sequence[float]) -> Optional[float]:
        """Foreground area (original px^2) inside the intersection of two boxes; None without a mask."""
        x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
        if x1 <= x0 or y1 <= y0:
            return 0.0 if self.mask is not None else None
        return self.fg_area((x0, y0, x1, y1))

    def fg_area(self, box: Sequence[float]) -> Optional[float]:
        """Foreground area (original px^2) under a box (original px), as ``FgFrame.area_in_box`` counts it;
        None without a mask.  A box outside the analysed area has 0."""
        if self.mask is None:
            return None
        m, ds = self.mask, int(self.ds)
        hm, wm = m.shape[:2]
        a0, a1 = max(0, int((box[0] - self.x0) // ds)), min(wm, int((box[2] - self.x0) // ds) + 1)
        b0, b1 = max(0, int((box[1] - self.y0) // ds)), min(hm, int((box[3] - self.y0) // ds) + 1)
        if a1 <= a0 or b1 <= b0:
            return 0.0
        return float(np.count_nonzero(m[b0:b1, a0:a1])) * ds * ds


def box_holds(box: Sequence[float], x: float, y: float, margin: float = 0.0) -> bool:
    x0, y0, x1, y1 = box[:4]
    w, h = x1 - x0, y1 - y0
    return x0 - margin * w <= x <= x1 + margin * w and y0 - margin * h <= y <= y1 + margin * h


def on_nobody(obs: Observation, box: Sequence[float], params: SwipeParams) -> bool:
    """A box with no dancer under it: less than ``loose_min_area`` px^2 of foreground (the mask), or, without a
    mask, no foreground group centre inside it."""
    area = obs.fg_area(box)
    if area is not None:
        return area < params.loose_min_area
    return not any(box_holds(box, g[0], g[1]) for g in obs.groups)


@dataclass
class FrameScore:
    groups: int = 0       # dancer groups in the frame
    covered: int = 0      # ... with a box on them
    boxes: int = 0        # boxes at or over the confidence
    loose: int = 0        # ... with nobody under them (``on_nobody``)
    hits: Tuple[bool, ...] = ()   # per dancer group (the observation's order): a box on it


def covers(obs: Observation, box: Sequence[float], group: Group, group_box, params: SwipeParams) -> bool:
    """A box is on a dancer group when the group's centre lies in it (grown by ``box_margin``), or -- with the mask
    and the group's box -- when it holds ``cover_share`` of the group's foreground.  The second test is for a group
    of two dancers in contact or a dancer and her shadow, whose centre lies between them: a box on either dancer
    covers it, whatever setting drew it (on 08/10 mur2 8 the centre test alone rewarded the setting that draws an
    extra, looser box across such a group)."""
    if box_holds(box, group[0], group[1], params.box_margin):
        return True
    if group_box is None or params.cover_share <= 0:
        return False
    inside = obs.fg_in(box, group_box)
    return inside is not None and inside >= params.cover_share * group[2]


def bare_groups(obs: Observation, boxes: Sequence[Box], params: SwipeParams) -> List[Group]:
    """The dancer groups no box covers: what makes a frame a « lost dancer » frame."""
    kept = [b for b in boxes if b[4] >= params.conf]
    return [g for g, gb in obs._dancers(params) if not any(covers(obs, b, g, gb, params) for b in kept)]


def _matched(holds: List[List[int]], n_boxes: int) -> List[bool]:
    """Per group: does it get a box of its own (a maximum one-to-one matching of groups to the boxes that hold
    them; Kuhn's augmenting paths -- a frame has a handful of each)?"""
    owner = [-1] * n_boxes

    def take(i: int, seen: List[bool]) -> bool:
        for j in holds[i]:
            if not seen[j]:
                seen[j] = True
                if owner[j] < 0 or take(owner[j], seen):
                    owner[j] = i
                    return True
        return False
    for i in range(len(holds)):
        take(i, [False] * n_boxes)
    got = [False] * len(holds)
    for i in owner:
        if i >= 0:
            got[i] = True
    return got


def score_frame(obs: Observation, boxes: Sequence[Box], params: SwipeParams) -> FrameScore:
    kept = [b for b in boxes if b[4] >= params.conf]
    dancers = obs._dancers(params)
    holds = [[j for j, b in enumerate(kept) if covers(obs, b, g, gb, params)] for g, gb in dancers]
    if params.one_box_one_dancer:
        hits = tuple(_matched(holds, len(kept)))
    else:
        hits = tuple(bool(h) for h in holds)
    loose = sum(on_nobody(obs, b, params) for b in kept)
    return FrameScore(groups=len(dancers), covered=sum(hits), boxes=len(kept), loose=loose, hits=hits)


@dataclass
class Tally:
    """One candidate's totals over the swipe's frames, and the frames one by one (for the paired noise)."""
    frames: int = 0
    groups: int = 0
    covered: int = 0
    boxes: int = 0
    loose: int = 0
    ms: List[float] = field(default_factory=list)     # inference time per frame
    rows: List[Tuple[int, int, int, Optional[bool]]] = field(default_factory=list)   # groups, covered, loose, lost
    hits: List[Tuple[bool, ...]] = field(default_factory=list)     # per frame: ``FrameScore.hits``

    def add(self, score: FrameScore, ms: Optional[float] = None, lost: Optional[bool] = None) -> None:
        """One frame's score, frames added in time order (the noise is measured on runs of neighbours); ``lost``
        = ``SwipeFrame.lost``, kept for the record (since 26/11 nothing is weighed by it)."""
        self.frames += 1
        self.groups += score.groups
        self.covered += score.covered
        self.boxes += score.boxes
        self.loose += score.loose
        self.rows.append((int(score.groups), int(score.covered), int(score.loose), lost))
        self.hits.append(tuple(score.hits))
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
    coverage: float           # share of the dancers it covers
    loose_per_frame: float    # boxes on nobody per frame
    ms_p50: Optional[float]
    ok: bool = True           # False: set aside (too many boxes on nobody); ``note`` says why
    note: str = ""
    net: float = 0.0          # coverage - loose_weight x boxes on nobody per dancer
    gain: float = 0.0         # net over the live setting's
    noise: float = 0.0        # one standard error of ``gain`` (paired, frame by frame, batch means)
    cost: float = 0.0         # ``Candidate.cost``
    bar: float = 0.0          # the gain it had to clear (``min_gain`` + its cost over the live setting's)


@dataclass
class SwipeResult:
    current: Candidate
    winner: Optional[Candidate]       # the best setting, None when there is no verdict
    switch: bool                      # True: the winner is another setting, worth applying
    rows: List[SwipeRow]              # every candidate, best first
    reason: str                       # one plain sentence for the operator
    frames: int = 0
    groups: int = 0                   # dancer groups seen in the kept frames (with per-group hits: those some
                                      # setting covers)
    lost_share: Optional[float] = None    # how often the live setting left a dancer bare (information only)
    recommended_conf: Optional[float] = None  # the confidence the winner should run at; None = no opinion, keep
                                      # the project's.  Always None for now: on 08/10 yolo11l and yolo11x give the
                                      # same false boxes at the same confidence (swipe-core report, section 3), so
                                      # the swipe names no confidence of its own (``SwipeParams.conf`` is only the
                                      # referee's cut, never one to run the show at)

    def headline(self) -> str:
        return self.reason


def _z_one_sided(p: float) -> float:
    """z with P(Z > z) = p for a standard normal (bisection on erfc; numpy and math only)."""
    p = min(max(float(p), 1e-12), 0.5)
    lo, hi = 0.0, 10.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if 0.5 * math.erfc(mid / math.sqrt(2.0)) > p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _net_frames(t: Tally, w: float) -> np.ndarray:
    return np.array([c - w * lo for _, c, lo, _ in t.rows], float)


def _batch_se(d: np.ndarray, block: int) -> float:
    """Standard error of the mean of ``d`` (frames in time order) by batch means: neighbouring frames 1 s apart
    see the same dancers in the same places, so an i.i.d. error would be too small."""
    n = len(d)
    if n < 2:
        return 0.0
    b = max(1, int(block))
    k = n // b
    if k < 2:
        return float(d.std(ddof=1)) / math.sqrt(n)
    means = np.array([d[i * b:(i + 1) * b].mean() if i < k - 1 else d[i * b:].mean() for i in range(k)])
    return float(means.std(ddof=1)) / math.sqrt(k)


def decide(tallies: Dict[Candidate, Tally], current: Candidate,
           params: Optional[SwipeParams] = None, lost_share: Optional[float] = None) -> SwipeResult:
    """The verdict.  ``tallies``: every candidate's scores over the same frames, in the same (time) order, the live
    setting's among them.  ``lost_share`` is accepted for callers and only reported: the frames are an unbiased
    sample, nothing is re-weighted.

    No verdict when the plate cannot be trusted or too few dancers were seen; a candidate with too many boxes on
    nobody is set aside; a setting is named only when its net beats the live setting's by its bar (``min_gain``
    plus ``cost_gain`` per doubling of its cost) and by z standard errors, z from ``alpha`` over the number of
    alternatives; among those, the cheapest within ``tie`` of the best at the best's input."""
    p = params or SwipeParams()
    cur = tallies.get(current)
    frames = max((t.frames for t in tallies.values()), default=0)
    groups = max((t.groups for t in tallies.values()), default=0)
    ref = cur if cur is not None else next(iter(tallies.values()), Tally())
    paired = bool(ref.rows) and all(len(t.rows) == len(ref.rows) and [r[0] for r in t.rows] == [r[0] for r in ref.rows]
                                    for t in tallies.values())
    # the dancers: the groups some candidate puts a box on (a group nobody covers -- a rope, a patch of light
    # the plate does not know -- counts for none of them and says nothing about which is better)
    union = None
    if paired and all(len(t.hits) == len(t.rows) and all(len(h) == r[0] for h, r in zip(t.hits, t.rows))
                      for t in tallies.values()):
        union = np.array([sum(any(h) for h in zip(*(t.hits[i] for t in tallies.values())))
                          for i in range(len(ref.rows))], float)
        groups = int(union.sum())
    if union is not None:
        gpf = float(union.mean()) if len(union) else 0.0
    else:
        gpf = ref.groups / ref.frames if ref.frames else 0.0
    w = float(p.loose_weight)

    def est(t: Tally):
        if not paired or not t.rows or gpf <= 0:
            return t.coverage, t.loose_per_frame
        return float(np.mean([r[1] for r in t.rows])) / gpf, float(np.mean([r[2] for r in t.rows]))

    def diff(a: Tally, b: Tally) -> Tuple[float, float]:
        """(net of a - net of b, its standard error), shares of the dancers."""
        if gpf <= 0:
            return 0.0, 0.0
        if paired:
            d = _net_frames(a, w) - _net_frames(b, w)
            return float(d.mean()) / gpf, _batch_se(d, p.noise_block) / gpf
        # not the same frames (or totals only): the pooled totals, and an unpaired error when frames are known
        def net(t: Tally) -> float:
            return (t.covered - w * t.loose) / t.groups if t.groups else 0.0
        var = 0.0
        for t in (a, b):
            v = _net_frames(t, w)
            if len(v) >= 2 and t.groups:
                var += float(v.var(ddof=1)) * len(v) / float(t.groups) ** 2
        return net(a) - net(b), math.sqrt(var)

    def rel_cost(c: Candidate) -> float:
        """Cost per frame over the live setting's: measured ms when both tallies carry them, else the model."""
        ta, tb = tallies.get(c), cur
        if ta is not None and tb is not None and ta.ms_p50 and tb.ms_p50:
            return ta.ms_p50 / tb.ms_p50
        return c.cost / current.cost if current.cost > 0 else 1.0

    cur_cov, cur_loose = est(cur) if cur is not None else (0.0, 0.0)
    ests = {cand: est(t) for cand, t in tallies.items()}
    # set aside: more boxes on nobody than the live setting AND than the settings' middle -- when every setting
    # puts boxes where the referee sees nobody (a dancer faded into the plate), it is the referee, not them
    middle = float(np.median([e[1] for e in ests.values()])) if ests else 0.0
    loose_cap = max(p.loose_floor, p.loose_ratio * max(cur_loose, middle))
    rows: List[SwipeRow] = []
    for cand, t in tallies.items():
        cov, loose = ests[cand]
        ok = cand == current or loose <= loose_cap
        gain, noise = diff(t, cur) if cur is not None and cand != current else (0.0, 0.0)
        net = cov - w * loose / gpf if gpf > 0 else 0.0
        bar = p.min_gain + p.cost_gain * max(0.0, math.log2(max(rel_cost(cand), 1e-9))) if cand != current else 0.0
        rows.append(SwipeRow(cand, cov, loose, t.ms_p50, ok, "" if ok else f"{loose:.2f} boxes a frame on nobody",
                             net=net, gain=gain, noise=noise, cost=cand.cost, bar=bar))
    rows.sort(key=lambda r: (not r.ok, -r.net))
    res = dict(frames=frames, groups=groups, lost_share=lost_share)
    if frames == 0:
        return SwipeResult(current, None, False, rows,
                           "no verdict: no frame with dancers on a valid empty-wall picture", **res)
    if groups < p.min_groups:
        return SwipeResult(current, None, False, rows,
                           f"no verdict: {groups} dancers seen in the kept frames, {p.min_groups} needed", **res)
    if cur is None:
        return SwipeResult(current, None, False, rows, f"no verdict: {current.label} was not measured", **res)
    seeing = [e for e in ests.values() if e[0] >= 0.5 * max(x[0] for x in ests.values())]
    cleanest = min(e[1] for e in seeing) if seeing else cur_loose
    if cleanest > p.referee_max_loose:
        return SwipeResult(current, None, False, rows,
                           f"no verdict: every setting puts boxes where the empty-wall picture shows nobody (at "
                           f"best {cleanest:.2f} a frame) - the picture may hold people or another light; take a "
                           f"new one when the wall is empty", **res)
    alternatives = [r for r in rows if r.ok and r.candidate != current]
    z = max(p.noise_z, _z_one_sided(p.alpha / max(1, len(alternatives))))
    proven = [r for r in alternatives if r.gain >= r.bar and r.gain >= z * r.noise]
    # once one setting is proven better than the live one, the best by net among those clearing their bar is
    # named: a setting at least as good as a proven one is better too.  (The test alone would favour the setting
    # whose errors resemble the live one's -- the same model at another size -- over a better, different one.)
    better = [r for r in alternatives if r.gain >= r.bar and r.net >= min(x.net for x in proven)] if proven else []
    if not better:
        others = [r for r in rows if r.candidate != current]
        why = "no other setting does clearly better"
        if others and all(not r.ok for r in others):
            why = "the other settings put boxes on nobody"
        return SwipeResult(current, current, False, rows,
                           f"keep {current.label}: it covers {100 * cur_cov:.0f} % of the dancers, {why}", **res)
    best = max(better, key=lambda r: r.net)
    ties = []
    for r in better:
        if r is best:
            ties.append(r)
            continue
        if not p.tie_across_inputs and target_hw(r.candidate.imgsz, r.candidate.shape) != \
                target_hw(best.candidate.imgsz, best.candidate.shape):
            continue
        d, se = diff(tallies[best.candidate], tallies[r.candidate])
        if d < max(p.tie, p.tie_z * se):
            ties.append(r)
    pick = min(ties, key=lambda r: (r.cost, -r.net))
    for r in ties:
        if r is not pick and not r.note:
            r.note = f"as good as {pick.candidate.label}, which is lighter"
    tail = f", with {pick.loose_per_frame:.2f} boxes a frame on nobody" if pick.loose_per_frame >= 0.005 else ""
    return SwipeResult(current, pick.candidate, True, rows,
                       f"{pick.candidate.label} covers {100 * pick.coverage:.0f} % of the dancers, "
                       f"{current.label} {100 * cur_cov:.0f} %{tail}", **res)


@dataclass
class SwipeFrame:
    t: float                  # seconds (the loop's clock)
    image: np.ndarray         # the raw camera frame, as the pipeline received it
    obs: Observation
    lost: bool                # the live setting left a dancer group bare on it (a flag: it chooses nothing)
    boxes: Tuple[Box, ...] = ()   # the live setting's boxes on it (its row in the swipe needs no new inference)


class FrameCollector:
    """Keeps the frames a swipe will run on: the newest ``params.frames`` frames with a dancer group on the wall,
    at least ``min_gap_s`` apart, whatever the live setting did on them -- an unbiased sample of the dancing.
    Each kept frame carries ``lost`` (the live setting left a dancer group bare on it) for the operator's progress
    and the background swipe; ``lost_share`` counts every frame with dancers offered over the last ``max_age_s``.
    A kept frame older than ``max_age_s`` is dropped when a newer frame is offered, or on ``prune(t_now)`` /
    ``frames_at(t_now)``."""

    def __init__(self, params: Optional[SwipeParams] = None):
        self.params = params or SwipeParams()
        self._pool: List[SwipeFrame] = []
        self._last_t: Optional[float] = None        # the last kept frame
        self._hist: Deque[Tuple[float, bool]] = deque()
        self._n_lost = 0

    def reset(self) -> None:
        self._pool.clear()
        self._last_t = None
        self._hist.clear()
        self._n_lost = 0

    def prune(self, t_now: float) -> None:
        """Drop the kept frames (and the counts behind ``lost_share``) older than ``max_age_s`` at ``t_now``:
        call it before reading ``frames`` / ``ready`` / ``progress`` when no frame was offered lately."""
        self._expire(float(t_now))

    def frames_at(self, t_now: float) -> List[SwipeFrame]:
        """``frames``, after pruning at ``t_now``."""
        self.prune(t_now)
        return self.frames

    def _expire(self, t: float) -> None:
        age = self.params.max_age_s
        if age is None or age <= 0:
            return
        lim = float(t) - float(age)
        if any(f.t < lim for f in self._pool):
            self._pool = [f for f in self._pool if f.t >= lim]
        while self._hist and self._hist[0][0] < lim:
            _, lost = self._hist.popleft()
            self._n_lost -= lost

    def offer(self, t: float, image, obs: Optional[Observation], boxes: Sequence[Box]) -> bool:
        """One live frame with its foreground and the live setting's boxes; True when it was kept.
        ``image``: the frame, or a callable returning it, called only when the frame is kept (so the caller copies
        a reused buffer only then).  An array is stored as given: pass a copy if the caller reuses its buffer."""
        p = self.params
        self._expire(t)
        if obs is None or not obs.dancer_groups(p):
            return False
        lost = bool(bare_groups(obs, boxes, p))
        self._hist.append((float(t), lost))
        self._n_lost += lost
        if self._last_t is not None and 0 <= t - self._last_t < p.min_gap_s:
            return False
        if p.frames <= 0:
            return False
        if len(self._pool) >= p.frames:
            self._pool.pop(0)          # keep the newest (a warmup must not run on an old rehearsal)
        img = image() if callable(image) else image
        self._pool.append(SwipeFrame(float(t), img, obs, lost, tuple(tuple(b) for b in boxes)))
        self._last_t = float(t)
        return True

    @property
    def lost_frames(self) -> int:
        """Kept frames where the live setting left a dancer bare (information: the frames are not chosen by it)."""
        return sum(1 for f in self._pool if f.lost)

    @property
    def frames(self) -> List[SwipeFrame]:
        """The swipe's frames, oldest first.  Score every candidate on all of them, in this order, with
        ``Tally.add(score, ms, lost=frame.lost)``; ``decide(..., lost_share=)`` only reports the share."""
        return list(self._pool)

    @property
    def lost_share(self) -> Optional[float]:
        """Share of the frames with dancers (last ``max_age_s``) on which the live setting left one bare."""
        return self._n_lost / len(self._hist) if self._hist else None

    @property
    def progress(self) -> float:
        return min(1.0, len(self._pool) / float(self.params.frames)) if self.params.frames else 1.0

    @property
    def ready(self) -> bool:
        return len(self._pool) >= self.params.frames
