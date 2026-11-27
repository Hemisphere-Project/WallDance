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
                 ids_dropped: int = 0, link: Optional[dict] = None) -> CheckResult:
    if not is_open or reconnecting:
        state = "reconnecting" if reconnecting else "not open"
        return CheckResult("camera", "fail", f"{source}: {state}")
    detail = f"{source} @ {fps:.1f} FPS"
    if ids_frames:
        detail += f" (acquired={ids_frames}, dropped={ids_dropped})"
    link_fact, link_how = camera_link_text(link)
    if link_fact:
        detail += f", {link_fact}"
    if fps < min_fps:
        return CheckResult("camera", "warn",
                           detail + f" - below the {min_fps:.0f} FPS show floor")
    if link_how:
        return CheckResult("camera", "warn", f"{detail} - {link_how}")
    return CheckResult("camera", "ok", detail)


LINK_LIMIT_HOW = "set ids_throughput_limit_mbps to 0 in the project (the camera's own)"


def camera_link_text(link: Optional[dict]) -> Tuple[str, str]:
    """(fact, instruction) of the camera's link throughput limit for the readiness camera row (stall verdict
    26/11 §8.1).  ``link`` = ``IDSCamera.get_throughput_limit()``: None when the project key is 0 (nothing
    said, as before).  The instruction is "" unless the camera's frame rate fell at the limit."""
    if not link or not isinstance(link, dict):
        return "", ""
    mbps, asked = link.get("mbps"), float(link.get("asked_mbps") or 0.0)
    if link.get("refused"):
        in_force = "the camera's own" if mbps is None else f"{float(mbps):.0f} MB/s (the camera's own)"
        return f"link limit {in_force}: the camera refused {asked:g} MB/s", ""
    fact = "link limit unknown" if mbps is None else f"link limit {float(mbps):.0f} MB/s"
    if link.get("fps_kept", True):
        return fact, ""
    return (f"{fact}: the camera runs {float(link.get('fps') or 0.0):.1f} FPS, not "
            f"{float(link.get('fps_before') or 0.0):.1f}", LINK_LIMIT_HOW)


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


OSC_ON_HOW = "Advanced > OSC > Enable OSC"


def check_osc(*, enabled: bool, ip: str, port: int, timeout_s: float,
              probe: Callable[[str, int, float], Tuple[str, str]] = probe_osc_udp
              ) -> CheckResult:
    if not enabled:   # H2: no output to the visuals is a warning, in readiness and in the health line
        return CheckResult("osc", "warn", "disabled: the visuals get no point - " + OSC_ON_HOW)
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


LIGHT_REMEDY = ("between two pieces, in STANDBY, Calibrate on the empty wall (live with the stage cleared, or "
                "on this lighting's empty take, then back to LIVE) and Save - never play a take in RUN")


def check_empty_wall(*, status: str, detail: str = "", light_ratio: Optional[float] = None,
                     band: float = LIGHT_CHANGE_RATIO, reference=None) -> CheckResult:
    """The empty-wall snapshot (clean plate, D29): present, from this camera crop, and the scene
    still looks like it.  ``status`` is FrameProcessor.plate_status; ``detail`` its readable line;
    ``light_ratio`` the live / snapshot brightness (the ops tick's 10 s median), None if unknown.
    ``reference`` (H4, #t-063): what the reference watch read of this plate against the camera -- a
    ``core.reference_watch.ReferenceVerdict``, « pending » (not read yet: not checked), None (not watched)."""
    if reference is not None and status in ("ready", "stale"):
        rst, rtext = reference_state(reference)
        if rst == "fail":
            return CheckResult("empty wall", "fail", rtext)
        if status == "ready":
            if rst != "ok":
                return CheckResult("empty wall", rst, f"{detail or 'empty wall: ready'}; {rtext}")
            detail = f"{detail or 'empty wall: ready'}, {rtext}"
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


