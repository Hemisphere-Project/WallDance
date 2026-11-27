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
summary every minute (the cadence of the real app, its GUI and camera thread included), and after each full
collection a census of the live objects by type: what grew, and who holds it (``TracedGcPolicy``).

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
        if trace:    # the census after each full collection: a traced session only (TracedGcPolicy, below)
            self.__class__ = TracedGcPolicy
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

    @property
    def collecting(self) -> bool:
        """True from the start of a full collection the policy makes (``collect``) to its end, else False -- also
        after one that raised, and never for one the interpreter started by itself.  A thread that wakes inside
        the collection's tail sees it before ``collected_n`` moves (``runtime.swipe_controller.after_frame_end``,
        the release of an engine)."""
        return self._ours

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


# ---------------------------------------------------------------------------------- the census (trace only)
CENSUS_TOP = 15                 # types listed by growth: since the previous census, and since the first
CENSUS_HOLDERS = 3              # the top growers since the previous census whose holders are looked for
CENSUS_HOLDER_MIN = 100         # ... when they grew by at least this many (a census where nothing grows stays cheap)
CENSUS_SAMPLES = 5              # instances sampled per grower: the newest (the end of gen 2's list)
CENSUS_SCAN = 100_000           # the newest objects scanned for those samples (bounds the scan's time)
CENSUS_LINE_MAX = 299           # characters per census line
CENSUS_NAME_MAX = 100           # characters of a type name
_HOLDER_TYPES = (dict, list, set)   # + collections.deque: a holder whose own holder is looked for


