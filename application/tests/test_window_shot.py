"""S1c (hub walldance#t-061 follow-up, #t-062): a picture of the app's own window through the remote API.

The real WallDanceGUI built by the real DpgUiAdapter in a viewport-less DearPyGui context (the A4 pattern), the
real RemoteApi over loopback HTTP, the main loop's own line (``MainLoop._tick_remote``) pumped on the main
thread. DearPyGui cannot show a viewport without a display (GLFW aborts) and ``output_frame_buffer`` before a
viewport crashes the process (both probed 26/11), so the frame buffer is faked where a picture must come back:
a fake ``output_frame_buffer`` that, like DearPyGui 2.3.1, hands an ``mvBuffer``-like frame (float32 RGBA 0..1,
top row first, ``get_width``/``get_height``) to the callback on another thread at the next render. The real
capture runs in ``window_shot_probe.py`` with a display (opt-in here: ``WD_DISPLAY_TESTS=1``).

Test names carry the gate clause they prove (``run_gate.sh``).
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import cv2
import numpy as np
import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")
import dearpygui._dearpygui as internal_dpg  # noqa: E402

from runtime import api  # noqa: E402
from services import live_watch, window_shot  # noqa: E402
from services.remote_api import RemoteApi, load_or_create_token  # noqa: E402
from services.window_shot import WindowShot  # noqa: E402

TESTS = Path(__file__).resolve().parent
EXTRA = TESTS.parents[1] / "extra"
MAIN = threading.main_thread()


# ----------------------------------------------------------------------------------------------
# DearPyGui's frame, faked as 2.3.1 delivers it
# ----------------------------------------------------------------------------------------------

class _MvBuffer(np.ndarray):
    """Like DearPyGui's mvBuffer: a flat float32 RGBA buffer with get_width() / get_height()."""

    def get_width(self):
        return self._w

    def get_height(self):
        return self._h


def _frame(rgb: np.ndarray) -> _MvBuffer:
    h, w = rgb.shape[:2]
    rgba = np.concatenate([rgb, np.full((h, w, 1), 255, np.uint8)], axis=2).astype(np.float32) / 255.0
    buf = rgba.reshape(-1).view(_MvBuffer)
    buf._w, buf._h = w, h
    return buf


def _ui_like(w=1340, h=900, seed=0) -> np.ndarray:
    """An RGB picture with a window's look: dark panels, a yellow band at the top, a green rail, text-like noise."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 30, np.uint8)
    img[: h // 12, :] = (255, 204, 0)                     # the support band (RGB)
    img[:, w - w // 4:] = (45, 90, 45)                    # a panel
    txt = rng.random((h, w)) < 0.08
    img[txt] = 220
    return img


class FakeDpg:
    """output_frame_buffer + the viewport queries, as DearPyGui 2.3.1 answers them on a desktop."""

    def __init__(self, rgb=None):
        self.rgb = _ui_like() if rgb is None else rgb
        self.client = (self.rgb.shape[1], self.rgb.shape[0])
        self.viewport_ok = True
        self.calls = []                                   # (thread name, callback) per output_frame_buffer
        self.cb_threads = []
        self.pending = None
        self.frame_fn = lambda: _frame(self.rgb)

    def output_frame_buffer(self, file="", *, callback=None, **kw):
        self.calls.append((threading.current_thread().name, callback))
        self.pending = callback

    def render(self):
        """The next render_dearpygui_frame: the frame goes to the callback, on DearPyGui's callback thread."""
        cb, self.pending = self.pending, None
        if cb is None:
            return

        def run():
            self.cb_threads.append(threading.current_thread() is not MAIN)
            cb(0, self.frame_fn(), None)
        t = threading.Thread(target=run, name="dpg-callbacks")
        t.start()
        t.join()


