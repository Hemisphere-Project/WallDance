"""S1a -- the watch tool (``extra/wdwatch.py``) and the app's live hit rate (``services/live_watch.py``).

Everything runs against a fake of the app's remote API on loopback (no laptop, no ssh): a poll cycle, the
size budget, back-off and resume after a dropped link, record-only never sending anything, the summary
screen; and, app side, the rolling hit rate with a fake scorer, its exposure in /status, the fitted
snapshot, and the main loop feeding it.  Test names carry the gate clause they prove (``run_gate.sh``).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np
import pytest

from runtime import api
from services import live_watch
from services.live_watch import HitRate, fit_jpeg, snapshot_fitted
from services.remote_api import RemoteApi

_EXTRA = Path(__file__).resolve().parents[2] / "extra"
_SPEC = importlib.util.spec_from_file_location("wdwatch", _EXTRA / "wdwatch.py")
wdwatch = importlib.util.module_from_spec(_SPEC)
sys.modules["wdwatch"] = wdwatch
_SPEC.loader.exec_module(wdwatch)

TOKEN = "t" * 32


def _jpeg(w: int = 640, h: int = 352, seed: int = 0, quality: int = 90) -> bytes:
    """A frame-like JPEG: a lit wall (gradient), two figures, sensor noise."""
    rng = np.random.default_rng(seed)
    img = np.tile(np.linspace(40, 200, w, dtype=np.float32), (h, 1))
    img[h // 4: h - 20, w // 3: w // 3 + 40] = 240
    img[h // 3: h - 30, w // 2: w // 2 + 35] = 230
    img = np.clip(img + rng.normal(0, 12, img.shape), 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", cv2.merge([img, img, img]), [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    assert ok
    return buf.tobytes()


# ---------------------------------------------------------------------------
# A fake of the app's remote API: answers like services/remote_api.py, logs every request
# ---------------------------------------------------------------------------

class FakeApp:
    def __init__(self):
        self.requests: list = []           # (method, path with query)
        self.lock = threading.Lock()
        self.started = time.time() - 600
        self.commit = "abc1234"
        self.state = "run"
        self.fps = 19.8
        self.camera_open = True
        self.hit = {"rate": 0.9, "groups": 50, "covered": 45, "frames": 60, "skipped": 0, "window_s": 60.0,
                    "age_s": 0.4, "total": {"frames": 600, "groups": 500, "covered": 450, "skipped": 0}}
        self.fit = True                    # this build shrinks snapshots
        self.jpeg = _jpeg()
        self.events: list = []
        self.seq = 0
        self.delay = 0.0                   # seconds before each answer (a slow link)
        self.down = False                  # the app does not answer (the tunnel closes the connection)
        self.link_up = True                # the laptop answers ssh at all
        self.token = TOKEN
        self.snapshot_code = None          # force an answer code for /snapshot.jpg
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def emit(self, type_, **fields):
        with self.lock:
            self.seq += 1
            self.events.append({"seq": self.seq, "t": round(time.time(), 3), "type": type_, **fields})

    def restart(self):
        with self.lock:
            self.started = time.time()
            self.seq, self.events = 0, []

    def status(self):
        now = time.time()
        st = {"t": round(now, 3), "uptime_s": round(now - self.started, 1),
              "app": {"commit": self.commit, "branch": "cordee-r2"}, "state": self.state,
              "project": "facade-cordee", "profile": "default", "fps": self.fps, "tracks": [1, 2],
              "camera": {"source": "ids", "open": self.camera_open, "type": "ids", "size": [1920, 1200]},
              "engine": {"model": "yolo11l-pose", "imgsz": 1280, "trt_requested": True, "trt_active": True},
              "recorder": {"state": "idle"}, "osc": {"enabled": True, "target": "10.0.0.5:7000"},
              "latest": {"StatsTick": {"type": "StatsTick", "payload": {
                  "fps": self.fps, "num_dancers": 2, "latency_ms": 48.2, "slots_text": "S1 S2",
                  "camera_running": self.camera_open, "camera_reconnecting": False,
                  "gpu_fallback_reason": "", "timing": {"yolo": 18.1}}}},
              "event_seq": self.seq, "remote": {"control_enabled": False, "bind": "127.0.0.1:8765"}}
        if self.hit is not None:
            st["hit_rate"] = self.hit
        if self.fit:
            st["snapshot_fit"] = True
        return st

    def _handler(self):
        app = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def _send(self, code, body: bytes, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _common(self, method):
                with app.lock:
                    app.requests.append((method, self.path))
                if app.delay:
                    time.sleep(app.delay)
                if app.down:                       # like ssh -L when the app's port refuses: no answer
                    self.close_connection = True
                    return False
                if self.headers.get("Authorization") != f"Bearer {app.token}":
                    self._send(401, b'{"error": "missing or bad bearer token"}')
                    return False
                return True

            def do_GET(self):
                if not self._common("GET"):
                    return
                u = urlparse(self.path)
                q = {k: v[-1] for k, v in parse_qs(u.query).items()}
                if u.path == "/api/v1/status":
                    return self._send(200, json.dumps(app.status()).encode())
                if u.path == "/api/v1/events":
                    since = int(q.get("since", 0))
                    types_ = set(q["types"].split(",")) if q.get("types") else None
                    with app.lock:
                        rows = [e for e in app.events if e["seq"] > since
                                and (not types_ or e["type"] in types_)]
                        nxt = app.seq
                    return self._send(200, json.dumps({"events": rows, "next": nxt,
                                                       "dropped": False}).encode())
                if u.path == "/api/v1/snapshot.jpg":
                    if app.snapshot_code:
                        return self._send(app.snapshot_code, b'{"error": "forced"}')
                    if app.fit:
                        code, body = snapshot_fitted(app.jpeg, q)    # the app's own fitting code
                    else:
                        code, body = 200, app.jpeg                   # an older build: the preview as it is
                    if code != 200:
                        return self._send(code, json.dumps(body).encode())
                    return self._send(200, body, "image/jpeg")
                return self._send(404, b'{"error": "unknown"}')

            def do_POST(self):
                if self._common("POST"):
                    self._send(200, b'{"queued": "?"}')

            do_PUT = do_DELETE = do_PATCH = do_POST

        return H

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeSession:
    def __init__(self, app: FakeApp, token: str = TOKEN):
        self.app, self.base, self.token, self.closed = app, app.base, token, False

    def alive(self):
        return not self.closed and self.app.link_up

    def close(self):
        self.closed = True


@pytest.fixture
def fake():
    app = FakeApp()
    yield app
    app.close()


def _opener(app: FakeApp, calls: list):
    cache = {"token": app.token}                      # wdremote's token cache on the dev box

    def open_session(refresh_token=False):
        calls.append(refresh_token)
        if not app.link_up:
            raise wdwatch.ApiError("tunnel: ssh: connect to host 100.106.250.59 port 22: timed out")
        if refresh_token:
            cache["token"] = app.token                # read again from the laptop
        return FakeSession(app, cache["token"])
    return open_session


def _watcher(app, tmp_path, calls=None, run=None, **kw):
    kw.setdefault("out", _Sink())
    return wdwatch.Watcher(_opener(app, calls if calls is not None else []), run or tmp_path / "run", **kw)


class _Sink:
    def __init__(self):
        self.lines = []

    def write(self, s):
        self.lines.append(s)

    def flush(self):
        pass

    @property
    def text(self):
        return "".join(self.lines)


def _log(w):
    return [json.loads(x) for x in (w.run_dir / "polls.jsonl").read_text().splitlines()]


# ---------------------------------------------------------------------------
# Clause: a poll cycle
# ---------------------------------------------------------------------------

def test_poll_cycle_reads_status_health_events_snapshot_and_hit_rate(fake, tmp_path):
    fake.emit("Alert", kind="fps_low", message="fps 12 < 15", data={})
    fake.emit("PreviewResize")                                  # not asked for: filtered by the app
    w = _watcher(fake, tmp_path)
    w.start()
    rec = w.poll_once()
    assert rec["ok"] and rec["link"] == "ok"
    assert rec["status"]["project"] == "facade-cordee"
    assert rec["hit_rate"]["rate"] == 0.9 and rec["hit_rate"]["groups"] == 50
    assert rec["health"]["fps"] == 19.8 and rec["health"]["latency_ms"] == 48.2
    assert rec["health"]["dancers"] == 2 and rec["health"]["camera_running"] is True
    assert [e["type"] for e in rec["events"]] == ["Alert"]
    snap = rec["snapshot"]
    jpg = (w.run_dir / snap["file"]).read_bytes()
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] <= 320 and len(jpg) == snap["bytes"] < len(fake.jpeg)
    paths = [urlparse(p).path for _, p in fake.requests]
    assert paths == ["/api/v1/status", "/api/v1/events", "/api/v1/snapshot.jpg"]
    log = _log(w)
    assert [r["kind"] for r in log] == ["start", "poll", "cue"]           # the app's alert is a cue
    assert (w.run_dir / "summary.txt").read_text().startswith("WallDance watch")
    # the next poll reads only the new events
    fake.emit("Toast", message="Calibrate done", duration=3.0, color=[1, 2, 3])
    rec2 = w.poll_once()
    assert [e["type"] for e in rec2["events"]] == ["Toast"]


def test_poll_cycle_loop_paces_and_stops(fake, tmp_path):
    waits = []
    w = _watcher(fake, tmp_path, interval=30.0)
    w.start()
    w.run(max_polls=3, sleep=waits.append)
    assert len(waits) == 2 and all(28.0 < s <= 30.0 for s in waits)
    log = _log(w)
    assert [r["kind"] for r in log if r["kind"] != "cue"] == ["start", "poll", "poll", "poll", "stop"]
    assert log[-1]["why"] == "max polls"


def test_poll_cycle_notices_an_app_restart_and_reads_its_events_from_its_start(fake, tmp_path):
    w = _watcher(fake, tmp_path)
    for _ in range(3):
        fake.emit("Toast", message="old", duration=1.0, color=[0, 0, 0])
    w.poll_once()
    fake.restart()
    fake.commit = "def5678"
    fake.emit("Toast", message="started", duration=1.0, color=[0, 0, 0])
    rec = w.poll_once()
    assert rec.get("restart") is True
    assert [e["message"] for e in rec["events"]] == ["started"]       # since 0 of the new app
    kinds = [r["kind"] for r in _log(w)]
    assert "restart" in kinds
    assert w.restarts == 1


def test_poll_cycle_through_the_real_remote_api(tmp_path):
    """End to end: the watcher against services/remote_api.py itself (fake scorer, fake preview)."""
    runtime, bus = api.RuntimeAPI(), api.EventBus()
    preview = _jpeg(1920, 1056, quality=70)
    remote = RemoteApi(runtime, bus, token=TOKEN, roots={"projects": tmp_path}, state_fn=lambda: "run",
                       snapshot_fn=lambda: preview, host="127.0.0.1", port=0, version={"commit": "x"})
    remote.hit_rate = HitRate(score=lambda fg, boxes: (fg["g"], fg["c"]))
    for g, c in ((4, 3), (2, 2), (5, 4)):
        remote.hit_rate.feed({"g": g, "c": c}, [])
    timing = {f"stage_{i}": i * 0.1 for i in range(200)}       # > 1 KB: the app gzips it
    remote.publish_status({"t": time.time(), "uptime_s": 12.0, "state": "run", "project": "p",
                           "timing": timing})
    bus.publish(api.Alert("camera_stall", "the camera stalled 2 s", {}))
    assert remote.start()
    try:
        app = SimpleNamespace(base=f"http://127.0.0.1:{remote.port}", token=TOKEN, link_up=True)
        w = wdwatch.Watcher(lambda refresh_token=False: FakeSession(app), tmp_path / "run", out=_Sink(),
                            budget=24 * 1024)
        rec = w.poll_once()
    finally:
        remote.stop()
    assert rec["ok"], rec.get("error")
    assert rec["status"]["timing"] == timing                    # gzip on the wire, decoded here
    assert rec["hit_rate"]["rate"] == round(9 / 11, 4) and rec["hit_rate"]["frames"] == 3
    assert [e["type"] for e in rec["events"]] == ["Alert"]
    assert rec["bytes"] <= 24 * 1024
    jpg = (w.run_dir / rec["snapshot"]["file"]).read_bytes()
    assert len(jpg) < len(preview) // 10
    assert cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR).shape[1] <= 320


def test_poll_cycle_through_wdremote_api_session_local(fake, tmp_path, monkeypatch):
    """The production session (wdremote.ApiSession, its token cache) in --local mode, no ssh."""
    wd = wdwatch._wdremote()
    monkeypatch.setattr(wd, "DEFAULT_CONFIG", tmp_path / "cfg" / "remote.json")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "remote_token.local").write_text(TOKEN + "\n")
    remote = wd.Remote(host="local", root=str(tmp_path), os="posix", api_port=fake.port)
    opener = wdwatch.tunnel_opener(wd.LocalTransport(remote), remote)
    w = wdwatch.Watcher(opener, tmp_path / "run", out=_Sink())
    rec = w.poll_once()
    assert rec["ok"], rec.get("error")
    assert rec["status"]["project"] == "facade-cordee"
    w.close()


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGUSR1"),
                    reason="the watch tool runs on the dev machine (POSIX): Windows has no SIGUSR1")
def test_poll_cycle_cli_process_usr1_summary_and_clean_stop(fake, tmp_path):
    """The real command, as Thomas starts it: polls, prints the screen on SIGUSR1, stops on SIGTERM."""
    import os
    import signal
    import subprocess
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "remote.json").write_text(json.dumps({"host": "local", "root": str(tmp_path), "os": "posix",
                                                 "api_port": fake.port}))
    (cfg / "remote_token.local").write_text(TOKEN + "\n")
    out = tmp_path / "watch"
    proc = subprocess.Popen([sys.executable, str(_EXTRA / "wdwatch.py"), "--local", "watch", "--record-only",
                             "--interval", "1", "--out", str(out)],
                            env=dict(os.environ, WD_REMOTE_CONFIG=str(cfg / "remote.json")),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            run = wdwatch.latest_run(out)
            if run and sum(1 for x in (run / "polls.jsonl").read_text().splitlines() if '"poll"' in x) >= 2:
                break
            time.sleep(0.2)
        proc.send_signal(signal.SIGUSR1)
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        text, _ = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, text
    assert "WallDance watch -- run" in text and "(RECORD-ONLY)" in text
    log = [json.loads(x) for x in (run / "polls.jsonl").read_text().splitlines()]
    assert log[0]["kind"] == "start" and log[-1] == dict(log[-1], kind="stop", why="interrupted")
    assert sum(r["kind"] == "poll" and r["ok"] for r in log) >= 2
    assert {m for m, _ in fake.requests} == {"GET"}


# ---------------------------------------------------------------------------
# Clause: the size budget
# ---------------------------------------------------------------------------

def test_budget_every_poll_stays_within_it_and_the_app_fits_the_snapshot(fake, tmp_path):
    budget = 16 * 1024
    w = _watcher(fake, tmp_path, budget=budget)
    for i in range(4):
        fake.emit("Toast", message=f"t{i}", duration=1.0, color=[0, 0, 0])
        rec = w.poll_once()
        assert rec["ok"] and rec["bytes"] <= budget
        assert "file" in rec["snapshot"]
    snaps = [p for m, p in fake.requests if urlparse(p).path == "/api/v1/snapshot.jpg"]
    for p in snaps:
        q = parse_qs(urlparse(p).query)
        assert int(q["max_bytes"][0]) <= budget and int(q["w"][0]) == 320 and int(q["q"][0]) == 50


def test_budget_too_tight_for_a_snapshot_skips_it(fake, tmp_path):
    w = _watcher(fake, tmp_path, budget=3 * 1024)
    rec = w.poll_once()
    assert rec["ok"] and rec["snapshot"]["skipped"].startswith("budget")
    assert not any(urlparse(p).path == "/api/v1/snapshot.jpg" for _, p in fake.requests)


def test_budget_an_older_build_gets_no_snapshot_unless_asked_and_never_over_budget(fake, tmp_path):
    fake.fit = False
    fake.jpeg = _jpeg(1280, 704, quality=95)          # a full-size preview, far over the budget
    w = _watcher(fake, tmp_path / "a", budget=40 * 1024)
    rec = w.poll_once()
    assert "cannot shrink" in rec["snapshot"]["skipped"]
    assert not any(urlparse(p).path == "/api/v1/snapshot.jpg" for _, p in fake.requests)
    w2 = _watcher(fake, tmp_path / "b", budget=40 * 1024, full_snapshots=True)
    rec = w2.poll_once()
    assert rec["ok"] and "over budget" in rec["snapshot"]["skipped"] and "not read" in rec["snapshot"]["skipped"]
    assert rec["bytes"] <= 40 * 1024
    assert not list((w2.run_dir / "snapshots").iterdir())


def test_budget_reader_refuses_an_answer_larger_than_its_room(fake):
    fake.fit = False
    r = wdwatch.ApiReader(fake.base, TOKEN)
    with pytest.raises(wdwatch.OverBudget) as e:
        r.get("/api/v1/snapshot.jpg", 1000)
    assert e.value.size == len(fake.jpeg)
    code, body, wire = r.get("/api/v1/snapshot.jpg", len(fake.jpeg))
    assert code == 200 and wire == len(body) == len(fake.jpeg)


def test_budget_shrinks_after_a_refusal_and_on_a_slow_link(fake, tmp_path):
    w = _watcher(fake, tmp_path)
    fake.snapshot_code = 413                          # the app could not fit it
    w.poll_once()
    assert w.shrink == 1
    fake.snapshot_code = None
    w.poll_once()
    last = [p for _, p in fake.requests if urlparse(p).path == "/api/v1/snapshot.jpg"][-1]
    q = parse_qs(urlparse(last).query)
    assert int(q["w"][0]) == 240 and int(q["q"][0]) == 45


def test_budget_app_side_fit_jpeg_and_its_parameters():
    big = _jpeg(1920, 1056, quality=70)
    out = fit_jpeg(big, 320, 50, 8000)
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    assert len(out) <= 8000 and img.shape[1] == 320 and img.shape[0] == 176
    tight = fit_jpeg(big, 320, 50, 2500)              # steps quality, then width, down to fit
    assert tight is not None and len(tight) <= 2500
    assert fit_jpeg(big, 320, 50, 200) is None
    assert snapshot_fitted(big, {}) == (200, big)     # no parameter: served as it is (wdremote snapshot)
    assert snapshot_fitted(big, {"w": "x"})[0] == 400
    assert snapshot_fitted(big, {"w": "160", "max_bytes": "200"})[0] == 413
    code, small = snapshot_fitted(big, {"w": "160", "q": "40"})
    assert code == 200 and cv2.imdecode(np.frombuffer(small, np.uint8), cv2.IMREAD_COLOR).shape[1] == 160
    assert snapshot_fitted(b"not a jpeg", {"w": "160"})[0] == 413


# ---------------------------------------------------------------------------
# Clause: back-off and resume after a dropped link
# ---------------------------------------------------------------------------

def test_backoff_on_a_dropped_link_then_resume(fake, tmp_path):
    calls = []
    w = _watcher(fake, tmp_path, calls, interval=30.0, max_interval=300.0)
    assert w.poll_once()["ok"] and w.pacer.interval == 30.0
    fake.link_up = False                              # 4G gone, or the laptop asleep
    seen = []
    for _ in range(6):
        rec = w.poll_once()
        assert not rec["ok"] and rec["link"] == "down"
        seen.append(rec["interval_s"])
    assert seen == [60.0, 120.0, 240.0, 300.0, 300.0, 300.0]
    fake.link_up = True
    rec = w.poll_once()
    assert rec["ok"] and rec["interval_s"] == 30.0
    links = [r for r in _log(w) if r["kind"] == "link"]
    assert [r["state"] for r in links] == ["down", "up"]
    assert links[1]["failed_polls"] == 6
    assert calls == [False] * 8                       # a fresh tunnel tried at each poll, then kept


def test_backoff_app_down_keeps_the_tunnel_then_resumes_after_a_restart(fake, tmp_path):
    calls = []
    w = _watcher(fake, tmp_path, calls)
    w.poll_once()
    fake.down = True                                  # the app quits, the tunnel stays
    r1, r2 = w.poll_once(), w.poll_once()
    assert r1["link"] == r2["link"] == "app_down" and r2["interval_s"] == 120.0
    assert len(calls) == 1                            # same tunnel
    fake.down = False
    fake.restart()
    rec = w.poll_once()
    assert rec["ok"] and rec.get("restart") is True
    cues = [r for r in _log(w) if r["kind"] == "cue"]
    assert [c["name"] for c in cues] == ["app", "app", "restart"]
    assert [c["on"] for c in cues] == [True, False, True]


def test_backoff_on_a_slow_link_and_back(fake, tmp_path):
    w = _watcher(fake, tmp_path, slow_s=0.3)
    fake.delay = 0.15                                 # three requests: ~0.45 s a poll
    w.poll_once()
    assert w.pacer.slow == 1 and w.pacer.interval == 60.0
    assert w._snap_size() == (240, 45)
    fake.delay = 0.0
    for _ in range(3):
        rec = w.poll_once()
    assert w.pacer.slow == 0 and rec["interval_s"] == 30.0


def test_backoff_a_timeout_drops_the_tunnel_and_a_bad_token_is_read_again(fake, tmp_path):
    calls = []
    w = _watcher(fake, tmp_path, calls, timeout=0.3)
    fake.delay = 0.6
    rec = w.poll_once()
    assert not rec["ok"] and rec["link"] == "timeout"
    fake.delay = 0.0
    fake.token = "u" * 32                             # the laptop's token changed (the cache is stale)
    rec = w.poll_once()
    assert rec["link"] == "auth"
    assert w.poll_once()["ok"]
    assert calls == [False, False, True]              # re-opened after the timeout; token re-read after 401


def test_backoff_survives_any_error_for_hours(fake, tmp_path, monkeypatch):
    w = _watcher(fake, tmp_path)
    monkeypatch.setattr(wdwatch, "health_of", lambda st: 1 / 0)
    rec = w.poll_once()
    assert not rec["ok"] and "ZeroDivisionError" in rec["error"]
    monkeypatch.undo()
    assert w.poll_once()["ok"]


# ---------------------------------------------------------------------------
# Clause: record-only never issues a POST or a command (and contacts no one)
# ---------------------------------------------------------------------------

def _scenario(w, fake):
    """Polls through everything a show night may bring: alerts, a low hit rate, low fps, the camera
    closing, the app restarting, the link dropping, a slow link."""
    fake.emit("Alert", kind="gpu_temp", message="GPU 86 C", data={})
    w.poll_once()
    fake.hit = dict(fake.hit, rate=0.4, covered=20)
    fake.fps = 4.0
    fake.camera_open = False
    w.poll_once()
    w.poll_once()
    fake.down = True
    w.poll_once()
    w.poll_once()
    fake.down = False
    fake.restart()
    w.poll_once()
    fake.link_up = False
    w.poll_once()
    w.poll_once()
    fake.link_up = True
    fake.delay = 0.05
    w.poll_once()


def test_record_only_never_posts_never_commands_never_contacts(fake, tmp_path):
    notified = []
    sink = _Sink()
    w = _watcher(fake, tmp_path, record_only=True, out=sink)
    w.start()
    _scenario(w, fake)
    assert fake.requests, "the fake saw the polls"
    methods = {m for m, _ in fake.requests}
    paths = {urlparse(p).path for _, p in fake.requests}
    assert methods == {"GET"}
    assert paths <= set(wdwatch.READ_PATHS)
    assert "/api/v1/command" not in paths and "/api/v1/clip" not in paths
    cues = [r for r in _log(w) if r["kind"] == "cue"]
    assert {c["name"] for c in cues} >= {"alert:gpu_temp", "hit", "fps", "camera", "app", "restart", "link"}
    assert "!!" not in sink.text                      # the cues stay in the log
    assert notified == []
    with pytest.raises(ValueError):
        _watcher(fake, tmp_path / "x", record_only=True, notify=notified.append)


def test_record_only_supervision_counterpart_does_cue_and_notify(fake, tmp_path):
    """The same night in supervision mode: cues printed and handed to the hook -- what record-only
    suppresses -- and still not one POST."""
    notified = []
    sink = _Sink()
    w = _watcher(fake, tmp_path, out=sink, notify=notified.append)
    _scenario(w, fake)
    assert {m for m, _ in fake.requests} == {"GET"}
    assert any("GPU 86 C" in n for n in notified) and any("hit rate 40 %" in n for n in notified)
    assert "!!" in sink.text


def test_record_only_reader_cannot_send(fake):
    r = wdwatch.ApiReader(fake.base, TOKEN)
    assert not any(hasattr(r, m) for m in ("post", "put", "delete", "submit", "command", "send"))
    for path in ("/api/v1/command", "/api/v1/clip", "/api/v1/files?path=projects", "/api/v1/logs"):
        with pytest.raises(wdwatch.ReadOnlyViolation):
            r.get(path, 10_000)
    with pytest.raises(wdwatch.ReadOnlyViolation):
        r._request("POST", "/api/v1/status", 10_000)
    assert fake.requests == []                        # refused before anything went on the wire


def test_record_only_the_source_has_no_way_to_send():
    tree = ast.parse((_EXTRA / "wdwatch.py").read_text())
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not consts & {"POST", "PUT", "DELETE", "PATCH", "/api/v1/command", "/api/v1/clip"}
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not names & {"urlopen", "request_post", "_submit", "submit"}


def test_record_only_cli_refuses_a_notify_hook(capsys):
    with pytest.raises(SystemExit) as e:
        wdwatch.main(["watch", "--record-only", "--notify", "echo hi"])
    assert "never contacts anyone" in str(e.value)


# ---------------------------------------------------------------------------
# Clause: the summary screen
# ---------------------------------------------------------------------------

def test_summary_screen_on_demand(fake, tmp_path, capsys):
    w = _watcher(fake, tmp_path, run=tmp_path / "out" / "2026-12-12_183000", host="tango@100.106.250.59",
                 record_only=True)
    w.start()
    fake.emit("Alert", kind="fps_low", message="fps 12 < 15", data={})
    w.poll_once()
    fake.link_up = False
    w.poll_once()
    fake.link_up = True
    fake.hit = dict(fake.hit, rate=0.8, groups=60, covered=48,
                    total={"frames": 660, "groups": 560, "covered": 498, "skipped": 0})
    w.poll_once()
    assert wdwatch.main(["summary", "--out", str(tmp_path / "out")]) == 0
    screen = capsys.readouterr().out.rstrip("\n")
    lines = screen.splitlines()
    assert 8 <= len(lines) <= 24 and all(len(x) <= 100 for x in lines)
    assert screen.isascii()
    assert "RECORD-ONLY" in lines[0] and "tango@100.106.250.59" in lines[0]
    assert "polls 2 ok, 1 failed" in screen
    assert "RUN | facade-cordee" in screen
    assert "now 80 % (48/60 groups" in screen
    assert "watched 80 % of 60 groups" in screen       # (498-450)/(560-500) since the watch began
    assert "9!8" in screen                             # one figure a poll, ! for no answer
    assert "fps 12 < 15" in screen
    assert "down 1x" in screen
    assert "SNAP   snapshots/" in screen
    assert (w.run_dir / "summary.txt").read_text().splitlines()[0] == lines[0]
    assert w.summary_text().splitlines()[0] == lines[0]


def test_summary_without_a_referee_or_a_poll(fake, tmp_path):
    assert "no poll yet" in wdwatch.render_summary([{"kind": "start", "run": "r"}], time.time())
    fake.hit = None
    w = _watcher(fake, tmp_path)
    w.poll_once()
    assert "HIT    absent" in w.summary_text()


def test_summary_reads_a_log_cut_by_a_crash(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    (run / "polls.jsonl").write_text('{"kind":"start","run":"r"}\n{"kind":"poll","t":1,"ok":false,'
                                     '"link":"down","interval_s":60}\n{"kind":"poll","t":')
    recs = wdwatch.load_records(run / "polls.jsonl")
    assert [r["kind"] for r in recs] == ["start", "poll"]
    assert "DOWN" in wdwatch.render_summary(recs, 100.0)


# ---------------------------------------------------------------------------
# Clause: the app-side rolling hit rate, with a fake scorer
# ---------------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _fg(g, c):
    return SimpleNamespace(g=g, c=c)


def _fake_score(fg, boxes):
    return None if fg.g is None else (fg.g, fg.c)


def test_hit_rate_rolls_over_its_window(tmp_path):
    clk = _Clock()
    h = HitRate(window_s=60.0, score=_fake_score, clock=clk)
    assert h.snapshot()["rate"] is None and h.snapshot()["frames"] == 0
    for g, c in ((4, 4), (4, 2), (2, 2)):
        h.feed(_fg(g, c), [])
        clk.t += 10
    s = h.snapshot()
    assert s["rate"] == 0.8 and (s["groups"], s["covered"], s["frames"]) == (10, 8, 3)
    clk.t += 35                                       # the first frame leaves the window
    s = h.snapshot()
    assert (s["groups"], s["covered"], s["rate"]) == (6, 4, round(4 / 6, 4))
    assert s["total"] == {"frames": 3, "groups": 10, "covered": 8, "skipped": 0}
    assert s["age_s"] == 45.0


def test_hit_rate_skips_frames_without_foreground_and_counts_a_frame_once():
    h = HitRate(score=_fake_score, clock=_Clock())
    fg = _fg(3, 1)
    h.feed(fg, [])
    h.feed(fg, [])                                    # no new frame since the last sample
    h.feed(_fg(None, None), [])                       # foreground off / invalid: skipped
    h.feed(None, [])                                  # no foreground at all: nothing
    s = h.snapshot()
    assert (s["frames"], s["skipped"], s["groups"], s["covered"]) == (1, 1, 3, 1)
    h2 = HitRate(score=_fake_score, clock=_Clock())
    h2.feed(_fg(0, 0), [])                            # an empty wall: no dancer group, no figure
    assert h2.snapshot()["rate"] is None and h2.snapshot()["frames"] == 1


def test_hit_rate_feeds_from_the_processor_boxes():
    seen = []
    h = HitRate(score=lambda fg, boxes: seen.append(boxes) or (1, 1), clock=_Clock())
    box = (10.0, 20.0, 60.0, 180.0, 0.8)
    h.feed_from(SimpleNamespace(last_fg=_fg(1, 1), last_raw_boxes=[box]))
    h.feed_from(SimpleNamespace(last_fg=_fg(1, 1)))   # before the first network pass: no boxes yet
    h.feed_from(None)
    assert seen == [[box], []]


def test_hit_rate_absent_without_the_referee_never_zero(monkeypatch):
    monkeypatch.setitem(sys.modules, "core.swipe", None)          # this build has no core/swipe.py
    assert live_watch.swipe_scorer() is None
    h = HitRate()
    h.feed(_fg(3, 3), [])
    assert h.snapshot() is None and not h.available


def test_hit_rate_a_failing_referee_turns_the_figure_off_quietly(capsys):
    h = HitRate(score=lambda fg, boxes: 1 / 0, clock=_Clock())
    h.feed(_fg(1, 1), [])                             # never raises into the main loop
    h.feed(_fg(1, 1), [])
    assert h.snapshot() is None
    assert capsys.readouterr().out.count("[HitRate] referee failed") == 1


def test_hit_rate_seam_calls_the_swipe_referee(monkeypatch):
    """One referee: score_frame(Observation.from_fg(fg), boxes, SwipeParams()), covered / groups."""
    calls = []

    class Params:
        pass

    class Observation:
        @classmethod
        def from_fg(cls, fg):
            return None if not fg.valid else ("obs", fg)

    def score_frame(obs, boxes, params):
        calls.append((obs, list(boxes), type(params)))
        return SimpleNamespace(groups=4, covered=3, boxes=len(boxes), loose=0)

    fake_mod = types.ModuleType("core.swipe")
    fake_mod.Observation, fake_mod.SwipeParams, fake_mod.score_frame = Observation, Params, score_frame
    monkeypatch.setitem(sys.modules, "core.swipe", fake_mod)
    h = HitRate(clock=_Clock())
    fg = SimpleNamespace(valid=True)
    box = (0.0, 0.0, 10.0, 10.0, 0.9)
    h.feed(fg, [box])
    h.feed(SimpleNamespace(valid=False), [box])       # from_fg -> None: skipped, not scored
    assert calls == [(("obs", fg), [box], Params)]
    s = h.snapshot()
    assert (s["rate"], s["frames"], s["skipped"]) == (0.75, 1, 1)


def test_hit_rate_with_the_real_referee_when_it_is_in_the_build():
    """Runs once core/swipe.py is on the branch (P5); skipped on a build without it."""
    pytest.importorskip("core.swipe")
    from core.foreground import FgBlob, FgFrame
    score = live_watch.swipe_scorer()
    blob = lambda x, y: FgBlob(x=x, y=y, area=4000.0, mass=1.0, bbox=(x - 20, y - 60, x + 20, y + 60))
    mask = np.zeros((200, 200), np.uint8)
    mask[60:105, 20:30] = mask[60:105, 120:130] = 255
    fg = FgFrame(mask, 0.0, 0.0, 4, blobs=[blob(100, 300), blob(500, 300)])
    groups, covered = score(fg, [(60.0, 200.0, 140.0, 420.0, 0.9)])
    assert groups == 2 and 0 <= covered <= groups     # the referee's own rule decides which
    assert score(fg, []) == (2, 0)
    assert score(FgFrame(None, 0.0, 0.0, 4, valid=False), []) is None


def test_hit_rate_exposed_in_status_absent_without_referee(tmp_path, monkeypatch):
    runtime, bus = api.RuntimeAPI(), api.EventBus()
    remote = RemoteApi(runtime, bus, token=TOKEN, roots={"projects": tmp_path}, state_fn=lambda: "standby",
                       snapshot_fn=lambda: _jpeg(), host="127.0.0.1", port=0)
    monkeypatch.setattr(live_watch, "swipe_scorer", lambda: None)
    assert "hit_rate" not in remote.status() and remote.status()["snapshot_fit"] is True
    remote.hit_rate = HitRate(score=_fake_score, clock=_Clock())
    remote.hit_rate.feed(_fg(5, 4), [])
    assert remote.status()["hit_rate"]["rate"] == 0.8
    assert remote.start()
    try:
        def get(path):
            req = urllib.request.Request(f"http://127.0.0.1:{remote.port}{path}",
                                         headers={"Authorization": f"Bearer {TOKEN}"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        code, body = get("/api/v1/status")
        assert code == 200 and json.loads(body)["hit_rate"]["groups"] == 5
        code, small = get("/api/v1/snapshot.jpg?w=160&q=40&max_bytes=6000")
        assert code == 200 and len(small) <= 6000
        assert cv2.imdecode(np.frombuffer(small, np.uint8), cv2.IMREAD_COLOR).shape[1] == 160
        code, full = get("/api/v1/snapshot.jpg")
        assert code == 200 and full == _jpeg()       # unchanged without parameters
        assert get("/api/v1/snapshot.jpg?max_bytes=100")[0] == 413
    finally:
        remote.stop()


def _import_main_loop():
    """As test_main_loop_guards: without torch/ultralytics, import against a stub core.pipeline."""
    if importlib.util.find_spec("torch") and importlib.util.find_spec("ultralytics"):
        import runtime.main_loop as ml
        return ml
    import core
    stub = types.ModuleType("core.pipeline")
    stub.ScaledTrack = object
    sys.modules["core.pipeline"] = stub
    try:
        import runtime.main_loop as ml
    finally:
        sys.modules.pop("core.pipeline", None)
        if getattr(core, "pipeline", None) is stub:
            delattr(core, "pipeline")
    return ml


def test_hit_rate_fed_by_the_main_loop_at_the_status_cadence():
    ml = _import_main_loop()
    fed = []
    remote = SimpleNamespace(publish_status=lambda st: None, client_active=lambda: (False, ""),
                             control_enabled=False,
                             hit_rate=SimpleNamespace(feed_from=lambda p: fed.append(p)))
    proc = SimpleNamespace(last_fg=object(), last_raw_boxes=[])
    app = SimpleNamespace(last_fps_time=0.0, _remote=remote, _remote_status=lambda: {}, processor=proc,
                          bus=SimpleNamespace(publish=lambda e: None))
    loop = ml.MainLoop(app)
    loop._tick_remote()
    loop._tick_remote()                               # within the 0.5 s cadence: no second sample
    assert fed == [proc]
    old = SimpleNamespace(publish_status=lambda st: None, client_active=lambda: (False, ""),
                          control_enabled=False)    # a remote without the figure
    app2 = SimpleNamespace(last_fps_time=0.0, _remote=old, _remote_status=lambda: {},
                           bus=SimpleNamespace(publish=lambda e: None))
    ml.MainLoop(app2)._tick_remote()


def test_hit_rate_raw_boxes_in_original_pixels():
    """``FrameProcessor._raw_boxes`` (copied verbatim from the swipe's 62f4662): letterbox (x, y, w, h)
    -> (x0, y0, x1, y1, box conf) in original px, the boxes the referee judges."""
    pytest.importorskip("torch")
    from core.pipeline import FrameProcessor
    bbox = (110.0, 52.0, 40.0, 120.0)
    dets = [(0, 0.0, bbox)]
    confs = {FrameProcessor._bbox_conf_key(bbox): 0.82}
    (x0, y0, x1, y1, conf), = FrameProcessor._raw_boxes(dets, confs, 2.0, 10.0, 12.0, 300.0, 40.0)
    assert (x0, y0, x1, y1) == (300.0 + 200.0, 40.0 + 80.0, 300.0 + 280.0, 40.0 + 320.0)
    assert conf == 0.82
