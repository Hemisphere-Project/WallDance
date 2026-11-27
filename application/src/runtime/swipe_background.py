"""The autonomous swipe (Plan Cordee P6) beside the live loop: it looks by itself, says, or switches.

``core.swipe_auto`` is the policy (when to look, what to say, when to switch); this is the machinery that feeds it
and acts on its advice, on its own thread, without the operator:

* **the look** (``tick``, every ``look_s`` = 5 s): the kept frames with dancers the previous run did not use,
  counted under the tap's lock (no image copied).  A run needs a FULL pool of them (``SwipeParams.frames`` = 150,
  1 s apart: two and a half minutes of dancing; never the operator's 80 % floor), then the policy's ``due``.  An
  empty wall, STANDBY, a model load, an operator's swipe, a calibration: ``idle`` -- no inference, no state that
  grows (on an empty wall or in STANDBY the second engine may be loaded: see the background run);
* **the background run**, much gentler than the operator's swipe: two settings only -- the live one, scored on
  the boxes it already made (``SwipeFrame.boxes``: no inference), and the OTHER MODEL AT THE LIVE NETWORK INPUT,
  kept RESIDENT (under ``ENGINE_LOAD_LOCK`` and without a CUDA graph).  A load or a release stops the loop for
  some 50 ms, so they happen only at moments where that does not show: out of RUN, or in RUN on a wall empty for
  ``SWIPE_AUTO_LOAD_EMPTY_S`` (no track, no identity slot held: the collector's own test) -- and at the operator's
  own click: the first look after the mode is set to suggest or auto loads it, whatever is on the wall (once).
  Never on entering RUN, never at a look that falls due later while a dancer is tracked.  So it loads when the
  mode is set, after a project or model load, in STANDBY or on an empty wall; a look that falls due without it
  while a dancer is tracked waits for such a moment (``WALL_WAIT``, in ``status``).  It is kept while the mode is
  on (no idle timer) and goes when the mode goes off (at once out of RUN, right after a live frame in RUN), when
  the project, input or TensorRT state changes, and at quit, never inside a run; the readiness report says what
  it holds on the GPU.  Its inferences are paced ``pace_s`` apart right after a live network pass (150 x 0.2 s:
  about 30 s of gentle inference), judged at the show's confidence.  A run without a verdict says nothing (the
  operator's swipe's ``no_verdict``);
* **the advice** (``SwipeAuto.advise``): ``none`` (it keeps the live setting), ``suggest`` (a warning with
  « Apply now » that stays until applied, dismissed or stale), ``apply`` (mode auto, a definite seamless winner:
  the switch, with an alert);
* **the plate**: the referee judges every setting against the empty-wall plate, so a verdict on a plate that is
  not usable (``LiveSetting.plate_ok`` False: no foreground, invalidated, refused for its camera, found stale) is
  worth nothing.  While it is not usable: ``waiting`` -- no run starts, no engine is loaded, no inference, no
  suggestion, no switch; the look says so (``PLATE_WAIT``, in ``status``); a suggestion shown goes; a run in
  progress stops at its next frame and its verdict is dropped.  Any change of the scene (the plate's state among
  it, either way) starts the policy's verdicts over AND the pool: frames kept before are never scored;
* **the switch** goes through ``runtime.seamless_switch`` (the operator's Apply's own path): the resident model
  becomes the live one between two frames and the old live model the resident one -- no camera close, no modal,
  the tracker and the slots untouched; never while a background inference is in flight on it (the switch's
  ``guard`` is this thread's resident lock).  The policy hears of it (the dwell, a new pool) once the switched-in
  model's first frames ran: a switch rolled back leaves the policy and the pool as they were, plus the ban.  A
  switch that fails is the switch's fault, the one thing of the swipe that belongs to the health line;
* **a resident that cannot be loaded** (a broken engine file, no GPU memory left beside TouchDesigner) is tried
  once: that setting is banned like a failed automatic switch -- and more: no load, no inference, no look run for
  it -- until Dismiss, a project load or a switch to it that works; it is said once, as the switch's fault, in
  plain words (the raw error goes to the log), and while it holds, the look says why the swipe waits.

Threading: the looker thread owns the resident model and its inferences; the loop thread owns the live model and
the swap.  Every CUDA call here happens under ENGINE_LOAD_LOCK (a live load waits, and a running look stops at its
next frame when one begins: ``live()`` is None then), on frames the current live model produced; in RUN releases
wait until the current live model has run a frame, out of RUN they happen at once.  The looker thread starts with a
mode that is not off and ends once the mode is off and its engine freed.  Errors end the look, never the app.
"""
from __future__ import annotations

