"""What the remote watch tool reads from the live app (S1a, ``extra/wdwatch.py``).

Two small pieces the remote API (``services/remote_api.py``) serves to a dev box on a slow 4G link:

* ``HitRate`` -- the **live hit rate**: the share of the foreground's dancer groups that have a detection box
  on them (« foreground groups with a box »), over a rolling window.  It is scored by the settings swipe's
  referee, ``core.swipe.score_frame`` -- one referee, not two -- imported lazily: in a build without
  ``core/swipe.py`` the figure is **absent** (``snapshot()`` is None, /status has no ``hit_rate``), never 0.
* ``fit_jpeg`` -- the preview JPEG shrunk to a width, a quality and a byte budget, so a snapshot every 30 s
  costs a few KB instead of a full-size frame (never the video stream).

The main loop feeds ``HitRate`` at the /status cadence (~2 Hz, ``MainLoop._tick_remote``) with the
processor's last foreground and raw boxes; only a new frame is scored.  Nothing here may raise into the show.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, Optional, Sequence, Tuple

# (fg, boxes) -> (dancer groups, groups with a box), or None when the frame cannot be refereed
Scorer = Callable[[Any, Sequence[Tuple[float, float, float, float, float]]], Optional[Tuple[int, int]]]


def swipe_scorer() -> Optional[Scorer]:
    """The seam to the referee: ``score_frame(Observation.from_fg(fg), boxes, SwipeParams())``, then
    (groups, covered); None for a frame whose foreground is off or invalid (``from_fg`` returns None).
    Returns None when ``core.swipe`` is not in this build."""
    try:
        from core.swipe import Observation, SwipeParams, score_frame
    except Exception:  # noqa: BLE001 - no referee in this build: the figure is absent
        return None

    def score(fg, boxes):
        obs = Observation.from_fg(fg)
        if obs is None:
            return None
        s = score_frame(obs, list(boxes or ()), SwipeParams())
        return int(s.groups), int(s.covered)

    return score


class HitRate:
    """Rolling « foreground groups with a box »: covered / groups pooled over the last ``window_s``.

    ``feed`` runs on the main loop thread, ``snapshot`` on the API's HTTP threads (one lock).  ``score`` is
    the referee (tests pass a fake); left None, ``swipe_scorer`` is tried once, on the first frame."""

    def __init__(self, window_s: float = 60.0, score: Optional[Scorer] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.window_s = float(window_s)
        self._score = score
        self._loaded = score is not None
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: Deque[Tuple[float, Optional[int], Optional[int]]] = deque()   # (t, groups, covered)
        self._total = [0, 0, 0, 0]          # frames scored, groups, covered, frames skipped (since start)
        self._last_fg: Any = None
        self._last_t: Optional[float] = None
        self._failed = False

    @property
    def available(self) -> bool:
        if not self._loaded:
            self._score, self._loaded = swipe_scorer(), True
        return self._score is not None

    def feed_from(self, processor) -> None:
        """One sample from a ``FrameProcessor``: its ``last_fg`` and ``last_raw_boxes``."""
        if processor is None:
            return
        self.feed(getattr(processor, "last_fg", None), getattr(processor, "last_raw_boxes", None))

    def feed(self, fg, boxes) -> None:
        """Score one frame; the same foreground object twice (no new frame) counts once."""
        if fg is None or fg is self._last_fg or self._failed:
            return
        self._last_fg = fg
        try:
            if not self.available:
                return
            r = self._score(fg, list(boxes or ()))
        except Exception as exc:  # noqa: BLE001 - the figure goes, the show stays
            self._failed = True
            print(f"[HitRate] referee failed ({type(exc).__name__}: {exc}); the live hit rate is off")
            return
        now = self._clock()
        with self._lock:
            if r is None:
                self._rows.append((now, None, None))
                self._total[3] += 1
            else:
                g, c = int(r[0]), int(r[1])
                self._rows.append((now, g, c))
                self._total[0] += 1
                self._total[1] += g
                self._total[2] += c
            self._last_t = now
            self._prune(now)

    def _prune(self, now: float) -> None:
        while self._rows and now - self._rows[0][0] > self.window_s:
            self._rows.popleft()

    def snapshot(self) -> Optional[Dict[str, Any]]:
        """The figure for /status; None (absent) without a referee or after it failed."""
        if self._failed or not self.available:
            return None
        now = self._clock()
        with self._lock:
            self._prune(now)
            rows = [r for r in self._rows if r[1] is not None]
            skipped = len(self._rows) - len(rows)
            groups = sum(r[1] for r in rows)
            covered = sum(r[2] for r in rows)
            total = list(self._total)
            last = self._last_t
        return {"rate": round(covered / groups, 4) if groups else None,
                "groups": groups, "covered": covered, "frames": len(rows), "skipped": skipped,
                "window_s": self.window_s,
                "age_s": round(now - last, 1) if last is not None else None,
                "total": {"frames": total[0], "groups": total[1], "covered": total[2], "skipped": total[3]}}


def fit_jpeg(jpg: bytes, max_w: int = 320, quality: int = 50,
             max_bytes: Optional[int] = None) -> Optional[bytes]:
    """``jpg`` re-encoded no wider than ``max_w`` at ``quality``; while over ``max_bytes`` the quality steps
    down to 30, then the width by a quarter down to 96 px.  None when it cannot fit (or does not decode)."""
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    h, w = img.shape[:2]
    width, q = max(1, min(int(max_w), w)), int(quality)
    best: Optional[bytes] = None
    while True:
        im = img if width >= w else cv2.resize(img, (width, max(1, round(h * width / w))),
                                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", im, [int(cv2.IMWRITE_JPEG_QUALITY), q])
        if ok and (best is None or len(buf) < len(best)):
            best = buf.tobytes()
        if max_bytes is None or (best is not None and len(best) <= max_bytes):
            return best
        if q > 30:
            q = max(30, q - 10)
        elif width > 96:
            width = max(96, int(width * 0.75))
        else:
            return None


def snapshot_fitted(jpg: bytes, query: Dict[str, str]) -> Tuple[int, Any]:
    """/snapshot.jpg?w=&q=&max_bytes= -> (200, jpeg bytes) or (4xx, error dict).  Without any of the three
    parameters the preview is served as it is (``wdremote snapshot``)."""
    if not any(k in query for k in ("w", "q", "max_bytes")):
        return 200, jpg
    try:
        w = max(32, min(int(query.get("w", 320)), 4096))
        q = max(10, min(int(query.get("q", 50)), 95))
        mb = max(1, int(query["max_bytes"])) if query.get("max_bytes") else None
    except ValueError as e:
        return 400, {"error": f"bad snapshot parameters: {e}"}
    try:
        out = fit_jpeg(jpg, w, q, mb)
    except Exception as e:  # noqa: BLE001 - a bad preview must not kill the handler
        return 500, {"error": f"snapshot re-encode failed: {e}"}
    if out is None:
        return 413, {"error": f"the snapshot does not fit in {mb} bytes"}
    return 200, out
