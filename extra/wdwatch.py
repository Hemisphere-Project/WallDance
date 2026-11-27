#!/usr/bin/env python3
"""wdwatch -- watch the WallDance app on the show laptop from the dev box, over a slow link (S1a, #t-061).

Every ~30 s it reads, through the app's loopback remote API (``services/remote_api.py``, reached through the
SSH tunnel and the cached token of ``extra/wdremote.py``):

* ``/status``: state, project, engine, fps, camera, recorder; the **health** figures (the last StatsTick, and
  the build's health line when it has one); the **live hit rate** -- foreground groups with a box, scored in
  the app by the swipe's referee (``core/swipe.py``), absent in a build without it;
* ``/events``: what the app said since the last poll (alerts, toasts, readiness, state and camera changes);
* ``/snapshot.jpg``, shrunk in the app to a few KB (``?w=&q=&max_bytes=``) -- never the video stream;
* opt-in (``--ui-shot-every N``, off by default): every N polls a small picture of the app's own window,
  ``/window.jpg`` (S1c), kept as ``ui_last.jpg`` in the run folder -- what the technician sees.

It logs them on this machine, in ``<out>/<YYYY-MM-DD_HHMMSS>/``: ``polls.jsonl`` (one JSON line a poll, plus
link, restart and cue lines), ``snapshots/*.jpg`` and ``summary.txt``, the one-screen summary rewritten after
each poll.  ``wdwatch summary`` prints that screen from the log at any time, and ``kill -USR1 <pid>`` prints it
in the watcher's own terminal.

**A slow 4G link.**  Each poll has a byte budget (``--budget-kb``): status and events first (small gzip JSON),
the snapshot gets what is left -- the app fits it to that size, and a snapshot that comes back larger is not
read.  A slow poll (over ``--slow-s``) doubles the interval (up to ``--max-interval``) and shrinks the next
snapshots; three fast polls in a row undo one step.

**Unattended for hours.**  A dropped link, a laptop asleep or an app restarting is a logged line, never an
exit: the tunnel is reopened with a back-off (60 s, 120, 240, then every ``--max-interval``), an app restart
is noticed (its start time, its event counter) and its events are read from its start.

**Two modes.**
* ``watch`` -- supervision (7-8/12): cues (link down, app down or restarted, the app's alerts, a low hit rate,
  low fps, the camera closed in RUN, the app's health line red) are printed and, with ``--notify CMD``, given
  to that command on stdin.
* ``watch --record-only`` -- the show (12/12): it only reads and logs, for the post-mortem.  It never sends a
  command and never contacts anyone: ``--notify`` is refused and the cues only go to the log.

In both modes the API client has no POST and refuses every endpoint but the reads above: a command is
``wdremote cmd``'s, typed by a person.

    python extra/wdwatch.py watch                        # supervision, logs under tmp_analysis/watch/
    python extra/wdwatch.py watch --record-only --hours 10
    python extra/wdwatch.py summary                      # the latest run's one-screen summary
"""
from __future__ import annotations

import argparse
import gzip
import http.client
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time
import traceback
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from urllib.parse import quote, urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_OUT = REPO / "tmp_analysis" / "watch"

# The only endpoints the watcher may call, all GET.  Nothing here can send a command.
READ_PATHS = ("/api/v1/ping", "/api/v1/status", "/api/v1/events", "/api/v1/snapshot.jpg",
              "/api/v1/window.jpg")      # S1c: the window picture, only with --ui-shot-every
EVENT_TYPES = ("Alert,AlertCleared,Toast,ReadinessResult,StateChanged,CameraStatus,TrtBanner,"
               "CalibReportCard,ConfigSaved")
JSON_CAP = 256 * 1024          # a status or events answer larger than this is not read (bytes on the wire)
OVERHEAD = 400                 # request + response headers, counted against the poll's budget
MIN_SNAP = 3 * 1024            # less room than this left in the budget: no snapshot this poll
SSH_OPTS = ["-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3"]


def _wdremote():
    """The sibling tool (tunnel, token cache, config), imported lazily: the tests and ``summary`` need no ssh."""
    if "wdremote" in sys.modules:
        return sys.modules["wdremote"]
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import wdremote
    return wdremote


# ---------------------------------------------------------------------------
# The read-only API client
# ---------------------------------------------------------------------------

class ReadOnlyViolation(RuntimeError):
    """Something asked the watcher to send, or to call an endpoint it must not."""


class ApiError(Exception):
    """The request did not get an answer (connection refused or reset, timeout)."""

    def __init__(self, what: str, timeout: bool = False):
        super().__init__(what)
        self.timeout = timeout


class OverBudget(Exception):
    def __init__(self, size: int, room: int):
        super().__init__(f"{size} B > {room} B")
        self.size, self.room = size, room


