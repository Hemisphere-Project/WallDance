"""The typed command/event seam between the runtime and its UI clients.

DECOMPOSITION_PLAN §4 / §5 Phase 3. The DPG desktop GUI is client #1
(``ui/adapter.py``); the tablet calibration client (Phase 5) is the second
consumer of the same vocabulary.

Commands (UI -> runtime)
    Dataclasses, validated on construction, queued thread-safely by
    ``RuntimeAPI.submit`` and executed at a *single* point in the main loop
    tick (``RuntimeAPI.drain``) -- never on the caller (DPG callback) thread.
    This formalizes the pending-flag pattern the controllers already used.

Events (runtime -> UI)
    Dataclasses published on the ``EventBus``; synchronous fan-out to all
    subscribers on the publishing thread (same thread model as the previous
    direct ``gui.*`` calls -- the GUI methods involved are the ones already
    hardened for cross-thread use, e.g. ``show_toast``). Every event is
    JSON-serializable via ``to_dict()`` **except** ``PreviewFrame`` (carries
    an ndarray; web/tablet clients use the MJPEG path instead). A semantic
    ``ConfigLoaded`` event is not needed by the DPG adapter (a config load
    fans out as granular ``ControlSync``/``ConfigList``/... events); add it
    when the tablet client wants a single signal.

Rules (§4): ``core/`` never imports ``runtime/`` or ``ui/``; ``runtime/``
never imports ``ui/`` (this module imports stdlib only); ``ui/adapter.py``
is the only place translating events->dpg and dpg-callbacks->commands.

Known exceptions kept *off* the bus (in-process port methods on the adapter,
because they are synchronous by nature, not fire-and-forget):
    - the TensorRT build prompt (the model controller blocks the main thread
      pumping ``render_frame`` until the operator answers -- a queued command
      could never drain while the loop is blocked);
    - ``render_frame`` itself (the modal-wait/model-load GUI pump);
    - the per-tick layout-change query (``consume_layout_change``).
"""
from __future__ import annotations

import threading
import traceback
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple, Type


class SystemState(Enum):
    """System operational states for show control (moved from gui_builder so
    the runtime owns the authoritative state; the UI re-exports it).

    Simplified 2-state system:
    - STANDBY: Preview + enhancement, no YOLO, no OSC
    - RUN: Full YOLO inference + OSC output
    """
    STANDBY = auto()  # Preview only, no YOLO processing, no OSC
    RUN = auto()      # Full pipeline: YOLO + tracking + OSC


def _check_member(value: str, allowed: tuple, what: str) -> None:
    if value not in allowed:
        raise ValueError(f"{what} must be one of {allowed}, got {value!r}")


class _Payload:
    """Shared helpers for command/event dataclasses."""

    def to_dict(self) -> Dict[str, Any]:
        return {"type": type(self).__name__, **asdict(self)}


# ======================================================================
# Commands (UI -> runtime)
# ======================================================================

class Command(_Payload):
    pass


# --- state / lifecycle -------------------------------------------------

@dataclass(frozen=True)
class SetState(Command):
    """STANDBY/RUN button. The GUI flips its own visuals immediately (as
    before); this keeps the runtime's authoritative mirror in sync."""
    state: str  # 'standby' | 'run'

    def __post_init__(self):
        _check_member(self.state, ("standby", "run"), "SetState.state")


@dataclass(frozen=True)
class Quit(Command):
    pass


# --- detection / sensitivity -------------------------------------------

@dataclass(frozen=True)
class SetSensitivity(Command):
    value: float


@dataclass(frozen=True)
class SetConfidence(Command):
    value: float


@dataclass(frozen=True)
class SetMotionSensitivity(Command):
    value: float


@dataclass(frozen=True)
class SetGapBridging(Command):
    """Dial B 'Gap bridging' [0,100] -> motion_sensitivity (OPERATOR_V2 §2.2)."""
    value: float


@dataclass(frozen=True)
class SetOutputSmoothing(Command):
    """Output box-size smoothing depth L (Track X §B.2); L>=1, default 1."""
    value: int


