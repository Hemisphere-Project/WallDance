"""X2 (Plan Cordée rev 2, #t-054): a light verdict from the empty wall, with one instruction.

The empty-wall plate (``core/foreground.CleanPlate``: the raw gray of the empty wall, downscaled) is read
in parts over the zone (the ROI): windows of one cell -- about 1/8 of the zone's long side, the short side
in proportion -- at half-cell steps, so a spot is never split by a cell border.  The brightest part against
the darkest one is the light's range.  On 08/10 it was 15 to 20 times
(notes/2026-11-25-rehearsal-0810-counter-analysis.md §2): 8 bits cannot hold white clothes in the oval and
dark hair at the edge together.  Above 4 times the verdict is red and says where the light is too strong,
where it is too weak, and the one thing to move: the IR flood (Thomas, 25/11: the app may ask for it).

The darkest part skips the darkest 5 % of the zone: a corner of night sky or roof inside the ROI (08/10's
façade ROI has one, top left) would otherwise keep a well-lit wall red for good.  The brightest part skips
nothing: a small hot spot is exactly what must turn red.

- The light off to one side (the bright part's centre is not in the middle third): aim the flood more
  toward the opposite side, away from the bright part -- back onto the middle of the zone.
- The light a spot in the middle (the bright part covers at most a third of the zone): move the flood back
  from the wall, or widen its beam, so it covers the whole zone.
- The light broad and centred but missing one side: aim the flood more toward the dark side.
- The whole zone dark: light it.

After a move the technician runs Calibrate again and reads the new verdict: one instruction at a time.
A bright part at white (clipped) only gives a lower bound of the ratio: the text says « at least ».

Pure numpy; a plate is ~450 x 320 floats, so a verdict costs well under a millisecond.  No GUI, no camera.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np

LIGHT_RED_RATIO = 4.0      # roadmap X2: red above 4 times between the brightest and the darkest part
LIGHT_CELLS_LONG = 8       # cells along the zone's long side (a part the technician can point at)
LIGHT_CELLS_MIN = 3        # ... and at least this many along its short side
LIGHT_CLIP_DN = 250.0      # a pixel at or above this is at white (the clip level used elsewhere)
LIGHT_CLIP_SHARE = 0.5     # the brightest part is « at white » when half its pixels are
LIGHT_DARK_DN = 16.0       # the brightest part below this: the whole zone is dark, there is no ratio to judge
LIGHT_FLOOR_DN = 1.0       # a darker part counts as this (the ratio is then a lower bound)
LIGHT_DARK_SKIP = 0.05     # the darkest part skips this share of the darkest parts (sky, roof in the ROI)
LIGHT_HOT_SHARE = 0.8      # the bright part: the parts at >= this share of the brightest one
LIGHT_SPOT_SHARE = 1 / 3   # a centred bright part covering at most this share of the zone is a spot

_COLS = ("left", "", "right")
_ROWS = ("top", "", "bottom")
_AGAIN = "then Calibrate on the empty wall again"


@dataclass
class LightVerdict:
    """The verdict on the light of the zone.  ``status`` speaks the readiness language: ``ok`` (green),
    ``warn``, ``fail`` (red), ``skip`` (nothing to judge)."""
    status: str
    detail: str                         # the fact, then « : » and the one instruction when there is one
    ratio: Optional[float] = None       # brightest / darkest part (a lower bound when ``bounded``)
    bright_dn: float = 0.0              # the brightest part's mean level (0-255)
    dark_dn: float = 0.0                # the darkest part's mean level
    bright_at: str = ""                 # where, in the zone: « top left », « middle », ...
    dark_at: str = ""
    clipped: bool = False               # the brightest part is at white
    bounded: bool = False               # the ratio is a lower bound (clipped, or a part below the floor)
    instruction: str = ""
    grid: Tuple[int, int] = (0, 0)      # the cell grid (columns, rows); parts are read at half-cell steps
    hot_share: float = 0.0              # the share of the zone in the bright part

    @property
    def red(self) -> bool:
        return self.status == "fail"

    def line(self) -> str:
        """One line for Calibrate's result and the console."""
        word = {"ok": "ok", "warn": "CHECK", "fail": "RED", "skip": "not judged"}.get(self.status, self.status)
        return f"Wall light: {word} - {self.detail}"


