"""remote-deploy#8 and #12: remote command validation and the phone monitor (loopback only, no GPU)."""
import http.client
import json
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

APP = Path("/data/WallDance/application")
sys.path.insert(0, str(APP / "src"))
sys.path.insert(0, "/data/WallDance/extra")
sys.path.insert(0, str(APP / "tests"))

import numpy as np  # noqa: E402
from runtime import api  # noqa: E402
from services.remote_api import RemoteApi  # noqa: E402
from core.tracker import DancerTracker  # noqa: E402
from core import config_schema  # noqa: E402
import wdremote as wr  # noqa: E402

WORK = Path(sys.argv[1])

# ------------------------------------------------------------------ #8
print("=== remote-deploy#8: a remote command with a wrong type / range ===")
tracker = DancerTracker()
tracker.logger.start_session(tempfile.mkdtemp(dir=WORK))
runtime, bus = api.RuntimeAPI(), api.EventBus()
state = {"v": "standby"}
# the app's own handler body (app.py:1974-1976 _cb_tracker_age_change)
runtime.register(api.SetTrackerMaxAge, lambda c: setattr(tracker, "max_age", c.value))
seen = []
runtime.register(api.SetOscTarget, lambda c: seen.append((c.ip, c.port)))
runtime.register(api.SetMaxDancers, lambda c: seen.append(("max_dancers", c.value)))
runtime.register(api.SetPersonHeight, lambda c: seen.append(("person_height", c.value)))
(WORK / "projects").mkdir(exist_ok=True)
(WORK / "projects" / "small.txt").write_text("0123456789")
remote = RemoteApi(runtime, bus, token="t" * 20, roots={"projects": WORK / "projects"},
                   state_fn=lambda: state["v"], port=0)
assert remote.start()

args = wr._parse_kv(["value=3O"])              # the typo from the claim: letter O
print("wdremote._parse_kv(['value=3O']) ->", args)
print("submit SetTrackerMaxAge", args, "->", remote.submit("SetTrackerMaxAge", args))
for t, a in (("SetTrackerMaxAge", {"value": 0}), ("SetTrackerMaxAge", {"value": -5}),
             ("SetPersonHeight", {"value": -300}), ("SetOscTarget", {"ip": 12, "port": "nine"}),
             ("SetMaxDancers", {"value": "2"}), ("SetMaxDancers", {"value": 99})):
    code, body = remote.submit(t, a)
    print(f"submit {t} {a} -> {code} {body.get('error') or 'queued'}")

# the tracker with a dancer in view, then the queued command is drained (STANDBY -> RUN carries it)
_PROFILE = [-0.45] * 5 + [-0.30, -0.30, -0.15, -0.15, 0.0, 0.0, 0.0, 0.0, 0.25, 0.25, 0.45, 0.45]


def det(x, y=400.0, h=120.0):
    dy = np.array(_PROFILE) * h
    dx = np.array([0, -3, 3, -5, 5, -12, 12, -20, 20, -25, 25, -8, 8, -8, 8, -8, 8], float) * h / 120
    k = np.stack([x + dx, y + dy], axis=1)
    xs, ys = k[:, 0], k[:, 1]
    return k, np.full(17, 0.8), np.array([xs.min(), ys.min(), xs.max() - xs.min(), ys.max() - ys.min()])


tracker.set_person_height(120)
for i in range(40):
    tracker.update([det(800.0)], frame_number=i)
print("tracks before the command:", len(tracker.tracks), "| max_age =", repr(tracker.max_age))
runtime._queue.clear()
remote.submit("SetTrackerMaxAge", args)
runtime.drain()
print("after drain: tracker.max_age =", repr(tracker.max_age))
errors = 0
for i in range(40, 100):
    try:
        tracker.update([det(800.0)], frame_number=i)
    except Exception as e:  # noqa: BLE001
        errors += 1
        last = f"{type(e).__name__}: {e}"
