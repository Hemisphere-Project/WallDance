"""Calib2 — dancer (subject) calibration evidence pool (UX_PLAN.md U4).

Unlike Calib1 (scene pass: empty stage, idempotent, each run replaces), Calib2
is **accumulative**: each run — live or during recording playback — appends an
evidence sample under ``projects/<name>/calib2/``, and the operator applies the
*pooled* result once.  Pooling across situations (costumes, dancer counts,
distances, slots) is the point: the sweet spot comes from the pooled
distribution, not one pass.

Per run we record confirmed-track bbox heights, mean keypoint confidences,
speeds (px/frame), achieved FPS, the active lighting profile and the ROI
geometry at capture time (px heights shift if the framing changes → stale
flag).  A handful of raw frames are saved alongside for the future gamma/CLAHE
confidence sweep (deferred to the annotated-footage loop).

Pure logic + JSON IO — no GUI/camera/tracker imports; unit-testable.
"""
from __future__ import annotations

import json
import os
import time

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Sequence, Tuple

import numpy as np

from config import (
    AUTOCAL_MIN_RATIO_BOUNDS,
    AUTOCAL_MAX_RATIO_BOUNDS,
    AUTOCAL_HEIGHT_PCTL_LO,
    AUTOCAL_HEIGHT_PCTL_HI,
    AUTOCAL2_WINDOW_FRAMES,
    AUTOCAL2_MIN_SAMPLES,
    AUTOCAL2_NET_HEIGHT_TARGET,
    AUTOCAL2_CONF_MARGIN,
    AUTOCAL2_CONF_BOUNDS,
    AUTOCAL2_BLUR_FRACTION,
    AUTOCAL2_SPEED_PCTL,
    AUTOCAL2_BLUR_BOUNDS_MS,
    AUTOCAL2_STALE_TOL,
)

_IMGSZ_PRESETS = (640, 800, 960, 1280, 1536, 1920)
_HEIGHT_MIN_PX = 20
_HEIGHT_MAX_PX = 800


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


@dataclass
class SubjectRun:
    """One Calib2 collection window's evidence."""
    timestamp: str = ""
    source: str = ""                 # "live" or "slot N"
    profile: str = "show"
    frames: int = 0
    heights: List[float] = field(default_factory=list)
    confs: List[float] = field(default_factory=list)     # mean keypoint conf per track-frame
    speeds: List[float] = field(default_factory=list)    # px/frame per track-frame
    fps: List[float] = field(default_factory=list)
    roi: Tuple[int, int, int, int] = (0, 0, 0, 0)        # x, y, w, h at capture
    roi_source: Tuple[int, int] = (0, 0)                 # full-frame size at capture

    @property
    def samples(self) -> int:
        return len(self.heights)

    def label(self) -> str:
        return (f"{self.timestamp}  {self.source}  [{self.profile}]  "
                f"{self.samples} samples / {self.frames} frames")

    def stale_for(self, roi: Sequence[int], roi_source: Sequence[int],
                  tol: float = AUTOCAL2_STALE_TOL) -> bool:
        """True if framing changed enough that px heights no longer compare."""
        old_long = max(self.roi[2], self.roi[3]) or max(self.roi_source[0], self.roi_source[1])
        new_long = max(roi[2], roi[3]) or max(roi_source[0], roi_source[1])
        if old_long <= 0 or new_long <= 0:
            return False
        return abs(new_long - old_long) / old_long > tol

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "SubjectRun":
        run = cls()
        for k, v in data.items():
            if hasattr(run, k):
                setattr(run, k, v)
        run.roi = tuple(run.roi)
        run.roi_source = tuple(run.roi_source)
        return run


class SubjectCollector:
    """Collect one run over a fixed frame window (drive from the process loop)."""

    def __init__(self, window_frames: int = AUTOCAL2_WINDOW_FRAMES):
        self.window_frames = int(window_frames)
        self.run = SubjectRun()
        self._collecting = False

    def start(self, source: str, profile: str,
              roi: Sequence[int], roi_source: Sequence[int]) -> None:
        self.run = SubjectRun(
            timestamp=time.strftime("%Y%m%d_%H%M%S"),
            source=source, profile=profile,
            roi=tuple(int(v) for v in roi),
            roi_source=tuple(int(v) for v in roi_source),
        )
        self._collecting = True

    def cancel(self) -> None:
        self._collecting = False

    @property
    def is_collecting(self) -> bool:
        return self._collecting

    @property
    def ready(self) -> bool:
        return self._collecting and self.run.frames >= self.window_frames

    def progress(self) -> float:
        if self.window_frames <= 0:
            return 1.0
        return min(1.0, self.run.frames / self.window_frames)

    def feed(self, samples: Sequence[Tuple[float, float, float]],
             fps_sample: float) -> None:
        """Add one frame: ``samples`` = (height_px, mean_conf, speed_px_per_frame)
        per confirmed track."""
        if not self._collecting:
            return
        for h, c, s in samples:
            if h and h > 0:
                self.run.heights.append(float(h))
                self.run.confs.append(float(c))
                self.run.speeds.append(float(s))
        if fps_sample and fps_sample > 0:
            self.run.fps.append(float(fps_sample))
        self.run.frames += 1

    def finish(self) -> SubjectRun:
        self._collecting = False
        return self.run


