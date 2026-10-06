"""Library half of the IR-marker eval harness (MRK-1). The CLI is marker_eval.py.

Everything here is importable without a GPU, torch or the app pipeline, so the
unit tests (application/tests/test_marker_eval.py) can drive it on synthetic
frames. It only needs numpy, cv2 and ``marker_model``. For a remote run that
module is uploaded next to this file (``wdremote py --with``); in a checkout
it comes from ``application/src/core``.

Contents (02-ir-markers.md section numbers):
  * ``detect_blobs``: the §2.2 detector. Threshold + early-out, sparse
    labelling through a 4 px cell grid, bincount features (area, n_sat, peak,
    intensity-weighted sub-pixel centroid, second moments giving elongation,
    orientation and streak length).
  * ``GlintMap`` (§2.2 step 5) and ``ChainLinker`` (persistence, fixed vs
    moving chains).
  * ``associate`` (§3.1 Tier A): one global Hungarian over (tracks x slots) x
    blobs, gate = max(4 px, g·H) + 0.5·L_streak, plus the contested-blob rule.
  * ``PoseMetrics``: per-frame accumulators for M1, M2b, M3, M4, M5, M6 data
    and M7. ``FloorStats`` covers M2 and M3 on the empty stage.
  * ``gap_study``: the synth_markers.py method (unlabelled offset vote
    through simulated YOLO gaps), on keypoints or on real blobs (M6).
  * ``GATES`` / ``gate_*``: the §6.3 go/no-go thresholds -> PASS / FAIL /
    NO-GO / N/A.
  * ``read_take_meta``: ``.meta`` v2 provenance (camera, rig sheet, camlog).
    It tolerates v1 sidecars and missing ones.
  * Contact-sheet helpers sized for a 4G link.
"""
from __future__ import annotations

import gzip
import itertools
import json
import math
import os
import random
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

try:                                   # uploaded next to us (wdremote py --with)
    import marker_model as mm          # type: ignore
except ImportError:                    # checkout: application/src on sys.path
    from core import marker_model as mm  # type: ignore