def check_swipe(*, fault: Optional[str] = None, result=None, fault_recovered: bool = False,
                seamless: bool = False, swipe_auto: str = "off") -> Optional[CheckResult]:
    """P5 (Plan Cordee): the settings swipe's readiness row -- or None, its usual answer.  The swipe is advice,
    not a readiness condition: no row when there is nothing to say (no swipe yet, collecting, no verdict, a
    stale or too old verdict, « keep », no plate; the swipe's own line beside its button says those), so READY
    is reachable without a swipe and never claims one ran.

    * ``fault``: a failed Apply (the model fell back to PyTorch or did not load), with what runs now and the
      one thing to do -- ``fail`` while nothing sure runs, ``warn`` (``fault_recovered``) once a setting that
      ran before runs again on TensorRT: a notice to acknowledge, not a show that is not ready;
    * ``result``: a verdict from a swipe the operator ran that still recommends a switch (the caller passes it
      only then: not applied, dismissed, stale or too old) -- ``warn``, worded for STANDBY: Apply reloads the
      model (a few seconds without tracking), so nothing here says to press it during a show; it names the Apply
      button by its label (pressing « Try other settings » again would start a new swipe and drop the advice) --
      unless ``seamless`` (P6: the winner's engine is built, Apply loads it beside the live model and switches
      between two frames, the show runs on).  ``swipe_auto`` off (the project key, off by default): Apply is P5's
      (STANDBY only, the reload), and so is this row, word for word -- ``seamless`` does not count then.

    The background (automatic) swipe never adds a row of its own except a ``fault``: a failed automatic switch or
    a failed seamless Apply (the caller passes it; the previous setting kept running: ``fault_recovered``)."""
    name = "swipe"
    if fault:
        # a failed Apply whose previous setting runs again on TensorRT is a notice to acknowledge, not a show that
        # is not ready: ``warn``; ``fail`` while nothing sure runs (the way back in progress or failed, PyTorch)
        return CheckResult(name, "warn" if fault_recovered else "fail", str(fault))
    if result is not None and getattr(result, "winner", None) is not None and getattr(result, "switch", False):
        reason = str(getattr(result, "reason", "") or "")
        win = getattr(getattr(result, "winner", None), "label", "")
        if swipe_auto == "off":
            # P5's row: the Apply button's label as the GUI shows it then
            return CheckResult(name, "warn", f"{reason} - in STANDBY press 'Apply {win} (STANDBY)', under Try other "
                                             f"settings")
        if seamless:
            return CheckResult(name, "warn", f"{reason} - press 'Apply {win}' (under Try other settings): it "
                                             f"switches between two frames, the show runs on")
        # the Apply button's label as the GUI shows it; « Try other settings » would start a NEW swipe
        return CheckResult(name, "warn", f"{reason} - in STANDBY press 'Apply {win} (STANDBY: a few seconds "
                                         f"without tracking)', under Try other settings")
    return None


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
                     plate_status: str = "", frame=None) -> CheckResult:
    """X2 (#t-054): the light verdict of the empty-wall snapshot (core/light_verdict.py) -- red when the
    brightest part of the zone is more than 4x its darkest, with the one thing to move (the IR flood).
    ``plate`` is the installed CleanPlate (None: none), ``roi`` the zone (x, y, w, h) in the plate's frame
    pixels (None: the whole frame), ``plate_status`` FrameProcessor.plate_status.  H2 (#t-040, follow-up):
    ``frame``, the camera's raw picture now (None: not read) -- under the darkness floor no IR light reaches the
    camera, and that is the verdict whatever the snapshot says (its shape is another light's)."""
    if frame is not None:
        from core.light_verdict import LIGHT_GRID_DS, picture_light_verdict
        dark = picture_light_verdict(frame, roi, int(getattr(plate, "ds", 0) or LIGHT_GRID_DS))
        if dark is not None:
            return CheckResult("wall light", dark.status, dark.detail)
    if plate is not None and plate_status == "size":
        return CheckResult("wall light", "skip", "the empty-wall snapshot is from another camera crop - "
                           "Calibrate on the empty wall")
    from core.light_verdict import plate_light_verdict
    v = plate_light_verdict(plate, roi)
    return CheckResult("wall light", v.status, v.detail)


def no_light_line(level: float, alert: bool = False) -> str:
    """H2 (#t-040, follow-up): the one text the health line (source « light ») and the Alerts line both say when
    no IR light reaches the camera -- « fact - instruction »; ``alert``: as the strip's entry (a capital)."""
    from core.light_verdict import LIGHT_NO_LIGHT_DN, NO_LIGHT_HOW
    text = (f"no IR light reaches the camera (the picture's brightest 1 % at {level:.0f} of 255, under "
            f"{LIGHT_NO_LIGHT_DN:g}) - {NO_LIGHT_HOW}")
    return text[0].upper() + text[1:] if alert else text


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
        self._no_light_dn: Optional[float] = None   # H2: « no light » holds, at this level (DN) when it began
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
             light_ratio: Optional[float] = None, light_ref: str = "",
             picture_dn: Optional[float] = None) -> List[Alert]:
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
            if self._light_alerted and self._no_light_dn is None:     # H2: « no light » is the picture's: kept
                self.cleared.append("light_changed")
            self._light_ref, self._light_alerted = light_ref, self._no_light_dn is not None
        # H2 (#t-040, follow-up): « no light ».  ``picture_dn``: the camera's picture's brightest 1 % over the zone
        # (core/light_verdict.zone_level; the caller's 10 s median; None = no measure, no live camera).  Under the
        # darkness floor the same strip entry says that no IR light reaches the camera, with its one instruction,
        # and the snapshot's ratio (« <= 0.25x ») is not said: one cause.  The light back above the floor: the entry
        # goes -- or turns into the ratio's, when that is still out of the band.
        from core.light_verdict import LIGHT_NO_LIGHT_DN
        was_dark = self._no_light_dn is not None
        if picture_dn is not None and picture_dn < LIGHT_NO_LIGHT_DN:
            if not was_dark:
                self._no_light_dn, self._light_alerted = float(picture_dn), True
                out.append(Alert("light_changed", no_light_line(picture_dn, alert=True),
                                 {"no_light": True, "picture_dn": round(float(picture_dn), 1),
                                  "snapshot": light_ref}))
        elif light_out_of_band(light_ratio, self.light_band):
            if not self._light_alerted or was_dark:
                self._no_light_dn = None
                self._light_alerted = True
                when = f" ({light_ref})" if light_ref else ""
                out.append(Alert(
                    "light_changed",
                    f"The light is {light_ratio_text(light_ratio)} the empty-wall snapshot{when}: "
                    f"{LIGHT_REMEDY}",
                    {"ratio": round(float(light_ratio), 3), "snapshot": light_ref}))
        elif (light_ratio is not None or was_dark) and self._light_alerted:
            self._no_light_dn = None
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

    def plate_tick(self, plate_stale: str = "") -> List[Alert]:
        """F2 (Plan Cordée rev 2, C5 ②): a stale empty-wall snapshot is a persistent alert.  ``plate_stale``:
        "" while the snapshot holds, else why it does not (the foreground latched « plate stale » at a time,
        or the camera was changed by hand); both hold until a new snapshot.  One alert per episode, kept in
        the alerts strip (ui.adapter.STRIP_ALERTS); cleared when a new snapshot ends the episode."""
        out: List[Alert] = []
        alerted = getattr(self, "_plate_stale_alerted", False)
        if plate_stale and not alerted:
            self._plate_stale_alerted = True
            out.append(Alert(
                "plate_stale",
                f"The empty-wall snapshot no longer matches the wall ({plate_stale}): the foreground is not "
                f"to be trusted until a new one - {LIGHT_REMEDY}",
                {"why": str(plate_stale)}))
        elif not plate_stale and alerted:
            self._plate_stale_alerted = False
            self.cleared.append("plate_stale")
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


