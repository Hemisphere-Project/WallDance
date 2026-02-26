"""
Video recording and playback for WallDance.
Manages 9 recording slots per project with timestamped history.
"""

from __future__ import annotations

import os
import re
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue, Empty
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
    playback_fps: float = 30.0  # FPS of the video being played


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
        
        # Threaded recording encoder
        self._recording_queue: Queue[Optional[np.ndarray]] = Queue(maxsize=300)  # ~10 sec buffer at 30fps
        self._recording_thread: Optional[threading.Thread] = None
        self._recording_running: bool = False
        
        # Playback state
        self._reader: Optional[cv2.VideoCapture] = None
        self._playback_path: Optional[str] = None
        self._playback_fps: float = 30.0
        self._playback_speed: float = 1.0
        
        # Threaded playback decoder
        self._playback_thread: Optional[threading.Thread] = None
        self._playback_running: bool = False
        self._playback_paused: bool = False
        self._frame_buffer: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._playback_frame_count: int = 0
    
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
        # Stop any ongoing playback/recording when switching projects
        self.stop_playback()
        self.stop_recording()
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
    def _recording_encoder_thread(self):
        """Background thread that writes frames from queue to disk."""
        print("[RecorderThread] Recording encoder thread started")
        frames_written = 0
        
        while self._recording_running:
            try:
                # Wait for frame with timeout to allow checking _recording_running
                frame = self._recording_queue.get(timeout=0.1)
                
                if frame is None:
                    # Sentinel value - stop signal
                    break
                
                if self._writer is not None:
                    self._writer.write(frame)
                    frames_written += 1
                    
            except Empty:
                # Timeout - just loop and check if still running
                continue
            except Exception as e:
                print(f"[RecorderThread] Error writing frame: {e}")
                break
        
        print(f"[RecorderThread] Recording encoder thread finished, wrote {frames_written} frames")
    
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
        
        # Clear any leftover frames in queue
        while not self._recording_queue.empty():
            try:
                self._recording_queue.get_nowait()
            except Empty:
                break
        
        # Start recording thread
        self._recording_running = True
        self._recording_thread = threading.Thread(
            target=self._recording_encoder_thread,
            name="RecordingEncoder",
            daemon=True
        )
        self._recording_thread.start()
        
        print(f"Started recording to slot {slot}: {filepath}")
        return True
    
    def write_frame(self, frame: np.ndarray):
        """Queue a frame for recording (non-blocking)."""
        if not self.is_recording:
            return
        
        # Resize if needed
        h, w = frame.shape[:2]
        if (w, h) != self._recording_size:
            frame = cv2.resize(frame, self._recording_size)
        
        # Make a copy since the frame buffer might be reused
        frame_copy = frame.copy()
        
        try:
            # Non-blocking put - drop frame if queue is full (better than blocking main loop)
            self._recording_queue.put_nowait(frame_copy)
            self._status.recording_frames += 1
        except Exception:
            # Queue full - drop frame rather than block
            print(f"[Recorder] Queue full, dropped frame (queue size: {self._recording_queue.qsize()})")
    
    def stop_recording(self) -> Optional[str]:
        """Stop recording and return the saved filepath."""
        if not self.is_recording:
            return None
        
        filepath = self._recording_path
        
        # Signal thread to stop
        self._recording_running = False
        
        # Send sentinel to unblock the thread if waiting
        try:
            self._recording_queue.put_nowait(None)
        except Exception:
            pass
        
        # Wait for thread to finish (with timeout)
        if self._recording_thread is not None:
            self._recording_thread.join(timeout=5.0)
            if self._recording_thread.is_alive():
                print("[Recorder] Warning: recording thread did not finish in time")
            self._recording_thread = None
        
        # Release writer after thread has stopped
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
    def _playback_decoder_thread(self):
        """Background thread that decodes video at the correct speed."""
        if self._reader is None:
            return
        
        frame_interval = 1.0 / self._playback_fps
        start_time = time.time()
        frame_count = 0
        paused_time = 0.0
        last_speed = self._playback_speed
        
        while self._playback_running:
            # If paused, just sleep and wait
            if self._playback_paused:
                if paused_time == 0.0:
                    paused_time = time.time()
                time.sleep(0.05)  # Check pause state every 50ms
                continue
            
            # Resume from pause - adjust start time
            if paused_time > 0.0:
                pause_duration = time.time() - paused_time
                start_time += pause_duration
                paused_time = 0.0
            
            # Speed changed - reset timing to avoid hang
            if self._playback_speed != last_speed:
                start_time = time.time() - (frame_count * frame_interval / self._playback_speed)
                last_speed = self._playback_speed
            
            # Calculate target time for this frame
            target_time = start_time + (frame_count * frame_interval / self._playback_speed)
            now = time.time()
            wait_time = target_time - now
            
            if wait_time > 0:
                time.sleep(wait_time)
            
            # Read next frame
            ret, frame = self._reader.read()
            if not ret:
                # End of video - loop back
                self._reader.set(cv2.CAP_PROP_POS_FRAMES, 0)
                frame_count = 0
                start_time = time.time()
                ret, frame = self._reader.read()
                if not ret:
                    print("Playback decoder thread: cannot read frame after loop")
                    break
            
            # Update buffer (latest frame overwrites previous)
            with self._frame_lock:
                self._frame_buffer = frame.copy()
                self._playback_frame_count = frame_count
            
            frame_count += 1
        
        # self._playback_running = False
        print("Playback decoder thread stopped")
    
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
        self._playback_fps = self._reader.get(cv2.CAP_PROP_FPS) or 30.0
        self._playback_speed = 1.0  # Reset speed on new playback
        
        self._status.state = RecorderState.PLAYING
        self._status.current_slot = slot
        self._status.playback_frame = 0
        self._status.playback_total = int(self._reader.get(cv2.CAP_PROP_FRAME_COUNT))
        self._status.playback_fps = self._playback_fps
        
        # Start decoder thread
        self._playback_running = True
        self._playback_paused = False
        self._frame_buffer = None
        self._playback_frame_count = 0
        self._playback_thread = threading.Thread(target=self._playback_decoder_thread, daemon=True)
        self._playback_thread.start()
        
        print(f"Started playback from slot {slot}: {filepath} ({self._status.playback_total} frames @ {self._playback_fps:.1f} FPS)")
        return True
    
    def read_frame(self, respect_fps: bool = True) -> Optional[np.ndarray]:
        """Get the latest decoded frame from the buffer.
        
        The decoder thread runs independently, so this always returns
        the most recent frame without blocking.
        """
        if not self.is_playing:
            return None
        
        with self._frame_lock:
            if self._frame_buffer is None:
                return None
            # Update status
            self._status.playback_frame = self._playback_frame_count
            return self._frame_buffer.copy()
    
    def set_playback_speed(self, speed: float):
        """Set playback speed multiplier (e.g. 0.5, 1.0, 2.0)."""
        if speed <= 0:
            return
        
        self._playback_speed = speed
        print(f"Playback speed set to {speed}x")
    
    def pause_playback(self):
        """Pause video playback (keeps current frame in buffer)."""
        if self.is_playing and not self._playback_paused:
            self._playback_paused = True
            print("Playback paused")
    
    def resume_playback(self):
        """Resume video playback."""
        if self.is_playing and self._playback_paused:
            self._playback_paused = False
            print("Playback resumed")
    
    def is_paused(self) -> bool:
        """Check if playback is paused."""
        return self.is_playing and self._playback_paused
    
    def next_frame(self):
        """Step forward one frame (when paused)."""
        if not self.is_playing or self._reader is None:
            return
        
        # Read and update buffer
        ret, frame = self._reader.read()
        if not ret:
            # Loop back
            self._reader.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = self._reader.read()
        
        if ret:
            with self._frame_lock:
                self._frame_buffer = frame.copy()
                self._playback_frame_count = int(self._reader.get(cv2.CAP_PROP_POS_FRAMES)) - 1
    
    def prev_frame(self):
        """Step backward one frame (when paused)."""
        if not self.is_playing or self._reader is None:
            return
        
        # Go back 2 frames, then read 1 (to land on previous frame)
        current_pos = self._reader.get(cv2.CAP_PROP_POS_FRAMES)
        target_pos = max(0, current_pos - 2)
        
        self._reader.set(cv2.CAP_PROP_POS_FRAMES, target_pos)
        ret, frame = self._reader.read()
        
        if ret:
            with self._frame_lock:
                self._frame_buffer = frame.copy()
                self._playback_frame_count = int(self._reader.get(cv2.CAP_PROP_POS_FRAMES)) - 1
    
    def stop_playback(self):
        """Stop playback and return to live mode."""
        # Stop decoder thread
        self._playback_running = False
        if self._playback_thread is not None:
            thread = self._playback_thread
            self._playback_thread = None
            # Only join if the thread was actually started (avoids RuntimeError)
            try:
                if thread.is_alive() or thread._started.is_set():  # type: ignore[attr-defined]
                    thread.join(timeout=2.0)
            except (RuntimeError, AttributeError):
                pass
        
        # Clean up reader
        if self._reader is not None:
            self._reader.release()
            self._reader = None
        
        if self.is_playing:
            print(f"Stopped playback from slot {self._status.current_slot}")
        
        with self._frame_lock:
            self._frame_buffer = None
        
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
