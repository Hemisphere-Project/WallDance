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
from typing import Deque, Dict, List, Optional

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
    MAX_PERSONS,
    MODELS_DIR,
    OSC_ENABLED,
    OSC_IP,
    OSC_PORT,
    PERSON_HEIGHT_MAX_RATIO,
    PERSON_HEIGHT_MIN_RATIO,
    PERSON_HEIGHT_PX,
    MOTION_BRIDGE_SENSITIVITY,
    PREVIEW_ENABLED,
    PREVIEW_RENDER_SCALE,
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
)
from config_store import ConfigStore, format_config_display, sanitize_project_name
from model_manager import ModelManager, ModelProgress, ModelStatus
from osc_output import OSCSender
from pipeline import FrameProcessor, ProcessingSettings, ScaledTrack
from visualization import draw_dancer
from gui import WallDanceGUI, get_display_scale
from gui_builder import SystemState
from enhancer import ImageEnhancer
from tracker import DancerTracker
from tracking_logger import _json_default
from video_recorder import VideoRecorder, RecorderState


# IDS Camera support (optional, falls back to OpenCV)
try:
    from ids_camera import (
        UnifiedCamera,
        IDSCamera,
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


class WallDanceApp:
    """Main application orchestrator."""

    _IMGSZ_PRESETS = (640, 800, 960, 1280, 1536, 1920)

    def __init__(self, startup_review: Optional[ReviewStartupOptions] = None):
        print("=" * 60)
        print("WallDance 1080p - Multi-Person Pose Detection")
        print("=" * 60)

        # Model loading is deferred until after GUI is created
        # so we can show a progress modal
        self.model = None
        self.model_manager = ModelManager(MODELS_DIR, use_tensorrt=USE_TENSORRT, imgsz=YOLO_IMGSZ)
        self.current_model = YOLO_MODEL
        self.current_model_name = YOLO_MODEL.replace(".pt", "").replace(".engine", "")
        self._model_loaded = False
        self._model_loading = False  # True while model is being loaded/switched
        self._source_transitioning = False  # True during playback↔live transitions
        self._pending_model_switch: Optional[str] = None  # Deferred model switch
        self._pending_trt_switch: Optional[bool] = None  # True=switch to TRT, False=switch to PT
        self._pending_trt_build: Optional[str] = None  # Model name to build TRT engine for
        self._pending_model_for_trt_build: Optional[str] = None  # Model to switch to after TRT build prompt

        self.settings = ProcessingSettings(
            confidence=YOLO_CONFIDENCE,
            imgsz=YOLO_IMGSZ,
            use_fp16=True,
            enhance_enabled=ENHANCE_ENABLED,
            enhance_lite=False,
            enhance_force=False,
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
        self.ids_ratio: float = IDS_RATIO  # Current IDS crop ratio (W/H)
        self.ids_gain_db: float = 0.0        # Current IDS gain (dB), 0 = default
        self.ids_exposure_us: float = 10000.0  # Current IDS exposure (µs)
        if self._use_unified_camera:
            self.unified_camera = UnifiedCamera()
            self.camera = CameraManager()  # Keep for compatibility with camera state
            print(f"[Camera] UnifiedCamera available (IDS Peak: {IDS_PEAK_AVAILABLE})")
        else:
            self.unified_camera = None
            self.camera = CameraManager()
            print("[Camera] Using OpenCV CameraManager")
        
        self.osc: Optional[OSCSender] = None
        self.osc_ip = OSC_IP
        self.osc_port = OSC_PORT
        self.osc_enabled = OSC_ENABLED

        self.enhancer = ImageEnhancer()
        self.tracker = DancerTracker()
        self.tracker.logger.camera_id = CAMERA_INDEX
        self.tracker.set_person_height(PERSON_HEIGHT_PX)
        self.processor = FrameProcessor(
            model=self.model,
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
        self.config_store = ConfigStore()
        self._current_project = "default"

        # Video recording
        self.recorder = VideoRecorder()
        self.recorder.on_playback_start = self._on_playback_start_event
        self._pending_rec_slot: Optional[int] = None  # Slot being recorded to
        self._rec_armed: bool = False  # True when REC clicked, waiting for slot selection

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
        self.last_tracked: List[ScaledTrack] = []
        self._total_frame_count: int = 0  # Phase 0: cumulative frame counter (live mode)
        self._last_raw_frame: Optional[np.ndarray] = None  # Last raw camera frame for BG capture
        self._last_review_frame: Optional[np.ndarray] = None
        self._startup_review = startup_review or ReviewStartupOptions()
        self._pause_at_frame_target = self._startup_review.pause_at_frame
        
        # Pending operations (deferred to main loop)
        self._pending_camera_refresh = False
        self._pending_project_switch: Optional[str] = None  # Config filepath to switch to
        self._pending_playback_events: Deque[str] = deque()
        self._pending_playback_events_lock = threading.Lock()
        self._camera_retry_backoff_s = 1.0
        self._camera_retry_max_s = 5.0
        self._next_camera_retry_time = 0.0
        self._camera_reconnecting = False
        self._ids_disconnect_timeout_s = 2.5
        self._last_camera_open_time = 0.0

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
            "model": self.current_model_name,
            "use_tensorrt": self.model_manager.is_using_tensorrt(),
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
            "ids_ratio": self.ids_ratio,
            "ids_gain_db": self.ids_gain_db,
            "ids_exposure_us": self.ids_exposure_us,
            "ids_exposure_max_us": max_exposure_for_fps(IDS_EXPOSURE_MIN_FPS),
            "ids_exposure_min_fps": IDS_EXPOSURE_MIN_FPS,
            "ids_exposure_warning_fps": IDS_EXPOSURE_WARNING_FPS,
            "texture_width": self.preview.width,
            "texture_height": self.preview.height,
            "camera_running": self.camera.state.is_open,
            "camera_reconnecting": self._camera_reconnecting,
        }

    def _get_gui_callbacks(self) -> Dict:
        return {
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
            "on_motion_sensitivity_change": self._cb_motion_sensitivity_change,
            "on_tracking_mode_change": self._cb_tracking_mode_change,
            "on_model_change": self._cb_model_change,
            "on_trt_toggle": self._cb_trt_toggle,
            "on_ids_ratio_change": self._cb_ids_ratio_change,
            "on_ids_gain_change": self._cb_ids_gain_change,
            "on_ids_exposure_change": self._cb_ids_exposure_change,
            "on_camera_change": self._cb_camera_change,
            "on_camera_refresh": self._cb_camera_refresh,
            "on_imgsz_change": self._cb_imgsz_change,
            "on_person_height_change": self._cb_person_height_change,
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
            "on_roi_edit_toggle": self._cb_roi_edit_toggle,
            "on_roi_reset": self._cb_roi_reset,
            "on_roi_x_change": self._cb_roi_x_change,
            "on_roi_y_change": self._cb_roi_y_change,
            "on_roi_w_change": self._cb_roi_w_change,
            "on_roi_h_change": self._cb_roi_h_change,
            "on_save_config": self._cb_save_config,
            "on_save_as_config": self._cb_save_as_config,
            "on_save_safe_defaults": self._cb_save_safe_defaults,
            "on_load_safe_defaults": self._cb_load_safe_defaults,
            "on_load_config": self._cb_load_config,
            "on_do_save_config": self._cb_do_save_config,
            "on_do_load_config": self._cb_do_load_config,
            "on_project_select": self._cb_project_select,
            "on_config_select": self._cb_config_select,
            "on_rec_live": self._cb_rec_live,
            "on_rec_toggle": self._cb_rec_toggle,
            "on_rec_slot_click": self._cb_rec_slot_click,
            "on_playback_speed_change": self._cb_playback_speed_change,
            "on_playback_pause": self._cb_playback_pause,
            "on_playback_force_pause": self._cb_playback_force_pause,
            "on_playback_next_frame": self._cb_playback_next_frame,
            "on_playback_prev_frame": self._cb_playback_prev_frame,
            "on_report_issue_request": self._cb_report_issue_request,
            "on_issue_submit": self._cb_issue_submit,
            "on_issue_dialog_closed": self._cb_issue_dialog_closed,
            "on_quit": self._cb_quit,
        }

    def _normalize_camera_source(self, source: Optional[str]) -> str:
        normalized = (source or self.camera.state.source or str(CAMERA_INDEX)).replace(" (unavailable)", "").strip()
        if normalized.lower().startswith("ids"):
            return "ids"
        return normalized

    def _camera_ui_sources(self) -> List[str]:
        sources = list(set(self.camera.state.available + self.camera.state.unavailable))
        current = self._normalize_camera_source(self.camera.state.source)
        if current and current not in sources:
            sources.append(current)
        sources.sort(key=lambda value: (value not in self.camera.state.available, value != "ids", value))
        return sources

    def _reset_camera_retry(self) -> None:
        self._camera_retry_backoff_s = 1.0
        self._next_camera_retry_time = 0.0
        self._camera_reconnecting = False

    def _schedule_camera_retry(self, delay: Optional[float] = None) -> None:
        if delay is None:
            delay = self._camera_retry_backoff_s
            self._camera_retry_backoff_s = min(self._camera_retry_backoff_s * 1.5, self._camera_retry_max_s)
        self._next_camera_retry_time = time.perf_counter() + max(0.0, delay)
        self._camera_reconnecting = True

    def _mark_camera_unavailable(self, source: Optional[str] = None, close_active: bool = False) -> None:
        source = self._normalize_camera_source(source)
        self.camera.state.source = source
        self.camera.state.is_open = False
        self._last_camera_open_time = 0.0

        if close_active:
            try:
                if self._use_unified_camera and self.unified_camera is not None:
                    self.unified_camera.close()
                else:
                    self.camera.close()
            except Exception as exc:
                print(f"[Camera] Close exception: {exc}")

        if source in self.camera.state.available:
            self.camera.state.available.remove(source)
        if source not in self.camera.state.unavailable:
            self.camera.state.unavailable.append(source)

        if self.gui:
            self.gui.update_camera_sources(self._camera_ui_sources(), source, self.camera.state.unavailable)
            self.gui.update_camera_status(False, source, reconnecting=self._camera_reconnecting)
            self.gui.config['camera_type'] = ""

    def _attempt_camera_connect(self, source: Optional[str] = None, retry_on_fail: bool = True) -> bool:
        source = self._normalize_camera_source(source)
        self.camera.state.source = source
        opened = self._open_camera(source)
        if opened:
            self._last_camera_open_time = time.perf_counter()
            self._reset_camera_retry()
            return True
        self._mark_camera_unavailable(source)
        if retry_on_fail:
            self._schedule_camera_retry()
        return False

    def _ids_stream_timed_out(self) -> bool:
        if not self._is_ids_camera_active() or self.unified_camera is None:
            return False
        frame_count, _ = self.unified_camera.get_ids_counters()
        if frame_count <= 0:
            if self._last_camera_open_time <= 0:
                return False
            return (time.perf_counter() - self._last_camera_open_time) > self._ids_disconnect_timeout_s
        return self.unified_camera.get_last_acquired_age_s() > self._ids_disconnect_timeout_s

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
        self.gui.sync_checkbox("roi_edit", self.roi_edit_mode)
        self.gui.sync_input("roi_x", self.settings.roi_x)
        self.gui.sync_input("roi_y", self.settings.roi_y)
        self.gui.sync_input("roi_w", self.settings.roi_w)
        self.gui.sync_input("roi_h", self.settings.roi_h)
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
    # Config persistence
    # ------------------------------------------------------------------
    def _get_saveable_config(self) -> Dict:
        return {
            "camera_source": self.camera.state.source,
            "model": self.current_model_name,
            "use_tensorrt": self.model_manager.is_using_tensorrt(),
            "confidence": self.settings.confidence,
            "yolo_imgsz": self.settings.imgsz,
            "fp16": self.settings.use_fp16,
            "person_height_px": self.settings.person_height_px,
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
            "ids_ratio": self.ids_ratio,
            "ids_gain_db": self.ids_gain_db,
            "ids_exposure_us": self.ids_exposure_us,
            "bg_subtract_enabled": self.settings.bg_subtract_enabled,
            "bg_subtract_sensitivity": self.settings.bg_subtract_sensitivity,
            "mog2_scale": self.processor.get_motion_scale(),
        }

    def _update_topbar_state(self, selected_filepath: Optional[str] = None):
        projects = self.config_store.list_projects()
        if self.gui:
            self.gui.update_project_list(projects, self._current_project)

        history = self.config_store.project_history(self._current_project)

        current_display = ""
        if selected_filepath:
            selected_abs = os.path.abspath(selected_filepath)
            for display, path in history.configs:
                if os.path.abspath(path) == selected_abs:
                    current_display = display
                    break
            if not current_display:
                current_display = format_config_display(os.path.basename(selected_filepath))

        if not current_display and history.configs:
            current_display = history.configs[0][0]

        if self.gui:
            self.gui.update_config_list(history.configs, current_display)
            if current_display:
                self.gui.set_current_config(current_display)

    def _execute_project_switch(self, config_filepath: str):
        """Execute a full project switch with proper cleanup and initialization.
        
        This is the unified path for both startup and runtime project switching.
        It ensures everything is properly closed and reinitialized.
        
        Args:
            config_filepath: Path to the config file to load
        """
        print(f"\n{'='*60}")
        print(f"[Project Switch] Starting switch to: {os.path.basename(config_filepath)}")
        print(f"{'='*60}")
        
        # 1. Stop any recording/playback (clear callback first)
        print("[Project Switch] Stopping recorder...")
        self._set_camera_frame_callback(None)
        self.recorder.stop_recording()
        self.recorder.stop_playback()
        
        # 2. Block processing
        self._model_loading = True
        
        # 3. Close camera
        camera_was_open = self.camera.state.is_open
        if camera_was_open:
            print("[Project Switch] Closing camera...")
            self.camera.close()
            self.camera.state.is_open = False
        
        # 4. Load the config
        try:
            config = self.config_store.load(config_filepath)
        except Exception as e:
            print(f"[Project Switch] ERROR: Failed to load config: {e}")
            self._model_loading = False
            if camera_was_open:
                self._open_camera(self.camera.state.source)
            return False
        
        # 5. Extract model info before applying config
        model_name = config.get("model", self.current_model_name)
        use_trt = config.get("use_tensorrt", False)
        new_imgsz = config.get("yolo_imgsz", self.settings.imgsz)
        base_name = model_name.replace('.pt', '').replace('.engine', '')
        
        print(f"[Project Switch] Target: model={model_name}, TRT={use_trt}, imgsz={new_imgsz}")
        
        # 6. Update imgsz in model manager BEFORE loading model
        self.settings.imgsz = new_imgsz
        self.model_manager.set_imgsz(new_imgsz)
        self._update_imgsz_roi_warning()
        
        # 7. Determine if we need to reload the model
        need_model_reload = (
            base_name != self.current_model_name or
            new_imgsz != self.settings.imgsz or
            use_trt != self.model_manager.is_using_tensorrt()
        )
        
        # For TRT, we always need to reload if imgsz changed (engines are size-specific)
        if self.model_manager.is_using_tensorrt() or use_trt:
            need_model_reload = True
        
        # 8. Load the model if needed
        if need_model_reload:
            # Check if TRT engine exists
            force_pt = not use_trt
            if use_trt and not self.model_manager.engine_exists(base_name):
                from model_manager import is_tensorrt_available
                if is_tensorrt_available():
                    # Prompt user before starting long TRT build
                    if self._prompt_trt_build_sync(base_name):
                        print(f"[Project Switch] User accepted TRT build for {base_name}@{new_imgsz}")
                        force_pt = False
                    else:
                        print(f"[Project Switch] User declined TRT build, using PyTorch")
                        force_pt = True
                        use_trt = False
                else:
                    print(f"[Project Switch] TRT not available, using PyTorch")
                    force_pt = True
                    use_trt = False
            
            print(f"[Project Switch] Loading model {model_name}... (TRT={use_trt}, force_pt={force_pt})")
            if not self._load_model_with_progress(model_name, force_pt=force_pt):
                print(f"[Project Switch] ERROR: Failed to load model")
                self._model_loading = False
                if camera_was_open:
                    self._open_camera(self.camera.state.source)
                return False
        
        # 9. Apply the rest of the config (skip model since we just loaded it)
        # Also skip imgsz since we already set it
        print("[Project Switch] Applying config settings...")
        self._apply_config_without_model(config)
        
        # 10. Update project tracking
        self._current_project = self.config_store.infer_project_from_config(config, config_filepath)
        self.config_store.remember_last_project(self._current_project)
        
        # 11. Update recorder
        self.recorder.set_project(self._current_project)
        
        # 12. Clear any pending operations that were set during config apply
        self._pending_model_switch = None
        self._pending_trt_switch = None
        self._pending_trt_build = None
        self._pending_model_for_trt_build = None
        
        # 13. Reopen camera if it was open (using new camera source from config if specified)
        camera_source = config.get("camera_source", self.camera.state.source)
        if camera_was_open or camera_source != self.camera.state.source:
            print(f"[Project Switch] Opening camera {camera_source}...")
            if self._attempt_camera_connect(camera_source):
                time.sleep(0.3)
                if self.camera.cap:
                    for _ in range(5):
                        self.camera.cap.grab()
        
        # 14. Update UI
        self._update_topbar_state(selected_filepath=config_filepath)
        self._update_recording_ui()
        if self.gui:
            self.gui.sync_combo("model", base_name)
            self.gui.set_trt_checkbox(self.model_manager.is_using_tensorrt())
            self.gui.update_camera_sources(self._camera_ui_sources(), self.camera.state.source, self.camera.state.unavailable)
            
            cam_type_str = ""
            if self._use_unified_camera and self.unified_camera is not None and self.unified_camera.is_open:
                if self.unified_camera.source_type == CameraSource.IDS_PEAK:
                    cam_type_str = "IDS_PEAK"
                else:
                    cam_type_str = "OPENCV"
            elif self.camera.state.is_open:
                cam_type_str = "OPENCV"
            self.gui.config['camera_type'] = cam_type_str
            
            self.gui.update_camera_status(
                self.camera.state.is_open,
                self.camera.state.source,
                reconnecting=self._camera_reconnecting,
            )
        
        # 15. Resume processing
        self._model_loading = False
        
        print(f"[Project Switch] Complete: {self._current_project}")
        print(f"{'='*60}\n")
        return True

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
            self.gui and self.gui.sync_combo("tracking_mode", "Motion First" if mode == TrackingMode.MOTION_FIRST else "YOLO First")
        # Load tracker_max_age AFTER tracking_mode so user value wins
        if "tracker_max_age" in config:
            self.tracker.max_age = config["tracker_max_age"]
            self.gui and self.gui.sync_slider("tracker_max_age", config["tracker_max_age"])
        if "tracker_smoothing" in config:
            self.tracker.smoothing_depth = config["tracker_smoothing"]
            self.gui and self.gui.sync_slider("tracker_smoothing", config["tracker_smoothing"])
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
            self._cb_ids_ratio_change(ratio)
            self.gui and self.gui.sync_slider("ids_ratio", ratio)

        # IDS gain
        if "ids_gain_db" in config:
            self._cb_ids_gain_change(config["ids_gain_db"])
            self.gui and self.gui.sync_slider("ids_gain_db", config["ids_gain_db"])

        # IDS exposure
        if "ids_exposure_us" in config:
            self._cb_ids_exposure_change(config["ids_exposure_us"])
            self.gui and self.gui.sync_slider("ids_exposure_us", self.ids_exposure_us)

        # Background subtraction
        if "bg_subtract_enabled" in config:
            self.settings.bg_subtract_enabled = config["bg_subtract_enabled"]
            self.gui and self.gui.sync_checkbox("bg_enable", config["bg_subtract_enabled"])
        if "bg_subtract_sensitivity" in config:
            self.settings.bg_subtract_sensitivity = config["bg_subtract_sensitivity"]
            self.gui and self.gui.sync_slider("bg_sensitivity", config["bg_subtract_sensitivity"])
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
        self.settings.confidence = value
        print(f"Confidence: {value:.2f}")
        self._request_reprocess()

    def _cb_motion_sensitivity_change(self, value: float):
        self.processor.set_motion_sensitivity(value)
        print(f"Motion bridge sensitivity: {value:.2f}")
        self._request_reprocess()

    def _cb_tracking_mode_change(self, mode_str: str):
        mode = TrackingMode(mode_str)
        self.tracker.set_tracking_mode(mode)
        self.processor.set_tracking_mode(mode)
        print(f"Tracking mode: {mode.value}")
        self._request_reprocess()

    def _cb_camera_change(self, value: str):
        source = self._normalize_camera_source(value)
        if source == self.camera.state.source and self.camera.state.is_open:
            return
        print(f"Selecting camera source: {source}")
        try:
            self._attempt_camera_connect(source)
        except Exception as e:
            print(f"[Camera] Switch to '{source}' crashed: {e}")
            import traceback; traceback.print_exc()
            self._mark_camera_unavailable(source)
            self._schedule_camera_retry()

    def _cb_camera_refresh(self):
        """Request a camera list refresh (deferred to main loop)."""
        self._pending_camera_refresh = True

    def _do_camera_refresh(self):
        """Actually perform camera refresh - called from main loop."""
        print("Refreshing camera list...")
        # Remember current source and whether it was open
        current_source = self._normalize_camera_source(self.camera.state.source)
        was_open = self.camera.state.is_open
        
        # Close camera first so detection can find all cameras
        if was_open:
            if self._use_unified_camera and self.unified_camera is not None:
                self.unified_camera.close()
            else:
                self.camera.close()
            self.camera.state.is_open = False
        
        # Detect all cameras (OpenCV + IDS)
        # ORDER MATTERS: probe IDS first (initialises GenTL), then release
        # the IDS library, THEN probe OpenCV.  If we probe OpenCV while
        # GenTL is active, the GenTL transport layer can hold USB locks
        # that cause a native crash when OpenCV opens the same device.
        available_sources = []
        
        # IDS cameras first (if available)
        if IDS_PEAK_AVAILABLE:
            try:
                ids_cameras = IDSCamera.list_cameras()
                if ids_cameras:
                    available_sources.append("ids")
                    print(f"IDS cameras detected: {len(ids_cameras)}")
            except Exception as e:
                print(f"IDS camera detection error: {e}")
            # Release IDS library NOW so OpenCV doesn't fight GenTL for USB
            try:
                IDSCamera._release_ids_library_fully()
            except Exception:
                pass
            import time as _t
            _t.sleep(0.5)  # USB stack settle time

        # OpenCV cameras (with IDS library released)
        opencv_cameras = CameraManager.detect_cameras()
        available_sources.extend(opencv_cameras)
        print(f"OpenCV cameras: {opencv_cameras}")

        available_sources = sorted(set(available_sources), key=lambda value: (value != "ids", value))
        unavailable_sources = []
        if current_source and current_source not in available_sources:
            unavailable_sources.append(current_source)

        self.camera.state.available = available_sources
        self.camera.state.unavailable = unavailable_sources
        self.camera.state.source = current_source
        print(f"Available cameras: {available_sources}")
        
        # Update GUI
        if self.gui:
            self.gui.update_camera_sources(self._camera_ui_sources(), current_source, unavailable_sources)

        if self.running and not was_open and current_source in self.camera.state.available:
            self._attempt_camera_connect(current_source)
            return
        
        # Reopen the camera if it was open and is still available
        if was_open:
            if current_source in self.camera.state.available:
                self._attempt_camera_connect(current_source)
            else:
                print(f"Camera {current_source} no longer available")
                self._mark_camera_unavailable(current_source)
                self._schedule_camera_retry()
                if self.gui:
                    self.gui.update_camera_status(False, current_source, reconnecting=self._camera_reconnecting)

    def _open_camera(self, source: str) -> bool:
        """Open camera using UnifiedCamera (IDS+OpenCV) or legacy CameraManager."""
        source = self._normalize_camera_source(source)
        if self._use_unified_camera and self.unified_camera is not None:
            return self._open_camera_unified(source)
        else:
            return self._open_camera_legacy(source)
    
    def _open_camera_unified(self, source: str) -> bool:
        """Open camera using UnifiedCamera (supports IDS and OpenCV)."""
        source = self._normalize_camera_source(source)
        # Close any existing camera (guarded — close must not prevent re-open)
        try:
            self.unified_camera.close()
        except Exception as e:
            print(f"[Camera] Warning: close before switch failed: {e}")
        
        # Determine source type
        if source.lower().startswith("ids"):
            camera_source = source
        else:
            camera_source = source
        
        opened = self.unified_camera.open(camera_source)
        
        if opened:
            # Sync state with legacy CameraManager state (for UI compatibility)
            self.camera.state.is_open = True
            self.camera.state.width = self.unified_camera.width
            self.camera.state.height = self.unified_camera.height
            self.camera.state.source = source
            if source in self.camera.state.unavailable:
                self.camera.state.unavailable.remove(source)
            if source not in self.camera.state.available:
                self.camera.state.available.append(source)
            self.camera.state.available.sort(key=lambda value: (value != "ids", value))
            
            # Report camera type
            source_type = self.unified_camera.source_type
            if source_type == CameraSource.IDS_PEAK:
                print(f"[Camera] IDS camera opened: {self.unified_camera.width}x{self.unified_camera.height}")
                cam_type_str = "IDS_PEAK"
                # Re-apply stored gain/exposure so they survive webcam round-trips
                self._reapply_ids_settings()
            else:
                print(f"[Camera] OpenCV camera opened: {self.unified_camera.width}x{self.unified_camera.height}")
                cam_type_str = "OPENCV"
            
            if self.gui:
                self.gui.update_camera_sources(self._camera_ui_sources(), source, self.camera.state.unavailable)
                self.gui.update_camera_status(True, source, reconnecting=False)
                self.gui.config['camera_type'] = cam_type_str
            
            # Update preview geometry
            self.preview.width = int(self.unified_camera.width * self.preview.render_scale)
            self.preview.height = int(self.unified_camera.height * self.preview.render_scale)
            if self.processor:
                self.processor.set_preview_size(self.preview.width, self.preview.height)
            # Notify GUI of new camera dimensions – layout will recompute
            if self.gui:
                self.gui.set_camera_dimensions(self.unified_camera.width, self.unified_camera.height)
            self._pending_preview_resize = True
        else:
            self.camera.state.is_open = False
            self._last_camera_open_time = 0.0
            if source not in self.camera.state.unavailable:
                self.camera.state.unavailable.append(source)
            
            if self.gui:
                self.gui.update_camera_sources(self._camera_ui_sources(), source, self.camera.state.unavailable)
                self.gui.update_camera_status(False, source, reconnecting=self._camera_reconnecting)
                self.gui.config['camera_type'] = ""
            print(f"[Camera] Failed to open: {source}")
        
        return opened
    
    def _open_camera_legacy(self, source: str) -> bool:
        """Open camera using legacy CameraManager (OpenCV only)."""
        source = self._normalize_camera_source(source)
        opened = self.camera.open(source)
        state = self.camera.state
        if opened:
            self._last_camera_open_time = time.perf_counter()
            if self.gui:
                self.gui.update_camera_sources(self._camera_ui_sources(), source, state.unavailable)
                self.gui.update_camera_status(True, source, reconnecting=False)
                self.gui.config['camera_type'] = "OPENCV"
            # Update preview geometry to match actual camera size
            self.preview.width = int(state.width * self.preview.render_scale)
            self.preview.height = int(state.height * self.preview.render_scale)
            if self.processor:
                self.processor.set_preview_size(self.preview.width, self.preview.height)
            # Notify GUI of new camera dimensions – layout will recompute
            if self.gui:
                self.gui.set_camera_dimensions(state.width, state.height)
            self._pending_preview_resize = True
            print(f"Camera {source} opened: {state.width}x{state.height}")
        else:
            self._last_camera_open_time = 0.0
            if self.gui:
                self.gui.update_camera_sources(self._camera_ui_sources(), source, state.unavailable)
                self.gui.update_camera_status(False, source, reconnecting=self._camera_reconnecting)
                self.gui.config['camera_type'] = ""
            print(f"Camera {source} unavailable")
        return opened
    
    def _is_ids_camera_active(self) -> bool:
        """Check if an IDS camera is currently active."""
        if not self._use_unified_camera or self.unified_camera is None:
            return False
        return (self.unified_camera.is_open and 
                self.unified_camera.source_type == CameraSource.IDS_PEAK)

    def _cb_imgsz_change(self, value: int):
        new_imgsz = int(value)
        old_imgsz = self.settings.imgsz
        
        if new_imgsz == old_imgsz:
            return
        
        self.settings.imgsz = new_imgsz
        self.model_manager.set_imgsz(new_imgsz)
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
        if self.model_manager.is_using_tensorrt():
            base_name = self.current_model_name
            
            # Check if engine exists for new imgsz
            if self.model_manager.engine_exists(base_name):
                # Engine exists, reload with TRT
                print(f"TRT engine exists for {base_name}@{new_imgsz}, reloading...")
                self._pending_trt_switch = True
                self._pending_model_switch = base_name
                # Block processing until model is reloaded to prevent imgsz mismatch
                self._model_loading = True
                self._model_loaded = False
            else:
                # No engine for new imgsz - fall back to PyTorch (don't prompt, just switch)
                print(f"No TRT engine for {base_name}@{new_imgsz}, falling back to PyTorch")
                self.gui.set_trt_checkbox(False)
                self._pending_trt_switch = False
                self._pending_model_switch = base_name
                # Block processing until model is reloaded to prevent imgsz mismatch
                self._model_loading = True
                self._model_loaded = False
                self.gui.show_toast(f"No TRT for {new_imgsz}px, using PyTorch", duration=3.0, color=(255, 200, 100))

    @staticmethod
    def _is_trt_input_size_mismatch_error(exc: Exception) -> bool:
        msg = str(exc).lower()
        return "input size" in msg and "max model size" in msg and "not equal to" in msg

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

    def _on_playback_start_event(self, event: str):
        """Called by VideoRecorder on playback start/restart/loop.

        The callback may be invoked by the playback decoder thread on
        loop/restart. Queue the work and let the main loop perform the
        reset/session rollover so tracker state is mutated from one thread.
        """
        print(f"[Playback] Event '{event}' — queueing tracker reset")
        with self._pending_playback_events_lock:
            self._pending_playback_events.append(event)

    def _drain_pending_playback_event(self) -> Optional[str]:
        """Return the next deferred playback event, if any."""
        with self._pending_playback_events_lock:
            if not self._pending_playback_events:
                return None
            return self._pending_playback_events.popleft()

    def _handle_playback_start_event(self, event: str):
        """Apply deferred playback start/restart/loop handling."""
        print(f"[Playback] Event '{event}' — resetting tracker")
        self._total_frame_count = 0
        self._cb_tracker_reset()
        self._start_session()

    def _start_session(self):
        """Create a per-run session directory and redirect the logger."""
        slot = self.recorder.status.current_slot
        stamp = time.strftime("%Y%m%d_%H%M%S")
        session_name = f"{stamp}_slot{slot}"
        sessions_root = os.path.join(
            self.config_store.config_dir,
            self._current_project,
            "sessions",
        )
        session_dir = os.path.join(sessions_root, session_name)
        os.makedirs(session_dir, exist_ok=True)

        # Redirect logger to the new session directory
        self.tracker.logger.start_session(session_dir)
        self.tracker.logger.log_settings(self._get_saveable_config())

        # Write session metadata
        meta = {
            "session": session_name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "project": self._current_project,
            "slot": slot,
            "model": self.current_model_name,
            "imgsz": self.settings.imgsz,
            "playback_path": self.recorder.playback_path,
        }
        meta_path = os.path.join(session_dir, "session.json")
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, default=_json_default)

        # Maintain a 'latest' symlink
        latest_link = os.path.join(sessions_root, "latest")
        try:
            if os.path.islink(latest_link):
                os.remove(latest_link)
            elif os.path.exists(latest_link):
                os.remove(latest_link)
            os.symlink(session_name, latest_link)
        except OSError as exc:
            print(f"[Session] Could not create 'latest' symlink: {exc}")

        print(f"[Session] {session_dir}")

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
            "project": self._current_project,
            "slot": self.recorder.status.current_slot,
            "frame": self.recorder.status.playback_frame,
            "playback_total": self.recorder.status.playback_total,
            "playback_fps": self.recorder.status.playback_fps,
            "playback_speed": self.recorder._playback_speed,
            "playback_path": self.recorder.playback_path,
            "model": self.current_model_name,
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
                self.config_store.config_dir,
                self._current_project,
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
            "project": self._current_project,
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

    def _cb_save_config(self):
        self._cb_do_save_config(self._current_project)

    def _cb_save_as_config(self):
        if self.gui:
            self.gui.show_save_config_dialog(self._current_project)

    def _cb_load_config(self):
        if self.gui:
            self.gui.show_load_config_dialog(self.config_store.config_dir, self._current_project)

    def _cb_do_save_config(self, project_name: str):
        filepath = self.config_store.save(project_name, self._get_saveable_config())
        new_project = sanitize_project_name(project_name)
        # Only switch recorder project if the name actually changed
        # (avoids stopping playback when saving to the same project)
        if new_project != self._current_project:
            self._current_project = new_project
            self.recorder.set_project(self._current_project)
            self._update_recording_ui()  # Refresh slots for new project
        self._update_topbar_state()
        if self.gui:
            self.gui.show_save_indicator("Saved!")
        print(f"Config saved: {filepath}")

    def _cb_do_load_config(self, filepath: str):
        """Handle config load request - defers to main loop for proper sequencing."""
        # Defer the actual project switch to main loop to avoid race conditions
        # The main loop will call _execute_project_switch() which handles:
        # - Stopping recording/playback
        # - Closing camera
        # - Loading model with correct imgsz
        # - Applying config
        # - Reopening camera
        print(f"Queuing project switch to: {os.path.basename(filepath)}")
        self._pending_project_switch = filepath
        self._model_loading = True  # Block processing immediately

    def _cb_project_select(self, project_name: str):
        latest = self.config_store.latest_for_project(project_name)
        if latest:
            self._cb_do_load_config(latest)
            print(f"Loaded latest config for project: {project_name}")
        else:
            print(f"No configs found for project: {project_name}")

    def _cb_config_select(self, project_name: str, config_display: str):
        history = self.config_store.project_history(project_name)
        for display, filepath in history.configs:
            if display == config_display:
                self._cb_do_load_config(filepath)
                return
        print(f"Config not found: {config_display}")

    def _cb_save_safe_defaults(self):
        """Save current settings as safe defaults for this project."""
        filepath = self.config_store.save_safe_defaults(self._current_project, self._get_saveable_config())
        if self.gui:
            self.gui.show_save_indicator("Safe defaults saved!")
        print(f"Safe defaults saved: {filepath}")

    def _cb_load_safe_defaults(self):
        """Load safe defaults for this project."""
        config = self.config_store.load_safe_defaults(self._current_project)
        if config:
            # Check if model or imgsz would change
            model_changes = config.get("model", self.current_model_name) != self.current_model_name
            imgsz_changes = config.get("yolo_imgsz", self.settings.imgsz) != self.settings.imgsz
            trt_changes = config.get("use_tensorrt", self.model_manager.is_using_tensorrt()) != self.model_manager.is_using_tensorrt()
            
            if model_changes or imgsz_changes or trt_changes:
                # Need full project switch for model/imgsz/trt changes
                # Save the config temporarily and trigger a project switch
                temp_path = self.config_store.save(self._current_project, config)
                self._pending_project_switch = temp_path
                self._model_loading = True
            else:
                # No model changes, just apply config directly
                self._apply_config_without_model(config)
            if self.gui:
                self.gui.show_save_indicator("Safe defaults loaded!")
            print(f"Safe defaults loaded for project: {self._current_project}")
        else:
            print(f"No safe defaults found for project: {self._current_project}")

    def _cb_model_change(self, model_name: str):
        """Handle model change from GUI dropdown.
        
        Note: This is called from a DearPyGui callback during render_frame().
        We defer the actual loading to the main loop to avoid race conditions.
        
        If TRT checkbox is checked:
        - Check if engine exists for new model
        - If not, prompt to build (via _pending_trt_build)
        - If user declines, switch model but disable TRT
        """
        # Check if we're already using this model (either .pt or .engine)
        base_name = model_name.replace('.pt', '').replace('.engine', '')
        current_base = self.current_model_name
        if base_name == current_base:
            print(f"[Model] Already using {base_name}, skipping switch")
            return
        
        # Check if a model load is already in progress
        if self._model_loading:
            print(f"[Model] WARNING: Model loading already in progress, ignoring switch to {model_name}")
            return
        
        # Check if TRT checkbox is enabled
        trt_enabled = self.model_manager.use_tensorrt
        
        if trt_enabled:
            # TRT is enabled - check if engine exists for new model
            from model_manager import is_tensorrt_available
            
            if is_tensorrt_available() and self.model_manager.engine_exists(base_name):
                # Engine exists, switch with TRT
                print(f"Queuing model switch to: {model_name} (TRT engine exists)...")
                self._pending_trt_switch = True
                self._pending_model_switch = model_name
                self._model_loading = True  # Block processing until model is reloaded
            elif is_tensorrt_available():
                # No engine - need to prompt user before building
                # Update model name tracking so TRT build knows which model
                print(f"No TRT engine for {base_name}, prompting to build...")
                self._pending_trt_build = base_name
                # Store that this is a model switch (not just TRT toggle on same model)
                self._pending_model_for_trt_build = base_name
            else:
                # TRT not available, switch with PT and disable checkbox
                print(f"Queuing model switch to: {model_name} (TRT not available)...")
                self._pending_trt_switch = False
                self._pending_model_switch = model_name
                self._model_loading = True  # Block processing until model is reloaded
        else:
            # TRT not enabled, just switch to PT model
            print(f"Queuing model switch to: {model_name}...")
            self._pending_trt_switch = False
            self._pending_model_switch = model_name
            self._model_loading = True  # Block processing until model is reloaded

    def _prompt_trt_build_sync(self, model_name: str) -> bool:
        """Show TRT build prompt and block until user responds.
        
        Used during startup / project switch before entering the main loop.
        
        Args:
            model_name: Base model name (e.g. "yolo11m-pose")
            
        Returns:
            True if user chose to build, False if declined.
        """
        if self.gui is None:
            return False
        
        user_choice = {"build_trt": None}
        
        def on_choice(build_trt: bool):
            user_choice["build_trt"] = build_trt
        
        self.gui.show_tensorrt_prompt(model_name, on_choice)
        
        # Spin the GUI event loop until the user clicks a button
        while user_choice["build_trt"] is None:
            dpg.render_dearpygui_frame()
            time.sleep(0.016)
        
        # Let modal close cleanly
        for _ in range(5):
            dpg.render_dearpygui_frame()
            time.sleep(0.02)
        
        return user_choice["build_trt"]

    def _cb_trt_toggle(self, enabled: bool):
        """Handle TensorRT toggle checkbox.
        
        If enabling TRT:
        - Check if .engine exists
        - If not, ask to generate
        - If user says no or generation fails, revert checkbox to off
        
        If disabling TRT:
        - Switch to .pt model
        """
        base_name = self.current_model_name
        
        if enabled:
            # User wants to enable TensorRT
            from model_manager import is_tensorrt_available
            
            if not is_tensorrt_available():
                print("TensorRT not available on this system")
                self.gui.set_trt_checkbox(False)
                self.gui.show_toast("TensorRT not available", duration=3.0, color=(255, 100, 100))
                return
            
            if self.model_manager.engine_exists(base_name):
                # Engine exists, just switch to it
                print(f"Switching to TensorRT engine for {base_name}...")
                self._pending_trt_switch = True
                self._pending_model_switch = base_name
                self._model_loading = True  # Block processing until model is reloaded
            else:
                # Need to build engine - show prompt
                print(f"No TensorRT engine for {base_name}, prompting to build...")
                self._pending_trt_build = base_name
        else:
            # User wants to disable TensorRT, switch to .pt
            print(f"Switching to PyTorch for {base_name}...")
            self._pending_trt_switch = False
            self._pending_model_switch = base_name
            self._model_loading = True  # Block processing until model is reloaded

    def _cb_ids_ratio_change(self, value: float):
        """Handle IDS crop-ratio slider change."""
        ratio = max(0.5, min(2.0, float(value)))
        self.ids_ratio = ratio
        if self._use_unified_camera and self.unified_camera is not None:
            ok = self.unified_camera.update_crop_ratio(ratio)
            if ok:
                # Propagate new resolution to legacy state
                self.camera.state.width = self.unified_camera.width
                self.camera.state.height = self.unified_camera.height
                # Notify GUI of new dimensions – layout auto-recomputes
                if self.gui:
                    self.gui.set_camera_dimensions(self.unified_camera.width, self.unified_camera.height)
                if self.processor:
                    self.processor.set_preview_size(self.preview.width, self.preview.height)
                print(f"[IDS Ratio] {ratio:.2f} → {self.unified_camera.width}x{self.unified_camera.height}")
            else:
                print(f"[IDS Ratio] update_crop_ratio failed for ratio={ratio:.2f}")

    def _reapply_ids_settings(self):
        """Re-apply stored IDS gain/exposure after camera reopen."""
        if not self._use_unified_camera or self.unified_camera is None:
            return
        # Exposure
        if getattr(self, 'ids_exposure_auto', True):
            self.unified_camera.set_exposure_auto(True)
            print("[IDS Reopen] Exposure: auto")
        else:
            self.unified_camera.set_exposure(self.ids_exposure_us)
            print(f"[IDS Reopen] Exposure: {self.ids_exposure_us:.0f} µs")
        # Gain
        if getattr(self, 'ids_gain_auto', True):
            self.unified_camera.set_gain_auto(True)
            print("[IDS Reopen] Gain: auto")
        else:
            self.unified_camera.set_gain(self.ids_gain_db)
            print(f"[IDS Reopen] Gain: {self.ids_gain_db:.1f} dB")

    def _cb_ids_gain_change(self, value: float):
        """Handle IDS gain slider change."""
        self.ids_gain_db = float(value)
        self.ids_gain_auto = False
        if self._use_unified_camera and self.unified_camera is not None:
            self.unified_camera.set_gain(self.ids_gain_db)
            print(f"[IDS Gain] {self.ids_gain_db:.1f} dB")
        if self.gui:
            self.gui.sync_checkbox("ids_gain_auto", False)

    def _cb_ids_gain_auto_toggle(self, enabled: bool):
        """Handle IDS gain auto checkbox toggle."""
        self.ids_gain_auto = enabled
        if self._use_unified_camera and self.unified_camera is not None:
            self.unified_camera.set_gain_auto(enabled)
            print(f"[IDS Gain] Auto {'ON' if enabled else 'OFF'}")

    def _cb_ids_exposure_change(self, value: float):
        """Handle IDS exposure slider change."""
        requested = float(value)
        clamped = clamp_exposure_for_min_fps(requested, IDS_EXPOSURE_MIN_FPS)
        if clamped < requested:
            print(
                f"[IDS Exposure] Requested {requested:.0f} µs exceeds "
                f"{IDS_EXPOSURE_MIN_FPS:.0f} FPS limit; using {clamped:.0f} µs"
            )
        self.ids_exposure_us = clamped
        self.ids_exposure_auto = False
        if self._use_unified_camera and self.unified_camera is not None:
            self.unified_camera.set_exposure(self.ids_exposure_us)
            print(f"[IDS Exposure] {self.ids_exposure_us:.0f} µs")
        if self.gui:
            self.gui.sync_slider("ids_exposure_us", self.ids_exposure_us)
            self.gui.sync_checkbox("ids_exposure_auto", False)

    def _cb_ids_exposure_auto_toggle(self, enabled: bool):
        """Handle IDS exposure auto checkbox toggle."""
        self.ids_exposure_auto = enabled
        if self._use_unified_camera and self.unified_camera is not None:
            self.unified_camera.set_exposure_auto(enabled)
            print(f"[IDS Exposure] Auto {'ON' if enabled else 'OFF'}")

    def _cb_playback_speed_change(self, speed: float):
        """Handle playback speed change."""
        self.recorder.set_playback_speed(speed)
    
    def _cb_playback_pause(self):
        """Handle pause/resume toggle."""
        if self.recorder.is_paused():
            self.recorder.resume_playback()
        else:
            self.recorder.pause_playback()
            self.tracker.logger.flush()  # Phase 0: flush log on pause
        self._update_recording_ui()

    def _cb_playback_force_pause(self):
        """Pause playback without toggling — no-op if already paused."""
        if self.recorder.is_playing and not self.recorder.is_paused():
            self.recorder.pause_playback()
            self.tracker.logger.flush()
            self._update_recording_ui()
    
    def _cb_playback_next_frame(self):
        """Handle next frame button."""
        self.recorder.next_frame()
        self.tracker.logger.flush()  # Phase 0: flush log on frame step
    
    def _cb_playback_prev_frame(self):
        """Handle previous frame button."""
        self.recorder.prev_frame()
        self.tracker.logger.flush()  # Phase 0: flush log on frame step

    def _cb_issue_dialog_closed(self):
        """Refresh playback controls after the review dialog closes."""
        self._update_recording_ui()

    def _apply_startup_review_mode(self):
        """Apply optional startup playback automation for review sessions."""
        opts = self._startup_review
        if opts.slot is None:
            return

        if not self.recorder.start_playback(
                opts.slot,
                opts.recording_index,
                start_frame=opts.play_at_frame):
            print(f"[Review] Failed to start playback for slot {opts.slot}")
            return

        if opts.playback_speed > 0:
            self.recorder.set_playback_speed(opts.playback_speed)
        if opts.paused:
            self.recorder.pause_playback()
        if opts.play_at_frame is not None:
            print(f"[Review] Jumped to frame {opts.play_at_frame}")

        self._pause_at_frame_target = opts.pause_at_frame
        self._update_recording_ui()
        if self.gui:
            message = f"Review mode: slot {opts.slot}"
            if opts.recording_index:
                message += f" item {opts.recording_index}"
            if opts.play_at_frame is not None:
                message += f" play@{opts.play_at_frame}"
            if opts.pause_at_frame is not None:
                message += f" pause@{opts.pause_at_frame}"
            self.gui.show_toast(message, duration=3.5, color=(120, 200, 255))

    def _maybe_pause_at_target_frame(self):
        """Pause playback automatically when the requested frame is reached."""
        if self._pause_at_frame_target is None:
            return
        if not self.recorder.is_playing or self.recorder.is_paused():
            return
        if self.recorder.status.playback_frame < self._pause_at_frame_target:
            return

        target = self._pause_at_frame_target
        self._pause_at_frame_target = None
        self.recorder.pause_playback()
        self.tracker.logger.flush()
        self._update_recording_ui()
        print(f"[Review] Auto-paused at frame {target}")
        if self.gui:
            self.gui.show_toast(
                f"Paused at frame {target}",
                duration=3.0,
                color=(120, 200, 255),
            )

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
        self.roi_edit_mode = bool(enabled) and self.settings.roi_enabled
        self._sync_roi_ui()
        if self.gui:
            message = "ROI edit mode: drag on preview" if self.roi_edit_mode else "ROI edit mode: off"
            self.gui.show_toast(message, duration=2.0, color=(120, 200, 255))

    def _cb_roi_reset(self):
        frame_w, frame_h = self._roi_source_size
        self._set_roi_rect(0, 0, frame_w, frame_h)
        print("ROI reset to full frame")

    def _cb_roi_x_change(self, value: int):
        self._set_roi_rect(value, self.settings.roi_y, self.settings.roi_w, self.settings.roi_h)

    def _cb_roi_y_change(self, value: int):
        self._set_roi_rect(self.settings.roi_x, value, self.settings.roi_w, self.settings.roi_h)

    def _cb_roi_w_change(self, value: int):
        self._set_roi_rect(self.settings.roi_x, self.settings.roi_y, value, self.settings.roi_h)

    def _cb_roi_h_change(self, value: int):
        self._set_roi_rect(self.settings.roi_x, self.settings.roi_y, self.settings.roi_w, value)

    # ------------------------------------------------------------------
    # Recording callbacks
    # ------------------------------------------------------------------
    def _set_camera_frame_callback(self, callback):
        """Set frame callback on the ACTIVE camera (unified or legacy)."""
        if self._use_unified_camera and self.unified_camera is not None:
            self.unified_camera.set_frame_callback(callback)
        else:
            self.camera.set_frame_callback(callback)

    def _camera_frame_callback(self, frame: np.ndarray):
        """Called from camera thread for each captured frame. Used for recording."""
        if self.recorder.is_recording:
            self.recorder.write_frame(frame)
    
    def _cb_rec_live(self):
        """Switch to live camera mode."""
        self._source_transitioning = True
        try:
            self._set_camera_frame_callback(None)  # Clear recording callback
            self.recorder.go_live()
            self._pending_rec_slot = None
            self._rec_armed = False
            # Restart IDS acquisition (was stopped during playback)
            if self._use_unified_camera and self.unified_camera is not None:
                self.unified_camera.start_acquisition()
            # Restore live camera dimensions for preview aspect ratio
            if self.gui and self.unified_camera and self.unified_camera.is_open:
                self.gui.set_camera_dimensions(self.unified_camera.width, self.unified_camera.height)
            self._update_recording_ui()
            print("Switched to LIVE input")
        finally:
            self._source_transitioning = False

    def _apply_playback_dimensions(self):
        """Update GUI preview dimensions to match the video being played."""
        status = self.recorder.status
        if self.gui and status.playback_width > 0 and status.playback_height > 0:
            self.gui.set_camera_dimensions(status.playback_width, status.playback_height)

    def _cb_rec_toggle(self):
        """Toggle recording mode."""
        if self.recorder.is_recording:
            # Stop recording - clear callback first
            self._set_camera_frame_callback(None)
            filepath = self.recorder.stop_recording()
            self._pending_rec_slot = None
            self._rec_armed = False
            self._update_recording_ui()
            print(f"Recording stopped: {filepath}")
        elif self.recorder.is_live:
            if self._rec_armed:
                # Cancel armed state
                self._rec_armed = False
                self._update_recording_ui()
                print("REC cancelled")
            else:
                # Arm for recording - waiting for slot selection
                self._rec_armed = True
                self._update_recording_ui()
                print("REC armed. Select a slot to start recording.")
        else:
            # Playing - ignore REC
            print("REC: Switch to LIVE mode first.")

    def _cb_rec_slot_click(self, slot: int, ctrl_held: bool):
        """Handle slot button click."""
        if ctrl_held:
            # Show history menu
            slot_info = self.recorder.get_slot_info(slot)
            if slot_info.has_recordings:
                self.gui.show_slot_history_menu(
                    slot, 
                    slot_info.recordings, 
                    lambda fp: self._play_recording(slot, fp)
                )
            else:
                print(f"Slot {slot} is empty")
            return

        if self.recorder.is_recording:
            # Already recording - stop and switch?
            print(f"Recording in progress. Stop first.")
            return

        slot_info = self.recorder.get_slot_info(slot)
        
        # If REC is armed, clicking any slot starts recording to it
        if self._rec_armed and self.recorder.is_live:
            fps = CAMERA_FPS
            size = (self.camera.state.width, self.camera.state.height)
            # Wire up camera callback BEFORE starting recording
            self._set_camera_frame_callback(self._camera_frame_callback)
            if self.recorder.start_recording(slot, fps, size):
                self._rec_armed = False
                self._pending_rec_slot = slot
                self._update_recording_ui()
                print(f"Recording to slot {slot}...")
            else:
                print(f"Failed to start recording to slot {slot}")
                self._set_camera_frame_callback(None)  # Remove callback on failure
                self._rec_armed = False
                self._update_recording_ui()
            return
        
        # Normal click: play if has recordings, show empty otherwise
        if slot_info.has_recordings:
            self._start_playback_safe(slot)
        else:
            print(f"Slot {slot} is empty")

    def _start_playback_safe(self, slot: int, recording_index: int = 0):
        """Start playback with proper IDS acquisition pause and transition guard.

        Ensures USB3 DMA from the IDS camera is stopped before playback
        begins, preventing PCIe bus contention with CUDA uploads that
        can trigger driver-level IRQ conflicts (BSOD).
        """
        self._source_transitioning = True
        try:
            # Flush any in-flight GPU work from the previous source
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
            except Exception:
                pass

            # Stop IDS acquisition BEFORE opening the new VideoCapture.
            # This eliminates USB3 DMA traffic during the transition and
            # prevents the main loop from reading a stale IDS frame in
            # the brief LIVE window inside start_playback().
            if self._use_unified_camera and self.unified_camera is not None:
                self.unified_camera.stop_acquisition()

            if not self.recorder.start_playback(slot, recording_index):
                # Playback failed — restart acquisition
                if self._use_unified_camera and self.unified_camera is not None:
                    self.unified_camera.start_acquisition()
                return

            self._pending_rec_slot = None
            self._apply_playback_dimensions()
            self._update_recording_ui()
        finally:
            self._source_transitioning = False

    def _play_recording(self, slot: int, filepath: str):
        """Play a specific recording from history."""
        slot_info = self.recorder.get_slot_info(slot)
        for idx, (display, path) in enumerate(slot_info.recordings):
            if path == filepath:
                self._start_playback_safe(slot, idx)
                return
        print(f"Recording not found: {filepath}")

    def _update_recording_ui(self):
        """Update the recording UI to match current state."""
        if not self.gui:
            return
        
        status = self.recorder.status
        slots_info = [(i, self.recorder.get_slot_info(i).has_recordings) for i in range(1, 10)]
        
        # Map state to string, including armed state
        if self._rec_armed and status.state == RecorderState.LIVE:
            state_str = "armed"
        else:
            state_str = status.state.value  # 'live', 'recording', 'playing'
        
        current_slot = status.current_slot
        
        self.gui.update_recording_ui(
            state=state_str,
            current_slot=current_slot if current_slot > 0 else (self._pending_rec_slot or 0),
            slots_info=slots_info,
            recording_frames=status.recording_frames,
            playback_frame=status.playback_frame,
            playback_total=status.playback_total,
            playback_fps=status.playback_fps,
            paused=self.recorder.is_paused(),
            playback_speed=self.recorder._playback_speed,
        )

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
        path = "ids" if self._is_ids_camera_active() else "opencv"
        timing = self.timing or {}
        state = "STALL" if stalled else "OK"

        ids_read_age_s = float("inf")
        ids_acq_age_s = float("inf")
        ids_frame_count = 0
        ids_dropped = 0
        if self._is_ids_camera_active() and self.unified_camera is not None:
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
        if key == dpg.mvKey_E:
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
                self._update_recording_ui()
            context = self._cb_report_issue_request()
            if context and self.gui:
                self.gui.show_issue_report_dialog(context)
        if key == dpg.mvKey_S and (dpg.is_key_down(dpg.mvKey_LControl) or dpg.is_key_down(dpg.mvKey_RControl)):
            self._cb_save_config()

    # ------------------------------------------------------------------
    # Model Loading with Progress
    # ------------------------------------------------------------------
    def _load_model_with_progress(self, model_name: str, force_pt: bool = False) -> bool:
        """
        Load model with GUI progress modal.
        Blocks until complete.
        
        Args:
            model_name: Model name (e.g., "yolo11m-pose" or "yolo11m-pose.pt")
            force_pt: If True, skip TensorRT and use .pt directly
            
        Returns:
            True if successful, False otherwise
        """
        if self.gui is None:
            # No GUI, load directly (shouldn't happen in normal flow)
            print("Warning: Loading model without GUI")
            try:
                self.model = self.model_manager.load_model(model_name, force_pt=force_pt)
                self.processor.model = self.model
                self._model_loaded = True
                # Warmup inference to force TRT engine initialization
                import numpy as np
                dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
                _ = self.model(dummy_frame, verbose=False)
                return True
            except Exception as e:
                print(f"Failed to load model: {e}")
                return False

        base_name = model_name.replace('.pt', '').replace('.engine', '')
        
        print(f"[Model] Starting model load: {model_name} (force_pt={force_pt})...")

        # Pause frame processing while loading model
        self._model_loading = True
        
        # Close camera during model loading to prevent buffer overflow/stale connection
        camera_was_open = self.camera.state.is_open
        camera_source = self.camera.state.source
        if camera_was_open:
            print("[Model] Pausing camera during model load...")
            self.camera.close()
            self.camera.state.is_open = False
        
        # Show progress modal
        print("[Model] Showing loading modal...")
        self.gui.show_model_loading_modal(f"Preparing {model_name}...")
        dpg.render_dearpygui_frame()
        
        # Thread-safe containers
        import threading
        import queue
        load_result = {"model": None, "error": None, "done": False}
        progress_queue = queue.Queue()  # For thread-safe progress updates
        current_status = {"status": None, "message": "", "detail": ""}  # Track current status for animation
        
        def progress_callback(progress: ModelProgress):
            # Don't call GUI from background thread - put in queue instead
            status_messages = {
                ModelStatus.CHECKING: "Checking model files...",
                ModelStatus.DOWNLOADING: "Downloading model...",
                ModelStatus.EXPORTING_TENSORRT: "Building TensorRT engine (2-5 min)...",
                ModelStatus.LOADING: "Loading model into GPU...",
                ModelStatus.READY: "Model ready!",
                ModelStatus.ERROR: f"Error: {progress.error}",
            }
            message = status_messages.get(progress.status, progress.message)
            detail = progress.message if progress.status != ModelStatus.ERROR else ""
            # Include status so we know when to animate
            progress_queue.put((progress.status, message, progress.progress, detail))
        
        self.model_manager.set_progress_callback(progress_callback)
        
        def load_in_background():
            try:
                load_result["model"] = self.model_manager.load_model(model_name, force_pt=force_pt)
            except Exception as e:
                load_result["error"] = e
            load_result["done"] = True
        
        # Start loading in background thread
        load_thread = threading.Thread(target=load_in_background, daemon=True)
        load_thread.start()
        
        # Keep UI responsive while waiting for load to complete
        while not load_result["done"]:
            # Process any pending progress updates from the queue
            while not progress_queue.empty():
                try:
                    status, message, progress_val, detail = progress_queue.get_nowait()
                    current_status["status"] = status
                    current_status["message"] = message
                    current_status["detail"] = detail
                except queue.Empty:
                    break
            
            # Update UI - animate if exporting TensorRT, otherwise show real progress
            if current_status["status"] == ModelStatus.EXPORTING_TENSORRT:
                self.gui.update_model_loading_progress(
                    current_status["message"], 0.5, current_status["detail"], animate=True
                )
            elif current_status["message"]:
                self.gui.update_model_loading_progress(
                    current_status["message"], 0.5, current_status["detail"], animate=False
                )
            
            # Keep GPU stats updated during loading
            self.gui.update_gpu_stats()
            
            dpg.render_dearpygui_frame()
            time.sleep(0.03)  # ~30 FPS for smoother animation
        
        # Process any remaining progress updates
        while not progress_queue.empty():
            try:
                status, message, progress_val, detail = progress_queue.get_nowait()
                self.gui.update_model_loading_progress(message, progress_val, detail)
            except queue.Empty:
                break
        
        # Check result
        if load_result["error"] is not None:
            e = load_result["error"]
            print(f"Failed to load model: {e}")
            self.gui.update_model_loading_progress(f"Error: {e}", 0.0, "Will retry with PyTorch model")
            dpg.render_dearpygui_frame()
            time.sleep(2)
            
            # Try fallback to .pt
            if not force_pt:
                self.gui.update_model_loading_progress("Retrying with PyTorch model...", 0.5, "")
                dpg.render_dearpygui_frame()
                
                # Run fallback in thread too
                fallback_result = {"model": None, "error": None, "done": False}
                def fallback_load():
                    try:
                        fallback_result["model"] = self.model_manager.load_model(model_name, force_pt=True)
                    except Exception as e2:
                        fallback_result["error"] = e2
                    fallback_result["done"] = True
                
                fallback_thread = threading.Thread(target=fallback_load, daemon=True)
                fallback_thread.start()
                
                while not fallback_result["done"]:
                    self.gui.update_gpu_stats()
                    dpg.render_dearpygui_frame()
                    time.sleep(0.05)
                
                if fallback_result["model"] is not None:
                    self.model = fallback_result["model"]
                    self.processor.model = self.model
                    self.current_model = f"{model_name.replace('.pt', '').replace('.engine', '')}.pt"
                    self.current_model_name = model_name.replace('.pt', '').replace('.engine', '')
                    self._model_loaded = True
                    self.gui.update_engine_type_badge(False)
                    # Do warmup inference for fallback model too
                    print("[Model] Running warmup inference (fallback)...")
                    try:
                        import numpy as np
                        dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
                        _ = self.model(dummy_frame, verbose=False)
                        print("[Model] Warmup complete")
                    except Exception as e:
                        print(f"[Model] Warmup failed (non-critical): {e}")
                    self.gui.show_toast("Using PyTorch (fallback)", duration=4.0, color=(255, 180, 80))
                    time.sleep(0.3)
                    self.gui.hide_model_loading_modal()
                    self._model_loading = False
                    # Reopen camera if it was open before
                    if camera_was_open:
                        print("[Model] Reopening camera after model load...")
                        self._open_camera(camera_source)
                        # Give camera time to stabilize
                        time.sleep(0.5)
                        if self.camera.cap is not None:
                            for _ in range(10):
                                self.camera.cap.grab()
                        if self.recorder.is_playing:
                            self._apply_playback_dimensions()
                    return True
                else:
                    print(f"Fallback also failed: {fallback_result['error']}")
            
            self.gui.hide_model_loading_modal()
            self._model_loading = False
            # Reopen camera if it was open before
            if camera_was_open:
                print("[Model] Reopening camera after model load failure...")
                self._open_camera(camera_source)
                time.sleep(0.5)
                if self.camera.cap is not None:
                    for _ in range(10):
                        self.camera.cap.grab()
                if self.recorder.is_playing:
                    self._apply_playback_dimensions()
            return False
        
        # Success path
        self.model = load_result["model"]
        self.processor.model = self.model
        
        # Update current model tracking
        base_name = model_name.replace('.pt', '').replace('.engine', '')
        if self.model_manager.use_tensorrt and self.model_manager.engine_exists(base_name):
            self.current_model = f"{base_name}.engine"
        else:
            self.current_model = f"{base_name}.pt"
        self.current_model_name = base_name
        
        self._model_loaded = True
        
        # Update engine type badge
        self.gui.update_engine_type_badge(self.model_manager.is_using_tensorrt())
        
        # Show toast if TensorRT was expected but fell back to PyTorch
        fallback_reason = self.model_manager.get_tensorrt_fallback_reason()
        if fallback_reason and self.model_manager.use_tensorrt:
            self.gui.show_toast(fallback_reason, duration=5.0, color=(255, 180, 80))
        
        # Do a warmup inference to force TRT engine to fully initialize
        # This prevents lazy loading during camera capture which causes buffer overflow
        print("[Model] Running warmup inference...")
        self.gui.update_model_loading_progress("Warming up model...", 0.98, "First inference may take a moment")
        dpg.render_dearpygui_frame()
        try:
            import numpy as np
            dummy_frame = np.zeros((640, 640, 3), dtype=np.uint8)
            _ = self.model(dummy_frame, verbose=False)
            print("[Model] Warmup complete")
        except Exception as e:
            print(f"[Model] Warmup failed: {e}")
            # If TensorRT warmup fails (e.g. incompatible engine), fall back to PyTorch
            if self.model_manager.is_using_tensorrt() and not force_pt:
                print("[Model] TensorRT warmup failed — falling back to PyTorch model...")
                self.gui.update_model_loading_progress("TRT engine incompatible, loading PyTorch...", 0.5, str(e)[:80])
                dpg.render_dearpygui_frame()
                try:
                    self.model = self.model_manager.load_model(model_name, force_pt=True)
                    self.processor.model = self.model
                    self.current_model = f"{base_name}.pt"
                    self.gui.update_engine_type_badge(False)
                    self.gui.show_toast("TRT engine incompatible — using PyTorch", duration=5.0, color=(255, 180, 80))
                    # Warmup the fallback model
                    _ = self.model(dummy_frame, verbose=False)
                    print("[Model] PyTorch fallback warmup complete")
                except Exception as e2:
                    print(f"[Model] PyTorch fallback also failed: {e2}")
            else:
                print(f"[Model] Warmup failed (non-critical): {e}")
        
        # Brief pause to show "ready" message
        time.sleep(0.3)
        self.gui.hide_model_loading_modal()
        self._model_loading = False
        # Reopen camera if it was open before
        if camera_was_open:
            print(f"[Model] Reopening camera {camera_source}...")
            self._open_camera(camera_source)
            # Give camera time to stabilize and flush any stale frames
            time.sleep(0.5)
            if self.camera.cap is not None:
                for _ in range(10):
                    self.camera.cap.grab()
            # If playback is active, restore video dimensions (camera reopen overwrites them)
            if self.recorder.is_playing:
                self._apply_playback_dimensions()
        print(f"Model loading complete: {self.current_model_name}")
        return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self):
        print("Detecting cameras...")
        self._do_camera_refresh()

        print(f"Opening camera {self.camera.state.source}...")
        if not self._attempt_camera_connect(self.camera.state.source):
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
        dpg.show_viewport()
        self._sync_roi_ui()
        
        # Load last project using the unified project switch path
        # This ensures startup and runtime project switching use the same code
        last_project = self.config_store.read_last_project()
        startup_config = None
        if self._startup_review.config_path:
            startup_config = os.path.abspath(self._startup_review.config_path)
        elif self._startup_review.project:
            startup_config = self.config_store.latest_for_project(
                sanitize_project_name(self._startup_review.project)
            )
        elif last_project:
            startup_config = self.config_store.latest_for_project(last_project)
        
        if startup_config:
            print(f"Loading last project: {last_project}")
            if not self._execute_project_switch(startup_config):
                print("ERROR: Failed to load last project. Exiting.")
                return
        else:
            # No saved project - load default model
            print("No saved project, loading default model...")
            force_pt_default = not USE_TENSORRT
            if USE_TENSORRT:
                from model_manager import is_tensorrt_available
                base_default = YOLO_MODEL.replace('.pt', '').replace('.engine', '')
                if is_tensorrt_available() and not self.model_manager.engine_exists(base_default):
                    # Prompt user before starting long TRT build
                    if self._prompt_trt_build_sync(base_default):
                        print("User accepted TRT build at startup")
                        force_pt_default = False
                    else:
                        print("User declined TRT build at startup, using PyTorch")
                        force_pt_default = True
                elif not is_tensorrt_available():
                    force_pt_default = True
            if not self._load_model_with_progress(YOLO_MODEL, force_pt=force_pt_default):
                print("ERROR: Failed to load model. Exiting.")
                return
            self.current_model_name = YOLO_MODEL.replace('.pt', '').replace('.engine', '')
            if self.gui:
                self.gui.sync_combo("model", self.current_model_name)
                self.gui.set_trt_checkbox(self.model_manager.is_using_tensorrt())
            self._update_topbar_state()
        
        # Initialize recording UI
        self.recorder.set_project(self._current_project)
        self._update_recording_ui()
        self._apply_startup_review_mode()

        # Show CPU fallback badge immediately if GPU is not available
        if self.gui and self.processor:
            self.gui.update_compute_mode_badge(self.processor.gpu_fallback_reason or "")
            if self.processor.gpu_fallback_reason:
                self.gui.show_toast(
                    "/!\\ Running on CPU - no GPU acceleration",
                    duration=6.0,
                    color=(255, 120, 120),
                )

        print("Starting main loop...")
        self.running = True
        rec_ui_update_counter = 0
        while self.running and dpg.is_dearpygui_running():
            # Handle pending project switch (deferred from callback)
            # This is the unified path for project/config switching
            if self._pending_project_switch is not None:
                config_filepath = self._pending_project_switch
                self._pending_project_switch = None
                self._execute_project_switch(config_filepath)
                continue  # Restart loop after switch

            # Handle pending playback events (deferred from decoder thread)
            pending_playback_event = self._drain_pending_playback_event()
            if pending_playback_event is not None:
                self._handle_playback_start_event(pending_playback_event)
                continue  # Restart loop after tracker/session reset
            
            # Handle pending camera refresh (deferred from callback)
            if self._pending_camera_refresh:
                self._pending_camera_refresh = False
                self._do_camera_refresh()
                continue  # Restart loop after refresh

            if (
                not self.recorder.is_playing
                and not self.camera.state.is_open
                and self._next_camera_retry_time > 0.0
                and time.perf_counter() >= self._next_camera_retry_time
            ):
                self._attempt_camera_connect(self.camera.state.source)
                continue
            
            # Handle pending TRT build request (user clicked TRT checkbox, engine doesn't exist)
            if self._pending_trt_build is not None:
                model_to_build = self._pending_trt_build
                model_for_switch = self._pending_model_for_trt_build  # May be set if this came from model dropdown
                self._pending_trt_build = None
                self._pending_model_for_trt_build = None
                
                # Show prompt and wait for user choice
                user_choice = {"build_trt": None}
                
                def on_choice(build_trt: bool):
                    user_choice["build_trt"] = build_trt
                
                self.gui.show_tensorrt_prompt(model_to_build, on_choice)
                
                # Wait for user to click a button
                while user_choice["build_trt"] is None:
                    self.gui.update_gpu_stats()
                    dpg.render_dearpygui_frame()
                    time.sleep(0.016)
                
                # Clean up modal
                for _ in range(5):
                    dpg.render_dearpygui_frame()
                    time.sleep(0.02)
                
                if user_choice["build_trt"]:
                    # User wants to build, proceed with TRT loading
                    print(f"User chose to build TensorRT engine for {model_to_build}")
                    self._pending_trt_switch = True
                    self._pending_model_switch = model_to_build
                    self._model_loading = True  # Block processing until model is reloaded
                else:
                    # User cancelled TRT build
                    print(f"User cancelled TensorRT build for {model_to_build}")
                    self.gui.set_trt_checkbox(False)
                    
                    # If this was a model switch (not just TRT toggle on same model),
                    # still switch to the new model but with PyTorch
                    if model_for_switch and model_for_switch != self.current_model_name:
                        print(f"Switching to {model_for_switch} with PyTorch instead...")
                        self._pending_trt_switch = False
                        self._pending_model_switch = model_for_switch
                        self._model_loading = True  # Block processing until model is reloaded
                continue
            
            # Handle pending model switch (deferred from callback to avoid race condition)
            if self._pending_model_switch is not None:
                model_to_load = self._pending_model_switch
                trt_switch = self._pending_trt_switch
                self._pending_model_switch = None
                self._pending_trt_switch = None
                
                # Determine force_pt based on TRT switch state
                # If trt_switch is False, force PT. If True or None, let model manager decide.
                force_pt = (trt_switch == False)
                
                print(f"Switching to model: {model_to_load}... (TRT: {trt_switch}, force_pt: {force_pt})")
                if not self._load_model_with_progress(model_to_load, force_pt=force_pt):
                    print(f"Failed to switch to model {model_to_load}")
                    self._model_loaded = self.model is not None
                    # Revert dropdown to current model
                    if self.gui:
                        self.gui.update_model_dropdown(self.current_model_name)
                        # Also revert TRT checkbox if it was a TRT switch attempt
                        if trt_switch:
                            self.gui.set_trt_checkbox(False)
                else:
                    # Success - update TRT checkbox to match actual state
                    if self.gui:
                        is_trt = self.model_manager.is_using_tensorrt()
                        self.gui.set_trt_checkbox(is_trt)
                continue  # Restart loop after model switch
            
            # Skip processing while model is loading/switching
            if self._model_loading or self._source_transitioning:
                dpg.render_dearpygui_frame()
                time.sleep(0.016)  # ~60 FPS UI update
                continue

            self._poll_roi_mouse_interaction()
            self._update_roi_drag_from_mouse()
                
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
                    self._update_recording_ui()
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
                    if self.gui:
                        self.gui.render_frame()
                        # Still update GPU stats periodically when waiting for camera
                        self._update_gpu_stats_if_due()
                    time.sleep(0.033)
                    continue

                if self._ids_stream_timed_out():
                    print("[Camera] IDS stream stalled, reconnecting silently")
                    self._mark_camera_unavailable(self.camera.state.source, close_active=True)
                    self._schedule_camera_retry(delay=0.5)
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
                            and self._is_ids_camera_active()
                            and self.processor.gpu_path_active
                            and self._model_loaded
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
                    self._mark_camera_unavailable(self.camera.state.source, close_active=True)
                    self._schedule_camera_retry(delay=0.5)
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
            if not self._model_loaded or self.model is None:
                should_process = False
            
            # Skip YOLO inference if not in RUN state (Phase 3 gating)
            if self.gui and self.gui.get_system_state() != SystemState.RUN:
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
                    if self.model_manager.is_using_tensorrt() and self._is_trt_input_size_mismatch_error(exc):
                        base_name = self.current_model_name
                        print(f"[TRT] Detected engine/input size mismatch during switch: {exc}")
                        print(f"[TRT] Queuing safe reload for {base_name}@{self.settings.imgsz}...")
                        if self.model_manager.engine_exists(base_name):
                            self._pending_trt_switch = True
                        else:
                            self._pending_trt_switch = False
                            if self.gui:
                                self.gui.set_trt_checkbox(False)
                                self.gui.show_toast(
                                    f"No TRT for {self.settings.imgsz}px, using PyTorch",
                                    duration=3.0,
                                    color=(255, 200, 100),
                                )
                        self._pending_model_switch = base_name
                        self._model_loading = True
                        self._model_loaded = False
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
                model_name=self.current_model_name,
                yolo_imgsz=self.settings.imgsz,
                preview_enabled=self.preview_enabled,
                preview_render_scale=self.preview.render_scale,
                osc_enabled=self.osc_enabled,
                osc_ip=self.osc_ip,
                osc_port=self.osc_port,
                camera_running=self.camera.state.is_open,
                camera_reconnecting=self._camera_reconnecting,
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
                self._update_recording_ui()
            self._maybe_pause_at_target_frame()
            
            _dpg_t0 = time.perf_counter()
            dpg.render_dearpygui_frame()
            _dpg_render_ms = (time.perf_counter() - _dpg_t0) * 1000.0

            # Inject GUI overhead into timing dict for spike logging
            if self.timing:
                self.timing["dpg_render"] = _dpg_render_ms
                self.timing["gui_stats"] = _gui_stats_ms
                if 'camera_read_ms' not in self.timing:
                    self.timing["camera_read"] = camera_read_ms if 'camera_read_ms' in dir() else 0.0

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
