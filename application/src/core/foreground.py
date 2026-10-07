"""Clean-plate foreground ("background snapshot", BRAINSTORM_APPROACHES_2026-10 §3.2).

The camera and the wall are fixed: a picture of the EMPTY wall (the plate) tells what is
not a dancer.  Each frame, the raw (un-enhanced) ROI gray is downscaled 4x, compared with
the plate scaled by the frame/plate brightness ratio, thresholded at k x the plate's noise,
cleaned (open / close) and split into connected components.  The identity-slot layer uses
it (``ForegroundMeasure``) as evidence next to YOLO and the belt:

* ghost veto: a track whose box holds almost no foreground cannot take a slot (static
  figures, stains, the door poster have none against the plate);
* foreground hold: a slot that would coast follows the foreground blob at its prediction;
* belt backing: a belt-only hold is not capped while the slot is on foreground.

Plate: ``CleanPlate.capture`` over ~2 s of empty-wall frames (median, full frame,
downscaled), saved as ``.npz`` in the project.  A plate whose size differs from the
frame (another camera crop) is ignored; a frame where most of the ROI differs from the
plate (lights changed, camera moved) disables the foreground for that frame and counts
toward a "re-capture the empty wall" hint.

Pure numpy + OpenCV; ~1-2 ms per frame on the laptop CPU for a 1300x570 ROI.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

PLATE_DS = 4                  # downscale factor (area average): 16 px -> 1, noise / 4
PLATE_MIN_FRAMES = 9


def _gray(frame: np.ndarray) -> np.ndarray:
    """Mono source (IDS: R == G == B) -> one channel, no conversion cost."""
    if frame.ndim == 3:
        return frame[:, :, 0]
    return frame


def _down(gray: np.ndarray, ds: int = PLATE_DS) -> np.ndarray:
    h, w = gray.shape[:2]
    return cv2.resize(gray.astype(np.float32), (w // ds, h // ds), interpolation=cv2.INTER_AREA)


@dataclass
class CleanPlate:
    """The empty-wall reference: full frame, downscaled ``ds`` times."""
    plate: np.ndarray             # float32 (H // ds, W // ds)
    sigma: float                  # temporal noise of one downscaled pixel (DN)
    frame_size: Tuple[int, int]   # (W, H) of the full frame it was taken from
    ds: int = PLATE_DS
    frames: int = 0
    created: str = ""
    source: str = ""

    @classmethod
    def from_frames(cls, frames: Sequence[np.ndarray], source: str = "",
                    ds: int = PLATE_DS) -> "CleanPlate":
        """Median of the downscaled frames; noise from consecutive-frame differences."""
        if len(frames) < 2:
            raise ValueError("a plate needs at least 2 frames")
        g0 = _gray(frames[0])
        H, W = g0.shape[:2]
        stack = np.stack([_down(_gray(f), ds) for f in frames])
        diffs = np.abs(np.diff(stack, axis=0))
        sigma = float(1.4826 * np.median(diffs) / math.sqrt(2.0))
        return cls(plate=np.median(stack, axis=0).astype(np.float32), sigma=max(sigma, 0.05),
                   frame_size=(int(W), int(H)), ds=ds, frames=len(frames),
                   created=time.strftime("%Y-%m-%dT%H:%M:%S"), source=source)

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, plate=self.plate, sigma=self.sigma,
                            frame_size=np.array(self.frame_size), ds=self.ds,
                            frames=self.frames, created=self.created, source=self.source)
        return path

    @classmethod
    def load(cls, path) -> "CleanPlate":
        with np.load(Path(path), allow_pickle=False) as z:
            return cls(plate=z["plate"].astype(np.float32), sigma=float(z["sigma"]),
                       frame_size=tuple(int(v) for v in z["frame_size"]), ds=int(z["ds"]),
                       frames=int(z["frames"]), created=str(z["created"]), source=str(z["source"]))


class PlateCapture:
    """Accumulates live frames for a plate (the "Capture empty wall" button)."""

    def __init__(self, n_frames: int = 40, every: int = 1):
        self.n = max(PLATE_MIN_FRAMES, int(n_frames))
        self.every = max(1, int(every))
        self._frames: List[np.ndarray] = []
        self._seen = 0

    def add(self, frame: np.ndarray) -> bool:
        """Feed one full frame; True once enough frames are in."""
        if self._seen % self.every == 0:
            self._frames.append(np.ascontiguousarray(_gray(frame)).copy())
        self._seen += 1
        return len(self._frames) >= self.n

    @property
    def progress(self) -> float:
        return min(1.0, len(self._frames) / self.n)

    def plate(self, source: str = "live") -> CleanPlate:
        return CleanPlate.from_frames(self._frames, source=source)


@dataclass
class FgParams:
    k: float = 4.0                # threshold, x the plate noise
    thr_floor: float = 2.0        # DN (downscaled): never below the 8-bit quantisation / codec noise
    gain_norm: bool = True        # scale the plate by the frame/plate brightness ratio
    gain_min_px: float = 0.05     # ... from at least this share of ROI pixels with plate >= gain_plate_dn
    gain_plate_dn: float = 3.0
    gain_range: Tuple[float, float] = (0.25, 4.0)
    min_area: int = 4             # downscaled px (= 64 original px): smaller components are noise
    max_components: int = 24
    max_fg_ratio: float = 0.25    # more of the ROI than this differs: the plate is stale -> no foreground
                                  # (two dancers at 25 m cover < 10 % of a wall ROI)
    open_k: int = 3
    close_k: int = 7
    # selective plate update: every update_every frames the plate moves toward the current frame by
    # update_alpha, except under protected boxes (YOLO-confirmed dancers, backed belts).  A lighting
    # change that left a lasting difference fades in ~update_every / fps / update_alpha s (~25 s).
    update_every: int = 10
    update_alpha: float = 0.02


@dataclass
class FgBlob:
    x: float                      # mass centroid, original px
    y: float
    area: float                   # original px^2
    mass: float
    bbox: Tuple[float, float, float, float]   # x0, y0, x1, y1 original px


@dataclass
class FgFrame:
    """One frame's foreground (``ForegroundMeasure`` for the slot layer)."""
    mask: Optional[np.ndarray]    # uint8 (downscaled ROI), None when invalid
    x0: float                     # original px of mask pixel (0, 0)'s top-left corner
    y0: float
    ds: int
    blobs: List[FgBlob] = field(default_factory=list)
    gain: float = 1.0
    fg_ratio: float = 0.0
    valid: bool = True
    reason: str = ""

    def support(self, cx: float, cy: float, h: float, wfrac: float = 0.45) -> float:
        """Share of foreground pixels in a person box (centre cx, cy; height h; width
        wfrac x h), original px.  1.0 when the foreground is unavailable (no veto)."""
        if not self.valid or self.mask is None:
            return 1.0
        m, ds = self.mask, self.ds
        bx0 = int((cx - wfrac * h / 2 - self.x0) / ds)
        bx1 = int((cx + wfrac * h / 2 - self.x0) / ds) + 1
        by0 = int((cy - h / 2 - self.y0) / ds)
        by1 = int((cy + h / 2 - self.y0) / ds) + 1
        Hm, Wm = m.shape
        bx0, bx1, by0, by1 = max(0, bx0), min(Wm, bx1), max(0, by0), min(Hm, by1)
        if bx1 <= bx0 or by1 <= by0:
            return 1.0        # the box is outside the analysed area: no evidence either way
        return float(m[by0:by1, bx0:bx1].mean())


