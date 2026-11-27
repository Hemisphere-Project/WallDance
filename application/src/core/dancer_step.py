"""X4 (hub walldance#t-055): the dancer step -- ONE dancer stands on the wall for 10 s and the app sets what
depends on her.  And the band (#t-050 ②): her height at the network, live, on the Live panel.

The third setup step, after the frame and the empty wall (roadmap 2026-11-25, §3 « A dancer »).  Pure logic: the
caller (runtime/calibration_flows.py) forces detection on for the step's frames, sets the enhancement the step
asks for before each frame (``DancerStep.current``) and feeds each processed frame (``observe``).  Three things:

1. **Her size**, from the detection boxes' heights AND the height of her foreground group (the frame against
   the empty-wall plate, core/foreground.py), with NO keypoint gate: release 1's automatic height
   (core/auto_height.py) wants a head and an ankle keypoint, and a dancer too small or too dark to give them
   is exactly the one who needs measuring.  A box that has no foreground under it (on a valid plate) is a box
   on nobody and does not count; a foreground group no box covers counts too, in box units (``box_per_group``):
   a silhouette's group runs smaller than the detector's box around the same body (08/10 mur2 3: 88 against
   101 px), and the thresholds that use her height (P4's 100 px, the band's 110 px) were set on boxes, so a group
   alone is scaled by the median box/group ratio of the frames where she has both (1.0 when she never has a box:
   a dancer no network sees then reads small, the safe side).  Her height at the network = her height x the
   letterbox scale (P4: core/input_shape.py).
2. **The enhancement, chosen on her**: among the settings up to the clean ceiling X3's empty-wall check
   recorded (``calibration_state['enhancement_ceiling']``) -- no enhancement, the ceiling, and the rungs
   between them (``candidates``) -- the step cycles the candidates in blocks of a few frames, interleaved over
   the 10 s so that each sees her in the same poses, and keeps the one with the best detection confidence on
   her.  Never one worse than none: « none » is a candidate and the default, and another wins only with a
   paired gain in confidence beyond its noise and no lower detection rate (``choose``).
3. **Belt glare**: the IR belt comes back at white; when it blooms over her body the detector loses her.  Per
   frame, the share of her body box at white that stands out of its surroundings (a grey opening the size of
   half her height: the clipped wall behind her is not glare) -- and the step says the belt blinds the
   detector when that share is large in many frames AND the detector sees her clearly less in those frames.

Nobody in view: it says so and sets nothing.  Two people (or more): it says so and sets nothing -- the step is for
ONE dancer alone (her size and the enhancement chosen on her would stand on both).
The step changes nothing by itself: the caller applies ``DancerResult`` and keeps the snapshot to put back.

**The band** (``DancerBand``): the rolling median of the dancers' height at the network over the last 10 s,
measured on the boxes and on the foreground groups alike (a dancer too small to be detected still counts).
Orange under ``BAND_WARN_PX`` (« too small for this input: use the larger one »; from under 100 px, back at 110:
``BAND_HYST_PX``), a plain note over
``BAND_NOTE_PX`` (a smaller input would do).  08/10: ~75 px in the 1280 square (orange), ~110 px at 1920 x 1056.
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional, Sequence, Tuple

import numpy as np

# --- the step -----------------------------------------------------------------------------------------------
STEP_FRAMES = 200            # 10 s at 20 fps
STEP_MIN_FRAMES = 100        # a step cut short (playback ended) still decides on this many
STEP_STALL_S = 5.0           # no frame for this long: the step is cut short (the source stopped, or never started)
BLOCK = 4                    # frames per candidate before the next one (the candidates interleave)
SETTLE = 1                   # first frame(s) of a block not counted: the enhancement change reaches YOLO there
MAX_CANDIDATES = 4           # none + up to three settings: each still gets ~37 counted frames in 200
GROUP_MIN_AREA = 300.0       # px^2: a foreground group this large may be a person (the swipe referee's group)
LOOSE_MIN_AREA = 64.0        # px^2 of foreground under a box below which it is a box on nobody (the referee's)
PRESENT_MIN_SHARE = 0.25     # her presence: a person seen in at least this share of the frames, else nobody
TWO_MIN_SHARE = 0.30         # two people: two persons apart in at least this share of the frames
PAIR_MIN = 10                # box/group pairs needed to put a group alone in box units (else taken as it is)
PAIR_RANGE = (0.6, 1.7)      # a box/group ratio outside this is not one body seen twice (a split or a merged group)
BODY_RANGE = (0.4, 2.0)      # a group alone counts as someone when its height (box units) is within this x the
                             # median box height of the window (the detector's own size gate is 0.3-2.5 x): a
                             # broad difference from the plate (a plate of another exposure, light) is no body
GAIN_Z = 1.0                 # a setting beats none by more than this many standard errors of the paired gain
MIN_COUNTED = 12             # counted frames a candidate needs to be judged
# --- the band and the readiness row -----------------------------------------------------------------------------
BAND_WARN_PX = 110.0         # her height at the network under this: orange (Phase 2b's knee 83-110 px) ...
BAND_HYST_PX = 10.0          # ... with this hysteresis: orange from under 100 px (the readiness row's own limit, P4),
                             # back to ok only at 110 -- the 10 s median hovers at ~110 at 1920 x 1056 (08/10)
BAND_NOTE_PX = 200.0         # over this: a plain note, a smaller input would do (the hangar ~200 px)
BAND_WINDOW_S = 10.0
BAND_MIN_SAMPLES = 20
DANCER_STEP_VALID_S = 12 * 3600.0   # the readiness row trusts the step's measure for an evening (same setup)
# --- belt glare ----------------------------------------------------------------------------------------------
GLARE_DN = 250               # at white (core/light_verdict.LIGHT_CLIP_DN)
GLARE_BG_DN = 235            # ... standing out of a surrounding below this (X1's highlight limit)
GLARE_SHARE = 0.04           # glare over this share of her body box is « on her »
GLARE_FRAMES = 0.25          # ... in at least this share of her frames
GLARE_BLIND_DROP = 0.15      # ... and the detector sees her this much less in those frames: the belt blinds it
GLARE_MAX_BODY = 600         # px: a group alone taller than this is not judged (a broad region, not a body)
GLARE_REF_MIN = 10           # frames free of glare needed to compare against; fewer (the bloom on her nearly all the
                             # time: 08/10 mur 3, every frame): she should be seen in every frame -- standing in full
                             # view, as the step asks (08/10: 99-100 % in the glare-free frames of every window)

Box = Tuple[float, float, float, float, float]     # x0, y0, x1, y1, conf (original px)
Rect = Tuple[float, float, float, float]          # x0, y0, x1, y1


@dataclass(frozen=True)
class Enh:
    """One enhancement setting: off, or on with (gamma, CLAHE clip)."""
    enabled: bool
    gamma: float = 1.0
    clahe: float = 1.0

    @property
    def label(self) -> str:
        return f"gamma {self.gamma:.2f} / CLAHE {self.clahe:.1f}" if self.enabled else "none"

    def key(self) -> str:
        return f"{self.gamma:.2f}/{self.clahe:.1f}" if self.enabled else "none"


NONE = Enh(False)


def box_per_group(pairs: Sequence[float], min_pairs: int = PAIR_MIN) -> float:
    """The median box height / group height of the persons seen with both (1.0 with fewer than ``min_pairs``)."""
    return float(statistics.median(pairs)) if len(pairs) >= min_pairs else 1.0


def body_ref(box_heights: Sequence[float], min_n: int = PAIR_MIN) -> Optional[float]:
    """The median box height of the window, the yardstick of a group alone; None with fewer than ``min_n``."""
    return float(statistics.median(box_heights)) if len(box_heights) >= min_n else None


def body_sized(g_box_units: float, ref: Optional[float]) -> bool:
    """Is a group alone (its height in box units) a body, against the window's box height?  Without a yardstick
    (no box seen yet): yes."""
    return ref is None or BODY_RANGE[0] * ref <= g_box_units <= BODY_RANGE[1] * ref


def candidates(ceiling: Optional[Tuple[float, float]], current: Optional[Enh] = None) -> List[Enh]:
    """The settings tried on her: none first, then the ceiling and the rungs between it and neutral -- half way
    in gamma, and the ceiling's CLAHE at gamma 1.0 -- never anything stronger than the ceiling, at most
    ``MAX_CANDIDATES``.  No ceiling (no clean empty-wall check on record): nothing stronger is known to be clean,
    so only the setting running now (``current``, when on) against none -- it can be kept or switched off, never
    raised; [] when it is off already (the step leaves the enhancement as it is)."""
    if ceiling is None:
        return [NONE, current] if current is not None and current.enabled else []
    g, c = float(ceiling[0]), float(ceiling[1])
    out = [NONE, Enh(True, g, c)]
    if g > 1.0:
        mid = 1.0 + (g - 1.0) / 2.0
        if g - mid >= 0.05:
            out.append(Enh(True, round(mid, 4), c))
        if c > 1.0 + 1e-6 and g - 1.0 >= 0.05:
            out.append(Enh(True, 1.0, c))
    return out[:MAX_CANDIDATES]


# --- one frame ----------------------------------------------------------------------------------------------
@dataclass
class Person:
    """Someone on the wall this frame: the best box on her and/or her foreground group (original px)."""
    box: Optional[Box] = None
    group: Optional[Rect] = None
    glare: Optional[float] = None        # share of her body box that is belt glare; None: not measured

    @property
    def body(self) -> Rect:
        return tuple(self.box[:4]) if self.box is not None else tuple(self.group)

    @property
    def height(self) -> float:
        """The box's height when a box covers her, else her group's."""
        b = self.body
        return float(b[3] - b[1])

    @property
    def conf(self) -> float:
        return float(self.box[4]) if self.box is not None else 0.0

    @property
    def group_h(self) -> Optional[float]:
        return float(self.group[3] - self.group[1]) if self.group is not None else None

    @property
    def pair(self) -> Optional[float]:
        """Box height / group height when she has both (and the group is a body, not a speck), else None."""
        g = self.group_h
        if self.box is None or not g:
            return None
        r = self.height / g
        return r if PAIR_RANGE[0] <= r <= PAIR_RANGE[1] else None


@dataclass
class DancerObs:
    persons: List[Person]
    fg_valid: bool = False
    lb_scale: float = 0.0
    loose: int = 0                       # boxes on nobody (a valid foreground under none of them)

    def apart(self) -> int:
        """Persons whose bodies do not overlap: two dancers in contact count once (a contact is not two)."""
        kept: List[Rect] = []
        for p in sorted(self.persons, key=lambda p: -p.height):
            b = p.body
            if not any(_overlap(b, k) > 0.3 for k in kept):
                kept.append(b)
        return len(kept)


def _overlap(a: Rect, b: Rect) -> float:
    """Intersection over the smaller of two rectangles."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    sa = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    sb = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / min(sa, sb)


