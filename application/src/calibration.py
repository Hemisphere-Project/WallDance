"""
Go-Live scene calibration (P2 of docs/ROADMAP.md).

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
    AUTOCAL_NOISE_SCALE,
    AUTOCAL_EXPOSURE_STABLE_CV,
    AUTOCAL_VARTHRESH_CANDIDATES,
    AUTOCAL_FP_TARGET,
    AUTOCAL_FP_GRID,
    AUTOCAL_EXCL_GRID,
    AUTOCAL_EXCL_MOTION_FRAC,
    AUTOCAL_EXCL_MOTION_FREQ,
    AUTOCAL_EXCL_SKEL_FREQ,
    AUTOCAL_EXCL_MIN_FRAMES,
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

    # MOG2 varThreshold — chosen by empirical background false-positive sweep.
    var_ok: bool = False
    var_threshold: Optional[float] = None
    var_fp_rate: float = 0.0          # background FP rate of the chosen threshold
    var_saturated: bool = False       # True if no candidate met the FP target
    # Diagnostic only (not used to set varThreshold): temporal noise of the
    # MOG2-input gray.  High σ on a near-black scene = CLAHE-amplified noise.
    noise_sigma: float = 0.0

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
        if self.var_ok:
            vt = (f"{self.var_threshold:.0f}(fp={self.var_fp_rate*100:.2f}%"
                  f"{',SAT' if self.var_saturated else ''})")
        else:
            vt = "n/a"
        return (f"[Calibrate] frames={self.frames} height={h} ratios={r} "
                f"(n={self.height_samples}) varThreshold={vt} "
                f"noise_sigma={self.noise_sigma:.2f} brightness={self.brightness_mean:.0f} "
                f"cv={self.brightness_cv:.3f} exposure="
                f"{'stable' if self.exposure_stable else 'drifting'} "
                f"fps={self.fps_achieved:.1f}")

    def summary(self) -> str:
        """Multi-line human summary for the result dialog."""
        lines = []
        if self.height_ok:
            lines.append(f"Person height: {self.person_height_px} px  "
                         f"(measured from {self.height_samples} detections)")
            lines.append(f"Height ratios: min {self.min_ratio:.2f}  "
                         f"max {self.max_ratio:.2f}")
        else:
            lines.append(f"Person height: NOT measured "
                         f"(only {self.height_samples} detections; "
                         f"need {AUTOCAL_MIN_HEIGHT_SAMPLES}) - kept current value")
        if self.var_ok and not self.var_saturated:
            lines.append(f"MOG2 varThreshold: {self.var_threshold:.0f}  "
                         f"(background false-positives {self.var_fp_rate*100:.2f}%)")
        elif self.var_ok and self.var_saturated:
            lines.append(f"MOG2 varThreshold: {self.var_threshold:.0f} (max)  "
                         f"- background still {self.var_fp_rate*100:.2f}% noisy: "
                         f"the scene is too noisy for MOG2 (raise IR / decouple CLAHE)")
        else:
            lines.append("MOG2 varThreshold: NOT measured - kept current value")
        lines.append(f"Brightness: {self.brightness_mean:.0f}  "
                     f"({'stable' if self.exposure_stable else 'still drifting'}, "
                     f"cv {self.brightness_cv:.3f}; noise sigma {self.noise_sigma:.2f})")
        lines.append(f"Achieved inference FPS: {self.fps_achieved:.1f}")
        return "\n".join(lines)


@dataclass
class ExclusionResult:
    """Outcome of building the auto exclusion mask."""
    grid: tuple = (0, 0)
    cells: list = field(default_factory=list)   # excluded (col, row) pairs
    frames: int = 0

    @property
    def count(self) -> int:
        return len(self.cells)

    def summary_line(self) -> str:
        gx, gy = self.grid
        if self.frames < AUTOCAL_EXCL_MIN_FRAMES:
            return "Exclusion mask: not built (too few frames)"
        if not self.cells:
            return f"Exclusion mask: none (no persistent ghost cells in {gx}x{gy} grid)"
        return f"Exclusion mask: {self.count} ghost cell(s) masked ({gx}x{gy} grid)"


class ExclusionMaskBuilder:
    """Builds and holds the auto exclusion mask (P1.4).

    A normalized ``grid`` over the frame.  During calibration, ``observe`` is
    called once per processed frame with the MOG2 foreground mask and the
    normalized positions of the *kept* skeletons.  ``build`` then marks cells
    that move often but ~never hold a skeleton as excluded.  ``excluded`` is the
    runtime query used to reject ghost detections.

    Pure grid logic (numpy + a cv2.resize) — no camera / tracker / transform
    knowledge; the caller supplies already-normalized [0,1] coordinates.
    """

    def __init__(self, grid=AUTOCAL_EXCL_GRID,
                 motion_frac: float = AUTOCAL_EXCL_MOTION_FRAC,
                 motion_freq: float = AUTOCAL_EXCL_MOTION_FREQ,
                 skel_freq: float = AUTOCAL_EXCL_SKEL_FREQ,
                 min_frames: int = AUTOCAL_EXCL_MIN_FRAMES):
        self.gx, self.gy = int(grid[0]), int(grid[1])
        self.motion_frac = float(motion_frac)
        self.motion_freq = float(motion_freq)
        self.skel_freq = float(skel_freq)
        self.min_frames = int(min_frames)
        self._motion = np.zeros((self.gy, self.gx), dtype=np.float64)
        self._skel = np.zeros((self.gy, self.gx), dtype=np.float64)
        self._frames = 0
        self._collecting = False
        self._cells: set = set()   # active excluded (col, row)

    @property
    def collecting(self) -> bool:
        return self._collecting

    @property
    def active(self) -> bool:
        return bool(self._cells)

    def start(self) -> None:
        self._motion[:] = 0.0
        self._skel[:] = 0.0
        self._frames = 0
        self._collecting = True

    def cancel(self) -> None:
        """Stop collecting without rebuilding (keeps any existing mask)."""
        self._collecting = False

    def observe(self, clean_mask: Optional[np.ndarray], skel_points) -> None:
        """Accumulate one frame: motion tiles + cells holding a kept skeleton."""
        if not self._collecting:
            return
        if clean_mask is not None and clean_mask.size:
            fg = (clean_mask == 255).astype(np.float32)
            tiles = cv2.resize(fg, (self.gx, self.gy), interpolation=cv2.INTER_AREA)
            self._motion += (tiles >= self.motion_frac)
        seen = set()
        for nx, ny in skel_points:
            if 0.0 <= nx < 1.0 and 0.0 <= ny < 1.0:
                cell = (int(ny * self.gy), int(nx * self.gx))
                if cell not in seen:
                    self._skel[cell] += 1.0
                    seen.add(cell)
        self._frames += 1

    def build(self) -> ExclusionResult:
        """Finalise: cells with frequent motion but ~no skeleton → excluded."""
        self._collecting = False
        cells: set = set()
        if self._frames >= self.min_frames:
            mfreq = self._motion / max(1, self._frames)
            sfreq = self._skel / max(1, self._frames)
            rows, cols = np.where((mfreq >= self.motion_freq) & (sfreq <= self.skel_freq))
            cells = {(int(c), int(r)) for c, r in zip(cols, rows)}
        self._cells = cells
        return ExclusionResult(grid=(self.gx, self.gy),
                               cells=sorted(cells), frames=self._frames)

    def excluded(self, nx: float, ny: float) -> bool:
        """True if the normalized position lands in an excluded cell."""
        if not self._cells or not (0.0 <= nx < 1.0 and 0.0 <= ny < 1.0):
            return False
        return (int(nx * self.gx), int(ny * self.gy)) in self._cells

    def set_cells(self, grid, cells) -> None:
        """Restore a persisted mask (e.g. on project load)."""
        self.gx, self.gy = int(grid[0]), int(grid[1])
        self._cells = {(int(c[0]), int(c[1])) for c in cells}
        self._collecting = False

    def get_cells(self) -> tuple:
        """(grid, sorted cells) for persistence."""
        return ((self.gx, self.gy), sorted(self._cells))

    def clear(self) -> None:
        self._cells = set()
        self._collecting = False


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
        # Per-pixel Welford accumulators for the temporal-noise diagnostic.
        self._noise_n = 0
        self._noise_mean: Optional[np.ndarray] = None
        self._noise_m2: Optional[np.ndarray] = None
        # Empirical varThreshold sweep: one MOG2 model per candidate + its
        # per-frame background-FP samples (median grid-tile foreground fraction).
        self._var_candidates: list[float] = [float(v) for v in AUTOCAL_VARTHRESH_CANDIDATES]
        self._var_models: list = []
        self._var_fp: list[list[float]] = [[] for _ in self._var_candidates]

    # ------------------------------------------------------------------
    # Lifecycle / status
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Begin a fresh collection window."""
        self._reset_accumulators()
        # One independent MOG2 model per candidate varThreshold.  history =
        # window so each adapts its background within the collection window.
        hist = max(2, self.window_frames)
        self._var_models = [
            cv2.createBackgroundSubtractorMOG2(
                history=hist, varThreshold=v, detectShadows=True)
            for v in self._var_candidates
        ]
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
    def feed(self, noise_gray: np.ndarray, track_heights: Iterable[float],
             fps_sample: float, now: float,
             brightness: Optional[float] = None) -> None:
        """Add one processed frame's samples to the window.

        noise_gray:    2D uint8 frame the background model consumes (any
                       resolution; downscaled internally) — its temporal sigma
                       drives varThreshold.  Pass the *MOG2 input* gray, not the
                       raw frame, so the measured noise matches what MOG2 fights.
        track_heights: bbox heights (px) of the confirmed tracks this frame.
        fps_sample:    achieved inference FPS for this frame (1000/process_wall_ms).
        now:           wall-clock timestamp (unused for gating; reserved).
        brightness:    raw-scene mean luma for the exposure report.  If None,
                       falls back to the mean of ``noise_gray`` (test convenience).
        """
        if self._state != CalState.COLLECTING:
            return

        for h in track_heights:
            if h and h > 0:
                self._heights.append(float(h))

        if noise_gray is not None and noise_gray.size:
            small = self._downscale(noise_gray)
            self._accumulate_noise(small)
            self._score_var_candidates(small)
            self._brightness.append(
                float(brightness) if brightness is not None else float(small.mean()))
        elif brightness is not None:
            self._brightness.append(float(brightness))

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

    def _score_var_candidates(self, small: np.ndarray) -> None:
        """Run each candidate MOG2 model on this frame and record its background
        false-positive level: the median grid-tile foreground fraction.

        The median over a grid is robust to the dancer minority — tiles with a
        dancer are outliers, so the median tile reflects the *background*.  A
        too-low varThreshold lights up every tile (noise) → high median; a
        good one leaves the background quiet → median ~0.
        """
        gx, gy = AUTOCAL_FP_GRID
        for model, fp_list in zip(self._var_models, self._var_fp):
            mask = model.apply(small)              # learningRate auto (1/history)
            fg = (mask == 255).astype(np.float32)  # hard foreground only
            # INTER_AREA resize of a 0/1 mask to the grid = mean (fraction) per tile.
            tiles = cv2.resize(fg, (gx, gy), interpolation=cv2.INTER_AREA)
            fp_list.append(float(np.median(tiles)))

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

        # --- background noise sigma (diagnostic only) ---
        if self._noise_m2 is not None and self._noise_n >= 2:
            var = self._noise_m2 / (self._noise_n - 1)
            std = np.sqrt(np.maximum(var, 0.0))
            # Median across pixels: moving dancers are a minority, so the median
            # std is the static-background sensor/scene noise floor.
            res.noise_sigma = float(np.median(std))

        # --- varThreshold by empirical background false-positive sweep ---
        if self._var_candidates and any(len(l) for l in self._var_fp):
            fp_rates = [float(np.median(l)) if l else 1.0 for l in self._var_fp]
            chosen = None
            for i, fp in enumerate(fp_rates):       # candidates ascending → lowest first
                if fp <= AUTOCAL_FP_TARGET:
                    chosen = i
                    break
            if chosen is None:                       # none clean enough → most conservative
                chosen = len(self._var_candidates) - 1
                res.var_saturated = True
            res.var_ok = True
            res.var_threshold = float(self._var_candidates[chosen])
            res.var_fp_rate = fp_rates[chosen]

        # --- report: exposure stability + FPS ---
        if self._brightness:
            b = np.asarray(self._brightness, dtype=np.float64)
            res.brightness_mean = float(b.mean())
            res.brightness_cv = float(b.std() / b.mean()) if b.mean() > 1e-6 else 0.0
            res.exposure_stable = res.brightness_cv < AUTOCAL_EXPOSURE_STABLE_CV
        if self._fps:
            res.fps_achieved = float(np.median(self._fps))

        return res