class ApiReader:
    """GET-only client of the app's remote API.  It has no way to POST, in any mode; a path outside
    ``READ_PATHS`` raises before anything goes on the wire."""

    def __init__(self, base: str, token: str, timeout: float = 20.0):
        u = urlsplit(base)
        self.host, self.port = u.hostname or "127.0.0.1", int(u.port or 80)
        self.token, self.timeout = token, float(timeout)

    def get(self, path: str, max_bytes: int) -> Tuple[int, bytes, int]:
        """(HTTP code, body decoded, bytes on the wire).  ``OverBudget`` when the answer is larger than
        ``max_bytes`` on the wire -- announced by Content-Length, it is not read at all."""
        if urlsplit(path).path not in READ_PATHS:
            raise ReadOnlyViolation(f"the watcher only reads: {path} is not one of {READ_PATHS}")
        return self._request("GET", path, max_bytes)

    def _request(self, method: str, path: str, max_bytes: int) -> Tuple[int, bytes, int]:
        if method != "GET":
            raise ReadOnlyViolation(f"the watcher never sends ({method} {path})")
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request("GET", path, headers={"Authorization": f"Bearer {self.token}",
                                               "Accept-Encoding": "gzip"})
            r = conn.getresponse()
            clen = r.getheader("Content-Length")
            if clen is not None and clen.isdigit() and int(clen) > max_bytes:
                raise OverBudget(int(clen), max_bytes)
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise OverBudget(len(data), max_bytes)
            wire = len(data)
            if r.getheader("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return r.status, data, wire
        except (socket.timeout, TimeoutError) as e:
            raise ApiError(f"timeout after {self.timeout:g} s ({e})", timeout=True) from None
        except (OSError, http.client.HTTPException) as e:
            raise ApiError(f"{type(e).__name__}: {e}") from None
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# The tunnel (wdremote's ApiSession kept open across polls)
# ---------------------------------------------------------------------------

class TunnelSession:
    """``wdremote.ApiSession`` -- the ssh -L tunnel to the laptop's 127.0.0.1:<api_port> and the cached
    token -- held open between polls.  Raises ``ApiError`` when the tunnel does not come up."""

    def __init__(self, tr, remote, refresh_token: bool = False):
        wd = _wdremote()
        try:
            if refresh_token:
                wd.get_token(tr, remote, refresh=True)
            self._s = wd.ApiSession(tr, remote)
            self._s.__enter__()
        except (Exception, SystemExit) as e:      # wdremote exits on a failed tunnel or token read
            raise ApiError(f"tunnel: {e}") from None
        self.base, self.token = self._s.base, self._s.token
        if self._s.proc is not None:              # ApiSession waits 15 s at most, then returns anyway
            u = urlsplit(self.base)
            try:
                with socket.create_connection((u.hostname, u.port), timeout=2.0):
                    pass
            except OSError:
                self.close()
                raise ApiError("tunnel: the ssh forward did not come up") from None

    def alive(self) -> bool:
        p = self._s.proc
        return p is None or p.poll() is None

    def close(self) -> None:
        try:
            self._s.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


def tunnel_opener(tr, remote) -> Callable[..., TunnelSession]:
    remote = replace(remote, ssh_opts=[*remote.ssh_opts, *SSH_OPTS])

    def open_session(refresh_token: bool = False) -> TunnelSession:
        return TunnelSession(tr, remote, refresh_token)
    return open_session


# ---------------------------------------------------------------------------
# Pace: interval, back-off, snapshot size
# ---------------------------------------------------------------------------

@dataclass
class Pacer:
    base: float = 30.0
    max_interval: float = 300.0
    slow_s: float = 10.0
    max_slow: int = 3
    fails: int = 0               # failed polls in a row
    slow: int = 0                # slow steps (each doubles the interval and shrinks the snapshot)
    fast: int = 0                # fast polls in a row

    def ok(self, dur_s: float) -> None:
        self.fails = 0
        if dur_s > self.slow_s:
            self.slow, self.fast = min(self.max_slow, self.slow + 1), 0
        else:
            self.fast += 1
            if self.fast >= 3 and self.slow > 0:
                self.slow, self.fast = self.slow - 1, 0

    def fail(self) -> None:
        self.fails += 1
        self.fast = 0

    @property
    def interval(self) -> float:
        k = self.fails if self.fails else self.slow
        return float(min(self.max_interval, self.base * 2 ** k))


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def _hms(t: Optional[float]) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t)) if t else "--:--:--"


def health_of(st: Dict[str, Any]) -> Dict[str, Any]:
    """The health figures of a /status: the last StatsTick, and the build's health line when it has one."""
    p = (((st.get("latest") or {}).get("StatsTick") or {}).get("payload") or {})
    h = {"fps": st.get("fps", p.get("fps")), "latency_ms": p.get("latency_ms"),
         "dancers": p.get("num_dancers"), "slots": p.get("slots_text"),
         "camera_running": p.get("camera_running"), "camera_reconnecting": p.get("camera_reconnecting"),
         "gpu_fallback": p.get("gpu_fallback_reason") or None,
         "enhance_bypassed": p.get("enhance_bypassed")}
    if isinstance(p.get("points"), dict):   # F4h: the points OSC sends, of which held (absent in older builds)
        h["points"] = p["points"]
    if "health" in st:
        h["line"] = st["health"]
    return h


# ---------------------------------------------------------------------------
# The watcher
# ---------------------------------------------------------------------------

_SCREEN_KEYS = ("state", "project", "uptime_s", "app", "engine", "camera", "recorder", "osc")


def _slim(rec: Dict[str, Any]) -> Dict[str, Any]:
    """A poll as the summary screen needs it (hours of full statuses stay in the file, not in memory)."""
    st = rec.get("status")
    if rec.get("kind") != "poll" or not isinstance(st, dict):
        return rec
    return dict(rec, status={k: st[k] for k in _SCREEN_KEYS if k in st})


class _Stop(BaseException):
    """SIGTERM: leave the loop cleanly (not an Exception, so no poll handler swallows it)."""


