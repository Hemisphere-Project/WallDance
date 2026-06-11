"""
High-level application orchestration for WallDance.
This module keeps the runtime glue small by delegating to:
- CameraManager (camera lifecycle)
- FrameProcessor (enhance → YOLO → tracking → OSC)
- ConfigStore (save/load presets)
- WallDanceGUI (DearPyGui front-end)
- ModelManager (model loading, TensorRT export)
"""

from __future__ import annotations

import argparse
from collections import deque
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import cv2
import dearpygui.dearpygui as dpg
import numpy as np

# Force unbuffered output so we see logs before crashes
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from camera_manager import CameraManager
from config import (
    BRIGHTNESS_THRESHOLD,
    CAMERA_FPS,
    CAMERA_HEIGHT,
    CAMERA_INDEX,
    CAMERA_WIDTH,
    CLAHE_CLIP_LIMIT,
    DENOISE_STRENGTH,
    ENHANCE_ENABLED,
    GAMMA_CORRECTION,
    MODELS_DIR,
    OSC_ENABLED,
    OSC_IP,
    OSC_PORT,
    PERSON_HEIGHT_MAX_RATIO,
    PERSON_HEIGHT_MIN_RATIO,
    PERSON_HEIGHT_PX,
    AUTOCAL_EXCL_GRID,
    PROJECT_PICKER_ON_START,
    MOTION_BRIDGE_SENSITIVITY,
    PREVIEW_ENABLED,
    PREVIEW_RENDER_SCALE,
    WEB_MONITOR_ENABLED,
    WEB_MONITOR_PORT,
    WEB_MONITOR_HOST,
    WEB_MONITOR_JPEG_QUALITY,
    WEB_MONITOR_MAX_FPS,
    SHOW_BBOX,
    SHOW_ID,
    SHOW_KEYPOINTS,
    SHOW_SKELETON,
    SHOW_TRAILS,
    TRACKER_MAX_AGE,
    IDS_USE_GPU_DIRECT,
    IDS_RATIO,
    USE_TENSORRT,
    YOLO_CONFIDENCE,
    YOLO_IMGSZ,
    YOLO_MODEL,
    TrackingMode,
    OPS_READINESS_ENABLED,
    OPS_OSC_PROBE_TIMEOUT_S,
    OPS_MIN_SHOW_FPS,
    OPS_CALIB_AGE_WARN_H,
    OPS_DISK_WARN_FREE_GB,
    OPS_DISK_FAIL_FREE_GB,
    OPS_HEIGHT_WINDOW_S,
    OPS_HEIGHT_MIN_SAMPLES,
)
from config_store import sanitize_project_name
from osc_output import OSCSender
from pipeline import FrameProcessor, ProcessingSettings, ScaledTrack
from visualization import draw_dancer
from gui import WallDanceGUI, get_display_scale, get_gpu_stats
from ops_monitor import (
    HealthMonitor,
    LoopWatchdog,
    ReadinessReport,
    check_calibration,
    check_camera,
    check_disk,
    check_gpu_temp,
    check_osc,
    check_tensorrt,
)
from gui_builder import SystemState
from enhancer import ImageEnhancer
from tracker import DancerTracker
from tracking_logger import _json_default
from video_recorder import VideoRecorder
from runtime.recording_controller import RecordingController
from runtime.model_controller import ModelController
from runtime.camera_controller import CameraController
from runtime.config_manager import ConfigManager
from runtime.calibration_flows import CalibrationFlows
from web_monitor import WebMonitor
from sensitivity_macro import macro_to_settings


# IDS Camera support (optional, falls back to OpenCV)
try:
    from ids_camera import (
        UnifiedCamera,
        IDS_EXPOSURE_MIN_FPS,
        IDS_EXPOSURE_WARNING_FPS,
        IDS_PEAK_AVAILABLE,
        CameraSource,
        clamp_exposure_for_min_fps,
        max_exposure_for_fps,
    )
    UNIFIED_CAMERA_AVAILABLE = True
except ImportError:
    UNIFIED_CAMERA_AVAILABLE = False
    IDS_PEAK_AVAILABLE = False
    UnifiedCamera = None
    CameraSource = None
    IDS_EXPOSURE_MIN_FPS = 15.0
    IDS_EXPOSURE_WARNING_FPS = 20.0

    def max_exposure_for_fps(min_fps: float) -> float:
        min_fps = float(min_fps)
        if min_fps <= 0:
            return 0.0
        return 1_000_000.0 / min_fps

    def clamp_exposure_for_min_fps(exposure_us: float, min_fps: float = IDS_EXPOSURE_MIN_FPS) -> float:
        exposure_us = float(exposure_us)
        if exposure_us <= 0:
            return 0.0
        return min(exposure_us, max_exposure_for_fps(min_fps))


@dataclass
class PreviewGeometry:
    render_scale: float = PREVIEW_RENDER_SCALE
    width: int = int(CAMERA_WIDTH * PREVIEW_RENDER_SCALE)
    height: int = int(CAMERA_HEIGHT * PREVIEW_RENDER_SCALE)


@dataclass
class ReviewStartupOptions:
    config_path: Optional[str] = None
    project: Optional[str] = None
    slot: Optional[int] = None
    recording_index: int = 0
    playback_speed: float = 1.0
    paused: bool = False
    play_at_frame: Optional[int] = None
    pause_at_frame: Optional[int] = None



class _CalibrationUiAdapter:
    """CalibrationUiPort over the dpg GUI; available is False before the GUI exists."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.gui is not None

    def set_calibrate_status(self, text):
        self._app.gui.set_calibrate_status(text)

    def show_toast(self, message: str, duration: float, color):
        self._app.gui.show_toast(message, duration=duration, color=color)

    def sync_slider(self, name: str, value):
        self._app.gui.sync_slider(name, value)

    def sync_combo(self, name: str, value: str):
        self._app.gui.sync_combo(name, value)

    def show_calibration_result_dialog(self, summary: str, on_save):
        self._app.gui.show_calibration_result_dialog(summary, on_save=on_save)

    def show_calib2_dialog(self, rows, proposal: str):
        self._app.gui.show_calib2_dialog(rows, proposal)


class _ConfigUiAdapter:
    """ConfigUiPort over the dpg GUI; available is False before the GUI exists."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.gui is not None

    def update_project_list(self, projects, current):
        self._app.gui.update_project_list(projects, current)

    def update_config_list(self, configs, current_display):
        self._app.gui.update_config_list(configs, current_display)

    def set_current_config(self, display: str):
        self._app.gui.set_current_config(display)

    def show_save_config_dialog(self, project: str):
        self._app.gui.show_save_config_dialog(project)

    def show_load_config_dialog(self, config_dir: str, project: str):
        self._app.gui.show_load_config_dialog(config_dir, project)

    def show_save_indicator(self, message: str):
        self._app.gui.show_save_indicator(message)

    def show_toast(self, message: str, duration: float, color):
        self._app.gui.show_toast(message, duration=duration, color=color)

    def set_active_profile(self, name: str):
        self._app.gui.set_active_profile(name)

    def show_project_picker(self, rows, last_project: str):
        self._app.gui.show_project_picker(rows, last_project=last_project)

    def sync_combo(self, name: str, value: str):
        self._app.gui.sync_combo(name, value)

    def set_trt_checkbox(self, enabled: bool):
        self._app.gui.set_trt_checkbox(enabled)

    def update_camera_sources(self, sources, current, unavailable):
        self._app.gui.update_camera_sources(sources, current, unavailable)

    def update_camera_status(self, is_open: bool, source: str, reconnecting: bool):
        self._app.gui.update_camera_status(is_open, source, reconnecting=reconnecting)

    def set_camera_type(self, camera_type: str):
        self._app.gui.config['camera_type'] = camera_type


class _CameraUiAdapter:
    """CameraUiPort over the dpg GUI; available is False before the GUI exists."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.gui is not None

    def update_camera_sources(self, sources, current, unavailable):
        self._app.gui.update_camera_sources(sources, current, unavailable)

    def update_camera_status(self, is_open: bool, source: str, reconnecting: bool):
        self._app.gui.update_camera_status(is_open, source, reconnecting=reconnecting)

    def set_camera_type(self, camera_type: str):
        self._app.gui.config['camera_type'] = camera_type

    def set_camera_dimensions(self, width: int, height: int):
        self._app.gui.set_camera_dimensions(width, height)

    def sync_checkbox(self, name: str, value: bool):
        self._app.gui.sync_checkbox(name, value)

    def sync_slider(self, name: str, value: float):
        self._app.gui.sync_slider(name, value)


class _ModelUiAdapter:
    """ModelUiPort over the dpg GUI; available is False before the GUI exists."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.gui is not None

    def show_model_loading_modal(self, message: str):
        self._app.gui.show_model_loading_modal(message)

    def update_model_loading_progress(self, message: str, progress: float,
                                      detail: str, animate: bool = False):
        self._app.gui.update_model_loading_progress(message, progress, detail, animate=animate)

    def hide_model_loading_modal(self):
        self._app.gui.hide_model_loading_modal()

    def update_engine_type_badge(self, is_trt: bool):
        self._app.gui.update_engine_type_badge(is_trt)

    def show_toast(self, message: str, duration: float, color):
        self._app.gui.show_toast(message, duration=duration, color=color)

    def set_trt_checkbox(self, enabled: bool):
        self._app.gui.set_trt_checkbox(enabled)

    def sync_model_combo(self, name: str):
        self._app.gui.sync_combo("model", name)

    def update_model_dropdown(self, name: str):
        self._app.gui.update_model_dropdown(name)

    def update_trt_banner(self, text, exporting: bool = False):
        self._app.gui.update_trt_banner(text, exporting=exporting)

    def show_tensorrt_prompt(self, model_name: str, on_choice):
        self._app.gui.show_tensorrt_prompt(model_name, on_choice)

    def update_gpu_stats(self):
        self._app.gui.update_gpu_stats()

    def render_frame(self):
        dpg.render_dearpygui_frame()


class _ModelCameraAdapter:
    """ModelCameraPort: pause/resume the legacy camera around a model load."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    def snapshot(self):
        state = self._app.camera.state
        return (state.is_open, state.source)

    def close(self):
        self._app.camera.close()
        self._app.camera.state.is_open = False

    def reopen_and_flush(self, source):
        self._app.cameras._open_camera(source)
        # Give camera time to stabilize and flush any stale frames
        time.sleep(0.5)
        if self._app.camera.cap is not None:
            for _ in range(10):
                self._app.camera.cap.grab()


class _RecordingUiAdapter:
    """RecordingUiPort over the dpg GUI; None-safe before the GUI exists."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.gui is not None

    def update_recording_ui(self, **kwargs):
        if self._app.gui:
            self._app.gui.update_recording_ui(**kwargs)

    def set_camera_dimensions(self, width: int, height: int):
        if self._app.gui:
            self._app.gui.set_camera_dimensions(width, height)

    def show_toast(self, message: str, duration: float, color):
        if self._app.gui:
            self._app.gui.show_toast(message, duration=duration, color=color)

    def show_slot_history_menu(self, slot, recordings, on_pick):
        self._app.gui.show_slot_history_menu(slot, recordings, on_pick)


class _RecordingCameraAdapter:
    """RecordingCameraPort over the app's unified/legacy camera pair."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    def set_frame_callback(self, callback):
        self._app.cameras._set_camera_frame_callback(callback)

    def start_acquisition(self):
        if self._app._use_unified_camera and self._app.unified_camera is not None:
            self._app.unified_camera.start_acquisition()

    def stop_acquisition(self):
        if self._app._use_unified_camera and self._app.unified_camera is not None:
            self._app.unified_camera.stop_acquisition()

    def live_dimensions(self):
        cam = self._app.unified_camera
        if cam and cam.is_open:
            return (cam.width, cam.height)
        return None

    def record_dimensions(self):
        state = self._app.camera.state
        return (state.width, state.height)


class _RecordingSessionAdapter:
    """SessionInfoPort over the app's project/config/model state."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def config_dir(self) -> str:
        return self._app.configs.config_store.config_dir

    @property
    def current_project(self) -> str:
        return self._app.configs._current_project

    @property
    def model_name(self) -> str:
        return self._app.models.current_model_name

    @property
    def imgsz(self) -> int:
        return self._app.settings.imgsz

    def saveable_config(self) -> dict:
        return self._app._get_saveable_config()