# --------------------------------------------------------------------------
# H2 (#t-040 ①): one health line during RUN -- the worst state wins
# --------------------------------------------------------------------------
# Every source is a CheckResult in readiness' vocabulary (ok < not checked < warn < fail: ``worst_status``,
# STATUS_WORDS, STATUS_COLORS), so the line is the worst row, with its one instruction (the detail's text after
# its last « - », else its last « : »).  Two signals of its own:
#   (a) ``BareGroupWatch``: a foreground group (a body against the empty-wall plate, as the live hit rate's
#       referee reads the foreground) that no output point sits on for more than 3 s -- the show's worst
#       failure, a dancer the audience sees and the visuals do not follow.  Red, never green while it holds;
#       « not checked » when the foreground cannot say (off, no plate, refused, no new frame).
#   (b) ``StallRate``: the camera's stalls a minute (core/camera_stall.py counts them), over a rolling window.

from collections import deque  # noqa: E402 - H2's own imports, kept in its section
from typing import Deque  # noqa: E402

BARE_GROUP_S = 3.0        # a group without a point for longer than this turns the line red
BARE_GAP_S = 1.0          # a group not seen bare (a point passed over it, it flickered under the size) for less
                          # than this keeps its count running; longer, its episode ends
BARE_REACH_H = 0.75       # a point sits on a group within this x the point's box height of the group's centre
                          # (the radius of the M1 scorer's « a point on her », tests/truth_score.py ``on``)
BARE_FOLLOW_H = 0.75      # a bare group this close (x the dancers' height) to one seen bare is that same group
BARE_STALE_S = 2.0        # no new processed frame for this long: (a) is not checked
STALL_WINDOW_S = 300.0    # the stall rate's window ...
STALL_MIN_SPAN_S = 60.0   # ... never divided by less than a minute of live camera (one stall is 1 a minute)
STALL_WARN_PER_MIN = 2.0  # each stall freezes the picture ~1.7 s: 2 a minute is ~6 % of the show without one
STALL_FAIL_PER_MIN = 6.0  # (08/10's field laptop: 0.7-2.9 a minute at the landscape crops); 6 is ~17 %
POINTS_ROW = "points"
MAX_DANCERS_HOW = "check Max dancers (3 Live)"
CAMERA_HOW = "check the camera's USB3 cable and power"
# The stall rate's one instruction (stall verdict 26/11 §8.3: the cable and its connectors are the first suspect).
# No " - " in it: the health line shows the text after the detail's last " - ".
STALL_HOW = "at the next break: reseat the camera's USB cable at both ends, then try the other cable"
EMPTY_WALL_HOW = "when the wall is empty: CALIBRATE (2 Calibrate), then Save to project"
RESTART_HOW = "restart the app"
# The order of the rows when two share the worst state: the first one names the line.
HEALTH_PRIORITY = ("camera", "output", POINTS_ROW, "empty wall", "frames", "engine", "camera stalls",
                   "enhancement", "ROI", "plate exposure", "fps", "gpu", "light", "no dancer", "over cap",
                   "height")


def foreground_groups(fg, zone_ok: Optional[Callable[[float, float], bool]] = None):
    """(groups, why): this frame's dancer groups [(x, y, area)] in original px, read as the live hit rate's
    referee reads them (``core.swipe.Observation.from_fg`` then ``dancer_groups``: the swipe's one finder, its
    ``group_min_area``), minus the groups whose centre lies in an excluded cell (``zone_ok``: the pipeline drops
    every detection there, so they can never get a point).  (None, why) when the frame cannot say."""
    if fg is None:
        return None, "no foreground on this frame"
    if not getattr(fg, "valid", False):
        return None, f"the foreground is not valid ({getattr(fg, 'reason', '') or 'invalid'})"
    try:
        from core.swipe import Observation, SwipeParams
    except Exception:  # noqa: BLE001 - no referee in this build: (a) is not checked, never green
        return None, "no foreground referee in this build (core/swipe.py)"
    obs = Observation.from_fg(fg)
    if obs is None:
        return None, "the foreground is not valid"
    groups = obs.dancer_groups(SwipeParams())
    if zone_ok is not None:
        groups = [g for g in groups if zone_ok(float(g[0]), float(g[1]))]
    return groups, ""


