"""Camera stalls (D34, 2026-10-08): stop the hold clocks, keep the OSC stream alive.

The field laptop's IDS camera sometimes delivers no frame for ~1.7 s (0.7-2.9 times a minute at
the landscape crops).  Nothing is processed meanwhile, so OSC went silent and, when the frames came
back, the slot layer's hold clocks had run through the silence: a fresh slot (1.5 s hold) was
dropped at once, an earned one (2 s) had ~0.3 s left to rebind.  An id lost = TouchDesigner drops
its video.

A **stall** is a silence of the live camera longer than ``stall_periods`` frame periods (3 x 50 ms
at 20 fps) and no longer than ``max_s`` (5 s), seen by the main loop: it polled the open camera
past the threshold and got no new frame (``waiting``).  A gap the main loop made itself (a model
switch, a blocking dialog: no poll) is not a stall.  Longer than ``max_s``, or the camera closed /
reconnecting, it is a camera loss and nothing here applies.  Playback never feeds this clock.

* **Camera-live time** (``offset``): the silence beyond the threshold is summed in ``offset``; the
  output stage's hold clocks read ``its clock - offset`` (``FrameProcessor``), so a stall costs a
  hold 3 frame periods, not 1.7 s.  No stall: ``offset`` stays 0.0 and every clock reads exactly
  what it read before.  The motion model (slot velocity, prediction, One-Euro) keeps the output
  clock -- the dancer moved -- and the slot's distances (jump check, re-acquire gates) widen by
  2 h/s x the silence (``IdentitySlots._silent_s``).  The tracker counts frames: a stall never
  aged it.
* **Keep-alive**: during a stall the main loop's polls ask ``keepalive_due``; the processor re-sends
  the last OSC frame (positions frozen, velocity 0, ``/dancer/state`` ``coasting``) on a grid of one
  frame period from the threshold on, until frames resume or the silence reaches ``max_s``.

Pure (no camera, no OSC): times are passed in by the caller, in one clock domain.
"""
from __future__ import annotations

from typing import Optional


class CameraStallClock:
    """Silences of the live camera -> the offset of the camera-live clock + the keep-alive grid."""

    def __init__(self, period_s: float = 0.05, stall_periods: float = 3.0, max_s: float = 5.0,
                 enabled: bool = True):
        self.enabled = bool(enabled)
        self.period_s = float(period_s)
        self.stall_periods = float(stall_periods)
        self.max_s = float(max_s)
        self.offset = 0.0                    # camera-silent time removed from the hold clocks (s)
        self.last_frame: Optional[float] = None   # time of the last live camera frame
        self.frames = 0                      # live camera frames seen (stamps what was emitted)
        self.stalls = 0                      # stalls bridged
        self.last_stall: Optional[dict] = None    # {"silence_s", "removed_s", "resends"} of the last one
        self._next_send: Optional[float] = None
        self._resends = 0
        self._waited = False                 # the main loop saw this silence past the threshold

    @property
    def threshold_s(self) -> float:
        """A silence longer than this is a stall."""
        return self.stall_periods * self.period_s

    def on_frame(self, now: float) -> float:
        """A new live camera frame at ``now``.  Returns the silent time removed from the hold
        clocks (0.0: no stall)."""
        removed = 0.0
        if self.enabled and self.last_frame is not None and self._waited:
            gap = now - self.last_frame
            if self.threshold_s < gap <= self.max_s:
                removed = gap - self.threshold_s
                self.offset += removed
                self.stalls += 1
                self.last_stall = {"silence_s": round(gap, 3), "removed_s": round(removed, 3),
                                   "resends": self._resends}
        self.last_frame = now
        self.frames += 1
        self._next_send = None
        self._resends = 0
        self._waited = False
        return removed

    def forget(self) -> None:
        """The camera is not live (closed, reconnecting, a take playing): the time until its next
        frame is not a stall, and the keep-alive stops."""
        self.last_frame = None
        self._next_send = None
        self._resends = 0
        self._waited = False

    def silence(self, now: float) -> Optional[float]:
        """Seconds since the last live frame (None: no live frame to measure from)."""
        return None if self.last_frame is None else now - self.last_frame

    def stalled(self, now: float) -> bool:
        """The silence at ``now`` is a stall's (past the threshold, within the cap)."""
        s = self.silence(now)
        return self.enabled and s is not None and self.threshold_s < s <= self.max_s

    def waiting(self, now: float) -> bool:
        """The main loop polled the live camera at ``now`` and got no new frame.  True while that
        is a stall (the silence then counts as one when the frames resume)."""
        if not self.stalled(now):
            return False
        self._waited = True
        return True

    def keepalive_due(self, now: float) -> bool:
        """A camera poll with no frame (``waiting``) during a stall, and the next slot of the
        keep-alive grid (the threshold, then every frame period) has come."""
        if not self.waiting(now):
            return False
        if self._next_send is None:
            self._next_send = self.last_frame + self.threshold_s
        return now >= self._next_send

    def keepalive_sent(self, now: float) -> None:
        self._resends += 1
        nxt = (self._next_send if self._next_send is not None else now) + self.period_s
        while nxt <= now:                    # a late tick skips the slots it missed
            nxt += self.period_s
        self._next_send = nxt


def stall_states(states, silence_s: float):
    """``/walldance/dancer/state`` rows ``[(id, state, age_s)]`` re-sent during a stall: every
    emitting slot is ``coasting`` (age = its coast age when it was coasting already, plus the
    silence so far); lost slots are re-sent as they were."""
    out = []
    for sid, state, age in states:
        if state == "lost":
            out.append((sid, state, age))
        else:
            base = float(age) if state == "coasting" else 0.0
            out.append((sid, "coasting", round(base + max(0.0, float(silence_s)), 3)))
    return out
