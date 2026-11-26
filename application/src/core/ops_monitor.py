"""Ops cluster (TODO Phase 7): show readiness, health alerts, loop watchdog.

Three independent pieces, all GUI-free and hardware-free (every external
input is injected) so the logic is unit-testable without a rig:

- Readiness checks: pure functions returning CheckResult, aggregated into a
  ReadinessReport printed at Go-Live. Best-effort and non-blocking - a bad
  result never prevents RUN.
- HealthMonitor: fed once per second from the main loop; emits Alerts
  (fps drop, no detection, camera down, GPU temp) with sustain windows and
  per-kind cooldowns. Clock is passed in, so tests advance a float.
- LoopWatchdog: a daemon thread observing a heartbeat the main loop updates;
  reports hangs (with a faulthandler stack dump) but takes no recovery
  actions itself. Long legitimate blocks (model loads, project switches)
  are suppressed via push_busy()/pop_busy().
"""
import faulthandler
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import shutil

from core.config import (
    LIGHT_CHANGE_RATIO,
    OPS_ALERT_COOLDOWN_S,
    OPS_CAMERA_DOWN_ALERT_S,
    OPS_FPS_BASELINE_WINDOW_S,
    OPS_FPS_DROP_FRACTION,
    OPS_FPS_DROP_SUSTAIN_S,
    OPS_GPU_POLL_S,
    OPS_GPU_TEMP_ALERT_C,
    OPS_GPU_TEMP_SUSTAIN_S,
    OPS_HEIGHT_STALE_S,
    OPS_NO_DETECTION_ALERT_S,
    OPS_OVER_CAP_ALERT_S,
    OPS_WATCHDOG_HANG_S,
    OPS_WATCHDOG_POLL_S,
    RECORDING_CODEC,
    RECORDING_GB_PER_HOUR,
)

# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------

# H3 (#t-026 ④): « skip » is a row that was NOT CHECKED (not measured, or not applicable).  It is never green:
# it ranks above ok when the rows fold into one worst state (``worst_status``, which H2 can reuse), it has its
# own words and its own neutral colour, and a report with one is never called READY (``readiness_verdict``).
_STATUS_ORDER = {"ok": 0, "skip": 1, "warn": 2, "fail": 3}
STATUS_WORDS = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "skip": "NOT CHECKED"}
NOT_CHECKED_COLOR = (150, 170, 215)     # a neutral blue-grey: readable, and neither green nor a hint's grey
STATUS_COLORS = {"ok": (120, 255, 120), "warn": (255, 200, 100), "fail": (255, 80, 80),
                 "skip": NOT_CHECKED_COLOR}
_VERDICT_HEAD = {"ok": "READY", "skip": "NOT ALL CHECKED", "warn": "CHECK", "fail": "NOT READY"}


def worst_status(statuses) -> str:
    """The worst of row statuses: fail > warn > skip (not checked) > ok; an unknown status counts as fail.
    No rows: skip (nothing was checked)."""
    statuses = list(statuses)
    if not statuses:
        return "skip"
    return max(statuses, key=lambda s: _STATUS_ORDER.get(s, _STATUS_ORDER["fail"]))


def readiness_verdict(rows) -> Tuple[str, str]:
    """H3: (state, one line) for readiness rows -- CheckResults, or (name, status) pairs.  ``state`` is
    ``worst_status``; the line starts with READY only when every row is ok, and names the rows that are not
    (« NOT ALL CHECKED - 9 ok, 2 not checked (dancer size, gpu) »)."""
    pairs = [(r.name, r.status) if isinstance(r, CheckResult) else (str(r[0]), str(r[1])) for r in rows]
    state = worst_status(s for _n, s in pairs)
    if not pairs:
        return state, f"{_VERDICT_HEAD[state]} - no rows"
    parts = []
    for st, word in (("ok", "ok"), ("fail", "fail"), ("warn", "warn"), ("skip", "not checked")):
        names = [n for n, s in pairs if s == st]
        if names:
            shown = ", ".join(names[:3]) + (", ..." if len(names) > 3 else "")
            parts.append(f"{len(names)} {word}" + ("" if st == "ok" else f" ({shown})"))
    others = [n for n, s in pairs if s not in _VERDICT_HEAD]
    if others:
        parts.append(f"{len(others)} unknown ({', '.join(others[:3])})")
    return state, f"{_VERDICT_HEAD.get(state, 'NOT READY')} - " + ", ".join(parts)


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail" | "skip" (not checked: never green, see readiness_verdict)
    detail: str


@dataclass
class ReadinessReport:
    results: List[CheckResult]

    @property
    def worst(self) -> str:
        return worst_status(r.status for r in self.results) if self.results else "ok"

    def counts(self) -> Dict[str, int]:
        out = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
        for r in self.results:
            out[r.status] += 1
        return out

    def verdict(self) -> Tuple[str, str]:
        return readiness_verdict(self.results)

    def console_block(self, header: str = "") -> str:
        lines = [f"[Readiness] ===== Go-Live check {header}====="]
        for r in self.results:
            lines.append(f"[Readiness] {r.status:<5} {r.name:<12} {r.detail}")
        c = self.counts()
        lines.append(
            f"[Readiness] ===== {c['ok']} ok, {c['warn']} warn, {c['fail']} fail, "
            f"{c['skip']} not checked: {self.verdict()[1].split(' - ')[0]} =====")
        return "\n".join(lines)

    def toast_summary(self) -> Tuple[str, Tuple[int, int, int]]:
        state, line = self.verdict()
        return f"Go-Live check: {line}", STATUS_COLORS.get(state, STATUS_COLORS["fail"])


