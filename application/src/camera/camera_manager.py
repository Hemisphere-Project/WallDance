"""
Camera management utilities for WallDance.
Encapsulates camera discovery, opening, and state tracking.
Supports threaded frame capture for consistent frame rate.
"""

from __future__ import annotations

import cv2
import threading
import time
import numpy as np
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from core.config import CAMERA_FPS, CAMERA_HEIGHT, CAMERA_INDEX, CAMERA_WIDTH


@dataclass
class CameraState:
    source: str = str(CAMERA_INDEX)
    width: int = CAMERA_WIDTH
    height: int = CAMERA_HEIGHT
    available: List[str] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)
    is_open: bool = False


class CameraManager:
    """Manage video capture lifecycle with optional threaded capture."""

    def __init__(self, threaded: bool = True):
        self.cap: Optional[cv2.VideoCapture] = None
        self.state = CameraState()
        
        # Threaded capture
        self._threaded = threaded
        self._capture_thread: Optional[threading.Thread] = None
        self._capture_running: bool = False
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_ready: bool = False
        self._capture_error: bool = False
        
        # Callback for recording (called from capture thread with each frame)
        self._frame_callback: Optional[Callable[[np.ndarray], None]] = None

    def set_frame_callback(self, callback: Optional[Callable[[np.ndarray], None]]):
        """Set a callback to receive every captured frame (for recording)."""
        self._frame_callback = callback

    # ------------------------------------------------------------------
    # Threaded Capture
    # ------------------------------------------------------------------
    def _capture_loop(self):
        """Background thread that continuously captures frames."""
        print("[CameraThread] Capture thread started")
        while self._capture_running:
            if self.cap is None or not self.cap.isOpened():
                self._capture_error = True
                break
            
            try:
                # Use grab() + retrieve() pattern for better control
                # grab() is faster and allows checking _capture_running more frequently
                if not self.cap.grab():
                    # Grab failed - camera might be disconnected
                    if self._capture_running:  # Only error if we weren't asked to stop
                        self._capture_error = True
                    break
                
                # Check again before retrieve
                if not self._capture_running:
                    break
                
                ret, frame = self.cap.retrieve()
                if ret and frame is not None:
                    with self._frame_lock:
                        self._latest_frame = frame
                        self._frame_ready = True
                    
                    # Call recording callback if set (outside lock to not block)
                    if self._frame_callback is not None:
                        try:
                            self._frame_callback(frame)
                        except Exception as e:
                            print(f"[CameraThread] Frame callback error: {e}")
                else:
                    # Retrieve failed
                    if self._capture_running:
                        self._capture_error = True
                    break
            except Exception as e:
                if self._capture_running:
                    print(f"[CameraThread] Capture error: {e}")
                    self._capture_error = True
                break
        
        print("[CameraThread] Capture thread finished")
    
    def read(self) -> tuple[bool, Optional[np.ndarray]]:
        """Read a frame. Uses threaded buffer if enabled, otherwise direct read.
        
        Returns:
            (True, frame) if a frame is available
            (False, None) if camera has an error or is not open
            (True, None) if camera is open but no frame yet (still initializing)
        """
        if not self._threaded:
            # Direct read
            if self.cap is None or not self.cap.isOpened():
                return False, None
            return self.cap.read()
        
        # Threaded read from buffer
        if self._capture_error:
            return False, None
        
        # If not open, return failure
        if not self.state.is_open:
            return False, None
        
        with self._frame_lock:
            if not self._frame_ready or self._latest_frame is None:
                # Camera is open but no frame yet - not an error, just wait
                return True, None
            # Return a copy to avoid buffer overwrite issues
            frame = self._latest_frame.copy()
            # Mark consumed so caller waits for a fresh captured frame next time.
            # This prevents processing the same frame multiple times when the
            # main loop runs faster than camera acquisition.
            self._frame_ready = False
            return True, frame
    
    def has_capture_error(self) -> bool:
        """Check if capture thread encountered an error."""
        return self._capture_error

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @staticmethod
    def detect_cameras(max_index: int = 10) -> List[str]:
        available = []
        # Suppress native stderr spam from obsensor/MSMF backends during probing
        import os, sys
        if sys.platform == 'win32':
            _old_stderr_fd = None
            _devnull_fd = None
            try:
                _old_stderr_fd = os.dup(2)
                _devnull_fd = os.open(os.devnull, os.O_WRONLY)
                os.dup2(_devnull_fd, 2)
            except Exception:
                _old_stderr_fd = None
        try:
            for i in range(max_index):
                try:
                    cap = cv2.VideoCapture(i)
                    if cap.isOpened():
                        available.append(str(i))
                        cap.release()
                except Exception:
                    break  # no point probing higher indices
        finally:
            if sys.platform == 'win32' and _old_stderr_fd is not None:
                try:
                    os.dup2(_old_stderr_fd, 2)
                    os.close(_old_stderr_fd)
                except Exception:
                    pass
                if _devnull_fd is not None:
                    try:
                        os.close(_devnull_fd)
                    except Exception:
                        pass
        return available if available else ["0"]

    @staticmethod
    def check_camera_available(source: str) -> bool:
        try:
            idx = int(source)
            cap = cv2.VideoCapture(idx)
        except ValueError:
            cap = cv2.VideoCapture(source)
        if cap.isOpened():
            cap.release()
            return True
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _stop_capture_thread(self):
        """Stop the capture thread if running."""
        if self._capture_thread is not None:
            self._capture_running = False
            # Give thread a moment to notice the flag
            time.sleep(0.05)
            self._capture_thread.join(timeout=1.0)
            if self._capture_thread.is_alive():
                print("[Camera] Warning: capture thread did not stop cleanly")
            self._capture_thread = None
        self._frame_ready = False
        self._latest_frame = None
        self._capture_error = False
    
    def _start_capture_thread(self):
        """Start the capture thread."""
        if not self._threaded:
            return
        
        self._capture_error = False
        self._frame_ready = False
        self._latest_frame = None
        self._capture_running = True
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="CameraCapture",
            daemon=True
        )
        self._capture_thread.start()
        
        # Wait a bit for first frame
        for _ in range(50):  # 500ms max wait
            with self._frame_lock:
                if self._frame_ready:
                    break
            time.sleep(0.01)
    
    def open(self, source: str, backend: int | None = None) -> bool:
        """Open or switch to a camera source.

        Args:
            source: Camera index (as string) or device path.
            backend: Optional OpenCV backend ID (e.g. cv2.CAP_DSHOW).
                     When *None*, the default backend is used.
        """
        # Stop any existing capture thread first
        self._stop_capture_thread()
        
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        try:
            idx = int(source)
            if backend is not None:
                self.cap = cv2.VideoCapture(idx, backend)
            else:
                self.cap = cv2.VideoCapture(idx)
        except ValueError:
            if backend is not None:
                self.cap = cv2.VideoCapture(source, backend)
            else:
                self.cap = cv2.VideoCapture(source)

        if self.cap is None or not self.cap.isOpened():
            self.state.is_open = False
            self.state.source = source
            if source not in self.state.unavailable:
                self.state.unavailable.append(source)
            return False

        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        self.cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS)

        # Flush camera buffer - read and discard a few frames to let camera stabilize
        for _ in range(5):
            self.cap.grab()

        actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.state.width = actual_w
        self.state.height = actual_h
        self.state.source = source
        self.state.is_open = True

        if source in self.state.unavailable:
            self.state.unavailable.remove(source)
        if source not in self.state.available:
            self.state.available.append(source)
        self.state.available.sort()
        
        # Start capture thread
        self._start_capture_thread()
        
        return True

    def close(self) -> None:
        # Stop capture thread first
        self._stop_capture_thread()
        
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.state.is_open = False