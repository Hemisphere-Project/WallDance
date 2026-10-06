"""
Video recording and playback for WallDance.
Manages 9 recording slots per project with timestamped history.
"""

from __future__ import annotations

import json
import os
import re
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue, Empty, Full
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from core.config_store import PROJECTS_DIR, sanitize_project_name
from core.config import RECORDING_CODEC, RECORDING_QUALITY, RECORDING_SLOTS
from core.input_transform import IDENTITY, InputTransform
from core.video_import import SLOT_VIDEO_EXTS   # what the slot listing accepts


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
    playback_width: int = 0
    playback_height: int = 0


def _jsonable(value):
    """Best-effort JSON coercion for provenance dicts (numpy, paths, tuples)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


class _RecordingJob:
    """One take: its frame queue, encoder thread, counters and sidecars.

    The encoder exits only on the stop sentinel, so every queued frame is
    written (the old loop discarded the queue tail on stop: 30 frames lost on
    a corpus take). The ``.meta`` sidecar is written by the encoder thread
    after the writer is released, i.e. once the file is complete.
    """

    def __init__(self, path: str, fourcc: str, fps: float,
                 size: Tuple[int, int], slot: int, meta: dict):
        self.path = path
        self.fourcc = fourcc
        self.fps = fps
        self.size = size
        self.slot = slot
        self.meta_start = meta
        self.meta_stop: dict = {}
        self.queue: "Queue[Optional[np.ndarray]]" = Queue(maxsize=300)
        self.frames_queued = 0
        self.frames_dropped = 0
        self.frames_written = 0
        self.actual_fps = 0.0
        self.started_at = datetime.now().isoformat(timespec="milliseconds")
        self.stopped_at: Optional[str] = None
        self.error: Optional[str] = None
        self.done = threading.Event()
        self._camlog = None
        self._camlog_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="RecordingEncoder",
                                        daemon=True)

    def start(self) -> None:
        self._thread.start()

    def put(self, frame: np.ndarray) -> bool:
        try:
            self.queue.put_nowait(frame.copy())
            self.frames_queued += 1
            return True
        except Full:
            self.frames_dropped += 1
            if self.frames_dropped in (1, 10, 100) or self.frames_dropped % 1000 == 0:
                print(f"[Recorder] Queue full, dropped {self.frames_dropped} frame(s)")
            return False

    def append_camlog(self, sample: dict) -> None:
        with self._camlog_lock:
            try:
                if self._camlog is None:
                    self._camlog = open(self.path + ".camlog.jsonl", "a", encoding="utf-8")
                self._camlog.write(json.dumps(_jsonable(sample)) + "\n")
                self._camlog.flush()
            except Exception as e:
                print(f"[Recorder] camlog write failed: {e}")

    def finish(self, meta_stop: dict) -> None:
        self.meta_stop = meta_stop
        self.stopped_at = datetime.now().isoformat(timespec="milliseconds")
        # Blocking put: a full queue still gets its sentinel once the encoder
        # catches up -- the frames ahead of it are written, not discarded.
        threading.Thread(target=self.queue.put, args=(None,), daemon=True).start()

    def _run(self) -> None:
        writer = None
        t0 = time.monotonic()
        try:
            # Create the VideoWriter on THIS thread - required on Windows where some
            # codecs (MJPG, mp4v) use COM/GDI objects that are thread-affine.
            writer = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*self.fourcc),
                                     self.fps, self.size)
            if not writer.isOpened():
                self.error = "VideoWriter failed to open"
                print(f"[RecorderThread] ERROR: {self.error}: {self.path}")
                while self.queue.get() is not None:   # drain until stop
                    pass
                return
            if self.fourcc == "MJPG":
                writer.set(cv2.VIDEOWRITER_PROP_QUALITY, RECORDING_QUALITY)
            print(f"[RecorderThread] VideoWriter opened: {self.fourcc} -> {self.path}")
            while True:
                frame = self.queue.get()
                if frame is None:
                    break
                try:
                    writer.write(frame)
                    self.frames_written += 1
                except Exception as e:
                    self.error = f"write failed: {e}"
                    print(f"[RecorderThread] Error writing frame: {e}")
        finally:
            elapsed = time.monotonic() - t0
            if writer is not None:
                writer.release()
            if elapsed > 0 and self.frames_written > 1:
                self.actual_fps = self.frames_written / elapsed
            else:
                self.actual_fps = self.fps
            with self._camlog_lock:
                if self._camlog is not None:
                    self._camlog.close()
                    self._camlog = None
            self._write_meta()
            print(f"[RecorderThread] wrote {self.frames_written} frames "
                  f"({self.frames_dropped} dropped) at {self.actual_fps:.2f} fps -> {self.path}")
            self.done.set()

    def _write_meta(self) -> None:
        meta = {
            # v1 keys first: playback reads actual_fps; the scenario fingerprint
            # reads frames (now the frames actually written = decodable).
            "actual_fps": round(self.actual_fps, 3),
            "frames": self.frames_written,
            "meta_version": 2,
            "slot": self.slot,
            "file": os.path.basename(self.path),
            "codec": self.fourcc,
            "container_fps": self.fps,
            "size": list(self.size),
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "frames_queued": self.frames_queued,
            "frames_dropped": self.frames_dropped,
            "camlog": os.path.basename(self.path) + ".camlog.jsonl"
                      if os.path.exists(self.path + ".camlog.jsonl") else None,
            "error": self.error,
        }
        for key, value in self.meta_start.items():
            meta.setdefault(key, value)
        if self.meta_stop:
            meta["at_stop"] = self.meta_stop
        try:
            with open(self.path + ".meta", "w", encoding="utf-8") as f:
                json.dump(_jsonable(meta), f, indent=1)
            print(f"[Recorder] Saved sidecar: {self.path}.meta (fps={self.actual_fps:.2f})")
        except Exception as e:
            print(f"[Recorder] Warning: could not write meta file: {e}")


class VideoRecorder:
    """Manages video recording and playback for 9 slots per project."""
    
    NUM_SLOTS = RECORDING_SLOTS
    
    def __init__(self, projects_dir: str = PROJECTS_DIR):
        self.projects_dir = projects_dir
        self._current_project: str = "default"
        self._status = RecorderStatus()
        
        # Recording state
        self._writer: Optional[cv2.VideoWriter] = None  # kept for compatibility, unused
        self._recording_path: Optional[str] = None
        self._recording_fourcc: str = "MJPG"
        self._recording_fps: float = 30.0
        self._recording_size: Tuple[int, int] = (1920, 1080)
        
        # Threaded recording encoder: one _RecordingJob per take (queue +
        # encoder thread + sidecars); stopped takes finalize in the background.
        self._job: Optional[_RecordingJob] = None
        self._finalizing: dict = {}
        
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
        # (transform, transformed frame) of _frame_buffer, made by the decoder
        # thread; None for identity / frames stored by the step/seek paths.
        self._frame_buffer_xf: Optional[Tuple[InputTransform, np.ndarray]] = None
        self._frame_new: bool = False  # True when decoder wrote a new frame not yet consumed
        self._frame_lock = threading.Lock()
        self._playback_frame_count: int = 0

        # Optional callback fired on playback start / loop / restart.
        # Signature: on_playback_start(event: str)  where event is
        # "start", "restart" (same slot), or "loop".
        # Intended for tracker reset so IDs don't carry across takes.
        self.on_playback_start: Optional[Callable[[str], None]] = None

        # Input transform (REQ-5). Slot files hold RAW frames; playback applies
        # the project's current transform (the same one the live camera uses)
        # in read_frame(), so ROI/mask/calibration match what is on screen.
        self._input_transform: InputTransform = IDENTITY
        self._playback_raw_size: Tuple[int, int] = (0, 0)
        self._playback_meta: dict = {}

    @property
    def input_transform(self) -> InputTransform:
        return self._input_transform

    def set_input_transform(self, transform: InputTransform) -> None:
        """Mirror/rotate played-back frames; playback dims follow."""
        self._input_transform = transform
        if self.is_playing:
            self._status.playback_width, self._status.playback_height = \
                transform.output_size(*self._playback_raw_size)

    @property
    def playback_recorded_transform(self) -> Optional[InputTransform]:
        """The transform that was live when the playing take was recorded
        (from its ``.meta``), or None for legacy / imported takes."""
        block = self._playback_meta.get("input_transform")
        if not isinstance(block, dict):
            return None
        try:
            return InputTransform(bool(block.get("mirror", False)),
                                  int(block.get("rotation", 0)))
        except (TypeError, ValueError):
            return None
    
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

    @property
    def playback_path(self) -> Optional[str]:
        """Return the currently playing recording path, if any."""
        return self._playback_path

    @property
    def recordings_dir(self) -> str:
        """Public recordings path for the current project (ops readiness disk check)."""
        return self._get_recordings_dir()
    
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
            if filename.startswith(pattern) and filename.endswith(SLOT_VIDEO_EXTS):
                filepath = os.path.join(recordings_dir, filename)
                # Parse timestamp from filename: slot_N_YYYYMMDD_HHMMSS.<ext>
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
        # slot_N_YYYYMMDD_HHMMSS.<ext> -> YYYY-MM-DD HH:MM:SS
        name = os.path.splitext(filename)[0]  # strip extension regardless of type
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
    def start_recording(self, slot: int, fps: float = 30.0,
                        size: Tuple[int, int] = (1920, 1080),
                        meta: Optional[dict] = None) -> bool:
        """Start recording to a slot. Returns True if started successfully.

        ``meta`` (optional) is the take's provenance known at start: camera
        settings, rig sheet, app/config versions... (MRK-0). It is merged into
        the ``.meta`` sidecar written when the take is finalized."""
        if not self.is_live:
            print(f"Cannot start recording: not in LIVE mode (current: {self._status.state})")
            return False

        if slot < 1 or slot > self.NUM_SLOTS:
            print(f"Invalid slot number: {slot}")
            return False

        # Stop any existing recording (shouldn't happen but safety)
        self.stop_recording()

        recordings_dir = self._get_recordings_dir()
        os.makedirs(recordings_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Codec / container selection driven by RECORDING_CODEC in config.py.
        # Keys are the exact fourcc strings (case-sensitive for cv2).
        _CODEC_CONTAINER = {
            "MJPG": ".avi",
            "FFV1": ".avi",
            "mp4v": ".mp4",
        }
        _lookup = {k.lower(): k for k in _CODEC_CONTAINER}
        codec = _lookup.get(RECORDING_CODEC.lower(), "MJPG")
        ext = _CODEC_CONTAINER[codec]
        filename = f"slot_{slot}_{timestamp}{ext}"
        filepath = os.path.join(recordings_dir, filename)
        print(f"[Recorder] Codec: {codec}  container: {ext}  -> {filename}")

        self._recording_path = filepath
        self._recording_fourcc = codec
        self._recording_fps = fps
        self._recording_size = size
        self._status.state = RecorderState.RECORDING
        self._status.current_slot = slot
        self._status.recording_frames = 0

        self._finalizing = {k: j for k, j in self._finalizing.items() if not j.done.is_set()}
        job = _RecordingJob(filepath, codec, fps, size, slot, dict(meta or {}))
        self._job = job
        self._finalizing[filepath] = job
        job.start()
        print(f"Started recording to slot {slot}: {filepath}")
        return True

    def write_frame(self, frame: np.ndarray):
        """Queue a frame for recording (non-blocking)."""
        job = self._job
        if not self.is_recording or job is None:
            return
        h, w = frame.shape[:2]
        if (w, h) != job.size:
            frame = cv2.resize(frame, job.size)
        if job.put(frame):
            self._status.recording_frames += 1

    def append_camlog(self, sample: dict) -> None:
        """Append one camera-telemetry sample (``<take>.camlog.jsonl``) to the
        running take -- exposure/gain drift under AE is invisible otherwise."""
        job = self._job
        if job is not None and self.is_recording:
            job.append_camlog(sample)

    def is_finalizing(self, filepath: Optional[str]) -> bool:
        """True while a stopped take is still draining to disk."""
        job = self._finalizing.get(filepath or "")
        return job is not None and not job.done.is_set()

    def wait_finalized(self, timeout: Optional[float] = None) -> bool:
        """Block until every stopped take is on disk with its ``.meta``."""
        deadline = None if timeout is None else time.monotonic() + timeout
        for job in list(self._finalizing.values()):
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not job.done.wait(left):
                return False
        return True

    def stop_recording(self, meta: Optional[dict] = None) -> Optional[str]:
        """Stop recording and return the saved filepath.

        Returns immediately: the take's encoder drains every queued frame (no
        tail loss, MRK-0) and writes the ``.meta`` sidecar on its own thread,
        so the GUI never freezes on stop. ``meta`` adds facts known at stop
        (camera settings at the end of the take)."""
        if not self.is_recording:
            return None
        job = self._job
        self._job = None
        filepath = self._recording_path
        if job is not None:
            job.finish(dict(meta or {}))
        self._status.state = RecorderState.LIVE
        self._status.current_slot = 0
        self._status.recording_frames = 0
        self._recording_path = None
        print(f"Stopped recording: finalizing {filepath} in the background")
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
        # Frame 0 was pre-decoded in start_playback(); continue from frame 1
        frame_count = 1
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
            
            # Read next frame (reader may have been released by stop_playback)
            reader = self._reader
            if reader is None:
                break
            ret, frame = reader.read()
            if not ret:
                # End of video - loop back
                reader = self._reader
                if reader is None:
                    break
                reader.set(cv2.CAP_PROP_POS_FRAMES, 0)
                frame_count = 0
                start_time = time.time()
                ret, frame = reader.read()
                if not ret:
                    print("Playback decoder thread: cannot read frame after loop")
                    break
                # Notify listener on loop (e.g. tracker reset)
                if self.on_playback_start:
                    try:
                        self.on_playback_start("loop")
                    except Exception as e:
                        print(f"[Playback] on_playback_start callback error: {e}")
            
            # Wait for the previous frame to be consumed before writing
            # the next one.  Without this, fast decode speeds cause the
            # buffer to be overwritten before the main loop processes it,
            # silently dropping frames.  Dropped frames make the Kalman
            # filter see larger-than-expected displacements (dt=1 but
            # actual motion spans multiple frames), degrading tracking
            # quality and producing speed-dependent results.
            while self._frame_new and self._playback_running and not self._playback_paused:
                time.sleep(0.001)

            # If we had to wait, reset the timing baseline so completed
            # processing time doesn't create a burst of catch-up frames.
            if time.time() - target_time > frame_interval:
                start_time = time.time() - (frame_count * frame_interval / self._playback_speed)

            # REQ-5: Mirror/Rotate on THIS thread (a 90/270 turn of a BGR
            # frame is a few ms of strided copy), not in read_frame on the
            # main loop; read_frame reuses it while the transform matches.
            xf = self._input_transform
            frame_xf = None if xf.is_identity else (xf, xf.apply(frame))
            with self._frame_lock:
                self._frame_buffer = frame.copy()
                self._frame_buffer_xf = frame_xf
                self._frame_new = True
                self._playback_frame_count = frame_count
            
            frame_count += 1
        
        self._playback_running = False
        print("Playback decoder thread stopped")
    
    def start_playback(self, slot: int, recording_index: int = 0,
                       start_frame: int | None = None) -> bool:
        """Start playing from a slot. recording_index=0 is latest."""
        # Remember which slot was playing before we tear down
        previous_slot = self._status.current_slot

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
        if self.is_finalizing(filepath):
            print(f"[Playback] {os.path.basename(filepath)} is still being written; waiting...")
            self._finalizing[filepath].done.wait(15.0)

        self._reader = cv2.VideoCapture(filepath)
        if not self._reader.isOpened():
            print(f"Failed to open video: {filepath}")
            self._reader = None
            return False
        
        self._playback_path = filepath

        # Try to read actual FPS from sidecar .meta (written at recording
        # time with the real measured frame rate).  Fall back to the FPS
        # stored in the container header, which may be the nominal
        # CAMERA_FPS rather than the true capture rate.
        container_fps = self._reader.get(cv2.CAP_PROP_FPS) or 30.0
        meta_path = filepath + ".meta"
        sidecar_fps: Optional[float] = None
        self._playback_meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r") as f:
                    meta = json.load(f)
                if isinstance(meta, dict):
                    self._playback_meta = meta
                sidecar_fps = meta.get("actual_fps")
                if sidecar_fps and sidecar_fps > 0:
                    print(f"[Playback] Using sidecar FPS: {sidecar_fps:.2f} "
                          f"(container says {container_fps:.2f})")
            except Exception as e:
                print(f"[Playback] Warning: could not read meta file: {e}")
        if sidecar_fps and sidecar_fps > 0:
            self._playback_fps = sidecar_fps
        else:
            # Legacy recording without .meta — cap to 20 FPS since the
            # container header often stores a nominal value that is too high.
            legacy_cap = 20.0
            effective = min(container_fps, legacy_cap)
            print(f"[Playback] No .meta sidecar — capping FPS to {effective:.1f} "
                  f"(container says {container_fps:.1f})")
            self._playback_fps = effective

        # Reset speed to 1x only when switching to a different slot;
        # keep the user-chosen speed when re-starting the same slot.
        is_same_slot = (slot == previous_slot)
        if not is_same_slot:
            self._playback_speed = 1.0
        
        self._status.state = RecorderState.PLAYING
        self._status.current_slot = slot
        self._status.playback_frame = 0
        self._status.playback_total = int(self._reader.get(cv2.CAP_PROP_FRAME_COUNT))
        self._status.playback_fps = self._playback_fps
        self._playback_raw_size = (int(self._reader.get(cv2.CAP_PROP_FRAME_WIDTH)),
                                   int(self._reader.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        self._status.playback_width, self._status.playback_height = \
            self._input_transform.output_size(*self._playback_raw_size)

        if start_frame is not None and self._status.playback_total > 0:
            target = max(0, min(int(start_frame), self._status.playback_total - 1))
            self._reader.set(cv2.CAP_PROP_POS_FRAMES, target)
        
        # Start decoder thread
        self._playback_running = True
        self._playback_paused = False
        self._playback_frame_count = 0

        # Pre-decode first frame on THIS thread so _frame_buffer is
        # immediately non-None.  Eliminates the race where the main loop
        # sees frame_buffer=None before the decoder thread wakes up.
        ret, first_frame = self._reader.read()
        if not ret or first_frame is None:
            print(f"Failed to decode first frame of {filepath}")
            self._reader.release()
            self._reader = None
            self._playback_running = False
            self._status.state = RecorderState.LIVE
            self._status.current_slot = 0
            return False

        self._frame_buffer = first_frame.copy()
        self._frame_buffer_xf = None
        self._frame_new = True  # Mark first frame as available for consumption
        if start_frame is not None:
            self._playback_frame_count = target
            self._status.playback_frame = target
        else:
            self._playback_frame_count = 0

        self._playback_thread = threading.Thread(target=self._playback_decoder_thread, daemon=True)
        self._playback_thread.start()
        
        print(f"Started playback from slot {slot}: {filepath} ({self._status.playback_total} frames @ {self._playback_fps:.1f} FPS)")

        # Notify listener (e.g. tracker reset) on start / restart
        if self.on_playback_start:
            event = "restart" if is_same_slot else "start"
            try:
                self.on_playback_start(event)
            except Exception as e:
                print(f"[Playback] on_playback_start callback error: {e}")

        return True
    
    @property
    def is_playback_active(self) -> bool:
        """True if the playback decoder thread is still running."""
        # Use the running flag rather than thread.is_alive() to avoid
        # the brief window between thread.start() and thread bootstrap.
        return self._playback_running

    def read_frame(self, respect_fps: bool = True) -> Optional[np.ndarray]:
        """Get the latest decoded frame from the buffer.
        
        The decoder thread paces frame decoding at the video's native FPS.
        This method returns the newest frame only once; subsequent calls
        return None until the decoder thread produces a fresh frame.
        This prevents the main loop from re-processing the same frame and
        keeps playback speed faithful to the recorded FPS.
        """
        if not self.is_playing:
            return None
        
        with self._frame_lock:
            if self._frame_buffer is None or not self._frame_new:
                return None
            # Update status
            self._status.playback_frame = self._playback_frame_count
            # Mark consumed so we don't re-process the same frame
            self._frame_new = False
            # Identity = the old .copy(); a transform makes the new array itself
            # -- or copies the decoder thread's pre-transformed frame (the
            # caller may draw on what it gets, so it always owns a fresh array).
            pre = self._frame_buffer_xf
            if pre is not None and pre[0] == self._input_transform:
                return pre[1].copy()
            return self._input_transform.apply_copy(self._frame_buffer)
    
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

    def requeue_frame(self):
        """Re-mark the current frame as new so the main loop reprocesses it.

        Useful when a parameter changes while paused — lets the user see
        the effect immediately without stepping forward/backward.
        """
        if not self._playback_paused or self._frame_buffer is None:
            return
        with self._frame_lock:
            self._frame_new = True
    
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
                self._frame_buffer_xf = None
                self._frame_new = True
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
                self._frame_buffer_xf = None
                self._frame_new = True
                self._playback_frame_count = int(self._reader.get(cv2.CAP_PROP_POS_FRAMES)) - 1

    def seek_frame(self, frame_index: int) -> bool:
        """Jump playback to an absolute frame index.

        The target frame is decoded immediately and placed in the shared
        buffer so the main loop can display/process it on the next tick.
        """
        if not self.is_playing or self._reader is None:
            return False

        total = max(1, self._status.playback_total)
        target = max(0, min(int(frame_index), total - 1))

        self._reader.set(cv2.CAP_PROP_POS_FRAMES, target)
        ret, frame = self._reader.read()
        if not ret or frame is None:
            return False

        with self._frame_lock:
            self._frame_buffer = frame.copy()
            self._frame_buffer_xf = None
            self._frame_new = True
            self._playback_frame_count = target
        self._status.playback_frame = target
        return True
    
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
            self._frame_buffer_xf = None
            self._frame_new = False
        
        self._playback_path = None
        self._playback_meta = {}
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
        """Clean up resources (lets a just-stopped take finish writing)."""
        self.stop_recording()
        self.stop_playback()
        if not self.wait_finalized(timeout=60.0):
            print("[Recorder] Warning: a take was still finalizing at shutdown")