def check_camera(*, is_open: bool, reconnecting: bool, source: str, fps: float,
                 min_fps: float, ids_frames: int = 0,
                 ids_dropped: int = 0) -> CheckResult:
    if not is_open or reconnecting:
        state = "reconnecting" if reconnecting else "not open"
        return CheckResult("camera", "fail", f"{source}: {state}")
    detail = f"{source} @ {fps:.1f} FPS"
    if ids_frames:
        detail += f" (acquired={ids_frames}, dropped={ids_dropped})"
    if fps < min_fps:
        return CheckResult("camera", "warn",
                           detail + f" - below the {min_fps:.0f} FPS show floor")
    return CheckResult("camera", "ok", detail)


def check_tensorrt(*, trt_requested: bool, trt_active: bool,
                   fallback_reason: Optional[str] = None,
                   gpu_fallback_reason: str = "",
                   engine_present: Optional[bool] = None,
                   engine_name: str = "") -> CheckResult:
    """``engine_name``: the engine file the current model/imgsz needs (e.g.
    ``yolo11x-pose_1280.engine``), named in the detail so the operator knows
    which one to build."""
    engine = f" {engine_name}" if engine_name else ""
    if trt_active:
        detail = f"engine{engine} active"
        if gpu_fallback_reason:
            return CheckResult("tensorrt", "warn",
                               detail + f" (GPU path degraded: {gpu_fallback_reason})")
        return CheckResult("tensorrt", "ok", detail)
    if trt_requested:
        # Track P (GPU-only): TRT is the show path — running PyTorch instead is a
        # pre-show FAIL (slower, and not what the goldens/calibration were tuned
        # on).  Still non-blocking: the operator may knowingly proceed on PyTorch.
        if engine_present is False:
            return CheckResult("tensorrt", "fail",
                               f"requested but the engine{engine} is MISSING — running "
                               "PyTorch (3-7x slower, not the show path). Build the "
                               "engine (Model panel, or extra/build_engines) before the show.")
        reason = fallback_reason or "unknown reason"
        return CheckResult("tensorrt", "fail",
                           f"requested but running PyTorch ({reason}) — not the "
                           "show path; rebuild/repair the engine.")
    # PyTorch by choice.  D33: the default x@1280 on PyTorch is 3-7x slower than
    # the show path -- say so (a warning: the operator unticked TensorRT).
    if engine_present is False:
        return CheckResult("tensorrt", "warn",
                           f"not requested (PyTorch, 3-7x slower) and the engine{engine} "
                           "is MISSING: build it (extra/build_engines) and tick TensorRT "
                           "for the show.")
    if engine_present:
        return CheckResult("tensorrt", "warn",
                           f"not requested (PyTorch, 3-7x slower); the engine{engine} "
                           "exists: tick TensorRT for the show.")
    return CheckResult("tensorrt", "ok", "not requested (PyTorch)")


def check_input_shape(*, requested: Optional[Tuple[int, int]], running: Optional[Tuple[int, int]],
                      imgsz: int, fallback_reason: Optional[str] = None,
                      roi_w: int = 0, roi_h: int = 0) -> CheckResult:
    """P1: the network's input -- the square ``imgsz`` or the project's rectangle (height x width).

    A rectangle the project asks for but cannot run (no engine) is a warning: the square runs instead, with
    the dancer smaller at the network.  The detail gives the letterbox padding share for the ROI."""
    from core.input_shape import padding_share, pick_shape, shape_str
    pad = ""
    if roi_w > 0 and roi_h > 0:
        pad = f", {100 * padding_share(roi_w, roi_h, imgsz, running):.0f} % padding for the {roi_w}x{roi_h} ROI"
    if requested and running != requested:
        return CheckResult("net input", "warn",
                           f"the project asks for {shape_str(requested)} but the square {imgsz} runs"
                           + (f" ({fallback_reason})" if fallback_reason else "") + pad)
    if running:
        return CheckResult("net input", "ok", f"{shape_str(running)} (rectangle){pad}")
    hint = ""
    if roi_w > 0 and roi_h > 0:
        better = pick_shape(roi_w, roi_h)
        if better:
            hint = f"; this ROI's shape suits yolo_shape {shape_str(better)}"
    return CheckResult("net input", "ok", f"square {imgsz}{pad}{hint}")


def probe_osc_udp(ip: str, port: int, timeout_s: float) -> Tuple[str, str]:
    """Best-effort UDP reachability probe on a private socket.

    A connected UDP socket surfaces ICMP port-unreachable as
    ConnectionResetError (Windows WSAECONNRESET) / ConnectionRefusedError
    (Linux), which is deterministic on loopback and most LANs. A silent
    timeout proves nothing either way (UDP), so it reads as ok with an
    honest caveat. Sends a valid no-argument OSC message to an address no
    consumer matches, so a live media server is unaffected.
    """
    # "/walldance/ping" padded to 4 bytes + ",," type tag header
    payload = b"/walldance/ping\x00,\x00\x00\x00"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout_s)
            s.connect((ip, port))
            s.send(payload)
            try:
                s.recv(64)
                return "ok", f"{ip}:{port} responded to the probe"
            except (ConnectionResetError, ConnectionRefusedError):
                return "fail", f"host reachable but nothing listening on {ip}:{port}"
            except socket.timeout:
                return "ok", (f"probe sent to {ip}:{port} "
                              "(UDP - listener cannot be confirmed)")
    except OSError as e:
        return "fail", f"{ip}:{port} unreachable ({e})"


