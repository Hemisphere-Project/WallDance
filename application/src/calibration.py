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

import math

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Iterable, Optional, Tuple

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
    AUTOCAL_SCALE_CANDIDATES,
    AUTOCAL_SCALE_PREFERENCE,
    AUTOCAL_SWEEP_STRIDE,
    AUTOCAL_BLUR_BUDGET_MS,
    AUTOCAL_SERVO_TARGET_BRIGHTNESS,
    AUTOCAL_SERVO_TOLERANCE,
    AUTOCAL_SERVO_CLIP_MAX_PCT,
    AUTOCAL_SERVO_GAIN_MAX_DB,
    AUTOCAL_SERVO_SETTLE_FRAMES,
    AUTOCAL_SERVO_MAX_STEPS,
    AUTOCAL_GAMMA_TARGET,
    AUTOCAL_GAMMA_BOUNDS,
    AUTOCAL_CLAHE_DEFAULT,
    AUTOCAL_CLAHE_NOISY,
    AUTOCAL_CLAHE_NOISE_SIGMA,
)
from ids_camera import IDS_EXPOSURE_MIN_FPS, max_exposure_for_fps

# Hard bounds for PERSON_HEIGHT_PX (mirrors the GUI slider range, config.py).
_HEIGHT_MIN_PX = 20
_HEIGHT_MAX_PX = 800


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def seed_gamma(brightness_mean: float,
               target: float = AUTOCAL_GAMMA_TARGET,
               bounds: Tuple[float, float] = AUTOCAL_GAMMA_BOUNDS) -> float:
    """Gamma that maps the measured raw mean toward ``target`` mid-gray.

    The enhancer LUT is ``out = 255 * (in/255) ** (1/gamma)`` (gamma > 1
    brightens), so solving (b/255)^(1/g) = target/255 gives
    g = ln(b/255) / ln(target/255).  Clamped: a near-black IR scene wants the
    sensor/gain fixed first (servo), not an extreme gamma.
    """
    b = _clamp(float(brightness_mean), 1.0, 250.0)
    g = math.log(b / 255.0) / math.log(target / 255.0)
    return round(_clamp(g, bounds[0], bounds[1]), 2)


def seed_clahe(noise_sigma: float) -> float:
    """CLAHE clip seed: back off on noisy scenes (CLAHE amplifies noise)."""
    return (AUTOCAL_CLAHE_NOISY if noise_sigma > AUTOCAL_CLAHE_NOISE_SIGMA
            else AUTOCAL_CLAHE_DEFAULT)


def scene_report_stats(gray: np.ndarray, grid: Tuple[int, int] = (8, 5),
                       center_frac: float = 0.5) -> dict:
    """Focus/clip/uniformity snapshot of one gray frame (Calib1 report card).

    Same metrics as the phone monitor (variance-of-Laplacian focus on the
    centre crop, clip percentages, min/max grid-tile uniformity with the
    darkest tile), kept here as a pure function for the calibration report.
    """
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    cf = center_frac
    x0, x1 = int(w * (0.5 - cf / 2)), int(w * (0.5 + cf / 2))
    y0, y1 = int(h * (0.5 - cf / 2)), int(h * (0.5 + cf / 2))
    crop = gray[y0:y1, x0:x1]
    focus = float(cv2.Laplacian(crop, cv2.CV_64F).var()) if crop.size else 0.0
    total = gray.size or 1
    clip_high = float(np.count_nonzero(gray >= 250)) / total * 100.0
    clip_low = float(np.count_nonzero(gray <= 5)) / total * 100.0
    gx, gy = grid
    tiles = cv2.resize(gray.astype(np.float32), (gx, gy),
                       interpolation=cv2.INTER_AREA)
    t_max = float(tiles.max()) if tiles.size else 0.0
    t_min = float(tiles.min()) if tiles.size else 0.0
    uniformity = (t_min / t_max) if t_max > 1e-6 else 0.0
    dark = np.unravel_index(int(tiles.argmin()), tiles.shape) if tiles.size else (0, 0)
    return {
        "focus": focus,
        "clip_high": clip_high,
        "clip_low": clip_low,
        "uniformity": uniformity,
        "dark_tile": (int(dark[1]), int(dark[0])),   # (col, row)
    }