@dataclass(frozen=True)
class ToggleBoxClamp(Command):
    """Output box-clamp toggle (Track X §B.1); default ON."""
    value: bool


# --- dancer ids: identity-slot output layer (CONT-6, phase 6 Live) -----------

@dataclass(frozen=True)
class SetIdentitySlots(Command):
    """Stable OSC dancer ids 1..N (identity slots) on/off; default ON."""
    enabled: bool


@dataclass(frozen=True)
class SetMaxDancers(Command):
    """The show's dancer cap N (slot ids 1..N), 1..16."""
    value: int

    def __post_init__(self):
        if not 1 <= int(self.value) <= 16:
            raise ValueError(f"SetMaxDancers.value must be 1..16, got {self.value!r}")


@dataclass(frozen=True)
class SetStability(Command):
    """Centroid smoothing knob 0 (responsive) .. 1 (calm)."""
    value: float

    def __post_init__(self):
        if not 0.0 <= float(self.value) <= 3.0:
            raise ValueError(f"SetStability.value must be 0..3, got {self.value!r}")


@dataclass(frozen=True)
class SetCoastSeconds(Command):
    """Hold a lost dancer's id this long (seconds, 0..10) before it vanishes."""
    value: float

    def __post_init__(self):
        if not 0.0 <= float(self.value) <= 10.0:
            raise ValueError(f"SetCoastSeconds.value must be 0..10, got {self.value!r}")


@dataclass(frozen=True)
class SetIntermittentConfirm(Command):
    """Tracker: confirm (emit) a track on intermittent YOLO hits, not only on a
    consecutive streak (tracker_intermittent_confirm; PLAN_25M: ON for the wall duos)."""
    enabled: bool


@dataclass(frozen=True)
class SetTrackingMode(Command):
    """Tracker priority: 'yolo_first' (default for new projects) or 'motion_first'."""
    value: str

    def __post_init__(self):
        if self.value not in ("yolo_first", "motion_first"):
            raise ValueError(f"SetTrackingMode.value must be yolo_first|motion_first, got {self.value!r}")


@dataclass(frozen=True)
class SetSmartHold(Command):
    """Hold earned by reputation (skeleton-backed tracking + movement), no hold for a dancer
    leaving across the ROI border (default ON)."""
    enabled: bool


@dataclass(frozen=True)
class CapturePlate(Command):
    """Record the EMPTY wall for ``frames`` frames (~2 s) as the clean plate (the
    "background snapshot" the dancer ids use as evidence).  The pipeline must be
    running (RUN, or playback of an empty-wall take).  Calibrate does it too."""
    frames: int = 40

    def __post_init__(self):
        if not 9 <= int(self.frames) <= 400:
            raise ValueError("CapturePlate.frames must be within 9..400")


@dataclass(frozen=True)
class SetForeground(Command):
    """Use the empty-wall snapshot (clean plate) as evidence for the dancer ids: ghost
    veto, hold a dancer YOLO lost, back the belt (default ON; no plate = no effect)."""
    enabled: bool


@dataclass(frozen=True)
class SetStaticGhostGuard(Command):
    """Static-ghost guard: a figure that never moves cannot take or keep a slot
    a moving dancer needs (default ON)."""
    enabled: bool


@dataclass(frozen=True)
class SetStaticRelease(Command):
    """Drop a slot that stayed static this long (seconds, 0 = off, 0..60)."""
    value: float

    def __post_init__(self):
        if not 0.0 <= float(self.value) <= 60.0:
            raise ValueError(f"SetStaticRelease.value must be 0..60, got {self.value!r}")


@dataclass(frozen=True)
class SetSlotFilterInput(Command):
    """One-Euro input: 'smoothed' (shipped), 'raw' or 'raw_skeleton' (less lag)."""
    value: str

    def __post_init__(self):
        if self.value not in ("smoothed", "raw", "raw_skeleton"):
            raise ValueError(f"SetSlotFilterInput.value must be smoothed|raw|raw_skeleton, got {self.value!r}")