import dataclasses
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.swipe import Candidate, SwipeParams
from core.swipe import score_frame
from core.swipe_auto import (MODE_AUTO, MODE_OFF, AutoParams, SwipeAuto, alike_ms, bar_for,  # noqa: F401 - PolicyPort
                             headroom, parse_mode, referee_params)
from core.swipe_runner import (SWIPE_MODELS, CandidateRunner, ExclusionCells, install_capture_guards,
                               live_run_from_boxes, no_graph_capture, result_dict, score_runs)
from runtime.seamless_switch import ENGINE_UNREADABLE, Prepared, SeamlessSwitch, _runner_factory, load_failure
from runtime.swipe_controller import LiveSetting, make_pacer, say

# the empty-wall plate is not usable (LiveSetting.plate_ok False): what the look says, and why a suggestion goes
PLATE_NOT_VALID = "the empty-wall plate is not valid - Calibrate on the empty wall"
PLATE_WAIT = f"the automatic swipe waits: {PLATE_NOT_VALID}"
# a look fell due without the second engine while a dancer is tracked: it loads at the next empty wall (or STANDBY)
WALL_WAIT = "the automatic swipe waits for an empty wall to load {}"
# why an automatic switch is only suggested: the same setting failed before (the ban), or the switch holds a fault
BANNED_SAYS = "the automatic switch to it failed: Dismiss lets it try again"
AUTO_FAILED = "the last automatic switch failed: Dismiss lets it try again"
APPLY_FAILED = "the last Apply failed: Dismiss lets it switch by itself again"


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


