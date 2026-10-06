"""Crash marker: tell the next start that the previous session died.

Audit 2026-10 ARCH-6 / §3.5: after a crash the launcher shows a Restart button,
the operator picks the project again -- and nothing says the last session
ended abnormally, nor why. Two files in ``<repo>/logs`` fix that:

* ``last_crash.json`` -- written by the main loop when a session ends on an
  uncaught exception or a persistent frame failure: time, reason, exception,
  traceback, project, run state, app version and the session's log file.
* ``session_running.json`` -- a sentinel written at startup and removed on
  any controlled exit. If it is still there at the next start without a
  ``last_crash.json``, the process died without Python noticing (native
  crash, killed, power loss); the old log may hold a faulthandler stack.

``begin_session`` reports either one once and archives it as
``last_crash.reported.json`` (readable through the remote API's logs root).
Every function is best-effort: the marker must never stop or crash the app.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

MARKER = "last_crash.json"
REPORTED = "last_crash.reported.json"
SENTINEL = "session_running.json"


def _write_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"raw": data}
    except (FileNotFoundError, NotADirectoryError):
        return None
    except ValueError as e:               # truncated / corrupt JSON
        return {"reason": f"unreadable {path.name} ({e})"}
    except OSError as e:                  # exists but cannot be read
        return {"reason": f"unreadable {path.name} ({e})"} if path.is_file() else None


def write_crash(log_dir: Path, *, reason: str, exception: str = "",
                traceback_text: str = "", project: Optional[str] = None,
                state: Optional[str] = None, log_path: Optional[Path] = None,
                version: Optional[str] = None) -> Optional[Path]:
    """Persist the crash note. Returns its path, or None if it could not be
    written (never raises)."""
    payload = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "reason": reason,
        "exception": exception,
        "traceback": traceback_text,
        "project": project,
        "state": state,
        "log": str(log_path) if log_path else None,
        "version": version,
        "pid": os.getpid(),
    }
    path = Path(log_dir) / MARKER
    try:
        _write_json(path, payload)
        return path
    except Exception as e:  # noqa: BLE001 - best-effort by design
        print(f"[Crash] could not write {path}: {e}")
        return None


def begin_session(log_dir: Path, *, log_path: Optional[Path] = None,
                  version: Optional[str] = None) -> Optional[Dict]:
    """Collect what the previous session left behind, archive it, and mark
    this session as running. Returns the previous crash info (``kind`` is
    ``"exception"`` or ``"unclean_exit"``), or None after a clean exit."""
    log_dir = Path(log_dir)
    previous: Optional[Dict] = None
    try:
        marker = log_dir / MARKER
        sentinel = log_dir / SENTINEL
        crash = _read_json(marker)
        running = _read_json(sentinel)
        if crash is not None:
            previous = dict(crash, kind="exception")
        elif running is not None:
            previous = {
                "kind": "unclean_exit",
                "time": running.get("started"),
                "reason": ("the previous session did not shut down (native "
                           "crash, killed, or power loss) - no Python "
                           "traceback; check its log for a faulthandler dump"),
                "project": running.get("project"),
                "log": running.get("log"),
                "version": running.get("version"),
                "pid": running.get("pid"),
            }
        if previous is not None:
            previous["reported_at"] = datetime.now().isoformat(timespec="seconds")
            _write_json(log_dir / REPORTED, previous)
        for p in (marker, sentinel):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        _write_json(sentinel, {
            "started": datetime.now().isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "log": str(log_path) if log_path else None,
            "version": version,
            "project": None,
        })
    except Exception as e:  # noqa: BLE001
        print(f"[Crash] session marker unavailable: {e}")
    return previous


def note_project(log_dir: Path, project: Optional[str]) -> None:
    """Record the loaded project in the running sentinel (for unclean exits)."""
    sentinel = Path(log_dir) / SENTINEL
    try:
        data = _read_json(sentinel)
        if data is None or data.get("project") == project:
            return
        data["project"] = project
        _write_json(sentinel, data)
    except Exception:  # noqa: BLE001
        pass


def end_session(log_dir: Path) -> None:
    """Controlled exit: drop the running sentinel (a crash marker, if one was
    written, stays for the next start)."""
    try:
        (Path(log_dir) / SENTINEL).unlink()
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[Crash] could not clear the session sentinel: {e}")


def format_report(info: Dict) -> Tuple[str, str]:
    """(console block, one-line toast) for a previous crash."""
    when = info.get("time") or "unknown time"
    project = info.get("project") or "no project"
    state = f", state {info['state']}" if info.get("state") else ""
    head = info.get("exception") or info.get("reason") or "unknown"
    lines = [
        "[Crash] ===== The previous WallDance session ended abnormally =====",
        f"[Crash] when: {when}   project: {project}{state}",
        f"[Crash] why:  {info.get('reason', '')}",
    ]
    if info.get("exception"):
        lines.append(f"[Crash] exception: {info['exception']}")
    tb = (info.get("traceback") or "").rstrip().splitlines()
    for ln in tb[-12:]:
        lines.append(f"[Crash]   {ln}")
    if info.get("log"):
        lines.append(f"[Crash] that session's log: {info['log']}")
    lines.append(f"[Crash] details kept in logs/{REPORTED}")
    hhmm = when[11:16] if len(when) >= 16 else when
    short = head.splitlines()[0] if head else ""
    if len(short) > 90:
        short = short[:87] + "..."
    toast = (f"Previous session crashed ({hhmm}, {project}): {short} "
             f"- details in logs/{REPORTED}")
    return "\n".join(lines), toast