@dataclass(frozen=True)
class ToggleIrBelt(Command):
    """Use the IR waist-belt detector to keep slots alive (no-op without it)."""
    enabled: bool


@dataclass(frozen=True)
class ToggleOscState(Command):
    """Opt-in OSC /walldance/dancer/state [id, state, age_s]; default OFF."""
    enabled: bool


@dataclass(frozen=True)
class CheckReadiness(Command):
    """Phase-⑤: run the Go-Live readiness checks on demand (OPERATOR_V2 §6)."""
    pass


@dataclass(frozen=True)
class RunDryRunReplay(Command):
    """Phase-⑤: replay the last recording with the current config, offline."""
    pass


@dataclass(frozen=True)
class RunCalibSweep(Command):
    """Phase-④: CLAHE x confidence pass-line sweep over a recording, scored vs the
    operator-confirmed dancer count N (auto-tune the detection enhancement; CLAHE
    has no formula). Runs offline in a subprocess. ``slot`` = the recording slot
    to sweep, or -1 for the newest recording."""
    n: int = 1
    slot: int = -1


@dataclass(frozen=True)
class ApplyCalibSweep(Command):
    """Phase-④: apply the last CalibSweep seed config to the project (save)."""
    pass


@dataclass(frozen=True)
class RunKnownNTune(Command):
    """Phase-④ known-N auto-tune (K1): joint coord-descent over the per-scene
    detection knobs (τ / θ_s / θ_m / tracker_max_age) against the current
    project's labelled scenarios, on the GPU+TRT cache. Runs offline in a
    subprocess (several minutes); reports a KnownNResult, does NOT save (Apply)."""
    pass


@dataclass(frozen=True)
class ApplyKnownNTune(Command):
    """Phase-④: save the last known-N tune result into the project + push live."""
    pass


@dataclass(frozen=True)
class SetPersonHeight(Command):
    value: int


@dataclass(frozen=True)
class SetImgsz(Command):
    value: int


@dataclass(frozen=True)
class SetTrackerMaxAge(Command):
    value: int


@dataclass(frozen=True)
class SetMog2Scale(Command):
    value: float


@dataclass(frozen=True)
class ResetTracker(Command):
    pass


# --- enhancement --------------------------------------------------------

@dataclass(frozen=True)
class ToggleEnhance(Command):
    """enabled=None flips the current value (keyboard shortcut). quiet=True
    reproduces the key path: flip + checkbox sync only, no reprocess."""
    enabled: Optional[bool] = None
    quiet: bool = False


@dataclass(frozen=True)
class ToggleEnhanceLite(Command):
    enabled: bool


@dataclass(frozen=True)
class ToggleEnhanceForce(Command):
    enabled: bool


@dataclass(frozen=True)
class ToggleGreyscale(Command):
    enabled: bool


@dataclass(frozen=True)
class SetEnhanceParam(Command):
    name: str  # brightness_threshold | clahe | gamma | denoise
    value: float

    def __post_init__(self):
        _check_member(self.name, ("brightness_threshold", "clahe", "gamma",
                                  "denoise"), "SetEnhanceParam.name")


# --- background subtraction ---------------------------------------------

@dataclass(frozen=True)
class BgCapture(Command):
    pass


@dataclass(frozen=True)
class BgClear(Command):
    pass


@dataclass(frozen=True)
class ToggleBgSubtract(Command):
    enabled: bool


@dataclass(frozen=True)
class SetBgSensitivity(Command):
    value: float


# --- overlays / preview ---------------------------------------------------

@dataclass(frozen=True)
class ToggleOverlay(Command):
    """enabled=None flips (keyboard shortcuts T/S/K/B/I/C)."""
    name: str  # skeleton | keypoints | bbox | trails | ids | ball
    enabled: Optional[bool] = None

    def __post_init__(self):
        _check_member(self.name, ("skeleton", "keypoints", "bbox", "trails",
                                  "ids", "ball"), "ToggleOverlay.name")