def _guard_dpg(monkeypatch, off):
    """Every DearPyGui function raises (and is recorded) when called off the main thread."""
    for mod in (dpg, internal_dpg):
        for name in dir(mod):
            fn = getattr(mod, name)
            if name.startswith("__") or not callable(fn) or isinstance(fn, type):
                continue

            def wrap(*a, __fn=fn, __name=name, **k):
                if threading.current_thread() is not MAIN:
                    off.append((__name, threading.current_thread().name))
                    raise AssertionError(f"dpg.{__name} called off the main thread")
                return __fn(*a, **k)
            monkeypatch.setattr(mod, name, wrap)


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


# ----------------------------------------------------------------------------------------------
# the app: real GUI (no viewport), real RemoteApi, the main loop's own line
# ----------------------------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path):
    from ui.adapter import DpgUiAdapter
    rt, bus = api.RuntimeAPI(), api.EventBus()
    adapter = DpgUiAdapter(rt, bus)
    adapter.create_gui({"camera_type": "IDS_PEAK"})
    others = []
    rt.register(api.SetState, others.append)
    state = {"v": "standby"}
    token = load_or_create_token(tmp_path / "tok")
    server = RemoteApi(rt, bus, token=token, roots={"projects": tmp_path}, state_fn=lambda: state["v"],
                       host="127.0.0.1", port=0)
    assert server.start()
    ml = _import_main_loop()
    shell = SimpleNamespace(last_fps_time=0.0, _remote=server, ui=adapter, _remote_status=lambda: {},
                            bus=bus)
    loop = ml.MainLoop(shell)
    ns = SimpleNamespace(rt=rt, bus=bus, adapter=adapter, server=server, state=state, token=token, loop=loop,
                         others=others, base=f"http://127.0.0.1:{server.port}", fake=None, serve_ms=[])

    def get(path, token=True, timeout=15):
        h = {"Authorization": f"Bearer {ns.token if token is True else token}"} if token else {}
        req = urllib.request.Request(ns.base + path, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)
    ns.get = get

    def tick():
        """One main-loop tick as far as this unit goes: MainLoop._tick_remote (its one line), then a render."""
        t0 = time.perf_counter()
        ns.loop._tick_remote()
        ns.serve_ms.append((time.perf_counter() - t0) * 1000)
        if ns.fake is not None:
            ns.fake.render()
    ns.tick = tick

    def pump_until(done, max_s=10.0):
        end = time.monotonic() + max_s
        while not done() and time.monotonic() < end:
            tick()
            time.sleep(0.003)
    ns.pump_until = pump_until

    def ask(path, token=True):
        """A client on its own thread while the main loop runs on this one; returns (code, body, headers)."""
        box = {}
        th = threading.Thread(target=lambda: box.update(r=get(path, token)), name="client")
        th.start()
        pump_until(lambda: not th.is_alive())
        th.join(5)
        return box["r"]
    ns.ask = ask
    try:
        yield ns
    finally:
        server.stop()
        dpg.destroy_context()


@pytest.fixture
def desktop(app, monkeypatch):
    """The window on a desktop: a fake frame buffer, and every DearPyGui call guarded to the main thread."""
    fake = FakeDpg()
    monkeypatch.setattr(dpg, "output_frame_buffer", fake.output_frame_buffer)
    monkeypatch.setattr(dpg, "is_viewport_ok", lambda: fake.viewport_ok)
    monkeypatch.setattr(dpg, "get_viewport_client_width", lambda: fake.client[0])
    monkeypatch.setattr(dpg, "get_viewport_client_height", lambda: fake.client[1])
    off = []
    _guard_dpg(monkeypatch, off)
    app.fake, app.off = fake, off
    return app


def _decode(body):
    return cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)


def _err(body):
    return json.loads(body)["error"]


# ----------------------------------------------------------------------------------------------
# Clause: the token
# ----------------------------------------------------------------------------------------------

