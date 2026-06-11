"""
Structured tracking event logger for diagnostics.

Replaces ad-hoc TRACKER_DEBUG prints with frame-stamped, structured
records that can be reviewed post-mortem to diagnose ID swaps, steals,
and ghost creation.

Output: JSONL file (one JSON object per line), auto-flushed periodically.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from typing import Any, Dict, List, Optional


class TrackingLogger:
    """Frame-stamped structured event logger for the tracker.

    Usage::

        logger = TrackingLogger(enabled=True, filepath="tracking_events.jsonl")
        logger.set_frame(42)
        logger.log("MATCH", {"det": 0, "track_id": 3, "cost": 12.5})
        logger.log("NEW_TRACK", {"track_id": 7, "position": [100, 200]})
        logger.flush()  # called periodically or on shutdown

    Events are stored in a rolling in-memory buffer AND appended to a
    JSONL file on disk.

    Per-run isolation
    -----------------
    Call ``start_session(session_dir)`` at playback start to redirect
    output to a timestamped session directory.  The file is opened in
    **write** mode (not append) so each run gets a clean log.
    """

    def __init__(
        self,
        enabled: bool = True,
        filepath: str = "tracking_events.jsonl",
        max_entries: int = 3000,
        flush_interval: float = 5.0,
        camera_id: int = 0,
    ):
        self.enabled = enabled
        self.filepath = filepath
        self.max_entries = max_entries
        self.flush_interval = flush_interval
        self.camera_id: int = camera_id

        self._frame: int = 0
        self._buffer: deque = deque(maxlen=max_entries)
        self._pending: List[Dict[str, Any]] = []  # not yet flushed to disk
        self._last_flush_time: float = time.time()
        self._file_handle: Optional[Any] = None
        self._session_dir: Optional[str] = None

        if self.enabled and self.filepath:
            self._open_file()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def start_session(self, session_dir: str):
        """Start a new session in *session_dir*.

        Closes any previous file, opens a fresh
        ``tracking_events.jsonl`` inside *session_dir* in **write**
        mode so this run is fully isolated from previous runs.
        """
        self.close()
        self._session_dir = session_dir
        os.makedirs(session_dir, exist_ok=True)
        self.filepath = os.path.join(session_dir, "tracking_events.jsonl")
        self._buffer.clear()
        self._pending.clear()
        self._frame = 0
        if self.enabled:
            self._open_file(mode="w")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_frame(self, frame: int):
        """Set the current frame number for subsequent log calls."""
        self._frame = frame

    @property
    def session_dir(self) -> Optional[str]:
        """Current session directory, or None if using legacy path."""
        return self._session_dir

    def log(self, event: str, data: Optional[Dict[str, Any]] = None):
        """Record a tracking event.

        Args:
            event: Event type string (MATCH, NEW_TRACK, DORMANT, etc.)
            data:  Event-specific payload dict.
        """
        if not self.enabled:
            return

        entry = {
            "frame": self._frame,
            "event": event,
            "camera": self.camera_id,
        }
        if data:
            entry["data"] = data

        self._buffer.append(entry)
        self._pending.append(entry)

        # Auto-flush periodically
        now = time.time()
        if now - self._last_flush_time >= self.flush_interval:
            self.flush()

    def log_settings(self, settings: Dict[str, Any]):
        """Emit a SESSION_SETTINGS event with all active config values.

        Should be called once at the start of each run (after reset)
        so that the JSONL log is self-describing — no need to guess
        which model / imgsz / confidence / enhancement was used.
        """
        if not self.enabled:
            return

        entry = {
            "event": "SESSION_SETTINGS",
            "timestamp": time.time(),
            "settings": settings,
        }
        self._buffer.append(entry)
        self._pending.append(entry)
        self.flush()

    def log_frame_summary(
        self,
        n_detections: int,
        n_tracks: int,
        track_states: List[Dict[str, Any]],
        n_dormant: int,
        matched_pairs: List[Dict[str, Any]],
    ):
        """Emit a FRAME_SUMMARY entry (called once per frame after update).

        This single entry lets you reconstruct the full tracker state at
        any frame — essential for post-mortem debugging.
        """
        self.log("FRAME_SUMMARY", {
            "n_detections": n_detections,
            "n_tracks": n_tracks,
            "track_states": track_states,
            "n_dormant": n_dormant,
            "matched_pairs": matched_pairs,
        })

    def flush(self):
        """Write pending entries to disk."""
        if not self._pending or not self._file_handle:
            self._last_flush_time = time.time()
            return

        try:
            for entry in self._pending:
                self._file_handle.write(json.dumps(entry, default=_json_default) + "\n")
            self._file_handle.flush()
        except (OSError, ValueError):
            pass  # don't crash the tracker on I/O errors

        self._pending.clear()
        self._last_flush_time = time.time()

    def reset(self):
        """Clear buffer and mark a new section.

        Called on playback restart / tracker reset.  When a session
        directory is active the file is already isolated so we just
        clear in-memory state.  For the legacy single-file path we
        still write a RESET marker.
        """
        self._buffer.clear()
        self._pending.clear()
        self._frame = 0

        # Legacy path: write separator in the shared file
        if self._file_handle and self._session_dir is None:
            try:
                self._file_handle.write(
                    json.dumps({"event": "RESET", "timestamp": time.time()}) + "\n"
                )
                self._file_handle.flush()
            except (OSError, ValueError):
                pass

    def close(self):
        """Flush and close the log file."""
        self.flush()
        if self._file_handle:
            try:
                self._file_handle.close()
            except OSError:
                pass
            self._file_handle = None

    def get_events_around_frame(self, frame: int, window: int = 5) -> List[Dict]:
        """Return logged events within ±window frames of the given frame.

        Useful for interactive debugging: pause on a bad frame, then
        query the log for context.
        """
        lo = frame - window
        hi = frame + window
        return [e for e in self._buffer if lo <= e.get("frame", -1) <= hi]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _open_file(self, mode: str = "a"):
        """Open (or re-open) the JSONL output file.

        Args:
            mode: File open mode — ``'a'`` for legacy append,
                  ``'w'`` for per-session isolated files.
        """
        try:
            self._file_handle = open(self.filepath, mode, encoding="utf-8")
            self._file_handle.write(
                json.dumps({
                    "event": "SESSION_START",
                    "timestamp": time.time(),
                    "camera": self.camera_id,
                }) + "\n"
            )
            self._file_handle.flush()
        except OSError as exc:
            print(f"[TrackingLogger] Could not open {self.filepath}: {exc}")
            self._file_handle = None


def _json_default(obj):
    """JSON serializer for numpy types and other non-standard objects."""
    import numpy as np
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return round(float(obj), 2)
    if isinstance(obj, np.ndarray):
        return [round(float(v), 2) for v in obj.flatten()]
    return str(obj)