class TracedGcPolicy(GcPolicy):
    """The policy of a traced session (``WD_GC_TRACE=1``, a DEV slot): the same decisions, plus a CENSUS of the
    live objects by type right after each full collection the policy makes -- start-up, its first live frame,
    STANDBY, a load, Calibrate's end, the valve: moments where the collection's pause is already accepted; a RUN
    frame never runs one by itself.  ``GcPolicy.__init__`` makes an instance this class only when it traces, so
    an untraced policy reads, calls and builds nothing of the census.

    One census, as ``[GC] census #N ...`` lines through the policy's log (each under ``CENSUS_LINE_MAX``):
    a summary (live objects, types, growth, its own ms); the top ``CENSUS_TOP`` types by growth since the
    previous census, then since the first (from the third census on); for the top ``CENSUS_HOLDERS`` growers
    since the previous one (by ``CENSUS_HOLDER_MIN`` objects at least), who holds ``CENSUS_SAMPLES`` of their newest instances -- the holder, and when it is a
    dict, list, set or deque, the holder's holder and the attribute or key it sits under (an instance's
    ``__dict__`` is seen through to its owner, as Python 3.12 does without the dict).  Types are counted by
    name (module + qualified name), so a class rebuilt at run time counts as one.

    Bounded work: one pass over the heap to count, a reversed pass over the newest ``CENSUS_SCAN`` objects at
    most to sample, at most three
    ``gc.get_referrers`` walks (all samples of a hop in one walk).  Kept between censuses: the first and the
    previous {name: count} dicts (str -> int: untracked by the collector, no object held alive)."""

    _census_n = 0
    _census_first: Optional[dict] = None
    _census_prev: Optional[dict] = None
    _census_t_first = 0.0
    _census_t_prev = 0.0

    def collect(self, reason: str) -> float:
        ms = GcPolicy.collect(self, reason)
        self._census(reason)
        return ms

    def _census(self, reason: str) -> None:
        t0 = self.clock()
        try:
            lines = self._census_lines(reason, t0)
        except Exception as e:  # noqa: BLE001 - a diagnostic must never stop the app
            lines = [f"[GC] census #{self._census_n + 1} failed: {type(e).__name__}: {e}"]
        for line in lines:
            self.log(line if len(line) <= CENSUS_LINE_MAX else line[:CENSUS_LINE_MAX - 3] + "...")

    def _census_lines(self, reason: str, t0: float) -> List[str]:
        import heapq
        from collections import Counter
        objs = self.gc.get_objects()
        live = len(objs)
        by_type = Counter(map(type, objs))
        counts: dict = {}
        names: dict = {}
        for t, k in by_type.items():
            name = _census_type_name(t)
            names[t] = name
            counts[name] = counts.get(name, 0) + k
        del by_type
        n = self._census_n + 1
        prev, first = self._census_prev, self._census_first
        body: List[str] = []
        picked: dict = {}
        if prev is not None:
            grew = heapq.nlargest(CENSUS_TOP, ((c - prev.get(k, 0), k) for k, c in counts.items()
                                               if c > prev.get(k, 0)))
            body += [f"[GC] census #{n} grew since #{n - 1}: {i}. {k} {counts[k]} {d:+d}"
                     + (f" (since #1 {counts[k] - first.get(k, 0):+d})" if n > 2 else "")
                     for i, (d, k) in enumerate(grew, 1)] or [f"[GC] census #{n}: no type grew since #{n - 1}"]
            if n > 2:
                grew1 = heapq.nlargest(CENSUS_TOP, ((c - first.get(k, 0), k) for k, c in counts.items()
                                                    if c > first.get(k, 0)))
                body += [f"[GC] census #{n} grew since #1: {i}. {k} {counts[k]} {d:+d}"
                         for i, (d, k) in enumerate(grew1, 1)]
            top = [k for d, k in grew[:CENSUS_HOLDERS] if d >= CENSUS_HOLDER_MIN]
            want = {t: k for t, k in names.items() if k in top}
            picked = {k: [] for k in top}
            need = len(top) * CENSUS_SAMPLES
            for o in reversed(objs[-CENSUS_SCAN:]):   # the newest first: a full collection appends the survivors
                k = want.get(type(o))
                if k is not None and len(picked[k]) < CENSUS_SAMPLES:
                    picked[k].append(o)
                    need -= 1
                    if need <= 0:
                        break
            o = None
            del want
        del objs, names
        if picked:
            body += self._census_holders(n, picked)
        now = self.clock()
        if prev is None:
            head = "the baseline of the next censuses"
        else:
            head = (f"{live - sum(prev.values()):+d} since #{n - 1} ({_census_ago(now - self._census_t_prev)})"
                    + (f", {live - sum(first.values()):+d} since #1 ({_census_ago(now - self._census_t_first)})"
                       if n > 2 else ""))
        self._census_n, self._census_prev, self._census_t_prev = n, counts, now
        if first is None:
            self._census_first, self._census_t_first = counts, now
        return [f"[GC] census #{n} ({reason}): {live} live objects, {len(counts)} types; {head}; "
                f"{(self.clock() - t0) * 1000.0:.1f} ms"] + body

    def _census_holders(self, n: int, picked: dict) -> List[str]:
        """Who holds the sampled instances: 2 hops, at most 3 heap walks (one per hop for all samples, and one to
        see an instance's ``__dict__`` through to its owner)."""
        from collections import deque
        holder_types = _HOLDER_TYPES + (deque,)
        samples = [o for lst in picked.values() for o in lst]
        ours = {id(picked), id(samples)} | {id(lst) for lst in picked.values()}
        h1 = _census_referrers(self.gc, samples, ours)                          # walk 1: the holders
        hop2 = [h for h in _census_unique(h1.values())
                if isinstance(h, holder_types) and _census_module(h) is None]
        ours.add(id(hop2))
        h2 = _census_referrers(self.gc, hop2, ours) if hop2 else {}               # walk 2: their holders
        owned = {id(h) for h in hop2 if type(h) is dict and _census_owns(h2.get(id(h)), h)}
        hop3 = [h for h in _census_unique(h2.values())
                if type(h) is dict and _census_module(h) is None and id(h) not in owned]
        ours.add(id(hop3))
        h3 = _census_referrers(self.gc, hop3, ours) if hop3 else {}               # walk 3: an instance dict's owner
        out = []
        for name, lst in picked.items():
            paths: dict = {}
            for o in lst:
                p = _census_path(o, h1, h2, h3, holder_types)
                paths[p] = paths.get(p, 0) + 1
            said = "; ".join(f"{c}x {p}" for p, c in sorted(paths.items(), key=lambda kv: -kv[1]))
            out.append(f"[GC] census #{n} holders of {name} ({len(lst)} newest): {said or 'none sampled'}")
        return out


