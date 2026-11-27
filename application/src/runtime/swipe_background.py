"""The autonomous swipe (Plan Cordee P6) beside the live loop: it looks by itself, says, or switches.

``core.swipe_auto`` is the policy (when to look, what to say, when to switch); this is the machinery that feeds it
and acts on its advice, on its own thread, without the operator:

* **the look** (``tick``, every ``look_s`` = 5 s): the kept frames with dancers the previous run did not use,
  counted under the tap's lock (no image copied).  A run needs a FULL pool of them (``SwipeParams.frames`` = 150,
  1 s apart: two and a half minutes of dancing; never the operator's 80 % floor), then the policy's ``due``.  An
  empty wall, STANDBY, a model load, an operator's swipe, a calibration: ``idle`` -- no engine loaded, no
  inference, no state that grows;
* **the background run**, much gentler than the operator's swipe: two settings only -- the live one, scored on
  the boxes it already made (``SwipeFrame.boxes``: no inference), and the OTHER MODEL AT THE LIVE NETWORK INPUT,
  kept RESIDENT (loaded only when a run is due, under ``ENGINE_LOAD_LOCK`` and without a CUDA graph; kept between
  runs; released after ``SWIPE_AUTO_RESIDENT_IDLE_S`` without a run due, when the mode goes off or the project,
  input or TensorRT state changes, never inside a run), its inferences paced ``pace_s`` apart right after a live
  network pass (150 x 0.2 s: about 30 s of gentle inference), judged at the show's confidence.  A run without a
  verdict says nothing (the operator's swipe's ``no_verdict``);
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
  it -- until Dismiss or a project load; it is said once, as the switch's fault, in plain words (the raw error
  goes to the log), and while it holds, the look says why the swipe waits.

Threading: the looker thread owns the resident model and its inferences; the loop thread owns the live model and
the swap.  Every CUDA call here happens under ENGINE_LOAD_LOCK (a live load waits, and a running look stops at its
next frame when one begins: ``live()`` is None then), on frames the current live model produced, and releases wait
until the current live model has run a frame.  Errors end the look, never the app.
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
                 pace_s: Optional[float] = None, idle_s: Optional[float] = None, models=SWIPE_MODELS,
                 runner_factory: Callable[[Any], Any] = _runner_factory,
                 engine_exists: Callable[[str], bool] = os.path.exists,
                 engine_lock=None, wall: Callable[[], float] = time.time, referee=None):
        from core.config import SWIPE_AUTO_LOOK_S, SWIPE_AUTO_PACE_S, SWIPE_AUTO_RESIDENT_IDLE_S
        self.processor = processor
        self.live = live
        self.engine_path = engine_path
        self.busy = busy
        self.switch = switch                  # the seamless switch (the operator's Apply's): it swaps, holds THE fault
        switch.on_switched = self._on_switched
        switch.on_confirmed = self._on_switch_confirmed
        switch.on_failed = self._on_switch_failed
        self.notify = notify
        self.auto = PolicyPort(mode, auto_params)
        self.params = params
        self.look_s = float(SWIPE_AUTO_LOOK_S if look_s is None else look_s)
        self.pace_s = float(SWIPE_AUTO_PACE_S if pace_s is None else pace_s)
        self.idle_s = float(SWIPE_AUTO_RESIDENT_IDLE_S if idle_s is None else idle_s)
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
        # settings an automatic switch failed to: no automatic attempt again until Dismiss or a project load
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
        self._last_due: Optional[float] = None          # loop clock of the last run due (the resident's idle time)
        self.history: Deque[Dict[str, Any]] = deque(maxlen=200)   # looks, advices, switches (bounded)
        self.inferences = 0                        # candidate inferences so far
        self.ticks = 0
        self.tick_ms: Deque[float] = deque(maxlen=500)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ mode, thread
    @property
    def mode(self) -> str:
        return self.auto.mode

    def set_mode(self, mode: str) -> str:
        m = parse_mode(mode)
        self.auto.mode = m
        if m == MODE_OFF:
            self._clear_suggestion("the automatic swipe is off")
            self.state = "off"
        elif self.state == "off":
            self.state = "watching"
        return m

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="SwipeAuto", daemon=True)
        self._thread.start()

    def stop(self, timeout: Optional[float] = 2.0) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.look_s)
            self._wake.clear()
            if self._stop.is_set():
                break
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - a look's error ends the look, never the app
                self.running = False
                say(f"[SwipeAuto] look failed: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ the look
    def _record(self, kind: str, **data) -> Dict[str, Any]:
        row = {"kind": kind, "wall": self.wall(), **data}
        self.history.append(row)
        return row

    def _free_pending(self, live: Optional[LiveSetting]) -> None:
        """A resident that no longer fits (mode off, another setting, another project) is released in the switch's
        safe window (right after a live frame, under the engine lock), with the models the switch frees."""
        if not (self.resident is not None and self._resident_invalid(live)):
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
            return True
        if self._idle_for() > self.idle_s:
            return True                       # no run due for a while (an empty wall): one engine, the live one
        return r.candidate != other_model(live.candidate, self.models) or r.key != self._resident_key(live)

    def _idle_for(self) -> float:
        """Loop-clock seconds since the last run was due (0 before any)."""
        if self._last_due is None:
            return 0.0
        try:
            return max(0.0, float(self.processor().loop_clock()) - self._last_due)
        except Exception:  # noqa: BLE001
            return 0.0

    @staticmethod
    def _resident_key(live: LiveSetting) -> Tuple:
        c = live.candidate
        return (live.project, int(c.imgsz), tuple(c.shape) if c.shape else None, bool(live.trt))

    def tick(self) -> str:
        """One look.  Returns what it did: off | loading | waiting (the plate is not usable) | idle | busy | banned
        (the other model could not be loaded) | run | probe | nothing-to-compare."""
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
        self._free_pending(live)
        if self.auto.mode == MODE_OFF:
            self.state = "off"
            return "off"
        if live is None:
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
        if len(fresh) < self.auto.run_frames:
            return "idle"                     # a full pool of fresh frames with dancers first: nothing scored, loaded
        cands = self.auto.candidates([other] if other is not None else [], live.candidate)
        why_not = ("the live model runs PyTorch" if not live.trt else
                   "no other model for this setting" if not cands else
                   f"{cands[0].label} not tried: its engine is not built on this machine ({cands[0].engine_file})"
                   if not self.engine_exists(self.engine_path(cands[0])) else None)
        if why_not:
            if self.last_look is None or self.last_look.get("outcome") != why_not:
                self.last_look = self._record("look", look="none", t=now, frames=0, seconds=0.0, outcome=why_not)
            return "nothing-to-compare"
        sp = self._run_params(proc)
        skipped = self.auto.skipped
        kind = self.auto.due(now, fresh, busy=False, room=self.auto.room(live.candidate, cands, fresh, sp))
        if kind == "idle":
            if self.auto.skipped > skipped:   # the pre-filter: the live setting leaves no room for the bar
                self.last_look = self._record("look", look="skipped", t=now, frames=len(fresh), seconds=0.0,
                                              outcome="no room for a gain over the bar: no run, no inference")
            return "idle"
        self._last_due = now
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
        """None once the resident is there, else (what the look says, the fault for the operator)."""
        r = self.resident
        if r is not None and r.candidate == other and r.key == self._resident_key(live):
            return None
        try:
            model, pose_runner, load_s = runner.load(self.engine_path(other), other)
        except Exception as exc:  # noqa: BLE001 - the live model runs on; this setting is not tried again
            return self._not_loaded(other, live, exc)
        self.resident = Resident(other, model, pose_runner, self._resident_key(live), load_s)
        self._record("load", resident=other.label, seconds=round(load_s, 2))
        say(f"[SwipeAuto] {other.label} loaded beside the live model ({load_s:.2f} s)")
        return None

    def _not_loaded(self, other: Candidate, live: LiveSetting, exc: BaseException) -> Tuple[str, str]:
        """One attempt only: the setting is banned (no load, no inference, no look for it until Dismiss or a project
        load), the raw error goes to the log, the operator reads plain words."""
        why = load_failure(exc)
        todo = (f"rebuild {other.engine_file} (extra/build_engines), then Dismiss lets it try again"
                if why == ENGINE_UNREADABLE else "Dismiss lets it try again, or set the automatic swipe to off")
        say(f"[SwipeAuto] {other.engine_file} did not load: {type(exc).__name__}: {exc}")
        wait = f"the automatic swipe waits: it could not load {other.label} ({why}) - {todo}"
        self._banned[other.key] = other.engine_file
        self._unloadable[other.key] = wait
        self._record("auto-banned", setting=other.label, engine=other.engine_file,
                     error=f"{type(exc).__name__}: {str(exc)[:160]}")
        return wait, (f"the automatic swipe could not load {other.label} ({why}): {live.candidate.label} still runs "
                      f"- {todo}")

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
        fault: Optional[str] = None              # the resident could not be loaded: said once, after the lock
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
            if self.referee is not None:             # refused / used the plate: told to the plate watch
                self.referee.note(result, frames, plate_ok=live.plate_ok)
            now = proc.loop_clock()
            # the « slower » rule: the candidate's times here and the live model's sampled over the same run, alike
            speed = self.auto.speed(run.ms, live_ms)
            ms = {c: v for c, v in ((other, speed[0]), (live.candidate, speed[1])) if v is not None}
            self.auto.ran(now, probe=(kind == "probe"))          # the policy's order: ran, then advise
            ran = True
            adv = self.auto.advise(now, result, newest_frame_t=max(float(f.t) for f in frames), ms=ms)
            outcome = result.reason
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
                self._fault_why = (fault, f"the automatic swipe could not load {other.label}: Dismiss lets it "
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
        streak is over, the pool starts again."""
        self.auto.switched(req["take_t"], req["old"], req["new"])

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
        (no second load), else the resident leaves first (only two engines are ever resident) and the switch
        loads the winner."""
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

    def clear_bans(self) -> None:
        """A project load: the settings an automatic switch failed to (or whose resident did not load) may be tried
        again."""
        self._banned.clear()
        self._unloadable.clear()

    def dismiss(self) -> None:
        """The operator's Dismiss: the suggestion goes (a failed switch is the switch's to acknowledge) and the
        settings an automatic switch failed to (or whose resident did not load) may be tried again."""
        self._banned.clear()
        self._unloadable.clear()
        if self.suggestion is not None:
            self.suggestion = None
            self.notify("cleared", {"text": "dismissed"})

    def status(self) -> Dict[str, Any]:
        """For /status and the readiness row (JSON-ready).  ``counts``: of the fresh frames with dancers, ``lost``
        the ones the live setting left a dancer bare on, ``covered`` the others."""
        sug = self.suggestion
        fresh, lost = self.last_counts
        return {"mode": self.mode, "state": self.state, "running": self.running,
                "resident": self.resident.candidate.label if self.resident is not None else None,
                "last_look": self.last_look, "last_advice": self.last_advice,
                "suggestion": ({k: v for k, v in sug.items() if k not in ("winner_c", "live_key", "result")}
                               if sug is not None else None),
                "inferences": self.inferences, "auto_banned": sorted(self._banned.values()),
                "counts": {"lost": lost, "covered": fresh - lost}}