N_EXT = len(mm.EXT_KPTS)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def rnd(v, nd: int = 3):
    """JSON-friendly rounding (None/NaN -> None)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if not math.isfinite(f):
        return None
    return round(f, nd)


def pct(values, q, nd: int = 3):
    v = [x for x in values if x is not None and math.isfinite(x)]
    return rnd(float(np.percentile(v, q)), nd) if v else None


def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return rnd(o, 4)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, Path):
        return str(o)
    return o


def to_gray(frame: np.ndarray, mono: Optional[bool] = None) -> Tuple[np.ndarray, bool]:
    """BGR (R==G==B for IDS recordings) or gray -> contiguous gray. ``mono``
    caches the B==G==R check: the first frame decides, as in the pipeline's
    P-1 path."""
    if frame.ndim == 2:
        return frame, True
    if mono is None:
        mono = bool(np.array_equal(frame[:, :, 0], frame[:, :, 1])
                    and np.array_equal(frame[:, :, 0], frame[:, :, 2]))
    if mono:
        return np.ascontiguousarray(frame[:, :, 0]), mono
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), mono


# ---------------------------------------------------------------------------
# Detector (§2.2 steps 1-2)
# ---------------------------------------------------------------------------

def detect_blobs(gray: np.ndarray, T: int, *, cell: int = 4, min_area: int = 2,
                 max_area: int = 0, offset: Sequence[float] = (0, 0),
                 max_blobs: int = 500, stats: Optional[dict] = None) -> List[dict]:
    """Bright-blob candidates at threshold ``T`` (pixels >= T).

    1. Early-out: ``cv2.threshold`` + ``countNonZero``; an empty frame costs
       ~0.5 ms and returns [].
    2. Sparse labelling: ``findNonZero``, then the points are grouped through
       a ``cell``-px grid with an 8-connected CC on the small cell image. This
       joins bloom halves and streak gaps; points up to ~2·cell-1 px apart
       merge.
    3. Per-blob features with ``bincount``. ``x``/``y`` is the
       intensity-weighted centroid in full-frame px (``offset`` added). Also
       ``area`` (px >= T), ``peak``, ``n_sat`` (px == 255) and ``mean``.
       ``len``/``wid`` are the major/minor extents sqrt(12·lambda) of the
       intensity-weighted second moments, including the pixel's own 1/12.
       ``elong`` = len/wid, ``angle`` is the major-axis orientation in degrees
       [0, 180) in image coordinates (y down), ``streak`` = max(0, len - wid),
       and ``bbox`` = [x0, y0, x1, y1] inclusive.

    Area gates: ``min_area`` <= area (<= ``max_area`` when > 0). No
    circularity filter: streaks are expected (§1.6). When more than
    ``max_blobs`` survive, the most saturated and largest are kept and
    ``stats['overflow']`` is set (visible-light footage: thousands)."""
    if gray.ndim == 3:
        gray = gray[:, :, 0]
    _, mask = cv2.threshold(gray, int(T) - 1, 255, cv2.THRESH_BINARY)
    n_px = int(cv2.countNonZero(mask))
    if stats is not None:
        stats["n_px"] = n_px
        stats["overflow"] = False
    if n_px == 0:
        return []
    pts = cv2.findNonZero(mask).reshape(-1, 2)
    xs = pts[:, 0].astype(np.int64)
    ys = pts[:, 1].astype(np.int64)
    vals = gray[ys, xs].astype(np.float64)
    cxs, cys = xs // cell, ys // cell
    cimg = np.zeros((gray.shape[0] // cell + 1, gray.shape[1] // cell + 1), np.uint8)
    cimg[cys, cxs] = 1
    nl, lab = cv2.connectedComponents(cimg, connectivity=8)
    lbl = lab[cys, cxs]
    n = int(nl)
    area = np.bincount(lbl, minlength=n)
    sw = np.bincount(lbl, vals, n)
    sx = np.bincount(lbl, vals * xs, n)
    sy = np.bincount(lbl, vals * ys, n)
    sxx = np.bincount(lbl, vals * xs * xs, n)
    syy = np.bincount(lbl, vals * ys * ys, n)
    sxy = np.bincount(lbl, vals * xs * ys, n)
    nsat = np.bincount(lbl, (vals >= 255).astype(np.float64), n)
    peak = np.zeros(n)
    np.maximum.at(peak, lbl, vals)
    x0 = np.full(n, np.iinfo(np.int64).max); y0 = x0.copy()
    x1 = np.full(n, -1, np.int64); y1 = x1.copy()
    np.minimum.at(x0, lbl, xs); np.minimum.at(y0, lbl, ys)
    np.maximum.at(x1, lbl, xs); np.maximum.at(y1, lbl, ys)
    ox, oy = float(offset[0]), float(offset[1])
    keep = np.nonzero((area >= max(1, min_area)) & ((area <= max_area) if max_area > 0 else True))[0]
    keep = keep[keep > 0]
    if len(keep) > max_blobs:
        order = np.lexsort((-area[keep], -nsat[keep]))
        keep = keep[order[:max_blobs]]
        if stats is not None:
            stats["overflow"] = True
    out = []
    for i in keep:
        w = sw[i]
        mx, my = sx[i] / w, sy[i] / w
        a = max(0.0, sxx[i] / w - mx * mx) + 1.0 / 12
        c = max(0.0, syy[i] / w - my * my) + 1.0 / 12
        b = sxy[i] / w - mx * my
        tr2, det = (a + c) / 2.0, math.sqrt(((a - c) / 2.0) ** 2 + b * b)
        l1, l2 = tr2 + det, max(1.0 / 12, tr2 - det)
        length, width = math.sqrt(12.0 * l1), math.sqrt(12.0 * l2)
        angle = math.degrees(0.5 * math.atan2(2.0 * b, a - c)) % 180.0
        out.append({
            "x": round(mx + ox, 2), "y": round(my + oy, 2), "area": int(area[i]),
            "peak": int(peak[i]), "n_sat": int(nsat[i]), "mean": round(w / area[i], 1),
            "len": round(length, 2), "wid": round(width, 2),
            "elong": round(length / width, 2), "angle": round(angle, 1),
            "streak": round(max(0.0, length - width), 2),
            "bbox": [int(x0[i] + ox), int(y0[i] + oy), int(x1[i] + ox), int(y1[i] + oy)],
        })
    return out


# ---------------------------------------------------------------------------
# Glint map (§2.2 step 5) + temporal chains (§2.2 step 4)
# ---------------------------------------------------------------------------

class GlintMap:
    """Static-glint cells (``cell`` px, dilated) built on an empty stage.
    Works in full-frame coordinates. ``shape`` = (height, width)."""

    def __init__(self, shape: Tuple[int, int], cell: int = 8):
        self.cell = int(cell)
        self.shape = (int(shape[0]), int(shape[1]))
        self.cells = np.zeros((self.shape[0] // self.cell + 1, self.shape[1] // self.cell + 1), bool)
        self.frames = 0
        self.T: Optional[int] = None
        self._full: Optional[np.ndarray] = None

    def add(self, blobs: Iterable[dict]) -> None:
        c = self.cell
        for b in blobs:
            bx0, by0, bx1, by1 = b["bbox"]
            self.cells[max(0, by0 // c):by1 // c + 1, max(0, bx0 // c):bx1 // c + 1] = True
        self.frames += 1
        self._full = None

    def finalize(self, dilate: int = 1) -> "GlintMap":
        if dilate > 0 and self.cells.any():
            k = np.ones((2 * dilate + 1, 2 * dilate + 1), np.uint8)
            self.cells = cv2.dilate(self.cells.astype(np.uint8), k) > 0
        self._full = None
        return self

    def contains(self, x: float, y: float) -> bool:
        cy, cx = int(y) // self.cell, int(x) // self.cell
        if 0 <= cy < self.cells.shape[0] and 0 <= cx < self.cells.shape[1]:
            return bool(self.cells[cy, cx])
        return False

    def flag(self, blobs: Iterable[dict]) -> None:
        for b in blobs:
            b["static"] = self.contains(b["x"], b["y"])

    def full_mask(self) -> np.ndarray:
        """Pixel-resolution mask (cached), full-frame shape."""
        if self._full is None:
            m = np.repeat(np.repeat(self.cells, self.cell, 0), self.cell, 1)
            self._full = m[:self.shape[0], :self.shape[1]]
        return self._full

    def free_mask(self, shape: Tuple[int, int], offset: Sequence[int] = (0, 0)) -> Optional[np.ndarray]:
        """uint8 mask (255 = outside the map) for an ROI crop of ``shape`` at
        ``offset`` (cached; for cv2 masked ops). None if it does not fit."""
        key = (tuple(shape), int(offset[0]), int(offset[1]))
        if getattr(self, "_free_key", None) != key or self._full is None:
            ox, oy = key[1], key[2]
            m = self.full_mask()[oy:oy + shape[0], ox:ox + shape[1]]
            if m.shape != tuple(shape):
                return None
            self._free = np.where(m, 0, 255).astype(np.uint8)
            self._free_key = key
        return self._free

    @property
    def n_cells(self) -> int:
        return int(self.cells.sum())

    def save(self, path) -> None:
        np.savez_compressed(path, cells=self.cells, cell=self.cell, shape=np.array(self.shape),
                            frames=self.frames, T=-1 if self.T is None else self.T)

    @classmethod
    def load(cls, path) -> "GlintMap":
        d = np.load(path)
        g = cls(tuple(int(v) for v in d["shape"]), int(d["cell"]))
        g.cells = d["cells"].astype(bool)
        g.frames = int(d["frames"])
        g.T = int(d["T"]) if int(d["T"]) >= 0 else None
        return g


class ChainLinker:
    """Greedy nearest-neighbour chain linking (globally sorted pairs) with a
    gate of ``link_px`` per processed frame.

    Each blob gets ``chain`` (id), ``age`` (hits so far) and ``persistent``,
    which means the chain was seen in >= ``persist_k`` of the last
    ``persist_n`` processed frames (§2.2 step 4: a new chain needs 2 of 3
    frames before it can drive anything alone). Chains keep running
    summaries, not point lists, so a 3-min take stays cheap."""

    def __init__(self, link_px: float = 40.0, max_gap: int = 2,
                 persist_k: int = 2, persist_n: int = 3):
        self.link_px = float(link_px)
        self.max_gap = int(max_gap)
        self.persist_k = int(persist_k)
        self.persist_n = int(persist_n)
        self.active: List[dict] = []
        self.done: List[dict] = []
        self._next = 0

    def _new(self, fidx: int, b: dict) -> dict:
        ch = {"id": self._next, "first_f": fidx, "last_f": fidx, "n": 1,
              "last": (b["x"], b["y"]), "min": [b["x"], b["y"]], "max": [b["x"], b["y"]],
              "sum": [b["x"], b["y"]], "peak": b["peak"], "area_max": b["area"],
              "hits": deque([fidx], maxlen=max(1, self.persist_n))}
        self._next += 1
        return ch

    def step(self, fidx: int, blobs: List[dict]) -> None:
        used_b, used_c = set(), set()
        if self.active and blobs:
            last = np.array([c["last"] for c in self.active])
            pos = np.array([(b["x"], b["y"]) for b in blobs])
            d = np.linalg.norm(last[:, None, :] - pos[None, :, :], axis=2)
            ci, bi = np.nonzero(d <= self.link_px)
            for k in np.argsort(d[ci, bi], kind="stable"):
                c, b = int(ci[k]), int(bi[k])
                if c in used_c or b in used_b:
                    continue
                used_c.add(c); used_b.add(b)
                ch, bl = self.active[c], blobs[b]
                ch["last"] = (bl["x"], bl["y"]); ch["last_f"] = fidx; ch["n"] += 1
                ch["min"] = [min(ch["min"][0], bl["x"]), min(ch["min"][1], bl["y"])]
                ch["max"] = [max(ch["max"][0], bl["x"]), max(ch["max"][1], bl["y"])]
                ch["sum"] = [ch["sum"][0] + bl["x"], ch["sum"][1] + bl["y"]]
                ch["peak"] = max(ch["peak"], bl["peak"]); ch["area_max"] = max(ch["area_max"], bl["area"])
                ch["hits"].append(fidx)
                bl["chain"] = ch["id"]
        still = []
        for c, ch in enumerate(self.active):
            if c not in used_c and fidx - ch["last_f"] > self.max_gap:
                self.done.append(ch)
            else:
                still.append(ch)
        self.active = still
        for j, b in enumerate(blobs):
            if j not in used_b:
                ch = self._new(fidx, b)
                self.active.append(ch)
                b["chain"] = ch["id"]
        by_id = {ch["id"]: ch for ch in self.active}
        for b in blobs:
            ch = by_id[b["chain"]]
            b["age"] = ch["n"]
            recent = sum(1 for f in ch["hits"] if f > fidx - self.persist_n)
            b["persistent"] = recent >= self.persist_k

    def finish(self) -> List[dict]:
        self.done.extend(self.active)
        self.active = []
        for ch in self.done:
            ch["span"] = float(math.hypot(ch["max"][0] - ch["min"][0], ch["max"][1] - ch["min"][1]))
            ch["mean"] = [ch["sum"][0] / ch["n"], ch["sum"][1] / ch["n"]]
            ch.pop("hits", None)
        return self.done


def classify_chains(chains: List[dict], static_px: float = 4.0,
                    static_min_frames: int = 10) -> Tuple[List[dict], List[dict], List[dict]]:
    """(fixed, moving, short): a fixed glint lives >= ``static_min_frames``
    and never moves more than ``static_px``. A moving chain has >= 3 hits,
    and the rest are short (one- or two-frame flashes)."""
    fixed, moving, short = [], [], []
    for ch in chains:
        if ch["n"] >= static_min_frames and ch.get("span", 0.0) <= static_px:
            fixed.append(ch)
        elif ch["n"] >= 3:
            moving.append(ch)
        else:
            short.append(ch)
    return fixed, moving, short


def recommend_threshold(max_natural: float, lo: int = 120, hi: int = 240) -> int:
    """§2.3: ``T_marker = clamp(max(120, 2·max_natural), <= 240)``."""
    return int(min(hi, max(lo, 2 * int(math.ceil(max_natural)))))


# ---------------------------------------------------------------------------
# Tracks + Tier-A association (§3.1)
# ---------------------------------------------------------------------------

def norm_track(t) -> dict:
    """A pose-dump row, a dict or a pipeline ``ScaledTrack`` -> the harness's
    track dict: id, kpts (17,2), conf (17,), bbox [x,y,w,h], fss, H."""
    g = (lambda k, d=None: t.get(k, d)) if isinstance(t, dict) else (lambda k, d=None: getattr(t, k, d))
    tid = g("id", g("track_id"))
    fss = g("fss", g("frames_since_skeleton"))
    bbox = np.asarray(g("bbox"), np.float64).reshape(-1)
    return {"id": int(tid), "kpts": np.asarray(g("kpts", g("keypoints")), np.float64).reshape(-1, 2),
            "conf": np.asarray(g("conf", g("confidence")), np.float64).reshape(-1),
            "bbox": bbox, "fss": int(fss) if fss is not None else -1, "H": float(bbox[3])}


@dataclass
class Assoc:
    """Tier-A result for one frame. ``rows`` lists (track index, slot, point,
    gate px, kpt conf) for every predicted slot. ``pairs`` lists (row,
    blob index, distance px, contested). ``owner[b]`` is the track index of
    blob b, or -1. ``within[b]`` is the set of track indices whose slot gate
    contains blob b."""
    rows: List[Tuple[int, int, np.ndarray, float, float]]
    pairs: List[Tuple[int, int, float, bool]]
    owner: np.ndarray
    contested: np.ndarray
    within: List[set]
    cost: np.ndarray

    def slot_blob(self) -> Dict[Tuple[int, int], Tuple[int, float, bool]]:
        return {(self.rows[r][0], self.rows[r][1]): (b, d, c) for r, b, d, c in self.pairs}


def associate(tracks: List[dict], blobs: List[dict], *, g: float = 0.20,
              min_gate_px: float = 4.0, kpt_conf_min: float = 0.1,
              harness: bool = False, contested_ratio: float = 2.0,
              H_of: Optional[Callable[[dict], float]] = None,
              skeleton_only: bool = True) -> Assoc:
    """One global Hungarian over (tracks x slots) x blobs with cost
    ``dist / gate``, where ``gate = max(min_gate_px, g·H) + 0.5·streak``.

    Keypoints are used down to ``kpt_conf_min`` (0.1): the marker is the
    arbiter. Only skeleton-fed tracks (fss == 0) predict slots: on a gap frame
    the keypoints are stale. A blob assigned to one track is **contested**
    when another track has a slot whose gate also contains it at a cost below
    ``contested_ratio`` x the assigned cost (§3.1: it counts as presence for
    both and is used for learning by neither)."""
    rows = []
    for ti, t in enumerate(tracks):
        if skeleton_only and t["fss"] != 0:
            continue
        pts, _ = mm.extremity_points(t["kpts"], t["conf"], harness=harness, conf_min=0.0)
        conf = t["conf"][list(mm.EXT_KPTS)]
        if harness:
            conf = np.append(conf, t["conf"][list(mm.HIP_KPTS)].min())
        H = H_of(t) if H_of else t["H"]
        gate0 = max(min_gate_px, g * H)
        for s in range(len(pts)):
            if conf[s] >= kpt_conf_min:
                rows.append((ti, s, pts[s], gate0, float(conf[s])))
    nb = len(blobs)
    owner = np.full(nb, -1, int)
    contested = np.zeros(nb, bool)
    within: List[set] = [set() for _ in range(nb)]
    if not rows or not nb:
        return Assoc(rows, [], owner, contested, within, np.zeros((len(rows), nb)))
    P = np.array([r[2] for r in rows])
    B = np.array([(b["x"], b["y"]) for b in blobs])
    gate = np.array([r[3] for r in rows])[:, None] + 0.5 * np.array([b.get("streak", 0.0) for b in blobs])[None, :]
    d = np.linalg.norm(P[:, None, :] - B[None, :, :], axis=2)
    cost = d / gate
    feas = cost <= 1.0
    for r, b in zip(*np.nonzero(feas)):
        within[b].add(rows[r][0])
    ri, bi = mm.assign(np.where(feas, cost, np.inf))
    pairs = []
    row_track = np.array([r[0] for r in rows])
    for r, b in zip(ri, bi):
        own = cost[r, b]
        others = feas[:, b] & (row_track != rows[r][0])
        is_cont = bool(others.any() and cost[others, b].min() < contested_ratio * max(own, 0.05))
        owner[b] = rows[r][0]
        contested[b] = is_cont
        pairs.append((int(r), int(b), float(d[r, b]), is_cont))
    return Assoc(rows, pairs, owner, contested, within, cost)


ZONE_ASSOC, ZONE_ON_SKELETON, ZONE_ON_GAP_TRACK, ZONE_FREE = "assoc", "on_skeleton", "on_gap_track", "free"


def blob_zones(tracks: List[dict], blobs: List[dict], A: Assoc, *, margin_h: float = 0.2,
               H_of: Optional[Callable[[dict], float]] = None) -> List[str]:
    """Where each blob sits: ``assoc`` (matched to a slot, contested
    included), ``on_skeleton`` (inside a skeleton-fed dancer's bbox + margin
    but not a slot: a buckle or glint on the costume, M2), ``on_gap_track``
    (inside a coasting track's bbox: a Tier-B candidate, neither hit nor FP),
    or ``free`` (away from every reported dancer: a stage false positive)."""
    out = []
    boxes = []
    for t in tracks:
        x, y, w, h = t["bbox"]
        m = margin_h * (H_of(t) if H_of else t["H"])
        boxes.append((x - m, y - m, x + w + m, y + h + m, t["fss"] == 0))
    for bi, b in enumerate(blobs):
        if A.owner[bi] >= 0:
            out.append(ZONE_ASSOC)
            continue
        zone = ZONE_FREE
        for x0, y0, x1, y1, skel in boxes:
            if x0 <= b["x"] <= x1 and y0 <= b["y"] <= y1:
                if skel:
                    zone = ZONE_ON_SKELETON
                    break
                zone = ZONE_ON_GAP_TRACK
        out.append(zone)
    return out


# ---------------------------------------------------------------------------
# Accumulators
# ---------------------------------------------------------------------------

class FloorStats:
    """Empty-stage stats (M2, M3 floor side): brightness tail per frame, the
    natural maximum outside the glint map, and moving/persistent
    false-positive candidates per frame."""

    def __init__(self):
        self.tail = {"max": [], "p999": [], "p9999": [], "n255": [], "mean": []}
        self.max_natural: List[int] = []

    def add_tail(self, gray: np.ndarray) -> None:
        """Brightness tail from a 256-bin histogram (~1 ms on 2 MP, vs ~15 ms
        for np.partition): max, p99.9, p99.99, #255 and mean."""
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
        n = hist.sum()
        cum = np.cumsum(hist)
        nz = np.nonzero(hist)[0]
        self.tail["max"].append(int(nz[-1]) if len(nz) else 0)
        self.tail["mean"].append(float((hist * np.arange(256)).sum() / max(1.0, n)))
        self.tail["n255"].append(int(hist[255]))
        # the value at sorted index floor(q·n) (same convention as np.partition)
        self.tail["p999"].append(int(np.searchsorted(cum, int(n * 0.999) + 1)))
        self.tail["p9999"].append(int(np.searchsorted(cum, int(n * 0.9999) + 1)))

    def add_natural(self, gray: np.ndarray, glint: Optional[GlintMap],
                    offset: Sequence[int] = (0, 0)) -> None:
        if glint is None or not glint.n_cells:
            self.max_natural.append(int(gray.max()))
            return
        free = glint.free_mask(gray.shape, offset)
        if free is None:
            self.max_natural.append(int(gray.max()))
            return
        _mn, mx, _l0, _l1 = cv2.minMaxLoc(gray, mask=free)
        self.max_natural.append(int(mx))

    def summary(self, T: int) -> dict:
        out = {}
        if self.tail["max"]:
            t = self.tail
            out["tail"] = {
                "max_median": int(np.median(t["max"])), "max_max": int(np.max(t["max"])),
                "p999_median": int(np.median(t["p999"])), "p9999_median": int(np.median(t["p9999"])),
                "p9999_max": int(np.max(t["p9999"])),
                "frames_with_255_pct": rnd(100 * float(np.mean(np.array(t["n255"]) > 0)), 2),
                "mean_luma_median": rnd(float(np.median(t["mean"])), 2)}
        if self.max_natural:
            mn = np.array(self.max_natural)
            out["max_natural"] = {"max": int(mn.max()), "p99": int(np.percentile(mn, 99)),
                                  "median": int(np.median(mn)), "frames": len(mn)}
            out["T_recommended"] = recommend_threshold(float(np.percentile(mn, 99.9)))
            out["max_natural_over_T"] = rnd(float(mn.max()) / T, 3)
        return out


def count_fp(blobs: List[dict]) -> Tuple[int, int]:
    """(non-static blobs, non-static persistent blobs) in one frame. On an
    empty stage every one of them is a false positive (M2)."""
    ns = [b for b in blobs if not b.get("static")]
    return len(ns), sum(1 for b in ns if b.get("persistent"))


def fp_summary(frames: Sequence[int], fp_raw: Sequence[int], fp_persist: Sequence[int],
               time_bin: int = 200) -> dict:
    """M2 on the empty stage: FP/frame (persistent = the gate's definition,
    raw = before the 2-of-3 persistence) + a time series (``time_bin``
    frames per bin) to tell the empty part from the crew walk-through."""
    if not len(frames):
        return {}
    fr, fp = np.asarray(fp_raw, np.float64), np.asarray(fp_persist, np.float64)
    bins = [[int(frames[i]), rnd(float(fp[i:i + time_bin].mean()), 4)] for i in range(0, len(fp), time_bin)]
    return {"fp_per_frame": rnd(float(fp.mean()), 4), "fp_per_frame_raw": rnd(float(fr.mean()), 4),
            "frames_with_fp_pct": rnd(100 * float(np.mean(fp > 0)), 2), "fp_per_frame_by_time": bins}


class PoseMetrics:
    """Accumulates M1/M2b/M3/M4/M5/M7 and the M6 study data over frames that
    have poses. Slot records cover every unoccluded predicted marker: a
    skeleton track (fss == 0) whose keypoint conf >= ``unocc_conf`` lies
    inside the analysed region."""

    def __init__(self, *, harness: bool = False, unocc_conf: float = 0.5,
                 fast_h: float = 0.2, body_margin_h: float = 0.2,
                 gate_h: float = 0.20, min_gate_px: float = 4.0,
                 region: Optional[Tuple[int, int, int, int]] = None,
                 exposure_of: Optional[Callable[[int], Optional[float]]] = None):
        self.S = N_EXT + (1 if harness else 0)
        self.gate_h = gate_h
        self.min_gate_px = min_gate_px
        self.harness = harness
        self.unocc_conf = unocc_conf
        self.fast_h = fast_h
        self.body_margin_h = body_margin_h
        self.region = region            # x0, y0, x1, y1 (exclusive) analysed by the detector
        self.exposure_of = exposure_of
        self.rec = defaultdict(list)    # slot-record columns
        self._last_kpt: Dict[Tuple[int, int], Tuple[int, np.ndarray]] = {}
        self._H: Dict[int, deque] = defaultdict(lambda: deque(maxlen=50))
        self.vis_seq: Dict[int, List[Tuple[int, int]]] = defaultdict(list)   # M5
        self.body = {"assoc": 0, "extra": 0, "free": 0, "free_persistent": 0}  # M2b + off-body FPs
        self.zones: List[str] = []
        self.duo = {"frames": 0, "assoc": 0, "contested": 0, "wrong": 0}        # M7
        self.study: Dict[int, Dict[int, dict]] = defaultdict(dict)              # M6
        self.residual_h: List[float] = []
        self.frames = 0

    def observe_heights(self, tracks: List[dict]) -> None:
        """Feed this frame's skeleton bbox heights. Call it BEFORE ``associate``
        so the association gate and the M1 gate see the same H."""
        for t in tracks:
            if t["fss"] == 0:
                self._H[t["id"]].append(t["H"])

    def H_of(self, t: dict) -> float:
        """Running median of the track's skeleton-frame bbox heights."""
        q = self._H[t["id"]]
        return float(np.median(q)) if q else max(20.0, t["H"])

    def _inside(self, p: np.ndarray, margin: float = 2.0) -> bool:
        if self.region is None:
            return True
        x0, y0, x1, y1 = self.region
        return x0 + margin <= p[0] < x1 - margin and y0 + margin <= p[1] < y1 - margin

    def add_frame(self, f: int, tracks: List[dict], blobs: List[dict], A: Assoc) -> None:
        self.frames += 1
        sb = A.slot_blob()
        expo = self.exposure_of(f) if self.exposure_of else None
        skel = [ti for ti, t in enumerate(tracks) if t["fss"] == 0]
        B = np.array([(b["x"], b["y"]) for b in blobs]) if blobs else np.zeros((0, 2))
        streak = np.array([b.get("streak", 0.0) for b in blobs]) if blobs else np.zeros(0)
        # -- M1 / M3 / M4 slot records -------------------------------------
        for ti in skel:
            t = tracks[ti]
            H = self.H_of(t)
            pts, _ = mm.extremity_points(t["kpts"], t["conf"], harness=self.harness, conf_min=0.0)
            conf = t["conf"][list(mm.EXT_KPTS)]
            if self.harness:
                conf = np.append(conf, t["conf"][list(mm.HIP_KPTS)].min())
            gate0 = max(self.min_gate_px, self.gate_h * H)
            for s in range(self.S):
                key = (t["id"], s)
                prev = self._last_kpt.get(key)
                speed = None
                if prev is not None and f > prev[0]:
                    speed = float(np.linalg.norm(pts[s] - prev[1])) / (f - prev[0]) / H
                if conf[s] > mm.KPT_CONF:
                    self._last_kpt[key] = (f, pts[s].copy())
                if conf[s] < self.unocc_conf or not self._inside(pts[s]):
                    continue
                if len(B):
                    d = np.linalg.norm(B - pts[s][None], axis=1)
                    near = d <= (gate0 + 0.5 * streak)
                    hit = bool(near.any())
                    dmin = float(d.min())
                else:
                    hit, dmin = False, None
                got = sb.get((ti, s))
                r = self.rec
                r["f"].append(f); r["tid"].append(t["id"]); r["slot"].append(s)
                r["H"].append(H); r["conf"].append(float(conf[s])); r["speed"].append(speed)
                r["hit"].append(hit); r["dmin_h"].append(None if dmin is None else dmin / H)
                r["assigned"].append(got is not None)
                r["expo"].append(expo)
                if got is not None:
                    b = blobs[got[0]]
                    r["peak"].append(b["peak"]); r["n_sat"].append(b["n_sat"])
                    r["area"].append(b["area"]); r["streak"].append(b.get("streak", 0.0))
                    r["contested"].append(got[2])
                    self.residual_h.append(got[1] / H)
                else:
                    for k in ("peak", "n_sat", "area", "streak", "contested"):
                        r[k].append(None)
        # -- M2b: blobs on a dancer that are not markers ---------------------
        zones = blob_zones(tracks, blobs, A, margin_h=self.body_margin_h, H_of=self.H_of)
        self.zones = zones
        for bi, b in enumerate(blobs):
            if b.get("static"):
                continue
            if zones[bi] == ZONE_ASSOC:
                self.body["assoc"] += 1
            elif zones[bi] == ZONE_ON_SKELETON:
                self.body["extra"] += 1
            elif zones[bi] == ZONE_FREE:
                self.body["free"] += 1
                self.body["free_persistent"] += int(bool(b.get("persistent")))
        # -- M5: markers visible per dancer-frame ----------------------------
        for ti, t in enumerate(tracks):
            if t["fss"] == 0:
                n = sum(1 for bi in range(len(blobs))
                        if A.owner[bi] == ti or (A.contested[bi] and ti in A.within[bi]))
            else:
                x, y, w, h = t["bbox"]
                m = self.body_margin_h * self.H_of(t)
                n = sum(1 for bi, b in enumerate(blobs) if not b.get("static") and A.owner[bi] < 0
                        and x - m <= b["x"] <= x + w + m and y - m <= b["y"] <= y + h + m)
            self.vis_seq[t["id"]].append((f, min(n, self.S)))
        # -- M7: duo frames ---------------------------------------------------
        if len(skel) >= 2:
            self.duo["frames"] += 1
            ext = []
            for ti in skel:
                pts, _ = mm.extremity_points(tracks[ti]["kpts"], tracks[ti]["conf"],
                                             harness=self.harness, conf_min=0.0)
                for s in range(len(pts)):
                    ext.append((ti, pts[s]))
            E = np.array([p for _, p in ext])
            for r, bi, _d, cont in A.pairs:
                self.duo["assoc"] += 1
                if cont:
                    self.duo["contested"] += 1
                    continue
                nearest = int(np.argmin(np.linalg.norm(E - B[bi][None], axis=1)))
                if ext[nearest][0] != A.rows[r][0]:
                    self.duo["wrong"] += 1
        # -- M6 data: labelled real-blob positions per skeleton frame --------
        for ti in skel:
            t = tracks[ti]
            C = mm.track_centroid(t["kpts"], t["conf"])
            if C is None:
                continue
            M = np.full((self.S, 2), np.nan)
            vis = np.zeros(self.S, bool)
            for s in range(self.S):
                got = sb.get((ti, s))
                if got is not None and not got[2]:
                    M[s] = B[got[0]]
                    vis[s] = True
            fr = {"C": C, "M": M[:N_EXT], "vis": vis[:N_EXT], "h": t["H"]}
            if self.harness:
                fr["hip"], fr["hip_ok"] = M[N_EXT], bool(vis[N_EXT])
            self.study[t["id"]][f] = fr

    # -- summaries ----------------------------------------------------------
    def m1(self) -> dict:
        r = self.rec
        n = len(r["f"])
        if not n:
            return {"N": 0}
        hit = np.array(r["hit"])
        H = np.array(r["H"])
        out = {"N": n, "recall": rnd(hit.mean(), 4),
               "recall_assigned": rnd(np.mean(r["assigned"]), 4)}
        q1, q2 = np.percentile(H, [100 / 3, 200 / 3])
        bins = {"far": H <= q1, "mid": (H > q1) & (H <= q2), "near": H > q2}
        out["by_distance"] = {k: {"N": int(m.sum()), "recall": rnd(hit[m].mean(), 4) if m.any() else None,
                                  "H_px": [rnd(H[m].min(), 0), rnd(H[m].max(), 0)] if m.any() else None}
                              for k, m in bins.items()}
        sp = np.array([np.nan if v is None else v for v in r["speed"]], np.float64)
        fast = sp >= self.fast_h
        out["fast"] = {"threshold_H_per_frame": self.fast_h, "N": int(fast.sum()),
                       "recall": rnd(hit[fast].mean(), 4) if fast.any() else None}
        slots = np.array(r["slot"])
        names = list(mm.SLOT_NAMES) + ([mm.HARNESS_SLOT] if self.harness else [])
        out["by_slot"] = {names[s]: rnd(hit[slots == s].mean(), 4) if (slots == s).any() else None
                          for s in range(self.S)}
        return out

    def m3_m4(self) -> dict:
        r = self.rec
        idx = [i for i, a in enumerate(r["assigned"]) if a]
        if not idx:
            return {"N": 0}
        peak = np.array([r["peak"][i] for i in idx]); nsat = np.array([r["n_sat"][i] for i in idx])
        area = np.array([r["area"][i] for i in idx]); H = np.array([r["H"][i] for i in idx])
        streak = np.array([r["streak"][i] for i in idx])
        sat = (peak >= 255) & (nsat >= 2)
        out = {"N": len(idx), "saturated_pct": rnd(100 * sat.mean(), 2),
               "peak_p10_p50": [pct(peak, 10, 0), pct(peak, 50, 0)],
               "streak_px_p50_p90": [pct(streak, 50, 1), pct(streak, 90, 1)]}
        expo = [r["expo"][i] for i in idx]
        if any(e is not None for e in expo):
            by = defaultdict(list)
            for e, s in zip(expo, sat):
                by[None if e is None else int(round(e / 100.0) * 100)].append(bool(s))
            out["saturated_pct_by_exposure_us"] = {str(k): [rnd(100 * np.mean(v), 1), len(v)]
                                                   for k, v in sorted(by.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))}
        edges = [0, 150, 250, 400, 100000]
        table = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = (H >= lo) & (H < hi)
            if m.any():
                table.append({"H_px": [lo, hi if hi < 100000 else None], "N": int(m.sum()),
                              "area_p10_p50_p90": [pct(area[m], 10, 1), pct(area[m], 50, 1), pct(area[m], 90, 1)],
                              "area_ge4_pct": rnd(100 * np.mean(area[m] >= 4), 1),
                              "saturated_pct": rnd(100 * np.mean(sat[m]), 1)})
        out["m4_area_by_H"] = table
        out["residual_H_p50_p90_p95"] = [pct(self.residual_h, 50), pct(self.residual_h, 90),
                                          pct(self.residual_h, 95)]
        if self.residual_h:
            out["proposed_gate_H"] = rnd(max(0.05, float(np.percentile(self.residual_h, 95)) * 1.25), 3)
        return out

    def m2b(self) -> dict:
        a, e = self.body["assoc"], self.body["extra"]
        return {"assoc_blobs": a, "extra_blobs": e,
                "extra_pct_of_marker_blobs": rnd(100.0 * e / a, 2) if a else None,
                "free_per_frame": rnd(self.body["free"] / max(1, self.frames), 4),
                "free_persistent_per_frame": rnd(self.body["free_persistent"] / max(1, self.frames), 4)}

    def m5(self, min_frames: int = 10) -> dict:
        tot = ge1 = ge2 = 0
        runs: List[int] = []
        for tid, seq in self.vis_seq.items():
            if len(seq) < min_frames:
                continue
            run, last_f = 0, None
            for f, n in seq:
                tot += 1
                ge1 += n >= 1
                ge2 += n >= 2
                if last_f is not None and f != last_f + 1 and run:
                    runs.append(run); run = 0
                if n == 0:
                    run += 1
                elif run:
                    runs.append(run); run = 0
                last_f = f
            if run:
                runs.append(run)
        if not tot:
            return {"N": 0}
        hist = np.bincount([min(n, self.S) for s in self.vis_seq.values() if len(s) >= min_frames
                            for _, n in s], minlength=self.S + 1)
        return {"N": tot, "ge1_pct": rnd(100 * ge1 / tot, 2), "ge2_pct": rnd(100 * ge2 / tot, 2),
                "n_visible_hist": hist.tolist(),
                "zero_runs": {"n": len(runs), "p50": pct(runs, 50, 1), "p90": pct(runs, 90, 1),
                              "max": max(runs) if runs else 0}}

    def m7(self) -> dict:
        d = self.duo
        if not d["frames"]:
            return {"duo_frames": 0}
        return {"duo_frames": d["frames"], "assoc": d["assoc"], "contested": d["contested"],
                "contested_rate": rnd(d["contested"] / d["assoc"], 4) if d["assoc"] else None,
                "wrong_dancer": d["wrong"]}


