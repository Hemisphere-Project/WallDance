"""No full garbage collection in the middle of a show (U3, walldance#t-029 « a long frame every few minutes »).

The defect (swipe session, mgr-ran 26/11, ``tmp_analysis/swipe-1126/live/p6i``): after start-up the process holds
~430,000 long-lived tracked objects (torch, ultralytics, TensorRT, the models).  CPython's full (generation-2)
collection walks them all and stops the frame loop for 94-159 ms: once 15-18 s after start in every run, and
again minutes later (493 s into a 600 s run: a 169 ms frame).  A ``gc.collect()`` between two frames costs
~100 ms on that heap.

The remedy, without leak semantics (no ``gc.freeze``: an object alive at the freeze and sitting in a reference
cycle would never be freed, and models are replaced later):

* once the app is up, the third threshold is raised (``THRESHOLD2``): no full collection starts by itself;
  generations 0 and 1 stay automatic (they walk only the young objects: a few ms);
* the app collects at moments where a pause is invisible -- start-up once the live model is warm (``start``,
  before the first frame; once more after the first live frame if that frame still built a lot), entering STANDBY, after a project load, after a model or engine load, after a
  Calibrate ends (``request``; the main loop serves it between two frames, ``tick``);
* a safety valve for a long RUN without such a moment: when the objects waiting in generation 2 reach
  ``1/VALVE_SHARE`` of the heap measured at the last full collection (CPython's own long-lived ratio: memory
  stays inside the stock interpreter's envelope), one line, and a collection at the next frame where the wall
  is empty -- no track on screen, no identity slot held.  Never while a dancer is tracked.

Every full collection is one ``[GC]`` line with its duration and reason (the app log's INFO: ``print``);
``WD_GC_TRACE=1`` -- and any DEV-slot session (``WD_SLOT=dev``) -- adds each generation-1 collection and a
summary every minute (the cadence of the real app, its GUI and camera thread included).

Lines are queued by the gc callback and printed by ``tick`` between frames, never during a collection: the app
log's tee holds a lock while it writes, and a collection can start inside that write.

Nothing happens at import: replays (``tests/replay.py``) never import this module and keep the interpreter's
defaults, so their numbers do not move.
"""
from __future__ import annotations

import gc as _gc
import os
import threading
import time
from typing import Callable, List, Optional

# gen-1 collections between two automatic full ones.  A gen-1 collection runs every 30-160 s on mgr-ran: a
# million of them is years -- never in practice.  (CPython stores it in a C int: < 2**31.)
THRESHOLD2 = 1_000_000
# The valve: a full collection is due when the objects moved into generation 2 since the last one reach
# 1/VALVE_SHARE of generation 2's size after it -- CPython's own ratio (long_lived_pending >= long_lived_total/4),
# the most memory the stock interpreter lets wait.  Measured on mgr-ran (26/11, x @ 1280, mur2 slot 7, real time):
# 425k objects in gen 2 after the start-up collection -> bound ~106k; a gen-1 collection every ~60-180 s moves
# ~12k there, so a RUN reaches the bound after ~20 min of it (42k at 576 s) and the waiting garbage stays tiny
# (one full collection at 576 s freed 31 objects: one namedtuple class built at run time, no GPU memory back;
# with the swipe tap off, RSS from 60 s to the take's end +1.00 %, the stock interpreter's +0.82 %, 27/11).
# The estimate runs ~10 % above CPython's own counter (gen-1
# untracks some tuples/dicts it does not count as freed): the valve trips a little early, never late.
VALVE_SHARE = 4
VALVE_DEFAULT = 100_000         # the bound until a collection of ours measured the heap
# The first live frame after start-up: if it moved this many objects into gen 2 (the live path finishing its
# set-up: in a replay without the app's warm-up, the first frame alone made ~200k long-lived objects, then
# ~8k per gen-1 collection), one more collection right after it -- nobody can be tracked on a first frame.
WARM_FRAME_MIN = 20_000
TRACE_ENV = "WD_GC_TRACE"
TRACE_EVERY_S = 60.0
NOTES_MAX = 50                  # queued lines kept between two ticks (a burst of collections cannot grow it)


def wall_empty(tracked, processor) -> bool:
    """No track on screen and no identity slot held (every slot ``lost``).  In doubt: not empty."""
    if tracked:
        return False
    states_fn = getattr(processor, "slot_states", None)
    if states_fn is None:
        return True
    try:
        states = states_fn() or []
    except Exception:  # noqa: BLE001 - a readout error must never start a collection
        return False
    return all(s[1] == "lost" for s in states)


