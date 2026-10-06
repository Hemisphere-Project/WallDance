"""services/remote_api.py — the remote ops API (REMOTE_OPS) over real HTTP on
loopback: auth, the guarded command policy, events, files (Range/gzip), logs,
clip jobs. Plus the app-log tee (services/app_log.py)."""
import gzip
import io
import json
import time
import urllib.error
import urllib.request

import cv2
import numpy as np
import pytest

from runtime import api
from services import app_log
from services.remote_api import POLICY, NEVER, RemoteApi, command_schema, load_or_create_token


@pytest.fixture
def server(tmp_path):
    projects = tmp_path / "projects"
    rec = projects / "p" / "recordings"
    rec.mkdir(parents=True)
    (projects / "p" / "p_20261006_120000.json").write_text('{"confidence": 0.4}')
    (projects / "p" / "events.jsonl").write_text('{"e": 1}\n' * 2000)
    vid = rec / "slot_1_20261006_120000.avi"
    w = cv2.VideoWriter(str(vid), cv2.VideoWriter_fourcc(*"MJPG"), 20.0, (64, 48))
    for i in range(40):
        w.write(np.full((48, 64, 3), i * 5, np.uint8))
    w.release()
    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "walldance_x.log"
    log.write_text("".join(f"line {i}\n" for i in range(500)))
    runtime, bus = api.RuntimeAPI(), api.EventBus()
    handled = []
    runtime.register(api.SetState, lambda c: handled.append(c))
    runtime.register(api.StartRecordingSlot, lambda c: handled.append(c))
    runtime.register(api.SetRigSheet, lambda c: handled.append(c))
    state = {"v": "standby"}
    token = load_or_create_token(tmp_path / "tok")
    remote = RemoteApi(runtime, bus, token=token,
                       roots={"projects": projects, "logs": logs},
                       state_fn=lambda: state["v"], log_path_fn=lambda: log,
                       snapshot_fn=lambda: b"\xff\xd8jpeg", host="127.0.0.1", port=0,
                       version={"commit": "abc"})
    assert remote.start()
    base = f"http://127.0.0.1:{remote.port}"
    yield dict(remote=remote, runtime=runtime, bus=bus, handled=handled, state=state,
               token=token, base=base, projects=projects, vid=vid)
    remote.stop()