# ---------------------------------------------------------------------------
# Gap study (synth_markers.py method; M6 on real blobs)
# ---------------------------------------------------------------------------

GAPS = (1, 2, 5, 10, 20, 40)
ESTIMATORS = ("hold", "cvdecay", "mean", "offset", "offset_ul", "offset_rb", "simil", "blend")


def tracks_from_rows(rows: Iterable[dict], harness_from_hips: bool = True) -> Dict[int, Dict[int, dict]]:
    """Pose-dump rows -> the study's per-track frames, with markers simulated
    AT the YOLO keypoints (the 02 §3.0 synthetic study). Only skeleton frames
    (fss == 0) with a centroid are kept."""
    by: Dict[int, Dict[int, dict]] = defaultdict(dict)
    for r in rows:
        if r["fss"] != 0:
            continue
        k = np.asarray(r["kpts"], np.float64); c = np.asarray(r["conf"], np.float64)
        C = mm.track_centroid(k, c)
        if C is None:
            continue
        fr = {"C": C, "M": k[list(mm.EXT_KPTS)], "vis": c[list(mm.EXT_KPTS)] > mm.KPT_CONF,
              "h": float(r["bbox"][3])}
        if harness_from_hips:
            fr["hip"] = k[list(mm.HIP_KPTS)].mean(0)
            fr["hip_ok"] = bool((c[list(mm.HIP_KPTS)] > mm.KPT_CONF).all())
        by[int(r["id"])][int(r["f"])] = fr
    return by