def check_osc(*, enabled: bool, ip: str, port: int, timeout_s: float,
              probe: Callable[[str, int, float], Tuple[str, str]] = probe_osc_udp
              ) -> CheckResult:
    if not enabled:
        return CheckResult("osc", "skip", "disabled")
    status, detail = probe(ip, port, timeout_s)
    return CheckResult("osc", status, detail)


def check_calibration(*, saved_at_iso: Optional[str], active_profile: str,
                      warn_age_h: float,
                      mask_cells: Optional[int] = None,
                      now: Optional[datetime] = None) -> CheckResult:
    mask_note = (f", exclusion mask {mask_cells} cell(s)"
                 if mask_cells is not None else "")
    if not saved_at_iso:
        return CheckResult("calibration", "warn",
                           f"no saved config found (profile: {active_profile})"
                           + mask_note)
    try:
        saved = datetime.fromisoformat(saved_at_iso)
    except ValueError:
        return CheckResult("calibration", "warn",
                           f"unreadable save timestamp {saved_at_iso!r}")
    now = now or datetime.now()
    age_h = (now - saved).total_seconds() / 3600.0
    detail = f"saved {age_h:.1f} h ago (profile: {active_profile}){mask_note}"
    if age_h > warn_age_h:
        return CheckResult("calibration", "warn",
                           detail + " - consider recalibrating after a re-rig")
    return CheckResult("calibration", "ok", detail)


def light_ratio_text(ratio: float) -> str:
    """"0.40x" -- or "<= 0.25x" / ">= 4x" beyond what the plate's gain normalisation follows."""
    if ratio <= 0.25:
        return "<= 0.25x"
    if ratio >= 4.0:
        return ">= 4x"
    return f"{ratio:.2f}x"


def light_out_of_band(ratio: Optional[float], band: float = LIGHT_CHANGE_RATIO) -> bool:
    return ratio is not None and not (1.0 / band <= ratio <= band)


LIGHT_REMEDY = ("Calibrate on the empty wall: live if the stage can be cleared, else play this "
                "lighting's empty take and Calibrate on it")


def check_empty_wall(*, status: str, detail: str = "", light_ratio: Optional[float] = None,
                     band: float = LIGHT_CHANGE_RATIO) -> CheckResult:
    """The empty-wall snapshot (clean plate, D29): present, from this camera crop, and the scene
    still looks like it.  ``status`` is FrameProcessor.plate_status; ``detail`` its readable line;
    ``light_ratio`` the live / snapshot brightness (the ops tick's 10 s median), None if unknown."""
    ok = status in ("ready", "capturing")
    if ok and light_out_of_band(light_ratio, band):
        return CheckResult("empty wall", "warn",
                           f"{detail or 'empty wall: ' + status} - the light is "
                           f"{light_ratio_text(light_ratio)} the snapshot: {LIGHT_REMEDY}")
    return CheckResult("empty wall", "ok" if ok else "warn",
                       detail or f"empty wall: {status}"
                       + ("" if ok else " - Calibrate on the empty wall (2 - Calibrate)"))


def check_dancer_size(*, measured: Optional[Tuple[float, int, float]], lb_scale: float, imgsz: int,
                      target_px: float = 100.0, sizes: Tuple[int, ...] = (960, 1280, 1536),
                      now: Optional[float] = None, max_age_s: float = 1800.0,
                      shape: Optional[Tuple[int, int]] = None, roi_w: int = 0, roi_h: int = 0) -> CheckResult:
    """The dancers' height in YOLO's input (D29; Calib2's image-size rule without the dancers pass).

    ``measured`` = FrameProcessor.dancer_height (median px of the confident full skeletons, samples, wall
    time), ``lb_scale`` = original px -> YOLO-input px.  YOLO wants the dancer at ~100 px in its input
    (Phase 2b: quality knee 83-110 px).  Below that: the ROI long side that would reach it at the current
    size, and the smallest image size that would (1920 is too slow on the show laptop).

    P4 (Plan Cordée rev 2): ``shape`` is the rectangle the network runs (P1), None for the square; with the
    ROI's size, a square that leaves the dancer small names the rectangle that would bring her up first."""
    import time as _time
    from core.input_shape import letterbox_scale, pick_shape, shape_str
    now = _time.time() if now is None else now
    if not measured or not lb_scale or now - measured[2] > max_age_s:
        return CheckResult("dancer size", "skip",
                           "not measured - have a dancer stand at the far wall for 10 s, then check again")
    h = float(measured[0])
    net = h * float(lb_scale)
    where = shape_str(shape) if shape else str(imgsz)
    detail = f"dancers ~{h:.0f} px -> ~{net:.0f} px in YOLO's input at {where}"
    if net >= target_px:
        return CheckResult("dancer size", "ok", detail)
    tips = []
    if not shape and roi_w > 0 and roi_h > 0:
        rect = pick_shape(roi_w, roi_h)
        if rect:
            rect_net = h * letterbox_scale(roi_w, roi_h, imgsz, rect)
            tips.append(f"the ROI's own shape: yolo_shape {shape_str(rect)} gives ~{rect_net:.0f} px")
    if shape:
        if roi_w > 0 and roi_h > 0:
            tips.append(f"narrow the ROI to the dancers' area ({roi_w}x{roi_h} now)")
        return CheckResult("dancer size", "warn", f"{detail}, below the ~{target_px:.0f} px YOLO needs: "
                           + "; ".join(tips or ["narrow the ROI to the dancers' area"]))
    roi_long = float(imgsz) / float(lb_scale)            # the letterbox fits the ROI's long side
    roi_ok = h * float(imgsz) / target_px
    bigger = [s for s in sizes if s > imgsz and net * s / float(imgsz) >= target_px]
    tips.append(f"{'or ' if tips else ''}narrow the ROI to the dancers' area (long side {roi_long:.0f} -> <= {roi_ok:.0f} px)")
    if bigger:
        tips.append(f"or Image Size {bigger[0]} (Advanced > Model)")
    elif imgsz < max(sizes):
        tips.append(f"Image Size {max(sizes)} gives ~{net * max(sizes) / float(imgsz):.0f} px")
    return CheckResult("dancer size", "warn", f"{detail}, below the ~{target_px:.0f} px YOLO needs: "
                       + "; ".join(tips))