@dataclass
class ServoResult:
    """Outcome of the exposure/gain servo phase (Calib1 phase A)."""
    ran: bool = False
    converged: bool = False
    steps: int = 0
    brightness: float = 0.0
    exposure_us: float = 0.0
    gain_db: float = 0.0
    note: str = ""

    def summary_line(self) -> str:
        if not self.ran:
            return "Exposure: not driven (playback or non-IDS camera) - kept current"
        state = "converged" if self.converged else f"stopped ({self.note})"
        return (f"Exposure: {self.exposure_us/1000.0:.1f} ms  gain {self.gain_db:.1f} dB  "
                f"-> brightness {self.brightness:.0f}  ({state}, {self.steps} steps)")


class ExposureServo:
    """Drives IDS exposure/gain toward a brightness target under a blur budget.

    Order of authority (UX_PLAN U3): exposure rises first, but only up to
    ``min(blur budget, FPS floor)`` — motion blur is the binding constraint,
    not frame rate — then analog gain takes over (Starvis2 = low read noise).
    Clipping always backs gain off before exposure.

    Pure logic: ``feed(brightness, clip_pct)`` returns ``("exposure", us)`` /
    ``("gain", db)`` commands for the caller to apply to the camera, or None
    while settling/done.  Unit-testable without hardware.
    """

    _EXPOSURE_MIN_US = 200.0

    def __init__(self, exposure_us: float, gain_db: float,
                 blur_budget_ms: float = AUTOCAL_BLUR_BUDGET_MS,
                 min_fps: float = IDS_EXPOSURE_MIN_FPS,
                 target: float = AUTOCAL_SERVO_TARGET_BRIGHTNESS,
                 tolerance: float = AUTOCAL_SERVO_TOLERANCE,
                 clip_max_pct: float = AUTOCAL_SERVO_CLIP_MAX_PCT,
                 gain_max_db: float = AUTOCAL_SERVO_GAIN_MAX_DB,
                 settle_frames: int = AUTOCAL_SERVO_SETTLE_FRAMES,
                 max_steps: int = AUTOCAL_SERVO_MAX_STEPS):
        self.exposure_cap_us = min(float(blur_budget_ms) * 1000.0,
                                   max_exposure_for_fps(min_fps))
        self.exposure_us = _clamp(float(exposure_us),
                                  self._EXPOSURE_MIN_US, self.exposure_cap_us)
        self.gain_db = _clamp(float(gain_db), 0.0, float(gain_max_db))
        self.target = float(target)
        self.tolerance = float(tolerance)
        self.clip_max_pct = float(clip_max_pct)
        self.gain_max_db = float(gain_max_db)
        self.settle_frames = int(settle_frames)
        self.max_steps = int(max_steps)
        self._settle = settle_frames  # let the initial state produce a frame
        self._steps = 0
        self._done = False
        self._converged = False
        self._note = ""
        self._brightness = 0.0

    @property
    def done(self) -> bool:
        return self._done

    def feed(self, brightness: float, clip_high_pct: float):
        """One frame's measurement in, at most one camera command out."""
        if self._done:
            return None
        self._brightness = float(brightness)
        if self._settle > 0:
            self._settle -= 1
            return None
        if self._steps >= self.max_steps:
            self._finish(False, "step limit")
            return None

        b = float(brightness)
        if clip_high_pct > self.clip_max_pct:
            if self.gain_db > 0.5:
                self.gain_db = max(0.0, self.gain_db - 3.0)
                return self._command("gain", self.gain_db)
            if self.exposure_us > self._EXPOSURE_MIN_US * 1.01:
                self.exposure_us = max(self._EXPOSURE_MIN_US, self.exposure_us * 0.7)
                return self._command("exposure", self.exposure_us)
            self._finish(False, "clipping at minimum settings")
            return None
        if b < self.target - self.tolerance:
            if self.exposure_us < self.exposure_cap_us * 0.99:
                factor = _clamp(self.target / max(b, 1.0), 1.3, 2.5)
                self.exposure_us = min(self.exposure_cap_us, self.exposure_us * factor)
                return self._command("exposure", self.exposure_us)
            if self.gain_db < self.gain_max_db - 0.1:
                self.gain_db = min(self.gain_max_db, self.gain_db + 3.0)
                return self._command("gain", self.gain_db)
            self._finish(False, "at exposure+gain limits - scene still dark, add IR")
            return None
        if b > self.target + self.tolerance:
            if self.gain_db > 0.5:
                self.gain_db = max(0.0, self.gain_db - 3.0)
                return self._command("gain", self.gain_db)
            factor = _clamp(self.target / max(b, 1.0), 0.4, 0.8)
            if self.exposure_us > self._EXPOSURE_MIN_US * 1.01:
                self.exposure_us = max(self._EXPOSURE_MIN_US, self.exposure_us * factor)
                return self._command("exposure", self.exposure_us)
            self._finish(False, "too bright at minimum settings")
            return None
        self._finish(True, "")
        return None

    def _command(self, kind: str, value: float):
        self._steps += 1
        self._settle = self.settle_frames
        return (kind, float(value))

    def _finish(self, converged: bool, note: str) -> None:
        self._done = True
        self._converged = converged
        self._note = note

    def result(self) -> ServoResult:
        return ServoResult(ran=True, converged=self._converged,
                           steps=self._steps, brightness=self._brightness,
                           exposure_us=self.exposure_us, gain_db=self.gain_db,
                           note=self._note)


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

    # MOG2 varThreshold + scale — chosen by a joint empirical background
    # false-positive sweep (they interact; KNOBS.md finding #2).
    var_ok: bool = False
    var_threshold: Optional[float] = None
    mog2_scale: Optional[float] = None
    var_fp_rate: float = 0.0          # background FP rate of the chosen pair
    var_saturated: bool = False       # True if no pair met the FP target
    # Diagnostic only (not used to set varThreshold): temporal noise of the
    # MOG2-input gray.  High σ on a near-black scene = CLAHE-amplified noise.
    noise_sigma: float = 0.0

    # CLAHE clip derived from the measured noise (gamma is seeded *before*
    # the window by the app, so the sweep sees the final motion-feed gamma).
    clahe_value: Optional[float] = None

    # Report-only
    brightness_mean: float = 0.0
    brightness_cv: float = 0.0
    exposure_stable: bool = False
    fps_achieved: float = 0.0
    # Scene report card (median over sampled raw frames)
    report_ok: bool = False
    focus_score: float = 0.0
    clip_high_pct: float = 0.0
    clip_low_pct: float = 0.0
    uniformity: float = 0.0
    dark_tile: tuple = (0, 0)

    def log_line(self) -> str:
        """Single structured line for the console log."""
        h = f"{self.person_height_px}px" if self.height_ok else "n/a"
        r = (f"[{self.min_ratio:.2f},{self.max_ratio:.2f}]"
             if self.height_ok else "[--]")
        if self.var_ok:
            vt = (f"{self.var_threshold:.0f}@s{self.mog2_scale:.2f}"
                  f"(fp={self.var_fp_rate*100:.2f}%"
                  f"{',SAT' if self.var_saturated else ''})")
        else:
            vt = "n/a"
        rep = (f" focus={self.focus_score:.0f} uniform={self.uniformity*100:.0f}%"
               f" clip={self.clip_high_pct:.2f}%" if self.report_ok else "")
        return (f"[Calibrate] frames={self.frames} height={h} ratios={r} "
                f"(n={self.height_samples}) var+scale={vt} "
                f"clahe={self.clahe_value} "
                f"noise_sigma={self.noise_sigma:.2f} brightness={self.brightness_mean:.0f} "
                f"cv={self.brightness_cv:.3f} exposure="
                f"{'stable' if self.exposure_stable else 'drifting'} "
                f"fps={self.fps_achieved:.1f}{rep}")

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
            lines.append(f"MOG2: varThreshold {self.var_threshold:.0f} @ scale "
                         f"{self.mog2_scale:.2f}  "
                         f"(background false-positives {self.var_fp_rate*100:.2f}%)")
        elif self.var_ok and self.var_saturated:
            lines.append(f"MOG2: varThreshold {self.var_threshold:.0f} @ scale "
                         f"{self.mog2_scale:.2f} (max)  "
                         f"- background still {self.var_fp_rate*100:.2f}% noisy: "
                         f"the scene is too noisy for MOG2 (raise IR / decouple CLAHE)")
        else:
            lines.append("MOG2 varThreshold: NOT measured - kept current value")
        if self.clahe_value is not None:
            noisy = self.noise_sigma > AUTOCAL_CLAHE_NOISE_SIGMA
            lines.append(f"CLAHE clip: {self.clahe_value:.1f}"
                         + ("  (reduced: scene noise is high)" if noisy else ""))
        lines.append(f"Brightness: {self.brightness_mean:.0f}  "
                     f"({'stable' if self.exposure_stable else 'still drifting'}, "
                     f"cv {self.brightness_cv:.3f}; noise sigma {self.noise_sigma:.2f})")
        if self.report_ok:
            warn_focus = " (LOW - check focus)" if self.focus_score < 50.0 else ""
            lines.append(f"Focus score: {self.focus_score:.0f}{warn_focus}")
            col, row = self.dark_tile
            warn_uni = (f" (darkest tile col {col} row {row} - aim IR there)"
                        if self.uniformity < 0.25 else "")
            lines.append(f"IR uniformity: {self.uniformity*100:.0f}%{warn_uni}")
            if self.clip_high_pct > 0.5:
                lines.append(f"Highlight clipping: {self.clip_high_pct:.2f}% "
                             f"- lower gain/exposure")
        lines.append(f"Achieved inference FPS: {self.fps_achieved:.1f}")
        return "\n".join(lines)


