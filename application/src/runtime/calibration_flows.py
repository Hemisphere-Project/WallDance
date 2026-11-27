"""CALIBRATE / DANCERS calibration orchestration peeled from WallDanceApp.

DECOMPOSITION_PLAN §5 Phase 2 (5). Method bodies moved verbatim from
app.py; ``self.<app attribute>`` references renamed to constructor-injected
dependencies. The calibration *math* stays untouched in
``core/calibration.py`` / ``core/calib2.py`` — this controller owns only
the orchestration state machines the main loop steps per frame:

- Calib1 (CALIBRATE, the EMPTY WALL phase): exposure/gain from the servo (live IDS) or
  from the played take's ``.meta``, then the SceneCalibrator collection window → apply
  result → the empty-wall YOLO check (core/empty_wall.py) → report card.
- Calib2 (DANCERS): SubjectCollector evidence run → pool save → review
  dialog → pooled apply.

Sibling controllers are injected under their app attribute names
(``models``/``cameras``/``configs``) so moved bodies keep their references
verbatim.
"""
from __future__ import annotations

import copy
import glob
import numbers
import os
import time
from typing import Callable, Optional, Protocol

import cv2
import numpy as np

from core.calib2 import (SubjectCollector, SubjectPool, load_fps_table,
                         probe_detection_counts)
from core.calib2 import aggregate as calib2_aggregate
from core.calibration import (ExposureServo, SceneCalibrator,
                              cap_gamma_for_noise, seed_gamma, zone_highlights)
from core.config import (AUTOCAL_BLUR_BUDGET_MS, AUTOCAL2_FRAME_SAMPLES,
                         AUTOCAL2_NOISE_REUSE_S, AUTOCAL2_WINDOW_FRAMES,
                         AUTOCAL_SERVO_GAIN_MAX_DB, EMPTY_WALL_CHECK, MODELS_DIR)
from core.empty_wall import EmptyWallCheck, NEUTRAL_GAMMA, PERSON_SETTLE, PersonWatch

try:
    from camera.ids_camera import CameraSource
except ImportError:
    CameraSource = None


class CalibrationUiPort(Protocol):
    """The GUI surface the calibration flows need (no dpg types)."""

    @property
    def available(self) -> bool: ...

    def set_calibrate_status(self, text: Optional[str]) -> None: ...

    def show_toast(self, message: str, duration: float, color) -> None: ...

    def sync_slider(self, name: str, value) -> None: ...

    def sync_combo(self, name: str, value: str) -> None: ...

    def show_calibration_result_dialog(self, summary: str, on_save) -> None: ...

    def show_calib2_dialog(self, rows, proposal: str) -> None: ...

    def update_calib2_proposal(self, summary: str) -> None: ...

    def update_aim_calib_state(self, text: str) -> None: ...