def place_text(x: float, y: float, bounds: Optional[Tuple[float, float, float, float]]) -> str:
    """Where a point lies in the watched zone (x0, y0, x1, y1), in thirds: « top left », « centre »."""
    if not bounds or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return ""
    fx = (x - bounds[0]) / (bounds[2] - bounds[0])
    fy = (y - bounds[1]) / (bounds[3] - bounds[1])
    row = "top" if fy < 1 / 3 else ("bottom" if fy > 2 / 3 else "middle")
    col = "left" if fx < 1 / 3 else ("right" if fx > 2 / 3 else "")
    return "centre" if (row, col) == ("middle", "") else f"{row} {col}".strip()


@dataclass
class _Bare:
    x: float
    y: float
    since: float              # the frame it was first seen without a point
    seen: float               # the last frame it was seen without a point
    where: str = ""
    counted: bool = False     # already counted in ``episodes``


class BareGroupWatch:
    """Signal (a): a foreground group no output point sits on, for more than ``sustain_s``.

    ``update`` once per processed frame: the frame's dancer groups (``foreground_groups``; None = the frame
    cannot say, with why) and its output points [(x, y, h)] -- what OSC sends (``FrameProcessor.sent_xy``) and
    the height of the point's box.  A point sits on a group when it lies within ``reach_h`` x its height of
    the group's centre.  A group with no point is followed from frame to frame (within ``follow_h`` x the
    dancers' height ``body_h``); a short gap (``gap_s``: a frame under the size, a point passing over it) does
    not restart its count, and a bare group on every frame counts too, wherever it is (a group that splits or
    jumps).  The signal ends ``gap_s`` after the group gets a point or disappears.  Times in one clock."""

    def __init__(self, sustain_s: float = BARE_GROUP_S, gap_s: float = BARE_GAP_S,
                 reach_h: float = BARE_REACH_H, follow_h: float = BARE_FOLLOW_H,
                 stale_s: float = BARE_STALE_S):
        self.sustain_s, self.gap_s, self.stale_s = float(sustain_s), float(gap_s), float(stale_s)
        self.reach_h, self.follow_h = float(reach_h), float(follow_h)
        self._open: List[_Bare] = []
        self._any_since: Optional[float] = None    # a bare group on every frame since (any group)
        self._any_where = ""
        self._last_t: Optional[float] = None
        self._why = "no frame yet"                  # why (a) is not checked ("" = checked)
        self.groups = self.points = self.bare = 0   # the last frame's counts
        self.episodes = 0                           # groups that went past sustain_s, since start

    def not_checked(self, now: float, why: str) -> None:
        """The frame (or the app's state) cannot say: (a) reads « not checked » until a frame can; a group seen
        bare keeps its count across a gap of up to ``gap_s`` (one invalid frame does not restart it)."""
        self._open = [b for b in self._open if now - b.seen <= self.gap_s]
        self._any_since, self._why, self._last_t = None, str(why or "not checked"), now
        self.groups = self.points = self.bare = 0

    def update(self, now: float, groups, points, body_h: float = 0.0,
               bounds: Optional[Tuple[float, float, float, float]] = None, why: str = "") -> None:
        if groups is None:
            self.not_checked(now, why or "no foreground on this frame")
            return
        pts = []
        for p in points or ():
            h = float(p[2]) if len(p) > 2 and p[2] else float(body_h or 0.0)
            pts.append((float(p[0]), float(p[1]), h))
        bare = [g for g in groups
                if not any((px - g[0]) ** 2 + (py - g[1]) ** 2 <= (self.reach_h * ph) ** 2 for px, py, ph in pts)]
        self._why, self._last_t = "", now
        self.groups, self.points, self.bare = len(groups), len(pts), len(bare)
        if bare:
            if self._any_since is None:
                self._any_since = now
            self._any_where = place_text(float(bare[0][0]), float(bare[0][1]), bounds)
        else:
            self._any_since = None
        h_ref = float(body_h or 0.0) or max([ph for _x, _y, ph in pts] + [100.0])
        radius2 = (self.follow_h * h_ref) ** 2
        pairs = sorted(((b.x - g[0]) ** 2 + (b.y - g[1]) ** 2, i, j)
                       for i, b in enumerate(self._open) for j, g in enumerate(bare))
        used_i, used_j = set(), set()
        for d2, i, j in pairs:
            if d2 > radius2 or i in used_i or j in used_j:
                continue
            used_i.add(i)
            used_j.add(j)
            b, g = self._open[i], bare[j]
            b.x, b.y, b.seen = float(g[0]), float(g[1]), now
            b.where = place_text(b.x, b.y, bounds)
        for j, g in enumerate(bare):
            if j not in used_j:
                self._open.append(_Bare(float(g[0]), float(g[1]), now, now,
                                        place_text(float(g[0]), float(g[1]), bounds)))
        self._open = [b for b in self._open if now - b.seen <= self.gap_s]
        for b in self._open:
            if not b.counted and now - b.since > self.sustain_s:
                b.counted = True
                self.episodes += 1

    def worst_s(self, now: float) -> float:
        """How long the longest-running group has gone without a point (0: none)."""
        ages = [now - b.since for b in self._open if now - b.seen <= self.gap_s]
        if self._any_since is not None:
            ages.append(now - self._any_since)
        return max(ages + [0.0])

    def state(self, now: float) -> CheckResult:
        if self._why:
            return CheckResult(POINTS_ROW, "skip", f"not checked: {self._why}")
        if self._last_t is None or now - self._last_t > self.stale_s:
            return CheckResult(POINTS_ROW, "skip", "not checked: no new frame processed")
        age = self.worst_s(now)
        if age > self.sustain_s:
            long = [b for b in self._open if now - b.seen <= self.gap_s and now - b.since > self.sustain_s]
            places = [b.where for b in long if b.where] or ([self._any_where] if self._any_where else [])
            n = max(1, len(long))
            who = "a body on the wall has" if n == 1 else f"{n} bodies on the wall have"
            where = f" ({', '.join(dict.fromkeys(places))})" if places else ""
            return CheckResult(POINTS_ROW, "fail", f"{who} had no point for {age:.0f} s{where} - if it is a "
                                                   f"dancer: {MAX_DANCERS_HOW}")
        g, p = self.groups, self.points
        return CheckResult(POINTS_ROW, "ok", f"{g} bod{'y' if g == 1 else 'ies'} on the wall, {p} point"
                                             f"{'' if p == 1 else 's'}: none without a point for more than "
                                             f"{self.sustain_s:.0f} s")

    def snapshot(self, now: float) -> Dict[str, object]:
        return {"groups": self.groups, "points": self.points, "bare": self.bare,
                "worst_s": round(self.worst_s(now), 1), "episodes": self.episodes,
                "not_checked": self._why or None}


