"""Camera retry/backoff, IDS↔OpenCV swap and IDS parameter orchestration
peeled from WallDanceApp.

DECOMPOSITION_PLAN §5 Phase 2 (3). Method bodies moved verbatim from
app.py; ``self.<app attribute>`` references renamed to constructor-injected
dependencies. The camera objects themselves (legacy ``CameraManager`` +
optional ``UnifiedCamera``) remain app-owned and are injected — the
controller owns the orchestration state: retry/backoff fields, the
``ids_*`` parameter cache (re-applied after reopen), and the deferred
refresh flag drained by the main loop.
"""
from __future__ import annotations

import time
import traceback
from typing import Callable, List, Optional, Protocol

from camera.camera_manager import CameraManager
from camera.ids_camera import (
    IDSCamera,
    IDS_EXPOSURE_MIN_FPS,
    IDS_PEAK_AVAILABLE,
    CameraSource,
    clamp_exposure_for_min_fps,
)
from core.config import CAMERA_INDEX, IDS_RATIO


class CameraUiPort(Protocol):
    """The GUI surface the camera cluster needs (no dpg types)."""

    @property
    def available(self) -> bool: ...

    def update_camera_sources(self, sources, current, unavailable) -> None: ...

    def update_camera_status(self, is_open: bool, source: str, reconnecting: bool) -> None: ...

    def set_camera_type(self, camera_type: str) -> None: ...

    def set_camera_dimensions(self, width: int, height: int) -> None: ...

    def sync_checkbox(self, name: str, value: bool) -> None: ...

    def sync_slider(self, name: str, value: float) -> None: ...


class CameraController:
    """Owns camera connect/retry/swap flows and the IDS parameter cache."""

    def __init__(
        self,
        camera,
        unified_camera,
        use_unified: bool,
        ui: CameraUiPort,
        preview_geometry: Callable[[int, int], None],
        repush_preview_size: Callable[[], None],
        is_running: Callable[[], bool],
    ) -> None:
        self.camera = camera
        self.unified_camera = unified_camera
        self._use_unified_camera = use_unified
        self.ui = ui
        self.preview_geometry = preview_geometry
        self.repush_preview_size = repush_preview_size
        self.is_running = is_running

        self.ids_ratio: float = IDS_RATIO  # Current IDS crop ratio (W/H)
        self.ids_gain_db: float = 0.0        # Current IDS gain (dB), 0 = default
        self.ids_exposure_us: float = 10000.0  # Current IDS exposure (µs)

        self._camera_retry_backoff_s = 1.0
        self._camera_retry_max_s = 5.0
        self._next_camera_retry_time = 0.0
        self._camera_reconnecting = False
        self._ids_disconnect_timeout_s = 2.5
        self._last_camera_open_time = 0.0
        self._pending_camera_refresh = False

    # ------------------------------------------------------------------
    # Source bookkeeping / retry-backoff
    # ------------------------------------------------------------------
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

        if self.ui.available:
            self.ui.update_camera_sources(self._camera_ui_sources(), source, self.camera.state.unavailable)
            self.ui.update_camera_status(False, source, reconnecting=self._camera_reconnecting)
            self.ui.set_camera_type("")

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

    # ------------------------------------------------------------------
    # Source switch / refresh
    # ------------------------------------------------------------------
    def _cb_camera_change(self, value: str):
        source = self._normalize_camera_source(value)
        if source == self.camera.state.source and self.camera.state.is_open:
            return
        print(f"Selecting camera source: {source}")
        try:
            self._attempt_camera_connect(source)
        except Exception as e:
            print(f"[Camera] Switch to '{source}' crashed: {e}")
            traceback.print_exc()
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
        if self.ui.available:
            self.ui.update_camera_sources(self._camera_ui_sources(), current_source, unavailable_sources)

        if self.is_running() and not was_open and current_source in self.camera.state.available:
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
                if self.ui.available:
                    self.ui.update_camera_status(False, current_source, reconnecting=self._camera_reconnecting)

    # ------------------------------------------------------------------
    # Open paths (unified / legacy)
    # ------------------------------------------------------------------
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

            if self.ui.available:
                self.ui.update_camera_sources(self._camera_ui_sources(), source, self.camera.state.unavailable)
                self.ui.update_camera_status(True, source, reconnecting=False)
                self.ui.set_camera_type(cam_type_str)

            # Update preview geometry (+ GUI dimensions; layout will recompute)
            self.preview_geometry(self.unified_camera.width, self.unified_camera.height)
        else:
            self.camera.state.is_open = False
            self._last_camera_open_time = 0.0
            if source not in self.camera.state.unavailable:
                self.camera.state.unavailable.append(source)

            if self.ui.available:
                self.ui.update_camera_sources(self._camera_ui_sources(), source, self.camera.state.unavailable)
                self.ui.update_camera_status(False, source, reconnecting=self._camera_reconnecting)
                self.ui.set_camera_type("")
            print(f"[Camera] Failed to open: {source}")

        return opened

    def _open_camera_legacy(self, source: str) -> bool:
        """Open camera using legacy CameraManager (OpenCV only)."""
        source = self._normalize_camera_source(source)
        opened = self.camera.open(source)
        state = self.camera.state
        if opened:
            self._last_camera_open_time = time.perf_counter()
            if self.ui.available:
                self.ui.update_camera_sources(self._camera_ui_sources(), source, state.unavailable)
                self.ui.update_camera_status(True, source, reconnecting=False)
                self.ui.set_camera_type("OPENCV")
            # Update preview geometry (+ GUI dimensions; layout will recompute)
            self.preview_geometry(state.width, state.height)
            print(f"Camera {source} opened: {state.width}x{state.height}")
        else:
            self._last_camera_open_time = 0.0
            if self.ui.available:
                self.ui.update_camera_sources(self._camera_ui_sources(), source, state.unavailable)
                self.ui.update_camera_status(False, source, reconnecting=self._camera_reconnecting)
                self.ui.set_camera_type("")
            print(f"Camera {source} unavailable")
        return opened

    def _is_ids_camera_active(self) -> bool:
        """Check if an IDS camera is currently active."""
        if not self._use_unified_camera or self.unified_camera is None:
            return False
        return (self.unified_camera.is_open and
                self.unified_camera.source_type == CameraSource.IDS_PEAK)

    def _set_camera_frame_callback(self, callback):
        """Set frame callback on the ACTIVE camera (unified or legacy)."""
        if self._use_unified_camera and self.unified_camera is not None:
            self.unified_camera.set_frame_callback(callback)
        else:
            self.camera.set_frame_callback(callback)

    # ------------------------------------------------------------------
    # IDS parameter callbacks (cached so they survive reopen round-trips)
    # ------------------------------------------------------------------
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
                if self.ui.available:
                    self.ui.set_camera_dimensions(self.unified_camera.width, self.unified_camera.height)
                self.repush_preview_size()
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
        if self.ui.available:
            self.ui.sync_checkbox("ids_gain_auto", False)

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
        if self.ui.available:
            self.ui.sync_slider("ids_exposure_us", self.ids_exposure_us)
            self.ui.sync_checkbox("ids_exposure_auto", False)

    def _cb_ids_exposure_auto_toggle(self, enabled: bool):
        """Handle IDS exposure auto checkbox toggle."""
        self.ids_exposure_auto = enabled
        if self._use_unified_camera and self.unified_camera is not None:
            self.unified_camera.set_exposure_auto(enabled)
            print(f"[IDS Exposure] Auto {'ON' if enabled else 'OFF'}")