@dataclass(frozen=True)
class TogglePreview(Command):
    """enabled=None flips. quiet=True reproduces the P-key path (flip +
    checkbox sync, no toast/placeholder)."""
    enabled: Optional[bool] = None
    quiet: bool = False


@dataclass(frozen=True)
class ToggleInputFpsCap(Command):
    enabled: bool


@dataclass(frozen=True)
class TogglePreviewCap(Command):
    enabled: bool


@dataclass(frozen=True)
class SetPreviewScale(Command):
    value: float


# --- ROI / mask -----------------------------------------------------------

@dataclass(frozen=True)
class SetRoi(Command):
    """ROI enable/disable from the checkbox (the rect itself is edited by
    mouse drag inside the UI-side editor)."""
    enabled: bool


@dataclass(frozen=True)
class ResetRoi(Command):
    pass


@dataclass(frozen=True)
class EditMask(Command):
    """Toggle the exclusion-mask paint mode."""
    pass


@dataclass(frozen=True)
class ClearMask(Command):
    pass


def _check_unit(name: str, **vals) -> None:
    for k, v in vals.items():
        if not 0.0 <= float(v) <= 1.0:
            raise ValueError(f"{name}.{k} must be 0..1 (normalized frame coords), got {v!r}")


@dataclass(frozen=True)
class SetRoiRect(Command):
    """Set and enable the ROI rect in normalized source-frame coords (0..1),
    the same space as the OSC centroids: remote ROI fixes without the mouse."""
    x: float
    y: float
    w: float
    h: float

    def __post_init__(self):
        _check_unit("SetRoiRect", x=self.x, y=self.y, w=self.w, h=self.h)
        if float(self.w) <= 0.0 or float(self.h) <= 0.0:
            raise ValueError("SetRoiRect.w and .h must be > 0")


@dataclass(frozen=True)
class ExcludeAt(Command):
    """Mask (or with include=True, unmask) the exclusion cell under a
    normalized source-frame point (an OSC centroid), plus ``radius`` cells
    around it: remote ghost-spot fix from a snapshot / the OSC stream."""
    x: float
    y: float
    radius: int = 0
    include: bool = False

    def __post_init__(self):
        _check_unit("ExcludeAt", x=self.x, y=self.y)
        if not 0 <= int(self.radius) <= 4:
            raise ValueError(f"ExcludeAt.radius must be 0..4, got {self.radius!r}")


# --- model / TRT -----------------------------------------------------------

@dataclass(frozen=True)
class LoadModel(Command):
    name: str


@dataclass(frozen=True)
class ToggleTrt(Command):
    enabled: bool


@dataclass(frozen=True)
class RebuildTrt(Command):
    pass


# --- camera -----------------------------------------------------------------

@dataclass(frozen=True)
class SelectSource(Command):
    source: str


@dataclass(frozen=True)
class RefreshCameras(Command):
    pass


@dataclass(frozen=True)
class SetInputTransform(Command):
    """Mirror horizontal / rotate the camera image at the source (REQ-5):
    live camera AND slot playback, before ROI/enhancement/YOLO. None keeps
    the current value. Rotation is clockwise degrees (0/90/180/270); mirror
    flips left-right in the rotated image. Saved with the project."""
    mirror: Optional[bool] = None
    rotation: Optional[int] = None

    def __post_init__(self):
        if self.rotation is not None and self.rotation not in (0, 90, 180, 270):
            raise ValueError("SetInputTransform.rotation must be one of "
                             f"(0, 90, 180, 270), got {self.rotation!r}")
        if self.mirror is not None and not isinstance(self.mirror, bool):
            raise ValueError(f"SetInputTransform.mirror must be a bool, got {self.mirror!r}")


@dataclass(frozen=True)
class SetIdsParam(Command):
    name: str  # ratio | gain_db | exposure_us
    value: float

    def __post_init__(self):
        _check_member(self.name, ("ratio", "gain_db", "exposure_us"),
                      "SetIdsParam.name")


# --- OSC ---------------------------------------------------------------------