class StallRate:
    """Signal (b): the camera's stalls a minute (``CameraStallClock.stalls``, a running count) over the last
    ``window_s`` of live camera, never divided by less than ``min_span_s``.  ``update`` about once a second with
    the count and whether the camera is live (open, not reconnecting, no take playing)."""

    def __init__(self, window_s: float = STALL_WINDOW_S, min_span_s: float = STALL_MIN_SPAN_S,
                 warn_per_min: float = STALL_WARN_PER_MIN, fail_per_min: float = STALL_FAIL_PER_MIN,
                 stale_s: float = 5.0):
        self.window_s, self.min_span_s = float(window_s), float(min_span_s)
        self.warn_per_min, self.fail_per_min, self.stale_s = float(warn_per_min), float(fail_per_min), stale_s
        self._rows: Deque[Tuple[float, float, int]] = deque()   # (t, live seconds so far, stalls so far)
        self._live_s = 0.0
        self._t: Optional[float] = None
        self._live = False
        self._count: Optional[int] = None

    def update(self, now: float, stalls: int, live: bool) -> None:
        stalls = int(stalls)
        if self._count is not None and stalls < self._count:      # a new stall clock: start again
            self._rows.clear()
        if self._t is not None and self._live and live:
            self._live_s += max(0.0, now - self._t)
        self._t, self._live, self._count = now, bool(live), stalls
        if live:
            self._rows.append((now, self._live_s, stalls))
        while self._rows and now - self._rows[0][0] > self.window_s:
            self._rows.popleft()

    def rate(self) -> Optional[Tuple[float, int, float]]:
        """(stalls a minute, stalls, live seconds) over the window; None before any live sample."""
        if not self._rows:
            return None
        _t0, l0, c0 = self._rows[0]
        _t1, l1, c1 = self._rows[-1]
        n, span = max(0, c1 - c0), max(0.0, l1 - l0)
        return 60.0 * n / max(span, self.min_span_s), n, span

    def state(self, now: float) -> CheckResult:
        name = "camera stalls"
        r = self.rate()
        if not self._live or self._t is None or now - self._t > self.stale_s or r is None:
            return CheckResult(name, "skip", "not checked: the camera is not live")
        per_min, n, span = r
        fact = f"{per_min:.1f} a minute ({n} in the last {max(span, 1.0) / 60:.1f} min of camera)"
        if per_min >= self.fail_per_min:
            return CheckResult(name, "fail", f"{fact}: the picture freezes often - {STALL_HOW}")
        if per_min >= self.warn_per_min:
            return CheckResult(name, "warn", f"{fact} - {STALL_HOW}")
        return CheckResult(name, "ok", fact)

    def snapshot(self) -> Optional[Dict[str, object]]:
        r = self.rate()
        if r is None:
            return None
        return {"per_min": round(r[0], 2), "stalls": r[1], "live_s": round(r[2], 1), "window_s": self.window_s}


