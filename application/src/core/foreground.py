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
plate (lights changed, camera moved, someone right in front of the lens) disables the
foreground for that frame, is not blended into the plate, and counts toward a "re-capture
the empty wall" hint.

Pure numpy + OpenCV; ~1-2 ms per frame on the laptop CPU for a 1300x570 ROI.
"""
from __future__ import annotations

import json
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
    # X6 (Plan Cordée rev 2): the camera it was taken at -- exposure_us, gain_db (None = auto / unknown), crop
    # (the frame size above) and the ROI [x, y, w, h].  Empty for a plate saved before 2026-11-26.
    camera: dict = field(default_factory=dict)
    # A2 ② (rehearsal build): in a plate the minute-save wrote (plates/live.npz), the slowly updated plate the
    # detector ran on and when it was saved; ``plate`` above stays the snapshot as captured.  None for a capture.
    live: Optional[np.ndarray] = None
    live_saved: str = ""

    def camera_mismatch(self, exposure_us: Optional[float], gain_db: Optional[float],
                        tol_exposure: float = 0.10, tol_gain_db: float = 1.0) -> Optional[str]:
        """X6 ③: why this plate does not describe a camera at ``exposure_us`` / ``gain_db``, or None.  A plate
        from another exposure or gain is refused, not scaled (on 08/10 the 20:06 plate, taken at 25 ms, was
        scaled onto takes at 8.8-12 ms).  Unknown on either side: no refusal."""
        pe, pg = self.camera.get("exposure_us"), self.camera.get("gain_db")
        if pe and exposure_us and abs(float(exposure_us) / float(pe) - 1.0) > tol_exposure:
            return (f"plate taken at {float(pe) / 1000:.1f} ms, the camera is at {float(exposure_us) / 1000:.1f} ms"
                    " - take the empty wall again")
        if pg is not None and gain_db is not None and abs(float(gain_db) - float(pg)) > tol_gain_db:
            return (f"plate taken at {float(pg):.1f} dB, the camera is at {float(gain_db):.1f} dB"
                    " - take the empty wall again")
        return None

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

    def save(self, path, compress: bool = True) -> Path:
        """Write the plate to ``path`` (``.npz`` appended when missing, as numpy did).  A2: atomically -- a
        temporary file beside it, flushed to the disk, then renamed over it -- so a power cut leaves the
        previous file or the new one, never half a plate.  ``compress=False`` (the minute-save): ~2 ms of
        CPU instead of ~75 for a 444 x 326 plate and its live copy, ~20 % more bytes; ``load`` reads both."""
        import os
        path = Path(path)
        if path.suffix != ".npz":
            path = path.with_name(path.name + ".npz")
        path.parent.mkdir(parents=True, exist_ok=True)
        extra = {} if self.live is None else {"live": np.asarray(self.live, np.float32),
                                              "live_saved": self.live_saved}
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            (np.savez_compressed if compress else np.savez)(
                f, plate=self.plate, sigma=self.sigma, frame_size=np.array(self.frame_size), ds=self.ds,
                frames=self.frames, created=self.created, source=self.source,
                camera=np.array(json.dumps(self.camera or {})), **extra)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return path

    @classmethod
    def load(cls, path) -> "CleanPlate":
        with np.load(Path(path), allow_pickle=False) as z:
            camera = {}
            if "camera" in z.files:      # X6: absent in a plate saved before 2026-11-26
                try:
                    camera = json.loads(str(z["camera"])) or {}
                except ValueError:
                    camera = {}
            plate = z["plate"].astype(np.float32)
            live = z["live"].astype(np.float32) if "live" in z.files else None     # A2 ②: a minute-save
            return cls(plate=plate, sigma=float(z["sigma"]),
                       frame_size=tuple(int(v) for v in z["frame_size"]), ds=int(z["ds"]),
                       frames=int(z["frames"]), created=str(z["created"]), source=str(z["source"]),
                       camera=camera, live=live if live is not None and live.shape == plate.shape else None,
                       live_saved=str(z["live_saved"]) if "live_saved" in z.files else "")


# A2 ② (rehearsal build): the live plate -- the snapshot as captured with its slowly updated copy -- is saved
# every minute beside the project's plates/latest.npz and resumed at start, so a relaunch during the show
# comes back with the foreground it had instead of a snapshot the dusk has made stale.
LIVE_PLATE_FILE = "live.npz"
LIVE_PLATE_SAVE_S = 60.0


def live_plate_of(det: "ForegroundDetector") -> CleanPlate:
    """What ``det`` runs on, as one CleanPlate to save: the snapshot as captured (its camera, noise, size and
    time) in ``plate`` and the slowly updated plate in ``live``, copied (the update writes it in place), so the
    file can be written from another thread while ``det`` goes on.  The snapshot is never written after its
    capture: shared, not copied (a torn one could not pass ``resumable_live_plate``'s pixel check anyway)."""
    from dataclasses import replace
    return replace(det.plate, camera=dict(det.plate.camera or {}),
                   live=np.array(det._live, np.float32, copy=True),
                   live_saved=time.strftime("%Y-%m-%dT%H:%M:%S"))


def resumable_live_plate(live_path, latest_path) -> Optional[CleanPlate]:
    """The plate the minute-save wrote at ``live_path`` when it is the snapshot ``latest_path`` holds (same
    capture time, size and pixels) with its live copy; else None -- no file, unreadable, or another capture
    since (the next minute-save writes that one)."""
    live_path, latest_path = Path(live_path), Path(latest_path)
    if not (live_path.is_file() and latest_path.is_file()):
        return None
    try:
        plate, base = CleanPlate.load(live_path), CleanPlate.load(latest_path)
    except Exception as exc:  # noqa: BLE001 - an unreadable live plate: the captured one loads as before
        print(f"[Foreground] live plate not resumed ({type(exc).__name__}: {exc})")
        return None
    if (plate.live is None or plate.created != base.created or tuple(plate.frame_size) != tuple(base.frame_size)
            or plate.ds != base.ds or not np.array_equal(plate.plate, base.plate)):
        return None
    return plate


def resume_live(det: "ForegroundDetector", plate: CleanPlate) -> bool:
    """A detector just built on ``plate`` goes on from the live plate saved with it.  False when ``plate``
    carries none (a captured plate) or one of another size."""
    if plate.live is None or plate.live.shape != det._live.shape:
        return False
    det._live = np.array(plate.live, np.float32, copy=True)
    return True


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
    # F1 (Plan Cordée rev 2): a clip-aware comparison.  The brightness ratio comes from pixels clipped
    # neither in the plate nor in the frame; where the plate is clipped (the lit oval) a pixel is foreground
    # when it is at least ``below_white`` under white; elsewhere the threshold is ``k_frame`` x this frame's
    # own residual noise (robust sigma of frame - ratio x plate), so it follows the camera gain.  At 30 dB on
    # 08/10 that is about 22 levels at quarter size.  False: the rule shipped before (one ratio for every
    # pixel, ``k`` x the plate's capture noise), whose clipped oval and 2-level threshold flood the frame.
    clip_aware: bool = True
    clip_dn: float = 250.0
    below_white: float = 22.0
    k_frame: float = 10.0
    thr_frame_floor: float = 6.0
    # selective plate update: every update_every frames the plate moves toward the current frame by
    # update_alpha, except under protected boxes (YOLO-confirmed dancers, backed belts).  A lighting
    # change that left a lasting difference fades in ~update_every / fps / update_alpha s (~25 s).
    update_every: int = 10
    update_alpha: float = 0.02
    # F2 (Plan Cordée rev 2, C2 ④): the slow update never absorbs a still dancer.  A foreground component
    # that lies mostly inside the protect boxes (a live slot, a backed belt, a recent skeleton: a person) is
    # HELD, and stays held while it stays on the wall -- pixel by pixel, and as a component that mostly
    # overlaps what was held -- even once YOLO and her slot have let her go.  The update skips the held pixels
    # of the last ``hold_memory`` frames, grown by ``hold_grow`` downscaled px (her sub-threshold rim).  A
    # difference no person stood in (a lamp, a shadow) is absorbed as before.  False: the rule shipped before.
    hold_groups: bool = True
    hold_seed_share: float = 0.5
    hold_memory: int = 20
    hold_grow: int = 2
    # ... and a group carried by memory alone (no protect box on it now) must be body-sized: at least this
    # share of the protect boxes' area (the pipeline's boxes are 0.6 h x 1.2 h, so 0.07 is about 0.05 h^2, a
    # sixth of what a dancer's group covers): a speck or a knot that touched her is not kept after she left.
    hold_min_box_share: float = 0.07
    # F2 (C5 ②): a plate found stale stays « stale » until a new plate: a leaky count of stale frames (+1 a
    # stale frame, -0.5 a valid one) latches at ``stale_latch_frames`` (about 1 s at 20 fps of a stale
    # scene; a frame or two of a passer-by does not).  Nothing but a new detector (a new plate) unlatches it.
    stale_latch_frames: int = 20


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
        self.last_thr = 0.0       # the threshold of the last processed frame (DN, downscaled)
        # F2: the stale latch (wall time it latched, "" = never) and the held groups (the update_plate call at
        # which each plate pixel was last held)
        self.stale_since = ""
        self._stale_score = 0.0
        self._lab: Optional[np.ndarray] = None
        self._lab_stats: Optional[np.ndarray] = None
        self._held_clock = 0
        self._held_last = np.full(self._live.shape, -(2 ** 30), np.int32)
        self._held_box_area = 0.0     # the protect boxes' median area (downscaled px), last seen

    def matches(self, frame_w: int, frame_h: int) -> bool:
        return (int(frame_w), int(frame_h)) == tuple(self.plate.frame_size)

    def _align(self, roi_gray: np.ndarray, ox: int, oy: int, base: np.ndarray):
        """The crop downscaled on the plate grid and the matching window of ``base`` (a plate):
        (cur, B, gx0, gy0), or None for an empty crop."""
        ds = self.plate.ds
        g = _gray(roi_gray)
        # align the crop on the plate grid: start at the first multiple of ds inside it
        sx, sy = (-ox) % ds, (-oy) % ds
        g = g[sy:, sx:]
        gx0, gy0 = (ox + sx) // ds, (oy + sy) // ds
        h, w = g.shape[:2]
        h, w = (h // ds) * ds, (w // ds) * ds
        if h <= 0 or w <= 0:
            return None
        cur = _down(g[:h, :w], ds)
        ph, pw = cur.shape
        B = base[gy0:gy0 + ph, gx0:gx0 + pw]
        if B.shape != cur.shape:   # ROI partly outside the plate (should not happen)
            ph, pw = min(ph, B.shape[0]), min(pw, B.shape[1])
            cur, B = cur[:ph, :pw], B[:ph, :pw]
        return cur, B, gx0, gy0

    def light_ratio(self, roi_gray: np.ndarray, ox: int, oy: int,
                    frame_size: Tuple[int, int]) -> Optional[float]:
        """Brightness of this raw crop over the SNAPSHOT as captured (not the slowly updated live
        plate): the median pixel ratio where the snapshot is lit enough to measure (the gain
        normalisation's rule), unclamped.  None for another camera crop, an empty crop or a
        snapshot too dark to measure.  Read-only: safe to sample between ``process`` calls."""
        if not self.matches(*frame_size):
            return None
        al = self._align(roi_gray, ox, oy, self.plate.plate)
        if al is None:
            return None
        cur, B = al[0], al[1]
        ok = B >= self.p.gain_plate_dn
        if not ok.size or ok.mean() < self.p.gain_min_px:
            return None
        return float(np.median(cur[ok] / B[ok]))

    def process(self, roi_gray: np.ndarray, ox: int, oy: int,
                frame_size: Tuple[int, int]) -> FgFrame:
        """``roi_gray``: the raw ROI crop (or the whole frame) at original resolution,
        whose top-left pixel is (ox, oy) of a ``frame_size`` (W, H) frame."""
        ds, p = self.plate.ds, self.p
        self._cur = None          # a rejected frame (other crop, empty ROI, stale) leaves update_plate nothing
        if not self.matches(*frame_size):
            self.last = FgFrame(None, ox, oy, ds, valid=False, reason="plate size")
            return self.last
        al = self._align(roi_gray, ox, oy, self._live)
        if al is None:
            self.last = FgFrame(None, ox, oy, ds, valid=False, reason="empty roi")
            return self.last
        cur, B, gx0, gy0 = al
        if p.clip_aware:
            gain, thr, m = self._compare_clip_aware(cur, B)
        else:
            gain = 1.0
            if p.gain_norm:
                ok = B >= p.gain_plate_dn
                if ok.mean() >= p.gain_min_px:
                    gain = float(np.median(cur[ok] / B[ok]))
                    gain = min(p.gain_range[1], max(p.gain_range[0], gain))
            thr = max(p.thr_floor, p.k * self.plate.sigma * math.sqrt(max(1.0, gain)))
            m = (np.abs(cur - gain * B) > thr).astype(np.uint8)
        self.last_thr = thr
        m = cv2.morphologyEx(cv2.morphologyEx(m, cv2.MORPH_OPEN, self._ko), cv2.MORPH_CLOSE, self._kc)
        ratio = float(m.mean())
        x0, y0 = float(gx0 * ds), float(gy0 * ds)
        if ratio > p.max_fg_ratio:
            self.stale_frames += 1
            self._note_stale(True)
            self.last = FgFrame(None, x0, y0, ds, gain=gain, fg_ratio=ratio, valid=False,
                                reason="plate stale")
            return self.last
        self.stale_frames = 0
        self._note_stale(False)
        # only now: a stale frame (someone right in front of the lens, a light change) must not
        # be blended into the plate -- it would leave the gain off for tens of seconds (§C.15)
        self._cur, self._cur_at, self._gain = cur, (gy0, gx0), gain
        n, lab, st, cen = cv2.connectedComponentsWithStats(m, connectivity=8)
        self._lab, self._lab_stats = lab, st      # F2: this frame's components, for update_plate's held groups
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

    def _compare_clip_aware(self, cur: np.ndarray, B: np.ndarray):
        """F1: (gain, threshold, uint8 mask) for one downscaled crop against its plate window."""
        p = self.p
        clipped = B >= p.clip_dn
        ok = (B >= p.gain_plate_dn) & ~clipped & (cur < p.clip_dn)
        gain = 1.0
        if p.gain_norm and ok.mean() >= p.gain_min_px:
            gain = float(np.median(cur[ok] / B[ok]))
            gain = min(p.gain_range[1], max(p.gain_range[0], gain))
        d = cur - gain * B
        thr = p.thr_frame_floor
        if ok.any():
            r = d[ok][::3]                       # every third pixel is plenty for a robust sigma
            sigma = 1.4826 * float(np.median(np.abs(r - np.median(r))))
            thr = max(p.thr_frame_floor, p.k_frame * sigma)
        m = np.where(clipped, cur <= 255.0 - p.below_white, np.abs(d) > thr).astype(np.uint8)
        return gain, thr, m

    def update_plate(self, protect: Sequence[Tuple[float, float, float, float]] = ()) -> bool:
        """Selective plate update after ``process``: every ``update_every`` calls, blend the
        analysed area toward the current frame (brightness-normalised) except inside the
        ``protect`` boxes (x0, y0, x1, y1 original px: the dancers) and the held groups (F2: a
        still dancer YOLO lost, ``_track_held``, kept up to date on every call).  Returns True when
        it ran.  A frame ``process`` rejected (stale, other crop, empty ROI) is never blended in; it
        still counts toward the cadence.  F2: each analysed frame is used once -- a call with no
        ``process`` before it (the pipeline refused the plate: changed by hand, another exposure)
        blends nothing, instead of the last analysed frame again."""
        self._frames += 1
        p = self.p
        held = self._track_held(protect) if self._cur is not None and p.hold_groups else None
        fresh, self._cur = self._cur, None
        if fresh is None or p.update_alpha <= 0 or self._frames % max(1, p.update_every):
            return False
        ds = self.plate.ds
        gy0, gx0 = self._cur_at
        cur = fresh / max(1e-6, self._gain)
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
        if held is not None:
            keep |= held[:ph, :pw]
        upd = B[:ph, :pw]
        blend = upd + p.update_alpha * (cur[:ph, :pw] - upd)
        upd[~keep] = blend[~keep]
        return True

    # ------------------------------------------------------------------ F2 (Plan Cordée rev 2)
    @property
    def stale_latched(self) -> bool:
        """« Plate stale » was seen long enough to latch: it stays until a new plate (a new detector)."""
        return bool(self.stale_since)

    def _note_stale(self, stale: bool) -> None:
        """C5 ②: the leaky count of stale frames; latched once, never unlatched by the scene."""
        self._stale_score = self._stale_score + 1.0 if stale else max(0.0, self._stale_score - 0.5)
        if stale and not self.stale_since and self._stale_score >= max(1, int(self.p.stale_latch_frames)):
            self.stale_since = time.strftime("%H:%M:%S")

    def _track_held(self, protect: Sequence[Tuple[float, float, float, float]]) -> np.ndarray:
        """C2 ④: this frame's held groups (every frame, from ``update_plate``) -> the pixels the slow update
        must keep (bool, this crop's window): held within the last ``hold_memory`` frames, grown by
        ``hold_grow``.  Held now: the components of the mask with at least ``hold_seed_share`` of their
        pixels inside the protect boxes (a person) or, body-sized (``hold_min_box_share``), near the recently
        held pixels (she stays, or sways); and the recently held pixels still foreground in a body-sized
        component (she merged with something larger)."""
        p, ds = self.p, self.plate.ds
        gy0, gx0 = self._cur_at
        last = self._held_last[gy0:gy0 + self._lab.shape[0], gx0:gx0 + self._lab.shape[1]]   # a view
        ph, pw = last.shape
        lab, st = self._lab[:ph, :pw], self._lab_stats
        boxes = np.zeros((ph, pw), dtype=bool)
        for x0, y0, x1, y1 in protect:
            a0, a1 = int(x0 // ds) - gx0, int(math.ceil(x1 / ds)) - gx0
            b0, b1 = int(y0 // ds) - gy0, int(math.ceil(y1 / ds)) - gy0
            a0, a1, b0, b1 = max(0, a0), min(pw, a1), max(0, b0), min(ph, b1)
            if a1 > a0 and b1 > b0:
                boxes[b0:b1, a0:a1] = True
        if len(protect):
            sizes = sorted((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in protect)
            self._held_box_area = float(sizes[len(sizes) // 2]) / (ds * ds)
        now = self._held_clock = self._held_clock + 1
        g = max(0, int(p.hold_grow))
        mem = (now - 1 - last) <= p.hold_memory              # held within the last hold_memory calls
        area = st[:, cv2.CC_STAT_AREA]
        n = len(area)
        big = area >= p.hold_min_box_share * self._held_box_area
        comp = ((np.bincount(lab[boxes], minlength=n)[:n] >= p.hold_seed_share * area)
                | (big & (np.bincount(lab[_grown(mem, g)], minlength=n)[:n] >= p.hold_seed_share * area)))
        comp[0] = False
        held_now = (lab > 0) & mem
        if held_now.any():                                  # a held pixel stays held in a body-sized group only
            ys, xs = np.nonzero(held_now)
            small = ~big[lab[ys, xs]]
            held_now[ys[small], xs[small]] = False
        for k in np.flatnonzero(comp):                      # each held component, inside its own box
            x, y, w, h = (int(v) for v in st[k, :4])
            held_now[y:y + h, x:x + w] |= lab[y:y + h, x:x + w] == k
        last[held_now] = now
        return _grown((now - last) <= p.hold_memory, g)


def _grown(mask: np.ndarray, g: int) -> np.ndarray:
    """F2: ``mask`` (bool) dilated by a disc of radius ``g`` px, computed only around its set pixels."""
    if g <= 0:
        return mask
    rows = np.flatnonzero(mask.any(1))
    if not len(rows):
        return mask
    cols = np.flatnonzero(mask.any(0))
    y0, y1 = max(0, int(rows[0]) - g), min(mask.shape[0], int(rows[-1]) + g + 1)
    x0, x1 = max(0, int(cols[0]) - g), min(mask.shape[1], int(cols[-1]) + g + 1)
    out = np.zeros_like(mask)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g + 1, 2 * g + 1))
    out[y0:y1, x0:x1] = cv2.dilate(np.ascontiguousarray(mask[y0:y1, x0:x1]).view(np.uint8), k).astype(bool)
    return out