def test_token_the_endpoint_needs_it(desktop):
    for path in ("/api/v1/window.jpg", "/api/v1/window.png", "/api/v1/window.jpg?w=320"):
        code, body, _ = desktop.ask(path, token=False)
        assert code == 401 and "bearer token" in _err(body)
        code, body, _ = desktop.ask(path, token="x" * 40)
        assert code == 401
    assert desktop.fake.calls == []                               # nothing was captured for them
    assert desktop.server.status()["window_shot"]["served"] == 0
    code, body, hdr = desktop.ask("/api/v1/window.jpg")
    assert code == 200 and hdr["Content-Type"] == "image/jpeg"


# ----------------------------------------------------------------------------------------------
# Clause: served on the main loop, no DearPyGui call off it
# ----------------------------------------------------------------------------------------------

def test_main_loop_serves_it_and_no_dearpygui_call_happens_off_the_main_thread(desktop):
    code, body, hdr = desktop.ask("/api/v1/window.png")
    assert code == 200 and hdr["Content-Type"] == "image/png"
    got = _decode(body)
    want = cv2.cvtColor(desktop.fake.rgb, cv2.COLOR_RGB2BGR)
    assert got.shape == want.shape and np.array_equal(got, want)  # lossless; colours and rows the right way
    assert hdr["X-Window-Size"] == "1340x900"
    # the capture was armed once, by the main loop, and the frame came on another thread (DearPyGui's)
    assert [t for t, _ in desktop.fake.calls] == ["MainThread"]
    assert desktop.fake.cb_threads == [True]
    assert desktop.off == []                                      # no dpg call from the HTTP or callback thread
    # the main loop never waits: the serve is a flag read, then one call that returns at once
    assert max(desktop.serve_ms) < 20, max(desktop.serve_ms)


def test_main_loop_hands_dearpygui_one_callback_that_lives_for_good(desktop):
    """The abort of 26/11: DearPyGui frees its reference to the callback on the render thread without the GIL.
    The callback must be the same long-lived object every capture, kept by the module."""
    desktop.server.window_shot.min_interval_s = 0.0
    for _ in range(3):
        assert desktop.ask("/api/v1/window.jpg?w=200")[0] == 200
    cbs = [cb for _, cb in desktop.fake.calls]
    assert len(cbs) == 3 and all(cb is desktop.server.window_shot.on_frame for cb in cbs)
    assert any(cb is cbs[0] for cb in window_shot._KEPT)
    src = (TESTS.parent / "src" / "ui" / "adapter.py").read_text(encoding="utf-8")
    assert "output_frame_buffer(callback=shot.on_frame)" in src


def test_main_loop_has_one_call_line_and_tolerates_an_app_without_it():
    src = (TESTS.parent / "src" / "runtime" / "main_loop.py").read_text(encoding="utf-8")
    assert sum("serve_window_shot" in line for line in src.splitlines()) == 1
    ml = _import_main_loop()
    remote = SimpleNamespace(publish_status=lambda st: None, client_active=lambda: (False, ""),
                             control_enabled=False, window_shot=WindowShot())
    for app in (SimpleNamespace(last_fps_time=0.0, _remote=remote, _remote_status=lambda: {},
                                bus=SimpleNamespace(publish=lambda e: None)),             # no ui at all
                SimpleNamespace(last_fps_time=0.0, _remote=remote, _remote_status=lambda: {},
                                bus=SimpleNamespace(publish=lambda e: None), ui=SimpleNamespace())):
        ml.MainLoop(app)._tick_remote()


