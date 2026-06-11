"""Recording/playback orchestration peeled from WallDanceApp.

DECOMPOSITION_PLAN §5 Phase 2 (1). Method bodies moved verbatim from
app.py; ``self.<app attribute>`` references renamed to constructor-injected
dependencies. The constructor takes narrow ports (Protocols below) and core
objects — never the app instance.

Threading model is unchanged: ``_on_playback_start_event`` may fire on the
playback decoder thread and only queues; the main loop drains the queue via
``_drain_pending_playback_event`` / ``_handle_playback_start_event``.
"""
from __future__ import annotations

import json
import os
import time
import threading
from collections import deque
from typing import Callable, Deque, Optional, Protocol

import numpy as np

from core.config import CAMERA_FPS
from core.tracking_logger import _json_default
from core.video_recorder import RecorderState


class RecordingUiPort(Protocol):
    """The GUI surface the recording cluster needs (no dpg types).

    ``available`` is False until the GUI exists; push methods must be
    None-safe no-ops in that window.
    """

    @property
    def available(self) -> bool: ...

    def update_recording_ui(self, **kwargs) -> None: ...

    def set_camera_dimensions(self, width: int, height: int) -> None: ...

    def show_toast(self, message: str, duration: float, color) -> None: ...

    def show_slot_history_menu(self, slot, recordings, on_pick) -> None: ...


class RecordingCameraPort(Protocol):
    """Camera surface for recording/playback transitions.

    Acquisition calls are no-ops when no unified (IDS) camera is active —
    the adapter owns that conditional, mirroring the previous inline checks.
    """

    def set_frame_callback(self, callback) -> None: ...

    def start_acquisition(self) -> None: ...

    def stop_acquisition(self) -> None: ...

    def live_dimensions(self) -> Optional[tuple]: ...

    def record_dimensions(self) -> tuple: ...


class SessionInfoPort(Protocol):
    """Facts `_start_session` records in the per-run session metadata."""

    @property
    def config_dir(self) -> str: ...

    @property
    def current_project(self) -> str: ...

    @property
    def model_name(self) -> str: ...

    @property
    def imgsz(self) -> int: ...

    def saveable_config(self) -> dict: ...


