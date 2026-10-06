"""Persistent app log: tee stdout/stderr to ``logs/walldance_<stamp>.log``.

Two problems this solves (audit 2026-10 ARCH-3 / ARCH-6):

* **Nothing persisted.** 400+ ``print()`` calls only reached the launcher's
  text box. After a crash or a show, nobody could read what happened, locally
  or through the remote API (``/api/v1/logs``).
* **Closing the launcher killed the show.** The app's stdout is a pipe the
  launcher reads. Once the launcher window is closed, the next print raised
  BrokenPipe/OSError on the main loop (the ``[Budget]`` line every 5 s). The
  tee keeps writing the file and silently drops a dead console.

Also enables ``faulthandler`` into the log, so a native crash leaves a stack.
Old logs are pruned to the newest ``keep``.
"""
from __future__ import annotations

import faulthandler
import io
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_current: Optional[Path] = None


class _Tee(io.TextIOBase):
    def __init__(self, console, logfile, lock: threading.Lock):
        self._console = console
        self._file = logfile
        self._lock = lock
        self._console_ok = console is not None

    def write(self, s: str) -> int:
        if not s:
            return 0
        with self._lock:
            try:
                self._file.write(s)
            except Exception:
                pass
        if self._console_ok:
            try:
                self._console.write(s)
            except (BrokenPipeError, OSError, ValueError, AttributeError):
                self._console_ok = False      # launcher gone: keep running, file-only
        return len(s)

    def flush(self) -> None:
        with self._lock:
            try:
                self._file.flush()
            except Exception:
                pass
        if self._console_ok:
            try:
                self._console.flush()
            except (BrokenPipeError, OSError, ValueError, AttributeError):
                self._console_ok = False

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self):          # some libraries inspect sys.stdout.encoding
        return "utf-8"


def install(log_dir: Path, keep: int = 40, header: str = "") -> Path:
    """Start teeing; returns the log path. Idempotent per process."""
    global _current
    if _current is not None:
        return _current
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"walldance_{time.strftime('%Y%m%d_%H%M%S')}.log"
    fh = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    lock = threading.Lock()
    sys.stdout = _Tee(sys.stdout, fh, lock)
    sys.stderr = _Tee(sys.stderr, fh, lock)
    try:
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:
        pass
    _current = path
    if header:
        print(header)
    _prune(log_dir, keep, path)
    return path


def current_log_path() -> Optional[Path]:
    return _current


def _prune(log_dir: Path, keep: int, current: Path) -> None:
    logs = sorted(log_dir.glob("walldance_*.log"), key=lambda p: p.stat().st_mtime,
                  reverse=True)
    for old in logs[keep:]:
        if old != current:
            try:
                old.unlink()
            except OSError:
                pass