class WallDanceApp:
    """Main application orchestrator."""

    _IMGSZ_PRESETS = (640, 800, 960, 1280, 1536, 1920)

    def __init__(self, startup_review: Optional[ReviewStartupOptions] = None):
        print("=" * 60)
        print("WallDance 1080p - Multi-Person Pose Detection")
        print("=" * 60)

        # Model loading is deferred until after GUI is created
        # so we can show a progress modal
        # Model load/switch + TRT build orchestration (DECOMPOSITION_PLAN
        # Phase 2 (2)): the controller owns model identity/loading state;
        # processor/watchdog are late-bound (created after this point).
        self.models = ModelController(
            models_dir=MODELS_DIR,
            ui=_ModelUiAdapter(self),
            camera=_ModelCameraAdapter(self),
            processor=lambda: self.processor,
            watchdog=lambda: self._watchdog,
            restore_playback_dims=self._restore_playback_dims,
            update_topbar=lambda: self.configs._update_topbar_state(),
        )

        self.settings = ProcessingSettings(
            confidence=YOLO_CONFIDENCE,
            imgsz=YOLO_IMGSZ,
            use_fp16=True,
            enhance_enabled=ENHANCE_ENABLED,
            enhance_lite=False,
            # Always-on semantics: enhancement applies whenever enabled; the
            # legacy brightness gate is an expert-mode override.
            enhance_force=True,
            person_height_px=PERSON_HEIGHT_PX,
            motion_sensitivity=MOTION_BRIDGE_SENSITIVITY,
            person_height_min_ratio=PERSON_HEIGHT_MIN_RATIO,
            person_height_max_ratio=PERSON_HEIGHT_MAX_RATIO,
            brightness_threshold=BRIGHTNESS_THRESHOLD,
            denoise_strength=0.0,
            osc_enabled=OSC_ENABLED,
        )

        # Camera: Use UnifiedCamera (IDS + OpenCV fallback) if available
        self._use_unified_camera = UNIFIED_CAMERA_AVAILABLE
        if self._use_unified_camera:
            self.unified_camera = UnifiedCamera()
            self.camera = CameraManager()  # Keep for compatibility with camera state
            print(f"[Camera] UnifiedCamera available (IDS Peak: {IDS_PEAK_AVAILABLE})")
        else:
            self.unified_camera = None
            self.camera = CameraManager()
            print("[Camera] Using OpenCV CameraManager")
        # Camera retry/swap + IDS parameter orchestration (DECOMPOSITION_PLAN
        # Phase 2 (3)): owns retry/backoff state, the ids_* parameter cache
        # and the deferred-refresh flag; the camera objects stay app-owned.
        self.cameras = CameraController(
            camera=self.camera,
            unified_camera=self.unified_camera,
            use_unified=self._use_unified_camera,
            ui=_CameraUiAdapter(self),
            preview_geometry=self._camera_preview_geometry,
            repush_preview_size=self._repush_preview_size,
            is_running=lambda: self.running,
        )
        
        self.osc: Optional[OSCSender] = None
        self.osc_ip = OSC_IP
        self.osc_port = OSC_PORT
        self.osc_enabled = OSC_ENABLED

        self.enhancer = ImageEnhancer()
        self.tracker = DancerTracker()
        self.tracker.logger.camera_id = CAMERA_INDEX
        self.tracker.set_person_height(PERSON_HEIGHT_PX)
        self.processor = FrameProcessor(
            model=self.models.model,
            settings=self.settings,
            enhancer=self.enhancer,
            tracker=self.tracker,
            osc_sender=self.osc,
        )
        # Sync initial GPU pipeline settings
        preview_w = int(CAMERA_WIDTH * PREVIEW_RENDER_SCALE)
        preview_h = int(CAMERA_HEIGHT * PREVIEW_RENDER_SCALE)
        self.processor.set_preview_size(preview_w, preview_h)
        
        if self.osc_enabled:
            self._init_osc()

        self.gui: Optional[WallDanceGUI] = None

        # Video recording
        self.recorder = VideoRecorder()

        # Preview/display state
        self.preview_enabled = PREVIEW_ENABLED
        self.preview_fps_cap = False
        self.input_fps_cap = False
        self._input_fps_cap_interval = 1.0 / 20.0  # 20 FPS = 50ms
        self._last_input_frame_time = 0.0
        self.preview_stride = 1
        self.preview = PreviewGeometry(
            render_scale=PREVIEW_RENDER_SCALE,
            width=int(CAMERA_WIDTH * PREVIEW_RENDER_SCALE),
            height=int(CAMERA_HEIGHT * PREVIEW_RENDER_SCALE),
        )
        self._pending_preview_resize = False
        self._last_preview_upload_time = 0.0

        # ROI state (stored in full-frame source coordinates)
        self.roi_edit_mode = False
        self._roi_drag_active = False
        self._roi_drag_mode: Optional[str] = None
        self._roi_drag_origin: Optional[tuple[int, int]] = None
        self._roi_drag_start_rect: Optional[tuple[int, int, int, int]] = None
        self._roi_mouse_was_down = False
        self._roi_source_size = (CAMERA_WIDTH, CAMERA_HEIGHT)

        # Exclusion-mask manual editor (ROADMAP §4.2 Phase 2 ④)
        self.mask_edit_mode = False
        self._mask_paint_active = False
        self._mask_paint_value: Optional[bool] = None
        self._mask_painted_cells: set = set()
        self._mask_mouse_was_down = False
        self.settings.roi_x = 0
        self.settings.roi_y = 0
        self.settings.roi_w = CAMERA_WIDTH
        self.settings.roi_h = CAMERA_HEIGHT

        # Visualization flags
        self.show_trails = SHOW_TRAILS
        self.show_skeleton = SHOW_SKELETON
        self.show_keypoints = SHOW_KEYPOINTS
        self.show_bbox = SHOW_BBOX
        self.show_ids = SHOW_ID

        # State for metrics
        self.frame_count = 0
        self.last_fps_time = time.time()
        self._last_gpu_stats_time = self.last_fps_time
        self.fps = 0.0
        self.timing: Dict[str, float] = {}
        self._last_spike_log_time = 0.0
        self._last_diag_log_time = 0.0
        self._last_fresh_preview_time = time.time()
        self._last_fresh_frame_time = time.time()
        self._last_preview_stalled_state = False
        self.latency_ms = 0.0
        self.running = False
        self._web_monitor: Optional[WebMonitor] = None  # smartphone focus/lighting monitor
        # Sensitivity macro (UX_PLAN U5): one operator dial; 50 = calibrated seed.
        self.sensitivity: float = 50.0
        self._sensitivity_conf_seed: float = YOLO_CONFIDENCE
        self._sensitivity_var_anchor: float = self.processor.get_motion_var_threshold()
        self.last_tracked: List[ScaledTrack] = []
        self._total_frame_count: int = 0  # Phase 0: cumulative frame counter (live mode)
        self._last_raw_frame: Optional[np.ndarray] = None  # Last raw camera frame for BG capture
        self._last_review_frame: Optional[np.ndarray] = None
        self._startup_review = startup_review or ReviewStartupOptions()
        # Recording/playback orchestration (DECOMPOSITION_PLAN Phase 2 (1)):
        # the controller owns slot/record/playback state and wires
        # recorder.on_playback_start itself.
        self.recording = RecordingController(
            recorder=self.recorder,
            tracker_logger=self.tracker.logger,
            camera=_RecordingCameraAdapter(self),
            ui=_RecordingUiAdapter(self),
            session=_RecordingSessionAdapter(self),
            on_playback_restart=self._on_playback_restart,
            startup_review=self._startup_review,
        )
        # Project/profile/config persistence flows (DECOMPOSITION_PLAN
        # Phase 2 (4)): owns the config store, current project, lighting
        # profile bundles and the deferred project-switch request; the
        # whole-app config appliers stay on the app, injected as callables.
        self.configs = ConfigManager(
            models=self.models,
            cameras=self.cameras,
            recording=self.recording,
            recorder=self.recorder,
            camera=self.camera,
            unified_camera=self.unified_camera,
            use_unified=self._use_unified_camera,
            settings=self.settings,
            ui=_ConfigUiAdapter(self),
            watchdog=lambda: self._watchdog,
            apply_config=self._apply_config_without_model,
            saveable_config=self._get_saveable_config,
            update_imgsz_roi_warning=self._update_imgsz_roi_warning,
            request_reprocess=self._request_reprocess,
        )
        # CALIBRATE / DANCERS orchestration (DECOMPOSITION_PLAN Phase 2
        # (5)): owns the calibration state machines stepped by the main
        # loop; the math stays in core/calibration.py + core/calib2.py.
        self.calibration = CalibrationFlows(
            processor=self.processor,
            enhancer=self.enhancer,
            tracker=self.tracker,
            settings=self.settings,
            recorder=self.recorder,
            camera=self.camera,
            unified_camera=self.unified_camera,
            use_unified=self._use_unified_camera,
            models=self.models,
            cameras=self.cameras,
            configs=self.configs,
            ui=_CalibrationUiAdapter(self),
            last_raw_frame=lambda: self._last_raw_frame,
            roi_source_size=lambda: self._roi_source_size,
            get_effective_roi=self._get_effective_roi,
            reset_sensitivity_anchor=self._reset_sensitivity_anchor,
            sync_mask_ui=self._sync_mask_ui,
            request_reprocess=self._request_reprocess,
            imgsz_change=self._cb_imgsz_change,
        )
        
        # Pending operations (deferred to main loop)

        # Ops cluster (TODO Phase 7): health alerts + main-loop watchdog
        self._health = HealthMonitor(gpu_stats_fn=get_gpu_stats)
        self._watchdog = LoopWatchdog()
        self._last_ops_tick = 0.0
        # Rolling (t, raw det height px) samples for the staleness alarm (⑤d)
        self._height_samples: deque = deque()

    # ------------------------------------------------------------------
    # OSC
    # ------------------------------------------------------------------
    def _init_osc(self):
        try:
            self.osc = OSCSender(self.osc_ip, self.osc_port)
            self.processor.attach_osc(self.osc)
        except Exception as exc:
            print(f"OSC init failed: {exc}")
            self.osc = None
            self.processor.attach_osc(None)

    # ------------------------------------------------------------------
    # GUI integration helpers
    # ------------------------------------------------------------------
    def _get_gui_config(self) -> Dict:
        state = self.camera.state
        all_sources = list(set(state.available + state.unavailable))
        if state.source and state.source not in all_sources:
            all_sources.append(state.source)
        all_sources.sort(key=lambda x: (x not in state.available, x != "ids", x))
        
        # Get DPI scale for video display sizing
        dpi_scale = get_display_scale()
        cam_w = state.width if state.width > 0 else CAMERA_WIDTH
        cam_h = state.height if state.height > 0 else CAMERA_HEIGHT
        # Initial estimates – will be recomputed by gui._recompute_layout()
        video_w = int(cam_w * 0.5 * dpi_scale)
        video_h = int(cam_h * 0.5 * dpi_scale)
        
        return {
            "video_width": video_w,
            "video_height": video_h,
            "camera_width": cam_w,
            "camera_height": cam_h,
            "camera_source": state.source,
            "camera_sources": all_sources if all_sources else ["0"],
            "model": self.models.current_model_name,
            "use_tensorrt": self.models.model_manager.is_using_tensorrt(),
            "confidence": self.settings.confidence,
            "fp16": self.settings.use_fp16,
            "yolo_imgsz": self.settings.imgsz,
            "person_height_px": self.settings.person_height_px,
            "enhance_enabled": self.settings.enhance_enabled,
            "enhance_lite": self.settings.enhance_lite,
            "enhance_force": self.settings.enhance_force,
            "greyscale": self.settings.greyscale,
            "clahe_clip": CLAHE_CLIP_LIMIT,
            "gamma": GAMMA_CORRECTION,
            "show_skeleton": self.show_skeleton,
            "show_keypoints": self.show_keypoints,
            "show_bbox": self.show_bbox,
            "show_trails": self.show_trails,
            "show_ids": self.show_ids,
            "tracker_max_age": TRACKER_MAX_AGE,
            "tracker_smoothing": 1,
            "tracking_mode": self.tracker.tracking_mode.value,
            "motion_sensitivity": self.settings.motion_sensitivity,
            "osc_enabled": self.osc_enabled,
            "osc_ip": self.osc_ip,
            "osc_port": self.osc_port,
            "preview_enabled": self.preview_enabled,
            "preview_fps_cap": self.preview_fps_cap,
            "input_fps_cap": self.input_fps_cap,
            "preview_scale": self.preview.render_scale,
            "roi_enabled": self.settings.roi_enabled,
            "roi_edit_mode": self.roi_edit_mode,
            "roi_x": self.settings.roi_x,
            "roi_y": self.settings.roi_y,
            "roi_w": self.settings.roi_w,
            "roi_h": self.settings.roi_h,
            "ids_ratio": self.cameras.ids_ratio,
            "ids_gain_db": self.cameras.ids_gain_db,
            "ids_exposure_us": self.cameras.ids_exposure_us,
            "ids_exposure_max_us": max_exposure_for_fps(IDS_EXPOSURE_MIN_FPS),
            "ids_exposure_min_fps": IDS_EXPOSURE_MIN_FPS,
            "ids_exposure_warning_fps": IDS_EXPOSURE_WARNING_FPS,
            "texture_width": self.preview.width,
            "texture_height": self.preview.height,
            "camera_running": self.camera.state.is_open,
            "camera_reconnecting": self.cameras._camera_reconnecting,
            "active_profile": self.configs._active_profile,
            "sensitivity": self.sensitivity,
        }

    def _show_qr(self):
        """Show a QR code so a phone can open the web monitor URL."""
        mon = self._web_monitor
        if mon is None or not mon.running:
            if self.gui:
                self.gui.show_toast(
                    "Web monitor is off (set WEB_MONITOR_ENABLED=True)",
                    color=(255, 180, 80))
            return
        if self.gui:
            self.gui.show_qr_dialog(mon.url(), mon.qr_matrix())

    def _get_gui_callbacks(self) -> Dict:
        return {
            "show_qr": self._show_qr,
            "on_system_state_change": self._cb_system_state_change,
            "on_enhance_toggle": self._cb_enhance_toggle,
            "on_enhance_lite_toggle": self._cb_enhance_lite_toggle,
            "on_enhance_force_toggle": self._cb_enhance_force_toggle,
            "on_greyscale_toggle": self._cb_greyscale_toggle,
            "on_brightness_threshold_change": self._cb_brightness_threshold_change,
            "on_clahe_change": self._cb_clahe_change,
            "on_gamma_change": self._cb_gamma_change,
            "on_denoise_change": self._cb_denoise_change,
            "on_bg_capture": self._cb_bg_capture,
            "on_bg_enable_toggle": self._cb_bg_enable_toggle,
            "on_bg_clear": self._cb_bg_clear,
            "on_bg_sensitivity_change": self._cb_bg_sensitivity_change,
            "on_confidence_change": self._cb_confidence_change,
            "on_sensitivity_change": self._cb_sensitivity_change,
            "on_motion_sensitivity_change": self._cb_motion_sensitivity_change,
            "on_model_change": self.models._cb_model_change,
            "on_trt_toggle": self.models._cb_trt_toggle,
            "on_trt_rebuild": self.models._cb_trt_rebuild,
            "on_ids_ratio_change": self.cameras._cb_ids_ratio_change,
            "on_ids_gain_change": self.cameras._cb_ids_gain_change,
            "on_ids_exposure_change": self.cameras._cb_ids_exposure_change,
            "on_camera_change": self.cameras._cb_camera_change,
            "on_camera_refresh": self.cameras._cb_camera_refresh,
            "on_imgsz_change": self._cb_imgsz_change,
            "on_person_height_change": self._cb_person_height_change,
            "on_calibrate": self.calibration._cb_calibrate,
            "on_calib2": self.calibration._cb_calib2,
            "on_calib2_apply": self.calibration._cb_calib2_apply,
            "on_calib2_clear": self.calibration._cb_calib2_clear,
            "on_visualization_toggle": self._cb_visualization_toggle,
            "on_tracker_age_change": self._cb_tracker_age_change,
            "on_mog2_scale_change": self._cb_mog2_scale_change,
            "on_tracker_reset": self._cb_tracker_reset,
            "on_osc_toggle": self._cb_osc_toggle,
            "on_osc_config": self._cb_osc_config,
            "on_preview_toggle": self._cb_preview_toggle,
            "on_input_fps_cap_toggle": self._cb_input_fps_cap_toggle,
            "on_preview_cap_toggle": self._cb_preview_cap_toggle,
            "on_preview_scale_change": self._cb_preview_scale_change,
            "on_roi_toggle": self._cb_roi_toggle,
            "on_roi_reset": self._cb_roi_reset,
            "on_mask_edit_toggle": self._cb_mask_edit_toggle,
            "on_mask_clear": self._cb_mask_clear,
            "on_save_config": self.configs._cb_save_config,
            "on_save_as_config": self.configs._cb_save_as_config,
            "on_save_safe_defaults": self.configs._cb_save_safe_defaults,
            "on_load_safe_defaults": self.configs._cb_load_safe_defaults,
            "on_load_config": self.configs._cb_load_config,
            "on_do_save_config": self.configs._cb_do_save_config,
            "on_do_load_config": self.configs._cb_do_load_config,
            "on_profile_switch": self.configs._cb_profile_switch,
            "on_project_select": self.configs._cb_project_select,
            "on_config_select": self.configs._cb_config_select,
            "on_project_launch": self.configs._cb_project_launch,
            "on_project_rename": self.configs._cb_project_rename,
            "on_project_delete": self.configs._cb_project_delete,
            "on_project_blank": self.configs._cb_project_blank,
            "on_rec_live": self.recording._cb_rec_live,
            "on_rec_toggle": self.recording._cb_rec_toggle,
            "on_rec_slot_click": self.recording._cb_rec_slot_click,
            "on_playback_speed_change": self.recording._cb_playback_speed_change,
            "on_playback_pause": self.recording._cb_playback_pause,
            "on_playback_force_pause": self.recording._cb_playback_force_pause,
            "on_playback_next_frame": self.recording._cb_playback_next_frame,
            "on_playback_prev_frame": self.recording._cb_playback_prev_frame,
            "on_report_issue_request": self._cb_report_issue_request,
            "on_issue_submit": self._cb_issue_submit,
            "on_issue_dialog_closed": self._cb_issue_dialog_closed,
            "on_quit": self._cb_quit,
        }

    def _normalize_roi_rect(self, x: int, y: int, w: int, h: int, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        frame_w = max(1, int(frame_w))
        frame_h = max(1, int(frame_h))
        x = max(0, min(int(x), frame_w - 1))
        y = max(0, min(int(y), frame_h - 1))
        w = max(1, int(w))
        h = max(1, int(h))
        w = min(w, frame_w - x)
        h = min(h, frame_h - y)
        return x, y, w, h

    def _sync_roi_ui(self):
        if not self.gui:
            return
        self.gui.sync_checkbox("roi_enable", self.settings.roi_enabled)
        self.gui.update_roi_rect_text(
            self.settings.roi_x,
            self.settings.roi_y,
            self.settings.roi_w,
            self.settings.roi_h,
            edit_mode=self.roi_edit_mode,
        )
        self._update_imgsz_roi_warning()

    def _get_recommended_imgsz_for_roi(self) -> tuple[int, int, int, int] | None:
        if not self.settings.roi_enabled:
            return None

        frame_w, frame_h = self._roi_source_size
        _, _, roi_w, roi_h = self._get_effective_roi(frame_w, frame_h)
        long_edge = max(roi_w, roi_h)
        min_target = long_edge * 1.5
        max_target = long_edge * 2.0

        low = self._IMGSZ_PRESETS[-1]
        for preset in self._IMGSZ_PRESETS:
            if preset >= min_target:
                low = preset
                break

        in_range = [preset for preset in self._IMGSZ_PRESETS if min_target <= preset <= max_target]
        high = in_range[-1] if in_range else low
        return low, high, roi_w, roi_h

    def _get_imgsz_roi_warning(self) -> Optional[str]:
        roi_info = self._get_recommended_imgsz_for_roi()
        if roi_info is None:
            return None

        low, high, roi_w, roi_h = roi_info
        current = int(self.settings.imgsz)
        if current >= low:
            return None

        if low == high:
            target = f"{low}px"
        else:
            target = f"{low}-{high}px"

        return f"ROI {roi_w}x{roi_h}: consider {target} imgsz for better detection."

    def _update_imgsz_roi_warning(self):
        if not self.gui:
            return
        self.gui.update_imgsz_roi_warning(self._get_imgsz_roi_warning())

    def _set_roi_rect(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        *,
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
        sync_ui: bool = True,
        request_reprocess: bool = True,
    ):
        if frame_w is None or frame_h is None:
            frame_w, frame_h = self._roi_source_size
        x, y, w, h = self._normalize_roi_rect(x, y, w, h, frame_w, frame_h)
        self.settings.roi_x = x
        self.settings.roi_y = y
        self.settings.roi_w = w
        self.settings.roi_h = h
        self._roi_source_size = (frame_w, frame_h)
        if sync_ui:
            self._sync_roi_ui()
        if request_reprocess:
            self._request_reprocess()

    def _clamp_roi_to_source(self, frame_w: int, frame_h: int, *, sync_ui: bool = True):
        self._set_roi_rect(
            self.settings.roi_x,
            self.settings.roi_y,
            self.settings.roi_w or frame_w,
            self.settings.roi_h or frame_h,
            frame_w=frame_w,
            frame_h=frame_h,
            sync_ui=sync_ui,
            request_reprocess=False,
        )

    def _get_effective_roi(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        x, y, w, h = self._normalize_roi_rect(
            self.settings.roi_x,
            self.settings.roi_y,
            self.settings.roi_w or frame_w,
            self.settings.roi_h or frame_h,
            frame_w,
            frame_h,
        )
        return x, y, w, h

    def _get_preview_item_rect(self) -> Optional[tuple[int, int, int, int]]:
        if not dpg.does_item_exist("video_image"):
            return None
        try:
            state = dpg.get_item_state("video_image")
        except Exception:
            state = None

        rect_min = None
        rect_size = None
        if isinstance(state, dict):
            rect_min = state.get("rect_min")
            rect_size = state.get("rect_size")

        if rect_min is None or rect_size is None:
            try:
                rect_min = dpg.get_item_rect_min("video_image")
                rect_size = dpg.get_item_rect_size("video_image")
            except Exception:
                return None

        if len(rect_min) < 2 or len(rect_size) < 2:
            return None

        img_x, img_y = int(rect_min[0]), int(rect_min[1])
        img_w, img_h = int(rect_size[0]), int(rect_size[1])
        if img_w <= 0 or img_h <= 0:
            return None
        return img_x, img_y, img_w, img_h

    def _get_preview_mouse_point(self) -> Optional[tuple[int, int, int, int]]:
        if not self.gui:
            return None
        rect = self._get_preview_item_rect()
        if rect is None:
            return None
        img_x, img_y, img_w, img_h = rect
        try:
            mouse_x, mouse_y = dpg.get_mouse_pos(local=False)
        except TypeError:
            mouse_x, mouse_y = dpg.get_mouse_pos()
        if mouse_x < img_x or mouse_y < img_y or mouse_x >= img_x + img_w or mouse_y >= img_y + img_h:
            return None
        frame_w, frame_h = self._roi_source_size
        frame_x = int((mouse_x - img_x) * frame_w / img_w)
        frame_y = int((mouse_y - img_y) * frame_h / img_h)
        frame_x = max(0, min(frame_w - 1, frame_x))
        frame_y = max(0, min(frame_h - 1, frame_y))
        return frame_x, frame_y, frame_w, frame_h

    def _classify_roi_drag_mode(self, frame_x: int, frame_y: int, frame_w: int, frame_h: int) -> str:
        roi_x, roi_y, roi_w, roi_h = self._get_effective_roi(frame_w, frame_h)
        roi_x2 = roi_x + roi_w
        roi_y2 = roi_y + roi_h
        edge_margin = max(6, int(min(frame_w, frame_h) * 0.01))

        near_left = abs(frame_x - roi_x) <= edge_margin
        near_right = abs(frame_x - roi_x2) <= edge_margin
        near_top = abs(frame_y - roi_y) <= edge_margin
        near_bottom = abs(frame_y - roi_y2) <= edge_margin
        inside = roi_x <= frame_x <= roi_x2 and roi_y <= frame_y <= roi_y2

        if near_left and near_top:
            return "resize_tl"
        if near_right and near_top:
            return "resize_tr"
        if near_left and near_bottom:
            return "resize_bl"
        if near_right and near_bottom:
            return "resize_br"
        if near_left and inside:
            return "resize_l"
        if near_right and inside:
            return "resize_r"
        if near_top and inside:
            return "resize_t"
        if near_bottom and inside:
            return "resize_b"
        if inside:
            return "move"
        return "new"

    def _apply_roi_drag(self, frame_x: int, frame_y: int, frame_w: int, frame_h: int):
        if self._roi_drag_origin is None or self._roi_drag_start_rect is None or self._roi_drag_mode is None:
            return

        start_x, start_y = self._roi_drag_origin
        roi_x, roi_y, roi_w, roi_h = self._roi_drag_start_rect
        roi_x2 = roi_x + roi_w
        roi_y2 = roi_y + roi_h
        dx = frame_x - start_x
        dy = frame_y - start_y
        min_size = 8

        if self._roi_drag_mode == "new":
            left = min(start_x, frame_x)
            top = min(start_y, frame_y)
            right = max(start_x, frame_x)
            bottom = max(start_y, frame_y)
        elif self._roi_drag_mode == "move":
            left = roi_x + dx
            top = roi_y + dy
            left = max(0, min(left, frame_w - roi_w))
            top = max(0, min(top, frame_h - roi_h))
            right = left + roi_w
            bottom = top + roi_h
        else:
            left = roi_x
            top = roi_y
            right = roi_x2
            bottom = roi_y2
            resize_mode = self._roi_drag_mode.replace("resize_", "")
            if "l" in resize_mode:
                left = min(frame_x, right - min_size)
            if "r" in resize_mode:
                right = max(frame_x, left + min_size)
            if "t" in resize_mode:
                top = min(frame_y, bottom - min_size)
            if "b" in resize_mode:
                bottom = max(frame_y, top + min_size)

        left = max(0, min(left, frame_w - 1))
        top = max(0, min(top, frame_h - 1))
        right = max(left + 1, min(right, frame_w))
        bottom = max(top + 1, min(bottom, frame_h))

        self._set_roi_rect(
            left,
            top,
            right - left,
            bottom - top,
            frame_w=frame_w,
            frame_h=frame_h,
            sync_ui=True,
            request_reprocess=False,
        )

    def _update_roi_drag_from_mouse(self):
        if not self._roi_drag_active:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        frame_x, frame_y, frame_w, frame_h = point
        self._apply_roi_drag(frame_x, frame_y, frame_w, frame_h)

    def _poll_roi_mouse_interaction(self):
        if not self.roi_edit_mode or not self.settings.roi_enabled:
            self._roi_mouse_was_down = False
            return

        try:
            is_down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        except Exception:
            return

        if is_down and not self._roi_mouse_was_down:
            self._handle_roi_mouse_down(app_data=dpg.mvMouseButton_Left)
        elif is_down and self._roi_mouse_was_down:
            self._update_roi_drag_from_mouse()
        elif (not is_down) and self._roi_mouse_was_down:
            self._handle_roi_mouse_up(app_data=dpg.mvMouseButton_Left)

        self._roi_mouse_was_down = is_down

    def _draw_roi_mask(self, frame: np.ndarray, source_w: int, source_h: int):
        if not self.settings.roi_enabled:
            return
        frame_h, frame_w = frame.shape[:2]
        x, y, w, h = self._get_effective_roi(source_w, source_h)
        x = int(round(x * frame_w / max(source_w, 1)))
        y = int(round(y * frame_h / max(source_h, 1)))
        w = max(1, int(round(w * frame_w / max(source_w, 1))))
        h = max(1, int(round(h * frame_h / max(source_h, 1))))
        border_color = (80, 220, 120) if self.roi_edit_mode else (100, 180, 240)
        cv2.rectangle(frame, (x, y), (x + w, y + h), border_color, 2)
        if self.roi_edit_mode:
            handle = max(4, min(10, int(min(frame_w, frame_h) * 0.01)))
            for hx, hy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
                cv2.rectangle(frame, (hx - handle, hy - handle), (hx + handle, hy + handle), border_color, -1)

    def _compose_roi_preview(self, preview_frame: Optional[np.ndarray], source_w: int, source_h: int) -> Optional[np.ndarray]:
        if preview_frame is None or source_w <= 0 or source_h <= 0:
            return None

        x, y, w, h = self._get_effective_roi(source_w, source_h)
        roi_frame = preview_frame
        if preview_frame.shape[1] == source_w and preview_frame.shape[0] == source_h:
            roi_frame = preview_frame[y:y + h, x:x + w]

        if roi_frame.size == 0:
            return None

        if roi_frame.shape[1] != w or roi_frame.shape[0] != h:
            roi_frame = cv2.resize(roi_frame, (w, h))

        canvas = np.zeros((source_h, source_w, 3), dtype=roi_frame.dtype)
        canvas[y:y + h, x:x + w] = roi_frame
        return canvas

    def _draw_roi_note(self, frame: np.ndarray, source_w: int, source_h: int):
        if not self.settings.roi_enabled:
            return

        _, _, roi_w_src, roi_h_src = self._get_effective_roi(source_w, source_h)
        note_lines = [f"ROI {roi_w_src}x{roi_h_src} | imgsz {self.settings.imgsz}"]
        roi_info = self._get_recommended_imgsz_for_roi()
        if roi_info is not None:
            low, high, _, _ = roi_info
            if low == high:
                note_lines.append(f"Suggested: {low}")
            else:
                note_lines.append(f"Suggested: {low}-{high}")

        frame_h, frame_w = frame.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = max(0.45, min(frame_w, frame_h) / 1400.0)
        thickness = 1
        line_height = max(18, int(24 * font_scale))
        box_width = 0
        for line in note_lines:
            (text_width, _), _ = cv2.getTextSize(line, font, font_scale, thickness)
            box_width = max(box_width, text_width)
        box_height = 12 + line_height * len(note_lines)
        cv2.rectangle(frame, (8, 8), (20 + box_width, 8 + box_height), (0, 0, 0), -1)
        text_y = 8 + line_height
        for idx, line in enumerate(note_lines):
            color = (0, 140, 255) if idx > 0 else (220, 220, 220)
            cv2.putText(frame, line, (12, text_y), font, font_scale, color, thickness, cv2.LINE_AA)
            text_y += line_height

    def _handle_roi_mouse_down(self, sender=None, app_data=None):
        if not self.roi_edit_mode or not self.settings.roi_enabled:
            return
        if app_data != dpg.mvMouseButton_Left:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        frame_x, frame_y, frame_w, frame_h = point
        self._roi_drag_active = True
        self._roi_drag_origin = (frame_x, frame_y)
        self._roi_drag_start_rect = self._get_effective_roi(frame_w, frame_h)
        self._roi_drag_mode = self._classify_roi_drag_mode(frame_x, frame_y, frame_w, frame_h)
        self._clamp_roi_to_source(frame_w, frame_h, sync_ui=False)

    def _handle_roi_mouse_move(self, sender=None, app_data=None):
        if not self._roi_drag_active:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        frame_x, frame_y, frame_w, frame_h = point
        self._apply_roi_drag(frame_x, frame_y, frame_w, frame_h)

    def _handle_roi_mouse_up(self, sender=None, app_data=None):
        if app_data != dpg.mvMouseButton_Left:
            return
        if self._roi_drag_active:
            self._roi_drag_active = False
            self._roi_drag_mode = None
            self._roi_drag_origin = None
            self._roi_drag_start_rect = None
            self._request_reprocess()

    # ------------------------------------------------------------------
    # Exclusion-mask manual editor (ROADMAP §4.2 Phase 2 ④)
    # ------------------------------------------------------------------
    def _mask_space_rect(self, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
        """The source-frame rect the exclusion grid is normalized over.

        The mask lives in the motion model's input space: the ROI crop when
        ROI is enabled, else the full frame (mirrors the pipeline's
        ``_exclusion_norm_xy`` ROI-local normalization).
        """
        if self.settings.roi_enabled:
            return self._get_effective_roi(frame_w, frame_h)
        return 0, 0, frame_w, frame_h

    def _mask_norm_point(self, frame_x: int, frame_y: int,
                         frame_w: int, frame_h: int) -> Optional[tuple[float, float]]:
        """Map a source-frame point into the mask's normalized [0,1) space."""
        rx, ry, rw, rh = self._mask_space_rect(frame_w, frame_h)
        if rw <= 0 or rh <= 0:
            return None
        nx = (frame_x - rx) / rw
        ny = (frame_y - ry) / rh
        if not (0.0 <= nx < 1.0 and 0.0 <= ny < 1.0):
            return None
        return nx, ny

    def _handle_mask_mouse_down(self, sender=None, app_data=None):
        if not self.mask_edit_mode or self._mask_paint_active:
            return
        if app_data != dpg.mvMouseButton_Left:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        nxy = self._mask_norm_point(*point)
        if nxy is None:
            return
        # The pressed cell's flip decides the paint value for the whole drag
        # (classic paint semantics: press on a clear cell → masking drag).
        result = self.processor.toggle_exclusion_cell(*nxy)
        if result is None:
            return
        col, row, state = result
        self._mask_paint_active = True
        self._mask_paint_value = state
        self._mask_painted_cells = {(col, row)}
        self._sync_mask_ui()

    def _handle_mask_mouse_move(self, sender=None, app_data=None):
        if not self._mask_paint_active:
            return
        point = self._get_preview_mouse_point()
        if point is None:
            return
        nxy = self._mask_norm_point(*point)
        if nxy is None:
            return
        cell = self.processor.paint_exclusion_cell(*nxy, self._mask_paint_value)
        if cell is not None and cell not in self._mask_painted_cells:
            self._mask_painted_cells.add(cell)
            self._sync_mask_ui()

    def _handle_mask_mouse_up(self, sender=None, app_data=None):
        if app_data != dpg.mvMouseButton_Left:
            return
        if self._mask_paint_active:
            self._mask_paint_active = False
            verb = "masked" if self._mask_paint_value else "unmasked"
            print(f"[Mask] {verb} {len(self._mask_painted_cells)} cell(s) "
                  f"manually")
            self._mask_paint_value = None
            self._mask_painted_cells = set()
            self._request_reprocess()

    def _poll_mask_mouse_interaction(self):
        """Mirror of _poll_roi_mouse_interaction for the mask editor."""
        if not self.mask_edit_mode:
            self._mask_mouse_was_down = False
            return
        try:
            is_down = dpg.is_mouse_button_down(dpg.mvMouseButton_Left)
        except Exception:
            return
        if is_down and not self._mask_mouse_was_down:
            self._handle_mask_mouse_down(app_data=dpg.mvMouseButton_Left)
        elif is_down and self._mask_mouse_was_down:
            self._handle_mask_mouse_move(app_data=dpg.mvMouseButton_Left)
        elif (not is_down) and self._mask_mouse_was_down:
            self._handle_mask_mouse_up(app_data=dpg.mvMouseButton_Left)
        self._mask_mouse_was_down = is_down

    def _draw_exclusion_overlay(self, frame: np.ndarray, source_w: int, source_h: int):
        """Grid + cell overlay on the preview while the mask editor is active."""
        if not self.mask_edit_mode:
            return
        grid, auto, manual_add, manual_remove = self.processor.get_exclusion_state()
        gx, gy = grid
        if gx <= 0 or gy <= 0:
            return
        frame_h, frame_w = frame.shape[:2]
        rx, ry, rw, rh = self._mask_space_rect(source_w, source_h)
        # Scale the mask-space rect into preview-frame coordinates.
        sx = frame_w / max(source_w, 1)
        sy = frame_h / max(source_h, 1)
        rx, ry = rx * sx, ry * sy
        rw, rh = rw * sx, rh * sy

        def cell_rect(col: int, row: int) -> tuple[int, int, int, int]:
            x0 = int(round(rx + col / gx * rw))
            y0 = int(round(ry + row / gy * rh))
            x1 = int(round(rx + (col + 1) / gx * rw))
            y1 = int(round(ry + (row + 1) / gy * rh))
            return x0, y0, x1, y1

        effective = (set(map(tuple, auto)) | set(map(tuple, manual_add))) \
            - set(map(tuple, manual_remove))
        overlay = frame.copy()
        for col, row in effective:
            x0, y0, x1, y1 = cell_rect(col, row)
            color = (60, 60, 230) if (col, row) in set(map(tuple, manual_add)) \
                else (40, 40, 180)
            cv2.rectangle(overlay, (x0, y0), (x1, y1), color, -1)
        cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, dst=frame)
        # Manually unmasked auto cells: outline only (auto wanted them, the
        # operator vetoed) so the veto stays visible and re-clickable.
        for col, row in set(map(tuple, manual_remove)) & set(map(tuple, auto)):
            x0, y0, x1, y1 = cell_rect(col, row)
            cv2.rectangle(frame, (x0, y0), (x1, y1), (140, 140, 140), 1)
        # Grid lines (thin) + status note.
        grid_color = (90, 90, 90)
        for col in range(gx + 1):
            x = int(round(rx + col / gx * rw))
            cv2.line(frame, (x, int(ry)), (x, int(ry + rh)), grid_color, 1)
        for row in range(gy + 1):
            y = int(round(ry + row / gy * rh))
            cv2.line(frame, (int(rx), y), (int(rx + rw), y), grid_color, 1)
        cv2.putText(frame, "MASK EDIT: click/drag cells to mask (red) / unmask",
                    (int(rx) + 8, int(ry) + 22), cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.45, min(frame_w, frame_h) / 1400.0),
                    (80, 220, 120), 1, cv2.LINE_AA)

    def _cb_mask_edit_toggle(self):
        """GUI button: toggle the exclusion-mask manual editor."""
        self.mask_edit_mode = not self.mask_edit_mode
        if self.mask_edit_mode and self.roi_edit_mode:
            self._cb_roi_edit_toggle(False)  # one paint mode at a time
        self._mask_paint_active = False
        self._mask_paint_value = None
        self._mask_painted_cells = set()
        if self.gui:
            self.gui.set_mask_edit_state(self.mask_edit_mode)
            message = ("Mask edit: click/drag preview cells"
                       if self.mask_edit_mode else "Mask edit: off")
            self.gui.show_toast(message, duration=2.5, color=(160, 200, 255))
        self._sync_mask_ui()

    def _cb_mask_clear(self):
        """GUI button: drop the whole mask (auto cells + manual overlays)."""
        self.processor.clear_exclusion()
        self._sync_mask_ui()
        self._request_reprocess()
        if self.gui:
            self.gui.show_toast("Exclusion mask cleared (auto + manual)",
                                duration=2.5, color=(255, 180, 80))
        print("[Mask] cleared (auto + manual)")

    def _sync_mask_ui(self):
        """Push the current mask cell counts to the GUI label."""
        if not self.gui:
            return
        _grid, auto, manual_add, manual_remove = self.processor.get_exclusion_state()
        effective = (set(map(tuple, auto)) | set(map(tuple, manual_add))) \
            - set(map(tuple, manual_remove))
        self.gui.update_exclusion_mask_text(
            len(effective), len(auto), len(manual_add), len(manual_remove))

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------
    def _get_saveable_config(self) -> Dict:
        excl_grid, excl_cells, excl_add, excl_remove = \
            self.processor.get_exclusion_state()
        return {
            "camera_source": self.camera.state.source,
            "model": self.models.current_model_name,
            # Save the *intent* (not the loaded state) so a fallback session
            # doesn't silently persist TRT=off and mute the alert banner.
            "use_tensorrt": self.models._trt_requested,
            "confidence": self.settings.confidence,
            "yolo_imgsz": self.settings.imgsz,
            "fp16": self.settings.use_fp16,
            "person_height_px": self.settings.person_height_px,
            "person_height_min_ratio": self.settings.person_height_min_ratio,
            "person_height_max_ratio": self.settings.person_height_max_ratio,
            "mog2_var_threshold": self.processor.get_motion_var_threshold(),
            "exclusion_grid": list(excl_grid),
            "exclusion_cells": [list(c) for c in excl_cells],
            "exclusion_manual_add": [list(c) for c in excl_add],
            "exclusion_manual_remove": [list(c) for c in excl_remove],
            "enhance_enabled": self.settings.enhance_enabled,
            "enhance_lite": self.settings.enhance_lite,
            "enhance_force": self.settings.enhance_force,
            "greyscale": self.settings.greyscale,
            "brightness_threshold": self.settings.brightness_threshold,
            "denoise_strength": self.settings.denoise_strength,
            "clahe_clip": self.enhancer.clahe_clip,
            "gamma": self.enhancer.gamma,
            "show_skeleton": self.show_skeleton,
            "show_keypoints": self.show_keypoints,
            "show_bbox": self.show_bbox,
            "show_trails": self.show_trails,
            "show_ids": self.show_ids,
            "tracking_mode": self.tracker.tracking_mode.value,
            "tracker_max_age": self.tracker.max_age,
            "tracker_smoothing": self.tracker.smoothing_depth,
            "max_persons": self.tracker.max_persons,
            "motion_sensitivity": self.processor.get_motion_sensitivity(),
            "osc_enabled": self.osc_enabled,
            "osc_ip": self.osc_ip,
            "osc_port": self.osc_port,
            "preview_enabled": self.preview_enabled,
            "preview_fps_cap": self.preview_fps_cap,
            "input_fps_cap": self.input_fps_cap,
            "preview_scale": self.preview.render_scale,
            "roi_enabled": self.settings.roi_enabled,
            "roi_x": self.settings.roi_x,
            "roi_y": self.settings.roi_y,
            "roi_w": self.settings.roi_w,
            "roi_h": self.settings.roi_h,
            "roi_source_w": self._roi_source_size[0],
            "roi_source_h": self._roi_source_size[1],
            "ids_ratio": self.cameras.ids_ratio,
            "ids_gain_db": self.cameras.ids_gain_db,
            "ids_exposure_us": self.cameras.ids_exposure_us,
            "bg_subtract_enabled": self.settings.bg_subtract_enabled,
            "bg_subtract_sensitivity": self.settings.bg_subtract_sensitivity,
            "mog2_scale": self.processor.get_motion_scale(),
            "blur_budget_ms": self.calibration.blur_budget_ms,
            "sensitivity": self.sensitivity,
            "sensitivity_conf_seed": self._sensitivity_conf_seed,
            # Persist the calibrated anchor, not just the live macro output —
            # otherwise a save while the dial is loose ratchets the calibrated
            # varThreshold away on the next load (ROADMAP bug #8).
            "sensitivity_var_anchor": self._sensitivity_var_anchor,
        }

    def _apply_config_without_model(self, config: Dict):
        """Apply config settings except model/imgsz (those are handled separately during project switch)."""
        # YOLO settings (except imgsz which is handled separately)
        if "confidence" in config:
            self.settings.confidence = config["confidence"]
            self.gui and self.gui.sync_slider("confidence", config["confidence"])
        if "person_height_px" in config:
            self.settings.person_height_px = config["person_height_px"]
            self.tracker.set_person_height(config["person_height_px"])
            self.gui and self.gui.sync_slider("person_height", config["person_height_px"])
        # Calibrated height ratios + MOG2 varThreshold (set by Go-Live calibration).
        if "person_height_min_ratio" in config:
            self.settings.person_height_min_ratio = float(config["person_height_min_ratio"])
        if "person_height_max_ratio" in config:
            self.settings.person_height_max_ratio = float(config["person_height_max_ratio"])
        if "mog2_var_threshold" in config:
            self.processor.set_motion_var_threshold(float(config["mog2_var_threshold"]))
            self._sensitivity_var_anchor = float(config["mog2_var_threshold"])
        # The persisted anchor (calibrated value) overrides the live macro
        # output above — older configs lack it and keep the fallback.
        if "sensitivity_var_anchor" in config:
            self._sensitivity_var_anchor = float(config["sensitivity_var_anchor"])
        # Sensitivity macro: restore the seed + dial (older configs lack them —
        # anchor on the loaded confidence at the midpoint).
        if "sensitivity_conf_seed" in config:
            self._sensitivity_conf_seed = float(config["sensitivity_conf_seed"])
        elif "confidence" in config:
            self._sensitivity_conf_seed = float(config["confidence"])
        if "sensitivity" in config:
            self.sensitivity = float(config["sensitivity"])
        elif "confidence" in config:
            self.sensitivity = 50.0
        self.gui and self.gui.sync_slider('sensitivity', self.sensitivity)
        if "exclusion_cells" in config:
            grid = tuple(config.get("exclusion_grid") or AUTOCAL_EXCL_GRID)
            self.processor.set_exclusion(
                grid, config["exclusion_cells"],
                config.get("exclusion_manual_add") or (),
                config.get("exclusion_manual_remove") or ())
            self._sync_mask_ui()
        if "yolo_imgsz" in config:
            # Just sync UI, don't trigger callback (imgsz already set)
            self.gui and self.gui.sync_combo("imgsz", str(config["yolo_imgsz"]))

        # Enhancement
        if "enhance_enabled" in config:
            self.settings.enhance_enabled = config["enhance_enabled"]
            self.gui and self.gui.sync_checkbox("enhance", config["enhance_enabled"])
        if "enhance_lite" in config:
            self.settings.enhance_lite = config["enhance_lite"]
            self.gui and self.gui.sync_checkbox("enhance_lite", config["enhance_lite"])
        if "enhance_force" in config:
            self.settings.enhance_force = config["enhance_force"]
            self.gui and self.gui.sync_checkbox("enhance_force", config["enhance_force"])
        if "greyscale" in config:
            self.settings.greyscale = config["greyscale"]
            self.gui and self.gui.sync_checkbox("greyscale", config["greyscale"])
        if "brightness_threshold" in config:
            self.settings.brightness_threshold = config["brightness_threshold"]
            self.gui and self.gui.sync_slider("brightness_threshold", config["brightness_threshold"])
        if "denoise_strength" in config:
            self.settings.denoise_strength = config["denoise_strength"]
            self.gui and self.gui.sync_slider("denoise", config["denoise_strength"])
        if "clahe_clip" in config:
            self.enhancer.clahe_clip = config["clahe_clip"]
            self.enhancer._update_clahe()
            self.gui and self.gui.sync_slider("clahe", config["clahe_clip"])
        if "gamma" in config:
            self.enhancer.gamma = config["gamma"]
            self.enhancer._update_gamma_lut()
            self.gui and self.gui.sync_slider("gamma", config["gamma"])

        # Visualization
        if "show_skeleton" in config:
            self.show_skeleton = config["show_skeleton"]
            self.gui and self.gui.sync_checkbox("skeleton", config["show_skeleton"])
        if "show_keypoints" in config:
            self.show_keypoints = config["show_keypoints"]
            self.gui and self.gui.sync_checkbox("keypoints", config["show_keypoints"])
        if "show_bbox" in config:
            self.show_bbox = config["show_bbox"]
            self.gui and self.gui.sync_checkbox("bbox", config["show_bbox"])
        if "show_trails" in config:
            self.show_trails = config["show_trails"]
            self.gui and self.gui.sync_checkbox("trails", config["show_trails"])
        if "show_ids" in config:
            self.show_ids = config["show_ids"]
            self.gui and self.gui.sync_checkbox("ids", config["show_ids"])

        # Tracker
        if "tracker_distance" in config:
            pass  # Legacy: distance is now auto-derived from person_height_px
        # Load tracking_mode FIRST so its defaults don't clobber user settings
        if "tracking_mode" in config:
            try:
                mode = TrackingMode(config["tracking_mode"])
            except ValueError:
                mode = TrackingMode.YOLO_FIRST
            self.tracker.set_tracking_mode(mode)
            self.processor.set_tracking_mode(mode)
        # Load tracker_max_age AFTER tracking_mode so user value wins
        if "tracker_max_age" in config:
            self.tracker.max_age = config["tracker_max_age"]
            self.gui and self.gui.sync_slider("tracker_max_age", config["tracker_max_age"])
        if "tracker_smoothing" in config:
            self.tracker.smoothing_depth = config["tracker_smoothing"]
            self.gui and self.gui.sync_slider("tracker_smoothing", config["tracker_smoothing"])
        if "max_persons" in config:
            self.tracker.max_persons = int(config["max_persons"])
        if "motion_sensitivity" in config:
            self.processor.set_motion_sensitivity(config["motion_sensitivity"])
            self.gui and self.gui.sync_slider("motion_sensitivity", config["motion_sensitivity"])

        # OSC
        if "osc_enabled" in config:
            self.osc_enabled = config["osc_enabled"]
            self.settings.osc_enabled = config["osc_enabled"]
            self.gui and self.gui.sync_checkbox("osc", config["osc_enabled"])
        if "osc_ip" in config:
            self.osc_ip = config["osc_ip"]
            self.gui and self.gui.sync_input("osc_ip", config["osc_ip"])
        if "osc_port" in config:
            self.osc_port = config["osc_port"]
            self.gui and self.gui.sync_input("osc_port", config["osc_port"])
        if self.osc_enabled and (config.get("osc_ip") or config.get("osc_port")):
            self._init_osc()

        # Preview
        if "preview_enabled" in config:
            self.preview_enabled = config["preview_enabled"]
            self.gui and self.gui.sync_checkbox("preview", config["preview_enabled"])
        if "preview_fps_cap" in config:
            self._cb_preview_cap_toggle(config["preview_fps_cap"])
            self.gui and self.gui.sync_checkbox("preview_cap", self.preview_fps_cap)
        if "input_fps_cap" in config:
            self.input_fps_cap = config["input_fps_cap"]
            self.gui and self.gui.sync_checkbox("input_fps_cap", self.input_fps_cap)
        if "preview_scale" in config:
            # Legacy config value – render scale is now auto-computed from layout.
            # Just store it; actual scale will be overridden by next layout recompute.
            pass

        if "roi_enabled" in config:
            self.settings.roi_enabled = bool(config["roi_enabled"])
        # Use the frame size that was active when the config was saved so
        # that _normalize_roi_rect clamps against the correct dimensions
        # (not the current _roi_source_size which may be stale/default).
        roi_frame_w = int(config.get("roi_source_w", self._roi_source_size[0]))
        roi_frame_h = int(config.get("roi_source_h", self._roi_source_size[1]))
        roi_x = int(config.get("roi_x", self.settings.roi_x))
        roi_y = int(config.get("roi_y", self.settings.roi_y))
        roi_w = int(config.get("roi_w", self.settings.roi_w or roi_frame_w))
        roi_h = int(config.get("roi_h", self.settings.roi_h or roi_frame_h))
        self._set_roi_rect(
            roi_x,
            roi_y,
            roi_w,
            roi_h,
            frame_w=roi_frame_w,
            frame_h=roi_frame_h,
            sync_ui=False,
            request_reprocess=False,
        )
        self.roi_edit_mode = False
        self._sync_roi_ui()

        # IDS crop ratio
        if "ids_ratio" in config:
            ratio = max(0.5, min(2.0, float(config["ids_ratio"])))
            self.cameras._cb_ids_ratio_change(ratio)
            self.gui and self.gui.sync_slider("ids_ratio", ratio)

        # IDS gain
        if "ids_gain_db" in config:
            self.cameras._cb_ids_gain_change(config["ids_gain_db"])
            self.gui and self.gui.sync_slider("ids_gain_db", config["ids_gain_db"])

        # IDS exposure
        if "ids_exposure_us" in config:
            self.cameras._cb_ids_exposure_change(config["ids_exposure_us"])
            self.gui and self.gui.sync_slider("ids_exposure_us", self.cameras.ids_exposure_us)

        # Background subtraction
        if "bg_subtract_enabled" in config:
            self.settings.bg_subtract_enabled = config["bg_subtract_enabled"]
            self.gui and self.gui.sync_checkbox("bg_enable", config["bg_subtract_enabled"])
        if "bg_subtract_sensitivity" in config:
            self.settings.bg_subtract_sensitivity = config["bg_subtract_sensitivity"]
            self.gui and self.gui.sync_slider("bg_sensitivity", config["bg_subtract_sensitivity"])
        if "blur_budget_ms" in config:
            self.calibration.blur_budget_ms = float(config["blur_budget_ms"])
        # MOG2 scale
        if "mog2_scale" in config and self.processor.motion_detector is not None:
            self.processor.set_motion_scale(config["mog2_scale"])
            self.gui and self.gui.sync_slider("mog2_scale", config["mog2_scale"])
        # Update BG status display
        if self.gui:
            bg = self.processor.bg_subtractor
            self.gui.update_bg_status(bg.has_reference, self.settings.bg_subtract_enabled)

    # ------------------------------------------------------------------
    # GUI callbacks
    # ------------------------------------------------------------------
    def _request_reprocess(self):
        """When paused, re-mark the current frame so the pipeline reruns it."""
        self.processor.invalidate_preview_cache()
        self.recorder.requeue_frame()

    def _cb_enhance_toggle(self, enabled: bool):
        self.settings.enhance_enabled = enabled
        print(f"Enhancement: {'ON' if enabled else 'OFF'}")
        self._request_reprocess()

    def _cb_enhance_lite_toggle(self, enabled: bool):
        self.settings.enhance_lite = enabled
        print(f"Enhancement Lite Mode: {'ON (gamma only)' if enabled else 'OFF (full CLAHE)'}")
        self._request_reprocess()

    def _cb_enhance_force_toggle(self, enabled: bool):
        self.settings.enhance_force = enabled
        print(f"Enhancement Force: {'ON (ignore brightness threshold)' if enabled else 'OFF (auto-bypass)'}")
        self._request_reprocess()

    def _cb_greyscale_toggle(self, enabled: bool):
        self.settings.greyscale = enabled
        print(f"Greyscale: {'ON (mono camera simulation)' if enabled else 'OFF'}")
        self._request_reprocess()

    def _cb_clahe_change(self, value: float):
        self.enhancer.clahe_clip = value
        self.enhancer._update_clahe()
        print(f"CLAHE clip: {value:.1f}")
        self._request_reprocess()

    def _cb_gamma_change(self, value: float):
        self.enhancer.gamma = value
        self.enhancer._update_gamma_lut()
        print(f"Gamma: {value:.2f}")
        self._request_reprocess()

    def _cb_brightness_threshold_change(self, value: int):
        self.settings.brightness_threshold = value
        print(f"Brightness threshold: {value}")
        self._request_reprocess()

    def _cb_denoise_change(self, value: float):
        self.settings.denoise_strength = value
        print(f"Denoise strength: {value:.2f}")
        self._request_reprocess()

    # --- Background subtraction callbacks ---
    def _cb_bg_capture(self):
        """Capture current frame as background reference."""
        frame = self._get_current_frame_for_bg()
        if frame is not None:
            # Clear any previous reference first to avoid stacking
            self.processor.bg_subtractor.clear()
            self.processor.bg_subtractor.capture_cpu(frame)
            # Auto-enable on capture
            self.settings.bg_subtract_enabled = True
            print(f"[BG] Background reference captured ({frame.shape[1]}x{frame.shape[0]}), auto-enabled")
            if self.gui:
                self.gui.sync_checkbox("bg_enable", True)
                self.gui.update_bg_status(True, True)
        else:
            print("[BG] No frame available for capture")

    def _cb_bg_enable_toggle(self, enabled: bool):
        self.settings.bg_subtract_enabled = enabled
        bg = self.processor.bg_subtractor
        if enabled and not bg.has_reference:
            print("[BG] Warning: enabled but no reference captured yet")
        print(f"[BG] Background subtraction: {'ON' if enabled else 'OFF'}")
        if self.gui:
            self.gui.update_bg_status(bg.has_reference, enabled)
        self._request_reprocess()

    def _cb_bg_clear(self):
        self.processor.bg_subtractor.clear()
        self.settings.bg_subtract_enabled = False
        if self.gui:
            self.gui.sync_checkbox("bg_enable", False)
            self.gui.update_bg_status(False, False)
        print("[BG] Background reference cleared")

    def _cb_bg_sensitivity_change(self, value: int):
        self.settings.bg_subtract_sensitivity = value
        print(f"[BG] Sensitivity: {value}")
        self._request_reprocess()

    def _get_current_frame_for_bg(self) -> 'Optional[np.ndarray]':
        """Get the current raw frame for BG capture (before any processing).
        
        Uses the last raw frame from the main loop (same frame the pipeline sees)
        to avoid mismatch from reading a separate camera buffer frame.
        Falls back to camera.read() if no stashed frame available.
        """
        # Prefer the stashed raw frame — same frame the pipeline processes
        if self._last_raw_frame is not None:
            return self._last_raw_frame.copy()
        
        # Fallback: read directly from camera
        try:
            if self._use_unified_camera and self.unified_camera is not None:
                cached = self.unified_camera.get_last_cpu_frame()
                if cached is not None:
                    return cached.copy()
                ret, frame = self.unified_camera.read()
            else:
                ret, frame = self.camera.read()
            if ret and frame is not None:
                return frame.copy()
        except Exception as e:
            print(f"[BG] Failed to get frame for capture: {e}")
        return None

    def _cb_confidence_change(self, value: float):
        # Expert override: also re-anchor the sensitivity macro on the new value.
        self.settings.confidence = value
        self._sensitivity_conf_seed = float(value)
        self.sensitivity = 50.0
        # Dial 50 means "anchor values": undo any macro-lowered varThreshold
        # so the dial position and the applied state agree (ROADMAP bug #8).
        self.processor.set_motion_var_threshold(self._sensitivity_var_anchor)
        self.gui and self.gui.sync_slider('sensitivity', 50.0)
        print(f"Confidence: {value:.2f}")
        self._request_reprocess()

    def _cb_sensitivity_change(self, value: float):
        """Operator macro: one dial driving confidence (+var at the loose end)."""
        self.sensitivity = float(value)
        m = macro_to_settings(value, self._sensitivity_conf_seed,
                              self._sensitivity_var_anchor)
        self.settings.confidence = m["confidence"]
        self.processor.set_motion_var_threshold(m["mog2_var_threshold"])
        self.gui and self.gui.sync_slider('confidence', m["confidence"])
        print(f"Sensitivity {value:.0f} -> confidence {m['confidence']:.2f}, "
              f"varThreshold {m['mog2_var_threshold']:.0f}")
        self._request_reprocess()

    def _reset_sensitivity_anchor(self, conf_seed: Optional[float] = None,
                                  var_anchor: Optional[float] = None):
        """Re-center the macro at 50 after a calibration set new seeds."""
        if conf_seed is not None:
            self._sensitivity_conf_seed = float(conf_seed)
        if var_anchor is not None:
            self._sensitivity_var_anchor = float(var_anchor)
        self.sensitivity = 50.0
        self.gui and self.gui.sync_slider('sensitivity', 50.0)

    def _cb_motion_sensitivity_change(self, value: float):
        self.processor.set_motion_sensitivity(value)
        print(f"Motion bridge sensitivity: {value:.2f}")
        self._request_reprocess()

    def _cb_imgsz_change(self, value: int):
        new_imgsz = int(value)
        old_imgsz = self.settings.imgsz
        
        if new_imgsz == old_imgsz:
            return
        
        self.settings.imgsz = new_imgsz
        self.models.model_manager.set_imgsz(new_imgsz)
        self._update_imgsz_roi_warning()
        roi_warning = self._get_imgsz_roi_warning()
        if roi_warning and self.gui:
            self.gui.show_toast("Current imgsz is below the ROI suggestion", duration=3.0, color=(255, 180, 80))
        
        max_cam_dim = max(self.camera.state.width, self.camera.state.height)
        if new_imgsz > max_cam_dim:
            print(f"⚠️  YOLO imgsz {new_imgsz} > camera {max_cam_dim}px - may reduce accuracy (padding)")
        else:
            print(f"YOLO imgsz: {new_imgsz}")
        
        # If TRT is enabled, we need to reload the model because engines are imgsz-specific
        if self.models.model_manager.is_using_tensorrt():
            base_name = self.models.current_model_name
            
            # Check if engine exists for new imgsz
            if self.models.model_manager.engine_exists(base_name):
                # Engine exists, reload with TRT
                print(f"TRT engine exists for {base_name}@{new_imgsz}, reloading...")
                self.models._pending_trt_switch = True
                self.models._pending_model_switch = base_name
                # Block processing until model is reloaded to prevent imgsz mismatch
                self.models._model_loading = True
                self.models._model_loaded = False
            else:
                # No engine for new imgsz - fall back to PyTorch (don't prompt, just switch)
                print(f"No TRT engine for {base_name}@{new_imgsz}, falling back to PyTorch")
                self.gui.set_trt_checkbox(False)
                self.models._pending_trt_switch = False
                self.models._pending_model_switch = base_name
                # Block processing until model is reloaded to prevent imgsz mismatch
                self.models._model_loading = True
                self.models._model_loaded = False
                self.gui.show_toast(f"No TRT for {new_imgsz}px, using PyTorch", duration=3.0, color=(255, 200, 100))

    def _cb_person_height_change(self, value: int):
        self.settings.person_height_px = int(value)
        self.tracker.set_person_height(int(value))
        self._request_reprocess()

    def _cb_visualization_toggle(self, name: str, enabled: bool):
        if name == "skeleton":
            self.show_skeleton = enabled
        elif name == "keypoints":
            self.show_keypoints = enabled
        elif name == "bbox":
            self.show_bbox = enabled
        elif name == "trails":
            self.show_trails = enabled
        elif name == "ids":
            self.show_ids = enabled
        print(f"{name.capitalize()}: {'ON' if enabled else 'OFF'}")

    def _cb_tracker_age_change(self, value: int):
        self.tracker.max_age = value
        print(f"Tracker max age: {value} frames")

    def _cb_mog2_scale_change(self, value: float):
        if self.processor.motion_detector is not None:
            self.processor.set_motion_scale(value)
            print(f"MOG2 scale: {value:.2f} ({int(1920*value)}x{int(1080*value)})")

    def _cb_tracker_reset(self):
        self.tracker.reset()
        # Reset MOG2 background model so it re-learns the scene
        if hasattr(self.processor, 'motion_detector') and self.processor.motion_detector is not None:
            self.processor.reset_motion_detectors()
        self.tracker.logger.log_settings(self._get_saveable_config())
        if self.osc:
            self.osc.send_clear()
        print("Tracker reset")

    def _camera_preview_geometry(self, width: int, height: int):
        """Preview geometry + GUI layout follow-up after a camera (re)open."""
        self.preview.width = int(width * self.preview.render_scale)
        self.preview.height = int(height * self.preview.render_scale)
        if self.processor:
            self.processor.set_preview_size(self.preview.width, self.preview.height)
        if self.gui:
            self.gui.set_camera_dimensions(width, height)
        self._pending_preview_resize = True

    def _repush_preview_size(self):
        """Re-push the current preview size to the processor (IDS ratio change)."""
        if self.processor:
            self.processor.set_preview_size(self.preview.width, self.preview.height)

    def _restore_playback_dims(self):
        """Re-apply playback preview dimensions after a camera reopen (model load)."""
        if self.recorder.is_playing:
            self.recording._apply_playback_dimensions()

    def _on_playback_restart(self):
        """Playback loop/restart rollover: reset frame counter + tracker (main thread)."""
        self._total_frame_count = 0
        self._cb_tracker_reset()

    def _cb_report_issue_request(self):
        """Build the current playback context for issue reporting."""
        if not self.recorder.is_playing:
            if self.gui:
                self.gui.show_toast(
                    "Issue reporting is available during playback only",
                    duration=2.5,
                    color=(255, 180, 120),
                )
            return None

        self.tracker.logger.flush()
        return {
            "project": self.configs._current_project,
            "slot": self.recorder.status.current_slot,
            "frame": self.recorder.status.playback_frame,
            "playback_total": self.recorder.status.playback_total,
            "playback_fps": self.recorder.status.playback_fps,
            "playback_speed": self.recorder._playback_speed,
            "playback_path": self.recorder.playback_path,
            "model": self.models.current_model_name,
            "imgsz": self.settings.imgsz,
            "tracker_max_age": self.tracker.max_age,
            "person_height_px": self.settings.person_height_px,
            "system_state": self.gui.get_system_state().name if self.gui else "UNKNOWN",
            "active_dancer_ids": sorted([t.track_id for t in self.last_tracked]),
        }

    def _cb_issue_submit(self, context: Dict, issue_type: str, note: str):
        """Persist a structured review issue for the current playback frame.

        issue_type contains comma-separated selected dancer IDs (e.g. "D1,D3")
        or empty string if none selected.

        When a session directory is active, issues are written there;
        otherwise falls back to the legacy ``review_issues/`` folder.
        """
        session_dir = self.tracker.logger.session_dir
        if session_dir:
            issue_dir = os.path.join(session_dir, "issues")
        else:
            issue_dir = os.path.join(
                self.configs.config_store.config_dir,
                self.configs._current_project,
                "review_issues",
            )
        os.makedirs(issue_dir, exist_ok=True)

        self.tracker.logger.flush()
        frame_num = int(context.get("frame", 0))
        slot_num = int(context.get("slot", 0))
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        slug = f"{timestamp}_slot{slot_num}_f{frame_num:05d}_{issue_type}"
        json_path = os.path.join(issue_dir, f"{slug}.json")
        png_path = os.path.join(issue_dir, f"{slug}.png")
        summary_path = os.path.join(issue_dir, "issues.jsonl")

        payload = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "issue_type": issue_type,
            "note": note.strip(),
            "dancer_labels": context.get("dancer_labels", {}),
            "context": context,
            "tracker_events": self.tracker.logger.get_events_around_frame(frame_num, window=30),
            "config_snapshot": self._get_saveable_config(),
            "tracked_ids": [track.track_id for track in self.last_tracked],
        }

        if self._last_review_frame is not None:
            try:
                cv2.imwrite(png_path, self._last_review_frame)
                payload["snapshot_path"] = png_path
            except Exception:
                payload["snapshot_path"] = None
        else:
            payload["snapshot_path"] = None

        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=_json_default)

        summary = {
            "created_at": payload["created_at"],
            "issue_type": issue_type,
            "dancer_labels": payload["dancer_labels"],
            "frame": frame_num,
            "slot": slot_num,
            "project": self.configs._current_project,
            "note": payload["note"],
            "json_path": json_path,
            "snapshot_path": payload["snapshot_path"],
        }
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, default=_json_default) + "\n")

        print(f"[Review] Saved issue report: {json_path}")
        if self.gui:
            self.gui.show_toast(
                f"Issue saved: slot {slot_num} frame {frame_num}",
                duration=3.0,
                color=(120, 220, 140),
            )

    def _cb_osc_toggle(self, enabled: bool):
        self.osc_enabled = enabled
        self.settings.osc_enabled = enabled
        if enabled and not self.osc:
            self._init_osc()
        print(f"OSC: {'ON' if enabled else 'OFF'}")

    def _cb_osc_config(self, ip: str, port: int):
        if ip != self.osc_ip or port != self.osc_port:
            self.osc_ip = ip
            self.osc_port = port
            if self.osc_enabled:
                self._init_osc()
            print(f"OSC target: {ip}:{port}")

    def _cb_issue_dialog_closed(self):
        """Refresh playback controls after the review dialog closes."""
        self.recording._update_recording_ui()

    def _cb_quit(self):
        self.tracker.logger.close()  # Phase 0: flush and close tracking log
        self.running = False
        self.recorder.close()

    def _cb_preview_toggle(self, enabled: bool):
        self.preview_enabled = enabled
        if enabled:
            print("Preview: ON (video pushed to GUI)")
            if self.gui:
                self.gui.show_toast(
                    "Preview ON",
                    duration=2.0,
                    color=(120, 220, 140),
                )
        else:
            print("Preview: OFF (no video output - measure raw FPS)")
            if self.gui:
                placeholder = np.zeros((max(1, self.preview.height),
                                        max(1, self.preview.width), 3),
                                       dtype=np.uint8)
                cv2.putText(
                    placeholder,
                    "PREVIEW OFF",
                    (max(20, self.preview.width // 8), max(40, self.preview.height // 2)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.8, self.preview.width / 900.0),
                    (0, 200, 255),
                    2,
                    cv2.LINE_AA,
                )
                cv2.putText(
                    placeholder,
                    "Playback continues, but frames are not displayed.",
                    (max(20, self.preview.width // 10), min(self.preview.height - 30, self.preview.height // 2 + 40)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    max(0.45, self.preview.width / 1600.0),
                    (180, 180, 180),
                    1,
                    cv2.LINE_AA,
                )
                self.gui.update_frame(placeholder)
                self.gui.show_toast(
                    "Preview OFF: playback keeps running but the image will not update",
                    duration=3.5,
                    color=(255, 200, 120),
                )

    def _cb_input_fps_cap_toggle(self, enabled: bool):
        self.input_fps_cap = enabled
        if enabled:
            print("Input FPS cap: ON (20 FPS limit)")
        else:
            print("Input FPS cap: OFF (uncapped)")

    def _cb_preview_cap_toggle(self, enabled: bool):
        self.preview_fps_cap = enabled
        # Sync to GPU pipeline if active
        if self.processor:
            self.processor.set_preview_fps_cap(10.0 if enabled else None)
            # Also halve preview resolution when capped to reduce GPU→CPU transfer
            self._sync_preview_size_to_gpu()
        if enabled:
            print("Preview FPS cap: ON (10 FPS limit, 0.5x preview)")
        else:
            print("Preview FPS cap: OFF (uncapped, full preview)")

    def _apply_preview_scale(self, value: float, force: bool = False):
        value = max(0.05, min(1.0, float(value)))
        if not force and abs(value - self.preview.render_scale) < 1e-3:
            return
        self.preview.render_scale = value
        cam_w = self.camera.state.width if self.camera.state.width > 0 else CAMERA_WIDTH
        cam_h = self.camera.state.height if self.camera.state.height > 0 else CAMERA_HEIGHT
        self.preview.width = int(cam_w * self.preview.render_scale)
        self.preview.height = int(cam_h * self.preview.render_scale)
        self._pending_preview_resize = True
        # Sync to GPU pipeline if active (exact dimensions for GPU resize)
        self._sync_preview_size_to_gpu()
        print(
            f"Preview render scale set: {self.preview.render_scale:.2f}x -> tex {self.preview.width}x{self.preview.height} (will resize)"
        )

    def _sync_preview_size_to_gpu(self):
        """Send preview dimensions to GPU pipeline, halved when cap is on."""
        if not self.processor:
            return
        w, h = self.preview.width, self.preview.height
        if self.preview_fps_cap:
            w = max(1, w // 2)
            h = max(1, h // 2)
        self.processor.set_preview_size(w, h)

    def _cb_preview_scale_change(self, value: float):
        # Manual slider removed; kept for backward compat with configs
        pass

    def _cb_roi_toggle(self, enabled: bool):
        self.settings.roi_enabled = bool(enabled)
        if self.settings.roi_enabled and (self.settings.roi_w <= 0 or self.settings.roi_h <= 0):
            frame_w, frame_h = self._roi_source_size
            self._set_roi_rect(0, 0, frame_w, frame_h, sync_ui=False, request_reprocess=False)
        if not self.settings.roi_enabled:
            self.roi_edit_mode = False
        self._sync_roi_ui()
        print(f"ROI: {'ON' if self.settings.roi_enabled else 'OFF'}")
        self._request_reprocess()

    def _cb_roi_edit_toggle(self, enabled: bool):
        if enabled and not self.settings.roi_enabled:
            self.settings.roi_enabled = True
        if enabled and self.mask_edit_mode:
            self._cb_mask_edit_toggle()  # one paint mode at a time
        self.roi_edit_mode = bool(enabled) and self.settings.roi_enabled
        if not self.roi_edit_mode:
            self._roi_drag_active = False
            self._roi_drag_mode = None
            self._roi_drag_origin = None
            self._roi_drag_start_rect = None
        self._sync_roi_ui()
        if self.gui:
            message = "ROI edit mode: drag on preview" if self.roi_edit_mode else "ROI edit mode: off"
            self.gui.show_toast(message, duration=2.0, color=(120, 200, 255))

    def _handle_preview_double_click(self, sender=None, app_data=None):
        """Double-click on the preview toggles ROI edit mode."""
        if app_data != dpg.mvMouseButton_Left:
            return
        if self.mask_edit_mode:
            return  # mask editor owns preview clicks
        if self.gui and self.gui.project_picker_visible():
            return
        if self._get_preview_mouse_point() is None:
            return
        self._cb_roi_edit_toggle(not self.roi_edit_mode)

    def _cb_roi_reset(self):
        frame_w, frame_h = self._roi_source_size
        self._set_roi_rect(0, 0, frame_w, frame_h)
        print("ROI reset to full frame")

    # ------------------------------------------------------------------
    # Recording callbacks
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _draw_height_ruler(self, frame, scale: float = 1.0, thickness_scale: float = 1.0):
        h, w = frame.shape[:2]
        height_px = int(self.settings.person_height_px * scale)
        ts = max(0.3, thickness_scale)
        x = int(30 * ts)
        y_center = h // 2
        y_top = max(10, y_center - height_px // 2)
        y_bottom = min(h - 10, y_center + height_px // 2)
        color = (0, 255, 255)
        bg_color = (0, 0, 0)
        line_thickness = max(1, int(2 * ts))
        cap_width = max(8, int(15 * ts))
        bg_thickness = line_thickness + max(2, int(4 * ts))
        cv2.line(frame, (x, y_top), (x, y_bottom), bg_color, bg_thickness)
        cv2.line(frame, (x - cap_width // 2, y_top), (x + cap_width // 2, y_top), bg_color, bg_thickness)
        cv2.line(frame, (x - cap_width // 2, y_bottom), (x + cap_width // 2, y_bottom), bg_color, bg_thickness)
        cv2.line(frame, (x, y_top), (x, y_bottom), color, line_thickness)
        cv2.line(frame, (x - cap_width // 2, y_top), (x + cap_width // 2, y_top), color, line_thickness)
        cv2.line(frame, (x - cap_width // 2, y_bottom), (x + cap_width // 2, y_bottom), color, line_thickness)

    def _draw_frame_number_overlay(self, frame, frame_number: int):
        """Phase 0: Draw frame number overlay in the top-right corner.

        Shows 'Frame: NNN' (or 'Frame: NNN / TTT' during playback)
        in white text on a dark semi-transparent background so it's
        always readable regardless of scene content.
        """
        h, w = frame.shape[:2]
        # Build label
        if self.recorder.is_playing:
            total = self.recorder.status.playback_total
            label = f"Frame: {frame_number}/{total}"
        else:
            label = f"Frame: {frame_number}"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.6
        thickness = 1
        color = (255, 255, 255)
        bg_color = (0, 0, 0)
        (tw, th), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        margin = 8
        text_x = w - tw - margin
        text_y = th + margin
        # Dark background rectangle
        cv2.rectangle(
            frame,
            (text_x - 4, text_y - th - 4),
            (text_x + tw + 4, text_y + baseline + 4),
            bg_color, -1,
        )
        cv2.putText(frame, label, (text_x, text_y), font, font_scale, color, thickness)

    def _update_gpu_stats_if_due(self, now: Optional[float] = None, interval_s: float = 1.0):
        """Update top-bar GPU stats at a fixed cadence without affecting FPS timing."""
        if self.gui is None:
            return
        t = now if now is not None else time.time()
        if t - self._last_gpu_stats_time >= interval_s:
            self._last_gpu_stats_time = t
            self.gui.update_gpu_stats()

    def _ops_heartbeat(self):
        """Watchdog beat + 1 Hz health tick.

        Must run on EVERY main-loop iteration - including the camera-down /
        no-frame continue paths, which never reach the FPS block at the loop
        tail. That guarantee is what lets the camera-down alert keep ringing
        during an outage.
        """
        self._watchdog.beat()
        now = time.time()
        if now - self._last_ops_tick < 1.0:
            return
        self._last_ops_tick = now
        in_run = bool(self.gui and self.gui.get_system_state() == SystemState.RUN)
        # Person-height staleness input (⑤d): 1 Hz sample of RAW detection
        # heights (pre-size-gate, original-space px) over a rolling window.
        for h in getattr(self.processor, "last_raw_det_heights", ()):
            self._height_samples.append((now, h))
        cutoff = now - OPS_HEIGHT_WINDOW_S
        while self._height_samples and self._height_samples[0][0] < cutoff:
            self._height_samples.popleft()
        height_median = None
        height_gate = None
        if len(self._height_samples) >= OPS_HEIGHT_MIN_SAMPLES:
            ph = float(self.settings.person_height_px)
            height_gate = (ph * float(self.settings.person_height_min_ratio),
                           ph * float(self.settings.person_height_max_ratio))
            height_median = float(np.median([h for _t, h in self._height_samples]))
        try:
            alerts = self._health.tick(
                now,
                fps=self.fps,
                n_tracked=len(self.last_tracked),
                in_run=in_run,
                model_ready=self.models._model_loaded and not self.models._model_loading,
                camera_open=self.camera.state.is_open,
                camera_reconnecting=self.cameras._camera_reconnecting,
                playback_active=self.recorder.is_playing,
                n_over_cap=self.tracker.last_over_cap,
                height_median=height_median,
                height_gate=height_gate,
            )
        except Exception as e:  # noqa: BLE001 - monitoring must never kill the loop
            print(f"[Alert] health tick failed: {e}")
            return
        for alert in alerts:
            self._emit_ops_alert(alert)

    def _emit_ops_alert(self, alert):
        print(f"[Alert] {alert.message}")
        if self.gui:
            self.gui.show_toast(f"/!\\ {alert.message}", duration=8.0,
                                color=(255, 80, 80))
        try:
            self.tracker.logger.log("OPS_ALERT", {"kind": alert.kind, **alert.data})
        except Exception:
            pass

    def _cb_system_state_change(self, state, old_state):
        """GUI system-state transitions; readiness check on entering RUN."""
        if (OPS_READINESS_ENABLED and state == SystemState.RUN
                and old_state != SystemState.RUN):
            threading.Thread(target=self._run_readiness_check,
                             name="OpsReadiness", daemon=True).start()

    def _run_readiness_check(self):
        """Best-effort Go-Live readiness line (~0.3 s, off the main loop).

        Prints a [Readiness] block + one toast. NEVER prevents RUN.
        """
        try:
            results = []
            ids_frames = ids_dropped = 0
            try:
                if self._use_unified_camera and self.unified_camera is not None:
                    ids_frames, ids_dropped = self.unified_camera.get_ids_counters()
            except Exception:
                pass
            results.append(check_camera(
                is_open=self.camera.state.is_open,
                reconnecting=self.cameras._camera_reconnecting,
                source=str(self.camera.state.source),
                fps=self.fps, min_fps=OPS_MIN_SHOW_FPS,
                ids_frames=ids_frames, ids_dropped=ids_dropped))
            results.append(check_tensorrt(
                trt_requested=bool(self.models._trt_requested),
                trt_active=self.models.model_manager.is_using_tensorrt(),
                fallback_reason=self.models.model_manager.get_tensorrt_fallback_reason(),
                gpu_fallback_reason=self.processor.gpu_fallback_reason or ""))
            results.append(check_osc(
                enabled=self.osc_enabled, ip=self.osc_ip, port=self.osc_port,
                timeout_s=OPS_OSC_PROBE_TIMEOUT_S))
            saved_at = None
            try:
                latest = self.configs.config_store.latest_for_project(self.configs._current_project)
                if latest:
                    saved_at = datetime.fromtimestamp(
                        os.path.getmtime(latest)).isoformat()
            except Exception:
                pass
            try:
                mask_cells = len(self.processor.get_exclusion()[1])
            except Exception:
                mask_cells = None
            results.append(check_calibration(
                saved_at_iso=saved_at, active_profile=self.configs._active_profile,
                warn_age_h=OPS_CALIB_AGE_WARN_H, mask_cells=mask_cells))
            results.append(check_disk(
                recordings_dir=self.recorder.recordings_dir,
                warn_free_gb=OPS_DISK_WARN_FREE_GB,
                fail_free_gb=OPS_DISK_FAIL_FREE_GB))
            results.append(check_gpu_temp(gpu_stats=get_gpu_stats(),
                                          warn_c=int(self._health.gpu_temp_c)))
            report = ReadinessReport(results)
            print(report.console_block(
                f"(project={self.configs._current_project}, "
                f"profile={self.configs._active_profile}) "))
            if self.gui:
                msg, color = report.toast_summary()
                self.gui.show_toast(msg, duration=6.0, color=color)
        except Exception as e:  # noqa: BLE001 - never disturb Go-Live
            print(f"[Readiness] check failed: {e}")

    def _log_timing_spikes_if_any(self, timing: Optional[Dict[str, float]]):
        """Diagnosis-only: print timing spikes with per-stage breakdown (throttled)."""
        if not timing:
            return

        now = time.time()
        if now - self._last_spike_log_time < 1.0:
            return

        watch_keys = [
            "preview_download",
            "preview_download_sync",
            "preview_download_numpy",
            "preview_download_cast",
            "extract_cpu_total",
            "extract_kpts_cpu",
            "extract_boxes_cpu",
            "yolo",
            "total",
            "dpg_render",
            "gui_stats",
            "camera_read",
            "preview_upload",
        ]
        spikes = []
        for key in watch_keys:
            value = timing.get(key)
            if value is None:
                continue
            threshold = 20.0 if key not in ("yolo", "total", "dpg_render", "process_wall") else 35.0
            if float(value) >= threshold:
                spikes.append((key, float(value)))

        # Periodic full budget breakdown (every 5s regardless of spikes)
        if not hasattr(self, '_last_budget_log_time'):
            self._last_budget_log_time = 0.0
        if now - self._last_budget_log_time >= 5.0:
            self._last_budget_log_time = now
            budget_keys = ["camera_read", "process_wall", "yolo", "preview_upload",
                           "preview_draw", "dpg_render", "gui_stats",
                           "preview_download", "extract_cpu_total",
                           "mog2_cvt", "mog2_feed", "tracker_update",
                           "track", "enhance"]
            parts = []
            for k in budget_keys:
                v = timing.get(k)
                if v is not None and float(v) > 0.1:
                    parts.append(f"{k}={float(v):.1f}")
            if parts:
                print(f"[Budget] {', '.join(parts)}  (FPS={self.fps:.1f})")

        if not spikes:
            return

        self._last_spike_log_time = now
        spikes.sort(key=lambda item: item[1], reverse=True)
        summary = ", ".join([f"{name}={value:.1f}ms" for name, value in spikes[:6]])
        print(f"[PerfSpike] {summary}")

    def _log_runtime_diag_if_stalled(
        self,
        camera_read_ms: float,
        process_wall_ms: float,
        preview_new: bool,
        frame_available: bool,
        gpu_tensor_available: bool,
        camera_waiting: bool = False,
    ):
        """Diagnosis-only: emit a compact runtime heartbeat + stall transitions."""
        now = time.time()
        # Stall detection: based on frame ACQUISITION, not preview generation.
        frame_acquired = frame_available or gpu_tensor_available
        if frame_acquired:
            self._last_fresh_frame_time = now
        if preview_new:
            self._last_fresh_preview_time = now

        stall_age_s = now - self._last_fresh_frame_time
        stalled = stall_age_s >= 0.25
        state_changed = stalled != self._last_preview_stalled_state
        if not state_changed:
            return  # only log on STALL ↔ OK transitions

        self._last_diag_log_time = now
        self._last_preview_stalled_state = stalled
        path = "ids" if self.cameras._is_ids_camera_active() else "opencv"
        timing = self.timing or {}
        state = "STALL" if stalled else "OK"

        ids_read_age_s = float("inf")
        ids_acq_age_s = float("inf")
        ids_frame_count = 0
        ids_dropped = 0
        if self.cameras._is_ids_camera_active() and self.unified_camera is not None:
            try:
                ids_read_age_s = float(self.unified_camera.get_last_frame_age_s())
                ids_acq_age_s = float(self.unified_camera.get_last_acquired_age_s())
                ids_frame_count, ids_dropped = self.unified_camera.get_ids_counters()
            except Exception:
                pass

        print(
            "[Diag] "
            f"state={state} "
            f"path={path} "
            f"stall_age={stall_age_s:.2f}s "
            f"cam_wait={int(camera_waiting)} "
            f"cam_read={camera_read_ms:.1f}ms "
            f"proc_wall={process_wall_ms:.1f}ms "
            f"preview_new={int(preview_new)} "
            f"frame={int(frame_available)} gpu_tensor={int(gpu_tensor_available)} "
            f"ids_read_age={ids_read_age_s:.2f}s ids_acq_age={ids_acq_age_s:.2f}s "
            f"ids_frames={ids_frame_count} ids_drop={ids_dropped} "
            f"yolo={float(timing.get('yolo', 0.0)):.1f} "
            f"extract_cpu={float(timing.get('extract_cpu_total', 0.0)):.1f} "
            f"preview_sync={float(timing.get('preview_download_sync', 0.0)):.1f}"
        )

    def _handle_key(self, sender, app_data):
        if dpg.does_item_exist("issue_report_dialog"):
            return

        key = app_data
        # Project picker: suppress all shortcuts while it is open (so typing a
        # project name into the inline rename field doesn't trigger them). Enter
        # launches the highlighted project, except while an inline rename/delete
        # prompt is active.
        if self.gui and self.gui.project_picker_visible():
            if (key == dpg.mvKey_Return
                    and not self.gui.project_picker_inline_active()):
                sel = self.gui.project_picker_selection()
                if sel:
                    self.gui.hide_project_picker()
                    self.configs._cb_project_launch(sel)
            return
        ctrl_down = dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)
        shift_down = dpg.is_key_down(dpg.mvKey_LShift) or dpg.is_key_down(dpg.mvKey_RShift)
        if key == dpg.mvKey_E and ctrl_down and shift_down:
            if self.gui:
                self.gui.set_expert_mode(not self.gui.expert_mode)
                print(f"Expert mode: {'ON' if self.gui.expert_mode else 'OFF'}")
        elif key == dpg.mvKey_E and not ctrl_down:
            self.settings.enhance_enabled = not self.settings.enhance_enabled
            self.gui and self.gui.sync_checkbox("enhance", self.settings.enhance_enabled)
            print(f"Enhancement: {'ON' if self.settings.enhance_enabled else 'OFF'}")
        elif key == dpg.mvKey_T:
            self.show_trails = not self.show_trails
            self.gui and self.gui.sync_checkbox("trails", self.show_trails)
            print(f"Trails: {'ON' if self.show_trails else 'OFF'}")
        elif key == dpg.mvKey_S and not (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)):
            self.show_skeleton = not self.show_skeleton
            self.gui and self.gui.sync_checkbox("skeleton", self.show_skeleton)
            print(f"Skeleton: {'ON' if self.show_skeleton else 'OFF'}")
        elif key == dpg.mvKey_K:
            self.show_keypoints = not self.show_keypoints
            self.gui and self.gui.sync_checkbox("keypoints", self.show_keypoints)
            print(f"Keypoints: {'ON' if self.show_keypoints else 'OFF'}")
        elif key == dpg.mvKey_B:
            self.show_bbox = not self.show_bbox
            self.gui and self.gui.sync_checkbox("bbox", self.show_bbox)
            print(f"Bounding box: {'ON' if self.show_bbox else 'OFF'}")
        elif key == dpg.mvKey_I:
            self.show_ids = not self.show_ids
            self.gui and self.gui.sync_checkbox("ids", self.show_ids)
            print(f"IDs: {'ON' if self.show_ids else 'OFF'}")
        elif key == dpg.mvKey_P:
            self.preview_enabled = not self.preview_enabled
            self.gui and self.gui.sync_checkbox("preview", self.preview_enabled)
            print(f"Preview: {'ON' if self.preview_enabled else 'OFF (measure raw FPS)'}")
        elif key == dpg.mvKey_F8:
            # Pause playback (only if playing, never resume)
            if self.recorder.is_playing and not self.recorder.is_paused():
                self.recorder.pause_playback()
                self.tracker.logger.flush()
                self.recording._update_recording_ui()
            context = self._cb_report_issue_request()
            if context and self.gui:
                self.gui.show_issue_report_dialog(context)
        if key == dpg.mvKey_S and (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)):
            self.configs._cb_save_config()

    # ------------------------------------------------------------------
    # Model Loading with Progress
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self):
        print("Detecting cameras...")
        self.cameras._do_camera_refresh()

        print(f"Opening camera {self.camera.state.source}...")
        if not self.cameras._attempt_camera_connect(self.camera.state.source):
            print(f"Warning: Camera {self.camera.state.source} not available, app will start without camera")

        print("Initializing GUI...")
        self.gui = WallDanceGUI(config=self._get_gui_config(), callbacks=self._get_gui_callbacks())
        dpi_scale = get_display_scale()
        # Fixed viewport size – layout engine fits the preview to whatever space is available
        window_width = int(1340 * dpi_scale)
        window_height = int(850 * dpi_scale)
        self.gui.setup(width=window_width, height=window_height)
        with dpg.handler_registry():
            dpg.add_key_press_handler(callback=self._handle_key)
            dpg.add_mouse_down_handler(callback=self._handle_roi_mouse_down)
            dpg.add_mouse_move_handler(callback=self._handle_roi_mouse_move)
            dpg.add_mouse_release_handler(callback=self._handle_roi_mouse_up)
            dpg.add_mouse_down_handler(callback=self._handle_mask_mouse_down)
            dpg.add_mouse_move_handler(callback=self._handle_mask_mouse_move)
            dpg.add_mouse_release_handler(callback=self._handle_mask_mouse_up)
            dpg.add_mouse_double_click_handler(callback=self._handle_preview_double_click)
        dpg.show_viewport()
        self._sync_roi_ui()
        
        # Project startup (unified switch path). A deliberate picker (ROADMAP §7B)
        # unless a CLI override (--project / config path), the kiosk env var, or
        # the config flag says to auto-load the last project.
        last_project = self.configs.config_store.read_last_project()
        startup_config = None
        if self._startup_review.config_path:
            startup_config = os.path.abspath(self._startup_review.config_path)
        elif self._startup_review.project:
            startup_config = self.configs.config_store.latest_for_project(
                sanitize_project_name(self._startup_review.project)
            )
        cli_override = startup_config is not None
        autolaunch_env = os.environ.get(
            "WALLDANCE_AUTOLAUNCH_LAST", "").lower() in ("1", "true", "yes")
        show_picker = PROJECT_PICKER_ON_START and not cli_override and not autolaunch_env

        if show_picker and self.configs.config_store.list_projects():
            # Nothing loads until the operator picks (or "Start blank"); the
            # picker queues a project switch handled by the main loop.
            print("Showing startup project picker")
            self.configs._show_startup_project_picker()
        else:
            if startup_config is None and last_project:
                startup_config = self.configs.config_store.latest_for_project(last_project)
            if startup_config:
                print(f"Loading project: {last_project or os.path.basename(startup_config)}")
                if not self.configs._execute_project_switch(startup_config):
                    print("ERROR: Failed to load project. Exiting.")
                    return
            elif not self.models._load_default_model_startup():
                print("ERROR: Failed to load model. Exiting.")
                return

        # Initialize recording UI
        self.recorder.set_project(self.configs._current_project)
        self.recording._update_recording_ui()
        self.recording._apply_startup_review_mode()

        # Show CPU fallback badge immediately if GPU is not available
        if self.gui and self.processor:
            self.gui.update_compute_mode_badge(self.processor.gpu_fallback_reason or "")
            if self.processor.gpu_fallback_reason:
                self.gui.show_toast(
                    "/!\\ Running on CPU - no GPU acceleration",
                    duration=6.0,
                    color=(255, 120, 120),
                )

        # Smartphone monitor (focus + IR-lighting assist). Best-effort: a
        # failure here must never stop the app. See docs/ROADMAP.md (P0).
        if WEB_MONITOR_ENABLED:
            try:
                self._web_monitor = WebMonitor(
                    port=WEB_MONITOR_PORT,
                    host=WEB_MONITOR_HOST,
                    jpeg_quality=WEB_MONITOR_JPEG_QUALITY,
                    max_fps=WEB_MONITOR_MAX_FPS,
                )
                if not self._web_monitor.start():
                    self._web_monitor = None
            except Exception as e:  # noqa: BLE001 - monitor is non-critical
                print(f"[WebMonitor] disabled (startup error): {e}")
                self._web_monitor = None

        print("Starting main loop...")
        self.running = True
        self._watchdog.start()
        rec_ui_update_counter = 0
        while self.running and dpg.is_dearpygui_running():
            self._ops_heartbeat()
            # Handle pending project switch (deferred from callback)
            # This is the unified path for project/config switching
            if self.configs._pending_project_switch is not None:
                config_filepath = self.configs._pending_project_switch
                self.configs._pending_project_switch = None
                self.configs._execute_project_switch(config_filepath)
                continue  # Restart loop after switch

            # Handle pending playback events (deferred from decoder thread)
            pending_playback_event = self.recording._drain_pending_playback_event()
            if pending_playback_event is not None:
                self.recording._handle_playback_start_event(pending_playback_event)
                continue  # Restart loop after tracker/session reset
            
            # Handle pending camera refresh (deferred from callback)
            if self.cameras._pending_camera_refresh:
                self.cameras._pending_camera_refresh = False
                self.cameras._do_camera_refresh()
                continue  # Restart loop after refresh

            if (
                not self.recorder.is_playing
                and not self.camera.state.is_open
                and self.cameras._next_camera_retry_time > 0.0
                and time.perf_counter() >= self.cameras._next_camera_retry_time
            ):
                self.cameras._attempt_camera_connect(self.camera.state.source)
                continue
            
            # Handle pending TRT build request (user clicked TRT checkbox, engine doesn't exist)
            if self.models._drain_pending_trt_build():
                continue  # Restart loop after build prompt

            # Handle pending model switch (deferred from callback to avoid race condition)
            if self.models._drain_pending_model_switch():
                continue  # Restart loop after model switch
            
            # Skip processing while model is loading/switching
            if self.models._model_loading or self.recording.source_transitioning:
                dpg.render_dearpygui_frame()
                time.sleep(0.016)  # ~60 FPS UI update
                continue

            self._poll_roi_mouse_interaction()
            self._update_roi_drag_from_mouse()
            self._poll_mask_mouse_interaction()
                
            if self._pending_preview_resize and self.gui:
                self.gui.resize_preview(self.preview.width, self.preview.height)
                self._pending_preview_resize = False

            # Process layout changes (viewport resize or camera dimension change)
            if self.gui and self.gui._layout_dirty:
                self.gui._layout_dirty = False
                new_scale = self.gui._fitted_render_scale
                cam_w = self.gui._camera_width or CAMERA_WIDTH
                cam_h = self.gui._camera_height or CAMERA_HEIGHT
                self.preview.render_scale = new_scale
                self.preview.width = max(1, int(cam_w * new_scale))
                self.preview.height = max(1, int(cam_h * new_scale))
                if self.processor:
                    self.processor.set_preview_size(self.preview.width, self.preview.height)
                self._pending_preview_resize = True

            # Get frame from appropriate source
            frame = None
            gpu_tensor = None  # GPU tensor path for IDS camera, None for playback/OpenCV
            camera_read_ms = 0.0
            preview_source_frame = None
            
            if self.recorder.is_playing:
                # Read from video file
                frame = self.recorder.read_frame()
                if frame is not None:
                    preview_source_frame = frame
                if frame is None:
                    # No new frame yet — decoder paces at video FPS, so we
                    # wait briefly to avoid spinning and re-processing the
                    # same frame (which would speed up playback and waste GPU).
                    if self.recorder.is_playback_active:
                        if self.gui:
                            self.gui.render_frame()
                        time.sleep(0.005)
                        continue
                    # Decoder thread exited — playback truly ended
                    self.recorder.go_live()
                    # Restart IDS acquisition (was stopped when playback started)
                    if self._use_unified_camera and self.unified_camera is not None:
                        self.unified_camera.start_acquisition()
                    if self.unified_camera and self.unified_camera.is_open:
                        self.gui.set_camera_dimensions(self.unified_camera.width, self.unified_camera.height)
                    self.recording._update_recording_ui()
                    continue
            else:
                # Read from camera - with safety checks for sudden disconnection
                # Use UnifiedCamera if available (supports IDS + OpenCV)
                if self._use_unified_camera and self.unified_camera is not None:
                    camera_ready = self.unified_camera.is_open and not self.unified_camera.has_error()
                else:
                    try:
                        camera_ready = self.camera.state.is_open and not self.camera.has_capture_error()
                    except Exception:
                        camera_ready = False
                
                if not camera_ready:
                    if self.camera.state.is_open:
                        # Capture/acquisition error while the camera is still
                        # marked open (e.g. the IDS acquisition thread died
                        # after 100 consecutive errors). The retry pump only
                        # runs when is_open is False, so without this the loop
                        # idles here forever with a green badge. Funnel into
                        # the existing reconnect state machine.
                        print("[Camera] Capture error while marked open - scheduling reconnect")
                        self.cameras._mark_camera_unavailable(self.camera.state.source,
                                                      close_active=True)
                        self.cameras._schedule_camera_retry(delay=0.5)
                        continue
                    if self.gui:
                        self.gui.render_frame()
                        # Still update GPU stats periodically when waiting for camera
                        self._update_gpu_stats_if_due()
                    time.sleep(0.033)
                    continue

                if self.cameras._ids_stream_timed_out():
                    print("[Camera] IDS stream stalled, reconnecting silently")
                    self.cameras._mark_camera_unavailable(self.camera.state.source, close_active=True)
                    self.cameras._schedule_camera_retry(delay=0.5)
                    continue

                # Read frame (BGR numpy or GPU tensor for IDS)
                gpu_tensor = None
                frame = None
                camera_read_ms = 0.0
                
                try:
                    _cam_t0 = time.perf_counter()
                    if self._use_unified_camera and self.unified_camera is not None:
                        if (
                            IDS_USE_GPU_DIRECT
                            and self.cameras._is_ids_camera_active()
                            and self.processor.gpu_path_active
                            and self.models._model_loaded
                        ):
                            ret, gpu_tensor = self.unified_camera.read_gpu()
                        else:
                            ret, frame = self.unified_camera.read()
                    else:
                        ret, frame = self.camera.read()
                    camera_read_ms = (time.perf_counter() - _cam_t0) * 1000.0
                except Exception as e:
                    print(f"Camera read exception: {e}")
                    ret, frame, gpu_tensor = False, None, None
                    camera_read_ms = 0.0
                
                # ret=False means camera error, ret=True with frame=None means still initializing
                if not ret:
                    print("Camera read failed, marking as unavailable")
                    self.cameras._mark_camera_unavailable(self.camera.state.source, close_active=True)
                    self.cameras._schedule_camera_retry(delay=0.5)
                    if self.gui:
                        self.gui.render_frame()
                        # Update GPU stats
                        self._update_gpu_stats_if_due()
                    continue
                
                # Camera is open but no frame yet (still initializing) - skip this iteration
                if frame is None and gpu_tensor is None:
                    self._log_runtime_diag_if_stalled(
                        camera_read_ms=camera_read_ms,
                        process_wall_ms=0.0,
                        preview_new=False,
                        frame_available=False,
                        gpu_tensor_available=False,
                        camera_waiting=True,
                    )
                    if self.gui:
                        self.gui.render_frame()
                        # Update GPU stats periodically
                        self._update_gpu_stats_if_due()
                    time.sleep(0.01)
                    continue

                if frame is not None:
                    preview_source_frame = frame
                elif gpu_tensor is not None and self.unified_camera is not None:
                    preview_source_frame = self.unified_camera.get_last_cpu_frame()

                # Input FPS cap: wait if too soon since last processed frame
                if self.input_fps_cap and (frame is not None or gpu_tensor is not None):
                    now = time.perf_counter()
                    elapsed = now - self._last_input_frame_time
                    if elapsed < self._input_fps_cap_interval:
                        remaining = self._input_fps_cap_interval - elapsed
                        if self.gui:
                            self.gui.render_frame()
                        time.sleep(remaining)
                    self._last_input_frame_time = time.perf_counter()

                # Update frame acquisition timestamp for stall detection.
                # Must happen here (not just in diag callback) because the diag
                # heartbeat may fire from a "waiting" iteration where frame=None.
                self._last_fresh_frame_time = time.time()
                
                # Recording is handled via camera callback thread - no write_frame here

            if preview_source_frame is not None:
                src_h, src_w = preview_source_frame.shape[:2]
                if (src_w, src_h) != self._roi_source_size:
                    self._clamp_roi_to_source(src_w, src_h, sync_ui=True)
            elif gpu_tensor is not None:
                _, _, src_h, src_w = gpu_tensor.shape
                if (src_w, src_h) != self._roi_source_size:
                    self._clamp_roi_to_source(src_w, src_h, sync_ui=True)

            # Stash raw frame for BG capture (before any processing)
            # Works for both camera and playback sources
            if frame is not None:
                self._last_raw_frame = frame

            should_process = True

            # Skip YOLO inference if model not loaded
            if not self.models._model_loaded or self.models.model is None:
                should_process = False
            
            # Skip YOLO inference if not in RUN state (Phase 3 gating).
            # Exception: a scene calibration forces YOLO on (even in Standby /
            # during playback) so it can measure detection heights.
            if (self.gui and self.gui.get_system_state() != SystemState.RUN
                    and not self.calibration._calibrating and not self.calibration._calibrating2):
                should_process = False

            # Phase 0: compute display frame number for tracker logging
            # (set outside should_process so overlay works even in STANDBY)
            if self.recorder.is_playing:
                _display_frame_num = self.recorder.status.playback_frame
            else:
                self._total_frame_count += 1
                _display_frame_num = self._total_frame_count
                
            if should_process:
                process_wall_ms = 0.0

                try:
                    _proc_t0 = time.perf_counter()
                    _need_preview = self.preview_enabled and (self.frame_count % self.preview_stride == 0)
                    if gpu_tensor is not None:
                        # Pass cached CPU frame for MOG2 motion detection
                        _raw_frame = None
                        if self.unified_camera is not None:
                            _raw_frame = self.unified_camera.get_last_cpu_frame()
                        tracked, display_frame, timing, latency_ms = self.processor.process_gpu_direct(
                            gpu_tensor, need_preview=_need_preview, frame_number=_display_frame_num,
                            raw_frame=_raw_frame
                        )
                    elif frame is not None:
                        tracked, display_frame, timing, latency_ms = self.processor.process(
                            frame, need_preview=_need_preview, frame_number=_display_frame_num
                        )
                    else:
                        time.sleep(0.001)
                        continue
                    process_wall_ms = (time.perf_counter() - _proc_t0) * 1000.0
                except AssertionError as exc:
                    if self.models.model_manager.is_using_tensorrt() and self.models._is_trt_input_size_mismatch_error(exc):
                        base_name = self.models.current_model_name
                        print(f"[TRT] Detected engine/input size mismatch during switch: {exc}")
                        print(f"[TRT] Queuing safe reload for {base_name}@{self.settings.imgsz}...")
                        if self.models.model_manager.engine_exists(base_name):
                            self.models._pending_trt_switch = True
                        else:
                            self.models._pending_trt_switch = False
                            if self.gui:
                                self.gui.set_trt_checkbox(False)
                                self.gui.show_toast(
                                    f"No TRT for {self.settings.imgsz}px, using PyTorch",
                                    duration=3.0,
                                    color=(255, 200, 100),
                                )
                        self.models._pending_model_switch = base_name
                        self.models._model_loading = True
                        self.models._model_loaded = False
                        time.sleep(0.01)
                        continue
                    raise
                self.last_tracked = tracked
                if display_frame is not None:
                    self._last_review_frame = display_frame.copy()
                elif preview_source_frame is not None:
                    self._last_review_frame = preview_source_frame.copy()
                self.timing = timing
                self.timing["camera_read"] = camera_read_ms
                self.timing["process_wall"] = process_wall_ms
                self.latency_ms = latency_ms
                if self.calibration._calibrating:
                    self.calibration._step_calibration(tracked, process_wall_ms)
                elif self.calibration._calibrating2:
                    self.calibration._step_calib2(tracked, process_wall_ms)
            else:
                process_wall_ms = 0.0
                # No processing (STANDBY mode) - show preview without YOLO.
                # For IDS GPU-direct: use cached CPU frame instead of expensive
                # GPU→CPU download which causes PCIe/USB3 contention.
                if gpu_tensor is not None:
                    # Use CPU-cached frame from read_gpu() — zero GPU download
                    if self.unified_camera is not None:
                        cached = self.unified_camera.get_last_cpu_frame()
                        if cached is not None:
                            display_frame = cached
                        else:
                            display_frame = None
                    else:
                        display_frame = None
                elif frame is not None:
                    # Apply BG subtraction in STANDBY mode too (for preview)
                    standby_frame = frame
                    if (self.settings.bg_subtract_enabled and 
                            self.processor.bg_subtractor.has_reference):
                        standby_frame = self.processor.bg_subtractor.apply_cpu(
                            frame, self.settings.bg_subtract_sensitivity
                        )
                    
                    should_enhance = self.settings.enhance_enabled
                    if should_enhance and not self.settings.enhance_lite and not self.settings.enhance_force:
                        # Check brightness threshold (same logic as pipeline)
                        gray = cv2.cvtColor(standby_frame, cv2.COLOR_BGR2GRAY)
                        brightness = float(np.mean(gray))
                        if brightness >= self.settings.brightness_threshold:
                            should_enhance = False
                    
                    if should_enhance:
                        if self.settings.enhance_lite:
                            display_frame = self.enhancer.enhance_simple(standby_frame)
                        else:
                            display_frame, _ = self.enhancer.enhance(standby_frame)
                    else:
                        display_frame = standby_frame.copy()
                else:
                    display_frame = None
                tracked = self.last_tracked

            if self.preview_enabled and (self.frame_count % self.preview_stride == 0):
                timing = dict(self.timing) if self.timing else {}
                
                # Determine whether a fresh preview frame is available.
                # GPU pipeline sets timing['preview_new'] based on its rate limiter.
                # For CPU pipeline or when no timing, fall back to FPS cap check.
                if 'preview_new' in timing:
                    preview_new = timing['preview_new']
                elif self.preview_fps_cap:
                    now_pv = time.time()
                    preview_new = (now_pv - self._last_preview_upload_time) >= 0.1
                else:
                    preview_new = True

                preview_input_available = display_frame is not None or (
                    (not self.settings.roi_enabled) and preview_source_frame is not None
                )
                if preview_new and preview_input_available:
                    render_w, render_h = self.preview.width, self.preview.height

                    if self.settings.roi_enabled and preview_source_frame is not None:
                        src_h, src_w = preview_source_frame.shape[:2]
                    elif self.settings.roi_enabled:
                        src_w, src_h = self._roi_source_size
                    elif 'original_w' in timing and 'original_h' in timing:
                        src_w = int(timing['original_w'])
                        src_h = int(timing['original_h'])
                    else:
                        preview_base = display_frame if display_frame is not None else preview_source_frame
                        if preview_base is None:
                            continue
                        src_h, src_w = preview_base.shape[:2]

                    if self.settings.roi_enabled:
                        preview_base = self._compose_roi_preview(display_frame, src_w, src_h)
                    else:
                        preview_base = display_frame if display_frame is not None else preview_source_frame

                    if preview_base is None:
                        continue

                    # Resize preview source to render target
                    dh, dw = preview_base.shape[:2]
                    if dw == render_w and dh == render_h:
                        preview_frame = np.ascontiguousarray(preview_base)
                    else:
                        preview_frame = cv2.resize(preview_base, (render_w, render_h))

                    # Push the CLEAN preview (pre-overlay) to the smartphone
                    # monitor so focus/lighting metrics are not polluted by the
                    # skeleton/bbox overlays. update_frame() copies internally.
                    if self._web_monitor is not None:
                        self._web_monitor.update_frame(preview_frame)

                    scale_x = render_w / src_w if src_w > 0 else 1.0
                    scale_y = render_h / src_h if src_h > 0 else 1.0

                    if scale_x != 1.0 or scale_y != 1.0:
                        scaled_tracks = []
                        for track in tracked:
                            scaled_tracks.append(
                                ScaledTrack(
                                    track_id=track.track_id,
                                    keypoints=track.keypoints * np.array([scale_x, scale_y]),
                                    confidence=track.confidence,
                                    bbox=track.bbox * np.array([scale_x, scale_y, scale_x, scale_y]),
                                    history=[pt * np.array([scale_x, scale_y]) for pt in track.history],
                                    velocity=track.velocity * np.array([scale_x, scale_y]),
                                )
                            )
                        thickness_scale = min(scale_x, scale_y)
                        ruler_scale = scale_x
                    else:
                        scaled_tracks = tracked
                        thickness_scale = 1.0
                        ruler_scale = 1.0

                    preview_t0 = time.time()
                    if self.settings.roi_enabled:
                        self._draw_roi_mask(preview_frame, src_w, src_h)
                    self._draw_exclusion_overlay(preview_frame, src_w, src_h)
                    for track in scaled_tracks:
                        draw_dancer(
                            preview_frame,
                            track,
                            show_skeleton=self.show_skeleton,
                            show_keypoints=self.show_keypoints,
                            show_bbox=self.show_bbox,
                            show_trail=self.show_trails,
                            show_id=self.show_ids,
                            thickness_scale=thickness_scale,
                        )
                    self._draw_height_ruler(preview_frame, scale=ruler_scale, thickness_scale=thickness_scale)
                    # Phase 0: frame number overlay (top-right)
                    self._draw_frame_number_overlay(preview_frame, _display_frame_num)
                    if self.settings.roi_enabled:
                        self._draw_roi_note(preview_frame, src_w, src_h)
                    self._last_review_frame = preview_frame.copy()
                    preview_draw_ms = (time.time() - preview_t0) * 1000
                    upload_t0 = time.time()
                    self.gui.update_frame(preview_frame)
                    preview_upload_ms = (time.time() - upload_t0) * 1000
                    self._last_preview_upload_time = time.time()
                    timing["preview_draw"] = preview_draw_ms
                    timing["preview_upload"] = preview_upload_ms
                    self.timing = timing
                    self._log_timing_spikes_if_any(self.timing)
                    self._log_runtime_diag_if_stalled(
                        camera_read_ms=camera_read_ms if 'camera_read_ms' in locals() else 0.0,
                        process_wall_ms=process_wall_ms if 'process_wall_ms' in locals() else 0.0,
                        preview_new=bool(preview_new),
                        frame_available=display_frame is not None,
                        gpu_tensor_available=gpu_tensor is not None,
                        camera_waiting=False,
                    )
                else:
                    # No fresh preview — still log diagnostics
                    self._log_runtime_diag_if_stalled(
                        camera_read_ms=camera_read_ms if 'camera_read_ms' in locals() else 0.0,
                        process_wall_ms=process_wall_ms if 'process_wall_ms' in locals() else 0.0,
                        preview_new=False,
                        frame_available=(frame is not None or display_frame is not None),
                        gpu_tensor_available=gpu_tensor is not None,
                        camera_waiting=False,
                    )
            else:
                if self.timing:
                    self.timing["preview_draw"] = 0.0
                    self.timing["preview_upload"] = 0.0
                    self._log_timing_spikes_if_any(self.timing)
                    self._log_runtime_diag_if_stalled(
                        camera_read_ms=camera_read_ms if 'camera_read_ms' in locals() else 0.0,
                        process_wall_ms=process_wall_ms if 'process_wall_ms' in locals() else 0.0,
                        preview_new=False,
                        frame_available=frame is not None,
                        gpu_tensor_available=gpu_tensor is not None,
                        camera_waiting=False,
                    )

            self.frame_count += 1
            now = time.time()
            if now - self.last_fps_time >= 1.0:
                self.fps = self.frame_count / (now - self.last_fps_time)
                self.frame_count = 0
                self.last_fps_time = now
            self._update_gpu_stats_if_due(now)

            # Get brightness from pipeline timing (already calculated there)
            # Fall back to enhancer status if not available
            brightness = self.timing.get("brightness", 0)
            if brightness == 0:
                brightness = self.enhancer.get_status().get("brightness", 0)
            enhance_bypassed = (
                self.settings.enhance_enabled
                and not self.settings.enhance_lite
                and brightness >= self.settings.brightness_threshold
            )
            _stats_t0 = time.perf_counter()
            self.gui.update_stats(
                fps=self.fps,
                num_dancers=len(tracked),
                latency_ms=self.latency_ms,
                brightness=brightness,
                timing=self.timing,
                input_res=(self.camera.state.width, self.camera.state.height),
                preview_tex=(self.preview.width, self.preview.height),
                model_name=self.models.current_model_name,
                yolo_imgsz=self.settings.imgsz,
                preview_enabled=self.preview_enabled,
                preview_render_scale=self.preview.render_scale,
                osc_enabled=self.osc_enabled,
                osc_ip=self.osc_ip,
                osc_port=self.osc_port,
                camera_running=self.camera.state.is_open,
                camera_reconnecting=self.cameras._camera_reconnecting,
                camera_type=self.gui.config.get('camera_type', ''),
                enhance_bypassed=enhance_bypassed,
                gpu_fallback_reason=self.processor.gpu_fallback_reason or "",
            )
            _gui_stats_ms = (time.perf_counter() - _stats_t0) * 1000.0
            
            # Update BG subtraction status (piggyback on stats update cycle)
            bg = self.processor.bg_subtractor
            if bg.has_reference:
                fg_ratio = self.timing.get("bg_fg_ratio", bg.foreground_ratio)
                is_mismatched = self.timing.get("bg_mismatched", bg.is_mismatched)
                self.gui.update_bg_status(
                    True, self.settings.bg_subtract_enabled,
                    fg_ratio, is_mismatched
                )
            
            # Update recording UI periodically (every 10 frames to avoid overhead)
            rec_ui_update_counter += 1
            if rec_ui_update_counter >= 10:
                rec_ui_update_counter = 0
                self.recording._update_recording_ui()
            self.recording._maybe_pause_at_target_frame()
            
            _dpg_t0 = time.perf_counter()
            dpg.render_dearpygui_frame()
            _dpg_render_ms = (time.perf_counter() - _dpg_t0) * 1000.0

            # Inject GUI overhead into timing dict for spike logging
            if self.timing:
                self.timing["dpg_render"] = _dpg_render_ms
                self.timing["gui_stats"] = _gui_stats_ms
                if 'camera_read_ms' not in self.timing:
                    self.timing["camera_read"] = camera_read_ms if 'camera_read_ms' in dir() else 0.0

        self._watchdog.stop()
        if self._web_monitor is not None:
            self._web_monitor.stop()
            self._web_monitor = None
        self.recorder.close()
        if self.camera.cap is not None:
            self.camera.cap.release()
        dpg.destroy_context()
        print("WallDance stopped.")


def main():
    parser = argparse.ArgumentParser(description="WallDance")
    parser.add_argument("--project", help="Load the latest config for this project at startup")
    parser.add_argument("--config", help="Load a specific config file at startup")
    parser.add_argument("--slot", type=int, help="Start playback from the given recording slot")
    parser.add_argument(
        "--recording-index",
        type=int,
        default=0,
        help="Playback history index for the chosen slot (0 = latest)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Initial playback speed for startup review mode",
    )
    parser.add_argument(
        "--paused",
        action="store_true",
        help="Start playback paused",
    )
    parser.add_argument(
        "--play-at-frame",
        type=int,
        help="Seek to this frame immediately after playback starts",
    )
    parser.add_argument(
        "--pause-at-frame",
        type=int,
        help="Automatically pause playback when this frame is reached",
    )
    args = parser.parse_args()

    startup_review = ReviewStartupOptions(
        config_path=args.config,
        project=args.project,
        slot=args.slot,
        recording_index=max(0, args.recording_index),
        playback_speed=max(0.1, args.speed),
        paused=args.paused,
        play_at_frame=args.play_at_frame,
        pause_at_frame=args.pause_at_frame,
    )

    app = WallDanceApp(startup_review=startup_review)
    app.run()


if __name__ == "__main__":
    main()