def gap_study(by: Dict[int, Dict[int, dict]], *, gaps: Sequence[int] = GAPS,
              min_track_frames: int = 30, anchor_stride: int = 2,
              require_all: bool = True, gap_gate_h: float = math.inf,
              seed: int = 0) -> dict:
    """Simulate YOLO gaps on real skeleton sequences and score centroid
    estimators (errors ÷ the track's median bbox height H).

    For every anchor t0 (every ``anchor_stride``-th skeleton frame) where all
    4 markers are visible, the offsets are frozen at t0. For each gap k, the
    frames t0+1..t0+k must be contiguous skeleton frames: they give the
    unlabelled slot tracker its observations and give the truth at t0+k
    (YOLO's centroid there). ``require_all`` additionally needs all 4 markers
    every frame and enumerates every n-subset at t0+k; this is the synthetic
    study, exactly. Otherwise (real blobs) the subsets come from the slots the
    tracker actually sees at t0+k. Estimators: hold, cvdecay (0.9-decayed
    velocity), mean (no model), offset (labelled), offset_ul (unlabelled),
    offset_rb (unlabelled, median vote), simil (Umeyama), blend (n = 1:
    50/50 with cvdecay). ``hip1``/``all5`` add the harness marker when the
    frames carry ``hip``/``hip_ok``."""
    rng = np.random.default_rng(seed)
    res = defaultdict(list)
    pair_res = defaultdict(list)
    static_bias = defaultdict(list)
    kin = {"ext_speed": [], "c_speed": [], "min_pair_dist": [], "label_swaps": [0, 0]}
    for tid, fr in by.items():
        if len(fr) < min_track_frames:
            continue
        H = float(np.median([v["h"] for v in fr.values()]))
        frames = sorted(fr)
        fset = set(frames)
        for f in frames:
            v = fr[f]
            for n in range(1, N_EXT + 1):
                for S in itertools.combinations(range(N_EXT), n):
                    if v["vis"][list(S)].all():
                        static_bias[n].append(float(np.linalg.norm(v["M"][list(S)].mean(0) - v["C"]) / H))
            if v["vis"].all():
                d = [np.linalg.norm(v["M"][a] - v["M"][b]) / H for a, b in itertools.combinations(range(N_EXT), 2)]
                kin["min_pair_dist"].append(float(min(d)))
            if f - 1 in fset:
                p = fr[f - 1]
                kin["c_speed"].append(float(np.linalg.norm(v["C"] - p["C"]) / H))
                for i in np.nonzero(v["vis"] & p["vis"])[0]:
                    kin["ext_speed"].append(float(np.linalg.norm(v["M"][i] - p["M"][i]) / H))
        for t0 in frames[::anchor_stride]:
            a = fr[t0]
            if not a["vis"].all():
                continue
            vel = (a["C"] - fr[t0 - 1]["C"]) if (t0 - 1) in fset else np.zeros(2)
            for k in gaps:
                if require_all:
                    ok = all((t0 + j) in fset and fr[t0 + j]["vis"].all() for j in range(1, k + 1))
                else:
                    ok = all((t0 + j) in fset for j in range(1, k + 1))
                if not ok:
                    break
                b = fr[t0 + k]
                truth = b["C"]
                hold = a["C"]
                cvd = a["C"] + vel * sum(0.9 ** j for j in range(1, k + 1))
                trk = mm.SlotTracker(a["M"], gate=gap_gate_h * H)
                for j in range(1, k + 1):
                    cur = fr[t0 + j]
                    obs = cur["M"][cur["vis"]]
                    trk.step(obs[rng.permutation(len(obs))])
                slots = trk.pos
                seen = trk.visible if not require_all else np.ones(N_EXT, bool)
                lab_ok = b["vis"]
                swapped = int(np.sum((np.linalg.norm(slots - b["M"], axis=1) > 1e-6) & lab_ok & seen))
                kin["label_swaps"][0] += int(swapped > 0)
                kin["label_swaps"][1] += 1
                offs = mm.learn_offsets(a["C"], a["M"])
                pool = [i for i in range(N_EXT) if seen[i]]
                for n in range(1, N_EXT + 1):
                    for S in itertools.combinations(pool, n):
                        S = list(S)
                        lab = [i for i in S if lab_ok[i]]
                        ests = {"hold": hold, "cvdecay": cvd}
                        if len(lab) == n:
                            ests["mean"] = b["M"][S].mean(0)
                            ests["offset"] = mm.offset_vote(b["M"][S], offs[S])
                        ests["offset_ul"] = mm.offset_vote(slots[S], offs[S])
                        ests["offset_rb"] = mm.offset_vote(slots[S], offs[S], robust=True)
                        # similarity on the labelled positions, as the 02 §3.0 study did
                        dst = b["M"][S] if len(lab) == n else slots[S]
                        if n >= 2:
                            s_, R_, t_ = mm.similarity_fit(a["M"][S], dst)
                            ests["simil"] = mm.apply_similarity(s_, R_, t_, a["C"])
                        else:
                            ests["simil"] = a["C"] + (dst[0] - a["M"][S[0]])
                        ests["blend"] = (ests["offset_ul"] if n >= 2
                                         else mm.blend_single(ests["offset_ul"], cvd))
                        if n == N_EXT and a.get("hip_ok") and b.get("hip_ok"):
                            oh = a["C"] - a["hip"]
                            res[("offset_ul", "hip1", k)].append(float(np.linalg.norm(b["hip"] + oh - truth) / H))
                            res[("hold", "hip1", k)].append(float(np.linalg.norm(hold - truth) / H))
                            five = np.vstack([slots, b["hip"][None]])
                            ofive = np.vstack([offs, oh[None]])
                            res[("offset_ul", "all5", k)].append(
                                float(np.linalg.norm(mm.offset_vote(five, ofive) - truth) / H))
                            dst5 = np.vstack([b["M"] if lab_ok.all() else slots, b["hip"][None]])
                            s5, R5, t5 = mm.similarity_fit(np.vstack([a["M"], a["hip"][None]]), dst5)
                            res[("simil", "all5", k)].append(
                                float(np.linalg.norm(mm.apply_similarity(s5, R5, t5, a["C"]) - truth) / H))
                            res[("hold", "all5", k)].append(float(np.linalg.norm(hold - truth) / H))
                        ptype = None
                        if n == 2:
                            names = {mm.SLOT_NAMES[i] for i in S}
                            ptype = ("wrists" if names == {"LW", "RW"} else
                                     "ankles" if names == {"LA", "RA"} else "mixed")
                        for e, est in ests.items():
                            if est is None:
                                continue
                            err = float(np.linalg.norm(est - truth) / H)
                            res[(e, n, k)].append(err)
                            if ptype:
                                pair_res[(e, ptype, k)].append(err)
    return {"res": res, "pair": pair_res, "bias": static_bias, "kin": kin}