def health_camera_row(*, is_open: bool, reconnecting: bool, playing: bool = False, source: str = "",
                      fps: float = 0.0, down_s: Optional[float] = None) -> CheckResult:
    """The camera, now: a recording playing in its place is orange (the visuals follow the take, not the
    dancers), the camera closed or reconnecting is red."""
    if playing:
        return CheckResult("camera", "warn", "a recording is playing, not the camera - LIVE (Recordings bar)")
    if not is_open or reconnecting:
        for_s = f" for {down_s:.0f} s" if down_s else ""
        return CheckResult("camera", "fail", f"{source + ': ' if source else ''}"
                                             f"{'reconnecting' if reconnecting else 'not open'}{for_s} - {CAMERA_HOW}")
    return CheckResult("camera", "ok", f"{source} @ {float(fps):.1f} FPS" if source else f"{float(fps):.1f} FPS")


def health_output_row(*, enabled: bool, target: str = "", errors: int = 0) -> CheckResult:
    """The output to the visuals: OSC off is orange (H2: readiness says the same, ``check_osc``); sends that
    fail now (``errors``: dropped in the last seconds -- nothing listens there) are red."""
    if not enabled:
        return CheckResult("output", "warn", "OSC is off: the visuals get no point - " + OSC_ON_HOW)
    if errors > 0:
        return CheckResult("output", "fail", f"OSC sends to {target} fail ({errors} dropped) - start "
                                             "TouchDesigner and its patch")
    return CheckResult("output", "ok", f"OSC to {target}" if target else "OSC on")


def health_plate_row(*, status: str, reason: str = "", stale_since: str = "", reference=None,
                     prompt: Optional[str] = None) -> CheckResult:
    """The empty-wall snapshot in use (FrameProcessor.plate_status): a stale or refused one is red (the
    foreground then sees nothing right).  ``stale_since``: F2's latched « plate stale » (when it latched),
    "" when none or not in this build.  H4 (#t-063): ``reference``, the reference watch's verdict on this plate
    (as ``check_empty_wall``): moved, another wall or refused is red and a cause (``CauseResult``: it names the
    line over « a body on the wall has had no point », whose groups are then the wall's edges); not read yet or
    unknown is not checked.  ``prompt``: the wall is provably empty now and the plate must be retaken -- the
    row says what to do now instead of its own instruction."""
    if prompt:
        return with_instruction(health_plate_row(status=status, reason=reason, stale_since=stale_since,
                                                 reference=reference), prompt)
    if reference is not None and status in ("ready", "stale"):
        rst, rtext = reference_state(reference)
        if rst == "fail":
            return CauseResult("empty wall", "fail", rtext)
        if status == "ready" and not stale_since:
            return CheckResult("empty wall", rst, f"ready, {rtext}" if rst == "ok" else f"ready; {rtext}")
    why = f" ({reason})" if reason else ""
    if stale_since:
        return CheckResult("empty wall", "fail", f"stale since {stale_since}{why} - {EMPTY_WALL_HOW}")
    if status == "ready":
        return CheckResult("empty wall", "ok", "ready")
    if status == "capturing":
        return CheckResult("empty wall", "skip", "being captured")
    if status == "off":
        return CheckResult("empty wall", "warn", "off: the dancers' points are not checked - 3 Live > Use "
                                                 "empty wall")
    if status == "none":
        return CheckResult("empty wall", "warn", f"none - {EMPTY_WALL_HOW}")
    if status == "size":
        return CheckResult("empty wall", "fail", f"from another camera crop - {EMPTY_WALL_HOW}")
    if status == "stale":
        return CheckResult("empty wall", "fail", f"no longer matches the wall{why} - {EMPTY_WALL_HOW}")
    return CheckResult("empty wall", "skip", f"not checked ({status})")


def health_frames_row(*, fail_streak: int) -> CheckResult:
    if fail_streak > 0:
        return CheckResult("frames", "fail", f"{fail_streak} frame(s) in a row failed to process - "
                                             + RESTART_HOW)
    return CheckResult("frames", "ok", "processed")


def health_engine_row(*, trt_requested: bool, trt_active: Optional[bool]) -> CheckResult:
    if trt_active is None:
        return CheckResult("engine", "skip", "not checked: no model loaded")
    if trt_requested and not trt_active:
        return CheckResult("engine", "fail", "running PyTorch, not the TensorRT engine - " + RESTART_HOW)
    return CheckResult("engine", "ok", "TensorRT" if trt_active else "PyTorch (as asked)")