def _census_type_name(t) -> str:
    mod = getattr(t, "__module__", None)
    qual = getattr(t, "__qualname__", None) or getattr(t, "__name__", "?")
    name = f"{mod}.{qual}" if isinstance(mod, str) else str(qual)
    return name[:CENSUS_NAME_MAX]


def _census_ago(s: float) -> str:
    return f"{int(s // 60)} min {int(s % 60)} s" if s >= 60 else f"{s:.0f} s"


def _census_unique(objs) -> list:
    seen, out = set(), []
    for o in objs:
        if id(o) not in seen:
            seen.add(id(o))
            out.append(o)
    return out


def _census_referrers(gc_module, targets, ours: set) -> dict:
    """One heap walk: {id(target): its first holder} -- not a frame, not the census's own containers."""
    import types
    tgt = tuple(targets)
    ids = {id(t) for t in tgt}
    found: dict = {}
    refs = gc_module.get_referrers(*tgt)
    skip = ours | {id(tgt), id(refs), id(found), id(ids)}
    for r in refs:
        if id(r) in skip or isinstance(r, types.FrameType):
            continue
        for x in gc_module.get_referents(r):
            if id(x) in ids and id(x) not in found:
                found[id(x)] = r
        if len(found) == len(ids):
            break
    del refs
    ours.add(id(found))
    return found


def _census_module(d) -> Optional[str]:
    """The module whose globals ``d`` is, or None."""
    if type(d) is dict and "__builtins__" in d and isinstance(d.get("__name__"), str):
        return d["__name__"]
    return None


def _census_owns(owner, d) -> bool:
    """``d`` is ``owner``'s instance dict (Python < 3.12, or 3.12 once the dict exists)."""
    return owner is not None and type(owner) is not dict and getattr(owner, "__dict__", None) is d


def _census_key(d: dict, target) -> str:
    """The key ``target`` sits under: a str key is a name; another key is data, so only its type is said."""
    for k, v in d.items():
        if v is target:
            return k[:40] if isinstance(k, str) else f"<{type(k).__name__}>"
        if k is target:
            return "(a key)"
    return "?"


def _census_attr(holder, target) -> str:
    """The attribute of ``holder`` that is ``target`` ('' when none is found: a list item, a cell...)."""
    d = getattr(holder, "__dict__", None)
    if d is target:
        return ".__dict__"
    if type(d) is dict:
        k = _census_key(d, target)
        if k != "?":
            return "." + k
    for cls in type(holder).__mro__:
        slots = cls.__dict__.get("__slots__", ())
        for s in ((slots,) if isinstance(slots, str) else slots):
            try:
                if getattr(holder, s) is target:
                    return "." + s
            except Exception:  # noqa: BLE001
                continue
    return ""


def _census_where(holder, target, owners: dict) -> str:
    """'Owner.attr', 'module m.name', "dict[key]" or the holder's type: where ``target`` sits in ``holder``."""
    if holder is None:
        return "? (no holder found)"
    if type(holder) is dict:
        mod = _census_module(holder)
        if mod is not None:
            return f"module {mod}.{_census_key(holder, target)}"
        owner = owners.get(id(holder))
        if _census_owns(owner, holder):
            return f"{_census_type_name(type(owner))}.{_census_key(holder, target)}"
        return f"dict[{_census_key(holder, target)}]"
    return _census_type_name(type(holder)) + _census_attr(holder, target)


def _census_path(o, h1: dict, h2: dict, h3: dict, holder_types) -> str:
    """One sample's holders, 2 hops: '<holder>' or '<holder type> <- <where it sits>'."""
    a = h1.get(id(o))
    if a is None:
        return "? (no holder found)"
    if not isinstance(a, holder_types) or _census_module(a) is not None:
        return _census_where(a, o, h2)
    b = h2.get(id(a))
    if type(a) is dict and _census_owns(b, a):         # an instance's __dict__: the owner's attribute
        return _census_where(a, o, h2)
    inner = f"dict[{_census_key(a, o)}]" if type(a) is dict else _census_type_name(type(a))
    return f"{inner} <- {_census_where(b, a, h3)}"


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
