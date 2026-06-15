"""CALIBRATE / DANCERS calibration orchestration peeled from WallDanceApp.

DECOMPOSITION_PLAN §5 Phase 2 (5). Method bodies moved verbatim from
app.py; ``self.<app attribute>`` references renamed to constructor-injected
dependencies. The calibration *math* stays untouched in
``core/calibration.py`` / ``core/calib2.py`` — this controller owns only
the orchestration state machines the main loop steps per frame:

- Calib1 (CALIBRATE): optional exposure/gain servo phase (live IDS only),
  then the SceneCalibrator collection window → apply result + report card.
- Calib2 (DANCERS): SubjectCollector evidence run → pool save → review
  dialog → pooled apply.

Sibling controllers are injected under their app attribute names
(``models``/``cameras``/``configs``) so moved bodies keep their references
verbatim.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional, Protocol

import cv2
import numpy as np

from core.calib2 import SubjectCollector, SubjectPool, load_fps_table
from core.calib2 import aggregate as calib2_aggregate
from core.calibration import (ExposureServo, SceneCalibrator,
                              cap_gamma_for_noise, seed_gamma)
from core.config import (AUTOCAL_BLUR_BUDGET_MS, AUTOCAL2_FRAME_SAMPLES,
                         AUTOCAL2_WINDOW_FRAMES, MODELS_DIR)

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

        self._calibrator = SceneCalibrator()    # Go-Live scene calibration (P2)
        self._calibrating = False               # True while a calibration window is collecting
        self._servo: Optional[ExposureServo] = None      # Calib1 phase A (live IDS only)
        self._servo_result = None                        # ServoResult for the result dialog
        # Calib2 (UX_PLAN U4): dancer evidence pool — accumulative across runs.
        self._calib2 = SubjectCollector()
        self._calibrating2 = False
        self._calib2_saved_frames = 0
        self.blur_budget_ms: float = AUTOCAL_BLUR_BUDGET_MS

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
            self._calibrator.cancel()
            self.processor.cancel_exclusion_calibration()
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
        # Calib1 phase A: exposure/gain servo — only when we can actually
        # drive the sensor (live IDS camera, not playback).
        self._servo = None
        self._servo_result = None
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
                                        blur_budget_ms=self.blur_budget_ms)
            if self.ui.available:
                self.ui.set_calibrate_status("Calibrating exposure...")
                self.ui.show_toast("Calibrating - driving exposure/gain, keep the stage clear",
                                   duration=2.5, color=(160, 200, 255))
        else:
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
                self.ui.show_toast("Calibrating scene - keep dancers in frame",
                                   duration=2.5, color=(160, 200, 255))
        print("[Calibrate] started "
              f"({'playback' if self.recorder.is_playing else 'live'}"
              f"{', exposure servo' if self._servo else ''})")

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
        # Phase A: exposure/gain servo (live IDS). Commands are applied through
        # the normal IDS callbacks so sliders/persistence stay in sync.
        if self._servo is not None:
            raw = self.last_raw_frame()
            if raw is not None:
                b = float(raw.mean())
                clip_pct = float(np.count_nonzero(raw >= 250)) / raw.size * 100.0
                cmd = self._servo.feed(b, clip_pct)
                if cmd is not None:
                    kind, value = cmd
                    if kind == "exposure":
                        self.cameras._cb_ids_exposure_change(value)
                        if self.ui.available:
                            self.ui.sync_slider("ids_exposure_us", self.cameras.ids_exposure_us)
                    else:
                        self.cameras._cb_ids_gain_change(value)
                        if self.ui.available:
                            self.ui.sync_slider("ids_gain_db", self.cameras.ids_gain_db)
                if self.ui.available:
                    self.ui.set_calibrate_status(f"Calibrating exposure ({b:.0f})")
            if self._servo.done:
                self._servo_result = self._servo.result()
                print("[Calibrate] " + self._servo_result.summary_line())
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
        """Apply a finished CalibrationResult to the running session + log it."""
        if result.height_ok and result.person_height_px:
            ph = int(result.person_height_px)
            self.settings.person_height_px = ph
            self.settings.person_height_min_ratio = float(result.min_ratio)
            self.settings.person_height_max_ratio = float(result.max_ratio)
            self.tracker.set_person_height(ph)
            if self.ui.available:
                self.ui.sync_slider('person_height', ph)
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
        print(result.log_line())
        self.request_reprocess()
        if self.ui.available:
            self.ui.set_calibrate_status(None)
            servo_line = (self._servo_result.summary_line() + "\n"
                          if self._servo_result else "")
            gamma_line = (f"Gamma seeded: {self.enhancer.gamma:.3f}"
                          + ("  (capped: scene noise high)" if gamma_capped else "")
                          + "\n")
            # "Save to project" must be a normal timestamped save (what startup
            # and the picker load) — safe-defaults stays a separate explicit
            # action (ROADMAP bug #6).
            self.ui.show_calibration_result_dialog(
                servo_line + gamma_line + result.summary(),
                on_save=self.configs._cb_save_config)

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

    def _calib2_aggregate(self, runs, roi_long_side):
        """calib2.aggregate with the live calib-time context: MOG2-input noise
        sigma (dark-target switch), the per-rig engine fps table and the
        current model (P-6 cost curve + report-only model advisory)."""
        noise = None
        mm = getattr(self.processor, "motion_model", None)
        if mm is not None:
            try:
                noise = float(mm.noise_sigma())
            except Exception:
                noise = None
        table = load_fps_table(os.path.join(MODELS_DIR, "fps_table.json"))
        current = getattr(self.models, "current_model_name", "") or ""
        return calib2_aggregate(runs, roi_long_side, noise_sigma=noise,
                                fps_table=table, current_model=current)

    def _show_calib2_dialog(self):
        """Open the evidence-pool dialog with all stored runs + a proposal preview."""
        if not self.ui.available:
            return
        pool = self._calib2_pool()
        entries = pool.load_runs()
        frame_w, frame_h = self.roi_source_size()
        roi = self.get_effective_roi(frame_w, frame_h)
        rows = [
            {
                "path": path,
                "label": run.label(),
                "stale": run.stale_for(roi, (frame_w, frame_h)),
            }
            for path, run in entries
        ]
        proposal = self._calib2_aggregate([r for _p, r in entries],
                                          max(roi[2], roi[3]))
        self.ui.show_calib2_dialog(rows, proposal.summary())

    def _cb_calib2_apply(self, selected_paths):
        """Apply the pooled proposal from the selected runs."""
        pool = self._calib2_pool()
        chosen = [run for path, run in pool.load_runs() if path in set(selected_paths)]
        frame_w, frame_h = self.roi_source_size()
        roi = self.get_effective_roi(frame_w, frame_h)
        prop = self._calib2_aggregate(chosen, max(roi[2], roi[3]))
        if not prop.ok:
            if self.ui.available:
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
        if prop.imgsz and prop.imgsz != int(self.settings.imgsz):
            if self.ui.available:
                self.ui.sync_combo('imgsz', str(prop.imgsz))
            self.imgsz_change(prop.imgsz)   # handles the TRT engine reload
        print("[Calib2] applied: " + prop.summary().replace("\n", " | "))
        self.request_reprocess()
        if self.ui.available:
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