def _holds(box: Sequence[float], x: float, y: float, margin: float = 0.10) -> bool:
    w, h = box[2] - box[0], box[3] - box[1]
    return (box[0] - margin * w <= x <= box[2] + margin * w) and (box[1] - margin * h <= y <= box[3] + margin * h)


def _fg_area(fg, box: Sequence[float]) -> Optional[float]:
    """Foreground area (original px^2) under a box, as the swipe referee counts it; None without a mask."""
    m = getattr(fg, "mask", None)
    if m is None:
        return None
    ds = int(getattr(fg, "ds", 4) or 4)
    hm, wm = m.shape[:2]
    a0, a1 = max(0, int((box[0] - fg.x0) // ds)), min(wm, int((box[2] - fg.x0) // ds) + 1)
    b0, b1 = max(0, int((box[1] - fg.y0) // ds)), min(hm, int((box[3] - fg.y0) // ds) + 1)
    if a1 <= a0 or b1 <= b0:
        return 0.0
    return float(np.count_nonzero(m[b0:b1, a0:a1])) * ds * ds


def _merge_groups(rects: List[Rect]) -> List[Rect]:
    """Foreground groups of one person split in pieces (an arm, the legs apart) joined: two rectangles whose
    boxes, grown by a quarter of the taller one's height, overlap."""
    rects = [tuple(r) for r in rects]
    changed = True
    while changed and len(rects) > 1:
        changed = False
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                a, b = rects[i], rects[j]
                g = 0.25 * max(a[3] - a[1], b[3] - b[1])
                if (a[0] - g <= b[2] and b[0] - g <= a[2] and a[1] - g <= b[3] and b[1] - g <= a[3]):
                    rects[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    del rects[j]
                    changed = True
                    break
            if changed:
                break
    return rects


def glare_share(gray: Optional[np.ndarray], body: Rect) -> Optional[float]:
    """The share of a body box at white (>= GLARE_DN) that stands out of its surroundings: the pixel is gone
    under a grey opening the size of half the body's height, and that opening is under GLARE_BG_DN there.  The
    clipped wall behind her (a broad region at white) survives the opening and is not counted; the belt's bloom
    over her (narrower than half her height) is.  None without a picture or a body inside it."""
    if gray is None or getattr(gray, "ndim", 0) < 2:
        return None
    if gray.ndim == 3:
        gray = gray[..., 0]
    H, W = gray.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in body)
    h = max(1, y1 - y0)
    k = max(5, int(h // 2) | 1)
    pad = k
    X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
    bx0, by0, bx1, by1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if bx1 <= bx0 or by1 <= by0:
        return None
    crop = gray[Y0:Y1, X0:X1]
    if crop.dtype != np.uint8:
        crop = np.clip(crop, 0, 255).astype(np.uint8)
    sub = crop[by0 - Y0:by1 - Y0, bx0 - X0:bx1 - X0]
    bright = sub >= GLARE_DN
    if not bright.any():
        return 0.0
    import cv2
    opened = cv2.morphologyEx(crop, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    bg = opened[by0 - Y0:by1 - Y0, bx0 - X0:bx1 - X0]
    glare = bright & (bg < GLARE_BG_DN)
    return float(np.count_nonzero(glare)) / float(max(1, sub.size))


def observe(boxes: Sequence[Sequence[float]], fg=None, *, conf: float, lb_scale: float = 0.0,
            gray: Optional[np.ndarray] = None, zone_ok=None) -> DancerObs:
    """One processed frame as the step and the band see it.  ``boxes``: (x0, y0, x1, y1, conf) in original px
    (``FrameProcessor.last_raw_boxes``: every YOLO person box before the size gate); ``fg``: its ``FgFrame``
    (``last_fg``) or None; ``conf``: the live confidence (boxes under it do not count); ``gray``: the raw
    frame, for the belt glare (None: not measured); ``zone_ok(x, y)``: False where the operator excluded the
    cells (the pipeline drops every detection there)."""
    kept = sorted((tuple(float(v) for v in b[:5]) for b in boxes if float(b[4]) >= conf), key=lambda b: -b[4])
    if zone_ok is not None:
        kept = [b for b in kept if zone_ok(0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3]))]
    valid = bool(fg is not None and getattr(fg, "valid", False) and getattr(fg, "mask", None) is not None)
    blobs = []
    if valid:
        for bl in getattr(fg, "blobs", None) or ():
            if float(bl.area) >= GROUP_MIN_AREA and (zone_ok is None or zone_ok(float(bl.x), float(bl.y))):
                blobs.append(bl)
    persons: List[Person] = []
    loose = 0
    used = set()
    for b in kept:                                   # strongest first: a weaker box over the same body is dropped
        if any(_overlap(b[:4], p.box[:4]) > 0.6 for p in persons if p.box is not None):
            continue
        if valid:
            area = _fg_area(fg, b)
            if area is not None and area < LOOSE_MIN_AREA:
                loose += 1
                continue
        bh = b[3] - b[1]
        mine = [i for i, bl in enumerate(blobs) if i not in used and _holds(b, float(bl.x), float(bl.y))
                and float(bl.bbox[3] - bl.bbox[1]) <= BODY_RANGE[1] * bh]
        used.update(mine)
        grp = _merge_groups([tuple(blobs[i].bbox) for i in mine])
        persons.append(Person(box=b, group=grp[0] if len(grp) == 1 else (
            (min(r[0] for r in grp), min(r[1] for r in grp), max(r[2] for r in grp), max(r[3] for r in grp))
            if grp else None)))
    rest = _merge_groups([tuple(bl.bbox) for i, bl in enumerate(blobs) if i not in used])
    for r in rest:                                   # a group no box covers: someone the detector misses
        if r[3] - r[1] >= 8:
            persons.append(Person(group=r))
    if gray is not None:                             # her body box: the detector's, else her group's (a blinded
        for p in persons:                            # frame has no box); a broad region is no body (not judged)
            if p.box is not None or p.height <= GLARE_MAX_BODY:
                p.glare = glare_share(gray, p.body)
    return DancerObs(persons=persons, fg_valid=valid, lb_scale=float(lb_scale or 0.0), loose=loose)


# --- the step -------------------------------------------------------------------------------------------------
@dataclass
class CandidateStats:
    enh: Enh
    frames: int = 0                     # counted frames with her present
    conf_sum: float = 0.0               # her best box confidence (0 when missed), summed
    seen: int = 0                       # frames with a box on her
    blocks: List[Tuple[int, float, int]] = field(default_factory=list)   # (cycle, mean conf, frames) per block

    @property
    def mean_conf(self) -> float:
        return self.conf_sum / self.frames if self.frames else 0.0

    @property
    def det(self) -> float:
        return self.seen / self.frames if self.frames else 0.0


@dataclass
class Glare:
    frames: int = 0                     # her frames with a body to judge
    median: float = 0.0                 # median share of her body box
    high: int = 0                       # frames over GLARE_SHARE
    det_high: Optional[float] = None    # share of those with a box on her
    det_low: Optional[float] = None     # ... of the others
    blinds: bool = False
    compared: bool = True               # against her glare-free frames (else: against every frame, GLARE_REF_MIN)

    def line(self) -> str:
        if self.frames == 0:
            return "belt glare: not judged"
        share = self.high / self.frames
        if self.blinds:
            vs = f" against {100 * self.det_low:.0f} % otherwise" if self.compared else " only"
            return (f"belt glare BLINDS the detector on her: at white over {100 * GLARE_SHARE:.0f} % of her body in "
                    f"{100 * share:.0f} % of the frames, seen in {100 * self.det_high:.0f} % of those{vs}"
                    " - a smaller or less reflective belt, or the IR flood further from the lens")
        if share >= GLARE_FRAMES:
            return (f"belt glare on her in {100 * share:.0f} % of the frames, the detector still sees her in "
                    f"{100 * self.det_high:.0f} % of those")
        return "belt glare: fine"


@dataclass
class DancerResult:
    frames: int
    present: float                      # share of frames with someone on the wall
    people: int                         # 0 nobody, 1, 2 (two or more)
    height_px: Optional[float] = None   # her height, original px (boxes where a box covers her, else her group)
    height_box: Optional[float] = None
    height_group: Optional[float] = None
    samples: int = 0
    n_box: int = 0
    n_group: int = 0
    lb_scale: float = 0.0
    stats: List[CandidateStats] = field(default_factory=list)
    chosen: Optional[Enh] = None        # None: the enhancement is left as it was (no ceiling, or not judged)
    why: str = ""
    ceiling: Optional[Tuple[float, float]] = None
    kept_current: bool = False
    glare: Glare = field(default_factory=Glare)
    fg_frames: int = 0                  # frames with a valid foreground
    group_scale: float = 1.0            # box units per group unit (``box_per_group``)
    unseen: bool = False                # nobody measured, but foreground groups moved: the detector misses her
    two: float = 0.0                    # share of frames with two people apart

    @property
    def nobody(self) -> bool:
        return self.people == 0

    @property
    def sets_nothing(self) -> bool:
        """Not ONE dancer alone (nobody, or two people): the step says why and sets nothing."""
        return self.people != 1

    @property
    def net_px(self) -> Optional[float]:
        return self.height_px * self.lb_scale if self.height_px and self.lb_scale else None

    def stat(self, enh: Enh) -> Optional[CandidateStats]:
        return next((s for s in self.stats if s.enh == enh), None)


def choose(stats: Sequence[CandidateStats], gain_z: float = GAIN_Z,
           min_counted: int = MIN_COUNTED) -> Tuple[Optional[Enh], str]:
    """The setting kept: the best mean confidence on her among those that beat « none » -- a paired gain (block
    by block of the same cycle, so both saw her in the same poses) over ``gain_z`` standard errors and a
    detection rate no lower than none's.  Else none.  (None, why) when « none » itself was not judged."""
    base = next((s for s in stats if not s.enh.enabled), None)
    if base is None or base.frames < min_counted:
        return None, "too few frames with her to judge the enhancement"
    by_cycle = {c: m for c, m, _n in base.blocks}
    best, best_gain, why = NONE, 0.0, "no setting beats none on her"
    for s in stats:
        if not s.enh.enabled or s.frames < min_counted:
            continue
        d = [m - by_cycle[c] for c, m, _n in s.blocks if c in by_cycle]
        if len(d) < 3:
            continue
        mean = statistics.fmean(d)
        se = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else float("inf")
        if mean > gain_z * se and mean > 0 and s.det >= base.det and s.mean_conf > base.mean_conf:
            if s.mean_conf - base.mean_conf > best_gain:
                best, best_gain = s.enh, s.mean_conf - base.mean_conf
                why = f"+{best_gain:.2f} confidence on her over none ({mean / se if se else 0:.1f} standard errors)"
    return best, why


class DancerStep:
    """The step's frames: ``current()`` is the enhancement to run the next frame with, ``feed(obs)`` takes the
    frame just processed (and returns the enhancement for the next one when it changes), ``done`` once
    ``frames`` frames were fed; then ``result()``.  ``cands`` = ``candidates(ceiling)``; [] tries nothing (the
    enhancement left as it is)."""

    def __init__(self, cands: Sequence[Enh], *, frames: int = STEP_FRAMES, block: int = BLOCK,
                 settle: int = SETTLE, ceiling: Optional[Tuple[float, float]] = None, kept_current: bool = False):
        self.cands = list(cands)
        self.frames = max(1, int(frames))
        self.block = max(1, int(block))
        self.settle = max(0, min(int(settle), self.block - 1))
        self.ceiling = tuple(ceiling) if ceiling is not None else None
        self.kept_current = bool(kept_current)
        self.i = 0
        self._stats = [CandidateStats(e) for e in self.cands]
        self._block_acc: Optional[Tuple[int, int, float, int]] = None   # (stat index, cycle, conf sum, frames)
        self._rows: List[List[Person]] = []                             # the persons of each frame
        self._box_h: List[float] = []
        self._pairs: List[float] = []
        self._fg = 0
        self._lb: List[float] = []

    @property
    def done(self) -> bool:
        return self.i >= self.frames

    def progress(self) -> float:
        return min(1.0, self.i / float(self.frames))

    def _slot(self, i: int) -> Tuple[int, int, int]:
        """(candidate index, cycle, position in the block) of frame ``i``."""
        b = i // self.block
        k = len(self.cands) or 1
        return b % k, b // k, i % self.block

    def current(self) -> Optional[Enh]:
        if not self.cands or self.done:
            return None
        return self.cands[self._slot(self.i)[0]]

    def _close_block(self) -> None:
        acc, self._block_acc = self._block_acc, None
        if acc is not None and acc[3] > 0:
            self._stats[acc[0]].blocks.append((acc[1], acc[2] / acc[3], acc[3]))

    def feed(self, obs: DancerObs) -> Optional[Enh]:
        if self.done:
            return None
        before = self.current()
        persons = obs.persons
        if obs.fg_valid:
            self._fg += 1
        if obs.lb_scale > 0:
            self._lb.append(obs.lb_scale)
        self._rows.append(list(persons))
        for p in persons:
            if p.box is not None:
                self._box_h.append(p.height)
            if p.pair is not None:
                self._pairs.append(p.pair)
        if self.cands:
            k, cycle, pos = self._slot(self.i)
            # her presence: a box, or a body-sized group alone (against the boxes so far); without a plate a frame
            # with no box may still hold her
            ref, gk = body_ref(self._box_h), box_per_group(self._pairs)
            present = (any(p.box is not None or body_sized(p.height * gk, ref) for p in persons)
                       or not obs.fg_valid)
            if pos >= self.settle and present:
                s = self._stats[k]
                c = max((p.conf for p in persons), default=0.0)
                s.frames += 1
                s.conf_sum += c
                s.seen += int(c > 0)
                acc = self._block_acc
                if acc is None or acc[0] != k or acc[1] != cycle:
                    self._close_block()
                    acc = (k, cycle, 0.0, 0)
                self._block_acc = (k, cycle, acc[2] + c, acc[3] + 1)
            if pos == self.block - 1:
                self._close_block()
        self.i += 1
        after = self.current()
        return after if (after is not None and after != before) else None

    def finish(self) -> None:
        """End the step now (a playback that ended): ``result`` decides on the frames fed."""
        self._close_block()
        self.frames = self.i

    def result(self) -> DancerResult:
        self._close_block()
        n = max(1, self.i)
        k, ref = box_per_group(self._pairs), body_ref(self._box_h)
        # a group alone is her only against the boxes' yardstick: with fewer than PAIR_MIN boxes in the 10 s the
        # detector does not see her, and a group alone (a sliver at the frame's edge, a shadow) measures nothing
        # (08/10 mur2 4: she hangs at the top edge, 4 boxes in 200 frames, 30 px groups read as her height)
        rows = [[p for p in row if p.box is not None or (ref is not None and body_sized(p.height * k, ref))]
                for row in self._rows]
        present = sum(1 for row in rows if row) / n
        two = sum(1 for row in rows if DancerObs(row).apart() >= 2) / n
        people = 0 if present < PRESENT_MIN_SHARE else (2 if two >= TWO_MIN_SHARE else 1)
        med = lambda v: float(statistics.median(v)) if v else None
        r = DancerResult(frames=self.i, present=present, people=people, ceiling=self.ceiling,
                         kept_current=self.kept_current, stats=self._stats, fg_frames=self._fg,
                         lb_scale=med(self._lb) or 0.0, two=two)
        if people >= 2:                            # not ONE dancer alone: nothing measured on her
            r.why = f"two people on the wall in {100 * two:.0f} % of {self.i} frames"
            return r
        if people == 0:
            if ref is None and sum(1 for row in self._rows if row) / n >= PRESENT_MIN_SHARE:
                r.unseen, r.n_box = True, len(self._box_h)
                r.why = f"someone on the wall, but the detector saw her in {len(self._box_h)} of {self.i} frames"
            else:
                r.why = "nobody seen on the wall"
            return r
        r.group_scale = k
        kept = [p for row in rows for p in row]
        r.height_px = med([p.height if p.box is not None else p.height * k for p in kept])
        groups = [p.group_h for p in kept if p.group is not None]
        r.height_box, r.height_group = med(self._box_h), med(groups)
        r.samples, r.n_box, r.n_group = len(kept), len(self._box_h), len(groups)
        if self.cands:
            r.chosen, r.why = choose(self._stats)
        else:
            r.why = "no clean empty-wall check on record: the enhancement is left as it is"
        r.glare = _glare([(p.glare, p.box is not None) for p in kept if p.glare is not None])
        return r


def _glare(rows: Sequence[Tuple[float, bool]]) -> Glare:
    g = Glare(frames=len(rows))
    if not rows:
        return g
    g.median = float(statistics.median(s for s, _b in rows))
    hi = [b for s, b in rows if s >= GLARE_SHARE]
    lo = [b for s, b in rows if s < GLARE_SHARE]
    g.high = len(hi)
    g.det_high = sum(hi) / len(hi) if hi else None
    g.det_low = sum(lo) / len(lo) if lo else None
    g.compared = len(lo) >= GLARE_REF_MIN
    ref = g.det_low if g.compared else 1.0          # too few frames free of it: against every frame
    g.blinds = bool(len(hi) >= GLARE_FRAMES * len(rows) and len(hi) >= 10
                    and ref - g.det_high >= GLARE_BLIND_DROP)
    return g


# --- the band (#t-050 ②) ------------------------------------------------------------------------------------
LARGER_SQUARES = (1280, 1536)       # the squares the show machines have engines for (beside RECT_PRESETS)


def larger_input(roi_w: int, roi_h: int, imgsz: int, shape=None, net_px: Optional[float] = None) -> str:
    """The input that shows the dancers largest, when it beats the one running by 5 %: « yolo_shape 1920x1056
    (~112 px) » / « Image Size 1536 (~92 px) »; "" when none does (or the ROI is unknown)."""
    from core.input_shape import RECT_PRESETS, letterbox_scale, shape_str
    if roi_w <= 0 or roi_h <= 0:
        return ""
    cur = letterbox_scale(roi_w, roi_h, imgsz, shape)
    opts = [(letterbox_scale(roi_w, roi_h, 0, r), f"yolo_shape {shape_str(r)}") for r in RECT_PRESETS]
    opts += [(letterbox_scale(roi_w, roi_h, q), f"Image Size {q}") for q in LARGER_SQUARES]
    best = max(opts, key=lambda o: o[0])
    if cur <= 0 or best[0] < 1.05 * cur:
        return ""
    return best[1] + (f" gives ~{net_px * best[0] / cur:.0f} px" if net_px else "")


@dataclass
class BandState:
    state: str            # "ok" | "warn" | "note" | "skip" (not measured)
    net_px: Optional[float]
    samples: int
    text: str


class DancerBand:
    """The dancers' height at the network, live: every person of each processed frame (a box's height, or her
    foreground group's in box units when no box covers her: ``box_per_group``) x the letterbox scale, over the
    last ``window_s``; the state of their median.  ``larger`` names the larger input when one exists (« use the
    larger one »).  ``median("box")`` / ``median("group")``: the boxes alone, the groups alone (as measured)."""

    def __init__(self, window_s: float = BAND_WINDOW_S, min_samples: int = BAND_MIN_SAMPLES,
                 warn_px: float = BAND_WARN_PX, note_px: float = BAND_NOTE_PX, hyst_px: float = BAND_HYST_PX):
        self.window_s, self.min_samples = float(window_s), int(min_samples)
        self.warn_px, self.note_px, self.hyst_px = float(warn_px), float(note_px), float(hyst_px)
        self._s: Deque[Tuple[float, Optional[float], Optional[float]]] = deque()   # (t, box net px, group net px)
        self._warn = False                  # orange now: it stays so until the median is back at ``warn_px``

    def reset(self) -> None:
        self._s.clear()
        self._warn = False

    def feed(self, t: float, obs: DancerObs) -> None:
        if obs.lb_scale > 0:
            for p in obs.persons:
                g = p.group_h
                self._s.append((float(t), p.height * obs.lb_scale if p.box is not None else None,
                                g * obs.lb_scale if g else None))
        while self._s and t - self._s[0][0] > self.window_s:
            self._s.popleft()

    def group_scale(self) -> float:
        return box_per_group([b / g for _t, b, g in self._s if b and g and PAIR_RANGE[0] <= b / g <= PAIR_RANGE[1]])

    def median(self, source: str = "all") -> Optional[float]:
        """The window's median: « box » the boxes, « group » the body-sized groups as measured, « all » the band
        (a box, else a body-sized group in box units)."""
        k, ref = self.group_scale(), body_ref([b for _t, b, _g in self._s if b is not None])
        if source == "box":
            v = [b for _t, b, _g in self._s if b is not None]
        elif source == "group":
            v = [g for _t, _b, g in self._s if g is not None and body_sized(g * k, ref)]
        else:
            v = [b if b is not None else g * k for _t, b, g in self._s
                 if b is not None or (g is not None and body_sized(g * k, ref))]
        return float(statistics.median(v)) if v else None

    def state(self, now: Optional[float] = None, where: str = "", larger: str = "") -> BandState:
        if now is not None:
            while self._s and now - self._s[0][0] > self.window_s:
                self._s.popleft()
        n = len(self._s)
        if n < self.min_samples:
            self._warn = False
            return BandState("skip", None, n, "dancer at the network: not measured (nobody seen lately)")
        h = self.median()
        at = f" at {where}" if where else ""
        self._warn = h < self.warn_px - self.hyst_px or (self._warn and h < self.warn_px)
        if self._warn:
            tip = f"use the larger one ({larger})" if larger else "narrow the ROI to the dancers' area"
            return BandState("warn", h, n, f"dancer ~{h:.0f} px{at}: too small for this input - {tip}")
        if h > self.note_px:
            return BandState("note", h, n, f"dancer ~{h:.0f} px{at}: large - a smaller input would do")
        return BandState("ok", h, n, f"dancer ~{h:.0f} px{at}")