def merge_studies(studies: Sequence[dict]) -> dict:
    out = {"res": defaultdict(list), "pair": defaultdict(list), "bias": defaultdict(list),
           "kin": {"ext_speed": [], "c_speed": [], "min_pair_dist": [], "label_swaps": [0, 0]}}
    for s in studies:
        for key in ("res", "pair", "bias"):
            for k, v in s[key].items():
                out[key][k] += v
        for k in ("ext_speed", "c_speed", "min_pair_dist"):
            out["kin"][k] += s["kin"][k]
        out["kin"]["label_swaps"][0] += s["kin"]["label_swaps"][0]
        out["kin"]["label_swaps"][1] += s["kin"]["label_swaps"][1]
    return out


def summarize_study(st: dict, gaps: Sequence[int] = GAPS) -> dict:
    """The synth_markers.py JSON layout: gap_table["n{n}_k{k}"] = {est: [p50, p90], N}."""
    res, pair, bias, kin = st["res"], st["pair"], st["bias"], st["kin"]
    q = lambda v, p: pct(v, p)  # noqa: E731
    out = {"static_bias_mean_of_visible": {str(n): {"p50": q(v, 50), "p90": q(v, 90), "N": len(v)}
                                          for n, v in sorted(bias.items())},
           "kinematics_per_frame_over_H": {
               "extremity_speed_p50_p90_p99": [q(kin["ext_speed"], 50), q(kin["ext_speed"], 90), q(kin["ext_speed"], 99)],
               "centroid_speed_p50_p90_p99": [q(kin["c_speed"], 50), q(kin["c_speed"], 90), q(kin["c_speed"], 99)],
               "min_intra_dancer_marker_dist_p5_p10_p50": [q(kin["min_pair_dist"], 5), q(kin["min_pair_dist"], 10),
                                                           q(kin["min_pair_dist"], 50)],
               "unlabelled_slot_swap_rate": rnd(kin["label_swaps"][0] / max(1, kin["label_swaps"][1]), 3),
               "N_gap_samples": kin["label_swaps"][1]},
           "gap_table": {}, "pair_table": {}}
    for n in range(1, N_EXT + 1):
        for k in gaps:
            row = {e: [q(res[(e, n, k)], 50), q(res[(e, n, k)], 90)] for e in ESTIMATORS if res.get((e, n, k))}
            if row:
                row["N"] = len(res.get(("hold", n, k), []))
                out["gap_table"][f"n{n}_k{k}"] = row
    for tag in ("hip1", "all5"):
        for k in gaps:
            row = {e: [q(res[(e, tag, k)], 50), q(res[(e, tag, k)], 90)] for e in ESTIMATORS if res.get((e, tag, k))}
            if row:
                row["N"] = len(res.get(("hold", tag, k), []))
                out["gap_table"][f"{tag}_k{k}"] = row
    for pt in ("wrists", "ankles", "mixed"):
        for k in gaps:
            row = {e: [q(pair[(e, pt, k)], 50), q(pair[(e, pt, k)], 90)] for e in ESTIMATORS if pair.get((e, pt, k))}
            if row:
                out["pair_table"][f"{pt}_k{k}"] = row
    return out


