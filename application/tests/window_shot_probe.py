"""S1c: the REAL capture of the REAL window -- needs a display (Linux: $DISPLAY; Windows: the desktop).

The app's GUI built by the real DpgUiAdapter with a real viewport, a main loop on this thread that does what
``MainLoop._tick`` does for this (``serve_window_shot`` then one render per tick), the real RemoteApi on loopback
asked from another thread -- in RUN, with the operator's remote-control tick off -- and the cost of one capture
to the loop measured: the render of a frame that carries a capture against the frames around it.

Prints one JSON line; with ``--out DIR`` it also saves the PNG and the JPEG it got (two files). Never imported
by the tests (DearPyGui aborts without a display): ``test_window_shot.py`` runs it in a subprocess, opt-in.

    python tests/window_shot_probe.py [--captures 6] [--size 2010x1350] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


def _pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 3) if xs else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--captures", type=int, default=6)
    ap.add_argument("--size", help="resize the viewport to WxH after setup (the laptop's DPI-scaled size)")
    ap.add_argument("--out", help="save the PNG and the JPEG it got here")
    ap.add_argument("--hold", type=float, default=0.0, help="keep the window up this long at the end (s)")
    a = ap.parse_args(argv)

    import cv2
    import numpy as np
    import dearpygui.dearpygui as dpg
    from runtime import api
    from services.remote_api import RemoteApi
    from ui.adapter import DpgUiAdapter

    main_thread = threading.get_ident()
    out = {"ok": False}
    rt, bus = api.RuntimeAPI(), api.EventBus()
    adapter = DpgUiAdapter(rt, bus)
    adapter.create_gui({"camera_type": "IDS_PEAK"})
    noop = lambda *x, **k: None  # noqa: E731
    roi = SimpleNamespace(**{n: noop for n in (
        "_handle_roi_mouse_down", "_handle_roi_mouse_move", "_handle_roi_mouse_up", "_handle_mask_mouse_down",
        "_handle_mask_mouse_move", "_handle_mask_mouse_up", "_handle_preview_double_click", "exit_edit_modes")})
    adapter.setup_viewport(roi)
    if a.size:
        w, h = (int(v) for v in a.size.lower().split("x"))
        dpg.set_viewport_width(w)
        dpg.set_viewport_height(h)
    # what the technician would see on a rehearsal night: RUN, the health line, a support message
    bus.publish(api.StateChanged("run"))
    bus.publish(api.HealthLine("warn", "WARN - points: a body on the wall has had no point for 2.1 s", {}))
    bus.publish(api.SupportMessageShown("probe-1", "Bonjour Nico, tout va bien, continue.", time.time(),
                                        time.time(), 0))
    remote = RemoteApi(rt, bus, token="p" * 32, roots={"projects": Path.cwd()}, state_fn=lambda: "run",
                       host="127.0.0.1", port=0)
    remote.control_enabled = False
    remote.window_shot.run_interval_s = 1.0              # measured back to back here; the RUN limit is checked last
    assert remote.start()
    base = f"http://127.0.0.1:{remote.port}"

    # where the callback runs (DearPyGui's callback thread, not the main loop)
    seen_threads = []
    real_deliver = remote.window_shot.deliver

    def deliver(rid, buf):
        seen_threads.append(threading.get_ident() != main_thread)
        real_deliver(rid, buf)
    remote.window_shot.deliver = deliver

    renders, capture_renders, serve_ms = [], [], []
    ticks = []                                           # (tick ms: serve + render, a capture armed in it)

    def tick():
        armed_now = bool(remote.window_shot.wanted())
        t0 = time.perf_counter()
        adapter.serve_window_shot(remote)
        t1 = time.perf_counter()
        serve_ms.append((t1 - t0) * 1000)
        adapter.render_frame_raw()
        t2 = time.perf_counter()
        ms = (t2 - t1) * 1000
        (capture_renders if armed_now else renders).append(ms)
        ticks.append(((t2 - t0) * 1000, armed_now))

    def run_for(seconds):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end and dpg.is_dearpygui_running():
            tick()
            time.sleep(0.004)

    def get(path):
        req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {'p' * 32}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    try:
        run_for(1.5)                                     # warm up; the window settles
        out["client"] = [dpg.get_viewport_client_width(), dpg.get_viewport_client_height()]
        results = []
        for i in range(a.captures):
            path = "/api/v1/window.png" if i == 0 else ("/api/v1/window.jpg" if i == 1
                                                         else "/api/v1/window.jpg?w=640&q=50&max_bytes=40000")
            box = {}
            th = threading.Thread(target=lambda p=path: box.update(r=get(p)), daemon=True)
            n0 = len(capture_renders)
            th.start()
            while th.is_alive():
                tick()
                time.sleep(0.004)
            code, body, hdr = box["r"]
            res = {"path": path, "code": code, "bytes": len(body), "capture_frames": len(capture_renders) - n0,
                   "wait_ms": hdr.get("X-Capture-Wait-Ms"), "size": hdr.get("X-Window-Size")}
            if code == 200:
                img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
                res["decoded"] = [int(img.shape[1]), int(img.shape[0])]
                res["spread"] = int(img.max()) - int(img.min())
                res["mean"] = round(float(img.mean()), 1)
                if a.out:
                    Path(a.out).mkdir(parents=True, exist_ok=True)
                    name = "window.png" if path.endswith(".png") else (
                        "window_full.jpg" if i == 1 else "window_small.jpg")
                    (Path(a.out) / name).write_bytes(body)
            else:
                res["error"] = body[:200].decode(errors="replace")
            results.append(res)
            run_for(1.1)                                 # past the 1 s rate limit; more ordinary frames
        # the rate limit, against the real thing: two at once -> one 429
        box1, box2 = {}, {}
        t1 = threading.Thread(target=lambda: box1.update(r=get("/api/v1/window.jpg?w=320")), daemon=True)
        t1.start()
        time.sleep(0.05)
        box2["r"] = get("/api/v1/window.jpg?w=320")
        while t1.is_alive():
            tick()
            time.sleep(0.004)
        out["concurrent_codes"] = [box1["r"][0], box2["r"][0]]
        remote.window_shot.run_interval_s = 10.0            # the shipped RUN limit: the next one within 10 s -> 429
        run_for(1.2)
        code, _, hdr = get("/api/v1/window.jpg?w=320")
        out["run_limit"] = [code, hdr.get("Retry-After")]
        if a.hold:
            run_for(a.hold)
        # what one capture costs the loop: the capture's tick and the 10 after it, over the median tick
        plain = [ms for ms, cap in ticks if not cap]
        med = statistics.median(plain) if plain else 0.0
        loop_cost = []
        for i, (ms, cap) in enumerate(ticks):
            if cap:
                window = ticks[i:i + 11]
                loop_cost.append(round(sum(max(0.0, m - med) for m, _ in window), 1))
        out["loop_cost_ms"] = {"per_capture": loop_cost, "median": round(statistics.median(loop_cost), 1)
                               if loop_cost else None, "max": max(loop_cost) if loop_cost else None,
                               "tick_median_ms": round(med, 2)}
        out.update(ok=all(r["code"] == 200 for r in results), results=results,
                   callback_off_main_thread=seen_threads and all(seen_threads),
                   render_ms={"frames": len(renders), "median": _pct(renders, 0.5), "p95": _pct(renders, 0.95),
                              "max": round(max(renders), 3) if renders else None},
                   capture_render_ms=[round(x, 3) for x in capture_renders],
                   capture_extra_ms_median=round(statistics.median(capture_renders) - _pct(renders, 0.5), 3)
                   if capture_renders and renders else None,
                   serve_call_ms_max=round(max(serve_ms), 3),
                   status=remote.status().get("window_shot"))
    finally:
        remote.stop()
        dpg.destroy_context()
    print("@@PROBE@@ " + json.dumps(out, default=str))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
