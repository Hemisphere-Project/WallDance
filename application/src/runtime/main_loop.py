"""The WallDance session driver: startup -> tick loop -> shutdown.

DECOMPOSITION_PLAN §5 Phase 4: ``WallDanceApp.run()`` moved here verbatim and
split into named tick stages; the old in-loop ``continue`` statements became
early stage returns. Statement order inside and across stages is exactly the
historical order -- NO reordering (the loop's ordering dependencies are
intentional; see the ops-heartbeat and camera-retry comments below).

Tick anatomy (one iteration of ``MainLoop.run()``):

    heartbeat -> drain commands -> pumps -> ui input -> acquire -> process
    -> preview -> events -> record -> render

A stage that consumes the tick (pending switch executed, camera waiting,
TRT reload queued, no composable preview input, ...) ends it early --
identical control flow to the old ``continue`` paths.

State split: cadence/diagnostic counters only the loop ever touched moved
onto ``MainLoop``; everything also read or written by command handlers,
controllers, or UI ports stays on the app and is reached through
``LoopHost`` (the explicit inventory of the loop's surface).
"""
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

import cv2
import numpy as np

from core.config import (
    CAMERA_HEIGHT,
    CAMERA_WIDTH,
    IDS_USE_GPU_DIRECT,
    OPS_HEIGHT_MIN_SAMPLES,
    OPS_HEIGHT_WINDOW_S,
    PROJECT_PICKER_ON_START,
    WEB_MONITOR_ENABLED,
    WEB_MONITOR_HOST,
    WEB_MONITOR_JPEG_QUALITY,
    WEB_MONITOR_MAX_FPS,
    WEB_MONITOR_PORT,
)
from core.config_store import sanitize_project_name
from core.pipeline import ScaledTrack
from core.visualization import draw_dancer
from runtime import api
from runtime.api import SystemState
from services.web_monitor import WebMonitor


class UiClientPort(Protocol):
    """The UI surface the loop drives (the DPG adapter today; any client
    satisfying this can host the runtime)."""

    def create_gui(self, config: Dict) -> None: ...

    def setup_viewport(self, roi) -> None: ...

    def is_running(self) -> bool: ...

    # Wait-path render (wraps toast expiry) vs bare loop-tail/pump render --
    # the split is deliberate, see ui/adapter.py.
    def render_frame(self) -> None: ...

    def render_frame_raw(self) -> None: ...

    def consume_layout_change(self) -> Optional[tuple]: ...

    def destroy(self) -> None: ...


class LoopHost(Protocol):
    """The exact WallDanceApp surface the main loop drives.

    Everything listed here is *shared* -- also read or written by command
    handlers, controllers, or UI ports outside the loop -- and therefore
    stays on the app. State only the loop touches lives on ``MainLoop``.
    """

    # command/event seam
    api: Any                  # runtime.api.RuntimeAPI
    bus: Any                  # runtime.api.EventBus
    ui: UiClientPort
    system_state: Any         # SystemState mirror (gates processing)
    running: bool

    # runtime controllers + the ui-side ROI/mask editor (never imported here)
    cameras: Any              # CameraController
    models: Any               # ModelController
    configs: Any              # ConfigManager
    recording: Any            # RecordingController
    calibration: Any          # CalibrationFlows
    roi: Any                  # ui.roi_mask_editor.RoiMaskEditor

    # core objects
    camera: Any               # CameraManager
    unified_camera: Any       # Optional[UnifiedCamera]
    _use_unified_camera: bool
    recorder: Any             # VideoRecorder
    processor: Any            # FrameProcessor
    enhancer: Any             # ImageEnhancer
    tracker: Any              # DancerTracker
    settings: Any             # ProcessingSettings

    # ops cluster (objects stay app-owned: controllers pause/resume them)
    _health: Any              # HealthMonitor
    _watchdog: Any            # LoopWatchdog
    _web_monitor: Any         # Optional[WebMonitor]; created by _startup()

    # preview / display state
    preview: Any              # PreviewGeometry
    preview_enabled: bool
    preview_stride: int
    preview_fps_cap: bool
    input_fps_cap: bool
    _input_fps_cap_interval: float
    _pending_preview_resize: bool
    show_skeleton: bool
    show_keypoints: bool
    show_bbox: bool
    show_trails: bool
    show_ids: bool

    # OSC / stats payload
    osc_enabled: bool
    osc_ip: str
    osc_port: int
    _ui_camera_type: str

    # per-session metrics + frame stashes (shared with command handlers)
    frame_count: int
    last_fps_time: float
    fps: float
    timing: Dict[str, float]
    latency_ms: float
    last_tracked: List[ScaledTrack]
    _total_frame_count: int
    _last_raw_frame: Optional[np.ndarray]
    _last_review_frame: Optional[np.ndarray]

    # startup
    _startup_review: Any      # ReviewStartupOptions

    def _get_gui_config(self) -> Dict: ...