@dataclass(frozen=True)
class ToggleOsc(Command):
    enabled: bool


@dataclass(frozen=True)
class SetOscTarget(Command):
    ip: str
    port: int


# --- calibration ---------------------------------------------------------------

@dataclass(frozen=True)
class StartCalibration(Command):
    """CALIBRATE button (also cancels a running calibration)."""
    pass


@dataclass(frozen=True)
class StartDancersRun(Command):
    """CALIB DANCERS button (also cancels a running collection)."""
    pass


@dataclass(frozen=True)
class ApplyCalib2(Command):
    selection: List[str] = field(default_factory=list)
    quiet: bool = False   # True = live subset preview/quiet-apply on a checkbox
                          # toggle (no result modal, no imgsz reload); False =
                          # explicit Apply (full commit incl. imgsz + save modal).


@dataclass(frozen=True)
class ClearCalib2Pool(Command):
    pass


@dataclass(frozen=True)
class ViewCalib2Pool(Command):
    """Open the Calib2 evidence-pool dialog without running a DANCERS pass."""
    pass


@dataclass(frozen=True)
class ViewAimCalibState(Command):
    """Refresh the Aim panel's 'Last calibrated' provenance line (Track S)."""
    pass


# --- config / project -------------------------------------------------------

@dataclass(frozen=True)
class SaveConfig(Command):
    """name=None saves to the current project (Save button / Ctrl+S /
    report-card 'Save to project'); name=str comes from the save-as dialog."""
    name: Optional[str] = None


@dataclass(frozen=True)
class SaveConfigAs(Command):
    """Open the save-as dialog."""
    pass


@dataclass(frozen=True)
class RequestLoadConfigDialog(Command):
    pass


@dataclass(frozen=True)
class LoadConfig(Command):
    filepath: str


@dataclass(frozen=True)
class SelectProject(Command):
    name: str


@dataclass(frozen=True)
class SelectConfigVersion(Command):
    project: str
    display: str


@dataclass(frozen=True)
class SwitchProfile(Command):
    name: str  # show | rehearsal (validated downstream against PROFILE_NAMES)


@dataclass(frozen=True)
class SaveSafeDefaults(Command):
    pass


@dataclass(frozen=True)
class LoadSafeDefaults(Command):
    pass


@dataclass(frozen=True)
class LaunchProject(Command):
    name: str


@dataclass(frozen=True)
class RenameProject(Command):
    old: str
    new: str


@dataclass(frozen=True)
class DeleteProject(Command):
    name: str


@dataclass(frozen=True)
class StartBlankProject(Command):
    pass


# --- recording / playback -----------------------------------------------------

@dataclass(frozen=True)
class PlaybackControl(Command):
    action: str  # live | record_toggle | speed | pause_toggle | force_pause | next_frame | prev_frame
    value: Optional[float] = None

    def __post_init__(self):
        _check_member(self.action, ("live", "record_toggle", "speed",
                                    "pause_toggle", "force_pause",
                                    "next_frame", "prev_frame"),
                      "PlaybackControl.action")


@dataclass(frozen=True)
class SelectSlot(Command):
    slot: int
    history: bool = False  # Ctrl+click -> history menu


@dataclass(frozen=True)
class PlaySlotRecording(Command):
    slot: int
    path: str


@dataclass(frozen=True)
class StartRecordingSlot(Command):
    """Record into ``slot`` in one step (goes LIVE first if playing back) --
    the remote API's REC; the GUI keeps its arm-then-click flow."""
    slot: int

    def __post_init__(self):
        if not 1 <= int(self.slot) <= 9:
            raise ValueError(f"StartRecordingSlot.slot must be 1-9, got {self.slot!r}")


@dataclass(frozen=True)
class StopRecording(Command):
    pass


IMPORT_MODES = ("auto", "copy", "transcode")