def recording_gb_per_hour(codec: str = RECORDING_CODEC) -> Tuple[str, float]:
    """(codec actually used, estimated GB/h at the show resolution and fps).

    Resolves the codec like the recorder does (case-insensitive, unknown ->
    MJPG), so the estimate matches what a recording would really write."""
    table = {k.lower(): k for k in RECORDING_GB_PER_HOUR}
    name = table.get(str(codec).lower(), "MJPG")
    return name, float(RECORDING_GB_PER_HOUR[name])


def check_disk(*, recordings_dir: str, warn_free_gb: float, fail_free_gb: float,
               disk_usage: Callable = shutil.disk_usage,
               codec: str = RECORDING_CODEC) -> CheckResult:
    try:
        usage = disk_usage(recordings_dir)
    except OSError as e:
        return CheckResult("disk", "warn", f"could not stat {recordings_dir} ({e})")
    free_gb = usage.free / 1e9
    codec, gb_per_h = recording_gb_per_hour(codec)
    hours = free_gb / gb_per_h
    detail = (f"{free_gb:.1f} GB free for recordings (~{hours:.1f} h of "
              f"{codec} at ~{gb_per_h:g} GB/h)")
    if free_gb < fail_free_gb:
        return CheckResult("disk", "fail", detail)
    if free_gb < warn_free_gb:
        return CheckResult("disk", "warn", detail)
    return CheckResult("disk", "ok", detail)


def check_gpu_temp(*, gpu_stats: Optional[dict], warn_c: int) -> CheckResult:
    if not gpu_stats or gpu_stats.get("util", -1) < 0:
        return CheckResult("gpu", "skip", "no NVIDIA GPU stats available")
    temp = gpu_stats.get("temp", -1)
    detail = f"{temp} C, {gpu_stats.get('util', 0)}% util"
    if gpu_stats.get("vram_pct", -1) >= 0:
        detail += f", {gpu_stats['vram_pct']:.0f}% VRAM"
    if temp >= warn_c:
        return CheckResult("gpu", "warn", detail + f" - at/above {warn_c} C")
    return CheckResult("gpu", "ok", detail)


# --------------------------------------------------------------------------
# H3 (#t-026 ④): the setup rows -- the enhancement, the ROI, the plate's exposure
# --------------------------------------------------------------------------

ENHANCE_ON_HOW = "Ctrl+Shift+E, Advanced > Enhancement > Enable"


def check_enhancement(*, enabled: bool, gamma: float, clahe: float, lite: bool = False, force: bool = True,
                      brightness: Optional[float] = None, threshold: float = 60.0,
                      calibrated: str = "") -> CheckResult:
    """The enhancement YOLO's picture gets: on / off, which (gamma, CLAHE), and red when it is OFF on a project
    whose Calibrate chose one that changes the picture (08/10: switched off by one key, worth ~18 points of
    recall, and nothing said so).  ``calibrated``: when Calibrate (or the dancer step) last set the gamma or the
    CLAHE of this project ("2026-10-08 21:32"), "" when never.  ``brightness``: the picture's, for the
    brightness gate (only when ``force`` is off), None when unknown."""
    which = (f"gamma {gamma:.2f}, no CLAHE (lite)" if lite else f"gamma {gamma:.2f} / CLAHE {clahe:.1f}")
    changes = abs(float(gamma) - 1.0) > 1e-3 or (not lite and float(clahe) > 1.0)
    if not enabled:
        if calibrated and changes:
            return CheckResult("enhancement", "fail",
                               f"OFF, but Calibrate chose {which} for this project ({calibrated}): turn it on - "
                               + ENHANCE_ON_HOW)
        if calibrated:
            return CheckResult("enhancement", "ok", f"off; Calibrate chose none here ({which}, {calibrated})")
        return CheckResult("enhancement", "warn",
                           f"OFF, and Calibrate never chose one for this project ({which}): Calibrate on the empty "
                           f"wall, then turn it on - {ENHANCE_ON_HOW}")
    by = f"Calibrate {calibrated}" if calibrated else "not chosen by Calibrate on this project"
    if not changes:
        return CheckResult("enhancement", "ok", f"on, neutral ({which}, {by}): the picture goes to YOLO as it is")
    if not force and brightness is not None and brightness >= threshold:
        return CheckResult("enhancement", "warn",
                           f"on ({which}) but faded out: the picture's brightness {brightness:.0f} is over its gate "
                           f"{threshold:.0f} - tick Force (expert mode, Advanced > Enhancement)")
    if not calibrated:
        return CheckResult("enhancement", "warn", f"on ({which}), {by} - Calibrate on the empty wall")
    return CheckResult("enhancement", "ok", f"on: {which} ({by})")


