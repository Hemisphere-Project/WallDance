"""
Structured tracking event logger for diagnostics.

Replaces ad-hoc TRACKER_DEBUG prints with frame-stamped, structured
records that can be reviewed post-mortem to diagnose ID swaps, steals,
and ghost creation.

Output: JSONL file (one JSON object per line), auto-flushed periodically.

Where it goes (CONT-10):
  * playback / replay runs: ``start_session(dir)`` -- one isolated file per run;
  * live runs: a **per-show timestamped folder** ``<root>/<stamp>_live/``
    opened on the first live frame (``set_live_root_provider``), a new one
    whenever the show changes project or comes back from playback, the oldest
    live folders pruned (``prune_live_sessions``);
  * every session file **rotates** into ``tracking_events.0001.jsonl``,
    ``.0002`` ... (oldest first) once it exceeds ``segment_bytes``; the live
    file is always ``tracking_events.jsonl`` (``session_event_files`` lists a
    session in order).
  Nothing is written to the working directory any more (the old
  ``./tracking_events.jsonl`` appended forever: ~200 MB per 2 h show).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

EVENTS_FILE = "tracking_events.jsonl"
_SEGMENT_RE = re.compile(r"^tracking_events\.(\d{4,})\.jsonl$")
_LIVE_DIR_RE = re.compile(r"^\d{8}_\d{6}(?:_\d+)?_live$")


def session_event_files(session_dir: str) -> List[str]:
    """A session's JSONL files in chronological order: the rotated segments
    (``tracking_events.0001.jsonl`` ...), then the current file."""
    try:
        names = os.listdir(session_dir)
    except OSError:
        return []
    segs = sorted((int(m.group(1)), n) for n in names
                  for m in [_SEGMENT_RE.match(n)] if m)
    out = [os.path.join(session_dir, n) for _i, n in segs]
    if EVENTS_FILE in names:
        out.append(os.path.join(session_dir, EVENTS_FILE))
    return out


def prune_live_sessions(root: str, keep: int,
                        current: Optional[str] = None) -> List[str]:
    """Delete the oldest ``<stamp>_live`` folders under *root* beyond *keep*.

    Only folders whose name matches the live-session pattern are candidates
    (playback ``<stamp>_slot<n>`` sessions, issues, symlinks are never
    touched), and *current* is always kept.  Returns the removed paths."""
    if keep <= 0:
        return []
    try:
        names = sorted(n for n in os.listdir(root)
                       if _LIVE_DIR_RE.match(n)
                       and os.path.isdir(os.path.join(root, n))
                       and not os.path.islink(os.path.join(root, n)))
    except OSError:
        return []
    removed = []
    cur = os.path.normpath(current) if current else None
    for n in names[:-keep] if len(names) > keep else []:
        path = os.path.join(root, n)
        if cur and os.path.normpath(path) == cur:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    return removed


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
        filepath: Optional[str] = "tracking_events.jsonl",
        max_entries: int = 3000,
        flush_interval: float = 5.0,
        camera_id: int = 0,
        segment_bytes: int = 0,
        max_segments: int = 0,
    ):
        """``filepath=None``: no file until a session starts (the app's
        default since CONT-10); ``segment_bytes`` > 0 rotates session files;
        ``max_segments`` > 0 caps the files kept per session (oldest dropped)."""
        self.enabled = enabled
        self.filepath = filepath
        self.max_entries = max_entries
        self.flush_interval = flush_interval
        self.camera_id: int = camera_id
        self.segment_bytes = int(segment_bytes or 0)
        self.max_segments = int(max_segments or 0)

        self._frame: int = 0
        self._buffer: deque = deque(maxlen=max_entries)
        self._pending: List[Dict[str, Any]] = []  # not yet flushed to disk
        self._last_flush_time: float = time.time()
        self._file_handle: Optional[Any] = None
        self._session_dir: Optional[str] = None
        self._segment: int = 0                    # rotated segments so far
        self._rotation_off: bool = False          # a rename failed this session

        # Live per-show folders (CONT-10): see set_live_root_provider().
        self._live_root_fn: Optional[Callable[[], Optional[str]]] = None
        self._live_meta_fn: Optional[Callable[[], Dict[str, Any]]] = None
        self._live_keep: int = 0
        self._live_root: Optional[str] = None     # root of the current live session

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
        self._live_root = None
        self._segment = 0
        self._rotation_off = False
        os.makedirs(session_dir, exist_ok=True)
        self.filepath = os.path.join(session_dir, EVENTS_FILE)
        self._buffer.clear()
        self._pending.clear()
        self._frame = 0
        if self.enabled:
            self._open_file(mode="w")

    # ------------------------------------------------------------------
    # Live per-show sessions (CONT-10)
    # ------------------------------------------------------------------

    def set_live_root_provider(
        self,
        root_fn: Optional[Callable[[], Optional[str]]],
        meta_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        keep: int = 0,
    ):
        """Route live runs to per-show folders.

        ``root_fn()`` is asked on every FRAME_SUMMARY: it returns the folder
        live sessions belong in (e.g. ``projects/<p>/sessions``), or None while
        the frames are not live (playback owns its explicit session).  When
        it returns a root other than the current live session's, the logger
        flushes what it has to the current destination and opens a fresh
        ``<root>/<stamp>_live/`` -- on the first live frame after startup, a
        project switch, or the end of a playback.  ``meta_fn()`` adds facts to
        the folder's ``session.json``; ``keep`` > 0 prunes older live folders.
        Exceptions from the callbacks are swallowed: logging never stops a show.
        """
        self._live_root_fn = root_fn
        self._live_meta_fn = meta_fn
        self._live_keep = int(keep or 0)

    @property
    def is_live_session(self) -> bool:
        return self._live_root is not None

    def start_live_session(self, root: str) -> Optional[str]:
        """Open ``<root>/<stamp>_live/`` as the current session (see above)."""
        # Events logged while no file was open (startup, before the first
        # live frame) were never written anywhere -- carry them over.
        carry = [] if self._file_handle else list(self._pending)
        self.close()
        self._pending = carry
        self._live_root = root
        self._segment = 0
        self._rotation_off = False
        stamp = time.strftime("%Y%m%d_%H%M%S")
        name, n = f"{stamp}_live", 1
        while os.path.exists(os.path.join(root, name)):
            n += 1
            name = f"{stamp}_{n}_live"
        session_dir = os.path.join(root, name)
        try:
            os.makedirs(session_dir, exist_ok=True)
        except OSError as exc:
            print(f"[TrackingLogger] Could not create live session {session_dir}: {exc}")
            self._session_dir = None
            return None
        self._session_dir = session_dir
        self.filepath = os.path.join(session_dir, EVENTS_FILE)
        if self.enabled:
            self._open_file(mode="w")
        meta: Dict[str, Any] = {"session": name, "mode": "live",
                                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        if self._live_meta_fn is not None:
            try:
                meta.update(self._live_meta_fn() or {})
            except Exception as exc:  # noqa: BLE001 - logging must not stop the show
                meta["meta_error"] = str(exc)[:200]
        try:
            with open(os.path.join(session_dir, "session.json"), "w",
                      encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2, default=_json_default)
        except OSError:
            pass
        prune_live_sessions(root, self._live_keep, current=session_dir)
        print(f"[TrackingLogger] live session {session_dir}")
        return session_dir

    def _maybe_roll_live_session(self):
        """Open a new live folder when live frames arrive outside one."""
        if self._live_root_fn is None or not self.enabled:
            return
        try:
            root = self._live_root_fn()
        except Exception:  # noqa: BLE001
            root = None
        if not root:
            return
        if (self._live_root is not None
                and os.path.normpath(root) == os.path.normpath(self._live_root)):
            return
        self.flush()      # what is pending belongs to the previous destination
        self.start_live_session(root)

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

    def log(self, event: str, data: Optional[Dict[str, Any]] = None,
            _autoflush: bool = True):
        """Record a tracking event.

        Args:
            event: Event type string (MATCH, NEW_TRACK, DORMANT, etc.)
            data:  Event-specific payload dict.
        """
        if not self.enabled:
            return None

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
        if _autoflush and now - self._last_flush_time >= self.flush_interval:
            self.flush()
        return entry

    def annotate_frame_summary(self, **fields):
        """Add output-stage fields (e.g. the identity-slot states, CONT-6) to
        this frame's FRAME_SUMMARY.  The summary is written by the tracker
        before the output stage runs; it is kept pending (no auto-flush on
        that entry) so the annotation lands in the same JSONL line.  If it was
        flushed anyway (a session roll), the fields go out as FRAME_OUTPUT."""
        if not self.enabled or not fields:
            return
        entry = getattr(self, "_last_summary", None)
        if (entry is not None and entry.get("frame") == self._frame
                and any(e is entry for e in self._pending[-8:])):
            entry.setdefault("data", {}).update(fields)
        else:
            self.log("FRAME_OUTPUT", dict(fields))

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
        emitted: Optional[List[int]] = None,
    ):
        """Emit a FRAME_SUMMARY entry (called once per frame after update).

        This single entry lets you reconstruct the full tracker state at
        any frame — essential for post-mortem debugging.  ``emitted`` is the
        id list sent to OSC this frame (CONT-10).  A FRAME_SUMMARY is also
        the live-frame tick that opens/rolls the per-show live folder.
        """
        self._maybe_roll_live_session()
        data = {
            "n_detections": n_detections,
            "n_tracks": n_tracks,
            "track_states": track_states,
            "n_dormant": n_dormant,
            "matched_pairs": matched_pairs,
        }
        if emitted is not None:
            data["emitted"] = emitted
        # No auto-flush on this entry: the output stage may still annotate it
        # (annotate_frame_summary); the next event / flush writes it.
        self._last_summary = self.log("FRAME_SUMMARY", data, _autoflush=False)

    def flush(self):
        """Write pending entries to disk."""
        if not self._pending or not self._file_handle:
            if not self._file_handle and len(self._pending) > self.max_entries:
                # No destination yet (before the first session): keep only the
                # newest entries -- they are carried into the first live folder.
                del self._pending[:-self.max_entries]
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
        self._maybe_rotate()

    def _maybe_rotate(self):
        """Rotate the session file once it exceeds ``segment_bytes``:
        ``tracking_events.jsonl`` -> ``tracking_events.<NNNN>.jsonl`` (oldest
        first) and a fresh current file.  Session files only."""
        if (self.segment_bytes <= 0 or self._session_dir is None
                or not self._file_handle or self._rotation_off):
            return
        try:
            size = os.fstat(self._file_handle.fileno()).st_size
        except (OSError, ValueError):
            return
        if size < self.segment_bytes:
            return
        try:
            self._file_handle.close()
        except OSError:
            pass
        self._file_handle = None
        self._segment += 1
        seg = os.path.join(self._session_dir,
                           f"tracking_events.{self._segment:04d}.jsonl")
        try:
            os.replace(self.filepath, seg)
        except OSError as exc:
            # e.g. a reader holds the file open on Windows: keep appending to
            # this file and stop trying for this session (no per-flush spam).
            print(f"[TrackingLogger] rotation failed ({exc}); "
                  "appending, rotation off for this session")
            self._segment -= 1
            self._rotation_off = True
            self._open_file(mode="a")
            return
        if self.max_segments > 0:
            files = session_event_files(self._session_dir)
            # the current file (re-opened below) counts as one segment
            for old in files[:max(0, len(files) - (self.max_segments - 1))]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        self._open_file(mode="w")

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
            head = {
                "event": "SESSION_START",
                "timestamp": time.time(),
                "camera": self.camera_id,
            }
            if self._segment:
                head["segment"] = self._segment + 1   # continuation after rotation
            self._file_handle.write(json.dumps(head) + "\n")
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