@dataclass(frozen=True)
class ImportVideoToSlot(Command):
    """Import an external video file into recording slot ``slot`` (REQ-1).
    Copied into the project's recordings/ as the slot's newest take (older
    takes stay in the Ctrl+click history), with a ``.meta`` sidecar. Runs on
    a worker thread; progress arrives as ImportProgress events.

    mode: ``auto`` = byte copy for .avi/.mp4, transcode anything else
    (.mov/.mkv/...) to MJPG .avi; ``copy`` / ``transcode`` force one."""
    slot: int
    path: str
    mode: str = "auto"

    def __post_init__(self):
        if not 1 <= int(self.slot) <= 9:
            raise ValueError(f"ImportVideoToSlot.slot must be 1-9, got {self.slot!r}")
        if not str(self.path or "").strip():
            raise ValueError("ImportVideoToSlot.path is empty")
        _check_member(self.mode, IMPORT_MODES, "ImportVideoToSlot.mode")


# --- rig sheet (MRK-0) ----------------------------------------------------------

@dataclass(frozen=True)
class SetRigSheet(Command):
    """One rig-sheet field (lens, f_number, illuminator offset...); validated
    against ``core.config_schema.RIG_FIELDS`` by the handler. None clears."""
    field: str
    value: Any = None
    echo: bool = False   # push the cleaned value back to the GUI (remote edits)


# --- review / misc -----------------------------------------------------------

@dataclass(frozen=True)
class RequestIssueReport(Command):
    """Build the playback context; the dialog opens via IssueReportContext."""
    pass


@dataclass(frozen=True)
class SubmitIssue(Command):
    context: Dict[str, Any]
    issue_type: str
    note: str


@dataclass(frozen=True)
class IssueDialogClosed(Command):
    pass


@dataclass(frozen=True)
class ShowQr(Command):
    pass


@dataclass(frozen=True)
class SetRemoteControl(Command):
    """Operator-only (phase 6 Live checkbox): allow the remote API's control
    commands while in RUN. Deliberately NOT on the remote allowlist."""
    enabled: bool


# ======================================================================
# Events (runtime -> UI)
# ======================================================================

class Event(_Payload):
    pass


@dataclass(frozen=True)
class StateChanged(Event):
    state: str  # 'standby' | 'run'


@dataclass(frozen=True)
class ReadinessResult(Event):
    """Phase-⑤ readiness rows for the Verify panel: [{name, status, detail}]."""
    rows: List[Dict[str, Any]]


@dataclass(frozen=True)
class DryRunResult(Event):
    """Phase-⑤ dry-run replay summary (lean replay metrics) or an error."""
    summary: Dict[str, Any]
    error: str = ""


@dataclass(frozen=True)
class CalibSweepResult(Event):
    """Phase-④ auto-tune sweep result: {clahe_curve, best_clahe, conf_curve,
    best_conf, derived, merged_config} or an error."""
    result: Dict[str, Any]
    error: str = ""


@dataclass(frozen=True)
class KnownNResult(Event):
    """Phase-④ known-N tune result: {project, baseline_score, tuned_score, delta,
    final, changed, evals} or an error."""
    result: Dict[str, Any]
    error: str = ""


@dataclass(frozen=True)
class DialBVisible(Event):
    """Phase-⑥: show/hide Dial B (gap-bridging) on the live surface. Calibration
    hides it when the scene's drop-rate says gap-bridging is inert; the raw
    motion_sensitivity slider stays in Advanced (OPERATOR_V2 P3)."""
    visible: bool = True


@dataclass(frozen=True)
class StatsTick(Event):
    """Per-tick stats blob; payload mirrors WallDanceGUI.update_stats kwargs."""
    payload: Dict[str, Any]


@dataclass(frozen=True)
class OutputLatency(Event):
    """Phase-⑥ lagged-tap status (Track X §7): the published output latency in
    ms (0 when the lagged tap is inactive).  Drives the latency readout next to
    the output-smoothing slider."""
    latency_ms: float
    enabled: bool = False


@dataclass(frozen=True)
class GpuStats(Event):
    """Trigger a GPU stats refresh (the GUI reads pynvml itself)."""
    pass