def where_words(u: float, v: float) -> str:
    """A position in the zone (``u`` left -> right, ``v`` top -> bottom, both 0..1) in the technician's
    words: « top left », « left », « middle », ...  Thirds of the zone, as seen on the screen."""
    col = _COLS[0 if u < 1 / 3 else 2 if u > 2 / 3 else 1]
    row = _ROWS[0 if v < 1 / 3 else 2 if v > 2 / 3 else 1]
    return " ".join(w for w in (row, col) if w) or "middle"


def opposite_words(where: str) -> str:
    """« top left » -> « bottom right », « left » -> « right », « middle » -> « middle »."""
    swap = {"top": "bottom", "bottom": "top", "left": "right", "right": "left"}
    return " ".join(swap.get(w, w) for w in where.split())


def _times(ratio: float, bounded: bool) -> str:
    r = f"{ratio:.0f}x" if ratio >= 10 else f"{ratio:.1f}x"
    return ("at least " + r) if bounded else r


def zone_parts(img: np.ndarray, roi: Optional[Sequence[float]] = None, ds: int = 1,
               cells_long: int = LIGHT_CELLS_LONG, clip_dn: float = LIGHT_CLIP_DN
               ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, Tuple[int, int]]]:
    """The parts of the zone ``roi`` = (x, y, w, h) in full-frame pixels on ``img``, a gray image
    downscaled ``ds`` times (a plate); None = the whole image.  The ROI is clamped onto the image.

    A part is one cell (``cells_long`` along the zone's long side, the short side in proportion), taken at
    half-cell steps so a spot is never split by a cell border.  Returns (means, shares at white) of the
    parts, each 2-D (rows, columns), their centres u (columns) and v (rows) as 0..1 of the zone, and the
    cell grid (columns, rows); None when the zone is too small to cut (< 2 px per cell)."""
    g = img
    if g.ndim == 3:
        g = g[:, :, 0]
    g = np.asarray(g, dtype=np.float32)
    H, W = g.shape[:2]
    if roi is None:
        x0, y0, x1, y1 = 0, 0, W, H
    else:
        x, y, w, h = (float(v) for v in roi)
        x0 = max(0, min(W, int(round(x / ds))))
        y0 = max(0, min(H, int(round(y / ds))))
        x1 = max(0, min(W, int(round((x + w) / ds))))
        y1 = max(0, min(H, int(round((y + h) / ds))))
    zw, zh = x1 - x0, y1 - y0
    if zw <= 0 or zh <= 0:
        return None
    if zw >= zh:
        cols = cells_long
        rows = max(LIGHT_CELLS_MIN, int(round(cells_long * zh / zw)))
    else:
        rows = cells_long
        cols = max(LIGHT_CELLS_MIN, int(round(cells_long * zw / zh)))
    if zw < 2 * cols or zh < 2 * rows:
        return None
    zone = g[y0:y1, x0:x1].astype(np.float64)
    # half-cells: their sums, pixel counts and pixels at white; a part is 2 x 2 of them, every half-cell
    xe = np.linspace(0, zw, 2 * cols + 1).round().astype(int)
    ye = np.linspace(0, zh, 2 * rows + 1).round().astype(int)

    def sums(a: np.ndarray) -> np.ndarray:
        s = np.add.reduceat(np.add.reduceat(a, ye[:-1], axis=0), xe[:-1], axis=1)
        return s[:-1, :-1] + s[1:, :-1] + s[:-1, 1:] + s[1:, 1:]

    n = np.outer(np.diff(ye), np.diff(xe)).astype(np.float64)
    n = n[:-1, :-1] + n[1:, :-1] + n[:-1, 1:] + n[1:, 1:]
    means = sums(zone) / n
    white = sums((zone >= clip_dn).astype(np.float64)) / n
    return means, white, xe[1:-1] / zw, ye[1:-1] / zh, (cols, rows)


