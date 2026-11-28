"""The autonomous swipe (Plan Cordee P6) beside the live loop: it looks by itself, says, or switches.

``core.swipe_auto`` is the policy (when to look, what to say, when to switch); this is the machinery that feeds it
and acts on its advice, on its own thread, without the operator:

* **the look** (``tick``, every ``look_s`` = 5 s): the kept frames with dancers the previous run did not use,
  counted under the tap's lock (no image copied).  A run needs a FULL pool of them (``SwipeParams.frames`` = 150,
  1 s apart: two and a half minutes of dancing; never the operator's 80 % floor), then the policy's ``due``.  An
  empty wall, STANDBY, a model load, an operator's swipe, a calibration: ``idle`` -- no inference, no state that
  grows (on an empty wall or in STANDBY the second engine may be loaded: see the background run).  In STANDBY no
  look runs at all (the pool is frozen there, the show's output is not live), so no switch is asked there; a run
  in progress when STANDBY begins stops at its next frame and its verdict is dropped;
* **the background run**, much gentler than the operator's swipe: two settings only -- the live one, scored on
  the boxes it already made (``SwipeFrame.boxes``: no inference), and the OTHER MODEL AT THE LIVE NETWORK INPUT,
  kept RESIDENT (under ``ENGINE_LOAD_LOCK`` and without a CUDA graph).  A load or a release stops the loop for
  some 50 ms, so they happen only at moments where that does not show: out of RUN, or in RUN on a wall empty for
  ``SWIPE_AUTO_LOAD_EMPTY_S`` (no track, no identity slot held: the collector's own test) -- and at the operator's
  own click in RUN: the first look after he sets the mode to suggest or auto loads it, whatever is on the wall
  (once).  Out of RUN a load starts only once the app has sat there ``SWIPE_AUTO_LOAD_SETTLE_S`` with nothing
  applied since (a project, a model -- another one at the same input too --, the mode set -- his click out of RUN
  too --, RUN or STANDBY pressed, a new empty-wall plate; a calibration or a capture counts nothing): a load
  cannot be cut short, and a man alone presses RUN when the dancers arrive, often right after he applied the
  project; RUN pressed before: nothing was started, and the load waits for the next such moment.  Never on
  entering RUN, never at a look that falls due later while a dancer is tracked.  So it loads after the mode is
  set, a project or model load, in STANDBY once settled or on an empty wall; a look that falls due without it
  while a dancer is tracked waits for such a moment (``WALL_WAIT``, in ``status``).  It is kept while the mode is
  on (no idle timer) and goes when the mode goes off (at once out of RUN, right after a live frame in RUN), when
  the project, input or TensorRT state changes, and at quit, never inside a run; the readiness report says what
  it holds on the GPU.  Its inferences are paced ``pace_s`` apart right after a live network pass (150 x 0.2 s:
  about 30 s of gentle inference), judged at the show's confidence.  A run without a verdict says nothing (the
  operator's swipe's ``no_verdict``);
* **the advice** (``SwipeAuto.advise``): ``none`` (it keeps the live setting), ``suggest`` (a warning with
  « Apply now » that stays until applied, dismissed, stale or ``SWIPE_VERDICT_MAX_AGE_S`` old -- P5's bound on the
  operator's own verdict), ``apply`` (mode auto, a definite seamless winner: the switch, with a toast; only a
  failure is an alert).  Dismiss means one thing at a time: on a suggestion, « not now » -- the policy puts that
  setting aside (``SWIPE_AUTO_SNOOZE_S`` of the loop clock, or until the scene changes: no suggestion, no switch,
  no look for it), no ban lifted, a fault left to its own Dismiss; with no suggestion on screen, the fault and the
  bans are acknowledged and lifted;
* **the plate**: the referee judges every setting against the empty-wall plate, so a verdict on a plate that is
  not usable (``LiveSetting.plate_ok`` False: no foreground, invalidated, refused for its camera, found stale) is
  worth nothing.  While it is not usable: ``waiting`` -- no run starts, no engine is loaded, no inference, no
  suggestion, no switch; the look says so (``PLATE_WAIT``, in ``status``); a suggestion shown goes; a run in
  progress stops at its next frame and its verdict is dropped.  The same wait while the reference watch
  (``core.reference_watch``, the rehearsal build's) says the plate in use is wrong -- the camera moved, another
  wall, refused by the referee three times...: a confirmed alert, ``WATCH.current(LiveSetting.plate_ref)`` --;
  the look quotes its status (``REFERENCES_WAIT``), the alert and its one instruction stay the watch's (the health
  line).  Any change of the scene (the plate's state among it, the watch's alert, the picture's enhancement:
  either way) starts the policy's verdicts over AND the pool: frames kept before are never scored;
* **the switch** goes through ``runtime.seamless_switch`` (the operator's Apply's own path): the resident model
  becomes the live one between two frames and the old live model the resident one -- no camera close, no modal,
  the tracker and the slots untouched; never while a background inference is in flight on it (the switch's
  ``guard`` is this thread's resident lock).  The policy hears of it (the dwell, a new pool) once the switched-in
  model's first frames ran: a switch rolled back leaves the policy and the pool as they were, plus the ban.  A
  switch that fails is the switch's fault, the one thing of the swipe that belongs to the health line;
* **a resident that cannot be loaded** (a broken engine file, no GPU memory left beside TouchDesigner) is tried
  once: that setting is banned like a failed automatic switch -- no load, no inference, no look run for it, never
  suggested -- until Dismiss, a project load or a switch to it that works; it is said once, as the switch's fault,
  in plain words (the raw error goes to the log), and while it holds, the look says why the swipe waits.

Threading: the looker thread owns the resident model and its inferences; the loop thread owns the live model and
the swap.  Every CUDA call here happens under ENGINE_LOAD_LOCK (a live load waits, and a running look stops at its
next frame when one begins: ``live()`` is None then), on frames the current live model produced; in RUN releases
wait until the current live model has run a frame, out of RUN they happen at once.  The looker thread starts with a
mode that is not off and ends once the mode is off and its engine freed.  Errors end the look, never the app.
"""
from __future__ import annotations

import dataclasses
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.swipe import Candidate, SwipeParams
from core.swipe import score_frame
from core.swipe_auto import (MODE_AUTO, MODE_OFF, NOTE_THE_LINE, AutoParams, SwipeAuto, alike_ms,  # noqa: F401 - PolicyPort
                             apply_released, bar_for, floor_words, headroom, parse_mode, referee_params,
                             released_modes, suggestion_text, unconfirmed_text)
from core.swipe_runner import (SWIPE_MODELS, CandidateRunner, ExclusionCells, install_capture_guards,
                               live_run_from_boxes, no_graph_capture, result_dict, score_runs)
from runtime.seamless_switch import ENGINE_UNREADABLE, Prepared, SeamlessSwitch, _runner_factory, load_failure
from runtime.swipe_controller import PLATE_UNUSABLE, LiveSetting, _plate_watch, make_pacer, say, what_changed

# the empty-wall plate is not usable (LiveSetting.plate_ok False): what the look says, and why a suggestion goes
PLATE_NOT_VALID = "the empty-wall plate is not valid - Calibrate on the empty wall"
PLATE_WAIT = f"the automatic swipe waits: {PLATE_NOT_VALID}"
# the reference watch says the references are wrong (its status quoted): no instruction here, the alert is its own
REFERENCES_WRONG = "the references are not right ({}) - see the health line"
REFERENCES_WAIT = f"the automatic swipe waits: {REFERENCES_WRONG}"
# a run's verdict when STANDBY began after its last frame
STANDBY_DROP = "STANDBY began during the run"
# a look fell due without the second engine while a dancer is tracked: it loads at the next empty wall (or STANDBY)
WALL_WAIT = "the automatic swipe waits for an empty wall to load {}"
# why an automatic switch is only suggested: the switch holds a fault (whose: what lifts it is said by the fault
# itself -- the alert, the readiness row -- since Dismiss on the suggestion means « not now »)
AUTO_FAILED = "the last automatic switch failed"
APPLY_FAILED = "the last Apply failed"
# the last guard: mode auto in a build that does not release it (the app's doors refuse it already)
AUTO_NOT_RELEASED = "Auto: switch is not released in this build"
# the Apply of a suggestion held back in this build (core.config.SWIPE_AUTO_APPLY_RELEASED): what a door that would
# apply one says -- nothing switches, the suggestion stays on the line
APPLY_HELD_BACK = f"a suggestion is advice only in this build: {NOTE_THE_LINE}"
# a suggestion on screen that the looks no longer name goes after this many of them in a row
UNCONFIRMED = 2
NOT_CONFIRMED = "the last looks did not confirm it"
# what the look and the readiness row say while an automatic switch's ban holds (the switch's fault says the same):
# it names the setting (``l @ 1280``), never its engine file -- the file is named only where the instruction is about
# the file (rebuild it)
BAN_WAIT = "the automatic swipe will not try {} again until you press Dismiss"
# ... and a setting whose second engine could not be loaded or run (``_ban``): its ban, said by the look's row and
# the readiness row before the one thing to do -- never by the alert, which says only that (Auto to off)
BAN_UNTIL = "will not try it again until you press Dismiss"
# what the look says while a setting the operator dismissed is put aside (the policy's snooze)
SNOOZE_WAIT = "the automatic swipe waits: {} was dismissed - it looks again at {}"
# the live frames run late (``LiveFrames``): no look starts, one in flight stops -- what the look's row says, with the
# time one in twenty of them takes or more and the floor it is over; it stays until the wait after it is over
# (``_stand_down_late``)
LATE_WAIT = "the automatic swipe waits: the live frames run late (one in twenty takes {} ms or more; the floor is {})"
LATE_WINDOW_S = 3.0       # the recent live frames: those of the last this long ...
LATE_MIN_FRAMES = 20      # ... at least this many, compared at their 95th percentile (one in twenty)
LATE_WARMUP_S = 3.0       # a look's first inferences warm up: the live frames beside them are not counted
# a load started out of RUN that RUN overtook (it cannot be cut short): the first RUN frames waited for it -- said once
LOAD_INTO_RUN = "the second model finished loading as RUN began"


def _setting_name(key: str) -> str:
    """A banned setting as the operator reads it (``Candidate.key`` ``yolo11l-pose@1760x960`` -> its label
    ``l @ 1760 x 960``), the key itself when it cannot be read."""
    model, _, size = key.partition("@")
    first, _, second = size.partition("x")
    try:
        return Candidate(model, int(first), (int(first), int(second)) if second else None).label
    except ValueError:
        return key


@dataclass
class Resident:
    """The other model at the live network input, loaded and kept between looks."""
    candidate: Candidate
    model: Any
    runner: Any               # its PoseRunner
    key: Tuple                # (project, network input, TensorRT) it is valid for
    load_s: float = 0.0
    mb: Optional[int] = None  # what it holds on the GPU, measured around its load (10 MB steps; None: unknown)


