"""
High-level application orchestration for WallDance.
This module keeps the runtime glue small by delegating to:
- CameraManager (camera lifecycle)
- FrameProcessor (enhance → YOLO → tracking → OSC)
- ConfigStore (save/load presets)
- DpgUiAdapter (the DearPyGui client behind the command/event seam)
- ModelManager (model loading, TensorRT export)

DECOMPOSITION_PLAN Phase 3: app.py is the composition root. It never touches
dpg directly -- GUI pushes go out as events on the EventBus, GUI input comes
back as commands drained once per main-loop tick (runtime/api.py); the only
dpg-speaking module is ui/adapter.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import cv2
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
    MOTION_BRIDGE_SENSITIVITY,
    PREVIEW_ENABLED,
    PREVIEW_RENDER_SCALE,
    SHOW_BBOX,
    SHOW_ID,
    SHOW_KEYPOINTS,
    SHOW_SKELETON,
    SHOW_TRAILS,
    TRACKER_MAX_AGE,
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
)
from osc_output import OSCSender
from pipeline import FrameProcessor, ProcessingSettings, ScaledTrack
from gui import get_display_scale, get_gpu_stats
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
from enhancer import ImageEnhancer
from tracker import DancerTracker
from tracking_logger import _json_default
from video_recorder import VideoRecorder
from runtime import api
from runtime.api import SystemState
from runtime.main_loop import MainLoop
from runtime.recording_controller import RecordingController
from runtime.model_controller import ModelController
from runtime.camera_controller import CameraController
from runtime.config_manager import ConfigManager
from runtime.calibration_flows import CalibrationFlows
from runtime.roi_state import RoiState
from ui.adapter import DpgUiAdapter
from ui.roi_mask_editor import RoiMaskEditor
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
    """CalibrationUiPort publishing seam events; available mirrors GUI existence."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.bus.ui_ready

    def set_calibrate_status(self, text):
        self._app.bus.publish(api.CalibProgress(text))

    def show_toast(self, message: str, duration: float, color):
        self._app.bus.publish(api.Toast(message, duration, color))

    def sync_slider(self, name: str, value):
        self._app.bus.publish(api.ControlSync("slider", name, value))

    def sync_combo(self, name: str, value: str):
        self._app.bus.publish(api.ControlSync("combo", name, value))

    def show_calibration_result_dialog(self, summary: str, on_save):
        # on_save is always configs._cb_save_config (both flows); the adapter
        # wires the dialog's "Save to project" to a SaveConfig command, so the
        # event stays pure data (tablet-transportable, §4).
        self._app.bus.publish(api.CalibReportCard(summary))

    def show_calib2_dialog(self, rows, proposal: str):
        self._app.bus.publish(api.Calib2PoolChanged(rows, proposal))


class _ConfigUiAdapter:
    """ConfigUiPort publishing seam events; available mirrors GUI existence."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.bus.ui_ready

    def update_project_list(self, projects, current):
        self._app.bus.publish(api.ProjectList(projects, current))

    def update_config_list(self, configs, current_display):
        self._app.bus.publish(api.ConfigList(configs, current_display))

    def set_current_config(self, display: str):
        self._app.bus.publish(api.CurrentConfig(display))

    def show_save_config_dialog(self, project: str):
        self._app.bus.publish(api.SaveConfigDialog(project))

    def show_load_config_dialog(self, config_dir: str, project: str):
        self._app.bus.publish(api.LoadConfigDialog(config_dir, project))

    def show_save_indicator(self, message: str):
        self._app.bus.publish(api.ConfigSaved(message))

    def show_toast(self, message: str, duration: float, color):
        self._app.bus.publish(api.Toast(message, duration, color))

    def set_active_profile(self, name: str):
        self._app.bus.publish(api.ActiveProfile(name))

    def show_project_picker(self, rows, last_project: str):
        self._app.bus.publish(api.ProjectPicker(rows, last_project))

    def sync_combo(self, name: str, value: str):
        self._app.bus.publish(api.ControlSync("combo", name, value))

    def set_trt_checkbox(self, enabled: bool):
        self._app.bus.publish(api.TrtCheckbox(enabled))

    def update_camera_sources(self, sources, current, unavailable):
        self._app.bus.publish(api.CameraSources(sources, current, unavailable))

    def update_camera_status(self, is_open: bool, source: str, reconnecting: bool):
        self._app.bus.publish(api.CameraStatus(is_open, source, reconnecting))

    def set_camera_type(self, camera_type: str):
        # Runtime keeps the authoritative copy (StatsTick reads it back).
        self._app._ui_camera_type = camera_type
        self._app.bus.publish(api.CameraType(camera_type))


class _CameraUiAdapter:
    """CameraUiPort publishing seam events; available mirrors GUI existence."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.bus.ui_ready

    def update_camera_sources(self, sources, current, unavailable):
        self._app.bus.publish(api.CameraSources(sources, current, unavailable))

    def update_camera_status(self, is_open: bool, source: str, reconnecting: bool):
        self._app.bus.publish(api.CameraStatus(is_open, source, reconnecting))

    def set_camera_type(self, camera_type: str):
        self._app._ui_camera_type = camera_type
        self._app.bus.publish(api.CameraType(camera_type))

    def set_camera_dimensions(self, width: int, height: int):
        self._app.bus.publish(api.CameraDimensions(width, height))

    def sync_checkbox(self, name: str, value: bool):
        self._app.bus.publish(api.ControlSync("checkbox", name, value))

    def sync_slider(self, name: str, value: float):
        self._app.bus.publish(api.ControlSync("slider", name, value))