class PolicyPort:
    """Every call into ``core.swipe_auto`` goes through here, in the order the policy asks (REPORT_P6, 66ddeaa): on
    each look ``fresh`` -> the live setting's own scores on them -> ``room`` -> ``due``; on "run" the candidates
    scored on the newest ``run_frames`` fresh frames -> ``ran`` -> ``advise``; ``switched`` once a swap's first
    frames ran (or a change by hand); ``reset`` on a new scene.  A rename or a new argument costs one edit in this
    class."""

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

    def advise(self, now: float, result, newest_frame_t: Optional[float], ms: Dict[Candidate, float]):
        return self.policy.advise(now, result, newest_frame_t=newest_frame_t, ms=ms)

    def switched(self, now: float, old: Candidate, new: Candidate) -> None:
        self.policy.switched(now, old, new)

    def reset(self) -> None:
        self.policy.reset()


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
                 empty_s: Optional[float] = None, gpu_used: Callable[[], Optional[int]] = _gpu_used_bytes):
        from core.config import SWIPE_AUTO_LOAD_EMPTY_S, SWIPE_AUTO_LOOK_S, SWIPE_AUTO_PACE_S
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
        self.in_run = in_run              # the app is in RUN (the show's output is live)
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
        # settings an automatic switch failed to: no automatic attempt again until Dismiss, a project load or a
        # switch to it that works (the operator's Apply, confirmed)
        self._banned: Dict[str, str] = {}
        # ... of them, those whose resident could not be loaded: no look at all for them, and what the look says
        self._unloadable: Dict[str, str] = {}
        self._fault_why: Optional[Tuple[str, str]] = None   # (the switch's fault, why it holds an apply back)
        self.resident: Optional[Resident] = None
        self._resident_lock = switch.guard         # held while the looker infers on the resident: the swap waits
        self._scene: Optional[Tuple] = None
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
        self._quiet_since: Optional[float] = None       # loop clock since when the wall is seen empty in RUN
        self._was_run = False                           # RUN at the last look (entering RUN starts the count again)
        self._next_s: Optional[float] = None            # the next look sooner than look_s (an empty wall counted)
        self._wall_wait: Optional[str] = None           # a look due waits for an empty wall: what its row says
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

    def set_mode(self, mode: str) -> str:
        """Off ends the automatic swipe for the session: the looker frees its engine (at once out of RUN, right
        after a live frame in RUN) and its thread ends.  Another mode wakes the looker -- ``start`` starts it
        again -- and the engine loads at that next look, whatever is on the wall: the operator's click (or a
        project load) is the moment, once.  Later, only where a pause does not show."""
        m = parse_mode(mode)
        self.auto.mode = m
        self._mode_set = m != MODE_OFF    # the next look may load: the operator chose this pause
        if m == MODE_OFF:
            self._clear_suggestion("the automatic swipe is off")
            self.state = "off"
            self._end_wall_wait()
        elif self.state == "off":
            self.state = "watching"
        self._wake.set()                  # the next look now: the operator's click is the moment
        return m

    def start(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._wake.set()              # the first look at once: the mode was just set
            self._thread = threading.Thread(target=self._loop, name="SwipeAuto", daemon=True)
            self._thread.start()

    def stop(self, timeout: Optional[float] = 3.0) -> None:
        """At quit (``_cb_quit``, on the loop thread, before the switch's ``stop``): the looker ends -- a load in
        progress ends first (it cannot be cut short: about 3 s cold), and what it loaded is dropped there, never
        kept (``_ensure_resident``) -- then the resident goes here, under the engine lock, unpaced: no frame is
        coming, and nothing here waits for a frame's end.  At most ``timeout`` in all; past it nothing is freed
        (said in the log)."""
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

    def _observe(self, proc) -> None:
        """This look's moment (``_quiet``): a load here does not show -- out of RUN, or in RUN on a wall seen empty
        (no track, no identity slot held) for ``empty_s``, counted from the first look that saw it empty and never
        from before RUN began -- or it is the first look after the mode was set (``set_mode``), whatever is on the
        wall: the operator's own click, used by that look only."""
        self._next_s = None
        mode_set, self._mode_set = self._mode_set, False
        run = bool(self.in_run())
        entered, self._was_run = run and not self._was_run, run
        if not run:
            self._quiet_since, self._quiet = None, True
            return
        try:
            empty = bool(self.wall_empty())
        except Exception:  # noqa: BLE001 - in doubt, a dancer is there
            empty = False
        now = float(proc.loop_clock())
        if not empty:
            self._quiet_since = None
        elif self._quiet_since is None or entered:
            self._quiet_since = now
        self._quiet = mode_set or (empty and now - self._quiet_since >= self.empty_s - 1e-6)

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
            self._end_wall_wait()             # nothing to load for this setting: nothing waits
            return None
        if self._fits(live, want):
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
                return None                   # a load or a switch began meanwhile: the next look
            failed = self._ensure_resident(proc, live, want, runner)
        if self._stop.is_set():
            return "off"                      # the app quits: nothing loaded is kept, nothing said
        if failed:
            wait, fault = failed
            self.last_look = self._record("look", look="none", t=now, frames=0, seconds=0.0, outcome=wait)
            if fault:
                self._fault_why = (fault, f"the automatic swipe could not load {want.label}: Dismiss lets it "
                                          f"try again")
                self.switch.fail(fault)
            return "banned"
        self._end_wall_wait()
        return None

    @staticmethod
    def _resident_key(live: LiveSetting) -> Tuple:
        c = live.candidate
        return (live.project, int(c.imgsz), tuple(c.shape) if c.shape else None, bool(live.trt))

    def tick(self) -> str:
        """One look.  Returns what it did: off | loading | waiting (the plate is not usable, or a look due waits for
        an empty wall to load the other model) | idle | busy | banned (the other model could not be loaded) | run |
        probe | nothing-to-compare."""
        t0 = time.perf_counter()
        try:
            return self._tick()
        finally:
            self.ticks += 1
            self.tick_ms.append((time.perf_counter() - t0) * 1000.0)

    def _tick(self) -> str:
        proc = self.processor()
        plate_pending = bool(getattr(proc, "plate_pending", False))   # read before the live setting: a load between
        live = self.live()
        self._observe(proc)
        self._free_pending(live)
        if self.auto.mode == MODE_OFF:
            self.state = "off"
            return "off"
        if live is None:
            self._next_s = min(self.look_s, 0.5)    # a model loads: its second engine follows as soon as it is live
            return "loading"
        scene = (live.project, live.roi, live.plate, live.plate_ok, live.frame)
        if scene != self._scene:
            if self._scene is not None:
                # nothing learned so far describes this scene: the policy's verdicts start over, and so does the
                # pool -- frames kept before now are never scored (those kept on a plate that was not usable, or
                # that a run already used: the policy's memory of them is gone)
                self.auto.reset()
                self._fresh_after = float(proc.loop_clock())
                self._clear_suggestion(PLATE_NOT_VALID if not live.plate_ok and not plate_pending else
                                       "the scene changed (project, ROI, empty-wall plate or camera)")
            self._scene = scene
        if self._last_live is not None and live.candidate != self._last_live and live.candidate != self._expect:
            self.auto.switched(proc.loop_clock(), self._last_live, live.candidate)       # by hand: the dwell too
            self._record("manual-change", old=self._last_live.label, new=live.candidate.label)
        if live.candidate == self._expect:
            self._expect = None
        self._last_live = live.candidate
        if self.suggestion is not None and self.suggestion.get("live_key") != live.key:
            self._clear_suggestion("the setting changed since the suggestion")
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
        frames, now = proc.swipe_fresh(self._fresh_after)
        fresh = self.auto.fresh(frames)
        self.last_counts = (len(fresh), sum(1 for f in fresh if f.lost))
        self.state = "watching"
        why = self.busy() or self.switch.busy()
        if why:
            return "busy"
        other = other_model(live.candidate, self.models)
        wait = self._unloadable.get(other.key) if other is not None else None
        if wait:
            # its resident could not be loaded: no load, no inference, no look until Dismiss or a project load
            if self.last_look is None or self.last_look.get("outcome") != wait:
                self.last_look = self._record("look", look="none", t=now, frames=0, seconds=0.0, outcome=wait)
            return "banned"
        got = self._load_when_quiet(proc, live, other, now)
        if got:
            return got                        # banned: it could not be loaded just now, said once as the switch's
                                              # fault; off: the app quits
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
        sp = self._run_params(proc)
        skipped = self.auto.skipped
        kind = self.auto.due(now, fresh, busy=False, room=self.auto.room(live.candidate, cands, fresh, sp))
        if kind == "idle":
            if self.auto.skipped > skipped:   # the pre-filter: the live setting leaves no room for the bar
                self.last_look = self._record("look", look="skipped", t=now, frames=len(fresh), seconds=0.0,
                                              outcome="no room for a gain over the bar: no run, no inference")
            return "idle"
        if not fits:
            # due, and no second engine while a dancer is tracked: no load now (it would show), the next empty wall
            self._wall_wait = wall_wait
            self.last_look = self._record("look", look="none", t=now, frames=len(fresh), seconds=0.0,
                                          outcome=wall_wait)
            return "waiting"
        self._run(kind, live, cands[0], fresh, now)
        return kind

    # ------------------------------------------------------------------ the background run
    def _cancelled(self, live0: LiveSetting) -> bool:
        if self._stop.is_set() or self.auto.mode == MODE_OFF or self.busy() or self.switch.busy():
            return True                       # (a switch -- the operator's Apply -- waits for nothing: it stops)
        now = self.live()
        return now is None or now.key != live0.key

    def _ensure_resident(self, proc, live: LiveSetting, other: Candidate,
                         runner: CandidateRunner) -> Optional[Tuple[str, str]]:
        """None once the resident is there, else (what the look says, the fault for the operator -- None while it
        only waits).  The caller holds the engine lock.  Loaded only at a moment where that does not show
        (``_quiet``: out of RUN, an empty wall, or the first look after the mode was set), a stale one freed first,
        there too: never a load or a release at a look that falls due while a dancer is tracked."""
        r = self.resident
        if r is not None and r.candidate == other and r.key == self._resident_key(live):
            return None
        if not self._quiet:
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
        self._record("load", resident=other.label, seconds=round(load_s, 2), mb=mb)
        say(f"[SwipeAuto] {other.label} loaded beside the live model ({load_s:.2f} s"
            + (f", about {mb} MB)" if mb is not None else ")"))
        return None

    def _not_loaded(self, other: Candidate, live: LiveSetting, exc: BaseException) -> Tuple[str, str]:
        """One attempt only: the setting is banned (no load, no inference, no look for it until Dismiss or a project
        load), the raw error goes to the log, the operator reads plain words -- "rebuild" only for an engine file
        shown bad, the free MB when the GPU had no room for it (``load_failure``)."""
        why = load_failure(exc, self.engine_path(other))
        say(f"[SwipeAuto] {other.engine_file} did not load: {type(exc).__name__}: {exc}{why.detail}")
        return self._ban(other, live, "load", why, f"{type(exc).__name__}: {str(exc)[:160]}")

    def _not_run(self, other: Candidate, live: LiveSetting, error: str) -> Tuple[str, str]:
        """A resident that loaded but whose inference failed (``run.error``): as one that cannot be loaded -- the
        setting is banned at the first failure, said once -- and the resident is handed to the release, so its
        memory comes back (a run that failed for lack of it would only fail again beside it, at every look)."""
        from runtime.seamless_switch import run_failure
        say(f"[SwipeAuto] {other.label} did not run: {error}")
        r = self.resident
        if r is not None and r.candidate == other:
            self.resident = None
            self.switch.release((r.model, r.runner))
            self._record("release", resident=r.candidate.label, purpose="its run failed")
        return self._ban(other, live, "run", run_failure(error), error)

    def _ban(self, other: Candidate, live: LiveSetting, verb: str, why, error: str) -> Tuple[str, str]:
        """The setting ``other`` is banned (``_unloadable``: no load, no inference, no look for it until Dismiss or
        a project load): (what the look says while it waits, the fault for the operator).  ``why``: a
        ``LoadFailure``, which says what to do."""
        todo = why.todo(other)
        wait = f"the automatic swipe waits: it could not {verb} {other.label} ({why}) - {todo}"
        self._banned[other.key] = other.engine_file
        self._unloadable[other.key] = wait
        self._record("auto-banned", setting=other.label, engine=other.engine_file, error=error[:160])
        return wait, (f"the automatic swipe could not {verb} {other.label} ({why}): {live.candidate.label} still "
                      f"runs - {todo}")

    def _run_params(self, proc) -> SwipeParams:
        """The referee's parameters for a background run (the policy's ``referee_params``: the collector's, at the
        show's confidence)."""
        base = self.params or getattr(getattr(proc, "swipe_collector", None), "params", None) or SwipeParams()
        conf = float(getattr(getattr(proc, "settings", None), "confidence", 0.0) or 0.0) or 0.15
        return self.auto.referee_params(conf, base)

    def _run(self, kind: str, live: LiveSetting, other: Candidate, fresh: list, now0: float) -> None:
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
        speed: Tuple[Optional[float], Optional[float]] = (None, None)
        outcome = ""
        fault: Optional[str] = None              # the resident could not be loaded / run: said once, after the lock
        verb = "load"
        try:
            install_capture_guards()
            runner = self.runner_factory(proc)
            with self.engine_lock, no_graph_capture():
                if self._cancelled(live):
                    outcome = "cancelled before it began"
                    return
                failed = self._ensure_resident(proc, live, other, runner)
                if failed:
                    outcome, fault = failed
                    return
                fh, fw = frames[0].image.shape[:2]
                exclusion = ExclusionCells.from_processor(proc, runner.inp.roi(fw, fh))
                pace = make_pacer(proc, self.pace_s, self._stop)

                def paced() -> None:
                    pace()
                    y = (getattr(proc, "timing", None) or {}).get("yolo")      # the live call, last frame
                    if isinstance(y, (int, float)):
                        live_ms.append(float(y))
                res = self.resident                     # (an operator's Apply may take it off meanwhile)
                with self._resident_lock:
                    run = runner.infer([f.image for f in frames], other, res.runner,
                                       cancelled=lambda: self._cancelled(live), pace=paced)
                self.inferences += len(run.ms)
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
            from runtime.swipe_controller import REFEREE_WAIT, referee_refused
            refused = referee_refused(result)        # the plate refused: said in this row, not to the plate watch
            if self.referee is not None:             # used the plate: told to the plate watch (a refusal is not)
                self.referee.note(result, frames, plate_ok=live.plate_ok, background=True)
            now = proc.loop_clock()
            # the « slower » rule: the candidate's times here and the live model's sampled over the same run, alike
            speed = self.auto.speed(run.ms, live_ms)
            ms = {c: v for c, v in ((other, speed[0]), (live.candidate, speed[1])) if v is not None}
            self.auto.ran(now, probe=(kind == "probe"))          # the policy's order: ran, then advise
            ran = True
            adv = self.auto.advise(now, result, newest_frame_t=max(float(f.t) for f in frames), ms=ms)
            outcome = REFEREE_WAIT if refused else result.reason
            self._act(adv, result, live)
        except Exception as exc:  # noqa: BLE001 - errors end the look, never the app
            outcome = f"error: {type(exc).__name__}: {str(exc)[:160]}"
            say(f"[SwipeAuto] {outcome}")
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
                ms_live=round(speed[1], 1) if speed[1] is not None else None)     # compared
            if fault:                         # the same visible path as a failed automatic switch: THE fault
                self._fault_why = (fault, f"the automatic swipe could not {verb} {other.label}: Dismiss lets it "
                                          f"try again")
                self.switch.fail(fault)

    # ------------------------------------------------------------------ the advice
    def _act(self, adv, result, live: LiveSetting) -> None:
        if result.winner is None:
            # no verdict (the operator's swipe's no_verdict): silent -- no toast, nothing withdrawn, no row
            self.last_advice = self._record("advice", advice="none", no_verdict=True, reason=result.reason)
            return
        a = {"kind": adv.kind, "level": adv.level, "winner": adv.winner.label if adv.winner else None,
             "seamless": adv.seamless, "streak": adv.streak, "text": adv.text, "why_not_auto": adv.why_not_auto,
             "reason": result.reason}
        banned = adv.winner is not None and adv.winner.key in self._banned
        if adv.kind == "apply" and (self.switch.fault or banned):
            adv.kind = "suggest"
            held = self._fault_why                # whose switch the fault is (the request carried ``auto``)
            whose = held[1] if held is not None and held[0] == self.switch.fault else AUTO_FAILED
            adv.why_not_auto = BANNED_SAYS if banned else whose
            said = adv.text.removeprefix(f"switching to {adv.winner.label}: ")     # the apply text, without its verb
            adv.text = f"{said}: clearly better - Apply now ({adv.why_not_auto})"
            a.update(kind="suggest", why_not_auto=adv.why_not_auto, text=adv.text)
        if adv.kind == "none" and result.winner is not None and result.winner == result.current:
            a["keeps"] = live.candidate.label
        self.last_advice = self._record("advice", advice=a["kind"], **{k: v for k, v in a.items() if k != "kind"})
        if adv.kind == "none":
            self._clear_suggestion(f"the last look keeps {live.candidate.label}")
            return
        if adv.kind == "suggest":
            self.suggestion = {**a, "winner_c": adv.winner, "live_key": live.key, "wall": self.wall(),
                               "result": result}
            self.notify("suggest", {**a, "result": result_dict(result)})
            return
        # apply (mode auto, a definite seamless winner): the resident goes live through the switch (warmed there
        # at the live call's arguments, swapped by the loop between two frames)
        r = self.resident
        why = (f"{adv.winner.label} is not loaded beside the live model" if r is None or r.candidate != adv.winner
               else self.switch.request(adv.winner, adv.text, prepared=Prepared(r.candidate, r.model, r.runner),
                                        auto=True))
        if why:
            text = (f"the automatic switch to {adv.winner.label} failed before it began ({why}): "
                    f"{live.candidate.label} still runs - set the automatic swipe to off")
            self._fault_why = (text, AUTO_FAILED)
            self.switch.fail(text)
            return
        self.state = "switching"
        self.notify("switching", {"text": adv.text, "to": adv.winner.label, "from": live.candidate.label})

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
        is live again -- not our resident; an automatic one is not tried again for that setting until Dismiss.  The
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
                self._record("auto-banned", setting=target.label, engine=target.engine_file)
        self.state = "watching" if self.auto.mode != MODE_OFF else "off"

    # ------------------------------------------------------------------ the operator's seamless Apply
    def request_switch(self, winner: Candidate, text: str,
                       on_done: Optional[Callable[[bool, str], None]] = None) -> Optional[str]:
        """The operator's seamless Apply with the automatic swipe beside it: its resident when that is the winner
        (no second load), else the resident leaves first and the switch loads the winner (the winner never loads
        beside it: two engines, the live one and the winner)."""
        r = self.resident
        if r is not None and r.candidate == winner:
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
    def _clear_suggestion(self, why: Optional[str]) -> None:
        if self.suggestion is None:
            return
        self.suggestion = None
        if why:
            self.notify("cleared", {"text": f"suggestion withdrawn: {why}"})

    def _lift(self, key: Optional[str] = None) -> None:
        """The ban on one setting (``key``), or on all, goes; so does the look's row that waits for one of them
        (/status no longer says it)."""
        waits = list(self._unloadable.values()) if key is None else [self._unloadable.get(key)]
        if self.last_look is not None and self.last_look.get("outcome") in [w for w in waits if w]:
            self.last_look = None
        if key is None:
            self._banned.clear()
            self._unloadable.clear()
        else:
            self._banned.pop(key, None)
            self._unloadable.pop(key, None)

    def clear_bans(self) -> None:
        """A project load: the settings an automatic switch failed to (or whose resident did not load) may be tried
        again."""
        self._lift()

    def dismiss(self) -> None:
        """The operator's Dismiss: the suggestion goes (a failed switch is the switch's to acknowledge) and the
        settings an automatic switch failed to (or whose resident did not load) may be tried again."""
        self._lift()
        if self.suggestion is not None:
            self.suggestion = None
            self.notify("cleared", {"text": "dismissed"})

    def held(self) -> bool:
        """A setting is banned: Dismiss has something to lift (its button stays on screen)."""
        return bool(self._banned)

    def health(self) -> Optional[str]:
        """While a ban holds, why the automatic swipe leaves a setting alone (the readiness row, once the switch has
        no fault left to say: a later switch succeeded); None without a ban."""
        if not self._banned:
            return None
        key = sorted(self._banned)[0]
        return self._unloadable.get(key) or (f"the automatic swipe will not try {self._banned[key]} again until "
                                             f"you press Dismiss")

    def status(self) -> Dict[str, Any]:
        """For /status and the readiness row (JSON-ready).  ``counts``: of the fresh frames with dancers, ``lost``
        the ones the live setting left a dancer bare on, ``covered`` the others."""
        sug = self.suggestion
        fresh, lost = self.last_counts
        return {"mode": self.mode, "state": self.state, "running": self.running,
                "resident": self.resident.candidate.label if self.resident is not None else None,
                "resident_mb": self.resident.mb if self.resident is not None else None,
                "last_look": self.last_look, "last_advice": self.last_advice,
                "suggestion": ({k: v for k, v in sug.items() if k not in ("winner_c", "live_key", "result")}
                               if sug is not None else None),
                "inferences": self.inferences, "auto_banned": sorted(self._banned.values()),
                "counts": {"lost": lost, "covered": fresh - lost}}
