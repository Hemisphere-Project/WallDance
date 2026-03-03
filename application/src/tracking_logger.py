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
    """

    def __init__(
        self,
        enabled: bool = True,
        filepath: str = "tracking_events.jsonl",
        max_entries: int = 3000,
        flush_interval: float = 5.0,
    ):
        self.enabled = enabled
        self.filepath = filepath
        self.max_entries = max_entries
        self.flush_interval = flush_interval

        self._frame: int = 0
        self._buffer: deque = deque(maxlen=max_entries)
        self._pending: List[Dict[str, Any]] = []  # not yet flushed to disk
        self._last_flush_time: float = time.time()
        self._file_handle: Optional[Any] = None

        if self.enabled and self.filepath:
            self._open_file()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_frame(self, frame: int):
        """Set the current frame number for subsequent log calls."""
        self._frame = frame

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
        }
        if data:
            entry["data"] = data

        self._buffer.append(entry)
        self._pending.append(entry)

        # Auto-flush periodically
        now = time.time()
        if now - self._last_flush_time >= self.flush_interval:
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
        """Clear buffer and start a new log file section.

        Called on playback restart / tracker reset so the log stays
        relevant to the current run.
        """
        self._buffer.clear()
        self._pending.clear()
        self._frame = 0

        # Write separator line in the log file
        if self._file_handle:
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

    def _open_file(self):
        """Open (or re-open) the JSONL output file in append mode."""
        try:
            self._file_handle = open(self.filepath, "a", encoding="utf-8")
            # Write a session header
            self._file_handle.write(
                json.dumps({
                    "event": "SESSION_START",
                    "timestamp": time.time(),
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