def health_alert_rows(mon: HealthMonitor, now: float) -> List[CheckResult]:
    """The HealthMonitor's conditions that hold now, past the alerts' own sustain windows (the alerts the
    strip and the toasts show, kept here for as long as they last, not for one toast): orange each."""
    out: List[CheckResult] = []

    def held(since: Optional[float], need: float) -> Optional[float]:
        return now - since if since is not None and now - since >= need else None

    s = held(mon._fps_low_since, mon.fps_sustain_s)
    if s is not None:
        base = mon._baseline()
        out.append(CheckResult("fps", "warn", f"FPS under half its baseline{f' {base:.1f}' if base else ''} for "
                                              f"{s:.0f} s - mains plugged in, close everything but WallDance and "
                                              "TouchDesigner"))
    s = held(mon._gpu_hot_since, mon.gpu_temp_sustain_s)
    if s is not None:
        temp = (mon._last_gpu_stats or {}).get("temp", "?")
        out.append(CheckResult("gpu", "warn", f"GPU at {temp} C for {s:.0f} s - raise the laptop's back, clear "
                                              "its vents"))
    if mon._light_alerted and getattr(mon, "_no_light_dn", None) is not None:
        # H2 (#t-040, follow-up): no light is THE cause (CauseResult: it names the line), red, its one instruction
        out.append(CauseResult("light", "fail", no_light_line(mon._no_light_dn)))
    elif mon._light_alerted:
        out.append(CheckResult("light", "warn", "the light is not the empty-wall snapshot's - " + LIGHT_REMEDY))
    s = held(mon._no_det_since, mon.no_detection_s)
    if s is not None:
        out.append(CheckResult("no dancer", "warn", f"no dancer tracked for {s:.0f} s - if dancers are on the "
                                                    "wall: check the IR and the picture"))
    s = held(mon._over_cap_since, mon.over_cap_s)
    if s is not None:
        out.append(CheckResult("over cap", "warn", f"more people than max_persons for {s:.0f} s - "
                                                   + MAX_DANCERS_HOW))
    s = held(mon._height_stale_since, mon.height_stale_s)
    if s is not None:
        out.append(CheckResult("height", "warn", "the dancers' height looks stale - a dancer standing 10 s at "
                                                 "the far wall"))
    return out


@dataclass
class HealthLineState:
    """The line: its state (ok | skip | warn | fail), the text shown, the row that names it and its one
    instruction, and every row it was folded from."""
    state: str
    text: str
    source: str = ""
    instruction: str = ""
    rows: List[CheckResult] = field(default_factory=list)

    @property
    def word(self) -> str:
        return STATUS_WORDS.get(self.state, "FAIL")

    @property
    def color(self) -> Tuple[int, int, int]:
        return STATUS_COLORS.get(self.state, STATUS_COLORS["fail"])

    def to_dict(self) -> Dict[str, object]:
        """For /status: small (a slow 4G link) -- the rows that are not ok, the others by name."""
        return {"state": self.state, "word": self.word, "line": self.text, "source": self.source,
                "instruction": self.instruction,
                "rows": [{"name": r.name, "status": r.status, "detail": r.detail}
                         for r in self.rows if r.status != "ok"],
                "ok": [r.name for r in self.rows if r.status == "ok"]}


def _rank(name: str) -> int:
    return HEALTH_PRIORITY.index(name) if name in HEALTH_PRIORITY else len(HEALTH_PRIORITY)


def health_line(rows) -> HealthLineState:
    """The worst row wins (``worst_status``); between rows of that state, ``HEALTH_PRIORITY`` picks.  Green
    only when every row is ok: a row not checked is never green, no rows is not checked."""
    rows = list(rows)
    state = worst_status(r.status for r in rows)
    if not rows:
        return HealthLineState("skip", f"{STATUS_WORDS['skip']} - nothing to check")
    if state == "ok":
        names = [r.name for r in sorted(rows, key=lambda r: _rank(r.name))]
        shown = ", ".join(names[:6]) + (", ..." if len(names) > 6 else "")
        return HealthLineState("ok", f"OK - all {len(rows)} checks fine ({shown})", rows=rows)
    worst = [r for r in rows if r.status == state] or [r for r in rows if r.status not in _STATUS_ORDER]
    top = min(worst, key=lambda r: (not isinstance(r, CauseResult), _rank(r.name)))   # H4: a cause first
    others = [r.name for r in sorted(rows, key=lambda r: (-_STATUS_ORDER.get(r.status, 3), _rank(r.name)))
              if r is not top and r.status != "ok"]
    more = f"   (+{len(others)}: {', '.join(others[:3])}{', ...' if len(others) > 3 else ''})" if others else ""
    instruction = next((top.detail.rsplit(sep, 1)[1] for sep in (" - ", ": ") if sep in top.detail), "")
    return HealthLineState(state, f"{STATUS_WORDS.get(state, 'FAIL')} - {top.name}: {top.detail}{more}",
                           source=top.name, instruction=instruction, rows=rows)


class HealthLine:
    """The line between ticks (the main loop owns one): signals (a) and (b), the last line, and since when its
    state is what it is."""

    def __init__(self, bare: Optional[BareGroupWatch] = None, stalls: Optional[StallRate] = None):
        self.bare = bare or BareGroupWatch()
        self.stalls = stalls or StallRate()
        self.last: Optional[HealthLineState] = None
        self.since: Optional[float] = None

    def compose(self, now: float, rows) -> HealthLineState:
        """``rows``: the other sources' CheckResults; (a) and (b) are added here (times in the clock the two
        signals were fed with)."""
        line = health_line(list(rows) + [self.bare.state(now), self.stalls.state(now)])
        if self.last is None or line.state != self.last.state:
            self.since = now
        self.last = line
        return line

    def to_dict(self, now: float) -> Dict[str, object]:
        if self.last is None:
            return {}
        d = self.last.to_dict()
        d["for_s"] = round(now - self.since, 1) if self.since is not None else None
        d["points"] = self.bare.snapshot(now)
        d["stall_rate"] = self.stalls.snapshot()
        return d