def check_roi(*, enabled: bool, drawn: Optional[Tuple[int, ...]], live: Optional[Tuple[int, int, int, int]] = None,
              frame_size: Optional[Tuple[int, int]] = None) -> CheckResult:
    """The zone YOLO watches (the ROI) and whether it still fits the camera's crop.  ``drawn`` = (x, y, w, h,
    src_w, src_h), the zone as drawn or loaded in its own picture (RoiState.stored_roi); ``live`` = the zone on
    the frames flowing (settings.roi_*: clamped onto them); ``frame_size`` = their (W, H), None with no
    picture.  The crop changed since the zone was drawn: orange when the zone still fits (it may frame
    another part of the wall), red when the new crop cuts it."""
    where = f"{frame_size[0]}x{frame_size[1]} picture" if frame_size else "picture"
    if not enabled:
        return CheckResult("ROI", "warn", f"off: YOLO watches the whole {where} - draw the dancers' wall "
                                          "(1 Rig > Region of Interest)")
    if not drawn or len(drawn) < 6 or int(drawn[2]) <= 0 or int(drawn[3]) <= 0:
        return CheckResult("ROI", "fail", "the zone is empty - draw the dancers' wall (1 Rig > Region of Interest)")
    x, y, w, h, sw, sh = (int(v) for v in drawn[:6])
    zone = f"{w}x{h} at ({x}, {y})"
    if not frame_size:
        return CheckResult("ROI", "skip", f"zone {zone}, drawn on a {sw}x{sh} picture; no picture now, so not "
                                          "checked against the camera's crop")
    W, H = int(frame_size[0]), int(frame_size[1])
    if (sw, sh) == (W, H):
        share = 100.0 * w * h / max(1, W * H)
        return CheckResult("ROI", "ok", f"{zone} on the {W}x{H} picture ({share:.0f} % of it)")
    if x + w <= W and y + h <= H:
        return CheckResult("ROI", "warn", f"zone {zone} drawn on a {sw}x{sh} picture, the camera gives {W}x{H}: it "
                                          "may frame another part of the wall - check it on the picture (1 Rig)")
    lw, lh = (int(live[2]), int(live[3])) if live else (max(0, min(w, W - x)), max(0, min(h, H - y)))
    return CheckResult("ROI", "fail", f"zone {zone} drawn on a {sw}x{sh} picture, cut to {lw}x{lh} by the "
                                      f"camera's {W}x{H}: draw it again (1 Rig > Region of Interest), then Calibrate")


def check_plate_exposure(*, plate=None, exposure_us: Optional[float] = None, gain_db: Optional[float] = None,
                         frame_size: Optional[Tuple[int, int]] = None, invalid: str = "", pending: str = "",
                         capturing: bool = False, source: str = "") -> CheckResult:
    """The empty-wall snapshot really loaded (``plate``: FrameProcessor's CleanPlate, the project's own or A2's
    live.npz) against the camera now: its exposure, gain (X6 stores them in the plate) and crop (its frame size)
    -- the rule RUN applies (CleanPlate.camera_mismatch, FrameProcessor._fg_frame), said before RUN.
    ``exposure_us`` / ``gain_db``: the camera's (CameraController.exposure_gain_now; None = auto or not an IDS
    camera); ``frame_size``: the frames flowing (None: no picture); ``invalid``: X5's reason (exposure, gain or
    crop changed by hand); ``pending``: the plate file the project names, not loaded yet."""
    name = "plate exposure"
    if capturing:
        return CheckResult(name, "skip", "the empty wall is being captured now")
    if pending:
        return CheckResult(name, "skip", f"the empty-wall snapshot {pending} is not loaded yet (no picture since "
                                         "the project opened)")
    if plate is None:
        return CheckResult(name, "skip", "no empty-wall snapshot to compare - Calibrate on the empty wall")
    cam = dict(getattr(plate, "camera", None) or {})
    pe, pg = cam.get("exposure_us"), cam.get("gain_db")
    pw, ph = (int(v) for v in plate.frame_size)
    created = str(getattr(plate, "created", "") or "")[5:16].replace("T", " ")
    taken = (f"taken at {float(pe) / 1000:.1f} ms" if pe else "taken at exposure ?") + \
            (f" / {float(pg):.1f} dB" if pg is not None else " / gain ?") + f" on {pw}x{ph}"
    stamp = ", ".join(v for v in (created, source) if v)
    if invalid:
        return CheckResult(name, "fail", f"refused: {invalid}")
    problems = []
    if frame_size and (pw, ph) != (int(frame_size[0]), int(frame_size[1])):
        problems.append(f"taken on a {pw}x{ph} picture, the camera gives {frame_size[0]}x{frame_size[1]}")
    why = plate.camera_mismatch(exposure_us, gain_db)
    if why:
        problems.append(why.split(" - ")[0])
    if problems:
        return CheckResult(name, "fail", "; ".join(problems) + " - RUN refuses it: Calibrate on the empty wall")
    if not cam or not pe or pg is None:
        return CheckResult(name, "warn", f"{taken} ({stamp}): the snapshot does not say at which exposure and gain "
                                         "it was taken - Calibrate on the empty wall")
    unknown = [what for what, known in (("exposure", exposure_us), ("gain", gain_db), ("crop", frame_size))
               if not known and known != 0.0]
    if unknown:
        return CheckResult(name, "skip", f"{taken} ({stamp}); the camera's {', '.join(unknown)} unknown now (no "
                                         "picture, auto, or not an IDS camera): not compared")
    return CheckResult(name, "ok", f"{taken} ({stamp}), as the camera now")