class RecordingController:
    """Owns slot/record/playback state transitions and their UI sync."""

    def __init__(
        self,
        recorder,
        tracker_logger,
        camera: RecordingCameraPort,
        ui: RecordingUiPort,
        session: SessionInfoPort,
        on_playback_restart: Callable[[], None],
        startup_review,
    ) -> None:
        self.recorder = recorder
        self.tracker_logger = tracker_logger
        self.camera = camera
        self.ui = ui
        self.session = session
        self._on_playback_restart = on_playback_restart
        self._startup_review = startup_review

        self._source_transitioning = False  # True during playback↔live transitions
        self._pending_rec_slot: Optional[int] = None  # Slot being recorded to
        self._rec_armed: bool = False  # True when REC clicked, waiting for slot selection
        self._pause_at_frame_target = startup_review.pause_at_frame
        self._pending_playback_events: Deque[str] = deque()
        self._pending_playback_events_lock = threading.Lock()

        recorder.on_playback_start = self._on_playback_start_event

    @property
    def source_transitioning(self) -> bool:
        """True during playback↔live transitions; the main loop skips processing."""
        return self._source_transitioning

    # ------------------------------------------------------------------
    # Playback start events (decoder thread → main loop)
    # ------------------------------------------------------------------
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
        self._on_playback_restart()
        self._start_session()

    def _start_session(self):
        """Create a per-run session directory and redirect the logger."""
        slot = self.recorder.status.current_slot
        stamp = time.strftime("%Y%m%d_%H%M%S")
        session_name = f"{stamp}_slot{slot}"
        sessions_root = os.path.join(
            self.session.config_dir,
            self.session.current_project,
            "sessions",
        )
        session_dir = os.path.join(sessions_root, session_name)
        os.makedirs(session_dir, exist_ok=True)

        # Redirect logger to the new session directory
        self.tracker_logger.start_session(session_dir)
        self.tracker_logger.log_settings(self.session.saveable_config())

        # Write session metadata
        meta = {
            "session": session_name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "project": self.session.current_project,
            "slot": slot,
            "model": self.session.model_name,
            "imgsz": self.session.imgsz,
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

    # ------------------------------------------------------------------
    # Playback controls (GUI callbacks)
    # ------------------------------------------------------------------
    def _cb_playback_speed_change(self, speed: float):
        """Handle playback speed change."""
        self.recorder.set_playback_speed(speed)

    def _cb_playback_pause(self):
        """Handle pause/resume toggle."""
        if self.recorder.is_paused():
            self.recorder.resume_playback()
        else:
            self.recorder.pause_playback()
            self.tracker_logger.flush()  # Phase 0: flush log on pause
        self._update_recording_ui()

    def _cb_playback_force_pause(self):
        """Pause playback without toggling — no-op if already paused."""
        if self.recorder.is_playing and not self.recorder.is_paused():
            self.recorder.pause_playback()
            self.tracker_logger.flush()
            self._update_recording_ui()

    def _cb_playback_next_frame(self):
        """Handle next frame button."""
        self.recorder.next_frame()
        self.tracker_logger.flush()  # Phase 0: flush log on frame step

    def _cb_playback_prev_frame(self):
        """Handle previous frame button."""
        self.recorder.prev_frame()
        self.tracker_logger.flush()  # Phase 0: flush log on frame step

    # ------------------------------------------------------------------
    # Startup review automation
    # ------------------------------------------------------------------
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
        if self.ui.available:
            message = f"Review mode: slot {opts.slot}"
            if opts.recording_index:
                message += f" item {opts.recording_index}"
            if opts.play_at_frame is not None:
                message += f" play@{opts.play_at_frame}"
            if opts.pause_at_frame is not None:
                message += f" pause@{opts.pause_at_frame}"
            self.ui.show_toast(message, duration=3.5, color=(120, 200, 255))

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
        self.tracker_logger.flush()
        self._update_recording_ui()
        print(f"[Review] Auto-paused at frame {target}")
        if self.ui.available:
            self.ui.show_toast(
                f"Paused at frame {target}",
                duration=3.0,
                color=(120, 200, 255),
            )

    # ------------------------------------------------------------------
    # Record / live / slot transitions
    # ------------------------------------------------------------------
    def _camera_frame_callback(self, frame: np.ndarray):
        """Called from camera thread for each captured frame. Used for recording."""
        if self.recorder.is_recording:
            self.recorder.write_frame(frame)

    def _cb_rec_live(self):
        """Switch to live camera mode."""
        self._source_transitioning = True
        try:
            self.camera.set_frame_callback(None)  # Clear recording callback
            self.recorder.go_live()
            self._pending_rec_slot = None
            self._rec_armed = False
            # Restart IDS acquisition (was stopped during playback)
            self.camera.start_acquisition()
            # Restore live camera dimensions for preview aspect ratio
            dims = self.camera.live_dimensions()
            if self.ui.available and dims:
                self.ui.set_camera_dimensions(dims[0], dims[1])
            self._update_recording_ui()
            print("Switched to LIVE input")
        finally:
            self._source_transitioning = False

    def _apply_playback_dimensions(self):
        """Update GUI preview dimensions to match the video being played."""
        status = self.recorder.status
        if self.ui.available and status.playback_width > 0 and status.playback_height > 0:
            self.ui.set_camera_dimensions(status.playback_width, status.playback_height)

    def _cb_rec_toggle(self):
        """Toggle recording mode."""
        if self.recorder.is_recording:
            # Stop recording - clear callback first
            self.camera.set_frame_callback(None)
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
                self.ui.show_slot_history_menu(
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
            size = self.camera.record_dimensions()
            # Wire up camera callback BEFORE starting recording
            self.camera.set_frame_callback(self._camera_frame_callback)
            if self.recorder.start_recording(slot, fps, size):
                self._rec_armed = False
                self._pending_rec_slot = slot
                self._update_recording_ui()
                print(f"Recording to slot {slot}...")
            else:
                print(f"Failed to start recording to slot {slot}")
                self.camera.set_frame_callback(None)  # Remove callback on failure
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
            self.camera.stop_acquisition()

            if not self.recorder.start_playback(slot, recording_index):
                # Playback failed — restart acquisition
                self.camera.start_acquisition()
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
        if not self.ui.available:
            return

        status = self.recorder.status
        slots_info = [(i, self.recorder.get_slot_info(i).has_recordings) for i in range(1, 10)]

        # Map state to string, including armed state
        if self._rec_armed and status.state == RecorderState.LIVE:
            state_str = "armed"
        else:
            state_str = status.state.value  # 'live', 'recording', 'playing'

        current_slot = status.current_slot

        self.ui.update_recording_ui(
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