@dataclass(frozen=True)
class CameraStatus(Event):
    is_open: bool
    source: str
    reconnecting: bool = False


@dataclass(frozen=True)
class CameraSources(Event):
    sources: List[str]
    current: str
    unavailable: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CameraType(Event):
    camera_type: str  # 'IDS_PEAK' | 'OPENCV' | ''


@dataclass(frozen=True)
class CameraDimensions(Event):
    width: int
    height: int


@dataclass(frozen=True)
class EngineBadge(Event):
    is_trt: bool


@dataclass(frozen=True)
class TrtBanner(Event):
    text: Optional[str]
    exporting: bool = False


@dataclass(frozen=True)
class TrtCheckbox(Event):
    enabled: bool


@dataclass(frozen=True)
class ComputeModeBadge(Event):
    reason: str  # '' = GPU path active


@dataclass(frozen=True)
class ModelDropdown(Event):
    name: str


@dataclass(frozen=True)
class ModelLoadModal(Event):
    message: str


@dataclass(frozen=True)
class ModelLoadProgress(Event):
    message: str
    progress: float
    detail: str = ""
    animate: bool = False


@dataclass(frozen=True)
class ModelLoadModalHide(Event):
    pass


@dataclass(frozen=True)
class CalibProgress(Event):
    text: Optional[str]  # None = idle (clears the status line)


@dataclass(frozen=True)
class CalibReportCard(Event):
    """Structured-enough for the desktop dialog today; 'Save to project'
    answers with a SaveConfig command."""
    summary: str


@dataclass(frozen=True)
class Calib2PoolChanged(Event):
    rows: List[Dict[str, Any]]  # {path, label, stale}
    proposal: str


@dataclass(frozen=True)
class Calib2ProposalUpdated(Event):
    """In-place refresh of the inline pool's proposal text after a checked-subset
    recompute (checkbox toggle) — does NOT re-render the run list/checkboxes."""
    proposal: str


@dataclass(frozen=True)
class AimCalibStateChanged(Event):
    """The Aim panel's 'Last calibrated' provenance line (Track S)."""
    text: str


@dataclass(frozen=True)
class ConfigSaved(Event):
    message: str  # save-indicator text ('Saved!', 'Safe defaults saved!', ...)


@dataclass(frozen=True)
class ConfigList(Event):
    configs: List[Any]  # (display, filepath) pairs
    current_display: str = ""


@dataclass(frozen=True)
class CurrentConfig(Event):
    display: str


@dataclass(frozen=True)
class ProjectList(Event):
    projects: List[str]
    current: str = ""


@dataclass(frozen=True)
class ProjectPicker(Event):
    rows: List[Any]  # (name, last_saved_display, save_count) tuples
    last_project: str = ""


@dataclass(frozen=True)
class SaveConfigDialog(Event):
    project: str


@dataclass(frozen=True)
class LoadConfigDialog(Event):
    config_dir: str
    project: str


@dataclass(frozen=True)
class ActiveProfile(Event):
    name: str


@dataclass(frozen=True)
class RecordingUi(Event):
    """Payload mirrors WallDanceGUI.update_recording_ui kwargs."""
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ImportProgress(Event):
    """REQ-1 video import status. state: started | progress | done | error.
    ``progress`` in [0, 1]; ``dest`` is the new slot file once done."""
    state: str
    slot: int
    source: str
    message: str = ""
    progress: float = 0.0
    dest: str = ""

    def __post_init__(self):
        _check_member(self.state, ("started", "progress", "done", "error"),
                      "ImportProgress.state")


@dataclass(frozen=True)
class SlotHistory(Event):
    slot: int
    recordings: List[Any]  # (display, filepath) pairs; pick answers with PlaySlotRecording


@dataclass(frozen=True)
class BgStatus(Event):
    has_reference: bool
    enabled: bool
    fg_ratio: float = 0.0
    mismatched: bool = False


