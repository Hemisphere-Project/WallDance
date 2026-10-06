"""Remote ops API — drive the running app and read it back (REMOTE_OPS).

Second client of the command/event seam (``runtime/api.py``; the docstring's
"tablet client" slot). A Claude session or Thomas on a dev box reaches it
through an **SSH port-forward** over the tailnet (``extra/wdremote.py`` opens
the tunnel). The server binds **127.0.0.1 only** and requires a bearer token
(``~/.walldance/remote_token``, generated on first start, readable over SSH).
Nothing is exposed on the venue LAN and there is no Windows Firewall prompt.

Endpoints (JSON unless noted; all but ``/ping`` need ``Authorization: Bearer``):

    GET  /api/v1/ping                       liveness + app version (no auth)
    GET  /api/v1/status                     state, project, camera, fps, tracks,
                                            engine, recorder, OSC, rig, remote policy
    GET  /api/v1/events?since=N&wait=S      long-poll the event ring buffer
    GET  /api/v1/commands                   allowlist: fields + policy class
    POST /api/v1/command  {type, args}      submit one command (policy-checked)
    GET  /api/v1/files?path=REL             list a dir under the shared roots
    GET  /api/v1/file?path=REL              download (HTTP Range; gzip for text)
    GET  /api/v1/logs?tail=N                tail of the current app log
    GET  /api/v1/snapshot.jpg               current preview (phone-monitor JPEG)
    POST /api/v1/clip {path,start,frames,scale,codec}   cut a small excerpt (job)
    GET  /api/v1/jobs/<id>                  job status

**Policy (Thomas, 2026-10-06: "full, guarded").** Each command class is one of:

- ``safe``: always allowed (readiness check, rig sheet, overlays).
- ``control``: allowed in STANDBY. In RUN it is allowed only while the operator
  has ticked *Allow remote control* in phase 6 Live. That toggle is GUI-only,
  never remotely switchable.
- ``heavy``: STANDBY only (subprocess tunes, model/engine/project loads, clips),
  because they compete with the show for the GPU/CPU or block the loop.
- ``never``: quit, delete/rename project, dialogs. Anything unlisted is denied.
"""
from __future__ import annotations

import dataclasses
import gzip
import io
import json
import os
import secrets
import threading
import time
import typing
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from runtime import api

SAFE, CONTROL, HEAVY, NEVER = "safe", "control", "heavy", "never"