def test_main_loop_serve_never_raises(desktop, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("driver lost")
    monkeypatch.setattr(dpg, "output_frame_buffer", boom)
    code, body, _ = desktop.ask("/api/v1/window.jpg")
    assert code == 503 and "could not be armed (RuntimeError: driver lost)" in _err(body)


# ----------------------------------------------------------------------------------------------
# Clause: one in flight; a second waits or is refused; the rate in RUN
# ----------------------------------------------------------------------------------------------

def test_one_in_flight_the_second_is_refused_and_a_loop_cannot_cost_frames(desktop):
    shot = desktop.server.window_shot
    box = {}
    first = threading.Thread(target=lambda: box.update(r=desktop.get("/api/v1/window.jpg?w=320")))
    first.start()
    t0 = time.monotonic()
    while not shot._busy and time.monotonic() - t0 < 5:           # the first is in flight, not served yet
        time.sleep(0.005)
    code, body, hdr = desktop.get("/api/v1/window.jpg?w=320")      # the second: refused at once
    assert code == 429 and hdr["Retry-After"] == "1" and "already in flight" in _err(body)
    assert json.loads(body)["retry_after"] == 1
    desktop.pump_until(lambda: not first.is_alive())
    assert box["r"][0] == 200
    code, body, hdr = desktop.get("/api/v1/window.jpg?w=320")      # within the second: refused
    assert code == 429 and "one window capture every 1 s" in _err(body) and hdr["Retry-After"] == "1"
    # a client looping as fast as it can for half a second: no capture beyond the first
    end = time.monotonic() + 0.5
    codes = []
    while time.monotonic() < end:
        codes.append(desktop.get("/api/v1/window.jpg?w=320")[0])
        desktop.tick()
    assert set(codes) == {429} and len(desktop.fake.calls) == 1
    st = desktop.server.status()["window_shot"]
    assert st["served"] == 1 and st["busy"] >= 2


def test_one_in_flight_in_run_one_every_ten_seconds(desktop):
    desktop.state["v"] = "run"
    shot = desktop.server.window_shot
    assert shot.run_interval_s == 10.0 and shot.min_interval_s == 1.0
    assert desktop.ask("/api/v1/window.jpg?w=320")[0] == 200
    shot._last_start -= 2.0                                       # 2 s later: STANDBY would allow it, RUN not
    code, body, hdr = desktop.ask("/api/v1/window.jpg?w=320")
    assert code == 429 and "every 10 s in RUN" in _err(body) and 7 <= int(hdr["Retry-After"]) <= 8
    desktop.state["v"] = "standby"
    assert desktop.ask("/api/v1/window.jpg?w=320")[0] == 200
    assert len(desktop.fake.calls) == 2


# ----------------------------------------------------------------------------------------------
# Clause: the fit, by the camera snapshot's fitter
# ----------------------------------------------------------------------------------------------

def test_fit_to_max_bytes_with_the_snapshot_fitter(desktop, monkeypatch):
    assert window_shot.snapshot_fitted is live_watch.snapshot_fitted      # one fitter, not two
    used = []
    monkeypatch.setattr(window_shot, "snapshot_fitted",
                        lambda jpg, q: used.append(dict(q)) or live_watch.snapshot_fitted(jpg, q))
    shot = desktop.server.window_shot
    shot.min_interval_s = 0.0
    code, body, _ = desktop.ask("/api/v1/window.jpg")              # no parameters: full width, quality 80
    assert code == 200 and _decode(body).shape[1] == 1340 and used[-1] == {"w": "1340", "q": "80"}
    full = len(body)
    code, body, _ = desktop.ask("/api/v1/window.jpg?w=640&q=60&max_bytes=20000")
    assert code == 200 and len(body) <= 20000 and _decode(body).shape[1] <= 640
    code, body, _ = desktop.ask("/api/v1/window.jpg?w=1340&max_bytes=30000")   # the fitter steps q then width
    assert code == 200 and len(body) <= 30000 < full
    code, body, _ = desktop.ask("/api/v1/window.jpg?max_bytes=200")
    assert code == 413 and "does not fit in 200 bytes" in _err(body)
    n = len(desktop.fake.calls)
    code, body, _ = desktop.ask("/api/v1/window.jpg?w=wide")
    assert code == 400 and "w='wide'" in _err(body) and len(desktop.fake.calls) == n   # refused before a capture
    code, body, hdr = desktop.ask("/api/v1/window.png?w=320")      # the PNG is always full size
    assert code == 200 and _decode(body).shape[1] == 1340 and hdr["Content-Type"] == "image/png"


# ----------------------------------------------------------------------------------------------
# Clause: 503 with the reason, never a crash and never a black picture passed off as real
# ----------------------------------------------------------------------------------------------

def test_503_no_viewport_in_a_headless_run_never_calls_the_capture(app, monkeypatch):
    calls = []
    # the real call would crash the process without a viewport: it must never be reached
    monkeypatch.setattr(dpg, "output_frame_buffer", lambda *a, **k: calls.append(1) or pytest.fail("called"))
    assert dpg.is_viewport_ok() is False                          # the real DearPyGui, no viewport
    t0 = time.monotonic()
    code, body, hdr = app.ask("/api/v1/window.jpg")
    assert code == 503 and "no window on screen (no viewport" in _err(body) and calls == []
    assert time.monotonic() - t0 < 2.0                            # refused by the main loop, not timed out
    assert hdr["Content-Type"] == "application/json"
    assert app.get("/api/v1/status")[0] == 200                    # the API is still up
    assert app.server.status()["window_shot"]["refused"] == 1


def test_503_a_minimised_window_a_build_without_the_call_a_blank_frame(desktop, monkeypatch):
    shot = desktop.server.window_shot
    shot.min_interval_s = 0.0
    desktop.fake.client = (0, 0)
    code, body, _ = desktop.ask("/api/v1/window.jpg")
    assert code == 503 and "the window is minimised (0 x 0 px)" in _err(body)
    desktop.fake.client = (1340, 900)
    desktop.fake.frame_fn = lambda: _frame(np.zeros((900, 1340, 3), np.uint8))     # all black
    code, body, _ = desktop.ask("/api/v1/window.png")
    assert code == 503 and "blank" in _err(body) and "minimised or hidden" in _err(body)
    desktop.fake.frame_fn = lambda: _frame(np.full((900, 1340, 3), 30, np.uint8))  # one grey
    assert desktop.ask("/api/v1/window.jpg")[0] == 503
    short = np.asarray(_frame(_ui_like()))[:-8].view(_MvBuffer)                     # a short buffer
    short._w, short._h = 1340, 900
    desktop.fake.frame_fn = lambda: short
    code, body, _ = desktop.ask("/api/v1/window.jpg")
    assert code == 503 and "could not be read" in _err(body)
    monkeypatch.delattr(dpg, "output_frame_buffer")
    code, body, _ = desktop.ask("/api/v1/window.jpg")
    assert code == 503 and "this DearPyGui build has no output_frame_buffer" in _err(body)
    desktop.adapter.gui, gui = None, desktop.adapter.gui
    try:
        code, body, _ = desktop.ask("/api/v1/window.jpg")
        assert code == 503 and "not built yet" in _err(body)
    finally:
        desktop.adapter.gui = gui
    assert desktop.off == []


def test_503_the_main_loop_does_not_serve_or_no_frame_is_rendered_and_a_late_frame_is_dropped(desktop):
    shot = desktop.server.window_shot
    shot.min_interval_s, shot.timeout_s = 0.0, 0.3
    t0 = time.monotonic()
    code, body, _ = desktop.get("/api/v1/window.jpg")              # nobody runs the main loop (a dialog)
    assert code == 503 and "the main loop did not serve the capture within 0.3 s" in _err(body)
    assert 0.25 < time.monotonic() - t0 < 2.0
    # armed, but no frame rendered after it (a minimised window on some platforms)
    box = {}
    th = threading.Thread(target=lambda: box.update(r=desktop.get("/api/v1/window.jpg")))
    th.start()
    while th.is_alive():
        desktop.loop._tick_remote()                               # arm, never render
        time.sleep(0.01)
    code, body, _ = box["r"]
    assert code == 503 and "no frame was rendered after the capture was armed" in _err(body)
    late = desktop.fake.pending                                   # DearPyGui renders it later: dropped
    assert late is not None
    desktop.fake.render()
    shot.timeout_s = 3.0
    desktop.fake.frame_fn = lambda: _frame(_ui_like(seed=5))
    code, body, _ = desktop.ask("/api/v1/window.png")              # the next request gets its own frame
    assert code == 200
    assert np.array_equal(_decode(body), cv2.cvtColor(_ui_like(seed=5), cv2.COLOR_RGB2BGR))


# ----------------------------------------------------------------------------------------------
# Clause: allowed in RUN with the remote-control tick off
# ----------------------------------------------------------------------------------------------

def test_run_allowed_with_the_remote_control_tick_off(desktop):
    desktop.state["v"] = "run"
    desktop.server.control_enabled = False
    req = urllib.request.Request(desktop.base + "/api/v1/command", method="POST",
                                 data=json.dumps({"type": "SetState", "args": {"state": "standby"}}).encode(),
                                 headers={"Authorization": f"Bearer {desktop.token}"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 403 and "Allow remote control" in json.loads(e.value.read())["error"]
    code, body, _ = desktop.ask("/api/v1/window.jpg?w=640")        # the picture is a read: allowed
    assert code == 200 and _decode(body).shape[1] == 640
    st = json.loads(desktop.get("/api/v1/status")[1])
    assert st["window_shot"]["served"] == 1 and st["window_shot"]["last"]["w"] == 1340
    assert st["window_shot"]["run_interval_s"] == 10.0
    assert st["remote"] == {"control_enabled": False, "bind": f"127.0.0.1:{desktop.server.port}"}
    assert desktop.others == []                                   # and nothing reached the show


# ----------------------------------------------------------------------------------------------
# Clause: wdremote ui-shot writes the file
# ----------------------------------------------------------------------------------------------

def _wdremote():
    spec = importlib.util.spec_from_file_location("wdremote_s1c", EXTRA / "wdremote.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wdremote_s1c"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def client(desktop, tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("LocalTransport emulates the laptop with a posix shell")
    wd = _wdremote()
    monkeypatch.setattr(wd, "DEFAULT_CONFIG", tmp_path / "cfg" / "remote.json")
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "remote_token.local").write_text(desktop.token + "\n")
    remote = wd.Remote(host="local", root=str(tmp_path), os="posix", api_port=desktop.server.port)
    return SimpleNamespace(wd=wd, tr=wd.LocalTransport(remote), remote=remote)


def _run_cli(desktop, client, argv):
    """wdremote <argv> on a client thread while the main loop runs here; (exit code)."""
    wd = client.wd
    a = wd.build_parser().parse_args(argv)
    box = {}
    th = threading.Thread(target=lambda: box.update(rc=wd.cmd_ui_shot(client.tr, client.remote, a)))
    th.start()
    desktop.pump_until(lambda: not th.is_alive(), max_s=20)
    th.join(5)
    return box["rc"]


def test_wdremote_ui_shot_writes_the_file(desktop, client, tmp_path, capsys):
    wd = client.wd
    assert wd._split_passthrough(["ui-shot", "-o", "run"]) == (["ui-shot", "-o", "run"], None)
    a = wd.build_parser().parse_args(["ui-shot", "-o", "x.jpg"])
    assert a.cmd == "ui-shot" and a.width is None and a.quality == 80 and not a.full
    desktop.server.window_shot.min_interval_s = 0.0
    out = tmp_path / "shots" / "fenetre.jpg"
    assert _run_cli(desktop, client, ["ui-shot", "-o", str(out)]) == 0
    img = _decode(out.read_bytes())
    assert img.shape[:2] == (900, 1340)
    assert f"[ui-shot] JPG 1340x900" in capsys.readouterr().out
    small = tmp_path / "petit.jpg"
    assert _run_cli(desktop, client, ["ui-shot", "-o", str(small), "--width", "400", "--max-bytes", "15000"]) == 0
    assert _decode(small.read_bytes()).shape[1] <= 400 and small.stat().st_size <= 15000
    png = tmp_path / "guide" / "phase1.png"
    assert _run_cli(desktop, client, ["ui-shot", "-o", str(png), "--full"]) == 0
    assert np.array_equal(_decode(png.read_bytes()), cv2.cvtColor(desktop.fake.rgb, cv2.COLOR_RGB2BGR))
    assert "[ui-shot] PNG 1340x900" in capsys.readouterr().out
    # busy: it waits the time the app names, then gets it
    desktop.server.window_shot.min_interval_s = 0.8
    again = tmp_path / "again.jpg"
    assert _run_cli(desktop, client, ["ui-shot", "-o", str(again), "--width", "300"]) == 0
    assert "busy (429), again in 1 s" in capsys.readouterr().out and again.is_file()
    # refused: no file, exit 1, the reason printed
    desktop.server.window_shot.min_interval_s = 0.0
    desktop.fake.client = (0, 0)
    nope = tmp_path / "nope.jpg"
    assert _run_cli(desktop, client, ["ui-shot", "-o", str(nope)]) == 1
    assert "refused (503)" in capsys.readouterr().out and not nope.exists()
    assert desktop.off == []


def test_wdremote_image_size_reader():
    wd = _wdremote()
    ok, png = cv2.imencode(".png", np.zeros((37, 53, 3), np.uint8))
    ok2, jpg = cv2.imencode(".jpg", np.zeros((41, 67, 3), np.uint8))
    assert wd._image_size(png.tobytes()) == (53, 37) and wd._image_size(jpg.tobytes()) == (67, 41)
    assert wd._image_size(b"not an image") is None


# ----------------------------------------------------------------------------------------------
# Clause: wdwatch's option, off by default, GET only
# ----------------------------------------------------------------------------------------------

def _wdwatch():
    if "wdwatch" in sys.modules and hasattr(sys.modules["wdwatch"], "Watcher"):
        return sys.modules["wdwatch"]
    spec = importlib.util.spec_from_file_location("wdwatch", EXTRA / "wdwatch.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wdwatch"] = mod
    spec.loader.exec_module(mod)
    return mod


class _Sink:
    def write(self, s):
        pass

    def flush(self):
        pass


def _watch(desktop, tmp_path, name, **kw):
    ww = _wdwatch()
    sess = SimpleNamespace(base=desktop.base, token=desktop.token, alive=lambda: True, close=lambda: None)
    return ww.Watcher(lambda refresh_token=False: sess, tmp_path / name, out=_Sink(), **kw)


def _poll(desktop, w):
    box = {}
    th = threading.Thread(target=lambda: box.update(rec=w.poll_once()))
    th.start()
    desktop.pump_until(lambda: not th.is_alive(), max_s=20)
    th.join(5)
    return box["rec"]


def test_wdwatch_option_is_off_by_default_and_stays_get_only(desktop, tmp_path, monkeypatch):
    ww = _wdwatch()
    a = ww.build_parser().parse_args(["watch"])
    assert a.ui_shot_every == 0 and a.ui_shot_width == 800 and a.ui_shot_kb == 80.0
    sent = []
    real = ww.ApiReader._request
    monkeypatch.setattr(ww.ApiReader, "_request",
                        lambda self, method, path, mb: sent.append((method, path)) or real(self, method, path, mb))
    # off by default: three polls, not one window picture
    w = _watch(desktop, tmp_path, "off")
    for _ in range(3):
        rec = _poll(desktop, w)
        assert rec["ok"] and "ui_shot" not in rec
    assert not any("/window" in p for _, p in sent) and desktop.fake.calls == []
    assert not (w.run_dir / "ui_last.jpg").exists()
    # on, every 2 polls: polls 1 and 3 take one; only the last is kept
    desktop.server.window_shot.min_interval_s = 0.0
    sent.clear()
    w = _watch(desktop, tmp_path, "on", ui_shot_every=2, ui_shot_width=640, ui_shot_kb=40)
    recs = [_poll(desktop, w) for _ in range(4)]
    assert [r["ui_shot"].get("file") for r in recs] == ["ui_last.jpg", None, "ui_last.jpg", None]
    assert recs[1]["ui_shot"] == {"skipped": "cadence"}
    last = (w.run_dir / "ui_last.jpg").read_bytes()
    assert len(last) <= 40 * 1024 and _decode(last).shape[1] <= 640
    assert sorted(p.name for p in w.run_dir.iterdir() if "ui_" in p.name) == ["ui_last.jpg"]
    wins = [p for _, p in sent if "/window" in p]
    assert len(wins) == 2 and all(urlparse(p).path == "/api/v1/window.jpg" for p in wins)
    assert "max_bytes=40960" in wins[0] and "w=640" in wins[0]
    assert {m for m, _ in sent} == {"GET"}                        # GET only, in every request
    assert "/api/v1/window.jpg" in ww.READ_PATHS and "/api/v1/window.png" not in ww.READ_PATHS
    for path in ("/api/v1/window.png", "/api/v1/command"):
        with pytest.raises(ww.ReadOnlyViolation):
            ww.ApiReader(desktop.base, desktop.token).get(path, 10_000)
    # a refusal is logged and skipped, the poll stands; an older build without the picture is skipped
    desktop.fake.client = (0, 0)
    w2 = _watch(desktop, tmp_path, "refused", ui_shot_every=1)
    rec = _poll(desktop, w2)
    assert rec["ok"] and rec["ui_shot"]["skipped"].startswith("http 503: no picture of the window")
    desktop.server.window_shot = None
    rec = _poll(desktop, _watch(desktop, tmp_path, "older", ui_shot_every=1))
    assert rec["ok"] and rec["ui_shot"] == {"skipped": "this build has no window picture"}
    # the source still has no way to send
    tree = ast.parse((EXTRA / "wdwatch.py").read_text(encoding="utf-8"))
    consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not consts & {"POST", "PUT", "DELETE", "PATCH", "/api/v1/command", "/api/v1/clip"}


# ----------------------------------------------------------------------------------------------
# Clause: the real capture -- needs a display (opt-in)
# ----------------------------------------------------------------------------------------------

def _has_display() -> bool:
    return os.name == "nt" or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


@pytest.mark.skipif(os.environ.get("WD_DISPLAY_TESTS") != "1" or not _has_display(),
                    reason="the real capture needs a desktop: DearPyGui 2.3.1 cannot show a viewport without a "
                           "display (GLFW aborts in show_viewport) and output_frame_buffer before a viewport "
                           "crashes the process -- run with WD_DISPLAY_TESTS=1 on a desktop")
def test_real_capture_of_the_real_window_on_a_display():
    """The app's GUI in a real viewport, the real call, the real RemoteApi, in RUN with the tick off: opens a
    window for a few seconds. In a subprocess: a DearPyGui abort must not take pytest down."""
    p = subprocess.run([sys.executable, str(TESTS / "window_shot_probe.py"), "--captures", "3"],
                       capture_output=True, text=True, timeout=180, cwd=str(TESTS.parent))
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@PROBE@@ ")), None)
    assert line is not None, (p.returncode, p.stderr[-2000:])
    r = json.loads(line[len("@@PROBE@@ "):])
    assert r["ok"] and r["callback_off_main_thread"] and r["concurrent_codes"] == [200, 429]
    assert r["run_limit"][0] == 429
    for res in r["results"]:
        assert res["code"] == 200 and res["spread"] > 50          # a real picture, not a blank one
    assert r["results"][0]["decoded"] == r["client"]              # the PNG is the window's client area
    print(f"\n[window_shot] client {r['client']}, loop cost per capture {r['loop_cost_ms']}")
