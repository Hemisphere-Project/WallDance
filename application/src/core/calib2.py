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

from core.config import (
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
    AUTOCAL2_FPS_BUDGET,
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
    confs: List[float] = field(default_factory=list)     # per track-frame; see conf_kind
    speeds: List[float] = field(default_factory=list)    # px/frame per track-frame
    fps: List[float] = field(default_factory=list)
    roi: Tuple[int, int, int, int] = (0, 0, 0, 0)        # x, y, w, h at capture
    roi_source: Tuple[int, int] = (0, 0)                 # full-frame size at capture
    # "box" = YOLO box confidence (⑤a, the units settings.confidence uses);
    # the dataclass default is the LEGACY kind so pre-⑤ runs loaded from disk
    # (which lack the key) are correctly tagged — start() stamps new runs "box".
    conf_kind: str = "kpt_mean"
    # YOLO imgsz at capture; 0 = unknown (legacy run). Lets the pooled imgsz
    # pick model inference cost from the measured fps (⑤c, fps ∝ imgsz⁻²).
    imgsz: int = 0

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
              roi: Sequence[int], roi_source: Sequence[int],
              imgsz: int = 0) -> None:
        self.run = SubjectRun(
            timestamp=time.strftime("%Y%m%d_%H%M%S"),
            source=source, profile=profile,
            roi=tuple(int(v) for v in roi),
            roi_source=tuple(int(v) for v in roi_source),
            conf_kind="box",
            imgsz=int(imgsz),
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

    def feed(self, samples: Sequence[Tuple[float, Optional[float], float]],
             fps_sample: float) -> None:
        """Add one frame: ``samples`` = (height_px, box_conf_or_None,
        speed_px_per_frame) per confirmed track.  ``None`` conf (bridge /
        cold-blob fed this frame) still contributes height + speed."""
        if not self._collecting:
            return
        for h, c, s in samples:
            if h and h > 0:
                self.run.heights.append(float(h))
                self.run.speeds.append(float(s))
                if c is not None:
                    self.run.confs.append(float(c))
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
    imgsz_satisfied: bool = False     # False if the net-height target is unmet
    imgsz_fps_limited: bool = False   # True if a bigger preset met the target
                                      # but was rejected by the FPS budget (⑤c)
    imgsz_pred_fps: Optional[float] = None  # predicted fps at the chosen imgsz
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
            fps_part = (f" @ ~{self.imgsz_pred_fps:.0f} fps"
                        if self.imgsz_pred_fps else "")
            cap_part = "  (capped by FPS budget)" if self.imgsz_fps_limited else ""
            lines.append(f"Image size: {self.imgsz}  "
                         f"(dancer ≈ {self.net_height_px:.0f} px in net input"
                         f"{fps_part}){cap_part}")
            if not self.imgsz_satisfied:
                # Explicit rig advisory, not a silent fallback (bug 12e).
                lines.append(
                    f"RIG ADVISORY: dancer ≈ {self.net_height_px:.0f} px in the "
                    f"net input, below the ~{AUTOCAL2_NET_HEIGHT_TARGET:.0f} px "
                    "the pose model needs - move the camera closer or use a "
                    "longer lens.")
        if self.confidence is not None:
            lines.append(f"Sensitivity seed (confidence): {self.confidence:.2f}")
        if self.blur_budget_ms is not None:
            lines.append(f"Blur budget for scene calibration: {self.blur_budget_ms:.0f} ms")
        if self.note:
            lines.append(self.note)
        return "\n".join(lines)


def select_imgsz(person_height_px: float, roi_long_side: float,
                 target_net_px: float = AUTOCAL2_NET_HEIGHT_TARGET,
                 presets: Sequence[int] = _IMGSZ_PRESETS,
                 fps_model=None,
                 fps_budget: Optional[float] = None
                 ) -> Tuple[int, bool, float, bool]:
    """Smallest imgsz whose net-input dancer height meets the target, within
    the FPS budget (bug 12e / P-6).

    net_height = person_height_px * imgsz / roi_long_side (letterbox on the
    long side).  ``fps_model(p)`` predicts achieved fps at preset ``p`` (built
    by ``aggregate`` from measured fps, cost ∝ imgsz²); presets predicted
    below ``fps_budget`` are rejected — the show FPS is never silently traded
    for net height.  Returns (imgsz, satisfied, net_height, fps_limited).
    """
    roi_long_side = max(float(roi_long_side), 1.0)
    h = max(float(person_height_px), 1.0)

    def net(p: int) -> float:
        return h * p / roi_long_side

    allowed = list(presets)
    fps_capped = False
    if fps_model is not None and fps_budget:
        in_budget = [p for p in presets if fps_model(p) >= fps_budget]
        if not in_budget:
            in_budget = [presets[0]]  # slowest rig: smallest preset, flagged
        if len(in_budget) < len(list(presets)):
            fps_capped = True
        allowed = in_budget

    for p in allowed:
        if net(p) >= target_net_px:
            return int(p), True, net(p), False

    # Height target unmet within the budget: largest allowed preset; flag
    # fps_limited when an out-of-budget preset would have met the target.
    p = allowed[-1]
    target_met_beyond_budget = fps_capped and any(
        net(q) >= target_net_px for q in presets if q not in allowed)
    return int(p), False, net(p), target_met_beyond_budget


def aggregate(runs: Sequence[SubjectRun], roi_long_side: float) -> SubjectProposal:
    """Pool the included runs into a proposal (provisional rules, UX_PLAN §6)."""
    prop = SubjectProposal(runs=len(runs))
    heights = np.asarray([h for r in runs for h in r.heights], dtype=np.float64)
    # Confidence seed evidence: BOX-conf runs only (⑤a) — keypoint-conf
    # means from legacy runs are a different unit and pinned the seed.
    confs = np.asarray([c for r in runs if r.conf_kind == "box"
                        for c in r.confs], dtype=np.float64)
    legacy_conf_runs = sum(1 for r in runs if r.conf_kind != "box" and r.confs)
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

    # FPS budget for the imgsz pick (bug 12e / P-6): model inference cost
    # ∝ imgsz² from each run's measured fps at its capture imgsz; legacy
    # runs without a recorded imgsz contribute nothing (no cap = old behavior).
    fps_base = [(float(np.median(np.asarray(r.fps, dtype=np.float64))), r.imgsz)
                for r in runs if r.fps and r.imgsz > 0]
    fps_model = None
    if fps_base:
        def fps_model(p: int) -> float:
            return float(np.median([f * (i / p) ** 2 for f, i in fps_base]))
    prop.imgsz, prop.imgsz_satisfied, prop.net_height_px, prop.imgsz_fps_limited = \
        select_imgsz(median_h, roi_long_side,
                     fps_model=fps_model, fps_budget=AUTOCAL2_FPS_BUDGET)
    if fps_model is not None and prop.imgsz:
        prop.imgsz_pred_fps = round(fps_model(prop.imgsz), 1)

    # Sensitivity seed: low enough to catch the weakest dancers actually seen,
    # with a small margin.  (KNOBS E2 wants ghost-rate-targeted seeding — that
    # needs annotated/ghost evidence; this is the provisional stand-in.)
    if confs.size:
        p05 = float(np.percentile(confs, 5.0))
        prop.confidence = round(_clamp(p05 - AUTOCAL2_CONF_MARGIN,
                                       *AUTOCAL2_CONF_BOUNDS), 2)
    elif legacy_conf_runs:
        prop.note = (f"No sensitivity seed: the {legacy_conf_runs} selected "
                     "run(s) predate the box-confidence upgrade - record a "
                     "new Calib2 run for a seed.")

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
