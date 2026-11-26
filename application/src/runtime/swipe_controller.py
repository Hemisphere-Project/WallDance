"""The settings swipe (Plan Cordée P5) on a worker thread beside the live loop.

The operator asks for it during the dancers' warmup (« Try other settings »).  The controller

1. waits until the pipeline's tap (``FrameProcessor._swipe_tap``) holds ~30 frames where the live setting
   leaves a dancer without a box (« collecting 12 / 30 »);
2. runs those frames through each candidate setting, one engine at a time (``core.swipe_runner``:
   « trying l @ 1920 x 1056 (3/6) »), its inferences paced one per live frame, in the loop's slack;
3. ends with a ``core.swipe.SwipeResult`` (``on_result``), which readiness reads and Apply acts on.

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

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from core.swipe import Candidate, SwipeParams
from core.swipe_runner import (SWIPE_MODELS, CandidateRunner, SwipeInput, SwipeOutcome,
                               install_capture_guards, plan_candidates, recommended_conf, run_swipe)

IDLE, COLLECTING, RUNNING, DONE, CANCELLED, DROPPED, ERROR = (
    "idle", "collecting", "running", "done", "cancelled", "dropped", "error")


@dataclass(frozen=True)
class LiveSetting:
    """What the live loop runs now.  ``model_id`` is the loaded model object's identity: any reload (project
    switch, model or size change, TensorRT toggle) changes it."""
    project: str
    candidate: Candidate
    model_id: int
    trt: bool = True

    @property
    def key(self) -> Tuple:
        return (self.project, self.candidate, self.model_id)


def make_pacer(proc, interval_s: float, stop: Optional[threading.Event] = None) -> Callable[[], None]:
    """Called before each candidate inference: at least ``interval_s`` after the previous one started, and,
    while the loop runs YOLO (a live network pass in the last 0.25 s), right after a live pass that came
    since -- about one candidate inference per live frame, in the loop's slack.  No wait for a live pass
    when the loop is not running YOLO (STANDBY, a model load)."""
    last = [0.0]
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
    return pace


def default_runner_factory(inp: SwipeInput, proc) -> CandidateRunner:
    return CandidateRunner(inp, bg_subtractor=getattr(proc, "bg_subtractor", None))


class SwipeController:
    def __init__(self, *, processor: Callable[[], Any], live: Callable[[], Optional[LiveSetting]],
                 engine_path: Callable[[Candidate], str], plate_ready: Callable[[], bool],
                 on_progress: Callable[[str, str, float], None] = lambda s, t, p: None,
                 on_result: Callable[[Dict[str, Any], str], None] = lambda r, e: None,
                 runner_factory: Callable[[SwipeInput, Any], Any] = default_runner_factory,
                 params: Optional[SwipeParams] = None, models: Sequence[str] = SWIPE_MODELS,
                 collect_timeout_s: float = 120.0, pace: bool = True, pace_s: Optional[float] = None,
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
        self.pace = bool(pace)
        if pace_s is None:
            from core.config import SWIPE_PACE_S
            pace_s = SWIPE_PACE_S
        self.pace_s = max(0.0, float(pace_s))
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
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.state in (COLLECTING, RUNNING)

    def _params(self, proc) -> SwipeParams:
        if self.params is not None:
            return self.params
        col = getattr(proc, "swipe_collector", None)
        return getattr(col, "params", None) or SwipeParams()

    def start(self) -> Optional[str]:
        """Start a swipe; None when started, else why not (also reported through ``on_result``)."""
        with self._lock:
            if self.busy:
                return "a swipe is already running"
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
        """Why the live setting is no longer the one the swipe started on, or None."""
        now = self.live()
        if now is None:
            return "a model load began (or no model is loaded)"
        if now.project != live0.project:
            return f"project switched to {now.project}"
        if now.key != live0.key:
            return f"the live setting changed ({live0.candidate.label} -> {now.candidate.label})"
        return None

    def _say(self, state: str, text: str, progress: float) -> None:
        self.state, self.text, self.progress = state, text, float(progress)
        self.on_progress(state, text, float(progress))

    def _end(self, state: str, text: str, outcome: Optional[SwipeOutcome] = None) -> None:
        self.outcome = outcome if state == DONE else None
        self.finished_at = time.time()
        self._say(state, text, 1.0 if state == DONE else self.progress)
        if state == DONE and outcome is not None:
            self.on_result(outcome.to_dict(), "")
        else:
            self.on_result({}, f"swipe {state}: {text}")
        print(f"[Swipe] {state}: {text}")

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
                frames, lost, prog, ready = proc.swipe_snapshot()
                text = f"collecting {lost} / {p.frames} frames where a dancer has no box"
                if text != last:
                    self._say(COLLECTING, text, 0.3 * prog)
                    last = text
                if ready:
                    break
                waited = self.clock() - t0
                if waited >= self.collect_timeout_s:
                    if frames:
                        note = (f"after {waited:.0f} s only {lost} frames with a dancer bare: "
                                f"{len(frames) - lost} covered ones fill in")
                        break
                    return self._end(ERROR, f"no frame with a dancer on the wall in {waited:.0f} s "
                                            "(RUN on, the empty-wall plate valid?)")
                self._cancel.wait(self.poll_s)
            collect_s = self.clock() - t0
            # 2. the candidates
            images = [f.image for f in frames]
            observations = [f.obs for f in frames]
            inp = SwipeInput.from_processor(proc)
            fh, fw = images[0].shape[:2]
            _rx, _ry, roi_w, roi_h = inp.roi(fw, fh)
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
                                lost_frames=lost, lost=[bool(f.lost) for f in frames], lost_share=share)
            outcome.timing["collect_s"] = collect_s
            outcome.timing["total_s"] = collect_s + outcome.timing.get("run_s", 0.0)
            if self._cancel.is_set():
                return self._end(CANCELLED, "cancelled")
            why = self._changed(live0)
            if why:
                return self._end(DROPPED, why)
            if outcome.result is None:
                errs = "; ".join(f"{r.candidate.label}: {r.error}" for r in outcome.runs if r.error)
                return self._end(ERROR, f"no setting could be scored ({errs or 'nothing ran'})")
            res = outcome.result
            text = res.reason + (f" ({note})" if note else "")
            skipped = "; ".join(f"{c.label}: {w}" for c, w in outcome.skipped)
            if skipped:
                print(f"[Swipe] not tried: {skipped}")
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

    def readiness(self, *, plate_ready: bool, live_now: Optional[LiveSetting]):
        from core.ops_monitor import check_swipe
        res = self.outcome.result if self.outcome is not None else None
        applied = (res is not None and self.applied is not None and live_now is not None
                   and live_now.candidate == self.applied)
        return check_swipe(plate_ready=plate_ready, state=self.state, text=self.text, result=res,
                           live_label=live_now.candidate.label if live_now else "",
                           stale=res is not None and not applied and self._stale(live_now),
                           applied=applied)

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
        if self._stale(live_now):
            return "the setting changed since the swipe: run it again", None, None
        return None, res.winner, recommended_conf(res)

    def mark_applied(self, winner: Candidate) -> None:
        self.applied = winner

    def status_dict(self) -> Dict[str, Any]:
        res = self.outcome.result if self.outcome is not None else None
        return {"state": self.state, "text": self.text, "progress": round(self.progress, 2),
                "winner": res.winner.label if res is not None and res.winner is not None else None,
                "switch": bool(res.switch) if res is not None else False,
                "applied": self.applied.label if self.applied is not None else None}