@dataclass
class ExclusionResult:
    """Outcome of building the auto exclusion mask."""
    grid: tuple = (0, 0)
    cells: list = field(default_factory=list)   # auto-excluded (col, row) pairs
    frames: int = 0
    manual_add: int = 0      # operator-forced exclusions kept across the build
    manual_remove: int = 0   # operator-forced un-exclusions kept across the build

    @property
    def count(self) -> int:
        return len(self.cells)

    def _manual_suffix(self) -> str:
        parts = []
        if self.manual_add:
            parts.append(f"+{self.manual_add} manual")
        if self.manual_remove:
            parts.append(f"-{self.manual_remove} unmasked")
        return f"  ({', '.join(parts)})" if parts else ""

    def summary_line(self) -> str:
        gx, gy = self.grid
        if self.frames < AUTOCAL_EXCL_MIN_FRAMES:
            return "Exclusion mask: not built (too few frames)" + self._manual_suffix()
        if not self.cells:
            return (f"Exclusion mask: none (no persistent ghost cells in "
                    f"{gx}x{gy} grid)" + self._manual_suffix())
        return (f"Exclusion mask: {self.count} ghost cell(s) masked "
                f"({gx}x{gy} grid)" + self._manual_suffix())


class ExclusionMaskBuilder:
    """Builds and holds the auto exclusion mask (P1.4) + manual overlays (④).

    A normalized ``grid`` over the frame.  During calibration, ``observe`` is
    called once per processed frame with the MOG2 foreground mask and the
    normalized positions of the *kept* skeletons.  ``build`` then marks cells
    that move often but ~never hold a skeleton as excluded.  ``excluded`` is the
    runtime query used to reject ghost detections.

    Manual overlays (ROADMAP §4.2 Phase 2 ④): the operator can force-mask
    cells the auto pass cannot know about (bystander benches, *static* facade
    ghosts that never move) and force-unmask false auto cells.  Overlays are
    kept separate from the auto cells so a Calib1 re-run (``build``) replaces
    only the auto mask — operator knowledge survives recalibration.  The
    effective mask is ``(auto | manual_add) - manual_remove``.

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
        self._cells: set = set()          # auto-built excluded (col, row)
        self._manual_add: set = set()     # operator-forced exclusions
        self._manual_remove: set = set()  # operator-forced un-exclusions
        self._effective: set = set()      # cache: (auto | add) - remove

    def _recompute_effective(self) -> None:
        self._effective = (self._cells | self._manual_add) - self._manual_remove

    @property
    def collecting(self) -> bool:
        return self._collecting

    @property
    def active(self) -> bool:
        return bool(self._effective)

    def effective_cells(self) -> set:
        """The mask actually applied: auto ∪ manual-add − manual-remove.

        Returns the internal cache — treat as read-only.
        """
        return self._effective

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
        """Finalise: cells with frequent motion but ~no skeleton → excluded.

        Replaces only the AUTO cells — manual overlays survive recalibration.
        """
        self._collecting = False
        cells: set = set()
        if self._frames >= self.min_frames:
            mfreq = self._motion / max(1, self._frames)
            sfreq = self._skel / max(1, self._frames)
            rows, cols = np.where((mfreq >= self.motion_freq) & (sfreq <= self.skel_freq))
            cells = {(int(c), int(r)) for c, r in zip(cols, rows)}
        self._cells = cells
        self._recompute_effective()
        return ExclusionResult(grid=(self.gx, self.gy),
                               cells=sorted(cells), frames=self._frames,
                               manual_add=len(self._manual_add),
                               manual_remove=len(self._manual_remove))

    def excluded(self, nx: float, ny: float) -> bool:
        """True if the normalized position lands in an excluded cell."""
        if not self._effective or not (0.0 <= nx < 1.0 and 0.0 <= ny < 1.0):
            return False
        return (int(nx * self.gx), int(ny * self.gy)) in self._effective

    def cell_at(self, nx: float, ny: float):
        """Grid cell (col, row) under a normalized point, or None if outside."""
        if not (0.0 <= nx < 1.0 and 0.0 <= ny < 1.0):
            return None
        return (int(nx * self.gx), int(ny * self.gy))

    def toggle_cell(self, col: int, row: int) -> bool:
        """Flip a cell's *effective* state; returns the new state.

        The flip is recorded relative to the auto mask, so it persists as an
        operator override: un-masking an auto cell records a manual-remove
        (the next Calib1 may re-detect the cell, the operator's word wins);
        masking a clean cell records a manual-add.
        """
        cell = (int(col), int(row))
        new_state = cell not in self.effective_cells()
        self.set_cell(col, row, new_state)
        return new_state

    def set_cell(self, col: int, row: int, excluded: bool) -> None:
        """Force a cell's effective state (paint-drag), recorded as an override."""
        cell = (int(col), int(row))
        if excluded:
            self._manual_remove.discard(cell)
            if cell not in self._cells:
                self._manual_add.add(cell)
        else:
            self._manual_add.discard(cell)
            if cell in self._cells:
                self._manual_remove.add(cell)
        self._recompute_effective()

    def set_cells(self, grid, cells, manual_add=(), manual_remove=()) -> None:
        """Restore a persisted mask (e.g. on project load)."""
        self.gx, self.gy = int(grid[0]), int(grid[1])
        self._cells = {(int(c[0]), int(c[1])) for c in cells}
        self._manual_add = {(int(c[0]), int(c[1])) for c in manual_add}
        self._manual_remove = {(int(c[0]), int(c[1])) for c in manual_remove}
        self._collecting = False
        self._recompute_effective()

    def get_cells(self) -> tuple:
        """(grid, sorted effective cells) — the mask as applied."""
        return ((self.gx, self.gy), sorted(self.effective_cells()))

    def get_state(self) -> tuple:
        """(grid, auto, manual_add, manual_remove) — the split, for persistence."""
        return ((self.gx, self.gy), sorted(self._cells),
                sorted(self._manual_add), sorted(self._manual_remove))

    def clear(self) -> None:
        """Drop everything — auto mask and operator overlays."""
        self._cells = set()
        self._manual_add = set()
        self._manual_remove = set()
        self._collecting = False
        self._recompute_effective()


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
        # Joint empirical var×scale sweep: one MOG2 model per (varThreshold,
        # scale) pair + its per-frame background-FP samples (median grid-tile
        # foreground fraction).  Scales are swept jointly with var because
        # they interact (KNOBS.md finding #2).
        self._var_pairs: list[tuple[float, float]] = [
            (float(v), float(s))
            for s in AUTOCAL_SCALE_CANDIDATES
            for v in AUTOCAL_VARTHRESH_CANDIDATES
        ]
        self._var_models: list = []
        self._var_fp: list[list[float]] = [[] for _ in self._var_pairs]
        # Scene report card samples (from the raw frame, every ~10th frame).
        self._report_samples: list[dict] = []

    # ------------------------------------------------------------------
    # Lifecycle / status
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Begin a fresh collection window."""
        self._reset_accumulators()
        # One independent MOG2 model per (var, scale) pair.  history = window
        # so each adapts its background within the collection window.
        hist = max(2, self.window_frames)
        self._var_models = [
            cv2.createBackgroundSubtractorMOG2(
                history=hist, varThreshold=v, detectShadows=True)
            for v, _s in self._var_pairs
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
             brightness: Optional[float] = None,
             report_frame: Optional[np.ndarray] = None) -> None:
        """Add one processed frame's samples to the window.

        noise_gray:    2D uint8 frame the background model consumes (full
                       resolution, pre-MOG2-scale) — its temporal sigma drives
                       varThreshold and the joint var×scale sweep resizes it
                       per candidate scale exactly as the motion model would.
        track_heights: bbox heights (px) of the confirmed tracks this frame.
        fps_sample:    achieved inference FPS for this frame (1000/process_wall_ms).
        now:           wall-clock timestamp (unused for gating; reserved).
        brightness:    raw-scene mean luma for the exposure report.  If None,
                       falls back to the mean of ``noise_gray`` (test convenience).
        report_frame:  raw frame for the scene report card (focus/clip/
                       uniformity); sampled every ~10th frame.
        """
        if self._state != CalState.COLLECTING:
            return

        for h in track_heights:
            if h and h > 0:
                self._heights.append(float(h))

        if noise_gray is not None and noise_gray.size:
            small = self._downscale(noise_gray)
            self._accumulate_noise(small)
            if self._frames % AUTOCAL_SWEEP_STRIDE == 0:
                self._score_var_candidates(noise_gray)
            self._brightness.append(
                float(brightness) if brightness is not None else float(small.mean()))
        elif brightness is not None:
            self._brightness.append(float(brightness))

        if report_frame is not None and self._frames % 10 == 0:
            try:
                self._report_samples.append(scene_report_stats(report_frame))
            except Exception:
                pass  # report card is best-effort; never break collection

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

    def _score_var_candidates(self, gray: np.ndarray) -> None:
        """Run each candidate (var, scale) MOG2 model on this frame and record
        its background false-positive level: the median grid-tile foreground
        fraction.

        Each candidate scale resizes the full-resolution MOG2-input gray
        exactly as the production motion model would, so the FP measurement
        carries the real noise-averaging effect of the downscale.  The median
        over a grid is robust to the dancer minority — tiles with a dancer are
        outliers, so the median tile reflects the *background*.
        """
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        gx, gy = AUTOCAL_FP_GRID
        smalls = {}
        for s in AUTOCAL_SCALE_CANDIDATES:
            if s < 1.0:
                smalls[s] = cv2.resize(gray, None, fx=s, fy=s,
                                       interpolation=cv2.INTER_AREA)
            else:
                smalls[s] = gray
        for (v, s), model, fp_list in zip(self._var_pairs, self._var_models,
                                          self._var_fp):
            mask = model.apply(smalls[s])          # learningRate auto (1/history)
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

        # --- var×scale by joint empirical background false-positive sweep ---
        if self._var_pairs and any(len(l) for l in self._var_fp):
            fp_by_pair = {
                pair: (float(np.median(l)) if l else 1.0)
                for pair, l in zip(self._var_pairs, self._var_fp)
            }
            chosen = None
            # Lowest var (most sensitive silhouette) wins first; at equal var,
            # scales in preference order (0.7 = Phase-C winner, then 1.0, 0.5).
            for v in sorted(AUTOCAL_VARTHRESH_CANDIDATES):
                for s in AUTOCAL_SCALE_PREFERENCE:
                    pair = (float(v), float(s))
                    if pair in fp_by_pair and fp_by_pair[pair] <= AUTOCAL_FP_TARGET:
                        chosen = pair
                        break
                if chosen:
                    break
            if chosen is None:
                # Nothing clean enough → most conservative pair (max var,
                # smallest scale = strongest noise averaging) + saturated flag.
                chosen = (float(max(AUTOCAL_VARTHRESH_CANDIDATES)),
                          float(min(AUTOCAL_SCALE_CANDIDATES)))
                res.var_saturated = True
            res.var_ok = True
            res.var_threshold = chosen[0]
            res.mog2_scale = chosen[1]
            res.var_fp_rate = fp_by_pair.get(chosen, 1.0)

        # --- CLAHE clip from the measured noise (gamma was seeded pre-window) ---
        res.clahe_value = seed_clahe(res.noise_sigma)

        # --- scene report card (median over sampled raw frames) ---
        if self._report_samples:
            res.report_ok = True
            res.focus_score = float(np.median(
                [s["focus"] for s in self._report_samples]))
            res.clip_high_pct = float(np.median(
                [s["clip_high"] for s in self._report_samples]))
            res.clip_low_pct = float(np.median(
                [s["clip_low"] for s in self._report_samples]))
            res.uniformity = float(np.median(
                [s["uniformity"] for s in self._report_samples]))
            tiles = [s["dark_tile"] for s in self._report_samples]
            res.dark_tile = max(set(tiles), key=tiles.count)

        # --- report: exposure stability + FPS ---
        if self._brightness:
            b = np.asarray(self._brightness, dtype=np.float64)
            res.brightness_mean = float(b.mean())
            res.brightness_cv = float(b.std() / b.mean()) if b.mean() > 1e-6 else 0.0
            res.exposure_stable = res.brightness_cv < AUTOCAL_EXPOSURE_STABLE_CV
        if self._fps:
            res.fps_achieved = float(np.median(self._fps))

        return res
