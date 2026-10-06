"""IR retroreflective-belt detector: a second measurement source for the output layer.

Each dancer wears a retroreflective belt at the waist (front and back). With an
IR projector next to the lens the belt comes back as a bright, roughly
horizontal band, much brighter than the body (yesterday's on-axis takes: peak
180-255 DN on a 20-60 DN body). When YOLO loses a dancer, the belt keeps the
centroid alive.

Pure numpy + cv2, no tracker / pipeline imports. Coordinates are full-frame
pixels of the gray frame the caller passes (after ``input_transform``).

Two modes
---------
* **Gated** (the live mode): ``detect_near(gray, predictions)`` with one
  ``(key, x, y, gate_px, expected_band_w_px | None)`` per tracked dancer. Only
  a small window around each prediction is analysed, and the result maps every
  key to its best ``BeltBlob`` or None. A blob goes to at most one key (greedy
  on score x distance), so two dancers crossing do not both claim the same
  belt. A window without any pixel above ``floor_dn`` returns at once.
* **Global**: ``detect(gray, roi=None)`` finds candidate clusters on the
  1/4-scale average against its opening, then segments each one at full
  resolution with the same rules as the gated mode (at most
  ``max_candidates`` clusters). A ``StaticMap`` lets it skip fixed glints.

Cost on dev37 (i7-3770K, 1 cv2 thread, machine loaded by other jobs): gated
~0.25 ms per lit prediction (0.05 ms when the window is dark); global ~1.6-2.4
ms on a 1488x1528 show-like frame, ~2.5 ms p50 on the busy 2026-10-05 takes.

Segmentation (per window)
-------------------------
The local background ``bg`` is a grey opening with a square kernel larger than
the band thickness: it erases thin bright structures of any orientation (belt,
eyes, glints) and keeps bodies and walls. A belt pixel must satisfy
``I >= max(floor_dn, k_bg * bg, bg + min_delta_dn)``: an absolute floor and an
adaptive threshold relative to the body/wall behind it.

Rejections (``BeltBlob.reason``; accepted blobs have ``reason is None``)
------------------------------------------------------------------------
* ``small`` / ``too_big``: area or thickness outside the band range;
* ``saturated_body``: a blob inside a large saturated region (a dancer close to
  the camera);
* ``static``: centroid on the static-glint map (``StaticMap``: empty-stage take
  or persistence);
* ``eyes``: two small compact spots side by side (eyes / glasses glowing on
  axis). With a band-width hint, a pair whose span is a large fraction of the
  expected band is a belt split by an arm and is merged instead;
* ``low_score``: the combined score below ``min_score``.

Robustness choices: the score prefers elongated, near-horizontal bands with high
contrast, but a turned or half-hidden belt (shorter, compact blob) is only
down-weighted, never rejected for shape alone. Two collinear pieces of similar
height with a small gap are merged (belt split by an arm); ``pieces`` says so.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, Hashable, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

Prediction = Tuple[Hashable, float, float, float, Optional[float]]


# ---------------------------------------------------------------------------
# Parameters + result type
# ---------------------------------------------------------------------------

@dataclass
class BeltParams:
    # adaptive threshold: I >= max(floor_dn, k_bg * bg, bg + min_delta_dn)
    floor_dn: int = 60
    k_bg: float = 2.0
    min_delta_dn: int = 40
    # band geometry (full-resolution px)
    min_area: int = 6
    max_area: int = 12000
    max_band_h: float = 48.0          # minor extent above this = body / close-up, not a band
    bg_kernel: int = 31               # opening kernel without a band-width hint
    # global mode: candidates on the 1/4-scale average against its opening
    global_bg_kernel: int = 9         # at 1/4 scale (= 36 px full res)
    cand_relax: float = 0.45          # 1/4-scale averaging dims a 3-5 px band to ~40-75 %
    max_candidates: int = 6
    # scoring
    min_score: float = 0.25
    good_contrast: float = 4.0        # mean / bg scale of the contrast score
    good_elong: float = 3.0
    horiz_tol_deg: float = 35.0
    # split belt (arm across it)
    merge_gap_h: float = 2.0          # max gap between pieces, x band height
    merge_gap_w: float = 0.45         # ... or x expected band width when known
    # eyes / glasses
    eyes_max_elong: float = 2.6
    eyes_max_area: int = 600
    eyes_min_sep: float = 1.4         # centre distance / mean spot bbox width
    eyes_max_sep: float = 7.0
    eyes_span_bw: float = 0.6         # with a hint: span < this x band width -> eyes
    # saturated close bodies
    sat_dn: int = 250
    sat_ctx_px: int = 400
    # gated mode
    gate_min_px: float = 6.0
    max_window_px: int = 40000        # larger windows are analysed downscaled


@dataclass
class BeltBlob:
    cx: float                 # contrast-weighted centroid, full-frame px
    cy: float
    w: float                  # major extent (band length), px
    h: float                  # minor extent (band thickness), px
    angle: float              # major axis vs horizontal, degrees in (-90, 90]
    elong: float              # w / h
    peak: int                 # max DN
    mean: float               # mean DN of the blob pixels
    area: int                 # px
    bg: float                 # local background (opening) under the blob, DN
    contrast: float           # mean / max(bg, 1)
    sat_frac: float           # share of pixels >= sat_dn
    score: float              # 0..1
    bbox: Tuple[int, int, int, int]   # x0, y0, x1, y1 inclusive
    pieces: int = 1           # 2 = merged from a split band
    reason: Optional[str] = None      # None = accepted, else why rejected
    key: Optional[Hashable] = None    # gated mode: the prediction it answers
    dist: Optional[float] = None      # gated mode: px from the prediction
    _m: Optional[np.ndarray] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("_m", None)
        for k in ("cx", "cy", "w", "h", "angle", "elong", "mean", "bg", "contrast", "sat_frac", "score",
                  "dist"):
            if d.get(k) is not None:
                d[k] = round(float(d[k]), 3)
        d["bbox"] = [int(v) for v in self.bbox]
        return d


# moment vector layout: area, sw, swx, swy, sx, sy, sxx, syy, sxy, nsat, sum_v, sum_bg, peak, x0, y0, x1, y1
_A, _SW, _SWX, _SWY, _SX, _SY, _SXX, _SYY, _SXY, _NSAT, _SV, _SBG, _PK, _X0, _Y0, _X1, _Y1 = range(17)


def _rect(k: int) -> np.ndarray:
    k = max(3, int(k) | 1)
    return cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))


def _blob_from_moments(arr: np.ndarray, p: BeltParams, m: Optional[list] = None) -> BeltBlob:
    """Moment sums -> features. ``m`` = the same sums as a list (plain-float
    arithmetic is cheaper than numpy scalars); ``arr`` is kept for merging."""
    m = arr.tolist() if m is None else m
    area = float(m[_A])
    sw = max(float(m[_SW]), 1e-9)
    cx, cy = m[_SWX] / sw, m[_SWY] / sw
    mx, my = m[_SX] / area, m[_SY] / area
    a = max(0.0, m[_SXX] / area - mx * mx) + 1.0 / 12
    c = max(0.0, m[_SYY] / area - my * my) + 1.0 / 12
    b = m[_SXY] / area - mx * my
    tr2, det = (a + c) / 2.0, math.sqrt(((a - c) / 2.0) ** 2 + b * b)
    l1, l2 = tr2 + det, max(1.0 / 12, tr2 - det)
    w, h = math.sqrt(12.0 * l1), math.sqrt(12.0 * l2)
    ang = math.degrees(0.5 * math.atan2(2.0 * b, a - c))     # image coords (y down)
    if ang <= -90.0:
        ang += 180.0
    mean = m[_SV] / area
    bg = m[_SBG] / area
    return BeltBlob(cx=float(cx), cy=float(cy), w=float(w), h=float(h), angle=float(ang),
                    elong=float(w / h), peak=int(m[_PK]), mean=float(mean), area=int(area),
                    bg=float(bg), contrast=float(mean / max(bg, 1.0)),
                    sat_frac=float(m[_NSAT] / area), score=0.0,
                    bbox=(int(m[_X0]), int(m[_Y0]), int(m[_X1]), int(m[_Y1])), _m=arr)


def _merge(b1: BeltBlob, b2: BeltBlob, p: BeltParams) -> BeltBlob:
    m = b1._m + b2._m
    m[_PK] = max(b1._m[_PK], b2._m[_PK])
    m[_X0] = min(b1._m[_X0], b2._m[_X0]); m[_Y0] = min(b1._m[_Y0], b2._m[_Y0])
    m[_X1] = max(b1._m[_X1], b2._m[_X1]); m[_Y1] = max(b1._m[_Y1], b2._m[_Y1])
    out = _blob_from_moments(m, p)
    out.pieces = b1.pieces + b2.pieces
    return out


# ---------------------------------------------------------------------------
# Static-glint map
# ---------------------------------------------------------------------------

class StaticMap:
    """Cells (``cell`` px) where something bright sits still: projector
    reflections, lamps, a bright door edge, hot pixels.

    Two ways to fill it:
      * **empty-stage take**: ``add(blobs)`` once per frame with every
        candidate (``detect(..., return_all=True)``), then ``finalize()``: a
        cell is static when hit in >= ``min_frac`` of the frames;
      * **persistence** (online): ``update(blobs, protect)`` keeps an EMA of
        hits per cell; a cell above ``on`` becomes static. ``protect`` =
        (x, y, r) discs around tracked dancers that must never be learnt, so a
        dancer holding still on the wall is not absorbed.
    """

    def __init__(self, shape: Tuple[int, int], cell: int = 8):
        self.cell = int(cell)
        self.shape = (int(shape[0]), int(shape[1]))
        gh, gw = self.shape[0] // self.cell + 1, self.shape[1] // self.cell + 1
        self.cells = np.zeros((gh, gw), bool)
        self.hits = np.zeros((gh, gw), np.float32)
        self.frames = 0

    # -- building --------------------------------------------------------
    def _cover(self, blobs: Iterable[BeltBlob]) -> np.ndarray:
        c = self.cell
        hit = np.zeros(self.cells.shape, bool)
        for b in blobs:
            x0, y0, x1, y1 = b.bbox
            hit[max(0, y0 // c):max(0, y1 // c) + 1, max(0, x0 // c):max(0, x1 // c) + 1] = True
        return hit

    def add(self, blobs: Iterable[BeltBlob]) -> None:
        self.hits += self._cover(blobs)
        self.frames += 1

    def finalize(self, min_frac: float = 0.1, dilate: int = 1) -> "StaticMap":
        if self.frames:
            self.cells |= self.hits >= max(1.0, min_frac * self.frames)
        if dilate > 0 and self.cells.any():
            k = np.ones((2 * dilate + 1, 2 * dilate + 1), np.uint8)
            self.cells = cv2.dilate(self.cells.astype(np.uint8), k) > 0
        return self

    def update(self, blobs: Iterable[BeltBlob], protect: Sequence[Tuple[float, float, float]] = (),
               alpha: float = 0.01, on: float = 0.6) -> None:
        """Online persistence: EMA of per-cell hits (``alpha`` ~ 1 / frames of
        memory); cells above ``on`` turn static (never released)."""
        hit = self._cover(blobs).astype(np.float32)
        keep = np.ones(self.cells.shape, bool)
        c = self.cell
        for (x, y, r) in protect:
            r = max(float(r), float(c))
            keep[max(0, int((y - r) // c)):int((y + r) // c) + 1,
                 max(0, int((x - r) // c)):int((x + r) // c) + 1] = False
        self.hits[keep] = (1.0 - alpha) * self.hits[keep] + alpha * hit[keep]
        self.frames += 1
        self.cells |= self.hits >= on

    # -- queries -----------------------------------------------------------
    def contains(self, x: float, y: float) -> bool:
        cy, cx = int(y) // self.cell, int(x) // self.cell
        if 0 <= cy < self.cells.shape[0] and 0 <= cx < self.cells.shape[1]:
            return bool(self.cells[cy, cx])
        return False

    def box_fraction(self, x0: int, y0: int, x1: int, y1: int) -> float:
        c = self.cell
        sub = self.cells[max(0, y0 // c):max(0, y1 // c) + 1, max(0, x0 // c):max(0, x1 // c) + 1]
        return float(sub.mean()) if sub.size else 0.0

    @property
    def n_cells(self) -> int:
        return int(self.cells.sum())

    def save(self, path) -> None:
        np.savez_compressed(path, cells=self.cells, hits=self.hits, cell=self.cell,
                            shape=np.array(self.shape), frames=self.frames)

    @classmethod
    def load(cls, path) -> "StaticMap":
        d = np.load(path)
        s = cls(tuple(int(v) for v in d["shape"]), int(d["cell"]))
        s.cells = d["cells"].astype(bool)
        s.hits = d["hits"].astype(np.float32)
        s.frames = int(d["frames"])
        return s


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class BeltDetector:
    """See the module docstring. ``static`` is an optional ``StaticMap``.
    ``last`` holds the per-call counters of the latest call (candidates,
    rejections by reason, windows analysed)."""

    def __init__(self, params: Optional[BeltParams] = None, static: Optional[StaticMap] = None):
        self.p = params or BeltParams()
        self.static = static
        self.last: dict = {}

    # -- public API --------------------------------------------------------
    def detect(self, gray: np.ndarray, roi: Optional[Sequence[int]] = None,
               return_all: bool = False) -> List[BeltBlob]:
        """Global mode. ``roi`` = (x, y, w, h) in full-frame px. Returns the
        accepted blobs sorted by score (``return_all``: rejected ones too,
        with ``reason`` set).

        Candidates come from the 1/4-scale average thresholded (relaxed)
        against its own opening (the background); each candidate cluster is
        then segmented at full resolution against that same background, up to
        ``max_candidates`` clusters (largest peak - background first: the
        retroreflector is the brightest thing on a dancer)."""
        p = self.p
        g = gray if gray.ndim == 2 else gray[:, :, 0]
        H, W = g.shape[:2]
        ox, oy, rw, rh = 0, 0, W, H
        if roi is not None:
            ox, oy = max(0, int(roi[0])), max(0, int(roi[1]))
            rw, rh = min(int(roi[2]), W - ox), min(int(roi[3]), H - oy)
        self.last = {"windows": 0, "candidates": 0, "rejected": {}}
        if rw < 16 or rh < 16:
            return []
        sub = g[oy:oy + rh, ox:ox + rw]
        w2, h2 = rw // 2, rh // 2
        w4, h4 = w2 // 2, h2 // 2
        g2 = cv2.resize(sub[:h2 * 2, :w2 * 2], (w2, h2), interpolation=cv2.INTER_AREA)
        g4 = cv2.resize(g2[:h4 * 2, :w4 * 2], (w4, h4), interpolation=cv2.INTER_AREA)
        if cv2.minMaxLoc(g4)[1] < p.floor_dn * p.cand_relax:
            return []
        bg4 = cv2.morphologyEx(g4, cv2.MORPH_OPEN, _rect(p.global_bg_kernel))
        m4 = cv2.compare(g4, cv2.LUT(bg4, self._lut(p.cand_relax)), cv2.CMP_GE)
        pts = cv2.findNonZero(m4)
        if pts is None:
            return []
        pts = pts.reshape(-1, 2)
        cell = 3                                   # at 1/4 scale = 12 px full res
        cimg = np.zeros((h4 // cell + 1, w4 // cell + 1), np.uint8)
        cimg[pts[:, 1] // cell, pts[:, 0] // cell] = 255
        cimg = cv2.dilate(cimg, np.ones((3, 3), np.uint8))   # one window per cluster
        n, _lab, st, _c = cv2.connectedComponentsWithStats(cimg, connectivity=8)
        boxes = []
        for i in range(1, n):
            x, y, bw, bh, _a = (int(v) for v in st[i])
            # the dilated cell box, at 1/4 scale (dilation already adds a cell of margin)
            u0, v0 = x * cell, y * cell
            u1, v1 = min(w4, (x + bw) * cell), min(h4, (y + bh) * cell)
            fx0, fy0, fx1, fy1 = ox + 4 * u0, oy + 4 * v0, ox + 4 * u1, oy + 4 * v1
            if self.static is not None and self.static.box_fraction(fx0, fy0, fx1 - 1, fy1 - 1) >= 0.99:
                self._count("static")
                continue
            _mn, pk, _ml, loc = cv2.minMaxLoc(g4[v0:v1, u0:u1])
            boxes.append((pk - float(bg4[v0 + loc[1], u0 + loc[0]]), u0, v0, u1, v1))
        self.last["candidates"] = len(boxes)
        if len(boxes) > p.max_candidates:
            boxes.sort(key=lambda b: -b[0])
            self.last["dropped_candidates"] = len(boxes) - p.max_candidates
            boxes = boxes[:p.max_candidates]
        raw: List[BeltBlob] = []
        for _r, u0, v0, u1, v1 in boxes:
            bgf = cv2.resize(bg4[v0:v1, u0:u1], (4 * (u1 - u0), 4 * (v1 - v0)),
                             interpolation=cv2.INTER_NEAREST)
            raw += self._segment(g, ox + 4 * u0, oy + 4 * v0, ox + 4 * u1, oy + 4 * v1, bg=bgf)
        blobs = self._finish(_dedupe(raw), g, hint_w=None)
        if return_all:
            return sorted(blobs, key=lambda b: (b.reason is not None, -b.score))
        return sorted((b for b in blobs if b.reason is None), key=lambda b: -b.score)

    def detect_near(self, gray: np.ndarray, predictions: Iterable[Prediction],
                    return_all: bool = False) -> Dict[Hashable, Optional[BeltBlob]]:
        """Gated mode. ``predictions``: (key, x, y, gate_px, expected_band_w_px
        or None). Returns {key: best accepted blob within the gate, or None}.
        Each blob answers at most one key (greedy on score x distance).
        ``return_all`` adds ``'_all'``: every blob seen in the windows."""
        p = self.p
        g = gray if gray.ndim == 2 else gray[:, :, 0]
        H, W = g.shape[:2]
        preds = list(predictions)
        out: Dict[Hashable, Optional[BeltBlob]] = {pr[0]: None for pr in preds}
        self.last = {"windows": 0, "candidates": 0, "rejected": {}}
        props: List[Tuple[float, int, BeltBlob]] = []
        seen: List[BeltBlob] = []
        for pi, (key, x, y, gate, bw) in enumerate(preds):
            gate = max(float(gate), p.gate_min_px)
            bw = float(bw) if bw else None
            kb = int(min(41.0, max(11.0, 0.75 * bw))) if bw else p.bg_kernel
            hx = gate + (0.6 * bw if bw else 0.5 * kb) + kb // 2 + 2
            hy = gate + (0.25 * bw if bw else 0.0) + kb // 2 + 2
            x0, x1 = int(max(0, x - hx)), int(min(W, x + hx + 1))
            y0, y1 = int(max(0, y - hy)), int(min(H, y + hy + 1))
            if x1 - x0 < 4 or y1 - y0 < 4:
                continue
            if cv2.minMaxLoc(g[y0:y1, x0:x1])[1] < p.floor_dn:
                self.last["windows"] += 1
                continue
            blobs = self._finish(self._segment(g, x0, y0, x1, y1, kb=kb), g, hint_w=bw)
            for b in blobs:
                d = math.hypot(b.cx - x, b.cy - y)
                b.dist = d
                if b.reason is None and d <= gate:
                    props.append((b.score * math.exp(-0.5 * (d / gate) ** 2), pi, b))
            seen += blobs
        props.sort(key=lambda t: -t[0])
        taken: List[BeltBlob] = []
        for _rank, pi, b in props:
            key = preds[pi][0]
            if out[key] is not None:
                continue
            if any(abs(b.cx - t.cx) < 3 and abs(b.cy - t.cy) < 3 for t in taken):
                continue
            b2 = BeltBlob(**dict(b.__dict__))
            b2.key = key
            out[key] = b2
            taken.append(b)
        if return_all:
            out["_all"] = _dedupe(seen)        # type: ignore[assignment]
        return out

    # -- internals -----------------------------------------------------------
    def _count(self, reason: str) -> None:
        r = self.last.setdefault("rejected", {})
        r[reason] = r.get(reason, 0) + 1

    @staticmethod
    def _background(c: np.ndarray, kb: int) -> np.ndarray:
        """Grey opening (square kernel ``kb``): thin bright structures of any
        orientation disappear, bodies and walls stay. Large kernels run at
        half resolution (4x cheaper, same result to a DN or two)."""
        h, w = c.shape[:2]
        if kb >= 15 and h >= 2 * kb and w >= 2 * kb:
            c2 = cv2.resize(c[:h // 2 * 2, :w // 2 * 2], (w // 2, h // 2), interpolation=cv2.INTER_AREA)
            b2 = cv2.morphologyEx(c2, cv2.MORPH_OPEN, _rect(kb // 2))
            return cv2.resize(b2, (w, h), interpolation=cv2.INTER_LINEAR)
        return cv2.morphologyEx(c, cv2.MORPH_OPEN, _rect(kb))

    def _lut(self, relax: float = 1.0) -> np.ndarray:
        """Threshold LUT on the background: max(floor, k*bg, bg + delta).
        ``relax`` < 1 (candidate search on the 1/4-scale average, which dims
        thin bands): max(relax*floor, bg*(1 + relax*(k-1)), bg + relax*delta)."""
        key = round(float(relax), 4)
        cache = self.__dict__.setdefault("_luts", {})
        sig = (self.p.floor_dn, self.p.k_bg, self.p.min_delta_dn, key)
        if cache.get(key, (None,))[0] != sig:
            i = np.arange(256, dtype=np.float64)
            t = np.maximum.reduce([np.full(256, self.p.floor_dn * relax),
                                   i * (1.0 + relax * (self.p.k_bg - 1.0)),
                                   i + self.p.min_delta_dn * relax])
            cache[key] = (sig, np.clip(np.ceil(t), 0, 255).astype(np.uint8))
        return cache[key][1]

    def _segment(self, g: np.ndarray, x0: int, y0: int, x1: int, y1: int,
                 kb: Optional[int] = None, bg: Optional[np.ndarray] = None) -> List[BeltBlob]:
        """Adaptive-threshold segmentation of one full-res window -> raw blobs
        (features only, no rejection). ``bg``: a precomputed background of the
        window's size (global mode), else an opening with kernel ``kb``.
        Components smaller than ``min_area`` are only counted (``small``).
        Windows above ``max_window_px`` (a dancer close to the camera) are
        analysed on an integer-downscaled copy, so the cost stays bounded."""
        p = self.p
        H, W = g.shape[:2]
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 - x0 < 3 or y1 - y0 < 3:
            return []
        self.last["windows"] = self.last.get("windows", 0) + 1
        c = g[y0:y1, x0:x1]
        if bg is not None and bg.shape != c.shape:
            bg = bg[:c.shape[0], :c.shape[1]]
        s = int(math.ceil(math.sqrt(c.size / float(p.max_window_px)))) if c.size > p.max_window_px else 1
        if s > 1:
            hs, ws = c.shape[0] // s, c.shape[1] // s
            c = cv2.resize(c[:hs * s, :ws * s], (ws, hs), interpolation=cv2.INTER_AREA)
            if bg is not None:
                bg = cv2.resize(bg[:hs * s, :ws * s], (ws, hs), interpolation=cv2.INTER_AREA)
            self.last["downscaled"] = self.last.get("downscaled", 0) + 1
        if bg is None:
            bg = self._background(c, max(3, (kb or p.bg_kernel) // s))
        m = cv2.compare(c, cv2.LUT(bg, self._lut()), cv2.CMP_GE)
        min_area = max(1, int(round(p.min_area / (s * s))))
        if cv2.countNonZero(m) < min_area:
            return []
        # label on a 3x3-closed mask (joins noise holes), features on the real pixels
        mc = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, lab, st, _cen = cv2.connectedComponentsWithStatsWithAlgorithm(mc, 8, cv2.CV_32S, cv2.CCL_GRANA)
        # component triage on plain ints (numpy calls on tiny arrays cost more than the work)
        s2 = float(s * s)
        keep, small, big = [], 0, 0
        for i, (bx, by, bw, bh, ba) in enumerate(st.tolist()):
            if i == 0:
                continue
            if ba < min_area:
                small += 1
            elif min(bw, bh) * s > 2 * p.max_band_h or ba * s2 > 4 * p.max_area:
                big += 1                       # obviously not a band: skip the feature pass
            else:
                keep.append((i, bx, by, bw, bh))
        if small or big:
            r = self.last.setdefault("rejected", {})
            if small:
                r["small"] = r.get("small", 0) + small
            if big:
                r["too_big"] = r.get("too_big", 0) + big
        # per kept component: cv2 moments on its bbox crop, grown by 1 px so no
        # crop is a 1-px line (cv2 reads a (k, 1) array of <= 4 values as a scalar)
        hh, ww = c.shape[:2]
        out = []
        for i, bx, by, bw, bh in keep:
            cx0, cy0 = max(0, bx - 1), max(0, by - 1)
            cx1, cy1 = min(ww, bx + bw + 1), min(hh, by + bh + 1)
            cc, bb = c[cy0:cy1, cx0:cx1], bg[cy0:cy1, cx0:cx1]
            mk = cv2.bitwise_and(cv2.compare(lab[cy0:cy1, cx0:cx1], i, cv2.CMP_EQ), m[cy0:cy1, cx0:cx1])
            mo = cv2.moments(mk, binaryImage=True)
            a0 = mo["m00"]
            if a0 * s2 < p.min_area:
                self._count("small")
                continue
            wo = cv2.moments(cv2.subtract(cc, bb, mask=mk))
            mean = cv2.mean(cc, mask=mk)[0]
            bgm = cv2.mean(bb, mask=mk)[0]
            pk = cv2.minMaxLoc(cc, mask=mk)[1]
            nsat = cv2.countNonZero(cv2.bitwise_and(cv2.compare(cc, p.sat_dn, cv2.CMP_GE), mk))
            # crop px (u, v) -> full-frame X = s*u + ox; a downscaled pixel stands for s*s
            ox, oy = x0 + s * cx0 + 0.5 * (s - 1), y0 + s * cy0 + 0.5 * (s - 1)
            su, sv = s * mo["m10"], s * mo["m01"]
            A = a0 * s2
            SX, SY = s2 * (su + ox * a0), s2 * (sv + oy * a0)
            sw = wo["m00"]
            if sw > 0:
                SW, SWX, SWY = s2 * sw, s2 * (s * wo["m10"] + ox * sw), s2 * (s * wo["m01"] + oy * sw)
            else:
                SW, SWX, SWY = A, SX, SY
            M = [0.0] * 17
            M[_A], M[_SW], M[_SWX], M[_SWY], M[_SX], M[_SY] = A, SW, SWX, SWY, SX, SY
            M[_SXX] = s2 * (s * s * mo["m20"] + 2 * ox * su + ox * ox * a0)
            M[_SYY] = s2 * (s * s * mo["m02"] + 2 * oy * sv + oy * oy * a0)
            M[_SXY] = s2 * (s * s * mo["m11"] + ox * sv + oy * su + ox * oy * a0)
            M[_NSAT], M[_SV], M[_SBG], M[_PK] = s2 * nsat, mean * A, bgm * A, pk
            M[_X0], M[_Y0] = bx * s + x0, by * s + y0
            M[_X1], M[_Y1] = (bx + bw) * s - 1 + x0, (by + bh) * s - 1 + y0
            out.append(_blob_from_moments(np.array(M), p, M))
        return out

    def _finish(self, blobs: List[BeltBlob], g: np.ndarray, hint_w: Optional[float]) -> List[BeltBlob]:
        """Size / saturation / static rejections, eyes vs split-belt pairing,
        then scoring. Returns every blob (rejected ones carry ``reason``)."""
        p = self.p
        for b in blobs:
            if b.area < p.min_area:
                b.reason = "small"
            elif b.h > p.max_band_h or b.area > p.max_area:
                b.reason = "too_big"
            elif self.static is not None and self.static.contains(b.cx, b.cy):
                b.reason = "static"
            elif b.peak >= p.sat_dn and self._sat_context(g, b) > max(p.sat_ctx_px, 2 * b.area):
                b.reason = "saturated_body"
        rejected = [b for b in blobs if b.reason is not None]
        live = [b for b in blobs if b.reason is None]
        out = rejected + self._pairs(live, hint_w)
        for b in out:
            if b.reason is None:
                b.score = self._score(b, hint_w)
                if b.score < p.min_score:
                    b.reason = "low_score"
            if b.reason is not None:
                self._count(b.reason)
        return out

    def _sat_context(self, g: np.ndarray, b: BeltBlob) -> int:
        """Saturated px around the blob (outside it): a close, blown-out body."""
        x0, y0, x1, y1 = b.bbox
        mx, my = int(max(b.w, 2 * b.h, 16)), int(max(2 * b.h, 16))
        H, W = g.shape[:2]
        win = g[max(0, y0 - my):min(H, y1 + my + 1), max(0, x0 - mx):min(W, x1 + mx + 1)]
        n = int(cv2.countNonZero(cv2.compare(win, self.p.sat_dn, cv2.CMP_GE)))
        return n - int(b._m[_NSAT])

    def _pairs(self, live: List[BeltBlob], hint_w: Optional[float]) -> List[BeltBlob]:
        """Eyes/glasses pairs -> rejected; split belt pieces -> merged."""
        p = self.p
        if len(live) < 2:
            return live
        cand = []
        for i in range(len(live)):
            for j in range(i + 1, len(live)):
                a, b = live[i], live[j]
                dx, dy = abs(a.cx - b.cx), abs(a.cy - b.cy)
                hmax = max(a.h, b.h)
                if dy > max(3.0, 0.8 * hmax, 0.35 * dx):
                    continue
                gap = max(0, max(a.bbox[0], b.bbox[0]) - min(a.bbox[2], b.bbox[2]) - 1)
                cand.append((gap, i, j))
        cand.sort()
        used = set()
        merged: Dict[int, BeltBlob] = {}
        for gap, i, j in cand:
            if i in used or j in used:
                continue
            a, b = live[i], live[j]
            span = max(a.bbox[2], b.bbox[2]) - min(a.bbox[0], b.bbox[0]) + 1
            bwa, bwb = a.bbox[2] - a.bbox[0] + 1, b.bbox[2] - b.bbox[0] + 1
            sep = abs(a.cx - b.cx) / max(1.0, 0.5 * (bwa + bwb))
            compact = (a.elong <= p.eyes_max_elong and b.elong <= p.eyes_max_elong
                       and max(a.area, b.area) <= p.eyes_max_area)
            similar = (max(a.area, b.area) <= 4 * min(a.area, b.area)
                       and max(a.peak, b.peak) <= 1.6 * min(a.peak, b.peak))
            eyes_like = compact and similar and p.eyes_min_sep <= sep <= p.eyes_max_sep
            hmax, hmin = max(a.h, b.h), min(a.h, b.h)
            gap_ok = gap <= max(p.merge_gap_h * hmax, p.merge_gap_w * hint_w if hint_w else 0.0)
            band_like = hmax <= 2.5 * hmin and max(a.mean, b.mean) <= 2.5 * min(a.mean, b.mean)
            if hint_w:
                if eyes_like and span < p.eyes_span_bw * hint_w:
                    a.reason = b.reason = "eyes"
                    used.update((i, j))
                elif gap_ok and band_like and span <= 1.6 * hint_w:
                    merged[i] = _merge(a, b, p)
                    used.update((i, j))
            else:
                if eyes_like:
                    a.reason = b.reason = "eyes"
                    used.update((i, j))
                elif gap_ok and band_like and _horizontal_piece(a, p) and _horizontal_piece(b, p):
                    merged[i] = _merge(a, b, p)
                    used.update((i, j))
        out = []
        for i, b in enumerate(live):
            if i in merged:
                out.append(merged[i])
            elif i not in used:
                out.append(b)
            elif b.reason is not None:
                out.append(b)
        return out

    def _score(self, b: BeltBlob, hint_w: Optional[float]) -> float:
        p = self.p
        # contrast vs the local background: 1 - exp(-2 (r-1)/(good-1)): r = 2 -> 0.49, 3 -> 0.74, 5 -> 0.93
        s_con = 1.0 - math.exp(-2.0 * max(0.0, b.contrast - 1.0) / (p.good_contrast - 1.0))
        s_el = 0.45 + 0.55 * min(1.0, max(0.0, (b.elong - 1.0) / (p.good_elong - 1.0)))
        dev = abs(b.angle)
        s_ang = 1.0 if (b.elong < 1.3 or dev <= p.horiz_tol_deg) else \
            max(0.55, 1.0 - (dev - p.horiz_tol_deg) / 90.0)
        s_sz = 1.0
        if hint_w:
            r = max(b.w, 1.0) / hint_w
            s_sz = math.exp(-0.5 * (math.log(r) / 0.5) ** 2) if r > 1 else \
                max(0.4, math.exp(-0.5 * (math.log(r) / 0.9) ** 2))
        s_sat = 0.7 if (b.sat_frac > 0.6 and b.area > 300) else 1.0
        s_px = 0.7 if b.area < 2 * p.min_area else 1.0
        return float(s_con * s_el * s_ang * s_sz * s_sat * s_px)


def _horizontal_piece(b: BeltBlob, p: BeltParams) -> bool:
    """A plausible piece of a split belt: elongated and near-horizontal."""
    return b.elong >= 1.6 and abs(b.angle) <= p.horiz_tol_deg


def _dedupe(blobs: List[BeltBlob], tol: float = 3.0) -> List[BeltBlob]:
    """Overlapping windows see the same blob twice: keep the larger copy."""
    out: List[BeltBlob] = []
    for b in sorted(blobs, key=lambda b: -b.area):
        if any(abs(b.cx - o.cx) < tol and abs(b.cy - o.cy) < tol for o in out):
            continue
        out.append(b)
    return out


def static_map_from_frames(frames: Iterable[np.ndarray], detector: Optional[BeltDetector] = None,
                           roi: Optional[Sequence[int]] = None, min_frac: float = 0.1,
                           cell: int = 8) -> StaticMap:
    """Empty-stage frames -> StaticMap (every candidate, accepted or not, is
    static by definition on an empty stage)."""
    det = BeltDetector(detector.p if detector else None)
    sm: Optional[StaticMap] = None
    for fr in frames:
        g = fr if fr.ndim == 2 else fr[:, :, 0]
        if sm is None:
            sm = StaticMap(g.shape[:2], cell)
        sm.add([b for b in det.detect(g, roi, return_all=True) if b.reason != "small"])
    if sm is None:
        raise ValueError("no frames")
    return sm.finalize(min_frac)