def _gpu_used_bytes() -> Optional[int]:
    """This process's GPU memory (NVML), else the device's used memory (other processes count then); None without
    a GPU.  Read around the resident's load: what the second engine costs."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
    except Exception:  # noqa: BLE001 - no torch (CPU tests)
        return None
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(torch.cuda.current_device())
        for p in pynvml.nvmlDeviceGetComputeRunningProcesses(h):
            if p.pid == os.getpid() and p.usedGpuMemory:
                return int(p.usedGpuMemory)
    except Exception:  # noqa: BLE001 - no NVML, or no per-process figure (Windows display driver)
        pass
    try:
        free, total = torch.cuda.mem_get_info()
        return int(total - free)
    except Exception:  # noqa: BLE001
        return None


def other_model(live: Candidate, models=SWIPE_MODELS) -> Optional[Candidate]:
    """The other model at the live setting's own network input (a switch to it is seamless)."""
    if live.model not in models:
        return None
    rest = [m for m in models if m != live.model]
    return Candidate(rest[0], live.imgsz, live.shape) if rest else None


class LiveFrames:
    """The live frames' own times as the looker sees them go by: ``FrameProcessor.timing["total"]``, the frame in
    hand to its OSC out -- the live frame's own time, not the looker's (an inference beside it counts only by what
    it adds to the live frame).  The processor keeps the last frame's alone (a new dict per frame), so the looker
    reads it once per live network pass (``frame_done``: the frame before is over by then) while it waits:
    between two inferences of a look, and for ``LATE_WINDOW_S`` before a look starts.  The looker's thread only."""

    def __init__(self, maxlen: int = 400):
        self.rows: Deque[Tuple[float, float]] = deque(maxlen=maxlen)   # (time.perf_counter() when seen, ms)
        self._last: Any = None            # the timing dict counted last (held: its id is never reused meanwhile)

    def see(self, proc) -> None:
        """The last live frame's time, once."""
        t = getattr(proc, "timing", None)
        if not isinstance(t, dict) or t is self._last:
            return
        self._last = t
        ms = t.get("total")
        if isinstance(ms, (int, float)):
            self.rows.append((time.perf_counter(), float(ms)))

    def wait(self, proc, until: float, stop: threading.Event) -> None:
        """Until ``until`` (``time.perf_counter()``), each live frame counted as it ends; at once when ``stop`` is
        set or the loop runs no network (no live pass in the last 0.25 s: STANDBY, a model load, no frame_done)."""
        ev = getattr(proc, "frame_done", None)
        while ev is not None and not stop.is_set():
            t = float(getattr(proc, "frame_done_t", 0.0) or 0.0)
            now = time.perf_counter()
            if now >= until or now - t > 0.25:
                break
            ev.clear()
            if float(getattr(proc, "frame_done_t", 0.0) or 0.0) <= t:
                ev.wait(min(until - now, 0.2))
            self.see(proc)

    def p95(self, since: float) -> Optional[float]:
        """The 95th percentile (nearest rank: one in twenty takes this or more) of the live frames seen in the last
        ``LATE_WINDOW_S`` and not before ``since``; None with fewer than ``LATE_MIN_FRAMES`` of them."""
        t0 = max(float(since), time.perf_counter() - LATE_WINDOW_S)
        v = sorted(ms for t, ms in self.rows if t >= t0)
        if len(v) < LATE_MIN_FRAMES:
            return None
        return v[int(math.ceil(0.95 * len(v))) - 1]

    def late(self, since: float, budget_ms: float) -> Optional[float]:
        """``p95`` when it is over ``budget_ms``; else None -- under it, too few frames, or no budget (``budget_ms``
        0)."""
        if budget_ms <= 0:
            return None
        p95 = self.p95(since)
        return p95 if p95 is not None and p95 > budget_ms else None


class PolicyPort:
    """Every call into ``core.swipe_auto`` goes through here, in the order the policy asks (REPORT_P6, 66ddeaa): on
    each look ``fresh`` -> the live setting's own scores on them -> ``room`` -> ``due``; on "run" the candidates
    scored on the newest ``run_frames`` fresh frames -> ``ran`` -> ``advise``; ``switched`` once a swap's first
    frames ran (or a change by hand); ``reset`` on a new scene; ``dismissed`` at the operator's Dismiss of a
    suggestion, ``snoozed`` at each look, ``end_snooze`` at a project load, ``turned_off`` when the mode goes off,
    ``put_aside`` when a setting is banned.
    A rename or a new argument costs one edit in this class."""

    def __init__(self, mode: str, params: Optional[AutoParams] = None):
        self.policy = SwipeAuto(parse_mode(mode), params)

    @property
    def mode(self) -> str:
        return self.policy.mode

    @mode.setter
    def mode(self, m: str) -> None:
        self.policy.mode = parse_mode(m)

    @property
    def params(self) -> AutoParams:
        return self.policy.params

    @property
    def run_frames(self) -> int:
        return int(self.policy.params.run_frames)

    @property
    def skipped(self) -> int:
        return int(getattr(self.policy, "skipped", 0))

    def fresh(self, frames: list) -> list:
        """The kept frames no earlier run looked at."""
        return self.policy.fresh(frames)

    def referee_params(self, live_conf: float, base: Optional[SwipeParams]) -> SwipeParams:
        return referee_params(live_conf, base)

    def candidates(self, planned: List[Candidate], live: Candidate) -> List[Candidate]:
        """What a background run tries besides the live setting (the policy keeps the seamless ones)."""
        return [c for c in self.policy.candidates(planned, live) if c != live]

    def room(self, live: Candidate, others: List[Candidate], frames: list, params: SwipeParams) -> Optional[float]:
        """The free pre-filter: the most another setting could gain on these frames (from the live setting's own
        stored boxes, no inference) minus the smallest bar among the candidates; None when unbounded."""
        hr = headroom([score_frame(f.obs, list(f.boxes), params) for f in frames], params)
        if hr is None or not others:
            return None
        return hr - min(bar_for(live, c, params) for c in others)

    def due(self, now: float, frames: list, busy: bool, room: Optional[float]) -> str:
        """run | probe | idle, from the fresh frames (``lost``: the live setting left a dancer bare on it)."""
        lost = sum(1 for f in frames if f.lost)
        newest = max((float(f.t) for f in frames), default=None)
        return self.policy.due(now, lost, len(frames) - lost, busy=busy, room=room, newest_frame_t=newest)

    def ran(self, now: float, probe: bool = False) -> None:
        self.policy.ran(now, probe=probe)

    def speed(self, cand_times: List[float], live_times: List[float]) -> Tuple[Optional[float], Optional[float]]:
        """(the candidate's ms, the live model's ms) for ``advise``'s « slower » rule, measured alike."""
        return alike_ms(cand_times, live_times, self.policy.params.speed_q)

    def advise(self, now: float, result, newest_frame_t: Optional[float], ms: Dict[Candidate, float],
               frame_ms: Optional[float] = None):
        return self.policy.advise(now, result, newest_frame_t=newest_frame_t, ms=ms, frame_ms=frame_ms)

    def switched(self, now: float, old: Candidate, new: Candidate) -> None:
        self.policy.switched(now, old, new)

    def reset(self) -> None:
        self.policy.reset()

    def dismissed(self, now: float, winner: Candidate, for_s: float) -> None:
        """The operator's Dismiss of a suggestion of ``winner``: not now (the streak ends, a snooze)."""
        self.policy.dismissed(now, winner, for_s)

    def snoozed(self, now: float) -> Optional[Candidate]:
        return self.policy.snoozed(now)

    def end_snooze(self) -> None:
        self.policy.end_snooze()

    def turned_off(self) -> None:
        """The mode went off: the snooze and the streak end (a mode set again starts fresh)."""
        self.policy.turned_off()

    def put_aside(self) -> None:
        """A setting was banned (a failed load, run or automatic switch): the streak ends -- once Dismiss lifts the
        ban, a fresh pair of looks is needed before it is suggested again."""
        self.policy._end_streak()

    def unconfirmed(self) -> None:
        """A look did not confirm the suggestion on screen: the next look as soon as a full pool is kept."""
        self.policy.unconfirmed()

    def withdrawn(self) -> None:
        """The comparison a suggestion (or a streak) was made on no longer holds: the streak ends."""
        self.policy.withdrawn()

    def failed(self, now: float, newest_frame_t: Optional[float] = None) -> None:
        """A look raised: the looks back off as after a « keep » (``newest_frame_t``: ``SwipeAuto.failed``)."""
        self.policy.failed(now, newest_frame_t)