POLICY: Dict[str, str] = {
    # safe: observation / metadata only
    "CheckReadiness": SAFE, "SetRigSheet": SAFE, "ToggleOverlay": SAFE,
    "SetPreviewScale": SAFE, "TogglePreviewCap": SAFE,
    # control: changes what the show does -- free in STANDBY, gated in RUN
    "SetState": CONTROL, "StartRecordingSlot": CONTROL, "StopRecording": CONTROL,
    "PlaybackControl": CONTROL, "SelectSlot": CONTROL, "PlaySlotRecording": CONTROL,
    "SetSensitivity": CONTROL, "SetConfidence": CONTROL,
    "SetMotionSensitivity": CONTROL, "SetGapBridging": CONTROL,
    "SetOutputSmoothing": CONTROL, "ToggleBoxClamp": CONTROL,
    "SetIdentitySlots": CONTROL, "SetMaxDancers": CONTROL, "SetStability": CONTROL,
    "SetCoastSeconds": CONTROL, "ToggleIrBelt": CONTROL, "ToggleOscState": CONTROL,
    "SetStaticGhostGuard": CONTROL, "SetStaticRelease": CONTROL, "SetSlotFilterInput": CONTROL,
    "SetIntermittentConfirm": CONTROL, "SetTrackingMode": CONTROL, "SetSmartHold": CONTROL,
    "SetPersonHeight": CONTROL, "SetTrackerMaxAge": CONTROL, "SetMog2Scale": CONTROL,
    "ResetTracker": CONTROL, "ToggleEnhance": CONTROL, "ToggleEnhanceLite": CONTROL,
    "ToggleEnhanceForce": CONTROL, "ToggleGreyscale": CONTROL,
    "SetEnhanceParam": CONTROL, "BgCapture": CONTROL, "BgClear": CONTROL,
    "ToggleBgSubtract": CONTROL, "SetBgSensitivity": CONTROL,
    "TogglePreview": CONTROL, "ToggleInputFpsCap": CONTROL, "SetRoi": CONTROL,
    "ResetRoi": CONTROL, "ClearMask": CONTROL, "SetRoiRect": CONTROL, "ExcludeAt": CONTROL, "ToggleOsc": CONTROL,
    "SetOscTarget": CONTROL, "SetIdsParam": CONTROL, "RefreshCameras": CONTROL,
    "SelectSource": CONTROL, "StartCalibration": CONTROL, "StartDancersRun": CONTROL,
    "ApplyCalib2": CONTROL, "ClearCalib2Pool": CONTROL, "ApplyCalibSweep": CONTROL,
    "ApplyKnownNTune": CONTROL, "SaveConfig": CONTROL, "SwitchProfile": CONTROL,
    "LoadSafeDefaults": CONTROL, "SelectConfigVersion": CONTROL,
    "SetInputTransform": CONTROL,
    # heavy: STANDBY only
    "RunDryRunReplay": HEAVY, "RunCalibSweep": HEAVY, "RunKnownNTune": HEAVY,
    "RebuildTrt": HEAVY, "LoadModel": HEAVY, "SetImgsz": HEAVY, "ToggleTrt": HEAVY,
    "LaunchProject": HEAVY, "SelectProject": HEAVY, "LoadConfig": HEAVY,
    "ImportVideoToSlot": HEAVY,   # multi-GB disk copy / transcode
    # never: destructive, or a GUI dialog nobody is there to answer
    "Quit": NEVER, "DeleteProject": NEVER, "RenameProject": NEVER,
    "StartBlankProject": NEVER, "SaveSafeDefaults": NEVER, "SaveConfigAs": NEVER,
    "RequestLoadConfigDialog": NEVER, "RequestIssueReport": NEVER,
    "SubmitIssue": NEVER, "IssueDialogClosed": NEVER, "ShowQr": NEVER,
    "ViewCalib2Pool": NEVER, "ViewAimCalibState": NEVER, "EditMask": NEVER,
}

# High-rate / non-JSON events stay out of the ring buffer; the latest value of
# the periodic ones is folded into /status instead.
_SKIP_EVENTS = {"PreviewFrame", "PreviewResize"}
_LATEST_ONLY = {"StatsTick", "GpuStats", "OutputLatency", "ModelLoadProgress"}

TEXT_SUFFIXES = (".json", ".jsonl", ".log", ".txt", ".csv", ".meta", ".md")
VIDEO_SUFFIXES = (".avi", ".mp4", ".mov", ".mkv")


def load_or_create_token(path: Path) -> str:
    """Read the bearer token, creating it (0600) on first use."""
    path = Path(path).expanduser()
    if path.is_file():
        token = path.read_text().strip()
        if len(token) >= 16:
            return token
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def check_policy(type_name: str, args: Dict[str, Any], state: str,
                 control_enabled: bool, allow_quit: bool = False) -> Optional[str]:
    """None when allowed, else the refusal reason.

    ``allow_quit``: the DEV slot is launched with WD_REMOTE_ALLOW_QUIT=1 so
    ``wdremote slot stop`` can quit it gracefully -- STANDBY only. The field
    (LIVE) app never allows a remote Quit."""
    if type_name == "Quit" and allow_quit:
        return None if state != "run" else "Quit is refused while in RUN"
    cls = POLICY.get(type_name)
    if cls is None:
        return f"{type_name} is not on the remote allowlist"
    if cls == NEVER:
        return f"{type_name} is never allowed remotely"
    if type_name == "SelectSlot" and args.get("history"):
        return "SelectSlot(history=True) opens a GUI menu; use PlaySlotRecording"
    if cls == HEAVY and state == "run":
        return f"{type_name} is STANDBY-only (it competes with the show or blocks the loop)"
    if cls == CONTROL and state == "run" and not control_enabled:
        return (f"{type_name} refused while in RUN: the operator has not enabled "
                "'Allow remote control' (phase 6 Live)")
    return None


