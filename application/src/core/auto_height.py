"""Automatic person height (replaces the Calib2 "dancers" step for ``person_height_px``).

``person_height_px`` scales the detection size gate (0.3..2.5 x), the tracker's match /
new-track / duplicate gates and the motion-blob gates.  Set by hand or by Calib2 it goes
stale (2026-10-06 night: 45 px for bodies of 120-190 px at the wall -- real dancers dropped by
the size gate in frames with two detections).  Here it is learned while the show runs: every
frame, the box height of each track YOLO fed this frame with a confident FULL skeleton (a head
keypoint and an ankle >= ``kpt_conf``) is a sample; the estimate is the median of the samples of
the last ``window_s`` seconds, moved toward at ``rate`` per second (no jumps), clamped to
[min_px, max_px].  Several dancers at different distances give their median; the 0.3..2.5 x
gate around it is wide (8x), so moderate depth differences stay inside it.
"""
from __future__ import annotations

from collections import deque
from typing import Deque, Iterable, Optional, Tuple

HEAD_KPTS = (0, 1, 2, 3, 4)
ANKLE_KPTS = (15, 16)


class AutoHeight:
    def __init__(self, window_s: float = 10.0, min_samples: int = 20, kpt_conf: float = 0.5,
                 rate: float = 0.5, min_px: float = 20.0, max_px: float = 1500.0):
        self.window_s = window_s
        self.min_samples = min_samples
        self.kpt_conf = kpt_conf
        self.rate = rate                  # fraction of the gap closed per second
        self.min_px, self.max_px = min_px, max_px
        self._samples: Deque[Tuple[float, float]] = deque()
        self._t: Optional[float] = None
        self.value: Optional[float] = None

    def reset(self) -> None:
        self._samples.clear()
        self._t = None
        self.value = None

    def window(self) -> list:
        """The heights sampled over the last ``window_s`` seconds (as of the last update)."""
        return [h for _t, h in self._samples]

    def per_second(self, bin_s: float = 1.0):
        """Every sample of the window, weighted so that each ``bin_s`` holding samples weighs 1 in all:
        (sorted [(height, weight)], number of such seconds).  A dense close-up (20 skeletons a second)
        cannot outvote a sparse far dancer (one a second), and two people seen in the same second share
        it instead of the taller one taking it."""
        bins = {}
        for t, h in self._samples:
            bins.setdefault(int(t // bin_s), []).append(h)
        hw = sorted((h, 1.0 / len(v)) for v in bins.values() for h in v)
        return hw, len(bins)

    def _sample(self, st) -> Optional[float]:
        if getattr(st, "frames_since_skeleton", None) != 0:
            return None
        kc = getattr(st, "confidence", None)
        bbox = getattr(st, "bbox", None)
        if kc is None or bbox is None or len(kc) < 17 or len(bbox) < 4:
            return None
        if not any(float(kc[i]) >= self.kpt_conf for i in HEAD_KPTS):
            return None
        if not any(float(kc[i]) >= self.kpt_conf for i in ANKLE_KPTS):
            return None
        h = float(bbox[3])
        return h if self.min_px <= h <= self.max_px else None

    def update(self, tracks: Iterable, t: float, current: float) -> Optional[float]:
        """Feed this frame's reported tracks (original px); returns the new height to use
        (moved from ``current`` toward the window median), or None while learning."""
        for st in tracks:
            h = self._sample(st)
            if h is not None:
                self._samples.append((t, h))
        while self._samples and t - self._samples[0][0] > self.window_s:
            self._samples.popleft()
        dt = 0.0 if self._t is None else max(0.0, min(1.0, t - self._t))
        self._t = t
        if len(self._samples) < self.min_samples:
            return None
        hs = sorted(h for _t, h in self._samples)
        med = hs[len(hs) // 2]
        base = self.value if self.value is not None else float(current)
        if self.value is None:
            self.value = med                       # first estimate: adopt it (the configured one may be far off)
        else:
            self.value = base + min(1.0, self.rate * dt) * (med - base)
        return self.value


def weighted_quantile(hw, q: float) -> float:
    """``q`` quantile of sorted (value, weight) pairs (AutoHeight.per_second)."""
    total = sum(w for _h, w in hw)
    acc = 0.0
    for h, w in hw:
        acc += w
        if acc >= q * total - 1e-9:
            return h
    return hw[-1][0]
