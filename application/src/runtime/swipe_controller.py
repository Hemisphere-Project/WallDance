"""The settings swipe (Plan Cordée P5) on a worker thread beside the live loop.

The operator asks for it during the dancers' warmup (« Try other settings »).  The controller

1. waits until the pipeline's tap (``FrameProcessor._swipe_tap``) holds enough frames with a dancer on the wall
   (``SwipeParams.frames``, one per ``min_gap_s``: « collecting 12 / 150 frames with a dancer on the wall »),
   ``collect_timeout_s`` at most: then it runs on what it holds only with at least ``MIN_SHARE`` of them, and
   otherwise ends without a verdict (no fault: the dancing was too short);
2. runs those frames through each OTHER candidate setting, one engine at a time (``core.swipe_runner``:
   « trying l @ 1920 x 1056 (1/2) »), its inferences paced one per live frame, in the loop's slack, the run
   phase bounded by ``core.config.SWIPE_RUN_MAX_S``; the live setting's row is scored from the boxes the live
   pass made on each kept frame (``SwipeFrame.boxes``), its engine not re-run (``rerun_live``: re-run it);
3. ends with a ``core.swipe.SwipeResult`` (``on_result``): advice -- the swipe's own line beside its button,
   a readiness row only while it recommends a switch (``readiness``) -- which Apply acts on, in STANDBY;
4. tells the plate watch what the referee made of the empty-wall plate (``RefereeReport``: refused it, or used
   it) -- ``core.reference_watch`` where the build has it, nothing where it does not.

Nothing switches by itself.  The main loop keeps running at the live setting throughout.

Threading (mirrors how the app already loads models: ``ModelController._load_model_with_progress`` loads on a
background thread, but with the loop paused; here the loop runs on):

* the worker owns its own model instances and ``GpuPipeline``; it never calls the live model, tracker or
  enhancer, and reads the kept frames through ``FrameProcessor.swipe_snapshot`` (under the tap's lock);
* a TensorRT load captures a CUDA graph, which another thread's CUDA work breaks (measured, see
  ``core.swipe_runner``): the swipe's engines load without one, one engine load at a time in the process
  (``ENGINE_LOAD_LOCK``, also taken by ``ModelManager.load_model``), and ``live()`` returns None while the
  app loads a model, which stops the worker at its next frame;
* a project switch or a model change mid-swipe (the live setting's identity changes: project, model, input,
  loaded model object) stops the worker within one inference and drops its result (state ``dropped``);
* ``on_progress`` / ``on_result`` are called on the worker thread (the GUI side only uses the cross-thread
  safe ``set_value``/``configure_item``, like the dry-run result).
"""
from __future__ import annotations

import dataclasses
import importlib
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from core.swipe import Candidate, SwipeParams
from core.swipe_runner import (SWIPE_MODELS, CandidateRunner, ExclusionCells, SwipeInput, SwipeOutcome,
                               install_capture_guards, plan_candidates, recommended_conf, run_swipe)

IDLE, COLLECTING, RUNNING, DONE, CANCELLED, DROPPED, ERROR, NO_VERDICT = (
    "idle", "collecting", "running", "done", "cancelled", "dropped", "error", "no_verdict")
# A verdict stands on at least this share of SwipeParams.frames, for every setting: the gate holds at 150 frames;
# at 60 a worse setting is named on mur2 8 (0.05-0.4 % of swipes); 120 is the floor, with a margin.
MIN_SHARE = 0.8
_MINUTES = {1.0: "a minute", 1.5: "a minute and a half", 2.0: "two minutes", 2.5: "two and a half minutes",
            3.0: "three minutes", 3.5: "three and a half minutes", 4.0: "four minutes"}


