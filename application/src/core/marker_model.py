"""IR retro-marker model (MRK-2): geometry, noise and appearance of wrist/ankle
(+ harness) markers.

Shared by the Phase-0a harness (``tmp_analysis/marker_eval.py``), the future
detector (MRK-3) and the fusion code (MRK-5/6). **Pure numpy**: the only
optional dependency is scipy's Hungarian solver, with a brute-force fallback
for the small matrices this module deals with. There are no tracker or
pipeline imports. Coordinates are plain pixel arrays in whatever space the
caller works in.

The module has four parts:

1. **Layout and the tracker's centroid definition.** COCO keypoints 9/10/15/16
   (L/R wrist, L/R ankle), plus the optional harness marker at the hip
   midpoint (11/12). ``track_centroid`` is ``DancerTrack._compute_centroid``:
   the confidence-weighted mean of the keypoints with conf > 0.3.
2. **The offset-vote estimator** (02-ir-markers §3.1/§3.2). While YOLO sees the
   dancer, learn one offset per slot, ``o_i = C - m_i``. Through a YOLO gap,
   estimate ``C ~ mean_i(m_i + o_i)`` over the visible slots, or the median
   of the votes with 3+ markers. Markers are identical dots, so slots are
   re-associated frame by frame with a Hungarian nearest-neighbour step
   (``SlotTracker``). A similarity fit is provided for comparison only;
   never use it with 2 markers (§3.0).
3. **The noise table R(n, k).** It gives the centroid error of the unlabelled
   offset vote as a function of the number of visible markers *n* and the gap
   length *k* (frames at ~20 fps), per scene type. Values come from the
   2026-10 synthetic study on real skeletons (02 §3.0, reproduced by
   ``marker_eval.py centroid --markers keypoints``). It converts to a Rayleigh
   sigma and a Kalman measurement variance R in pixels.
4. **Appearance physics and a renderer.** Marker size vs dancer height, streak
   length vs speed x exposure, dwell fraction (02 §1.6), and a swept-disc
   rasteriser that injects saturated discs or motion streaks into a uint8
   frame. This lets the detector and fusion be tested end to end before real
   marker footage exists.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np

# ---------------------------------------------------------------------------
# 1. Layout + centroid definition
# ---------------------------------------------------------------------------

EXT_KPTS: Tuple[int, ...] = (9, 10, 15, 16)          # COCO L/R wrist, L/R ankle
SLOT_NAMES: Tuple[str, ...] = ("LW", "RW", "LA", "RA")
HIP_KPTS: Tuple[int, int] = (11, 12)                 # harness marker ~ hip midpoint
HARNESS_SLOT = "HIP"
# == core.config.KEYPOINT_CONFIDENCE (the tracker's centroid definition); kept
# literal so this module stays import-free (a unit test pins the equality).
KPT_CONF = 0.3
# Rayleigh: median = sigma * sqrt(2 ln 2). 2-D isotropic Gaussian error -> the
# radial error is Rayleigh-distributed, so sigma = p50 / 1.1774.
RAYLEIGH_MEDIAN = math.sqrt(2.0 * math.log(2.0))


def track_centroid(kpts: np.ndarray, conf: np.ndarray,
                   thresh: float = KPT_CONF) -> Optional[np.ndarray]:
    """The tracker's measurement: the confidence-weighted mean of the keypoints
    whose confidence is > ``thresh``. Returns None when no keypoint qualifies
    (the tracker would then fall back to the bbox centre)."""
    k = np.asarray(kpts, np.float64).reshape(-1, 2)
    c = np.asarray(conf, np.float64).reshape(-1)
    m = c > thresh
    if not m.any():
        return None
    return np.average(k[m], axis=0, weights=c[m])


def extremity_points(kpts: np.ndarray, conf: np.ndarray, *, harness: bool = False,
                     conf_min: float = KPT_CONF) -> Tuple[np.ndarray, np.ndarray]:
    """(points (S,2), visible (S,)) for the marker slots of one skeleton:
    LW, RW, LA, RA, plus HIP when ``harness``. The harness point is the hip
    midpoint and is visible only when both hips clear ``conf_min``."""
    k = np.asarray(kpts, np.float64).reshape(-1, 2)
    c = np.asarray(conf, np.float64).reshape(-1)
    pts = k[list(EXT_KPTS)]
    vis = c[list(EXT_KPTS)] > conf_min
    if harness:
        hip = k[list(HIP_KPTS)].mean(axis=0)
        pts = np.vstack([pts, hip[None]])
        vis = np.append(vis, bool((c[list(HIP_KPTS)] > conf_min).all()))
    return pts, vis


# ---------------------------------------------------------------------------
# 2. Offset-vote estimator + unlabelled slot association
# ---------------------------------------------------------------------------

def assign(cost: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Minimum-cost assignment (rows, cols) of a rectangular cost matrix.
    Uses scipy when available; otherwise brute force, which is fine for the
    <= 6x6 matrices of one dancer's slots. Use +inf for forbidden pairs:
    they are dropped from the result."""
    cost = np.asarray(cost, np.float64)
    if cost.size == 0:
        return np.zeros(0, int), np.zeros(0, int)
    finite = np.isfinite(cost)
    big = (np.abs(cost[finite]).max() + 1.0) * 1e3 if finite.any() else 1.0
    c = np.where(finite, cost, big)
    try:
        from scipy.optimize import linear_sum_assignment
        r, k = linear_sum_assignment(c)
    except ImportError:                          # pragma: no cover - scipy is a dep
        r, k = _assign_bruteforce(c)
    keep = finite[r, k]
    return np.asarray(r)[keep], np.asarray(k)[keep]


