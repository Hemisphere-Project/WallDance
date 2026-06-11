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

from config import (
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
)

# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------

_STATUS_ORDER = {"ok": 0, "skip": 0, "warn": 1, "fail": 2}


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail" | "skip"
    detail: str


@dataclass
class ReadinessReport:
    results: List[CheckResult]

    @property
    def worst(self) -> str:
        worst = "ok"
        for r in self.results:
            if _STATUS_ORDER[r.status] > _STATUS_ORDER[worst]:
                worst = r.status
        return worst

    def counts(self) -> Dict[str, int]:
        out = {"ok": 0, "warn": 0, "fail": 0, "skip": 0}
        for r in self.results:
            out[r.status] += 1
        return out

    def console_block(self, header: str = "") -> str:
        lines = [f"[Readiness] ===== Go-Live check {header}====="]
        for r in self.results:
            lines.append(f"[Readiness] {r.status:<5} {r.name:<12} {r.detail}")
        c = self.counts()
        lines.append(
            f"[Readiness] ===== {c['ok']} ok, {c['warn']} warn, {c['fail']} fail =====")
        return "\n".join(lines)

    def toast_summary(self) -> Tuple[str, Tuple[int, int, int]]:
        c = self.counts()
        msg = f"Go-Live check: {c['ok']} ok"
        if c["warn"]:
            msg += f", {c['warn']} warn"
        if c["fail"]:
            msg += f", {c['fail']} FAIL"
        msg += " - see console"
        color = {"ok": (120, 255, 120), "warn": (255, 200, 100),
                 "fail": (255, 80, 80)}[self.worst]
        return msg, color


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
                   gpu_fallback_reason: str = "") -> CheckResult:
    if trt_active:
        detail = "engine active"
        if gpu_fallback_reason:
            return CheckResult("tensorrt", "warn",
                               detail + f" (GPU path degraded: {gpu_fallback_reason})")
        return CheckResult("tensorrt", "ok", detail)
    if trt_requested:
        reason = fallback_reason or "unknown reason"
        return CheckResult("tensorrt", "warn",
                           f"requested but running PyTorch ({reason})")
    return CheckResult("tensorrt", "ok", "not requested (PyTorch)")


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


def check_disk(*, recordings_dir: str, warn_free_gb: float, fail_free_gb: float,
               disk_usage: Callable = shutil.disk_usage) -> CheckResult:
    try:
        usage = disk_usage(recordings_dir)
    except OSError as e:
        return CheckResult("disk", "warn", f"could not stat {recordings_dir} ({e})")
    free_gb = usage.free / 1e9
    hours = free_gb / 55.0  # measured MJPG rate on the corpus recordings
    detail = f"{free_gb:.1f} GB free for recordings (~{hours:.1f} h of MJPG)"
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
                 gpu_stats_fn: Optional[Callable[[], dict]] = None):
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
             height_gate: Optional[Tuple[float, float]] = None) -> List[Alert]:
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
                    f"{now - self._height_stale_since:.0f}s - run Calib2",
                    {"median": height_median, "lo": lo, "hi": hi}), out)
        else:
            self._height_stale_since = None

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
