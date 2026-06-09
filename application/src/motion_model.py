"""Unified motion model (ROADMAP P3 §5, Stage 1).

One motion source for the whole detection stack, replacing the two
full-frame MOG2 models that run today (``bridge`` @0.001 + ``crossval``
@0.005, differing only in learn rate -- ROADMAP Bug #2).

Two questions, one model:

* **"is there a silhouette here?"** -> a single **slow** MOG2 (a paused
  dancer must stay foreground for seconds; used for bridging + cold
  detection).
* **"is it moving *right now*?"** -> **frame differencing** (no learning
  rate, inherently fast-adapting; used for ghost rejection).  This is what
  lets us drop the second, fast MOG2 entirely.

Frozen surface (what Stage 2 wiring and P2 calibration depend on):

    feed(gray_fixed)                  # once/frame
    reset()
    noise_sigma() -> float            # temporal noise of the MOG2-input gray
    foreground_blob(roi) -> (blob, r) # MOG2 silhouette in an ROI (bridging)
    foreground_blobs(person_height)   # global MOG2 silhouettes (cold detect)
    foreground_ratio(roi) -> float    # MOG2 fg fraction in an ROI
    recent_motion(roi) -> float       # frame-diff fraction ("moving now?")
    recent_motion_blob(roi) -> (blob, r)

**Fixed-gray contract (ROADMAP Bug #1).**  ``feed`` expects a gray that is
*decoupled from the display CLAHE/gamma path*.  Per-frame adaptive CLAHE
amplifies noise differently each frame and fights MOG2's stationary-background
assumption, so the model never applies it.  An optional *fixed* (frame-
independent) gamma is available for dark IR scenes; it is cached once and so
introduces no per-frame jitter.

Stage 1 is intentionally a thin composition over the proven
``MotionDetector`` primitives -- nothing imports this module yet.  Stage 3
inlines ``MotionDetector`` into this class and removes the wrapper.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

from config import (
    MOTION_BRIDGE_MOG2_SCALE,
    MOTION_BRIDGE_MOG2_VAR_THRESHOLD,
    MOTION_BRIDGE_MOG2_LEARN_RATE,
)
from motion_detector import MotionBlob, MotionDetector

Roi = Tuple[float, float, float, float]  # (x, y, w, h) in original-frame coords


class MotionModel:
    """Single MOG2 (slow, silhouette) + frame-diff (fast, "moving now")."""

    def __init__(
        self,
        *,
        scale: float = MOTION_BRIDGE_MOG2_SCALE,
        var_threshold: float = MOTION_BRIDGE_MOG2_VAR_THRESHOLD,
        learn_rate: float = MOTION_BRIDGE_MOG2_LEARN_RATE,
        fixed_gamma: float = 1.0,
    ):
        self._det = MotionDetector()
        self._det.set_scale(scale)
        self._det.set_learn_rate(learn_rate)
        self._det.set_var_threshold(var_threshold)
        self._scale = self._det._scale
        self._fixed_gamma = float(fixed_gamma)
        self._gamma_lut: Optional[np.ndarray] = None
        self._build_gamma_lut()
        # Welford accumulators for the temporal-noise estimate, measured on the
        # exact MOG2-input gray (matches calibration.SceneCalibrator's
        # definition: median per-pixel temporal std).
        self._noise_n = 0
        self._noise_mean: Optional[np.ndarray] = None
        self._noise_m2: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    # Fixed-gray preprocessing (Bug #1)
    # ------------------------------------------------------------------
    def _build_gamma_lut(self) -> None:
        if self._fixed_gamma == 1.0:
            self._gamma_lut = None
            return
        inv = 1.0 / self._fixed_gamma
        self._gamma_lut = np.array(
            [((i / 255.0) ** inv) * 255 for i in range(256)], dtype=np.uint8)

    def _apply_fixed_gray(self, gray: np.ndarray) -> np.ndarray:
        """Frame-independent normalization only -- never adaptive CLAHE."""
        if self._gamma_lut is not None:
            return cv2.LUT(gray, self._gamma_lut)
        return gray

    # ------------------------------------------------------------------
    # Frozen surface
    # ------------------------------------------------------------------
    def feed(self, gray_fixed: np.ndarray) -> None:
        """Update the model from one fixed (non-display-CLAHE) gray frame."""
        g = self._apply_fixed_gray(gray_fixed)
        small, brightness = MotionDetector.preprocess(g, self._scale)
        self._accumulate_noise(small)
        self._det.feed_preprocessed(small, brightness, raw_gray=g)

    def reset(self) -> None:
        self._det.reset()
        self._noise_n = 0
        self._noise_mean = None
        self._noise_m2 = None

    def noise_sigma(self) -> float:
        """Median per-pixel temporal std of the MOG2-input gray (0 if <2 frames).

        Same definition P2 calibration uses, so the two agree and varThreshold
        can be reasoned about consistently across calibration and runtime.
        """
        if self._noise_m2 is None or self._noise_n < 2:
            return 0.0
        var = self._noise_m2 / (self._noise_n - 1)
        return float(np.median(np.sqrt(np.maximum(var, 0.0))))

    # ---- MOG2 silhouette: bridging + cold detection -------------------
    def foreground_blobs(self, person_height: int, **kw) -> List[MotionBlob]:
        """Global MOG2 silhouette blobs (motion-first cold detection / bridge)."""
        return self._det.detect(person_height, **kw)

    def foreground_blob(
        self,
        roi: Roi,
        *,
        target_centroid: Optional[np.ndarray] = None,
        min_motion_ratio: float = 0.02,
        include_shadows: bool = False,
    ) -> Tuple[Optional[MotionBlob], float]:
        """Track-conditioned MOG2 silhouette blob inside an ROI (bridging)."""
        x, y, w, h = roi
        return self._det.extract_local_motion_blob(
            x, y, w, h,
            target_centroid=target_centroid,
            min_motion_ratio=min_motion_ratio,
            include_shadows=include_shadows,
        )

    def foreground_ratio(self, roi: Roi, **kw) -> float:
        """Fraction of MOG2 foreground inside an ROI (crossval / presence)."""
        x, y, w, h = roi
        return self._det.motion_ratio_in_bbox(x, y, w, h, **kw)

    # ---- frame-diff: "moving right now?" ------------------------------
    def recent_motion(
        self,
        roi: Roi,
        *,
        threshold: int = 15,
    ) -> float:
        """Frame-diff foreground fraction in an ROI -- the ghost-rejection signal.

        Independent of the MOG2 learning rate, so it answers "moving now?"
        even when a slow MOG2 has absorbed (or not yet learned) the scene.
        """
        x, y, w, h = roi
        # min_ratio=0.0 -> the blob path is skipped for fully-static ROIs
        # (fg_count==0 early-returns); we only need the ratio here.
        _blob, ratio = self._det.frame_diff_blob_in_bbox(
            x, y, w, h, threshold=threshold, min_ratio=0.0)
        return ratio

    def recent_motion_blob(
        self,
        roi: Roi,
        *,
        target_centroid: Optional[np.ndarray] = None,
        threshold: int = 15,
        min_ratio: float = 0.03,
    ) -> Tuple[Optional[MotionBlob], float]:
        """Frame-diff blob (position measurement when MOG2 has absorbed the dancer)."""
        x, y, w, h = roi
        return self._det.frame_diff_blob_in_bbox(
            x, y, w, h,
            target_centroid=target_centroid,
            threshold=threshold,
            min_ratio=min_ratio,
        )

    # ------------------------------------------------------------------
    # State / tuning pass-throughs
    # ------------------------------------------------------------------
    @property
    def frame_count(self) -> int:
        return self._det.frame_count

    @property
    def brightness(self) -> float:
        return self._det.last_brightness

    @property
    def has_model(self) -> bool:
        return self._det.has_mask

    @property
    def detector(self) -> MotionDetector:
        """The underlying MotionDetector.

        Stage-2 compatibility accessor: the pipeline crossval tree and the
        tracker bridge still consume a MotionDetector directly.  They migrate
        to the clean MotionModel surface (and this accessor is removed) in
        Stage 3.
        """
        return self._det

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def clean_mask(self) -> Optional[np.ndarray]:
        """Morphologically-cleaned MOG2 foreground (exclusion-mask building)."""
        return self._det._clean_mask

    def set_scale(self, scale: float) -> None:
        self._det.set_scale(scale)
        self._scale = self._det._scale
        # Scale change resets MOG2; noise stats are now stale.
        self._noise_n = 0
        self._noise_mean = None
        self._noise_m2 = None

    def set_learn_rate(self, rate: float) -> None:
        self._det.set_learn_rate(rate)

    def set_var_threshold(self, base: float) -> None:
        self._det.set_var_threshold(base)

    def get_var_threshold(self) -> float:
        return float(self._det._var_threshold)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _accumulate_noise(self, small: np.ndarray) -> None:
        x = small.astype(np.float32)
        if self._noise_mean is None or self._noise_mean.shape != x.shape:
            self._noise_n = 1
            self._noise_mean = x.copy()
            self._noise_m2 = np.zeros_like(x)
            return
        self._noise_n += 1
        delta = x - self._noise_mean
        self._noise_mean += delta / self._noise_n
        self._noise_m2 += delta * (x - self._noise_mean)