class _ModelUiAdapter:
    """ModelUiPort publishing seam events; available mirrors GUI existence.

    Two synchronous exceptions stay direct calls into the DPG adapter (see
    runtime/api.py): the blocking TRT prompt and the render pump the model
    controller spins while a load/modal is in flight.
    """

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.bus.ui_ready

    def show_model_loading_modal(self, message: str):
        self._app.bus.publish(api.ModelLoadModal(message))

    def update_model_loading_progress(self, message: str, progress: float,
                                      detail: str, animate: bool = False):
        self._app.bus.publish(api.ModelLoadProgress(message, progress, detail, animate))

    def hide_model_loading_modal(self):
        self._app.bus.publish(api.ModelLoadModalHide())

    def update_engine_type_badge(self, is_trt: bool):
        self._app.bus.publish(api.EngineBadge(is_trt))

    def show_toast(self, message: str, duration: float, color):
        self._app.bus.publish(api.Toast(message, duration, color))

    def set_trt_checkbox(self, enabled: bool):
        self._app.bus.publish(api.TrtCheckbox(enabled))

    def sync_model_combo(self, name: str):
        self._app.bus.publish(api.ControlSync("combo", "model", name))

    def update_model_dropdown(self, name: str):
        self._app.bus.publish(api.ModelDropdown(name))

    def update_trt_banner(self, text, exporting: bool = False):
        self._app.bus.publish(api.TrtBanner(text, exporting))

    def show_tensorrt_prompt(self, model_name: str, on_choice):
        self._app.ui.show_tensorrt_prompt(model_name, on_choice)

    def update_gpu_stats(self):
        self._app.bus.publish(api.GpuStats())

    def render_frame(self):
        self._app.ui.render_frame_raw()


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
    """RecordingUiPort publishing seam events (the adapter drops them
    pre-GUI, preserving the old None-safety)."""

    def __init__(self, app: "WallDanceApp"):
        self._app = app

    @property
    def available(self) -> bool:
        return self._app.bus.ui_ready

    def update_recording_ui(self, **kwargs):
        self._app.bus.publish(api.RecordingUi(kwargs))

    def set_camera_dimensions(self, width: int, height: int):
        self._app.bus.publish(api.CameraDimensions(width, height))

    def show_toast(self, message: str, duration: float, color):
        self._app.bus.publish(api.Toast(message, duration, color))

    def show_slot_history_menu(self, slot, recordings, on_pick):
        # on_pick (a _play_recording closure) is intentionally unused: the
        # menu answers with a PlaySlotRecording command, whose handler routes
        # to the same controller method on the main loop.
        self._app.bus.publish(api.SlotHistory(slot, recordings))


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

        # The command/event seam (DECOMPOSITION_PLAN Phase 3): commands are
        # queued by UI clients and drained once per main-loop tick; events
        # fan out to subscribers (the DPG adapter is subscriber #1).
        self.bus = api.EventBus()
        self.api = api.RuntimeAPI()
        self.ui = DpgUiAdapter(self.api, self.bus)
        # Runtime-owned mirrors of state the GUI used to be queried for.
        # The GUI starts in RUN (gui.py __init__) -- mirror that.
        self.system_state = SystemState.RUN
        self._ui_camera_type = ""

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

        # Video recording
        self.recorder = VideoRecorder()

        # Preview/display state
        self.preview_enabled = PREVIEW_ENABLED
        self.preview_fps_cap = False
        self.input_fps_cap = False
        self._input_fps_cap_interval = 1.0 / 20.0  # 20 FPS = 50ms
        self.preview_stride = 1
        self.preview = PreviewGeometry(
            render_scale=PREVIEW_RENDER_SCALE,
            width=int(CAMERA_WIDTH * PREVIEW_RENDER_SCALE),
            height=int(CAMERA_HEIGHT * PREVIEW_RENDER_SCALE),
        )
        self._pending_preview_resize = False

        # ROI/mask editor (DECOMPOSITION_PLAN Phase 2 (6)): mouse/drag/paint
        # state + preview compose in ui/roi_mask_editor.py; the runtime-side
        # ROI facts (source size, effective rect) in runtime/roi_state.py.
        self.settings.roi_x = 0
        self.settings.roi_y = 0
        self.settings.roi_w = CAMERA_WIDTH
        self.settings.roi_h = CAMERA_HEIGHT
        self.roi = RoiMaskEditor(
            state=RoiState(self.settings, (CAMERA_WIDTH, CAMERA_HEIGHT)),
            settings=self.settings,
            processor=self.processor,
            imgsz_presets=self._IMGSZ_PRESETS,
            gui=lambda: self.ui.gui,
            request_reprocess=self._request_reprocess,
        )

        # Visualization flags
        self.show_trails = SHOW_TRAILS
        self.show_skeleton = SHOW_SKELETON
        self.show_keypoints = SHOW_KEYPOINTS
        self.show_bbox = SHOW_BBOX
        self.show_ids = SHOW_ID

        # State for metrics
        self.frame_count = 0
        self.last_fps_time = time.time()
        self.fps = 0.0
        self.timing: Dict[str, float] = {}
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
            update_imgsz_roi_warning=self.roi._update_imgsz_roi_warning,
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
            roi_source_size=lambda: self.roi.state.source_size,
            get_effective_roi=self.roi.state.effective_roi,
            reset_sensitivity_anchor=self._reset_sensitivity_anchor,
            sync_mask_ui=self.roi._sync_mask_ui,
            request_reprocess=self._request_reprocess,
            imgsz_change=self._cb_imgsz_change,
        )
        
        # Pending operations (deferred to main loop)

        # Ops cluster (TODO Phase 7): health alerts + main-loop watchdog
        self._health = HealthMonitor(gpu_stats_fn=get_gpu_stats)
        self._watchdog = LoopWatchdog()

        # Wire the command vocabulary to its runtime handlers (Phase 3 seam).
        self._register_command_handlers()

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
            "roi_edit_mode": self.roi.roi_edit_mode,
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
            self.bus.publish(api.Toast(
                "Web monitor is off (set WEB_MONITOR_ENABLED=True)",
                color=(255, 180, 80)))
            return
        self.bus.publish(api.QrDialog(mon.url(), mon.qr_matrix()))

    def _register_command_handlers(self):
        """Wire every seam command to its runtime handler (the former
        callbacks-dict targets, unchanged). Executed by ``self.api.drain()``
        at one point in the main loop tick -- never on a caller thread."""
        reg = self.api.register
        # state / lifecycle
        reg(api.SetState, self._cmd_set_state)
        reg(api.Quit, lambda c: self._cb_quit())
        reg(api.ShowQr, lambda c: self._show_qr())
        # detection / sensitivity
        reg(api.SetSensitivity, lambda c: self._cb_sensitivity_change(c.value))
        reg(api.SetConfidence, lambda c: self._cb_confidence_change(c.value))
        reg(api.SetMotionSensitivity,
            lambda c: self._cb_motion_sensitivity_change(c.value))
        reg(api.SetPersonHeight, lambda c: self._cb_person_height_change(c.value))
        reg(api.SetImgsz, lambda c: self._cb_imgsz_change(c.value))
        reg(api.SetTrackerMaxAge, lambda c: self._cb_tracker_age_change(c.value))
        reg(api.SetMog2Scale, lambda c: self._cb_mog2_scale_change(c.value))
        reg(api.ResetTracker, lambda c: self._cb_tracker_reset())
        # enhancement
        reg(api.ToggleEnhance, self._cmd_toggle_enhance)
        reg(api.ToggleEnhanceLite, lambda c: self._cb_enhance_lite_toggle(c.enabled))
        reg(api.ToggleEnhanceForce, lambda c: self._cb_enhance_force_toggle(c.enabled))
        reg(api.ToggleGreyscale, lambda c: self._cb_greyscale_toggle(c.enabled))
        reg(api.SetEnhanceParam, self._cmd_set_enhance_param)
        # background subtraction
        reg(api.BgCapture, lambda c: self._cb_bg_capture())
        reg(api.BgClear, lambda c: self._cb_bg_clear())
        reg(api.ToggleBgSubtract, lambda c: self._cb_bg_enable_toggle(c.enabled))
        reg(api.SetBgSensitivity, lambda c: self._cb_bg_sensitivity_change(c.value))
        # overlays / preview
        reg(api.ToggleOverlay, self._cmd_toggle_overlay)
        reg(api.TogglePreview, self._cmd_toggle_preview)
        reg(api.ToggleInputFpsCap, lambda c: self._cb_input_fps_cap_toggle(c.enabled))
        reg(api.TogglePreviewCap, lambda c: self._cb_preview_cap_toggle(c.enabled))
        reg(api.SetPreviewScale, lambda c: self._cb_preview_scale_change(c.value))
        # ROI / mask (ui-side editor; commands route to its entry points)
        reg(api.SetRoi, lambda c: self.roi._cb_roi_toggle(c.enabled))
        reg(api.ResetRoi, lambda c: self.roi._cb_roi_reset())
        reg(api.EditMask, lambda c: self.roi._cb_mask_edit_toggle())
        reg(api.ClearMask, lambda c: self.roi._cb_mask_clear())
        # model / TRT
        reg(api.LoadModel, lambda c: self.models._cb_model_change(c.name))
        reg(api.ToggleTrt, lambda c: self.models._cb_trt_toggle(c.enabled))
        reg(api.RebuildTrt, lambda c: self.models._cb_trt_rebuild())
        # camera
        reg(api.SelectSource, lambda c: self.cameras._cb_camera_change(c.source))
        reg(api.RefreshCameras, lambda c: self.cameras._cb_camera_refresh())
        reg(api.SetIdsParam, self._cmd_set_ids_param)
        # OSC
        reg(api.ToggleOsc, lambda c: self._cb_osc_toggle(c.enabled))
        reg(api.SetOscTarget, lambda c: self._cb_osc_config(c.ip, c.port))
        # calibration
        reg(api.StartCalibration, lambda c: self.calibration._cb_calibrate())
        reg(api.StartDancersRun, lambda c: self.calibration._cb_calib2())
        reg(api.ApplyCalib2, lambda c: self.calibration._cb_calib2_apply(c.selection))
        reg(api.ClearCalib2Pool, lambda c: self.calibration._cb_calib2_clear())
        # config / project
        reg(api.SaveConfig, lambda c: (self.configs._cb_do_save_config(c.name)
                                       if c.name else self.configs._cb_save_config()))
        reg(api.SaveConfigAs, lambda c: self.configs._cb_save_as_config())
        reg(api.RequestLoadConfigDialog, lambda c: self.configs._cb_load_config())
        reg(api.LoadConfig, lambda c: self.configs._cb_do_load_config(c.filepath))
        reg(api.SelectProject, lambda c: self.configs._cb_project_select(c.name))
        reg(api.SelectConfigVersion,
            lambda c: self.configs._cb_config_select(c.project, c.display))
        reg(api.SwitchProfile, lambda c: self.configs._cb_profile_switch(c.name))
        reg(api.SaveSafeDefaults, lambda c: self.configs._cb_save_safe_defaults())
        reg(api.LoadSafeDefaults, lambda c: self.configs._cb_load_safe_defaults())
        reg(api.LaunchProject, lambda c: self.configs._cb_project_launch(c.name))
        reg(api.RenameProject, lambda c: self.configs._cb_project_rename(c.old, c.new))
        reg(api.DeleteProject, lambda c: self.configs._cb_project_delete(c.name))
        reg(api.StartBlankProject, lambda c: self.configs._cb_project_blank())
        # recording / playback
        reg(api.PlaybackControl, self._cmd_playback_control)
        reg(api.SelectSlot,
            lambda c: self.recording._cb_rec_slot_click(c.slot, c.history))
        reg(api.PlaySlotRecording,
            lambda c: self.recording._play_recording(c.slot, c.path))
        # review / misc
        reg(api.RequestIssueReport, lambda c: self._cmd_request_issue_report())
        reg(api.SubmitIssue,
            lambda c: self._cb_issue_submit(c.context, c.issue_type, c.note))
        reg(api.IssueDialogClosed, lambda c: self._cb_issue_dialog_closed())

    # --- command handlers that fan out / carry key-shortcut semantics ---

    def _cmd_set_state(self, c: api.SetState):
        """Runtime mirror of the GUI's STANDBY/RUN state (the GUI flips its
        own visuals on click; the mirror gates processing + readiness)."""
        old_state = self.system_state
        self.system_state = SystemState[c.state.upper()]
        self.bus.publish(api.StateChanged(c.state))
        self._cb_system_state_change(self.system_state, old_state)

    def _cmd_set_enhance_param(self, c: api.SetEnhanceParam):
        {
            "brightness_threshold": self._cb_brightness_threshold_change,
            "clahe": self._cb_clahe_change,
            "gamma": self._cb_gamma_change,
            "denoise": self._cb_denoise_change,
        }[c.name](c.value)

    def _cmd_set_ids_param(self, c: api.SetIdsParam):
        {
            "ratio": self.cameras._cb_ids_ratio_change,
            "gain_db": self.cameras._cb_ids_gain_change,
            "exposure_us": self.cameras._cb_ids_exposure_change,
        }[c.name](c.value)

    def _cmd_playback_control(self, c: api.PlaybackControl):
        rec = self.recording
        if c.action == "live":
            rec._cb_rec_live()
        elif c.action == "record_toggle":
            rec._cb_rec_toggle()
        elif c.action == "speed":
            rec._cb_playback_speed_change(c.value)
        elif c.action == "pause_toggle":
            rec._cb_playback_pause()
        elif c.action == "force_pause":
            rec._cb_playback_force_pause()
        elif c.action == "next_frame":
            rec._cb_playback_next_frame()
        elif c.action == "prev_frame":
            rec._cb_playback_prev_frame()

    def _cmd_toggle_enhance(self, c: api.ToggleEnhance):
        if c.enabled is None or c.quiet:
            # Keyboard path (E): flip + checkbox sync only, no reprocess.
            enabled = (not self.settings.enhance_enabled
                       if c.enabled is None else c.enabled)
            self.settings.enhance_enabled = enabled
            self.bus.publish(api.ControlSync("checkbox", "enhance", enabled))
            print(f"Enhancement: {'ON' if enabled else 'OFF'}")
        else:
            self._cb_enhance_toggle(c.enabled)

    _OVERLAY_FLAGS = {
        "skeleton": ("show_skeleton", "Skeleton"),
        "keypoints": ("show_keypoints", "Keypoints"),
        "bbox": ("show_bbox", "Bounding box"),
        "trails": ("show_trails", "Trails"),
        "ids": ("show_ids", "IDs"),
    }

    def _cmd_toggle_overlay(self, c: api.ToggleOverlay):
        if c.enabled is None:
            # Keyboard path (T/S/K/B/I): flip + checkbox sync.
            attr, label = self._OVERLAY_FLAGS[c.name]
            enabled = not getattr(self, attr)
            setattr(self, attr, enabled)
            self.bus.publish(api.ControlSync("checkbox", c.name, enabled))
            print(f"{label}: {'ON' if enabled else 'OFF'}")
        else:
            self._cb_visualization_toggle(c.name, c.enabled)

    def _cmd_toggle_preview(self, c: api.TogglePreview):
        if c.enabled is None or c.quiet:
            # Keyboard path (P): flip + checkbox sync, no toast/placeholder.
            enabled = not self.preview_enabled if c.enabled is None else c.enabled
            self.preview_enabled = enabled
            self.bus.publish(api.ControlSync("checkbox", "preview", enabled))
            print(f"Preview: {'ON' if enabled else 'OFF (measure raw FPS)'}")
        else:
            self._cb_preview_toggle(c.enabled)

    def _cmd_request_issue_report(self):
        """F8 / Report-issue button: pause (if playing), then open the dialog
        via an IssueReportContext event built from runtime state."""
        self.recording._cb_playback_force_pause()
        context = self._cb_report_issue_request()
        if context:
            self.bus.publish(api.IssueReportContext(context))

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
            "roi_source_w": self.roi.state.source_size[0],
            "roi_source_h": self.roi.state.source_size[1],
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
            self._sync("slider","confidence", config["confidence"])
        if "person_height_px" in config:
            self.settings.person_height_px = config["person_height_px"]
            self.tracker.set_person_height(config["person_height_px"])
            self._sync("slider","person_height", config["person_height_px"])
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
        self._sync("slider",'sensitivity', self.sensitivity)
        if "exclusion_cells" in config:
            grid = tuple(config.get("exclusion_grid") or AUTOCAL_EXCL_GRID)
            self.processor.set_exclusion(
                grid, config["exclusion_cells"],
                config.get("exclusion_manual_add") or (),
                config.get("exclusion_manual_remove") or ())
            self.roi._sync_mask_ui()
        if "yolo_imgsz" in config:
            # Just sync UI, don't trigger callback (imgsz already set)
            self._sync("combo","imgsz", str(config["yolo_imgsz"]))

        # Enhancement
        if "enhance_enabled" in config:
            self.settings.enhance_enabled = config["enhance_enabled"]
            self._sync("checkbox","enhance", config["enhance_enabled"])
        if "enhance_lite" in config:
            self.settings.enhance_lite = config["enhance_lite"]
            self._sync("checkbox","enhance_lite", config["enhance_lite"])
        if "enhance_force" in config:
            self.settings.enhance_force = config["enhance_force"]
            self._sync("checkbox","enhance_force", config["enhance_force"])
        if "greyscale" in config:
            self.settings.greyscale = config["greyscale"]
            self._sync("checkbox","greyscale", config["greyscale"])
        if "brightness_threshold" in config:
            self.settings.brightness_threshold = config["brightness_threshold"]
            self._sync("slider","brightness_threshold", config["brightness_threshold"])
        if "denoise_strength" in config:
            self.settings.denoise_strength = config["denoise_strength"]
            self._sync("slider","denoise", config["denoise_strength"])
        if "clahe_clip" in config:
            self.enhancer.clahe_clip = config["clahe_clip"]
            self.enhancer._update_clahe()
            self._sync("slider","clahe", config["clahe_clip"])
        if "gamma" in config:
            self.enhancer.gamma = config["gamma"]
            self.enhancer._update_gamma_lut()
            self._sync("slider","gamma", config["gamma"])

        # Visualization
        if "show_skeleton" in config:
            self.show_skeleton = config["show_skeleton"]
            self._sync("checkbox","skeleton", config["show_skeleton"])
        if "show_keypoints" in config:
            self.show_keypoints = config["show_keypoints"]
            self._sync("checkbox","keypoints", config["show_keypoints"])
        if "show_bbox" in config:
            self.show_bbox = config["show_bbox"]
            self._sync("checkbox","bbox", config["show_bbox"])
        if "show_trails" in config:
            self.show_trails = config["show_trails"]
            self._sync("checkbox","trails", config["show_trails"])
        if "show_ids" in config:
            self.show_ids = config["show_ids"]
            self._sync("checkbox","ids", config["show_ids"])

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
            self._sync("slider","tracker_max_age", config["tracker_max_age"])
        if "tracker_smoothing" in config:
            self.tracker.smoothing_depth = config["tracker_smoothing"]
            self._sync("slider","tracker_smoothing", config["tracker_smoothing"])
        if "max_persons" in config:
            self.tracker.max_persons = int(config["max_persons"])
        if "motion_sensitivity" in config:
            self.processor.set_motion_sensitivity(config["motion_sensitivity"])
            self._sync("slider","motion_sensitivity", config["motion_sensitivity"])

        # OSC
        if "osc_enabled" in config:
            self.osc_enabled = config["osc_enabled"]
            self.settings.osc_enabled = config["osc_enabled"]
            self._sync("checkbox","osc", config["osc_enabled"])
        if "osc_ip" in config:
            self.osc_ip = config["osc_ip"]
            self._sync("input","osc_ip", config["osc_ip"])
        if "osc_port" in config:
            self.osc_port = config["osc_port"]
            self._sync("input","osc_port", config["osc_port"])
        if self.osc_enabled and (config.get("osc_ip") or config.get("osc_port")):
            self._init_osc()

        # Preview
        if "preview_enabled" in config:
            self.preview_enabled = config["preview_enabled"]
            self._sync("checkbox","preview", config["preview_enabled"])
        if "preview_fps_cap" in config:
            self._cb_preview_cap_toggle(config["preview_fps_cap"])
            self._sync("checkbox","preview_cap", self.preview_fps_cap)
        if "input_fps_cap" in config:
            self.input_fps_cap = config["input_fps_cap"]
            self._sync("checkbox","input_fps_cap", self.input_fps_cap)
        if "preview_scale" in config:
            # Legacy config value – render scale is now auto-computed from layout.
            # Just store it; actual scale will be overridden by next layout recompute.
            pass

        if "roi_enabled" in config:
            self.settings.roi_enabled = bool(config["roi_enabled"])
        # Use the frame size that was active when the config was saved so
        # that _normalize_roi_rect clamps against the correct dimensions
        # (not the current _roi_source_size which may be stale/default).
        roi_frame_w = int(config.get("roi_source_w", self.roi.state.source_size[0]))
        roi_frame_h = int(config.get("roi_source_h", self.roi.state.source_size[1]))
        roi_x = int(config.get("roi_x", self.settings.roi_x))
        roi_y = int(config.get("roi_y", self.settings.roi_y))
        roi_w = int(config.get("roi_w", self.settings.roi_w or roi_frame_w))
        roi_h = int(config.get("roi_h", self.settings.roi_h or roi_frame_h))
        self.roi._set_roi_rect(
            roi_x,
            roi_y,
            roi_w,
            roi_h,
            frame_w=roi_frame_w,
            frame_h=roi_frame_h,
            sync_ui=False,
            request_reprocess=False,
        )
        self.roi.roi_edit_mode = False
        self.roi._sync_roi_ui()

        # IDS crop ratio
        if "ids_ratio" in config:
            ratio = max(0.5, min(2.0, float(config["ids_ratio"])))
            self.cameras._cb_ids_ratio_change(ratio)
            self._sync("slider","ids_ratio", ratio)

        # IDS gain
        if "ids_gain_db" in config:
            self.cameras._cb_ids_gain_change(config["ids_gain_db"])
            self._sync("slider","ids_gain_db", config["ids_gain_db"])

        # IDS exposure
        if "ids_exposure_us" in config:
            self.cameras._cb_ids_exposure_change(config["ids_exposure_us"])
            self._sync("slider","ids_exposure_us", self.cameras.ids_exposure_us)

        # Background subtraction
        if "bg_subtract_enabled" in config:
            self.settings.bg_subtract_enabled = config["bg_subtract_enabled"]
            self._sync("checkbox","bg_enable", config["bg_subtract_enabled"])
        if "bg_subtract_sensitivity" in config:
            self.settings.bg_subtract_sensitivity = config["bg_subtract_sensitivity"]
            self._sync("slider","bg_sensitivity", config["bg_subtract_sensitivity"])
        if "blur_budget_ms" in config:
            self.calibration.blur_budget_ms = float(config["blur_budget_ms"])
        # MOG2 scale
        if "mog2_scale" in config and self.processor.motion_detector is not None:
            self.processor.set_motion_scale(config["mog2_scale"])
            self._sync("slider","mog2_scale", config["mog2_scale"])
        # Update BG status display
        bg = self.processor.bg_subtractor
        self.bus.publish(api.BgStatus(bg.has_reference, self.settings.bg_subtract_enabled))

    # ------------------------------------------------------------------
    # GUI callbacks
    # ------------------------------------------------------------------
    def _sync(self, kind: str, name: str, value):
        """Push a control value to UI clients (no callback fired).

        Replaces the old guarded ``self.gui and self.gui.sync_*`` pushes; the
        adapter drops events while no GUI exists, preserving that guard."""
        self.bus.publish(api.ControlSync(kind, name, value))

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
            self._sync("checkbox", "bg_enable", True)
            self.bus.publish(api.BgStatus(True, True))
        else:
            print("[BG] No frame available for capture")

    def _cb_bg_enable_toggle(self, enabled: bool):
        self.settings.bg_subtract_enabled = enabled
        bg = self.processor.bg_subtractor
        if enabled and not bg.has_reference:
            print("[BG] Warning: enabled but no reference captured yet")
        print(f"[BG] Background subtraction: {'ON' if enabled else 'OFF'}")
        self.bus.publish(api.BgStatus(bg.has_reference, enabled))
        self._request_reprocess()

    def _cb_bg_clear(self):
        self.processor.bg_subtractor.clear()
        self.settings.bg_subtract_enabled = False
        self._sync("checkbox", "bg_enable", False)
        self.bus.publish(api.BgStatus(False, False))
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
        self._sync("slider",'sensitivity', 50.0)
        print(f"Confidence: {value:.2f}")
        self._request_reprocess()

    def _cb_sensitivity_change(self, value: float):
        """Operator macro: one dial driving confidence (+var at the loose end)."""
        self.sensitivity = float(value)
        m = macro_to_settings(value, self._sensitivity_conf_seed,
                              self._sensitivity_var_anchor)
        self.settings.confidence = m["confidence"]
        self.processor.set_motion_var_threshold(m["mog2_var_threshold"])
        self._sync("slider",'confidence', m["confidence"])
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
        self._sync("slider",'sensitivity', 50.0)

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
        self.roi._update_imgsz_roi_warning()
        roi_warning = self.roi._get_imgsz_roi_warning()
        if roi_warning:
            self.bus.publish(api.Toast("Current imgsz is below the ROI suggestion",
                                       3.0, (255, 180, 80)))
        
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
                self.bus.publish(api.TrtCheckbox(False))
                self.models._pending_trt_switch = False
                self.models._pending_model_switch = base_name
                # Block processing until model is reloaded to prevent imgsz mismatch
                self.models._model_loading = True
                self.models._model_loaded = False
                self.bus.publish(api.Toast(f"No TRT for {new_imgsz}px, using PyTorch",
                                           3.0, (255, 200, 100)))

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
        self.bus.publish(api.CameraDimensions(width, height))
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
            self.bus.publish(api.Toast(
                "Issue reporting is available during playback only",
                2.5, (255, 180, 120)))
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
            "system_state": self.system_state.name,
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
        self.bus.publish(api.Toast(
            f"Issue saved: slot {slot_num} frame {frame_num}",
            3.0, (120, 220, 140)))

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
            self.bus.publish(api.Toast("Preview ON", 2.0, (120, 220, 140)))
        else:
            print("Preview: OFF (no video output - measure raw FPS)")
            if self.bus.ui_ready:
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
                self.bus.publish(api.PreviewFrame(placeholder))
                self.bus.publish(api.Toast(
                    "Preview OFF: playback keeps running but the image will not update",
                    3.5, (255, 200, 120)))

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
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
            msg, color = report.toast_summary()
            self.bus.publish(api.Toast(msg, 6.0, color))
        except Exception as e:  # noqa: BLE001 - never disturb Go-Live
            print(f"[Readiness] check failed: {e}")

    # Keyboard shortcuts live in ui/adapter.py (_handle_key) since Phase 3:
    # runtime effects arrive as commands, GUI-local toggles stay in the adapter.

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self):
        """Run the session: startup → tick loop → shutdown.

        The whole driver lives in runtime/main_loop.py as explicit tick
        stages (DECOMPOSITION_PLAN Phase 4); WallDanceApp is the
        composition root: wiring, command handlers, lifecycle."""
        MainLoop(self).run()


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