def command_schema() -> Dict[str, Dict]:
    out = {}
    for name, cls in sorted(POLICY.items()):
        if cls == NEVER:
            continue
        ctype = getattr(api, name, None)
        if ctype is None:
            continue
        hints = typing.get_type_hints(ctype)
        out[name] = {
            "policy": cls,
            "fields": {f.name: {"type": str(hints.get(f.name, f.type)).replace("typing.", ""),
                                "required": f.default is dataclasses.MISSING
                                and f.default_factory is dataclasses.MISSING}
                       for f in dataclasses.fields(ctype)},
            "doc": (ctype.__doc__ or "").strip().split("\n")[0],
        }
    return out


class RemoteApi:
    """Thread-safe facade the HTTP handler talks to.

    The main loop owns the truth: it calls ``publish_status`` (~2 Hz) with a
    plain dict and drains submitted commands at its usual single point. HTTP
    threads only read snapshots and enqueue commands.
    """

    def __init__(self, runtime: api.RuntimeAPI, bus: api.EventBus, *,
                 token: str, roots: Dict[str, Path],
                 state_fn: Callable[[], str],
                 log_path_fn: Callable[[], Optional[Path]] = lambda: None,
                 snapshot_fn: Callable[[], Optional[bytes]] = lambda: None,
                 host: str = "127.0.0.1", port: int = 8765,
                 buffer: int = 2000, version: Optional[Dict] = None,
                 allow_quit: bool = False):
        self.runtime = runtime
        self.token = token
        self.roots = {k: Path(v).resolve() for k, v in roots.items()}
        self.state_fn = state_fn
        self.log_path_fn = log_path_fn
        self.snapshot_fn = snapshot_fn
        self.host, self.port = host, int(port)
        self.version = version or {}
        self.control_enabled = False          # operator-only (GUI), never remote
        self.allow_quit = bool(allow_quit)    # DEV slot only (WD_REMOTE_ALLOW_QUIT=1)
        self._lock = threading.Condition()
        self._events: Deque[Tuple[int, float, Dict]] = deque(maxlen=buffer)
        self._seq = 0
        self._latest: Dict[str, Dict] = {}
        self._status: Dict[str, Any] = {}
        self._last_client: Tuple[float, str] = (0.0, "")
        self._jobs: Dict[str, Dict] = {}
        self._server: Optional[ThreadingHTTPServer] = None
        bus.subscribe(self._on_event)

    # -- runtime side -------------------------------------------------------
    def _on_event(self, event: api.Event) -> None:
        name = type(event).__name__
        if name in _SKIP_EVENTS:
            return
        try:
            payload = json.loads(json.dumps(event.to_dict(), default=str))
        except Exception:
            return
        with self._lock:
            if name in _LATEST_ONLY:
                self._latest[name] = payload
                return
            self._seq += 1
            self._events.append((self._seq, time.time(), payload))
            self._lock.notify_all()

    def publish_status(self, status: Dict[str, Any]) -> None:
        with self._lock:
            self._status = status

    def client_active(self, within_s: float = 10.0) -> Tuple[bool, str]:
        t, who = self._last_client
        return (time.time() - t) < within_s, who

    # -- handler side -------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        with self._lock:
            out = dict(self._status)
            out["latest"] = dict(self._latest)
            out["event_seq"] = self._seq
        out["remote"] = {"control_enabled": self.control_enabled,
                         "bind": f"{self.host}:{self.port}"}
        return out

    def events_since(self, since: int, wait: float, types: Optional[set]) -> Dict:
        deadline = time.time() + max(0.0, min(wait, 60.0))
        with self._lock:
            while True:
                rows = [{"seq": s, "t": round(t, 3), **e} for s, t, e in self._events
                        if s > since and (not types or e.get("type") in types)]
                oldest = self._events[0][0] if self._events else self._seq + 1
                if rows or time.time() >= deadline:
                    return {"events": rows, "next": self._seq,
                            "dropped": since + 1 < oldest and since > 0}
                self._lock.wait(timeout=max(0.05, deadline - time.time()))

    def submit(self, type_name: str, args: Dict[str, Any]) -> Tuple[int, Dict]:
        reason = check_policy(type_name, args, self.state_fn(), self.control_enabled,
                              self.allow_quit)
        if reason:
            return 403, {"error": reason}
        if type_name in ("PlaySlotRecording", "LoadConfig"):
            key = "path" if type_name == "PlaySlotRecording" else "filepath"
            resolved = self.resolve(str(args.get(key, "")), absolute_ok=True)
            if resolved is None:
                return 403, {"error": f"{key} must be inside {sorted(self.roots)}"}
            args = dict(args, **{key: str(resolved)})
        if type_name == "SetRigSheet":
            args = dict(args, echo=True)
        if type_name == "ImportVideoToSlot":
            src, err = self.resolve_import_source(str(args.get("path", "")))
            if err:
                return 400, {"error": err}
            args = dict(args, path=src)
        ctype = getattr(api, type_name)
        try:
            command = ctype(**args)
        except (TypeError, ValueError) as e:
            return 400, {"error": f"bad arguments for {type_name}: {e}"}
        self.runtime.submit(command)
        return 200, {"queued": type_name, "args": args, "event_seq": self._seq}

    def resolve(self, rel: str, absolute_ok: bool = False) -> Optional[Path]:
        """Map 'projects/x/y' (or an absolute path under a root) to a real path
        inside one of the shared roots; None when it escapes them."""
        if not rel:
            return None
        cand: Optional[Path] = None
        p = Path(rel)
        if p.is_absolute():
            if not absolute_ok:
                return None
            cand = p.resolve()
        else:
            head, _, tail = rel.replace("\\", "/").partition("/")
            root = self.roots.get(head)
            if root is None:
                return None
            cand = (root / tail).resolve() if tail else root
        for root in self.roots.values():
            if cand == root or root in cand.parents:
                return cand
        return None

    def resolve_import_source(self, path: str) -> Tuple[Optional[str], Optional[str]]:
        """(real path, None) or (None, reason) for a video to import (REQ-1).

        A file already on the show machine: an absolute path anywhere, or
        'projects/...' (any shared root). It must be an existing regular
        file with a video extension -- validated here so a bad path is a
        400 on the call, not a toast nobody sees."""
        from core.video_import import VideoImportError, validate_source
        if not path:
            return None, "path is required"
        p = Path(path).expanduser()
        if not p.is_absolute():
            under = self.resolve(path)
            if under is None:
                return None, (f"path must be absolute, or relative to a shared root "
                              f"{sorted(self.roots)}")
            p = under
        try:
            return validate_source(str(p)), None
        except VideoImportError as e:
            return None, str(e)

    def listing(self, rel: str) -> Tuple[int, Dict]:
        if not rel:
            return 200, {"roots": sorted(self.roots)}
        p = self.resolve(rel)
        if p is None or not p.exists():
            return 404, {"error": f"no such path under the shared roots: {rel}"}
        if p.is_file():
            st = p.stat()
            return 200, {"files": [{"name": p.name, "size": st.st_size, "mtime": st.st_mtime}]}
        dirs, files = [], []
        for c in sorted(p.iterdir()):
            try:
                st = c.stat()
            except OSError:
                continue
            (dirs if c.is_dir() else files).append(
                {"name": c.name, "size": st.st_size, "mtime": round(st.st_mtime, 3)})
        return 200, {"path": rel, "dirs": dirs, "files": files}

    def log_tail(self, n: int) -> Dict:
        path = self.log_path_fn()
        if not path or not Path(path).is_file():
            return {"path": None, "lines": []}
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * max(1, n)))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
        return {"path": str(path), "lines": lines}

    # -- clip jobs ----------------------------------------------------------
    def start_clip(self, args: Dict[str, Any]) -> Tuple[int, Dict]:
        if self.state_fn() == "run":
            return 403, {"error": "clip extraction is STANDBY-only"}
        src = self.resolve(str(args.get("path", "")))
        if src is None or not src.is_file() or not src.name.endswith(VIDEO_SUFFIXES):
            return 400, {"error": "path must be a recording under projects/"}
        try:
            start = max(0, int(args.get("start", 0)))
            frames = max(1, min(int(args.get("frames", 300)), 6000))
            scale = max(0.1, min(float(args.get("scale", 0.5)), 1.0))
        except (TypeError, ValueError) as e:
            return 400, {"error": f"bad clip arguments: {e}"}
        codec = str(args.get("codec", "mp4v"))
        if codec not in ("mp4v", "MJPG", "FFV1"):
            return 400, {"error": "codec must be mp4v | MJPG | FFV1"}
        job_id = f"clip-{int(time.time() * 1000)}"
        project_dir = src.parent.parent
        ext = ".mp4" if codec == "mp4v" else ".avi"
        out = project_dir / "clips" / f"{src.stem}_f{start}-{start + frames}_x{scale:g}{ext}"
        job = {"id": job_id, "state": "running", "src": str(src), "output": None,
               "progress": 0.0, "error": None}
        self._jobs[job_id] = job
        threading.Thread(target=self._run_clip, name=f"RemoteClip-{job_id}", daemon=True,
                         args=(job, src, out, start, frames, scale, codec)).start()
        return 200, job

    def _run_clip(self, job, src, out, start, frames, scale, codec) -> None:
        import cv2
        cap = writer = None
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            cap = cv2.VideoCapture(str(src))
            if not cap.isOpened():
                raise RuntimeError("cannot open source")
            fps = cap.get(cv2.CAP_PROP_FPS) or 20.0
            meta = Path(str(src) + ".meta")
            if meta.is_file():
                try:
                    fps = float(json.loads(meta.read_text()).get("actual_fps") or fps)
                except Exception:
                    pass
            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            n = 0
            while n < frames:
                ok, frame = cap.read()
                if not ok:
                    break
                if scale != 1.0:
                    frame = cv2.resize(frame, None, fx=scale, fy=scale,
                                       interpolation=cv2.INTER_AREA)
                if writer is None:
                    h, w = frame.shape[:2]
                    writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*codec), fps, (w, h))
                    if not writer.isOpened():
                        raise RuntimeError(f"cannot open writer ({codec})")
                writer.write(frame)
                n += 1
                job["progress"] = round(n / frames, 3)
            rel = None
            for name, root in self.roots.items():
                if root in out.resolve().parents:
                    rel = f"{name}/{out.resolve().relative_to(root).as_posix()}"
            job.update(state="done", output=rel, frames=n,
                       size=out.stat().st_size if out.exists() else 0)
        except Exception as e:
            job.update(state="error", error=str(e))
        finally:
            if cap is not None:
                cap.release()
            if writer is not None:
                writer.release()

    # -- server -------------------------------------------------------------
    def start(self) -> bool:
        handler = _make_handler(self)
        try:
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as e:
            print(f"[RemoteApi] could not bind {self.host}:{self.port}: {e}")
            return False
        self._server.daemon_threads = True
        self.port = int(self._server.server_address[1])   # resolves port=0 (tests)
        threading.Thread(target=self._server.serve_forever, name="RemoteApi",
                         daemon=True).start()
        print(f"[RemoteApi] listening on {self.host}:{self.port} "
              "(reach it through an SSH tunnel: extra/wdremote.py)")
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def _make_handler(remote: RemoteApi):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WallDanceRemote/1"

        def log_message(self, *args, **kwargs):     # quiet: the app log is for the show
            pass

        # -- helpers --
        def _json(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode()
            gz = "gzip" in (self.headers.get("Accept-Encoding") or "") and len(body) > 1024
            if gz:
                body = gzip.compress(body, 5)
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            if gz:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self) -> bool:
            auth = self.headers.get("Authorization") or ""
            ok = secrets.compare_digest(auth.encode(), f"Bearer {remote.token}".encode())
            if ok:
                remote._last_client = (time.time(), self.client_address[0])
            else:
                self._json(401, {"error": "missing or bad bearer token"})
            return ok

        def _query(self) -> Dict[str, str]:
            q = parse_qs(urlparse(self.path).query)
            return {k: v[-1] for k, v in q.items()}

        # -- verbs --
        def do_GET(self):
            path = urlparse(self.path).path.rstrip("/")
            if path == "/api/v1/ping":
                return self._json(200, {"ok": True, "app": "walldance",
                                        "version": remote.version})
            if not self._authed():
                return
            q = self._query()
            if path == "/api/v1/status":
                return self._json(200, remote.status())
            if path == "/api/v1/events":
                types = set(q["types"].split(",")) if q.get("types") else None
                return self._json(200, remote.events_since(
                    int(q.get("since", 0)), float(q.get("wait", 0)), types))
            if path == "/api/v1/commands":
                return self._json(200, command_schema())
            if path == "/api/v1/files":
                return self._json(*remote.listing(q.get("path", "")))
            if path == "/api/v1/file":
                return self._send_file(q.get("path", ""))
            if path == "/api/v1/logs":
                return self._json(200, remote.log_tail(max(1, min(int(q.get("tail", 200)), 20000))))
            if path == "/api/v1/snapshot.jpg":
                jpg = remote.snapshot_fn()
                if not jpg:
                    return self._json(404, {"error": "no preview (phone monitor off?)"})
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.end_headers()
                self.wfile.write(jpg)
                return
            if path.startswith("/api/v1/jobs/"):
                job = remote._jobs.get(path.rsplit("/", 1)[-1])
                return self._json(200 if job else 404, job or {"error": "no such job"})
            return self._json(404, {"error": f"unknown endpoint {path}"})

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")
            if not self._authed():
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {"error": f"bad JSON body: {e}"})
            if path == "/api/v1/command":
                return self._json(*remote.submit(str(body.get("type", "")),
                                                 dict(body.get("args") or {})))
            if path == "/api/v1/clip":
                return self._json(*remote.start_clip(body))
            return self._json(404, {"error": f"unknown endpoint {path}"})

        def _send_file(self, rel: str) -> None:
            p = remote.resolve(rel)
            if p is None or not p.is_file():
                return self._json(404, {"error": f"no such file under the shared roots: {rel}"})
            size = p.stat().st_size
            rng = self.headers.get("Range") or ""
            gz = (not rng and p.name.endswith(TEXT_SUFFIXES)
                  and "gzip" in (self.headers.get("Accept-Encoding") or ""))
            if gz:
                buf = io.BytesIO()
                with open(p, "rb") as fi, gzip.GzipFile(fileobj=buf, mode="wb",
                                                        compresslevel=6) as fo:
                    while True:
                        chunk = fi.read(1 << 20)
                        if not chunk:
                            break
                        fo.write(chunk)
                body = buf.getvalue()
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Original-Size", str(size))
                self.end_headers()
                self.wfile.write(body)
                return
            start, end = 0, size - 1
            if rng.startswith("bytes="):
                a, _, b = rng[6:].partition("-")
                try:
                    start = int(a) if a else max(0, size - int(b))
                    end = int(b) if (a and b) else size - 1
                except ValueError:
                    return self._json(416, {"error": "bad Range"})
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            length = end - start + 1
            self.send_response(206 if rng else 200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(p, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

    return Handler