def _req(ctx, path, body=None, token=True, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = f"Bearer {ctx['token']}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(ctx["base"] + path, data=data, headers=h,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _json(ctx, path, body=None, **kw):
    code, raw, _ = _req(ctx, path, body, **kw)
    return code, json.loads(raw)


def test_auth_ping_status(server):
    code, body = _json(server, "/api/v1/ping", token=False)
    assert code == 200 and body["version"] == {"commit": "abc"}
    code, _ = _json(server, "/api/v1/status", token=False)
    assert code == 401
    server["remote"].publish_status({"state": "standby", "project": "p"})
    seq_before = server["remote"].status()["event_seq"]
    server["bus"].publish(api.StatsTick(payload={"fps": 19.7}))   # periodic: latest only
    code, body = _json(server, "/api/v1/status")
    assert code == 200 and body["project"] == "p"
    assert body["latest"]["StatsTick"]["payload"] == {"fps": 19.7}
    assert body["event_seq"] == seq_before                        # not in the ring
    assert body["remote"] == {"control_enabled": False,
                              "bind": f"127.0.0.1:{server['remote'].port}"}
    assert server["remote"].client_active()[0]


def test_events_long_poll(server):
    remote, bus = server["remote"], server["bus"]
    _, first = _json(server, "/api/v1/events?since=0")
    start = first["next"]
    bus.publish(api.Toast("one"))
    bus.publish(api.Alert("camera_down", "camera down"))
    _, got = _json(server, f"/api/v1/events?since={start}&wait=1")
    assert [e["type"] for e in got["events"]][0] == "Toast"
    assert got["next"] == start + 2
    t0 = time.time()
    _, empty = _json(server, f"/api/v1/events?since={got['next']}&wait=0.3")
    assert empty["events"] == [] and time.time() - t0 >= 0.25


def test_command_policy(server):
    remote, runtime, handled, state = (server[k] for k in ("remote", "runtime", "handled", "state"))
    # STANDBY: control allowed
    code, body = _json(server, "/api/v1/command", {"type": "StartRecordingSlot", "args": {"slot": 3}})
    assert code == 200
    runtime.drain()
    assert handled[-1] == api.StartRecordingSlot(3)
    # bad args -> 400; unknown / never -> 403
    assert _json(server, "/api/v1/command", {"type": "StartRecordingSlot", "args": {"slot": 0}})[0] == 400
    assert _json(server, "/api/v1/command", {"type": "Quit"})[0] == 403
    assert _json(server, "/api/v1/command", {"type": "DeleteProject", "args": {"name": "p"}})[0] == 403
    assert _json(server, "/api/v1/command", {"type": "SetRemoteControl", "args": {"enabled": True}})[0] == 403
    assert _json(server, "/api/v1/command", {"type": "SelectSlot", "args": {"slot": 1, "history": True}})[0] == 403
    # RUN: control refused until the operator allows it; heavy always refused
    state["v"] = "run"
    code, body = _json(server, "/api/v1/command", {"type": "SetState", "args": {"state": "standby"}})
    assert code == 403 and "Allow remote control" in body["error"]
    code, _ = _json(server, "/api/v1/command", {"type": "SetRigSheet", "args": {"field": "f_number", "value": 2}})
    assert code == 200                                    # safe: always
    remote.control_enabled = True
    assert _json(server, "/api/v1/command", {"type": "SetState", "args": {"state": "standby"}})[0] == 200
    assert _json(server, "/api/v1/command", {"type": "RunKnownNTune"})[0] == 403
    runtime.drain()
    assert api.SetRigSheet("f_number", 2, echo=True) in handled   # remote edits echo
    # paths in commands must stay inside the shared roots
    state["v"] = "standby"
    code, body = _json(server, "/api/v1/command",
                       {"type": "PlaySlotRecording", "args": {"slot": 1, "path": "/etc/passwd"}})
    assert code == 403


def test_schema_excludes_never():
    schema = command_schema()
    assert "Quit" not in schema and "StartRecordingSlot" in schema
    assert schema["StartRecordingSlot"]["fields"]["slot"]["required"] is True
    assert all(POLICY[name] != NEVER for name in schema)
    assert "SetRemoteControl" not in POLICY        # operator-only, never remote


def test_files_range_gzip_logs_snapshot(server):
    code, body = _json(server, "/api/v1/files?path=projects/p")
    assert code == 200 and {d["name"] for d in body["dirs"]} == {"recordings"}
    assert _json(server, "/api/v1/files?path=projects/../..")[0] == 404
    assert _json(server, "/api/v1/file?path=../../etc/passwd")[0] == 404
    full = server["vid"].read_bytes()
    code, part, hdr = _req(server, "/api/v1/file?path=projects/p/recordings/" + server["vid"].name,
                           headers={"Range": "bytes=100-"})
    assert code == 206 and part == full[100:]
    assert hdr["Content-Range"] == f"bytes 100-{len(full) - 1}/{len(full)}"
    code, raw, hdr = _req(server, "/api/v1/file?path=projects/p/events.jsonl",
                          headers={"Accept-Encoding": "gzip"})
    assert hdr.get("Content-Encoding") == "gzip" and len(raw) < 2000
    assert gzip.decompress(raw) == (server["projects"] / "p" / "events.jsonl").read_bytes()
    code, body = _json(server, "/api/v1/logs?tail=3")
    assert body["lines"] == ["line 497", "line 498", "line 499"]
    code, raw, hdr = _req(server, "/api/v1/snapshot.jpg")
    assert code == 200 and raw.startswith(b"\xff\xd8")


def test_clip_job(server):
    rel = "projects/p/recordings/" + server["vid"].name
    code, job = _json(server, "/api/v1/clip", {"path": rel, "start": 10, "frames": 20,
                                               "scale": 0.5, "codec": "MJPG"})
    assert code == 200
    for _ in range(100):
        _, job = _json(server, f"/api/v1/jobs/{job['id']}")
        if job["state"] != "running":
            break
        time.sleep(0.05)
    assert job["state"] == "done" and job["frames"] == 20, job
    cap = cv2.VideoCapture(str(server["projects"].parent / job["output"]))
    assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == 32
    cap.release()
    server["state"]["v"] = "run"
    assert _json(server, "/api/v1/clip", {"path": rel})[0] == 403


def test_app_log_tee_survives_dead_console(tmp_path):
    class DeadPipe(io.StringIO):
        def write(self, s):
            raise BrokenPipeError

    import threading
    f = open(tmp_path / "a.log", "w", encoding="utf-8")
    tee = app_log._Tee(DeadPipe(), f, threading.Lock())
    tee.write("first\n")          # console raises -> swallowed, file keeps going
    tee.write("second\n")
    tee.flush()
    assert (tmp_path / "a.log").read_text() == "first\nsecond\n"
    for i in range(5):
        p = tmp_path / f"walldance_{i}.log"
        p.write_text("x")
        import os
        os.utime(p, (i, i))
    app_log._prune(tmp_path, keep=2, current=tmp_path / "walldance_0.log")
    assert sorted(p.name for p in tmp_path.glob("walldance_*.log")) == \
        ["walldance_0.log", "walldance_3.log", "walldance_4.log"]


def test_every_command_is_classified():
    """A new command must be put in a policy class (unlisted = denied, which
    is only intended for the operator-only SetRemoteControl)."""
    commands = {n for n, c in vars(api).items()
                if isinstance(c, type) and issubclass(c, api.Command) and c is not api.Command}
    assert commands - set(POLICY) == {"SetRemoteControl"}
    assert set(POLICY) <= commands
    assert POLICY["SetInputTransform"] == "control"


def test_set_input_transform_policy(server):
    """control: free in STANDBY, in RUN only once the operator allows it."""
    runtime, state, remote = server["runtime"], server["state"], server["remote"]
    got = []
    runtime.register(api.SetInputTransform, got.append)
    assert _json(server, "/api/v1/command",
                 {"type": "SetInputTransform", "args": {"rotation": 90}})[0] == 200
    assert _json(server, "/api/v1/command",
                 {"type": "SetInputTransform", "args": {"rotation": 45}})[0] == 400
    state["v"] = "run"
    assert _json(server, "/api/v1/command",
                 {"type": "SetInputTransform", "args": {"mirror": True}})[0] == 403
    remote.control_enabled = True
    assert _json(server, "/api/v1/command",
                 {"type": "SetInputTransform", "args": {"mirror": True}})[0] == 200
    runtime.drain()
    assert got == [api.SetInputTransform(rotation=90), api.SetInputTransform(mirror=True)]