class GcPolicy:
    """One per process (``POLICY``); tests build their own with a fake ``gc_module``."""

    def __init__(self, gc_module=_gc, clock: Callable[[], float] = time.perf_counter,
                 log: Callable[[str], None] = print, trace: Optional[bool] = None):
        self.gc = gc_module
        self.clock = clock
        self.log = log
        if trace is None:    # a DEV-slot session (wdslot sets WD_SLOT=dev) is a test session: it traces
            trace = os.environ.get(TRACE_ENV, "") not in ("", "0") or os.environ.get("WD_SLOT") == "dev"
        self.trace = bool(trace)
        self.installed = False
        self.saved_threshold: Optional[tuple] = None
        self.pending = 0              # objects moved into gen 2 since the last full collection (long_lived_pending)
        self.total = 0                # gen 2's size after our last full collection (long_lived_total), 0 = unknown
        self.stats = {0: [0, 0.0, 0.0], 1: [0, 0.0, 0.0], 2: [0, 0.0, 0.0]}   # gen -> [n, total ms, max ms]
        self.collections: List[tuple] = []       # (reason, ms, freed) of each full collection we ran
        self.collected_n = 0                     # full collections we ran this session, never reset: the main
                                                 # loop compares it around a tick (P6, an engine's release)
        self._requests: List[str] = []
        self._valve = False
        self._ours = False
        self._t0 = [0.0, 0.0, 0.0]
        self._examined = 0
        self._notes: List[str] = []
        self._trace_t = 0.0
        self._trace_last = None
        # edges the main loop's hook watches (app_tick)
        self._was_run: Optional[bool] = None
        self._was_calibrating = False
        self._first_frame = False

    # ------------------------------------------------------------------ set-up
    def install(self) -> None:
        """Raise the third threshold (the first two stay) and start timing every collection."""
        if self.installed:
            return
        g0, g1, g2 = self.gc.get_threshold()
        self.saved_threshold = (g0, g1, g2)
        self.gc.set_threshold(g0, g1, THRESHOLD2)
        self.gc.callbacks.append(self._on_gc)
        self.installed = True
        self._trace_t = self.clock()

    def uninstall(self) -> None:
        if not self.installed:
            return
        try:
            self.gc.callbacks.remove(self._on_gc)
        except ValueError:
            pass
        if self.saved_threshold is not None:
            self.gc.set_threshold(*self.saved_threshold)
        self.installed = False
        self._requests.clear()
        self._valve = False

    def start(self) -> None:
        """The app is up and its live model warm (the model load ran its warm-up inference): from now on no full
        collection by itself, and the start-up one now -- before the first RUN frame, not in it."""
        self.install()
        self.collect("start-up")
        self._first_frame = True

    # ------------------------------------------------------------------ moments
    def request(self, reason: str) -> None:
        """A moment where a pause is invisible (STANDBY, a load, Calibrate's end): collect between the next two
        frames that allow it (``tick``).  Requests before ``install`` (or in a replay) are ignored."""
        if self.installed and reason not in self._requests:
            self._requests.append(reason)

    def collect(self, reason: str) -> float:
        """One full collection now; one ``[GC]`` line.  Returns its duration in ms."""
        self._requests.clear()
        self._valve = False
        t0 = self.clock()
        self._ours = True
        try:
            freed = self.gc.collect()
        finally:
            self._ours = False
            self.collected_n += 1                # counted even if it raised: its pause was spent
        ms = (self.clock() - t0) * 1000.0
        try:
            self.total = len(self.gc.get_objects(2))   # every survivor is in gen 2 now: the valve's measure
        except Exception:  # noqa: BLE001
            pass
        self.pending = 0
        self.collections.append((reason, round(ms, 1), freed))
        del self.collections[:-20]
        self._flush_notes()
        self.log(f"[GC] full collection ({reason}): {ms:.1f} ms, {freed} objects freed, {self.total} in gen 2")
        return ms

    def valve_bound(self) -> int:
        return self.total // VALVE_SHARE if self.total > 0 else VALVE_DEFAULT

    def tick(self, in_run: bool, wall_empty: bool, busy: bool = False) -> Optional[str]:
        """Between two frames (the main loop's render stage end).  Collects if one is due and the moment is
        invisible: not in RUN, or the wall empty; never while ``busy`` (a Calibrate or a plate capture is
        taking frames -- its end is a moment of its own).  Returns the reason when it collected."""
        if not self.installed:
            return None
        self._flush_notes()
        if self.trace:
            self._trace_tick()
        if self._first_frame:
            self._first_frame = False
            if self.pending >= WARM_FRAME_MIN:
                self.request("start-up, the first live frame")
        bound = self.valve_bound()
        if not self._valve and self.pending >= bound:
            self._valve = True
            self.log(f"[GC] valve: ~{self.pending} objects waiting in gen 2 (bound {bound}): a full collection "
                     "at the next frame with the wall empty")
        if not (self._requests or self._valve) or busy:
            return None
        if in_run and not wall_empty:
            return None
        reason = ", ".join(self._requests + (["valve"] if self._valve else []))
        self.collect(reason)
        return reason

    # ------------------------------------------------------------------ the gc callback
    def _on_gc(self, phase: str, info: dict) -> None:
        g = int(info.get("generation", 0))
        if phase == "start":
            self._t0[g] = self.clock()
            if g == 1:   # gen 0 + gen 1 are examined; what survives moves to gen 2 (CPython's long_lived_pending)
                self._examined = len(self.gc.get_objects(0)) + len(self.gc.get_objects(1))
            return
        ms = (self.clock() - self._t0[g]) * 1000.0
        st = self.stats[g]
        st[0] += 1
        st[1] += ms
        st[2] = max(st[2], ms)
        collected = int(info.get("collected", 0))
        if g == 1:
            survivors = max(0, self._examined - collected)
            self.pending += survivors
            if self.trace:
                self._note(f"[GC] gen 1: {ms:.2f} ms, {self._examined} examined, {survivors} to gen 2, "
                           f"~{self.pending} waiting there")
        elif g == 2:
            self.pending = 0
            if not self._ours:
                self._note(f"[GC] full collection outside the policy: {ms:.1f} ms, {collected} objects freed "
                           f"(thread {threading.current_thread().name})")

    def _note(self, line: str) -> None:
        if len(self._notes) < NOTES_MAX:
            self._notes.append(line)

    def _flush_notes(self) -> None:
        if self._notes:
            notes, self._notes = self._notes, []
            for line in notes:
                self.log(line)

    def _trace_tick(self) -> None:
        now = self.clock()
        if now - self._trace_t < TRACE_EVERY_S:
            return
        self._trace_t = now
        s = self.stats
        self.log(f"[GC] {TRACE_EVERY_S:.0f} s: gen0 {s[0][0]} (max {s[0][2]:.2f} ms), gen1 {s[1][0]} "
                 f"(max {s[1][2]:.2f} ms), gen2 {s[2][0]} (max {s[2][2]:.1f} ms); ~{self.pending} waiting in "
                 f"gen 2 (bound {self.valve_bound()}), counts {self.gc.get_count()}")