def min_frames(p: SwipeParams) -> int:
    """The fewest frames a verdict may stand on (``MIN_SHARE`` of ``p.frames``, rounded up)."""
    return max(1, -(-int(p.frames) * int(round(MIN_SHARE * 100)) // 100))


def dancing_needed(p: SwipeParams) -> str:
    """How much dancing fills the pool, in words: « about two and a half minutes »."""
    m = round(2.0 * float(p.frames) * float(p.min_gap_s) / 60.0) / 2.0
    return "about " + _MINUTES.get(m, f"{m:g} minutes")


@dataclass(frozen=True)
class LiveSetting:
    """What the live loop runs now, and the scene it judges on.  ``model_id`` is the loaded model object's
    identity: any reload (project switch, model or size change, TensorRT toggle) changes it.  A verdict
    measured on another ROI, plate or camera frame size no longer describes this scene."""
    project: str
    candidate: Candidate
    model_id: int
    trt: bool = True
    roi: Tuple = ()                 # (enabled, x, y, w, h)
    plate: Any = ""                 # the empty-wall plate the foreground holds (``plate_identity``)
    plate_ok: bool = True           # loaded, not invalidated by hand (X5), not refused for its camera (X6),
                                    # not found stale (F2's latch, where the build has it)
    frame: Tuple = ()               # the camera frame (width, height)
    enhance: Tuple = ()             # what the network sees: enhancement on / lite / forced, CLAHE, gamma,
                                    # greyscale, background subtraction and its sensitivity
    exclusion: Tuple = ()           # the operator's excluded cells (grid, cells): boxes the referee drops
    # the plate object ``plate`` identifies -- the one the reference watch keys its verdict on (``WATCH.current``);
    # not part of the key, nor compared: ``plate`` stands for it
    plate_ref: Any = field(default=None, compare=False, repr=False)

    @property
    def key(self) -> Tuple:
        return (self.project, self.candidate, self.model_id, self.roi, self.plate, self.plate_ok, self.frame,
                self.enhance, self.exclusion)


# what_changed's sentence when the plate in use can no longer be used (found stale, invalidated, refused): the swipe
# is refused until a new one, so it says what to do, and Apply's « since the swipe: run it again » is not added
PLATE_UNUSABLE = ("the empty-wall plate is no longer valid - Calibrate on the empty wall, then run "
                  "'Try other settings' again")


def what_changed(old: "LiveSetting", new: Optional["LiveSetting"]) -> Optional[str]:
    """Why ``new`` is not the setting and scene ``old`` was measured on, in plain words; None if it is."""
    if new is None:
        return "a model load began (or no model is loaded)"
    if new.project != old.project:
        return f"project switched to {new.project}"
    if new.candidate != old.candidate or new.model_id != old.model_id:
        return f"the live setting changed ({old.candidate.label} -> {new.candidate.label})"
    if new.roi != old.roi:
        return "the ROI changed"
    if new.plate != old.plate or new.plate_ok != old.plate_ok:
        return "the empty-wall plate changed" if new.plate_ok else PLATE_UNUSABLE
    if new.frame != old.frame:
        return "the camera frame size changed"
    if new.enhance != old.enhance:
        return "the enhancement changed"
    if new.exclusion != old.exclusion:
        return "the excluded cells changed"
    return None


def say(text: str) -> None:
    """Console line that a cp1252 console cannot break (the laptop): non-ASCII is replaced."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(str(text).encode("ascii", "replace").decode("ascii"))


# ---------------------------------------------------------------------------------------------------------------
# The referee's word on the empty-wall plate, for the plate watch (``core.reference_watch``: it counts the
# refusals in a row and raises the plate alert at three; an accept starts the count over).  The module is not in
# every build: where it is absent, nothing is told and nothing fails.

# core.swipe.decide's sentence when it refuses the plate -- recognised here and nowhere else (a test pins it
# against decide's real output)
REFEREE_REFUSAL = "no verdict: every setting puts boxes where the empty-wall picture shows nobody"

# the automatic swipe's own row (``status()["last_look"]["outcome"]``) after a background look the referee refused
# the plate on: that refusal is not told to the plate watch (``RefereeReport.note(background=True)``), and the
# looker waits as after any run without a verdict
REFEREE_WAIT = "the referee does not trust the empty-wall picture: the automatic swipe waits"


def referee_refused(result) -> bool:
    """The referee refused the empty-wall plate: no verdict, every setting puts boxes where it shows nobody."""
    return (result is not None and getattr(result, "winner", None) is None
            and str(getattr(result, "reason", "") or "").startswith(REFEREE_REFUSAL))


def referee_used(result) -> bool:
    """The referee judged on the plate: « keep » or a switch (a setting named)."""
    return result is not None and getattr(result, "winner", None) is not None


def _plate_watch():
    """``core.reference_watch`` when this build has it, else None (looked up at each use)."""
    try:
        return importlib.import_module("core.reference_watch")
    except ModuleNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 - the plate watch must never sink a swipe
        say(f"[Swipe] the plate watch did not import: {type(exc).__name__}: {exc}")
        return None


class RefereeReport:
    """Every finished swipe goes through ``note`` with its verdict and the kept frames it was judged on: the
    plate refused -> ``reference_watch.note_referee_refusal(reason)``; the plate used (« keep », a switch) ->
    ``reference_watch.note_referee_accept()``; any other end (too few frames or dancers, cancelled, dropped, an
    error) tells nothing, and so does any verdict judged on a plate that was not usable (``plate_ok`` False: found
    stale, invalidated, refused for its camera -- the referee judged against a plate that does not hold, its
    verdict is worth nothing: above all no accept).  A refusal is told only when its frames share none with the last refusal told (its
    oldest kept frame after that one's newest, or its newest before that one's oldest): the same pool pressed
    three times is one refusal.  The automatic swipe's background look (``background``) tells the watch nothing,
    either way: the alert is for three deliberate presses of the operator's -- on a wall the referee refuses for
    another reason than a wrong plate the looker alone would raise it in the middle of a show (its own row says it,
    ``REFEREE_WAIT``), and its accepts, one every look, would start the operator's refusals over so the alert
    hardly ever built.  The watch counts; nothing here does."""

    def __init__(self) -> None:
        self._last: Optional[Tuple[float, float]] = None    # (oldest, newest) frame time of the last refusal told
        self._lock = threading.Lock()

    def note(self, result, frames: Sequence[Any] = (), plate_ok: bool = True,
             background: bool = False) -> Optional[str]:
        """Returns what the watch was told: "refusal", "accept", or None (nothing, or no watch in this build).
        ``plate_ok``: the plate the verdict's frames were judged on was usable (the ``LiveSetting.plate_ok`` the
        swipe ran on); False tells nothing.  ``background``: the verdict is the automatic swipe's own look, not
        the operator's press: it tells the watch nothing, either way -- a refusal would build the plate alert by
        itself mid-show, an accept would start the operator's own refusals over at every look -- and nothing of
        it is remembered as told."""
        if not plate_ok or background:
            return None
        if referee_refused(result):
            ts = [float(f.t) for f in frames or ()]
            span = (min(ts), max(ts)) if ts else None
            with self._lock:
                last = self._last
                if last is not None and span is not None and not (span[0] > last[1] or span[1] < last[0]):
                    return None                     # frames a told refusal already stood on: not a new one
                if span is not None:
                    self._last = span
            told, name, args = "refusal", "note_referee_refusal", (str(result.reason),)
        elif referee_used(result):
            told, name, args = "accept", "note_referee_accept", ()
        else:
            return None
        watch = _plate_watch()
        hook = getattr(watch, name, None) if watch is not None else None
        if hook is None:
            return None
        try:
            hook(*args)
        except Exception as exc:  # noqa: BLE001
            say(f"[Swipe] the plate watch's {name} failed: {type(exc).__name__}: {exc}")
            return None
        return told


def make_pacer(proc, interval_s: float, stop: Optional[threading.Event] = None,
               start: Optional[float] = None) -> Callable[[], None]:
    """Called before each candidate inference: at least ``interval_s`` after the previous one started, and,
    while the loop runs YOLO (a live network pass in the last 0.25 s), right after a live pass that came
    since -- about one candidate inference per live frame, in the loop's slack.  No wait for a live pass
    when the loop is not running YOLO (STANDBY, a model load), and at most 0.2 s for one.

    ``start`` (a ``time.perf_counter()`` value): the first call too waits for a live pass after it (the release's
    wait with a processor that has no ``frame_end``).  Without it the first call lets any earlier live pass count,
    and returns at once.

    ``pace.release()``: before an engine's release, not an inference -- ``after_frame_end``."""
    last = [0.0 if start is None else float(start)]
    stop = stop or threading.Event()

    def pace() -> None:
        wait = last[0] + interval_s - time.perf_counter()
        if wait > 0:
            stop.wait(wait)
        ev = getattr(proc, "frame_done", None)
        if ev is not None and time.perf_counter() - float(getattr(proc, "frame_done_t", 0.0)) <= 0.25:
            if float(proc.frame_done_t) <= last[0]:          # no live pass since our last inference
                ev.clear()
                if float(proc.frame_done_t) <= last[0]:
                    ev.wait(0.2)
        last[0] = time.perf_counter()
    pace.release = lambda: after_frame_end(proc, stop)
    return pace


def after_frame_end(proc, stop: Optional[threading.Event] = None) -> None:
    """Called right before an engine is let go (a TensorRT destructor holds the GIL some 50-70 ms): returns
    right after the END of a live frame that ends after the call -- its OSC is out and the loop's tick is over
    (``frame_end``, set by the loop) -- so the stall delays no frame's output, only the next frame's start.
    Not the end of the network pass (``frame_done``): that frame's tracker and OSC are still to come, and a
    release there delayed its OSC (the P6 re-read, 27/11: network -> OSC 57-74 ms instead of 8, in 5 releases
    of 5).  At most 0.2 s; no wait when the loop is not running YOLO (no live pass in the last 0.25 s: STANDBY,
    a model load, the loop stopped) or once ``stop`` is set.  A processor without ``frame_end``: the next live
    pass, as before.

    The collector policy (``core.gc_policy``): a tick in which it ran a full collection does not end its frame
    (``MainLoop._guarded_tick``), and while it collects (120-175 ms on the show laptop) this thread cannot run.
    So when the 0.2 s run out and the policy collected since the wait began, or is collecting right now, the
    release would land right after that collection, in the same gap between frames: it waits once more for a
    frame's end, 0.2 s at most again.  "Right now" counts: the policy's counter (``collected_n``) moves only once
    its collection is over, and this thread wakes inside the collection's tail, before it moves (measured, 8 of
    8: the policy's ``collecting`` is still true there).  The policy is read through ``sys.modules``, never imported here (the
    replay drivers do not load it); without the policy, or without a collection, nothing changes."""
    ev = getattr(proc, "frame_end", None)
    if ev is None:
        make_pacer(proc, 0.0, stop, start=time.perf_counter())()
        return
    t0 = time.perf_counter()
    if (stop is not None and stop.is_set()) or t0 - float(getattr(proc, "frame_done_t", 0.0)) > 0.25:
        return

    def policy() -> Optional[Tuple[int, bool]]:
        """(full collections counted, one under way), or None without the policy."""
        try:
            pol = getattr(sys.modules.get("core.gc_policy"), "POLICY", None)
            n = getattr(pol, "collected_n", None)
            return None if n is None else (int(n), bool(getattr(pol, "collecting", False)))
        except Exception:  # noqa: BLE001 - a broken policy changes nothing here
            return None

    def frame_end_after(t: float) -> bool:
        """At most 0.2 s for the end of a live frame that ends after ``t``; False when the bound ran out."""
        if float(proc.frame_end_t) > t:
            return True
        ev.clear()
        if float(proc.frame_end_t) > t:
            return True
        return ev.wait(0.2)

    before = policy()
    if frame_end_after(t0) or before is None:
        return
    now = policy()
    if now is not None and (now[0] != before[0] or now[1]) and (stop is None or not stop.is_set()):
        frame_end_after(time.perf_counter())


def default_runner_factory(inp: SwipeInput, proc) -> CandidateRunner:
    return CandidateRunner(inp, bg_subtractor=getattr(proc, "bg_subtractor", None))


class SwipeController:
    def __init__(self, *, processor: Callable[[], Any], live: Callable[[], Optional[LiveSetting]],
                 engine_path: Callable[[Candidate], str], plate_ready: Callable[[], bool],
                 on_progress: Callable[[str, str, float], None] = lambda s, t, p: None,
                 on_result: Callable[[Dict[str, Any], str], None] = lambda r, e: None,
                 runner_factory: Callable[[SwipeInput, Any], Any] = default_runner_factory,
                 params: Optional[SwipeParams] = None, models: Sequence[str] = SWIPE_MODELS,
                 collect_timeout_s: float = 190.0, pace: bool = True, pace_s: Optional[float] = None,
                 run_max_s: Optional[float] = None, verdict_max_age_s: Optional[float] = None,
                 rerun_live: bool = False, referee: Optional[RefereeReport] = None,
                 poll_s: float = 0.25,
                 engine_exists: Callable[[str], bool] = os.path.exists,
                 clock: Callable[[], float] = time.monotonic):
        self.processor = processor
        self.live = live
        self.engine_path = engine_path
        self.plate_ready = plate_ready
        self.on_progress = on_progress
        self.on_result = on_result
        self.runner_factory = runner_factory
        self.params = params
        self.models = tuple(models)
        self.collect_timeout_s = float(collect_timeout_s)
        self.rerun_live = bool(rerun_live)       # True: the live setting's engine re-runs the frames (a check)
        self.referee = referee if referee is not None else RefereeReport()   # the plate watch's word
        self.pace = bool(pace)
        if pace_s is None:
            from core.config import SWIPE_PACE_S
            pace_s = SWIPE_PACE_S
        self.pace_s = max(0.0, float(pace_s))
        from core.config import SWIPE_RUN_MAX_S, SWIPE_VERDICT_MAX_AGE_S
        self.run_max_s = float(SWIPE_RUN_MAX_S if run_max_s is None else run_max_s)
        self.verdict_max_age_s = float(SWIPE_VERDICT_MAX_AGE_S if verdict_max_age_s is None else verdict_max_age_s)
        self.fault: Optional[str] = None        # a failed Apply: the readiness row's one ``fail`` ...
        self.fault_recovered = False            # ... ``warn`` instead once the previous setting runs again
        self.dismissed = False                  # the operator dismissed the verdict's advice
        self.poll_s = float(poll_s)
        self.engine_exists = engine_exists
        self.clock = clock
        self.state = IDLE
        self.text = ""
        self.progress = 0.0
        self.outcome: Optional[SwipeOutcome] = None
        self.outcome_live: Optional[LiveSetting] = None   # the live setting the outcome was measured on
        self.finished_at: Optional[float] = None          # wall time
        self.applied: Optional[Candidate] = None          # the winner Apply switched to
        self._ended_at: Optional[float] = None            # clock() at the last end
        self.rerun_guard_s = 1.0                          # a Run this soon after an end is a late Cancel click
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.state in (COLLECTING, RUNNING)

    def _params(self, proc) -> SwipeParams:
        """The referee's parameters, frozen for this swipe, its box threshold the show's confidence (the
        candidates are judged at the threshold the show runs; 0.15 without one)."""
        base = self.params
        if base is None:
            col = getattr(proc, "swipe_collector", None)
            base = getattr(col, "params", None) or SwipeParams()
        conf = float(getattr(getattr(proc, "settings", None), "confidence", 0.0) or 0.0) or 0.15
        return dataclasses.replace(base, conf=conf)

    def start(self) -> Optional[str]:
        """Start a swipe; None when started, else why not (also reported through ``on_result``)."""
        with self._lock:
            if self.busy:
                return "a swipe is already running"
            if self._ended_at is not None and self.clock() - self._ended_at < self.rerun_guard_s:
                return "a swipe just ended - press again to start a new one"
            live = self.live()
            why = None
            if live is None:
                why = "no model loaded"
            elif not live.trt:
                why = ("the swipe compares TensorRT engines and the live model runs PyTorch: "
                       "tick TensorRT first")
            elif not self.plate_ready():
                why = "no valid empty-wall plate: Calibrate on the empty wall first"
            if why is not None:
                self.state, self.text = ERROR, why
                self.on_progress(ERROR, why, 0.0)
                self.on_result({}, why)
                return why
            self._cancel.clear()
            self.outcome, self.outcome_live, self.applied = None, live, None
            self.fault, self.dismissed, self.fault_recovered = None, False, False
            self.state, self.text, self.progress = COLLECTING, "collecting frames", 0.0
            self._thread = threading.Thread(target=self._work, args=(live,), name="SettingsSwipe",
                                            daemon=True)
            self._thread.start()
            return None

    def cancel(self) -> None:
        self._cancel.set()

    def join(self, timeout: Optional[float] = None) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    # ------------------------------------------------------------------
    def _changed(self, live0: LiveSetting) -> Optional[str]:
        """Why the live setting or its scene is no longer the one the swipe started on, or None."""
        return what_changed(live0, self.live())

    def _say(self, state: str, text: str, progress: float) -> None:
        self.state, self.text, self.progress = state, text, float(progress)
        self.on_progress(state, text, float(progress))

    def _end(self, state: str, text: str, outcome: Optional[SwipeOutcome] = None) -> None:
        self.outcome = outcome if state == DONE else None
        self.finished_at = time.time()
        self._ended_at = self.clock()
        self._say(state, text, 1.0 if state == DONE else self.progress)
        if state == DONE and outcome is not None:
            self.on_result(outcome.to_dict(), "")
        elif state == NO_VERDICT:                   # not a fault: the sentence says what to do
            self.on_result({}, text)
        else:
            self.on_result({}, f"swipe {state}: {text}")
        say(f"[Swipe] {state}: {text}")

    def _pacer(self, proc) -> Optional[Callable[[], None]]:
        if not self.pace:
            return None
        return make_pacer(proc, self.pace_s, self._cancel)

    def _work(self, live0: LiveSetting) -> None:
        try:
            install_capture_guards()
            proc = self.processor()
            p = self._params(proc)
            # 1. the kept frames
            t0 = self.clock()
            last = None
            note = ""
            while True:
                if self._cancel.is_set():
                    return self._end(CANCELLED, "cancelled while collecting")
                why = self._changed(live0)
                if why:
                    return self._end(DROPPED, why)
                frames, lost, prog, ready = proc.swipe_snapshot()      # lost: information only
                text = f"collecting {len(frames)} / {p.frames} frames with a dancer on the wall"
                if text != last:
                    self._say(COLLECTING, text, 0.3 * prog)
                    last = text
                if ready:
                    break
                waited = self.clock() - t0
                if waited >= self.collect_timeout_s:
                    if len(frames) >= min_frames(p):
                        note = f"the verdict stands on {len(frames)} / {p.frames} frames"
                        break
                    if frames:
                        return self._end(NO_VERDICT, f"only {len(frames)} / {p.frames} frames with a dancer on "
                                                     f"the wall in {waited:.0f} s - the swipe needs "
                                                     f"{dancing_needed(p)} of dancing; press again later")
                    return self._end(ERROR, f"no frame with a dancer on the wall in {waited:.0f} s "
                                            "(RUN on, the empty-wall plate valid?)")
                self._cancel.wait(self.poll_s)
            collect_s = self.clock() - t0
            # the referee's threshold now: the frames were kept at this confidence (the tap starts over on another)
            p = self._params(proc)
            # 2. the candidates
            images = [f.image for f in frames]
            observations = [f.obs for f in frames]
            inp = SwipeInput.from_processor(proc)
            fh, fw = images[0].shape[:2]
            roi = inp.roi(fw, fh)
            _rx, _ry, roi_w, roi_h = roi
            try:
                exclusion = ExclusionCells.from_processor(proc, roi)
            except Exception:  # noqa: BLE001 - a fake / older processor: no mask
                exclusion = None
            plan = plan_candidates(live0.candidate, roi_w, roi_h,
                                   lambda c: self.engine_exists(self.engine_path(c)),
                                   models=self.models, dancer_net_px=self._dancer_net_px(proc))
            if live0.candidate not in plan.candidates:
                return self._end(ERROR, f"the live setting's engine {live0.candidate.engine_file} is missing")
            runner = self.runner_factory(inp, proc)

            def progress(text: str, frac: float) -> None:
                self._say(RUNNING, text, 0.3 + 0.7 * frac)

            def cancelled() -> bool:
                return self._cancel.is_set() or self._changed(live0) is not None

            share = proc.swipe_lost_share() if hasattr(proc, "swipe_lost_share") else None
            outcome = run_swipe(images, observations, plan, live0.candidate, runner, self.engine_path, p,
                                progress=progress, cancelled=cancelled, pace=self._pacer(proc),
                                lost_frames=lost, lost=[bool(f.lost) for f in frames], lost_share=share,
                                exclusion=exclusion, max_s=self.run_max_s,
                                live_boxes=None if self.rerun_live else [f.boxes for f in frames])
            outcome.timing["collect_s"] = collect_s
            outcome.timing["total_s"] = collect_s + outcome.timing.get("run_s", 0.0)
            if self._cancel.is_set():
                return self._end(CANCELLED, "cancelled")
            why = self._changed(live0)
            if why:
                return self._end(DROPPED, why)
            if outcome.live_error:
                return self._end(ERROR, f"the live setting could not be re-run: {outcome.live_error}")
            if outcome.timed_out:                   # no verdict on part of the frames: none at all
                return self._end(NO_VERDICT, f"no verdict: the swipe ran over {self.run_max_s:.0f} s (the live "
                                             "loop left it little room) - run it again")
            if outcome.result is None:
                errs = "; ".join(f"{r.candidate.label}: {r.error}" for r in outcome.runs if r.error)
                return self._end(ERROR, f"no setting could be scored ({errs or 'nothing ran'})")
            res = outcome.result
            text = res.reason + (f" ({note})" if note else "")
            skipped = "; ".join(f"{c.label}: {w}" for c, w in outcome.skipped)
            if skipped:
                say(f"[Swipe] not tried: {skipped}")
            self.referee.note(res, frames, plate_ok=live0.plate_ok)
            self._end(DONE, text, outcome)
        except Exception as exc:  # noqa: BLE001 - a swipe failure must not stop the show
            import traceback
            traceback.print_exc()
            self._end(ERROR, f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _dancer_net_px(proc) -> Optional[float]:
        """The dancers' measured height at the network now (``check_dancer_size``'s figure), if fresh."""
        m = getattr(proc, "dancer_height", None)
        lb = float(getattr(proc, "last_lb_scale", 0.0) or 0.0)
        if not m or not lb or time.time() - float(m[2]) > 1800.0:
            return None
        return float(m[0]) * lb

    # ------------------------------------------------------------------
    # Readiness, Apply, remote status
    # ------------------------------------------------------------------
    def _stale(self, live_now: Optional[LiveSetting]) -> bool:
        o = self.outcome_live
        return o is not None and (live_now is None or live_now.key != o.key)

    def age_s(self) -> Optional[float]:
        return None if self.finished_at is None else max(0.0, time.time() - self.finished_at)

    def _advice_live(self, live_now: Optional[LiveSetting]) -> bool:
        """The verdict still advises a switch: done, names another setting, not applied, not dismissed, not
        stale, not too old."""
        res = self.outcome.result if self.outcome is not None else None
        if res is None or res.winner is None or not res.switch or self.dismissed:
            return False
        if self.applied is not None or self._stale(live_now):
            return False
        age = self.age_s()
        return age is not None and age <= self.verdict_max_age_s

    def readiness(self, *, plate_ready: bool, live_now: Optional[LiveSetting], other_fault: Optional[str] = None,
                  seamless: Optional[bool] = None, swipe_auto: str = "off"):
        """The swipe's readiness row, or None: the swipe is advice, not a readiness condition.  A ``warn`` row
        only while a swipe the operator ran recommends a switch (worded for STANDBY, or for the seamless Apply when
        ``seamless``); for a failed Apply a ``fail`` row while nothing sure runs and a ``warn`` one once the
        previous setting runs again -- this controller's reload, or a failed seamless switch (``other_fault``:
        ``SeamlessSwitch.health()``, the previous setting never stopped: ``warn``); no row otherwise (no swipe
        yet, collecting, no verdict, stale, too old, « keep », no plate; a background suggestion) -- the swipe's
        own line beside its button says those.  ``swipe_auto``: the project key (off: P5's row, P5's Apply)."""
        from core.ops_monitor import check_swipe
        result = self.outcome.result if self._advice_live(live_now) else None
        if self.fault:
            return check_swipe(fault=self.fault, fault_recovered=self.fault_recovered, result=result,
                               seamless=bool(seamless), swipe_auto=swipe_auto)
        return check_swipe(fault=other_fault, fault_recovered=True, result=result, seamless=bool(seamless),
                           swipe_auto=swipe_auto)

    def applicable(self, live_now: Optional[LiveSetting]) -> Tuple[Optional[str], Optional[Candidate],
                                                                    Optional[float]]:
        """(why not, winner, recommended confidence or None) for Apply."""
        if self.busy:
            return "a swipe is still running", None, None
        res = self.outcome.result if self.outcome is not None else None
        if res is None:
            return "no swipe result to apply - run 'Try other settings' first", None, None
        if res.winner is None:
            return f"the swipe gave no verdict ({res.reason})", None, None
        if not res.switch:
            return f"the swipe says keep {res.current.label}: nothing to apply", None, None
        if self.dismissed:
            return "dismissed - run 'Try other settings' again", None, None
        if self._stale(live_now):
            why = what_changed(self.outcome_live, live_now) if self.outcome_live is not None else None
            if why == PLATE_UNUSABLE:               # it already says what to do
                return why, None, None
            return f"{why or 'the setting changed'} since the swipe: run it again", None, None
        age = self.age_s()
        if age is not None and age > self.verdict_max_age_s:
            return f"the swipe is {age / 60.0:.0f} min old: run it again", None, None
        return None, res.winner, recommended_conf(res)

    def dismiss(self) -> None:
        """The operator's Dismiss: the advice goes, a failed Apply is acknowledged."""
        self.dismissed = True
        self.fault, self.fault_recovered = None, False

    def set_fault(self, text: Optional[str], recovered: bool = False) -> None:
        """A failed Apply.  ``recovered``: a setting that ran before runs again on TensorRT (the row is a notice
        to acknowledge, ``warn``); else nothing sure runs yet (``fail``)."""
        self.fault = text
        self.fault_recovered = bool(recovered) and bool(text)

    def mark_applied(self, winner: Candidate) -> None:
        self.applied = winner
        self.fault, self.fault_recovered = None, False

    def status_dict(self) -> Dict[str, Any]:
        res = self.outcome.result if self.outcome is not None else None
        return {"state": self.state, "text": self.text, "progress": round(self.progress, 2),
                "winner": res.winner.label if res is not None and res.winner is not None else None,
                "switch": bool(res.switch) if res is not None else False,
                "applied": self.applied.label if self.applied is not None else None,
                "dismissed": self.dismissed, "fault": self.fault,
                "age_s": round(self.age_s(), 1) if self.age_s() is not None else None}