class Watcher:
    """One poll every ``pacer.interval`` seconds, logged; never exits on a link or app failure.

    ``open_session(refresh_token=False)`` returns an object with ``base``, ``token``, ``alive()`` and
    ``close()`` (``TunnelSession``; the tests pass a fake pointing at a fake API server)."""

    def __init__(self, open_session: Callable[..., Any], run_dir: Path, *, record_only: bool = False,
                 interval: float = 30.0, max_interval: float = 300.0, slow_s: float = 10.0,
                 budget: int = 40 * 1024, snap_width: int = 320, snap_quality: int = 50,
                 snap_every: int = 1, full_snapshots: bool = False, max_snap_mb: float = 2000.0,
                 event_types: str = EVENT_TYPES, cue_hit: float = 0.75, cue_fps: float = 10.0,
                 timeout: float = 20.0, notify: Optional[Callable[[str], None]] = None,
                 host: str = "", clock: Callable[[], float] = time.time, out=None,
                 ui_shot_every: int = 0, ui_shot_width: int = 800, ui_shot_kb: float = 80.0):
        if record_only and notify is not None:
            raise ValueError("record-only never contacts anyone: no notify hook")
        self.open_session = open_session
        self.run_dir = Path(run_dir)
        self.record_only = bool(record_only)
        self.pacer = Pacer(base=float(interval), max_interval=float(max_interval), slow_s=float(slow_s))
        self.budget = int(budget)
        self.snap_width, self.snap_quality = int(snap_width), int(snap_quality)
        self.snap_every = max(1, int(snap_every))
        self.full_snapshots = bool(full_snapshots)
        self.max_snap_bytes = int(max_snap_mb * 1024 * 1024)
        self.event_types = event_types
        self.cue_hit, self.cue_fps = float(cue_hit), float(cue_fps)
        self.timeout = float(timeout)
        self.notify = notify
        self.host = host
        self.clock = clock
        self.out = out if out is not None else sys.stdout
        self.session: Any = None
        self.n = 0                              # polls so far
        self.shrink = 0                         # extra snapshot shrink steps after an over-budget answer
        self.snap_bytes = 0
        self.event_since: Optional[int] = None  # None: first contact with this app
        self.app_seen: Optional[Dict[str, Any]] = None
        self.restarts = 0
        self.down_since: Optional[float] = None
        self.down_polls = 0
        self.down_state = ""
        self.need_token = False
        self._cues: Dict[str, str] = {}         # active cue -> its text
        self._streak: Dict[str, int] = {}
        self.records: Deque[Dict[str, Any]] = deque(maxlen=6000)
        # S1c (opt-in, 0 = off): a small picture of the app's window every N polls, the last one kept
        self.ui_shot_every = max(0, int(ui_shot_every))
        self.ui_shot_width, self.ui_shot_bytes = int(ui_shot_width), int(ui_shot_kb * 1024)
        (self.run_dir / "snapshots").mkdir(parents=True, exist_ok=True)
        self._log_path = self.run_dir / "polls.jsonl"

    # -- log ------------------------------------------------------------------
    def _log(self, rec: Dict[str, Any]) -> None:
        self.records.append(_slim(rec))         # the file keeps everything; memory what the screen shows
        try:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str, separators=(",", ":")) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:                    # a full disk must not stop the watch
            print(f"[wdwatch] cannot write the log: {e}", file=sys.stderr)

    def _say(self, line: str) -> None:
        try:
            print(line, file=self.out, flush=True)
        except Exception:  # noqa: BLE001 - a closed terminal must not stop the watch
            pass

    def start(self, **meta) -> None:
        self._log({"kind": "start", "t": round(self.clock(), 3), "iso": _iso(self.clock()),
                   "run": self.run_dir.name, "host": self.host, "record_only": self.record_only,
                   "interval_s": self.pacer.base, "budget": self.budget, "pid": os.getpid(), **meta})

    def close(self, why: str = "stopped") -> None:
        if self.session is not None:
            self.session.close()
            self.session = None
        self._log({"kind": "stop", "t": round(self.clock(), 3), "iso": _iso(self.clock()), "why": why,
                   "polls": self.n})

    # -- one poll ---------------------------------------------------------------
    def poll_once(self) -> Dict[str, Any]:
        t = self.clock()
        m0 = time.monotonic()
        self.n += 1
        rec: Dict[str, Any] = {"kind": "poll", "t": round(t, 3), "iso": _iso(t), "n": self.n}
        try:
            used = self._poll(rec)
            dur = time.monotonic() - m0
            self.pacer.ok(dur)
            rec.update(ok=True, link="ok", bytes=used, dur_s=round(dur, 2))
            self._link_up(t)
        except ApiError as e:
            self._failed(rec, t, str(e), timeout=e.timeout)
        except _AuthError as e:
            self._failed(rec, t, str(e), state="auth")
        except _AppError as e:
            self._failed(rec, t, str(e), state="app_down")
        except Exception as e:  # noqa: BLE001 - unattended: log it, keep watching
            tb = traceback.format_exception_only(type(e), e)[-1].strip()
            self._failed(rec, t, f"watcher error: {tb}", state="error")
        rec["interval_s"] = self.pacer.interval
        self._log(rec)
        self._cue_check(rec)
        self._write_summary()
        self._say(self._one_line(rec))
        return rec

    def _session(self):
        if self.session is not None and not self.session.alive():
            self.session.close()
            self.session = None
        if self.session is None:
            self.session = self.open_session(refresh_token=self.need_token)
            self.need_token = False
        return self.session

    def _poll(self, rec: Dict[str, Any]) -> int:
        s = self._session()
        api = ApiReader(s.base, s.token, self.timeout)
        used = 0
        code, body, wire = api.get("/api/v1/status", JSON_CAP)
        used += wire + OVERHEAD
        if code == 401:
            self.need_token = True
            raise _AuthError("401: the token was refused (re-read from the laptop next poll)")
        if code != 200:
            raise _AppError(f"status answered {code}")
        st = json.loads(body or b"{}")
        self._check_restart(st, rec)
        rec["status"] = st
        rec["hit_rate"] = st.get("hit_rate")
        rec["health"] = health_of(st)
        used += self._events(api, st, rec)
        rec["snapshot"], snap_used = self._snapshot(api, st, self.budget - used, rec)
        if self.ui_shot_every:                  # S1c, opt-in: its own budget, outside the poll's
            rec["ui_shot"], ui_used = self._ui_shot(api, st)
            snap_used += ui_used
        return used + snap_used

    def _check_restart(self, st: Dict[str, Any], rec: Dict[str, Any]) -> None:
        up, t_app, seq = st.get("uptime_s"), st.get("t"), st.get("event_seq")
        started = (t_app - up) if isinstance(up, (int, float)) and isinstance(t_app, (int, float)) else None
        commit = (st.get("app") or {}).get("commit")
        prev = self.app_seen
        if prev is not None:
            moved = (started is not None and prev["started"] is not None
                     and abs(started - prev["started"]) > 30.0)
            back = isinstance(seq, int) and isinstance(prev["seq"], int) and seq < prev["seq"]
            if moved or back or (commit and prev["commit"] and commit != prev["commit"]):
                self.restarts += 1
                self.event_since = 0                # the new app's events, from its start
                rec["restart"] = True
                self._log({"kind": "restart", "t": rec["t"], "iso": rec["iso"], "uptime_s": up,
                           "commit": commit, "prev_commit": prev["commit"], "event_seq": seq})
        self.app_seen = {"started": started, "seq": seq, "commit": commit}

    def _events(self, api: ApiReader, st: Dict[str, Any], rec: Dict[str, Any]) -> int:
        seq = st.get("event_seq")
        if self.event_since is None:            # first contact: the last few events, not the whole ring
            self.event_since = max(0, int(seq or 0) - 50)
        path = (f"/api/v1/events?since={self.event_since}&wait=0"
                f"&types={quote(self.event_types, safe=',')}")
        try:
            code, body, wire = api.get(path, JSON_CAP)
        except OverBudget as e:
            rec["events_skipped"] = f"over {JSON_CAP} B ({e.size} B)"
            self.event_since = int(seq or self.event_since)
            return OVERHEAD
        if code == 200:
            ev = json.loads(body or b"{}")
            rec["events"] = ev.get("events", [])
            if ev.get("dropped"):
                rec["events_dropped"] = True
            self.event_since = int(ev.get("next", self.event_since))
        else:
            rec["events_skipped"] = f"http {code}"
        return wire + OVERHEAD

    def _snap_size(self) -> Tuple[int, int]:
        k = self.pacer.slow + self.shrink
        return max(96, int(self.snap_width * 0.75 ** k)), max(30, self.snap_quality - 5 * k)

    def _snapshot(self, api: ApiReader, st: Dict[str, Any], left: int,
                  rec: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        every = self.snap_every * (2 if self.pacer.slow >= self.pacer.max_slow else 1)
        if (self.n - 1) % every:
            return {"skipped": "cadence"}, 0
        if self.snap_bytes >= self.max_snap_bytes:
            return {"skipped": "disk cap reached"}, 0
        fit = bool(st.get("snapshot_fit"))
        if not fit and not self.full_snapshots:
            return {"skipped": "this build cannot shrink snapshots (--full-snapshots to take them anyway)"}, 0
        room = left - OVERHEAD
        if room < MIN_SNAP:
            return {"skipped": f"budget: {max(0, left)} B left"}, 0
        w, q = self._snap_size()
        path = f"/api/v1/snapshot.jpg?w={w}&q={q}&max_bytes={room}" if fit else "/api/v1/snapshot.jpg"
        try:
            code, body, wire = api.get(path, room)
        except OverBudget as e:
            self.shrink = min(self.shrink + 1, 4)
            return {"skipped": f"over budget: {e.size} B > {room} B, not read", "w": w, "q": q}, OVERHEAD
        except ApiError as e:                   # the status came: the poll stands, the snapshot is lost
            return {"skipped": f"link: {e}"}, OVERHEAD
        if code == 413:
            self.shrink = min(self.shrink + 1, 4)
            return {"skipped": f"the app could not fit it in {room} B", "w": w, "q": q}, wire + OVERHEAD
        if code != 200 or not body:
            return {"skipped": f"http {code} (no preview?)"}, wire + OVERHEAD
        if self.shrink and len(body) < room // 2:
            self.shrink -= 1
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(rec["t"])) + f"-{self.n:05d}.jpg"
        path_out = self.run_dir / "snapshots" / name
        try:
            tmp = path_out.with_suffix(".part")
            tmp.write_bytes(body)
            os.replace(tmp, path_out)
        except OSError as e:
            return {"skipped": f"cannot save: {e}"}, wire + OVERHEAD
        self.snap_bytes += len(body)
        return {"file": f"snapshots/{name}", "bytes": len(body), "w": w if fit else None,
                "q": q if fit else None}, wire + OVERHEAD

    def _ui_shot(self, api: ApiReader, st: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """S1c (``--ui-shot-every N``): every N polls (2N on a slow link) a small picture of the app's own
        window, fitted in the app to ``--ui-shot-kb``, saved over ``ui_last.jpg`` in the run folder. A GET like
        the rest; the app may answer 429 (one capture every 10 s in RUN) or 503 (no window): logged, skipped."""
        every = self.ui_shot_every * (2 if self.pacer.slow >= self.pacer.max_slow else 1)
        if (self.n - 1) % every:
            return {"skipped": "cadence"}, 0
        if "window_shot" not in st:
            return {"skipped": "this build has no window picture"}, 0
        room = self.ui_shot_bytes
        path = f"/api/v1/window.jpg?w={self.ui_shot_width}&q=60&max_bytes={room}"
        try:
            code, body, wire = api.get(path, room)
        except OverBudget as e:
            return {"skipped": f"over budget: {e.size} B > {room} B, not read"}, OVERHEAD
        except ApiError as e:
            return {"skipped": f"link: {e}"}, OVERHEAD
        if code != 200 or not body:
            try:
                why = str(json.loads(body or b"{}").get("error", ""))[:160]
            except ValueError:
                why = ""
            return {"skipped": f"http {code}" + (f": {why}" if why else "")}, wire + OVERHEAD
        try:
            tmp = self.run_dir / "ui_last.jpg.part"
            tmp.write_bytes(body)
            os.replace(tmp, self.run_dir / "ui_last.jpg")
        except OSError as e:
            return {"skipped": f"cannot save: {e}"}, wire + OVERHEAD
        return {"file": "ui_last.jpg", "bytes": len(body), "w": self.ui_shot_width}, wire + OVERHEAD

    # -- failures and the link's state ---------------------------------------------
    def _failed(self, rec: Dict[str, Any], t: float, why: str, state: Optional[str] = None,
                timeout: bool = False) -> None:
        if state is None:
            alive = self.session is not None and self.session.alive()
            state = "timeout" if timeout else ("app_down" if alive else "down")
        self.pacer.fail()
        if self.session is not None and (state != "app_down" or self.pacer.fails % 3 == 0):
            self.session.close()                # a fresh tunnel next time (a hung 4G channel, a new token)
            self.session = None
        rec.update(ok=False, link=state, error=why)
        if self.down_since is None:
            self.down_since, self.down_polls = t, 0
            self._log({"kind": "link", "t": round(t, 3), "iso": _iso(t), "state": state, "error": why})
        self.down_polls += 1
        self.down_state = state

    def _link_up(self, t: float) -> None:
        if self.down_since is not None:
            self._log({"kind": "link", "t": round(t, 3), "iso": _iso(t), "state": "up",
                       "down_s": round(t - self.down_since, 1), "failed_polls": self.down_polls,
                       "was": self.down_state})
            self.down_since, self.down_polls, self.down_state = None, 0, ""

    # -- cues -------------------------------------------------------------------
    def _conditions(self, rec: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """name -> text while the condition holds, None when it does not.  A failed poll judges only the
        link and the app: what the app looks like stays as last seen (no « back to normal » unseen)."""
        c: Dict[str, Optional[str]] = {"link": None, "app": None}
        if not rec.get("ok"):
            if rec.get("link") == "app_down":
                c["app"] = f"the app does not answer (since {_hms(self.down_since)})"
            else:
                c["link"] = f"the laptop is unreachable ({rec.get('link')}, since {_hms(self.down_since)})"
            return c
        c.update(hit=None, fps=None, camera=None)
        st = rec.get("status") or {}
        run = st.get("state") == "run"
        hit = rec.get("hit_rate") or {}
        if hit.get("rate") is not None and (hit.get("groups") or 0) >= 10 and hit["rate"] < self.cue_hit:
            c["hit"] = (f"hit rate {100 * hit['rate']:.0f} % ({hit.get('covered')}/{hit.get('groups')} "
                        f"foreground groups with a box)")
        fps = (rec.get("health") or {}).get("fps")
        if run and isinstance(fps, (int, float)) and fps < self.cue_fps:
            c["fps"] = f"{fps:.1f} fps in RUN"
        cam = st.get("camera") or {}
        if run and cam.get("open") is False:
            c["camera"] = f"the camera is closed in RUN ({cam.get('source')})"
        line = (rec.get("health") or {}).get("line")       # H2: the app's one health line, red
        c["health"] = (f"health line red: {_health_line(line)}"
                       if isinstance(line, dict) and line.get("state") == "fail" else None)
        return c

    def _cue_check(self, rec: Dict[str, Any]) -> None:
        cues: List[Tuple[str, str, bool]] = []
        for name, text in self._conditions(rec).items():
            n = self._streak.get(name, 0) + 1 if text else 0
            self._streak[name] = n
            if text and n >= 2 and name not in self._cues:
                self._cues[name] = text
                cues.append((name, text, True))
            elif not text and name in self._cues:
                del self._cues[name]
                cues.append((name, f"{name} back to normal", False))
        if rec.get("restart"):
            cues.append(("restart", f"the app restarted (up {rec['status'].get('uptime_s')} s)", True))
        for e in rec.get("events") or []:
            if e.get("type") == "Alert":
                cues.append((f"alert:{e.get('kind')}", f"app alert {e.get('kind')}: {e.get('message')}", True))
        for name, text, on in cues:
            self._log({"kind": "cue", "t": rec["t"], "iso": rec["iso"], "name": name, "on": on, "text": text})
            if self.record_only:
                continue                        # the log only: record-only contacts no one
            self._say(f"!! {_hms(rec['t'])} {text}")
            if self.notify is not None:
                try:
                    self.notify(f"WallDance {_hms(rec['t'])}: {text}")
                except Exception as e:  # noqa: BLE001
                    self._say(f"[wdwatch] notify failed: {e}")

    # -- screens ------------------------------------------------------------------
    def _one_line(self, rec: Dict[str, Any]) -> str:
        if not rec.get("ok"):
            return (f"{_hms(rec['t'])} {rec.get('link')}: {rec.get('error')} -- retry in "
                    f"{rec['interval_s']:.0f} s")
        st, h, hit = rec.get("status") or {}, rec.get("health") or {}, rec.get("hit_rate")
        rate = hit.get("rate") if hit else None
        hs = "hit n/a" if not hit else ("hit --" if rate is None else f"hit {100 * rate:.0f} %")
        snap = rec.get("snapshot") or {}
        ss = f"snapshot {snap['bytes'] / 1024:.1f} KB" if "file" in snap else "no snapshot"
        hl = h.get("line") if isinstance(h.get("line"), dict) else {}
        hs += f" health {hl.get('word') or str(hl.get('state') or '-').upper()}" if hl else ""
        return (f"{_hms(rec['t'])} {str(st.get('state', '?')).upper()} fps {h.get('fps')} {hs} | {ss} | "
                f"poll {rec.get('bytes', 0) / 1024:.1f} KB in {rec.get('dur_s')} s | next "
                f"{rec['interval_s']:.0f} s")

    def summary_text(self, minutes: float = 15.0) -> str:
        return render_summary(list(self.records), self.clock(), minutes)

    def _write_summary(self) -> None:
        try:
            tmp = self.run_dir / "summary.txt.part"
            tmp.write_text(self.summary_text() + "\n", encoding="utf-8")
            os.replace(tmp, self.run_dir / "summary.txt")
        except Exception as e:  # noqa: BLE001
            print(f"[wdwatch] cannot write the summary: {e}", file=sys.stderr)

    # -- the loop ---------------------------------------------------------------
    def run(self, hours: Optional[float] = None, max_polls: Optional[int] = None,
            sleep: Callable[[float], None] = time.sleep) -> None:
        end = time.monotonic() + hours * 3600 if hours else None
        why = "stopped"
        try:
            while True:
                m0 = time.monotonic()
                self.poll_once()
                if max_polls is not None and self.n >= max_polls:
                    why = "max polls"
                    break
                wait = max(1.0, self.pacer.interval - (time.monotonic() - m0))
                if end is not None and time.monotonic() + wait > end:
                    why = "hours elapsed"
                    break
                sleep(wait)
        except (KeyboardInterrupt, _Stop):
            why = "interrupted"
        finally:
            self.close(why)


class _AuthError(Exception):
    pass


class _AppError(Exception):
    pass


# ---------------------------------------------------------------------------
# The one-screen summary (from the log)
# ---------------------------------------------------------------------------

def _ago(s: float) -> str:
    s = max(0, int(s))
    return f"{s} s" if s < 120 else (f"{s // 60} min" if s < 7200 else f"{s // 3600} h {s % 3600 // 60:02d}")


def _dur(s: Optional[float]) -> str:
    if not isinstance(s, (int, float)):
        return "?"
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}" if s >= 3600 else f"{s // 60}m{s % 60:02d}"


def _pct(r: Optional[float]) -> str:
    return "--" if r is None else f"{100 * r:.0f} %"


def _health_line(line: Any) -> str:
    if isinstance(line, dict):
        for k in ("line", "text", "summary"):
            if isinstance(line.get(k), str):
                return line[k]
        return json.dumps(line, default=str)
    return str(line)


# H2: one character a poll for the app's health line -- G ok, n not checked, o warn, R fail, - none, ! no answer
_HEALTH_MARK = {"ok": "G", "skip": "n", "warn": "o", "fail": "R"}


def _health_spark(polls: List[Dict[str, Any]], since: float) -> str:
    out = []
    for p in polls:
        if p["t"] < since:
            continue
        line = (p.get("health") or {}).get("line") if p.get("ok") else None
        out.append("!" if not p.get("ok") else
                   (_HEALTH_MARK.get(line.get("state"), "?") if isinstance(line, dict) else "-"))
    return "".join(out)


_HELD_WORDS = {"fg": "held on the foreground", "belt": "held on the belt", "weak": "held on a weak detection",
               "coasting": "coasting", "live": "live"}   # core/points_report.SLOT_WORDS (this tool runs app-free)
# W1e: the same states in the POINTS line's short words, when the long ones would pass the summary's width
_HELD_SHORT = {"fg": "fg", "belt": "belt", "weak": "weak", "coasting": "coast", "live": "live"}


def _points_line(oks: List[Dict[str, Any]], since: float, minutes: float, width: int = 100) -> str:
    """F4h: « POINTS now 1 sent, 1 held: D1 held on the foreground 21.6s | 15 min: sent at 5 of 17 polls, held
    at 5 » -- the points OSC sends, from the build's StatsTick ``points``.  "" (no line, the screen as before)
    when the build does not report them or no point was sent in the window.  W1e: within ``width``, the counts
    never cut -- the slots in short words past it (« D1 fg 10.7s, D2 belt 0.3s »), then without their ages, then
    the counts alone."""
    win = [p for p in oks if p["t"] >= since and isinstance((p.get("health") or {}).get("points"), dict)]
    sent = [p for p in win if (p["health"]["points"].get("sent") or 0) > 0]
    if not sent:
        return ""
    held = [p for p in sent if (p["health"]["points"].get("held") or 0) > 0]
    pts = (oks[-1].get("health") or {}).get("points")
    counts = f" | {minutes:.0f} min: sent at {len(sent)} of {len(win)} polls, held at {len(held)}"
    if not isinstance(pts, dict) or not pts.get("sent"):
        return f"POINTS now none sent{counts}"
    head = f"POINTS now {pts.get('sent')} sent, {pts.get('held') or 0} held"

    def one(s: Dict[str, Any], words: Dict[str, str], ages: bool) -> str:
        age = s.get("age_s")
        tail = f" {age:.1f}s" if ages and isinstance(age, (int, float)) and s.get("state") != "live" else ""
        return f"D{s.get('id')} {words.get(s.get('state'), s.get('state'))}{tail}"
    for words, ages in ((_HELD_WORDS, True), (_HELD_SHORT, True), (_HELD_SHORT, False)):   # W1e: fit, counts whole
        line = f"{head}: " + ", ".join(one(s, words, ages) for s in pts.get("slots") or ()) + counts
        if len(line) <= width:
            return line
    return head + counts


def render_summary(records: List[Dict[str, Any]], now: float, minutes: float = 15.0,
                   width: int = 100) -> str:
    """One screen (<= 24 lines of <= ``width`` ASCII characters) from a run's log records."""
    start = next((r for r in records if r.get("kind") == "start"), {})
    polls = [r for r in records if r.get("kind") == "poll"]
    mode = "RECORD-ONLY" if start.get("record_only") else "supervision"
    out = [f"WallDance watch -- run {start.get('run', '?')} ({mode}) -- {start.get('host') or '?'}"]
    if not polls:
        out.append(f"now {_hms(now)} | no poll yet")
        return "\n".join(x[:width] for x in out)
    since = now - minutes * 60
    last = polls[-1]
    oks = [p for p in polls if p.get("ok")]
    out.append(f"now {_hms(now)} | last poll {_hms(last['t'])} ({_ago(now - last['t'])} ago) "
               f"{'ok' if last.get('ok') else last.get('link')} | next ~{last.get('interval_s', 0):.0f} s"
               f" | polls {len(oks)} ok, {len(polls) - len(oks)} failed")
    lo = oks[-1] if oks else None
    if lo is not None:
        st, h = lo.get("status") or {}, lo.get("health") or {}
        asof = "" if lo is last else f"  [as of {_hms(lo['t'])}]"
        app, eng, cam = st.get("app") or {}, st.get("engine") or {}, st.get("camera") or {}
        rec, osc = st.get("recorder") or {}, st.get("osc") or {}
        restarts = sum(1 for r in records if r.get("kind") == "restart")
        out.append(f"APP    {str(st.get('state', '?')).upper()} | {st.get('project')} | up "
                   f"{_dur(st.get('uptime_s'))} | build {str(app.get('commit') or '?')[:9]} "
                   f"{app.get('branch') or ''} | restarts seen {restarts}{asof}")
        lat = h.get("latency_ms")
        fps = h.get("fps")
        out.append(f"ENGINE {eng.get('model')} @ {eng.get('imgsz')} trt {eng.get('trt_active')} | fps "
                   f"{fps if not isinstance(fps, float) else round(fps, 1)} | latency "
                   f"{'?' if not isinstance(lat, (int, float)) else round(lat)} ms | dancers "
                   f"{h.get('dancers')} | {h.get('slots') or ''}")
        out.append(f"CAMERA {'open' if cam.get('open') else 'CLOSED'} ({cam.get('type')}) "
                   f"{'x'.join(str(v) for v in cam.get('size') or [])}"
                   f"{' RECONNECTING' if h.get('camera_reconnecting') else ''} | recorder {rec.get('state')}"
                   f" | OSC {'on' if osc.get('enabled') else 'off'} {osc.get('target', '')}"
                   f"{' | GPU fallback: ' + str(h['gpu_fallback']) if h.get('gpu_fallback') else ''}")
        pts_line = _points_line(oks, since, minutes, width)
        if pts_line:
            out.append(pts_line)
        out.append("HEALTH " + (_health_line(h["line"]) if "line" in h else "(no health line in this build)"))
        if "line" in h:                     # H2: its state at each poll of the window
            spark = _health_spark(polls, since)
            red = sum(1 for p in polls if p["t"] >= since and p.get("ok")
                      and ((p.get("health") or {}).get("line") or {}).get("state") == "fail")
            for_s = h["line"].get("for_s") if isinstance(h["line"], dict) else None
            held = f"this state {_dur(for_s)}, " if isinstance(for_s, (int, float)) else ""
            out.append(f"       {held}{minutes:.0f} min: red at {red} poll(s) | {spark[-40:]}  (G ok, n not "
                       "checked, o warn, R red, ! no answer)")
        hit = lo.get("hit_rate")
        if not hit:
            out.append("HIT    absent (this build has no referee, core/swipe.py)")
        else:
            recent = [p for p in oks if p["t"] >= since and (p.get("hit_rate") or {}).get("rate") is not None]
            rates = sorted(p["hit_rate"]["rate"] for p in recent)
            g_run = c_run = 0                   # since the watch began, from the app's running totals
            prev = None
            for p in oks:
                tot = (p.get("hit_rate") or {}).get("total")
                if not tot:
                    continue
                if prev is not None:
                    if tot["groups"] >= prev["groups"] and tot["covered"] >= prev["covered"]:
                        g_run += tot["groups"] - prev["groups"]
                        c_run += tot["covered"] - prev["covered"]
                    else:                       # the app restarted: its totals began again at 0
                        g_run += tot["groups"]
                        c_run += tot["covered"]
                prev = tot
            span = (f"{minutes:.0f} min: min {_pct(rates[0])} median {_pct(rates[len(rates) // 2])}"
                    if rates else f"{minutes:.0f} min: no figure")
            run_s = f"watched {_pct(c_run / g_run)} of {g_run} groups" if g_run else "watched: no group yet"
            out.append(f"HIT    now {_pct(hit.get('rate'))} ({hit.get('covered')}/{hit.get('groups')} groups, "
                       f"{hit.get('window_s', 0):.0f} s) | {run_s} | frames without fg "
                       f"{hit.get('skipped', 0)}")
            spark = "".join("!" if not p.get("ok") else
                            ("-" if (p.get("hit_rate") or {}).get("rate") is None
                             else str(min(9, int(10 * p["hit_rate"]["rate"]))))
                            for p in polls if p["t"] >= since)
            out.append(f"       {span} | {spark[-40:]}  (tens of %, - none, ! no answer)")
    else:
        out.append("APP    no answer yet")
    cues = [r for r in records if r.get("kind") == "cue" and r["t"] >= since]
    if cues:
        out.append(f"CUES   last {minutes:.0f} min: {len(cues)}")
        for c in cues[-4:]:
            out.append(f"       {_hms(c['t'])} {'' if c.get('on') else '(over) '}{c.get('text')}")
    else:
        out.append(f"CUES   none in the last {minutes:.0f} min")
    evs = [e for p in polls if p["t"] >= since for e in (p.get("events") or [])]
    if evs:
        counts: Dict[str, int] = {}
        for e in evs:
            counts[e.get("type", "?")] = counts.get(e.get("type", "?"), 0) + 1
        out.append(f"EVENTS last {minutes:.0f} min: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
        for e in [e for e in evs if e.get("type") in ("Alert", "Toast")][-2:]:
            kind = f" {e['kind']}" if e.get("kind") else ""
            out.append(f"       {_hms(e.get('t'))} {e.get('type')}{kind}: {e.get('message')}")
    links = [r for r in records if r.get("kind") == "link"]
    downs: List[str] = []
    for i, r in enumerate(links):
        if r.get("state") != "up":
            up = next((u for u in links[i + 1:] if u.get("state") == "up"), None)
            downs.append(f"{_hms(r['t'])}-{_hms(up['t']) if up else 'now'} {r.get('state')}")
    tot_b = sum(p.get("bytes") or 0 for p in polls)
    avg_b = tot_b / len(oks) if oks else 0
    avg_d = sum(p.get("dur_s") or 0 for p in oks) / len(oks) if oks else 0
    out.append(f"LINK   {'ok' if last.get('ok') else 'DOWN (' + str(last.get('link')) + ')'} | "
               f"{avg_b / 1024:.1f} KB a poll, {avg_d:.1f} s | {tot_b / 1e6:.2f} MB in all | "
               f"down {len(downs)}x" + (": " + ", ".join(downs[-2:]) if downs else ""))
    snaps = [p for p in polls if "file" in (p.get("snapshot") or {})]
    if snaps:
        s = snaps[-1]["snapshot"]
        out.append(f"SNAP   {s['file']} {s['bytes'] / 1024:.1f} KB "
                   f"{'' if s.get('w') is None else str(s['w']) + ' px q' + str(s.get('q'))} "
                   f"({_ago(now - snaps[-1]['t'])} ago) | {len(snaps)} snapshots")
    else:
        why = (last.get("snapshot") or {}).get("skipped", "none yet")
        out.append(f"SNAP   none ({why})")
    return "\n".join(x[:width] for x in out[:24])


def load_records(path: Path, tail_bytes: int = 32 * 1024 * 1024) -> List[Dict[str, Any]]:
    """A run's log records; past ``tail_bytes`` only the head line (the start) and the tail are read."""
    out: List[Dict[str, Any]] = []
    size = path.stat().st_size
    with open(path, "rb") as f:
        first = f.readline()
        if size > tail_bytes:
            f.seek(size - tail_bytes)
            f.readline()                        # a partial line
            lines = [first] + f.read().splitlines()
        else:
            lines = [first] + f.read().splitlines()
    for line in lines:
        try:
            out.append(json.loads(line))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue                            # a line cut by a crash
    return out


def latest_run(out_dir: Path) -> Optional[Path]:
    runs = sorted(p for p in Path(out_dir).glob("*/polls.jsonl"))
    return runs[-1].parent if runs else None


def new_run_dir(out_dir: Path, now: Optional[float] = None) -> Path:
    name = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(now or time.time()))
    d, k = Path(out_dir) / name, 2
    while d.exists():
        d, k = Path(out_dir) / f"{name}-{k}", k + 1
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def notify_command(cmd: str) -> Callable[[str], None]:
    argv = shlex.split(cmd)

    def notify(text: str) -> None:
        subprocess.run(argv, input=text.encode(), timeout=30, check=False)
    return notify


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="wdwatch", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", help="ssh target (default: wdremote's config)")
    ap.add_argument("--root", help="laptop checkout (default: wdremote's config)")
    ap.add_argument("--local", action="store_true", help="the app runs on this machine (tests, dry runs)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("watch", help="poll the app every ~30 s and log it (Ctrl-C or SIGTERM to stop)")
    w.add_argument("--record-only", action="store_true",
                   help="the show: read and log only, never a command, never contact anyone")
    w.add_argument("--out", default=str(DEFAULT_OUT), help="logs go to <out>/<YYYY-MM-DD_HHMMSS>/")
    w.add_argument("--interval", type=float, default=30.0, help="seconds between polls (default 30)")
    w.add_argument("--max-interval", type=float, default=300.0, help="back-off ceiling, s (default 300)")
    w.add_argument("--slow-s", type=float, default=10.0, help="a poll longer than this is slow (default 10)")
    w.add_argument("--timeout", type=float, default=20.0, help="per request, s (default 20)")
    w.add_argument("--budget-kb", type=float, default=40.0, help="bytes a poll may cost (default 40 KB)")
    w.add_argument("--snap-width", type=int, default=320, help="snapshot width, px (default 320)")
    w.add_argument("--snap-quality", type=int, default=50, help="snapshot JPEG quality (default 50)")
    w.add_argument("--snap-every", type=int, default=1, help="a snapshot every N polls (default 1)")
    w.add_argument("--full-snapshots", action="store_true",
                   help="an older build that cannot shrink: take its full preview if it fits the budget")
    w.add_argument("--max-snap-mb", type=float, default=2000.0, help="stop saving snapshots past this")
    w.add_argument("--hours", type=float, help="stop after this long (default: until stopped)")
    w.add_argument("--event-types", default=EVENT_TYPES)
    w.add_argument("--cue-hit", type=float, default=0.75, help="cue under this hit rate (default 0.75)")
    w.add_argument("--cue-fps", type=float, default=10.0, help="cue under this fps in RUN (default 10)")
    w.add_argument("--notify", help="supervision only: a command given each cue on stdin")
    w.add_argument("--ui-shot-every", type=int, default=0, metavar="N",
                   help="S1c: every N polls also a small picture of the app's window, kept as ui_last.jpg in "
                        "the run folder (default 0 = off)")
    w.add_argument("--ui-shot-width", type=int, default=800, help="its width, px (default 800)")
    w.add_argument("--ui-shot-kb", type=float, default=80.0, help="its size cap, KB (default 80)")

    s = sub.add_parser("summary", help="the one-screen summary of a run (default: the latest)")
    s.add_argument("--out", default=str(DEFAULT_OUT))
    s.add_argument("--run", help="a run folder (default: the latest under --out)")
    s.add_argument("--minutes", type=float, default=15.0)
    return ap


def cmd_summary(a) -> int:
    run = Path(a.run) if a.run else latest_run(Path(a.out))
    if run is None or not (run / "polls.jsonl").is_file():
        print(f"wdwatch: no run under {a.out}")
        return 1
    print(render_summary(load_records(run / "polls.jsonl"), time.time(), a.minutes))
    return 0


def cmd_watch(a) -> int:
    if a.record_only and a.notify:
        raise SystemExit("wdwatch: --record-only never contacts anyone: --notify is refused")
    wd = _wdremote()
    remote = wd.load_remote(host=a.host, root=a.root, **({"os": "posix"} if a.local else {}))
    tr = wd.LocalTransport(remote) if a.local else wd.SshTransport(remote)
    run_dir = new_run_dir(Path(a.out))
    w = Watcher(tunnel_opener(tr, remote), run_dir, record_only=a.record_only, interval=a.interval,
                max_interval=a.max_interval, slow_s=a.slow_s, budget=int(a.budget_kb * 1024),
                snap_width=a.snap_width, snap_quality=a.snap_quality, snap_every=a.snap_every,
                full_snapshots=a.full_snapshots, max_snap_mb=a.max_snap_mb, event_types=a.event_types,
                cue_hit=a.cue_hit, cue_fps=a.cue_fps, timeout=a.timeout,
                notify=notify_command(a.notify) if a.notify else None, host=remote.host,
                ui_shot_every=a.ui_shot_every, ui_shot_width=a.ui_shot_width, ui_shot_kb=a.ui_shot_kb)

    def _term(*_):
        raise _Stop()
    signal.signal(signal.SIGTERM, _term)
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, lambda *_: w._say(w.summary_text()))
    w.start(argv=sys.argv[1:])
    w._say(f"[wdwatch] {'RECORD-ONLY ' if a.record_only else ''}watching {remote.host} every "
           f"{a.interval:g} s -> {run_dir} (pid {os.getpid()}; kill -USR1 for the summary)")
    w.run(hours=a.hours)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "summary":
        return cmd_summary(a)
    return cmd_watch(a)


if __name__ == "__main__":
    sys.exit(main())