def light_verdict(img: Optional[np.ndarray], roi: Optional[Sequence[float]] = None, ds: int = 1,
                  red_ratio: float = LIGHT_RED_RATIO) -> LightVerdict:
    """The verdict on the zone ``roi`` (x, y, w, h, full-frame pixels; None = the whole image) of the
    empty-wall image ``img`` (a plate's ``plate`` array, downscaled ``ds`` times)."""
    if img is None:
        return LightVerdict("skip", "no empty-wall snapshot to judge the light - Calibrate on the empty wall")
    cut = zone_parts(img, roi, ds)
    if cut is None:
        return LightVerdict("skip", "the zone is too small to judge the light - check the ROI")
    means, white, us, vs, grid = cut

    b = float(means.max())
    order = np.argsort(means.ravel(), kind="stable")
    k = min(order.size - 1, int(LIGHT_DARK_SKIP * order.size))       # skip the darkest 5 % of the parts
    jd, id_ = np.unravel_index(int(order[k]), means.shape)
    d = float(means[jd, id_])
    dark_at = where_words(float(us[id_]), float(vs[jd]))
    hot = means >= LIGHT_HOT_SHARE * b
    hj, hi = np.nonzero(hot)
    bright_at = where_words(float(us[hi].mean()), float(vs[hj].mean()))     # the bright part's centre
    hot_share = float(hot.mean())
    jb, ib = np.unravel_index(int(means.argmax()), means.shape)
    clipped = bool(b >= LIGHT_CLIP_DN or white[jb, ib] >= LIGHT_CLIP_SHARE
                   or float(white[hot].mean()) >= LIGHT_CLIP_SHARE)
    common = dict(bright_dn=b, dark_dn=d, bright_at=bright_at, dark_at=dark_at, clipped=clipped, grid=grid,
                  hot_share=hot_share)

    if b < LIGHT_DARK_DN:
        todo = f"light the zone with the IR flood, {_AGAIN}"
        return LightVerdict("fail", f"the whole zone is dark (its brightest part is at {b:.0f} of 255): {todo}",
                            ratio=None, instruction=todo, **common)

    floored = d < LIGHT_FLOOR_DN
    ratio = b / max(d, LIGHT_FLOOR_DN)
    bounded = clipped or floored
    times = _times(ratio, bounded)
    limit = f"red above {red_ratio:g}x"

    if ratio > red_ratio:
        if bright_at != "middle":
            fact = f"the {bright_at} of the zone is {times} brighter than the {dark_at} ({limit})"
            todo = (f"aim the IR flood more toward the {opposite_words(bright_at)}, away from the {bright_at}, "
                    f"{_AGAIN}")
        elif hot_share <= LIGHT_SPOT_SHARE or dark_at == "middle":
            fact = (f"the light is a spot in the middle of the zone, {times} brighter than the {dark_at} "
                    f"({limit})")
            todo = f"move the IR flood back from the wall (or widen its beam) so it covers the whole zone, {_AGAIN}"
        else:
            fact = (f"the light does not reach the {dark_at} of the zone: the middle is {times} brighter "
                    f"({limit})")
            todo = f"aim the IR flood more toward the {dark_at}, {_AGAIN}"
        return LightVerdict("fail", f"{fact}: {todo}", ratio=ratio, bounded=bounded, instruction=todo,
                            **common)

    if clipped:
        fact = (f"the {bright_at} of the zone is at white, so its light is at least {ratio:.1f}x the "
                f"{dark_at} and may be above {red_ratio:g}x")
        todo = "Calibrate on the empty wall again, then check again"
        return LightVerdict("warn", f"{fact}: {todo}", ratio=ratio, bounded=True, instruction=todo, **common)

    fact = (f"even enough: the brightest part of the zone ({bright_at}) is {times} the darkest ({dark_at}), "
            f"{limit}")
    return LightVerdict("ok", fact, ratio=ratio, bounded=bounded, **common)


def plate_light_verdict(plate, roi: Optional[Sequence[float]] = None,
                        red_ratio: float = LIGHT_RED_RATIO) -> LightVerdict:
    """The verdict of a ``CleanPlate`` (None: no plate) over ``roi`` (x, y, w, h in the plate's full-frame
    pixels; None = the whole frame).  The one function Calibrate's own flow can call on the plate it took."""
    if plate is None:
        return light_verdict(None)
    return light_verdict(plate.plate, roi, int(getattr(plate, "ds", 1) or 1), red_ratio)
