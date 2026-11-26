"""A picture of the app's own window, through the remote API (S1c, hub walldance#t-061 and #t-062).

``GET /api/v1/window.jpg`` (fitted for a slow link like ``/snapshot.jpg``: ``?w=&q=&max_bytes=``) and
``GET /api/v1/window.png`` (full size, lossless, only when asked): what the technician sees -- the panels, the
readiness rows, the health line, the support band, the cards. Only the app's own frame buffer (DearPyGui's
``output_frame_buffer``): never the desktop, never another window. Two uses: the remote supervisor of 7-8/12
sees what Nico sees without a remote desktop, and the French operator guide gets real screenshots.

**Threads** (the support line's pattern, ``services/support_line.py``): nothing here calls DearPyGui.

- HTTP thread: ``WindowShot.picture`` -> one request (``take``), then it waits, at most ``timeout_s``.
- main loop: ``DpgUiAdapter.serve_window_shot`` (``ui/adapter.py``; one call line in ``MainLoop._tick_remote``)
  sees the request, checks the window and arms ONE capture: ``dpg.output_frame_buffer(callback=...)`` returns
  at once and the next ``render_dearpygui_frame`` reads the frame buffer.
- DearPyGui hands the frame to the callback on its own callback thread; the callback keeps a reference and wakes
  the HTTP thread (``deliver``). The HTTP thread converts and encodes it (numpy, cv2).

**What DearPyGui 2.3.1 does** (probed on mgr-ran, 26/11, ``tmp_analysis/r2-1126/gates/shot/probes``): the call
returns in ~0.05 ms; the frame comes with the next rendered frame (sometimes the one after), on a thread that is
not the main loop's, as an ``mvBuffer``: float32 RGBA in 0..1, the top row first, ``get_width()`` x
``get_height()`` = the viewport's client area. **Called before a viewport exists it crashes the process
(segfault)**, and without a display the viewport cannot be shown at all (GLFW aborts in ``show_viewport``): so
the adapter checks ``is_viewport_ok()`` first, and a headless run or a test answers 503.

**The callback must outlive every capture.** DearPyGui keeps the callable in an ``mvPyObject`` whose destructor
does ``Py_XDECREF`` without the GIL, on the render thread (DearPyGui's src/mvPyUtils.cpp). A fresh closure per
capture was freed right there, and the process aborted (« PyThreadState_Get: the function must be called with
the GIL held », the first real run, 26/11). So each ``WindowShot`` makes ONE callable, ``on_frame``, and the
module keeps it for the life of the process (``_KEPT``): DearPyGui's decrement never reaches zero. The frame
buffer itself is created and released with the GIL held (on the main loop's call and on the callback thread).

**What a capture costs the loop** (mgr-ran, 26/11, ``tests/window_shot_probe.py``): DearPyGui reads the whole
frame buffer and converts it to floats inside that one render call, so the cost grows with the window: one
render of ~17 ms (median; 30 ms at most) at 1340 x 900, ~60 ms (116 at most) at 2010 x 1350 -- the laptop's
window, UI scale 1.5 on its 2560 x 1600 screen -- and ~100 ms (peaks near 280) there when the machine is loaded,
~85 ms at 2680 x 1800; a plain render is ~2.5 ms. So at the laptop's size one capture is more than one camera
frame (50 ms), and the loop may log it as a camera stall. Arming it costs ~0.2 ms and the conversion runs on
the HTTP thread. It cannot be made cheaper from Python, so the rate bounds it.

**Policy**: a read (GET), allowed in every state, RUN included, whatever the operator's « Allow remote control
during RUN » tick -- like the camera snapshot; the bearer token like every endpoint. **Rate**: one capture in
flight; at most one every ``min_interval_s`` (1 s) in STANDBY and one every ``run_interval_s`` (10 s) in RUN;
another request is refused (429 with ``Retry-After``), so a loop of requests cannot cost the show more than
one long frame every 10 s.

**Never a picture passed off as real**: no window, a minimised one (client area under 16 px), a build without
the call, a main loop that does not serve within ``timeout_s``, or a frame that comes back blank (one colour:
a hidden or minimised window's buffer) -- each answers 503 with its reason.
"""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from services.live_watch import snapshot_fitted

WINDOW_PATHS = ("/api/v1/window.jpg", "/api/v1/window.png")
WINDOW_JPEG_QUALITY = 80          # /window.jpg without q: the text of the panels stays readable at full width
BLANK_SPREAD = 4                  # a frame whose pixels all lie within this many levels is blank, not a window
_KEPT: list = []                  # every on_frame ever handed to DearPyGui: never freed (see the docstring)