@dataclass
class _Tick:
    """Per-iteration locals carried between tick stages (the old ``run()``
    body locals, unchanged in meaning)."""

    frame: Optional[np.ndarray] = None
    gpu_tensor: Any = None  # GPU tensor path for IDS camera, None for playback/OpenCV
    camera_read_ms: float = 0.0
    preview_source_frame: Optional[np.ndarray] = None
    display_frame_num: int = 0
    process_wall_ms: float = 0.0
    display_frame: Optional[np.ndarray] = None
    tracked: List[ScaledTrack] = field(default_factory=list)
    gui_stats_ms: float = 0.0


class MainLoop:
    """Runs one WallDance session against a ``LoopHost``."""

    def __init__(self, app: LoopHost):
        self.app = app
        # Loop-internal cadence/diagnostic state (moved from
        # WallDanceApp.__init__ in Phase 4 -- nothing else touches it).
        self._last_input_frame_time = 0.0
        self._last_preview_upload_time = 0.0
        self._last_gpu_stats_time = app.last_fps_time
        self._last_spike_log_time = 0.0
        self._last_diag_log_time = 0.0
        self._last_fresh_preview_time = time.time()
        self._last_fresh_frame_time = time.time()
        self._last_preview_stalled_state = False
        self._last_ops_tick = 0.0
        # Rolling (t, raw det height px) samples for the staleness alarm (⑤d)
        self._height_samples: deque = deque()
        self._rec_ui_update_counter = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def run(self):
        app = self.app
        if not self._startup():
            return
        print("Starting main loop...")
        app.running = True
        app._watchdog.start()
        while app.running and app.ui.is_running():
            self._tick()
        self._shutdown()

    def _startup(self) -> bool:
        """Bring the session up: camera -> GUI -> project -> recording UI ->
        web monitor. False = fatal (project/model load failed)."""
        app = self.app
        print("Detecting cameras...")
        app.cameras._do_camera_refresh()

        print(f"Opening camera {app.camera.state.source}...")
        if not app.cameras._attempt_camera_connect(app.camera.state.source):
            print(f"Warning: Camera {app.camera.state.source} not available, app will start without camera")

        print("Initializing GUI...")
        app.ui.create_gui(app._get_gui_config())
        app.ui.setup_viewport(app.roi)
        app.roi._sync_roi_ui()

        # Project startup (unified switch path). A deliberate picker (ROADMAP §7B)
        # unless a CLI override (--project / config path), the kiosk env var, or
        # the config flag says to auto-load the last project.
        last_project = app.configs.config_store.read_last_project()
        startup_config = None
        if app._startup_review.config_path:
            startup_config = os.path.abspath(app._startup_review.config_path)
        elif app._startup_review.project:
            startup_config = app.configs.config_store.latest_for_project(
                sanitize_project_name(app._startup_review.project)
            )
        cli_override = startup_config is not None
        autolaunch_env = os.environ.get(
            "WALLDANCE_AUTOLAUNCH_LAST", "").lower() in ("1", "true", "yes")
        show_picker = PROJECT_PICKER_ON_START and not cli_override and not autolaunch_env

        if show_picker and app.configs.config_store.list_projects():
            # Nothing loads until the operator picks (or "Start blank"); the
            # picker queues a project switch handled by the main loop.
            print("Showing startup project picker")
            app.configs._show_startup_project_picker()
        else:
            if startup_config is None and last_project:
                startup_config = app.configs.config_store.latest_for_project(last_project)
            if startup_config:
                print(f"Loading project: {last_project or os.path.basename(startup_config)}")
                if not app.configs._execute_project_switch(startup_config):
                    print("ERROR: Failed to load project. Exiting.")
                    return False
            elif not app.models._load_default_model_startup():
                print("ERROR: Failed to load model. Exiting.")
                return False

        # Initialize recording UI
        app.recorder.set_project(app.configs._current_project)
        app.recording._update_recording_ui()
        app.recording._apply_startup_review_mode()

        # Show CPU fallback badge immediately if GPU is not available
        if app.processor:
            app.bus.publish(api.ComputeModeBadge(app.processor.gpu_fallback_reason or ""))
            if app.processor.gpu_fallback_reason:
                app.bus.publish(api.Toast(
                    "/!\\ Running on CPU - no GPU acceleration",
                    6.0, (255, 120, 120)))

        # Smartphone monitor (focus + IR-lighting assist). Best-effort: a
        # failure here must never stop the app. See docs/ROADMAP.md (P0).
        if WEB_MONITOR_ENABLED:
            try:
                app._web_monitor = WebMonitor(
                    port=WEB_MONITOR_PORT,
                    host=WEB_MONITOR_HOST,
                    jpeg_quality=WEB_MONITOR_JPEG_QUALITY,
                    max_fps=WEB_MONITOR_MAX_FPS,
                )
                if not app._web_monitor.start():
                    app._web_monitor = None
            except Exception as e:  # noqa: BLE001 - monitor is non-critical
                print(f"[WebMonitor] disabled (startup error): {e}")
                app._web_monitor = None

        return True

    def _shutdown(self):
        """Tear the session down (order preserved from run())."""
        app = self.app
        # Final drain: commands queued during the last frame (notably Quit,
        # which flushes/closes the tracker logger) must still execute --
        # gui.stop() ends the dpg loop before the next tick would drain them.
        app.api.drain()
        app._watchdog.stop()
        if app._web_monitor is not None:
            app._web_monitor.stop()
            app._web_monitor = None
        app.recorder.close()
        if app.camera.cap is not None:
            app.camera.cap.release()
        app.ui.destroy()
        print("WallDance stopped.")

    # ------------------------------------------------------------------
    # One tick
    # ------------------------------------------------------------------
    def _tick(self):
        app = self.app
        self._ops_heartbeat()
        # The single command execution point (Phase 3 seam): everything
        # the UI queued since the last tick runs here, on this thread.
        app.api.drain()
        if self._tick_pumps():
            return
        self._tick_ui_input()
        t = self._tick_acquire()
        if t is None:
            return
        if not self._tick_process(t):
            return
        if not self._tick_preview(t):
            return
        self._tick_events(t)
        self._tick_record()
        self._tick_render(t)

    def _tick_pumps(self) -> bool:
        """Deferred-work pumps (project switch, playback event, camera
        refresh/retry, TRT build, model switch, load wait). True = the pump
        consumed this tick (the old ``continue``)."""
        app = self.app
        # Handle pending project switch (deferred from callback)
        # This is the unified path for project/config switching
        if app.configs._pending_project_switch is not None:
            config_filepath = app.configs._pending_project_switch
            app.configs._pending_project_switch = None
            app.configs._execute_project_switch(config_filepath)
            return True  # Restart loop after switch

        # Handle pending playback events (deferred from decoder thread)
        pending_playback_event = app.recording._drain_pending_playback_event()
        if pending_playback_event is not None:
            app.recording._handle_playback_start_event(pending_playback_event)
            return True  # Restart loop after tracker/session reset

        # Handle pending camera refresh (deferred from callback)
        if app.cameras._pending_camera_refresh:
            app.cameras._pending_camera_refresh = False
            app.cameras._do_camera_refresh()
            return True  # Restart loop after refresh

        if (
            not app.recorder.is_playing
            and not app.camera.state.is_open
            and app.cameras._next_camera_retry_time > 0.0
            and time.perf_counter() >= app.cameras._next_camera_retry_time
        ):
            app.cameras._attempt_camera_connect(app.camera.state.source)
            return True

        # Handle pending TRT build request (user clicked TRT checkbox, engine doesn't exist)
        if app.models._drain_pending_trt_build():
            return True  # Restart loop after build prompt

        # Handle pending model switch (deferred from callback to avoid race condition)
        if app.models._drain_pending_model_switch():
            return True  # Restart loop after model switch

        # Skip processing while model is loading/switching
        if app.models._model_loading or app.recording.source_transitioning:
            app.ui.render_frame_raw()
            time.sleep(0.016)  # ~60 FPS UI update
            return True

        return False

    def _tick_ui_input(self):
        """ROI/mask mouse polling + preview-resize/layout maintenance."""
        app = self.app
        app.roi._poll_roi_mouse_interaction()
        app.roi._update_roi_drag_from_mouse()
        app.roi._poll_mask_mouse_interaction()

        if app._pending_preview_resize and app.bus.ui_ready:
            app.bus.publish(api.PreviewResize(app.preview.width, app.preview.height))
            app._pending_preview_resize = False

        # Process layout changes (viewport resize or camera dimension change)
        layout = app.ui.consume_layout_change()
        if layout is not None:
            new_scale, cam_w, cam_h = layout
            cam_w = cam_w or CAMERA_WIDTH
            cam_h = cam_h or CAMERA_HEIGHT
            app.preview.render_scale = new_scale
            app.preview.width = max(1, int(cam_w * new_scale))
            app.preview.height = max(1, int(cam_h * new_scale))
            if app.processor:
                app.processor.set_preview_size(app.preview.width, app.preview.height)
            app._pending_preview_resize = True

    def _tick_acquire(self) -> Optional[_Tick]:
        """Acquire stage: one frame (or GPU tensor) from playback or camera,
        plus ROI source clamp + raw-frame stash. None ends the tick (waiting,
        reconnecting, playback rollover -- the old ``continue`` paths)."""
        app = self.app
        # Get frame from appropriate source
        frame = None
        gpu_tensor = None  # GPU tensor path for IDS camera, None for playback/OpenCV
        camera_read_ms = 0.0
        preview_source_frame = None

        if app.recorder.is_playing:
            # Read from video file
            frame = app.recorder.read_frame()
            if frame is not None:
                preview_source_frame = frame
            if frame is None:
                # No new frame yet -- decoder paces at video FPS, so we
                # wait briefly to avoid spinning and re-processing the
                # same frame (which would speed up playback and waste GPU).
                if app.recorder.is_playback_active:
                    app.ui.render_frame()
                    time.sleep(0.005)
                    return None
                # Decoder thread exited -- playback truly ended
                app.recorder.go_live()
                # Restart IDS acquisition (was stopped when playback started)
                if app._use_unified_camera and app.unified_camera is not None:
                    app.unified_camera.start_acquisition()
                if app.unified_camera and app.unified_camera.is_open:
                    app.bus.publish(api.CameraDimensions(
                        app.unified_camera.width, app.unified_camera.height))
                app.recording._update_recording_ui()
                return None
        else:
            # Read from camera - with safety checks for sudden disconnection
            # Use UnifiedCamera if available (supports IDS + OpenCV)
            if app._use_unified_camera and app.unified_camera is not None:
                camera_ready = app.unified_camera.is_open and not app.unified_camera.has_error()
            else:
                try:
                    camera_ready = app.camera.state.is_open and not app.camera.has_capture_error()
                except Exception:
                    camera_ready = False

            if not camera_ready:
                if app.camera.state.is_open:
                    # Capture/acquisition error while the camera is still
                    # marked open (e.g. the IDS acquisition thread died
                    # after 100 consecutive errors). The retry pump only
                    # runs when is_open is False, so without this the loop
                    # idles here forever with a green badge. Funnel into
                    # the existing reconnect state machine.
                    print("[Camera] Capture error while marked open - scheduling reconnect")
                    app.cameras._mark_camera_unavailable(app.camera.state.source,
                                                         close_active=True)
                    app.cameras._schedule_camera_retry(delay=0.5)
                    return None
                app.ui.render_frame()
                # Still update GPU stats periodically when waiting for camera
                self._update_gpu_stats_if_due()
                time.sleep(0.033)
                return None

            if app.cameras._ids_stream_timed_out():
                print("[Camera] IDS stream stalled, reconnecting silently")
                app.cameras._mark_camera_unavailable(app.camera.state.source, close_active=True)
                app.cameras._schedule_camera_retry(delay=0.5)
                return None

            # Read frame (BGR numpy or GPU tensor for IDS)
            gpu_tensor = None
            frame = None
            camera_read_ms = 0.0

            try:
                _cam_t0 = time.perf_counter()
                if app._use_unified_camera and app.unified_camera is not None:
                    if (
                        IDS_USE_GPU_DIRECT
                        and app.cameras._is_ids_camera_active()
                        and app.processor.gpu_path_active
                        and app.models._model_loaded
                    ):
                        ret, gpu_tensor = app.unified_camera.read_gpu()
                    else:
                        ret, frame = app.unified_camera.read()
                else:
                    ret, frame = app.camera.read()
                camera_read_ms = (time.perf_counter() - _cam_t0) * 1000.0
            except Exception as e:
                print(f"Camera read exception: {e}")
                ret, frame, gpu_tensor = False, None, None
                camera_read_ms = 0.0

            # ret=False means camera error, ret=True with frame=None means still initializing
            if not ret:
                print("Camera read failed, marking as unavailable")
                app.cameras._mark_camera_unavailable(app.camera.state.source, close_active=True)
                app.cameras._schedule_camera_retry(delay=0.5)
                app.ui.render_frame()
                # Update GPU stats
                self._update_gpu_stats_if_due()
                return None

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
                app.ui.render_frame()
                # Update GPU stats periodically
                self._update_gpu_stats_if_due()
                time.sleep(0.01)
                return None

            if frame is not None:
                preview_source_frame = frame
            elif gpu_tensor is not None and app.unified_camera is not None:
                preview_source_frame = app.unified_camera.get_last_cpu_frame()

            # Input FPS cap: wait if too soon since last processed frame
            if app.input_fps_cap and (frame is not None or gpu_tensor is not None):
                now = time.perf_counter()
                elapsed = now - self._last_input_frame_time
                if elapsed < app._input_fps_cap_interval:
                    remaining = app._input_fps_cap_interval - elapsed
                    app.ui.render_frame()
                    time.sleep(remaining)
                self._last_input_frame_time = time.perf_counter()

            # Update frame acquisition timestamp for stall detection.
            # Must happen here (not just in diag callback) because the diag
            # heartbeat may fire from a "waiting" iteration where frame=None.
            self._last_fresh_frame_time = time.time()

            # Recording is handled via camera callback thread - no write_frame here

        if preview_source_frame is not None:
            src_h, src_w = preview_source_frame.shape[:2]
            if (src_w, src_h) != app.roi.state.source_size:
                app.roi._clamp_roi_to_source(src_w, src_h, sync_ui=True)
        elif gpu_tensor is not None:
            _, _, src_h, src_w = gpu_tensor.shape
            if (src_w, src_h) != app.roi.state.source_size:
                app.roi._clamp_roi_to_source(src_w, src_h, sync_ui=True)

        # Stash raw frame for BG capture (before any processing)
        # Works for both camera and playback sources
        if frame is not None:
            app._last_raw_frame = frame

        return _Tick(frame=frame, gpu_tensor=gpu_tensor,
                     camera_read_ms=camera_read_ms,
                     preview_source_frame=preview_source_frame)

    def _tick_process(self, t: _Tick) -> bool:
        """Process stage: YOLO + tracking via the pipeline (RUN / calibration)
        or the STANDBY preview path. False ends the tick (TRT-mismatch reload
        queued, no processable input)."""
        app = self.app
        should_process = True

        # Skip YOLO inference if model not loaded
        if not app.models._model_loaded or app.models.model is None:
            should_process = False

        # Skip YOLO inference if not in RUN state (Phase 3 gating).
        # Exception: a scene calibration forces YOLO on (even in Standby /
        # during playback) so it can measure detection heights.
        if (app.system_state != SystemState.RUN
                and not app.calibration._calibrating and not app.calibration._calibrating2):
            should_process = False

        # Phase 0: compute display frame number for tracker logging
        # (set outside should_process so overlay works even in STANDBY)
        if app.recorder.is_playing:
            t.display_frame_num = app.recorder.status.playback_frame
        else:
            app._total_frame_count += 1
            t.display_frame_num = app._total_frame_count

        if should_process:
            process_wall_ms = 0.0

            try:
                _proc_t0 = time.perf_counter()
                _need_preview = app.preview_enabled and (app.frame_count % app.preview_stride == 0)
                if t.gpu_tensor is not None:
                    # Pass cached CPU frame for MOG2 motion detection
                    _raw_frame = None
                    if app.unified_camera is not None:
                        _raw_frame = app.unified_camera.get_last_cpu_frame()
                    tracked, display_frame, timing, latency_ms = app.processor.process_gpu_direct(
                        t.gpu_tensor, need_preview=_need_preview, frame_number=t.display_frame_num,
                        raw_frame=_raw_frame
                    )
                elif t.frame is not None:
                    tracked, display_frame, timing, latency_ms = app.processor.process(
                        t.frame, need_preview=_need_preview, frame_number=t.display_frame_num
                    )
                else:
                    time.sleep(0.001)
                    return False
                process_wall_ms = (time.perf_counter() - _proc_t0) * 1000.0
            except AssertionError as exc:
                if app.models.model_manager.is_using_tensorrt() and app.models._is_trt_input_size_mismatch_error(exc):
                    base_name = app.models.current_model_name
                    print(f"[TRT] Detected engine/input size mismatch during switch: {exc}")
                    print(f"[TRT] Queuing safe reload for {base_name}@{app.settings.imgsz}...")
                    if app.models.model_manager.engine_exists(base_name):
                        app.models._pending_trt_switch = True
                    else:
                        app.models._pending_trt_switch = False
                        app.bus.publish(api.TrtCheckbox(False))
                        app.bus.publish(api.Toast(
                            f"No TRT for {app.settings.imgsz}px, using PyTorch",
                            3.0, (255, 200, 100)))
                    app.models._pending_model_switch = base_name
                    app.models._model_loading = True
                    app.models._model_loaded = False
                    time.sleep(0.01)
                    return False
                raise
            app.last_tracked = tracked
            if display_frame is not None:
                app._last_review_frame = display_frame.copy()
            elif t.preview_source_frame is not None:
                app._last_review_frame = t.preview_source_frame.copy()
            app.timing = timing
            app.timing["camera_read"] = t.camera_read_ms
            app.timing["process_wall"] = process_wall_ms
            app.latency_ms = latency_ms
            if app.calibration._calibrating:
                app.calibration._step_calibration(tracked, process_wall_ms)
            elif app.calibration._calibrating2:
                app.calibration._step_calib2(tracked, process_wall_ms)
        else:
            process_wall_ms = 0.0
            # No processing (STANDBY mode) - show preview without YOLO.
            # For IDS GPU-direct: use cached CPU frame instead of expensive
            # GPU→CPU download which causes PCIe/USB3 contention.
            if t.gpu_tensor is not None:
                # Use CPU-cached frame from read_gpu() -- zero GPU download
                if app.unified_camera is not None:
                    cached = app.unified_camera.get_last_cpu_frame()
                    if cached is not None:
                        display_frame = cached
                    else:
                        display_frame = None
                else:
                    display_frame = None
            elif t.frame is not None:
                # Apply BG subtraction in STANDBY mode too (for preview)
                standby_frame = t.frame
                if (app.settings.bg_subtract_enabled and
                        app.processor.bg_subtractor.has_reference):
                    standby_frame = app.processor.bg_subtractor.apply_cpu(
                        t.frame, app.settings.bg_subtract_sensitivity
                    )

                should_enhance = app.settings.enhance_enabled
                if should_enhance and not app.settings.enhance_lite and not app.settings.enhance_force:
                    # Check brightness threshold (same logic as pipeline)
                    gray = cv2.cvtColor(standby_frame, cv2.COLOR_BGR2GRAY)
                    brightness = float(np.mean(gray))
                    if brightness >= app.settings.brightness_threshold:
                        should_enhance = False

                if should_enhance:
                    if app.settings.enhance_lite:
                        display_frame = app.enhancer.enhance_simple(standby_frame)
                    else:
                        display_frame, _ = app.enhancer.enhance(standby_frame)
                else:
                    display_frame = standby_frame.copy()
            else:
                display_frame = None
            tracked = app.last_tracked

        t.process_wall_ms = process_wall_ms
        t.display_frame = display_frame
        t.tracked = tracked
        return True

    def _tick_preview(self, t: _Tick) -> bool:
        """Preview stage: compose + publish the preview frame (stride/rate
        gated) + the stall diagnostics that ride on it. False ends the tick
        (no composable preview input -- the old ``continue`` paths)."""
        app = self.app
        if app.preview_enabled and (app.frame_count % app.preview_stride == 0):
            timing = dict(app.timing) if app.timing else {}

            # Determine whether a fresh preview frame is available.
            # GPU pipeline sets timing['preview_new'] based on its rate limiter.
            # For CPU pipeline or when no timing, fall back to FPS cap check.
            if 'preview_new' in timing:
                preview_new = timing['preview_new']
            elif app.preview_fps_cap:
                now_pv = time.time()
                preview_new = (now_pv - self._last_preview_upload_time) >= 0.1
            else:
                preview_new = True

            preview_input_available = t.display_frame is not None or (
                (not app.settings.roi_enabled) and t.preview_source_frame is not None
            )
            if preview_new and preview_input_available:
                render_w, render_h = app.preview.width, app.preview.height

                if app.settings.roi_enabled and t.preview_source_frame is not None:
                    src_h, src_w = t.preview_source_frame.shape[:2]
                elif app.settings.roi_enabled:
                    src_w, src_h = app.roi.state.source_size
                elif 'original_w' in timing and 'original_h' in timing:
                    src_w = int(timing['original_w'])
                    src_h = int(timing['original_h'])
                else:
                    preview_base = t.display_frame if t.display_frame is not None else t.preview_source_frame
                    if preview_base is None:
                        return False
                    src_h, src_w = preview_base.shape[:2]

                if app.settings.roi_enabled:
                    preview_base = app.roi._compose_roi_preview(t.display_frame, src_w, src_h)
                else:
                    preview_base = t.display_frame if t.display_frame is not None else t.preview_source_frame

                if preview_base is None:
                    return False

                # Resize preview source to render target
                dh, dw = preview_base.shape[:2]
                if dw == render_w and dh == render_h:
                    preview_frame = np.ascontiguousarray(preview_base)
                else:
                    preview_frame = cv2.resize(preview_base, (render_w, render_h))

                # Push the CLEAN preview (pre-overlay) to the smartphone
                # monitor so focus/lighting metrics are not polluted by the
                # skeleton/bbox overlays. update_frame() copies internally.
                if app._web_monitor is not None:
                    app._web_monitor.update_frame(preview_frame)

                scale_x = render_w / src_w if src_w > 0 else 1.0
                scale_y = render_h / src_h if src_h > 0 else 1.0

                if scale_x != 1.0 or scale_y != 1.0:
                    scaled_tracks = []
                    for track in t.tracked:
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
                    scaled_tracks = t.tracked
                    thickness_scale = 1.0
                    ruler_scale = 1.0

                preview_t0 = time.time()
                if app.settings.roi_enabled:
                    app.roi._draw_roi_mask(preview_frame, src_w, src_h)
                app.roi._draw_exclusion_overlay(preview_frame, src_w, src_h)
                for track in scaled_tracks:
                    draw_dancer(
                        preview_frame,
                        track,
                        show_skeleton=app.show_skeleton,
                        show_keypoints=app.show_keypoints,
                        show_bbox=app.show_bbox,
                        show_trail=app.show_trails,
                        show_id=app.show_ids,
                        thickness_scale=thickness_scale,
                    )
                self._draw_height_ruler(preview_frame, scale=ruler_scale, thickness_scale=thickness_scale)
                # Phase 0: frame number overlay (top-right)
                self._draw_frame_number_overlay(preview_frame, t.display_frame_num)
                if app.settings.roi_enabled:
                    app.roi._draw_roi_note(preview_frame, src_w, src_h)
                app._last_review_frame = preview_frame.copy()
                preview_draw_ms = (time.time() - preview_t0) * 1000
                upload_t0 = time.time()
                app.bus.publish(api.PreviewFrame(preview_frame))
                preview_upload_ms = (time.time() - upload_t0) * 1000
                self._last_preview_upload_time = time.time()
                timing["preview_draw"] = preview_draw_ms
                timing["preview_upload"] = preview_upload_ms
                app.timing = timing
                self._log_timing_spikes_if_any(app.timing)
                self._log_runtime_diag_if_stalled(
                    camera_read_ms=t.camera_read_ms,
                    process_wall_ms=t.process_wall_ms,
                    preview_new=bool(preview_new),
                    frame_available=t.display_frame is not None,
                    gpu_tensor_available=t.gpu_tensor is not None,
                    camera_waiting=False,
                )
            else:
                # No fresh preview -- still log diagnostics
                self._log_runtime_diag_if_stalled(
                    camera_read_ms=t.camera_read_ms,
                    process_wall_ms=t.process_wall_ms,
                    preview_new=False,
                    frame_available=(t.frame is not None or t.display_frame is not None),
                    gpu_tensor_available=t.gpu_tensor is not None,
                    camera_waiting=False,
                )
        else:
            if app.timing:
                app.timing["preview_draw"] = 0.0
                app.timing["preview_upload"] = 0.0
                self._log_timing_spikes_if_any(app.timing)
                self._log_runtime_diag_if_stalled(
                    camera_read_ms=t.camera_read_ms,
                    process_wall_ms=t.process_wall_ms,
                    preview_new=False,
                    frame_available=t.frame is not None,
                    gpu_tensor_available=t.gpu_tensor is not None,
                    camera_waiting=False,
                )
        return True

    def _tick_events(self, t: _Tick):
        """Events stage: FPS bookkeeping + StatsTick / BgStatus on the bus."""
        app = self.app
        app.frame_count += 1
        now = time.time()
        if now - app.last_fps_time >= 1.0:
            app.fps = app.frame_count / (now - app.last_fps_time)
            app.frame_count = 0
            app.last_fps_time = now
        self._update_gpu_stats_if_due(now)

        # Get brightness from pipeline timing (already calculated there)
        # Fall back to enhancer status if not available
        brightness = app.timing.get("brightness", 0)
        if brightness == 0:
            brightness = app.enhancer.get_status().get("brightness", 0)
        enhance_bypassed = (
            app.settings.enhance_enabled
            and not app.settings.enhance_lite
            and brightness >= app.settings.brightness_threshold
        )
        _stats_t0 = time.perf_counter()
        app.bus.publish(api.StatsTick(dict(
            fps=app.fps,
            num_dancers=len(t.tracked),
            latency_ms=app.latency_ms,
            brightness=brightness,
            timing=app.timing,
            input_res=(app.camera.state.width, app.camera.state.height),
            preview_tex=(app.preview.width, app.preview.height),
            model_name=app.models.current_model_name,
            yolo_imgsz=app.settings.imgsz,
            preview_enabled=app.preview_enabled,
            preview_render_scale=app.preview.render_scale,
            osc_enabled=app.osc_enabled,
            osc_ip=app.osc_ip,
            osc_port=app.osc_port,
            camera_running=app.camera.state.is_open,
            camera_reconnecting=app.cameras._camera_reconnecting,
            camera_type=app._ui_camera_type,
            enhance_bypassed=enhance_bypassed,
            gpu_fallback_reason=app.processor.gpu_fallback_reason or "",
        )))
        t.gui_stats_ms = (time.perf_counter() - _stats_t0) * 1000.0

        # Lagged-tap latency (Track X §7).  Published on change (incl. fps drift)
        # so the phase-⑥ readout and /walldance/meta/latency_ms stay current
        # without per-frame spam.  latency = L / fps; 0 when the tap is inactive.
        s = app.settings
        lag_active = (bool(getattr(s, "output_lagged_enabled", False))
                      and int(getattr(s, "output_smoothing_l", 1)) > 1)
        latency_ms = (int(s.output_smoothing_l) / app.fps * 1000.0
                      if lag_active and app.fps > 0 else 0.0)
        last = getattr(app, "_output_latency_pub", None)
        if (last is None or abs(latency_ms - last) > 1.0
                or (last > 0) != (latency_ms > 0)):
            app._output_latency_pub = latency_ms
            app.bus.publish(api.OutputLatency(latency_ms, lag_active))
            if app.osc_enabled and app.processor.osc is not None:
                app.processor.osc.send_latency_ms(latency_ms)

        # Update BG subtraction status (piggyback on stats update cycle)
        bg = app.processor.bg_subtractor
        if bg.has_reference:
            fg_ratio = app.timing.get("bg_fg_ratio", bg.foreground_ratio)
            is_mismatched = app.timing.get("bg_mismatched", bg.is_mismatched)
            app.bus.publish(api.BgStatus(
                True, app.settings.bg_subtract_enabled,
                fg_ratio, is_mismatched))

    def _tick_record(self):
        """Record stage: periodic recording-UI refresh + pause-at-frame.
        (Recording itself rides the camera callback thread, not the loop.)"""
        app = self.app
        # Update recording UI periodically (every 10 frames to avoid overhead)
        self._rec_ui_update_counter += 1
        if self._rec_ui_update_counter >= 10:
            self._rec_ui_update_counter = 0
            app.recording._update_recording_ui()
        app.recording._maybe_pause_at_target_frame()

    def _tick_render(self, t: _Tick):
        """Render stage: one UI frame + GUI overhead into the timing dict."""
        app = self.app
        _dpg_t0 = time.perf_counter()
        app.ui.render_frame_raw()
        _dpg_render_ms = (time.perf_counter() - _dpg_t0) * 1000.0

        # Inject GUI overhead into timing dict for spike logging
        if app.timing:
            app.timing["dpg_render"] = _dpg_render_ms
            app.timing["gui_stats"] = t.gui_stats_ms
            if 'camera_read_ms' not in app.timing:
                app.timing["camera_read"] = t.camera_read_ms

    # ------------------------------------------------------------------
    # Loop-only helpers (moved verbatim from WallDanceApp in Phase 4)
    # ------------------------------------------------------------------
    def _update_gpu_stats_if_due(self, now: Optional[float] = None, interval_s: float = 1.0):
        """Update top-bar GPU stats at a fixed cadence without affecting FPS timing."""
        app = self.app
        if not app.bus.ui_ready:
            return
        t = now if now is not None else time.time()
        if t - self._last_gpu_stats_time >= interval_s:
            self._last_gpu_stats_time = t
            app.bus.publish(api.GpuStats())

    def _ops_heartbeat(self):
        """Watchdog beat + 1 Hz health tick.

        Must run on EVERY main-loop iteration - including the camera-down /
        no-frame continue paths, which never reach the FPS block at the loop
        tail. That guarantee is what lets the camera-down alert keep ringing
        during an outage.
        """
        app = self.app
        app._watchdog.beat()
        now = time.time()
        if now - self._last_ops_tick < 1.0:
            return
        self._last_ops_tick = now
        in_run = bool(app.bus.ui_ready and app.system_state == SystemState.RUN)
        # Person-height staleness input (⑤d): 1 Hz sample of RAW detection
        # heights (pre-size-gate, original-space px) over a rolling window.
        for h in getattr(app.processor, "last_raw_det_heights", ()):
            self._height_samples.append((now, h))
        cutoff = now - OPS_HEIGHT_WINDOW_S
        while self._height_samples and self._height_samples[0][0] < cutoff:
            self._height_samples.popleft()
        height_median = None
        height_gate = None
        if len(self._height_samples) >= OPS_HEIGHT_MIN_SAMPLES:
            ph = float(app.settings.person_height_px)
            height_gate = (ph * float(app.settings.person_height_min_ratio),
                           ph * float(app.settings.person_height_max_ratio))
            height_median = float(np.median([h for _t, h in self._height_samples]))
        try:
            alerts = app._health.tick(
                now,
                fps=app.fps,
                n_tracked=len(app.last_tracked),
                in_run=in_run,
                model_ready=app.models._model_loaded and not app.models._model_loading,
                camera_open=app.camera.state.is_open,
                camera_reconnecting=app.cameras._camera_reconnecting,
                playback_active=app.recorder.is_playing,
                n_over_cap=app.tracker.last_over_cap,
                height_median=height_median,
                height_gate=height_gate,
            )
        except Exception as e:  # noqa: BLE001 - monitoring must never kill the loop
            print(f"[Alert] health tick failed: {e}")
            return
        for alert in alerts:
            self._emit_ops_alert(alert)

    def _emit_ops_alert(self, alert):
        app = self.app
        print(f"[Alert] {alert.message}")
        app.bus.publish(api.Alert(alert.kind, alert.message, alert.data))
        try:
            app.tracker.logger.log("OPS_ALERT", {"kind": alert.kind, **alert.data})
        except Exception:
            pass

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
                print(f"[Budget] {', '.join(parts)}  (FPS={self.app.fps:.1f})")

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
        app = self.app
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
        path = "ids" if app.cameras._is_ids_camera_active() else "opencv"
        timing = app.timing or {}
        state = "STALL" if stalled else "OK"

        ids_read_age_s = float("inf")
        ids_acq_age_s = float("inf")
        ids_frame_count = 0
        ids_dropped = 0
        if app.cameras._is_ids_camera_active() and app.unified_camera is not None:
            try:
                ids_read_age_s = float(app.unified_camera.get_last_frame_age_s())
                ids_acq_age_s = float(app.unified_camera.get_last_acquired_age_s())
                ids_frame_count, ids_dropped = app.unified_camera.get_ids_counters()
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

    def _draw_height_ruler(self, frame, scale: float = 1.0, thickness_scale: float = 1.0):
        h, w = frame.shape[:2]
        height_px = int(self.app.settings.person_height_px * scale)
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
        if self.app.recorder.is_playing:
            total = self.app.recorder.status.playback_total
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