class ForegroundDetector:
    """Per-frame foreground against a ``CleanPlate`` (see the module docstring)."""

    def __init__(self, plate: CleanPlate, params: Optional[FgParams] = None):
        self.plate = plate
        self.p = params or FgParams()
        self._ko = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.p.open_k, self.p.open_k))
        self._kc = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (self.p.close_k, self.p.close_k))
        self.stale_frames = 0     # consecutive frames rejected as "plate stale"
        self.last: Optional[FgFrame] = None
        self._live = plate.plate.copy()   # the plate in use (selective update); the file stays as captured
        self._cur: Optional[np.ndarray] = None
        self._cur_at: Tuple[int, int] = (0, 0)
        self._frames = 0

    def matches(self, frame_w: int, frame_h: int) -> bool:
        return (int(frame_w), int(frame_h)) == tuple(self.plate.frame_size)

    def process(self, roi_gray: np.ndarray, ox: int, oy: int,
                frame_size: Tuple[int, int]) -> FgFrame:
        """``roi_gray``: the raw ROI crop (or the whole frame) at original resolution,
        whose top-left pixel is (ox, oy) of a ``frame_size`` (W, H) frame."""
        ds, p = self.plate.ds, self.p
        if not self.matches(*frame_size):
            self.last = FgFrame(None, ox, oy, ds, valid=False, reason="plate size")
            return self.last
        g = _gray(roi_gray)
        # align the crop on the plate grid: start at the first multiple of ds inside it
        sx, sy = (-ox) % ds, (-oy) % ds
        g = g[sy:, sx:]
        gx0, gy0 = (ox + sx) // ds, (oy + sy) // ds
        h, w = g.shape[:2]
        h, w = (h // ds) * ds, (w // ds) * ds
        if h <= 0 or w <= 0:
            self.last = FgFrame(None, ox, oy, ds, valid=False, reason="empty roi")
            return self.last
        cur = _down(g[:h, :w], ds)
        ph, pw = cur.shape
        B = self._live[gy0:gy0 + ph, gx0:gx0 + pw]
        if B.shape != cur.shape:   # ROI partly outside the plate (should not happen)
            ph, pw = min(ph, B.shape[0]), min(pw, B.shape[1])
            cur, B = cur[:ph, :pw], B[:ph, :pw]
        gain = 1.0
        if p.gain_norm:
            ok = B >= p.gain_plate_dn
            if ok.mean() >= p.gain_min_px:
                gain = float(np.median(cur[ok] / B[ok]))
                gain = min(p.gain_range[1], max(p.gain_range[0], gain))
        self._cur, self._cur_at, self._gain = cur, (gy0, gx0), gain
        thr = max(p.thr_floor, p.k * self.plate.sigma * math.sqrt(max(1.0, gain)))
        m = (np.abs(cur - gain * B) > thr).astype(np.uint8)
        m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, self._ko), cv2.MORPH_CLOSE, self._kc)
        ratio = float(m.mean())
        x0, y0 = float(gx0 * ds), float(gy0 * ds)
        if ratio > p.max_fg_ratio:
            self.stale_frames += 1
            self.last = FgFrame(None, x0, y0, ds, gain=gain, fg_ratio=ratio, valid=False,
                                reason="plate stale")
            return self.last
        self.stale_frames = 0
        n, lab, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
        blobs: List[FgBlob] = []
        ad = np.abs(cur - gain * B)
        order = sorted(range(1, n), key=lambda j: -st[j, cv2.CC_STAT_AREA])
        for j in order[:p.max_components]:
            area = int(st[j, cv2.CC_STAT_AREA])
            if area < p.min_area:
                break
            bx, by, bw, bh = (int(st[j, k]) for k in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                                                       cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
            sub = lab[by:by + bh, bx:bx + bw] == j
            wgt = ad[by:by + bh, bx:bx + bw][sub]
            ys, xs = np.nonzero(sub)
            mass = float(wgt.sum())
            if mass <= 0:
                cxd, cyd = float(cen[j][0]), float(cen[j][1])
            else:
                cxd = bx + float((xs * wgt).sum()) / mass
                cyd = by + float((ys * wgt).sum()) / mass
            blobs.append(FgBlob(x=x0 + (cxd + 0.5) * ds, y=y0 + (cyd + 0.5) * ds,
                                area=float(area * ds * ds), mass=mass,
                                bbox=(x0 + bx * ds, y0 + by * ds,
                                      x0 + (bx + bw) * ds, y0 + (by + bh) * ds)))
        self.last = FgFrame(m, x0, y0, ds, blobs=blobs, gain=gain, fg_ratio=ratio)
        return self.last

    def update_plate(self, protect: Sequence[Tuple[float, float, float, float]] = ()) -> bool:
        """Selective plate update after ``process``: every ``update_every`` calls, blend the
        analysed area toward the current frame (brightness-normalised) except inside the
        ``protect`` boxes (x0, y0, x1, y1 original px: the dancers).  Returns True when it ran."""
        self._frames += 1
        p = self.p
        if self._cur is None or p.update_alpha <= 0 or self._frames % max(1, p.update_every):
            return False
        ds = self.plate.ds
        gy0, gx0 = self._cur_at
        cur = self._cur / max(1e-6, self._gain)
        ph, pw = cur.shape
        B = self._live[gy0:gy0 + ph, gx0:gx0 + pw]
        ph, pw = min(ph, B.shape[0]), min(pw, B.shape[1])
        keep = np.zeros((ph, pw), dtype=bool)
        for x0, y0, x1, y1 in protect:
            a0, a1 = int(x0 // ds) - gx0, int(math.ceil(x1 / ds)) - gx0
            b0, b1 = int(y0 // ds) - gy0, int(math.ceil(y1 / ds)) - gy0
            a0, a1, b0, b1 = max(0, a0), min(pw, a1), max(0, b0), min(ph, b1)
            if a1 > a0 and b1 > b0:
                keep[b0:b1, a0:a1] = True
        upd = B[:ph, :pw]
        blend = upd + p.update_alpha * (cur[:ph, :pw] - upd)
        upd[~keep] = blend[~keep]
        return True