# --------------------------------------------------------------------------
# H4 (#t-063): the references watched (core/reference_watch.py)
# --------------------------------------------------------------------------
# The empty-wall row and the health line's « empty wall » source say the CAUSE when the empty-wall picture no
# longer lines up with the camera (moved) or is not this wall, with one instruction, and that row names the line
# over « a body on the wall has had no point » (the groups are then edges, not bodies) -- the other signal stays
# listed.  One readiness row says where the references come from (``check_references``).

REFERENCES_HOW = "STANDBY, then CALIBRATE on the empty wall (2 Calibrate), then Save to project"


@dataclass
class CauseResult(CheckResult):
    """A row that is the cause of other rows of its state (H4: the empty-wall picture moved, or is another
    wall): between rows of the same state it names the line (``health_line``); the others stay listed."""


def reference_state(reference) -> Tuple[str, str]:
    """(row status, text) for the reference watch's verdict: « pending » (no verdict yet) and ``unknown`` are
    not checked, never green; ``matches`` ok; ``moved``, ``other_wall`` and the referee's ``refused`` red."""
    if isinstance(reference, str):
        return "skip", "not checked against the camera yet (it is, as soon as frames arrive)"
    st = str(getattr(reference, "status", "") or "")
    text = str(getattr(reference, "instruction", "") or "")
    if st == "matches":
        shift = getattr(reference, "shift_px", None)
        return "ok", "lines up with the camera" + (f" ({float(shift):.1f} px)" if shift is not None else "")
    if st in ("moved", "other_wall", "refused", "dark_plate", "blind"):
        return "fail", text
    return "skip", text or "not checked against the camera"


def with_instruction(row: CheckResult, instruction: str) -> CheckResult:
    """``row`` (same class, name and status) with ``instruction`` in place of its own (the text after its last
    « - »)."""
    import copy
    fact = row.detail.rsplit(" - ", 1)[0] if " - " in row.detail else row.detail
    out = copy.copy(row)
    out.detail = f"{fact} - {instruction}"
    return out


def _ago_text(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "just now"
    if seconds < 90 * 60:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 36 * 3600:
        return f"{seconds / 3600:.0f} h ago"
    return f"{seconds / 86400:.0f} days ago"


def check_references(*, plate=None, calibrated_at: str = "", calibrated_with: Optional[dict] = None,
                     model: str = "", net_input: str = "", session_day: str = "",
                     session_start: Optional[datetime] = None, now: Optional[datetime] = None) -> CheckResult:
    """H4 (#t-063): where the references come from, at load.  ``plate``: the CleanPlate in memory (a plate
    Calibrate retook and holds until Save is the one shown, not the file); ``calibrated_at``: the last
    Calibrate (« YYYY-MM-DD HH:MM », "" when none is recorded); ``calibrated_with``: the model and network input
    that Calibrate ran with ({"model", "input"}, None when not recorded: a project calibrated before H4);
    ``model`` / ``net_input``: what runs now; ``session_day``: the day this session began (« YYYY-MM-DD »),
    ``session_start`` when (a plate taken since, past midnight, is this session's).  Orange when the model or
    the input differ from Calibrate's, when the empty-wall picture is from another day than the session, or
    missing; never green on a guess."""
    now = now or datetime.now()
    facts, problems = [], []
    created = str(getattr(plate, "created", "") or "") if plate is not None else ""
    if plate is None:
        facts.append("no empty-wall picture")
        problems.append("there is no empty-wall picture")
    elif not created:
        facts.append("empty wall taken at an unknown time")
        problems.append("the empty-wall picture has no capture time")
    else:
        try:
            taken = datetime.fromisoformat(created)
            facts.append(f"empty wall taken {taken:%Y-%m-%d %H:%M} ({_ago_text((now - taken).total_seconds())})")
            if (session_day and taken.strftime("%Y-%m-%d") != session_day
                    and not (session_start is not None and taken >= session_start)):
                problems.append(f"the empty-wall picture is from another day ({taken:%Y-%m-%d})")
        except ValueError:
            facts.append(f"empty wall taken {created}")
            problems.append("the empty-wall picture's capture time is unreadable")
    with_model = str((calibrated_with or {}).get("model") or "")
    with_input = str((calibrated_with or {}).get("input") or "")
    if calibrated_at:
        made = f"Calibrate {calibrated_at}"
        made += f" with {with_model} @ {with_input}" if with_model and with_input else " (its model not recorded)"
        facts.append(made)
    else:
        facts.append("no Calibrate recorded")
        problems.append("this project has no Calibrate recorded")
    facts.append(f"runs {model or '?'} @ {net_input or '?'}")
    if with_model and model and with_model != model:
        problems.append(f"Calibrate was made with {with_model}, {model} runs now")
    if with_input and net_input and with_input != net_input:
        problems.append(f"Calibrate was made at {with_input}, the network runs at {net_input}")
    detail = "; ".join(facts)
    if problems:
        return CheckResult("references", "warn", f"{detail}: {'; '.join(problems)} - {REFERENCES_HOW}")
    return CheckResult("references", "ok", detail)
