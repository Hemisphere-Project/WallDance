"""
Video recording and playback for WallDance.
Manages 9 recording slots per project with timestamped history.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Tuple

import cv2
import numpy as np

from config_store import PROJECTS_DIR, sanitize_project_name


class RecorderState(Enum):
    LIVE = "live"           # Using camera input
    RECORDING = "recording" # Recording from camera to a slot
    PLAYING = "playing"     # Playing back from a slot


@dataclass
class SlotInfo:
    """Information about a recording slot."""
    slot_id: int
    recordings: List[Tuple[str, str]]  # (display_name, filepath) sorted newest first
    
    @property
    def has_recordings(self) -> bool:
        return len(self.recordings) > 0
    
    @property
    def latest_path(self) -> Optional[str]:
        return self.recordings[0][1] if self.recordings else None


@dataclass
class RecorderStatus:
    """Current state of the video recorder."""
    state: RecorderState = RecorderState.LIVE
    current_slot: int = 0  # 0 = none, 1-9 = slot number
    recording_frames: int = 0
    playback_frame: int = 0
    playback_total: int = 0


class VideoRecorder:
    """Manages video recording and playback for 9 slots per project."""
    
    NUM_SLOTS = 9
    
    def __init__(self, projects_dir: str = PROJECTS_DIR):
        self.projects_dir = projects_dir
        self._current_project: str = "default"
        self._status = RecorderStatus()
        
        # Recording state
        self._writer: Optional[cv2.VideoWriter] = None
        self._recording_path: Optional[str] = None
        self._recording_fps: float = 30.0
        self._recording_size: Tuple[int, int] = (1920, 1080)
        
        # Playback state
        self._reader: Optional[cv2.VideoCapture] = None
        self._playback_path: Optional[str] = None
    
    @property
    def status(self) -> RecorderStatus:
        return self._status
    
    @property
    def is_live(self) -> bool:
        return self._status.state == RecorderState.LIVE
    
    @property
    def is_recording(self) -> bool:
        return self._status.state == RecorderState.RECORDING
    
    @property
    def is_playing(self) -> bool:
        return self._status.state == RecorderState.PLAYING
    
    def set_project(self, project_name: str):
        """Set the current project (creates recordings folder if needed)."""
        self._current_project = sanitize_project_name(project_name)
        recordings_dir = self._get_recordings_dir()
        os.makedirs(recordings_dir, exist_ok=True)
    
    def _get_recordings_dir(self) -> str:
        """Get the recordings directory for current project."""
        return os.path.join(self.projects_dir, self._current_project, "recordings")
    
    def _get_slot_pattern(self, slot: int) -> str:
        """Get filename pattern for a slot."""
        return f"slot_{slot}_"
    
    def get_slot_info(self, slot: int) -> SlotInfo:
        """Get information about a specific slot (1-9)."""
        if slot < 1 or slot > self.NUM_SLOTS:
            return SlotInfo(slot_id=slot, recordings=[])
        
        recordings_dir = self._get_recordings_dir()
        if not os.path.exists(recordings_dir):
            return SlotInfo(slot_id=slot, recordings=[])
        
        pattern = self._get_slot_pattern(slot)
        recordings = []
        
        for filename in os.listdir(recordings_dir):
            if filename.startswith(pattern) and filename.endswith(".avi"):
                filepath = os.path.join(recordings_dir, filename)
                # Parse timestamp from filename: slot_N_YYYYMMDD_HHMMSS.avi
                display = self._format_recording_display(filename)
                recordings.append((display, filepath))
        
        # Sort by filename (newest first due to timestamp format)
        recordings.sort(key=lambda x: x[1], reverse=True)
        return SlotInfo(slot_id=slot, recordings=recordings)
    
    def get_all_slots_info(self) -> List[SlotInfo]:
        """Get information about all 9 slots."""
        return [self.get_slot_info(i) for i in range(1, self.NUM_SLOTS + 1)]
    
    def _format_recording_display(self, filename: str) -> str:
        """Convert recording filename to human-readable display."""
        # slot_N_YYYYMMDD_HHMMSS.avi -> YYYY-MM-DD HH:MM:SS
        name = filename.replace(".avi", "")
        parts = name.split("_")
        if len(parts) >= 4:
            date_str = parts[2]
            time_str = parts[3]
            try:
                return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]} {time_str[:2]}:{time_str[2:4]}:{time_str[4:]}"
            except Exception:
                pass
        return name
    
    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def start_recording(self, slot: int, fps: float = 30.0, size: Tuple[int, int] = (1920, 1080)) -> bool:
        """Start recording to a slot. Returns True if started successfully."""
        if not self.is_live:
            print(f"Cannot start recording: not in LIVE mode (current: {self._status.state})")
            return False
        
        if slot < 1 or slot > self.NUM_SLOTS:
            print(f"Invalid slot number: {slot}")
            return False
        
        # Stop any existing recording (shouldn't happen but safety)
        self.stop_recording()
        
        # Create recordings directory
        recordings_dir = self._get_recordings_dir()
        os.makedirs(recordings_dir, exist_ok=True)
        
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"slot_{slot}_{timestamp}.avi"
        filepath = os.path.join(recordings_dir, filename)
        
        # Use FFV1 codec for lossless compression (or MJPG for near-lossless)
        # FFV1 is lossless but slower, MJPG is faster but slightly lossy
        # Using raw/uncompressed would be too large
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')  # Good quality, reasonable size
        
        self._writer = cv2.VideoWriter(filepath, fourcc, fps, size)
        if not self._writer.isOpened():
            print(f"Failed to create video writer for {filepath}")
            self._writer = None
            return False
        
        self._recording_path = filepath
        self._recording_fps = fps
        self._recording_size = size
        self._status.state = RecorderState.RECORDING
        self._status.current_slot = slot
        self._status.recording_frames = 0
        
        print(f"Started recording to slot {slot}: {filepath}")
        return True
    
    def write_frame(self, frame: np.ndarray):
        """Write a frame to the current recording."""
        if not self.is_recording or self._writer is None:
            return
        
        # Resize if needed
        h, w = frame.shape[:2]
        if (w, h) != self._recording_size:
            frame = cv2.resize(frame, self._recording_size)
        
        self._writer.write(frame)
        self._status.recording_frames += 1
    
    def stop_recording(self) -> Optional[str]:
        """Stop recording and return the saved filepath."""
        if not self.is_recording:
            return None
        
        filepath = self._recording_path
        
        if self._writer is not None:
            self._writer.release()
            self._writer = None
        
        frames = self._status.recording_frames
        self._status.state = RecorderState.LIVE
        self._status.current_slot = 0
        self._status.recording_frames = 0
        self._recording_path = None
        
        print(f"Stopped recording: {frames} frames saved to {filepath}")
        return filepath
    
    # ------------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------------
    def start_playback(self, slot: int, recording_index: int = 0) -> bool:
        """Start playing from a slot. recording_index=0 is latest."""
        # Stop any current playback or recording
        self.stop_playback()
        self.stop_recording()
        
        slot_info = self.get_slot_info(slot)
        if not slot_info.has_recordings:
            print(f"No recordings in slot {slot}")
            return False
        
        if recording_index >= len(slot_info.recordings):
            print(f"Recording index {recording_index} out of range for slot {slot}")
            return False
        
        filepath = slot_info.recordings[recording_index][1]
        
        self._reader = cv2.VideoCapture(filepath)
        if not self._reader.isOpened():
            print(f"Failed to open video: {filepath}")
            self._reader = None
            return False
        
        self._playback_path = filepath
        self._status.state = RecorderState.PLAYING
        self._status.current_slot = slot
        self._status.playback_frame = 0
        self._status.playback_total = int(self._reader.get(cv2.CAP_PROP_FRAME_COUNT))
        
        print(f"Started playback from slot {slot}: {filepath} ({self._status.playback_total} frames)")
        return True
    
    def read_frame(self) -> Optional[np.ndarray]:
        """Read next frame from playback. Returns None if not playing or error."""
        if not self.is_playing or self._reader is None:
            return None
        
        ret, frame = self._reader.read()
        if not ret:
            # End of video - loop back to beginning
            self._reader.set(cv2.CAP_PROP_POS_FRAMES, 0)
            self._status.playback_frame = 0
            ret, frame = self._reader.read()
            if not ret:
                print("Playback error: cannot read frame after loop")
                self.stop_playback()
                return None
        
        self._status.playback_frame = int(self._reader.get(cv2.CAP_PROP_POS_FRAMES))
        return frame
    
    def stop_playback(self):
        """Stop playback and return to live mode."""
        if self._reader is not None:
            self._reader.release()
            self._reader = None
        
        if self.is_playing:
            print(f"Stopped playback from slot {self._status.current_slot}")
        
        self._playback_path = None
        self._status.state = RecorderState.LIVE
        self._status.current_slot = 0
        self._status.playback_frame = 0
        self._status.playback_total = 0
    
    def go_live(self):
        """Return to live camera mode."""
        self.stop_recording()
        self.stop_playback()
    
    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def close(self):
        """Clean up resources."""
        self.stop_recording()
        self.stop_playback()
