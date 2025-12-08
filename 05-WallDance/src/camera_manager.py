"""
Camera management utilities for WallDance.
Encapsulates camera discovery, opening, and state tracking.
"""

from __future__ import annotations

import cv2
from dataclasses import dataclass, field
from typing import List, Optional

from config import CAMERA_FPS, CAMERA_HEIGHT, CAMERA_INDEX, CAMERA_WIDTH


@dataclass
class CameraState:
    source: str = str(CAMERA_INDEX)
    width: int = CAMERA_WIDTH
    height: int = CAMERA_HEIGHT
    available: List[str] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)
    is_open: bool = False


class CameraManager:
    """Manage video capture lifecycle."""

    def __init__(self):
        self.cap: Optional[cv2.VideoCapture] = None
        self.state = CameraState()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    @staticmethod
    def detect_cameras(max_index: int = 10) -> List[str]:
        available = []
        for i in range(max_index):
            cap = cv2.VideoCapture(i)
            if cap.isOpened():
                available.append(str(i))
                cap.release()
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
    def open(self, source: str) -> bool:
        """Open or switch to a camera source."""
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        try:
            idx = int(source)
            self.cap = cv2.VideoCapture(idx)
        except ValueError:
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
        return True

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.state.is_open = False

    def mark_unavailable(self, source: str) -> None:
        if source not in self.state.unavailable:
            self.state.unavailable.append(source)
        self.state.unavailable.sort()
        self.state.is_open = False