def _bgr_from_buffer(buf: Any):
    """DearPyGui's frame (``mvBuffer``: float32 RGBA 0..1, top row first, ``get_width``/``get_height``) ->
    a uint8 BGR image. ValueError when its size does not add up."""
    import cv2
    import numpy as np
    w, h = int(buf.get_width()), int(buf.get_height())
    mv = memoryview(buf)
    a = np.frombuffer(buf, dtype=np.uint8 if mv.format in ("B", "b", "c") else np.float32)
    if w <= 0 or h <= 0 or a.size != w * h * 4:
        raise ValueError(f"the frame buffer is {a.size} values for {w} x {h} px")
    rgba = a.reshape(h, w, 4)
    u8 = rgba if rgba.dtype == np.uint8 else cv2.convertScaleAbs(rgba, alpha=255.0)
    return cv2.cvtColor(u8, cv2.COLOR_RGBA2BGR)


class WindowShot:
    """The hand-off between the remote API's HTTP threads and the main loop, for one capture at a time.

    HTTP thread: ``picture`` (or ``take``). Main loop: ``wanted``, ``armed``, ``refuse``. DearPyGui's callback
    thread: ``deliver``. One lock; nothing blocks the main loop."""

    def __init__(self, timeout_s: float = 3.0, min_interval_s: float = 1.0, run_interval_s: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        self.timeout_s = float(timeout_s)
        self.min_interval_s = float(min_interval_s)
        self.run_interval_s = float(run_interval_s)
        self._clock = clock
        self._lock = threading.Condition()
        self._seq = 0
        self._req = 0                     # the request waiting for a frame (0: none)
        self._armed = 0                   # the request the main loop has armed a capture for
        self._t_armed: Optional[float] = None
        self._result: Optional[Tuple[str, Any, float]] = None
        self._busy = False
        self._last_start: Optional[float] = None
        self._stats: Dict[str, Any] = {"served": 0, "refused": 0, "busy": 0, "last": None}

        def on_frame(sender, app_data, user_data=None):
            """``dpg.output_frame_buffer``'s callback, on DearPyGui's callback thread: no dpg call here."""
            self.deliver(self._armed, app_data)
        self.on_frame = on_frame          # the same callable for every capture, and never freed:
        _KEPT.append(on_frame)

    # -- main loop ---------------------------------------------------------------------------------------
    def wanted(self) -> int:
        """The request id the main loop should arm a capture for, else 0. A plain read: no lock, no wait."""
        rid = self._req
        return rid if rid and rid != self._armed else 0

    def armed(self, rid: int) -> None:
        """The main loop armed DearPyGui's capture for ``rid``: it is not armed twice."""
        with self._lock:
            if rid == self._req:
                self._armed, self._t_armed = rid, self._clock()

    def refuse(self, rid: int, reason: str) -> None:
        """The main loop cannot capture (no window, minimised, no call): the HTTP thread answers 503 at once."""
        with self._lock:
            if rid == self._req and self._result is None:
                self._result = ("error", str(reason), self._clock())
                self._armed = rid
                self._lock.notify_all()

    # -- DearPyGui's callback thread ------------------------------------------------------------------------
    def deliver(self, rid: int, buffer: Any) -> None:
        """The frame for ``rid`` (kept by reference, converted on the HTTP thread); a late one is dropped."""
        with self._lock:
            if rid == self._req and self._result is None:
                self._result = ("frame", buffer, self._clock())
                self._lock.notify_all()

    # -- HTTP thread ---------------------------------------------------------------------------------------
    def take(self, in_run: bool = False) -> Tuple[int, Any, Dict[str, str]]:
        """One capture: (200, BGR uint8 image, headers) or (429 | 503, {"error": ...}, headers).
        ``in_run``: the app is in RUN -- the longer interval applies."""
        now = self._clock()
        interval = self.run_interval_s if in_run else self.min_interval_s
        with self._lock:
            if self._busy:
                self._stats["busy"] += 1
                return 429, {"error": "a window capture is already in flight; try again in a second",
                             "retry_after": 1}, {"Retry-After": "1"}
            if self._last_start is not None and now - self._last_start < interval:
                wait = interval - (now - self._last_start)
                self._stats["busy"] += 1
                retry = max(1, math.ceil(wait))
                return 429, {"error": f"one window capture every {interval:g} s{' in RUN' if in_run else ''}; "
                                      f"try again in {wait:.1f} s", "retry_after": retry}, {"Retry-After": str(retry)}
            self._busy = True
            self._last_start = now
            self._seq += 1
            rid = self._seq
            self._req, self._result, self._t_armed = rid, None, None
        try:
            with self._lock:
                deadline = now + self.timeout_s
                while self._result is None:
                    left = deadline - self._clock()
                    if left <= 0:
                        break
                    self._lock.wait(left)
                res, t_armed = self._result, self._t_armed
                self._req, self._result = 0, None  # served or given up: a late frame is dropped
            if res is None:
                why = ("the main loop did not serve the capture" if t_armed is None
                       else "no frame was rendered after the capture was armed")
                return self._refused(f"{why} within {self.timeout_s:g} s (a model load, a dialog, a minimised "
                                     "window?); try again")
            kind, payload, t_done = res
            res = None
            if kind == "error":
                return self._refused(payload)
            try:
                img = _bgr_from_buffer(payload)
            except Exception as e:  # noqa: BLE001 - a strange buffer is a 503, never a crash
                return self._refused(f"the frame buffer could not be read: {e}")
            payload = None                         # DearPyGui's buffer is released here
            small = img[::8, ::8]
            if int(small.max()) - int(small.min()) < BLANK_SPREAD:
                return self._refused(f"the frame came back blank ({img.shape[1]} x {img.shape[0]} px of one "
                                     "colour): the window is minimised or hidden")
            wait_ms = (t_done - now) * 1000.0
            last = {"t": round(time.time(), 3), "w": int(img.shape[1]), "h": int(img.shape[0]),
                    "wait_ms": round(wait_ms, 1),
                    "armed_ms": round((t_armed - now) * 1000.0, 1) if t_armed is not None else None}
            with self._lock:
                self._stats["served"] += 1
                self._stats["last"] = last
            return 200, img, {"X-Window-Size": f"{img.shape[1]}x{img.shape[0]}",
                              "X-Capture-Wait-Ms": f"{wait_ms:.0f}"}
        finally:
            with self._lock:
                self._busy = False

    def _refused(self, reason: str) -> Tuple[int, Dict[str, str], Dict[str, str]]:
        with self._lock:
            self._stats["refused"] += 1
        print(f"[RemoteApi] window picture refused: {reason}")
        return 503, {"error": f"no picture of the window: {reason}"}, {}

    def picture(self, query: Dict[str, str], png: bool = False,
                in_run: bool = False) -> Tuple[int, Any, Dict[str, str]]:
        """(200, encoded bytes, headers with Content-Type) or (4xx/5xx, {"error": ...}, headers).

        ``png``: the full-size lossless PNG. Else a JPEG fitted by ``live_watch.snapshot_fitted`` -- the camera
        snapshot's fitter -- from a lossless copy: ``w`` (default the window's full width), ``q`` (default
        ``WINDOW_JPEG_QUALITY``) and ``max_bytes`` (413 when it cannot fit). Bad parameters are a 400 before
        any capture."""
        import cv2
        for k in ("w", "q", "max_bytes"):
            if not png and query.get(k):
                try:
                    int(query[k])
                except ValueError:
                    return 400, {"error": f"bad window picture parameter {k}={query[k]!r}"}, {}
        code, img, headers = self.take(in_run)
        if code != 200:
            return code, img, headers
        if png:
            ok, buf = cv2.imencode(".png", img, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
            if not ok:
                return 500, {"error": "PNG encoding failed"}, {}
            return 200, buf.tobytes(), dict(headers, **{"Content-Type": "image/png"})
        ok, raw = cv2.imencode(".bmp", img)           # lossless and nearly free: the fitter decodes it
        if not ok:
            return 500, {"error": "encoding failed"}, {}
        q = dict(query)
        q.setdefault("w", str(img.shape[1]))
        q.setdefault("q", str(WINDOW_JPEG_QUALITY))
        code, body = snapshot_fitted(raw.tobytes(), q)
        if code != 200:
            return code, body, {}
        return 200, body, dict(headers, **{"Content-Type": "image/jpeg"})

    def status(self) -> Dict[str, Any]:
        """For /api/v1/status (its presence says this build serves window pictures): the rate limits, and how
        the requests went -- served, refused (503), busy (429), the last one's size and wait."""
        with self._lock:
            return {"min_interval_s": self.min_interval_s, "run_interval_s": self.run_interval_s,
                    "timeout_s": self.timeout_s,
                    **{k: (dict(v) if isinstance(v, dict) else v) for k, v in self._stats.items()}}