print(f"next 60 frames: {errors} raised ({last if errors else '-'}); "
      "main_loop exits with code 3 at 50 in a row (OPS_TICK_ERROR_EXIT_STREAK)")

flat, warns = config_schema.validate_flat({"tracker_max_age": "3O", "person_height_px": -300})
print("the same junk through the config LOAD path (config_schema.validate_flat):", flat, warns)

# HTTP edge cases
base = f"http://127.0.0.1:{remote.port}"
hdr = {"Authorization": "Bearer " + "t" * 20}


def call(path, data=None, headers=None):
    req = urllib.request.Request(base + path, data=data, headers={**hdr, **(headers or {})},
                                 method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()[:80]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:80]
    except Exception as e:  # noqa: BLE001
        return type(e).__name__, str(e)[:80]


import io, contextlib  # noqa: E402
err = io.StringIO()
with contextlib.redirect_stderr(err):
    print("GET /events?since=abc ->", call("/api/v1/events?since=abc"))
    print("POST /command body [1,2] ->", call("/api/v1/command", data=b"[1,2]"))
    print("GET /file Range bytes=0-999999 on a 10-byte file ->",
          call("/api/v1/file?path=projects/small.txt", headers={"Range": "bytes=0-999999"}))
    time.sleep(0.3)
tb = [ln for ln in err.getvalue().splitlines() if "Error" in ln]
print("handler tracebacks printed to the app's stderr:", tb)
remote.stop()

# ------------------------------------------------------------------ #12
print("\n=== remote-deploy#12: the phone monitor ===")
from services import web_monitor as wm  # noqa: E402
from core import config as cfg  # noqa: E402
print("config:", "WEB_MONITOR_ENABLED =", cfg.WEB_MONITOR_ENABLED, "| HOST =", cfg.WEB_MONITOR_HOST,
      "| PORT =", cfg.WEB_MONITOR_PORT)
mon = wm.WebMonitor(port=0, host="127.0.0.1")      # loopback + ephemeral port for this test only
assert mon.start()
port = mon._server.server_address[1]
frame = np.full((270, 480, 3), 40, np.uint8)
frame[100:170, 200:280] = 200
mon.update_frame(frame)


def get(path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.getheader("Content-Type"), body


s1 = get("/snapshot.jpg")
print("GET /snapshot.jpg without any credential ->", s1[0], s1[1], len(s1[2]), "bytes")
print("GET /control?focus=1 without any credential ->", get("/control?focus=1")[::2])
s2 = get("/snapshot.jpg")
print("snapshot changed by the anonymous /control call:", s1[2] != s2[2],
      "| mon.get_jpeg() (what the remote API's /snapshot.jpg serves) changed too:", mon.get_jpeg() == s2[2])
before = threading.active_count()
socks = []
for _ in range(60):
    s = socket.create_connection(("127.0.0.1", port))
    s.sendall(b"GET /stats.json HTTP/1.1\r\nHost: x\r\n\r\n")     # keep-alive, then silence
    socks.append(s)
time.sleep(1.0)
print(f"60 idle keep-alive clients -> handler threads alive: {threading.active_count() - before}; "
      f"handler timeout = {wm._MonitorHandler.timeout}")
time.sleep(3.0)
print(f"3 s later, still alive: {threading.active_count() - before}")
for s in socks:
    s.close()

real_connect = socket.socket.connect


def no_route(self, addr):
    if addr and addr[0] == "8.8.8.8":
        raise OSError(101, "Network is unreachable")
    return real_connect(self, addr)


print("get_lan_ip() on this box:", wm.get_lan_ip())
socket.socket.connect = no_route
print("get_lan_ip() with no route to 8.8.8.8 (closed show LAN):", wm.get_lan_ip(),
      "| hostname resolves to", socket.gethostbyname(socket.gethostname()))
socket.socket.connect = real_connect
mon.stop()