class ReadinessAuto:
    """H3 (#t-026 ④): when the readiness report runs by itself -- once at start, once after each Calibrate.

    ``poll`` is fed every main-loop tick and returns the reason on the one tick a report is due, else None:
    - « start »: once the app is ready (``ready``: the project and its model loaded, the GUI up, no switch
      pending) and the camera has had its chance -- frames flowing for ``start_settle_s``, or ``camera_grace_s``
      of ready with no picture (the report then says the camera is not open);
    - « after Calibrate »: armed by ``calibrate_ended`` (a Calibrate's result card) and by the scene Calibrate's
      running flag going down (a cancel or a stop included), due once nothing is ``busy`` (a Calibrate, the
      dancers pass, a plate capture) for ``calibrate_settle_s``.
    Nothing is due while busy or not ready.  A due report waits while a toast is on screen (``quiet`` False:
    a « Previous session crashed » toast is not covered by the report's), at most ``quiet_wait_s``.  One report
    covers every cause pending, so two causes never make two reports.  It decides nothing else: the state
    RUN/STANDBY is never touched (A4: one writer)."""

    def __init__(self, start_settle_s: float = 3.0, camera_grace_s: float = 15.0,
                 calibrate_settle_s: float = 1.0, quiet_wait_s: float = 20.0):
        self.start_settle_s = start_settle_s
        self.camera_grace_s = camera_grace_s
        self.calibrate_settle_s = calibrate_settle_s
        self.quiet_wait_s = quiet_wait_s
        self.start_pending = True
        self.calibrate_pending = False
        self.reports: List[Tuple[float, str]] = []      # (when, why) of each report made due
        self._was_calibrating = False
        self._idle_since: Optional[float] = None
        self._flow_since: Optional[float] = None
        self._due_since: Optional[float] = None

    def calibrate_ended(self) -> None:
        self.calibrate_pending = True

    def poll(self, now: float, *, ready: bool, busy: bool, calibrating: bool, flowing: bool,
             quiet: bool = True) -> Optional[str]:
        if self._was_calibrating and not calibrating:
            self.calibrate_pending = True
        self._was_calibrating = bool(calibrating)
        idle = bool(ready) and not busy and not calibrating
        if not idle:
            self._idle_since = self._due_since = None
        elif self._idle_since is None:
            self._idle_since = now
        if not flowing:
            self._flow_since = None
        elif self._flow_since is None:
            self._flow_since = now
        if not idle:
            return None
        idle_s = now - self._idle_since
        start_due = self.start_pending and idle_s >= self.start_settle_s and (
            (self._flow_since is not None and now - self._flow_since >= self.start_settle_s)
            or idle_s >= self.camera_grace_s)
        calibrate_due = self.calibrate_pending and idle_s >= self.calibrate_settle_s
        if not (start_due or calibrate_due):
            self._due_since = None
            return None
        if self._due_since is None:
            self._due_since = now
        if not quiet and now - self._due_since < self.quiet_wait_s:
            return None
        reason = ", ".join(w for w, pending in (("start", self.start_pending),
                                                ("after Calibrate", self.calibrate_pending)) if pending)
        self.start_pending = self.calibrate_pending = False
        self._due_since = None
        self.reports.append((now, reason))
        return reason


def check_wall_light(*, plate=None, roi: Optional[Tuple[int, int, int, int]] = None,
                     plate_status: str = "") -> CheckResult:
    """X2 (#t-054): the light verdict of the empty-wall snapshot (core/light_verdict.py) -- red when the
    brightest part of the zone is more than 4x its darkest, with the one thing to move (the IR flood).
    ``plate`` is the installed CleanPlate (None: none), ``roi`` the zone (x, y, w, h) in the plate's frame
    pixels (None: the whole frame), ``plate_status`` FrameProcessor.plate_status."""
    if plate is not None and plate_status == "size":
        return CheckResult("wall light", "skip", "the empty-wall snapshot is from another camera crop - "
                           "Calibrate on the empty wall")
    from core.light_verdict import plate_light_verdict
    v = plate_light_verdict(plate, roi)
    return CheckResult("wall light", v.status, v.detail)


# --------------------------------------------------------------------------
# Health alerts
# --------------------------------------------------------------------------

@dataclass
class Alert:
    kind: str  # "fps_drop" | "no_detection" | "camera_down" | "gpu_temp" | "over_cap"
    message: str
    data: dict = field(default_factory=dict)


