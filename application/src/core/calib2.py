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
    AUTOCAL_CLAHE_NOISE_SIGMA,
    AUTOCAL2_WINDOW_FRAMES,
    AUTOCAL2_MIN_SAMPLES,
    AUTOCAL2_NET_HEIGHT_TARGET,
    AUTOCAL2_NET_HEIGHT_TARGET_DARK,
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
    net_target_px: float = AUTOCAL2_NET_HEIGHT_TARGET  # target used for the pick
    imgsz_dark_mode: bool = False     # True when the high-noise (dark) target applied
    model_advisory: str = ""          # P-6 fps-table model suggestion (report-only)
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
            dark_part = ("  [dark scene: small-target mode]"
                         if self.imgsz_dark_mode else "")
            lines.append(f"Image size: {self.imgsz}  "
                         f"(dancer ≈ {self.net_height_px:.0f} px in net input"
                         f"{fps_part}){cap_part}{dark_part}")
            if not self.imgsz_satisfied:
                # Explicit rig advisory, not a silent fallback (bug 12e).
                lines.append(
                    f"RIG ADVISORY: dancer ≈ {self.net_height_px:.0f} px in the "
                    f"net input, below the ~{self.net_target_px:.0f} px "
                    "the pose model needs - move the camera closer or use a "
                    "longer lens.")
        if self.model_advisory:
            lines.append(self.model_advisory)
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


def load_fps_table(path: str) -> Optional[dict]:
    """Load ``models/fps_table.json``: {model: {imgsz: fps}} measured per-rig
    at engine-build time (P-6 / Phase 2b — the imgsz^-2 law breaks under ~960
    where fixed overhead dominates, so the cost curve is measured, not
    assumed). Returns {model: {int imgsz: float fps}} or None when missing
    or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    table = {}
    for model, row in raw.items():
        if model.startswith("_") or not isinstance(row, dict):
            continue
        try:
            pts = {int(k): float(v) for k, v in row.items() if float(v) > 0}
        except (TypeError, ValueError):
            continue
        if pts:
            table[model] = pts
    return table or None


def _table_fps_model(fps_base: list, table_row: dict):
    """Predict whole-loop fps at preset ``p`` by scaling each run's live fps
    with the rig table's engine-fps ratio (candidate preset / capture imgsz);
    per-run fallback to the imgsz^-2 law where the table lacks a point."""
    def fps_model(p: int) -> float:
        preds = []
        for f, i in fps_base:
            t_p, t_i = table_row.get(int(p)), table_row.get(int(i))
            if t_p and t_i:
                preds.append(f * t_p / t_i)
            else:
                preds.append(f * (i / p) ** 2)
        return float(np.median(preds))
    return fps_model


# Largest-first; n/s are last resorts (Phase 2b: capacity is the only reliable
# lever on hard small-far/dark scenes and never hurts elsewhere; yolo26 loses
# tier-for-tier on this corpus and its conf scale would break seeded configs).
_MODEL_TIERS_DESC = ("yolo11x-pose", "yolo11l-pose", "yolo11m-pose",
                     "yolo11s-pose", "yolo11n-pose")


def advise_model(fps_table: Optional[dict], current_model: str, imgsz: int,
                 fps_base: list,
                 fps_budget: float = AUTOCAL2_FPS_BUDGET
                 ) -> Tuple[Optional[str], Optional[float]]:
    """Largest yolo11 tier predicted to hold ``fps_budget`` at ``imgsz``.

    Cross-model prediction scales each run's live fps by the rig table's
    fps ratio candidate@imgsz / current@capture-imgsz — the same whole-loop
    simplification as the ⑤c imgsz cap. Report-only; never auto-applied.
    Returns (model, predicted_fps), or (None, None) when the table, the
    current model's reference points, or live fps evidence are missing —
    callers should treat that as "no advisory", except the explicit
    nothing-fits case which returns (None, predicted_fps_of_smallest)."""
    if not fps_table or not fps_base or not imgsz or not current_model:
        return None, None
    cur_row = fps_table.get(current_model)
    if not cur_row:
        return None, None
    last_pred = None
    for cand in _MODEL_TIERS_DESC:
        row = fps_table.get(cand)
        if not row or int(imgsz) not in row:
            continue
        preds = [f * row[int(imgsz)] / cur_row[int(i)]
                 for f, i in fps_base if cur_row.get(int(i))]
        if not preds:
            continue
        pred = float(np.median(preds))
        last_pred = round(pred, 1)
        if pred >= fps_budget:
            return cand, round(pred, 1)
    return None, last_pred


def aggregate(runs: Sequence[SubjectRun], roi_long_side: float,
              noise_sigma: Optional[float] = None,
              fps_table: Optional[dict] = None,
              current_model: str = "") -> SubjectProposal:
    """Pool the included runs into a proposal (provisional rules, UX_PLAN §6).

    ``noise_sigma`` (live MOG2-input temporal noise, same definition as
    Calib1's) switches the net-height target to the dark-scene value when it
    exceeds the ⑤b threshold — Phase 2b measured the imgsz curve INVERTING on
    dark/noisy scenes (downscale acts as denoise; tmp_analysis/phase2b).
    ``fps_table``/``current_model`` enable the per-rig measured cost curve and
    the report-only model advisory (P-6)."""
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
    cur_row = (fps_table or {}).get(current_model)
    if fps_base and cur_row:
        # Per-rig measured cost curve (P-6); quadratic fallback per point.
        fps_model = _table_fps_model(fps_base, cur_row)
    elif fps_base:
        def fps_model(p: int) -> float:
            return float(np.median([f * (i / p) ** 2 for f, i in fps_base]))

    # Dark/noisy regime: the imgsz quality curve inverts (Phase 2b) — aim at
    # the small dark-scene target instead of the standard pose target.
    dark = bool(noise_sigma is not None
                and noise_sigma > AUTOCAL_CLAHE_NOISE_SIGMA)
    target = AUTOCAL2_NET_HEIGHT_TARGET_DARK if dark else AUTOCAL2_NET_HEIGHT_TARGET
    prop.imgsz_dark_mode = dark
    prop.net_target_px = target

    prop.imgsz, prop.imgsz_satisfied, prop.net_height_px, prop.imgsz_fps_limited = \
        select_imgsz(median_h, roi_long_side, target_net_px=target,
                     fps_model=fps_model, fps_budget=AUTOCAL2_FPS_BUDGET)
    if fps_model is not None and prop.imgsz:
        prop.imgsz_pred_fps = round(fps_model(prop.imgsz), 1)

    # Model advisory (P-6, report-only): largest yolo11 tier inside the FPS
    # budget at the chosen imgsz, from the per-rig engine fps table.
    if fps_table and current_model and prop.imgsz:
        best, pred = advise_model(fps_table, current_model, prop.imgsz,
                                  fps_base)
        if best and best != current_model:
            prop.model_advisory = (
                f"Model advisory: {best} is the largest tier predicted to "
                f"hold {AUTOCAL2_FPS_BUDGET:.0f} fps at {prop.imgsz} "
                f"(~{pred:.0f} fps; current: {current_model}).")
        elif best is None and pred is not None:
            prop.model_advisory = (
                f"Model advisory: no yolo11 tier is predicted to hold "
                f"{AUTOCAL2_FPS_BUDGET:.0f} fps at {prop.imgsz} on this rig "
                f"(smallest ~{pred:.0f} fps; current: {current_model}).")

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