@dataclass
class SubjectProposal:
    """Pooled result over the included runs.  ``ok`` gates the apply."""
    ok: bool = False
    runs: int = 0
    samples: int = 0
    person_height_px: Optional[int] = None
    min_ratio: Optional[float] = None
    max_ratio: Optional[float] = None
    imgsz: Optional[int] = None
    imgsz_satisfied: bool = False     # False if even the largest preset misses the target
    net_height_px: float = 0.0        # dancer height in net-input px at the chosen imgsz
    confidence: Optional[float] = None
    blur_budget_ms: Optional[float] = None
    note: str = ""

    def summary(self) -> str:
        if not self.ok:
            return (f"Pool not ready: {self.samples} samples over {self.runs} run(s) "
                    f"(need {AUTOCAL2_MIN_SAMPLES}). Add more runs.")
        lines = [
            f"Pooled: {self.samples} samples over {self.runs} run(s)",
            f"Person height: {self.person_height_px} px  "
            f"(ratios {self.min_ratio:.2f} / {self.max_ratio:.2f})",
        ]
        if self.imgsz:
            sat = "" if self.imgsz_satisfied else "  (max preset - still below target)"
            lines.append(f"Image size: {self.imgsz}  "
                         f"(dancer ≈ {self.net_height_px:.0f} px in net input){sat}")
        if self.confidence is not None:
            lines.append(f"Sensitivity seed (confidence): {self.confidence:.2f}")
        if self.blur_budget_ms is not None:
            lines.append(f"Blur budget for scene calibration: {self.blur_budget_ms:.0f} ms")
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)


def select_imgsz(person_height_px: float, roi_long_side: float,
                 target_net_px: float = AUTOCAL2_NET_HEIGHT_TARGET,
                 presets: Sequence[int] = _IMGSZ_PRESETS) -> Tuple[int, bool, float]:
    """Smallest imgsz whose net-input dancer height meets the target.

    net_height = person_height_px * imgsz / roi_long_side (letterbox on the
    long side).  Returns (imgsz, satisfied, net_height_at_choice).
    """
    roi_long_side = max(float(roi_long_side), 1.0)
    h = max(float(person_height_px), 1.0)
    for p in presets:
        net = h * p / roi_long_side
        if net >= target_net_px:
            return int(p), True, net
    p = presets[-1]
    return int(p), False, h * p / roi_long_side


def aggregate(runs: Sequence[SubjectRun], roi_long_side: float) -> SubjectProposal:
    """Pool the included runs into a proposal (provisional rules, UX_PLAN §6)."""
    prop = SubjectProposal(runs=len(runs))
    heights = np.asarray([h for r in runs for h in r.heights], dtype=np.float64)
    confs = np.asarray([c for r in runs for c in r.confs], dtype=np.float64)
    speeds = np.asarray([s for r in runs for s in r.speeds], dtype=np.float64)
    fps = np.asarray([f for r in runs for f in r.fps], dtype=np.float64)
    prop.samples = int(heights.size)
    if heights.size < AUTOCAL2_MIN_SAMPLES:
        return prop

    median_h = float(np.median(heights))
    if median_h <= 0:
        return prop
    prop.ok = True
    prop.person_height_px = int(round(_clamp(median_h, _HEIGHT_MIN_PX, _HEIGHT_MAX_PX)))
    lo = float(np.percentile(heights, AUTOCAL_HEIGHT_PCTL_LO))
    hi = float(np.percentile(heights, AUTOCAL_HEIGHT_PCTL_HI))
    prop.min_ratio = round(_clamp(lo / median_h, *AUTOCAL_MIN_RATIO_BOUNDS), 3)
    prop.max_ratio = round(_clamp(hi / median_h, *AUTOCAL_MAX_RATIO_BOUNDS), 3)

    prop.imgsz, prop.imgsz_satisfied, prop.net_height_px = select_imgsz(
        median_h, roi_long_side)

    # Sensitivity seed: low enough to catch the weakest dancers actually seen,
    # with a small margin.  (KNOBS E2 wants ghost-rate-targeted seeding — that
    # needs annotated/ghost evidence; this is the provisional stand-in.)
    if confs.size:
        p05 = float(np.percentile(confs, 5.0))
        prop.confidence = round(_clamp(p05 - AUTOCAL2_CONF_MARGIN,
                                       *AUTOCAL2_CONF_BOUNDS), 2)

    # Blur budget: exposure such that the p95-speed dancer blurs less than
    # AUTOCAL2_BLUR_FRACTION of their height during the exposure.
    if speeds.size and fps.size:
        speed_px_frame = float(np.percentile(speeds, AUTOCAL2_SPEED_PCTL))
        fps_med = float(np.median(fps))
        if speed_px_frame > 0 and fps_med > 0:
            speed_px_ms = speed_px_frame * fps_med / 1000.0
            budget = (AUTOCAL2_BLUR_FRACTION * median_h) / max(speed_px_ms, 1e-6)
            prop.blur_budget_ms = round(_clamp(budget, *AUTOCAL2_BLUR_BOUNDS_MS), 1)

    return prop


class SubjectPool:
    """Disk-backed pool of runs under ``<project_dir>/calib2/``."""

    def __init__(self, project_dir: str):
        self.dir = os.path.join(project_dir, "calib2")

    def save_run(self, run: SubjectRun) -> str:
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, f"{run.timestamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(run.to_json(), f)
        return path

    def frames_dir(self, run: SubjectRun) -> str:
        return os.path.join(self.dir, f"{run.timestamp}_frames")

    def load_runs(self) -> List[Tuple[str, SubjectRun]]:
        """Sorted (path, run) pairs, oldest first; unreadable files skipped."""
        if not os.path.isdir(self.dir):
            return []
        out = []
        for name in sorted(os.listdir(self.dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.dir, name)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    out.append((path, SubjectRun.from_json(json.load(f))))
            except (OSError, ValueError):
                continue
        return out

    def clear(self) -> int:
        """Delete all runs (keeps saved frame samples). Returns count removed."""
        removed = 0
        for path, _run in self.load_runs():
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        return removed