def study_r_table(gap_table: dict, ks: Sequence[int] = mm.R_TABLE_KS) -> dict:
    """gap_table -> one scene of ``marker_model.R_TABLE`` (paste-ready)."""
    rows = [("hold", "n4", "hold"), ("cvdecay", "n4", "cvdecay"), (1, "n1", "offset_ul"),
            (2, "n2", "offset_ul"), (3, "n3", "offset_ul"), (4, "n4", "offset_ul"),
            ("hip", "hip1", "offset_ul"), ("4+hip", "all5", "offset_ul")]
    out = {}
    for name, pre, est in rows:
        cells = {}
        for k in ks:
            v = gap_table.get(f"{pre}_k{k}", {}).get(est)
            if v and v[0] is not None:
                cells[k] = tuple(v)
        if cells:
            out[name] = cells
    return out


def format_gap_table(gap_table: dict, ests=("hold", "cvdecay", "offset_ul", "offset_rb", "simil", "blend")) -> str:
    lines = [f"{'cell':<10}" + "".join(f"{e:>14}" for e in ests) + "       N"]
    for key, row in gap_table.items():
        lines.append(f"{key:<10}" + "".join(
            f"{('%.3f/%.3f' % tuple(row[e])) if e in row and row[e][0] is not None else '-':>14}"
            for e in ests) + f"  {row.get('N', ''):>6}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Go / no-go (§6.3)
# ---------------------------------------------------------------------------

GATES = {
    "M1": {"metric": "per-marker recall (unoccluded marker-frames with a blob within the gate)",
           "go": "recall >= 0.95 overall and in the far-distance bin; >= 0.90 on fast marker-frames",
           "nogo": "far-bin recall < 0.85"},
    "M2": {"metric": "empty-stage moving FP/frame after the glint map; non-marker blobs on a dancer",
           "go": "<= 0.01 FP/frame; <= 5 % of marker blobs", "nogo": "> 0.05 FP/frame"},
    "M3": {"metric": "associated blobs with peak 255 and n_sat >= 2; empty-stage max_natural vs T",
           "go": ">= 95 % saturated; max_natural <= 0.6 T", "nogo": "(rethink exposure/illuminator)"},
    "M4": {"metric": "blob area vs distance (H bins); smallest size with area >= 4 px far",
           "go": "documented table (INFO)", "nogo": "-"},
    "M5": {"metric": "dancer-frames with >= 1 / >= 2 markers; 0-marker run lengths",
           "go": ">= 1 visible on >= 90 % (aerial); 0-runs p50 <= 10, p90 <= 20 frames",
           "nogo": ">= 1 visible on < 75 %"},
    "M6": {"metric": "centroid error of the unlabelled offset vote vs YOLO centroid, by n and gap k",
           "go": "n >= 2, k <= 20: median <= 0.10 H and p90 <= 0.30 H; k >= 10 not worse than hold",
           "nogo": "median > 0.15 H, or worse than hold at k >= 10 on a fast take"},
    "M7": {"metric": "duo: contested-blob rate; wrong-dancer slot uses",
           "go": "0 wrong-dancer slot uses after the contested rule", "nogo": "systematic cross-assignment"},
    "M8": {"metric": "end-to-end replay markers on vs off (Phase-2 gate)", "go": "Phase 2", "nogo": "-"},
    "M9": {"metric": "visible invisibility (operator judgement)", "go": "manual", "nogo": "-"},
}


def _g(status: str, value, note: str = "", **kw) -> dict:
    d = {"status": status, "value": value}
    if note:
        d["note"] = note
    d.update(kw)
    return d


def gate_m1(m1: dict, min_n: int = 30) -> dict:
    if not m1.get("N"):
        return _g("N/A", None, "no unoccluded marker-frames (no poses?)")
    far = m1["by_distance"]["far"]
    fast = m1["fast"]
    v = {"recall": m1["recall"], "far": far["recall"], "fast": fast["recall"]}
    if far["N"] >= min_n and far["recall"] is not None and far["recall"] < 0.85:
        return _g("NO-GO", v, "far-bin recall < 0.85")
    ok = m1["recall"] >= 0.95
    if far["N"] >= min_n and far["recall"] is not None:
        ok &= far["recall"] >= 0.95
    if fast["N"] >= min_n and fast["recall"] is not None:
        ok &= fast["recall"] >= 0.90
    return _g("PASS" if ok else "FAIL", v)


def gate_m2(floor: Optional[dict], m2b: Optional[dict]) -> dict:
    v = {}
    st = []
    if floor and floor.get("fp_per_frame") is not None:
        fp = floor["fp_per_frame"]
        v["fp_per_frame"] = fp
        st.append("NO-GO" if fp > 0.05 else ("PASS" if fp <= 0.01 else "FAIL"))
    if m2b and m2b.get("extra_pct_of_marker_blobs") is not None and m2b.get("assoc_blobs", 0) >= 50:
        e = m2b["extra_pct_of_marker_blobs"]
        v["extra_pct"] = e
        st.append("PASS" if e <= 5.0 else "FAIL")
    if not st:
        return _g("N/A", None, "needs an empty-stage take (floor) or >= 50 associated blobs")
    worst = "NO-GO" if "NO-GO" in st else ("FAIL" if "FAIL" in st else "PASS")
    return _g(worst, v)


def gate_m3(m3: Optional[dict], floor: Optional[dict], T: int, min_n: int = 30) -> dict:
    v = {}
    st = []
    if m3 and m3.get("N", 0) >= min_n:
        v["saturated_pct"] = m3["saturated_pct"]
        st.append("PASS" if m3["saturated_pct"] >= 95.0 else "FAIL")
    if floor and floor.get("max_natural"):
        mn = floor["max_natural"]["max"]
        v["max_natural"] = mn
        v["T"] = T
        st.append("PASS" if mn <= 0.6 * T else "FAIL")
    if not st:
        return _g("N/A", None, "needs associated blobs or an empty-stage take")
    return _g("FAIL" if "FAIL" in st else "PASS", v)


def gate_m5(m5: dict, aerial: bool = True) -> dict:
    if not m5.get("N"):
        return _g("N/A", None, "no dancer-frames")
    v = {"ge1_pct": m5["ge1_pct"], "ge2_pct": m5["ge2_pct"],
         "zero_run_p50": m5["zero_runs"]["p50"], "zero_run_p90": m5["zero_runs"]["p90"]}
    if m5["ge1_pct"] < 75.0:
        return _g("NO-GO", v, ">= 1 visible on < 75 % -> add a harness front/back marker")
    zr = m5["zero_runs"]
    ok = m5["ge1_pct"] >= 90.0
    if zr["n"]:
        ok &= (zr["p50"] or 0) <= 10 and (zr["p90"] or 0) <= 20
    note = "" if aerial else "gate defined for the aerial take (slot 4)"
    return _g("PASS" if ok else "FAIL", v, note)


def gate_m6(summary: dict, *, fast_take: Optional[bool] = None, min_n: int = 50,
            est: str = "offset_ul") -> dict:
    gt = summary.get("gap_table", {})
    if fast_take is None:
        cs = summary.get("kinematics_per_frame_over_H", {}).get("centroid_speed_p50_p90_p99", [None])[0]
        fast_take = bool(cs is not None and cs >= 0.04)
    fails, nogos, checked = [], [], 0
    for n in (2, 3, 4):
        for k in (1, 2, 5, 10, 20):
            row = gt.get(f"n{n}_k{k}")
            if not row or row.get("N", 0) < min_n or est not in row:
                continue
            checked += 1
            p50, p90 = row[est]
            hold = row["hold"][0]
            if p50 > 0.10 or p90 > 0.30:
                fails.append(f"n{n}_k{k} {p50:.3f}/{p90:.3f}")
            if p50 > 0.15:
                nogos.append(f"n{n}_k{k} median {p50:.3f} > 0.15")
            if k >= 10 and p50 > hold:
                fails.append(f"n{n}_k{k} worse than hold {p50:.3f} > {hold:.3f}")
                if fast_take:
                    nogos.append(f"n{n}_k{k} worse than hold on a fast take")
    if not checked:
        return _g("N/A", None, f"no n>=2 cell with N >= {min_n}")
    v = {"cells": checked, "fast_take": fast_take}
    for key in ("n2_k10", "n4_k10", "n2_k20"):          # [p50, p90, hold p50, N] when counted
        if key in gt and est in gt[key] and gt[key].get("N", 0) >= min_n:
            v[key] = gt[key][est] + [gt[key]["hold"][0], gt[key]["N"]]
    if nogos:
        return _g("NO-GO", v, "; ".join(nogos[:4]))
    if fails:
        return _g("FAIL", v, "; ".join(fails[:4]))
    return _g("PASS", v)


def gate_m7(m7: dict) -> dict:
    if not m7.get("duo_frames"):
        return _g("N/A", None, "no frame with >= 2 skeleton tracks")
    v = {"wrong_dancer": m7["wrong_dancer"], "contested_rate": m7["contested_rate"]}
    return _g("PASS" if m7["wrong_dancer"] == 0 else "FAIL", v)


def format_gates(gates: dict) -> str:
    lines = []
    for k in sorted(gates, key=lambda s: int(s[1:])):
        g = gates[k]
        val = json.dumps(jsonable(g.get("value")), separators=(",", ":")) if g.get("value") is not None else ""
        note = f"  ({g['note']})" if g.get("note") else ""
        lines.append(f"  {k:<3} {g['status']:<6} {val}{note}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# .meta v2 provenance (MRK-0)
# ---------------------------------------------------------------------------

SHOOT_BRIEF_RIG = ("camera_distance_m", "illuminator_offset_cm", "illuminator", "lens",
                   "f_number", "filter", "markers")
_CAM_KEYS = {"ExposureTime": "exposure_us", "Gain": "gain_db", "ExposureAuto": "exposure_auto",
             "GainAuto": "gain_auto", "PixelFormat": "pixel_format", "Gamma": "gamma",
             "BlackLevel": "black_level", "AcquisitionFrameRate": "acq_fps",
             "DeviceTemperature": "temperature_c", "AutoFeatureExposureTimeUpperLimit": "ae_upper_limit_us",
             "DeviceModelName": "model", "DeviceSerialNumber": "serial",
             "DeviceFirmwareVersion": "firmware", "SensorName": "sensor",
             "Width": "width", "Height": "height", "OffsetX": "offset_x", "OffsetY": "offset_y"}
CONFIG_KEYS = ("roi_enabled", "roi_x", "roi_y", "roi_w", "roi_h", "roi_source_w", "roi_source_h",
               "person_height_px", "model", "yolo_imgsz", "use_tensorrt", "gamma", "clahe_clip",
               "ids_exposure_us", "ids_gain_db", "ids_ratio", "blur_budget_ms", "confidence")


def _camera(c: Optional[dict]) -> dict:
    if not isinstance(c, dict):
        return {}
    out = {}
    nodes = c.get("nodes") or {}
    for node, key in _CAM_KEYS.items():
        if node in nodes:
            out[key] = nodes[node]
    for k in ("source", "open", "measured_fps", "frame_size", "dropped_frames", "error"):
        if k in c:
            out[k] = c[k]
    app = c.get("app_settings") or {}
    for k in ("user_set", "crop_ratio", "exposure_auto", "gain_auto", "auto_exposure_limit_us"):
        if k in app:
            out["app_" + k] = app[k]
    return out


def _stats(v: List[float]) -> Optional[dict]:
    v = [float(x) for x in v if isinstance(x, (int, float))]
    if not v:
        return None
    return {"min": rnd(min(v), 2), "median": rnd(float(np.median(v)), 2), "max": rnd(max(v), 2)}


def read_camlog(path: Path) -> Optional[dict]:
    """``<take>.camlog.jsonl`` (≈1 Hz: t, frames, nodes{ExposureTime, Gain,
    ExposureAuto, GainAuto, DeviceTemperature...}) -> a summary plus the
    (frames, exposure_us, gain_db) samples used to segment a take."""
    if not path.exists():
        return None
    samples = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    expo, gain, temp, ae, ag, series = [], [], [], set(), set(), []
    for s in samples:
        n = s.get("nodes") or {}
        e, g = n.get("ExposureTime"), n.get("Gain")
        if isinstance(e, (int, float)):
            expo.append(e)
        if isinstance(g, (int, float)):
            gain.append(g)
        if isinstance(n.get("DeviceTemperature"), (int, float)):
            temp.append(n["DeviceTemperature"])
        if "ExposureAuto" in n:
            ae.add(str(n["ExposureAuto"]))
        if "GainAuto" in n:
            ag.add(str(n["GainAuto"]))
        if isinstance(s.get("frames"), (int, float)):
            series.append((int(s["frames"]), e, g))
    out = {"samples": len(samples), "exposure_us": _stats(expo), "gain_db": _stats(gain),
           "temperature_c": _stats(temp), "exposure_auto": sorted(ae), "gain_auto": sorted(ag),
           "series": series}
    if expo:
        out["exposure_varies"] = bool(max(expo) > 1.02 * min(expo))
    return out


def exposure_lookup(camlog: Optional[dict], meta_exposure: Optional[float] = None
                    ) -> Optional[Callable[[int], Optional[float]]]:
    """frame index -> exposure µs from the camlog series (step function at
    each sample's frame count). None without a varying camlog."""
    if not camlog or not camlog.get("series"):
        if meta_exposure is None:
            return None
        return lambda f: meta_exposure
    ser = sorted((f, e) for f, e, _g in camlog["series"] if isinstance(e, (int, float)))
    if not ser:
        return None
    fs = np.array([s[0] for s in ser]); es = np.array([s[1] for s in ser], np.float64)

    def lookup(f: int) -> Optional[float]:
        i = int(np.searchsorted(fs, f, side="right")) - 1
        return float(es[max(0, i)])
    return lookup


def read_take_meta(video: Path, rig_overrides: Optional[dict] = None) -> dict:
    """Parse ``<video>.meta``: v2 (MRK-0, ``meta_version: 2``), v1 (only
    ``actual_fps`` + ``frames``) or none. Always returns a dict. ``present`` /
    ``meta_version`` say what was there; ``shoot_brief.missing`` lists the
    setup-sheet facts that are absent (02 §6.1), ``shoot_brief.warnings``
    the settings that violate the shoot brief (§1.5)."""
    video = Path(video)
    mp = Path(str(video) + ".meta")
    out: dict = {"file": video.name, "present": mp.exists(), "meta_version": None}
    raw: dict = {}
    if mp.exists():
        try:
            raw = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            out["error"] = f"unreadable .meta: {e}"
            raw = {}
    if raw:
        out["meta_version"] = int(raw.get("meta_version", 1))
    for k in ("actual_fps", "frames", "container_fps", "codec", "size", "slot", "started_at",
              "stopped_at", "frames_queued", "frames_dropped", "error", "project", "profile",
              "config_file", "system_state"):
        if k in raw and raw[k] is not None:
            out[k] = raw[k]
    cam = _camera(raw.get("camera"))
    if cam:
        out["camera"] = cam
    at_stop = _camera((raw.get("at_stop") or {}).get("camera"))
    if at_stop:
        out["camera_at_stop"] = {k: at_stop[k] for k in ("exposure_us", "gain_db", "exposure_auto",
                                                         "gain_auto", "temperature_c") if k in at_stop}
    rig = dict(raw.get("rig") or {})
    if rig_overrides:
        rig.update({k: v for k, v in rig_overrides.items() if v not in (None, "")})
        out["rig_overrides"] = sorted(rig_overrides)
    if rig:
        out["rig"] = rig
    for k in ("app", "engine"):
        if isinstance(raw.get(k), dict):
            out[k] = raw[k]
    cfg = raw.get("config")
    if isinstance(cfg, dict):
        out["config"] = {k: cfg[k] for k in CONFIG_KEYS if k in cfg}
    cl_name = raw.get("camlog") or (video.name + ".camlog.jsonl")
    cl = read_camlog(video.parent / cl_name)
    if cl:
        out["camlog"] = {k: v for k, v in cl.items() if k != "series"}
    # shoot-brief completeness + warnings
    missing, warn = [], []
    if not out["present"]:
        warn.append("no .meta sidecar: fps defaults to the container's, camera state unknown")
    elif out["meta_version"] == 1:
        warn.append("old .meta (v1: actual_fps + frames only): camera state and rig unknown "
                    "-- fill the take sheet by hand (--rig KEY=VALUE)")
    if "exposure_us" not in cam:
        missing.append("camera.exposure_us")
    if "gain_db" not in cam:
        missing.append("camera.gain_db")
    for k in SHOOT_BRIEF_RIG:
        if k not in rig:
            missing.append(f"rig.{k}")
    e = cam.get("exposure_us")
    if isinstance(e, (int, float)) and e > 25000:
        warn.append(f"exposure {e / 1000:.1f} ms > 25 ms: wrists/ankles will streak (02 s1.5)")
    for key, label in (("exposure_auto", "auto-exposure"), ("gain_auto", "auto-gain")):
        v = cam.get(key)
        if v is not None and str(v).lower() not in ("off", "false", "0"):
            warn.append(f"{label} was {v} at REC: settings may drift within the take")
    if cl and cl.get("exposure_varies"):
        warn.append("exposure varied within the take (camlog); M3 is reported per exposure")
    off = rig.get("illuminator_offset_cm")
    if isinstance(off, (int, float)) and off > 10:
        warn.append(f"illuminator {off} cm off the lens axis (> 10 cm): weaker retro return")
    codec = out.get("codec")
    if codec and str(codec).upper() != "FFV1":
        warn.append(f"codec {codec}: not lossless (FFV1 preferred for marker work)")
    if out.get("frames_dropped"):
        warn.append(f"{out['frames_dropped']} frame(s) dropped by the recorder queue")
    out["shoot_brief"] = {"missing": missing, "warnings": warn}
    out["_raw_config"] = cfg if isinstance(cfg, dict) else None
    out["_camlog_series"] = cl.get("series") if cl else None
    return out


def public_meta(meta: dict) -> dict:
    """The meta dict without the private (large) keys."""
    return {k: v for k, v in meta.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Contact sheets (small: downscaled, capped, JPEG)
# ---------------------------------------------------------------------------

_LUT = np.clip(255.0 * (np.arange(256) / 255.0) ** 0.45, 0, 255).astype(np.uint8)


class TilePool:
    """Bounded pool of crops: ``top`` keeps the highest scores; ``sample`` is
    a seeded reservoir sample, which spreads the tiles over the take."""

    def __init__(self, cap: int, mode: str = "sample", seed: int = 0):
        self.cap, self.mode = int(cap), mode
        self.items: List[Tuple[float, dict]] = []
        self.seen = 0
        self.rng = random.Random(seed)

    def offer(self, score: float, make: Callable[[], dict]) -> None:
        if self.cap <= 0:
            return
        self.seen += 1
        if self.mode == "top":
            if len(self.items) < self.cap:
                self.items.append((score, make()))
            else:
                worst = min(range(len(self.items)), key=lambda i: self.items[i][0])
                if score > self.items[worst][0]:
                    self.items[worst] = (score, make())
        else:
            if len(self.items) < self.cap:
                self.items.append((score, make()))
            else:
                j = self.rng.randrange(self.seen)
                if j < self.cap:
                    self.items[j] = (score, make())

    def tiles(self) -> List[dict]:
        if self.mode == "top":
            return [t for _, t in sorted(self.items, key=lambda it: -it[0])]
        return [t for _, t in sorted(self.items, key=lambda it: it[1].get("f", 0))]


def crop(gray: np.ndarray, x: float, y: float, half: int = 24,
         offset: Sequence[int] = (0, 0)) -> Tuple[np.ndarray, Tuple[int, int]]:
    """A (2·half)^2 crop centred on full-frame (x, y), zero-padded at the
    edges. Returns the crop and its full-frame origin."""
    cx, cy = int(round(x - offset[0])), int(round(y - offset[1]))
    out = np.zeros((2 * half, 2 * half), np.uint8)
    x0, y0 = cx - half, cy - half
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(gray.shape[1], x0 + 2 * half), min(gray.shape[0], y0 + 2 * half)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = gray[sy0:sy1, sx0:sx1]
    return out, (x0 + int(offset[0]), y0 + int(offset[1]))


def render_tile(t: dict, size: int = 96) -> np.ndarray:
    """A tile dict {img, origin, marks[(x, y, kind)], top, bottom} -> BGR.
    kinds: 'kp' red cross (predicted keypoint), 'blob' green circle,
    'gate' yellow circle of radius r."""
    img = t["img"]
    s = size / img.shape[1]
    b = cv2.cvtColor(cv2.resize(_LUT[img], (size, size), interpolation=cv2.INTER_NEAREST),
                     cv2.COLOR_GRAY2BGR)
    ox, oy = t["origin"]
    for m in t.get("marks", []):
        x, y = (m[0] - ox) * s, (m[1] - oy) * s
        p = (int(round(x)), int(round(y)))
        if m[2] == "kp":
            cv2.drawMarker(b, p, (0, 0, 255), cv2.MARKER_CROSS, 9, 1)
        elif m[2] == "blob":
            cv2.circle(b, p, 6, (0, 255, 0), 1)
        elif m[2] == "gate":
            cv2.circle(b, p, max(2, int(m[3] * s)), (0, 200, 255), 1)
    if t.get("top"):
        cv2.putText(b, t["top"], (2, 10), cv2.FONT_HERSHEY_PLAIN, 0.75, (0, 255, 0), 1)
    if t.get("bottom"):
        cv2.putText(b, t["bottom"], (2, size - 3), cv2.FONT_HERSHEY_PLAIN, 0.75, (0, 255, 255), 1)
    return b


def make_sheet(tiles: List[dict], title: str, cols: int = 8, size: int = 96) -> Optional[np.ndarray]:
    if not tiles:
        return None
    imgs = [render_tile(t, size) for t in tiles]
    while len(imgs) % cols:
        imgs.append(np.zeros_like(imgs[0]))
    grid = np.vstack([np.hstack(imgs[i:i + cols]) for i in range(0, len(imgs), cols)])
    head = np.zeros((18, grid.shape[1], 3), np.uint8)
    cv2.putText(head, title[:120], (4, 13), cv2.FONT_HERSHEY_PLAIN, 0.9, (255, 255, 255), 1)
    return np.vstack([head, grid])


def write_jpeg(path: Path, img: np.ndarray, quality: int = 80, max_width: int = 1024) -> int:
    if img.shape[1] > max_width:
        s = max_width / img.shape[1]
        img = cv2.resize(img, (max_width, int(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
    if not ok:
        return 0
    Path(path).write_bytes(buf.tobytes())
    return len(buf)


def overview_image(gray: np.ndarray, offset: Sequence[int], blobs: List[dict], tracks: List[dict],
                   A: Optional[Assoc], width: int = 640, label: str = "") -> np.ndarray:
    """Downscaled frame with tracks (bbox, slot keypoints) and blobs: green =
    associated, red = contested, orange = free, grey = static glint."""
    s = width / gray.shape[1]
    img = cv2.cvtColor(cv2.resize(_LUT[gray], (width, int(gray.shape[0] * s)),
                                  interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR)
    ox, oy = offset
    P = lambda x, y: (int(round((x - ox) * s)), int(round((y - oy) * s)))  # noqa: E731
    for t in tracks:
        x, y, w, h = t["bbox"]
        col = (255, 200, 0) if t["fss"] == 0 else (120, 120, 120)
        cv2.rectangle(img, P(x, y), P(x + w, y + h), col, 1)
        if t["fss"] == 0:
            for k in mm.EXT_KPTS:
                cv2.drawMarker(img, P(*t["kpts"][k]), (0, 0, 255), cv2.MARKER_CROSS, 7, 1)
        cv2.putText(img, f"id{t['id']} fss{t['fss']}", P(x, y - 4), cv2.FONT_HERSHEY_PLAIN, 0.8, col, 1)
    for i, b in enumerate(blobs):
        if b.get("static"):
            col = (150, 150, 150)
        elif A is not None and A.owner[i] >= 0:
            col = (0, 0, 255) if A.contested[i] else (0, 255, 0)
        else:
            col = (0, 165, 255)
        cv2.circle(img, P(b["x"], b["y"]), 5, col, 1)
    if label:
        cv2.putText(img, label, (4, 14), cv2.FONT_HERSHEY_PLAIN, 1.0, (255, 255, 255), 1)
    return img


class JsonlWriter:
    """Optional per-frame JSONL (gzip) -- big on long takes, off by default."""

    def __init__(self, path: Optional[Path]):
        self.fh = gzip.open(path, "wt", encoding="utf-8") if path else None

    def write(self, rec: dict) -> None:
        if self.fh:
            self.fh.write(json.dumps(jsonable(rec), separators=(",", ":")) + "\n")

    def close(self) -> None:
        if self.fh:
            self.fh.close()
            self.fh = None