POLICY = GcPolicy()


def start() -> None:
    POLICY.start()


def request(reason: str) -> None:
    POLICY.request(reason)


def app_tick(app, tracked) -> None:
    """The main loop's hook, once per frame at the end of the render stage (runtime/main_loop.py).  Watches the
    two moments the app has no single call site for -- entering STANDBY (RUN -> STANDBY) and a Calibrate's end
    (``_calibrating``/``_calibrating2`` falling) -- then lets the policy collect if one is due.  Never raises."""
    p = POLICY
    if not p.installed:
        return
    try:
        state = getattr(getattr(app, "system_state", None), "name", "")
        in_run = state == "RUN"
        if p._was_run and state == "STANDBY":
            p.request("STANDBY")
        p._was_run = in_run
        cal = getattr(app, "calibration", None)
        calibrating = bool(cal is not None and (getattr(cal, "_calibrating", False)
                                                or getattr(cal, "_calibrating2", False)))
        if p._was_calibrating and not calibrating:
            p.request("Calibrate ended")
        p._was_calibrating = calibrating
        proc = getattr(app, "processor", None)
        busy = calibrating or getattr(proc, "_plate_capture", None) is not None
        p.tick(in_run, wall_empty(tracked, proc) if in_run else True, busy)
    except Exception as e:  # noqa: BLE001 - a collection policy must never stop the loop
        if not getattr(p, "_tick_error_said", False):
            p._tick_error_said = True
            print(f"[GC] policy tick failed (once): {type(e).__name__}: {e}")