@dataclass(frozen=True)
class ControlSync(Event):
    """Push a control value into the GUI without firing its callback."""
    kind: str  # slider | checkbox | combo | input
    name: str
    value: Any

    def __post_init__(self):
        _check_member(self.kind, ("slider", "checkbox", "combo", "input"),
                      "ControlSync.kind")


@dataclass(frozen=True)
class Toast(Event):
    message: str
    duration: float = 3.0
    color: Tuple[int, int, int] = (255, 200, 100)  # gui_constants.WARN_AMBER


@dataclass(frozen=True)
class Alert(Event):
    """Ops alert (health monitor); the desktop adapter renders it as a toast."""
    kind: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertCleared(Event):
    """The condition behind an ops alert ended (e.g. ``light_changed``): drop its strip entry."""
    kind: str


@dataclass(frozen=True)
class IssueReportContext(Event):
    context: Dict[str, Any]


@dataclass(frozen=True)
class RemoteStatus(Event):
    """Remote API state for the top-bar REMOTE chip + Live checkbox."""
    active: bool            # an authenticated client in the last few seconds
    control_enabled: bool
    client: str = ""


@dataclass(frozen=True)
class QrDialog(Event):
    url: str
    matrix: Optional[List[Any]] = None  # rows of bools, or None


@dataclass(frozen=True)
class PreviewFrame(Event):
    """The documented non-JSON event: carries the BGR ndarray for the
    in-process preview. Web/tablet clients consume the MJPEG path instead."""
    frame: Any

    def to_dict(self) -> Dict[str, Any]:
        shape = getattr(self.frame, "shape", None)
        return {"type": "PreviewFrame", "shape": list(shape) if shape else None}


@dataclass(frozen=True)
class PreviewResize(Event):
    width: int
    height: int


# ======================================================================
# Bus + queue
# ======================================================================

class EventBus:
    """Synchronous fan-out of runtime events to all subscribers.

    A subscriber exception is printed, never propagated -- a UI rendering
    problem must not kill the show pipeline (DECOMPOSITION_PLAN §1.2).

    ``ui_ready`` mirrors the old ``app.gui is not None`` availability checks
    used by the controllers' UI ports: False until the DPG adapter has built
    the GUI (events published earlier are dropped by the adapter anyway).
    """

    def __init__(self) -> None:
        self._subscribers: List[Callable[[Event], None]] = []
        self.ui_ready = False

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        self._subscribers.append(fn)

    def publish(self, event: Event) -> None:
        for fn in self._subscribers:
            try:
                fn(event)
            except Exception:
                print(f"[EventBus] subscriber failed on {type(event).__name__}:")
                traceback.print_exc()


class RuntimeAPI:
    """Thread-safe command queue, drained at one point in the main loop tick.

    ``submit`` may be called from any thread (DPG callback thread, decoder
    thread, ...). ``drain`` snapshots the queue and dispatches on the caller
    (main-loop) thread; commands submitted *during* a drain run next tick,
    bounding re-entrancy from handlers that pump the GUI. A handler exception
    is printed, never propagated (parity with DPG's callback-thread behavior,
    where a raise never reached the main loop).
    """

    def __init__(self) -> None:
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._handlers: Dict[Type[Command], Callable[[Any], None]] = {}

    def register(self, command_type: Type[Command],
                 handler: Callable[[Any], None]) -> None:
        if command_type in self._handlers:
            raise ValueError(f"duplicate handler for {command_type.__name__}")
        self._handlers[command_type] = handler

    def submit(self, command: Command) -> None:
        with self._lock:
            self._queue.append(command)

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def drain(self) -> int:
        """Execute queued commands (FIFO). Returns the number executed."""
        with self._lock:
            if not self._queue:
                return 0
            batch = list(self._queue)
            self._queue.clear()
        for command in batch:
            handler = self._handlers.get(type(command))
            if handler is None:
                print(f"[RuntimeAPI] no handler registered for "
                      f"{type(command).__name__} -- command dropped")
                continue
            try:
                handler(command)
            except Exception:
                print(f"[RuntimeAPI] handler failed for {type(command).__name__}:")
                traceback.print_exc()
        return len(batch)