def _assign_bruteforce(c: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    n, m = c.shape
    if n > m:
        k, r = _assign_bruteforce(c.T)
        order = np.argsort(r)
        return r[order], k[order]
    if m > 9:
        raise ValueError("brute-force assignment limited to 9 columns (install scipy)")
    best, best_cols = math.inf, None
    for cols in itertools.permutations(range(m), n):
        s = c[np.arange(n), cols].sum()
        if s < best:
            best, best_cols = s, cols
    return np.arange(n), np.asarray(best_cols, int)


def associate_points(slots: np.ndarray, obs: np.ndarray,
                     gate: Union[float, np.ndarray] = math.inf) -> np.ndarray:
    """Hungarian nearest-neighbour association of observations to slots.
    Returns, per slot, the index of its observation or -1. Slots with a NaN
    position and pairs farther than ``gate`` (scalar or per slot) stay
    unmatched."""
    slots = np.asarray(slots, np.float64).reshape(-1, 2)
    obs = np.asarray(obs, np.float64).reshape(-1, 2)
    out = np.full(len(slots), -1, int)
    if len(slots) == 0 or len(obs) == 0:
        return out
    d = np.linalg.norm(slots[:, None, :] - obs[None, :, :], axis=2)
    g = np.broadcast_to(np.asarray(gate, np.float64).reshape(-1, 1)
                        if np.ndim(gate) else np.float64(gate), d.shape)
    d = np.where(np.isfinite(d) & (d <= g), d, np.inf)
    r, c = assign(d)
    out[r] = c
    return out


class SlotTracker:
    """Unlabelled slot tracking through a YOLO gap (§3.2 step 1).

    Markers are identical, so each frame the slots are re-associated to the
    observed points with a global Hungarian step. A slot that finds no point
    keeps its last position. With ``use_velocity``, the prediction is
    ``last position + last displacement``. ``visible`` is the slot mask of the
    most recent ``step``.
    """

    def __init__(self, positions: np.ndarray, gate: float = math.inf,
                 use_velocity: bool = False):
        self.pos = np.asarray(positions, np.float64).reshape(-1, 2).copy()
        self.vel = np.zeros_like(self.pos)
        self.gate = float(gate)
        self.use_velocity = use_velocity
        self.visible = np.isfinite(self.pos).all(axis=1)
        self.last_seen = np.where(self.visible, 0, -1)
        self.t = 0

    def predict(self) -> np.ndarray:
        return self.pos + self.vel if self.use_velocity else self.pos.copy()

    def step(self, obs: np.ndarray) -> np.ndarray:
        """Associate this frame's observed points; returns the visible mask."""
        self.t += 1
        obs = np.asarray(obs, np.float64).reshape(-1, 2)
        pred = self.predict()
        idx = associate_points(pred, obs, self.gate)
        vis = idx >= 0
        new = obs[idx[vis]]
        if self.use_velocity:
            self.vel[vis] = new - self.pos[vis]
        self.pos[vis] = new
        self.last_seen[vis] = self.t
        self.visible = vis
        return vis


def learn_offsets(centroid: np.ndarray, markers: np.ndarray) -> np.ndarray:
    """Per-slot offsets ``o_i = C - m_i`` (NaN rows stay NaN = unknown slot)."""
    c = np.asarray(centroid, np.float64).reshape(1, 2)
    return c - np.asarray(markers, np.float64).reshape(-1, 2)


def ema_offsets(prev: Optional[np.ndarray], new: np.ndarray, alpha: float) -> np.ndarray:
    """EMA of the offsets over YOLO frames (``alpha`` = weight of ``new``),
    NaN-aware: a slot unseen so far takes the new value; a slot not seen this
    frame keeps the old one."""
    new = np.asarray(new, np.float64)
    if prev is None:
        return new.copy()
    prev = np.asarray(prev, np.float64)
    out = np.where(np.isnan(prev), new, (1.0 - alpha) * prev + alpha * new)
    return np.where(np.isnan(new), prev, out)


def offset_vote(points: np.ndarray, offsets: np.ndarray, *, robust: bool = False,
                weights: Optional[np.ndarray] = None) -> Optional[np.ndarray]:
    """Centroid estimate from the visible markers and their frozen offsets.

    ``points``/``offsets`` are (n,2), already slot-aligned; rows with a NaN
    are ignored. ``robust`` uses the coordinate-wise median of the votes when
    3+ markers vote (§3.2: resist one bad slot); with 1-2 it is the mean.
    Returns None when nothing votes (n = 0 -> coast)."""
    v = np.asarray(points, np.float64).reshape(-1, 2) + np.asarray(offsets, np.float64).reshape(-1, 2)
    ok = np.isfinite(v).all(axis=1)
    if not ok.any():
        return None
    v = v[ok]
    if robust and len(v) >= 3:
        return np.median(v, axis=0)
    if weights is not None:
        w = np.asarray(weights, np.float64).reshape(-1)[ok]
        if w.sum() > 0:
            return np.average(v, axis=0, weights=w)
    return v.mean(axis=0)


def blend_single(vote: np.ndarray, hold: np.ndarray, w_vote: float = 0.5) -> np.ndarray:
    """n = 1 rule (§3.2): blend the single-marker vote 50/50 with the hold
    position. Short-gap p50 drops from 0.073 to 0.058 H on the aerial take."""
    return w_vote * np.asarray(vote, np.float64) + (1.0 - w_vote) * np.asarray(hold, np.float64)


def similarity_fit(src: np.ndarray, dst: np.ndarray,
                   scale_clip: Tuple[float, float] = (0.6, 1.6)):
    """Umeyama 2-D similarity ``dst ~ s R src + t``; returns (s, R, t).
    For comparison only: it does not beat the frozen offsets and fails on an
    ankle-only pair (02 §3.0)."""
    src = np.asarray(src, np.float64).reshape(-1, 2)
    dst = np.asarray(dst, np.float64).reshape(-1, 2)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    var_s = (xs ** 2).sum() / len(src)
    cov = xd.T @ xs / len(src)
    U, D, Vt = np.linalg.svd(cov)
    S = np.eye(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[1, 1] = -1
    R = U @ S @ Vt
    s = float(np.trace(np.diag(D) @ S) / max(var_s, 1e-9))
    s = float(np.clip(s, *scale_clip))
    t = mu_d - s * R @ mu_s
    return s, R, t


def apply_similarity(s: float, R: np.ndarray, t: np.ndarray, p: np.ndarray) -> np.ndarray:
    return s * (R @ np.asarray(p, np.float64).reshape(2)) + t


@dataclass
class OffsetModel:
    """The per-dancer state Tier B needs: frozen offsets (S,2) learned on the
    last YOLO frame (or an EMA), the centroid and H at that frame. ``estimate``
    takes slot-aligned marker positions (NaN = slot not seen this frame)."""
    offsets: np.ndarray
    centroid: np.ndarray
    H: float
    robust: bool = True

    @classmethod
    def learn(cls, centroid: np.ndarray, markers: np.ndarray, H: float,
              robust: bool = True) -> "OffsetModel":
        return cls(learn_offsets(centroid, markers), np.asarray(centroid, np.float64).copy(),
                   float(H), robust)

    def update(self, centroid: np.ndarray, markers: np.ndarray, alpha: float = 1.0,
               H: Optional[float] = None) -> None:
        self.offsets = ema_offsets(self.offsets, learn_offsets(centroid, markers), alpha)
        self.centroid = np.asarray(centroid, np.float64).copy()
        if H is not None:
            self.H = float(H)

    def n_votes(self, markers: np.ndarray) -> int:
        v = np.asarray(markers, np.float64).reshape(-1, 2) + self.offsets
        return int(np.isfinite(v).all(axis=1).sum())

    def estimate(self, markers: np.ndarray, hold: Optional[np.ndarray] = None,
                 single_blend: float = 0.5) -> Optional[np.ndarray]:
        est = offset_vote(markers, self.offsets, robust=self.robust)
        if est is None:
            return None
        if hold is not None and self.n_votes(markers) == 1 and single_blend < 1.0:
            return blend_single(est, hold, single_blend)
        return est


# ---------------------------------------------------------------------------
# 3. Noise table R(n, k)
# ---------------------------------------------------------------------------

# Median / p90 centroid error (÷ H, the track's median YOLO bbox height) of the
# UNLABELLED offset vote, by layout and gap k (frames @ ~20 fps). Measured on
# real skeletons with markers simulated at the YOLO keypoints, so the values
# carry YOLO keypoint noise and are conservative (02-ir-markers §3.0):
#   aerial = hangar-aerial (3_TANGO_HANGAR-whitebg2 s4, 5,088 frames, fast swing)
#   climb  = tango-H s8 + tango-H2 s9 (slow wall climbs)
#   floor  = hangar-floor window (whitebg2 s3, frames 1000-3999)
# Layout keys: 1..4 = extremity markers visible; "hip" = harness marker alone;
# "4+hip" = 4 extremities + harness; "hold"/"cvdecay" = today's baselines
# (last position / constant velocity with 0.9 decay ~ the KF coast).
# Regenerate with `marker_eval.py centroid --markers keypoints --poses dump:...`.
R_TABLE_KS: Tuple[int, ...] = (1, 2, 5, 10, 20)
R_TABLE: Dict[str, Dict[Union[int, str], Dict[int, Tuple[float, float]]]] = {
    "aerial": {
        "hold": {1: (0.068, 0.148), 2: (0.102, 0.229), 5: (0.186, 0.460), 10: (0.283, 0.707), 20: (0.362, 1.043)},
        "cvdecay": {1: (0.068, 0.172), 2: (0.102, 0.253), 5: (0.185, 0.451), 10: (0.338, 0.773), 20: (0.443, 1.081)},
        1: {1: (0.073, 0.292), 2: (0.092, 0.358), 5: (0.130, 0.467), 10: (0.189, 0.553), 20: (0.200, 0.590)},
        2: {1: (0.062, 0.192), 2: (0.076, 0.232), 5: (0.100, 0.272), 10: (0.137, 0.332), 20: (0.144, 0.369)},
        3: {1: (0.051, 0.142), 2: (0.061, 0.167), 5: (0.079, 0.193), 10: (0.103, 0.234), 20: (0.126, 0.242)},
        4: {1: (0.043, 0.111), 2: (0.048, 0.128), 5: (0.063, 0.134), 10: (0.074, 0.171), 20: (0.102, 0.185)},
        "hip": {1: (0.042, 0.099), 2: (0.045, 0.117), 5: (0.062, 0.130), 10: (0.070, 0.174), 20: (0.053, 0.157)},
        "4+hip": {1: (0.033, 0.084), 2: (0.037, 0.097), 5: (0.049, 0.106), 10: (0.059, 0.143), 20: (0.085, 0.150)},
    },
    "climb": {
        "hold": {1: (0.020, 0.045), 2: (0.022, 0.051), 5: (0.028, 0.064), 10: (0.038, 0.083), 20: (0.055, 0.142)},
        "cvdecay": {1: (0.029, 0.071), 2: (0.040, 0.112), 5: (0.071, 0.192), 10: (0.104, 0.297), 20: (0.169, 0.404)},
        1: {1: (0.032, 0.109), 2: (0.035, 0.118), 5: (0.044, 0.145), 10: (0.057, 0.178), 20: (0.075, 0.220)},
        2: {1: (0.028, 0.080), 2: (0.031, 0.089), 5: (0.038, 0.103), 10: (0.047, 0.121), 20: (0.058, 0.143)},
        3: {1: (0.024, 0.065), 2: (0.027, 0.070), 5: (0.032, 0.083), 10: (0.041, 0.097), 20: (0.047, 0.104)},
        4: {1: (0.021, 0.057), 2: (0.023, 0.057), 5: (0.028, 0.067), 10: (0.033, 0.084), 20: (0.039, 0.075)},
        "hip": {1: (0.018, 0.043), 2: (0.020, 0.045), 5: (0.020, 0.053), 10: (0.028, 0.066), 20: (0.037, 0.073)},
        "4+hip": {1: (0.017, 0.047), 2: (0.019, 0.049), 5: (0.023, 0.053), 10: (0.027, 0.066), 20: (0.034, 0.072)},
    },
    "floor": {
        "hold": {1: (0.020, 0.046), 2: (0.019, 0.053), 5: (0.030, 0.068), 10: (0.038, 0.090), 20: (0.063, 0.135)},
        "cvdecay": {1: (0.029, 0.075), 2: (0.044, 0.112), 5: (0.070, 0.185), 10: (0.108, 0.266), 20: (0.142, 0.337)},
        1: {1: (0.029, 0.085), 2: (0.035, 0.103), 5: (0.047, 0.134), 10: (0.062, 0.211), 20: (0.104, 0.356)},
        2: {1: (0.025, 0.069), 2: (0.029, 0.082), 5: (0.037, 0.095), 10: (0.054, 0.134), 20: (0.083, 0.227)},
        3: {1: (0.022, 0.059), 2: (0.026, 0.067), 5: (0.031, 0.074), 10: (0.042, 0.106), 20: (0.073, 0.173)},
        4: {1: (0.020, 0.054), 2: (0.023, 0.059), 5: (0.026, 0.058), 10: (0.036, 0.089), 20: (0.059, 0.135)},
        "hip": {1: (0.018, 0.043), 2: (0.020, 0.049), 5: (0.024, 0.060), 10: (0.032, 0.079), 20: (0.054, 0.131)},
        "4+hip": {1: (0.017, 0.046), 2: (0.021, 0.047), 5: (0.021, 0.048), 10: (0.027, 0.070), 20: (0.040, 0.106)},
    },
}
DEFAULT_SCENE = "aerial"      # the fast scene: the larger (conservative) R


def layout_key(n: int, harness: bool = False) -> Union[int, str, None]:
    """R_TABLE layout for n visible extremity markers (+ a visible harness).
    A harness with 1-3 extremities uses the harness-only row (the offset-vote
    mean with a few noisy extremities was not studied); None = n=0, coast."""
    if harness:
        return "4+hip" if n >= 4 else "hip"
    if n <= 0:
        return None
    return int(min(n, 4))


def marker_error(n: int, k: float, *, scene: str = DEFAULT_SCENE, q: str = "p50",
                 harness: bool = False, monotone: bool = True) -> Optional[float]:
    """Centroid error ÷ H for ``n`` visible markers ``k`` frames into a gap.

    Linear interpolation in k between the table's gaps (1, 2, 5, 10, 20).
    k < 1 uses k = 1. For k > 20 it extrapolates the 10->20 slope, never
    below the k = 20 value: the table stops at 1 s and the error keeps
    growing. With ``monotone`` the row is made non-decreasing in k first, so
    a small-N dip (aerial "hip" at k = 20, N = 34) never lowers R."""
    key = n if isinstance(n, str) else layout_key(int(n), harness)
    if key is None:
        return None
    row = R_TABLE[scene][key]
    ks = np.array(R_TABLE_KS, np.float64)
    vals = np.array([row[int(kk)][0 if q == "p50" else 1] for kk in R_TABLE_KS])
    if monotone:
        vals = np.maximum.accumulate(vals)
    k = max(1.0, float(k))
    if k <= ks[-1]:
        return float(np.interp(k, ks, vals))
    slope = max(0.0, (vals[-1] - vals[-2]) / (ks[-1] - ks[-2]))
    return float(vals[-1] + slope * (k - ks[-1]))


def marker_sigma(n: int, k: float, H: float, **kw) -> Optional[float]:
    """Per-axis sigma (px) of the marker centroid measurement: the Rayleigh
    sigma from the table median, ``p50 / 1.1774 x H``. Example (02 §3.2):
    H = 175 px (tracker space at imgsz 1280), n = 4, k <= 5 -> 6-9 px."""
    e = marker_error(n, k, q="p50", **kw)
    return None if e is None else e / RAYLEIGH_MEDIAN * float(H)


def marker_R(n: int, k: float, H: float, **kw) -> Optional[float]:
    """Kalman measurement variance (px^2) for a marker-fed update: sigma^2.
    Compare ``TRACKER_MEASUREMENT_NOISE = 2.0`` for a YOLO skeleton: markers are
    a moderate-R source (about 20-110x YOLO's), not a tiny-R one."""
    s = marker_sigma(n, k, H, **kw)
    return None if s is None else s * s


def hold_error(k: float, *, scene: str = DEFAULT_SCENE, q: str = "p50") -> float:
    """Today's baseline (hold the last position) at gap k, ÷ H."""
    return marker_error("hold", k, scene=scene, q=q)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. Appearance physics + renderer
# ---------------------------------------------------------------------------

PERSON_HEIGHT_CM = 170.0


def mm_per_px(distance_m: float, focal_mm: float = 8.0, pixel_um: float = 2.9) -> float:
    """Object-plane pixel footprint: 0.3625·D mm/px for the 8 mm lens on the
    IMX664's 2.9 µm pixels (7.25 mm/px at 20 m), 02 §1.6."""
    return pixel_um * 1e-3 / focal_mm * distance_m * 1e3


def marker_diameter_px(marker_cm: float, person_height_px: float,
                       person_height_cm: float = PERSON_HEIGHT_CM) -> float:
    """Expected marker diameter from the dancer's pixel height (§2.2 step 3):
    ``d ~ marker_cm / 170 x H`` (before 1-3 px of bloom)."""
    return float(marker_cm) / person_height_cm * float(person_height_px)


def streak_length_px(speed_px_per_frame: float, exposure_us: float, fps: float) -> float:
    """Motion during the exposure: ``speed x exposure x fps`` px. A wrist at
    2-5 m/s, 20 m away, gives 7-17 px at 25 ms."""
    return float(speed_px_per_frame) * float(exposure_us) * 1e-6 * float(fps)


def dwell_fraction(d: float, L: float) -> float:
    """Mean share of the exposure a marker of size d spends on each pixel it
    crosses while moving L px: ``d / (d + L)`` (02 §1.6). The renderer below is
    exact; on a long streak's centre line it gives ~d/L."""
    return float(d) / max(1e-9, float(d) + float(L))


def streak_level(static_level: float, d: float, L: float) -> float:
    """Expected streak level (DN, before clipping) of a marker whose static
    image would read ``static_level``. It can fall below the threshold when
    the illuminator is off-axis (02 §1.6)."""
    return float(static_level) * dwell_fraction(d, L)


def _coverage_time(qx: np.ndarray, qy: np.ndarray, a: np.ndarray, b: np.ndarray,
                   r: float) -> np.ndarray:
    """Fraction of t in [0,1] for which the disc of radius r centred at
    a + t(b - a) covers point q. Exact: |a - q + t u|^2 <= r^2 is a quadratic
    in t."""
    ux, uy = float(b[0] - a[0]), float(b[1] - a[1])
    wx, wy = a[0] - qx, a[1] - qy
    A = ux * ux + uy * uy
    if A < 1e-12:
        return ((wx * wx + wy * wy) <= r * r).astype(np.float64)
    B = 2.0 * (wx * ux + wy * uy)
    C = wx * wx + wy * wy - r * r
    disc = B * B - 4.0 * A * C
    ok = disc > 0
    sq = np.sqrt(np.where(ok, disc, 0.0))
    t0 = np.clip((-B - sq) / (2.0 * A), 0.0, 1.0)
    t1 = np.clip((-B + sq) / (2.0 * A), 0.0, 1.0)
    return np.where(ok, np.maximum(0.0, t1 - t0), 0.0)


def _gauss_kernel(sigma: float) -> np.ndarray:
    rad = max(1, int(math.ceil(3.0 * sigma)))
    x = np.arange(-rad, rad + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return img
    k = _gauss_kernel(sigma)
    pad = len(k) // 2
    p = np.pad(img, pad, mode="constant")
    tmp = np.apply_along_axis(lambda r: np.convolve(r, k, mode="valid"), 1, p)
    return np.apply_along_axis(lambda c: np.convolve(c, k, mode="valid"), 0, tmp)


def render_marker(gray: np.ndarray, center: Sequence[float], *, diameter: float,
                  level: float, velocity: Sequence[float] = (0.0, 0.0),
                  psf_sigma: float = 0.0, supersample: int = 4,
                  offset: Sequence[float] = (0.0, 0.0)) -> Optional[Tuple[int, int, int, int]]:
    """Inject one marker into a uint8 gray frame, in place.

    The marker is a disc of ``diameter`` px whose static image reads
    ``level`` DN. It can be far above 255: the retro return of an on-axis
    illuminator is ~1,000x white paper. During the exposure it moves by
    ``velocity`` px (the streak, centred on ``center``). For each pixel the
    renderer computes the exact share of the exposure the disc covers it,
    supersampled for anti-aliasing. The result is
    ``bg x (1 - share) + level x share``, an optional Gaussian PSF (bloom),
    clipped to 255. ``offset`` is subtracted from ``center`` (pass the ROI
    origin when ``gray`` is an ROI crop). Returns the touched bbox
    (x0, y0, x1, y1), inclusive, or None when it falls outside the frame."""
    if gray.dtype != np.uint8 or gray.ndim != 2:
        raise ValueError("render_marker expects a 2-D uint8 frame")
    cx = float(center[0]) - float(offset[0])
    cy = float(center[1]) - float(offset[1])
    vx, vy = float(velocity[0]), float(velocity[1])
    a = np.array([cx - vx / 2.0, cy - vy / 2.0])
    b = np.array([cx + vx / 2.0, cy + vy / 2.0])
    r = max(0.25, float(diameter) / 2.0)
    margin = r + 1.0 + (3.0 * psf_sigma if psf_sigma > 0 else 0.0)
    x0 = int(math.floor(min(a[0], b[0]) - margin)); x1 = int(math.ceil(max(a[0], b[0]) + margin))
    y0 = int(math.floor(min(a[1], b[1]) - margin)); y1 = int(math.ceil(max(a[1], b[1]) + margin))
    h, w = gray.shape
    if x1 < 0 or y1 < 0 or x0 >= w or y0 >= h:
        return None
    ys = np.arange(y0, y1 + 1, dtype=np.float64)
    xs = np.arange(x0, x1 + 1, dtype=np.float64)
    gx, gy = np.meshgrid(xs, ys)
    ss = max(1, int(supersample))
    offs = (np.arange(ss) + 0.5) / ss - 0.5
    share = np.zeros_like(gx)
    for ox in offs:
        for oy in offs:
            share += _coverage_time(gx + ox, gy + oy, a, b, r)
    share /= ss * ss
    if psf_sigma > 0:
        share = _blur(share, psf_sigma)
    # clip the patch to the frame
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(w - 1, x1), min(h - 1, y1)
    sub = share[cy0 - y0:cy1 - y0 + 1, cx0 - x0:cx1 - x0 + 1]
    bg = gray[cy0:cy1 + 1, cx0:cx1 + 1].astype(np.float64)
    out = bg * (1.0 - np.clip(sub, 0.0, 1.0)) + float(level) * sub
    touched = sub > 1e-6
    gray[cy0:cy1 + 1, cx0:cx1 + 1] = np.where(
        touched, np.clip(np.rint(out), 0, 255), bg).astype(np.uint8)
    return cx0, cy0, cx1, cy1


@dataclass
class SyntheticMarker:
    """One marker to render: position (frame px), diameter, static level,
    per-exposure displacement (streak), and a label for ground truth."""
    x: float
    y: float
    diameter: float
    level: float
    vx: float = 0.0
    vy: float = 0.0
    track_id: int = -1
    slot: int = -1


def render_markers(gray: np.ndarray, markers: Iterable[SyntheticMarker], *,
                   psf_sigma: float = 0.0, offset: Sequence[float] = (0.0, 0.0),
                   supersample: int = 4) -> List[Tuple[int, int, int, int]]:
    """Render many markers (in place); returns the touched bboxes."""
    boxes = []
    for m in markers:
        bb = render_marker(gray, (m.x, m.y), diameter=m.diameter, level=m.level,
                           velocity=(m.vx, m.vy), psf_sigma=psf_sigma,
                           supersample=supersample, offset=offset)
        if bb is not None:
            boxes.append(bb)
    return boxes
