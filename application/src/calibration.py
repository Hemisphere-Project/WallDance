"""
Go-Live scene calibration (P2 of docs/ROBUSTNESS_PLAN.md).

A WallDance operator should rig the camera, aim the IR, press **one calibration
button**, and get a scene-appropriate config — without per-venue knob tuning.
This module is the measurement core behind that button: it collects a short
window of samples (with YOLO forced on, working live OR during recording
playback) and computes the biggest manual knobs from what it actually sees:

  * ``PERSON_HEIGHT_PX`` + min/max ratios — from the distribution of YOLO
    detection heights (median, with robust percentiles for the spread).
  * MOG2 base ``varThreshold`` — from the measured background-noise sigma, so
    noise up to N-sigma stays background instead of being tuned by hand.
  * A report — exposure stability (brightness sigma/mu) and achieved FPS.

It is deliberately explicit and logged: ``compute()`` returns a
:class:`CalibrationResult` that the app applies and shows to the operator, who
confirms before it is saved to the project.  No silent auto-tuning.

The class is pure logic (numpy + cv2 for the downscale only) with no GUI,
camera or tracker dependencies, so it is unit-testable by feeding synthetic
samples — see ``tests/test_calibration.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Iterable, Optional

import cv2
import numpy as np

from config import (
    AUTOCAL_WINDOW_FRAMES,
    AUTOCAL_MIN_HEIGHT_SAMPLES,
    AUTOCAL_HEIGHT_PCTL_LO,
    AUTOCAL_HEIGHT_PCTL_HI,
    AUTOCAL_MIN_RATIO_BOUNDS,
    AUTOCAL_MAX_RATIO_BOUNDS,
    AUTOCAL_VARTHRESH_NSIGMA,
    AUTOCAL_VARTHRESH_BOUNDS,
    AUTOCAL_NOISE_SCALE,
    AUTOCAL_EXPOSURE_STABLE_CV,
)

# Hard bounds for PERSON_HEIGHT_PX (mirrors the GUI slider range, config.py).
_HEIGHT_MIN_PX = 20
_HEIGHT_MAX_PX = 800


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class CalState(Enum):
    IDLE = auto()
    COLLECTING = auto()
    DONE = auto()


@dataclass
class CalibrationResult:
    """Outcome of one calibration window.  ``*_ok`` flags say what is trustworthy."""

    frames: int = 0

    # Person size
    height_samples: int = 0
    height_ok: bool = False
    person_height_px: Optional[int] = None
    min_ratio: Optional[float] = None
    max_ratio: Optional[float] = None

    # MOG2 background noise → varThreshold
    noise_ok: bool = False
    noise_sigma: float = 0.0
    var_threshold: Optional[float] = None

    # Report-only
    brightness_mean: float = 0.0
    brightness_cv: float = 0.0
    exposure_stable: bool = False
    fps_achieved: float = 0.0

    def log_line(self) -> str:
        """Single structured line for the console log."""
        h = f"{self.person_height_px}px" if self.height_ok else "n/a"
        r = (f"[{self.min_ratio:.2f},{self.max_ratio:.2f}]"
             if self.height_ok else "[--]")
        vt = f"{self.var_threshold:.0f}" if self.noise_ok else "n/a"
        return (f"[Calibrate] frames={self.frames} height={h} ratios={r} "
                f"(n={self.height_samples}) noise_sigma={self.noise_sigma:.2f} "
                f"varThreshold={vt} brightness={self.brightness_mean:.0f} "
                f"cv={self.brightness_cv:.3f} exposure="
                f"{'stable' if self.exposure_stable else 'drifting'} "
                f"fps={self.fps_achieved:.1f}")

    def summary(self) -> str:
        """Multi-line human summary for the result dialog."""
        lines = []
        if self.height_ok:
            lines.append(f"Person height: {self.person_height_px} px  "
                         f"(was measured from {self.height_samples} detections)")
            lines.append(f"Height ratios: min {self.min_ratio:.2f}  "
                         f"max {self.max_ratio:.2f}")
        else:
            lines.append(f"Person height: NOT measured "
                         f"(only {self.height_samples} detections; "
                         f"need {AUTOCAL_MIN_HEIGHT_SAMPLES}) - kept current value")
        if self.noise_ok:
            lines.append(f"MOG2 varThreshold: {self.var_threshold:.0f}  "
                         f"(background noise sigma {self.noise_sigma:.2f})")
        else:
            lines.append("MOG2 varThreshold: NOT measured - kept current value")
        lines.append(f"Brightness: {self.brightness_mean:.0f}  "
                     f"({'stable' if self.exposure_stable else 'still drifting'}, "
                     f"cv {self.brightness_cv:.3f})")
        lines.append(f"Achieved inference FPS: {self.fps_achieved:.1f}")
        return "\n".join(lines)


class SceneCalibrator:
    """Collect-then-compute scene calibration over a fixed frame window.

    Drive it from the processing loop:

        cal.start()
        # each processed frame, while cal.is_collecting:
        cal.feed(gray, [t.bbox[3] for t in tracked], fps_sample, now)
        if cal.ready:
            result = cal.compute()
            # apply + log + offer save

    ``feed`` is cheap: a small downscale + one Welford update + a few appends.
    """

    def __init__(self, window_frames: int = AUTOCAL_WINDOW_FRAMES):
        self.window_frames = int(window_frames)
        self._state = CalState.IDLE
        self._reset_accumulators()

    def _reset_accumulators(self) -> None:
        self._frames = 0
        self._heights: list[float] = []
        self._brightness: list[float] = []
        self._fps: list[float] = []
        # Per-pixel Welford accumulators for temporal noise sigma.
        self._noise_n = 0
        self._noise_mean: Optional[np.ndarray] = None
        self._noise_m2: Optional[np.ndarray] = None

    # ------------------------------------------------------------------
    # Lifecycle / status
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Begin a fresh collection window."""
        self._reset_accumulators()
        self._state = CalState.COLLECTING

    def cancel(self) -> None:
        self._state = CalState.IDLE

    @property
    def is_collecting(self) -> bool:
        return self._state == CalState.COLLECTING

    @property
    def ready(self) -> bool:
        """True once enough frames have been collected to compute."""
        return self._state == CalState.COLLECTING and self._frames >= self.window_frames

    @property
    def frames(self) -> int:
        return self._frames

    def progress(self) -> float:
        """Fraction of the window collected, 0..1."""
        if self.window_frames <= 0:
            return 1.0
        return min(1.0, self._frames / self.window_frames)

    # ------------------------------------------------------------------
    # Intake
    # ------------------------------------------------------------------
    def feed(self, gray: np.ndarray, track_heights: Iterable[float],
             fps_sample: float, now: float) -> None:
        """Add one processed frame's samples to the window.

        gray:          2D uint8 frame (any resolution; downscaled internally).
        track_heights: bbox heights (px) of the confirmed tracks this frame.
        fps_sample:    achieved inference FPS for this frame (1000/process_wall_ms).
        now:           wall-clock timestamp (unused for gating; reserved).
        """
        if self._state != CalState.COLLECTING:
            return

        for h in track_heights:
            if h and h > 0:
                self._heights.append(float(h))

        if gray is not None and gray.size:
            small = self._downscale(gray)
            self._brightness.append(float(small.mean()))
            self._accumulate_noise(small)

        if fps_sample and fps_sample > 0:
            self._fps.append(float(fps_sample))

        self._frames += 1

    @staticmethod
    def _downscale(gray: np.ndarray) -> np.ndarray:
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        s = AUTOCAL_NOISE_SCALE
        if s < 1.0:
            gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        return gray

    def _accumulate_noise(self, small: np.ndarray) -> None:
        x = small.astype(np.float32)
        if self._noise_mean is None or self._noise_mean.shape != x.shape:
            # First frame (or a resolution change mid-window) → (re)start.
            self._noise_n = 1
            self._noise_mean = x.copy()
            self._noise_m2 = np.zeros_like(x)
            return
        self._noise_n += 1
        delta = x - self._noise_mean
        self._noise_mean += delta / self._noise_n
        self._noise_m2 += delta * (x - self._noise_mean)

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    def compute(self) -> CalibrationResult:
        """Finalise the window into a :class:`CalibrationResult` and go DONE."""
        self._state = CalState.DONE
        res = CalibrationResult(frames=self._frames)

        # --- person height + ratios ---
        heights = np.asarray(self._heights, dtype=np.float64)
        res.height_samples = int(heights.size)
        if heights.size >= AUTOCAL_MIN_HEIGHT_SAMPLES:
            median_h = float(np.median(heights))
            if median_h > 0:
                res.height_ok = True
                res.person_height_px = int(round(
                    _clamp(median_h, _HEIGHT_MIN_PX, _HEIGHT_MAX_PX)))
                lo = float(np.percentile(heights, AUTOCAL_HEIGHT_PCTL_LO))
                hi = float(np.percentile(heights, AUTOCAL_HEIGHT_PCTL_HI))
                res.min_ratio = round(_clamp(lo / median_h, *AUTOCAL_MIN_RATIO_BOUNDS), 3)
                res.max_ratio = round(_clamp(hi / median_h, *AUTOCAL_MAX_RATIO_BOUNDS), 3)

        # --- background noise sigma → varThreshold ---
        if self._noise_m2 is not None and self._noise_n >= 2:
            var = self._noise_m2 / (self._noise_n - 1)
            std = np.sqrt(np.maximum(var, 0.0))
            # Median across pixels: moving dancers are a minority, so the median
            # std is the static-background sensor/scene noise floor.
            sigma = float(np.median(std))
            res.noise_ok = True
            res.noise_sigma = sigma
            res.var_threshold = round(_clamp(
                (AUTOCAL_VARTHRESH_NSIGMA * sigma) ** 2, *AUTOCAL_VARTHRESH_BOUNDS), 1)

        # --- report: exposure stability + FPS ---
        if self._brightness:
            b = np.asarray(self._brightness, dtype=np.float64)
            res.brightness_mean = float(b.mean())
            res.brightness_cv = float(b.std() / b.mean()) if b.mean() > 1e-6 else 0.0
            res.exposure_stable = res.brightness_cv < AUTOCAL_EXPOSURE_STABLE_CV
        if self._fps:
            res.fps_achieved = float(np.median(self._fps))

        return res