class CalibrationFlows:
    """Owns the CALIBRATE / DANCERS state machines stepped by the main loop."""

    def __init__(
        self,
        processor,
        enhancer,
        tracker,
        settings,
        recorder,
        camera,
        unified_camera,
        use_unified: bool,
        models,
        cameras,
        configs,
        ui: CalibrationUiPort,
        last_raw_frame: Callable[[], Optional[np.ndarray]],
        roi_source_size: Callable[[], tuple],
        get_effective_roi: Callable[[int, int], tuple],
        reset_sensitivity_anchor: Callable[..., None],
        sync_mask_ui: Callable[[], None],
        request_reprocess: Callable[[], None],
        imgsz_change: Callable[[int], None],
        capture_plate: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.processor = processor
        self.enhancer = enhancer
        self.tracker = tracker
        self.settings = settings
        self.recorder = recorder
        self.camera = camera
        self.unified_camera = unified_camera
        self._use_unified_camera = use_unified
        self.models = models
        self.cameras = cameras
        self.configs = configs
        self.ui = ui
        self.last_raw_frame = last_raw_frame
        self.roi_source_size = roi_source_size
        self.get_effective_roi = get_effective_roi
        self.reset_sensitivity_anchor = reset_sensitivity_anchor
        self.sync_mask_ui = sync_mask_ui
        self.request_reprocess = request_reprocess
        self.imgsz_change = imgsz_change
        self.capture_plate = capture_plate

        self._calibrator = SceneCalibrator()    # Go-Live scene calibration (P2)
        self._calibrating = False               # True while a calibration window is collecting
        self._servo: Optional[ExposureServo] = None      # Calib1 phase A (live IDS only)
        self._servo_result = None                        # ServoResult for the result dialog
        self._take_camera_line = ""                      # exposure/gain restored from the take
        # D29 empty-wall YOLO check (after the scene window; _calibrating stays True)
        self.wall_check_enabled: bool = EMPTY_WALL_CHECK
        self._wall_check: Optional[EmptyWallCheck] = None
        self._wall_conf: Optional[float] = None          # live confidence, restored after the check
        self._wall_result = None                         # EmptyWallResult for the result dialog
        self._scene_pending = None                       # (CalibrationResult, gamma_capped) for the dialog
        self._pre_enhancement = None                     # the project's calibrated (gamma, CLAHE) at Calibrate
        # X3: emptiness first -- the person watch before anything is measured (core/empty_wall.py)
        self.emptiness_first: bool = True
        self._emptiness: Optional[PersonWatch] = None
        self._emptiness_resume = None                    # the enhancement the next phase expects
        self.enhancement_ceiling = None                  # X3: the strongest clean (gamma, CLAHE) of the last check
        self.last_stop: Optional[str] = None             # why the last Calibrate stopped (someone in view)
        # A6 ②: what Calibrate found, to put back when it is cancelled or stopped; the plate held until Save
        self._snapshot: Optional[dict] = None
        self._pending_plate = None                       # (plate, the app's writer) recorded, not yet written
        self._plate_hold = None                          # our done-callback while the capture runs
        self._plate_commit_requested = False
        # Calib2 (UX_PLAN U4): dancer evidence pool — accumulative across runs.
        self._calib2 = SubjectCollector()
        self._calibrating2 = False
        self._calib2_saved_frames = 0
        self.blur_budget_ms: float = AUTOCAL_BLUR_BUDGET_MS
        # Track C — calibration provenance: which phase set which knob + when.
        # In-memory (session-scoped); config persistence is a Track S follow-up.
        # Aim/Calib1 owns the scene knobs; Calib2 (DANCERS) owns subject knobs.
        self.calibration_state: dict = {}
        # Track C (noise-σ unify): Aim's last clean-scene window σ + when, so a
        # Calib2 pass run right after Aim reuses it for the dark net-height
        # target instead of re-reading a now-dancer-populated live motion model.
        self._last_calib1_noise_sigma: Optional[float] = None
        self._last_calib1_noise_ts: float = 0.0

    def _stamp_calib_state(self, source: str, *knobs: str) -> None:
        """Record which calibration phase ('aim'/'dancers') set which knob, and
        when (Track C/S provenance — feeds the Aim 'Last calibrated' line)."""
        ts = time.strftime("%Y-%m-%d %H:%M")
        epoch = time.time()
        for k in knobs:
            self.calibration_state[k] = {"source": source, "ts": ts, "epoch": epoch}

    # Track S — Aim "Last calibrated" provenance line --------------------
    _CALIB_KNOB_LABELS = {
        "gamma": "gamma", "mog2_var_threshold": "var", "mog2_scale": "scale",
        "clahe": "CLAHE", "person_height_px": "height", "confidence": "conf",
        "imgsz": "imgsz", "blur_budget_ms": "blur",
        "ids_exposure_us": "exposure", "ids_gain_db": "gain",
        "enhancement_ceiling": "ceiling",
    }

    @staticmethod
    def _ago(epoch: float) -> str:
        if not epoch:
            return ""
        try:
            secs = max(0.0, time.time() - float(epoch))
        except (TypeError, ValueError):
            return ""
        if secs < 90:
            return "just now"
        mins = secs / 60.0
        if mins < 90:
            return f"{int(round(mins))} min ago"
        hrs = mins / 60.0
        if hrs < 36:
            return f"{int(round(hrs))} h ago"
        return f"{int(round(hrs / 24.0))} d ago"

    def _aim_calib_line(self) -> str:
        """Format the calibration provenance (which phase set which knob + when)
        into the Aim panel's 'Last calibrated' line."""
        st = self.calibration_state or {}

        def group(src, label):
            ks = [k for k, v in st.items()
                  if isinstance(v, dict) and v.get("source") == src]
            if not ks:
                return None
            latest = max((st[k].get("epoch", 0) or 0) for k in ks)
            names = ", ".join(self._CALIB_KNOB_LABELS.get(k, k) for k in sorted(ks))
            when = self._ago(latest)
            sep = " " if when else ""
            return f"{label}{sep}{when} ({names})"

        parts = [p for p in (group("aim", "Aim:"), group("dancers", "Dancers:")) if p]
        return "Last calibrated · " + " · ".join(parts) if parts else "Last calibrated: --"

    def _cb_view_aim_state(self) -> None:
        """Push the current provenance line to the Aim panel (on phase entry)."""
        if self.ui.available:
            self.ui.update_aim_calib_state(self._aim_calib_line())

    # ------------------------------------------------------------------
    # Calib1 — CALIBRATE (scene window, optional exposure servo)
    # ------------------------------------------------------------------
    def _cb_calibrate(self):
        """Calibrate button → start a Go-Live scene calibration window.

        Forces YOLO on for the window (so it works in Standby and during
        recording playback) and measures person size, MOG2 noise and FPS.  The
        run loop feeds frames to ``self._calibrator`` and applies the result.
        """
        if self._calibrating:
            # Second press cancels a run that is still collecting (e.g. if
            # playback was paused before the window filled).
            self._calibrating = False
            self._servo = None
            self._emptiness = self._emptiness_resume = None
            self._calibrator.cancel()
            self.processor.cancel_exclusion_calibration()
            if self._wall_check is not None:   # stopped during the empty-wall check: the scene pick, unchecked
                first = self._wall_check.tried[0]
                self._set_enhancement(first.gamma, first.clahe)
                self._end_wall_check(cancelled=True)
            self._restore_snapshot()           # A6 ②: and everything Calibrate changed goes back
            if self.ui.available:
                self.ui.set_calibrate_status(None)
                self.ui.show_toast("Calibration cancelled",
                                   duration=2.0, color=(255, 180, 80))
            print("[Calibrate] cancelled")
            return
        if not self.models._model_loaded or self.models.model is None:
            if self.ui.available:
                self.ui.show_toast("Load a model before calibrating",
                                   duration=3.0, color=(255, 180, 80))
            return
        # Need frames flowing: a live camera (IDS/unified or OpenCV) or playback.
        cam_open = (
            (self.unified_camera is not None and self.unified_camera.is_open)
            or (self.camera is not None and self.camera.state.is_open)
        )
        if not cam_open and not self.recorder.is_playing:
            if self.ui.available:
                self.ui.show_toast("Start the camera or play a recording first",
                                   duration=3.0, color=(255, 180, 80))
            return
        # A6 ②: what this Calibrate may change, as it is now (put back if it is cancelled or stopped)
        self._snapshot = self._take_snapshot()
        self.last_stop = None
        # Calib1 phase A: exposure/gain servo — only when we can actually
        # drive the sensor (live IDS camera, not playback).
        self._servo = None
        self._servo_result = None
        self._take_camera_line = ""
        # The project's calibrated enhancement, before the gamma seed below replaces it: the
        # empty-wall check tries it first and keeps it when YOLO sees nobody with it.
        self._pre_enhancement = ((float(self.enhancer.gamma), float(self.enhancer.clahe_clip))
                                 if "gamma" in (self.calibration_state or {}) else None)
        live_ids = (
            not self.recorder.is_playing
            and self._use_unified_camera
            and self.unified_camera is not None
            and self.unified_camera.is_open
            and self.unified_camera.source_type == CameraSource.IDS_PEAK
        )
        self._calibrating = True
        if live_ids:
            self._servo = ExposureServo(self.cameras.ids_exposure_us, self.cameras.ids_gain_db,
                                        blur_budget_ms=self.blur_budget_ms,
                                        gain_max_db=self._servo_gain_max_db())
            if self.ui.available:
                self.ui.set_calibrate_status("Calibrating exposure...")
                self.ui.show_toast("Calibrating - driving exposure/gain, keep the stage clear",
                                   duration=2.5, color=(160, 200, 255))
        else:
            # Playback of an empty-wall take (D29): the camera must run at the exposure/gain the
            # take was recorded with, or the enhancement and the plate picked on it do not match live.
            cam = self.recorder.playback_camera if self.recorder.is_playing else None
            if cam is not None:
                self.cameras._cb_ids_exposure_change(cam["exposure_us"])
                self.cameras._cb_ids_gain_change(cam["gain_db"])
                self._snapshot["camera_touched"] = True
                if self.ui.available:
                    self.ui.sync_slider("ids_exposure_us", self.cameras.ids_exposure_us)
                    self.ui.sync_slider("ids_gain_db", self.cameras.ids_gain_db)
                self._stamp_calib_state("aim", "ids_exposure_us", "ids_gain_db")
                self._take_camera_line = (f"Exposure {cam['exposure_us'] / 1000.0:.1f} ms / gain "
                                          f"{cam['gain_db']:.1f} dB: from the take\n")
                print("[Calibrate] " + self._take_camera_line.strip())
            # No camera control: seed gamma from the current raw brightness so
            # the var sweep sees the final motion-feed gamma, then collect.
            raw = self.last_raw_frame()
            if raw is not None:
                self._seed_gamma_for_calibration(float(raw.mean()))
            self._calibrator.start()
            # Exclusion masks are MANUAL-ONLY (OPERATOR_V2 decision 5) — Aim no
            # longer opens an exclusion-collection window.
            if self.ui.available:
                self.ui.set_calibrate_status("Calibrating 0%")
                self.ui.show_toast("Calibrating scene - keep the stage clear",
                                   duration=2.5, color=(160, 200, 255))
        print("[Calibrate] started "
              f"({'playback' if self.recorder.is_playing else 'live'}"
              f"{', exposure servo' if self._servo else ''})")
        if self.emptiness_first:
            self._start_emptiness()

    def _servo_gain_max_db(self) -> float:
        """The servo's gain ceiling: AUTOCAL_SERVO_GAIN_MAX_DB, cut to what the camera can do
        (the IMX664 tops out at 30 dB = x31.6; steps above it would change nothing)."""
        cap = float(AUTOCAL_SERVO_GAIN_MAX_DB)
        try:
            rng = self.unified_camera.get_gain_range_db()
        except Exception:
            return cap
        if (isinstance(rng, (tuple, list)) and len(rng) == 2
                and isinstance(rng[1], (int, float)) and rng[1] > 0):
            return min(cap, float(rng[1]))
        return cap

    def _servo_roi(self, raw):
        """The region the exposure servo exposes for: the operator's ROI (x, y, w, h) on this
        frame when one is enabled, else None (= the whole frame)."""
        enabled = getattr(self.settings, "roi_enabled", False)
        if not (isinstance(enabled, (bool, np.bool_)) and enabled):
            return None
        h, w = raw.shape[:2]
        try:
            return tuple(int(v) for v in self.get_effective_roi(w, h))
        except Exception:
            return None

    def _seed_gamma_for_calibration(self, brightness: float):
        """Apply the gamma seed before the collection window (Calib1 phase B)."""
        g = seed_gamma(brightness)
        self.enhancer.gamma = g
        self.enhancer._update_gamma_lut()
        if self.ui.available:
            self.ui.sync_slider('gamma', g)
        print(f"[Calibrate] gamma seeded to {g:.3f} "
              f"(raw brightness {brightness:.0f})")

    def _step_calibration(self, tracked, process_wall_ms):
        """Feed one processed frame to the active calibrator; finalize when ready."""
        if self._emptiness is not None:            # X3: emptiness first, before anything is measured
            self._step_emptiness()
            return
        if self._wall_check is not None:
            self._step_wall_check()
            return
        # Phase A: exposure/gain servo (live IDS). Commands are applied through
        # the normal IDS callbacks so sliders/persistence stay in sync.
        if self._servo is not None:
            raw = self.last_raw_frame()
            if raw is not None:
                # X1: expose for the highlights of the zone (the ROI, else the frame): its 99.5th percentile
                # under 235 with belt-sized blobs left out, the median a floor (zone_highlights, ExposureServo).
                z = zone_highlights(raw, self._servo_roi(raw))
                b, clip_pct, region = z.median, z.clip_pct, z.region
                cmd = self._servo.feed(b, clip_pct, region, zone=z)
                if cmd is not None:
                    kind, value = cmd
                    print(f"[Calibrate] servo: {region} median {b:.0f}, clip {clip_pct:.1f}%, p99.5 {z.p995:.0f} "
                          f"-> {kind} {value / 1000.0 if kind == 'exposure' else value:.2f}"
                          f" {'ms' if kind == 'exposure' else 'dB'}")
                    if kind == "exposure":
                        self.cameras._cb_ids_exposure_change(value)
                        if self.ui.available:
                            self.ui.sync_slider("ids_exposure_us", self.cameras.ids_exposure_us)
                    else:
                        self.cameras._cb_ids_gain_change(value)
                        if self.ui.available:
                            self.ui.sync_slider("ids_gain_db", self.cameras.ids_gain_db)
                    if self._snapshot is not None:
                        self._snapshot["camera_touched"] = True
                if self.ui.available:
                    self.ui.set_calibrate_status(f"Calibrating exposure ({b:.0f})")
            if self._servo.done:
                self._servo_result = self._servo.result()
                # A6 ①: report what the camera controller runs at, not the servo's own idea of it
                for attr, key in (("exposure_us", "ids_exposure_us"), ("gain_db", "ids_gain_db")):
                    v = getattr(self.cameras, key, None)
                    if isinstance(v, numbers.Real) and not isinstance(v, bool):
                        setattr(self._servo_result, attr, float(v))
                print("[Calibrate] " + self._servo_result.summary_line())
                if self._servo_result.conflict and self.ui.available:   # X1: the light is the limit, not a setting
                    self.ui.show_toast(self._servo_result.conflict_line(), duration=10.0, color=(255, 140, 70))
                self._seed_gamma_for_calibration(self._servo_result.brightness)
                self._servo = None
                self._calibrator.start()
                # Exclusion masks are MANUAL-ONLY (OPERATOR_V2 decision 5).
            return

        cal = self._calibrator
        if not cal.is_collecting:
            self._calibrating = False
            return
        heights = []
        for t in (tracked or []):
            b = getattr(t, 'bbox', None)
            if b is not None and len(b) >= 4 and b[3] > 0:
                heights.append(float(b[3]))
        fps_sample = (1000.0 / process_wall_ms) if process_wall_ms > 0 else 0.0
        # Noise from the actual MOG2 input (post-CLAHE), so varThreshold matches
        # what the background model fights; brightness from the raw frame so the
        # exposure report reflects true IR scene luma (near-black on dark rigs).
        noise_gray = self.processor.get_last_motion_gray()
        raw = self.last_raw_frame()
        if noise_gray is None:
            noise_gray = raw  # motion detection disabled → fall back to raw
        brightness = float(raw.mean()) if raw is not None else None
        cal.feed(noise_gray, heights, fps_sample, time.time(),
                 brightness=brightness, report_frame=raw)
        if self.ui.available:
            self.ui.set_calibrate_status(f"Calibrating {int(cal.progress() * 100)}%")
        if cal.ready:
            self._calibrating = False
            self._apply_calibration(cal.compute())

    def _apply_calibration(self, result):
        """Apply a finished CalibrationResult to the running session + log it.

        Aim/scene pass: derives servo + gamma + var + clean-plate.  Person
        height is DIAGNOSTIC-ONLY here (measured for the report card + MOG2
        scaling) — Calib2 (DANCERS) is the SOLE writer of person_height_px,
        which needs dancers in frame; an empty-stage height measure is noise
        (Track C: height ownership).
        """
        if self._snapshot is None:                 # A6 ②: reached without _cb_calibrate (tools, tests)
            self._snapshot = self._take_snapshot()
        self._snapshot["scene_applied"] = True
        # Track C apply-gate: warn (never block — calibration is idempotent)
        # when the scene defeats MOG2, so the operator can fix IR/gain before
        # trusting the ghost rejection.
        if result.var_saturated and self.ui.available:
            self.ui.show_toast(
                "Calibrate: scene too noisy for MOG2 (varThreshold saturated) "
                "— ghost rejection is weak. Add/even the IR or lower gain, "
                "then re-Aim.", duration=6.0, color=(255, 140, 70))
        # Track C (noise-σ unify): keep Aim's clean-scene window σ for a
        # following Calib2 pass (see _calib2_aggregate).
        self._last_calib1_noise_sigma = float(result.noise_sigma)
        self._last_calib1_noise_ts = time.time()
        # Person height is Calib2-owned (Track C) — Calib1 no longer writes it.
        if result.var_ok and result.var_threshold:
            self.processor.set_motion_var_threshold(result.var_threshold)
            self.reset_sensitivity_anchor(var_anchor=result.var_threshold)
        if result.var_ok and result.mog2_scale:
            self.processor.set_motion_scale(result.mog2_scale)
            if self.ui.available:
                self.ui.sync_slider("mog2_scale", result.mog2_scale)
        if result.clahe_value is not None:
            self.enhancer.clahe_clip = float(result.clahe_value)
            self.enhancer._update_clahe()
            if self.ui.available:
                self.ui.sync_slider("clahe", float(result.clahe_value))
        # ⑤b: cap the seeded gamma when the window-measured noise σ is high —
        # on a verydark scene aggressive brightening mostly amplifies noise
        # (ghosts).  After the window on purpose: the var sweep saw the
        # brighter gamma, so the picked varThreshold is conservative.
        capped_gamma, gamma_capped = cap_gamma_for_noise(
            self.enhancer.gamma, result.noise_sigma)
        if gamma_capped:
            self.enhancer.gamma = capped_gamma
            self.enhancer._update_gamma_lut()
            if self.ui.available:
                self.ui.sync_slider('gamma', capped_gamma)
            print(f"[Calibrate] gamma capped to {capped_gamma:.3f} "
                  f"(noise sigma {result.noise_sigma:.2f})")
        # Exclusion masks are MANUAL-ONLY now (OPERATOR_V2 decision 5): Aim
        # derives servo + gamma + var + clean-plate but does NOT auto-build or
        # activate an exclusion mask.  Any manually-painted mask is left intact
        # and keeps applying (we never opened a collection window).
        touched = ["gamma"]
        if result.var_ok and result.var_threshold:
            touched += ["mog2_var_threshold", "mog2_scale"]
        if result.clahe_value is not None:
            touched.append("clahe")
        self._stamp_calib_state("aim", *touched)
        # H4 (#t-063): the model and the network input this Calibrate ran with (the « references » readiness row)
        shape, imgsz = getattr(self.settings, "yolo_shape", None), getattr(self.settings, "imgsz", 0)
        self.calibration_state["network"] = {
            "source": "calibrate", "ts": time.strftime("%Y-%m-%d %H:%M"), "epoch": time.time(),
            "model": str(getattr(self.models, "current_model_name", "") or ""),
            "input": f"{int(shape[0])}x{int(shape[1])}" if shape else str(int(imgsz or 0))}
        print(result.log_line())
        self.request_reprocess()
        # Calibrate runs on the EMPTY wall: record it as the clean plate (the foreground
        # evidence for the dancer ids; the "clean-plate" Aim always promised, D16).
        # A6 ②: recorded now, written to the project at Save (commit_pending_plate).
        self._capture_pending_plate()
        self._scene_pending = (result, gamma_capped)
        self._wall_result = None
        if not self._start_wall_check():
            self._finish_calibration()

    # ------------------------------------------------------------------
    # D29 — empty-wall YOLO check (the last stage of Calibrate)
    # ------------------------------------------------------------------
    def _set_enhancement(self, gamma: float, clahe: float) -> None:
        self.enhancer.gamma = float(gamma)
        self.enhancer._update_gamma_lut()
        self.enhancer.clahe_clip = float(clahe)
        self.enhancer._update_clahe()
        if self.ui.available:
            self.ui.sync_slider("gamma", float(gamma))
            self.ui.sync_slider("clahe", float(clahe))

    def _start_wall_check(self) -> bool:
        """Run the pipeline on the same empty wall with each enhancement of the ladder, strongest
        first, until YOLO finds nobody (core/empty_wall.py).  YOLO runs at the check's lower
        confidence meanwhile, so near misses count too; the live confidence comes back after."""
        if not self.wall_check_enabled:
            return False
        try:
            chk = EmptyWallCheck(float(self.enhancer.gamma), float(self.enhancer.clahe_clip),
                                 float(self.settings.confidence),
                                 keep=getattr(self, "_pre_enhancement", None),
                                 region=self._wall_region())
        except (TypeError, ValueError) as exc:
            print(f"[EmptyWall] check not started ({exc})")
            return False
        self._wall_check = chk
        first = chk.current()
        if first != (float(self.enhancer.gamma), float(self.enhancer.clahe_clip)):
            self._set_enhancement(*first)         # the project's calibrated enhancement goes first
        self._wall_conf = float(self.settings.confidence)
        self.settings.confidence = chk.conf_limit
        self._calibrating = True                  # YOLO stays forced on; the loop keeps stepping
        if self.ui.available:
            self.ui.set_calibrate_status("Checking the empty wall 0%")
        print(f"[EmptyWall] YOLO check: {len(chk.steps)} candidates, persons >= {chk.conf_limit:.2f}")
        return True

    def _step_wall_check(self) -> None:
        chk = self._wall_check
        nxt = chk.feed(list(getattr(self.processor, "last_raw_dets", None) or ()))
        if nxt is not None:
            self._set_enhancement(*nxt)
        if chk.done and chk.result.person is not None:     # X3: never lowered because of a person
            self._end_wall_check()
            self._stop_for_person(chk.result.person, chk.result.region)
        elif chk.done:
            self._set_enhancement(chk.result.gamma, chk.result.clahe)
            self._end_wall_check()
            self._finish_calibration()
        elif self.ui.available:
            self.ui.set_calibrate_status(f"Checking the empty wall {int(chk.progress() * 100)}%")

    def _end_wall_check(self, cancelled: bool = False) -> None:
        chk = self._wall_check
        self._wall_check = None
        self._calibrating = False
        if self._wall_conf is not None:
            self.settings.confidence = self._wall_conf
            self._wall_conf = None
        if chk is None or cancelled or chk.result is None:
            if cancelled:
                print("[EmptyWall] check cancelled: the scene pick is kept, unchecked")
            return
        r = chk.result
        self._wall_result = r
        print(r.log_line())
        if r.person is not None:                   # X3: the caller stops and puts everything back
            return
        if (r.gamma, r.clahe) != (chk.tried[0].gamma, chk.tried[0].clahe):
            self._stamp_calib_state("aim", "gamma", "clahe")
        self.request_reprocess()
        if not r.clean and self.ui.available:
            self.ui.show_toast("YOLO now and then sees a person on the EMPTY wall, even at the weakest "
                               "setting: if an object there looks like a person, remove it or move the "
                               "Region of Interest off it, then Calibrate again.",
                               duration=8.0, color=(255, 140, 70))

    def _finish_calibration(self) -> None:
        """The report card: servo / take exposure, gamma, the scene window, the empty-wall check."""
        result, gamma_capped = self._scene_pending or (None, False)
        self._scene_pending = None
        self._snapshot = None                      # A6 ②: a finished Calibrate is the session's state now
        self._record_ceiling()
        if not self.ui.available or result is None:
            return
        self.ui.set_calibrate_status(None)
        self.ui.update_aim_calib_state(self._aim_calib_line())  # Track S
        servo_line = (self._servo_result.summary_line() + "\n"
                      if self._servo_result else self._take_camera_line)
        gamma_line = (f"Gamma: {self.enhancer.gamma:.3f}"
                      + ("  (capped: scene noise high)" if gamma_capped else "")
                      + "\n")
        wall = (self._wall_result.summary() + "\n") if self._wall_result is not None else ""
        # "Save to project" must be a normal timestamped save (what startup
        # and the picker load) — safe-defaults stays a separate explicit
        # action (ROADMAP bug #6).
        self.ui.show_calibration_result_dialog(
            servo_line + gamma_line + result.summary() + "\n" + wall,
            on_save=self._save_calibration)

    # ------------------------------------------------------------------
    # X3 — emptiness first; A6 ② — a cancelled or stopped Calibrate puts things back
    # ------------------------------------------------------------------
    def _wall_region(self):
        """The ROI (x, y, w, h) on the current frame, else None: where ``Sighting.where`` places someone."""
        raw = self.last_raw_frame()
        return self._servo_roi(raw) if raw is not None else None

    def _start_emptiness(self) -> None:
        """X3: before anything is measured, watch a few frames for someone in view, at gamma <= neutral so
        an enhancement ghost does not pass for a person (core/empty_wall.py)."""
        snap = self._snapshot or {}
        self._emptiness_resume = (float(self.enhancer.gamma), float(self.enhancer.clahe_clip))
        g0 = snap.get("gamma")
        g = min(g0, NEUTRAL_GAMMA) if g0 is not None else NEUTRAL_GAMMA
        c = snap.get("clahe")
        self._set_enhancement(g, c if c is not None else self._emptiness_resume[1])
        self._emptiness = PersonWatch(float(self.settings.confidence), settle=PERSON_SETTLE)
        if self.ui.available:
            self.ui.set_calibrate_status("Checking that the wall is empty...")
        print(f"[Calibrate] emptiness first: watching {self._emptiness.frames} frames at gamma {g:.2f}, "
              f"persons >= {self._emptiness.confidence:.2f}")

    def _step_emptiness(self) -> None:
        w = self._emptiness
        w.feed(list(getattr(self.processor, "last_raw_dets", None) or ()))
        if not w.done:
            return
        self._emptiness = None
        if w.person:
            self._stop_for_person(w.sighting, self._wall_region())
            return
        resume, self._emptiness_resume = self._emptiness_resume, None
        if resume is not None:
            self._set_enhancement(*resume)
        print(f"[Calibrate] the wall is empty: a person in {w.hits} of {w.seen} frames")
        if self.ui.available:
            self.ui.set_calibrate_status("Calibrating exposure..." if self._servo is not None
                                         else "Calibrating 0%")

    def _stop_for_person(self, sighting, region) -> None:
        """X3: someone is in view -- Calibrate stops, says where, and changes nothing (A6 ②)."""
        self._calibrating = False
        self._servo = None
        self._emptiness = None
        self._emptiness_resume = None
        self._calibrator.cancel()
        self.processor.cancel_exclusion_calibration()
        if self._wall_check is not None:
            self._end_wall_check(cancelled=True)
        self._restore_snapshot()
        where = sighting.where(region) if sighting is not None else "in the picture"
        self.last_stop = (f"Someone is in view, {where}: Calibrate stopped and changed nothing. "
                          "Clear the wall, then press Calibrate again.")
        print(f"[Calibrate] stopped: {self.last_stop}"
              + (f" ({sighting.hits}/{sighting.frames} frames, conf up to {sighting.conf:.2f}, "
                 f"h {sighting.h:.0f} px)" if sighting is not None else ""))
        if self.ui.available:
            self.ui.set_calibrate_status(f"Stopped: someone in view, {where}")
            self.ui.show_toast(self.last_stop, duration=10.0, color=(255, 140, 70))

    @staticmethod
    def _real(v):
        return float(v) if isinstance(v, numbers.Real) and not isinstance(v, bool) else None

    def _take_snapshot(self) -> dict:
        """What a Calibrate may change, as it is before it starts."""
        r = self._real

        def read(fn):
            try:
                return r(fn())
            except Exception:  # noqa: BLE001
                return None
        cams = self.cameras
        return {
            "gamma": r(self.enhancer.gamma), "clahe": r(self.enhancer.clahe_clip),
            "confidence": r(self.settings.confidence),
            "exposure_us": r(getattr(cams, "ids_exposure_us", None)),
            "gain_db": r(getattr(cams, "ids_gain_db", None)),
            "exposure_auto": getattr(cams, "ids_exposure_auto", None),
            "gain_auto": getattr(cams, "ids_gain_auto", None),
            "var": read(self.processor.get_motion_var_threshold),
            "scale": read(self.processor.get_motion_scale),
            "state": copy.deepcopy(self.calibration_state),
            "noise": (self._last_calib1_noise_sigma, self._last_calib1_noise_ts),
            "camera_touched": False, "scene_applied": False,
        }

    def _restore_snapshot(self) -> bool:
        """A6 ②: put back what this Calibrate changed -- enhancement, confidence, exposure/gain, MOG2 var
        and scale, the provenance -- and drop the plate it recorded.  False when there is nothing to restore."""
        snap, self._snapshot = self._snapshot, None
        self._drop_pending_plate()
        self._scene_pending = None
        self._wall_result = None
        self._servo_result = None
        if snap is None:
            return False
        if snap["gamma"] is not None and snap["clahe"] is not None:
            self._set_enhancement(snap["gamma"], snap["clahe"])
        if snap["confidence"] is not None:
            self.settings.confidence = snap["confidence"]
        if snap["camera_touched"]:
            cams = self.cameras
            if snap["exposure_auto"] is True:
                cams._cb_ids_exposure_auto_toggle(True)
            elif snap["exposure_us"] is not None:
                cams._cb_ids_exposure_change(snap["exposure_us"])
            if snap["gain_auto"] is True:
                cams._cb_ids_gain_auto_toggle(True)
            elif snap["gain_db"] is not None:
                cams._cb_ids_gain_change(snap["gain_db"])
            if self.ui.available:
                self.ui.sync_slider("ids_exposure_us", cams.ids_exposure_us)
                self.ui.sync_slider("ids_gain_db", cams.ids_gain_db)
        if snap["scene_applied"]:
            if snap["var"] is not None:
                self.processor.set_motion_var_threshold(snap["var"])
                self.reset_sensitivity_anchor(var_anchor=snap["var"])
            if snap["scale"] is not None:
                self.processor.set_motion_scale(snap["scale"])
                if self.ui.available:
                    self.ui.sync_slider("mog2_scale", snap["scale"])
        self.calibration_state = snap["state"]
        self._last_calib1_noise_sigma, self._last_calib1_noise_ts = snap["noise"]
        self.request_reprocess()
        print(f"[Calibrate] put back: gamma {snap['gamma']}, CLAHE {snap['clahe']}"
              + (f", exposure {snap['exposure_us']} us, gain {snap['gain_db']} dB" if snap["camera_touched"] else "")
              + (f", MOG2 var {snap['var']} scale {snap['scale']}" if snap["scene_applied"] else ""))
        return True

    def _record_ceiling(self) -> None:
        """X3: the strongest setting the empty-wall check found clean, kept for the dancer step (X4)."""
        r = self._wall_result
        self.enhancement_ceiling = r.ceiling if r is not None else None
        if self.enhancement_ceiling is not None:
            self._stamp_calib_state("aim", "enhancement_ceiling")
            g, c = self.enhancement_ceiling
            self.calibration_state["enhancement_ceiling"].update(
                gamma=round(float(g), 4), clahe=round(float(c), 3), kept_current=bool(r.kept_current))

    # ------------------------------------------------------------------
    # A6 ② — the empty-wall plate is written at Save, not before
    # ------------------------------------------------------------------
    def _capture_pending_plate(self) -> None:
        """Record the empty wall with the app's capture (the processor averages the next frames and installs
        the plate for this session), but hold the app's writer: the file is written when the project is
        saved (``commit_pending_plate``, on every SaveConfig).  A cancelled or stopped Calibrate, or one
        kept for this session only, leaves the project's plate on disk as it was."""
        self._drop_pending_plate(revert=False)
        if self.capture_plate is None:
            return
        try:
            self.capture_plate("calibrate")
        except Exception as exc:  # noqa: BLE001 - never fail a calibration on it
            print(f"[Foreground] plate capture not started ({type(exc).__name__}: {exc})")
            return
        proc = self.processor
        write = getattr(proc, "_plate_capture_done", None)
        if not callable(write):
            return                                 # nothing to hold: the capture writes as before

        def hold(plate, error, _write=write):
            self._plate_hold = None
            stamp = getattr(_write, "stamp_camera", None)   # X6: its camera facts now, as the capture ends, not at Save
            if plate is not None and callable(stamp):
                try:
                    stamp(plate)
                except Exception as exc:  # noqa: BLE001 - the plate is kept; the writer stamps it at Save
                    print(f"[Foreground] empty wall's camera not recorded yet ({type(exc).__name__}: {exc})")
            if plate is None or self._plate_commit_requested:   # a failure is reported at once
                self._plate_commit_requested = False
                _write(plate, error)
            else:
                self._pending_plate = (plate, _write)
                print("[Foreground] empty wall recorded for this session: written to the project at Save")
            ended = getattr(self.ui, "calibrate_plate_ended", None)   # X2: the card waiting for its verdict
            if self.ui.available and callable(ended):
                ended(plate is not None)
        proc._plate_capture_done = hold
        self._plate_hold = hold

    def _drop_pending_plate(self, revert: bool = True) -> None:
        pending, self._pending_plate = self._pending_plate, None
        hold, self._plate_hold = self._plate_hold, None
        self._plate_commit_requested = False
        proc = self.processor
        if hold is not None and getattr(proc, "_plate_capture_done", None) is hold:
            proc._plate_capture = None             # still averaging: stopped, or its frames become the plate
            proc._plate_capture_done = None
            print("[Foreground] Calibrate's empty-wall recording stopped")
        if revert and pending is not None:         # this session goes back to the project's plate (or none)
            proc.set_clean_plate(None)
            proc._fg_loaded_path = None            # the processor reloads settings.fg_plate_path
            print("[Foreground] Calibrate's empty-wall recording dropped: the project's plate stays")

    def commit_pending_plate(self) -> bool:
        """A6 ②: write the plate the last Calibrate recorded -- the app calls it on every project save.
        True when one is written (or will be, the moment its capture ends); never while Calibrate runs."""
        if self._calibrating:
            return False
        pending, self._pending_plate = self._pending_plate, None
        if pending is not None:
            plate, write = pending
            write(plate, "")
            return True
        if self._plate_hold is not None and getattr(self.processor, "_plate_capture_done", None) is self._plate_hold:
            self._plate_commit_requested = True
            return True
        return False

    def _save_calibration(self) -> None:
        """The result dialog's « Save to project »: the config, then the plate Calibrate recorded."""
        self.configs._cb_save_config()
        self.commit_pending_plate()

    # ------------------------------------------------------------------
    # Calib2 — dancer evidence pool (UX_PLAN U4)
    # ------------------------------------------------------------------
    def _calib2_pool(self) -> SubjectPool:
        return SubjectPool(os.path.join(self.configs.config_store.config_dir,
                                        self.configs._current_project))

    def _cb_calib2(self):
        """CALIB DANCERS button: collect one evidence run (live or playback)."""
        if self._calibrating2:
            self._calibrating2 = False
            self._calib2.cancel()
            if self.ui.available:
                self.ui.set_calibrate_status(None)
                self.ui.show_toast("Dancer calibration cancelled",
                                   duration=2.0, color=(255, 180, 80))
            print("[Calib2] cancelled")
            return
        if self._calibrating:
            if self.ui.available:
                self.ui.show_toast("Scene calibration is running",
                                   duration=2.5, color=(255, 180, 80))
            return
        if not self.models._model_loaded or self.models.model is None:
            if self.ui.available:
                self.ui.show_toast("Load a model before calibrating",
                                   duration=3.0, color=(255, 180, 80))
            return
        cam_open = (
            (self.unified_camera is not None and self.unified_camera.is_open)
            or (self.camera is not None and self.camera.state.is_open)
        )
        if not cam_open and not self.recorder.is_playing:
            if self.ui.available:
                self.ui.show_toast("Start the camera or play a recording first",
                                   duration=3.0, color=(255, 180, 80))
            return
        source = (f"slot {self.recorder.status.current_slot}"
                  if self.recorder.is_playing else "live")
        frame_w, frame_h = self.roi_source_size()
        roi = self.get_effective_roi(frame_w, frame_h)
        self._calib2.start(source, self.configs._active_profile, roi, (frame_w, frame_h),
                           imgsz=int(self.settings.imgsz))
        self._calib2_saved_frames = 0
        self._calibrating2 = True
        if self.ui.available:
            self.ui.set_calibrate_status("Dancers 0%")
            self.ui.show_toast("Dancer calibration - have 1-4 dancers move around",
                               duration=3.0, color=(160, 200, 255))
        print(f"[Calib2] run started ({source}, profile={self.configs._active_profile})")

    def _step_calib2(self, tracked, process_wall_ms):
        """Feed one processed frame to the dancer-run collector."""
        col = self._calib2
        if not col.is_collecting:
            self._calibrating2 = False
            return
        samples = []
        for t in (tracked or []):
            b = getattr(t, 'bbox', None)
            if b is None or len(b) < 4 or b[3] <= 0:
                continue
            # YOLO BOX conf — same units settings.confidence thresholds
            # (kp-conf means pinned the seed at the clamp; bug #11 / ⑤a).
            # None when the track was bridge/cold-blob fed this frame.
            box_conf = getattr(t, 'box_conf', None)
            vel = getattr(t, 'velocity', None)
            speed = float(np.linalg.norm(vel)) if vel is not None else 0.0
            samples.append((float(b[3]), box_conf, speed))
        fps_sample = (1000.0 / process_wall_ms) if process_wall_ms > 0 else 0.0
        col.feed(samples, fps_sample)

        # Save a few raw frames for the future gamma/CLAHE confidence sweep.
        stride = max(1, AUTOCAL2_WINDOW_FRAMES // AUTOCAL2_FRAME_SAMPLES)
        raw = self.last_raw_frame()
        if (col.run.frames % stride == 0
                and self._calib2_saved_frames < AUTOCAL2_FRAME_SAMPLES
                and raw is not None):
            fdir = self._calib2_pool().frames_dir(col.run)
            try:
                os.makedirs(fdir, exist_ok=True)
                cv2.imwrite(os.path.join(fdir, f"f{col.run.frames:04d}.jpg"),
                            raw)
                self._calib2_saved_frames += 1
            except Exception:
                pass

        if self.ui.available:
            self.ui.set_calibrate_status(f"Dancers {int(col.progress() * 100)}%")
        if col.ready:
            self._calibrating2 = False
            run = col.finish()
            pool = self._calib2_pool()
            path = pool.save_run(run)
            print(f"[Calib2] run saved: {path} ({run.samples} samples)")
            if self.ui.available:
                self.ui.set_calibrate_status(None)
            self._show_calib2_dialog()

    def _make_imgsz_probe(self, runs):
        """K3: a probe(imgszs) -> {imgsz: mean detections/frame} over the saved
        Calib2 frames, or None.  Best-effort and PyTorch-only — a TRT engine is
        fixed-imgsz, so a cross-imgsz probe is unreliable (-> None -> aggregate
        falls back to the σ>4 heuristic).  Runs YOLO at a low conf floor; the
        preset that detects more of the known dancers reads the scene better
        (Phase 2b: downscale denoises dark scenes)."""
        try:
            if self.models.model_manager.is_using_tensorrt():
                return None
        except Exception:
            return None
        model = getattr(self.processor, "model", None)
        if model is None:
            return None
        pool = self._calib2_pool()
        frames = []
        for run in runs:
            try:
                for fp in sorted(glob.glob(os.path.join(pool.frames_dir(run), "*.jpg"))):
                    img = cv2.imread(fp)
                    if img is not None:
                        frames.append(img)
            except Exception:
                continue
        if not frames:
            return None

        def detect_fn(frame, imgsz, conf):
            try:
                res = model(frame, imgsz=int(imgsz), conf=float(conf), verbose=False)
                boxes = res[0].boxes if res else None
                return len(boxes) if boxes is not None else 0
            except Exception:
                return 0

        return lambda imgszs: probe_detection_counts(detect_fn, frames, imgszs)

    def _calib2_aggregate(self, runs, roi_long_side, with_probe: bool = False):
        """calib2.aggregate with the live calib-time context: MOG2-input noise
        sigma (dark-target switch), the per-rig engine fps table and the
        current model (P-6 cost curve + report-only model advisory).  On the
        explicit apply (``with_probe``) the K3 imgsz probe supersedes the σ
        heuristic; previews/toggles stay cheap (σ only)."""
        # Track C (noise-σ unify): if Aim ran recently, reuse its clean-scene
        # window σ (same MOG2-input-gray statistic) — a fresh Calib2 pass has
        # dancers in frame, which biases a live motion_model.noise_sigma() read
        # and could flip the 110↔45 dark net-height target.
        noise = None
        if (self._last_calib1_noise_sigma is not None and
                time.time() - self._last_calib1_noise_ts < AUTOCAL2_NOISE_REUSE_S):
            noise = self._last_calib1_noise_sigma
        if noise is None:
            mm = getattr(self.processor, "motion_model", None)
            if mm is not None:
                try:
                    noise = float(mm.noise_sigma())
                except Exception:
                    noise = None
        table = load_fps_table(os.path.join(MODELS_DIR, "fps_table.json"))
        current = getattr(self.models, "current_model_name", "") or ""
        probe = self._make_imgsz_probe(runs) if with_probe else None
        return calib2_aggregate(runs, roi_long_side, noise_sigma=noise,
                                fps_table=table, current_model=current,
                                imgsz_probe=probe)

    def _show_calib2_dialog(self):
        """Open the evidence-pool dialog with all stored runs + a proposal preview."""
        if not self.ui.available:
            return
        pool = self._calib2_pool()
        entries = pool.load_runs()
        frame_w, frame_h = self.roi_source_size()
        roi = self.get_effective_roi(frame_w, frame_h)
        # Track C: a run is stale if the framing drifted OR it was captured
        # under a different lighting profile than the one now active (its px
        # heights / detection conf no longer compare).
        active = getattr(self.configs, "_active_profile", None)
        rows = []
        for path, run in entries:
            framing_stale = run.stale_for(roi, (frame_w, frame_h))
            profile_stale = bool(active) and run.profile != active
            reason = ("framing changed" if framing_stale
                      else (f"profile != {active}" if profile_stale else ""))
            rows.append({
                "path": path,
                "label": run.label(),
                "stale": framing_stale or profile_stale,
                "stale_reason": reason,
            })
        # Initial proposal = the DEFAULT-checked (non-stale) subset, so the
        # preview matches the checkboxes; toggling recomputes for the actual
        # selection (Track C).  Fall back to all runs if every run is stale.
        default_runs = [run for (_p, run), row in zip(entries, rows)
                        if not row["stale"]] or [r for _p, r in entries]
        proposal = self._calib2_aggregate(default_runs, max(roi[2], roi[3]))
        self.ui.show_calib2_dialog(rows, proposal.summary())

    def _cb_calib2_apply(self, selected_paths, quiet: bool = False):
        """Apply the pooled proposal from the selected runs.

        ``quiet=True`` (checkbox toggle, Track C): recompute over the checked
        subset, apply the instant detection knobs (height/conf/blur) and refresh
        the inline proposal text live — NO result modal and NO imgsz/engine
        reload (heavy → deferred to the explicit Apply).  ``quiet=False`` (Apply
        button): full commit incl. the imgsz reload + the save-to-project modal.
        """
        pool = self._calib2_pool()
        chosen = [run for path, run in pool.load_runs() if path in set(selected_paths)]
        frame_w, frame_h = self.roi_source_size()
        roi = self.get_effective_roi(frame_w, frame_h)
        # K3: probe the imgsz empirically only on the real commit (not quiet
        # toggles, which stay cheap).
        prop = self._calib2_aggregate(chosen, max(roi[2], roi[3]),
                                      with_probe=not quiet)
        if not prop.ok:
            if self.ui.available:
                if quiet:                       # show why in-place, don't pop a toast
                    self.ui.update_calib2_proposal(prop.summary())
                else:
                    self.ui.show_toast(prop.summary(),
                                       duration=4.0, color=(255, 180, 80))
            return
        self.settings.person_height_px = int(prop.person_height_px)
        self.settings.person_height_min_ratio = float(prop.min_ratio)
        self.settings.person_height_max_ratio = float(prop.max_ratio)
        self.tracker.set_person_height(int(prop.person_height_px))
        if self.ui.available:
            self.ui.sync_slider('person_height', int(prop.person_height_px))
        if prop.confidence is not None:
            self.settings.confidence = float(prop.confidence)
            if self.ui.available:
                self.ui.sync_slider('confidence', float(prop.confidence))
            self.reset_sensitivity_anchor(conf_seed=float(prop.confidence))
        if prop.blur_budget_ms is not None:
            self.blur_budget_ms = float(prop.blur_budget_ms)
        # imgsz changes swap the TRT engine (seconds) — only on the explicit
        # Apply, never per checkbox toggle.  The proposed imgsz still shows in
        # the preview text either way.
        if (not quiet) and prop.imgsz and prop.imgsz != int(self.settings.imgsz):
            if self.ui.available:
                self.ui.sync_combo('imgsz', str(prop.imgsz))
            self.imgsz_change(prop.imgsz)   # handles the TRT engine reload
        touched = ["person_height_px"]
        if prop.confidence is not None:
            touched.append("confidence")
        if prop.blur_budget_ms is not None:
            touched.append("blur_budget_ms")
        if (not quiet) and prop.imgsz:
            touched.append("imgsz")
        self._stamp_calib_state("dancers", *touched)
        print(f"[Calib2] {'preview' if quiet else 'applied'}: "
              + prop.summary().replace("\n", " | "))
        self.request_reprocess()
        if not self.ui.available:
            return
        self.ui.update_aim_calib_state(self._aim_calib_line())  # Track S
        if quiet:
            self.ui.update_calib2_proposal(prop.summary())
        else:
            self.ui.show_calibration_result_dialog(
                "Dancer calibration (pooled)\n" + prop.summary(),
                on_save=self.configs._cb_save_config)

    def _cb_calib2_clear(self):
        removed = self._calib2_pool().clear()
        print(f"[Calib2] pool cleared ({removed} runs)")
        if self.ui.available:
            self.ui.show_toast(f"Cleared {removed} run(s)",
                               duration=2.5, color=(150, 200, 255))

    def _cb_view_calib2_pool(self):
        """Open the evidence-pool dialog without running a new DANCERS pass."""
        self._show_calib2_dialog()
