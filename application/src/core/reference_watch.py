"""H4 (#t-063): the references are watched -- the empty-wall picture against what the camera sees now.

The foreground (core/foreground.py) compares every frame with the empty-wall picture (the plate) pixel by pixel:
it is only right while the camera sees the wall from where the plate was taken.  X6 refuses a plate from another
exposure, gain or crop, F2 latches « plate stale » when more than a quarter of the zone differs; between those
lines nothing said that the plate no longer lines up with the picture -- the camera nudged a few pixels, the
tripod knocked, another evening's plate of the same crop, a plate of another wall.  The foreground then rims every
edge of the wall, and the health line blamed « a body on the wall » for what is the plate.

``check_reference(plate, frame, roi, exclude)`` registers the plate against the live picture ON GRADIENTS, so the
light may differ:

* both pictures on the plate's grid (the frame area-averaged ``plate.ds`` times, as the plate was made), in log
  units with a floor at a tenth of each picture's own level (a gain becomes an offset, a gamma a factor), minus
  their local mean over ~6 plate px (the light's shape goes, the wall's edges stay);
* pixels at white in either picture (the IR oval), the given ``exclude`` boxes (dancers, foreground groups) and
  everything outside the zone are left out;
* a phase correlation over the zone finds the shift, even a large one; ECC (OpenCV's enhanced correlation)
  refines it to a fraction of a pixel, as a translation, then as an affine map (a rotation, a zoom); the
  correlation after alignment says whether it is the same wall at all.

It answers ``matches`` (the plate lines up: under ``MOVED_PX``), ``moved`` (the same wall, displaced:
``shift_px`` says by how much, the largest displacement over the zone), ``other_wall`` (both pictures have
structure, nothing lines up) or ``unknown`` (cannot tell: too dark, too flat, too much hidden -- never green).

``ReferenceWatch`` holds the last verdict for the app (one place, read by the readiness row and the health line),
runs the check off the frame path (a worker thread, ``submit``), counts the swipe referee's refusals
(``note_referee_refusal``) and says when the wall is provably empty and the plate should be retaken
(``prompt``).  It never retakes a plate and never touches the camera: alert first, one instruction each.

Pure numpy + OpenCV: no detector, no torch, no GUI.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

# --------------------------------------------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------------------------------------------

MATCHES, MOVED, OTHER_WALL, UNKNOWN = "matches", "moved", "other_wall", "unknown"
STATUSES = (MATCHES, MOVED, OTHER_WALL, UNKNOWN)

# Where « moved » starts (full-frame px, the largest displacement over the zone).  Measured on 08/10 with the
# 21:36 plate and F2's foreground (tmp_analysis/r2-1126/gates/h4: shift_f2.json and the gate's section D, mur2
# takes 2 and 6-8 displaced by 0.5-6 px in four directions): the smallest motion that puts more than 0.10 groups
# a frame on nobody is 3 px sideways (take 2: +0.2 to +0.375 groups a frame; 2.5 px: +0.025 to +0.05; up or
# down: none up to 6 px); the share of the zone stays under 1 % and the stale latch never trips.  The line sits
# under it by more than the estimate's error (<= 0.2 px from 2 to 10 px), and over the camera's own drift on
# 08/10, which F2 does not feel (0.9 px median at 22:00, 1.7 px at 22:20, read up to 2.2 px with its slight
# roll).  « matches » is required under half a pixel.
MOVED_PX = 2.5
AFFINE_MIN_PX = 0.5      # the affine map is kept when it moves a corner of the zone this much more than the shift
AFFINE_GAIN = 0.02       # ... and lines the zone up better than the translation by this much (correlation): on
                         # 08/10 a rotation of 1 deg gains 0.22, a zoom of 2 % 0.44; dancers bending it, <= 0.008
MOVED_TILES = 0.75       # « moved » needs this share of the zone's tiles lined up after the displacement (08/10,
                         # dancers in view: 0.93-1.0); a scene that shares only a part reads 0.40-0.53: another wall.
                         # Between OTHER_MAX and SAME_MIN too: under it another wall, at or over it unknown
CLIP_DN = 250.0           # a pixel at or above this (on the plate's grid) is at white: no edge to read there
FLOOR_SHARE = 0.10        # the log floor: this share of the picture's own level (its 75th percentile in the zone)
FLOOR_MIN_DN = 2.0        # ... never under this
LOCAL_SIGMA = 6.0         # plate px: the local mean taken off (the light's shape), ~24 full-frame px
EDGE_SIGMA = 0.7          # plate px: a light blur against noise and the frame's aliasing on the plate's grid
EXCLUDE_GROW = 2          # plate px around each excluded box
MIN_VALID = 1500          # plate px of the zone left to compare (~24,000 full-frame px), else unknown
STRUCTURE_MIN = 0.020     # the edges' strength (log units, rms over the zone) under which a picture is flat
SAME_MIN = 0.35           # correlation after alignment: at or above, the same wall
OTHER_MAX = 0.25          # ... at or under (with structure in both), another wall; between: unknown (08/10 and the
                          # corpus: other walls 0.15 at most, the same wall with people in view 0.49 at least)
ECC_ITER = 30
ECC_EPS = 1e-3          # the correlation's gain per step under which ECC stops (a few steps from the
                         # phase correlation's start)

FIX_PLATE = "STANDBY, then CALIBRATE on the empty wall (2 Calibrate), then Save to project"
FIX_MOVED = "STANDBY, check the zone and mask (1 Rig), then CALIBRATE on the empty wall, Save to project"
TODO_DARK = "check the IR flood and the picture"
TODO_AGAIN = f"if the wall is empty and lit: {FIX_PLATE}"


@dataclass
class ReferenceVerdict:
    """What the plate is to the picture now.  ``shift_px``: the largest displacement of the zone between the
    plate and the picture (full-frame px; None when unknown or another wall); ``structure``: the weaker of the
    two pictures' edge strength (log units, rms; under ``STRUCTURE_MIN`` a picture is too flat or too dark);
    ``status``: one of ``STATUSES``; ``instruction``: the fact, then « - » and the one thing to do."""
    shift_px: Optional[float]
    structure: float
    status: str
    instruction: str
    dx: Optional[float] = None          # the displacement at the zone's centre, full-frame px (+x right, +y down)
    dy: Optional[float] = None
    score: Optional[float] = None       # the correlation after alignment (-1..1)
    score_zero: Optional[float] = None  # ... before (as the foreground compares them)
    rotation_deg: float = 0.0
    scale: float = 1.0
    valid_share: float = 0.0            # the share of the zone compared (not at white, not excluded)
    model: str = ""                     # « translation » | « affine » | « » (no alignment)
    ms: float = 0.0
    at: float = 0.0                     # time.time() of the check
    plate_created: str = ""             # the plate it read (its capture time)

    @property
    def alert(self) -> bool:
        """The plate is the cause: it does not line up with the wall, is not this wall, or the swipe's referee
        refused it three times in a row (the watch's own ``refused``)."""
        return self.status in (MOVED, OTHER_WALL, "refused", "dark_plate", "blind")

    def to_dict(self) -> dict:
        r = lambda v, n=2: None if v is None else round(float(v), n)   # noqa: E731
        return {"status": self.status, "shift_px": r(self.shift_px), "dx": r(self.dx), "dy": r(self.dy),
                "structure": r(self.structure, 4), "score": r(self.score, 3), "score_zero": r(self.score_zero, 3),
                "rotation_deg": r(self.rotation_deg, 3), "scale": r(self.scale, 5),
                "valid_share": r(self.valid_share, 3), "model": self.model, "ms": r(self.ms, 1),
                "instruction": self.instruction, "plate_created": self.plate_created}


def _gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 3:
        return frame[:, :, 0]
    return frame


def _plate_grid(plate) -> Tuple[np.ndarray, int, Tuple[int, int], str]:
    """(the plate picture on its grid (float32), ds, the full frame size (W, H) it was taken from, created)."""
    if hasattr(plate, "plate"):
        img = np.asarray(plate.plate, np.float32)
        ds = int(getattr(plate, "ds", 1) or 1)
        fs = getattr(plate, "frame_size", None)
        size = (int(fs[0]), int(fs[1])) if fs is not None else (img.shape[1] * ds, img.shape[0] * ds)
        return img, ds, size, str(getattr(plate, "created", "") or "")
    img = np.asarray(_gray(np.asarray(plate)), np.float32)
    return img, 1, (img.shape[1], img.shape[0]), ""


def _down(gray: np.ndarray, ds: int) -> np.ndarray:
    """As core/foreground._down: the frame on the plate's grid (area average)."""
    if ds <= 1:
        return np.asarray(gray, np.float32)
    h, w = gray.shape[:2]
    if gray.dtype == np.uint8:          # averaged as 8 bits (rounded: 0.3 DN), at a quarter of the cost
        return cv2.resize(gray, (w // ds, h // ds), interpolation=cv2.INTER_AREA).astype(np.float32)
    return cv2.resize(np.asarray(gray, np.float32), (w // ds, h // ds), interpolation=cv2.INTER_AREA)


def _zone(roi, ds: int, shape: Tuple[int, int]) -> Tuple[int, int, int, int]:
    """The zone (x0, y0, x1, y1) on the plate's grid, clamped; the whole picture for None."""
    H, W = shape
    if roi is None:
        return 0, 0, W, H
    x, y, w, h = (float(v) for v in roi[:4])
    x0 = max(0, min(W, int(math.floor(x / ds))))
    y0 = max(0, min(H, int(math.floor(y / ds))))
    x1 = max(0, min(W, int(math.ceil((x + w) / ds))))
    y1 = max(0, min(H, int(math.ceil((y + h) / ds))))
    return x0, y0, x1, y1


_LAPLACE = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)   # Immerkaer's noise mask (|.| = 6)
_EDGE_NOISE_GAIN = 1.0 / (2.0 * math.sqrt(math.pi) * EDGE_SIGMA) if EDGE_SIGMA > 0 else 1.0   # white noise rms


def _edges(img: np.ndarray, valid: np.ndarray, level_mask: np.ndarray) -> Tuple[np.ndarray, float]:
    """(the picture in log units with a floor at a tenth of its own level, minus its local mean (the light's
    shape), lightly blurred, 0 outside ``valid``; the rms its noise alone would have there).  The noise is read
    on the picture itself (the median of a second difference: edges are too few to move it), so a dark picture
    whose log is mostly noise is not taken for structure."""
    vals = img[::2, ::2][(level_mask & valid)[::2, ::2]]          # robust statistics: a quarter of the pixels
    level = float(np.percentile(vals, 75)) if vals.size else float(np.percentile(img, 75))
    floor = max(FLOOR_MIN_DN, FLOOR_SHARE * level)
    L = cv2.log(np.maximum(np.asarray(img, np.float32), 0.0) + np.float32(floor))
    w = valid.astype(np.float32)
    inner = cv2.erode(valid.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    r = np.abs(cv2.filter2D(L, -1, _LAPLACE))[::2, ::2][inner[::2, ::2]]
    noise = (1.4826 * float(np.median(r)) / 6.0) * _EDGE_NOISE_GAIN if r.size else 0.0
    num = _local_mean(L * w)
    den = _local_mean(w)
    E = (L - num / np.maximum(den, 1e-3)) * w
    if EDGE_SIGMA > 0:
        E = cv2.GaussianBlur(E, (0, 0), EDGE_SIGMA)
    return (E * w).astype(np.float32), noise


def _local_mean(a: np.ndarray) -> np.ndarray:
    """A local mean over ~``LOCAL_SIGMA`` plate px: two passes of a box of ``_BOX`` px (a Gaussian's shape)."""
    return cv2.blur(cv2.blur(a, (_BOX, _BOX)), (_BOX, _BOX))


_BOX = int(round(math.sqrt(6.0 * LOCAL_SIGMA ** 2 + 1.0))) | 1     # two boxes of 15 px: sigma 6.1


def _structure(E: np.ndarray, ok: np.ndarray, noise: float) -> float:
    """The edges' strength beyond the noise (log units, rms over the compared pixels)."""
    e2 = float(np.mean(E[ok].astype(np.float64) ** 2)) if ok.any() else 0.0
    return math.sqrt(max(0.0, e2 - noise * noise))


def _ncc(a: np.ndarray, b: np.ndarray, m: np.ndarray) -> Optional[float]:
    if int(m.sum()) < 16:
        return None
    x, y = a[m].astype(np.float64), b[m].astype(np.float64)
    x, y = x - x.mean(), y - y.mean()
    d = math.sqrt(float((x * x).sum()) * float((y * y).sum()))
    return float((x * y).sum() / d) if d > 0 else None


def _moved_where(dx: Optional[float], dy: Optional[float]) -> str:
    """« (the wall 3 px left, 1 px lower in the picture) »: where the wall went, "" under half a pixel."""
    if dx is None or dy is None:
        return ""
    parts = []
    if abs(dx) >= 0.5:
        parts.append(f"{abs(dx):.0f} px {'right' if dx > 0 else 'left'}")
    if abs(dy) >= 0.5:
        parts.append(f"{abs(dy):.0f} px {'lower' if dy > 0 else 'higher'}")
    return f" (the wall {', '.join(parts)} in the picture)" if parts else ""


def _verdict_text(status: str, shift: Optional[float] = None, dx: Optional[float] = None,
                  dy: Optional[float] = None, crop: str = "") -> str:
    """« fact - instruction » for a verdict (``crop``: « another camera crop » note, "" when the same)."""
    if status == MATCHES:
        return f"the empty-wall picture lines up with the camera ({shift:.1f} px)"
    if status == MOVED:
        return (f"the camera moved about {max(1, round(shift))} px since the empty-wall picture"
                f"{_moved_where(dx, dy)}{crop}: the zone and the exclusion mask moved with it - {FIX_MOVED}")
    if status == OTHER_WALL:
        return (f"the empty-wall picture is not this wall{crop}: nothing in it lines up with the camera - "
                f"{FIX_PLATE}")
    return "not checked"


def _unknown_text(why: str, todo: str) -> str:
    return f"not checked against the camera: {why} - {todo}"


DARK_DN = 20.0            # a picture whose zone's 99th percentile (on the plate's grid) is under this is too dark to
                          # compare: a few grey levels, no edge to register (the gate's dark line; the 14/10 10:02
                          # plate sits at 13 DN and blinds the foreground, every lit plate of 08/10 at 255)
SHARP_MIN = 0.5           # the frame's fine edges over its coarse ones, against the plate's: under it, blurred
                          # (08/10: 1.02-1.03, another light included; the corpus with people 0.88-2.2; the 08/10
                          # frame blurred 4 / 8 / 16 px: 0.57 / 0.29 / 0.17)
LK_POINTS = (1200, 2000)  # the plate's strongest edge points followed at half and at full plate resolution
LK_ITER = 12
LK_EPS = 0.02             # plate px: a step this small (at the zone's corners) ends the refinement
TILES = (3, 5)            # the zone cut in 3 x 5 tiles ...
TILE_MIN = 0.3            # ... each lines up when its own correlation is at least this
_DEBUG: dict = {}
_PREP: dict = {}          # the plate's side, prepared once per plate picture and zone (its identity and a checksum)


def _plate_key(P: np.ndarray, zone, ds: int) -> tuple:
    return (id(P), P.__array_interface__["data"][0], P.shape, float(P[::17, ::13].sum()), tuple(zone), ds)


def _p99(img: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    a = img[y0:y1:2, x0:x1:2]
    return float(np.percentile(a, 99)) if a.size else 0.0


def _bands(E: np.ndarray, m: np.ndarray, Eq: np.ndarray) -> float:
    """The fine edges (``E`` less its 3 x 3 mean, rms over ``m``) over the coarse ones (``Eq``: the zone's edges
    at a quarter of the grid, rms): a blur takes the fine ones, the light scales both alike."""
    fine = (E - cv2.blur(E, (3, 3)))[::2, ::2][m[::2, ::2]]
    if not fine.size or not Eq.size:
        return 0.0
    co = float(np.mean(Eq.astype(np.float64) ** 2))
    return math.sqrt(float(np.mean(fine.astype(np.float64) ** 2)) / max(1e-12, co))


def _points(E: np.ndarray, valid: np.ndarray, n: int):
    """The ``n`` strongest edge points of ``E`` inside ``valid`` (x, y, value, gx, gy), float32 arrays."""
    gx = cv2.Sobel(E, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(E, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    inner = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    ys, xs = np.nonzero(inner)
    if xs.size == 0:
        return None
    mag = np.hypot(gx[ys, xs], gy[ys, xs])
    if xs.size > n:
        keep = np.argpartition(-mag, n)[:n]
        ys, xs = ys[keep], xs[keep]
    return (xs.astype(np.float32), ys.astype(np.float32), E[ys, xs].astype(np.float32), gx[ys, xs], gy[ys, xs])


def _prep_plate(P: np.ndarray, zone, ds: int) -> dict:
    """The plate's side of the check (its edges, their strength, the points followed), cached per picture and zone."""
    key = _plate_key(P, zone, ds)
    got = _PREP.get(key)
    if got is not None:
        return got
    x0, y0, x1, y1 = zone
    H, W = P.shape
    valid = np.zeros((H, W), bool)
    valid[y0:y1, x0:x1] = True
    clip = P >= CLIP_DN
    if clip.any():
        clip = cv2.dilate(clip.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    vp = valid & ~clip
    out = {"p99": _p99(P, x0, y0, x1, y1), "valid": vp}
    if int(vp.sum()) >= MIN_VALID:
        E, noise = _edges(P, vp, valid)
        out.update(E=E, noise=noise, structure=_structure(E, vp, noise))
        E1 = cv2.pyrDown(E)
        v1 = cv2.resize(vp.astype(np.uint8), (E1.shape[1], E1.shape[0]), interpolation=cv2.INTER_NEAREST).astype(bool)
        E2 = cv2.pyrDown(np.ascontiguousarray(E1[y0 // 2:(y1 + 1) // 2, x0 // 2:(x1 + 1) // 2]))
        out.update(E1=E1, E2=E2, sharp=_bands(E, vp, E2), win2=cv2.createHanningWindow((E2.shape[1], E2.shape[0]), cv2.CV_32F),
                   pts=(_points(E1, v1, LK_POINTS[0]), _points(E, vp, LK_POINTS[1])))
    if len(_PREP) > 8:
        _PREP.clear()
    _PREP[key] = out
    return out


def _sample(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return cv2.remap(img, x.reshape(1, -1), y.reshape(1, -1), cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                     borderValue=0).ravel()


def _lk(Ef: np.ndarray, okf: np.ndarray, pts, c, M: np.ndarray, affine: bool,
        eps: float = LK_EPS) -> Tuple[np.ndarray, int]:
    """Inverse-compositional Lucas-Kanade of the plate's points against the frame's edges ``Ef`` (both on one
    grid), robust (Huber) and with a contrast factor; ``M`` (3 x 3, plate -> frame about the centre ``c``) as the
    start.  Returns (M, the points used)."""
    xs, ys, T, gx, gy = pts
    X, Y = xs - c[0], ys - c[1]
    J = np.stack([gx * X, gx * Y, gy * X, gy * Y, gx, gy], 1) if affine else np.stack([gx, gy], 1)
    J = J.astype(np.float64)
    H, W = Ef.shape
    n = 0
    for _ in range(LK_ITER):
        u = M[0, 0] * X + M[0, 1] * Y + M[0, 2] + c[0]
        v = M[1, 0] * X + M[1, 1] * Y + M[1, 2] + c[1]
        iu, iv = np.rint(u).astype(np.int64), np.rint(v).astype(np.int64)
        inside = (iu >= 1) & (iv >= 1) & (iu < W - 1) & (iv < H - 1)
        inside[inside] = okf[iv[inside], iu[inside]]
        n = int(inside.sum())
        if n < 64:
            return M, n
        I = _sample(Ef, u.astype(np.float32), v.astype(np.float32)).astype(np.float64)
        Ti = T.astype(np.float64)
        a = float(np.dot(I[inside], Ti[inside]) / max(1e-12, np.dot(Ti[inside], Ti[inside])))
        a = min(5.0, max(0.2, a)) if a > 0 else 1.0
        e = np.where(inside, I / a - Ti, 0.0)
        s = 1.4826 * float(np.median(np.abs(e[inside]))) + 1e-9
        w = np.where(inside, np.minimum(1.0, 1.5 * s / np.maximum(np.abs(e), 1e-12)), 0.0)
        Jw = J * w[:, None]
        try:
            d = np.linalg.solve(Jw.T @ J, Jw.T @ e)
        except np.linalg.LinAlgError:
            return M, n
        D = np.eye(3)
        if affine:
            D[0, 0] += d[0]; D[0, 1] = d[1]; D[1, 0] = d[2]; D[1, 1] += d[3]; D[0, 2] = d[4]; D[1, 2] = d[5]
        else:
            D[0, 2], D[1, 2] = d[0], d[1]
        try:
            M = M @ np.linalg.inv(D)
        except np.linalg.LinAlgError:
            return M, n
        if not np.all(np.isfinite(M)):
            return np.eye(3), 0
        step = float(np.abs(d[-2:]).max()) + (float(np.abs(d[:4]).max()) * max(abs(X).max(), abs(Y).max())
                                                if affine else 0.0)
        if step < eps:
            break
    return M, n


def check_reference(plate, frame, roi=None, exclude=None) -> ReferenceVerdict:
    """The plate (``CleanPlate`` as the build holds it in memory, or a picture) against ``frame`` (the raw
    camera frame as the pipeline receives it: mono uint8 H x W, or BGR with R == G == B) over the zone ``roi``
    (x, y, w, h in frame px; None = the whole frame), ignoring the ``exclude`` boxes (x1, y1, x2, y2 in frame
    px: the dancers' boxes and foreground groups in RUN; None on the STANDBY preview).  Never raises: a
    picture it cannot read is ``unknown``.  Never writes into ``plate`` or ``frame``.  (dx, dy): a wall feature
    at (x, y) in the plate is seen at (x + dx, y + dy) in ``frame``."""
    t0 = time.perf_counter()
    created = ""
    try:
        if plate is None:
            return _unknown("no empty-wall picture", FIX_PLATE, t0)
        if frame is None:
            return _unknown("no picture from the camera", TODO_DARK, t0)
        P, ds, psize, created = _plate_grid(plate)
        g = _gray(np.asarray(frame))
        if P.ndim != 2 or g.ndim != 2 or P.size == 0 or g.size == 0:
            return _unknown("an empty picture", TODO_DARK, t0, created)
        crop = ""
        if (int(psize[0]), int(psize[1])) != (int(g.shape[1]), int(g.shape[0])):
            crop = f" (another crop: {psize[0]}x{psize[1]}, camera {g.shape[1]}x{g.shape[0]})"
        # the plate and the frame on one grid: another crop is compared over their common part, top left
        H, W = min(P.shape[0], g.shape[0] // ds), min(P.shape[1], g.shape[1] // ds)
        if P.shape != (H, W):
            P = np.ascontiguousarray(P[:H, :W])
        x0, y0, x1, y1 = _zone(roi, ds, (H, W))
        if x1 - x0 < 16 or y1 - y0 < 16:
            return _unknown("the zone is too small", "check the zone (1 Rig)", t0, created)
        pp = _prep_plate(P, (x0, y0, x1, y1), ds)
        F = _down(g[:H * ds, :W * ds], ds)
        p99f = _p99(F, x0, y0, x1, y1)
        if pp["p99"] < DARK_DN:
            return _unknown(f"the empty-wall picture is too dark to compare ({pp['p99']:.0f} DN)",
                            FIX_DARK, t0, created)
        if p99f < DARK_DN:
            return _unknown(f"the camera's picture is too dark to compare ({p99f:.0f} DN)", TODO_DARK, t0, created)
        valid = np.zeros((H, W), bool)
        valid[y0:y1, x0:x1] = True
        clip = F >= CLIP_DN
        if clip.any():
            clip = cv2.dilate(clip.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        okf = ~clip
        for b in exclude or ():
            bx0 = max(0, int(math.floor(float(b[0]) / ds)) - EXCLUDE_GROW)
            by0 = max(0, int(math.floor(float(b[1]) / ds)) - EXCLUDE_GROW)
            bx1 = min(W, int(math.ceil(float(b[2]) / ds)) + EXCLUDE_GROW)
            by1 = min(H, int(math.ceil(float(b[3]) / ds)) + EXCLUDE_GROW)
            if bx1 > bx0 and by1 > by0:
                okf[by0:by1, bx0:bx1] = False
        ok = valid & okf & pp["valid"]
        n_zone = (x1 - x0) * (y1 - y0)
        n_ok = int(ok.sum())
        share = n_ok / float(max(1, n_zone))
        if n_ok < MIN_VALID or "E" not in pp:
            return _unknown(f"only {100 * share:.0f} % of the zone can be compared (at white or hidden)",
                            TODO_AGAIN, t0, created, valid_share=share)
        EP = pp["E"]
        EF, noise_f = _edges(F, okf, valid & okf)
        EFn = EF.copy()
        EFn[~okf] = np.nan                    # where the frame cannot be compared: NaN, carried by the warps
        sp, sf = pp["structure"], _structure(EF[::2, ::2], ok[::2, ::2], noise_f)
        structure = min(sp, sf)
        zero = _ncc(EP[::2, ::2], EF[::2, ::2], ok[::2, ::2])
        if structure < STRUCTURE_MIN:
            if sp < sf:
                return _unknown("the empty-wall picture is too dark or too flat to compare",
                                f"if the wall is empty: {FIX_PLATE}", t0, created, structure, share, zero)
            return _unknown("the camera's picture is too dark or too flat to compare", TODO_DARK, t0, created,
                            structure, share, zero)
        # coarse: phase correlation over the zone at a quarter of the plate's grid (any shift up to half the zone)
        h0, h1 = (x0 // 2, y0 // 2), ((x1 + 1) // 2, (y1 + 1) // 2)
        EF1, okf1 = cv2.pyrDown(EF), cv2.resize(okf.astype(np.uint8), (pp["E1"].shape[1], pp["E1"].shape[0]),
                                                 interpolation=cv2.INTER_NEAREST).astype(bool)
        zt = pp["E2"]
        zf = cv2.pyrDown(np.ascontiguousarray(EF1[h0[1]:h1[1], h0[0]:h1[0]]))
        (cdx, cdy), _resp = cv2.phaseCorrelate(zt, zf, pp["win2"])
        sharp = _bands(EF, ok, zf) / max(1e-6, pp["sharp"])
        cdx, cdy = 2.0 * cdx, 2.0 * cdy
        c1 = ((h0[0] + h1[0]) / 2.0, (h0[1] + h1[1]) / 2.0)
        c0 = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        best = None
        for sx, sy in ((cdx, cdy), (0.0, 0.0)):         # from the phase correlation; from zero only if that fails
            M = np.array([[1, 0, sx], [0, 1, sy], [0, 0, 1]], np.float64)
            M, _n = _lk(EF1, okf1, pp["pts"][0], c1, M, True, 0.05)
            M[:2, 2] *= 2.0                                  # half resolution -> the plate's grid
            M[:2, 2] += (M[:2, :2] - np.eye(2)) @ (np.array(c0) - np.array(c1) * 2.0)   # same map about c0
            Ma, na = _lk(EF, okf, pp["pts"][1], c0, M, True, LK_EPS)
            Mt = np.array([[1, 0, Ma[0, 2]], [0, 1, Ma[1, 2]], [0, 0, 1]], np.float64)
            zw, zh = x1 - x0, y1 - y0
            spread = _spread(Ma, Mt, zw, zh)
            Mx, model = Mt, "translation"
            cc, tiles = _ncc_warped(EP, EFn, pp["valid"], Mt, c0, (x0, y0, x1, y1)), None
            if spread * ds > AFFINE_MIN_PX and na >= 64 and abs(np.linalg.det(Ma[:2, :2]) - 1.0) < 0.2:
                # the affine map is kept when it lines the zone up better than its translation alone: moving
                # dancers (no exclude) can bend it a little without any gain
                cca, tla = _ncc_warped(EP, EFn, pp["valid"], Ma, c0, (x0, y0, x1, y1)), None
                _DEBUG["gain"] = None if cca is None or cc is None else cca - cc
                if cca is not None and (cc is None or cca >= cc + AFFINE_GAIN):
                    Mx, model, cc, tiles = Ma, "affine", cca, tla
            if cc is not None and (best is None or cc > best[0]):
                best = (cc, Mx, model, tiles)
            if best is not None and best[0] >= SAME_MIN:
                break
        if best is None:
            return _other_or_unknown(zero, None, structure, share, t0, created, crop)
        cc, M, model, tiles = best
        _DEBUG.update(sharp=sharp, cc=cc)
        if sharp < SHARP_MIN and cc > OTHER_MAX:
            # the same picture, blurred: a displacement read on it is not to be trusted (nor a match)
            return _unknown(f"the camera's picture is blurred ({100 * sharp:.0f} % of the empty-wall picture's "
                            "fine edges)", "check the focus and the lens", t0, created, structure, share, zero, cc)
        if cc < SAME_MIN:
            if cc > OTHER_MAX and _tiles(EP, EFn, pp["valid"], M, c0, (x0, y0, x1, y1)) < MOVED_TILES:
                # between the lines, the tiles tell: only a part of the zone lines up -- a scene that shares a
                # part with the plate (the same hall, another backdrop); most of it weakly -- cannot tell
                return ReferenceVerdict(None, structure, OTHER_WALL, _verdict_text(OTHER_WALL, crop=crop), score=cc,
                                        score_zero=zero, valid_share=share, ms=(time.perf_counter() - t0) * 1000,
                                        at=time.time(), plate_created=created)
            return _other_or_unknown(zero, cc, structure, share, t0, created, crop)
        zw, zh = x1 - x0, y1 - y0
        corners = np.array([[-zw / 2, -zh / 2], [zw / 2, -zh / 2], [-zw / 2, zh / 2], [zw / 2, zh / 2], [0, 0]],
                           np.float64)
        A, t = M[:2, :2], M[:2, 2]
        disp = corners @ A.T + t - corners
        mags = np.hypot(disp[:, 0], disp[:, 1]) * ds
        shift = float(mags.max())
        dx, dy = float(disp[4, 0] * ds), float(disp[4, 1] * ds)
        rot = float(math.degrees(math.atan2(A[1, 0] - A[0, 1], A[0, 0] + A[1, 1])))
        scale = float(math.sqrt(max(1e-12, abs(np.linalg.det(A)))))
        status = MOVED if (shift >= MOVED_PX or crop) else MATCHES
        if status == MOVED and _tiles(EP, EFn, pp["valid"], M, c0, (x0, y0, x1, y1)) < MOVED_TILES:
            # only a part of the zone lines up after the displacement: a scene that shares a part with the plate
            return ReferenceVerdict(None, structure, OTHER_WALL, _verdict_text(OTHER_WALL, crop=crop), score=cc,
                                    score_zero=zero, valid_share=share, ms=(time.perf_counter() - t0) * 1000,
                                    at=time.time(), plate_created=created)
        return ReferenceVerdict(shift, structure, status, _verdict_text(status, shift, dx, dy, crop), dx=dx,
                                dy=dy, score=cc, score_zero=zero, rotation_deg=rot, scale=scale, valid_share=share,
                                model=model, ms=(time.perf_counter() - t0) * 1000, at=time.time(),
                                plate_created=created)
    except Exception as exc:  # noqa: BLE001 - a check that fails says so, never green, never raises
        return _unknown(f"the check failed ({type(exc).__name__}: {exc})", TODO_AGAIN, t0, created)


def _spread(Ma: np.ndarray, Mt: np.ndarray, zw: float, zh: float) -> float:
    """The largest difference, over the zone's corners, between two maps about its centre (plate px)."""
    c = np.array([[-zw / 2, -zh / 2], [zw / 2, -zh / 2], [-zw / 2, zh / 2], [zw / 2, zh / 2]], np.float64)
    d = c @ (Ma[:2, :2] - Mt[:2, :2]).T + (Ma[:2, 2] - Mt[:2, 2])
    return float(np.hypot(d[:, 0], d[:, 1]).max())


def _warp_back(EFn, M, c0, zone):
    """The frame's edges brought back onto the plate's zone by ``M`` (NaN where they cannot be compared)."""
    x0, y0, x1, y1 = zone
    A = M[:2, :2]
    t = M[:2, 2] + np.array(c0) - A @ np.array(c0) + A @ np.array([x0, y0], np.float64)  # zone px -> frame px
    return cv2.warpAffine(EFn, np.hstack([A, t[:, None]]).astype(np.float64), (x1 - x0, y1 - y0),
                          flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_CONSTANT,
                          borderValue=float("nan"))


def _ncc_warped(EP, EFn, pv, M, c0, zone) -> Optional[float]:
    """The correlation of the plate's edges with the frame's brought back onto the plate by ``M``, over the zone
    (every other pixel: the edges are smooth over 2 px)."""
    x0, y0, x1, y1 = zone
    fw = _warp_back(EFn, M, c0, zone)
    m = np.isfinite(fw) & pv[y0:y1, x0:x1]
    return _ncc(EP[y0:y1:2, x0:x1:2], fw[::2, ::2], m[::2, ::2])


def _tiles(EP, EFn, pv, M, c0, zone) -> float:
    """The share of the zone's tiles (``TILES``) that line up on their own (``TILE_MIN``) after ``M``, of those
    with enough of both pictures to compare: a scene that shares only a part with the plate lines up only there."""
    x0, y0, x1, y1 = zone
    fw = _warp_back(EFn, M, c0, zone)
    m = np.isfinite(fw) & pv[y0:y1, x0:x1]
    ez = EP[y0:y1, x0:x1]
    nx, ny = TILES
    xs, ys = np.linspace(0, x1 - x0, nx + 1).astype(int), np.linspace(0, y1 - y0, ny + 1).astype(int)
    good = n = 0
    for jy in range(ny):
        for ix in range(nx):
            sl = (slice(ys[jy], ys[jy + 1]), slice(xs[ix], xs[ix + 1]))
            if m[sl].mean() < 0.3:
                continue
            v = _ncc(ez[sl], fw[sl], m[sl])
            if v is None:
                continue
            n += 1
            good += v >= TILE_MIN
    _DEBUG["tiles"] = good / n if n else 0.0
    return good / n if n else 0.0


def _unknown(why: str, todo: str, t0: float, created: str = "", structure: float = 0.0, valid_share: float = 0.0,
             score_zero: Optional[float] = None, score: Optional[float] = None) -> ReferenceVerdict:
    return ReferenceVerdict(None, float(structure), UNKNOWN, _unknown_text(why, todo), score=score,
                            score_zero=score_zero, valid_share=valid_share, ms=(time.perf_counter() - t0) * 1000,
                            at=time.time(), plate_created=created)


def _other_or_unknown(zero: Optional[float], score: Optional[float], structure: float, share: float, t0: float,
                      created: str, crop: str = "") -> ReferenceVerdict:
    """No alignment reaches ``SAME_MIN``: another wall when even the best is at or under ``OTHER_MAX``."""
    best = max(v for v in (score, zero, -1.0) if v is not None)
    if best <= OTHER_MAX:
        return ReferenceVerdict(None, structure, OTHER_WALL, _verdict_text(OTHER_WALL, crop=crop), score=score,
                                score_zero=zero, valid_share=share, ms=(time.perf_counter() - t0) * 1000,
                                at=time.time(), plate_created=created)
    return _unknown(f"the picture only partly looks like the empty-wall picture{crop} (correlation {best:.2f}: "
                    "the light changed, or people are in view)", TODO_AGAIN, t0, created, structure, share, zero,
                    score)


# --------------------------------------------------------------------------------------------------------------
# The watch: the last verdict, held in one place; the checks off the frame path; the referee's refusals; the prompt
# --------------------------------------------------------------------------------------------------------------

REFUSED = "refused"           # the watch's own status: the swipe's referee refused the plate REFUSALS_ALERT times
DARK_PLATE = "dark_plate"     # ... the plate's own signal is too weak for the foreground (``plate_signal``)
BLIND = "blind"               # ... the foreground has found no one for ``BLIND_TICKS`` checks while the detector did
REFUSALS_ALERT = 3
RUN_EVERY_S = 5.0             # a check every few seconds in RUN (outside the boxes and groups) ...
STANDBY_EVERY_S = 10.0        # ... and on the STANDBY preview (no exclude: nothing is detected there)
CONFIRM_S = 1.0               # an alert (moved, another wall) is shown once a second check, this soon, says it too:
                              # one frame where the dancers hide the wall's edges never turns the line red
EMPTY_S = 5.0                 # the wall provably empty this long (no detection, a still picture): the prompt
STILL_MEAN_DN = 1.5           # a still picture: the zone (1/16 size) changed less than this on average in 1 s ...
STILL_MAX_DN = 12.0           # ... and nowhere by more than this
PROMPT_RUN = "the wall is empty now: STANDBY, then CALIBRATE (2 Calibrate) on it, then Save to project"
PROMPT_STANDBY = "the wall is empty now: CALIBRATE (2 Calibrate) on it, then Save to project"
PROMPT_MOVED = ("the wall is empty now: STANDBY, check the zone and the mask (1 Rig), then CALIBRATE on it and "
                "Save to project")
PROMPT_DARK = ("the wall is empty now: light it as for the show, then CALIBRATE (2 Calibrate) on it and "
               "Save to project")
_STARTED = time.time()        # the session (this process) began: a plate of another day is flagged

# A plate that makes the foreground blind (#t-063 item 7).  The 14/10 10:02 plate, captured on a 3.6 ms take
# (its zone's 99th percentile at 13 DN), puts the foreground's threshold at 570 DN against 12 to 28 for a usable
# plate and the foreground finds 0 of 169 dancers on the 08/10 frames (refwatch-1126/gate/plate_quality_table.txt).
# Its signal: the zone's 99th percentile on the plate's grid; its noise: the threshold the foreground would use
# against a frame of its own light, max(FgParams.thr_frame_floor, k_frame x the plate's sigma) = 6 DN on every
# plate on disk.  The blind plate reads 2.1; the darkest plates the corpus used, 4.3 (mur25m 06/10 slot 1, 26 DN)
# and up; every lit plate of 08/10 42.  The line sits between, 1.4 x over the blind one.
PLATE_SNR_MIN = 3.0
# The running signal: confident detections (>= BLIND_CONF) in the zone on BLIND_TICKS of the 1 Hz ticks and not
# one foreground group (the swipe's group_min_area and up) on any of them.  A usable plate puts a group on a
# truth dancer on 0.917 to 0.976 of the frames (the same table), the blind one on none: fifteen ticks in a row with
# dancers and no group at all do not happen on a usable plate.
BLIND_TICKS = 15
BLIND_CONF = 0.4
FIX_DARK = "light the wall as for the show, then CALIBRATE on the empty wall (2 Calibrate), then Save to project"


@dataclass
class PlateSignal:
    """The plate's own signal against its noise (``plate_signal``)."""
    p99: float                    # the zone's 99th percentile, DN on the plate's grid
    noise: float                  # the foreground's threshold against a frame of the plate's own light, DN
    ratio: float
    usable: bool
    text: str                     # « fact - instruction » when not usable, "" when usable


def plate_signal(plate, roi=None) -> Optional[PlateSignal]:
    """Whether the plate (``CleanPlate`` in memory) carries enough signal for the foreground: its zone's 99th
    percentile against the threshold the foreground would set on a frame of its own light (``PLATE_SNR_MIN``).
    None without a plate; never raises (an unreadable plate is not usable)."""
    if plate is None:
        return None
    try:
        P, ds, _size, _created = _plate_grid(plate)
        x0, y0, x1, y1 = _zone(roi, ds, P.shape)
        p99 = _p99(P, x0, y0, x1, y1)
        try:
            from core.foreground import FgParams
            fp = FgParams()
            floor, k = float(fp.thr_frame_floor), float(fp.k_frame)
        except Exception:  # noqa: BLE001 - the foreground's defaults of 26/11
            floor, k = 6.0, 10.0
        noise = max(floor, k * float(getattr(plate, "sigma", 0.0) or 0.0))
        ratio = p99 / noise if noise > 0 else 0.0
    except Exception as exc:  # noqa: BLE001
        return PlateSignal(0.0, 0.0, 0.0, False, f"the empty-wall picture cannot be read ({type(exc).__name__}) - "
                                                 f"{FIX_DARK}")
    if ratio >= PLATE_SNR_MIN:
        return PlateSignal(p99, noise, ratio, True, "")
    return PlateSignal(p99, noise, ratio, False,
                       f"the empty-wall picture is too dark for the foreground (its brightest part {p99:.0f} DN, "
                       f"{ratio:.1f} x its noise, needs {PLATE_SNR_MIN:.0f} x) - {FIX_DARK}")


def session_day() -> str:
    """The day this session (the app's process) started, « YYYY-MM-DD »."""
    return time.strftime("%Y-%m-%d", time.localtime(_STARTED))


def session_started():
    """When this session (the app's process) started, a naive local datetime."""
    from datetime import datetime
    return datetime.fromtimestamp(_STARTED)


class ReferenceWatch:
    """The references watched, for the app.  The main loop's 1 Hz tick asks ``due`` and hands a frame to
    ``submit``: the check runs in a worker thread (``run_async``; inline in tests) and its verdict is held here,
    the one place the readiness row and the health line read (``current``).  A new plate object (loaded,
    captured by Calibrate, swapped, resumed by A2) drops the old verdict at once: it read another plate.  An
    alert is confirmed by a second check ``CONFIRM_S`` later before it is shown; a match is shown at once."""

    def __init__(self, run_every_s: float = RUN_EVERY_S, standby_every_s: float = STANDBY_EVERY_S,
                 run_async: bool = True):
        self.run_every_s, self.standby_every_s = float(run_every_s), float(standby_every_s)
        self.run_async = bool(run_async)
        self._lock = threading.Lock()
        self._verdict: Optional[ReferenceVerdict] = None   # the verdict shown
        self._unconfirmed: Optional[ReferenceVerdict] = None   # an alert waiting for its second check
        self._plate = None                 # the plate object the watch follows (the verdict read this one)
        self._busy = False
        self._last_submit: Optional[float] = None
        self._was_busy = False             # a Calibrate or a capture ran since the last check
        self.checks = 0
        self.last_why = ""
        self.refusals = 0                  # the swipe referee's refusals in a row (note_referee_refusal)
        self.refusal_reasons: List[str] = []
        self._empty_since: Optional[float] = None
        self._prev_small: Optional[np.ndarray] = None
        self._prompting = False
        self._signal: Tuple[object, Optional[PlateSignal]] = (None, None)   # (the plate it read, its signal)
        self._blind_plate = None           # the plate the « foreground sees nothing » count is about
        self.blind_ticks = 0               # ticks with dancers detected and no foreground group, in a row

    # -- the checks ------------------------------------------------------------------------------------------
    def due(self, now: float, plate, *, in_run: bool, busy: bool = False) -> Optional[str]:
        """Why a check is due now (« plate »: a new plate; « after Calibrate »; « load »; « confirm »: an alert
        to confirm; « tick »), else None.  Never while ``busy`` (a Calibrate or a plate capture runs: the light
        and the plate are changing) or while a check runs."""
        if busy:
            self._was_busy = True
            return None
        if plate is None or self._busy:
            return None
        if plate is not self._plate:
            return "plate"
        if self._was_busy:
            return "after Calibrate"
        if self._last_submit is None:
            return "load"
        if self._unconfirmed is not None and now - self._last_submit >= CONFIRM_S:
            return "confirm"
        if now - self._last_submit >= (self.run_every_s if in_run else self.standby_every_s):
            return "tick"
        return None

    def submit(self, now: float, plate, frame, roi=None, exclude=None, why: str = "") -> bool:
        """Start one check of ``plate`` against ``frame`` (copied: the camera's buffer goes on); False when one
        still runs.  The verdict lands in ``current`` when it ends."""
        if plate is None or frame is None:
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            if plate is not self._plate:
                self._plate, self._verdict, self._unconfirmed = plate, None, None
                self.refusals, self.refusal_reasons = 0, []
                self.blind_ticks = 0
        self._was_busy = False
        self._last_submit = now
        job = (plate, np.array(_gray(np.asarray(frame)), copy=True, order="C"),
               tuple(roi) if roi is not None else None,
               [tuple(float(v) for v in b[:4]) for b in exclude] if exclude else None, why)
        if self.run_async:
            threading.Thread(target=self._run, args=job, name="ReferenceWatch", daemon=True).start()
        else:
            self._run(*job)
        return True

    def _run(self, plate, frame, roi, exclude, why) -> None:
        v = None
        try:
            v = check_reference(plate, frame, roi, exclude)
        finally:
            with self._lock:
                if v is not None and plate is self._plate:
                    self.last_why = why
                    self.checks += 1
                    if not v.alert:
                        self._verdict, self._unconfirmed = v, None
                    elif self._unconfirmed is not None or (self._verdict is not None and self._verdict.alert):
                        self._verdict, self._unconfirmed = v, None       # two checks in a row: shown
                    else:
                        self._unconfirmed = v                             # held for its second check
                self._busy = False

    def wait(self, timeout_s: float = 5.0) -> bool:
        """Until the running check ends (tests, tools); True when none runs."""
        t_end = time.monotonic() + timeout_s
        while self._busy and time.monotonic() < t_end:
            time.sleep(0.005)
        return not self._busy

    def forget(self) -> None:
        """No plate any more (removed, project without one): no verdict."""
        with self._lock:
            self._plate, self._verdict, self._unconfirmed = None, None, None
            self.refusals, self.refusal_reasons = 0, []
            self._signal, self._blind_plate, self.blind_ticks = (None, None), None, 0

    def current(self, plate=None) -> Optional[ReferenceVerdict]:
        """The verdict shown -- None when no check has read the plate in use (``plate``, when given, must be the
        one the verdict read) or the first one is an alert not confirmed yet.  Three refusals in a row from the
        swipe's referee turn it into an alert of its own (status ``refused``), through this same path."""
        with self._lock:
            v, followed = self._verdict, self._plate
            n, reasons = self.refusals, list(self.refusal_reasons)
            sig_plate, sig = self._signal
            blind = self.blind_ticks >= BLIND_TICKS and (plate is None or self._blind_plate is plate)
        if plate is not None and sig_plate is plate and sig is not None and not sig.usable and n < REFUSALS_ALERT \
                and not (v is not None and plate is followed and v.status in (MOVED, OTHER_WALL)):
            return ReferenceVerdict(None, 0.0, DARK_PLATE, sig.text, at=time.time())
        if blind and n < REFUSALS_ALERT and not (v is not None and v.status in (MOVED, OTHER_WALL)):
            return ReferenceVerdict(None, 0.0, BLIND,
                                    f"the foreground found no one on {self.blind_ticks} checks while the detector saw "
                                    f"dancers: the empty-wall picture does not fit the picture now - {FIX_DARK}",
                                    at=time.time())
        if plate is not None and plate is not followed:
            return None
        if n >= REFUSALS_ALERT:
            why = reasons[-1] if reasons else "no reason given"
            base = v if v is not None else ReferenceVerdict(None, 0.0, UNKNOWN, "")
            return ReferenceVerdict(base.shift_px, base.structure, REFUSED,
                                    f"the swipe's referee refused the empty-wall picture {n} times in a row "
                                    f"({why}) - {FIX_PLATE}", dx=base.dx, dy=base.dy, score=base.score,
                                    score_zero=base.score_zero, valid_share=base.valid_share, model=base.model,
                                    at=time.time(), plate_created=base.plate_created)
        return v

    # -- the plate's own signal; the foreground that sees nothing (#t-063 item 7) ---------------------------------
    def note_plate(self, plate, roi=None) -> Optional[PlateSignal]:
        """The plate in use (loaded, captured by Calibrate, swapped): its signal is read once per plate object.
        Returns the signal when it was read now and is NOT usable (the caller says it once), else None."""
        with self._lock:
            if plate is None:
                self._signal = (None, None)
                return None
            if self._signal[0] is plate:
                return None
        sig = plate_signal(plate, roi)
        with self._lock:
            self._signal = (plate, sig)
        return sig if sig is not None and not sig.usable else None

    def note_foreground(self, plate, dancers: Optional[int], groups: Optional[int]) -> bool:
        """Once a tick in RUN: ``dancers`` confident detections in the zone (None: the detector does not run),
        ``groups`` foreground groups of a dancer's size on the last frame (None: the foreground is off or
        refused the frame).  Counts the ticks with dancers and no group, in a row; any group starts it again.
        Returns whether the foreground is blind now (``BLIND_TICKS``)."""
        with self._lock:
            if plate is not self._blind_plate:
                self._blind_plate, self.blind_ticks = plate, 0
            if plate is None or groups is None or dancers is None:
                return self.blind_ticks >= BLIND_TICKS
            if groups > 0:
                self.blind_ticks = 0
            elif dancers > 0:
                self.blind_ticks += 1
            return self.blind_ticks >= BLIND_TICKS

    # -- the swipe referee's hook ------------------------------------------------------------------------------
    def note_referee_refusal(self, reason: str = "") -> int:
        """The swipe's referee refused the plate (P5: it cannot trust it).  Records and counts only; at
        ``REFUSALS_ALERT`` in a row ``current`` raises the plate alert.  Returns the count."""
        with self._lock:
            self.refusals += 1
            self.refusal_reasons = (self.refusal_reasons + [str(reason or "")])[-REFUSALS_ALERT:]
            return self.refusals

    def note_referee_accept(self) -> None:
        """The referee used the plate: the refusals in a row start again from zero."""
        with self._lock:
            self.refusals, self.refusal_reasons = 0, []

    # -- the prompt: the wall provably empty, the plate to retake -------------------------------------------------
    def note_scene(self, now: float, frame, roi=None, detections: Optional[int] = None) -> bool:
        """Once a second: whether the wall is provably empty -- the live model ran and saw nobody
        (``detections`` == 0; None = it did not run: STANDBY, so not provable) and the picture is still (the
        zone at 1/16 size against the last call).  Returns whether it is empty now."""
        small = None
        if frame is not None:
            g = _gray(np.asarray(frame))
            H, W = g.shape[:2]
            x0, y0, x1, y1 = _zone(roi, 1, (H, W))
            z = g[y0:y1, x0:x1]
            if z.size:
                small = cv2.resize(np.asarray(z, np.float32), (max(1, z.shape[1] // 16), max(1, z.shape[0] // 16)),
                                   interpolation=cv2.INTER_AREA)
        prev, self._prev_small = self._prev_small, small
        still = (small is not None and prev is not None and prev.shape == small.shape
                 and float(np.mean(np.abs(small - prev))) < STILL_MEAN_DN
                 and float(np.max(np.abs(small - prev))) < STILL_MAX_DN)
        empty = still and detections is not None and int(detections) == 0
        if not empty:
            self._empty_since = None
        elif self._empty_since is None:
            self._empty_since = now
        return empty

    def empty_for(self, now: float) -> float:
        return 0.0 if self._empty_since is None else max(0.0, now - self._empty_since)

    def prompt(self, now: float, plate_problem: str, verdict=None, in_run: bool = True) -> Optional[str]:
        """The instruction when the wall has been provably empty ``EMPTY_S`` and the plate is a problem
        (``plate_problem``: why -- missing, stale, moved, another wall, refused; "" when none), else None.  In
        the app's own rules: Calibrate runs in STANDBY or RUN, between two pieces in STANDBY; a camera that moved
        moved the zone and the mask too (``verdict``)."""
        if not plate_problem or self.empty_for(now) < EMPTY_S:
            return None
        if getattr(verdict, "status", "") == MOVED:
            return PROMPT_MOVED
        if getattr(verdict, "status", "") in (DARK_PLATE, BLIND):
            return PROMPT_DARK
        return PROMPT_RUN if in_run else PROMPT_STANDBY

    def take_toast(self, prompt: Optional[str]) -> Optional[str]:
        """The prompt once per episode: the message the first time it turns on, then None until it ends."""
        if not prompt:
            self._prompting = False
            return None
        if self._prompting:
            return None
        self._prompting = True
        return prompt


WATCH = ReferenceWatch()


def note_referee_refusal(reason: str = "") -> int:
    """The hook for the swipe's referee (another session's code): it refused the empty-wall plate.  Records and
    counts only; three in a row raise the plate alert through the watch's verdict (``ReferenceWatch.current``).
    Nothing in this build calls it yet."""
    return WATCH.note_referee_refusal(reason)


def note_referee_accept() -> None:
    """The referee's counterpart: it used the plate, the refusals in a row are reset."""
    WATCH.note_referee_accept()


def zone_of(settings) -> Optional[Tuple[int, int, int, int]]:
    """The zone the pipeline watches, (x, y, w, h) in frame px, as ``FrameProcessor.light_ratio`` reads it; None
    without a ROI (the whole frame)."""
    s = settings
    w, h = int(getattr(s, "roi_w", 0) or 0), int(getattr(s, "roi_h", 0) or 0)
    if getattr(s, "roi_enabled", False) and w > 0 and h > 0:
        return int(s.roi_x), int(s.roi_y), w, h
    return None


def watch_of(app) -> ReferenceWatch:
    """The app's watch: ``app._reference_watch`` when set (tests), else the module's one."""
    w = getattr(app, "_reference_watch", None)
    return w if isinstance(w, ReferenceWatch) else WATCH


def plate_problem(*, status: str, stale_since: str = "", verdict: Optional[ReferenceVerdict] = None) -> str:
    """Why the plate in use must be retaken ("" when it need not): missing, from another crop, stale (F2),
    moved, another wall, refused by the referee.  ``status``: FrameProcessor.plate_status."""
    if status == "none":
        return "no empty-wall picture"
    if status == "size":
        return "the empty-wall picture is from another camera crop"
    if verdict is not None and verdict.status in (MOVED, OTHER_WALL, REFUSED, DARK_PLATE, BLIND):
        return {MOVED: "the camera moved since the empty-wall picture",
                OTHER_WALL: "the empty-wall picture is not this wall",
                REFUSED: "the referee refused the empty-wall picture",
                DARK_PLATE: "the empty-wall picture is too dark for the foreground",
                BLIND: "the foreground finds no one with this empty-wall picture"}[verdict.status]
    if status == "stale" or stale_since:
        return "the empty-wall picture is stale"
    return ""


def _group_min_area() -> float:
    try:
        from core.swipe import SwipeParams
        return float(SwipeParams().group_min_area)
    except Exception:  # noqa: BLE001 - no referee in this build: its default
        return 300.0


def confident_dancers(processor, conf: float = BLIND_CONF) -> int:
    """The detector's boxes of the last frame at ``conf`` and over (``last_raw_dets``: conf, x, y, h; the
    detector runs on the zone, so they are in it)."""
    n = 0
    for d in getattr(processor, "last_raw_dets", None) or ():
        try:
            n += float(d[0]) >= conf
        except (TypeError, ValueError, IndexError):
            continue
    return n


def foreground_groups(processor) -> Optional[int]:
    """The foreground's groups of a dancer's size on the last frame (the swipe's ``group_min_area`` and up); None
    when the foreground is off or did not compare the frame (no plate, refused, stale)."""
    fg = getattr(processor, "last_fg", None)
    if fg is None or not getattr(fg, "valid", False):
        return None
    min_area = _group_min_area()
    return sum(1 for b in (getattr(fg, "blobs", None) or ()) if float(getattr(b, "area", 0.0)) >= min_area)


def exclude_boxes(processor, body_h: float = 0.0, zone_area: float = 0.0, max_share: float = 0.4,
                  grow: float = 0.15) -> List[Tuple[float, float, float, float]]:
    """What a check in RUN leaves out: the detector's boxes of the last frame (``last_raw_boxes``, grown by
    ``grow``) and the foreground's dancer groups (the swipe referee's reader: ``group_min_area`` and up; their
    blob boxes).  A group larger than 3 x the dancers' height (``body_h``) is not a body -- a rim along an edge
    of a moved plate -- and stays in; when the groups would hide more than ``max_share`` of the zone
    (``zone_area``, px^2), only the boxes are left out."""
    out: List[Tuple[float, float, float, float]] = []
    for b in getattr(processor, "last_raw_boxes", None) or ():
        try:
            x0, y0, x1, y1 = (float(v) for v in b[:4])
        except (TypeError, ValueError):
            continue
        gx, gy = grow * (x1 - x0), grow * (y1 - y0)
        out.append((x0 - gx, y0 - gy, x1 + gx, y1 + gy))
    fg = getattr(processor, "last_fg", None)
    if fg is None or not getattr(fg, "valid", False):
        return out
    min_area = _group_min_area()
    groups = []
    for blob in getattr(fg, "blobs", None) or ():
        if float(getattr(blob, "area", 0.0)) < min_area:
            continue
        x0, y0, x1, y1 = (float(v) for v in blob.bbox)
        if body_h > 0 and max(x1 - x0, y1 - y0) > 3.0 * body_h:
            continue
        groups.append((x0 - 8.0, y0 - 8.0, x1 + 8.0, y1 + 8.0))
    hidden = sum((b[2] - b[0]) * (b[3] - b[1]) for b in groups)
    if zone_area > 0 and hidden > max_share * zone_area:
        return out
    return out + groups