class BackgroundSwipe:
    def __init__(self, *, processor: Callable[[], Any], live: Callable[[], Optional[LiveSetting]],
                 engine_path: Callable[[Candidate], str], busy: Callable[[], Optional[str]],
                 switch: SeamlessSwitch,
                 notify: Callable[[str, Dict[str, Any]], None] = lambda kind, payload: None,
                 mode: str = MODE_OFF, auto_params: Optional[AutoParams] = None,
                 params: Optional[SwipeParams] = None, look_s: Optional[float] = None,
                 pace_s: Optional[float] = None, models=SWIPE_MODELS,
                 runner_factory: Callable[[Any], Any] = _runner_factory,
                 engine_exists: Callable[[str], bool] = os.path.exists,
                 engine_lock=None, wall: Callable[[], float] = time.time, referee=None,
                 in_run: Callable[[], bool] = lambda: False, wall_empty: Callable[[], bool] = lambda: True,
                 empty_s: Optional[float] = None, gpu_used: Callable[[], Optional[int]] = _gpu_used_bytes,
                 settle_s: Optional[float] = None,
                 snooze_s: Optional[float] = None, max_age_s: Optional[float] = None,
                 standby: Callable[[], bool] = lambda: False, frame_budget_ms: Optional[float] = None):
        from core.config import (SWIPE_AUTO_FRAME_FLOOR_MS, SWIPE_AUTO_LOAD_EMPTY_S, SWIPE_AUTO_LOAD_SETTLE_S,
                                 SWIPE_AUTO_LOOK_S,
                                 SWIPE_AUTO_PACE_S, SWIPE_AUTO_SNOOZE_S, SWIPE_VERDICT_MAX_AGE_S)
        self.processor = processor
        self.live = live
        self.engine_path = engine_path
        self.busy = busy
        self.switch = switch                  # the seamless switch (the operator's Apply's): it swaps, holds THE fault
        switch.on_switched = self._on_switched
        switch.on_confirmed = self._on_switch_confirmed
        switch.on_failed = self._on_switch_failed
        switch.on_superseded = self._on_switch_superseded
        self.notify = notify
        self.auto = PolicyPort(mode, auto_params)
        self.params = params
        self.look_s = float(SWIPE_AUTO_LOOK_S if look_s is None else look_s)
        self.pace_s = float(SWIPE_AUTO_PACE_S if pace_s is None else pace_s)
        self.empty_s = float(SWIPE_AUTO_LOAD_EMPTY_S if empty_s is None else empty_s)
        # out of RUN, a load starts only once the app has sat there this long with nothing applied since
        self.settle_s = float(SWIPE_AUTO_LOAD_SETTLE_S if settle_s is None else settle_s)
        self.snooze_s = float(SWIPE_AUTO_SNOOZE_S if snooze_s is None else snooze_s)    # Dismiss: not now, this long
        self.max_age_s = float(SWIPE_VERDICT_MAX_AGE_S if max_age_s is None else max_age_s)  # a suggestion's age
        # the live frames run late over it: no look -- the owner's 15 fps floor (0: no budget -- the bench's gate
        # runs on a shared GPU)
        self.frame_budget_ms = float(SWIPE_AUTO_FRAME_FLOOR_MS if frame_budget_ms is None else frame_budget_ms)
        self.in_run = in_run              # the app is in RUN (the show's output is live)
        self.standby = standby            # the app is in STANDBY: no look (out of RUN alone is when loads may go)
        self.wall_empty = wall_empty      # no track on screen, no identity slot held (core.gc_policy.wall_empty)
        self.gpu_used = gpu_used          # bytes on the GPU, read around the resident's load
        self.models = tuple(models)
        self.runner_factory = runner_factory
        self.engine_exists = engine_exists
        if engine_lock is None:
            try:
                from core.model_manager import ENGINE_LOAD_LOCK as engine_lock
            except Exception:  # noqa: BLE001 - no ultralytics (CPU tests)
                engine_lock = threading.RLock()
        self.engine_lock = engine_lock
        self.wall = wall
        self.referee = referee      # the plate watch's word (RefereeReport, fix6): the operator's swipe's, shared
        # settings banned -- an automatic switch to them failed, or their resident could not be loaded or run: no
        # look at all for them (never suggested, never switched to by itself) until Dismiss, a project load or a
        # switch to it that works (the operator's Apply, confirmed); key -> engine file
        self._banned: Dict[str, str] = {}
        # ... of them, those whose resident could not be loaded or run: what the look says (else ``BAN_WAIT``)
        self._unloadable: Dict[str, str] = {}
        self._snooze_says: Optional[str] = None    # what the look says while a dismissed setting is put aside
        self._fault_why: Optional[Tuple[str, str]] = None   # (the switch's fault, why it holds an apply back)
        self.resident: Optional[Resident] = None
        self._mb_seen: Dict[str, int] = {}         # a second model's GPU memory, measured at its last load (label)
        self._resident_lock = switch.guard         # held while the looker infers on the resident: the swap waits
        self._scene: Optional[Tuple] = None
        self._exclusion: Optional[Tuple] = None    # the excluded cells at the last look: a change ends the streak
        self._fresh_after: Optional[float] = None  # loop clock of the last scene change: only frames kept after it
        self._last_live: Optional[Candidate] = None
        self._expect: Optional[Candidate] = None   # the setting a switch moved to (not a manual change)
        self.running = False                       # a background run is in progress
        self.state = "off" if self.auto.mode == MODE_OFF else "watching"
        self.suggestion: Optional[Dict[str, Any]] = None
        self.last_look: Optional[Dict[str, Any]] = None
        self.last_advice: Optional[Dict[str, Any]] = None
        self.last_counts: Tuple[int, int] = (0, 0)      # fresh frames with dancers, of which lost
        self._quiet = True                              # this look's moment: a load or a release would not show
        self._mode_set = False                          # the mode was just set (not off): the next look's moment
        self._click = False                             # ... taken by a look, kept until a look tries the load
        self._click_look = False                        # this look is that one: a load moment whatever else is true
        self._quiet_since: Optional[float] = None       # loop clock since when the wall is seen empty in RUN
        self._was_run = False                           # RUN at the last look (entering RUN starts the count again)
        self._set_out = False                           # the mode was last set out of RUN
        self._click_out = False                         # the click kept was made out of RUN: it lapses in RUN
        self._settle_again = False                      # a mode set, RUN or STANDBY pressed: the settle counts again
        self._out_since: Optional[float] = None         # loop clock since when the app sits out of RUN, nothing applied
        self._settle_key: Optional[Tuple] = None        # ... on this live setting (another project, model, plate:
                                                        # again -- ``_settle_key_of``)
        self._next_s: Optional[float] = None            # the next look sooner than look_s (an empty wall counted)
        self._wall_wait: Optional[str] = None           # a look due waits for an empty wall: what its row says
        self._ref_wait: Optional[str] = None            # the look waits on the reference watch: what its row says
        self.frames_ms = LiveFrames()                   # the live frames' own times, as the looker sees them go by
        self._late_until: Optional[float] = None        # loop clock until which no look starts: the frames ran late
        self._late_says: Optional[str] = None           # ... what the look's row says meanwhile
        self._late_n = 0                                # looks stood down in a row for late frames
        self._frame_ms_before: Optional[float] = None   # the live frames' 95th percentile before the last look
        self._look_failures = 0                         # looks that raised, in a row (``LOOK_FAILURES``)
        self.history: Deque[Dict[str, Any]] = deque(maxlen=200)   # looks, advices, switches (bounded)
        self.inferences = 0                        # candidate inferences so far
        self.ticks = 0
        self.tick_ms: Deque[float] = deque(maxlen=500)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thread_lock = threading.Lock()       # the looker ending by itself (mode off) against start()

    # ------------------------------------------------------------------ mode, thread
    @property
    def mode(self) -> str:
        return self.auto.mode

    def set_mode(self, mode: str, click: bool = True) -> str:
        """Off ends the automatic swipe for the session: the looker frees its engine (at once out of RUN, right
        after a live frame in RUN) and its thread ends.  Another mode wakes the looker -- ``start`` starts it
        again.  The operator's click (``click``) in RUN: the engine loads at that next look, whatever is on the
        wall -- his own act is the moment, once, a snooze or not.  His click out of RUN: the moment comes once the
        app has sat there ``settle_s`` from it, and goes if RUN begins first.  A project's mode (``click`` False:
        its load, a new project) is no moment of its own: out of RUN the settle counts from it, in RUN the wall must
        be empty.  Later, only where a pause does not show.  Off ends what the operator said to this mode and what
        it saw: the suggestion, a Dismiss's snooze, the streak -- a mode set again starts fresh."""
        m = parse_mode(mode)
        self.auto.mode = m
        self._set_out = not self.in_run()                 # (before the click: the looker reads them in that order)
        self._mode_set = m != MODE_OFF and bool(click)    # the next look may load: the operator chose this pause
        self._settle_again = True                         # out of RUN, the settle counts from now
        if m == MODE_OFF:
            self._clear_suggestion("the automatic swipe is off")
            self.auto.turned_off()
            self._end_snooze_row()
            self.state = "off"
            self._end_wall_wait()
        elif self.state == "off":
            self.state = "watching"
        self._wake.set()                  # the next look now: his click in RUN is the moment, out of RUN the count
        return m

    def state_changed(self) -> None:
        """RUN or STANDBY pressed: out of RUN, the settle before a load counts again from the next look."""
        self._settle_again = True

    def start(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.set()              # the first look at once: the mode was just set
            self._thread = threading.Thread(target=self._loop, name="SwipeAuto", daemon=True)
            self._thread.start()

    def stop(self, timeout: Optional[float] = 8.0) -> None:
        """At quit (``_cb_quit``, on the loop thread, before the switch's ``stop``): the looker ends -- a load in
        progress ends first (it cannot be cut short: about 3 s cold, more beside a busy GPU), and what it loaded is
        dropped there, never kept (``_ensure_resident``) -- then the resident goes here, under the engine lock,
        unpaced: no frame is coming, and nothing here waits for a frame's end.  At most ``timeout`` in all (8 s,
        as the switch's own: a cold load ends inside it, before the process tears CUDA down); past it nothing is
        freed (said in the log)."""
        self._stop.set()
        self._wake.set()
        t0 = time.perf_counter()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)
            if t.is_alive():
                say("[SwipeAuto] still in a look at quit: nothing freed")
                return
        left = -1 if timeout is None else max(0.5, timeout - (time.perf_counter() - t0))
        if not self._release_now("quit", left):
            say("[SwipeAuto] the engine lock stayed taken at quit: nothing freed")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.look_s if self._next_s is None else self._next_s)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - a look's error ends the look, never the app
                self.running = False
                say(f"[SwipeAuto] look failed: {type(exc).__name__}: {exc}")
            with self._thread_lock:
                if self.auto.mode == MODE_OFF and self.resident is None:
                    self._thread = None   # off, nothing held: nothing of the automatic swipe runs any more
                    return

    # ------------------------------------------------------------------ the look
    def _record(self, kind: str, **data) -> Dict[str, Any]:
        row = {"kind": kind, "wall": self.wall(), **data}
        self.history.append(row)
        return row

    def _free_pending(self, live: Optional[LiveSetting]) -> None:
        """A resident that no longer fits (mode off, another setting, another project, a model load) goes: out of
        RUN at once (no second engine waits for the first RUN frame beside a reloaded model), in RUN in the switch's
        safe window (right after a live frame, under the engine lock), with the models the switch frees."""
        if not (self.resident is not None and self._resident_invalid(live)):
            return
        if not self.in_run():
            self._release_now("standby")  # a live load holding the engine lock: the next look tries again
            self.switch.free_pending()    # what the switch frees goes in the same pass, as in RUN
            return

        def drop_resident() -> None:
            if self.resident is not None and self._resident_invalid(self.live()):
                dropped = self.resident.candidate.label
                self.resident = None
                self._record("release", resident=dropped)
        self.switch.free_pending(drop_resident)

    def _resident_invalid(self, live: Optional[LiveSetting]) -> bool:
        r = self.resident
        if r is None:
            return False
        if self.auto.mode == MODE_OFF or live is None:
            return True                       # (no idle timer: while the mode is on and it fits, it stays)
        return r.candidate != other_model(live.candidate, self.models) or r.key != self._resident_key(live)

    def _release_now(self, purpose: str, timeout: Optional[float] = None) -> bool:
        """The resident freed on this thread now, under the engine lock (its destructor frees the engine here):
        out of RUN, at quit, or replaced at a moment where that does not show.  The lock taken by someone else
        (a live load): False, nothing freed."""
        if self.resident is None:
            return True
        if not (self.engine_lock.acquire(timeout=timeout) if timeout else self.engine_lock.acquire(blocking=False)):
            return False
        try:
            r, self.resident = self.resident, None
            if r is not None:
                self._record("release", resident=r.candidate.label, purpose=purpose)
            del r
        finally:
            self.engine_lock.release()
        return True

    def _observe(self, proc, live: Optional[LiveSetting] = None) -> None:
        """This look's moment (``_quiet``): a load here does not show -- out of RUN once the app has sat there
        ``settle_s`` on this live setting with nothing applied since (a mode set, RUN or STANDBY pressed, a model
        loading, another project, model or setting, a new empty-wall plate; a look that finds the app busy -- a
        calibration, a capture -- counts nothing: the settle starts at the first look after it), or in RUN on a
        wall seen empty (no track, no identity slot held) for ``empty_s``, counted from the first look that saw it
        empty and never from before RUN began -- or it is the first look after the operator set the mode in RUN
        (``set_mode``), whatever is on the wall: his own click, kept until a look tries the load
        (``_load_when_quiet``) -- a look that cannot (the plate pending, a model loading, busy) leaves it to the
        next.  His click out of RUN is a moment once settled, and lapses in RUN."""
        self._next_s = None
        mode_set, self._mode_set = self._mode_set, False
        if mode_set:
            self._click, self._click_out = True, self._set_out
        run = bool(self.in_run())
        entered, self._was_run = run and not self._was_run, run
        key = self._settle_key_of(live) if live is not None else None
        again, self._settle_again = self._settle_again, False
        # busy (a calibration, an empty-wall capture, a model load, an Apply, his swipe, a project switch, the
        # switch's swap): never counted -- the settle starts at the first look that finds the app free again
        busy = not run and bool(self.busy() or self.switch.busy())
        if run or again or busy or key is None or key != self._settle_key:
            self._out_since = None            # the settle counts again (out of RUN, from the next look with a model)
        self._settle_key = key
        if not run:
            self._quiet_since = None
            left = self.settle_s
            if key is not None and not busy:
                now = float(proc.loop_clock())
                if self._out_since is None:
                    self._out_since = now
                left = self._out_since + self.settle_s - now
            self._quiet = left <= 1e-6
            self._click_look = self._click and self._quiet
            if not self._quiet and key is not None:
                self._next_s = min(self.look_s, max(0.25, left))   # look again once the app has sat there so long
            return
        if self._click and self._click_out:
            self._click = False               # his click out of RUN: RUN began before it settled -- the next moment
        self._click_look = self._click
        try:
            empty = bool(self.wall_empty())
        except Exception:  # noqa: BLE001 - in doubt, a dancer is there
            empty = False
        now = float(proc.loop_clock())
        if not empty:
            self._quiet_since = None
        elif self._quiet_since is None or entered:
            self._quiet_since = now
        self._quiet = self._click or (empty and now - self._quiet_since >= self.empty_s - 1e-6)

    def _wanted(self, live: LiveSetting, other: Optional[Candidate]) -> Optional[Candidate]:
        """The second engine a look would run (the other model at the live input), or None: the mode off, the live
        model on PyTorch, no other model, or its engine not built here."""
        if self.auto.mode == MODE_OFF or not live.trt or other is None:
            return None
        cands = self.auto.candidates([other], live.candidate)
        if not cands or not self.engine_exists(self.engine_path(cands[0])):
            return None
        return cands[0]

    def _fits(self, live: LiveSetting, cand: Candidate) -> bool:
        r = self.resident
        return r is not None and r.candidate == cand and r.key == self._resident_key(live)

    def _end_wall_wait(self) -> None:
        """The wait for an empty wall is over: its row leaves /status."""
        if self._wall_wait is not None and self.last_look is not None \
                and self.last_look.get("outcome") == self._wall_wait:
            self.last_look = None
        self._wall_wait = None

    def _load_when_quiet(self, proc, live: LiveSetting, other: Optional[Candidate], now: float) -> Optional[str]:
        """The second engine made ready for the looks, at a moment where its load does not show (``_quiet``); else
        nothing now (a look that falls due waits: ``WALL_WAIT``).  "banned" when it could not be loaded (said once,
        as the switch's fault, like a run's failed load), "off" when the app quit meanwhile (what loaded went at
        once), else None."""
        want = self._wanted(live, other)
        if want is None:
            self._click = False               # the click's moment: nothing to load for this setting
            self._end_wall_wait()             # nothing to load for this setting: nothing waits
            return None
        if self._fits(live, want):
            self._click = False               # ... or it is loaded already
            return None
        if not self._quiet:
            if self._quiet_since is not None:      # an empty wall being counted: look again once it has lasted
                left = self._quiet_since + self.empty_s - float(proc.loop_clock())
                self._next_s = min(self.look_s, max(0.25, left))
            return None
        install_capture_guards()
        runner = self.runner_factory(proc)
        with self.engine_lock, no_graph_capture():
            if self._cancelled(live):
                return None                   # a load or a switch began meanwhile: the next look (the click kept)
            self._click = False               # the click's moment is used here: the load is tried
            failed = self._ensure_resident(proc, live, want, runner)
        if self._stop.is_set():
            return "off"                      # the app quits: nothing loaded is kept, nothing said
        if failed:
            wait, fault = failed
            if fault is None:
                return None                   # RUN began before the load started: no load, the next moment
            self.last_look = self._record("look", look="none", t=now, frames=0, seconds=0.0, outcome=wait)
            if fault:
                self._fault_why = (fault, f"the automatic swipe could not load {want.label}")
                self.switch.fail(fault)
            return "banned"
        self._end_wall_wait()
        return None

    @staticmethod
    def _resident_key(live: LiveSetting) -> Tuple:
        c = live.candidate
        return (live.project, int(c.imgsz), tuple(c.shape) if c.shape else None, bool(live.trt))

    @classmethod
    def _settle_key_of(cls, live: LiveSetting) -> Tuple:
        """What the settle before a load sits on: the resident's key, and the live model itself -- another model
        at the same input, or the same one loaded again (``model_id``: any reload, a swap), counts the settle
        again, even when no look saw its load -- and the empty-wall plate (a calibration or a capture made
        between two looks: a new plate)."""
        return cls._resident_key(live), live.candidate, live.model_id, live.plate

    def tick(self) -> str:
        """One look.  Returns what it did: off | loading | waiting (the plate is not usable, the reference watch
        says it is wrong, or a look due waits for an empty wall to load the other model) | idle (STANDBY among
        it) | busy | banned (the other model could not be loaded or run, or an automatic switch to it failed) |
        snoozed (the operator dismissed it a moment ago) | run | probe | nothing-to-compare.  First, a suggestion
        too old goes, whatever the wall (an empty wall runs no look)."""
        t0 = time.perf_counter()
        try:
            self.withdraw_stale()
            return self._tick()
        finally:
            self.ticks += 1
            self.tick_ms.append((time.perf_counter() - t0) * 1000.0)

    def _tick(self) -> str:
        proc = self.processor()
        plate_pending = bool(getattr(proc, "plate_pending", False))   # read before the live setting: a load between
        live = self.live()
        self._observe(proc, live)
        self._free_pending(live)
        if self.auto.mode == MODE_OFF:
            self.state = "off"
            return "off"
        if live is None:
            self._next_s = min(self.look_s, 0.5)    # a model loads: its second engine follows as soon as it is live
            return "loading"
        wrong = self._references_wrong(live)      # the watch's status while it says the references are wrong
        scene = (live.project, live.roi, live.plate, live.plate_ok, live.frame, live.enhance, wrong is not None)
        if scene != self._scene:
            if self._scene is not None:
                # nothing learned so far describes this scene: the policy's verdicts start over, and so does the
                # pool -- frames kept before now are never scored (those kept on a plate that was not usable or
                # that the watch called wrong, on another picture, or that a run already used: the policy's
                # memory of them is gone)
                self.auto.reset()
                self._fresh_after = float(proc.loop_clock())
                self._clear_suggestion(PLATE_NOT_VALID if not live.plate_ok and not plate_pending else
                                       REFERENCES_WRONG.format(wrong) if wrong else
                                       "the scene changed (project, ROI, empty-wall plate, camera or picture "
                                       "enhancement)")
            self._scene = scene
        if self._last_live is not None and live.candidate != self._last_live and live.candidate != self._expect:
            self.auto.switched(proc.loop_clock(), self._last_live, live.candidate)       # by hand: the dwell too
            self._record("manual-change", old=self._last_live.label, new=live.candidate.label)
        if live.candidate == self._expect:
            self._expect = None
        self._last_live = live.candidate
        if self.suggestion is not None and self.suggestion.get("live_key") != live.key:
            # judged on another comparison (the excluded cells, the live model reloaded): it goes, and the streak
            # with it -- a new suggestion needs two looks in a row on this one (was: one look, and a new toast)
            self._clear_suggestion("the setting changed since the suggestion")
            self.auto.withdrawn()
        if self._exclusion is not None and live.exclusion != self._exclusion:
            self.auto.withdrawn()             # the same without a suggestion: a verdict seen once does not count
        self._exclusion = live.exclusion
        if not live.plate_ok:
            # a verdict on this plate is worth nothing: no frame read, no engine, no inference, nothing said
            self.state = "waiting"
            self.last_counts = (0, 0)
            if plate_pending:
                return "waiting"              # the plate is not loaded yet (start-up, a project load): not known
            if self.last_look is None or self.last_look.get("outcome") != PLATE_WAIT:
                self.last_look = self._record("look", look="none", t=float(proc.loop_clock()), frames=0,
                                              seconds=0.0, outcome=PLATE_WAIT)
            return "waiting"
        if self.last_look is not None and self.last_look.get("outcome") == PLATE_WAIT:
            self.last_look = None             # the plate is usable again: the wait is over, no look since
        if wrong:
            # the watch says this plate is wrong (moved, another wall, refused): the same wait as the plate's -- no
            # frame read, no engine, no inference; said once, quoting the watch, whose alert it is
            self.state = "waiting"
            self.last_counts = (0, 0)
            self._ref_wait = REFERENCES_WAIT.format(wrong)
            if self.last_look is None or self.last_look.get("outcome") != self._ref_wait:
                self.last_look = self._record("look", look="none", t=float(proc.loop_clock()), frames=0,
                                              seconds=0.0, outcome=self._ref_wait)
            return "waiting"
        if self._ref_wait is not None:
            if self.last_look is not None and self.last_look.get("outcome") == self._ref_wait:
                self.last_look = None         # the watch no longer says so: the wait is over, no look since
            self._ref_wait = None
        frames, now = proc.swipe_fresh(self._fresh_after)
        fresh = self.auto.fresh(frames)
        self.last_counts = (len(fresh), sum(1 for f in fresh if f.lost))
        self.state = "watching"
        if self._late_until is not None and (now >= self._late_until or self._click_look):
            self._end_late_wait()             # over (the operator's click: at once, and the series starts over)
        if self._click_look:
            self._late_n = 0
        why = self.busy() or self.switch.busy()
        if why:
            return "busy"
        other = other_model(live.candidate, self.models)
        wait, did = self._stands_down(other, now)
        if wait and did == "snoozed" and self._click_look:
            # the operator's click is a load moment whatever else is true: the setting put aside loads now, so the
            # look after the snooze does not wait for an empty wall; no look, no inference for it until then
            got = self._load_when_quiet(proc, live, other, now)
            if got:
                return got
        if wait:
            # banned or dismissed a moment ago: no inference, no look, no load but the click's -- the row says why
            if self.last_look is None or self.last_look.get("outcome") != wait:
                self.last_look = self._record("look", look="none", t=now, frames=0, seconds=0.0, outcome=wait)
            return did
        got = self._load_when_quiet(proc, live, other, now)
        if got:
            return got                        # banned: it could not be loaded just now, said once as the switch's
                                              # fault; off: the app quits
        if self.standby():
            return "idle"                     # STANDBY: the second engine may load (above), no look, no switch
        if len(fresh) < self.auto.run_frames:
            self._end_wall_wait()             # no look is due any more
            return "idle"                     # a full pool of fresh frames with dancers first: nothing scored
        cands = self.auto.candidates([other] if other is not None else [], live.candidate)
        why_not = ("the live model runs PyTorch" if not live.trt else
                   "no other model for this setting" if not cands else
                   f"{cands[0].label} not tried: its engine is not built on this machine ({cands[0].engine_file})"
                   if not self.engine_exists(self.engine_path(cands[0])) else None)
        if why_not:
            if self.last_look is None or self.last_look.get("outcome") != why_not:
                self.last_look = self._record("look", look="none", t=now, frames=0, seconds=0.0, outcome=why_not)
            return "nothing-to-compare"
        fits = self._fits(live, cands[0])
        wall_wait = WALL_WAIT.format(cands[0].label)
        if fits or self._wall_wait != wall_wait:
            self._end_wall_wait()
        elif self._wall_wait is not None:     # still no moment to load it: the look waits, the policy not asked again
            if self.last_look is None or self.last_look.get("outcome") != wall_wait:
                self.last_look = self._record("look", look="none", t=now, frames=len(fresh), seconds=0.0,
                                              outcome=wall_wait)
            return "waiting"
        if self._late_until is not None:
            # the live frames ran late beside a look: no look until the wait is over -- its row says so
            if self.last_look is None or self.last_look.get("outcome") != self._late_says:
                self.last_look = self._record("look", look="none", t=now, frames=len(fresh), seconds=0.0,
                                              outcome=self._late_says)
            return "waiting"
        sp = self._run_params(proc)
        skipped = self.auto.skipped
        kind = self.auto.due(now, fresh, busy=False, room=self.auto.room(live.candidate, cands, fresh, sp))
        if kind == "idle":
            if self.auto.skipped > skipped:   # the pre-filter: the live setting leaves no room for the bar
                self.last_look = self._record("look", look="skipped", t=now, frames=len(fresh), seconds=0.0,
                                              outcome="no room for a gain over the bar: no run, no inference")
                self._unconfirmed(None)       # ... nor for the suggestion on screen: it does not confirm it
            return "idle"
        if not fits:
            # due, and no second engine while a dancer is tracked: no load now (it would show), the next empty wall
            self._wall_wait = wall_wait
            self.last_look = self._record("look", look="none", t=now, frames=len(fresh), seconds=0.0,
                                          outcome=wall_wait)
            return "waiting"
        late = self._late_before(proc)
        if late is not None:
            # the live frames run late already: a look would only add to them (its frames stay fresh)
            self.last_look = self._record("look", look="none", t=now, frames=len(fresh), seconds=0.0,
                                          outcome=self._stand_down_late(late, float(proc.loop_clock())))
            return "waiting"
        self._run(kind, live, cands[0], fresh, now, frame_ms=self._frame_ms_before)
        return kind

    # ------------------------------------------------------------------ the live frames' time
    def _late_before(self, proc) -> Optional[float]:
        """Right before a look starts: the live frames of the next ``LATE_WINDOW_S`` (the looker waits for them,
        counting each as it ends); one in twenty of them takes this or more, over the budget -- or None: under it,
        too few of them, or the loop runs no network.  Their 95th percentile, over the budget or not, is kept for
        the look's advice (``_frame_ms_before``: the frame the show runs at now, its own inferences not yet in it)."""
        t0 = time.perf_counter()
        self.frames_ms.wait(proc, t0 + LATE_WINDOW_S, self._stop)
        self._frame_ms_before = self.frames_ms.p95(t0)
        return self.frames_ms.late(t0, self.frame_budget_ms)

    def _stand_down_late(self, ms: float, now: float) -> str:
        """The live frames run late (one in twenty takes ``ms`` or more): no look for the wait after a « keep »
        (the policy's ``keep_wait_s``, doubled at each stand-down in a row up to ``keep_wait_max_s``, as its keeps
        are), so a look cannot start and stop over and over; said in the look's row until the wait is over.
        Returns that row's words."""
        p = self.auto.params
        self._late_n += 1
        wait = min(float(p.keep_wait_s) * 2.0 ** (self._late_n - 1), max(float(p.keep_wait_max_s),
                                                                         float(p.keep_wait_s)))
        self._late_until = float(now) + wait
        self._late_says = LATE_WAIT.format(int(round(ms)), floor_words(self.frame_budget_ms))
        say(f"[SwipeAuto] the live frames run late (one in twenty takes {ms:.0f} ms or more, the floor is "
            f"{floor_words(self.frame_budget_ms)}): no look for {wait:.0f} s")
        return self._late_says

    def _end_late_wait(self) -> None:
        """The wait after late frames is over: its row leaves /status."""
        if self._late_says is not None and self.last_look is not None \
                and self.last_look.get("outcome") == self._late_says:
            self.last_look = None
        self._late_until = self._late_says = None

    # ------------------------------------------------------------------ the background run
    def _cancelled(self, live0: LiveSetting, look: bool = False) -> bool:
        """``look``: a look's run, which stops too in STANDBY and while the watch says the references are wrong
        (``_stood_down``); a load of the second engine does not."""
        if self._stop.is_set() or self.auto.mode == MODE_OFF or self.busy() or self.switch.busy():
            return True                       # (a switch -- the operator's Apply -- waits for nothing: it stops)
        now = self.live()
        return now is None or now.key != live0.key or (look and self._stood_down(now) is not None)

    def _references_wrong(self, live: LiveSetting) -> Optional[str]:
        """The reference watch's status while it says the plate this setting is judged against is wrong (a
        confirmed alert: moved, another wall, refused...), else None: no verdict for that plate, a match, an alert
        not confirmed yet, no plate object, no watch in this build.  The plate is ``LiveSetting.plate_ref``, the
        object ``plate`` identifies: the one the watch keys its verdict on."""
        if live.plate_ref is None:
            return None
        rw = _plate_watch()
        watch = getattr(rw, "WATCH", None) if rw is not None else None
        if watch is None:
            return None
        try:
            v = watch.current(live.plate_ref)
        except Exception:  # noqa: BLE001 - the watch must never sink a look
            return None
        return str(v.status).replace("_", " ") if v is not None and v.alert else None

    def _stood_down(self, live: LiveSetting) -> Optional[str]:
        """Why a look must not run, or its verdict count, now: STANDBY, or the watch says the references are
        wrong; None when it may."""
        if self.standby():
            return STANDBY_DROP
        wrong = self._references_wrong(live)
        return REFERENCES_WRONG.format(wrong) if wrong else None

    def _ensure_resident(self, proc, live: LiveSetting, other: Candidate,
                         runner: CandidateRunner) -> Optional[Tuple[str, str]]:
        """None once the resident is there, else (what the look says, the fault for the operator -- None while it
        only waits).  The caller holds the engine lock.  Loaded only at a moment where that does not show
        (``_quiet``: out of RUN once settled, an empty wall, or the first look after his click in RUN), a stale one
        freed first, there too: never a load or a release at a look that falls due while a dancer is tracked."""
        r = self.resident
        if r is not None and r.candidate == other and r.key == self._resident_key(live):
            return None
        if not self._quiet:
            return WALL_WAIT.format(other.label), None
        if not self._was_run and self.in_run():
            # this look's moment was out of RUN and RUN has begun since: never a load (nor a release) on entering
            # RUN -- the next moment (an empty wall, STANDBY once settled); the click's moment is over with it
            self._quiet = False
            return WALL_WAIT.format(other.label), None
        if r is not None:
            del r
            self._release_now("replaced")     # under the caller's lock: freed here, before the new one loads
        used0 = self.gpu_used()
        try:
            model, pose_runner, load_s = runner.load(self.engine_path(other), other)
        except Exception as exc:  # noqa: BLE001 - the live model runs on; this setting is not tried again
            return self._not_loaded(other, live, exc)
        if self._stop.is_set():
            # the app quits (``stop``): what was just loaded goes here, under the caller's lock -- never kept as a
            # resident that nothing would free before CUDA is torn down
            del model, pose_runner
            self._record("release", resident=other.label, purpose="quit")
            return "cancelled before it began", None
        used1 = self.gpu_used()
        mb = (int(round((used1 - used0) / 2 ** 20 / 10.0)) * 10
              if used0 is not None and used1 is not None and used1 >= used0 else None)
        self.resident = Resident(other, model, pose_runner, self._resident_key(live), load_s, mb)
        if mb is not None:
            self._mb_seen[other.label] = mb
        self._record("load", resident=other.label, seconds=round(load_s, 2), mb=mb)
        say(f"[SwipeAuto] {other.label} loaded beside the live model ({load_s:.2f} s"
            + (f", about {mb} MB)" if mb is not None else ")"))
        if not self._was_run and self.in_run():
            # RUN began while it loaded (a load cannot be cut short): the first RUN frames waited for it
            say(f"[SwipeAuto] {other.label} finished loading as RUN began: the first RUN frames waited for it")
            self.last_look = self._record("look", look="none", t=float(proc.loop_clock()), frames=0,
                                          seconds=round(load_s, 2), outcome=LOAD_INTO_RUN)
        return None

    def _not_loaded(self, other: Candidate, live: LiveSetting, exc: BaseException) -> Tuple[str, str]:
        """One attempt only: the setting is banned (no load, no inference, no look for it until Dismiss or a project
        load), the raw error goes to the log, the operator reads plain words -- "rebuild" only for an engine file
        shown bad, the free MB when the GPU had no room for it (``load_failure``)."""
        why = load_failure(exc, self.engine_path(other))
        say(f"[SwipeAuto] {other.engine_file} did not load: {why.more(other)}")
        return self._ban(other, live, "load", why, f"{type(exc).__name__}: {str(exc)[:160]}")

    def _not_run(self, other: Candidate, live: LiveSetting, error: str,
                 what: Optional[str] = None) -> Tuple[str, str]:
        """A resident that loaded but whose inference failed (``run.error``): as one that cannot be loaded -- the
        setting is banned at the first failure, said once -- and the resident is handed to the release, so its
        memory comes back (a run that failed for lack of it would only fail again beside it, at every look).
        ``what``: the cause the sentences say when it is not the engine (``LOOKS_FAIL``: the looks' own code
        raised), out of memory staying « no room on the GPU »."""
        from runtime.seamless_switch import RUN_FAILED, run_failure
        say(f"[SwipeAuto] {other.label} did not run: {error}")
        r = self.resident
        if r is not None and r.candidate == other:
            self.resident = None
            self.switch.release((r.model, r.runner))
            self._record("release", resident=r.candidate.label, purpose="its run failed")
        return self._ban(other, live, "run", run_failure(error, what=what or RUN_FAILED), error)

    def _stands_down(self, other: Optional[Candidate], now: float) -> Tuple[Optional[str], str]:
        """(what the look says, what the tick returns) while the looker leaves ``other`` alone: banned until
        Dismiss, a project load or a switch to it that works -- "banned" --, or dismissed by the operator a moment
        ago, the policy's snooze -- "snoozed"; else (None, "").  The snooze's row goes once it is over."""
        if other is None:
            return None, ""
        if other.key in self._banned:
            return self._ban_says(other.key), "banned"
        snoozed = self.auto.snoozed(now)
        if snoozed is not None and snoozed == other:
            return self._snooze_says or SNOOZE_WAIT.format(other.label, "the end of the snooze"), "snoozed"
        self._end_snooze_row(snoozed is None)
        return None, ""

    def _end_snooze_row(self, over: bool = True) -> None:
        """The snooze's row leaves /status (no longer what the look waits for); ``over``: the snooze too."""
        if self._snooze_says is not None and self.last_look is not None \
                and self.last_look.get("outcome") == self._snooze_says:
            self.last_look = None
        if over:
            self._snooze_says = None

    def _ban_says(self, key: str) -> str:
        """Why the automatic swipe leaves a banned setting alone (the look's row, the readiness row): its load or
        run failure and what to do, or the automatic switch to it that failed (the setting by its name)."""
        return self._unloadable.get(key) or BAN_WAIT.format(_setting_name(key))

    def _put_aside(self, cand: Candidate) -> None:
        """``cand`` is banned: a suggestion of it goes, without a word (the switch's fault says what happened), and
        so does the streak that named it (after the ban, two looks in a row again)."""
        self.auto.put_aside()
        if self.suggestion is not None and self.suggestion.get("winner_c") == cand:
            self._clear_suggestion(None)

    def _ban(self, other: Candidate, live: LiveSetting, verb: str, why, error: str) -> Tuple[str, str]:
        """The setting ``other`` is banned (``_unloadable``: no load, no inference, no look for it until Dismiss or
        a project load): (what the look and the readiness row say while it waits, the fault for the operator).
        ``why``: a ``LoadFailure``, the cause in plain words.  Whatever the cause, ONE thing to do, Auto to off
        (``LoadFailure.todo``): the alert and the red line say only that; the readiness row and the look's row say
        the ban beside it (``BAN_UNTIL``); the detail -- the error, what the checks found, a file to rebuild -- goes
        to the log line and /status (``Fault.detail``: the switch's ``fault_detail``)."""
        from runtime.seamless_switch import Fault
        todo = why.todo(other)
        said = f"could not {verb} {other.label} ({why})"
        wait = f"the automatic swipe waits: it {said} and {BAN_UNTIL} - {todo}"
        self._banned[other.key] = other.engine_file
        self._unloadable[other.key] = wait
        self._put_aside(other)
        self._record("auto-banned", setting=other.label, engine=other.engine_file, error=error[:160])
        runs = f"{live.candidate.label} still runs - {todo}"
        return wait, Fault(f"the automatic swipe {said}: {runs}", row=f"the automatic swipe {said} and {BAN_UNTIL}: "
                                                                      f"{runs}", detail=why.more(other),
                           background=True)       # Auto to off acknowledges it (``SeamlessSwitch.turned_off``)

    def _run_params(self, proc) -> SwipeParams:
        """The referee's parameters for a background run (the policy's ``referee_params``: the collector's, at the
        show's confidence)."""
        base = self.params or getattr(getattr(proc, "swipe_collector", None), "params", None) or SwipeParams()
        conf = float(getattr(getattr(proc, "settings", None), "confidence", 0.0) or 0.0) or 0.15
        return self.auto.referee_params(conf, base)

    # a look that raised (an error in the code around the engine: the scoring, the advice): what /status says -- its
    # class and message go to the log line only -- and the looks back off as after a « keep »; the LOOK_FAILURES-th
    # in a row puts the setting aside as a run that failed does (``_not_run``: its ban, its sentences), with its own
    # cause (``LOOKS_FAIL``: the looker's own code failed, not the engine).  While a suggestion is on screen it is
    # also a look that does not confirm it (``_unconfirmed``), the reason under its line ``LOOK_RAISED``
    LOOK_FAILED = "the automatic swipe waits: its last look failed, see the log"
    LOOKS_FAIL = "its looks keep failing"
    LOOK_FAILURES = 3
    LOOK_RAISED = "the last look failed (an error, written in the log)"

    def _run(self, kind: str, live: LiveSetting, other: Candidate, fresh: list, now0: float,
             frame_ms: Optional[float] = None) -> None:
        """One look at ``other`` on the newest fresh frames; ``frame_ms``: the live frames' 95th percentile over the
        3 s before it (``_late_before``), for the advice's estimate of a slower winner's frame (None: not measured,
        a slower winner is refused)."""
        proc = self.processor()
        sp = self._run_params(proc)
        # the newest run_frames fresh frames, whatever the policy calls the look (a probe runs on them too)
        frames = sorted(fresh, key=lambda f: float(f.t))[-self.auto.run_frames:]
        ran = False
        self.running = True
        self.state = "probing" if kind == "probe" else "looking"
        t_wall0 = time.perf_counter()
        run = None
        live_ms: List[float] = []
        late: List[Optional[float]] = [None]      # one in twenty of the live frames beside it took this or more
        speed: Tuple[Optional[float], Optional[float]] = (None, None)
        outcome = ""
        fault: Optional[str] = None              # the resident could not be loaded / run: said once, after the lock
        verb = "load"
        try:
            install_capture_guards()
            runner = self.runner_factory(proc)
            with self.engine_lock, no_graph_capture():
                if self._cancelled(live, look=True):
                    outcome = "cancelled before it began"
                    return
                failed = self._ensure_resident(proc, live, other, runner)
                if failed:
                    outcome, fault = failed
                    return
                fh, fw = frames[0].image.shape[:2]
                exclusion = ExclusionCells.from_processor(proc, runner.inp.roi(fw, fh))
                pace = make_pacer(proc, self.pace_s, self._stop)
                t_next: List[float] = [0.0]             # when the pace lets the next inference go
                t_warm: List[Optional[float]] = [None]  # the live frames count from then (the first ones warm up)

                def paced() -> None:
                    self.frames_ms.wait(proc, t_next[0], self._stop)   # the live frames ending meanwhile, counted
                    pace()
                    self.frames_ms.see(proc)
                    t = time.perf_counter()
                    if t_warm[0] is None:
                        t_warm[0] = t + LATE_WARMUP_S
                    t_next[0] = t + self.pace_s
                    y = (getattr(proc, "timing", None) or {}).get("yolo")      # the live call, last frame
                    if isinstance(y, (int, float)):
                        live_ms.append(float(y))

                def stopped() -> bool:
                    """Before each inference: the look stops when cancelled, or once the live frames beside it run
                    late (``late``, kept: the stand-down below)."""
                    if self._cancelled(live, look=True):
                        return True
                    if late[0] is None and t_warm[0] is not None:
                        late[0] = self.frames_ms.late(t_warm[0], self.frame_budget_ms)
                    return late[0] is not None
                res = self.resident                     # (an operator's Apply may take it off meanwhile)
                with self._resident_lock:
                    run = runner.infer([f.image for f in frames], other, res.runner, cancelled=stopped, pace=paced)
                self.inferences += len(run.ms)
            if run.error == "cancelled" and late[0] is not None:
                # the live frames ran late beside it: no verdict on part of the frames, which stay fresh, and no
                # look for a while -- it would only make them late again
                outcome = self._stand_down_late(late[0], float(proc.loop_clock()))
                return
            if run.error:
                outcome = run.error
                if run.error != "cancelled":
                    # the first failed run bans the setting, as a resident that cannot be loaded: said once, its
                    # engine released, no run again (was: a load lock, a frame on the GPU and a failure at every
                    # look for the rest of the night, the second engine kept, nothing said)
                    (outcome, fault), verb = self._not_run(other, live, run.error), "run"
                return
            if len(run.boxes) < len(frames):
                outcome = f"ran {len(run.boxes)} / {len(frames)} frames"     # never a verdict on part of them
                return
            self._late_n = 0                         # the live frames kept up beside a whole look
            # the operator's swipe's own scoring: the live row from the boxes the live pass stored with each frame
            result = score_runs([f.obs for f in frames],
                                [live_run_from_boxes(live.candidate, [f.boxes for f in frames]), run],
                                live.candidate, sp, lost=[bool(f.lost) for f in frames],
                                lost_share=proc.swipe_lost_share(), exclusion=exclusion)
            if result is None:
                outcome = "no verdict: the live setting could not be scored"
                return
            after = self.live()
            if after is None or after.key != live.key:
                # the plate (or the setting) changed after the last frame was inferred: the verdict goes
                gone = after is not None and not after.plate_ok
                outcome = f"dropped: {PLATE_NOT_VALID if gone else 'the setting changed during the run'}"
                return
            down = self._stood_down(after)
            if down:                                 # STANDBY, or the watch's alert, after the last frame
                outcome = f"dropped: {down}"
                return
            from runtime.swipe_controller import REFEREE_WAIT, referee_refused
            refused = referee_refused(result)        # the plate refused: said in this row, not to the plate watch
            if self.referee is not None:             # the plate watch hears nothing of a look, either way
                self.referee.note(result, frames, plate_ok=live.plate_ok, background=True)
            now = proc.loop_clock()
            # the « slower » rule: the candidate's times here and the live model's sampled over the same run, alike
            speed = self.auto.speed(run.ms, live_ms)
            ms = {c: v for c, v in ((other, speed[0]), (live.candidate, speed[1])) if v is not None}
            self.auto.ran(now, probe=(kind == "probe"))          # the policy's order: ran, then advise
            ran = True
            adv = self.auto.advise(now, result, newest_frame_t=max(float(f.t) for f in frames), ms=ms,
                                   frame_ms=frame_ms)
            outcome = REFEREE_WAIT if refused else (adv.outcome or result.reason)   # the floor's row, if it refused
            self._act(adv, result, live)
            self._look_failures = 0
        except Exception as exc:  # noqa: BLE001 - errors end the look, never the app
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
            say(f"[SwipeAuto] the look at {other.label} failed: {error}")
            self._look_failures += 1
            if self._look_failures >= self.LOOK_FAILURES:
                self._look_failures = 0
                (outcome, fault), verb = self._not_run(other, live, error, self.LOOKS_FAIL), "run"
            else:
                outcome = self.LOOK_FAILED
                # no suggestion on screen: its frames stay fresh, no look for a while.  A suggestion on screen: a
                # look that does not confirm it, as any other -- its frames count as looked at, the line stops
                # inviting and the next look comes on a full fresh pool (about 150 s); the second withdraws it.
                # Each counts toward the LOOK_FAILURES too: the third in a row still puts the setting aside
                sug = self.suggestion is not None
                self.auto.failed(float(proc.loop_clock()),
                                 newest_frame_t=max((float(f.t) for f in frames), default=None) if sug else None)
                self._unconfirmed(None, reason=self.LOOK_RAISED)
        finally:
            seconds = time.perf_counter() - t_wall0
            if not ran:                       # cancelled, failed, not scored: its frames stay fresh
                self.auto.ran(proc.loop_clock(), probe=(kind == "probe"))
            self.running = False
            if self.state in ("looking", "probing"):
                self.state = "watching"
            self.last_look = self._record(
                "look", look=kind, t=now0, frames=len(frames), seconds=round(seconds, 2),
                inferences=len(run.ms) if run is not None else 0, outcome=outcome,
                ms_p50=round(run.ms_p50, 1) if run is not None and run.ms_p50 else None,
                ms_cand=round(speed[0], 1) if speed[0] is not None else None,     # what the « slower » rule
                ms_live=round(speed[1], 1) if speed[1] is not None else None,     # compared ...
                ms_frame=round(frame_ms, 1) if frame_ms is not None else None)    # ... and the frame before it
            if fault:                         # the same visible path as a failed automatic switch: THE fault
                self._fault_why = (fault, f"the automatic swipe could not {verb} {other.label}")
                self.switch.fail(fault)

    # ------------------------------------------------------------------ the advice
    def _act(self, adv, result, live: LiveSetting) -> None:
        """One verdict's advice.  A suggestion is shown once (one toast): a later look that names it again
        updates its line silently (figures, time: its age counts from then); a look that does not (a « keep »,
        another winner, no verdict -- the referee's refusal of the plate among them --, a winner the « slower »
        rule refuses) takes its « Apply now? » off the line, and ``UNCONFIRMED`` of them in a row withdraw it
        (``_unconfirmed``)."""
        sug = self.suggestion
        if result.winner is None:
            # no verdict (the operator's swipe's no_verdict): no toast, no row; a suggestion on screen unconfirmed
            self.last_advice = self._record("advice", advice="none", no_verdict=True, reason=result.reason)
            self._unconfirmed(result)
            return
        a = {"kind": adv.kind, "level": adv.level, "winner": adv.winner.label if adv.winner else None,
             "seamless": adv.seamless, "streak": adv.streak, "text": adv.text, "why_not_auto": adv.why_not_auto,
             "reason": result.reason}
        if adv.winner is not None and adv.winner.key in self._banned:
            # banned while this look ran (the looker runs none for a banned setting): never suggested, never
            # switched to -- nothing said, nothing withdrawn
            self.last_advice = self._record("advice", advice="none", banned=adv.winner.label, reason=result.reason)
            return
        held = None
        if adv.kind == "apply" and MODE_AUTO not in released_modes():
            held = (AUTO_NOT_RELEASED, "")         # the last guard: shown as a suggestion, nothing switches
        elif adv.kind == "apply" and self.switch.fault:
            why = self._fault_held()
            held = (why, why)
        if held is not None:
            adv.kind, adv.why_not_auto, adv.held = "suggest", held[0], held[1]
            adv.said = adv.text.removeprefix(f"switching to {adv.winner.label}: ")    # the apply text, no verb
            adv.text = suggestion_text(adv.said, adv.streak, adv.held)
            a.update(kind="suggest", why_not_auto=adv.why_not_auto, text=adv.text)
        if adv.kind == "none" and result.winner is not None and result.winner == result.current:
            a["keeps"] = live.candidate.label
        self.last_advice = self._record("advice", advice=a["kind"], **{k: v for k, v in a.items() if k != "kind"})
        if sug is not None and adv.kind in ("seen", "suggest") and adv.winner == sug["winner_c"]:
            # named again: the same line, the figures and the time of this look -- no toast, no alert
            seen = int(sug.get("seen", 2)) + 1
            hold, why = adv.held, adv.why_not_auto
            if adv.kind == "seen":
                # « seen once » in the policy's streak: the line says the reason that holds NOW (the dwell may be
                # over, the fault dismissed since), the switch's fault as for « apply » above
                if not hold and adv.seamless and adv.level == "definite" and not getattr(adv, "too_slow", "") \
                        and self.auto.mode == MODE_AUTO and MODE_AUTO in released_modes():
                    hold = self._fault_held()
                why = sug.get("why_not_auto")
                if why == sug.get("held"):         # the reason the line said: the one that holds now, or none
                    why = hold or adv.why_not_auto
            self.suggestion = {**sug, "text": suggestion_text(adv.said, seen, hold), "held": hold, "seen": seen,
                               "said": adv.said, "unconfirmed": 0, "reason": result.reason, "level": adv.level,
                               "why_not_auto": why, "wall": self.wall(), "result": result}
            self._record("confirmed", setting=adv.winner.label, seen=seen)
            self.notify("suggest", {**{k: v for k, v in self.suggestion.items() if k in a}, "again": True,
                                    "result": result_dict(result)})
            return
        if adv.kind in ("none", "seen"):
            # a « keep », another winner seen once, the suggestion's own winner refused (the « slower » rule)
            same = sug is not None and adv.winner == sug["winner_c"]
            self._unconfirmed(result, adv.why_not_auto if same and adv.kind == "none" else "")
            return
        if sug is not None and adv.winner != sug["winner_c"]:
            self._unconfirmed(result, replaced=True)  # another winner shown or switched to: this one goes
        if adv.kind == "suggest":
            # the setting it was judged on, for what_changed's words -- without the plate object (``plate_ref``,
            # in no word and no key): a suggestion that outlives its plate (no live model to see the scene change)
            # does not keep it in memory up to its max age
            self.suggestion = {**a, "winner_c": adv.winner, "live_key": live.key, "held": adv.held,
                               "seen": adv.streak, "said": adv.said, "unconfirmed": 0,
                               "live": dataclasses.replace(live, plate_ref=None), "wall": self.wall(),
                               "result": result}
            self.notify("suggest", {**a, "result": result_dict(result)})
            return
        # apply (mode auto, a definite seamless winner): the resident goes live through the switch (warmed there
        # at the live call's arguments, swapped by the loop between two frames)
        busy = self.busy() or self.switch.busy()
        if busy:
            # an operator's Apply (or a load, a calibration...) began since the run: nothing failed -- no fault, no
            # ban, nothing said; the next look decides again
            self._record("not-switched", setting=adv.winner.label, why=busy)
            return
        r = self.resident
        why = (f"{adv.winner.label} is not loaded beside the live model" if r is None or r.candidate != adv.winner
               else self.switch.request(adv.winner, adv.text, prepared=Prepared(r.candidate, r.model, r.runner),
                                        auto=True))
        if why and self.switch.busy():
            self._record("not-switched", setting=adv.winner.label, why=why)    # an Apply landed in between: the same
            return
        if why:
            text = (f"the automatic switch to {adv.winner.label} failed before it began ({why}): "
                    f"{live.candidate.label} still runs - Dismiss lets it try again")
            self._fault_why = (text, AUTO_FAILED)
            self.switch.fail(text)
            return
        self.state = "switching"
        self.notify("switching", {"text": adv.text, "to": adv.winner.label, "from": live.candidate.label})   # a toast

    def _fault_held(self) -> str:
        """What holds an automatic switch back while the switch holds a fault (said on the suggestion's line):
        whose switch failed, or "" without a fault."""
        if not self.switch.fault:
            return ""
        why = self._fault_why                      # whose switch the fault is (the request carried ``auto``)
        return why[1] if why is not None and why[0] == self.switch.fault else AUTO_FAILED

    # ------------------------------------------------------------------ the switch's hooks (loop thread)
    def _on_switched(self, req, old_pair, old_cand: Candidate, new_cand: Candidate, take_t: float) -> bool:
        """A switch happened (the operator's Apply or ours).  The new live model is no longer the resident; the
        old live one becomes it when it runs at the new input (the way back is as cheap): True = kept.  The policy
        is told once its first frames ran (``_on_switch_confirmed``): until then it may still be rolled back."""
        if self.resident is not None and self.resident.model is req["prepared"].model:
            self.resident = None
        self._expect = new_cand
        self._clear_suggestion(None)
        self.state = "watching" if self.auto.mode != MODE_OFF else "off"
        same_input = (int(old_cand.imgsz), old_cand.shape) == (int(new_cand.imgsz), new_cand.shape)
        live = self.live()
        if same_input and self.resident is None and self.auto.mode != MODE_OFF and live is not None:
            c = new_cand
            key = (live.project, int(c.imgsz), tuple(c.shape) if c.shape else None, bool(live.trt))
            self.resident = Resident(old_cand, old_pair[0], old_pair[1], key, 0.0)
            return True
        return False

    def _on_switch_confirmed(self, req) -> None:
        """The switched-in model ran its first frames: now the setting changed -- the dwell starts at the swap, the
        streak is over, the pool starts again.  A ban on that setting goes: it runs, and runs well."""
        self._lift(req["new"].key)
        self.auto.switched(req["take_t"], req["old"], req["new"])

    def _on_switch_superseded(self, req) -> None:
        """A model load replaced the switched-in model during its first frames: the switch did not last.  The
        setting it moved to is no longer expected (a change by hand to it later is one); the old live model kept as
        the resident goes once it no longer fits the live setting (``_free_pending``).  The policy never heard of
        the switch."""
        self._expect = None

    def _on_switch_failed(self, req) -> None:
        """A switch that did not happen (it failed before it began, or its first frames raised): the old live model
        is live again -- not our resident; after an automatic one the looker stands down for that setting (no look,
        no suggestion, no switch: ``_stands_down``) until Dismiss, a project load or a switch to it that works.  The
        policy never heard of it: its dwell, streak and pool are as they were before the swap."""
        old = req.get("old_pair")
        if old is not None and self.resident is not None and self.resident.model is old[0]:
            self.resident = None
        self._expect = req.get("old")      # the setting live again is not a change by hand (a look in between saw
                                           # the new one)
        if self.switch.fault:              # whose switch failed: what a later apply held back says
            self._fault_why = (self.switch.fault, AUTO_FAILED if req.get("auto") else APPLY_FAILED)
        if req.get("auto"):
            target = req.get("winner") or req.get("new")
            if target is not None:
                self._banned[target.key] = target.engine_file
                self._put_aside(target)
                self._record("auto-banned", setting=target.label, engine=target.engine_file)
        self.state = "watching" if self.auto.mode != MODE_OFF else "off"

    # ------------------------------------------------------------------ the operator's seamless Apply
    def request_switch(self, winner: Candidate, text: str,
                       on_done: Optional[Callable[[bool, str], None]] = None,
                       suggestion: bool = False) -> Optional[str]:
        """The operator's seamless Apply with the automatic swipe beside it: its resident when that is the winner
        (no second load), else the resident leaves first and the switch loads the winner (the winner never loads
        beside it: two engines, the live one and the winner).  A resident loaded for another project, input or
        TensorRT state than the live one (``_fits``) is never handed over: it leaves, as for another winner.
        ``suggestion``: the Apply of this swipe's suggestion -- refused while this build holds it back
        (``apply_released``): nothing switches, the resident stays."""
        if suggestion and not apply_released():
            self._record("not-switched", setting=winner.label, why=APPLY_HELD_BACK)
            return APPLY_HELD_BACK
        r = self.resident
        live = self.live()
        if live is not None and self._fits(live, winner):
            return self.switch.request(winner, text, on_done, prepared=Prepared(r.candidate, r.model, r.runner))
        why = self.switch.can_switch(winner) or self.switch.busy()
        if why:
            return why
        why = self.switch.request(winner, text, on_done)
        if why is None and r is not None:
            # freed by the switch's worker before it loads the winner, once a look in progress has stopped (it
            # holds the engine lock; the switch's request stops it at its next frame) -- nothing waits here
            self.resident = None
            self.switch.release((r.model, r.runner))
            self._record("release", resident=r.candidate.label, purpose="apply")
        return why

    # ------------------------------------------------------------------ the operator's side
    def _unconfirmed(self, result=None, why: str = "", replaced: bool = False, reason: str = "") -> None:
        """A look that did not confirm the suggestion on screen (``result``: its verdict, None for a look the
        pre-filter skipped or a look that raised -- ``reason``: what the line says under it then, else the look's
        row; ``why``: the policy's reason when it refused the suggestion's own winner).  The
        ``UNCONFIRMED``-th in a row withdraws it.  Before that, the line stops inviting: no « Apply now? », its
        figures and « not confirmed at the last look (HH:MM) » (``unconfirmed_text``), and no Apply button -- the
        event's ``winner`` is None, and the app's line shows Apply only with a winner --; and the next look comes
        as soon as a full pool of fresh frames is kept, not after the back-off or the wait after a refusal
        (``SwipeAuto.unconfirmed``): gone one pool of dancing (about 150 s) after that first look.  A look that
        names it again restores the line (``_act``).  ``replaced``: another winner takes the line (a new
        suggestion, a switch): counted only."""
        sug = self.suggestion
        if sug is None:
            return
        sug["unconfirmed"] = int(sug.get("unconfirmed", 0)) + 1
        if sug["unconfirmed"] >= UNCONFIRMED:
            self._clear_suggestion(NOT_CONFIRMED)
            return
        if replaced:
            return
        said = sug.get("said") or str(sug.get("text", "")).split(", seen ")[0]
        at = time.strftime("%H:%M", time.localtime(float(self.wall())))
        sug["text"] = unconfirmed_text(said, at, why)
        self.auto.unconfirmed()
        self._record("unconfirmed", setting=sug["winner_c"].label, why=why or None)
        shown = {k: v for k, v in sug.items() if k in ("kind", "level", "seamless", "streak", "text", "why_not_auto",
                                                        "reason")}
        self.notify("suggest", {**shown, "again": True, "winner": None, "unconfirmed": sug["unconfirmed"],
                                "result": (result_dict(result) if result is not None else
                                           {"reason": reason or (self.last_look or {}).get("outcome", "")})})

    def _clear_suggestion(self, why: Optional[str]) -> None:
        if self.suggestion is None:
            return
        self.suggestion = None
        if why:
            self.notify("cleared", {"text": f"suggestion withdrawn: {why}"})

    # an Apply of a suggestion the last look did not confirm (pressed as its line changed, or from afar)
    LAST_NOT_CONFIRMED = "the last look did not confirm it"

    def withdraw_stale(self, live: Optional[LiveSetting] = None, setting: bool = False) -> Optional[str]:
        """The suggestion goes once it no longer stands, and why it went (the swipe line: « suggestion withdrawn:
        <why> »), or None: ``setting`` -- judged on another setting or scene than ``live`` (another project among
        them; P5's words for what changed) --, or older than ``max_age_s`` (P5's bound on the operator's own
        verdict, said the same way).  Every look asks the age, whatever the wall; the operator's Apply asks both,
        and whether the last look confirmed it.  A setting or scene that changed ends the streak too."""
        sug = self.suggestion
        if sug is None:
            return None
        why = None
        if setting and (live is None or sug.get("live_key") != live.key):
            changed = what_changed(sug["live"], live) if sug.get("live") is not None else None
            why = (PLATE_NOT_VALID if changed == PLATE_UNUSABLE else
                   f"{changed or 'the setting changed'} since the suggestion")
            self.auto.withdrawn()             # another comparison: the streak goes with it
        if why is None and setting and int(sug.get("unconfirmed", 0)) > 0:
            why = self.LAST_NOT_CONFIRMED     # its Apply button is off the line already: a press in flight
        if why is None:
            age = float(self.wall()) - float(sug.get("wall", self.wall()))
            if age > self.max_age_s:
                why = f"it is {age / 60.0:.0f} min old"
        if why is not None:
            self._clear_suggestion(why)
        return why

    def _lift(self, key: Optional[str] = None) -> None:
        """The ban on one setting (``key``), or on all, goes; so does the look's row that waits for one of them
        (/status no longer says it)."""
        waits = [self._ban_says(k) for k in (list(self._banned) if key is None else [key]) if k in self._banned]
        if self.last_look is not None and self.last_look.get("outcome") in waits:
            self.last_look = None
        if key is None:
            self._banned.clear()
            self._unloadable.clear()
        else:
            self._banned.pop(key, None)
            self._unloadable.pop(key, None)

    def clear_bans(self) -> None:
        """A project load: the settings an automatic switch failed to (or whose resident did not load or run) may
        be tried again, and a setting the operator dismissed may be suggested again."""
        self._lift()
        self.auto.end_snooze()
        self._end_snooze_row()

    def dismiss(self, not_now: bool = True, behind: bool = False) -> bool:
        """The operator's Dismiss: one press, one meaning.  A suggestion on screen (``not_now``: the app says the
        swipe line shows it) -- « not now »: it goes, the policy ends the streak and puts that setting aside (no
        suggestion, no switch by itself, no look for it) for ``snooze_s`` of the loop clock or until the scene
        changes; no ban is lifted, and a fault stays for its own Dismiss.  No suggestion on screen: a fault or a
        ban held is acknowledged -- the settings an automatic switch failed to (or whose resident did not load or
        run) may be tried again (the fault is the switch's to drop), and a suggestion not shown goes too:
        ``behind`` (the operator's Dismiss over his own verdict, the suggestion behind it) -- « not now » for it as
        well, put aside as above (was: dropped, the streak kept: shown again with a toast at the next look);
        without it (an Apply), it just goes.  True for « not now » on the line."""
        sug = self.suggestion
        if sug is not None and not_now:
            self._not_now(sug)
            return True
        self._lift()
        if sug is not None:
            if behind:
                self._not_now(sug)
            else:
                self.suggestion = None
                self.notify("cleared", {"text": "dismissed"})
        return False

    def _not_now(self, sug: Dict[str, Any]) -> None:
        """The operator's « not now » to the suggestion ``sug``: it goes, its setting is put aside (``dismiss``)."""
        self.suggestion = None
        winner = sug["winner_c"]
        self.auto.dismissed(float(self.processor().loop_clock()), winner, self.snooze_s)
        until = time.strftime("%H:%M", time.localtime(float(self.wall()) + self.snooze_s))
        self._end_snooze_row()
        self._snooze_says = SNOOZE_WAIT.format(winner.label, until)
        self._record("dismissed", setting=winner.label, seconds=self.snooze_s)
        self.notify("cleared", {"text": "dismissed"})

    def held(self) -> bool:
        """A setting is banned: Dismiss has something to lift (its button stays on screen)."""
        return bool(self._banned)

    def to_load(self) -> Optional[Tuple[str, Optional[int]]]:
        """The second model still to load for the live setting -- (its label, its GPU memory in MB measured at its
        last load this session, None: never measured) -- or None: the mode off, nothing live (a model loads), the
        live model on PyTorch, no other model or its engine not built here, banned (``health`` says why), or loaded
        already.  For the readiness row: what the load at the next moment will take beside the live model."""
        if self.auto.mode == MODE_OFF:
            return None
        live = self.live()
        if live is None:
            return None
        want = self._wanted(live, other_model(live.candidate, self.models))
        if want is None or want.key in self._banned or self._fits(live, want):
            return None
        return want.label, self._mb_seen.get(want.label)

    def health(self) -> Optional[str]:
        """While a ban holds, why the automatic swipe leaves a setting alone (the readiness row, once the switch has
        no fault left to say: a later switch succeeded); None without a ban."""
        if not self._banned:
            return None
        return self._ban_says(sorted(self._banned)[0])

    def status(self) -> Dict[str, Any]:
        """For /status and the readiness row (JSON-ready).  ``counts``: of the fresh frames with dancers, ``lost``
        the ones the live setting left a dancer bare on, ``covered`` the others."""
        sug = self.suggestion
        fresh, lost = self.last_counts
        return {"mode": self.mode, "state": self.state, "running": self.running,
                "resident": self.resident.candidate.label if self.resident is not None else None,
                "resident_mb": self.resident.mb if self.resident is not None else None,
                "last_look": self.last_look, "last_advice": self.last_advice,
                "suggestion": ({k: v for k, v in sug.items() if k not in ("winner_c", "live_key", "live", "result")}
                               if sug is not None else None),
                "inferences": self.inferences, "auto_banned": sorted(self._banned.values()),
                "counts": {"lost": lost, "covered": fresh - lost}}