class HealthMonitor:
    """Per-second health tick -> alerts with sustain windows + cooldowns.

    All timing flows through the `now` argument; tests advance a float.
    Live-show conditions (in RUN, camera up, model ready, not playback)
    gate the fps/no-detection alerts so standby, playback, and model loads
    never false-alert.
    """

    def __init__(self, *, fps_window_s: float = OPS_FPS_BASELINE_WINDOW_S,
                 fps_drop_fraction: float = OPS_FPS_DROP_FRACTION,
                 fps_sustain_s: float = OPS_FPS_DROP_SUSTAIN_S,
                 no_detection_s: float = OPS_NO_DETECTION_ALERT_S,
                 over_cap_s: float = OPS_OVER_CAP_ALERT_S,
                 height_stale_s: float = OPS_HEIGHT_STALE_S,
                 camera_down_s: float = OPS_CAMERA_DOWN_ALERT_S,
                 gpu_temp_c: float = OPS_GPU_TEMP_ALERT_C,
                 gpu_temp_sustain_s: float = OPS_GPU_TEMP_SUSTAIN_S,
                 gpu_poll_s: float = OPS_GPU_POLL_S,
                 cooldown_s: float = OPS_ALERT_COOLDOWN_S,
                 min_baseline_samples: int = 30,
                 gpu_stats_fn: Optional[Callable[[], dict]] = None,
                 light_band: float = LIGHT_CHANGE_RATIO):
        self.fps_window_s = fps_window_s
        self.fps_drop_fraction = fps_drop_fraction
        self.fps_sustain_s = fps_sustain_s
        self.no_detection_s = no_detection_s
        self.over_cap_s = over_cap_s
        self.height_stale_s = height_stale_s
        self.camera_down_s = camera_down_s
        self.gpu_temp_c = gpu_temp_c
        self.gpu_temp_sustain_s = gpu_temp_sustain_s
        self.gpu_poll_s = gpu_poll_s
        self.cooldown_s = cooldown_s
        self.min_baseline_samples = min_baseline_samples
        self.gpu_stats_fn = gpu_stats_fn
        self.light_band = light_band

        self._fps_samples: List[Tuple[float, float]] = []  # (t, fps)
        self._was_in_run = False
        self._fps_low_since: Optional[float] = None
        self._no_det_since: Optional[float] = None
        self._over_cap_since: Optional[float] = None
        self._height_stale_since: Optional[float] = None
        self._cam_down_since: Optional[float] = None
        self._gpu_hot_since: Optional[float] = None
        self._last_gpu_poll = 0.0
        self._last_gpu_stats: Optional[dict] = None
        self._last_fired: Dict[str, float] = {}
        self._light_alerted = False          # one "light_changed" alert per episode
        self._light_ref: Optional[str] = None
        self.cleared: List[str] = []         # alert kinds whose condition ended (strip entries to drop)

    def pop_cleared(self) -> List[str]:
        out, self.cleared = self.cleared, []
        return out

    def _fire(self, now: float, alert: Alert, out: List[Alert]) -> None:
        last = self._last_fired.get(alert.kind)
        if last is not None and now - last < self.cooldown_s:
            return
        self._last_fired[alert.kind] = now
        out.append(alert)

    def _baseline(self) -> Optional[float]:
        if len(self._fps_samples) < self.min_baseline_samples:
            return None
        values = sorted(f for _, f in self._fps_samples)
        return values[len(values) // 2]

    def tick(self, now: float, *, fps: float, n_tracked: int, in_run: bool,
             model_ready: bool, camera_open: bool, camera_reconnecting: bool,
             playback_active: bool, n_over_cap: int = 0,
             height_median: Optional[float] = None,
             height_gate: Optional[Tuple[float, float]] = None,
             light_ratio: Optional[float] = None, light_ref: str = "") -> List[Alert]:
        out: List[Alert] = []
        live = (in_run and camera_open and not camera_reconnecting
                and not playback_active and model_ready)

        # The standby loop runs a different fps regime - reset the baseline
        # on every entry into RUN so Go-Live never compares across regimes.
        if in_run and not self._was_in_run:
            self._fps_samples.clear()
            self._fps_low_since = None
            self._no_det_since = None
            self._over_cap_since = None
            self._height_stale_since = None
        self._was_in_run = in_run

        # --- fps drop -----------------------------------------------------
        if live:
            baseline = self._baseline()
            if baseline is not None and fps < self.fps_drop_fraction * baseline:
                if self._fps_low_since is None:
                    self._fps_low_since = now
                elif now - self._fps_low_since >= self.fps_sustain_s:
                    self._fire(now, Alert(
                        "fps_drop",
                        f"FPS dropped to {fps:.1f} "
                        f"(baseline {baseline:.1f}) for "
                        f"{now - self._fps_low_since:.0f}s",
                        {"fps": fps, "baseline": baseline}), out)
            else:
                self._fps_low_since = None
                # Only healthy samples feed the baseline, so a slow decay
                # cannot drag the reference down with it.
                self._fps_samples.append((now, fps))
                cutoff = now - self.fps_window_s
                while self._fps_samples and self._fps_samples[0][0] < cutoff:
                    self._fps_samples.pop(0)
        else:
            self._fps_low_since = None

        # --- no detection ---------------------------------------------------
        if live and n_tracked == 0:
            if self._no_det_since is None:
                self._no_det_since = now
            elif now - self._no_det_since >= self.no_detection_s:
                self._fire(now, Alert(
                    "no_detection",
                    f"No dancers tracked for {now - self._no_det_since:.0f}s "
                    "while RUNNING",
                    {"seconds": now - self._no_det_since}), out)
        else:
            self._no_det_since = None

        # --- reported tracks capped at max_persons (bug 12c) -----------------
        # Unlike fps/no-detection this also matters during playback rehearsal
        # (a ghost flood shows up the same way), so gate only on RUN + model.
        if in_run and model_ready and n_over_cap > 0:
            if self._over_cap_since is None:
                self._over_cap_since = now
            elif now - self._over_cap_since >= self.over_cap_s:
                self._fire(now, Alert(
                    "over_cap",
                    f"More people than max_persons visible - "
                    f"{n_over_cap} track(s) over the cap suppressed for "
                    f"{now - self._over_cap_since:.0f}s (ghost flood, or "
                    "raise max_persons)",
                    {"n_over_cap": n_over_cap}), out)
        else:
            self._over_cap_since = None

        # --- person-height staleness (⑤d) -------------------------------------
        # Median of RAW detection heights (pre-size-gate) vs the configured
        # gate — a stale person_height_px config silently drops every real
        # dancer at the size filter, so the tracks themselves can't carry
        # this signal.  Would have caught the bulk-copied h=56 configs.
        out_of_gate = (height_median is not None and height_gate is not None
                       and not (height_gate[0] <= height_median <= height_gate[1]))
        if live and out_of_gate:
            if self._height_stale_since is None:
                self._height_stale_since = now
            elif now - self._height_stale_since >= self.height_stale_s:
                lo, hi = height_gate
                self._fire(now, Alert(
                    "height_stale",
                    f"Person height calibration looks stale - live median "
                    f"detection ~{height_median:.0f}px outside the gate "
                    f"{lo:.0f}-{hi:.0f}px for "
                    f"{now - self._height_stale_since:.0f}s - no confident full skeleton to "
                    f"re-measure it yet (Advanced > Person height)",
                    {"median": height_median, "lo": lo, "hi": hi}), out)
        else:
            self._height_stale_since = None

        # --- light changed since the empty-wall snapshot -----------------------
        # ``light_ratio``: the caller's 10 s median (that is the sustain), in standby too -- a project
        # opened under other light is the case to catch.  One alert per episode, no cooldown re-ring
        # (the alerts strip keeps it visible); re-armed when the light is back in the band or when
        # the snapshot changes (a Calibrate / re-capture).  None = no measure: the state is kept.
        if light_ref != self._light_ref:
            if self._light_alerted:
                self.cleared.append("light_changed")
            self._light_ref, self._light_alerted = light_ref, False
        if light_out_of_band(light_ratio, self.light_band):
            if not self._light_alerted:
                self._light_alerted = True
                when = f" ({light_ref})" if light_ref else ""
                out.append(Alert(
                    "light_changed",
                    f"The light is {light_ratio_text(light_ratio)} the empty-wall snapshot{when}: "
                    f"{LIGHT_REMEDY}",
                    {"ratio": round(float(light_ratio), 3), "snapshot": light_ref}))
        elif light_ratio is not None and self._light_alerted:
            self._light_alerted = False
            self.cleared.append("light_changed")

        # --- camera down ----------------------------------------------------
        if camera_reconnecting or not camera_open:
            if self._cam_down_since is None:
                self._cam_down_since = now
            elif now - self._cam_down_since >= self.camera_down_s:
                # _fire's cooldown makes this re-ring while still down.
                self._fire(now, Alert(
                    "camera_down",
                    f"Camera down for {now - self._cam_down_since:.0f}s - "
                    "check USB3/power",
                    {"seconds": now - self._cam_down_since}), out)
        else:
            self._cam_down_since = None

        # --- gpu temp -------------------------------------------------------
        if self.gpu_stats_fn is not None and now - self._last_gpu_poll >= self.gpu_poll_s:
            self._last_gpu_poll = now
            try:
                self._last_gpu_stats = self.gpu_stats_fn()
            except Exception:
                self._last_gpu_stats = None
        stats = self._last_gpu_stats
        if stats and stats.get("util", -1) >= 0:
            if stats.get("temp", -1) >= self.gpu_temp_c:
                if self._gpu_hot_since is None:
                    self._gpu_hot_since = now
                elif now - self._gpu_hot_since >= self.gpu_temp_sustain_s:
                    self._fire(now, Alert(
                        "gpu_temp",
                        f"GPU at {stats['temp']} C for "
                        f"{now - self._gpu_hot_since:.0f}s",
                        {"temp": stats["temp"]}), out)
            else:
                self._gpu_hot_since = None

        return out


# --------------------------------------------------------------------------
# Loop watchdog
# --------------------------------------------------------------------------

class LoopWatchdog:
    """Daemon thread that reports main-loop hangs; takes no recovery actions.

    The main loop calls beat() every iteration. Long legitimate blocks
    (model load, TRT build, project switch) are bracketed with
    push_busy()/pop_busy() - a re-entrant counter - so they never read as
    hangs. On a real hang the default handler prints an [Alert] line and
    dumps all thread stacks via faulthandler (the stuck stack is the
    diagnosis), once per episode, with a "resumed" line on recovery.
    """

    def __init__(self, *, hang_s: float = OPS_WATCHDOG_HANG_S,
                 poll_s: float = OPS_WATCHDOG_POLL_S,
                 on_hang: Optional[Callable[[float], None]] = None,
                 on_resume: Optional[Callable[[float], None]] = None):
        self.hang_s = hang_s
        self.poll_s = poll_s
        self.on_hang = on_hang or self._default_on_hang
        self.on_resume = on_resume or self._default_on_resume
        self._last_beat = time.monotonic()
        self._busy_depth = 0
        self._busy_lock = threading.Lock()
        self._reported = False
        self._hang_started: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _default_on_hang(age: float) -> None:
        print(f"[Alert] Main loop hang: no heartbeat for {age:.1f}s "
              "- thread stacks follow")
        try:
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        except Exception:
            pass

    @staticmethod
    def _default_on_resume(duration: float) -> None:
        print(f"[Alert] Main loop resumed after {duration:.1f}s")

    def beat(self) -> None:
        self._last_beat = time.monotonic()

    def push_busy(self, reason: str = "") -> None:
        with self._busy_lock:
            self._busy_depth += 1

    def pop_busy(self) -> None:
        with self._busy_lock:
            self._busy_depth = max(0, self._busy_depth - 1)
            if self._busy_depth == 0:
                self._last_beat = time.monotonic()

    @staticmethod
    def evaluate(last_beat: float, now: float, hang_s: float, busy_depth: int,
                 already_reported: bool) -> Optional[float]:
        """Pure hang decision: returns the hang age when a NEW hang should be
        reported, else None."""
        if busy_depth > 0 or already_reported:
            return None
        age = now - last_beat
        return age if age >= hang_s else None

    def _run(self) -> None:
        while not self._stop.wait(self.poll_s):
            now = time.monotonic()
            age = self.evaluate(self._last_beat, now, self.hang_s,
                                self._busy_depth, self._reported)
            if age is not None:
                self._reported = True
                self._hang_started = self._last_beat
                try:
                    self.on_hang(age)
                except Exception:
                    pass
            elif self._reported and now - self._last_beat < self.hang_s:
                self._reported = False
                if self._hang_started is not None:
                    try:
                        self.on_resume(self._last_beat - self._hang_started)
                    except Exception:
                        pass
                    self._hang_started = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._last_beat = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="OpsWatchdog",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
